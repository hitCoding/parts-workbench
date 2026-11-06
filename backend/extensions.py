"""Attachments in SQLite, QR labels, local OCR and scan lookup."""
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import Response
import segno
from .db import one, rows, uid, now
from .domain import need, text, Problem

Image.MAX_IMAGE_PIXELS=20_000_000
OCR_LOCK=threading.Lock()
ROOT=Path(__file__).resolve().parent.parent


def image_bytes(data):
    need(len(data)<=8*1024*1024,'图片不能超过 8 MB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            need(image.width*image.height<=20_000_000,'图片像素过大')
            need(image.format in ('PNG','JPEG','WEBP'),'请使用 PNG / JPEG / WebP 图片')
            image.load()
            output=io.BytesIO();image.convert('RGB').save(output,'JPEG',quality=90)
            need(output.tell()<=8*1024*1024,'图片转换后过大，请缩小后上传')
            return output.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise Problem('无法读取图片，或图片像素过大')


def ocr_engine():
    if sys.platform=='darwin' and shutil.which('swiftc'):return 'Apple Vision（本机）'
    if shutil.which('tesseract'):return 'Tesseract（本机）'
    return None


def recognize(data):
    normalized=image_bytes(data)
    engine=ocr_engine();need(engine,'服务器未安装 OCR 引擎；Linux 可安装 tesseract-ocr 与中文语言包',503)
    with tempfile.TemporaryDirectory(prefix='parts-ocr-') as directory:
        path=Path(directory)/'input.jpg';path.write_bytes(normalized)
        try:
            if sys.platform=='darwin':
                binary=ROOT/'.runtime'/'ocr-vision'
                with OCR_LOCK:
                    if not binary.exists() or binary.stat().st_mtime<(ROOT/'backend'/'ocr.swift').stat().st_mtime:
                        binary.parent.mkdir(exist_ok=True)
                        subprocess.run(['swiftc',str(ROOT/'backend'/'ocr.swift'),'-o',str(binary)],check=True,capture_output=True,timeout=120)
                result=subprocess.run([str(binary),str(path)],check=True,capture_output=True,timeout=45)
                lines=json.loads(result.stdout)
            else:
                installed=subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,check=True,timeout=10).stdout
                languages='eng+chi_sim' if 'chi_sim' in installed else 'eng'
                result=subprocess.run(['tesseract',str(path),'stdout','-l',languages,'--psm','6'],capture_output=True,text=True,check=True,timeout=45)
                lines=[{'text':line,'confidence':None} for line in result.stdout.splitlines() if line.strip()]
        except subprocess.TimeoutExpired:raise Problem('本次识别超时，请裁剪图片后重试',504)
        except (subprocess.CalledProcessError,ValueError):raise Problem('本机 OCR 引擎运行失败，请检查安装与图片',503)
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['名称','类别','封装','数值','数量','数量方式','盒号','格号'])
    for line in lines[:300]:
        value=line['text'].strip()
        package=re.search(r'\b(0201|0402|0603|0805|1206|1210|SOT-?23|SOT-?223|SOIC-?\d+|LQFP-?\d+)\b',value,re.I)
        writer.writerow([value,'',package.group(1) if package else '','','','unknown','',''])
    return {'engine':engine,'text':'\n'.join(l['text'] for l in lines),'lines':lines,'csv':output.getvalue()}


def extension_routes(store):
    router=APIRouter(prefix='/api')

    @router.get('/capabilities')
    def capabilities():
        return {'ocr_engine':ocr_engine(),'attachments':'SQLite BLOB','schema_version':2,'supplier_connected':False,'offline_writes':False}

    @router.get('/parts/{pid}/attachments')
    def attachments(pid:str):
        with store.transaction() as con:
            one(con,'parts',pid)
            return rows(con,'SELECT id,part_id,name,mime,size,created_at FROM attachments WHERE part_id=? ORDER BY created_at DESC',(pid,))

    @router.post('/parts/{pid}/attachments')
    def upload(pid:str,file:UploadFile=File(...)):
        content=file.file.read(8*1024*1024+1)
        need(content and len(content)<=8*1024*1024,'附件须在 8 MB 以内')
        name=Path(file.filename or '附件').name[:180]
        if content.startswith(b'%PDF-'):
            mime='application/pdf';name=name if name.lower().endswith('.pdf') else name+'.pdf'
        else:
            content=image_bytes(content);mime='image/jpeg';name=Path(name).stem+'.jpg'
        with store.transaction() as con:
            one(con,'parts',pid)
            need(con.execute('SELECT COUNT(*) FROM attachments WHERE part_id=?',(pid,)).fetchone()[0]<12,'单个器件最多 12 个附件')
            need(con.execute('SELECT COALESCE(SUM(size),0) FROM attachments').fetchone()[0]+len(content)<=150*1024*1024,'附件总量已达 150 MB，请整理后上传')
            identity=uid()
            con.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?,?)',(identity,pid,name,mime,len(content),content,now()))
            return {'id':identity}

    @router.get('/attachments/{identity}')
    def attachment(identity:str):
        with store.transaction() as con:
            item=one(con,'attachments',identity)
            headers={'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'}
            if item['mime']=='application/pdf': headers['Content-Disposition']='attachment; filename="datasheet.pdf"'
            return Response(item['content'],media_type=item['mime'],headers=headers)

    @router.delete('/attachments/{identity}')
    def delete_attachment(identity:str):
        with store.lock:
            store.snapshot(store.backup_name('before-attachment-delete'))
            with store.transaction() as con:
                one(con,'attachments',identity);con.execute('DELETE FROM attachments WHERE id=?',(identity,))
        return {'ok':True}

    @router.get('/qr/{kind}/{identity}')
    def qr(kind:str,identity:str):
        need(kind in ('part','slot','box'),'标签类型不存在',404)
        with store.transaction() as con: one(con,{'part':'parts','slot':'slots','box':'boxes'}[kind],identity)
        code=segno.make(f'PARTS:{kind}:{identity}',micro=False,error='M')
        out=io.BytesIO();code.save(out,kind='svg',scale=3,border=4)
        return Response(out.getvalue(),media_type='image/svg+xml',headers={'Cache-Control':'private, max-age=300','X-Content-Type-Options':'nosniff'})

    @router.post('/scan/resolve')
    def resolve(data:dict):
        value=text(data.get('code'),2000).strip()
        need(value,'请扫描或输入编码')
        parsed=urlsplit(value)
        if parsed.scheme in ('http','https'):
            params=parse_qs(parsed.query)
            if params.get('scan'):value=params['scan'][0]
        match=re.fullmatch(r'PARTS:(part|slot|box):([a-f0-9]{32})',value)
        with store.transaction() as con:
            if match:
                kind,identity=match.groups()
                one(con,{'part':'parts','slot':'slots','box':'boxes'}[kind],identity)
                if kind=='slot':
                    slot=one(con,'slots',identity)
                    stock=con.execute('SELECT part_id FROM stock WHERE slot_id=?',(identity,)).fetchone()
                    return {'slot_id':identity,'box_id':slot['box_id'],'part_id':stock[0] if stock else None}
                return {kind+'_id':identity}
            location=re.fullmatch(r'([\w-]+)\s*[/\s]\s*([A-Ta-t])(\d{1,2})',value)
            if location:
                code,row,col=location.groups()
                slot=con.execute('''SELECT s.* FROM slots s JOIN boxes b ON b.id=s.box_id
                    WHERE b.code=? COLLATE NOCASE AND s.row_no=? AND s.col_no=?''',(code,ord(row.upper())-65,int(col)-1)).fetchone()
                if slot:
                    item=con.execute('SELECT part_id FROM stock WHERE slot_id=?',(slot['id'],)).fetchone()
                    return {'slot_id':slot['id'],'box_id':slot['box_id'],'part_id':item[0] if item else None}
            box=con.execute('SELECT id FROM boxes WHERE code=? COLLATE NOCASE',(value,)).fetchone()
            if box:return {'box_id':box[0]}
            matches=rows(con,'SELECT id FROM parts WHERE archived=0 AND (supplier_code=? OR name=? COLLATE NOCASE)',(value,value))
            if len(matches)==1:return {'part_id':matches[0]['id']}
            return {'query':value,'ambiguous':len(matches)>1}

    @router.post('/ocr')
    def ocr(file:UploadFile=File(...)):
        return recognize(file.file.read(8*1024*1024+1))

    return router
