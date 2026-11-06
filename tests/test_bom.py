import io
import json
from openpyxl import Workbook
from test_workbench import client,ok

HEADERS=['No.','Quantity','Comment','Designator','Footprint','Value','Manufacturer Part','Manufacturer','Supplier Part','Supplier','LCSC Price','Total']


def workbook(lines,second_sheet=False):
    book=Workbook()
    if second_sheet:
        book.active.append(['Project Title','测试电源板'])
        sheet=book.create_sheet('bom模板')
    else:sheet=book.active;sheet.title='bom模板'
    sheet.append(['','','','','Bill of Materials','test-bom'])
    sheet.append([]);sheet.append(['','','','','Project Title：','测试电源板'])
    sheet.append([]);sheet.append([]);sheet.append(HEADERS)
    for line in lines:sheet.append(line)
    sheet.append([])
    if not second_sheet:book.create_sheet('Field汇总').append(['Total Quantity',99])
    stream=io.BytesIO();book.save(stream);return stream.getvalue()


def row(value='100nF',model='CAP-100N',code='C111',package='C0603',refs='C1, C2',qty=2):
    return [None,qty,value or model,refs,package,value,model,'测试品牌',code,'LCSC','',None]


def parse(c,lines=None,**kwargs):
    return ok(c.post('/api/files/parse',data={'kind':'bom'},files={'file':('BOM.xlsx',workbook(lines or [row()],**kwargs))}))


def create_project(c,parsed,boards=1):
    return ok(c.post('/api/projects',json={**parsed,'kind':'bom','name':'BOM测试项目','boards':boards}))['id']


def part(c,package='0603',code='C111',model='CAP-100N',category='电容',value='100nF'):
    return ok(c.post('/api/parts',json={'name':model,'category':category,'package':package,'value':value,'supplier_code':code}))['id']


def test_eda_title_header_summary_sheet_and_mapping(client):
    parsed=parse(client,[row(),row('','CHIP-1','C222','SOT-23-6_L2.9-W1.6-P0.95','U1',1)],second_sheet=True)
    assert parsed['format']=='easyeda_bom' and parsed['header_row']==6 and parsed['sheet']=='bom模板'
    assert parsed['project_name']=='测试电源板' and len(parsed['rows'])==2
    preview=ok(client.post('/api/import/preview',json={**parsed,'kind':'bom'}))
    cap,chip=preview['rows']
    assert preview['errors']==0 and cap['row_no']==7 and cap['per_board']==2
    assert (cap['category'],cap['package'],cap['value'],cap['supplier_code'])==('电容','0603','100nF','C111')
    assert chip['package']=='SOT-23-6' and chip['name']=='CHIP-1'
    assert not ok(client.get('/api/state'))['parts']


def test_eda_inventory_entry_is_rejected_and_quantity_errors_use_source_rows(client):
    raw=workbook([row(qty=0)])
    wrong=client.post('/api/files/parse',data={'kind':'inventory'},files={'file':('BOM.xlsx',raw)})
    assert wrong.status_code==400 and '项目与 BOM' in wrong.json()['detail']
    parsed=ok(client.post('/api/files/parse',data={'kind':'bom'},files={'file':('BOM.xlsx',raw)}))
    preview=ok(client.post('/api/import/preview',json={**parsed,'kind':'bom'}))
    assert preview['errors']==1 and preview['rows'][0]['row_no']==7
    assert client.post('/api/projects',json={**parsed,'name':'bad'}).status_code==400


def test_exact_catalog_identity_and_source_survive_backup(client):
    identity=part(client)
    parsed=parse(client);pid=create_project(client,parsed,boards=3)
    result=ok(client.get('/api/projects/'+pid));line=result['lines'][0]
    assert line['part']['id']==identity and line['match']=='supplier_code' and line['demand']==6
    assert line['supplier_code']=='C111' and 'manufacturer_part' not in line
    assert result['source']['sheet']=='bom模板' and line['source_package']=='C0603'
    assert line['status']=='short' and not result['ready']
    backup=client.get('/api/backup');assert backup.status_code==200
    ok(client.post('/api/restore',files={'file':('backup.sqlite3',backup.content)},headers={'X-Confirm-Restore':'RESTORE'}))
    assert ok(client.get('/api/projects/'+pid))['lines'][0]['match']=='supplier_code'
    assert not ok(client.get('/api/state'))['movements']


def test_catalog_identity_keeps_value_and_package_differences_visible(client):
    identity=part(client)
    parsed=parse(client);pid=create_project(client,parsed)
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part']['id']==identity and line['match']=='supplier_code'
    for source in (row(package='C0805'),row('22uF')):
        pid=create_project(client,parse(client,[source]))
        line=ok(client.get('/api/projects/'+pid))['lines'][0]
        assert line['part']['id']==identity and line['match']=='supplier_code' and line['match_notes']


def test_duplicate_catalog_ids_and_different_supplier_are_reviewed(client):
    part(client);part(client,model='CAP-OTHER')
    pid=create_project(client,parse(client))
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part'] is None and len(line['candidates'])==2
    pid=create_project(client,parse(client,[row(code='C999')]))
    assert ok(client.get('/api/projects/'+pid))['lines'][0]['part'] is None


def test_ignored_manufacturer_columns_do_not_generate_conflicts(client):
    identity=part(client,model='R-SAME',category='电阻',value='100k',package='0603',code='')
    parsed=parse(client,[row('511kΩ','R-SAME','','R0603','R1',1),row('100kΩ','R-SAME','','R0603','R2',1)])
    preview=ok(client.post('/api/import/preview',json={**parsed,'kind':'bom'}))
    assert preview['errors']==0 and all(not e['warnings'] for e in preview['rows'])
    assert 'R-SAME' not in json.dumps(parsed) and '测试品牌' not in json.dumps(parsed)
    assert set(parsed['ignored_columns'])=={'Manufacturer Part','Manufacturer'}
    pid=create_project(client,parsed)
    result=ok(client.get('/api/projects/'+pid))
    assert result['lines'][0]['part'] is None
    assert result['lines'][1]['part']['id']==identity and result['lines'][1]['match']=='value_package'
    assert all(not line['warnings'] for line in result['lines'])
    mismatched=parse(client,[row(refs='C1',qty=2)])
    preview=ok(client.post('/api/import/preview',json={**mismatched,'kind':'bom'}))
    assert '位号个数' in preview['rows'][0]['warnings'][0]


def test_chinese_bom_headers_and_passive_aliases(client):
    raw='说明,测试\n数量,位号,封装,名称,厂家型号,商城编号\n1,L1,L1210,15uH,INDUCTOR,C123\n'
    parsed=ok(client.post('/api/files/parse',data={'kind':'bom'},files={'file':('BOM.csv',raw.encode())}))
    preview=ok(client.post('/api/import/preview',json={**parsed,'kind':'bom'}))
    entry=preview['rows'][0]
    assert (entry['category'],entry['value'],entry['package'])==('电感','15uH','1210')


def test_full_model_and_package_match_without_bom_category(client):
    identity=part(client,model='JW5357MSOTB#TR',category='芯片',package='SOT-23-6',code='C5197556',value='')
    bid=ok(client.post('/api/boxes',json={'code':'U01','name':'芯片盒','rows':1,'cols':1}))['id']
    slot=ok(client.get('/api/state'))['boxes'][0]['slots'][0]['id']
    ok(client.post('/api/stock',json={'part_id':identity,'slot_id':slot,'quantity':5,'action':'in'}))
    pid=create_project(client,parse(client,[row('','JW5357MSOTB#TR','','SOT-23-6','U2',1)]))
    result=ok(client.get('/api/projects/'+pid));line=result['lines'][0]
    assert line['category']=='' and line['supplier_code']==''
    assert line['part']['id']==identity and line['match']=='model'
    assert line['status']=='enough' and line['available_before']==5 and result['ready']


def test_full_model_still_requires_unique_candidate_and_matching_package(client):
    part(client,model='JW5357MSOTB#TR',category='芯片',package='SOT-23-6',value='')
    wrong=create_project(client,parse(client,[row('','JW5357MSOTB#TR','','SOT-23-5','U2',1)]))
    assert ok(client.get('/api/projects/'+wrong))['lines'][0]['match']=='model'
    assert ok(client.get('/api/projects/'+wrong))['lines'][0]['match_notes']
    part(client,model='JW5357MSOTB#TR',category='芯片',package='SOT-23-6',code='C222',value='')
    duplicate=create_project(client,parse(client,[row('','JW5357MSOTB#TR','','SOT-23-6','U2',1)]))
    line=ok(client.get('/api/projects/'+duplicate))['lines'][0]
    assert line['part'] is None and line['match']=='review' and len(line['candidates'])==2


def test_full_model_does_not_override_supplier_or_parameter_conflicts():
    from backend.domain import match_line
    p={'id':'chip','name':'JW5357MSOTB#TR','category':'芯片','package':'SOT-23-6','value':'','specs':{'耐压':'18V'},'supplier_code':'C5197556','archived':0}
    line={'confirmed':False,'part_id':None,'name':p['name'],'category':'','package':p['package'],'value':'','specs':'{}','supplier_code':'','warnings':[]}
    assert match_line(line,[p])[2]=='model'
    for fields in ({'supplier_code':'C999'},{'specs':'{"耐压":"24V"}'},{'value':'5V'},{'category':'MOS管'},{'warnings':['源文件需要核对']}):
        chosen,_,reason=match_line({**line,**fields},[p])
        assert chosen is None and reason=='review'
    # A unique passive value/package match keeps a separate match label.
    passive={**p,'name':'10k','category':'电阻','package':'0603','value':'10k','specs':{}}
    demand={**line,'name':'10k','category':'电阻','package':'0603','value':'10k'}
    assert match_line(demand,[passive])[2]=='value_package'
    assert match_line({**demand,'specs':'{"精度":"1%"}'},[passive])[0] is None


def test_same_catalog_inductor_accepts_explicit_eda_body_size_alias(client):
    identity=part(client,model='SWPA4018S4R7MT',category='电感',package='SMD,4X4MM',code='C60117',value='4.7uH')
    pid=create_project(client,parse(client,[row('4.7uH','SWPA4018S4R7MT','C60117','IND-SMD_L4.0-W4.0','L1',1)]))
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part']['id']==identity and line['match']=='supplier_code'
    assert line['package']=='IND-SMD_L4.0-W4.0' and line['part']['package']=='SMD,4X4MM'
    # Original source and inventory labels are retained, never rewritten as a guess.
    for source in (row('10uH',code='C60117',package='IND-SMD_L4.0-W4.0',refs='L1',qty=1),
                   row('4.7uH',code='C60117',package='IND-SMD_L5.0-W5.0',refs='L1',qty=1),
                   row('4.7uH',code='C999',package='IND-SMD_L4.0-W4.0',refs='L1',qty=1),
                   row('4.7uH',code='',package='IND-SMD_L4.0-W4.0',refs='L1',qty=1)):
        other=create_project(client,parse(client,[source]))
        result=ok(client.get('/api/projects/'+other))['lines'][0]
        if result['supplier_code']=='C60117':
            assert result['part']['id']==identity and result['match']=='supplier_code' and result['match_notes']
        else:
            assert result['part'] is None and result['match']=='review'


def test_duplicate_catalog_still_needs_review_with_body_size_alias(client):
    for model in ('SWPA4018S4R7MT','DUPLICATE'):
        part(client,model=model,category='电感',package='SMD,4X4MM',code='C60117',value='4.7uH')
    pid=create_project(client,parse(client,[row('4.7uH',code='C60117',package='IND-SMD_L4.0-W4.0',refs='L1',qty=1)]))
    line=ok(client.get('/api/projects/'+pid))['lines'][0]
    assert line['part'] is None and line['match']=='review' and len(line['candidates'])==2
