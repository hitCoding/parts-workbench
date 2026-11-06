import io
import sqlite3
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import Store
from test_workbench import ok, setup_stock


def png():
    buffer=io.BytesIO();Image.new('RGB',(30,30),'green').save(buffer,'PNG');return buffer.getvalue()


def setup(c,name='admin',role=None):
    if role:
        return ok(c.post('/api/users',json={'username':name,'password':'test-only-password','role':role}))['id']
    return ok(c.post('/api/auth/setup',json={'username':name,'password':'test-only-password'}))


def login(c,name='admin'):
    return ok(c.post('/api/auth/login',json={'username':name,'password':'test-only-password'}))


def test_first_setup_and_session_auth(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as c:
        assert c.get('/api/state').status_code==401
        assert ok(c.get('/api/auth/status'))['setup_required']
        assert c.post('/api/auth/setup',json={'username':'admin','password':'short'}).status_code==400
        response=c.post('/api/auth/setup',json={'username':'admin','password':'test-only-password'})
        assert response.status_code==200 and 'HttpOnly' in response.headers['set-cookie']
        assert c.post('/api/auth/setup',json={'username':'other','password':'test-only-password'}).status_code==409
        assert ok(c.get('/api/state'))['storage']['schema_version']==2
        ok(c.post('/api/auth/logout'))
        assert c.get('/api/state').status_code==401
        login(c)
        assert ok(c.get('/api/auth/status'))['user']['role']=='admin'


def test_server_enforces_roles_and_revokes_sessions(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as admin:
        setup(admin);part,slot,_=setup_stock(admin)
        viewer_id=setup(admin,'viewer','viewer');setup(admin,'editor','editor')
        with TestClient(admin.app) as viewer,TestClient(admin.app) as editor:
            login(viewer,'viewer');login(editor,'editor')
            assert viewer.get('/api/state').status_code==200
            assert viewer.post('/api/scan/resolve',json={'code':'R01 / A1'}).status_code==200
            assert viewer.post('/api/stock',json={'part_id':part,'slot_id':slot,'quantity':0}).status_code==403
            assert viewer.get('/api/backup').status_code==403
            assert editor.post('/api/parts',json={'name':'editor-part','category':'芯片'}).status_code==200
            assert editor.get('/api/users').status_code==403
            assert editor.post('/api/restore/preview',files={'file':('x',b'bad')}).status_code==403
            ok(admin.put(f'/api/users/{viewer_id}',json={'active':False}))
            assert viewer.get('/api/state').status_code==401
        user=ok(admin.get('/api/auth/status'))['user']
        assert admin.put(f"/api/users/{user['id']}",json={'active':False}).status_code==400
        assert any(a['username']=='editor' for a in ok(admin.get('/api/audit')))


def test_attachments_roundtrip_and_restore_keeps_current_accounts(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as c:
        setup(c);part,_,_=setup_stock(c)
        identity=ok(c.post(f'/api/parts/{part}/attachments',files={'file':('label.png',png())}))['id']
        payload=c.get(f'/api/attachments/{identity}');assert payload.headers['content-type']=='image/jpeg'
        assert Image.open(io.BytesIO(payload.content)).size==(30,30)
        assert c.post(f'/api/parts/{part}/attachments',files={'file':('x.svg',b'<svg/>')}).status_code==400
        backup=c.get('/api/backup').content
        new_id=setup(c,'later','viewer')
        ok(c.delete(f'/api/attachments/{identity}'))
        assert c.get(f'/api/attachments/{identity}').status_code==404
        ok(c.post('/api/restore',files={'file':('backup.sqlite3',backup)},headers={'X-Confirm-Restore':'RESTORE'}))
        assert c.get(f'/api/attachments/{identity}').status_code==200
        assert any(u['id']==new_id for u in ok(c.get('/api/users')))
        assert list(c.app.state.store.backups.glob('before-attachment-delete-*'))


def test_v1_upgrade_and_legacy_restore_preserve_boxes(tmp_path):
    directory=tmp_path/'data';directory.mkdir();path=directory/'inventory.sqlite3'
    with sqlite3.connect(path) as con:
        con.executescript(Path('backend/schema-v1.sql').read_text())
        con.execute("INSERT INTO boxes VALUES('legacy','OLD','旧盒','原位置',1,1)")
        con.execute("INSERT INTO slots VALUES('old-slot','legacy',0,0)")
    legacy=path.read_bytes()
    with TestClient(create_app(directory)) as c:
        setup(c)
        assert ok(c.get('/api/state'))['boxes'][0]['name']=='旧盒'
        assert list(c.app.state.store.backups.glob('before-upgrade-v2-*'))
        ok(c.post('/api/boxes',json={'code':'NEW','name':'新盒','rows':1,'cols':1}))
        assert ok(c.post('/api/restore/preview',files={'file':('v1.sqlite3',legacy)}))['counts']['boxes']==1
        ok(c.post('/api/restore',files={'file':('v1.sqlite3',legacy)},headers={'X-Confirm-Restore':'RESTORE'}))
        assert len(ok(c.get('/api/state'))['boxes'])==1
        assert len(ok(c.get('/api/users')))==1


def test_qr_and_scan_lookup(tmp_path):
    with TestClient(create_app(tmp_path/'data')) as c:
        setup(c);part,slot,box=setup_stock(c)
        svg=c.get(f'/api/qr/slot/{slot}')
        assert svg.status_code==200 and '<svg' in svg.text
        assert ok(c.post('/api/scan/resolve',json={'code':f'PARTS:slot:{slot}'}))=={'slot_id':slot,'box_id':box,'part_id':part}
        assert ok(c.post('/api/scan/resolve',json={'code':'r01 / A1'}))['part_id']==part
        assert c.get('/api/qr/slot/missing').status_code==404


def test_setup_token_and_login_throttle(tmp_path,monkeypatch):
    monkeypatch.setenv('PARTS_SETUP_TOKEN','test-setup-secret')
    with TestClient(create_app(tmp_path/'data')) as c:
        assert c.post('/api/auth/setup',json={'username':'admin','password':'test-only-password'}).status_code==403
        ok(c.post('/api/auth/setup',json={'username':'admin','password':'test-only-password','setup_token':'test-setup-secret'}))
        ok(c.post('/api/auth/logout'))
        for _ in range(10):assert c.post('/api/auth/login',json={'username':'admin','password':'wrong'}).status_code==401
        assert c.post('/api/auth/login',json={'username':'admin','password':'wrong'}).status_code==429
