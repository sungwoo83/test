"""Exclusive workplace assignments, privacy, concurrency and historical preservation."""
import concurrent.futures
import http.client
import json
import unittest
import app
import office
import test_app
import test_management

class AssignmentFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post

    def pending(self,name='next',keys=None,department=None):
        self.cookie=''
        result=self.request('/api/register',{'employee_id':name,'display_name':'배정 대기 '+name,'password':self.password,'department_id':department or self.a['id'],'indicators':keys or []})
        self.assertEqual(result[0],200,result)
        return self.cookie
    def options(self):return self.request('/api/assignment-options?department_id='+self.a['id'])[1]
    def adjust(self,target='next',revision=None,**kw):
        return self.post('/api/performance/office',{'action':'reassign','department_id':self.a['id'],'indicator_id':self.ka[0],'employee_id':target,'roster_revision':revision or self.options()['revision'],'reason':'담당자 변경 확인',**kw})

    def test_public_availability_hides_identity_and_own_selection_is_visible(self):
        self.cookie='';result=self.options();item=next(i for i in result['items'] if i['indicator_id']==self.ka[0])
        self.assertTrue(item['taken']);self.assertFalse(item['own']);self.assertNotIn('owners',item);self.assertNotIn('revision',result)
        self.assertNotIn('worker',json.dumps(result))
        self.cookie=self.worker;self.assertTrue(next(i for i in self.options()['items'] if i['indicator_id']==self.ka[0])['own'])
        self.cookie=self.manager;self.assertEqual(next(i for i in self.options()['items'] if i['indicator_id']==self.ka[0])['owners'][0]['employee_id'],'worker')

    def test_registration_profile_and_confirmation_reject_occupied_keys(self):
        cookie=self.pending();self.assertEqual(self.post('/api/profile',{'revision':1,'department_id':self.a['id'],'indicators':[self.ka[0]],'employee_id':'worker'})[0],409)
        self.cookie='';self.assertEqual(self.request('/api/register',{'employee_id':'blocked','display_name':'중복 신청','password':self.password,'department_id':self.a['id'],'indicators':self.ka})[0],409)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/office',{'action':'assignment','employee_id':'next','revision':1,'indicators':self.ka,'reason':'배정 확정'})[0],409)
        with app.connect() as db:self.assertEqual(db.execute("SELECT assignment_confirmed FROM users WHERE employee_id='next'").fetchone()[0],0)

    def test_reassign_preserves_old_records_and_invalidates_affected_sessions(self):
        self.cookie=self.worker;result=self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'5','status':'진행중','future_plan':'후속 계획','reason':'기존 실적'});self.assertEqual(result[0],200)
        cookie=self.pending();self.cookie=self.manager;self.assertEqual(self.adjust()[0],200)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT employee_id,actual FROM monthly_actuals').fetchone()[:],('worker','5'))
            former=db.execute("SELECT indicators FROM users WHERE employee_id='worker'").fetchone()[0];self.assertNotIn(self.ka[0],json.loads(former))
            successor=db.execute("SELECT * FROM users WHERE employee_id='next'").fetchone();self.assertEqual(json.loads(successor['indicators']),[self.ka[0]]);self.assertTrue(successor['assignment_confirmed'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_log WHERE action='지표 담당자 조정'").fetchone()[0],1)
        for c in (self.worker,cookie):self.cookie=c;self.assertEqual(self.request('/api/me')[0],401)
        self.cookie='';self.request('/api/login',{'employee_id':'next','password':self.password})
        self.assertEqual(self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'8','status':'진행중','reason':'신규 담당 실적'})[0],200)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM monthly_actuals').fetchone()[0],2)

    def test_scope_stale_revision_and_legacy_duplicate_resolution(self):
        self.pending();self.cookie=self.manager;old=self.options()['revision']
        self.assertEqual(self.adjust(department_id=self.b['id'])[0],403)
        self.assertEqual(self.adjust()[0],200);self.assertEqual(self.adjust(target='',revision=old)[0],409)
        with app.connect() as db:
            db.execute("UPDATE users SET indicators=?,assignment_confirmed=1 WHERE employee_id='worker'",(json.dumps(self.ka),))
            person=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            with self.assertRaisesRegex(Exception,'다른 직원'):office.assignment(db,person,self.a['id'],self.ka[0])
        self.assertTrue(next(i for i in self.options()['items'] if i['indicator_id']==self.ka[0])['conflict'])
        self.assertEqual(self.adjust()[0],200)
        self.assertFalse(next(i for i in self.options()['items'] if i['indicator_id']==self.ka[0])['conflict'])

    def test_simultaneous_confirmation_has_exactly_one_owner(self):
        with app.connect() as db:db.execute("UPDATE users SET indicators='[]' WHERE employee_id='worker'")
        self.pending('one',self.ka);self.pending('two',self.ka);self.cookie=self.manager;csrf=self.request('/api/me')[1]['csrf'];cookie=self.cookie
        def confirm(employee):
            c=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
            body={'csrf':csrf,'action':'assignment','employee_id':employee,'revision':1,'indicators':self.ka,'reason':'동시 확정 시험'}
            c.request('POST','/api/performance/office',json.dumps(body),{'Cookie':cookie,'Content-Type':'application/json','X-Requested-With':'PerformanceManager'})
            r=c.getresponse();status=r.status;r.read();c.close();return status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(confirm,['one','two']))
        self.assertEqual(sorted(statuses),[200,409])
        with app.connect() as db:self.assertEqual(len(office.owners(db,self.a['id'],self.ka[0])),1)

    def test_inactive_reservation_and_cross_workplace_independence(self):
        with app.connect() as db:db.execute("UPDATE users SET account_status='inactive' WHERE employee_id='worker'")
        self.pending();self.cookie=self.manager;self.assertTrue(next(i for i in self.options()['items'] if i['indicator_id']==self.ka[0])['taken']);self.assertEqual(self.adjust()[0],200)
        self.pending('elsewhere',self.kb,self.b['id'])

    def test_empty_selection_can_wait_for_manager(self):
        self.pending();me=self.request('/api/me')[1]
        self.assertEqual(me['user']['indicators'],[]);self.assertFalse(me['user']['assignment_confirmed'])
        self.assertEqual(self.post('/api/profile',{'revision':1,'department_id':self.b['id'],'indicators':[]})[0],200)

    def test_reassignment_preserves_frozen_plans_and_evidence(self):
        import performance
        import performance_files
        from test_performance import definition
        with app.connect() as db:
            superuser=db.execute("SELECT * FROM users WHERE employee_id='super'").fetchone()
            worker=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            for year in (2025,2026):
                plan=definition(owner='worker');plan.update(start=f'{year}-01-01',end=f'{year}-11-30');plan['schedule']=[{'effective':f'{year}-01-01','cadence':'monthly'}]
                performance.post(db,superuser,'/api/performance/plan',{'department_id':self.a['id'],'year':year,'indicator_id':self.ka[0],'revision':0,'definition':plan,'reason':'연도 기준 설정'},app.CATALOG,app.available)
            record=performance.post(db,worker,'/api/performance/record',{'department_id':self.a['id'],'year':2026,'date':'2026-05-10','indicator_id':self.ka[0],'revision':0,'values':{'count':'5'},'narrative':'기존 추진 내용','plan':'계획','status':'진행중','reason':'실적 입력'},app.CATALOG,app.available)['record']
            file=performance_files.upload(db,worker,record['id'],'proof.pdf',b'%PDF-assignment-proof',app.CATALOG,app.DB.parent/'uploads')['file']
            db.execute('INSERT INTO performance_years(department,year,closed,revision,report) VALUES(?,?,1,1,?)',(self.a['id'],2025,'{}'))
            frozen=db.execute('SELECT definition,revision FROM performance_plans WHERE year=2025').fetchone()[:]
        self.pending();self.cookie=self.manager;self.assertEqual(self.adjust()[0],200)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT definition,revision FROM performance_plans WHERE year=2025').fetchone()[:],frozen)
            self.assertEqual(json.loads(db.execute('SELECT definition FROM performance_plans WHERE year=2026').fetchone()[0])['representative'],'next')
            self.assertEqual(db.execute('SELECT employee FROM performance_records WHERE id=?',(record['id'],)).fetchone()[0],'worker')
            self.assertIsNone(db.execute('SELECT deleted_at FROM performance_files WHERE id=?',(file['id'],)).fetchone()[0])
            self.assertEqual((app.DB.parent/'uploads'/db.execute('SELECT storage_name FROM performance_files WHERE id=?',(file['id'],)).fetchone()[0]).read_bytes(),b'%PDF-assignment-proof')

    def test_handover_confirms_only_explicit_keys_and_cannot_keep_third_party_conflict(self):
        extra=next(k['id'] for k in app.available(self.a['id']) if k['id'] not in self.ka)
        self.pending(keys=[extra]);self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/account',{'action':'handover','employee_id':'worker','successor':'next','indicators':[self.ka[0]],'reason':'후임자 인계'})[0],200)
        with app.connect() as db:
            row=db.execute("SELECT * FROM users WHERE employee_id='next'").fetchone();self.assertTrue(row['assignment_confirmed']);self.assertEqual(json.loads(row['indicators']),[self.ka[0]])
            # Legacy collision with another confirmed owner must not propagate through a handover.
            db.execute("UPDATE users SET indicators=?,assignment_confirmed=1 WHERE employee_id='manager'",(json.dumps([self.ka[1]]),))
        self.assertEqual(self.post('/api/performance/account',{'action':'handover','employee_id':'worker','successor':'next','indicators':[self.ka[1]],'reason':'중복 인계 차단'})[0],409)

    def test_console_superadmin_creation_does_not_reserve_employee_indicators(self):
        import manage
        from unittest.mock import patch
        department=next(d['id'] for d in app.CATALOG['departments'] if 'AI정보융합실' in d['name'])
        self.pending('aiworker',keys=[app.available(department)[0]['id']],department=department)
        with app.connect() as db:db.execute("UPDATE users SET assignment_confirmed=1 WHERE employee_id='aiworker'")
        with patch('sys.argv',['manage.py','add-superadmin','--employee-id','new-super','--department-id',department,'--name','운영자','--db',str(app.DB)]),patch('manage.read_password',return_value='synthetic-only-password'):
            manage.main()
        with app.connect() as db:
            row=db.execute("SELECT * FROM users WHERE employee_id='new-super'").fetchone()
            self.assertEqual(row['role'],'superadmin');self.assertEqual(json.loads(row['indicators']),[])

    def test_pending_or_duplicate_owner_cannot_upload_evidence(self):
        import evidence
        self.cookie=self.worker;result=self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'5','status':'진행중','reason':'실적 등록'});self.assertEqual(result[0],200)
        with app.connect() as db:
            db.execute("UPDATE users SET assignment_confirmed=0 WHERE employee_id='worker'")
            worker=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            with self.assertRaisesRegex(Exception,'배정을 확정'):evidence.upload(db,worker,result[1]['record_id'],'proof.pdf',b'%PDF-proof',app.CATALOG,app.DB.parent/'uploads')
