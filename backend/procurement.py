"""LCSC exported order ingestion and receipts. Never turn shipped quantities into stock implicitly."""
import io
import json
import re
from decimal import Decimal
from fastapi import APIRouter, UploadFile, File
from .db import uid, now, rows, one
from .domain import need, Problem, text, integer, create_part, set_stock, canonical_package, canonical_value
from .imports import workbook_rows, MAX_UPLOAD, MAX_ROWS
from .order_updates import existing_items, reopen_order, revision, technical_changed, update_order, archive_order
from .receipts import receive_purchase, inspect_receipts, correct_receipts, snapshot_before_change

ORDER_PREFIX='lcsc-order:'


def quantity(value,allow_blank=False):
    value=text(value).replace(',','').replace('，','').strip()
    if not value and allow_blank:return None
    match=re.fullmatch(r'(\d+)(?:\.0+)?\s*(?:个|只|片|件|PCS|pcs|pc|PC)?',value)
    need(match,'数量无法识别，请填写整数或“100个”')
    return integer(match[1],'数量')


def infer(description):
    specs={}
    pattern=r'(?<![\w.])(\d+(?:\.\d+)?\s*(?:[pnuμµm]?F|[nuμµm]?H|[kKMm]?Ω))'
    match=re.search(pattern,description)
    value=match[1].replace(' ','').replace('μ','u').replace('µ','u') if match else ''
    if re.search(r'ESD|二极管',description,re.I):category='二极管'
    elif '沟道' in description:category='MOS管'
    elif '电阻' in description or 'Ω' in value:category='电阻'
    elif value.endswith('F'):category='电容'
    elif value.endswith('H'):category='电感'
    elif re.search(r'Type-C|接头|接插件|间距:|卡座|IPEX|接触|P数:',description,re.I):category='连接器'
    elif '开关' in description:category='开关'
    elif re.search(r'降压型|稳压器|芯片',description):category='芯片'
    else:category='其他'
    if category in ('电阻','电容','电感'):
        tolerance=re.search(r'±?\s*(\d+(?:\.\d+)?)\s*%',description)
        if tolerance:specs['精度']=tolerance[1]+'%'
    if category=='电阻':
        power=re.search(r'(\d+(?:\.\d+)?)\s*(mW|W)',description)
        if power:specs['功率']=format((Decimal(power[1])/(1000 if power[2]=='mW' else 1)).normalize(),'f')+'W'
    if category in ('电容','MOS管'):
        voltage=re.search(r'(\d+(?:\.\d+)?)\s*V',description)
        if voltage:specs['耐压']=voltage[1]+'V'
    if category=='电容':
        dielectric=re.search(r'\b(X[578][RS]|C0G|NP0|Y5V)\b',description,re.I)
        if dielectric:specs['介质']=dielectric[1].upper()
    if category=='电感':
        current=re.search(r'(\d+(?:\.\d+)?)\s*(mA|A)',description)
        if current:specs['额定电流']=format(Decimal(current[1])/(1000 if current[2]=='mA' else 1),'f')+'A'
    if category not in ('电阻','电容','电感'):value=''
    return category,value,specs


def parse_order(file):
    content=file.file.read(MAX_UPLOAD+1)
    need(len(content)<=MAX_UPLOAD,'订单文件不能超过 10 MB')
    tables=workbook_rows(content,file.filename or '',all_sheets=True)
    result=None;warnings=[]
    for sheet_name,table in tables:
        header_index=next((i for i,r in enumerate(table) if '商品编号' in r and '厂家型号' in r and '商品名称' in r),None)
        if header_index is None:continue
        header=table[header_index]
        def get(row,key):
            i=header.index(key) if key in header else -1
            return text(row[i],2000) if 0<=i<len(row) else ''
        order_id=''
        for row in table[:header_index]:
            for i,v in enumerate(row[:-1]):
                if re.sub(r'[\s：:]','',v)=='订单编号':order_id=text(row[i+1],80).upper()
        if not order_id:
            found=re.search(r'SO\d{8,40}',file.filename or '',re.I)
            if found:order_id=found[0].upper()
        lines=[]
        for index,row in enumerate(table[header_index+1:],start=header_index+2):
            code=get(row,'商品编号').upper()
            if not code:continue
            if not re.fullmatch(r'C\d+',code):
                warnings.append(f'原表第 {index} 行编号不是立创商品编号，未导入');continue
            skipped=get(row,'是否不发此货').strip().lower()
            if skipped in ('是','不发','true','1','yes'):
                warnings.append(f'{code} 标记不发此货，未导入');continue
            description=get(row,'商品名称');category,value,specs=infer(description)
            qty_key=next((k for k in ('订购数量（修改后）','订购数量(修改后)','订购数量','购买数量','数量') if k in header),'')
            qty=get(row,qty_key);shipped=get(row,'发货数量')
            lines.append({'source_row':index,'supplier_code':code,'name':get(row,'厂家型号'),'description':description,
                'brand':get(row,'品牌'),'package':canonical_package(get(row,'封装')),'category':category,'value':value,'specs':specs,
                'quantity':qty,'shipped':shipped,'unit_price':get(row,'商品单价')})
        if not lines:continue
        need(len(lines)<=MAX_ROWS,'订单明细最多 5000 行')
        need(result is None,'文件包含多份订单，请分别导出后导入')
        result={'order_id':order_id,'filename':text(file.filename,200),'sheet':sheet_name,'lines':lines,'warnings':warnings}
    need(result,'没有找到立创订单商品表头，请上传立创订单详情 XLS / XLSX / CSV')
    return result


def validate_order(con,data):
    order_id=text(data.get('order_id'),80).upper()
    need(re.fullmatch(r'[A-Z0-9_-]{3,80}',order_id),'请填写有效订单号（字母、数字、短横线或下划线）')
    imported=con.execute('SELECT value FROM settings WHERE key=?',(ORDER_PREFIX+order_id,)).fetchone()
    updating=bool(imported) and data.get('update_existing') is True
    order=json.loads(imported['value']) if imported else None
    owned=existing_items(con,order) if updating else {}
    raw_lines=data.get('lines')
    need(isinstance(raw_lines,list) and 0<len(raw_lines)<=MAX_ROWS,'订单明细不能为空，最多 5000 行')
    existing=rows(con,'SELECT * FROM parts')
    entries=[];seen=set()
    for index,raw in enumerate(raw_lines,1):
        need(isinstance(raw,dict),'订单行格式无效')
        entry={k:text(raw.get(k),2000 if k=='description' else 200) for k in ('supplier_code','name','package','category','value','brand','description','unit_price')}
        entry['supplier_code']=entry['supplier_code'].upper();entry['package']=canonical_package(entry['package'])
        entry.update({'source_row':raw.get('source_row',index),'reuse_id':None,'errors':[],'warnings':[],'specs':{}})
        try:
            need(re.fullmatch(r'C\d{1,30}',entry['supplier_code']),'缺少有效商城编号')
            need(entry['supplier_code'] not in seen,'订单内商品编号重复，请先合并或确认源文件')
            seen.add(entry['supplier_code'])
            need(entry['name'] and entry['category'],'型号与类别不能为空')
            entry['quantity']=quantity(raw.get('quantity'));need(entry['quantity']>0,'订购数量必须大于零')
            entry['shipped']=quantity(raw.get('shipped'),allow_blank=True)
            need(entry['shipped'] is None or entry['shipped']<=entry['quantity'],'发货数量大于订购数量，请核对')
            need(isinstance(raw.get('specs',{}),dict),'参数格式无效')
            entry['specs']={text(k,50):text(v,100) for k,v in raw.get('specs',{}).items() if text(k) and text(v)}
            candidates=[p for p in existing if p['supplier_code'].upper()==entry['supplier_code']]
            need(len(candidates)<=1,'已有多份相同商城编号档案，请先整理')
            if candidates:
                p=candidates[0];need(not p['archived'],'该商城编号已归档，请先恢复')
                previous=owned.get(entry['supplier_code'])
                if previous:
                    need(previous['status']!='cancelled','原采购项已取消，不能重新导入恢复')
                    need(entry['quantity']>=previous['received'],f"订购数量不能小于已入库 {previous['received']} 个")
                    if technical_changed(p,entry):
                        need(not con.execute('SELECT 1 FROM reservations WHERE part_id=?',(p['id'],)).fetchone(),'器件已被项目占用，请先释放占用再修改参数')
                        entry['warnings'].append('型号 / 参数将同步更新档案，草稿项目需要重新核料')
                    entry['warnings'].append(f"更新原采购项，保留已入库 {previous['received']} 个")
                else:
                    need(p['package']==entry['package'],'商城编号已存在，但封装不同，请核对')
                    if p['category']==entry['category'] and p['value'] and entry['value']:
                        need(canonical_value(p['value'],p['category'])==canonical_value(entry['value'],entry['category']),'商城编号已存在，但数值不同，请核对')
                    for key,value in entry['specs'].items():
                        previous_value=json.loads(p['specs']).get(key)
                        need(not previous_value or previous_value.casefold()==value.casefold(),f'已有档案的{key}不同，请先核对档案')
                entry['reuse_id']=p['id']
                if not previous:entry['warnings'].append('复用已有档案，保留原参数')
            if entry['category']=='其他':entry['warnings'].append('类别未识别，请按实物修改或保留“其他”')
            if entry['category']=='电容' and '介质' not in entry['specs']:entry['warnings'].append('订单未提供介质，BOM 自动匹配前需补充')
        except Problem as exc:entry['errors'].append(exc.message)
        entries.append(entry)
    return {'order_id':order_id,'filename':text(data.get('filename'),200),'lines':entries,'already_imported':bool(imported),
        'update_existing':updating,'expected_revision':data.get('expected_revision',revision(order) if order else ''),
        'retained':sum(code not in seen for code in owned),
        'errors':sum(bool(e['errors']) for e in entries),'total_quantity':sum(e.get('quantity',0) for e in entries if not e['errors'])}


def order_records(con):
    return [json.loads(r['value']) for r in rows(con,'SELECT value FROM settings WHERE key LIKE ? ORDER BY key DESC',(ORDER_PREFIX+'%',))]


def purchase_routes(store):
    router=APIRouter(prefix='/api')

    @router.post('/purchases/import/parse')
    def parse(file:UploadFile=File(...)):
        parsed=parse_order(file)
        with store.transaction() as con:
            if con.execute('SELECT 1 FROM settings WHERE key=?',(ORDER_PREFIX+text(parsed['order_id']).upper(),)).fetchone():parsed['update_existing']=True
            checked=validate_order(con,parsed)
        return {**parsed,**checked}

    @router.post('/purchases/import/preview')
    def preview(data:dict):
        with store.transaction() as con:return validate_order(con,data)

    @router.get('/purchases/orders/{order_id}')
    def edit_order(order_id:str):
        with store.transaction() as con:
            data=reopen_order(con,order_id)
            return validate_order(con,data)

    @router.put('/purchases/orders/{order_id}/archive')
    def set_order_archive(order_id:str,data:dict):
        with store.transaction() as con:return archive_order(con,order_id,data.get('archived'))

    @router.post('/purchases/import/commit')
    def commit(data:dict):
        with store.lock:
            backup=snapshot_before_change(store,'before-order-update' if data.get('update_existing') is True else 'before-order-import')
            with store.transaction() as con:
                checked=validate_order(con,data)
                need(not checked['already_imported'] or checked['update_existing'],'该订单已导入，请直接验收现有采购项，避免重复入库',409)
                need(not checked['errors'],'订单有错误行，请修正后重新预览')
                if checked['update_existing']:return {**update_order(con,checked,data),'previous_backup':backup}
                need(not data.get('update_existing'),'原订单不存在，请重新检查后导入',409)
                identities=[];shipped={};items={}
                for entry in checked['lines']:
                    part_id=entry['reuse_id']
                    if not part_id:
                        part_id=create_part(con,{**entry,'product_name':entry['description'],'notes':'\n'.join(filter(None,[entry['brand'],entry['description'],'来自立创订单 '+checked['order_id']]))})
                    identity=uid();identities.append(identity);shipped[identity]=entry['shipped']
                    items[identity]={k:entry[k] for k in ('source_row','supplier_code','description','brand','unit_price')}
                    notes='\n'.join(filter(None,['订单 '+checked['order_id'],'商城编号 '+entry['supplier_code'],entry['unit_price']]))
                    con.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?)',(identity,part_id,None,entry['quantity'],0,'ordered','立创商城',notes,now()))
                order={'order_id':checked['order_id'],'filename':checked['filename'],'purchase_ids':identities,'shipped':shipped,'items':items,'created_at':now()}
                con.execute('INSERT INTO settings VALUES(?,?)',(ORDER_PREFIX+checked['order_id'],json.dumps(order,ensure_ascii=False)))
                return {'order_id':checked['order_id'],'count':len(identities),'total_quantity':checked['total_quantity']}

    @router.post('/purchases/receive-batch')
    def receive_batch(data:dict):
        items=data.get('items');need(isinstance(items,list) and 0<len(items)<=5000,'请勾选需要验收的物料')
        seen=set()
        with store.transaction() as con:
            total=0
            for item in items:
                need(isinstance(item,dict),'验收行格式无效')
                identity=text(item.get('id'));need(identity not in seen,'同一采购项不能重复验收');seen.add(identity)
                purchase=one(con,'purchases',identity)
                need(purchase['status'] in ('ordered','partial'),'采购项已经完成、取消或尚未购买，请刷新')
                qty=integer(item.get('quantity'),'验收数量',1)
                need(qty<=purchase['quantity']-purchase['received'],'验收数量超过待入库数量')
                receive_purchase(con,purchase,item.get('slot_id'),qty,'采购批量验收 '+identity[:8])
                total+=qty
        return {'count':len(items),'quantity':total}

    @router.post('/purchases/receipts/inspect')
    def receipt_details(data:dict):
        with store.transaction() as con:return {'items':inspect_receipts(con,data.get('ids'))}

    @router.post('/purchases/receipts/correct')
    def receipt_correction(data:dict):
        with store.lock:
            backup=snapshot_before_change(store,'before-receipt-correction')
            with store.transaction() as con:return {**correct_receipts(con,data),'previous_backup':backup}

    @router.post('/intake/manual')
    def manual(data:dict):
        with store.transaction() as con:
            part_id=data.get('part_id')
            if part_id:
                part=one(con,'parts',part_id);need(not part['archived'],'器件已归档')
            else:
                part_data=data.get('part');need(isinstance(part_data,dict),'请填写新器件信息')
                code=text(part_data.get('supplier_code'),100)
                need(not code or not con.execute('SELECT 1 FROM parts WHERE supplier_code=? COLLATE NOCASE',(code,)).fetchone(),'商城编号已存在，请切换到已有器件')
                part_id=create_part(con,part_data)
            qty=integer(data.get('quantity'),'本次入库数量',1)
            set_stock(con,{'part_id':part_id,'slot_id':data.get('slot_id'),'quantity':qty,'action':'in','note':text(data.get('note'),2000) or '手动入库'})
            return {'id':part_id,'quantity':qty}

    return router
