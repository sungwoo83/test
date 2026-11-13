import io
import json
import unittest
import zipfile
from xml.etree import ElementTree as ET
import app
import management
import progress
import test_app
import test_management
import test_entries

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def change_cells(body,changes):
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(body)) as archive,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for info in archive.infolist():
            content=archive.read(info.filename)
            if info.filename in changes:
                root=ET.fromstring(content)
                for ref,value in changes[info.filename].items():
                    cell=root.find(f'.//s:c[@r="{ref}"]',NS)
                    for child in list(cell):cell.remove(child)
                    if isinstance(value,tuple) and value[0]=='formula':
                        cell.attrib.pop('t',None);ET.SubElement(cell,'{'+NS['s']+'}f').text=value[1]
                    elif isinstance(value,tuple) and value[0]=='percent':
                        cell.attrib.pop('t',None);cell.set('s','2');ET.SubElement(cell,'{'+NS['s']+'}v').text=value[1]
                    else:
                        cell.set('t','inlineStr');inline=ET.SubElement(cell,'{'+NS['s']+'}is');ET.SubElement(inline,'{'+NS['s']+'}t').text=str(value)
                content=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            target.writestr(info.filename,content)
    return output.getvalue()

class BulkFlow(unittest.TestCase):
    setUp=test_management.ManagementFlow.setUp
    tearDown=test_app.ProfileFlow.tearDown
    request=test_app.ProfileFlow.request
    register=test_app.ProfileFlow.register
    post=test_management.ManagementFlow.post
    binary=test_entries.EntryFlow.binary
    def template(self):
        self.cookie=self.super_cookie
        status,body,_=self.binary('/api/admin/indicators/template.xlsx?year=2026');self.assertEqual(status,200);return body
    def preview(self,body,csrf=None):
        status,raw,_=self.binary('/api/admin/indicators/import-preview?year=2026',body,filename='indicators.xlsx',csrf=csrf)
        return status,json.loads(raw)
    def apply(self,token):return self.post('/api/admin/indicators/import-apply',{'token':token,'reason':'전체 지표 기준 수정'})

    def test_unchanged_template_and_access_control(self):
        body=self.template();status,preview=self.preview(body);self.assertEqual(status,200)
        self.assertEqual(preview['master_count'],0);self.assertEqual(preview['target_count'],0)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM indicator_definitions').fetchone()[0],0)
        self.cookie=self.manager
        self.assertEqual(self.binary('/api/admin/indicators/template.xlsx?year=2026')[0],403)
        self.assertEqual(self.preview(body)[0],403)
        self.cookie=self.worker;self.assertEqual(self.apply(preview['token'])[0],403)

    def test_master_and_department_preview_then_atomic_apply(self):
        with app.connect() as db:
            before_settings=[tuple(row) for row in db.execute('SELECT * FROM department_indicator_settings ORDER BY department_id,indicator_id,year')]
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'일괄 수정 지표','I2':'20','J2':'5'},'xl/worksheets/sheet2.xml':{'G2':'20','H2':'5'}})
        status,preview=self.preview(body);self.assertEqual(status,200,preview)
        self.assertEqual(preview['master_count'],1);self.assertEqual(preview['target_count'],1)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM indicator_definitions').fetchone()[0],0)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM department_indicator_settings ORDER BY department_id,indicator_id,year')],before_settings)
        self.assertEqual(self.apply(preview['token'])[0],200)
        with app.connect() as db:
            catalog=management.effective_catalog(db,app.CATALOG)
            first=next(k for k in catalog['indicators'] if k['id']==self.ka[0]);self.assertEqual(first['name'],'일괄 수정 지표')
            self.assertEqual(progress.settings(db,self.a['id'],2026,first)['target'],'20')
        self.assertEqual(self.apply(preview['token'])[0],409)

    def test_validation_errors_do_not_apply_any_rows(self):
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'아직 반영되면 안 되는 이름','J3':'-1'}})
        self.assertEqual(self.preview(body)[0],400)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM indicator_definitions').fetchone()[0],0)
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'A2':'UNKNOWN'}})
        self.assertEqual(self.preview(body)[0],400)

    def test_reject_formulas_and_bad_workbooks_csrf(self):
        original=self.template()
        formula=change_cells(original,{'xl/worksheets/sheet1.xml':{'I2':('formula','1+1')}})
        self.assertEqual(self.preview(formula)[0],400)
        self.assertEqual(self.preview(b'not a workbook')[0],400)
        self.assertEqual(self.preview(original,csrf='invalid')[0],403)

    def test_revision_conflict_prevents_import(self):
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'업로드한 이름'}})
        status,preview=self.preview(body);self.assertEqual(status,200)
        self.assertEqual(self.post('/api/admin/indicator',{'indicator_id':self.ka[0],'revision':0,'name':'다른 사용자가 먼저 수정','reason':'개별 지표 수정'})[0],200)
        self.assertEqual(self.apply(preview['token'])[0],409)
        with app.connect() as db:self.assertEqual(management.effective_catalog(db,app.CATALOG)['indicators'][0]['name'],'다른 사용자가 먼저 수정')

    def test_preview_token_bound_to_login_session(self):
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'세션 확인 이름'}})
        _,preview=self.preview(body)
        me=self.request('/api/me')[1];self.request('/api/logout',{'csrf':me['csrf']})
        self.request('/api/login',{'employee_id':'super','password':self.password})
        self.assertEqual(self.apply(preview['token'])[0],409)

    def test_closed_month_kept_and_percent_cells_supported(self):
        self.cookie=self.super_cookie
        self.post('/api/monthly/settings',{**self.context,'indicator_id':self.ka[0],'revision':0,'target':'10','weight':'5','unit':'%','entry_mode':'numeric','direction':'higher','reason':'목표 등록'})
        self.cookie=self.worker;self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'5','reason':'실적 등록'})
        self.cookie=self.manager;self.post('/api/monthly/period',{**self.context,'status':'closed','revision':0,'reason':'월마감'})
        body=change_cells(self.template(),{'xl/worksheets/sheet2.xml':{'G2':('percent','0.2')}})
        status,preview=self.preview(body);self.assertEqual(status,200,preview)
        self.assertEqual(self.apply(preview['token'])[0],200)
        with app.connect() as db:
            row=db.execute('SELECT * FROM monthly_actuals').fetchone();self.assertEqual(json.loads(row['snapshot'])['target'],'10')
            first=app.available(self.a['id'],db)[0];self.assertEqual(progress.settings(db,self.a['id'],2026,first)['target'],'20.0')

    def test_type_change_validation_rolls_back_master_edits(self):
        self.cookie=self.worker;self.post('/api/monthly/actual',{**self.context,'indicator_id':self.ka[0],'revision':0,'value':'3','reason':'실적 등록'})
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'반영 금지 이름'},'xl/worksheets/sheet2.xml':{'J2':'서술형'}})
        self.assertEqual(self.preview(body)[0],409)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM indicator_definitions').fetchone()[0],0)

    def test_shared_string_cells_from_excel_are_supported(self):
        body=self.template();output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(body)) as archive,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
            for info in archive.infolist():
                content=archive.read(info.filename)
                if info.filename=='xl/worksheets/sheet1.xml':
                    root=ET.fromstring(content);cell=root.find('.//s:c[@r="G2"]',NS)
                    for child in list(cell):cell.remove(child)
                    cell.set('t','s');ET.SubElement(cell,'{'+NS['s']+'}v').text='0';content=ET.tostring(root,encoding='utf-8')
                target.writestr(info.filename,content)
            target.writestr('xl/sharedStrings.xml','<sst xmlns="'+NS['s']+'" count="1" uniqueCount="1"><si><t>Excel 공유문자열 지표</t></si></sst>')
        status,preview=self.preview(output.getvalue());self.assertEqual(status,200,preview)
        self.assertEqual(self.apply(preview['token'])[0],200)
        with app.connect() as db:self.assertEqual(management.effective_catalog(db,app.CATALOG)['indicators'][0]['name'],'Excel 공유문자열 지표')

    def test_month_closing_after_preview_requires_revalidation(self):
        body=change_cells(self.template(),{'xl/worksheets/sheet1.xml':{'G2':'재검증 필요 이름'}})
        _,preview=self.preview(body)
        self.cookie=self.manager
        self.post('/api/monthly/period',{**self.context,'revision':0,'status':'closed','reason':'월 취합 마감'})
        self.cookie=self.super_cookie;self.assertEqual(self.apply(preview['token'])[0],409)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM indicator_definitions').fetchone()[0],0)

if __name__=='__main__':unittest.main(verbosity=2)
