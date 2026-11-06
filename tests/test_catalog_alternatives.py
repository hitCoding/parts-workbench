import pytest
from test_workbench import client, ok
from test_bom import parse, row, create_project
from test_value_matching import add_part, demand, stock


def capacitor(c, code='C1562', name='0402CG330J500NT', package='0402'):
    return add_part(c, name=name, category='电容', value='33pF', package=package,
                    specs={'精度': '5%', '耐压': '50V'}, code=code)


@pytest.mark.parametrize('codes', [
    'C1562, C115628', 'c1562，C115628', 'C115628; C1562', 'C115628；C1562',
    'C115628、C1562', 'C115628 / C1562', 'C115628\nC1562', 'C1562 | C115628',
    'C115628\tC1562', 'C1562, C1562',
])
def test_bom_alternative_catalog_codes_match_one_available_part(client, codes):
    identity = capacitor(client)
    stock(client, identity, 100)
    parsed = parse(client, [row('33pF', code=codes, package='C0402', refs='C1', qty=1)])
    pid = create_project(client, parsed, boards=2)
    result = ok(client.get('/api/projects/' + pid))
    line = result['lines'][0]
    assert line['part']['id'] == identity and line['match'] == 'supplier_code'
    assert line['supplier_code'] == codes.upper() and line['missing_specs'] == ['耐压', '介质']
    assert line['demand'] == 2 and line['available_before'] == 100 and result['ready']
    state = ok(client.get('/api/state'))
    assert state['parts'][0]['exact'] == 100 and state['parts'][0]['reserved'] == 0
    assert len(state['movements']) == 1


def test_multiple_matching_catalog_alternatives_require_review(client):
    first = capacitor(client)
    other = capacitor(client, code='C115628', name='ALTERNATIVE-33P')
    result = demand(client, category='电容', value='33pF', code='C1562, C115628')
    line = result['lines'][0]
    assert line['part'] is None and line['match'] == 'review' and not result['ready']
    assert {p['id'] for p in line['candidates']} == {first, other}
    # An archived alternative is excluded from matching.
    ok(client.post('/api/parts/' + other + '/archive', json={'archived': True}))
    line = ok(client.get('/api/projects/' + result['id']))['lines'][0]
    assert line['part']['id'] == first and line['match'] == 'supplier_code'


def test_duplicate_catalog_record_still_blocks_alternative_matching(client):
    capacitor(client)
    capacitor(client, name='DUPLICATE', package='0603')
    capacitor(client, code='C115628', name='ALTERNATIVE')
    line = demand(client, category='电容', value='33pF', code='C1562,C115628')['lines'][0]
    assert line['part'] is None and line['match'] == 'review'


def test_catalog_lists_compare_complete_ids_and_preserve_requirements(client):
    identity = capacitor(client)
    for code in ('C15620,C115628', 'C1562-ALT,C115628', 'C115628'):
        line = demand(client, category='电容', value='33pF', code=code)['lines'][0]
        assert line['part'] is None
    for changes in ({'value': '22pF'}, {'package': '0603'}, {'category': '电阻'},
                    {'specs': {'耐压': '100V'}}, {'specs': {'介质': 'X7R'}}):
        fields = {'category': '电容', 'value': '33pF', 'code': 'C1562,C115628', **changes}
        line = demand(client, **fields)['lines'][0]
        assert line['part']['id'] == identity and line['match'] == 'supplier_code' and line['match_notes']
    pid = create_project(client, parse(client, [row('33pF', code='C1562,C115628',
                                                   package='C0402', refs='C1', qty=2)]))
    line = ok(client.get('/api/projects/' + pid))['lines'][0]
    assert line['warnings'] and line['part'] is None


def test_multiple_codes_do_not_fall_back_to_one_package_label_match(client):
    add_part(client, category='电感', value='4.7uH', package='SMD,4X4MM', code='C60117')
    add_part(client, category='电感', value='4.7uH', package='IND-SMD_L4.0-W4.0', code='C222')
    line = demand(client, category='电感', value='4.7uH', package='IND-SMD_L4.0-W4.0',
                  code='C60117,C222')['lines'][0]
    assert line['part'] is None and line['match'] == 'review'
