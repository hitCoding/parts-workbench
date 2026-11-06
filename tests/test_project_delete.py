import sqlite3
import pytest
from fastapi.testclient import TestClient
from test_workbench import client,ok,setup_stock,project


def remove(c,pid):
    return c.request('DELETE','/api/projects/'+pid,json={'confirm':True})


def test_delete_draft_keeps_inventory_purchases_and_restorable_backup(client):
    part,slot,_=setup_stock(client)
    pid=project(client,name='需要删除的项目')
    other=project(client,name='保留项目')
    purchase=ok(client.post('/api/purchases',json={'part_id':part,'project_id':pid,'quantity':5,'notes':'原采购备注'}))['id']
    ok(client.post(f'/api/purchases/{purchase}/order'))
    ok(client.post(f'/api/purchases/{purchase}/receive',json={'slot_id':slot,'quantity':2}))
    before=ok(client.get('/api/state'))
    result=ok(remove(client,pid))
    after=ok(client.get('/api/state'))
    assert [p['id'] for p in after['projects']]==[other]
    assert after['parts']==before['parts'] and after['boxes']==before['boxes'] and after['movements']==before['movements']
    kept=after['purchases'][0]
    assert kept['id']==purchase and kept['project_id'] is None
    assert (kept['status'],kept['quantity'],kept['received'])==('partial',5,2)
    assert '需要删除的项目' in kept['notes'] and '原采购备注' in kept['notes']
    with client.app.state.store.connect() as con:
        assert not con.execute('SELECT 1 FROM bom_lines WHERE project_id=?',(pid,)).fetchone()
        assert not con.execute('SELECT 1 FROM settings WHERE key=?',('bom-source:'+pid,)).fetchone()
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    backup=client.app.state.store.backups/result['previous_backup']
    assert backup.is_file() and result['purchases_preserved']==1
    ok(client.post('/api/restore',files={'file':('before-delete.sqlite3',backup.read_bytes())},headers={'X-Confirm-Restore':'RESTORE'}))
    assert ok(client.get('/api/projects/'+pid))['name']=='需要删除的项目'
    assert ok(client.get('/api/state'))['purchases'][0]['project_id']==pid


def test_delete_reserved_releases_only_that_projects_reservation(client):
    setup_stock(client,10)
    deleted=project(client,quantity=2,boards=2)
    remaining=project(client,quantity=1,boards=1,name='另一项目')
    ok(client.post(f'/api/projects/{deleted}/reserve'))
    ok(client.post(f'/api/projects/{remaining}/reserve'))
    before=ok(client.get('/api/state'))
    result=ok(remove(client,deleted))
    after=ok(client.get('/api/state'));stock=after['parts'][0]
    assert result['released']==4 and stock['exact']==10 and stock['reserved']==1 and stock['available']==9
    assert after['movements']==before['movements']
    assert ok(client.get('/api/projects/'+remaining))['status']=='reserved'


def test_delete_built_does_not_return_issued_stock_or_erase_history(client):
    setup_stock(client,10)
    pid=project(client,quantity=2,boards=2,name='已领料项目')
    ok(client.post(f'/api/projects/{pid}/reserve'));ok(client.post(f'/api/projects/{pid}/issue'))
    before=ok(client.get('/api/state'))
    assert before['parts'][0]['exact']==6
    ok(remove(client,pid))
    after=ok(client.get('/api/state'))
    assert after['parts']==before['parts'] and after['movements']==before['movements']
    assert any(m['kind']=='项目领料' and m['note']=='已领料项目' for m in after['movements'])
    assert client.get('/api/projects/'+pid).status_code==404
    assert remove(client,pid).status_code==404
    assert len(list(client.app.state.store.backups.glob('before-project-delete-*')))==1


def test_delete_requires_confirmation_and_write_access(client):
    setup_stock(client);pid=project(client)
    assert client.request('DELETE','/api/projects/'+pid,json={'confirm':False}).status_code==400
    ok(client.post('/api/users',json={'username':'reader','password':'test-readonly-password','role':'viewer'}))
    with TestClient(client.app) as reader:
        ok(reader.post('/api/auth/login',json={'username':'reader','password':'test-readonly-password'}))
        assert remove(reader,pid).status_code==403
    assert client.get('/api/projects/'+pid).status_code==200
    assert not list(client.app.state.store.backups.glob('before-project-delete-*'))


@pytest.mark.parametrize('error_type',[OSError,sqlite3.OperationalError])
def test_backup_failure_does_not_delete_any_project_data(client,monkeypatch,error_type):
    setup_stock(client);pid=project(client)
    before=ok(client.get('/api/state'))
    def fail_snapshot(target):raise error_type('test disk full')
    monkeypatch.setattr(client.app.state.store,'snapshot',fail_snapshot)
    assert remove(client,pid).status_code==503
    assert ok(client.get('/api/state'))==before
    assert client.get('/api/projects/'+pid).status_code==200
