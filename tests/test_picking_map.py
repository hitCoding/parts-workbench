import re
from backend.picking import picking_plan
from backend.picking_map import location_map
from test_workbench import client, ok, setup_stock, project
from test_picking import create, resistor


def sheets(client,pid):
    with client.app.state.store.transaction() as con:
        return location_map(con,picking_plan(con,pid))


def test_map_preserves_whole_layout_and_aggregates_only_allocated_bom_lines(client):
    part,slot,bid=setup_stock(client,10)
    detail=ok(client.get('/api/state'))
    spare=detail['boxes'][0]['slots'][1]['id']
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':spare,'action':'count','quantity':30,'mode':'exact'}))
    pid=create(client,[resistor(),resistor(3),resistor(),dict(name='NC',qty=1)])
    lines=ok(client.get('/api/projects/'+pid))['lines']
    ok(client.put('/api/bom/'+lines[2]['id']+'/supply',json={'mode':'external','note':'样品袋'}))
    before=ok(client.get('/api/state'))
    maps=sheets(client,pid)
    assert len(maps)==1 and maps[0]['id']==bid
    assert [c['label'] for c in maps[0]['cells']]==['A1','A2','A3','B1','B2','B3']
    picked=[c for c in maps[0]['cells'] if c['highlight']=='pick']
    assert sum(c['take'] for c in picked)==5
    assert sum(len(c['allocations']) for c in picked)==2
    assert all(c['empty'] for c in maps[0]['cells'][2:])
    response=client.get('/api/projects/'+pid+'/picking/map')
    assert response.status_code==200 and '打印 / 保存 PDF' in response.text
    assert '样品袋' in response.text and '不贴装' in response.text
    assert sum(map(int,re.findall(r'data-take="(\d+)"',response.text)))==5
    after=ok(client.get('/api/state'))
    for key in ('parts','boxes','movements','projects'):assert before[key]==after[key]


def test_map_marks_uncertain_locations_as_reference_and_keeps_unmatched_items(client):
    setup_stock(client,100,'estimate')
    pid=create(client,[resistor(),dict(name='缺少模块',qty=1)])
    result=sheets(client,pid)
    cell=result[0]['cells'][0]
    assert cell['highlight']=='reference' and cell['take']==0
    assert cell['mode']=='estimate' and cell['quantity']==100
    response=client.get('/api/projects/'+pid+'/picking/map')
    assert '仅参考 · 暂不取料' in response.text and '待盘点' not in cell['title']
    assert '缺少模块' in response.text and '有 2 项待处理' in response.text
    assert 'class="cell pick"' not in response.text


def test_large_box_tiles_keep_real_row_column_and_skip_unrelated_sections(client):
    part,_,_=setup_stock(client,0)
    bid=ok(client.post('/api/boxes',json={'code':'Z20','name':'大盒','rows':20,'cols':20}))['id']
    slot=next(b for b in ok(client.get('/api/state'))['boxes'] if b['id']==bid)['slots'][-1]['id']
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'count','quantity':10,'mode':'exact'}))
    pid=create(client,[resistor()])
    result=sheets(client,pid)
    assert len(result)==1 and result[0]['range']=='S–T 行 · 17–20 列'
    assert result[0]['grid_cols']==4 and result[0]['grid_rows']==2
    assert result[0]['cells'][0]['label']=='S17'
    assert result[0]['cells'][-1]['label']=='T20' and result[0]['cells'][-1]['take']==2


def test_map_keeps_chip_model_cleans_description_and_escapes_all_labels(client):
    _,_,bid=setup_stock(client,0)
    slot=ok(client.get('/api/state'))['boxes'][0]['slots'][1]['id']
    part=ok(client.post('/api/parts',json={'name':'CHIP123','category':'芯片','package':'<b>SOT-23</b>','supplier_code':'C123','product_name':'编带 3.3V 托盘 系列 袋装;隐藏内容'}))['id']
    ok(client.post('/api/stock',json={'part_id':part,'slot_id':slot,'action':'count','quantity':10,'mode':'exact'}))
    ok(client.put('/api/boxes/'+bid,json={'code':'R01','name':'<script>盒子</script>','area':'<img src=x>','rows':2,'cols':3}))
    pid=create(client,[dict(name='CHIP123',supplier_code='C123',qty=1,designators='<svg>')],name='<script>项目</script>')
    result=sheets(client,pid)
    cell=result[0]['cells'][1]
    assert cell['title']=='CHIP123' and cell['description']=='3.3V'
    response=client.get('/api/projects/'+pid+'/picking/map')
    assert '<script>' not in response.text and '<svg>' not in response.text and '<img src=x>' not in response.text
    assert '&lt;script&gt;' in response.text and '&lt;B&gt;SOT-23&lt;/B&gt;' in response.text
    assert '隐藏内容' not in response.text


def test_all_omitted_and_already_issued_projects_never_highlight_new_picks(client):
    setup_stock(client,10)
    pid=create(client,[dict(name='NC',qty=1)])
    assert not sheets(client,pid)
    assert '没有可标记的库存位置' in client.get('/api/projects/'+pid+'/picking/map').text
    pid=project(client,quantity=2,boards=1)
    ok(client.post('/api/projects/'+pid+'/reserve'))
    ok(client.post('/api/projects/'+pid+'/issue'))
    result=sheets(client,pid)
    assert all(c['take']==0 for page in result for c in page['cells'])
    assert result[0]['cells'][0]['highlight']=='reference'
    response=client.get('/api/projects/'+pid+'/picking/map')
    assert '请勿重复取料' in response.text and 'class="cell pick"' not in response.text
