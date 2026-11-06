"""Bounded CSV/XLSX parsing, column mapping, and atomic inventory imports."""
import csv
import io
import json
import re
import zipfile
from openpyxl import load_workbook
from .domain import Problem, need, text, integer, create_part, set_stock, canonical_package
from .db import rows
from .bom import is_eda_bom, adapt_eda_bom, review_bom

MAX_UPLOAD = 10 * 1024 * 1024
MAX_ROWS = 5000
ALIASES = {
 'name': ['名称','器件名称','型号','name','comment','value'],
 'category': ['类别','category'], 'package': ['封装','package','footprint'],
 'value': ['数值','参数值','value'], 'quantity': ['数量','quantity','qty'],
 'per_board': ['单板用量','单板数量','quantity','qty','数量'], 'designators':['位号','designator','designators'],
 'box': ['盒号','box'], 'cell':['格号','cell'], 'mode':['数量方式','mode'],
 'supplier_code':['商城编号','supplier_code','lcsc','supplier part','商品编号','供应商料号','立创编号'],
 'notes':['备注','notes'],
 '耐压':['耐压','voltage'],'介质':['介质','dielectric'],'精度':['精度','tolerance'],
 '功率':['功率','power'],'额定电流':['额定电流','current']
}


def workbook_rows(data,filename,all_sheets=False):
    """Read data only. LCSC orders use a later header and sometimes hidden template sheets."""
    name=filename.lower()
    tables=[]
    if name.endswith('.xls'):
        import xlrd
        book=None
        try:
            book=xlrd.open_workbook(file_contents=data,on_demand=True)
            need(book.nsheets<=20,'工作表过多，最多支持 20 张')
            for index in range(book.nsheets if all_sheets else min(book.nsheets,1)):
                sheet=book.sheet_by_index(index)
                need(sheet.nrows<=MAX_ROWS+200 and sheet.ncols<=100,'表格超过行列限制')
                content=[]
                for row in sheet.get_rows():
                    content.append([text(int(c.value) if c.ctype==xlrd.XL_CELL_NUMBER and float(c.value).is_integer() else c.value,2000) for c in row])
                tables.append((sheet.name,content))
        except Problem:raise
        except Exception:raise Problem('无法读取 XLS，请使用有效的 Excel 97–2003 文件')
        finally:
            if book is not None:book.release_resources()
    elif name.endswith('.xlsx'):
        book=None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                need(sum(x.file_size for x in z.infolist())<40*1024*1024,'Excel 解压后过大')
            book=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
            need(len(book.sheetnames)<=20,'工作表过多，最多支持 20 张')
            sheets=book.worksheets if all_sheets else book.worksheets[:1]
            for sheet in sheets:
                content=[]
                for row in sheet.iter_rows(values_only=True):
                    need(len(row)<=100,'表格最多支持 100 列')
                    content.append([text(v,2000) for v in row])
                    need(len(content)<=MAX_ROWS+200,'表格超过行数限制')
                tables.append((sheet.title,content))
        except Problem:raise
        except Exception:raise Problem('无法读取 Excel，请使用有效的 .xlsx 文件')
        finally:
            if book is not None:book.close()
    else:
        need(name.endswith(('.csv','.tsv')),'支持 CSV、TSV、XLS 和 XLSX 文件')
        decoded=None
        for encoding in ('utf-8-sig','gb18030'):
            try:decoded=data.decode(encoding);break
            except UnicodeDecodeError:pass
        need(decoded is not None,'无法识别文件编码，请保存为 UTF-8 CSV')
        try:dialect=csv.Sniffer().sniff(decoded[:8192],delimiters=',;\t')
        except csv.Error:dialect=csv.excel_tab if name.endswith('.tsv') else csv.excel
        content=[]
        for row in csv.reader(io.StringIO(decoded),dialect):
            need(len(row)<=100,'表格最多支持 100 列')
            if any(text(v) for v in row):content.append([text(v,2000) for v in row])
            need(len(content)<=MAX_ROWS+200,'表格超过行数限制')
        tables.append(('CSV',content))
    need(sum(len(r) for _,table in tables for r in table)<=520000,'表格数据量过大')
    return tables


def table_file(file,kind='auto'):
    data=file.file.read(MAX_UPLOAD+1)
    need(len(data)<=MAX_UPLOAD,'文件不能超过 10 MB')
    tables=workbook_rows(data,file.filename or '',all_sheets=True)
    need(tables,'Excel 没有工作表')
    detected=[]
    for sheet,table in tables:
        for index,row in enumerate(table[:200]):
            if is_eda_bom(row):
                detected.append((sheet,table,index));break
    if detected:
        need(kind!='inventory','检测到立创 / 嘉立创 EDA 的 BOM，请到“项目与 BOM”使用“导入 BOM”')
        need(len(detected)==1,'文件包含多张 BOM 明细表，请分别导出后导入')
        sheet,table,index=detected[0]
        parsed=adapt_eda_bom(table,index,sheet,file.filename)
        need(0<len(parsed['rows'])<=MAX_ROWS,'BOM 明细不能为空，最多 5000 行')
        need(all(parsed['headers']) and len(set(parsed['headers']))==len(parsed['headers']),'标题列不能为空或重名')
        return parsed
    content=tables[0][1]
    need(len(content)<=MAX_ROWS+1,'最多导入 5000 行')
    need(not any('商品编号' in row and '厂家型号' in row and '商品名称' in row for row in content[:200]),'检测到立创订单，请在“采购与入库”使用“导入立创订单”入口')
    need(len(content) > 1, '文件需要标题行和至少一行数据')
    headers = content[0]
    need(len(set(headers)) == len(headers) and all(headers), '标题列不能为空或重名')
    entries = []
    for row in content[1:]:
        need(len(row) <= len(headers), '数据列数超过标题列数，请检查分隔符')
        if any(row):
            entries.append(dict(zip(headers, row + ['']*(len(headers)-len(row)))))
    mapping = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            found = next((h for h in headers if h.casefold() == alias.casefold()), None)
            if found:
                mapping[field] = found
                break
    return {'headers': headers, 'rows': entries, 'mapping': mapping, 'filename': text(file.filename)}


def mapped(entry, mapping):
    def get(key):
        return text(entry.get(mapping.get(key, ''), ''))
    return {'name': get('name'), 'category': get('category'), 'package': canonical_package(get('package')),
            'value': get('value'), 'quantity': get('quantity'), 'per_board': get('per_board'),
            'designators': get('designators'), 'box': get('box').upper(), 'cell': get('cell').upper(),
            'mode': {'精确':'exact','估算':'estimate','未知':'unknown'}.get(get('mode'), get('mode') or 'exact'),
            'supplier_code': get('supplier_code').upper(),
            'source_package':get('source_package'), 'notes': get('notes'),
            'specs': {k: get(k) for k in ('耐压','介质','精度','功率','额定电流') if get(k)}}


def validate_rows(con, data, kind):
    need(isinstance(data.get('rows'), list) and 0 < len(data['rows']) <= MAX_ROWS, '导入数据行数无效')
    need(isinstance(data.get('mapping'), dict), '请设置列对应关系')
    entries = []
    seen_slots = set()
    existing_parts = rows(con, 'SELECT * FROM parts')
    for index, raw in enumerate(data['rows'], start=2):
        need(isinstance(raw, dict), f'第 {index} 行格式无效')
        entry = mapped(raw, data['mapping'])
        errors = []
        if not entry['name']:
            errors.append('缺少名称')
        try:
            if kind == 'bom':
                entry['per_board'] = integer(entry['per_board'], '单板用量', 1, 1000000)
            else:
                need(entry['category'], '缺少类别')
                need(entry['mode'] in ('exact','estimate','unknown'), '数量方式无效')
                entry['quantity'] = None if entry['mode'] == 'unknown' else integer(entry['quantity'], '数量')
                if entry['box'] or entry['cell']:
                    need(entry['box'] and entry['cell'], '盒号与格号需要同时填写')
                    m = re.fullmatch(r'([A-T])(\d{1,2})', entry['cell'])
                    need(m, '格号应为 A1、B2 等')
                    slot = con.execute('''SELECT sl.id FROM slots sl JOIN boxes b ON b.id=sl.box_id
                        WHERE b.code=? AND sl.row_no=? AND sl.col_no=?''', (entry['box'], ord(m[1])-65, int(m[2])-1)).fetchone()
                    need(slot, '盒子或格子不存在，请先建盒')
                    entry['slot_id'] = slot['id']
                    need(slot['id'] not in seen_slots, '文件中多个器件指向同一格')
                    seen_slots.add(slot['id'])
                    need(not con.execute('SELECT 1 FROM stock WHERE slot_id=?', (slot['id'],)).fetchone(), '该格已有记录，批量导入不覆盖已有库存')
                else:
                    need(entry['quantity'] in (0, None), '数量大于零时必须指定盒号和格号')
                duplicate = next((p for p in existing_parts if p['name'].casefold() == entry['name'].casefold() and p['category'] == entry['category'] and p['package'].casefold() == entry['package'].casefold() and p['value'] == entry['value'] and json.loads(p['specs']) == entry['specs']), None)
                if duplicate:
                    need(not duplicate['archived'], '同档案器件已归档，请先恢复')
                    entry['reuse_id'] = duplicate['id']
        except Problem as e:
            errors.append(e.message)
        source_row=raw.get('原表行号','') if data.get('format')=='easyeda_bom' else ''
        entry['row_no']=int(source_row) if re.fullmatch(r'\d{1,6}',str(source_row)) else index
        entry['errors'] = errors
        entries.append(entry)
    if kind=='bom':review_bom(entries)
    return entries


def import_inventory(con, data):
    entries = validate_rows(con, data, 'inventory')
    need(not any(e['errors'] for e in entries), '存在错误行，请修正后重新导入')
    ids = []
    for entry in entries:
        pid = entry.get('reuse_id')
        if not pid:
            # Reuse within this import too, so two locations do not create duplicate part identities.
            found = con.execute('SELECT * FROM parts WHERE name=? AND category=? AND package=? AND value=? AND specs=? AND archived=0',
                                (entry['name'],entry['category'],entry['package'],entry['value'],json.dumps(entry['specs'],ensure_ascii=False))).fetchone()
            pid = found['id'] if found else create_part(con, entry)
        if entry.get('slot_id'):
            set_stock(con, {**entry, 'part_id':pid,'action':'count','note':'批量导入初始库存'})
        ids.append(pid)
    return len(ids)
