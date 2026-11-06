"""Cookie sessions and server-enforced roles; no public registration after initial setup."""
import hashlib
import os
import re
import secrets
import threading
import time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .db import uid, now, one, rows
from .domain import need, text, Problem

COOKIE = 'parts_session'
HASHER = PasswordHasher()
TTL = 7 * 24 * 3600


def public_user(user):
    return {k:user[k] for k in ('id','username','display_name','role','active')}


def validate_password(value):
    need(isinstance(value,str) and 10<=len(value)<=128,'密码须为 10–128 个字符')
    return value


def credentials(data):
    username=text(data.get('username'),80).strip()
    need(re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username),'用户名须为 3–40 位字母、数字或 . _ -')
    password=validate_password(data.get('password'))
    return username,password


def session_user(store,token):
    if not token or len(token)>200: return None
    digest=hashlib.sha256(token.encode()).hexdigest()
    with store.transaction() as con:
        row=con.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',(digest,int(time.time()))).fetchone()
        return dict(row) if row else None


def issue_session(con,user,request):
    token=secrets.token_urlsafe(32)
    con.execute('DELETE FROM sessions WHERE expires_at<?',(int(time.time()),))
    con.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],int(time.time())+TTL))
    response=JSONResponse({'user':public_user(user)})
    response.set_cookie(COOKIE,token,httponly=True,samesite='strict',secure=request.url.scheme=='https' or os.getenv('PARTS_SECURE_COOKIE')=='1',max_age=TTL,path='/')
    return response


def auth_routes(store):
    router=APIRouter(prefix='/api')
    failures={}
    failure_lock=threading.Lock()

    @router.get('/health')
    def health(): return {'status':'ok'}

    @router.get('/auth/status')
    def status(request:Request):
        user=session_user(store,request.cookies.get(COOKIE))
        with store.transaction() as con:
            setup=con.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
        return {'setup_required':setup,'setup_token_required':bool(os.getenv('PARTS_SETUP_TOKEN')) or request.client.host not in ('127.0.0.1','::1','testclient'), 'user':public_user(user) if user else None}

    @router.post('/auth/setup')
    def setup(request:Request,data:dict):
        configured=os.getenv('PARTS_SETUP_TOKEN','')
        if configured:
            need(secrets.compare_digest(str(data.get('setup_token','')),configured),'初始化口令不正确',403)
        else:
            need(request.client.host in ('127.0.0.1','::1','testclient'),'请在服务器本机初始化，或配置 PARTS_SETUP_TOKEN',403)
        username,password=credentials(data)
        with store.transaction() as con:
            need(con.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0,'管理员已经创建',409)
            identity=uid()
            con.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(identity,username,text(data.get('display_name') or username,80),HASHER.hash(password),'admin',1,now()))
            return issue_session(con,one(con,'users',identity),request)

    @router.post('/auth/login')
    def login(request:Request,data:dict):
        address=request.client.host
        with failure_lock:
            failures[address]=[t for t in failures.get(address,[]) if t>time.time()-300]
            need(len(failures[address])<10,'尝试过多，请 5 分钟后重试',429)
        username=text(data.get('username'),80)
        password=str(data.get('password',''))[:129]
        with store.transaction() as con:
            row=con.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
            valid=False
            if row:
                try: valid=HASHER.verify(row['password_hash'],password)
                except (VerificationError,InvalidHashError): pass
            if not valid:
                with failure_lock: failures[address].append(time.time())
                raise Problem('用户名或密码不正确',401)
            with failure_lock: failures.pop(address,None)
            return issue_session(con,dict(row),request)

    @router.post('/auth/logout')
    def logout(request:Request):
        with store.transaction() as con:
            token=request.cookies.get(COOKIE,'')
            con.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
        response=JSONResponse({'ok':True});response.delete_cookie(COOKIE,path='/');return response

    @router.post('/auth/password')
    def password(request:Request,data:dict):
        new=validate_password(data.get('new_password'))
        with store.transaction() as con:
            user=one(con,'users',request.state.user['id'])
            try: HASHER.verify(user['password_hash'],str(data.get('current_password','')))
            except (VerificationError,InvalidHashError): raise Problem('当前密码不正确',403)
            con.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(new),user['id']))
            con.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            return issue_session(con,user,request)

    @router.get('/users')
    def users():
        with store.transaction() as con: return [public_user(u) for u in rows(con,'SELECT * FROM users ORDER BY created_at')]

    @router.post('/users')
    def add_user(data:dict):
        username,password=credentials(data)
        role=data.get('role','viewer');need(role in ('admin','editor','viewer'),'角色无效')
        with store.transaction() as con:
            identity=uid()
            con.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)',(identity,username,text(data.get('display_name') or username,80),HASHER.hash(password),role,1,now()))
            return {'id':identity}

    @router.put('/users/{identity}')
    def edit_user(identity:str,data:dict):
        with store.transaction() as con:
            user=one(con,'users',identity)
            role=data.get('role',user['role']);active=bool(data.get('active',user['active']))
            need(role in ('admin','editor','viewer'),'角色无效')
            if user['role']=='admin' and user['active'] and (not active or role!='admin'):
                need(con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id!=?",(identity,)).fetchone()[0]>0,'至少保留一名启用的管理员')
            con.execute('UPDATE users SET role=?,active=?,display_name=? WHERE id=?',(role,int(active),text(data.get('display_name',user['display_name']),80),identity))
            con.execute('DELETE FROM sessions WHERE user_id=?',(identity,))
            return {'ok':True}

    @router.get('/audit')
    def audit():
        with store.transaction() as con: return rows(con,'SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 300')

    return router
