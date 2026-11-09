import http.client
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import app
import test_app
import test_management
import test_entries

class ReviewFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post
    binary=test_entries.EntryFlow.binary

    def add_worker(self,employee='000007',department=None,indicators=None):
        self.cookie=''
        self.assertEqual(self.request('/api/register',{'employee_id':employee,'display_name':'테스트 직원 '+employee,'password':self.password,'department_id':department or self.a['id'],'indicators':indicators or self.ka})[0],200)
        return self.cookie
    def save(self,employee='worker',value='5',revision=0,**extra):
        return self.post('/api/monthly/actual',{**self.context,'employee_id':employee,'indicator_id':self.ka[0],'revision':revision,'value':value,'status':'진행중','future_plan':'다음 달 계획','reason':'월별 실적 입력',**extra})
    def report(self,department=None):
        query='year=2026&month=5'+('&department_id='+department if department else '')
        return self.request('/api/review?'+query)

    def test_employee_entries_remain_independent_and_manager_edits_owner(self):
        self.cookie=self.worker;self.assertEqual(self.save()[0],200)
        second=self.add_worker();self.assertEqual(self.save(employee='000007',value='8')[0],200)
        self.cookie=self.manager
        status,report=self.report();self.assertEqual(status,200)
        records={r['employee_id']:r['record'] for r in report['items'] if r['indicator']['id']==self.ka[0]}
        self.assertEqual(records['worker']['actual'],'5');self.assertEqual(records['000007']['actual'],'8')
        self.assertEqual(self.save(value='9',revision=1,status='완료',future_plan='관리자 수정 계획')[0],200)
        with app.connect() as db:
            row=db.execute("SELECT * FROM monthly_actuals WHERE employee_id='worker'").fetchone()
            self.assertEqual(row['updated_by'],'manager');self.assertEqual(row['employee_id'],'worker')
        self.cookie=self.worker
        result=self.request('/api/monthly?department_id='+self.a['id']+'&year=2026&month=5')[1]
        self.assertEqual(result['items'][0]['record']['actual'],'9')
        self.assertEqual(self.save(employee='000007',value='99',revision=1)[0],403)

    def test_review_scope_and_superadmin_all_workplaces(self):
        self.cookie=self.worker;self.save()
        self.assertEqual(self.report()[0],403)
        self.cookie=self.manager
        self.assertEqual(self.report(self.b['id'])[0],403)
        report=self.report()[1];self.assertEqual([p['department_id'] for p in report['workplaces']],[self.a['id']])
        self.cookie=self.super_cookie
        all_report=self.report()[1];ids={p['department_id'] for p in all_report['workplaces']}
        self.assertIn(self.a['id'],ids);self.assertIn(self.b['id'],ids)
        filtered=self.report(self.a['id'])[1];self.assertEqual(len(filtered['workplaces']),1)

    def test_input_presence_drafts_and_unassigned_staff(self):
        self.cookie=self.worker;self.save(value='',status='미착수')
        self.cookie=self.manager
        report=self.report()[1];worker=next(e for e in report['employees'] if e['employee_id']=='worker')
        self.assertEqual(worker['draft_count'],1);self.assertEqual(worker['entered_count'],0);self.assertEqual(worker['missing_count'],1)
        self.save(value='0',revision=1)
        worker=next(e for e in self.report()[1]['employees'] if e['employee_id']=='worker')
        self.assertEqual(worker['entered_count'],1);self.assertEqual(worker['completion_percent'],'50.00')

    def test_manager_close_reopen_and_wrong_department_blocked(self):
        self.cookie=self.worker;self.save()
        self.cookie=self.manager
        close={**self.context,'revision':0,'status':'closed','reason':'월 취합 마감'}
        self.assertEqual(self.post('/api/monthly/period',close)[0],200)
        self.assertEqual(self.save(revision=1)[0],409)
        self.assertEqual(self.post('/api/monthly/period',{**close,'department_id':self.b['id'],'status':'open'})[0],403)
        self.assertEqual(self.post('/api/monthly/period',{**close,'revision':1,'status':'open','reason':'취합 재확인'})[0],200)
        self.assertEqual(self.save(revision=1)[0],200)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/period',{**close,'revision':2})[0],403)

    def test_same_indicator_does_not_share_employee_evidence(self):
        self.cookie=self.worker;_,record=self.save()
        status,body,_=self.binary('/api/evidence/upload?record_id='+str(record['record_id']),b'%PDF-1.4\nproof')
        self.assertEqual(status,200);file_id=json.loads(body)['file']['id']
        self.add_worker()
        self.assertEqual(self.binary('/api/evidence/'+str(file_id))[0],403)
        self.cookie=self.manager
        self.assertEqual(self.binary('/api/evidence/'+str(file_id))[0],200)

    def test_xlsx_is_valid_filtered_and_safe_text(self):
        self.cookie=self.manager
        self.post('/api/monthly/settings',{**self.context,'indicator_id':self.ka[0],'revision':0,'target':'10','weight':'5','unit':'건','entry_mode':'numeric','direction':'higher','reason':'목표 설정'})
        self.cookie=self.worker;self.save(future_plan='=HYPERLINK("https://example.invalid","text")')
        self.add_worker()
        self.cookie=self.manager
        status,body,headers=self.binary('/api/review.xlsx?year=2026&month=5')
        self.assertEqual(status,200);self.assertIn('spreadsheetml.sheet',headers['Content-Type'])
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            workbook=ET.fromstring(z.read('xl/workbook.xml'))
            self.assertEqual([s.attrib['name'] for s in workbook.findall('s:sheets/s:sheet',ns)],['근무지현황','직원입력현황','지표실적'])
            people=ET.fromstring(z.read('xl/worksheets/sheet2.xml'))
            self.assertIn('000007',[t.text for t in people.findall('.//s:t',ns)])
            details=ET.fromstring(z.read('xl/worksheets/sheet3.xml'))
            self.assertFalse(details.findall('.//s:f',ns))
            self.assertTrue(any((t.text or '').startswith('=HYPERLINK') for t in details.findall('.//s:t',ns)))
            percentages=[v.text for c in details.findall('.//s:c',ns) if c.attrib.get('s')=='2' for v in c.findall('s:v',ns)]
            self.assertIn('0.50',percentages)
        self.assertEqual(self.binary('/api/review.xlsx?year=2026&month=5&department_id='+self.b['id'])[0],403)
        self.cookie=self.worker;self.assertEqual(self.binary('/api/review.xlsx?year=2026&month=5')[0],403)

    def test_legacy_migration_preserves_evidence_and_original_actor(self):
        original=app.DB
        try:
            with tempfile.TemporaryDirectory() as folder:
                app.DB=Path(folder)/'legacy.sqlite3'
                with sqlite3.connect(app.DB) as db:
                    db.executescript('''
                    CREATE TABLE monthly_actuals(id INTEGER PRIMARY KEY,department_id TEXT NOT NULL,indicator_id TEXT NOT NULL,year INTEGER NOT NULL,month INTEGER NOT NULL,actual TEXT NOT NULL,adjusted TEXT,adjustment_reason TEXT,snapshot TEXT NOT NULL,revision INTEGER NOT NULL,updated_by TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(department_id,indicator_id,year,month));
                    CREATE TABLE audit_log(id INTEGER PRIMARY KEY,action TEXT NOT NULL,actor TEXT NOT NULL,department_id TEXT,target TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
                    CREATE TABLE evidence_files(id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES monthly_actuals(id),storage_name TEXT NOT NULL UNIQUE,original_name TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL,deleted_at TEXT);
                    ''')
                    db.execute('INSERT INTO monthly_actuals VALUES(42,?,?,?,?,?,NULL,NULL,?,2,?,?)',(self.a['id'],self.ka[0],2026,5,'7',json.dumps({'id':self.ka[0],'name':'기존 실적','type':'계량'}),'manager',app.now()))
                    db.execute('INSERT INTO audit_log VALUES(1,?,?,?,?,NULL,?,?,?)',('실적 입력','worker',self.a['id'],'42','{}','최초 등록',app.now()))
                    db.execute('INSERT INTO evidence_files VALUES(9,42,?,?,?,?,?,?,NULL)',('stored-file','기존증빙.pdf',3,'abc','worker',app.now()))
                app.initialize()
                with app.connect() as db:
                    self.assertEqual(db.execute('SELECT employee_id FROM monthly_actuals WHERE id=42').fetchone()[0],'worker')
                    self.assertEqual(db.execute('SELECT record_id FROM evidence_files WHERE id=9').fetchone()[0],42)
                    self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])
                    self.assertTrue(list((app.DB.parent/'backups').glob('*.sqlite3')))
        finally:app.DB=original
