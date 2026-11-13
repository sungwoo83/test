import copy
import io
import json
import unittest
import zipfile
from decimal import Decimal
import app
import management as m
import performance as p
import office
import evidence_bundle
import performance_files
import test_app
import test_management
import test_performance as tp
definition=tp.definition


class ExceptionAndPrecision(unittest.TestCase):
    def test_zero_missing_excluded_and_full_are_distinct(self):
        plan=definition();plan['components'][0]['no_target_policy']='exclude'
        missing=p.calculate(plan,{});self.assertIsNone(missing['score'])
        zero=p.calculate(plan,{'count':'0'});self.assertEqual(zero['score'],'6.000');self.assertEqual(zero['effective_weight'],'20')
        no_target={'state':'no_target','count':'0','reason':'당해연도 대상 항목 없음'}
        excluded=p.calculate(plan,{'count':no_target},True);self.assertTrue(excluded['fully_excluded']);self.assertEqual(excluded['effective_weight'],'0');self.assertIsNone(excluded['percent'])
        plan['components'][0]['no_target_policy']='full';full=p.calculate(plan,{'count':no_target},True);self.assertEqual(full['score'],'20.000');self.assertEqual(full['percent'],'100.000')
        for bad in [{**no_target,'count':'1'},{**no_target,'reason':''}]:
            with self.assertRaises(m.APIError):p.calculate(plan,{'count':bad})
        plan['components'][0]['no_target_policy']='none'
        with self.assertRaises(m.APIError):p.calculate(plan,{'count':no_target})

    def test_partial_exclusion_retains_other_components(self):
        plan=definition();a=plan['components'][0];a.update(weight='10',no_target_policy='exclude');b={**a,'id':'other','no_target_policy':'none'};plan['components'].append(b)
        result=p.calculate(plan,{'count':{'state':'no_target','count':0,'reason':'해당 공시항목 없음'},'other':'10'},True)
        self.assertEqual(result['score'],'10.000');self.assertEqual(result['effective_weight'],'10');self.assertEqual(result['percent'],'100.000')
        self.assertEqual(len(result['components']),2)

    def test_rounding_and_sum_use_underlying_values(self):
        self.assertEqual(p.fmt('1.23456'),'1.235');self.assertEqual(p.fmt('1.23449'),'1.234')
        plan=definition();plan.update(configured=True,time_aggregation='sum')
        rows=[{'id':i,'employee':'worker','start':f'2026-11-{i*7+1:02d}','end':f'2026-11-{i*7+7:02d}','state':'approved','updated_at':'now','definition':json.dumps(plan),'values_json':json.dumps({'count':v})} for i,v in enumerate(['2','3','5'])]
        totals=p.numeric_totals(plan,rows,'2026-11-30','worker');self.assertEqual(totals['percent'],'100.000');self.assertEqual(totals['values']['count'],'10')
        self.assertNotIn('code',totals)


class RevisionFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post
    payload=tp.PerformanceFlow.payload
    configure=tp.PerformanceFlow.configure
    save=tp.PerformanceFlow.save
    transition=tp.PerformanceFlow.transition

    def test_target_routes_super_only_schedule_route_preserves_rules(self):
        self.configure();self.cookie=self.manager
        for path,payload in [('/api/performance/plan',self.payload(revision=1,definition=definition())),('/api/monthly/settings',{**self.context,'indicator_id':self.ka[1],'revision':0,'target':'1','reason':'목표 수정 시도'}),('/api/performance/copy-year',self.payload(year=2027,source_year=2026))]:self.assertEqual(self.post(path,payload)[0],403)
        before=self.request('/api/performance?department_id='+self.a['id']+'&year=2026&date=2026-11-13&view=review')[1]['items'][0]['definition']
        malicious=definition();malicious['weight']='999'
        status,data=self.post('/api/performance/schedule',self.payload(revision=1,schedule=[{'effective':'2026-01-01','cadence':'weekly'}],due_days=3,definition=malicious));self.assertEqual(status,200,data)
        after=self.request('/api/performance?department_id='+self.a['id']+'&year=2026&date=2026-11-13&view=review')[1]['items'][0]['definition']
        self.assertEqual(after['weight'],before['weight']);self.assertEqual(after['components'],before['components']);self.assertEqual(after['schedule'][0]['cadence'],'weekly')

    def test_new_signup_requires_manager_assignment_confirmation(self):
        self.configure();self.cookie=''
        status,_=self.request('/api/register',{'employee_id':'pending','display_name':'배정 대기 직원','password':self.password,'department_id':self.a['id'],'indicators':self.ka});self.assertEqual(status,200)
        cookie=self.cookie;me=self.request('/api/me')[1];self.assertEqual(me['user']['assignment_confirmed'],0)
        status,_=self.post('/api/performance/record',self.payload(revision=0,values={'count':'5'},narrative='',plan='',status='진행중'));self.assertEqual(status,403)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/office',self.payload(action='assignment',employee_id='pending',revision=me['user']['revision'],indicators=self.ka))[0],200)
        self.cookie=cookie;self.assertEqual(self.request('/api/me')[0],401)
        self.cookie='';self.request('/api/login',{'employee_id':'pending','password':self.password})
        self.assertEqual(self.post('/api/performance/record',self.payload(revision=0,values={'count':'5'},narrative='',plan='',status='진행중'))[0],200)

    def complete_office(self):
        with app.connect() as db:
            self.keys=[k['id'] for k in app.available(self.a['id'],db)]
            db.execute("UPDATE users SET indicators=? WHERE employee_id='worker'",(json.dumps(self.keys),));db.execute("UPDATE users SET indicators='[]' WHERE employee_id='manager'")
        for key in self.keys:
            self.configure(key=key)
            self.cookie=self.worker
            status,data=self.post('/api/performance/record',self.payload(indicator_id=key,revision=0,values={'count':'10'},narrative='공식 실적',plan='후속 계획',status='완료'));self.assertEqual(status,200,data)
            self.assertEqual(self.transition('submit',1,indicator_id=key)[0],200);self.assertEqual(self.transition('approve',2,self.manager,indicator_id=key)[0],200)

    def test_official_snapshot_permissions_lock_withdraw_and_revisions(self):
        self.complete_office();self.cookie=self.worker
        self.assertEqual(self.post('/api/performance/office',self.payload(action='submit',version=0))[0],403)
        self.cookie=self.manager
        uri='/api/performance/office?department_id='+self.a['id']+'&year=2026&date=2026-11-13'
        status,preview=self.request(uri);self.assertEqual(status,200,preview);self.assertTrue(preview['ready'])
        status,submitted=self.post('/api/performance/office',self.payload(action='submit',version=0));self.assertEqual(status,200,submitted)
        self.assertEqual(self.transition('return',3,self.manager)[0],409)
        self.cookie=self.manager;self.assertEqual(self.post('/api/performance/office',self.payload(action='submit',version=0))[0],409)
        with app.connect() as db:before=db.execute('SELECT snapshot FROM office_submissions WHERE id=?',(submitted['id'],)).fetchone()[0]
        config=definition();config['components'][0]['target']='20';self.configure(config,1)
        with app.connect() as db:self.assertEqual(db.execute('SELECT snapshot FROM office_submissions WHERE id=?',(submitted['id'],)).fetchone()[0],before)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/office',self.payload(action='withdraw',submission_id=submitted['id']))[0],200)
        self.assertEqual(self.transition('return',3,self.manager)[0],200)

    def test_paper_routing_is_separate_from_goals_and_super_only(self):
        self.cookie=self.manager
        payload=self.payload(revision=0,recipients=['공문 검증 전담부서'])
        self.assertEqual(self.post('/api/performance/evidence-routing',payload)[0],403)
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/performance/evidence-routing',payload)[0],200)
        result=self.request('/api/performance/evidence-routing')[1]
        item=next(i for i in result['items'] if i['id']==self.ka[0]);self.assertEqual(item['recipients'],['공문 검증 전담부서'])
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM performance_plans').fetchone()[0],0)

    def test_bundle_super_only_grouping_docx_and_hash_validation(self):
        self.configure();record=self.save()[1]['record'];storage=app.DB.parent/'uploads'
        with app.connect() as db:
            user=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone();performance_files.upload(db,user,record['id'],'증빙.pdf',b'%PDF-private-test',app.CATALOG,storage)
            row=db.execute('SELECT * FROM performance_plans').fetchone();config=json.loads(row['definition']);config['verification_departments']=['AI정보융합실','성과혁신실'];db.execute('UPDATE performance_plans SET definition=?',(json.dumps(config),))
        for cookie in [self.worker,self.manager]:
            self.cookie=cookie
            import test_entries
            self.assertEqual(test_entries.EntryFlow.binary(self,'/api/evidence-bundle.zip?year=2026&state=all')[0],403)
        with app.connect() as db:
            superuser=db.execute("SELECT * FROM users WHERE employee_id='super'").fetchone()
            bundle,summary=evidence_bundle.export(db,superuser,{'year':2026,'state':'all','group_by':'validator'},app.CATALOG,app.available,storage)
            self.assertEqual(summary['files'],1);self.assertEqual(summary['groups'],2)
            with zipfile.ZipFile(bundle) as z:
                self.assertEqual(len([n for n in z.namelist() if n.endswith('.pdf')]),2)
                self.assertTrue(all(not n.startswith('/') and '..' not in n.split('/') for n in z.namelist()))
                doc=next(n for n in z.namelist() if n.endswith('.docx'))
                with zipfile.ZipFile(io.BytesIO(z.read(doc))) as word:self.assertIn('공문 초안',word.read('word/document.xml').decode())
                self.assertTrue(any(n.endswith('실적증빙목록.xlsx') for n in z.namelist()));self.assertFalse(any(n.endswith('.sqlite3') for n in z.namelist()))
            bundle.close()
            file=db.execute('SELECT storage_name FROM performance_files').fetchone();(storage/file[0]).write_bytes(b'tampered')
            with self.assertRaises(m.APIError):evidence_bundle.export(db,superuser,{'year':2026,'state':'all'},app.CATALOG,app.available,storage)

    def test_legacy_material_requires_explicit_reconciliation(self):
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/actual',{**self.context,'month':3,'indicator_id':self.ka[0],'revision':0,'value':'3','reason':'이전 실적'})[0],200)
        config=definition();config['start']='2026-04-01';config['schedule'][0]['effective']=config['start'];self.configure(config)
        record=self.save()[1]['record'];self.transition('submit',1);self.transition('approve',2,self.manager)
        with app.connect() as db:
            db.execute("UPDATE users SET indicators='[]' WHERE employee_id='manager'")
            manager=db.execute("SELECT * FROM users WHERE employee_id='manager'").fetchone();indicator=app.available(self.a['id'],db)[0]
            available=lambda dep,connection:[indicator] if dep==self.a['id'] else []
            payload=self.payload(action='submit',version=0)
            with self.assertRaises(m.APIError):office.post(db,manager,payload,app.CATALOG,available)
            payload['legacy_dispositions']={self.ka[0]:{'mode':'included','record_id':record['id'],'reason':'이전 실적 3건을 확정 누적값 5건에 포함하여 재확인'}}
            result=office.post(db,manager,payload,app.CATALOG,available);self.assertEqual(result['version'],1)
            original=db.execute('SELECT id FROM monthly_actuals').fetchone()[0]
            with self.assertRaises(m.APIError):office.guard(db,'legacy',original)


if __name__=='__main__':unittest.main()
