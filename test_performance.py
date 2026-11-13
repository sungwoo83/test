import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from decimal import Decimal
import app
import management
import performance as p
import operations
import performance_files
import test_app
import test_management


def definition(owner='worker',mode='numeric'):
    return {'mode':mode,'weight':'20','components':[] if mode=='text' else [{'id':'count','name':'건수','weight':'20','target':'10','unit':'건','method':'ratio','floor':'.3','cap':'1'}],
      'schedule':[{'effective':'2026-01-01','cadence':'monthly'}],'start':'2026-01-01','end':'2026-11-30','due_days':7,'curve':'linear','milestones':[],
      'aggregation':'representative','representative':owner,'time_aggregation':'latest'}


class PeriodAndScore(unittest.TestCase):
    def test_week_boundaries_and_effective_cadence(self):
        plan=definition();plan['schedule']=[{'effective':'2026-01-01','cadence':'monthly'},{'effective':'2026-11-01','cadence':'weekly'}]
        oct=p.period(plan,'2026-10-31');self.assertEqual((oct['start'],oct['end']),('2026-10-01','2026-10-31'))
        first=p.period(plan,'2026-11-01');self.assertEqual((first['start'],first['end']),('2026-11-01','2026-11-01'))
        week=p.period(plan,'2026-11-13');self.assertEqual((week['start'],week['end']),('2026-11-09','2026-11-15'))
        last=p.period(plan,'2026-11-30');self.assertEqual((last['start'],last['end']),('2026-11-30','2026-11-30'))
        with self.assertRaises(management.APIError):p.period(plan,'2026-12-01')

    def test_ratio_floor_cap_bands_and_no_fabricated_text_score(self):
        plan=definition()
        self.assertEqual(p.calculate(plan,{'count':'0'})['score'],'6.0000')
        self.assertEqual(p.calculate(plan,{'count':'50'})['score'],'20.0000')
        self.assertEqual(p.calculate(plan,{'count':'5'})['percent'],'50.0000')
        self.assertIsNone(p.calculate(definition(mode='text'),{})['score'])
        with self.assertRaises(management.APIError):p.calculate(plan,{},True)
        with self.assertRaises(management.APIError):p.calculate(plan,{'count':'-1'})
        plan['components'][0].update({'method':'bands_lower','target':'0','bands':[['0','1'],['1','.6'],['2','.3']]})
        self.assertEqual(p.calculate(plan,{'count':'1'})['score'],'12.0000')
        self.assertEqual(p.calculate(plan,{'count':'7'})['score'],'6.0000')

    def test_ratio_aggregation_uses_total_denominator(self):
        plan=definition();plan.update({'aggregation':'ratio'});plan['components'][0].update({'method':'rate','target':'100','unit':'%','floor':'0'})
        records=[]
        for i,(n,d) in enumerate([('1','2'),('9','10')]):
            records.append({'id':i,'employee':str(i),'start':'2026-11-01','end':'2026-11-30','updated_at':'now','state':'approved','definition':json.dumps(plan),'values_json':json.dumps({'count':{'numerator':n,'denominator':d}})})
        result=p.aggregate(plan,records)
        self.assertTrue(result['ready']);self.assertEqual(result['calculation']['percent'],'83.3333')
        records[1]['state']='draft';self.assertEqual(p.aggregate(plan,records)['calculation']['percent'],'50.0000')


    def test_period_and_employee_averages_are_explicit(self):
        plan=definition();plan.update({'aggregation':'average','time_aggregation':'average'})
        rows=[]
        for i,(employee,value,month) in enumerate([('a','0',10),('a','10',11),('b','9',11)]):
            rows.append({'id':i,'employee':employee,'start':f'2026-{month:02d}-01','end':f'2026-{month:02d}-30','updated_at':'now','state':'approved','definition':json.dumps(plan),'values_json':json.dumps({'count':value})})
        # Employee a average=5, employee b=9, then employee average=7.
        self.assertEqual(p.aggregate(plan,rows)['calculation']['percent'],'70.0000')


class PerformanceFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post

    def payload(self,**extra):return {'department_id':self.a['id'],'year':2026,'date':'2026-11-13','indicator_id':self.ka[0],'reason':'통합 성과관리 검증',**extra}
    def configure(self,config=None,revision=0,key=None):
        self.cookie=self.manager
        payload=self.payload(revision=revision,definition=config or definition())
        if key:payload['indicator_id']=key
        status,data=self.post('/api/performance/plan',payload);self.assertEqual(status,200,data)
    def save(self,value='5',revision=0,**extra):
        self.cookie=self.worker
        return self.post('/api/performance/record',self.payload(revision=revision,values={'count':value},narrative='실적 내용',plan='다음 기간 계획',status='진행중',**extra))
    def transition(self,action,revision,cookie=None,**extra):
        self.cookie=cookie or self.worker
        return self.post('/api/performance/transition',self.payload(employee_id='worker',action=action,revision=revision,**extra))
    def view(self,mode='own'):
        self.cookie=self.manager if mode=='review' else self.worker
        return self.request('/api/performance?department_id='+self.a['id']+'&year=2026&date=2026-11-13&view='+mode)
    def test_plan_permissions_schema_validation_and_stale_revisions(self):
        self.cookie=self.worker;self.assertEqual(self.post('/api/performance/plan',self.payload(revision=0,definition=definition()))[0],403)
        bad=definition();bad['components'][0]['weight']='5';self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/plan',self.payload(revision=0,definition=bad))[0],400)
        self.configure();self.assertEqual(self.post('/api/performance/plan',self.payload(revision=0,definition=definition()))[0],409)
        self.cookie=self.worker;self.assertEqual(self.request('/api/performance?department_id='+self.b['id']+'&year=2026&date=2026-11-13')[0],403)
        self.assertEqual(self.view('review')[0],200)
        self.cookie=self.worker;self.assertEqual(self.request('/api/performance?department_id='+self.a['id']+'&year=2026&date=2026-11-13&view=review')[0],403)

    def test_submit_approve_return_and_snapshot_preservation(self):
        self.configure();status,data=self.save();self.assertEqual(status,200,data)
        self.assertEqual(data['record']['calculation']['percent'],'50.0000')
        self.assertEqual(self.transition('submit',1)[0],200);self.assertEqual(self.save(revision=2)[0],409)
        self.assertEqual(self.transition('approve',2)[0],403)
        self.assertEqual(self.transition('approve',2,self.manager)[0],200)
        changed=definition();changed['components'][0]['target']='20';self.configure(changed,1)
        item=self.view()[1]['items'][0];self.assertEqual(item['records'][0]['calculation']['percent'],'50.0000')
        self.assertFalse(self.view('review')[1]['items'][0]['aggregate']['ready'])
        self.assertEqual(self.transition('return',3,self.manager)[0],200)
        self.assertEqual(self.save(revision=4,rebase=True)[0],200)
        self.assertEqual(self.view()[1]['items'][0]['records'][0]['calculation']['percent'],'30.0000')

    def test_weekly_switch_and_legacy_data_are_not_overwritten(self):
        plan=definition();plan['schedule']=[{'effective':'2026-01-01','cadence':'monthly'},{'effective':'2026-11-01','cadence':'weekly'}]
        self.configure(plan);status,data=self.save();self.assertEqual(status,200,data)
        self.assertEqual(data['record']['start'],'2026-11-09')
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/actual',{**self.payload(),'month':11,'revision':0,'value':'7','reason':'중복 입력'})[0],409)
        changed=definition();self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/plan',self.payload(revision=1,definition=changed))[0],409)
        # A separate indicator with old monthly data must start the new workflow later.
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/actual',{**self.payload(indicator_id=self.ka[1]),'month':5,'revision':0,'value':'7','reason':'이전 실적'})[0],200)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/plan',self.payload(indicator_id=self.ka[1],revision=0,definition=definition()))[0],409)

    def test_period_lock_requires_all_assigned_people_and_blocks_evidence(self):
        self.configure();self.save();self.transition('submit',1);self.transition('approve',2,self.manager)
        self.cookie=self.manager
        payload=self.payload(revision=0,closed=True,reason='기간 취합 마감')
        self.assertEqual(self.post('/api/performance/lock',payload)[0],409)
        status,data=self.post('/api/performance/record',self.payload(employee_id='manager',revision=0,values={'count':'10'},narrative='',plan='',status='완료'));self.assertEqual(status,200,data)
        self.assertEqual(self.post('/api/performance/transition',self.payload(employee_id='manager',revision=1,action='submit'))[0],200)
        self.assertEqual(self.post('/api/performance/transition',self.payload(employee_id='manager',revision=2,action='approve'))[0],200)
        self.assertEqual(self.post('/api/performance/lock',payload)[0],200)
        self.assertEqual(self.transition('return',3,self.manager)[0],409)
        self.cookie=self.manager;self.assertEqual(self.post('/api/performance/lock',{**payload,'revision':1,'closed':False})[0],200)

    def test_password_status_handover_and_transfer_keep_history(self):
        self.configure();self.save()
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'worker','action':'reset-password','password':'replacement-test-password','reason':'계정 복구'})[0],200)
        self.cookie=self.worker;self.assertEqual(self.request('/api/me')[0],401)
        self.cookie='';self.assertEqual(self.request('/api/login',{'employee_id':'worker','password':'replacement-test-password'})[0],200);self.worker=self.cookie
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'worker','action':'handover','successor':'manager','indicators':[self.ka[0]],'reason':'담당자 인계'})[0],200)
        with app.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT definition FROM performance_plans').fetchone()[0])['representative'],'manager')
            self.assertEqual(db.execute('SELECT employee FROM performance_records').fetchone()[0],'worker')
            self.assertNotIn('replacement-test-password',json.dumps([dict(r) for r in db.execute('SELECT * FROM audit_log')]))
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'worker','action':'transfer','department_id':self.b['id'],'indicators':self.kb,'reason':'근무지 변경'})[0],200)
        self.cookie='';self.request('/api/login',{'employee_id':'worker','password':'replacement-test-password'});history=self.request('/api/performance/history')[1]
        self.assertEqual(history['records'][0]['department'],self.a['id'])
        self.cookie=self.super_cookie
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'worker','action':'status','status':'inactive','reason':'휴직 처리'})[0],200)
        self.cookie='';self.assertEqual(self.request('/api/login',{'employee_id':'worker','password':'replacement-test-password'})[0],403)

    def test_assigned_employee_evaluator_and_year_copy(self):
        self.configure(definition(mode='text'))
        self.cookie=self.manager
        payload=self.payload(revision=0,evaluator='worker',grade='',adjustment='0',excluded=False)
        self.assertEqual(self.post('/api/performance/rating',payload)[0],200)
        self.cookie=self.worker;self.assertEqual(len(self.request('/api/performance/assignments')[1]['tasks']),1)
        self.assertEqual(self.post('/api/performance/grade',self.payload(revision=1,grade='A'))[0],200)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/grade',self.payload(revision=2,grade='S'))[0],403)
        self.assertEqual(self.post('/api/performance/copy-year',self.payload(year=2027,source_year=2026))[0],200)
        self.assertEqual(self.post('/api/performance/copy-year',self.payload(year=2027,source_year=2026))[0],409)
        with app.connect() as db:
            new=json.loads(db.execute('SELECT definition FROM performance_plans WHERE year=2027').fetchone()[0]);self.assertEqual(new['schedule'][0]['effective'],'2027-01-01')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM performance_ratings WHERE year=2027').fetchone()[0],0)

    def test_annual_freeze_preserves_report_and_blocks_old_and_new_writes(self):
        with app.connect() as db:keys=[k['id'] for k in app.available(self.a['id'],db)]
        for key in keys:self.configure(config=definition(owner='worker' if key==self.ka[0] else ''),key=key)
        self.save(value='10');self.transition('submit',1);self.transition('approve',2,self.manager)
        self.cookie=self.manager
        for key in keys[1:]:self.assertEqual(self.post('/api/performance/rating',self.payload(indicator_id=key,revision=0,evaluator='manager',grade='',adjustment='0',excluded=True))[0],200)
        uri='/api/performance/annual?department_id='+self.a['id']+'&year=2026'
        report=self.request(uri)[1];self.assertTrue(report['ready']);self.assertEqual(report['score'],'20.0000')
        self.assertEqual(self.post('/api/performance/year',self.payload(revision=0,closed=True))[0],200)
        self.assertEqual(self.post('/api/performance/plan',self.payload(revision=1,definition=definition()))[0],409)
        self.assertEqual(self.post('/api/monthly/actual',{**self.payload(),'month':1,'revision':0,'value':'1','reason':'과거 변경'})[0],409)
        self.assertEqual(self.request(uri)[1]['score'],'20.0000')
        self.assertEqual(self.post('/api/performance/year',self.payload(revision=1,closed=False))[0],200)

    def test_structured_weekly_records_feed_the_main_dashboard(self):
        config=definition();config['schedule'][0]['cadence']='weekly';self.configure(config);self.save()
        self.cookie=self.worker
        status,data=self.request('/api/dashboard?year=2026&month=11');self.assertEqual(status,200,data)
        metric=next(r for r in data['metrics'] if r['indicator']['id']==self.ka[0])
        self.assertEqual(metric['source'],'periodic');self.assertEqual(metric['achievement_percent'],'50.0000');self.assertEqual(metric['workflow_state'],'draft')
        self.assertEqual(metric['evidence_count'],0);self.assertEqual(metric['history'][10]['percent'],'50.0000')

    def test_manager_cannot_take_over_another_manager_account(self):
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'manager','action':'reset-password','password':'takeover-test-password','reason':'관리자 초기화 시도'})[0],403)
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'manager','action':'password','old_password':self.password,'password':'replacement-manager-password','reason':'본인 비밀번호 변경'})[0],200)
        self.assertEqual(self.request('/api/me')[0],401)

    def test_manager_can_submit_own_physical_department_but_not_review_it(self):
        self.configure()
        # Keep management scope A while the manager personally moves to B.
        with app.connect() as db:
            db.execute("UPDATE users SET department_id=?,indicators=? WHERE employee_id='manager'",(self.b['id'],json.dumps(self.kb)))
            config=definition(owner='manager');db.execute('INSERT INTO performance_plans VALUES(?,?,?,?,1)',(self.b['id'],2026,self.kb[0],json.dumps(config)))
        self.cookie=self.manager
        uri='/api/performance?department_id='+self.b['id']+'&year=2026&date=2026-11-13'
        self.assertEqual(self.request(uri+'&view=own')[0],200)
        self.assertEqual(self.request(uri+'&view=review')[0],403)
        self.assertEqual(self.post('/api/performance/record',self.payload(department_id=self.b['id'],indicator_id=self.kb[0],employee_id='manager',revision=0,values={'count':'5'},narrative='',plan='',status='진행중'))[0],200)
        self.assertEqual(self.post('/api/performance/transition',self.payload(department_id=self.b['id'],indicator_id=self.kb[0],employee_id='manager',revision=1,action='submit'))[0],200)
        self.assertEqual(self.post('/api/performance/transition',self.payload(department_id=self.b['id'],indicator_id=self.kb[0],employee_id='manager',revision=2,action='approve'))[0],403)

    def test_preview_and_manual_backup_are_scoped(self):
        self.configure();self.cookie=self.worker
        result=self.post('/api/performance/preview',self.payload(values={'count':'5'}))
        self.assertEqual(result[0],200);self.assertEqual(result[1]['calculation']['score'],'10.0000')
        self.assertEqual(self.post('/api/performance/plan',self.payload(revision=1,definition=[]))[0],403)
        self.cookie=self.manager;self.assertEqual(self.post('/api/performance/plan',self.payload(revision=1,definition=[]))[0],400)
        self.assertEqual(self.post('/api/performance/backup',{'reason':'관리자 백업 시도'})[0],403)
        self.cookie=self.super_cookie;status,result=self.post('/api/performance/backup',{'reason':'수퍼관리자 백업 검증'});self.assertEqual(status,200,result)
        status,result=self.request('/api/performance/operations');self.assertEqual(status,200,result);self.assertEqual(len(result['backups']),1)
        self.assertEqual(self.request('/api/performance/overview?year=2026&date=2026-11-13')[0],200)
        self.cookie=self.worker;self.assertEqual(self.request('/api/performance/overview?year=2026&date=2026-11-13')[0],403)

    def test_handover_and_evaluator_proof_access_are_explicit(self):
        import test_entries
        self.configure();record=self.save()[1]['record']
        with app.connect() as db:
            worker=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            file=performance_files.upload(db,worker,record['id'],'private.pdf',b'%PDF-handover-proof',app.CATALOG,app.DB.parent/'uploads')['file']
        self.cookie='';self.register('successor');successor_cookie=self.cookie
        self.cookie='';self.register('unrelated');unrelated_cookie=self.cookie
        self.cookie=successor_cookie;self.assertEqual(test_entries.EntryFlow.binary(self,'/api/performance/files/'+str(file['id']))[0],403)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/account',{'employee_id':'worker','action':'handover','successor':'successor','indicators':[self.ka[0]],'reason':'명시적 담당자 인계'})[0],200)
        self.cookie='';self.request('/api/login',{'employee_id':'successor','password':self.password})
        self.assertEqual(test_entries.EntryFlow.binary(self,'/api/performance/files/'+str(file['id']))[0],200)
        self.cookie=unrelated_cookie;self.assertEqual(test_entries.EntryFlow.binary(self,'/api/performance/files/'+str(file['id']))[0],403)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/transition',self.payload(employee_id='worker',revision=1,action='submit_on_behalf'))[0],200)
        self.assertEqual(self.transition('approve',2,self.manager)[0],200)
        self.cookie=self.manager
        self.assertEqual(self.post('/api/performance/rating',self.payload(revision=0,evaluator='unrelated',grade='',adjustment='0',excluded=False))[0],200)
        self.cookie=unrelated_cookie;self.assertEqual(test_entries.EntryFlow.binary(self,'/api/performance/files/'+str(file['id']))[0],200)

    def test_evidence_and_verified_backup_restore(self):
        self.configure();record=self.save()[1]['record'];storage=app.DB.parent/'uploads'
        with app.connect() as db:
            user=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            file=performance_files.upload(db,user,record['id'],'proof.pdf',b'%PDF-test-proof',app.CATALOG,storage)['file']
        archive=app.DB.parent/'safe.zip';operations.backup(app.DB,archive)
        dest=app.DB.parent/'restored';result=operations.restore(archive,dest);self.assertTrue(result['verified'])
        import sqlite3
        with sqlite3.connect(dest/'database.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        self.assertEqual(len(list((dest/'uploads').iterdir())),1)
        with self.assertRaises(ValueError):operations.restore(archive,dest)
        # Traversal paths and corrupt content are rejected before creating a target.
        bad=app.DB.parent/'bad.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(bad,'w') as target:
            for info in source.infolist():target.writestr(info.filename,b'changed' if info.filename=='database.sqlite3' else source.read(info.filename))
        with self.assertRaises(ValueError):operations.restore(bad,app.DB.parent/'untrusted')
        self.assertFalse((app.DB.parent/'untrusted').exists())
        self.transition('submit',1)
        with app.connect() as db:
            user=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            with self.assertRaises(management.APIError):performance_files.upload(db,user,record['id'],'later.pdf',b'late',app.CATALOG,storage)


if __name__=='__main__':unittest.main()
