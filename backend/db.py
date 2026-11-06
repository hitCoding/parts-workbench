import json
import sqlite3
import threading
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, table, item_id):
    # Table identifiers are internal constants, never request parameters.
    item = con.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
    if item is None:
        from .domain import Problem
        raise Problem('记录不存在', 404)
    return dict(item)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'inventory.sqlite3'
        self.backups = self.directory.parent / 'backups'
        self.lock = threading.RLock()

    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA busy_timeout=15000')
        return con

    def init(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with closing(self.connect()) as con:
                version = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            if version not in ('1','2'):
                raise RuntimeError('不支持的数据库版本，请先迁移')
            if version == '1':
                self.snapshot(self.backup_name('before-upgrade-v2'))
        with self.transaction() as con:
            con.executescript(Path(__file__).with_name('schema.sql').read_text())

    @contextmanager
    def transaction(self):
        with self.lock:
            con = self.connect()
            try:
                con.execute('BEGIN IMMEDIATE')
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    def snapshot(self, target):
        with self.lock:
            with closing(self.connect()) as source, closing(sqlite3.connect(target)) as dest:
                source.backup(dest)

    def backup_name(self, prefix='backup'):
        return self.backups / f'{prefix}-{datetime.now().strftime("%Y%m%d-%H%M%S")}-{uid()[:6]}.sqlite3'
