import json
import subprocess
import sys
from pathlib import Path
import test_app
import unittest
import tempfile
import sqlite3
import app

class ManagementFlow(unittest.TestCase):
    request = test_app.ProfileFlow.request
    register = test_app.ProfileFlow.register
    tearDown = test_app.ProfileFlow.tearDown
    def setUp(self):
        test_app.ProfileFlow.setUp(self)
        self.register('worker')
        self.worker=self.cookie
        self.cookie='';self.register('manager')
        with app.connect() as db:
            db.execute("UPDATE users SET role='admin',admin_department_id=? WHERE employee_id='manager'",(self.a['id'],))
        self.manager=self.cookie
        self.cookie=''
        self.request('/api/register',{'employee_id':'super','password':self.password,'department_id':'SYS-PERFORMANCE','indicators':[],'role':'superadmin'})
        self.super_cookie=self.cookie
        self.assertEqual(self.request('/api/me')[1]['user']['role'],'employee')
        with app.connect() as db:db.execute("UPDATE users SET role='superadmin' WHERE employee_id='super'")
        self.context={'department_id':self.a['id'],'year':2026,'month':5}
    def post(self,path,data):
        csrf=self.request('/api/me')[1]['csrf']
        return self.request(path,{**data,'csrf':csrf})
    def test_role_boundaries_and_registration_forgery(self):
        self.cookie=self.worker
        self.assertEqual(self.request('/api/admin/users')[0],403)
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'worker','role':'superadmin','reason':'권한위조'})[0],403)
        self.cookie=self.manager
        self.assertEqual(self.request('/api/admin/indicators')[0],403)
        self.assertEqual(self.request('/api/monthly?department_id='+self.b['id']+'&year=2026&month=5')[0],403)
        me=self.request('/api/me')[1]
        self.assertEqual(self.post('/api/profile',{'department_id':self.b['id'],'indicators':self.kb,'revision':me['user']['revision']})[0],200)
        self.assertEqual(self.request('/api/monthly?department_id='+self.b['id']+'&year=2026&month=5')[0],403)
        self.assertEqual(self.request('/api/monthly?department_id='+self.a['id']+'&year=2026&month=5')[0],200)
    def test_adjustment_lock_reopen_and_snapshot(self):
        self.cookie=self.worker
        actual={**self.context,'indicator_id':self.ka[0],'revision':0,'value':'12.5','reason':'월간실적'}
        self.assertEqual(self.post('/api/monthly/actual',actual)[0],200)
        self.assertEqual(self.post('/api/monthly/adjust',{**actual,'revision':1,'value':'11'})[0],403)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/monthly/adjust',{**actual,'revision':1,'value':'11','reason':'증빙 검토 조정'})[0],200)
        with app.connect() as db:
            r=db.execute('SELECT * FROM monthly_actuals').fetchone()
            self.assertEqual(r['actual'],'12.5');self.assertEqual(r['adjusted'],'11')
            snapshot=json.loads(r['snapshot'])
        self.assertEqual(self.post('/api/monthly/period',{**self.context,'status':'closed','revision':0,'reason':'5월 마감'})[0],200)
        self.assertEqual(self.post('/api/monthly/adjust',{**actual,'revision':2,'value':'10'})[0],409)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/actual',{**actual,'revision':2})[0],409)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/monthly/period',{**self.context,'status':'open','revision':1,'reason':'마감해제'})[0],403)
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/monthly/period',{**self.context,'status':'open','revision':1,'reason':'증빙보완 재개방'})[0],200)
        self.assertEqual(self.post('/api/admin/indicator',{'indicator_id':self.ka[0],'revision':0,'name':'테스트 지표 수정','unit':'건','target':'20','points':'5','formula_note':'테스트 설명','reason':'기준변경'})[0],200)
        with app.connect() as db:
            after=json.loads(db.execute('SELECT snapshot FROM monthly_actuals').fetchone()[0]);self.assertEqual(after,snapshot)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/monthly/adjust',{**actual,'revision':2,'value':None,'reason':'조정 해제'})[0],200)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/actual',{**actual,'revision':3,'value':'13'})[0],200)
    def test_concurrency_and_master_permissions(self):
        self.cookie=self.worker
        payload={**self.context,'indicator_id':self.ka[0],'revision':0,'value':'4','reason':'월별 입력'}
        self.assertEqual(self.post('/api/monthly/actual',payload)[0],200)
        self.assertEqual(self.post('/api/monthly/actual',payload)[0],409)
        self.assertEqual(self.post('/api/monthly/actual',{**payload,'month':13})[0],400)
        self.cookie=self.super_cookie
        edit={'indicator_id':self.ka[0],'revision':0,'name':'새 지표명','reason':'검토수정','unit':'건'}
        self.assertEqual(self.post('/api/admin/indicator',edit)[0],200)
        self.assertEqual(self.post('/api/admin/indicator',edit)[0],409)
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'super','role':'employee','reason':'권한해제'})[0],409)
    def test_scoped_audit_and_sessions_revoked(self):
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'worker','role':'admin','admin_department_id':self.b['id'],'reason':'부서 관리자 지정'})[0],200)
        self.cookie=self.worker;self.assertEqual(self.request('/api/me')[0],401)
        self.cookie=self.manager
        self.assertEqual(self.request('/api/admin/audit')[1]['events'],[])
        self.cookie=self.super_cookie
        self.assertEqual(self.request('/api/admin/audit')[1]['events'][0]['action'],'권한 변경')
    def test_bootstrap_requires_office_and_only_once(self):
        with app.connect() as db:db.execute("UPDATE users SET role='employee' WHERE employee_id='super'")
        command=[sys.executable,str(Path(app.__file__).parent/'manage.py'),'bootstrap-superadmin','--db',str(app.DB),'--employee-id']
        denied=subprocess.run(command+['worker'],capture_output=True)
        self.assertNotEqual(denied.returncode,0)
        first=subprocess.run(command+['super'],capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        second=subprocess.run(command+['super'],capture_output=True)
        self.assertNotEqual(second.returncode,0)

    def test_existing_database_migration(self):
        original_db=app.DB
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB=Path(folder)/'legacy.sqlite3'
                with sqlite3.connect(app.DB) as db:
                    db.execute('CREATE TABLE users(employee_id TEXT PRIMARY KEY,password_hash TEXT NOT NULL,department_id TEXT NOT NULL,indicators TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)')
                    db.execute('INSERT INTO users VALUES(?,?,?,?,1,?)',('legacy',app.password_hash(self.password),self.a['id'],json.dumps(self.ka),app.now()))
                app.initialize()
                with app.connect() as db:
                    row=db.execute('SELECT * FROM users').fetchone()
                    self.assertEqual(row['role'],'employee')
                    self.assertEqual(json.loads(row['indicators']),self.ka)
                    self.assertEqual(row['employee_id'],'legacy')
        finally:
            app.DB=original_db

    def register_ai_employee(self):
        department=next((d for d in app.CATALOG['departments'] if d['name']=='AI정보융합실'),None)
        if department is None:
            self.skipTest('예시 데이터에는 AI정보융합실이 없습니다.')
        self.cookie=''
        ids=[k['id'] for k in app.available(department['id'])[:1]]
        self.assertEqual(self.request('/api/register',{'employee_id':'ai-worker','password':self.password,'department_id':department['id'],'indicators':ids,'role':'superadmin'})[0],200)
        self.assertEqual(self.request('/api/me')[1]['user']['role'],'employee')
        return department,ids

    def test_ai_department_superadmin_assignment_and_profile(self):
        department,ids=self.register_ai_employee()
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'worker','role':'superadmin','reason':'다른 부서 거절 확인'})[0],400)
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'ai-worker','role':'superadmin','reason':'AI정보융합실 담당자 지정'})[0],200)
        self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'ai-worker','password':self.password})[0],200)
        me=self.request('/api/me')[1]
        self.assertEqual(me['user']['role'],'superadmin')
        self.assertEqual(self.request('/api/admin/users')[0],200)
        self.assertEqual(self.post('/api/profile',{'department_id':department['id'],'indicators':ids,'revision':me['user']['revision']})[0],200)
        self.assertEqual(self.post('/api/profile',{'department_id':self.b['id'],'indicators':self.kb,'revision':me['user']['revision']})[0],409)

    def test_ai_department_first_superadmin_bootstrap(self):
        self.register_ai_employee()
        with app.connect() as db:
            db.execute("UPDATE users SET role='employee' WHERE employee_id='super'")
        result=subprocess.run([sys.executable,str(Path(app.__file__).parent/'manage.py'),'bootstrap-superadmin','--db',str(app.DB),'--employee-id','ai-worker'],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT role FROM users WHERE employee_id='ai-worker'").fetchone()[0],'superadmin')

    def test_superadmin_password_reset_hash_and_session_revocation(self):
        from unittest.mock import patch
        import manage
        # This is a synthetic test password, unrelated to deployed credentials.
        password='테스트12!'
        argv=['manage.py','set-superadmin-password','--employee-id','super','--db',str(app.DB)]
        with patch.object(sys,'argv',argv),patch.object(manage.getpass,'getpass',side_effect=[password,password]),patch('builtins.print'):
            manage.main()
        self.cookie=self.super_cookie
        self.assertEqual(self.request('/api/me')[0],401)
        self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'super','password':self.password})[0],401)
        self.assertEqual(self.request('/api/login',{'employee_id':'super','password':password})[0],200)
        with app.connect() as db:
            stored=db.execute("SELECT password_hash FROM users WHERE employee_id='super'").fetchone()[0]
            audit=dict(db.execute("SELECT * FROM audit_log WHERE action='수퍼관리자 비밀번호 설정'").fetchone())
            self.assertNotIn(password,stored)
            self.assertNotIn(password,json.dumps(audit,ensure_ascii=False))
            self.assertNotIn(stored,json.dumps(audit,ensure_ascii=False))

    def test_managers_cannot_delegate_admin_role(self):
        self.cookie=self.manager
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'worker','role':'admin','admin_department_id':self.a['id'],'reason':'관리자 지정 시도'})[0],403)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/admin/role',{'employee_id':'worker','role':'admin','admin_department_id':self.a['id'],'reason':'본인 권한 변경 시도'})[0],403)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT role FROM users WHERE employee_id='worker'").fetchone()[0],'employee')

    def test_first_superadmin_can_be_created_without_signup(self):
        from unittest.mock import patch
        import manage
        department=next((d for d in app.CATALOG['departments'] if d['name']=='AI정보융합실'),None)
        if not department:self.skipTest('예시 데이터에는 AI정보융합실이 없습니다.')
        with app.connect() as db:db.execute("UPDATE users SET role='employee' WHERE role='superadmin'")
        password='테스트12!'
        argv=['manage.py','create-superadmin','--employee-id','first-admin','--department-id',department['id'],'--db',str(app.DB)]
        with patch.object(sys,'argv',argv),patch.object(manage.getpass,'getpass',side_effect=[password,password]),patch('builtins.print'):
            manage.main()
        self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'first-admin','password':password})[0],200)
        self.assertEqual(self.request('/api/me')[1]['user']['role'],'superadmin')
        self.assertEqual(self.request('/api/admin/users')[0],200)

    def test_additional_superadmin_name_and_existing_admins_preserved(self):
        from unittest.mock import patch
        import manage
        password='테스트12!'
        argv=['manage.py','add-superadmin','--employee-id','second-admin','--department-id','SYS-PERFORMANCE','--name','테스트담당','--db',str(app.DB)]
        with patch.object(sys,'argv',argv),patch.object(manage.getpass,'getpass',side_effect=[password,password]),patch('builtins.print'):
            manage.main()
        self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'second-admin','password':password})[0],200)
        me=self.request('/api/me')[1]['user']
        self.assertEqual(me['display_name'],'테스트담당')
        self.assertEqual(me['role'],'superadmin')
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT role FROM users WHERE employee_id='super'").fetchone()[0],'superadmin')
