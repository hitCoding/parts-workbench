"""Printable, read-only box layouts for the current BOM picking allocation."""
import re
from collections import defaultdict
from html import escape
from pathlib import Path
from .db import rows
from .domain import part_rows


def clean_name(value):
    value=re.split(r'[;；]',value or '',maxsplit=1)[0]
    return re.sub(r'\s+',' ',re.sub(r'编带|托盘|系列|袋装','',value)).strip()


def location_map(con,plan):
    picks=defaultdict(list);references=defaultdict(list)
    for item in plan['rows']:picks[item['slot_id']].append(item)
    for item in plan['pending']:
        for slot_id in item['reference_slot_ids']:references[slot_id].append(item)
    parts=part_rows(con)
    contents={s['slot_id']:(p,s) for p in parts for s in p['locations']}
    slots=rows(con,'SELECT * FROM slots ORDER BY row_no,col_no')
    sheets=[]
    for box in rows(con,'SELECT * FROM boxes ORDER BY code'):
        box_slots=[s for s in slots if s['box_id']==box['id']]
        if not any(s['id'] in picks or s['id'] in references for s in box_slots):continue
        # Wide/tall boxes are tiled, never reflowed: each tile keeps real coordinates.
        for start_row in range(0,box['rows'],3):
            for start_col in range(0,box['cols'],8):
                tile=[s for s in box_slots if start_row<=s['row_no']<start_row+3 and start_col<=s['col_no']<start_col+8]
                if not any(s['id'] in picks or s['id'] in references for s in tile):continue
                cells=[]
                for slot in tile:
                    part,stock=contents.get(slot['id'],({},{}))
                    allocated=picks[slot['id']];pending=references[slot['id']]
                    quantity=sum(i['quantity'] for i in allocated)
                    description=clean_name(part.get('product_name')) or part.get('value') or part.get('name','')
                    model=part.get('name','')
                    chip=part.get('category') not in ('电阻','电容','电感')
                    cells.append({**slot,'label':f"{chr(65+slot['row_no'])}{slot['col_no']+1}",
                                  'title':model if chip and model else description,
                                  'description':description if chip and description.casefold()!=model.casefold() else '',
                                  'package':part.get('package',''),'quantity':stock.get('quantity'),
                                  'mode':stock.get('mode',''),'empty':not part,'take':quantity,
                                  'highlight':'pick' if quantity else 'reference' if pending else 'muted',
                                  'allocations':allocated,'pending':pending})
                end_row=min(start_row+3,box['rows']);end_col=min(start_col+8,box['cols'])
                sheets.append({**box,'cells':cells,'grid_cols':end_col-start_col,'grid_rows':end_row-start_row,
                               'range':f'{chr(65+start_row)}–{chr(64+end_row)} 行 · {start_col+1}–{end_col} 列',
                               'split':box['rows']>3 or box['cols']>8,
                               'pick_cells':sum(c['take']>0 for c in cells),
                               'take':sum(c['take'] for c in cells)})
    return sheets


def esc(value):
    return escape(str(value))


def render_cell(cell):
    classes='cell '+cell['highlight']+(' empty' if cell['empty'] else '')
    heading=f'<div class="cell-top"><b>{esc(cell["label"])}</b><span>{esc(cell["package"])}</span></div>'
    if cell['empty']:return f'<article class="{classes}">{heading}<span class="empty-label">空格</span></article>'
    description=f'<div class="description">{esc(cell["description"])}</div>' if cell['description'] else ''
    mode={'exact':'精确','estimate':'估算','unknown':'未知'}[cell['mode']]
    stock='数量未知' if cell['mode']=='unknown' else f'存 {cell["quantity"]} · {mode}'
    badge=f'<strong class="take">取 {cell["take"]} 颗</strong>' if cell['take'] else '<strong class="reference-label">仅参考 · 暂不取料</strong>' if cell['pending'] else ''
    entries=cell['allocations'] or cell['pending']
    designators=' / '.join(f'#{item["line_no"]} · '+(item['designators'] or '无位号') for item in entries)
    refs=f'<div class="designators">{esc(designators)}</div>' if designators else ''
    return f'''<article class="{classes}" data-slot="{esc(cell['id'])}" data-take="{cell['take']}">
{heading}<div class="cell-name">{esc(cell['title'])}</div>{description}
<div class="cell-bottom">{badge}<span>{stock}</span></div>{refs}</article>'''


def render_map(plan,sheets):
    title=esc(plan['name'])
    summary=f'{plan["boards"]} 块 · 建议取料 {plan["pick_lines"]} 项 / {plan["pick_quantity"]} 颗 · 待处理 {len(plan["pending"])} 项 · 自备 {len(plan["external"])} 项 · 不贴装 {len(plan["omitted"])} 项'
    status_note='项目已领料：图中只标参考位置，请勿重复取料。' if plan['status']=='built' else '按当前精确可用库存分配；生成或打印不会占用、扣减库存。'
    pages=[]
    for number,sheet in enumerate(sheets,1):
        cells=''.join(render_cell(c) for c in sheet['cells'])
        section=f' · 本页 {esc(sheet["range"])}' if sheet['split'] else ''
        pages.append(f'''<section class="sheet">
<header class="sheet-header"><div><div class="eyebrow">BOM 备料位置图</div><h1>{title}</h1><p>{summary}</p></div><span class="page-number">{number} / {len(sheets)}</span></header>
<div class="box-heading"><div><b class="box-code">{esc(sheet['code'])}</b><h2>{esc(sheet['name'])}</h2><span>{sheet['rows']} × {sheet['cols']} 格{section}</span></div><span>{esc(sheet['area'] or '未设置区域')} · 本页取 {sheet['take']} 颗</span></div>
<div class="grid" style="--cols:{sheet['grid_cols']}">{cells}</div>
<footer><span><b class="legend-pick">粗框＋取料数</b> 当前 BOM 要取料 <b class="legend-reference">虚线框</b> 待处理 / 参考位置；其余格子仅作定位</span><span>{status_note}</span></footer></section>''')
    if not sheets:pages.append(f'<section class="sheet empty-sheet"><h1>{title} · 备料位置图</h1><p>{summary}</p><p>当前没有可标记的库存位置。请查看下面的待处理或外部自备清单。</p></section>')
    extras=[]
    for key,label in (('pending','待处理'),('external','外部自备 / 样品'),('omitted','不贴装')):
        for item in plan[key]:
            extras.append([label,f'#{item["line_no"]}',item['designators'],item['name'],item['demand'],item['location'],item['notes']])
    # Keep matching caveats available on paper without burying the physical layout.
    seen=set()
    for item in plan['rows']:
        if item['notes'] and item['id'] not in seen:
            extras.append(['匹配提示',f'#{item["line_no"]}',item['designators'],item['name'],item['demand'],'',item['notes']]);seen.add(item['id'])
    if extras:
        headers=''.join(f'<th>{h}</th>' for h in ('分类','BOM行','位号','器件','需求','参考位置','说明'))
        body=''.join('<tr>'+''.join(f'<td>{esc(v)}</td>' for v in row)+'</tr>' for row in extras)
        pages.append(f'<section class="sheet appendix"><h1>{title} · 备料说明</h1><p>待处理、自备和不贴装项目不计入图中建议取料数。匹配提示请结合实物和 BOM 核对。</p><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></section>')
    warning=f'<p class="warning">有 {len(plan["pending"])} 项待处理：先按高亮格子找料，剩余项目见末尾备料说明。</p>' if plan['pending'] else ''
    css=Path(__file__).with_name('picking_map.css').read_text()
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · BOM 位置图</title><style>{css}</style></head><body>
<div class="toolbar"><div><strong>打印 BOM 位置图</strong><p>A4 横向 · 每页最多 3 × 8 格 · 大盒按原行列分区，仅输出有关区段</p><p>{status_note}</p>{warning}</div><button onclick="window.print()">打印 / 保存 PDF</button></div>
<main>{''.join(pages)}</main></body></html>'''
