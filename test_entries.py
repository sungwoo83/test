import hashlib
import http.client
import json
import unittest
from urllib.parse import quote
import app
import evidence
import test_app
import test_management

class EntryFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post

    def configure(self,**values):
        self.cookie=self.manager
        payload={**self.context,'indicator_id':self.ka[0],'revision':0,'target':'10','weight':'5','unit':'건','entry_mode':'numeric','direction':'higher','reason':'목표 기준 설정',**values}
        return self.post('/api/monthly/settings',payload)
    def save(self,**values):
        self.cookie=self.worker
        return self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'5','status':'진행중','future_plan':'다음 달 현장 점검','narrative':'추진 내역','reason':'실적입력',**values})
    def monthly(self,month=5):
        self.cookie=self.worker
        status,result=self.request('/api/monthly?department_id='+self.a['id']+'&year=2026&month='+str(month))
        self.assertEqual(status,200)
        return next(i for i in result['items'] if i['indicator']['id']==self.ka[0])
    def binary(self,path,body=None,filename='증빙자료.pdf',csrf=None,declared_size=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        headers={'Cookie':self.cookie}
        if body is not None:
            if csrf is None:csrf=self.request('/api/me')[1]['csrf']
            headers.update({'X-Requested-With':'PerformanceManager','X-CSRF-Token':csrf,'X-File-Name':quote(filename),'Content-Type':'application/octet-stream'})
            if declared_size is not None:headers['Content-Length']=str(declared_size)
        conn.request('POST' if body is not None else 'GET',path,body,headers)
        response=conn.getresponse();data=response.read();status=response.status;out_headers=dict(response.getheaders());conn.close()
        return status,data,out_headers

    def test_goal_weight_permissions_and_revision(self):
        self.assertEqual(self.configure()[0],200)
        self.assertEqual(self.configure()[0],409)
        self.assertEqual(self.configure(department_id=self.b['id'],indicator_id=self.kb[0])[0],403)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/monthly/settings',{**self.context,'indicator_id':self.ka[0],'revision':1,'target':'20','reason':'권한 확인'})[0],403)
        item=self.monthly();self.assertEqual(item['settings']['weight'],'5')
        self.assertEqual(item['settings']['target'],'10')

    def test_numeric_progress_plan_and_rate(self):
        self.configure()
        self.assertEqual(self.save(status='진행중')[0],200)
        item=self.monthly();record=item['record']
        self.assertEqual(record['status'],'진행중')
        self.assertEqual(record['future_plan'],'다음 달 현장 점검')
        self.assertEqual(record['achievement']['percent'],'50.00')
        self.assertEqual(self.save(revision=1,status='완료',value='')[0],400)
        self.assertEqual(self.save(revision=1,status='완료',value='10')[0],200)
        self.assertEqual(self.monthly()['record']['achievement']['percent'],'100.00')
        self.assertEqual(self.save(revision=2,status='임의상태')[0],400)

    def test_percent_units_lower_direction_and_zero_target(self):
        self.configure(target='20',unit='%')
        self.assertEqual(self.save(value='10')[0],200)
        self.assertEqual(self.monthly()['record']['achievement']['percent'],'50.00')
        self.assertEqual(self.configure(revision=1,target='5',direction='lower')[0],200)
        self.assertEqual(self.monthly()['record']['achievement']['percent'],'50.00')
        self.assertEqual(self.configure(revision=2,target='0')[0],200)
        rate=self.monthly()['record']['achievement']
        self.assertIsNone(rate['percent']);self.assertIn('0 이하',rate['label'])

    def test_closed_snapshots_preserved_open_months_updated(self):
        self.configure()
        self.save()
        self.cookie=self.manager
        self.assertEqual(self.post('/api/monthly/period',{**self.context,'revision':0,'status':'closed','reason':'월마감'})[0],200)
        self.assertEqual(self.save(month=6,value='6')[0],200)
        self.assertEqual(self.configure(month=6,revision=1,target='20')[0],200)
        self.assertEqual(self.monthly(5)['record']['achievement']['percent'],'50.00')
        self.assertEqual(self.monthly(6)['record']['achievement']['percent'],'30.00')
        self.assertEqual(self.configure(revision=2,target='30')[0],409)
        self.assertEqual(self.save(month=6,revision=1,value='7')[0],409)

    def test_textual_entry_and_mode_change_restriction(self):
        self.assertEqual(self.configure(entry_mode='text',target='')[0],200)
        self.assertEqual(self.save(status='미착수',value='',narrative='')[0],200)
        self.assertEqual(self.save(revision=1,status='완료',narrative='')[0],400)
        self.assertEqual(self.save(revision=1,status='완료',narrative='종합 실적 보고 완료')[0],200)
        item=self.monthly();self.assertIsNone(item['record']['achievement']['percent'])
        self.assertEqual(item['record']['narrative'],'종합 실적 보고 완료')
        self.assertEqual(self.configure(revision=1,entry_mode='numeric')[0],409)

    def test_empty_unstarted_entry_allows_evidence(self):
        self.configure()
        status,response=self.save(value='',status='미착수',narrative='')
        self.assertEqual(status,200)
        self.assertIsNone(self.monthly()['record']['achievement']['percent'])
        record_id=response['record_id']
        self.cookie=self.worker
        self.assertEqual(self.binary('/api/evidence/upload?record_id='+str(record_id),b'%PDF-1.4\nexample')[0],200)

    def test_evidence_upload_download_scope_and_soft_delete(self):
        self.configure();_,saved=self.save();record_id=saved['record_id']
        body=b'%PDF-1.4\nexample file'
        status,raw,_=self.binary('/api/evidence/upload?record_id='+str(record_id),body)
        self.assertEqual(status,200);file=json.loads(raw)['file'];path='/api/evidence/'+str(file['id'])
        self.assertEqual(file['sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(len(self.monthly()['record']['attachments']),1)
        status,data,headers=self.binary(path);self.assertEqual(status,200);self.assertEqual(data,body)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['Content-Type'],'application/octet-stream')
        self.cookie='';self.assertEqual(self.binary(path)[0],401)
        self.request('/api/register',{'employee_id':'outside','password':self.password,'department_id':self.b['id'],'indicators':self.kb})
        self.assertEqual(self.binary(path)[0],403)
        self.cookie=self.worker
        self.assertEqual(self.post('/api/evidence/delete',{'file_id':file['id']})[0],200)
        self.assertEqual(self.binary(path)[0],404)
        with app.connect() as db:
            row=db.execute('SELECT * FROM evidence_files WHERE id=?',(file['id'],)).fetchone()
            self.assertIsNotNone(row['deleted_at'])
            self.assertTrue((app.DB.parent/'uploads'/row['storage_name']).is_file())

    def test_evidence_limits_csrf_and_month_closing(self):
        self.configure();_,saved=self.save();url='/api/evidence/upload?record_id='+str(saved['record_id'])
        self.assertEqual(self.binary(url,b'data',csrf='wrong')[0],403)
        self.assertEqual(self.binary(url,b'data',filename='../private.pdf')[0],400)
        self.assertEqual(self.binary(url,b'data',filename='run.exe')[0],400)
        self.assertEqual(self.binary(url,b'',declared_size=evidence.MAX_SIZE+1)[0],413)
        for i in range(10):self.assertEqual(self.binary(url,b'file',filename=f'evidence{i}.pdf')[0],200)
        self.assertEqual(self.binary(url,b'file')[0],400)
        file_id=self.monthly()['record']['attachments'][0]['id']
        self.cookie=self.manager
        self.post('/api/monthly/period',{**self.context,'revision':0,'status':'closed','reason':'월마감'})
        self.cookie=self.worker
        self.assertEqual(self.binary(url,b'file')[0],409)
        self.assertEqual(self.post('/api/evidence/delete',{'file_id':file_id})[0],409)
        self.assertEqual(self.binary('/api/evidence/'+str(file_id))[0],200)

    def test_legacy_numerical_records_keep_value_and_definition(self):
        old={'id':self.ka[0],'name':'이전 서식 이름','type':'비계량','target':'10','points':'3'}
        with app.connect() as db:
            db.execute('INSERT INTO monthly_actuals(department_id,indicator_id,year,month,actual,snapshot,revision,updated_by,updated_at) VALUES(?,?,?,?,?,?,1,?,?)',
                (self.a['id'],self.ka[0],2026,5,'7',json.dumps(old),'worker',app.now()))
        app.initialize()
        record=self.monthly()['record'];definition=json.loads(record['snapshot'])
        self.assertEqual(record['actual'],'7')
        self.assertEqual(definition['entry_mode'],'numeric')
        self.assertEqual(definition['name'],'이전 서식 이름')
        self.assertEqual(record['achievement']['percent'],'70.00')

if __name__=='__main__':unittest.main(verbosity=2)
