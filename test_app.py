import http.client
import importlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
import app

class ProfileFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DB = Path(self.temp.name) / 'test.sqlite3'
        app.initialize()
        self.server = ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookie = ''
        self.password = 'correct-password-123'
        self.a, self.b = app.CATALOG['departments'][:2]
        self.ka = [k['id'] for k in app.available(self.a['id'])[:2]]
        self.kb = [k['id'] for k in app.available(self.b['id'])[:1]]
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()
    def request(self,path,data=None,headers=None):
        c = http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        h = {'Cookie':self.cookie}
        if data is not None:
            h.update({'Content-Type':'application/json','X-Requested-With':'PerformanceManager'})
        if headers: h.update(headers)
        c.request('POST' if data is not None else 'GET',path,json.dumps(data) if data is not None else None,h)
        r=c.getresponse(); status=r.status; value=json.loads(r.read())
        cookie=r.getheader('Set-Cookie')
        if cookie: self.cookie=cookie.split(';')[0]
        c.close(); return status,value
    def register(self,employee='000123'):
        return self.request('/api/register',{'employee_id':employee,'display_name':'테스트 직원 '+employee,'password':self.password,'department_id':self.a['id'],'indicators':self.ka})
    def test_full_flow_and_database_persistence(self):
        self.assertEqual(self.request('/api/me')[0],401)
        self.assertEqual(self.register()[0],200)
        status,me=self.request('/api/me'); self.assertEqual(status,200)
        self.assertEqual(me['user']['employee_id'],'000123')
        self.assertEqual(me['user']['indicators'],sorted(self.ka))
        self.assertEqual(self.register()[0],409)
        self.assertEqual(self.request('/api/profile',{'csrf':'invalid','department_id':self.b['id'],'indicators':self.kb})[0],403)
        change={'csrf':me['csrf'],'revision':me['user']['revision'],'department_id':self.b['id'],'indicators':self.kb}
        self.assertEqual(self.request('/api/profile',{**change,'indicators':self.ka})[0],400)
        self.assertEqual(self.request('/api/profile',{**change,'indicators':[]})[0],400)
        self.assertEqual(self.request('/api/profile',change)[0],200)
        self.assertEqual(self.request('/api/profile',change)[0],409)
        status,updated=self.request('/api/me'); self.assertEqual(updated['user']['department_id'],self.b['id'])
        self.assertEqual(len(updated['history']),2)
        self.assertEqual(json.loads(updated['history'][0]['before_json'])['department_id'],self.a['id'])
        self.assertEqual(self.request('/api/logout',{'csrf':updated['csrf']})[0],200)
        self.assertEqual(self.request('/api/me')[0],401)
        app.initialize()  # Reopening persisted DB must retain account and settings.
        self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':self.password})[0],200)
        self.assertEqual(self.request('/api/me')[1]['user']['indicators'],self.kb)
        with app.connect() as db:
            hashed=db.execute('SELECT password_hash FROM users').fetchone()[0]
            self.assertNotIn(self.password,hashed)
    def test_registration_requires_name_and_preserves_it_after_login(self):
        payload={'employee_id':'NAMED001','password':self.password,'department_id':self.a['id'],'indicators':self.ka}
        for name in [None,'','   ','이름'*51,123,'잘못된\n이름']:
            self.assertEqual(self.request('/api/register',{**payload,'display_name':name})[0],400)
        self.assertEqual(self.request('/api/register',{**payload,'display_name':'  테스트 직원  '})[0],200)
        me=self.request('/api/me')[1];self.assertEqual(me['user']['display_name'],'테스트 직원')
        self.request('/api/logout',{'csrf':me['csrf']})
        self.assertEqual(self.request('/api/login',{'employee_id':'NAMED001','password':self.password})[0],200)
        self.assertEqual(self.request('/api/me')[1]['user']['display_name'],'테스트 직원')
    def test_existing_account_without_name_can_still_login(self):
        self.register()
        with app.connect() as db:db.execute("UPDATE users SET display_name='' WHERE employee_id='000123'")
        self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':self.password})[0],200)

    def test_validation_and_authentication_limits(self):
        self.assertEqual(self.register()[0],200)
        self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':self.password}, {'X-Requested-With':''})[0],403)
        self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':self.password}, {'Sec-Fetch-Site':'cross-site'})[0],403)
        for _ in range(10):
            self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':'incorrect'})[0],401)
        self.assertEqual(self.request('/api/login',{'employee_id':'000123','password':self.password})[0],429)
    def test_other_accounts_cannot_be_targeted(self):
        self.register('000123')
        first_cookie=self.cookie
        self.cookie=''; self.register('000456')
        me=self.request('/api/me')[1]
        self.assertEqual(self.request('/api/profile',{'employee_id':'000123','csrf':me['csrf'],'revision':1,'department_id':self.b['id'],'indicators':self.kb})[0],200)
        self.cookie=first_cookie
        self.assertEqual(self.request('/api/me')[1]['user']['department_id'],self.a['id'])
    def test_catalog_coverage(self):
        self.assertGreaterEqual(len(app.CATALOG['departments']),2)
        self.assertEqual(len({d['id'] for d in app.CATALOG['departments']}),len(app.CATALOG['departments']))
        for d in app.CATALOG['departments']:
            if d['kind']=='운영': continue
            self.assertTrue(app.available(d['id']),d['name'])
        self.assertEqual(app.available('unknown'),[])
    def test_session_expiration(self):
        self.register()
        with app.connect() as db: db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.request('/api/me')[0],401)

if __name__=='__main__': unittest.main(verbosity=2)
