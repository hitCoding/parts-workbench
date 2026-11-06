from fastapi.testclient import TestClient
from test_workbench import client,ok
from test_purchase_revision import order_received,inspect,correct


def archive(c,order_id,value=True):
    return c.put('/api/purchases/orders/'+order_id+'/archive',json={'archived':value})


def test_order_archive_and_restore_preserve_every_purchase_and_stock(client):
    pid,slots,original=order_received(client,10)
    order_id=original['order_id'];before=ok(client.get('/api/state'))
    assert not before['purchase_orders'][0].get('archived')
    ok(archive(client,order_id))
    archived=ok(client.get('/api/state'))
    saved=archived['purchase_orders'][0]
    assert saved['archived'] is True and saved['archived_at']
    for key in ('parts','boxes','projects','purchases','movements'):assert archived[key]==before[key]
    # Repeating the action does not change archive time or create business records.
    ok(archive(client,order_id));assert ok(client.get('/api/state'))==archived
    ok(archive(client,order_id,False))
    restored=ok(client.get('/api/state'))
    assert not restored['purchase_orders'][0]['archived']
    assert 'archived_at' not in restored['purchase_orders'][0]
    for key in ('parts','boxes','projects','purchases','movements'):assert restored[key]==before[key]


def test_pending_order_cannot_be_hidden_by_archive(client):
    pid,slots,original=order_received(client,8)
    before=ok(client.get('/api/state'))
    assert archive(client,original['order_id']).status_code==400
    assert ok(client.get('/api/state'))==before


def test_archive_requires_write_access_valid_order_and_boolean(client):
    pid,slots,original=order_received(client,10)
    assert archive(client,'DOES_NOT_EXIST').status_code==404
    assert archive(client,original['order_id'],'false').status_code==400
    ok(client.post('/api/users',json={'username':'reader','password':'test-readonly-password','role':'viewer'}))
    with TestClient(client.app) as reader:
        ok(reader.post('/api/auth/login',json={'username':'reader','password':'test-readonly-password'}))
        assert archive(reader,original['order_id']).status_code==403
    assert not ok(client.get('/api/state'))['purchase_orders'][0].get('archived')


def test_order_reimport_keeps_history_until_it_has_pending_work(client):
    pid,slots,original=order_received(client,10);order_id=original['order_id']
    ok(archive(client,order_id))
    edit=ok(client.get('/api/purchases/orders/'+order_id));edit['lines'][0]['description']='只修改商品名称'
    ok(client.post('/api/purchases/import/commit',json=edit))
    assert ok(client.get('/api/state'))['purchase_orders'][0]['archived']
    edit=ok(client.get('/api/purchases/orders/'+order_id));edit['lines'][0]['quantity']=12
    ok(client.post('/api/purchases/import/commit',json=edit))
    state=ok(client.get('/api/state'))
    assert not state['purchase_orders'][0]['archived']
    assert state['purchases'][0]['received']==10 and state['purchases'][0]['status']=='partial'


def test_correcting_archived_receipt_reopens_pending_order(client):
    pid,slots,original=order_received(client,10)
    ok(archive(client,original['order_id']))
    item=inspect(client,[pid])[0]
    item['allocations']=[{'slot_id':slots[2]['id'],'quantity':10}]
    ok(correct(client,item))
    assert ok(client.get('/api/state'))['purchase_orders'][0]['archived']
    item=inspect(client,[pid])[0];item['allocations'][0]['quantity']=8
    ok(correct(client,item))
    state=ok(client.get('/api/state'))
    assert not state['purchase_orders'][0]['archived']
    assert state['purchases'][0]['received']==8


def test_archive_state_is_in_sqlite_backups(client):
    pid,slots,original=order_received(client,10)
    ok(archive(client,original['order_id']))
    backup=client.get('/api/backup');assert backup.status_code==200
    ok(archive(client,original['order_id'],False))
    ok(client.post('/api/restore',files={'file':('archived.sqlite3',backup.content)},headers={'X-Confirm-Restore':'RESTORE'}))
    assert ok(client.get('/api/state'))['purchase_orders'][0]['archived']
