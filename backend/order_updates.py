"""Reopen or reimport an existing order without creating duplicate purchase rows."""
import hashlib
import json
from .db import rows, uid, now
from .domain import need, text, create_part, product_name, save_product_name

PREFIX='lcsc-order:'


def revision(order):
    return hashlib.sha256(json.dumps(order,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def get_order(con,order_id):
    row=con.execute('SELECT value FROM settings WHERE key=?',(PREFIX+order_id,)).fetchone()
    need(row,'未找到已导入订单',404)
    return json.loads(row['value'])


def save_order(con,order):
    con.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(order,ensure_ascii=False),PREFIX+order['order_id']))


def unarchive_if_pending(con,order):
    if not order.get('archived'):return False
    pending=any(con.execute("SELECT 1 FROM purchases WHERE id=? AND status NOT IN ('received','cancelled')",(pid,)).fetchone() for pid in order['purchase_ids'])
    if not pending:return False
    order['archived']=False
    order.pop('archived_at',None)
    return True


def archive_order(con,order_id,archived):
    need(type(archived) is bool,'请指定是否归档订单')
    order=get_order(con,order_id)
    if archived:
        purchases=[con.execute('SELECT status FROM purchases WHERE id=?',(pid,)).fetchone() for pid in order['purchase_ids']]
        need(purchases and all(p and p['status'] in ('received','cancelled') for p in purchases),'订单还有未完成采购，请先完成验收或取消剩余采购项')
    if bool(order.get('archived'))!=archived:
        order['archived']=archived
        if archived:order['archived_at']=now()
        else:order.pop('archived_at',None)
        save_order(con,order)
    return {'order_id':order_id,'archived':archived}


def existing_items(con,order):
    result={}
    for pid in order['purchase_ids']:
        p=con.execute('SELECT pu.*,p.supplier_code,p.name,p.category,p.package,p.value,p.specs,p.notes part_notes FROM purchases pu JOIN parts p ON pu.part_id=p.id WHERE pu.id=?',(pid,)).fetchone()
        if p:
            p=dict(p)
            need(p['supplier_code'] not in result,'订单内商城编号重复，请先核对原采购记录')
            result[p['supplier_code']]=p
    return result


def reopen_order(con,order_id):
    order=get_order(con,order_id);lines=[]
    for index,p in enumerate(existing_items(con,order).values(),1):
        saved=order.get('items',{}).get(p['id'],{})
        override=con.execute('SELECT value FROM settings WHERE key=?',('product-name:'+p['part_id'],)).fetchone()
        lines.append({'source_row':saved.get('source_row',index),'supplier_code':p['supplier_code'],
            'name':p['name'],'description':saved.get('description') or (json.loads(override['value']) if override else product_name({'name':p['name'],'notes':p['part_notes']})),
            'brand':saved.get('brand',''),'unit_price':saved.get('unit_price',''),
            'package':p['package'],'category':p['category'],'value':p['value'],'specs':json.loads(p['specs']),
            'quantity':p['quantity'],'shipped':order['shipped'].get(p['id'])})
    return {'order_id':order_id,'filename':order['filename'],'lines':lines,'update_existing':True,'expected_revision':revision(order)}


def technical_changed(part,entry):
    return any(part[k]!=entry[k] for k in ('name','category','package','value')) or json.loads(part['specs'])!=entry['specs']


def update_order(con,checked,data):
    order=get_order(con,checked['order_id'])
    need(data.get('expected_revision')==revision(order),'订单已被修改，请重新打开或重新上传后检查',409)
    owned=existing_items(con,order);added=0;updated=0
    for entry in checked['lines']:
        previous=owned.get(entry['supplier_code'])
        if previous:
            need(previous['status']!='cancelled','已取消的采购项不能通过重新导入恢复')
            need(entry['quantity']>=previous['received'],'订购数量不能小于已经入库的数量')
            part_id=previous['part_id'];identity=previous['id']
            if technical_changed(previous,entry):create_part(con,entry,part_id)
            con.execute('UPDATE purchases SET quantity=?,status=? WHERE id=?',
                (entry['quantity'],'received' if previous['received']==entry['quantity'] else 'partial' if previous['received'] else 'ordered',identity))
            updated+=1
        else:
            part_id=entry['reuse_id']
            if not part_id:
                part_id=create_part(con,{**entry,'notes':'\n'.join(filter(None,[entry['brand'],entry['description'],'来自立创订单 '+checked['order_id']]))})
            identity=uid();order['purchase_ids'].append(identity);added+=1
            notes='\n'.join(filter(None,['订单 '+checked['order_id'],'商城编号 '+entry['supplier_code'],entry['unit_price']]))
            con.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?)',(identity,part_id,None,entry['quantity'],0,'ordered','立创商城',notes,now()))
        if entry['description']:save_product_name(con,part_id,entry['description'])
        order.setdefault('items',{})[identity]={k:entry[k] for k in ('source_row','supplier_code','description','brand','unit_price')}
        order['shipped'][identity]=entry['shipped']
    order['filename']=checked['filename'];order['updated_at']=now()
    unarchive_if_pending(con,order)
    save_order(con,order)
    return {'order_id':order['order_id'],'count':updated+added,'updated':updated,'added':added,'total_quantity':checked['total_quantity']}
