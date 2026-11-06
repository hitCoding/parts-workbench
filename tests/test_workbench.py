import csv
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.domain import canonical_value


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as c:
        assert c.post('/api/auth/setup',json={'username':'testadmin','password':'test-only-password'}).status_code==200
        yield c


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def setup_stock(c, quantity=10, mode='exact', name='10 kΩ'):
    bid=ok(c.post('/api/boxes',json={'code':'R01','name':'电阻盒','rows':2,'cols':3}))['id']
    pid=ok(c.post('/api/parts',json={'name':name,'category':'电阻','package':'0603','value':'10k','specs':{'精度':'1%','功率':'0.1W'}}))['id']
    state=ok(c.get('/api/state'));slot=state['boxes'][0]['slots'][0]['id']
    ok(c.post('/api/stock',json={'part_id':pid,'slot_id':slot,'quantity':quantity,'mode':mode,'action':'count'}))
    return pid,slot,bid


def project(c, quantity=2, boards=2, name='测试板', pkg='0603', specs=True, duplicate=False):
    entry={'名称':'10 kΩ','类别':'电阻','封装':pkg,'数值':'10k','用量':str(quantity)}
    if specs:entry.update({'精度':'1%','功率':'0.1W'})
    mapping={k:k for k in ['精度','功率']};mapping.update({'name':'名称','category':'类别','package':'封装','value':'数值','per_board':'用量'})
    payload={'name':name,'boards':boards,'rows':[entry,entry] if duplicate else [entry],'mapping':mapping}
    pid=ok(c.post('/api/projects',json=payload))['id']
    return pid


def test_stock_persists_and_archive_is_reversible(client):
    pid,slot,_=setup_stock(client)
    ok(client.post('/api/stock',json={'part_id':pid,'slot_id':slot,'action':'out','quantity':3}))
    state=ok(client.get('/api/state'))
    assert state['parts'][0]['available']==7
    assert state['movements'][0]['delta']==-3
    assert client.post(f'/api/parts/{pid}/archive',json={'archived':True}).status_code==400
    ok(client.post('/api/stock',json={'part_id':pid,'slot_id':slot,'action':'count','quantity':0}))
    ok(client.post(f'/api/parts/{pid}/archive',json={'archived':True}))
    ok(client.post(f'/api/parts/{pid}/archive',json={'archived':False}))
    assert ok(client.get('/api/state'))['parts'][0]['archived']==0
    store=client.app.state.store
    with TestClient(create_app(store.directory)) as restarted:
        ok(restarted.post('/api/auth/login',json={'username':'testadmin','password':'test-only-password'}))
        assert ok(restarted.get('/api/state'))['parts'][0]['id']==pid


def test_negative_fractional_quantity_and_collision_rollback(client):
    pid,slot,_=setup_stock(client)
    other=ok(client.post('/api/parts',json={'name':'1k','category':'电阻'}))['id']
    for payload in [dict(part_id=pid,slot_id=slot,action='out',quantity=20),dict(part_id=pid,slot_id=slot,quantity=1.5),dict(part_id=other,slot_id=slot,quantity=2)]:
        assert client.post('/api/stock',json=payload).status_code==400
    assert next(p for p in ok(client.get('/api/state'))['parts'] if p['id']==pid)['exact']==10
    assert len(ok(client.get('/api/state'))['movements'])==1


def test_reservations_prevent_double_use_and_issue_once(client):
    part,slot,_=setup_stock(client,6)
    p1=project(client,2,2)
    p2=project(client,2,2,name='另一个项目')
    ok(client.post(f'/api/projects/{p1}/reserve'))
    second=ok(client.get(f'/api/projects/{p2}'))
    assert second['shortages'][0]['quantity']==2
    assert client.post(f'/api/projects/{p2}/reserve').status_code==400
    assert client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'out','quantity':3}).status_code==400
    assert client.put(f'/api/projects/{p1}',json={'boards':3}).status_code==400
    ok(client.post(f'/api/projects/{p1}/issue'))
    assert client.post(f'/api/projects/{p1}/issue').status_code==400
    assert ok(client.get('/api/state'))['parts'][0]['exact']==2
    assert ok(client.get('/api/state'))['parts'][0]['reserved']==0


def test_duplicate_bom_lines_share_inventory(client):
    setup_stock(client,5)
    pid=project(client,3,1,duplicate=True)
    result=ok(client.get(f'/api/projects/{pid}'))
    assert result['lines'][0]['shortage']==0
    assert result['lines'][1]['shortage']==1
    assert result['shortages'][0]['quantity']==1
    assert client.post(f'/api/projects/{pid}/reserve').status_code==400


def test_unknown_and_estimated_stock_require_count(client):
    part,slot,_=setup_stock(client,100,'estimate')
    pid=project(client,1,1)
    assert ok(client.get(f'/api/projects/{pid}'))['lines'][0]['status']=='count'
    assert client.post(f'/api/projects/{pid}/reserve').status_code==400
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'count','quantity':None,'mode':'unknown'}))
    assert client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'out','quantity':1}).status_code==400
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'count','quantity':5,'mode':'exact'}))
    ok(client.post(f'/api/projects/{pid}/reserve'))
    assert client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'count','mode':'unknown'}).status_code==400


def test_matching_conservative_and_manual_confirmation(client):
    part,_,_=setup_stock(client)
    pid=project(client,pkg='0805')
    result=ok(client.get(f'/api/projects/{pid}'))
    line=result['lines'][0]
    assert line['match']=='review' and not result['ready']
    assert client.put(f"/api/bom/{line['id']}/match",json={'part_id':part,'confirmed':True}).status_code==400
    ok(client.put(f"/api/bom/{line['id']}/match",json={'part_id':part,'confirmed':True,'reason':'已核对测试项目数据手册'}))
    assert ok(client.get(f'/api/projects/{pid}'))['ready']
    assert canonical_value('0.1uF','电容')==canonical_value('100nF','电容')
    assert canonical_value('4K7','电阻')==canonical_value('4.7kΩ','电阻')


def test_partial_purchase_receipt_atomic(client):
    part,slot,_=setup_stock(client,0)
    pid=ok(client.post('/api/purchases',json={'part_id':part,'quantity':10}))['id']
    assert client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slot,'quantity':4}).status_code==400
    ok(client.post(f'/api/purchases/{pid}/order'))
    assert ok(client.get('/api/state'))['parts'][0]['exact']==0
    ok(client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slot,'quantity':4}))
    assert ok(client.get('/api/state'))['purchases'][0]['status']=='partial'
    assert client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slot,'quantity':7}).status_code==400
    ok(client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slot,'quantity':6}))
    assert ok(client.get('/api/state'))['parts'][0]['exact']==10
    assert client.post(f'/api/purchases/{pid}/receive',json={'slot_id':slot,'quantity':1}).status_code==400


def test_move_preserves_quantity_and_releases_source(client):
    part,slot,_=setup_stock(client,10)
    state=ok(client.get('/api/state'));source=state['parts'][0]['locations'][0]
    target=state['boxes'][0]['slots'][1]['id']
    ok(client.post('/api/stock/move',json={'stock_id':source['id'],'slot_id':target}))
    p=ok(client.get('/api/state'))['parts'][0]
    assert p['locations'][0]['slot_id']==target and p['exact']==10
    assert len(ok(client.get('/api/state'))['movements'])==3


def test_inventory_import_preview_atomic_no_overwrite(client):
    setup_stock(client)
    payload={'rows':[{'名称':'新件','类别':'芯片','数量':'3','盒号':'R01','格号':'A2'},{'名称':'错误件','类别':'芯片','数量':'4','盒号':'R01','格号':'A1'}],
             'mapping':{'name':'名称','category':'类别','quantity':'数量','box':'盒号','cell':'格号'}}
    assert ok(client.post('/api/import/preview',json=payload))['errors']==1
    assert client.post('/api/import/commit',json=payload).status_code==400
    assert len(ok(client.get('/api/state'))['parts'])==1
    payload['rows']=payload['rows'][:1]
    assert ok(client.post('/api/import/commit',json=payload))['count']==1
    assert len(ok(client.get('/api/state'))['parts'])==2


def test_csv_xlsx_parse_and_bom_preview(client):
    raw='名称,类别,封装,单板用量\n测试芯片,芯片,SOP-8,1\n'
    parsed=ok(client.post('/api/files/parse',files={'file':('bom.csv',raw.encode('gb18030'),'text/csv')}))
    assert parsed['mapping']['per_board']=='单板用量'
    assert ok(client.post('/api/import/preview',json={**parsed,'kind':'bom'}))['errors']==0
    from openpyxl import Workbook
    book=Workbook();book.active.append(['名称','数量']);book.active.append(['芯片',2]);stream=io.BytesIO();book.save(stream)
    parsed=ok(client.post('/api/files/parse',files={'file':('test.xlsx',stream.getvalue())}))
    assert parsed['rows'][0]['数量']=='2'
    assert client.post('/api/files/parse',files={'file':('bad.csv',b'a,a\n1,2')}).status_code==400


def test_backup_restore_rejects_corruption_and_saves_previous(client):
    part,slot,_=setup_stock(client,10)
    backup=client.get('/api/backup');assert backup.status_code==200
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'out','quantity':3}))
    uploaded={'file':('backup.sqlite3',backup.content)}
    assert ok(client.post('/api/restore/preview',files=uploaded))['counts']['parts']==1
    assert client.post('/api/restore',files=uploaded).status_code==400
    ok(client.post('/api/restore',files=uploaded,headers={'X-Confirm-Restore':'RESTORE'}))
    assert ok(client.get('/api/state'))['parts'][0]['exact']==10
    assert list(client.app.state.store.backups.glob('before-restore-*.sqlite3'))
    assert client.post('/api/restore/preview',files={'file':('bad.sqlite3',b'no')}).status_code==400
    assert ok(client.get('/api/state'))['parts'][0]['exact']==10


def test_simultaneous_project_reservations_do_not_overdraw(client):
    setup_stock(client,4)
    p1=project(client,2,2)
    p2=project(client,2,2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda pid:client.post(f'/api/projects/{pid}/reserve').status_code,[p1,p2]))
    assert sorted(statuses)==[200,400]
    part=ok(client.get('/api/state'))['parts'][0]
    assert part['available']==0 and part['reserved']==4


def test_origin_guard(client):
    assert client.post('/api/parts',json={'name':'x','category':'芯片'},headers={'Origin':'https://external.example'}).status_code==403
    assert not ok(client.get('/api/state'))['parts']


def test_part_edits_invalidate_draft_manual_matches(client):
    part,_,_=setup_stock(client)
    pid=project(client,pkg='0805')
    line=ok(client.get(f'/api/projects/{pid}'))['lines'][0]
    ok(client.put(f"/api/bom/{line['id']}/match",json={'part_id':part,'confirmed':True,'reason':'封装替代已核对'}))
    assert ok(client.get(f'/api/projects/{pid}'))['ready']
    ok(client.put(f'/api/parts/{part}',json={'name':'10 kΩ','category':'电阻','package':'0603','value':'10k','specs':{'精度':'5%','功率':'0.1W'}}))
    assert not ok(client.get(f'/api/projects/{pid}'))['ready']


def test_xlsx_numeric_zero_and_duplicate_location_preview(client):
    from openpyxl import Workbook
    book=Workbook();book.active.append(['名称','类别','数量']);book.active.append(['零库存','芯片',0]);stream=io.BytesIO();book.save(stream)
    parsed=ok(client.post('/api/files/parse',files={'file':('zero.xlsx',stream.getvalue())}))
    assert parsed['rows'][0]['数量']=='0'
    assert ok(client.post('/api/import/preview',json=parsed))['errors']==0


def test_foreign_schema_backup_is_rejected(client,tmp_path):
    setup_stock(client)
    path=tmp_path/'foreign.sqlite3'
    with sqlite3.connect(path) as con:con.execute('CREATE TABLE fake(id TEXT)')
    r=client.post('/api/restore/preview',files={'file':('foreign.sqlite3',path.read_bytes())})
    assert r.status_code==400
    assert len(ok(client.get('/api/state'))['parts'])==1
