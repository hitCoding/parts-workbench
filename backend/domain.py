"""Business rules shared by API endpoints. All stock writes run in a transaction."""
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from .db import uid, now, rows, one

REQUIRED_SPECS={'电阻':('精度','功率'),'电容':('耐压','介质'),'电感':('额定电流',)}


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def need(condition, message, status=400):
    if not condition:
        raise Problem(message, status)


def text(v, limit=500):
    return str('' if v is None else v).strip()[:limit]


def integer(v, label, minimum=0, maximum=1000000000):
    try:
        n = Decimal(str(v))
        need(n.is_finite() and n == n.to_integral_value(), f'{label}必须为整数')
        n = int(n)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise Problem(f'{label}必须为整数')
    need(minimum <= n <= maximum, f'{label}必须在 {minimum}～{maximum} 之间')
    return n


def canonical_package(value):
    p = re.sub(r'\s+', '', text(value)).upper()
    # Only known passive footprint prefixes are aliases. SOT variants need review.
    m = re.fullmatch(r'[RCL]?(0201|0402|0603|0805|1206|1210|2010|2512)', p)
    return m[1] if m else p


def canonical_value(value, category):
    raw = text(value).replace('μ', 'u').replace('µ', 'u').replace('Ω', '').replace('Ω', '').replace(' ', '')
    if category == '电阻':
        raw = re.sub(r'(ohms?|欧姆|欧)$', '', raw, flags=re.I)
        embedded = re.fullmatch(r'(\d+)([RrKkMm])(\d+)', raw)
        if embedded:
            raw = embedded[1] + '.' + embedded[3] + embedded[2]
        pattern = r'(\d+(?:\.\d+)?)([RrKkMm]?)'
        factors = {'': 1, 'r': 1, 'k': 1000, 'm': 1000000}
    elif category in ('电容', '电感'):
        raw = re.sub(r'[FfHh]$', '', raw)
        pattern = r'(\d+(?:\.\d+)?)([pnuUmM]?)'
        factors = {'': 1, 'p': Decimal('1e-12'), 'n': Decimal('1e-9'), 'u': Decimal('1e-6'), 'm': Decimal('1e-3')}
    else:
        return raw.casefold()
    m = re.fullmatch(pattern, raw)
    if not m:
        return raw.casefold()
    return str((Decimal(m[1]) * Decimal(factors[m[2].lower()])).normalize())


def clean_specs(data):
    need(isinstance(data, dict), '参数必须是对象')
    return {text(k, 50): text(v, 100) for k, v in data.items() if text(k) and text(v)}


def product_name(part):
    """Read legacy order descriptions without changing the original model or notes."""
    lines=[line.strip() for line in part['notes'].splitlines() if line.strip()]
    source=next((i for i,line in enumerate(lines) if re.fullmatch(r'来自立创订单\s+[A-Z0-9_-]+',line)),0)
    return lines[source-1] if source else part['name']


def save_product_name(con,part_id,value):
    con.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('product-name:'+part_id,json.dumps(text(value,2000),ensure_ascii=False)))


def create_part(con, data, item_id=None):
    name, category = text(data.get('name'), 200), text(data.get('category'), 50)
    need(name and category, '请填写器件名称和类别')
    specs = clean_specs(data.get('specs', {}))
    values = (name, category, canonical_package(data.get('package')), text(data.get('value'), 100),
              json.dumps(specs, ensure_ascii=False), text(data.get('supplier_code'), 100), text(data.get('notes'), 2000))
    if item_id:
        one(con, 'parts', item_id)
        used = con.execute('SELECT 1 FROM reservations WHERE part_id=?', (item_id,)).fetchone()
        need(not used, '器件已被项目占用，请先释放占用再修改档案')
        con.execute('UPDATE parts SET name=?,category=?,package=?,value=?,specs=?,supplier_code=?,notes=? WHERE id=?', (*values, item_id))
        con.execute("UPDATE bom_lines SET confirmed=0,part_id=NULL,reason='' WHERE part_id=? AND project_id IN (SELECT id FROM projects WHERE status='draft')", (item_id,))
    else:
        item_id = uid()
        con.execute('INSERT INTO parts(id,name,category,package,value,specs,supplier_code,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (item_id, *values, now()))
    if 'product_name' in data:save_product_name(con,item_id,data['product_name'])
    return item_id


def create_box(con, data, item_id=None):
    code, name = text(data.get('code'), 30).upper(), text(data.get('name'), 100)
    need(re.fullmatch(r'[A-Z0-9_-]{1,30}', code), '盒号请使用字母、数字、下划线或短横线')
    need(name, '请填写盒子名称')
    r, c = integer(data.get('rows', 3), '行数', 1, 20), integer(data.get('cols', 8), '列数', 1, 20)
    if item_id:
        old = one(con, 'boxes', item_id)
        need(old['rows'] == r and old['cols'] == c, '已有盒子的行列不能变更；请新建盒子后移位，保留位置编号')
        con.execute('UPDATE boxes SET code=?,name=?,area=? WHERE id=?', (code, name, text(data.get('area'), 100), item_id))
    else:
        item_id = uid()
        con.execute('INSERT INTO boxes VALUES(?,?,?,?,?,?)', (item_id, code, name, text(data.get('area'), 100), r, c))
        con.executemany('INSERT INTO slots VALUES(?,?,?,?)', [(uid(), item_id, a, b) for a in range(r) for b in range(c)])
    return item_id


def journal(con, part_id, slot_id, kind, before, after, note):
    delta = after - before if before is not None and after is not None else None
    con.execute('INSERT INTO movements VALUES(?,?,?,?,?,?,?,?,?)', (uid(), part_id, slot_id, kind, before, after, delta, text(note, 1000), now()))


def available(con, part_id, exclude_project=None):
    physical = con.execute("SELECT COALESCE(SUM(quantity),0) FROM stock WHERE part_id=? AND mode='exact'", (part_id,)).fetchone()[0]
    reserved = con.execute('SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE part_id=? AND (? IS NULL OR project_id!=?)', (part_id, exclude_project, exclude_project)).fetchone()[0]
    return physical - reserved


def set_stock(con, data):
    part_id, slot_id = data.get('part_id'), data.get('slot_id')
    part = one(con, 'parts', part_id)
    need(not part['archived'], '已归档器件不能入库')
    one(con, 'slots', slot_id)
    current = con.execute('SELECT * FROM stock WHERE slot_id=?', (slot_id,)).fetchone()
    need(not current or current['part_id'] == part_id, '该格已有其他器件，请先移位')
    action, mode = data.get('action', 'count'), data.get('mode', 'exact')
    need(action in ('count', 'in', 'out'), '库存操作无效')
    need(mode in ('exact', 'estimate', 'unknown'), '数量记录方式无效')
    before = current['quantity'] if current else 0
    if action == 'count':
        after = None if mode == 'unknown' else integer(data.get('quantity'), '盘点数量')
    else:
        need(current is not None or action == 'in', '该格没有库存')
        need(not current or current['mode'] != 'unknown', '数量未知，请先盘点')
        qty = integer(data.get('quantity'), '操作数量', 1)
        after = (before or 0) + (qty if action == 'in' else -qty)
        mode = current['mode'] if current else 'exact'
        need(after >= 0, '库存不足')
    con.execute('INSERT INTO stock VALUES(?,?,?,?,?) ON CONFLICT(slot_id) DO UPDATE SET quantity=excluded.quantity,mode=excluded.mode',
                (uid(), part_id, slot_id, after, mode))
    need(available(con, part_id) >= 0, '操作会影响已占用库存，请先释放项目占用')
    journal(con, part_id, slot_id, {'count':'盘点', 'in':'入库', 'out':'领料'}[action], before, after, data.get('note', ''))


def move_stock(con, data):
    source = one(con, 'stock', data.get('stock_id'))
    target_id = data.get('slot_id')
    one(con, 'slots', target_id)
    need(source['slot_id'] != target_id, '请选择不同的目标格子')
    target = con.execute('SELECT * FROM stock WHERE slot_id=?', (target_id,)).fetchone()
    need(not target or target['part_id'] == source['part_id'], '目标格已有其他器件')
    # Whole-position transfers preserve quantity confidence and prevent estimate + exact mixing.
    if target:
        need(target['mode'] == source['mode'], '两处数量记录方式不同，请先盘点后合并')
        after = None if source['mode'] == 'unknown' else target['quantity'] + source['quantity']
        con.execute('UPDATE stock SET quantity=? WHERE id=?', (after, target['id']))
    else:
        after = source['quantity']
        con.execute('INSERT INTO stock VALUES(?,?,?,?,?)', (uid(), source['part_id'], target_id, after, source['mode']))
    con.execute('DELETE FROM stock WHERE id=?', (source['id'],))
    journal(con, source['part_id'], source['slot_id'], '移出', source['quantity'], 0, '整格移位')
    journal(con, source['part_id'], target_id, '移入', target['quantity'] if target else 0, after, '整格移位')


def stock_rows(con):
    return rows(con, '''SELECT s.*,b.code box_code,b.name box_name,b.area,sl.row_no,sl.col_no
        FROM stock s JOIN slots sl ON sl.id=s.slot_id JOIN boxes b ON b.id=sl.box_id ORDER BY b.code,sl.row_no,sl.col_no''')


def part_rows(con):
    result = rows(con, 'SELECT * FROM parts ORDER BY created_at DESC,id')
    stock = stock_rows(con)
    names={r['key'][13:]:json.loads(r['value']) for r in rows(con,"SELECT * FROM settings WHERE key LIKE 'product-name:%'")}
    for p in result:
        p['product_name']=names.get(p['id']) or product_name(p)
        p['specs'] = json.loads(p['specs'])
        p['locations'] = [s for s in stock if s['part_id'] == p['id']]
        p['exact'] = sum(s['quantity'] for s in p['locations'] if s['mode'] == 'exact')
        p['available'] = available(con, p['id'])
        p['reserved'] = p['exact'] - p['available']
    return result


def spec_match(required, actual):
    for key, val in required.items():
        got = actual.get(key)
        if not got:
            return False
        # Conservative equality. Higher ratings are candidates for human review.
        if re.sub(r'\s+', '', val).casefold() != re.sub(r'\s+', '', got).casefold():
            return False
    return True


def supplier_codes(value):
    """BOM cells may list alternative catalog IDs; compare complete IDs only."""
    return {code.casefold() for code in re.split(r'[,，;；、/|\s]+', text(value)) if code}


def match_notes(line, part):
    """Describe source differences without treating catalog identity as a fuzzy match."""
    notes=[]
    codes=supplier_codes(line.get('supplier_code'))
    if codes and text(part['supplier_code']).casefold() not in codes:
        notes.append(f"商城编号不同：BOM {line['supplier_code']}；库存 {part['supplier_code'] or '未填写'}")
    if canonical_package(line['package'])!=canonical_package(part['package']):
        notes.append(f"封装标注不同：BOM {line['package'] or '未填写'}；库存 {part['package'] or '未填写'}")
    if line['category'] and line['category']!=part['category']:
        notes.append(f"类别不同：BOM {line['category']}；库存 {part['category']}")
    category=line['category'] or part['category']
    model_value=part['category'] not in REQUIRED_SPECS and line['value'].casefold()==part['name'].casefold()
    if line['value'] and not model_value and (not part['value'] or canonical_value(line['value'],category)!=canonical_value(part['value'],category)):
        notes.append(f"数值字段不同：BOM {line['value']}；库存 {part['value'] or '未填写'}")
    if part['category'] not in REQUIRED_SPECS and line['name'].casefold()!=part['name'].casefold():
        notes.append(f"名称/型号标注不同：BOM {line['name']}；库存 {part['name']}")
    for key,value in json.loads(line['specs']).items():
        if not spec_match({key:value},part['specs']):
            notes.append(f"{key}不同：BOM {value}；库存 {part['specs'].get(key) or '未填写'}")
    return notes


def unmatched_reason(line, candidates):
    if line.get('warnings'):return '源文件用量与位号需要核对后确认关联'
    if line['name'].casefold() in ('nc','dnp','不贴','不焊') or line['value'].casefold() in ('nc','dnp','不贴','不焊'):
        return 'BOM 标记为不贴装，请确认该行是否需要备料'
    codes=supplier_codes(line.get('supplier_code'))
    catalog=[p for p in candidates if text(p['supplier_code']).casefold() in codes]
    if len(catalog)>1:return '商城编号命中多个器件档案，请选择实际使用的器件'
    if not candidates:return '库存未找到对应商城编号或完整型号，请建档或核对替代料'
    if codes and not catalog:return '未找到 BOM 指定的商城编号；同型号或同规格候选需核对为替代料'
    if len(candidates)==1:
        differences=match_notes(line,candidates[0])
        if differences:return '；'.join(differences)
    return '存在多个候选，或缺少匹配所需的类别、数值、封装，请核对'


def match_line(line, parts):
    if line['confirmed'] and line['part_id']:
        chosen = next((p for p in parts if p['id'] == line['part_id'] and not p['archived']), None)
        return chosen, [], 'manual' if chosen else 'unmatched'
    candidates, exact, by_code, by_model, by_value = [], [], [], [], []
    codes=supplier_codes(line.get('supplier_code'))
    passive = line['category'] in REQUIRED_SPECS
    req_specs = json.loads(line['specs'])
    complete=all(key in req_specs for key in REQUIRED_SPECS.get(line['category'],()))
    sufficient = bool(line['package'] and line['category'])
    for p in parts:
        if p['archived']:continue
        same_name = p['name'].casefold() == line['name'].casefold()
        part_code=text(p['supplier_code']).casefold()
        same_code = bool(codes) and part_code in codes
        same_value = bool(line['value']) and p['category'] == line['category'] and canonical_value(p['value'], p['category']) == canonical_value(line['value'], line['category'])
        if not (same_code or same_name or same_value):continue
        candidates.append(p)
        # Catalog IDs identify products. EDA footprint labels and missing inventory
        # metadata are reported separately and do not veto an unambiguous identity.
        if same_code:by_code.append(p)
        category_ok = not line['category'] or p['category'] == line['category']
        model_value=p['category'] not in REQUIRED_SPECS and line['value'].casefold()==p['name'].casefold()
        value_ok=not line['value'] or model_value or bool(p['value']) and canonical_value(p['value'],line['category'] or p['category'])==canonical_value(line['value'],line['category'] or p['category'])
        code_ok=not codes or not part_code or same_code
        if category_ok and value_ok and spec_match(req_specs,p['specs']) and code_ok:
            if same_name and p['category'] and p['category'] not in REQUIRED_SPECS:by_model.append(p)
            if sufficient and canonical_package(line['package']) == p['package'] and (not passive or same_value):
                (exact if complete else by_value).append(p)
    candidates.sort(key=lambda p:(text(p['supplier_code']).casefold() not in codes,p['package']!=line['package']))
    if line.get('warnings') or len(by_code)>1:
        return None,candidates,'review' if candidates else 'unmatched'
    if len(by_code)==1:return by_code[0],candidates,'supplier_code'
    if len(by_model)==1:return by_model[0],candidates,'model'
    if len(exact)==1:return exact[0],candidates,'exact'
    if len(by_value)==1:return by_value[0],candidates,'value_package'
    return None,candidates,'review' if candidates else 'unmatched'


def project_result(con, project_id):
    from .bom import load_bom_source, supply_mode
    project = one(con, 'projects', project_id)
    source=load_bom_source(con,project_id)
    project['source']={k:v for k,v in source.items() if k!='lines'}
    parts = part_rows(con)
    lines = rows(con, 'SELECT * FROM bom_lines WHERE project_id=? ORDER BY line_no', (project_id,))
    totals = defaultdict(int)
    for line in lines:
        line.update({'supplier_code':'','source_package':'','warnings':[],**source['lines'].get(line['id'],{})})
        line['supply_mode']=supply_mode(line)
        line.setdefault('supply_note','')
        chosen, candidates, match = match_line(line, parts)
        line['specs_data'] = json.loads(line['specs'])
        line['missing_specs']=[key for key in REQUIRED_SPECS.get(line['category'],()) if key not in line['specs_data']]
        line['match'] = match
        line['candidates'] = candidates
        line['part'] = chosen
        line['match_notes'] = match_notes(line,chosen) if chosen else []
        line['unmatched_reason'] = '' if chosen else unmatched_reason(line,candidates)
        line['demand'] = line['per_board'] * project['boards']
        if chosen and line['supply_mode']=='stock':
            totals[chosen['id']] += line['demand']
    remaining = {pid: available(con, pid, project_id) for pid in totals}
    shortages = []
    for line in lines:
        p = line['part']
        if line['supply_mode']!='stock':
            line.update(status=line['supply_mode'],shortage=0,available_before=0,unmatched_reason='')
            continue
        if p:
            qty = max(0, remaining[p['id']])
            line['available_before'] = qty
            line['shortage'] = max(0, line['demand'] - qty)
            remaining[p['id']] -= min(qty, line['demand'])
            uncertain = any(s['mode'] != 'exact' for s in p['locations'])
            line['status'] = 'enough' if not line['shortage'] else 'count' if uncertain else 'short'
        else:
            line['shortage'] = None
            line['status'] = 'review' if line['candidates'] else 'unmatched'
    for pid, demand in totals.items():
        p = next(p for p in parts if p['id'] == pid)
        short = max(0, demand - available(con, pid, project_id))
        if short:
            shortages.append({'part': p, 'quantity': short})
    project['lines'], project['shortages'] = lines, shortages
    stock_lines=[l for l in lines if l['supply_mode']=='stock']
    project['stock_lines']=len(stock_lines)
    project['omitted']=sum(l['supply_mode']=='omit' for l in lines)
    project['external']=sum(l['supply_mode']=='external' for l in lines)
    project['unresolved'] = sum(1 for l in stock_lines if not l['part'])
    project['uncertain'] = sum(1 for l in lines if l['status'] == 'count')
    project['ready'] = bool(stock_lines) and not project['unresolved'] and not shortages
    return project


def reserve_project(con, project_id):
    project = project_result(con, project_id)
    need(project['status'] == 'draft', '只有备料中的项目可以占用库存')
    need(project['ready'], '仍有待确认、待盘点或缺料项，无法占用库存')
    totals = defaultdict(int)
    for line in project['lines']:
        if line['supply_mode']!='stock':continue
        p = line['part']
        totals[p['id']] += line['demand']
        # Freeze automatic matches for the exact BOM that is being reserved.
        con.execute("UPDATE bom_lines SET part_id=?,reason=CASE WHEN confirmed=1 THEN reason ELSE '占用时锁定匹配' END,confirmed=1 WHERE id=?", (p['id'], line['id']))
    for pid, qty in totals.items():
        need(available(con, pid) >= qty, '可用库存发生变化，请重新核料')
        con.execute('INSERT INTO reservations VALUES(?,?,?)', (project_id, pid, qty))
    con.execute("UPDATE projects SET status='reserved' WHERE id=?", (project_id,))


def issue_project(con, project_id):
    project = one(con, 'projects', project_id)
    need(project['status'] == 'reserved', '请先占用库存，再统一领料')
    reserved = rows(con, 'SELECT * FROM reservations WHERE project_id=?', (project_id,))
    need(reserved, '项目没有占用记录')
    for reservation in reserved:
        qty = reservation['quantity']
        stocks = rows(con, "SELECT * FROM stock WHERE part_id=? AND mode='exact' ORDER BY slot_id", (reservation['part_id'],))
        for stock in stocks:
            take = min(qty, stock['quantity'])
            if take:
                con.execute('UPDATE stock SET quantity=quantity-? WHERE id=?', (take, stock['id']))
                journal(con, stock['part_id'], stock['slot_id'], '项目领料', stock['quantity'], stock['quantity'] - take, project['name'])
                qty -= take
        need(qty == 0, '在库数量不足，领料已撤销')
    con.execute('DELETE FROM reservations WHERE project_id=?', (project_id,))
    con.execute("UPDATE projects SET status='built' WHERE id=?", (project_id,))
