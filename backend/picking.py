"""Read-only picking plans and per-project supply choices, stored with BOM metadata."""
import csv
import io
import json
from html import escape
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response
from .bom import SOURCE_PREFIX, load_bom_source
from .db import one, now
from .domain import need, text, project_result

SUPPLY_LABELS={'stock':'库存取料','omit':'不贴装','external':'外部自备 / 样品'}


def picking_plan(con, pid):
    project=project_result(con,pid)
    plan={'project_id':pid,'name':project['name'],'boards':project['boards'],'status':project['status'],
          'generated_at':now(),'rows':[],'pending':[],'external':[],'omitted':[]}
    remaining={}
    for line in project['lines']:
        part=line['part']
        item={k:line[k] for k in ('id','line_no','name','designators','demand','supply_mode','supply_note')}
        item.update(model=part['name'] if part else '',product_name=part['product_name'] if part else '',
                    package=part['package'] if part else line['package'],
                    supplier_code=part['supplier_code'] if part else line['supplier_code'],
                    quantity=0,location='',notes='；'.join(line['match_notes']),
                    reference_slot_ids=[s['slot_id'] for s in part['locations']] if part else [])
        if line['supply_mode']!='stock':
            item['notes']=line['supply_note'] or SUPPLY_LABELS[line['supply_mode']]
            plan['omitted' if line['supply_mode']=='omit' else 'external'].append(item)
            continue
        if project['status']=='built':
            item.update(notes='项目已领料；本清单仅供位置参考',location='、'.join(f"{s['box_code']} / {chr(65+s['row_no'])}{s['col_no']+1}" for s in part['locations']) if part else '')
            plan['pending'].append(item)
            continue
        take=min(line['demand'],line.get('available_before',0)) if part else 0
        for slot in sorted(part['locations'] if part else [],key=lambda s:s['slot_id']):
            if slot['mode']!='exact':continue
            available=remaining.setdefault(slot['id'],slot['quantity'])
            quantity=min(take,available)
            if quantity:
                remaining[slot['id']]-=quantity;take-=quantity
                plan['rows'].append({**item,'quantity':quantity,'location':f"{slot['box_code']} / {chr(65+slot['row_no'])}{slot['col_no']+1}",
                                     'area':slot['area'],'slot_id':slot['slot_id'],
                                     'sort_key':(slot['box_code'],slot['row_no'],slot['col_no'],line['line_no'])})
        if not part or line['shortage']:
            message=line['unmatched_reason'] if not part else f"尚缺 {line['shortage']} 颗"+('，含估算/未知数量，请盘点' if line['status']=='count' else '')
            if part:
                item['location']='、'.join(f"{s['box_code']} / {chr(65+s['row_no'])}{s['col_no']+1}" for s in part['locations'])
            plan['pending'].append({**item,'notes':message,'missing':line['shortage']})
    plan['rows'].sort(key=lambda item:item['sort_key'])
    for item in plan['rows']:item.pop('sort_key')
    plan['pick_lines']=len({item['id'] for item in plan['rows']})
    plan['pick_quantity']=sum(item['quantity'] for item in plan['rows'])
    return plan


def export_rows(plan):
    for key,label in (('rows','库存取料'),('pending','待处理'),('external','外部自备 / 样品'),('omitted','不贴装')):
        for item in plan[key]:
            yield [label,item['line_no'],item['designators'],item['name'],item['model'],item['package'],item['supplier_code'],
                   item['demand'],item['quantity'],item['location'],item['notes']]


HEADERS=['分类','BOM行','位号','需求名称','库存型号','封装','商城编号','需求数量','本位置建议取料','位置','说明']


def picking_routes(store):
    from .picking_map import location_map, render_map
    router=APIRouter(prefix='/api')

    @router.put('/bom/{lid}/supply')
    def set_supply(lid:str,data:dict):
        mode=data.get('mode');need(isinstance(mode,str) and mode in SUPPLY_LABELS,'备料方式无效')
        with store.transaction() as con:
            line=one(con,'bom_lines',lid);project=one(con,'projects',line['project_id'])
            need(project['status']=='draft','请先释放占用；已领料项目不能修改备料方式')
            source=load_bom_source(con,project['id'])
            source.setdefault('lines',{}).setdefault(lid,{}).update(supply_mode=mode,supply_note=text(data.get('note'),500))
            con.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (SOURCE_PREFIX+project['id'],json.dumps(source,ensure_ascii=False)))
        return {'ok':True}

    @router.get('/projects/{pid}/picking')
    def preview(pid:str):
        with store.transaction() as con:return picking_plan(con,pid)

    @router.get('/projects/{pid}/picking/map',response_class=HTMLResponse)
    def map_printable(pid:str):
        with store.transaction() as con:
            plan=picking_plan(con,pid)
            sheets=location_map(con,plan)
        return render_map(plan,sheets)

    @router.get('/projects/{pid}/picking.csv')
    def download(pid:str):
        with store.transaction() as con:plan=picking_plan(con,pid)
        output=io.StringIO();writer=csv.writer(output);writer.writerow(HEADERS)
        for row in export_rows(plan):
            writer.writerow(["'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value for value in row])
        return Response(('\ufeff'+output.getvalue()).encode('utf-8'),media_type='text/csv',
                        headers={'Content-Disposition':'attachment; filename="picking-list.csv"'})

    @router.get('/projects/{pid}/picking/print',response_class=HTMLResponse)
    def printable(pid:str):
        with store.transaction() as con:plan=picking_plan(con,pid)
        title=escape(plan['name'])
        body=''.join('<tr>'+''.join('<td>'+escape(str(value))+'</td>' for value in row)+'</tr>' for row in export_rows(plan))
        headers=''.join('<th>'+escape(h)+'</th>' for h in HEADERS)
        return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{title} · 位置清单</title>
<style>body{{font:13px system-ui,sans-serif;color:#243830;padding:24px}}h1{{font-size:22px}}table{{border-collapse:collapse;width:100%;font-size:11px}}th,td{{border:1px solid #ccc;padding:7px;text-align:left;overflow-wrap:anywhere}}thead{{display:table-header-group}}tr{{break-inside:avoid}}p{{line-height:1.7}}button{{padding:8px 16px}}@page{{size:A4 landscape;margin:12mm}}@media print{{button{{display:none}}body{{padding:0}}}}</style>
<button onclick="window.print()">打印 / 保存 PDF</button><h1>{title} · 位置清单</h1>
<p>{plan['boards']} 块 · 可取料 {plan['pick_lines']} 项 / {plan['pick_quantity']} 颗 · 待处理 {len(plan['pending'])} 项 · 外部自备 {len(plan['external'])} 项 · 不贴装 {len(plan['omitted'])} 项</p>
<p>本清单不占用或扣减库存。取料数量按生成时精确可用库存计算；待处理与外部自备项需另行落实。已领料项目仅供位置参考。</p>
<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></html>'''
    return router
