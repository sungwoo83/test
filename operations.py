"""Private SQLite + evidence backups and verified restore into a new directory."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
import zipfile


def backup(database,output):
    database=Path(database).resolve();output=Path(output).resolve()
    if not database.is_file():raise ValueError('DB 파일을 찾을 수 없습니다.')
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise ValueError('기존 백업은 덮어쓰지 않습니다.')
    manifest={};temporary=output.with_suffix('.partial')
    try:
        with tempfile.TemporaryDirectory() as folder,closing(sqlite3.connect(database,timeout=30)) as lock:
            lock.execute('BEGIN IMMEDIATE')
            snapshot=Path(folder)/'database.sqlite3'
            with closing(sqlite3.connect(database)) as source,closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                # Active sessions must not be resurrected after restore.
                target.execute('PRAGMA journal_mode=DELETE')
                target.execute('DELETE FROM sessions');target.commit()
            files={'database.sqlite3':snapshot}
            uploads=database.parent/'uploads'
            if uploads.exists():
                for path in uploads.iterdir():
                    if path.is_symlink() or not path.is_file() or not re.fullmatch('[a-f0-9]{48}',path.name):raise ValueError('증빙 저장 경로를 확인해 주세요.')
                    files['uploads/'+path.name]=path
            with zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED) as archive:
                for name,path in files.items():
                    content=path.read_bytes();manifest[name]={'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)};archive.writestr(name,content)
                archive.writestr('manifest.json',json.dumps({'format':1,'files':manifest}))
            lock.rollback()
        os.chmod(temporary,0o600);temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True);raise
    return {'archive':str(output),'files':len(manifest)}


def restore(archive,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('복원 대상은 존재하지 않는 새 디렉터리여야 합니다. 운영 데이터는 덮어쓰지 않습니다.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or len(names)>100000 or sum(i.file_size for i in z.infolist())>4*1024**3:raise ValueError('백업 크기·구성을 확인해 주세요.')
        if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>16*1024**2:raise ValueError('백업 검증정보가 없습니다.')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!=1 or set(manifest.get('files',{}))!=set(names)-{'manifest.json'} or 'database.sqlite3' not in names:raise ValueError('백업 목록이 일치하지 않습니다.')
        with tempfile.TemporaryDirectory(dir=destination.parent) as folder:
            work=Path(folder)
            for name,info in manifest['files'].items():
                if name!='database.sqlite3' and not re.fullmatch('uploads/[a-f0-9]{48}',name):raise ValueError('허용되지 않은 백업 경로입니다.')
                data=z.read(name)
                if len(data)!=info['size'] or hashlib.sha256(data).hexdigest()!=info['sha256']:raise ValueError('백업 파일 검증에 실패했습니다.')
                target=work/name;target.parent.mkdir(exist_ok=True);target.write_bytes(data);os.chmod(target,0o600)
            with closing(sqlite3.connect(work/'database.sqlite3')) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('DB 무결성 검사에 실패했습니다.')
                for table in ('evidence_files','performance_files'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                        for row in db.execute(f'SELECT storage_name,sha256 FROM {table} WHERE deleted_at IS NULL'):
                            path=work/'uploads'/row[0]
                            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row[1]:raise ValueError('DB와 증빙파일이 일치하지 않습니다.')
            # Move the verified directory only after every validation has succeeded.
            work.rename(destination)
    return {'database':str(destination/'database.sqlite3'),'verified':True}


def start_service(database,interval_hours=24):
    def run():
        while True:
            try:
                folder=Path(database).parent/'backups';folder.mkdir(parents=True,exist_ok=True)
                latest=max((p.stat().st_mtime for p in folder.glob('performance-*.zip')),default=0)
                if time.time()-latest>=interval_hours*3600:
                    backup(database,folder/time.strftime('performance-%Y%m%dT%H%M%SZ.zip',time.gmtime()))
                (folder/'status.json').write_text(json.dumps({'checked_at':time.time(),'ok':True}))
            except Exception:
                folder=Path(database).parent/'backups'
                if folder.exists():(folder/'status.json').write_text(json.dumps({'checked_at':time.time(),'ok':False,'error':'백업 실패: 서버 저장공간과 권한을 확인해 주세요.'}))
            time.sleep(3600)
    threading.Thread(target=run,daemon=True,name='private-backup').start()


def main():
    p=argparse.ArgumentParser();commands=p.add_subparsers(dest='command',required=True)
    b=commands.add_parser('backup');b.add_argument('--db',required=True);b.add_argument('--output',required=True)
    r=commands.add_parser('restore');r.add_argument('--archive',required=True);r.add_argument('--destination',required=True)
    args=p.parse_args()
    try:print(json.dumps(backup(args.db,args.output) if args.command=='backup' else restore(args.archive,args.destination),ensure_ascii=False))
    except (ValueError,OSError,sqlite3.Error,zipfile.BadZipFile,KeyError,json.JSONDecodeError) as e:p.exit(1,str(e)+'\n')
if __name__=='__main__':main()
