"""Keep independent employee entries while retaining legacy record/evidence IDs."""
import json
import sqlite3
import uuid
from pathlib import Path


def infer_owner(db,row):
    event=db.execute("SELECT actor FROM audit_log WHERE action='실적 입력' AND target=? AND department_id=? ORDER BY id LIMIT 1",(str(row['id']),row['department_id'])).fetchone()
    if event:return event['actor'],0
    return row['updated_by'] or 'legacy-'+str(row['id']),1


def migrate(db):
    columns={r['name'] for r in db.execute('PRAGMA table_info(monthly_actuals)')}
    if 'employee_id' not in columns:
        # A dated local backup and a transaction protect existing records and files.
        db.commit()
        location=db.execute('PRAGMA database_list').fetchone()['file']
        if location:
            source=Path(location);folder=source.parent/'backups';folder.mkdir(exist_ok=True)
            with sqlite3.connect(folder/(source.stem+'.before-employee-scope-'+uuid.uuid4().hex[:10]+'.sqlite3')) as backup:
                db.backup(backup)
        rows=list(db.execute('SELECT * FROM monthly_actuals'))
        db.execute('PRAGMA foreign_keys=OFF')
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE monthly_actuals_employee (
                id INTEGER PRIMARY KEY, department_id TEXT NOT NULL, indicator_id TEXT NOT NULL,
                year INTEGER NOT NULL, month INTEGER NOT NULL, actual TEXT NOT NULL, adjusted TEXT,
                adjustment_reason TEXT, snapshot TEXT NOT NULL, revision INTEGER NOT NULL,
                updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '진행중',future_plan TEXT NOT NULL DEFAULT '',narrative TEXT NOT NULL DEFAULT '',
                employee_id TEXT NOT NULL DEFAULT '',owner_inferred INTEGER NOT NULL DEFAULT 0,
                UNIQUE(department_id,indicator_id,year,month,employee_id))''')
            fields=['id','department_id','indicator_id','year','month','actual','adjusted','adjustment_reason','snapshot','revision','updated_by','updated_at','status','future_plan','narrative']
            for row in rows:
                owner,inferred=infer_owner(db,row)
                db.execute('INSERT INTO monthly_actuals_employee('+','.join(fields)+',employee_id,owner_inferred) VALUES('+','.join('?' for _ in range(len(fields)+2))+')',tuple(row[k] for k in fields)+(owner,inferred))
            db.execute('DROP TABLE monthly_actuals')
            db.execute('ALTER TABLE monthly_actuals_employee RENAME TO monthly_actuals')
            if list(db.execute('PRAGMA foreign_key_check')):raise sqlite3.IntegrityError('Existing evidence references could not be preserved')
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.execute('PRAGMA foreign_keys=ON')
    for row in db.execute("SELECT * FROM monthly_actuals WHERE employee_id='' ").fetchall():
        owner,inferred=infer_owner(db,row)
        db.execute('UPDATE monthly_actuals SET employee_id=?,owner_inferred=? WHERE id=?',(owner,inferred,row['id']))
    db.execute('CREATE INDEX IF NOT EXISTS monthly_by_period ON monthly_actuals(year,month,department_id,employee_id)')
    db.execute('CREATE INDEX IF NOT EXISTS users_by_department ON users(department_id)')
