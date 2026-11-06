import pytest
from test_workbench import client, ok
from test_bom import part, parse, row, create_project
from test_value_matching import stock

MODEL = 'TS-1088-AR02016'
EDA_PACKAGE = 'SW-SMD_L3.9-W3.0-P4.45'
MALL_PACKAGE = 'SMD,4X3MM'


@pytest.mark.parametrize('required,stored', [
    (EDA_PACKAGE, MALL_PACKAGE), (MALL_PACKAGE, EDA_PACKAGE),
])
def test_identical_switch_catalog_and_model_accept_known_package_alias(client, required, stored):
    identity = part(client, model=MODEL, category='开关', value='', code='C720477', package=stored)
    stock(client, identity, 10)
    pid = create_project(client, parse(client, [row('', MODEL, 'C720477', required, 'SW1', 1)]))
    result = ok(client.get('/api/projects/' + pid))
    line = result['lines'][0]
    assert line['part']['id'] == identity and line['match'] == 'supplier_code'
    assert line['package'] == required and line['part']['package'] == stored
    assert line['available_before'] == 10 and result['ready']
    assert ok(client.get('/api/state'))['parts'][0]['reserved'] == 0


@pytest.mark.parametrize('model,code,required,stored', [
    (MODEL, 'C720477', 'SW-SMD_L3.9-W3.0-P4.5', MALL_PACKAGE),
    (MODEL, 'C720477', 'SW-SMD_L3.8-W3.0-P4.45', MALL_PACKAGE),
    (MODEL, 'C720477', 'SW-SMD_L3.9-W3.0', MALL_PACKAGE),
    (MODEL, 'C720477', EDA_PACKAGE, 'SMD,4X4MM'),
    (MODEL, 'C999', EDA_PACKAGE, MALL_PACKAGE),
    (MODEL, '', EDA_PACKAGE, MALL_PACKAGE),
    ('TS-1088-OTHER', 'C720477', EDA_PACKAGE, MALL_PACKAGE),
])
def test_catalog_priority_reports_switch_differences_without_guessing_other_catalogs(client, model, code, required, stored):
    identity = part(client, model=MODEL, category='开关', value='', code='C720477', package=stored)
    pid = create_project(client, parse(client, [row('', model, code, required, 'SW1', 1)]))
    line = ok(client.get('/api/projects/' + pid))['lines'][0]
    if code == 'C999':
        assert line['part'] is None and line['match'] == 'review'
    else:
        assert line['part']['id'] == identity
        assert line['match'] == ('supplier_code' if code else 'model')
        assert line['match_notes']
    assert line['candidates'][0]['id'] == identity


def test_duplicate_switch_catalog_still_requires_review(client):
    for model in (MODEL, 'DUPLICATE'):
        part(client, model=model, category='开关', value='', code='C720477', package=MALL_PACKAGE)
    pid = create_project(client, parse(client, [row('', MODEL, 'C720477', EDA_PACKAGE, 'SW1', 1)]))
    line = ok(client.get('/api/projects/' + pid))['lines'][0]
    assert line['part'] is None and line['match'] == 'review' and len(line['candidates']) == 2
