import json
import sqlite3
import pytest
from test_workbench import client,ok,setup_stock,project
from test_procurement import parsed


def order_received(c,qty=8):
    _,_,_=setup_stock(c,0)
    draft=parsed(c);ok(c.post('/api/purchases/import/commit',json=draft))
    state=ok(c.get('/api/state'));purchase=state['purchases'][0];slots=state['boxes'][0]['slots']
    ok(c.post('/api/purchases/receive-batch',json={'items':[{'id':purchase['id'],'slot_id':slots[1]['id'],'quantity':qty}]}))
    return purchase['id'],slots,draft


def inspect(c,ids):return ok(c.post('/api/purchases/receipts/inspect',json={'ids':ids}))['items']


def correct(c,item):return c.post('/api/purchases/receipts/correct',json={'items':[item],'reason':'核对实物修正'})


def test_update_received_order_keeps_identity_receipt_and_stock(client):
    pid,slots,original=order_received(client,10)
    before=ok(client.get('/api/state'))
    edit=ok(client.get('/api/purchases/orders/'+original['order_id']))
    line=edit['lines'][0];line['description']='新的商品名称 托盘；后半不用于短名称';line['name']='修正型号';line['quantity']=12
    checked=ok(client.post('/api/purchases/import/preview',json=edit));assert checked['errors']==0
    result=ok(client.post('/api/purchases/import/commit',json=checked))
    assert (result['updated'],result['added'])==(1,0)
    after=ok(client.get('/api/state'));p=after['purchases'][0]
    assert p['id']==pid and p['received']==10 and p['quantity']==12 and p['status']=='partial'
    assert p['part_name']=='修正型号' and p['product_name']==line['description']
    assert after['parts'][0]['product_name']==line['description']
    assert after['parts'][0]['locations']==before['parts'][0]['locations']
    assert [{k:v for k,v in m.items() if k!='part_name'} for m in after['movements']]==[{k:v for k,v in m.items() if k!='part_name'} for m in before['movements']]
    assert (client.app.state.store.backups/result['previous_backup']).is_file()
    assert client.post('/api/purchases/import/commit',json=checked).status_code==409


def test_reupload_same_order_preserves_received_and_adds_only_new_skus(client):
    pid,slots,original=order_received(client,8)
    upload=parsed(client,qty='12个',shipped='12个')
    assert upload['already_imported'] and upload['update_existing']
    new={**upload['lines'][0],'supplier_code':'C54321','source_row':10,'name':'新物料'}
    upload['lines'].append(new)
    result=ok(client.post('/api/purchases/import/commit',json=upload))
    assert result['added']==1 and result['updated']==1
    state=ok(client.get('/api/state'));assert len(state['parts'])==3 and len(state['purchases'])==2
    existing=next(p for p in state['purchases'] if p['id']==pid)
    assert existing['received']==8 and existing['quantity']==12
    assert sum(p['exact'] for p in state['parts'])==8
    edit=ok(client.get('/api/purchases/orders/'+original['order_id']));edit['lines']=edit['lines'][1:]
    checked=ok(client.post('/api/purchases/import/preview',json=edit));assert checked['retained']==1
    ok(client.post('/api/purchases/import/commit',json=checked))
    assert len(ok(client.get('/api/state'))['purchases'])==2


def test_order_cannot_reduce_purchase_below_received(client):
    pid,slots,original=order_received(client,8)
    edit=ok(client.get('/api/purchases/orders/'+original['order_id']));edit['lines'][0]['quantity']=7;edit['lines'][0]['shipped']=7
    before=ok(client.get('/api/state'))
    checked=ok(client.post('/api/purchases/import/preview',json=edit));assert checked['errors']==1
    assert client.post('/api/purchases/import/commit',json=checked).status_code==400
    assert ok(client.get('/api/state'))==before


def test_receipt_correction_moves_only_received_portion_and_records_difference(client):
    pid,slots,_=order_received(client,8)
    p=ok(client.get('/api/state'))['purchases'][0]
    # Unrelated stock of the same part must remain untouched.
    ok(client.post('/api/intake/manual',json={'part_id':p['part_id'],'slot_id':slots[1]['id'],'quantity':3}))
    before=ok(client.get('/api/state'))
    item=inspect(client,[pid])[0]
    item['allocations']=[{'slot_id':slots[2]['id'],'quantity':6}]
    result=ok(correct(client,item));assert result['delta']==-2
    after=ok(client.get('/api/state'));part=next(p for p in after['parts'] if p['id']==before['purchases'][0]['part_id'])
    assert {s['slot_id']:s['quantity'] for s in part['locations']}=={slots[1]['id']:3,slots[2]['id']:6}
    assert after['purchases'][0]['received']==6
    original_ids={m['id'] for m in before['movements']};assert original_ids.issubset({m['id'] for m in after['movements']})
    assert sum(m['delta'] for m in after['movements'] if m['kind']=='验收修正')==-2
    # Stale browser saves are rejected and don't repeat the correction.
    assert correct(client,item).status_code==409
    assert ok(client.get('/api/state'))==after
    # Identical content saves are a no-op, never a second inbound receipt.
    unchanged=inspect(client,[pid])[0]
    assert ok(correct(client,unchanged))['count']==0
    assert ok(client.get('/api/state'))==after


def test_legacy_receipts_are_reconstructed_and_can_be_split_or_withdrawn(client):
    pid,slots,_=order_received(client,10)
    with client.app.state.store.transaction() as con:
        con.execute('DELETE FROM settings WHERE key=?',('purchase-receipts:'+pid,))
    item=inspect(client,[pid])[0];assert item['allocations']==[{'slot_id':slots[1]['id'],'quantity':10}]
    item['allocations']=[{'slot_id':slots[1]['id'],'quantity':3},{'slot_id':slots[2]['id'],'quantity':7}]
    ok(correct(client,item))
    state=ok(client.get('/api/state'));assert state['purchases'][0]['status']=='received'
    assert sum(p['exact'] for p in state['parts'])==10
    item=inspect(client,[pid])[0];item['allocations']=[];ok(correct(client,item))
    state=ok(client.get('/api/state'));assert state['purchases'][0]['status']=='ordered' and state['purchases'][0]['received']==0
    assert sum(p['exact'] for p in state['parts'])==0
    # Receiving again after correction updates the saved allocations, not legacy totals.
    ok(client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slots[2]['id'],'quantity':4}))
    assert inspect(client,[pid])[0]['allocations']==[{'slot_id':slots[2]['id'],'quantity':4}]


def test_receipt_collision_or_insufficient_stock_rolls_back_whole_batch(client):
    pid,slots,_=order_received(client,8)
    item=inspect(client,[pid])[0];item['allocations']=[{'slot_id':slots[0]['id'],'quantity':6}]
    before=ok(client.get('/api/state'))
    assert correct(client,item).status_code==400
    assert ok(client.get('/api/state'))==before
    part=before['purchases'][0]['part_id']
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slots[1]['id'],'quantity':7,'action':'out'}))
    item['allocations']=[]
    before=ok(client.get('/api/state'))
    assert correct(client,item).status_code==400
    assert ok(client.get('/api/state'))==before


def test_unknown_and_reserved_stock_prevent_unsafe_correction(client):
    pid,slots,_=order_received(client,8)
    state=ok(client.get('/api/state'));p=state['purchases'][0]
    ok(client.post('/api/stock',json={'part_id':p['part_id'],'slot_id':slots[1]['id'],'quantity':8,'mode':'estimate','action':'count'}))
    item=inspect(client,[pid])[0];item['allocations']=[]
    assert correct(client,item).status_code==400
    ok(client.post('/api/stock',json={'part_id':p['part_id'],'slot_id':slots[1]['id'],'quantity':8,'action':'count'}))
    pr=project(client,quantity=5,boards=1)
    lid=ok(client.get('/api/projects/'+pr))['lines'][0]['id']
    ok(client.put('/api/bom/'+lid+'/match',json={'part_id':p['part_id'],'confirmed':True,'reason':'测试确认同料'}))
    ok(client.post(f'/api/projects/{pr}/reserve'))
    before=ok(client.get('/api/state'))
    item['allocations']=[{'slot_id':slots[1]['id'],'quantity':4}]
    assert correct(client,item).status_code==400
    assert ok(client.get('/api/state'))==before
    # A pure move retains the reserved total and is safe.
    item['allocations']=[{'slot_id':slots[2]['id'],'quantity':8}]
    ok(correct(client,item))


def test_receipt_edit_cannot_over_receive_and_requires_reason(client):
    pid,slots,_=order_received(client,8)
    item=inspect(client,[pid])[0];item['allocations']=[{'slot_id':slots[1]['id'],'quantity':11}]
    assert correct(client,item).status_code==400
    item['allocations'][0]['quantity']=8
    assert client.post('/api/purchases/receipts/correct',json={'items':[item]}).status_code==400


@pytest.mark.parametrize('route',['/api/purchases/import/commit','/api/purchases/receipts/correct'])
def test_revision_backup_failure_keeps_everything(client,monkeypatch,route):
    pid,slots,original=order_received(client,8)
    if route.endswith('commit'):
        data=ok(client.get('/api/purchases/orders/'+original['order_id']));data['lines'][0]['description']='修改名称'
    else:
        item=inspect(client,[pid])[0];item['allocations']=[];data={'items':[item],'reason':'test'}
    before=ok(client.get('/api/state'))
    def fail(target):raise sqlite3.OperationalError('disk error')
    monkeypatch.setattr(client.app.state.store,'snapshot',fail)
    assert client.post(route,json=data).status_code==503
    assert ok(client.get('/api/state'))==before


def test_batch_correction_rolls_back_earlier_valid_rows(client):
    pid,slots,original=order_received(client,8)
    second=parsed(client,order_id='SO12345678901',code='C98765')
    ok(client.post('/api/purchases/import/commit',json=second))
    second_id=next(p['id'] for p in ok(client.get('/api/state'))['purchases'] if p['id']!=pid)
    ok(client.post('/api/purchases/receive-batch',json={'items':[{'id':second_id,'slot_id':slots[3]['id'],'quantity':4}]}))
    items=inspect(client,[pid,second_id]);items[0]['allocations'][0]['quantity']=5
    items[1]['allocations']=[{'slot_id':slots[0]['id'],'quantity':2}]
    before=ok(client.get('/api/state'))
    assert client.post('/api/purchases/receipts/correct',json={'items':items,'reason':'批量核对'}).status_code==400
    assert ok(client.get('/api/state'))==before
    assert [r['received'] for r in inspect(client,[pid,second_id])]==[8,4]


def test_receipt_version_changes_after_another_partial_receipt(client):
    pid,slots,_=order_received(client,4)
    item=inspect(client,[pid])[0];item['allocations'][0]['quantity']=3
    ok(client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slots[2]['id'],'quantity':2}))
    before=ok(client.get('/api/state'))
    assert correct(client,item).status_code==409
    assert ok(client.get('/api/state'))==before


def test_corrected_records_survive_backup_restore(client):
    pid,slots,_=order_received(client,8)
    item=inspect(client,[pid])[0];item['allocations'][0]['quantity']=6
    result=ok(correct(client,item))
    backup=client.app.state.store.backups/result['previous_backup']
    ok(client.post('/api/restore',files={'file':('before-correction.sqlite3',backup.read_bytes())},headers={'X-Confirm-Restore':'RESTORE'}))
    assert inspect(client,[pid])[0]['received']==8
    assert sum(p['exact'] for p in ok(client.get('/api/state'))['parts'])==8
