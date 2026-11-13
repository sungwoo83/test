"""폐쇄망용 직원 설정 모듈. Python 3.11+, 외부 패키지 불필요."""
import argparse
import sys
if __name__=='__main__':sys.modules['app']=sys.modules[__name__]
import management
import evidence
from urllib.parse import urlsplit, parse_qs, quote, unquote
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import ssl
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / 'catalog.json'
DEMO = not CATALOG_PATH.exists()
CATALOG = json.loads((ROOT / 'catalog.example.json' if DEMO else CATALOG_PATH).read_text())
if not any(d['id']=='SYS-PERFORMANCE' for d in CATALOG['departments']):
    CATALOG['departments'].append({'id':'SYS-PERFORMANCE','name':'성과혁신실','group':'평가총괄','kind':'운영'})
DB = ROOT / 'data' / 'performance.sqlite3'
SECURE = False
TTL = 8 * 3600

def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          employee_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
          department_id TEXT NOT NULL, indicators TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES users,
          csrf TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS changes (
          id INTEGER PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES users,
          before_json TEXT, after_json TEXT NOT NULL, changed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
          scope TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start REAL NOT NULL);
        ''')
        management.initialize(db)
        import manual_defaults
        manual_defaults.seed(db, CATALOG)
        import performance
        performance.initialize(db)
        import office
        office.initialize(db)

def now():
    return datetime.now(timezone.utc).isoformat()

def available(department_id, db=None):
    dept = next((d for d in CATALOG['departments'] if d['id'] == department_id), None)
    if not dept:
        return []
    target = dept.get('indicator_department', dept['name'])
    if dept['kind'] in ('지역본부', '사무소', '생태탐방원'):
        target = dept['kind']
    if db is None:
        with connect() as connection:
            items = management.effective_catalog(connection, CATALOG)['indicators']
    else:
        items = management.effective_catalog(db, CATALOG)['indicators']
    return [k for k in items if k['kind'] == dept['kind'] and k['applies_to'] == target]

def profile(row):
    return {k: json.loads(row[k]) if k == 'indicators' else row[k]
            for k in ('employee_id', 'department_id', 'indicators', 'revision', 'role', 'admin_department_id', 'display_name', 'assignment_confirmed')}

def selection(data):
    dept, values = data.get('department_id'), data.get('indicators')
    allowed = {k['id'] for k in available(dept)}
    if dept == 'SYS-PERFORMANCE':
        if values != []: raise ValueError('평가총괄 부서는 담당지표를 비워 주세요.')
        return dept, []
    if not allowed:
        raise ValueError('근무지를 선택해 주세요.')
    if not isinstance(values, list) or not values or len(values) > 165:
        raise ValueError('담당지표를 하나 이상 선택해 주세요.')
    if any(not isinstance(v, str) or v not in allowed for v in values):
        raise ValueError('선택한 근무지에 해당하는 담당지표를 선택해 주세요.')
    return dept, sorted(set(values))

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def headers_common(self, content_type, size):
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def reply(self, code, value, cookie=None):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.headers_common('application/json; charset=utf-8', len(body))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def session(self, db):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            token = cookie['session'].value if 'session' in cookie else ''
        except Exception:
            token = ''
        digest = hashlib.sha256(token.encode()).hexdigest()
        return db.execute("SELECT s.* FROM sessions s JOIN users u ON u.employee_id=s.employee_id WHERE s.token_hash=? AND s.expires>? AND u.account_status='active'", (digest, time.time())).fetchone()

    def cookie(self, token, age):
        return f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}' + ('; Secure' if SECURE else '')

    def do_GET(self):
        path = self.path.split('?')[0]
        if path=='/api/evidence-bundle.zip':
            return self.evidence_bundle()
        if path.startswith('/api/performance/files/'):
            return self.performance_file_download(path)
        if path=='/api/performance.xlsx':
            return self.performance_export()
        if path.startswith('/api/evidence/'):
            try:
                file_id=int(path.rsplit('/',1)[-1])
                with connect() as db:
                    session=self.session(db)
                    if not session: return self.reply(401, {'error':'로그인이 필요합니다.'})
                    user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                    file,file_path=evidence.download(db,user,file_id,CATALOG,DB.parent/'uploads')
                    body=file_path.read_bytes()
                self.send_response(200)
                self.headers_common('application/octet-stream',len(body))
                self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(file['original_name'],safe=''))
                self.end_headers();self.wfile.write(body);return
            except management.APIError as exc:
                return self.reply(exc.status, {'error':str(exc)})
            except (ValueError,OSError):
                return self.reply(404, {'error':'증빙파일을 찾을 수 없습니다.'})
        if path == '/api/catalog':
            with connect() as db:
                return self.reply(200, {'demo': DEMO, 'departments': CATALOG['departments'],
                    'indicators_by_department': {d['id']: available(d['id'], db) for d in CATALOG['departments']}})
        if path == '/api/admin/indicators/template.xlsx':
            try:
                import indicator_bulk
                with connect() as db:
                    session=self.session(db)
                    if not session:return self.reply(401, {'error':'로그인이 필요합니다.'})
                    user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                    mparams={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                    year=indicator_bulk.year_arg(mparams)
                    body=indicator_bulk.template(db,user,year,management.effective_catalog(db,CATALOG),available)
                self.send_response(200)
                self.headers_common('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',len(body))
                self.send_header('Content-Disposition',f'attachment; filename="indicator-template-{year}.xlsx"')
                self.end_headers();self.wfile.write(body);return
            except management.APIError as exc:return self.reply(exc.status, {'error':str(exc)})
        if path == '/api/review.xlsx':
            try:
                import review
                with connect() as db:
                    session=self.session(db)
                    if not session:return self.reply(401, {'error':'로그인이 필요합니다.'})
                    user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                    params={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                    report=review.report(db,user,params,management.effective_catalog(db,CATALOG),available)
                    body=review.export(report)
                self.send_response(200)
                self.headers_common('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',len(body))
                filename=f'monthly-performance-{report["year"]}-{report["month"]:02d}.xlsx'
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
                self.end_headers();self.wfile.write(body);return
            except management.APIError as exc:return self.reply(exc.status, {'error':str(exc)})
        if path in ('/api/monthly','/api/review','/api/dashboard') or path.startswith('/api/admin/') or path.startswith('/api/performance'):
            with connect() as db:
                session = self.session(db)
                if not session: return self.reply(401, {'error':'로그인이 필요합니다.'})
                user = db.execute('SELECT * FROM users WHERE employee_id=?', (session['employee_id'],)).fetchone()
                params = {k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                try:
                    if path=='/api/performance/office':
                        import office
                        result=office.preview(db,user,params,management.effective_catalog(db,CATALOG),available)
                    elif path.startswith('/api/performance'):
                        import performance
                        result=performance.get(db,user,path,params,management.effective_catalog(db,CATALOG),available)
                    else:result=management.get(db,user,path,params,management.effective_catalog(db,CATALOG),available)
                    return self.reply(200,result)
                except management.APIError as exc:
                    return self.reply(exc.status, {'error':str(exc)})
        if path == '/api/me':
            with connect() as db:
                session = self.session(db)
                if not session:
                    return self.reply(401, {'error': '로그인이 필요합니다.'})
                user = db.execute('SELECT * FROM users WHERE employee_id=?', (session['employee_id'],)).fetchone()
                history = db.execute('SELECT before_json,after_json,changed_at FROM changes WHERE employee_id=? ORDER BY id DESC LIMIT 20', (session['employee_id'],)).fetchall()
                return self.reply(200, {'user': profile(user), 'csrf': session['csrf'], 'history': [dict(r) for r in history]})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/dashboard.js': ('dashboard.js','text/javascript; charset=utf-8'), '/performance.js':('performance.js','text/javascript; charset=utf-8'), '/knps-logo.svg': ('knps-logo.svg','image/svg+xml'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            return self.reply(404, {'error': '페이지를 찾을 수 없습니다.'})
        name, mime = files[path]
        body = (ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.headers_common(mime, len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.headers.get('X-Requested-With') != 'PerformanceManager' or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'):
            return self.reply(403, {'error': '허용되지 않은 요청입니다.'})
        if urlsplit(self.path).path=='/api/admin/indicators/import-preview':
            return self.preview_indicator_workbook()
        if urlsplit(self.path).path=='/api/performance/backup':
            return self.performance_backup()
        if urlsplit(self.path).path=='/api/performance/upload':
            return self.performance_file_upload()
        if urlsplit(self.path).path=='/api/evidence/upload':
            return self.upload_evidence()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                return self.reply(413, {'error': '요청 크기를 확인해 주세요.'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(415, {'error': 'JSON 요청이 필요합니다.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('입력 형식을 확인해 주세요.')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if self.path in ('/api/register', '/api/login'):
                    employee, password = data.get('employee_id', ''), data.get('password', '')
                    if not isinstance(employee, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,30}', employee):
                        raise ValueError('사번은 영문·숫자·밑줄·하이픈으로 1~30자 입력해 주세요.')
                    if not isinstance(password, str) or not 1 <= len(password) <= 128:
                        raise ValueError('비밀번호는 128자 이내로 입력해 주세요.')
                    scopes = ['employee:' + employee, 'ip:' + self.client_address[0]]
                    for scope in scopes:
                        attempt = db.execute('SELECT * FROM attempts WHERE scope=?', (scope,)).fetchone()
                        limit = 10 if scope.startswith('employee:') else 100
                        if attempt and attempt['failures'] >= limit and time.time() - attempt['window_start'] < 900:
                            return self.reply(429, {'error': '시도 횟수를 초과했습니다. 15분 후 다시 시도해 주세요.'})
                    if self.path == '/api/register':
                        if len(password) < 10:
                            raise ValueError('비밀번호는 10자 이상 입력해 주세요.')
                        display_name=data.get('display_name')
                        if not isinstance(display_name,str) or not 1<=len(display_name.strip())<=100 or any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in display_name):
                            raise ValueError('이름을 1~100자로 입력해 주세요.')
                        display_name=display_name.strip()
                        dept, indicators = selection(data)
                        if db.execute('SELECT 1 FROM users WHERE employee_id=?', (employee,)).fetchone():
                            return self.reply(409, {'error': '이미 가입된 사번입니다. 로그인해 주세요.'})
                        db.execute('INSERT INTO users(employee_id,password_hash,department_id,indicators,revision,created_at,display_name,assignment_confirmed) VALUES(?,?,?,?,1,?,?,0)', (employee, password_hash(password), dept, json.dumps(indicators), now(),display_name))
                        db.execute('INSERT INTO changes(employee_id,after_json,changed_at) VALUES(?,?,?)', (employee, json.dumps({'department_id': dept, 'indicators': indicators}), now()))
                    else:
                        row = db.execute('SELECT * FROM users WHERE employee_id=?', (employee,)).fetchone()
                        stored = row['password_hash'] if row else password_hash('dummy-password')
                        if not hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored) or not row:
                            for scope in scopes:
                                stamp = time.time()
                                db.execute('''INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(scope) DO UPDATE SET
                                failures=CASE WHEN ?-window_start>=900 THEN 1 ELSE failures+1 END,
                                window_start=CASE WHEN ?-window_start>=900 THEN ? ELSE window_start END''', (scope, stamp, stamp, stamp, stamp))
                            db.commit()
                            return self.reply(401, {'error': '사번 또는 비밀번호를 확인해 주세요.'})
                    account=db.execute('SELECT account_status FROM users WHERE employee_id=?',(employee,)).fetchone()
                    if account['account_status']!='active':return self.reply(403,{'error':'비활성화된 계정입니다. 관리자에게 문의해 주세요.'})
                    db.execute('DELETE FROM attempts WHERE scope=?', (scopes[0],))
                    db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
                    token = secrets.token_urlsafe(32)
                    db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), employee, secrets.token_urlsafe(32), time.time() + TTL))
                    db.commit()
                    return self.reply(200, {'ok': True}, self.cookie(token, TTL))
                session = self.session(db)
                if not session:
                    return self.reply(401, {'error': '로그인이 필요합니다.'})
                if not hmac.compare_digest(str(data.get('csrf', '')), session['csrf']):
                    return self.reply(403, {'error': '세션을 새로고침하고 다시 시도해 주세요.'})
                user = db.execute('SELECT * FROM users WHERE employee_id=?', (session['employee_id'],)).fetchone()
                if self.path.startswith('/api/performance'):
                    import performance
                    if self.path=='/api/performance/office':
                        import office
                        result=office.post(db,user,data,management.effective_catalog(db,CATALOG),available)
                    elif self.path=='/api/performance/files/delete':
                        import performance_files
                        result=performance_files.delete(db,user,data.get('file_id'),CATALOG)
                    else:result=performance.post(db,user,self.path,data,management.effective_catalog(db,CATALOG),available)
                    db.commit();return self.reply(200,result)
                if self.path.startswith('/api/monthly/') or self.path.startswith('/api/admin/') or self.path=='/api/evidence/delete':
                    result = management.post(db,user,self.path,data,management.effective_catalog(db,CATALOG),available)
                    db.commit()
                    return self.reply(200,result)
                if self.path == '/api/logout':
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (session['token_hash'],))
                    db.commit()
                    return self.reply(200, {'ok': True}, self.cookie('', 0))
                if self.path == '/api/profile':
                    dept, indicators = selection(data)
                    row = db.execute('SELECT * FROM users WHERE employee_id=?', (session['employee_id'],)).fetchone()
                    if data.get('revision') != row['revision']:
                        return self.reply(409, {'error': '다른 화면에서 설정이 변경되었습니다. 새로고침해 주세요.'})
                    if row['role']=='superadmin' and not management.can_be_superadmin(dept, CATALOG):
                        return self.reply(409, {'error':'수퍼관리자의 소속은 성과혁신실 또는 AI정보융합실이어야 합니다.'})
                    before = {'department_id': row['department_id'], 'indicators': json.loads(row['indicators'])}
                    after = {'department_id': dept, 'indicators': indicators}
                    if before != after:
                        db.execute('UPDATE users SET department_id=?,indicators=?,assignment_confirmed=0,revision=revision+1 WHERE employee_id=?', (dept, json.dumps(indicators), row['employee_id']))
                        db.execute('INSERT INTO changes(employee_id,before_json,after_json,changed_at) VALUES(?,?,?,?)', (row['employee_id'], json.dumps(before), json.dumps(after), now()))
                    db.commit()
                    return self.reply(200, {'ok': True})
                return self.reply(404, {'error': '요청을 찾을 수 없습니다.'})
        except management.APIError as exc:
            self.reply(exc.status, {'error':str(exc)})
        except (ValueError, TypeError, UnicodeError) as exc:
            self.reply(400, {'error': str(exc) if type(exc) is ValueError else '입력값을 확인해 주세요.'})
        except sqlite3.Error:
            self.reply(503, {'error': '저장에 실패했습니다. 잠시 후 다시 시도해 주세요.'})


    def upload_evidence(self):
        created_path=None
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=evidence.MAX_SIZE:
                return self.reply(413, {'error':'증빙파일은 파일당 10MB 이하로 업로드해 주세요.'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/octet-stream':
                return self.reply(415, {'error':'파일 업로드 형식을 확인해 주세요.'})
            params=parse_qs(urlsplit(self.path).query)
            record_id=int(params.get('record_id',[''])[0])
            filename=unquote(self.headers.get('X-File-Name',''))
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                session=self.session(db)
                if not session:return self.reply(401, {'error':'로그인이 필요합니다.'})
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):
                    return self.reply(403, {'error':'세션을 새로고침하고 다시 시도해 주세요.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                evidence.accessible_record(db,user,record_id,CATALOG,True)
                body=self.rfile.read(length)
                if len(body)!=length:raise management.APIError(400,'파일 전송이 완료되지 않았습니다.')
                result=evidence.upload(db,user,record_id,filename,body,CATALOG,DB.parent/'uploads')
                storage_name=db.execute('SELECT storage_name FROM evidence_files WHERE id=?',(result['file']['id'],)).fetchone()[0]
                created_path=DB.parent/'uploads'/storage_name
                db.commit()
                created_path=None
                return self.reply(200,result)
        except management.APIError as exc:
            self.reply(exc.status, {'error':str(exc)})
        except (ValueError,UnicodeError):
            self.reply(400, {'error':'증빙 요청을 확인해 주세요.'})
        except (OSError,sqlite3.Error):
            self.reply(503, {'error':'파일 저장에 실패했습니다. 다시 시도해 주세요.'})
        finally:
            if created_path is not None:created_path.unlink(missing_ok=True)


    def preview_indicator_workbook(self):
        try:
            import indicator_bulk
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=indicator_bulk.MAX_FILE:return self.reply(413, {'error':'양식은 5MB 이하의 XLSX 파일로 업로드해 주세요.'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/octet-stream':return self.reply(415, {'error':'XLSX 파일 업로드 형식을 확인해 주세요.'})
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                session=self.session(db)
                if not session:return self.reply(401, {'error':'로그인이 필요합니다.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                management.require_super(user)
                csrf=self.headers.get('X-CSRF-Token','')
                if not hmac.compare_digest(csrf,session['csrf']):return self.reply(403, {'error':'세션을 새로고침하고 다시 시도해 주세요.'})
                params={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                year=indicator_bulk.year_arg(params)
                body=self.rfile.read(length)
                if len(body)!=length:raise management.APIError(400,'파일 전송이 완료되지 않았습니다.')
                result=indicator_bulk.preview(db,user,body,year,management.effective_catalog(db,CATALOG),available,csrf)
                db.commit();return self.reply(200,result)
        except management.APIError as exc:self.reply(exc.status, {'error':str(exc)})
        except (ValueError,UnicodeError):self.reply(400, {'error':'평가연도와 XLSX 파일을 확인해 주세요.'})
        except (sqlite3.Error,OSError):self.reply(503, {'error':'파일 검증에 실패했습니다. 다시 시도해 주세요.'})


    def evidence_bundle(self):
        bundle=None
        try:
            import evidence_bundle
            with connect() as db:
                session=self.session(db)
                if not session:return self.reply(401,{'error':'로그인이 필요합니다.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone();management.require_super(user)
                params={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()};db.execute('BEGIN')
                bundle,summary=evidence_bundle.export(db,user,params,management.effective_catalog(db,CATALOG),available,DB.parent/'uploads')
            with connect() as db:management.audit(db,user['employee_id'],'공문 검증용 증빙 일괄 다운로드',None,summary['year'],None,summary,'수퍼관리자 공문 검증자료 취합')
            bundle.seek(0,2);length=bundle.tell();bundle.seek(0)
            self.send_response(200);self.headers_common('application/zip',length);self.send_header('Content-Disposition',f'attachment; filename="evidence-{summary["year"]}-{summary["group_by"]}.zip"');self.end_headers()
            for block in iter(lambda:bundle.read(1024*1024),b''):self.wfile.write(block)
        except management.APIError as e:self.reply(e.status,{'error':str(e)})
        except (OSError,sqlite3.Error):self.reply(503,{'error':'증빙자료 취합에 실패했습니다. 저장공간과 파일 상태를 확인해 주세요.'})
        finally:
            if bundle:bundle.close()

    def performance_backup(self):
        try:
            import operations
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=65536:return self.reply(413,{'error':'요청 크기를 확인해 주세요.'})
            data=json.loads(self.rfile.read(length))
            with connect() as db:
                session=self.session(db)
                if not session:return self.reply(401,{'error':'로그인이 필요합니다.'})
                if not hmac.compare_digest(str(data.get('csrf','')),session['csrf']):return self.reply(403,{'error':'세션을 새로고침해 주세요.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone();management.require_super(user);reason=management.reason(data)
            folder=DB.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
            name='performance-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+secrets.token_hex(4)+'.zip'
            result=operations.backup(DB,folder/name)
            with connect() as db:management.audit(db,user['employee_id'],'수동 백업',None,name,None,{'files':result['files']},reason)
            (folder/'status.json').write_text(json.dumps({'ok':True,'checked_at':time.time()}))
            return self.reply(200,{'ok':True,'filename':name})
        except management.APIError as e:self.reply(e.status,{'error':str(e)})
        except (ValueError,OSError,sqlite3.Error):self.reply(400,{'error':'백업 파일과 저장공간을 확인해 주세요.'})

    def performance_file_download(self,path):
        try:
            import performance_files
            with connect() as db:
                session=self.session(db)
                if not session:return self.reply(401,{'error':'로그인이 필요합니다.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone()
                file,stored=performance_files.download(db,user,int(path.rsplit('/',1)[-1]),CATALOG,DB.parent/'uploads');body=stored.read_bytes()
            self.send_response(200);self.headers_common('application/octet-stream',len(body));self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(file['original_name'],safe=''));self.end_headers();self.wfile.write(body)
        except management.APIError as e:self.reply(e.status,{'error':str(e)})
        except (ValueError,OSError):self.reply(404,{'error':'증빙파일을 찾을 수 없습니다.'})

    def performance_file_upload(self):
        created=None
        try:
            import performance_files
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=evidence.MAX_SIZE:return self.reply(413,{'error':'파일당 10MB 이하로 업로드해 주세요.'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/octet-stream':return self.reply(415,{'error':'파일 전송 형식을 확인해 주세요.'})
            with connect() as db:
                db.execute('BEGIN IMMEDIATE');session=self.session(db)
                if not session:return self.reply(401,{'error':'로그인이 필요합니다.'})
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):return self.reply(403,{'error':'세션을 새로고침해 주세요.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone();params=parse_qs(urlsplit(self.path).query);key=int(params.get('record_id',[''])[0])
                body=self.rfile.read(length)
                if len(body)!=length:raise management.APIError(400,'파일 전송이 완료되지 않았습니다.')
                result=performance_files.upload(db,user,key,unquote(self.headers.get('X-File-Name','')),body,CATALOG,DB.parent/'uploads')
                stored=db.execute('SELECT storage_name FROM performance_files WHERE id=?',(result['file']['id'],)).fetchone()[0];created=DB.parent/'uploads'/stored;db.commit();created=None
                return self.reply(200,result)
        except management.APIError as e:self.reply(e.status,{'error':str(e)})
        except (ValueError,OSError,sqlite3.Error):self.reply(400,{'error':'파일 또는 저장 공간을 확인해 주세요.'})
        finally:
            if created:created.unlink(missing_ok=True)

    def performance_export(self):
        try:
            import performance,review
            with connect() as db:
                session=self.session(db)
                if not session:return self.reply(401,{'error':'로그인이 필요합니다.'})
                user=db.execute('SELECT * FROM users WHERE employee_id=?',(session['employee_id'],)).fetchone();params={k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                dep,year=performance.scope(db,user,params,CATALOG);management.require_manager(user,dep)
                if params.get('kind')=='office':
                    import office
                    report=office.snapshot(db,user,params,management.effective_catalog(db,CATALOG))
                    rows=[['지표','가중치','유효배점','득점','득점률','누계 집계기준','이전자료 검토']]
                    details=[['지표','사번','기간','상태','실적·대상없음 사유','추진내용','향후계획']]
                    for item in report['items']:
                        calc=item['aggregate'].get('calculation') or {};rows.append([item['indicator']['name'],item['definition']['weight'],calc.get('effective_weight'),calc.get('score'),calc.get('percent'),item['definition']['time_aggregation'],json.dumps(item.get('legacy_review',{}),ensure_ascii=False)])
                        for record in item['records']:details.append([item['indicator']['name'],record['employee'],record['start']+' ~ '+record['end'],record['state'],json.dumps(record['values'],ensure_ascii=False),record['narrative'],record['plan']])
                    sheets=[('공식 제출집계',rows),('직원 원시실적',details),('제출정보',[['기준일','버전','상태','제출자','사유','원본 SHA-256'],[report['as_of'],report['submission_version'],report['submission_state'],report['actor'],report['reason'],report['sha256']]])]
                elif params.get('kind')=='audit':
                    events=performance.get(db,user,'/api/performance/audit',params,CATALOG,available)['events'];rows=[['시각','처리자','작업','대상','사유','변경 전','변경 후']]+[[r[k] for k in ('created_at','actor','action','target','reason','before_json','after_json')] for r in events];sheets=[('변경이력',rows)]
                elif params.get('kind')=='annual':
                    report=performance.annual(db,user,dep,year,management.effective_catalog(db,CATALOG),available);rows=[['지표','배점','득점','평가자','등급','가감점','결측','근거']]+[[r['indicator']['name'],r['definition']['weight'],r['score'],r['rating']['evaluator'],r['rating']['grade'],r['rating']['adjustment'],r['rating']['excluded'],r['reason']] for r in report['items']];rows.append(['합계',report['maximum'],report['score']]);sheets=[('최종평가',rows)]
                else:
                    report=performance.get(db,user,'/api/performance',{**params,'view':'review'},management.effective_catalog(db,CATALOG),available);rows=[['지표','사번','시작','종료','주기','진행상태','제출상태','득점','실적','내용','향후계획','검토사유']]
                    for item in report['items']:
                        for r in item['history']:rows.append([item['indicator']['name'],r['employee'],r['start'],r['end'],r['cadence'],r['status'],r['state'],r['calculation']['score'],json.dumps(r['values'],ensure_ascii=False),r['narrative'],r['plan'],r['feedback']])
                    sheets=[('기간별실적',rows)]
                body=review.xlsx(sheets)
            self.send_response(200);self.headers_common('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',len(body));self.send_header('Content-Disposition','attachment; filename="performance.xlsx"');self.end_headers();self.wfile.write(body)
        except management.APIError as e:self.reply(e.status,{'error':str(e)})

def main():
    global DB, SECURE
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--no-auto-backup', action='store_true')
    parser.add_argument('--key', type=Path)
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error('--cert와 --key를 함께 지정해 주세요.')
    DB = args.db
    initialize()
    if not args.no_auto_backup:
        import operations
        operations.start_service(DB)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.cert, args.key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        SECURE = True
    print(f'Performance Manager: {"https" if SECURE else "http"}://{args.host}:{args.port}', flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
