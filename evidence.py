"""Authenticated evidence storage. Files are never served as public static assets."""
import hashlib
import secrets
from pathlib import Path
import management as m

MAX_SIZE=10*1024*1024
MAX_FILES=10
EXTENSIONS={'.pdf','.png','.jpg','.jpeg','.hwp','.hwpx','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.txt','.csv','.zip'}

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_files (
        id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES monthly_actuals(id),
        storage_name TEXT NOT NULL UNIQUE,original_name TEXT NOT NULL,size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL,deleted_at TEXT)''')

def accessible_record(db,user,record_id,catalog,write=False):
    row=db.execute('SELECT * FROM monthly_actuals WHERE id=?',(record_id,)).fetchone()
    if not row:raise m.APIError(404,'실적을 찾을 수 없습니다.')
    m.department_scope(user,row['department_id'],catalog)
    if user['role']=='employee':
        import json
        if row['indicator_id'] not in json.loads(user['indicators']):raise m.APIError(403,'담당지표의 증빙만 접근할 수 있습니다.')
    if write and m.period(db,row['department_id'],row['year'],row['month'])['status']=='closed':
        raise m.APIError(409,'마감된 월의 증빙은 변경할 수 없습니다.')
    return row

def upload(db,user,record_id,filename,body,catalog,storage):
    record=accessible_record(db,user,record_id,catalog,True)
    if not isinstance(filename,str) or not 1<=len(filename)<=180 or any(ord(c)<32 for c in filename) or '/' in filename or '\\' in filename:
        raise m.APIError(400,'파일명을 확인해 주세요.')
    if Path(filename).suffix.lower() not in EXTENSIONS:raise m.APIError(400,'지원하지 않는 파일 형식입니다.')
    if not 0<len(body)<=MAX_SIZE:raise m.APIError(413,'증빙파일은 파일당 10MB 이하로 업로드해 주세요.')
    if db.execute('SELECT COUNT(*) FROM evidence_files WHERE record_id=? AND deleted_at IS NULL',(record_id,)).fetchone()[0]>=MAX_FILES:
        raise m.APIError(400,'지표의 월별 증빙은 최대 10개입니다.')
    storage=Path(storage);storage.mkdir(parents=True,exist_ok=True)
    key=secrets.token_hex(24);digest=hashlib.sha256(body).hexdigest();path=storage/key
    try:
        with path.open('xb') as file:file.write(body)
        cursor=db.execute('INSERT INTO evidence_files(record_id,storage_name,original_name,size,sha256,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?)',
            (record_id,key,filename,len(body),digest,user['employee_id'],m.stamp()))
        public={'id':cursor.lastrowid,'record_id':record_id,'original_name':filename,'size':len(body),'sha256':digest}
        m.audit(db,user['employee_id'],'증빙 업로드',record['department_id'],cursor.lastrowid,None,public,'지표 증빙 등록')
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {'ok':True,'file':public}

def download(db,user,file_id,catalog,storage):
    file=db.execute('SELECT * FROM evidence_files WHERE id=? AND deleted_at IS NULL',(file_id,)).fetchone()
    if not file:raise m.APIError(404,'증빙파일을 찾을 수 없습니다.')
    accessible_record(db,user,file['record_id'],catalog)
    path=Path(storage)/file['storage_name']
    if not path.is_file():raise m.APIError(404,'저장된 증빙파일을 찾을 수 없습니다.')
    return dict(file),path

def delete(db,user,file_id,catalog):
    file=db.execute('SELECT * FROM evidence_files WHERE id=? AND deleted_at IS NULL',(file_id,)).fetchone()
    if not file:raise m.APIError(404,'증빙파일을 찾을 수 없습니다.')
    record=accessible_record(db,user,file['record_id'],catalog,True)
    if user['role']=='employee' and file['uploaded_by']!=user['employee_id']:raise m.APIError(403,'본인이 올린 증빙만 삭제할 수 있습니다.')
    db.execute('UPDATE evidence_files SET deleted_at=? WHERE id=?',(m.stamp(),file_id))
    m.audit(db,user['employee_id'],'증빙 삭제',record['department_id'],file_id,
        {'filename':file['original_name'],'sha256':file['sha256']},{'deleted':True},'지표 증빙 삭제; 원본 보존')
    return {'ok':True}
