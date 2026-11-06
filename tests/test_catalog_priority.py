from test_workbench import client, ok
from test_value_matching import add_part, demand


def test_whole_bom_matches_catalog_ids_across_unrelated_package_labels(client):
    entries = [
        ('HAL250SO', '其他', 'C7420962', 'SOT-23-3', 'SOT-23-3L', ''),
        ('LAIL-PHC2.54-2P-01-XH-L', '连接器', 'C54905973', 'CONN-TH_2P-P2.50_HC-XH-2A-G', '插件,P=2.5MM', ''),
        ('BWIPX-1-001E', '连接器', 'C496552', 'IPEX-SMD_BWIPX-1-001E', 'SMD', '6GHz'),
        ('TJ-S3528UG6W9TLC6TW-A5', '其他', 'C440446', 'LED-SMD_L3.2-W2.8-R-RD', 'SMD3528-2P', ''),
        ('AO3400A', 'MOS管', 'C20917', 'SOT-23-3', 'SOT-23', ''),
        ('NANO SIM 7P H1.37', '连接器', 'C7529384', 'SIM-SMD_NANO-SIM-7P-H1.37', 'SMD', ''),
        ('AZ5325-01F', '其他', 'C2928737', 'DFN1006-2L-BI', 'DFN1006-2(0402)', ''),
        ('HX PZ2.0-1x2P ZZ', '连接器', 'C22438285', 'HDR-TH_2P-P2.00-V-M', '插件,P=2MM', ''),
    ]
    rows=[]; identities=[]
    for model, category, code, source_package, stored_package, value in entries:
        identities.append(add_part(client, name=model, category=category, value='', package=stored_package, code=code))
        rows.append(dict(name=model, category='', supplier_code=code, package=source_package, value=value, quantity=1))
    mapping={k:k for k in ('name','category','supplier_code','package','value')};mapping['per_board']='quantity'
    pid=ok(client.post('/api/projects',json={'name':'整单编号匹配','boards':1,'rows':rows,'mapping':mapping}))['id']
    for _ in range(2):
        result=ok(client.get('/api/projects/'+pid))
        assert result['unresolved']==0
        assert [l['part']['id'] for l in result['lines']]==identities
        assert all(l['match']=='supplier_code' and l['match_notes'] for l in result['lines'])
        # Identity matching alone does not manufacture stock or make this BOM ready.
        assert not result['ready'] and len(result['shortages'])==len(entries)
    assert not ok(client.get('/api/state'))['movements']


def test_repeated_matching_preserves_manual_choice_and_source_conflicts(client):
    add_part(client, specs={'精度':'1%'})
    other=add_part(client, name='手动替代料', code='C222')
    result=demand(client, code='C25076');line=result['lines'][0]
    ok(client.put('/api/bom/'+line['id']+'/match', json={'part_id':other,'confirmed':True,'reason':'本项目已核对替代料'}))
    repeated=ok(client.get('/api/projects/'+result['id']))['lines'][0]
    assert repeated['part']['id']==other and repeated['match']=='manual'
    assert repeated['reason']=='本项目已核对替代料' and repeated['match_notes']
    missing=demand(client, code='C999')['lines'][0]
    assert missing['part'] is None and '商城编号' in missing['unmatched_reason']


def test_model_fallback_handles_model_value_but_not_a_different_model(client):
    identity=add_part(client,name='KH-TYPE-C-16P',category='连接器',package='SMD',value='',code='C709357')
    payload={'name':'无编号项目','boards':1,'rows':[{'name':'KH-TYPE-C-16P','value':'KH-TYPE-C-16P','package':'USB-C-SMD_KH-TYPE-C-16P','qty':1}],
             'mapping':{'name':'name','value':'value','package':'package','per_board':'qty'}}
    pid=ok(client.post('/api/projects',json=payload))['id']
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part']['id']==identity and line['match']=='model' and line['match_notes']
    payload['rows'][0]['value']='DIFFERENT-MODEL'
    pid=ok(client.post('/api/projects',json=payload))['id']
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part'] is None and '数值字段不同' in line['unmatched_reason']
