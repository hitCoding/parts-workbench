import pytest
from test_workbench import client,ok


def add_part(c,name='100Ω',category='电阻',value='100Ω',package='0402',specs=None,code='C25076'):
    return ok(c.post('/api/parts',json={'name':name,'category':category,'value':value,'package':package,'specs':specs or {},'supplier_code':code}))['id']


def demand(c,category='电阻',value='100Ω',package='0402',specs=None,code='',quantity=1,duplicate=False):
    row={'name':value,'category':category,'value':value,'package':package,'quantity':quantity,'supplier_code':code,**(specs or {})}
    mapping={k:k for k in ['name','category','value','package','supplier_code',*(specs or {})]};mapping['per_board']='quantity'
    pid=ok(c.post('/api/projects',json={'name':'数值匹配验证','rows':[row,row] if duplicate else [row],'mapping':mapping,'boards':1}))['id']
    return ok(c.get('/api/projects/'+pid))


def stock(c,part,quantity=5,mode='exact'):
    ok(c.post('/api/boxes',json={'code':'R01','name':'测试盒','rows':1,'cols':1}))
    slot=ok(c.get('/api/state'))['boxes'][0]['slots'][0]['id']
    ok(c.post('/api/stock',json={'part_id':part,'slot_id':slot,'quantity':quantity,'mode':mode,'action':'count'}))


@pytest.mark.parametrize('category,value,package,specs,missing',[
    ('电阻','100Ω','0402',{'精度':'1%','功率':'0.0625W'},['精度','功率']),
    ('电容','100nF','0603',{'耐压':'16V','介质':'X7R'},['耐压','介质']),
    ('电感','4.7uH','SMD,4X4MM',{'额定电流':'2.2A'},['额定电流']),
])
def test_unique_value_package_matches_with_missing_specs_notice(client,category,value,package,specs,missing):
    part=add_part(client,category=category,value=value,package=package,specs=specs)
    stock(client,part)
    result=demand(client,category,value,package);line=result['lines'][0]
    assert line['part']['id']==part and line['match']=='value_package'
    assert line['missing_specs']==missing and line['specs_data']=={}
    assert line['status']=='enough' and result['ready'] and line['available_before']==5
    assert not [m for m in ok(client.get('/api/state'))['movements'] if m['kind']=='项目领料']


def test_multiple_value_package_candidates_still_need_review(client):
    add_part(client,specs={'精度':'1%','功率':'0.0625W'})
    add_part(client,name='另一种100Ω',specs={'精度':'5%','功率':'0.125W'},code='C222')
    result=demand(client);line=result['lines'][0]
    assert line['part'] is None and line['match']=='review' and len(line['candidates'])==2
    assert not result['ready']
    # A provided tolerance can distinguish candidates, with missing power still noted.
    result=demand(client,specs={'精度':'1%'});line=result['lines'][0]
    assert line['part']['supplier_code']=='C25076' and line['match']=='value_package'
    assert line['missing_specs']==['功率']


def test_value_match_preserves_explicit_requirements(client):
    add_part(client,specs={'精度':'1%','功率':'0.0625W'})
    for changes in ({'package':'0603'},{'value':'1kΩ'},{'category':'电容'},{'specs':{'精度':'0.1%'}},{'specs':{'耐压':'50V'}},{'code':'C999'}):
        line=demand(client,**changes)['lines'][0]
        assert line['part'] is None
    complete=demand(client,specs={'精度':'1%','功率':'0.0625W'})['lines'][0]
    assert complete['match']=='exact' and not complete['missing_specs']


def test_value_match_still_checks_stock_confidence_and_combined_usage(client):
    part=add_part(client,specs={'精度':'1%','功率':'0.0625W'})
    stock(client,part,5,mode='estimate')
    result=demand(client)
    assert result['lines'][0]['part']['id']==part and result['lines'][0]['status']=='count'
    assert client.post('/api/projects/'+result['id']+'/reserve').status_code==400
    result=demand(client,quantity=3,duplicate=True)
    assert result['shortages'][0]['quantity']==6 and not result['ready']


def test_value_match_can_be_reserved_without_losing_missing_specs_notice(client):
    part=add_part(client,specs={'精度':'1%','功率':'0.0625W'})
    stock(client,part,5)
    result=demand(client)
    ok(client.post('/api/projects/'+result['id']+'/reserve'))
    reserved=ok(client.get('/api/projects/'+result['id']))['lines'][0]
    assert reserved['part']['id']==part and reserved['missing_specs']==['精度','功率']
    state=ok(client.get('/api/state'))
    assert state['parts'][0]['exact']==5 and state['parts'][0]['reserved']==1
