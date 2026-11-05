"""폐쇄망용 직원 설정 모듈. Python 3.11+, 외부 패키지 불필요."""
import argparse
import management
from urllib.parse import urlsplit, parse_qs
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
            for k in ('employee_id', 'department_id', 'indicators', 'revision', 'role', 'admin_department_id')}

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
        return db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?', (digest, time.time())).fetchone()

    def cookie(self, token, age):
        return f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}' + ('; Secure' if SECURE else '')

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/catalog':
            with connect() as db:
                return self.reply(200, {'demo': DEMO, 'departments': CATALOG['departments'],
                    'indicators_by_department': {d['id']: available(d['id'], db) for d in CATALOG['departments']}})
        if path == '/api/monthly' or path.startswith('/api/admin/'):
            with connect() as db:
                session = self.session(db)
                if not session: return self.reply(401, {'error':'로그인이 필요합니다.'})
                user = db.execute('SELECT * FROM users WHERE employee_id=?', (session['employee_id'],)).fetchone()
                params = {k:v[0] for k,v in parse_qs(urlsplit(self.path).query).items()}
                try:
                    return self.reply(200, management.get(db,user,path,params,management.effective_catalog(db,CATALOG),available))
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
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
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
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
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
                        dept, indicators = selection(data)
                        if db.execute('SELECT 1 FROM users WHERE employee_id=?', (employee,)).fetchone():
                            return self.reply(409, {'error': '이미 가입된 사번입니다. 로그인해 주세요.'})
                        db.execute('INSERT INTO users(employee_id,password_hash,department_id,indicators,revision,created_at) VALUES(?,?,?,?,1,?)', (employee, password_hash(password), dept, json.dumps(indicators), now()))
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
                if self.path.startswith('/api/monthly/') or self.path.startswith('/api/admin/'):
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
                    if row['role']=='superadmin' and dept!='SYS-PERFORMANCE':
                        return self.reply(409, {'error':'수퍼관리자의 소속은 성과혁신실로 유지해야 합니다.'})
                    before = {'department_id': row['department_id'], 'indicators': json.loads(row['indicators'])}
                    after = {'department_id': dept, 'indicators': indicators}
                    if before != after:
                        db.execute('UPDATE users SET department_id=?,indicators=?,revision=revision+1 WHERE employee_id=?', (dept, json.dumps(indicators), row['employee_id']))
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

def main():
    global DB, SECURE
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--key', type=Path)
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error('--cert와 --key를 함께 지정해 주세요.')
    DB = args.db
    initialize()
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
