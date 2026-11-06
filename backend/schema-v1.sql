PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS parts(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,package TEXT NOT NULL DEFAULT '',
 value TEXT NOT NULL DEFAULT '',specs TEXT NOT NULL DEFAULT '{}',supplier_code TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '',archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boxes(
 id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,area TEXT NOT NULL DEFAULT '',
 rows INTEGER NOT NULL CHECK(rows BETWEEN 1 AND 20),cols INTEGER NOT NULL CHECK(cols BETWEEN 1 AND 20));
CREATE TABLE IF NOT EXISTS slots(
 id TEXT PRIMARY KEY,box_id TEXT NOT NULL REFERENCES boxes(id),row_no INTEGER NOT NULL,col_no INTEGER NOT NULL,
 UNIQUE(box_id,row_no,col_no));
CREATE TABLE IF NOT EXISTS stock(
 id TEXT PRIMARY KEY,part_id TEXT NOT NULL REFERENCES parts(id),slot_id TEXT NOT NULL UNIQUE REFERENCES slots(id),
 quantity INTEGER CHECK(quantity>=0),mode TEXT NOT NULL CHECK(mode IN('exact','estimate','unknown')),
 CHECK((mode='unknown' AND quantity IS NULL) OR (mode!='unknown' AND quantity IS NOT NULL)));
CREATE TABLE IF NOT EXISTS movements(
 id TEXT PRIMARY KEY,part_id TEXT NOT NULL REFERENCES parts(id),slot_id TEXT REFERENCES slots(id),kind TEXT NOT NULL,
 before_qty INTEGER,after_qty INTEGER,delta INTEGER,note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY,name TEXT NOT NULL,boards INTEGER NOT NULL CHECK(boards BETWEEN 1 AND 100000),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','reserved','built')),created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bom_lines(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),line_no INTEGER NOT NULL,name TEXT NOT NULL,
 category TEXT NOT NULL DEFAULT '',package TEXT NOT NULL DEFAULT '',value TEXT NOT NULL DEFAULT '',specs TEXT NOT NULL DEFAULT '{}',
 per_board INTEGER NOT NULL CHECK(per_board BETWEEN 1 AND 1000000),designators TEXT NOT NULL DEFAULT '',
 part_id TEXT REFERENCES parts(id),confirmed INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',UNIQUE(project_id,line_no));
CREATE TABLE IF NOT EXISTS reservations(
 project_id TEXT NOT NULL REFERENCES projects(id),part_id TEXT NOT NULL REFERENCES parts(id),quantity INTEGER NOT NULL CHECK(quantity>0),
 PRIMARY KEY(project_id,part_id));
CREATE TABLE IF NOT EXISTS purchases(
 id TEXT PRIMARY KEY,part_id TEXT NOT NULL REFERENCES parts(id),project_id TEXT REFERENCES projects(id),
 quantity INTEGER NOT NULL CHECK(quantity>0),received INTEGER NOT NULL DEFAULT 0 CHECK(received>=0 AND received<=quantity),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','ordered','partial','received','cancelled')),
 supplier TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_stock_part ON stock(part_id);
CREATE INDEX IF NOT EXISTS idx_bom_project ON bom_lines(project_id);
CREATE INDEX IF NOT EXISTS idx_moves_time ON movements(created_at);
