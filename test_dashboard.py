import json
import unittest
from datetime import date
from decimal import Decimal
import app
import dashboard
import management
import test_app
import test_management


class DashboardCriteria(unittest.TestCase):
    def test_calendar_and_late_schedule(self):
        self.assertEqual(dashboard.expected(date(2026,11,30),2026),100)
        self.assertEqual(dashboard.expected(date(2026,12,31),2026),100)
        self.assertEqual(dashboard.expected(date(2025,12,31),2026),0)
        self.assertEqual(dashboard.month_end(2028,2),date(2028,2,29))
        self.assertEqual(dashboard.expected(date(2026,4,30),2026,'late'),0)
        self.assertEqual(dashboard.expected(date(2026,10,31),2026,'late'),65)
        self.assertEqual(dashboard.expected(date(2026,11,30),2026,'late'),100)

    def test_thresholds_and_deadline(self):
        config={'entry_mode':'numeric','target':'100','dashboard_curve':'linear'}
        day=date(2026,7,1);pace=dashboard.expected(day,2026)
        code=lambda value,when=day:dashboard.health(config,value,'진행중',True,when,2026)['code']
        self.assertEqual(code(pace),'good')
        self.assertEqual(code(pace*Decimal('.8')),'warning')
        self.assertEqual(code(pace*Decimal('.79')),'risk')
        self.assertEqual(code('99.99',date(2026,11,30)),'risk')
        self.assertEqual(code('100',date(2026,11,30)),'good')
        self.assertEqual(dashboard.health(config,None,None,False,date(2026,11,30),2026)['code'],'risk')
        self.assertEqual(dashboard.health({**config,'target':None},None,None,False,day,2026)['code'],'pending')
        self.assertEqual(dashboard.health({**config,'dashboard_curve':'late'},None,None,False,date(2026,3,31),2026)['code'],'good')

    def test_text_has_status_based_health(self):
        config={'entry_mode':'text'}
        for status,when,want in [('진행중',date(2026,6,30),'good'),('미착수',date(2026,10,31),'risk'),('진행중',date(2026,11,9),'warning'),('진행중',date(2026,11,30),'risk'),('완료',date(2026,11,30),'good')]:
            self.assertEqual(dashboard.health(config,None,status,True,when,2026)['code'],want)


class DashboardFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post

    def configure(self,**values):
        self.cookie=self.manager
        status,result=self.post('/api/monthly/settings',{**self.context,'indicator_id':self.ka[0],'revision':0,'target':'100','weight':'5','unit':'건','entry_mode':'numeric','direction':'higher','reason':'대시보드 기준 설정',**values})
        self.assertEqual(status,200,result)

    def save(self,**values):
        self.cookie=self.worker
        status,result=self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'50','status':'진행중','future_plan':'다음 달 보완 예정','reason':'월간 실적',**values})
        self.assertEqual(status,200,result)

    def report(self,month=6,today=date(2026,11,9)):
        with app.connect() as db:
            user=db.execute("SELECT * FROM users WHERE employee_id='worker'").fetchone()
            return dashboard.report(db,user,{'year':2026,'month':month},management.effective_catalog(db,app.CATALOG),app.available,today=today)

    def metric(self,report):return next(x for x in report['metrics'] if x['indicator']['id']==self.ka[0])

    def test_api_authentication_and_own_scope(self):
        self.cookie='';self.assertEqual(self.request('/api/dashboard?year=2026&month=5')[0],401)
        for cookie in [self.worker,self.manager,self.super_cookie]:
            self.cookie=cookie
            self.assertEqual(self.request('/api/dashboard?year=2026&month=5')[0],200)
            self.assertEqual(self.request('/api/dashboard?year=2026&month=5&employee_id=someone-else')[0],403)
            self.assertEqual(self.request('/api/dashboard?year=2026&month=5&department_id='+self.b['id'])[0],403)
        self.assertEqual(self.request('/api/dashboard?year=2026&month=12')[0],400)

    def test_latest_value_not_sum_and_missing_month(self):
        self.configure();self.save(month=4,value='20');self.save(month=5,value='50')
        item=self.metric(self.report())
        self.assertEqual(item['actual'],'50');self.assertEqual(item['source_month'],5)
        self.assertEqual(item['achievement_percent'],'50.00');self.assertEqual(item['input_state'],'미입력')
        self.assertEqual(item['future_plan'],'다음 달 보완 예정')
        self.assertEqual(item['history'][3]['percent'],'20.00')
        self.assertFalse(item['history'][5]['present'])
        self.assertEqual(self.metric(self.report(month=4))['actual'],'20')

    def test_closed_history_goal_and_adjusted_value(self):
        self.configure();self.save(value='50')
        self.cookie=self.manager
        self.assertEqual(self.post('/api/monthly/adjust',{**self.context,'indicator_id':self.ka[0],'employee_id':'worker','revision':1,'value':'60','reason':'증빙 검토'})[0],200)
        self.assertEqual(self.post('/api/monthly/period',{**self.context,'status':'closed','revision':0,'reason':'5월 마감'})[0],200)
        self.configure(month=6,revision=1,target='200',dashboard_curve='late')
        item=self.metric(self.report())
        self.assertEqual(item['actual'],'60');self.assertEqual(item['achievement_percent'],'30.00')
        self.assertEqual(item['history'][4]['percent'],'60.00')
        self.assertEqual(self.metric(self.report(month=5))['settings']['target'],'100')
        with app.connect() as db:
            before=[tuple(x) for x in db.execute('SELECT * FROM monthly_actuals')]
        self.report()
        with app.connect() as db:self.assertEqual(before,[tuple(x) for x in db.execute('SELECT * FROM monthly_actuals')])

    def test_missing_goal_and_text_are_not_fake_percentages(self):
        self.configure(target=None);self.save()
        item=self.metric(self.report());self.assertIsNone(item['achievement_percent'])
        self.assertEqual(item['health']['code'],'pending');self.assertEqual(item['graph_mode'],'actual')
        self.cookie=self.manager
        self.configure(indicator_id=self.ka[1],entry_mode='text',target=None)
        self.save(indicator_id=self.ka[1],value='',status='완료',narrative='성과 보고서 제출')
        item=next(x for x in self.report()['metrics'] if x['indicator']['id']==self.ka[1])
        self.assertEqual(item['graph_mode'],'status');self.assertIsNone(item['achievement_percent'])
        self.assertEqual(item['health']['code'],'good')

    def test_curve_preserved_when_existing_edit_omits_field(self):
        self.configure(dashboard_curve='late');self.configure(revision=1,target='200')
        self.assertEqual(self.metric(self.report())['settings']['dashboard_curve'],'late')
        self.cookie=self.manager
        status,_=self.post('/api/monthly/settings',{**self.context,'indicator_id':self.ka[0],'revision':2,'dashboard_curve':'invalid','reason':'잘못된 계획'})
        self.assertEqual(status,400)
        self.assertEqual(self.metric(self.report())['settings']['dashboard_curve'],'late')


if __name__=='__main__':unittest.main()
