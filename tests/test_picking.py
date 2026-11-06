import csv
import io
from test_workbench import client, ok, setup_stock, project


def create(c, rows, name='混合备料'):
    mapping={k:k for k in ('name','value','category','package','supplier_code','designators')}
    mapping['per_board']='qty'
    return ok(c.post('/api/projects',json={'name':name,'boards':1,'rows':rows,'mapping':mapping}))['id']


def resistor(qty=2):
    return dict(name='10 kΩ',value='10k',category='电阻',package='0603',qty=qty,designators=','.join(f'R{i+1}' for i in range(qty)))


def test_partial_output_then_external_and_omit_allow_only_inventory_issue(client):
    part,_,_=setup_stock(client,10)
    pid=create(client,[resistor(),dict(name='NC',qty=1),dict(name='样品模块',qty=1),dict(name='缺少器件',qty=1)])
    detail=ok(client.get('/api/projects/'+pid))
    assert detail['omitted']==1 and detail['unresolved']==2 and not detail['ready']
    plan=ok(client.get('/api/projects/'+pid+'/picking'))
    assert plan['pick_quantity']==2 and len(plan['pending'])==2 and len(plan['omitted'])==1
    assert client.post('/api/projects/'+pid+'/reserve').status_code==400
    sample=detail['lines'][2];missing=detail['lines'][3]
    ok(client.put('/api/bom/'+sample['id']+'/supply',json={'mode':'external','note':'工作台样品袋'}))
    ok(client.put('/api/bom/'+missing['id']+'/supply',json={'mode':'omit'}))
    detail=ok(client.get('/api/projects/'+pid))
    assert detail['ready'] and detail['stock_lines']==1 and detail['external']==1 and detail['omitted']==2
    plan=ok(client.get('/api/projects/'+pid+'/picking'))
    assert not plan['pending'] and plan['external'][0]['notes']=='工作台样品袋'
    assert ok(client.get('/api/state'))['parts'][0]['exact']==10
    ok(client.post('/api/projects/'+pid+'/reserve'))
    assert client.put('/api/bom/'+sample['id']+'/supply',json={'mode':'stock'}).status_code==400
    ok(client.post('/api/projects/'+pid+'/issue'))
    state=ok(client.get('/api/state'))
    assert next(p for p in state['parts'] if p['id']==part)['exact']==8
    assert len([m for m in state['movements'] if m['kind']=='项目领料'])==1
    assert ok(client.get('/api/projects/'+pid+'/picking'))['pick_quantity']==0


def test_unmatched_sample_modes_can_be_restored_and_survive_backup(client):
    pid=create(client,[dict(name='DNP',qty=1),dict(name='NCV123',qty=1,supplier_code='C123')])
    detail=ok(client.get('/api/projects/'+pid));dnp,sample=detail['lines']
    assert dnp['supply_mode']=='omit' and sample['supply_mode']=='stock'
    ok(client.put('/api/bom/'+dnp['id']+'/supply',json={'mode':'stock'}))
    ok(client.put('/api/bom/'+sample['id']+'/supply',json={'mode':'external','note':'实验室'}))
    assert ok(client.get('/api/projects/'+pid))['unresolved']==1
    backup=client.get('/api/backup')
    ok(client.post('/api/restore',files={'file':('backup.sqlite3',backup.content)},headers={'X-Confirm-Restore':'RESTORE'}))
    detail=ok(client.get('/api/projects/'+pid))
    assert detail['lines'][0]['supply_mode']=='stock'
    assert detail['lines'][1]['supply_mode']=='external' and detail['lines'][1]['supplier_code']=='C123'
    ok(client.put('/api/bom/'+sample['id']+'/supply',json={'mode':'stock'}))
    assert ok(client.get('/api/projects/'+pid))['unresolved']==2
    for mode in ('invalid',None,[]):
        assert client.put('/api/bom/'+sample['id']+'/supply',json={'mode':mode}).status_code==400


def test_picking_allocates_multiple_slots_once_and_respects_other_reservations(client):
    part,_,_=setup_stock(client,4)
    slots=ok(client.get('/api/state'))['boxes'][0]['slots']
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slots[1]['id'],'action':'count','quantity':4,'mode':'exact'}))
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slots[2]['id'],'action':'count','quantity':100,'mode':'estimate'}))
    other=project(client,quantity=3,boards=1)
    ok(client.post('/api/projects/'+other+'/reserve'))
    pid=create(client,[resistor(3),resistor(3)])
    plan=ok(client.get('/api/projects/'+pid+'/picking'))
    assert plan['pick_quantity']==5 and len(plan['pending'])==1 and plan['pending'][0]['missing']==1
    assert all(item['slot_id']!=slots[2]['id'] for item in plan['rows'])
    for slot in slots[:2]:assert sum(item['quantity'] for item in plan['rows'] if item['slot_id']==slot['id'])<=4
    assert ok(client.get('/api/state'))['parts'][0]['exact']==8
    csv_result=client.get('/api/projects/'+pid+'/picking.csv')
    rows=list(csv.reader(io.StringIO(csv_result.content.decode('utf-8-sig'))))
    assert sum(int(row[8]) for row in rows[1:] if row[0]=='库存取料')==5
    assert any(row[0]=='待处理' for row in rows[1:])


def test_all_external_or_unpopulated_can_export_without_empty_reservation(client):
    pid=create(client,[dict(name='nc',qty=1),dict(name='样品',qty=2)])
    sample=ok(client.get('/api/projects/'+pid))['lines'][1]
    ok(client.put('/api/bom/'+sample['id']+'/supply',json={'mode':'external'}))
    detail=ok(client.get('/api/projects/'+pid))
    assert detail['stock_lines']==0 and detail['unresolved']==0 and not detail['ready']
    assert client.post('/api/projects/'+pid+'/reserve').status_code==400
    plan=ok(client.get('/api/projects/'+pid+'/picking'))
    assert not plan['rows'] and not plan['pending'] and plan['external'][0]['demand']==2
    assert client.get('/api/projects/'+pid+'/picking.csv').status_code==200
    assert not ok(client.get('/api/state'))['movements']


def test_external_matched_duplicate_is_excluded_from_quantity_and_reservation(client):
    setup_stock(client,3)
    pid=create(client,[resistor(),resistor()])
    result=ok(client.get('/api/projects/'+pid))
    assert result['shortages'][0]['quantity']==1
    ok(client.put('/api/bom/'+result['lines'][1]['id']+'/supply',json={'mode':'external'}))
    assert ok(client.get('/api/projects/'+pid))['ready']
    plan=ok(client.get('/api/projects/'+pid+'/picking'))
    assert plan['pick_quantity']==2 and plan['external'][0]['demand']==2 and not plan['pending']
    ok(client.post('/api/projects/'+pid+'/reserve'))
    assert ok(client.get('/api/state'))['parts'][0]['reserved']==2
    ok(client.post('/api/projects/'+pid+'/release'))
    assert ok(client.get('/api/projects/'+pid))['lines'][1]['supply_mode']=='external'


def test_print_and_csv_escape_user_content_and_keep_pending_rows(client):
    pid=create(client,[dict(name='=1+1',qty=1),dict(name='<script>alert(1)</script>',qty=1)],name='<img src=x onerror=alert(1)>')
    response=client.get('/api/projects/'+pid+'/picking/print')
    assert response.status_code==200 and '<script>' not in response.text and '<img src=x' not in response.text
    assert '&lt;script&gt;' in response.text and '待处理' in response.text
    response=client.get('/api/projects/'+pid+'/picking.csv')
    rows=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert rows[1][3]=="'=1+1" and len(rows)==3
