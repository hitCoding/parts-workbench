import csv
import io
import json
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from .db import Store, rows, one, uid, now
from .domain import (Problem, need, text, integer, create_part, create_box, set_stock, move_stock,
                     part_rows, stock_rows, project_result, reserve_project, issue_project)
from .imports import table_file, validate_rows, import_inventory, MAX_UPLOAD
from .auth import auth_routes, session_user, COOKIE
from .extensions import extension_routes
from .procurement import purchase_routes, order_records
from .bom import save_bom_source, SOURCE_PREFIX
from .receipts import receive_purchase

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir=None):
    store = Store(data_dir or os.getenv('PARTS_DATA_DIR', ROOT / 'data'))

    @asynccontextmanager
    async def lifespan(app):
        store.init()
        # Keep an independent startup snapshot, using SQLite's consistent backup API.
        store.snapshot(store.backup_name('startup'))
        yield

    app = FastAPI(title='料格 · 电子物料工作台', version='2.0.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    from .picking import picking_routes
    app.include_router(picking_routes(store))
    app.include_router(auth_routes(store))
    app.include_router(extension_routes(store))
    app.include_router(purchase_routes(store))

    @app.middleware('http')
    async def same_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin:
            if urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'请从本应用页面操作'}, status_code=403)
        # Reject DNS-rebinding hosts for the default local-only configuration.
        allowed = os.getenv('PARTS_ALLOWED_HOSTS', 'localhost,127.0.0.1,::1,testserver').split(',')
        if request.url.hostname not in allowed:
            return JSONResponse({'detail':'访问地址未配置'},status_code=403)
        path=request.url.path
        public=path in ('/api/health','/api/auth/status','/api/auth/login','/api/auth/setup')
        user=None
        if path.startswith('/api/') and not public:
            user=session_user(store,request.cookies.get(COOKIE))
            if not user:
                return JSONResponse({'detail':'请先登录'},status_code=401)
            request.state.user=user
            admin_only=path in ('/api/backup','/api/audit','/api/users') or path.startswith(('/api/users/','/api/restore'))
            if admin_only and user['role']!='admin':
                return JSONResponse({'detail':'仅管理员可以操作'},status_code=403)
            read_only=request.method in ('GET','HEAD','OPTIONS') or path in ('/api/scan/resolve','/api/auth/logout','/api/auth/password')
            if not read_only and user['role']=='viewer':
                return JSONResponse({'detail':'只读账号不能修改数据'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        if path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        if user and request.method not in ('GET','HEAD','OPTIONS') and response.status_code<400:
            with store.transaction() as con:
                con.execute('INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)',(uid(),user['id'],user['username'],request.method,path,response.status_code,now()))
        return response

    @app.exception_handler(Problem)
    async def business_error(request, exc):
        return JSONResponse({'detail':exc.message},status_code=exc.status)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse({'detail':'数据冲突：盒号重复、格子已占用，或记录正被使用'},status_code=409)

    @app.get('/api/state')
    def state():
        with store.transaction() as con:
            boxes = rows(con,'SELECT * FROM boxes ORDER BY code')
            for box in boxes:
                box['slots'] = rows(con,'SELECT * FROM slots WHERE box_id=? ORDER BY row_no,col_no',(box['id'],))
            orders=order_records(con)
            parts=part_rows(con)
            parts_by_id={p['id']:p for p in parts}
            orders_by_purchase={identity:order for order in orders for identity in order['purchase_ids']}
            purchases=rows(con,'''SELECT pu.*,p.name part_name,p.package,p.specs,pr.name project_name FROM purchases pu
                JOIN parts p ON p.id=pu.part_id LEFT JOIN projects pr ON pr.id=pu.project_id ORDER BY pu.created_at DESC''')
            for item in purchases:
                order=orders_by_purchase.get(item['id'],{})
                item['order_id']=order.get('order_id','')
                item['source_shipped']=order.get('shipped',{}).get(item['id'])
                item['product_name']=order.get('items',{}).get(item['id'],{}).get('description') or parts_by_id[item['part_id']]['product_name']
                item['order_line_no']=order.get('purchase_ids',[]).index(item['id'])+1 if order else None
            purchases.sort(key=lambda p:(orders_by_purchase.get(p['id'],{}).get('created_at',p['created_at']),-(p['order_line_no'] or 0)),reverse=True)
            return {'parts':parts,'boxes':boxes,'purchase_orders':orders,
                    'projects':rows(con,'SELECT * FROM projects ORDER BY created_at DESC'),
                    'purchases':purchases,
                    'movements':rows(con,'''SELECT m.*,p.name part_name,b.code box_code,sl.row_no,sl.col_no FROM movements m JOIN parts p ON p.id=m.part_id
                        LEFT JOIN slots sl ON sl.id=m.slot_id LEFT JOIN boxes b ON b.id=sl.box_id ORDER BY m.created_at DESC LIMIT 300'''),
                    'storage':{'database':str(store.path),'backups':str(store.backups),'schema_version':2}}

    @app.post('/api/parts')
    def add_part(data:dict):
        with store.transaction() as con:
            return {'id':create_part(con,data)}

    @app.put('/api/parts/{pid}')
    def edit_part(pid:str,data:dict):
        with store.transaction() as con:
            return {'id':create_part(con,data,pid)}

    @app.post('/api/parts/{pid}/archive')
    def archive(pid:str,data:dict):
        with store.transaction() as con:
            one(con,'parts',pid)
            archived = bool(data.get('archived',True))
            if archived:
                need(not con.execute('SELECT 1 FROM reservations WHERE part_id=?',(pid,)).fetchone(),'器件已被项目占用，请先释放')
                need(not con.execute("SELECT 1 FROM stock WHERE part_id=? AND (quantity>0 OR mode='unknown')",(pid,)).fetchone(),'请先处理该器件的库存再归档')
                need(not con.execute("SELECT 1 FROM purchases WHERE part_id=? AND status NOT IN('received','cancelled')",(pid,)).fetchone(),'该器件仍有未完成采购')
            con.execute('UPDATE parts SET archived=? WHERE id=?',(int(archived),pid))
            return {'ok':True}

    @app.post('/api/boxes')
    def add_box(data:dict):
        with store.transaction() as con:
            return {'id':create_box(con,data)}

    @app.put('/api/boxes/{bid}')
    def edit_box(bid:str,data:dict):
        with store.transaction() as con:
            return {'id':create_box(con,data,bid)}

    @app.delete('/api/boxes/{bid}')
    def delete_box(bid:str):
        with store.transaction() as con:
            one(con,'boxes',bid)
            used=con.execute('SELECT 1 FROM stock JOIN slots ON slots.id=stock.slot_id WHERE slots.box_id=?',(bid,)).fetchone()
            history=con.execute('SELECT 1 FROM movements JOIN slots ON slots.id=movements.slot_id WHERE slots.box_id=?',(bid,)).fetchone()
            need(not used and not history,'有库存或历史记录的盒子不能删除，可修改名称或将库存移到新盒')
            con.execute('DELETE FROM slots WHERE box_id=?',(bid,))
            con.execute('DELETE FROM boxes WHERE id=?',(bid,))
            return {'ok':True}

    @app.post('/api/stock')
    def stock(data:dict):
        with store.transaction() as con:
            set_stock(con,data)
            return {'ok':True}

    @app.post('/api/stock/move')
    def move(data:dict):
        with store.transaction() as con:
            move_stock(con,data)
            return {'ok':True}

    @app.post('/api/stock/{sid}/clear')
    def clear_slot(sid:str):
        with store.transaction() as con:
            s=one(con,'stock',sid)
            need(s['quantity']==0,'只有已盘点为零的格子可以清空')
            con.execute('DELETE FROM stock WHERE id=?',(sid,))
            return {'ok':True}

    @app.post('/api/files/parse')
    def parse(file:UploadFile=File(...),kind:str=Form('auto')):
        return table_file(file,kind)

    @app.post('/api/import/preview')
    def preview(data:dict):
        with store.transaction() as con:
            entries=validate_rows(con,data,'bom' if data.get('kind')=='bom' else 'inventory')
            return {'rows':entries,'errors':sum(bool(e['errors']) for e in entries)}

    @app.post('/api/import/commit')
    def commit(data:dict):
        with store.lock:
            store.snapshot(store.backup_name('before-import'))
            with store.transaction() as con:
                return {'count':import_inventory(con,data)}

    @app.post('/api/projects')
    def add_project(data:dict):
        with store.transaction() as con:
            entries=validate_rows(con,data,'bom')
            errors=[f"第 {e['row_no']} 行：{'；'.join(e['errors'])}" for e in entries if e['errors']]
            need(not errors,'\n'.join(errors[:10]))
            name=text(data.get('name'),200)
            need(name,'请填写项目名称')
            boards=integer(data.get('boards',1),'板数',1,100000)
            pid=uid()
            con.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(pid,name,boards,'draft',now()))
            source_lines=[]
            for index,e in enumerate(entries,start=1):
                identity=uid();source_lines.append((identity,e))
                con.execute('''INSERT INTO bom_lines(id,project_id,line_no,name,category,package,value,specs,per_board,designators)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',(identity,pid,index,e['name'],e['category'],e['package'],e['value'],json.dumps(e['specs'],ensure_ascii=False),e['per_board'],e['designators']))
            save_bom_source(con,pid,data,source_lines)
            return {'id':pid}

    @app.get('/api/projects/{pid}')
    def project(pid:str):
        with store.transaction() as con:
            return project_result(con,pid)

    @app.put('/api/projects/{pid}')
    def edit_project(pid:str,data:dict):
        with store.transaction() as con:
            p=one(con,'projects',pid)
            need(p['status']=='draft','请先释放占用；已领料的项目不能修改')
            name=text(data.get('name',p['name']),200)
            need(name,'项目名称不能为空')
            boards=integer(data.get('boards',p['boards']),'板数',1,100000)
            con.execute('UPDATE projects SET name=?,boards=? WHERE id=?',(name,boards,pid))
            return {'ok':True}

    @app.delete('/api/projects/{pid}')
    def delete_project(pid:str,data:dict):
        need(data.get('confirm') is True,'请确认删除项目')
        with store.lock:
            with store.transaction() as con:
                one(con,'projects',pid)
            previous=store.backup_name('before-project-delete')
            try:store.snapshot(previous)
            except (OSError,sqlite3.Error):raise Problem('备份失败，项目未删除，请检查存储空间后重试',503)
            with store.transaction() as con:
                p=one(con,'projects',pid)
                purchases=rows(con,'SELECT id,notes FROM purchases WHERE project_id=?',(pid,))
                for purchase in purchases:
                    note='原项目：'+p['name']+'（已删除）'
                    con.execute('UPDATE purchases SET project_id=NULL,notes=? WHERE id=?',
                                ('\n'.join(filter(None,[note,purchase['notes']])),purchase['id']))
                released=con.execute('SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE project_id=?',(pid,)).fetchone()[0]
                con.execute('DELETE FROM reservations WHERE project_id=?',(pid,))
                con.execute('DELETE FROM bom_lines WHERE project_id=?',(pid,))
                con.execute('DELETE FROM settings WHERE key=?',(SOURCE_PREFIX+pid,))
                con.execute('DELETE FROM projects WHERE id=?',(pid,))
            return {'ok':True,'previous_backup':previous.name,'released':released,'purchases_preserved':len(purchases)}

    @app.put('/api/bom/{lid}/match')
    def manual_match(lid:str,data:dict):
        with store.transaction() as con:
            line=one(con,'bom_lines',lid)
            p=one(con,'projects',line['project_id'])
            need(p['status']=='draft','请先释放项目占用再修改匹配')
            part_id=data.get('part_id') or None
            if part_id:
                part=one(con,'parts',part_id)
                need(not part['archived'],'器件已归档')
                need(data.get('confirmed') is True and text(data.get('reason')),'请核对参数、封装与引脚，并填写确认依据')
            con.execute('UPDATE bom_lines SET part_id=?,confirmed=?,reason=? WHERE id=?',(part_id,int(bool(part_id)),text(data.get('reason')),lid))
            return {'ok':True}

    @app.post('/api/projects/{pid}/{action}')
    def project_action(pid:str,action:str):
        with store.transaction() as con:
            if action=='reserve':
                reserve_project(con,pid)
            elif action=='release':
                p=one(con,'projects',pid)
                need(p['status']=='reserved','项目没有占用库存')
                con.execute('DELETE FROM reservations WHERE project_id=?',(pid,))
                con.execute("UPDATE projects SET status='draft' WHERE id=?",(pid,))
            elif action=='issue':
                issue_project(con,pid)
            else:
                raise Problem('操作不存在',404)
            return {'ok':True}

    @app.post('/api/purchases')
    def add_purchase(data:dict):
        with store.transaction() as con:
            part=one(con,'parts',data.get('part_id'))
            need(not part['archived'],'器件已归档')
            qty=integer(data.get('quantity'),'采购数量',1)
            pid=data.get('project_id') or None
            if pid:
                one(con,'projects',pid)
            item_id=uid()
            con.execute('INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?)',(item_id,part['id'],pid,qty,0,'draft',text(data.get('supplier')),text(data.get('notes')),now()))
            return {'id':item_id}

    @app.post('/api/purchases/{pid}/order')
    def order(pid:str):
        with store.transaction() as con:
            p=one(con,'purchases',pid)
            need(p['status']=='draft','只有待购买项可以标记已购买')
            con.execute("UPDATE purchases SET status='ordered' WHERE id=?",(pid,))
            return {'ok':True}

    @app.post('/api/purchases/{pid}/cancel')
    def cancel(pid:str):
        with store.transaction() as con:
            p=one(con,'purchases',pid)
            need(p['status'] in ('draft','ordered'),'已部分或全部入库的采购不能取消')
            con.execute("UPDATE purchases SET status='cancelled' WHERE id=?",(pid,))
            return {'ok':True}

    @app.post('/api/purchases/{pid}/receive')
    def receive(pid:str,data:dict):
        with store.transaction() as con:
            p=one(con,'purchases',pid)
            need(p['status'] in ('ordered','partial'),'请先标记已购买，已完成采购不能重复入库')
            qty=integer(data.get('quantity'),'验收数量',1)
            need(qty<=p['quantity']-p['received'],'验收数量超过尚未入库数量')
            receive_purchase(con,p,data.get('slot_id'),qty,'采购验收入库 '+pid[:8])
            return {'ok':True}

    @app.get('/api/export/{kind}')
    def export(kind:str):
        with store.transaction() as con:
            headers=['名称','类别','封装','数值','数量','数量方式','盒号','格号','商城编号','耐压','介质','精度','功率','额定电流','备注']
            output=io.StringIO();writer=csv.writer(output);writer.writerow(headers)
            if kind=='inventory':
                for p in part_rows(con):
                    if p['archived']: continue
                    for s in p['locations'] or [{}]:
                        cell=chr(65+s['row_no'])+str(s['col_no']+1) if s else ''
                        row=[p['name'],p['category'],p['package'],p['value'],s.get('quantity',0),s.get('mode','exact'),s.get('box_code',''),cell,p['supplier_code'],*[p['specs'].get(k,'') for k in ('耐压','介质','精度','功率','额定电流')],p['notes']]
                        writer.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for v in row])
            elif kind=='template':
                writer.writerow(['10 kΩ','电阻','0603','10k','100','exact','R01','A1','','','','1%','0.1W','','请先创建 R01 盒'])
            elif kind=='bom-template':
                output=io.StringIO();writer=csv.writer(output)
                writer.writerow(['名称','类别','封装','数值','单板用量','位号','耐压','介质','精度','功率','额定电流'])
                writer.writerow(['10 kΩ','电阻','0603','10k',2,'R1,R2','','','1%','0.1W',''])
                writer.writerow(['100 nF','电容','0603','100nF',1,'C1','16V','X7R','','',''])
                writer.writerow(['AMS1117-3.3','芯片','SOT-223','',1,'U1','','','','',''])
            else: raise Problem('导出类型不存在',404)
            return Response(('\ufeff'+output.getvalue()).encode('utf-8'),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{kind}.csv"'})

    @app.get('/api/backup')
    def backup():
        target=store.backup_name('manual')
        store.snapshot(target)
        return FileResponse(target,filename=target.name,media_type='application/vnd.sqlite3')

    @app.post('/api/restore/preview')
    def restore_preview(file:UploadFile=File(...)):
        data=file.file.read(200*1024*1024+1)
        need(len(data)<=200*1024*1024,'备份不能超过 200 MB')
        path=validate_backup(data)
        try:
            with closing(sqlite3.connect(path)) as con:
                summary={t:con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('parts','boxes','stock','projects','purchases','movements')}
            return {'counts':summary}
        finally: path.unlink(missing_ok=True)

    def validate_backup(data):
        need(data.startswith(b'SQLite format 3\x00'),'请选择本系统导出的 SQLite 备份')
        fd,path=tempfile.mkstemp(suffix='.sqlite3');os.close(fd);path=Path(path);path.write_bytes(data)
        try:
            with store.lock, closing(sqlite3.connect(f'file:{path}?mode=ro&immutable=1',uri=True)) as source, closing(sqlite3.connect(':memory:')) as target:
                version=source.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                need(version in ('1','2'),'备份版本不兼容')
                schema='schema-v1.sql' if version=='1' else 'schema.sql'
                target.executescript(Path(__file__).with_name(schema).read_text())
                expected=target.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
                actual=source.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
                need([tuple(r) for r in expected]==[tuple(r) for r in actual],'备份结构不兼容，或包含额外数据库对象')
                need(source.execute('PRAGMA integrity_check').fetchone()[0]=='ok','备份完整性检查失败')
                need(not source.execute('PRAGMA foreign_key_check').fetchall(),'备份的关联数据不完整')
            if version=='1':
                with closing(sqlite3.connect(path)) as migrated:
                    migrated.executescript(Path(__file__).with_name('schema.sql').read_text())
            return path
        except Problem:
            path.unlink(missing_ok=True);raise
        except Exception:
            path.unlink(missing_ok=True);raise Problem('备份损坏或格式不兼容')

    @app.post('/api/restore')
    def restore(request:Request,file:UploadFile=File(...)):
        need(request.headers.get('x-confirm-restore')=='RESTORE','请先预览并确认恢复备份')
        data=file.file.read(200*1024*1024+1)
        need(len(data)<=200*1024*1024,'备份不能超过 200 MB')
        source_path=validate_backup(data)
        try:
            with store.lock:
                previous=store.backup_name('before-restore');store.snapshot(previous)
                # Restore business data while preserving current identities and sessions.
                with store.transaction() as current:
                    identities={table:rows(current,f'SELECT * FROM {table}') for table in ('users','sessions','audit_log')}
                with closing(sqlite3.connect(source_path)) as src, closing(store.connect()) as dest:
                    src.execute('DELETE FROM sessions');src.execute('DELETE FROM users');src.execute('DELETE FROM audit_log')
                    for table,records in identities.items():
                        for record in records:
                            columns=list(record)
                            src.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',tuple(record.values()))
                    src.commit()
                    src.backup(dest)
            return {'ok':True,'previous_backup':previous.name}
        finally:
            source_path.unlink(missing_ok=True)

    @app.post('/api/demo')
    def demo():
        with store.transaction() as con:
            need(not con.execute('SELECT 1 FROM parts').fetchone() and not con.execute('SELECT 1 FROM boxes').fetchone(),'仅空库可以载入示例，现有数据不会被覆盖')
            bids={code:create_box(con,{'code':code,'name':name,'area':'工作台左侧','rows':3,'cols':4}) for code,name in [('R01','贴片电阻'),('C01','贴片电容'),('U01','芯片与分立器件')]}
            sample=[('10 kΩ','电阻','0603','10k',{'精度':'1%','功率':'0.1W'},'R01',0,180,'exact'),
                ('1 kΩ','电阻','0603','1k',{'精度':'1%','功率':'0.1W'},'R01',1,200,'estimate'),
                ('4.7 kΩ','电阻','0603','4.7k',{'精度':'1%','功率':'0.1W'},'R01',2,100,'estimate'),
                ('100 nF','电容','0603','100nF',{'耐压':'16V','介质':'X7R'},'C01',0,200,'exact'),
                ('10 μF','电容','0603','10uF',{'耐压':'10V','介质':'X5R'},'C01',4,100,'estimate'),
                ('AO3400A','MOS管','SOT-23','',{'耐压':'30V'},'U01',0,12,'exact'),
                ('AMS1117-3.3','芯片','SOT-223','',{},'U01',2,0,'exact')]
            for name,cat,pkg,val,specs,code,col,qty,mode in sample:
                pid=create_part(con,{'name':name,'category':cat,'package':pkg,'value':val,'specs':specs})
                slot=con.execute('SELECT id FROM slots WHERE box_id=? AND row_no=? AND col_no=?',(bids[code],col//4,col%4)).fetchone()[0]
                set_stock(con,{'part_id':pid,'slot_id':slot,'quantity':qty,'mode':mode,'note':'可选示例初始库存'})
            return {'ok':True}

    dist=ROOT/'frontend'/'dist'
    if dist.exists():
        app.mount('/',StaticFiles(directory=dist,html=True),name='frontend')
    else:
        @app.get('/')
        def missing_build():
            return JSONResponse({'message':'请先运行前端 npm run build，或使用 Vite 开发服务。'})
    return app


app=create_app()
