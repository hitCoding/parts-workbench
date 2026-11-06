"""Local account recovery. Run interactively on the server; passwords never in arguments."""
import argparse
import getpass
import os
from pathlib import Path
from .auth import HASHER, validate_password
from .db import Store, uid, now
from .domain import need


def main():
    parser=argparse.ArgumentParser(description='料格本机账号恢复')
    parser.add_argument('command',choices=['reset-password'])
    parser.add_argument('username')
    args=parser.parse_args()
    password=getpass.getpass('新密码（至少10个字符）: ')
    need(password==getpass.getpass('再次输入新密码: '),'两次输入不一致')
    validate_password(password)
    directory=os.getenv('PARTS_DATA_DIR',Path(__file__).resolve().parent.parent/'data')
    store=Store(directory);store.init()
    store.snapshot(store.backup_name('before-account-recovery'))
    with store.transaction() as con:
        user=con.execute('SELECT * FROM users WHERE username=?',(args.username,)).fetchone()
        need(user,'账号不存在')
        con.execute('UPDATE users SET password_hash=? WHERE id=?',(HASHER.hash(password),user['id']))
        con.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
        con.execute('INSERT INTO audit_log VALUES(?,?,?,?,?,?,?)',(uid(),user['id'],'本机账号恢复','CLI','reset-password',200,now()))
    print('密码已更新，原登录会话已撤销。')


if __name__=='__main__': main()
