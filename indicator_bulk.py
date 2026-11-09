"""Whole-catalog XLSX editing with validation, preview and atomic application."""
import hashlib
import io
import json
import posixpath
import re
import secrets
import time
import zipfile
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
import management as m
import progress
import review

MAX_FILE=5*1024*1024
MAX_UNPACKED=25*1024*1024
MAX_ROWS=10000
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
MASTER_HEADERS=['지표ID','버전','기관유형','적용부서','범주','평가유형','지표명','단위','목표치','가중치(배점)','산식·평가기준']
TARGET_HEADERS=['근무지ID','근무지','평가연도','지표ID','지표명(참고)','설정버전','목표치','가중치(배점)','단위','입력방식','달성률방식']


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS indicator_imports (
        token_hash TEXT PRIMARY KEY,actor TEXT NOT NULL,session_hash TEXT NOT NULL,
        payload TEXT NOT NULL,expires REAL NOT NULL,consumed INTEGER NOT NULL DEFAULT 0)''')


def year_arg(params):return m.period_args({'year':params.get('year'),'month':1})[0]

def num(value):return Decimal(str(value)) if value not in (None,'') else ''

def template(db,user,year,catalog,available):
    m.require_super(user)
    masters=[MASTER_HEADERS]
    for k in catalog['indicators']:
        masters.append([k['id'],k['revision'],k['kind'],k['applies_to'],k['category'],k['type'],k['name'],k.get('unit',''),num(k.get('target')),num(k.get('points')),k.get('formula_note','')])
    targets=[TARGET_HEADERS]
    for d in catalog['departments']:
        if d['kind']=='운영':continue
        for k in available(d['id'],db):
            c=progress.settings(db,d['id'],year,k)
            targets.append([d['id'],d['name'],year,k['id'],k['name'],c['revision'],num(c['target']),num(c['weight']),c['unit'],'수치형' if c['entry_mode']=='numeric' else '서술형','상향형' if c['direction']=='higher' else '하향형'])
    guide=[['항목','작성 안내'],['평가연도',str(year)],['지표정의','노란색 지표명·단위·목표·배점·산식 설명을 수정합니다. 공통 기본값입니다.'],['부서별목표','노란색 목표·가중치·단위·입력방식·달성률방식을 수정합니다. 선택 연도의 근무지별 기준입니다.'],['회색 항목','ID·버전·근무지·평가유형 등 회색 항목과 시트명, 제목 행, 행 수는 유지합니다. 신규 지표 추가·삭제는 지원하지 않습니다.'],['입력방식','수치형 / 서술형 중 입력합니다. 이미 실적이 있으면 입력방식 전환은 제한됩니다.'],['달성률방식','상향형 / 하향형 중 입력합니다.'],['단위 %','20% 목표는 20으로 입력합니다. Excel에서 20%로 입력한 셀은 단위가 %일 때 20으로 읽습니다.'],['가중치','백분율이 아닌 배점 숫자를 입력합니다. 미확정 값은 공란으로 둡니다.'],['수식','수식은 값으로 붙여넣기 한 후 업로드합니다. XLSX 파일만 지원합니다.'],['적용','변경 미리보기와 검증을 거친 후 일괄 반영합니다. 오류가 있으면 전체를 반영하지 않습니다.'],['마감','부서별 변경은 미마감 월에만 반영하며 마감월은 보존합니다. 연중 모든 월이 마감된 부서는 수정하지 않습니다.'],['공통 기본값','부서별 별도 설정이 있으면 그 값을 우선합니다. 공통 지표 수정은 기존 실적에 자동 소급하지 않습니다.'],['충돌','다운로드 후 다른 사람이 수정했다면 새 양식을 내려받아 작업합니다.'],['보존','실적·증빙·관리자 권한·직원 담당지표는 이 양식에서 삭제하거나 변경하지 않습니다.']]
    original=review.xlsx([('지표정의',masters),('부서별목표',targets),('사용안내',guide)])
    # Yellow fields are editable; grey keys are protected against accidental editing.
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as dest:
        for info in source.infolist():
            body=source.read(info.filename)
            if info.filename=='xl/styles.xml':
                root=ET.fromstring(body);fills=root.find('s:fills',NS);xfs=root.find('s:cellXfs',NS)
                for color in ('FFFFF2CC','FFE7E6E6'):
                    fill=ET.SubElement(fills,'{'+NS['s']+'}fill');pattern=ET.SubElement(fill,'{'+NS['s']+'}patternFill',{'patternType':'solid'});ET.SubElement(pattern,'{'+NS['s']+'}fgColor',{'rgb':color});ET.SubElement(pattern,'{'+NS['s']+'}bgColor',{'indexed':'64'})
                fills.set('count',str(len(fills)))
                for fill,locked in [('2','0'),('3','1')]:
                    xf=ET.SubElement(xfs,'{'+NS['s']+'}xf',{'numFmtId':'0','fontId':'0','fillId':fill,'borderId':'0','xfId':'0','applyProtection':'1','applyFill':'1'})
                    ET.SubElement(xf,'{'+NS['s']+'}alignment',{'vertical':'top','wrapText':'1'});ET.SubElement(xf,'{'+NS['s']+'}protection',{'locked':locked})
                xfs.set('count',str(len(xfs)));body=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            elif info.filename in ('xl/worksheets/sheet1.xml','xl/worksheets/sheet2.xml'):
                root=ET.fromstring(body)
                for cell in root.findall('.//s:c',NS):
                    match=re.fullmatch(r'([A-Z]+)(\d+)',cell.attrib['r'])
                    if int(match[2])>1:cell.set('s','3' if match[1] in ('G','H','I','J','K') else '4')
                protection=ET.Element('{'+NS['s']+'}sheetProtection',{'sheet':'1','objects':'1','scenarios':'1','autoFilter':'0'})
                sheet_data=root.find('s:sheetData',NS);root.insert(list(root).index(sheet_data)+1,protection)
                body=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            dest.writestr(info.filename,body)
    return output.getvalue()


class Cell:
    def __init__(self,value='',percent=False):self.value=value;self.percent=percent

def _xml(body):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():raise m.APIError(400,'지원하지 않는 XML 형식입니다.')
    return ET.fromstring(body)

def read_workbook(body):
    if not 0<len(body)<=MAX_FILE:raise m.APIError(413,'양식은 5MB 이하의 XLSX 파일로 업로드해 주세요.')
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(infos)>200 or len(set(names))!=len(names) or sum(i.file_size for i in infos)>MAX_UNPACKED or any(i.flag_bits&1 for i in infos):raise m.APIError(400,'파일이 너무 크거나 지원하지 않는 압축 형식입니다.')
            if any('vbaproject' in n.lower() for n in names):raise m.APIError(400,'매크로가 없는 XLSX 양식을 사용해 주세요.')
            workbook=_xml(archive.read('xl/workbook.xml'));rels=_xml(archive.read('xl/_rels/workbook.xml.rels'))
            relationships={r.attrib['Id']:r.attrib for r in rels}
            shared=[]
            if 'xl/sharedStrings.xml' in names:
                root=_xml(archive.read('xl/sharedStrings.xml'))
                shared=[''.join(t.text or '' for t in cell.findall('.//s:t',NS)) for cell in root.findall('s:si',NS)]
                if len(shared)>100000:raise m.APIError(400,'문자열 수가 너무 많습니다.')
            percent_styles=set()
            if 'xl/styles.xml' in names:
                styles=_xml(archive.read('xl/styles.xml'));custom={int(x.attrib['numFmtId']):x.attrib.get('formatCode','') for x in styles.findall('s:numFmts/s:numFmt',NS)}
                for index,style in enumerate(styles.findall('s:cellXfs/s:xf',NS)):
                    fmt=int(style.attrib.get('numFmtId',0));code=re.sub(r'"[^"]*"|\\.', '',custom.get(fmt,''))
                    if fmt in (9,10) or '%' in code:percent_styles.add(index)
            result={}
            for sheet in workbook.findall('s:sheets/s:sheet',NS):
                name=sheet.attrib.get('name')
                if name not in ('지표정의','부서별목표'):continue
                if name in result:raise m.APIError(400,'중복된 시트가 있습니다.')
                relationship=relationships[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
                if relationship.get('TargetMode')=='External':raise m.APIError(400,'외부 연결 시트는 지원하지 않습니다.')
                target=relationship['Target'];path=posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
                if not path.startswith('xl/'):raise m.APIError(400,'시트 경로가 올바르지 않습니다.')
                root=_xml(archive.read(path));rows=[];seen_rows=set()
                for row in root.findall('s:sheetData/s:row',NS):
                    row_index=int(row.attrib.get('r',0))
                    if row_index in seen_rows:raise m.APIError(400,'중복된 행 번호가 있습니다.')
                    seen_rows.add(row_index)
                    if row_index>MAX_ROWS or len(rows)>=MAX_ROWS:raise m.APIError(400,'행 수가 너무 많습니다.')
                    cells={}
                    for cell in row.findall('s:c',NS):
                        match=re.fullmatch(r'([A-Z]+)(\d+)',cell.attrib.get('r',''))
                        if not match or int(match[2])!=row_index or match[1] in cells:raise m.APIError(400,'셀 위치를 확인해 주세요.')
                        if cell.find('s:f',NS) is not None:raise m.APIError(400,f'{name} {row_index}행: 수식을 값으로 붙여넣기 해 주세요.')
                        kind=cell.attrib.get('t');value=cell.find('s:v',NS)
                        if kind=='s':text=shared[int(value.text)]
                        elif kind=='inlineStr':text=''.join(t.text or '' for t in cell.findall('.//s:t',NS))
                        elif kind in ('b','e'):raise m.APIError(400,f'{name} {row_index}행: 오류값·참/거짓 값은 사용할 수 없습니다.')
                        else:text=value.text if value is not None and value.text is not None else ''
                        if len(text)>10000:raise m.APIError(400,'셀 내용이 너무 깁니다.')
                        cells[match[1]]=Cell(text,kind not in ('s','inlineStr') and int(cell.attrib.get('s',0)) in percent_styles)
                    if any(c.value!='' for c in cells.values()):rows.append((row_index,cells))
                result[name]=rows
            if set(result)!= {'지표정의','부서별목표'}:raise m.APIError(400,'지표정의와 부서별목표 시트가 있는 원본 양식을 사용해 주세요.')
            return result
    except m.APIError:raise
    except (zipfile.BadZipFile,KeyError,ET.ParseError,ValueError,IndexError,TypeError,AttributeError,UnicodeError,RuntimeError,OverflowError,OSError):
        raise m.APIError(400,'XLSX 양식을 읽을 수 없습니다. 내려받은 양식을 사용해 주세요.')


def integer(value):
    try:
        number=Decimal(value)
        if not number.is_finite() or number!=number.to_integral_value() or number<0 or number>10000000:raise ValueError()
        return int(number)
    except (InvalidOperation,ValueError):raise m.APIError(400,'연도·버전 값을 확인해 주세요.')

def number_cell(cell,unit='',weight=False):
    value=cell.value
    if cell.percent and value!='':
        if weight or unit!='%':raise m.APIError(400,'백분율 셀은 단위가 %인 목표치에만 사용해 주세요. 가중치는 점수로 입력합니다.')
        value=format(Decimal(value)*100,'f')
    return m.number(value,True)

def same(before,after,keys):
    for key in keys:
        a,b=before.get(key),after.get(key)
        if key in ('target','points','weight'):
            if a in (None,'') and b in (None,''):continue
            if a in (None,'') or b in (None,'') or Decimal(str(a))!=Decimal(str(b)):return False
        elif (a or '')!=(b or ''):return False
    return True

def period_versions(db,department,year):
    return [(month,m.period(db,department,year,month)['status'],m.period(db,department,year,month)['revision']) for month in range(1,13)]


def plan(db,body,year,catalog,available):
    workbook=read_workbook(body);masters={k['id']:k for k in catalog['indicators']}
    departments={d['id']:d for d in catalog['departments'] if d['kind']!='운영'}
    targets={(did,k['id']):k for did in departments for k in available(did,db)}
    actions=[];changes=[];errors=[];seen_master=set();seen_targets=set()
    for sheet,headers in [('지표정의',MASTER_HEADERS),('부서별목표',TARGET_HEADERS)]:
        rows=workbook[sheet]
        if not rows or rows[0][0]!=1 or [rows[0][1].get(chr(65+i),Cell()).value for i in range(len(headers))]!=headers:
            raise m.APIError(400,f'{sheet}: 첫 행의 항목명과 순서를 유지해 주세요.')
        for index,cells in rows[1:]:
            try:
                if any(len(col)>1 or col>'K' for col in cells):raise m.APIError(400,'추가 열은 지원하지 않습니다.')
                values=[cells.get(chr(65+i),Cell()) for i in range(11)];text=[c.value for c in values]
                if sheet=='지표정의':
                    key=text[0]
                    if key not in masters or key in seen_master:raise m.APIError(400,'지표ID가 없거나 중복되었습니다.')
                    seen_master.add(key);original=masters[key]
                    if integer(text[1])!=original['revision']:raise m.APIError(409,'지표가 수정되었습니다. 최신 양식을 다시 내려받아 주세요.')
                    if text[2:6]!=[original.get(key) or '' for key in ('kind','applies_to','category','type')]:raise m.APIError(400,'기관유형·적용부서·범주·평가유형은 회색 항목이므로 유지해 주세요.')
                    definition=m.indicator_definition({'name':text[6],'unit':text[7],'target':number_cell(values[8],text[7]),'points':number_cell(values[9],weight=True),'formula_note':text[10]})
                    if not same(original,definition,definition):
                        actions.append({'path':'/api/admin/indicator','data':{'indicator_id':key,'revision':original['revision'],**definition}})
                        changes.append({'sheet':sheet,'row':index,'key':key,'label':definition['name'],'before':{k:original.get(k) for k in definition},'after':definition})
                else:
                    key=(text[0],text[3])
                    if key not in targets or key in seen_targets:raise m.APIError(400,'근무지·지표 조합이 없거나 중복되었습니다.')
                    seen_targets.add(key);original=targets[key];department=departments[key[0]]
                    if text[1]!=department['name'] or integer(text[2])!=year or text[4]!=original['name']:raise m.APIError(400,'근무지·평가연도·지표명 참고 항목은 유지해 주세요.')
                    before=progress.settings(db,key[0],year,original)
                    if integer(text[5])!=before['revision']:raise m.APIError(409,'부서별 목표가 수정되었습니다. 최신 양식을 다시 내려받아 주세요.')
                    modes={'수치형':'numeric','서술형':'text'};directions={'상향형':'higher','하향형':'lower'}
                    if text[9] not in modes or text[10] not in directions:raise m.APIError(400,'입력방식은 수치형/서술형, 달성률방식은 상향형/하향형으로 입력합니다.')
                    definition={'target':number_cell(values[6],text[8]),'weight':number_cell(values[7],weight=True),'unit':text[8].strip(),'entry_mode':modes[text[9]],'direction':directions[text[10]]}
                    if not same(before,definition,definition):
                        periods=period_versions(db,key[0],year);opened=[p[0] for p in periods if p[1]=='open']
                        if not opened:raise m.APIError(409,'해당 연도의 모든 월이 마감되었습니다.')
                        actions.append({'path':'/api/monthly/settings','data':{'department_id':key[0],'indicator_id':key[1],'year':year,'month':opened[0],'revision':before['revision'],**definition}})
                        changes.append({'sheet':sheet,'row':index,'key':key[0]+' / '+key[1],'label':department['name']+' · '+original['name'],'before':{k:before.get(k) for k in definition},'after':definition})
            except (m.APIError,InvalidOperation) as exc:errors.append(f'{sheet} {index}행: {str(exc) or "숫자 형식을 확인해 주세요."}')
    if set(masters)!=seen_master:errors.append('지표정의의 전체 행을 유지해 주세요. 누락된 지표가 있습니다.')
    if set(targets)!=seen_targets:errors.append('부서별목표의 전체 행을 유지해 주세요. 누락된 근무지·지표가 있습니다.')
    if errors:raise m.APIError(400,'\n'.join(errors[:20])+ ('\n외 추가 오류가 있습니다.' if len(errors)>20 else ''))
    baselines={'masters':{k:v['revision'] for k,v in masters.items()},'targets':{did+'::'+kid:progress.settings(db,did,year,k)['revision'] for (did,kid),k in targets.items()},'periods':{did:period_versions(db,did,year) for did in departments}}
    return {'actions':actions,'changes':changes,'year':year,'baselines':baselines}


def execute(db,user,actions,catalog,available,reason):
    for action in actions:m.post(db,user,action['path'],{**action['data'],'reason':reason},m.effective_catalog(db,catalog),available)


def preview(db,user,body,year,catalog,available,csrf):
    m.require_super(user);payload=plan(db,body,year,catalog,available)
    db.execute('SAVEPOINT bulk_validation')
    try:execute(db,user,payload['actions'],catalog,available,'일괄 수정 사전 검증')
    finally:
        db.execute('ROLLBACK TO bulk_validation');db.execute('RELEASE bulk_validation')
    token=secrets.token_urlsafe(32);db.execute('DELETE FROM indicator_imports WHERE expires<?',(time.time(),))
    db.execute('INSERT INTO indicator_imports(token_hash,actor,session_hash,payload,expires) VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['employee_id'],hashlib.sha256(csrf.encode()).hexdigest(),json.dumps(payload,ensure_ascii=False),time.time()+900))
    return {'token':token,'changes':payload['changes'],'master_count':sum(a['path']=='/api/admin/indicator' for a in payload['actions']),'target_count':sum(a['path']=='/api/monthly/settings' for a in payload['actions']),'expires_in':900}


def apply(db,user,data,catalog,available):
    m.require_super(user);reason=m.reason(data);token=data.get('token','')
    if not isinstance(token,str) or len(token)>200:raise m.APIError(400,'미리보기를 다시 실행해 주세요.')
    row=db.execute('SELECT * FROM indicator_imports WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row or row['actor']!=user['employee_id'] or row['session_hash']!=hashlib.sha256(str(data.get('csrf','')).encode()).hexdigest() or row['expires']<time.time() or row['consumed']:
        raise m.APIError(409,'미리보기가 만료되었거나 이미 반영되었습니다. 다시 검증해 주세요.')
    payload=json.loads(row['payload']);baseline=payload['baselines']
    if {k['id']:k['revision'] for k in catalog['indicators']}!=baseline['masters']:raise m.APIError(409,'지표가 변경되었습니다. 최신 양식으로 다시 검증해 주세요.')
    for d in catalog['departments']:
        if d['kind']=='운영':continue
        if [list(p) for p in period_versions(db,d['id'],payload['year'])]!=baseline['periods'].get(d['id']):raise m.APIError(409,'월 마감 상태가 변경되었습니다. 다시 검증해 주세요.')
        for k in available(d['id'],db):
            if progress.settings(db,d['id'],payload['year'],k)['revision']!=baseline['targets'].get(d['id']+'::'+k['id']):raise m.APIError(409,'목표·가중치가 변경되었습니다. 다시 검증해 주세요.')
    execute(db,user,payload['actions'],catalog,available,reason)
    db.execute('UPDATE indicator_imports SET consumed=1 WHERE token_hash=?',(row['token_hash'],))
    m.audit(db,user['employee_id'],'지표 일괄 수정',None,payload['year'],None,{'change_count':len(payload['actions'])},reason)
    return {'ok':True,'change_count':len(payload['actions'])}
