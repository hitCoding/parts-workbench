"""EasyEDA/LCSC BOM adapters and source metadata, without changing the stock schema."""
import json
import re
from .domain import canonical_package, text

SOURCE_PREFIX='bom-source:'
IGNORED_COLUMNS={'manufacturer part','manufacturer part number','mpn','manufacturer','厂家型号','制造商料号','制造商型号','制造商','厂家','品牌'}
BOM_ALIASES={
    'per_board':('quantity','qty','数量','单板用量','单板数量'),
    'designators':('designator','designators','位号'),
    'package':('footprint','package','封装'),
    'name':('comment','名称','器件名称','name','value','数值'),
    'value':('value','数值','参数值'),
    'supplier_code':('supplier part','supplier part number','lcsc part','lcsc part #','lcsc','商城编号','商品编号','供应商料号','立创编号'),
}


def columns_for(headers):
    found={}
    for key,aliases in BOM_ALIASES.items():
        for alias in aliases:
            column=next((h for h in headers if h.casefold()==alias),None)
            if column:
                found[key]=column;break
    return found


def is_eda_bom(headers):
    columns=columns_for(headers)
    return all(k in columns for k in ('per_board','designators','package')) and bool(columns.get('name')) and bool(columns.get('supplier_code') or any(h.casefold() in IGNORED_COLUMNS for h in headers))


def bom_package(value):
    package=canonical_package(value)
    # EasyEDA appends land-pattern dimensions to these explicit package/pin names.
    base=package.split('_',1)[0]
    if re.fullmatch(r'(?:[RCL](?:0201|0402|0603|0805|1206|1210|2010|2512)|(?:SOT|SOD|SOIC|SOP|SSOP|TSSOP|MSOP|QFN|DFN|LQFP|TQFP)-\d+(?:-\d+)?)',base):
        return canonical_package(base)
    return package


def bom_category(package,value,designators):
    raw=text(package).upper()
    prefix=re.match(r'^([RCL])(?:0201|0402|0603|0805|1206|1210|2010|2512)(?:_|$)',raw)
    if prefix:return {'R':'电阻','C':'电容','L':'电感'}[prefix[1]]
    if re.fullmatch(r'\d+(?:\.\d+)?\s*[pnuμµm]?F',value,re.I):return '电容'
    if re.fullmatch(r'\d+(?:\.\d+)?\s*[nuμµm]?H',value,re.I):return '电感'
    if re.fullmatch(r'\d+(?:\.\d+)?\s*[kKMm]?[ΩΩ]',value):return '电阻'
    if re.match(r'^(?:HDR|CONN|FPC|FFC|USB)[-_]',raw):return '连接器'
    refs=[s for s in re.split(r'[,，;；\s]+',designators.upper()) if s]
    if refs and all(re.fullmatch(r'[RCL]\d+',s) for s in refs) and len({s[0] for s in refs})==1:
        return {'R':'电阻','C':'电容','L':'电感'}[refs[0][0]]
    return ''


def adapt_eda_bom(table,header_index,sheet,filename):
    original_headers=list(table[header_index])
    while original_headers and not original_headers[-1]:original_headers.pop()
    headers=[h for h in original_headers if h.casefold() not in IGNORED_COLUMNS]
    columns=columns_for(headers)
    generated=('识别类别','规范封装','识别数值','原始封装','原表行号')
    from .domain import need
    need(not any(h in headers for h in generated),'BOM 包含保留的识别列名，请调整源文件列名')
    entries=[]
    for number,row in enumerate(table[header_index+1:],header_index+2):
        if not any(row):continue
        entry={h:v for h,v in zip(original_headers,row+['']*max(0,len(original_headers)-len(row))) if h in headers}
        def get(field):return text(entry.get(columns.get(field,''),''),2000)
        # Ignore purely decorative/footer rows; a part with missing quantity still gets validated.
        if not any(get(k) for k in ('name','supplier_code','designators')):continue
        value=get('value');description=get('name')
        category=bom_category(get('package'),value or description,get('designators'))
        if not value and category in ('电阻','电容','电感'):
            if re.fullmatch(r'[\d.]+\s*[pnuμµmkKMRr]?(?:[FfHhΩΩ])?|\d+[RrKkMm]\d+',description):value=description
        entry.update({'识别类别':category,'规范封装':bom_package(get('package')),'识别数值':value,
                      '原始封装':get('package'),'原表行号':str(number)})
        entries.append(entry)
    mapping={k:v for k,v in columns.items() if k not in ('value','package')}
    mapping.update({'category':'识别类别','package':'规范封装','value':'识别数值','source_package':'原始封装'})
    project_name=''
    for row in table[:header_index]:
        for i,cell in enumerate(row[:-1]):
            if re.sub(r'[\s:：]','',cell).casefold() in ('projecttitle','项目名称','工程名称'):
                project_name=next((v for v in row[i+1:] if v),'')
    return {'headers':headers+list(generated),'rows':entries,'mapping':mapping,'filename':text(filename),
            'format':'easyeda_bom','sheet':sheet,'header_row':header_index+1,'project_name':project_name,
            'ignored_columns':[h for h in original_headers if h.casefold() in IGNORED_COLUMNS]}


def review_bom(entries):
    for entry in entries:
        entry['warnings']=[]
        refs=[r for r in re.split(r'[,，;；\s]+',entry['designators']) if r]
        if refs and isinstance(entry['per_board'],int) and len(refs)!=entry['per_board']:
            entry['warnings'].append('位号个数与单板用量不同，请核对')


def save_bom_source(con,project_id,source,lines):
    record={'format':text(source.get('format')),'sheet':text(source.get('sheet')),'filename':text(source.get('filename')),
            'lines':{identity:{k:entry.get(k,[] if k=='warnings' else '') for k in ('supplier_code','source_package','row_no','warnings')} for identity,entry in lines}}
    con.execute('INSERT INTO settings(key,value) VALUES(?,?)',(SOURCE_PREFIX+project_id,json.dumps(record,ensure_ascii=False)))


def load_bom_source(con,project_id):
    record=con.execute('SELECT value FROM settings WHERE key=?',(SOURCE_PREFIX+project_id,)).fetchone()
    return json.loads(record['value']) if record else {'lines':{}}


def supply_mode(line):
    mode=line.get('supply_mode')
    if mode in ('stock','omit','external'):return mode
    markers={'nc','dnp','不贴','不焊'}
    return 'omit' if any(text(line.get(key)).casefold() in markers for key in ('name','value')) else 'stock'
