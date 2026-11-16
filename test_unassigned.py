"""Signup is independent of assignment; empty accounts can sign in and select later."""
import json
import unittest
import app
import test_app

class UnassignedFlow(unittest.TestCase):
    setUp=test_app.ProfileFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request

    def signup(self,**extra):
        return self.request('/api/register',{'employee_id':'new-user','display_name':'신규 직원','password':self.password,'department_id':self.a['id'],**extra})
    def test_signup_never_assigns_indicators_and_login_needs_only_credentials(self):
        self.assertEqual(self.signup(indicators=self.ka,assignment_confirmed=1,role='superadmin')[0],200)
        me=self.request('/api/me')[1];self.assertEqual(me['user']['indicators'],[]);self.assertEqual(me['user']['assignment_confirmed'],0);self.assertEqual(me['user']['role'],'employee')
        self.assertEqual(self.request('/api/logout',{'csrf':me['csrf']})[0],200)
        self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':self.password})[0],200)
        self.assertEqual(self.request('/api/me')[1]['user']['indicators'],[])
        self.assertEqual(self.request('/api/dashboard?year=2026&month=5')[1]['summary']['indicator_count'],0)
        self.assertEqual(self.request('/api/performance?view=own&year=2026&date=2026-05-10&department_id='+self.a['id'])[1]['items'],[])
        self.assertEqual(self.request('/api/monthly?year=2026&month=5&department_id='+self.a['id'])[1]['items'],[])
    def test_employee_selects_after_login_and_waits_for_manager(self):
        self.assertEqual(self.signup()[0],200);me=self.request('/api/me')[1]
        self.assertEqual(self.request('/api/profile',{'csrf':me['csrf'],'revision':me['user']['revision'],'department_id':self.a['id'],'indicators':self.ka})[0],200)
        me=self.request('/api/me')[1];self.assertEqual(me['user']['indicators'],sorted(self.ka));self.assertFalse(me['user']['assignment_confirmed'])
        self.assertEqual(self.request('/api/monthly/actual',{'csrf':me['csrf'],'department_id':self.a['id'],'year':2026,'month':5,'indicator_id':self.ka[0],'value':'5','status':'진행중','revision':0,'reason':'배정 대기 입력 시도'})[0],403)
        self.assertEqual(self.request('/api/logout',{'csrf':me['csrf']})[0],200)
        self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':self.password})[0],200)
    def test_known_workplace_with_no_catalog_indicators_can_signup(self):
        original=app.CATALOG
        try:
            app.CATALOG={**original,'departments':original['departments']+[{'id':'EMPTY-OFFICE','name':'미설정 근무지','group':'시험','kind':'본사'}]}
            self.assertEqual(self.signup(department_id='EMPTY-OFFICE')[0],200)
            me=self.request('/api/me')[1];self.assertEqual(me['user']['indicators'],[])
            self.request('/api/logout',{'csrf':me['csrf']})
            self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':self.password})[0],200)
            self.assertEqual(self.request('/api/profile',{'csrf':self.request('/api/me')[1]['csrf'],'revision':1,'department_id':'EMPTY-OFFICE'})[0],200)
        finally:app.CATALOG=original
    def test_blank_legacy_assignments_are_normalized_without_changing_credentials(self):
        self.assertEqual(self.signup()[0],200)
        for value in ('','null','""'):
            with app.connect() as db:db.execute("UPDATE users SET indicators=? WHERE employee_id='new-user'",(value,))
            app.initialize();self.cookie=''
            self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':self.password})[0],200)
            self.assertEqual(self.request('/api/me')[1]['user']['indicators'],[])
            self.assertEqual(self.request('/api/dashboard?year=2026&month=5')[0],200)
    def test_empty_assignment_does_not_bypass_authentication_or_inactive_status(self):
        self.assertEqual(self.signup()[0],200);self.cookie=''
        self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':'incorrect'})[0],401)
        with app.connect() as db:db.execute("UPDATE users SET account_status='inactive' WHERE employee_id='new-user'")
        self.assertEqual(self.request('/api/login',{'employee_id':'new-user','password':self.password})[0],403)
