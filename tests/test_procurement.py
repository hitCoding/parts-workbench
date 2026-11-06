import csv
import io
import json
from test_workbench import client,ok,setup_stock
from backend.procurement import infer


def order_file(order_id='SO12345678900',qty='10个',shipped='8个',code='C12345'):
    buffer=io.StringIO();writer=csv.writer(buffer)
    writer.writerow(['客户编号：','ignored-personal-value'])
    writer.writerow(['订单编号：',order_id])
    writer.writerow(['序号','商品编号','品牌','厂家型号','封装 ','商品名称','订购数量（修改后）','发货数量','是否不发此货','商品单价'])
    writer.writerow(['1',code,'测试品牌','R-10K','0603','厚膜电阻 10kΩ ±1% 100mW 编带',qty,shipped,'','￥0.1/个'])
    return buffer.getvalue().encode('utf-8-sig')


def parsed(c,**kwargs):
    return ok(c.post('/api/purchases/import/parse',files={'file':('LCSC.csv',order_file(**kwargs))}))


def test_order_parse_whitelists_products_and_never_sets_stock(client):
    preview=parsed(client)
    assert preview['total_quantity']==10 and preview['errors']==0
    line=preview['lines'][0]
    assert line['quantity']==10 and line['shipped']==8 and line['specs']['功率']=='0.1W'
    assert 'ignored-personal-value' not in json.dumps(preview)
    state=ok(client.get('/api/state'));assert not state['parts'] and not state['purchases']
    ok(client.post('/api/purchases/import/commit',json=preview))
    state=ok(client.get('/api/state'))
    assert len(state['parts'])==1 and state['parts'][0]['available']==0
    assert state['purchases'][0]['status']=='ordered' and state['purchases'][0]['source_shipped']==8
    assert state['purchase_orders'][0]['order_id']==preview['order_id']


def test_order_duplicate_prevented_and_same_sku_reused(client):
    preview=parsed(client);ok(client.post('/api/purchases/import/commit',json=preview))
    assert parsed(client)['already_imported']
    assert client.post('/api/purchases/import/commit',json=preview).status_code==409
    another=parsed(client,order_id='SO12345678901')
    assert another['lines'][0]['reuse_id']
    ok(client.post('/api/purchases/import/commit',json=another))
    state=ok(client.get('/api/state'));assert len(state['parts'])==1 and len(state['purchases'])==2
    assert state['parts'][0]['available']==0


def test_bad_quantity_and_existing_sku_conflict_are_reviewable(client):
    assert parsed(client,qty='1.5个')['errors']==1
    assert parsed(client,qty='10个',shipped='11个')['errors']==1
    base=parsed(client);ok(client.post('/api/purchases/import/commit',json=base))
    other=parsed(client,order_id='SO12345678901');other['lines'][0]['package']='0402'
    checked=ok(client.post('/api/purchases/import/preview',json=other));assert checked['errors']==1
    assert client.post('/api/purchases/import/commit',json=other).status_code==400
    assert len(ok(client.get('/api/state'))['purchases'])==1


def test_batch_receipts_partial_and_collision_roll_back(client):
    _,slot,_=setup_stock(client,quantity=0)
    a=parsed(client);ok(client.post('/api/purchases/import/commit',json=a))
    b=parsed(client,order_id='SO12345678901',code='C54321');b['lines'][0]['name']='different part'
    ok(client.post('/api/purchases/import/commit',json=b))
    state=ok(client.get('/api/state'));purchases=state['purchases']
    free=state['boxes'][0]['slots'][1]['id']
    conflict={'items':[{'id':p['id'],'slot_id':free,'quantity':3} for p in purchases]}
    assert client.post('/api/purchases/receive-batch',json=conflict).status_code==400
    state=ok(client.get('/api/state'));assert all(p['received']==0 for p in state['purchases'])
    purchase=purchases[0]
    ok(client.post('/api/purchases/receive-batch',json={'items':[{'id':purchase['id'],'slot_id':free,'quantity':3}]}))
    state=ok(client.get('/api/state'));current=next(p for p in state['purchases'] if p['id']==purchase['id'])
    assert current['received']==3 and current['status']=='partial'
    ok(client.post('/api/purchases/receive-batch',json={'items':[{'id':purchase['id'],'slot_id':free,'quantity':7}]}))
    assert client.post('/api/purchases/receive-batch',json={'items':[{'id':purchase['id'],'slot_id':free,'quantity':1}]}).status_code==400


def test_manual_new_part_and_stock_are_atomic(client):
    existing,occupied,_=setup_stock(client)
    payload={'part':{'name':'new intake','category':'芯片','supplier_code':'C666'},'slot_id':occupied,'quantity':5}
    assert client.post('/api/intake/manual',json=payload).status_code==400
    state=ok(client.get('/api/state'));assert len(state['parts'])==1
    payload['slot_id']=state['boxes'][0]['slots'][1]['id']
    result=ok(client.post('/api/intake/manual',json=payload))
    ok(client.post('/api/intake/manual',json={'part_id':result['id'],'slot_id':payload['slot_id'],'quantity':2}))
    state=ok(client.get('/api/state'))
    assert next(p for p in state['parts'] if p['id']==result['id'])['available']==7
    assert client.post('/api/intake/manual',json=payload).status_code==400


def test_inference_preserves_zero_and_does_not_guess_dielectric():
    cat,value,specs=infer('厚膜电阻 0Ω ±1% 10W 编带')
    assert (cat,value,specs['功率'])==('电阻','0Ω','10W')
    cat,value,specs=infer('100nF ±10% 50V 编带')
    assert cat=='电容' and '介质' not in specs


def test_order_uploaded_to_inventory_gets_correct_entry_guidance(client):
    response=client.post('/api/files/parse',files={'file':('LCSC.csv',order_file())})
    assert response.status_code==400
    assert '导入立创订单' in response.json()['detail']


def test_esd_parasitic_capacitance_is_not_a_capacitor():
    category,value,specs=infer('普容5.0V，单路双向ESD静电防护器件，30kV，CJ：12pF；音频防护')
    assert category=='二极管' and value=='' and '介质' not in specs
