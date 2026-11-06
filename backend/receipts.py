"""Purchase receipt allocations and transactional corrections, including legacy receipts."""
import hashlib
import json
import sqlite3
from collections import defaultdict
from .db import rows, one, uid
from .domain import need, Problem, text, integer, set_stock, journal, available
from .order_updates import PREFIX as ORDER_PREFIX, save_order, unarchive_if_pending

PREFIX='purchase-receipts:'


def allocations(con,purchase):
    saved=con.execute('SELECT value FROM settings WHERE key=?',(PREFIX+purchase['id'],)).fetchone()
    if saved:
        value=json.loads(saved['value'])
        result=value['allocations']
        revision=value['revision']
    else:
        # Earlier versions used an eight-character purchase identity in receipt notes.
        matches=rows(con,'SELECT id FROM purchases WHERE substr(id,1,8)=?',(purchase['id'][:8],))
        need(len(matches)==1 or purchase['received']==0,'历史采购标识重复，无法安全还原验收，请使用库存盘点')
        result=defaultdict(int)
        for m in rows(con,'SELECT slot_id,delta FROM movements WHERE part_id=? AND note IN (?,?)',
                      (purchase['part_id'],'采购批量验收 '+purchase['id'][:8],'采购验收入库 '+purchase['id'][:8])):
            need(m['slot_id'] and m['delta'] is not None and m['delta']>0,'历史验收流水不完整，无法安全修改')
            result[m['slot_id']]+=m['delta']
        result=dict(result)
        revision=hashlib.sha256(json.dumps([purchase['id'],purchase['quantity'],purchase['received'],result],sort_keys=True).encode()).hexdigest()
    need(sum(result.values())==purchase['received'],'验收记录与已入库数量不一致，请先核对流水')
    return result,revision


def save_allocations(con,pid,result):
    con.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (PREFIX+pid,json.dumps({'revision':uid(),'allocations':result})))


def receive_purchase(con,purchase,slot_id,qty,note):
    current,_=allocations(con,purchase)
    set_stock(con,{'part_id':purchase['part_id'],'slot_id':slot_id,'quantity':qty,'action':'in','note':note})
    current[slot_id]=current.get(slot_id,0)+qty
    save_allocations(con,purchase['id'],current)
    received=purchase['received']+qty
    con.execute('UPDATE purchases SET received=?,status=? WHERE id=?',
                (received,'received' if received==purchase['quantity'] else 'partial',purchase['id']))


def inspect_receipts(con,identities):
    need(isinstance(identities,list) and 0<len(identities)<=5000,'请选择需要修改的采购项')
    result=[]
    for identity in dict.fromkeys(identities):
        purchase=one(con,'purchases',identity)
        need(purchase['status'] in ('ordered','partial','received'),'待购买或已取消的采购不能修改验收')
        current,revision=allocations(con,purchase)
        result.append({'id':identity,'revision':revision,'received':purchase['received'],'quantity':purchase['quantity'],
                       'allocations':[{'slot_id':slot,'quantity':qty} for slot,qty in current.items()]})
    return result


def correct_receipts(con,data):
    reason=text(data.get('reason'),500);need(reason,'请填写修改原因')
    items=data.get('items');need(isinstance(items,list) and 0<len(items)<=5000,'请选择需要修改的验收记录')
    changes=[];seen=set();deltas=defaultdict(int);part_ids=set()
    for item in items:
        need(isinstance(item,dict),'验收修改行无效')
        pid=text(item.get('id'));need(pid not in seen,'同一采购项不能重复修改');seen.add(pid)
        p=one(con,'purchases',pid)
        need(p['status'] in ('ordered','partial','received'),'采购状态已变更，请刷新')
        current,revision=allocations(con,p)
        need(item.get('revision')==revision,'验收记录已被修改，请关闭后重新打开',409)
        entries=item.get('allocations');need(isinstance(entries,list) and len(entries)<=5000,'存放明细无效')
        updated={}
        for entry in entries:
            need(isinstance(entry,dict),'存放明细无效')
            slot=text(entry.get('slot_id'));one(con,'slots',slot)
            need(slot not in updated,'同一采购项的格子不能重复，请合并数量')
            updated[slot]=integer(entry.get('quantity'),'修改后已入库数量',1)
        total=sum(updated.values());need(total<=p['quantity'],'修改后已入库数量不能超过采购数量')
        if updated==current:continue
        part=one(con,'parts',p['part_id']);need(not part['archived'],'器件已归档，请先恢复档案')
        for slot in current.keys()|updated.keys():
            deltas[p['part_id'],slot]+=updated.get(slot,0)-current.get(slot,0)
        changes.append((p,updated,total));part_ids.add(p['part_id'])
    # Validate final balances as a whole, so moving reserved stock between slots works.
    planned=[]
    for (part_id,slot),delta in deltas.items():
        if not delta:continue
        stock=con.execute('SELECT * FROM stock WHERE slot_id=?',(slot,)).fetchone()
        need(not stock or stock['part_id']==part_id,'目标格已有其他器件，请换一个空格或同器件格子')
        need(not stock or stock['mode']=='exact','相关格子数量为估算或未知，请先盘点为精确数量')
        before=stock['quantity'] if stock else 0
        after=before+delta
        need(after>=0,'原格库存不足，可能已经领用或移位；请核对实物与流水，移回原格后再修改')
        planned.append((stock,part_id,slot,before,after))
    for stock,part_id,slot,before,after in planned:
        if stock:con.execute('UPDATE stock SET quantity=? WHERE id=?',(after,stock['id']))
        else:con.execute('INSERT INTO stock VALUES(?,?,?,?,?)',(uid(),part_id,slot,after,'exact'))
        journal(con,part_id,slot,'验收修正',before,after,reason+'；采购项 '+','.join(p['id'] for p,_,_ in changes if p['part_id']==part_id))
    for part_id in part_ids:
        need(available(con,part_id)>=0,'修改会影响项目已占用库存，请先释放项目占用')
    for p,updated,total in changes:
        con.execute('UPDATE purchases SET received=?,status=? WHERE id=?',
                    (total,'received' if total==p['quantity'] else 'partial' if total else 'ordered',p['id']))
        save_allocations(con,p['id'],updated)
    changed_ids={p['id'] for p,_,_ in changes}
    if changed_ids:
        for record in rows(con,'SELECT value FROM settings WHERE key LIKE ?',(ORDER_PREFIX+'%',)):
            order=json.loads(record['value'])
            if changed_ids.intersection(order['purchase_ids']) and unarchive_if_pending(con,order):save_order(con,order)
    return {'count':len(changes),'delta':sum(deltas.values())}


def snapshot_before_change(store,prefix):
    target=store.backup_name(prefix)
    try:store.snapshot(target)
    except (OSError,sqlite3.Error):raise Problem('备份失败，未修改数据，请检查存储空间后重试',503)
    return target.name
