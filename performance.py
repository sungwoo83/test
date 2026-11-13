"""Versioned indicator plans, calendar periods, submission workflow and evaluation."""
import calendar
import json
import re
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
import management as m
import progress


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS performance_plans(department TEXT,year INTEGER,indicator TEXT,definition TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(department,year,indicator));
    CREATE TABLE IF NOT EXISTS performance_records(id INTEGER PRIMARY KEY,department TEXT NOT NULL,year INTEGER NOT NULL,indicator TEXT NOT NULL,employee TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,cadence TEXT NOT NULL,definition TEXT NOT NULL,values_json TEXT NOT NULL,narrative TEXT NOT NULL,plan TEXT NOT NULL,status TEXT NOT NULL,state TEXT NOT NULL,feedback TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL,updated_at TEXT NOT NULL,UNIQUE(department,year,indicator,employee,start,end));
    CREATE TABLE IF NOT EXISTS performance_locks(department TEXT,year INTEGER,indicator TEXT,start TEXT,end TEXT,closed INTEGER NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(department,year,indicator,start,end));
    CREATE TABLE IF NOT EXISTS performance_ratings(department TEXT,year INTEGER,indicator TEXT,evaluator TEXT,grade TEXT,adjustment TEXT NOT NULL DEFAULT '0',excluded INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(department,year,indicator));
    CREATE TABLE IF NOT EXISTS performance_years(department TEXT,year INTEGER,closed INTEGER NOT NULL,revision INTEGER NOT NULL,report TEXT,PRIMARY KEY(department,year));
    CREATE TABLE IF NOT EXISTS performance_files(id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES performance_records(id),storage_name TEXT NOT NULL UNIQUE,original_name TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS performance_transfers(id INTEGER PRIMARY KEY,employee TEXT,from_department TEXT,to_department TEXT,before_json TEXT,after_json TEXT,reason TEXT,actor TEXT,created_at TEXT);
    ''')
    cols={r['name'] for r in db.execute('PRAGMA table_info(users)')}
    if 'account_status' not in cols:db.execute("ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'")


def fail(message,status=400):raise m.APIError(status,message)
def dumps(value):return json.dumps(value,ensure_ascii=False)
def dec(value):return Decimal(m.number(value))
def fmt(value):return format(Decimal(value).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP),'f')
def integer(value,low=2020,high=2100):
    if isinstance(value,bool) or not str(value).isdigit() or not low<=int(value)<=high:fail('연도 또는 번호를 확인해 주세요.')
    return int(value)
def day(value):
    try:return date.fromisoformat(value)
    except (ValueError,TypeError):fail('날짜는 YYYY-MM-DD로 입력해 주세요.')
def text(value,limit=5000):
    if not isinstance(value,str) or len(value)>limit:fail('문자 입력 길이를 확인해 주세요.')
    return value.strip()
def manager(user):return user['role'] in ('admin','superadmin')
def scope(db,user,params,catalog):
    department=params.get('department_id',user['department_id'])
    if not (params.get('view')=='own' and department==user['department_id']):m.department_scope(user,department,catalog)
    return department,integer(params.get('year'))
def writable(db,department,year):
    row=db.execute('SELECT closed FROM performance_years WHERE department=? AND year=?',(department,year)).fetchone()
    if row and row['closed']:fail('확정 보관된 평가연도입니다. 관리자가 연도 마감을 해제해 주세요.',409)
def indicator_for(db,department,key,available):
    item=next((k for k in available(department,db) if k['id']==key),None)
    if not item:fail('해당 근무지의 지표를 선택해 주세요.')
    return item


def default_components(key,config):
    """Only explicit manual rules; unknown components remain manager-configurable."""
    def c(i,name,weight,target,unit,method='ratio',**extra):return {'id':str(i),'name':name,'weight':str(weight),'target':str(target),'unit':unit,'method':method,'floor':'0.3','cap':'1',**extra}
    rules={
      'KPI-024':[c(1,'공공데이터 제공 운영 실태 평가',5,'74.8','점'),c(2,'데이터기반행정 실태 점검',5,'72.9','점')],
      'KPI-025':[c(1,'AI 인프라 체계 확립 이행률',10,20,'%'),c(2,'AI 기반 일하는 방식 혁신 과제',10,3,'건'),c(3,'AI 전문교육 이수 인원',10,10,'명')],
      'KPI-027':[c(1,'혁신계획 수립·결과보고', '.5', '.5','점','points'),c(2,'혁신 수상·학술대회', '.5','.5','점','points'),c(3,'경영공시 매뉴얼 미준수 건수',1,0,'건','bands_lower',bands=[['0','1'],['1','.6'],['2','.3']]),c(4,'동반성장 활동',1,1,'점','points')],
      'KPI-028':[c(1,'내PC지키미 대응지수','1.5',90,'%','bands',bands=[['90','1'],['70','.75'],['50','.5'],['0','.3']]),c(2,'보안점검 조치지수',1,90,'%','bands',bands=[['90','1'],['70','.75'],['50','.5'],['0','.3']]),c(3,'해킹메일 대응지수','2.5',90,'점','bands',bands=[['90','1'],['70','.75'],['50','.5'],['-100','.3']])],
      'KPI-029':[c(1,'체크리스트 계획 수립·이행','.25',100,'%'),c(2,'모니터링 제출 횟수','.25',4,'회','bands',bands=[['4','1'],['3','.8'],['2','.6'],['1','.4'],['0','.3']])],
      'KPI-030':[c(1,'부패·불공정요인 제도개선',3,2,'건','bands',bands=[['2','1'],['1','.65'],['0','.3']]),c(2,'감사 지적사항 적시 이행률',1,100,'%','bands',bands=[['100','1'],['90','.8'],['70','.6'],['0','.3']]),c(3,'내부통제 강화·개선',1,1,'점','points')]
    }
    if config['entry_mode']=='text':return []
    if config.get('manual_reference') and key in rules:return rules[key]
    target=config.get('target');weight=config.get('weight')
    return [c(1,'지표 실적',weight or 0,target or 100,config.get('unit',''),floor='0')] if target and Decimal(target)>0 else []


def get_plan(db,department,year,indicator):
    row=db.execute('SELECT * FROM performance_plans WHERE department=? AND year=? AND indicator=?',(department,year,indicator['id'])).fetchone()
    if row:return {**json.loads(row['definition']),'revision':row['revision'],'configured':True}
    config=progress.settings(db,department,year,indicator)
    if config.get('manual_reference'):
        config['manual_reference']={**config['manual_reference'],'note':'편람 원문 세부목표입니다. 통합 성과관리에서는 세부 실적과 등록된 산식으로 계산합니다. 환산점수 직접 입력 항목은 평가 근거를 확인해 입력하며 결측·가감점은 최종 평가에서 사유와 함께 처리합니다.'}
    return {'name':indicator['name'],'mode':config['entry_mode'],'weight':config.get('weight') or '0',
        'components':default_components(indicator['id'],config),'schedule':[{'effective':f'{year}-01-01','cadence':'monthly'}],
        'start':f'{year}-01-01','end':f'{year}-11-30','due_days':7,'curve':config.get('dashboard_curve','linear'),
        'milestones':[],'aggregation':'representative','representative':'','time_aggregation':'latest',
        'revision':0,'configured':False,'reference':config.get('manual_reference')}


def period(plan,reference):
    d=day(reference);start,end=day(plan['start']),day(plan['end'])
    if not start<=d<=end:fail('지표의 관리기간 안에서 기준일을 선택해 주세요.')
    schedule=plan['schedule'];current=max((r for r in schedule if day(r['effective'])<=d),key=lambda r:r['effective'])
    following=[day(r['effective']) for r in schedule if day(r['effective'])>d]
    cadence=current['cadence']
    if cadence=='weekly':a=d-timedelta(days=d.weekday());b=a+timedelta(days=6)
    else:a=d.replace(day=1);b=d.replace(day=calendar.monthrange(d.year,d.month)[1])
    a=max(a,start,day(current['effective']));b=min(b,end,min(following)-timedelta(days=1) if following else end)
    return {'start':a.isoformat(),'end':b.isoformat(),'cadence':cadence,'label':('주별' if cadence=='weekly' else '월별')+f' {a} ~ {b}',
            'due':min(b+timedelta(days=plan['due_days']),end).isoformat()}


def validate_plan(db,department,year,item,data):
    if not isinstance(data,dict):fail('지표 설정 형식을 확인해 주세요.')
    definition={k:data.get(k) for k in ('mode','weight','components','schedule','start','end','due_days','curve','milestones','aggregation','representative','time_aggregation')}
    ref=progress.settings(db,department,year,item).get('manual_reference')
    if ref:definition['reference']={**ref,'note':'편람 원문 참고입니다. 등록된 세부 실적·산식으로 계산하며, 환산점수 직접 입력 항목은 확인한 점수와 근거를 입력합니다.'}
    definition['name']=item['name'];definition['weight']=m.number(definition['weight'])
    if dec(definition['weight'])<0 or definition['mode'] not in ('numeric','text'):fail('배점과 입력 방식을 확인해 주세요.')
    a,b=day(definition['start']),day(definition['end'])
    if a.year!=year or b.year!=year or a>b:fail('관리 시작일·종료일은 해당 평가연도 안에 지정해 주세요.')
    schedule=definition['schedule']
    if not isinstance(schedule,list) or not 1<=len(schedule)<=24:fail('관리주기를 1~24개 지정해 주세요.')
    previous=None
    for r in schedule:
        if not isinstance(r,dict):fail('관리주기 형식을 확인해 주세요.')
        d=day(r.get('effective'))
        if r.get('cadence') not in ('monthly','weekly') or not a<=d<=b or previous and d<=previous:fail('주기 적용일은 관리기간 안에서 중복 없이 날짜순으로 지정해 주세요.')
        previous=d
    if day(schedule[0]['effective'])!=a:fail('첫 관리주기 적용일은 관리 시작일이어야 합니다.')
    definition['due_days']=integer(definition['due_days'],0,60)
    if definition['curve'] not in ('linear','late','milestones'):fail('진행계획을 선택해 주세요.')
    milestones=definition['milestones']
    if not isinstance(milestones,list) or len(milestones)>24:fail('중간목표를 확인해 주세요.')
    prev=a-timedelta(days=1);last=Decimal(0)
    for r in milestones:
        if not isinstance(r,dict):fail('중간목표 형식을 확인해 주세요.')
        d=day(r.get('date'));p=dec(r.get('percent'))
        if not a<=d<=b or d<=prev or not last<=p<=100:fail('중간목표는 날짜·달성률 증가 순서로 지정해 주세요.')
        prev,last=d,p
    if definition['curve']=='milestones' and (not milestones or day(milestones[-1]['date'])!=b or last!=100):fail('중간목표의 마지막 값은 관리 종료일 100%여야 합니다.')
    if definition['aggregation'] not in ('sum','ratio','average','representative') or definition['time_aggregation'] not in ('latest','sum','average'):fail('취합 방법을 선택해 주세요.')
    owner=text(definition.get('representative') or '',30)
    if owner:
        row=db.execute("SELECT * FROM users WHERE employee_id=? AND account_status='active'",(owner,)).fetchone()
        if not row or row['department_id']!=department or item['id'] not in json.loads(row['indicators']):fail('대표 담당자는 현재 소속의 해당 지표 담당자로 지정해 주세요.')
    definition['representative']=owner
    components=definition['components']
    if not isinstance(components,list) or len(components)>30:fail('세부항목은 최대 30개입니다.')
    ids=set();total=Decimal(0)
    for r in components:
        if not isinstance(r,dict):fail('세부항목 형식을 확인해 주세요.')
        key=r.get('id','')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',key) or key in ids:fail('세부항목 ID를 중복 없이 지정해 주세요.')
        ids.add(key);r['name']=text(r.get('name'),200);r['unit']=text(r.get('unit',''),30)
        if not r['name'] or r.get('method') not in ('ratio','lower','rate','bands','bands_lower','points'):fail('세부항목 이름·계산방식을 확인해 주세요.')
        for k in ('target','weight','floor','cap'):r[k]=m.number(r.get(k))
        if not 0<=dec(r['floor'])<=dec(r['cap'])<=1 or dec(r['weight'])<0:fail('기본점수·상한은 0~1, 배점은 0 이상이어야 합니다.')
        if r['method'] in ('ratio','lower','rate') and dec(r['target'])<=0:fail('비율 산식의 목표는 0보다 커야 합니다.')
        if r['method']=='rate' and (definition['aggregation']=='average' or definition['time_aggregation']=='average'):fail('비율의 평균 대신 분모·분자 합산 또는 최신 누적값을 선택해 주세요.')
        if definition['aggregation']=='ratio' and r['method']!='rate':fail('분모·분자 합산 취합은 모든 세부항목을 비율(rate) 방식으로 설정해 주세요.')
        bands=r.get('bands',[])
        if r['method'].startswith('bands'):
            if not isinstance(bands,list) or not 1<=len(bands)<=20:fail('구간별 기준·득점비율을 입력해 주세요.')
            seen=set()
            for pair in bands:
                if not isinstance(pair,list) or len(pair)!=2:fail('구간 기준은 [수치,득점비율] 형태입니다.')
                v,p=dec(pair[0]),dec(pair[1])
                if v in seen or not 0<=p<=1:fail('중복 기준 또는 잘못된 득점비율입니다.')
                seen.add(v)
        total+=dec(r['weight'])
    if definition['mode']=='numeric' and (not components or total!=dec(definition['weight'])):fail('세부항목 배점 합계가 지표 가중치와 같아야 합니다.')
    if definition['mode']=='text' and components:fail('비계량 지표는 세부 수치항목 없이 등급으로 평가합니다.')
    # No calendar edits may split an existing saved period.
    for record in db.execute('SELECT start,end FROM performance_records WHERE department=? AND year=? AND indicator=?',(department,year,item['id'])):
        try:p=period(definition,record['start'])
        except m.APIError:fail('기존 실적의 기간을 관리범위에서 제외할 수 없습니다.',409)
        if p['start']!=record['start'] or p['end']!=record['end']:fail('기존 실적 기간을 나누는 주기 변경은 할 수 없습니다. 다음 미입력 기간부터 적용해 주세요.',409)
    return definition


def calculate(plan,values,complete=False):
    if plan['mode']=='text':return {'score':None,'percent':None,'components':[]}
    if not isinstance(values,dict) or set(values)-{c['id'] for c in plan['components']}:fail('세부 실적 항목을 확인해 주세요.')
    parts=[];total=Decimal(0);missing=[]
    for component in plan['components']:
        key=component['id'];v=values.get(key)
        if v in (None,''):
            missing.append(key);parts.append({'id':key,'name':component['name'],'score':None});continue
        method=component['method'];weight=dec(component['weight']);target=dec(component['target'])
        if method=='rate':
            if not isinstance(v,dict) or set(v)!={'numerator','denominator'}:fail('비율 실적은 분자·분모를 입력해 주세요.')
            n,d=dec(v['numerator']),dec(v['denominator'])
            if n<0 or d<=0 or n>d:fail('분모는 양수, 분자는 0 이상 분모 이하여야 합니다. 결측은 평가에서 별도 처리하세요.')
            actual=n/d*100;ratio=actual/target
        else:
            actual=dec(v)
            if component['unit'] in ('건','회','명') and actual<0:fail('건수·횟수·인원은 0 이상으로 입력해 주세요.')
            if method=='ratio':ratio=actual/target
            elif method=='lower':
                if actual<=0:fail('하향형 실적은 0보다 커야 합니다. 0건 최고점은 구간 방식으로 설정해 주세요.')
                ratio=target/actual
            elif method=='points':
                if not 0<=actual<=weight:fail('환산점수는 해당 세부항목 배점 범위 안에서 입력해 주세요.')
                ratio=actual/weight if weight else Decimal(0)
            else:
                bands=sorted([(dec(x[0]),dec(x[1])) for x in component['bands']],reverse=method=='bands')
                ratio=next((p for v,p in bands if actual>=v),Decimal(0)) if method=='bands' else next((p for v,p in bands if actual<=v),bands[-1][1])
        # Manual converted points are already scored, so do not add a floor again.
        ratio=max(Decimal(0) if method=='points' else dec(component['floor']),min(dec(component['cap']),ratio))
        score=weight*ratio;total+=score
        parts.append({'id':key,'name':component['name'],'actual':fmt(actual),'score':fmt(score),'weight':component['weight'],'formula':method,'target':component['target'],'floor':component['floor'],'cap':component['cap']})
    if complete and missing:fail('모든 세부항목에 실적을 입력한 후 제출해 주세요.')
    return {'score':fmt(total) if not missing else None,'percent':fmt(total/dec(plan['weight'])*100) if not missing and dec(plan['weight'])>0 else None,'components':parts,'missing':missing}


def lock_state(db,dep,year,key,p):
    row=db.execute('SELECT * FROM performance_locks WHERE department=? AND year=? AND indicator=? AND start=? AND end=?',(dep,year,key,p['start'],p['end'])).fetchone()
    return dict(row) if row else {'closed':0,'revision':0}
def check_open(db,record):
    writable(db,record['department'],record['year'])
    if lock_state(db,record['department'],record['year'],record['indicator'],record)['closed']:fail('마감된 기간입니다.',409)
def inherited_readable(db,user,record):
    if user['department_id']!=record['department']:return False
    for transfer in db.execute('SELECT after_json,created_at FROM performance_transfers WHERE employee=? AND from_department=?',(record['employee'],record['department'])):
        after=json.loads(transfer['after_json'])
        if after.get('successor')==user['employee_id'] and record['indicator'] in after.get('indicators',[]) and record['start']<=transfer['created_at'][:10]:return True
    return False


def record_access(db,user,key,catalog,write=False):
    row=db.execute('SELECT * FROM performance_records WHERE id=?',(integer(key,1,10**12),)).fetchone()
    if not row:fail('실적을 찾을 수 없습니다.',404)
    own=row['employee']==user['employee_id']
    appointed=db.execute('SELECT 1 FROM performance_ratings WHERE department=? AND year=? AND indicator=? AND evaluator=?',(row['department'],row['year'],row['indicator'],user['employee_id'])).fetchone()
    if write:
        if own and row['department']==user['department_id'] and row['indicator'] in json.loads(user['indicators']):pass
        elif manager(user):m.department_scope(user,row['department'],catalog)
        else:fail('현재 담당지표만 수정할 수 있습니다.',403)
    elif own or row['state']=='approved' and appointed or inherited_readable(db,user,row):pass
    elif manager(user):m.department_scope(user,row['department'],catalog)
    else:fail('본인·인계받은 실적 또는 지정된 평가 자료만 조회할 수 있습니다.',403)
    if write:
        check_open(db,row)
        if row['state'] not in ('draft','returned'):fail('제출·확정된 실적은 반려 후 수정해 주세요.',409)
    return dict(row)
def public_record(db,row):
    r=dict(row);r['definition']=json.loads(r['definition']);r['values']=json.loads(r.pop('values_json'))
    r['calculation']=calculate(r['definition'],r['values'])
    r['files']=[dict(f) for f in db.execute('SELECT id,original_name,size,uploaded_by FROM performance_files WHERE record_id=? AND deleted_at IS NULL',(r['id'],))]
    return r

def pack_records(db,rows):return [public_record(db,r) for r in rows]

def aggregate(plan,records):
    approved=[r for r in records if r['state']=='approved']
    if not approved:return {'ready':False,'reason':'확정 실적 없음','calculation':None,'sources':[]}
    by_person={}
    for r in sorted(approved,key=lambda r:(r['end'],r['updated_at'],r['id'])):by_person.setdefault(r['employee'],[]).append(r)
    selected=[]
    if plan['aggregation']=='representative':
        if not plan['representative']:return {'ready':False,'reason':'대표 담당자를 지정해 주세요.','calculation':None,'sources':[]}
        by_person={k:v for k,v in by_person.items() if k==plan['representative']}
        if not by_person:return {'ready':False,'reason':'대표 담당자의 확정 실적 없음','calculation':None,'sources':[]}
    for rows in by_person.values():selected.extend(rows[-1:] if plan['time_aggregation']=='latest' else rows)
    if any(json.loads(r['definition'])['mode']!=plan['mode'] for r in selected):return {'ready':False,'reason':'실적과 현재 기준의 입력 방식이 다릅니다. 반려·최신 기준 적용 후 재제출해 주세요.','calculation':None,'sources':[r['id'] for r in selected]}
    if plan['mode']=='text':return {'ready':True,'reason':'비계량 등급 평가 필요','calculation':None,'sources':[r['id'] for r in selected]}
    values={}
    # Require compatible rules; never silently reinterpret prior snapshots.
    for r in selected:
        old=json.loads(r['definition'])
        if old['components']!=plan['components']:return {'ready':False,'reason':'확정 실적의 계산 기준이 현재 기준과 다릅니다. 이전 기준을 복원하거나 해당 실적을 검토·재제출해 주세요.','calculation':None,'sources':[r['id'] for r in selected]}
    for c in plan['components']:
        raw=[json.loads(r['values_json']).get(c['id']) for r in selected]
        if any(v in ('',None) for v in raw):return {'ready':False,'reason':'확정 실적 세부항목 누락','calculation':None,'sources':[]}
        if c['method']=='rate':values[c['id']]={'numerator':fmt(sum(dec(v['numerator']) for v in raw)),'denominator':fmt(sum(dec(v['denominator']) for v in raw))}
        else:
            person_values=[]
            for employee in sorted({r['employee'] for r in selected}):
                person_raw=[dec(json.loads(r['values_json'])[c['id']]) for r in selected if r['employee']==employee]
                person_values.append(sum(person_raw)/len(person_raw) if plan['time_aggregation']=='average' else sum(person_raw))
            combined=sum(person_values)/len(person_values) if plan['aggregation']=='average' else sum(person_values)
            values[c['id']]=fmt(combined)
    try:result=calculate(plan,values,True)
    except m.APIError as e:return {'ready':False,'reason':str(e),'calculation':None,'sources':[r['id'] for r in selected]}
    return {'ready':True,'reason':'확정 실적 기준','values':values,'calculation':result,'sources':[r['id'] for r in selected]}


def annual(db,user,dep,year,catalog,available):
    m.require_manager(user,dep);items=[];total=Decimal(0);maximum=Decimal(0);ready=True
    for indicator in available(dep,db):
        plan=get_plan(db,dep,year,indicator)
        if not plan['configured']:continue
        records=[dict(r) for r in db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=?',(dep,year,indicator['id']))]
        result=aggregate(plan,records);rating=db.execute('SELECT * FROM performance_ratings WHERE department=? AND year=? AND indicator=?',(dep,year,indicator['id'])).fetchone()
        rating=dict(rating) if rating else {'evaluator':'','grade':None,'adjustment':'0','excluded':0,'reason':'','revision':0}
        score=None;why=result['reason']
        if rating['excluded']:score=Decimal(0);why='결측 제외: '+rating['reason']
        elif plan['mode']=='text':
            if result['ready'] and rating['grade'] in ('S','A','B','C','D'):
                score=dec(plan['weight'])*{'S':Decimal(1),'A':Decimal('.95'),'B':Decimal('.90'),'C':Decimal('.85'),'D':Decimal('.8')}[rating['grade']]
            else:why='확정 보고서와 평가자의 등급 입력 필요'
        elif result['ready']:score=dec(result['calculation']['score'])
        if score is None:ready=False
        else:
            if not rating['excluded']:
                maximum+=dec(plan['weight']);score=max(Decimal(0),min(dec(plan['weight']),score+dec(rating['adjustment'])));total+=score
        items.append({'indicator':indicator,'definition':plan,'aggregate':result,'rating':rating,'score':fmt(score) if score is not None else None,'reason':why,'unconfirmed':sum(r['state']!='approved' for r in records)})
        if not rating['excluded'] and any(r['state']!='approved' for r in records):ready=False
    row=db.execute('SELECT * FROM performance_years WHERE department=? AND year=?',(dep,year)).fetchone()
    state=dict(row) if row else {'closed':0,'revision':0,'report':None}
    result={'department_id':dep,'year':year,'items':items,'score':fmt(total),'maximum':fmt(maximum),'ready':bool(items) and maximum>0 and ready and len(items)==len(available(dep,db)),'year_state':state}
    if state['closed'] and state['report']:
        frozen=json.loads(state['report']);frozen['year_state']=state;return frozen
    return result


def get(db,user,path,params,catalog,available):
    if path=='/api/performance/overview':
        m.require_super(user);year=integer(params.get('year'));reference=params.get('date',f'{year}-01-01');day(reference);places=[]
        for dep in catalog['departments']:
            if dep['kind']=='운영':continue
            definitions=list(db.execute('SELECT definition FROM performance_plans WHERE department=? AND year=?',(dep['id'],year)))
            counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM performance_records WHERE department=? AND year=? GROUP BY state',(dep['id'],year))}
            weekly=monthly=0
            for definition_row in definitions:
                try:active=period(json.loads(definition_row[0]),reference)
                except m.APIError:continue
                weekly+=active['cadence']=='weekly';monthly+=active['cadence']=='monthly'
            places.append({'department_id':dep['id'],'name':dep['name'],'configured':len(definitions),'total':len(available(dep['id'],db)),'weekly':weekly,'monthly':monthly,'states':counts})
        return {'places':places}
    if path=='/api/performance/operations':
        m.require_super(user)
        from pathlib import Path
        db_path=Path(db.execute('PRAGMA database_list').fetchone()['file'])
        folder=db_path.parent/'backups';status_path=folder/'status.json'
        try:status=json.loads(status_path.read_text()) if status_path.exists() else {'ok':False}
        except (ValueError,OSError):status={'ok':False,'error':'백업 상태를 확인해 주세요.'}
        return {'status':status,'backups':[{'name':p.name,'size':p.stat().st_size} for p in sorted(folder.glob('performance-*.zip'),reverse=True)[:100]]}
    if path=='/api/performance/assignments':
        tasks=[]
        for rating in db.execute('SELECT * FROM performance_ratings WHERE evaluator=?',(user['employee_id'],)):
            assigned_plan=db.execute('SELECT definition FROM performance_plans WHERE department=? AND year=? AND indicator=?',(rating['department'],rating['year'],rating['indicator'])).fetchone()
            if not assigned_plan or json.loads(assigned_plan[0])['mode']!='text':continue
            records=pack_records(db,db.execute("SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? AND state='approved'",(rating['department'],rating['year'],rating['indicator'])))
            tasks.append({'rating':dict(rating),'records':records})
        return {'tasks':tasks}
    if path=='/api/performance/history':
        owner=user['employee_id']
        return {'records':pack_records(db,db.execute('SELECT * FROM performance_records WHERE employee=? ORDER BY year DESC,end DESC LIMIT 300',(owner,))),
                'legacy':[{**dict(r),'files':progress.list_attachments(db,r['id'])} for r in db.execute('SELECT id,department_id,indicator_id,year,month,actual,status FROM monthly_actuals WHERE employee_id=? ORDER BY year DESC,month DESC LIMIT 300',(owner,))]}
    if path=='/api/performance/users':
        if not manager(user):fail('관리자만 조회할 수 있습니다.',403)
        return {'users':[dict(r) for r in db.execute("SELECT employee_id,display_name,department_id,indicators,role,account_status FROM users WHERE (?='superadmin' OR department_id=?) ORDER BY employee_id",(user['role'],user['admin_department_id']))]}
    dep,year=scope(db,user,params,catalog)
    if path=='/api/performance/annual':return annual(db,user,dep,year,catalog,available)
    if path=='/api/performance/audit':
        m.require_manager(user,dep)
        return {'events':[dict(r) for r in db.execute('SELECT * FROM audit_log WHERE department_id=? ORDER BY id DESC LIMIT 1000',(dep,))]}
    if path!='/api/performance':fail('요청을 찾을 수 없습니다.',404)
    view=params.get('view','own')
    if view=='review':m.require_manager(user,dep)
    reference=params.get('date',f'{year}-01-01');d=day(reference)
    if d.year!=year:fail('기준일은 선택한 평가연도 안이어야 합니다.')
    indicators=available(dep,db)
    if view!='review':indicators=[k for k in indicators if dep==user['department_id'] and k['id'] in json.loads(user['indicators'])]
    people=[dict(r) for r in db.execute("SELECT employee_id,display_name,indicators FROM users WHERE department_id=? AND account_status='active'",(dep,))]
    results=[]
    for indicator in indicators:
        plan=get_plan(db,dep,year,indicator)
        try:p=period(plan,reference)
        except m.APIError:p=None
        records=[dict(r) for r in db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? ORDER BY end,id',(dep,year,indicator['id']))]
        visible=records if view=='review' else [r for r in records if r['employee']==user['employee_id'] or inherited_readable(db,user,r)]
        current=[r for r in visible if p and r['start']==p['start'] and r['end']==p['end']]
        assigned=[p for p in people if indicator['id'] in json.loads(p['indicators'])]
        legacy=[dict(r) for r in db.execute('SELECT employee_id,year,month,actual,status FROM monthly_actuals WHERE department_id=? AND year=? AND indicator_id=? AND (?=1 OR employee_id=?) ORDER BY month',(dep,year,indicator['id'],int(view=='review'),user['employee_id']))]
        results.append({'indicator':indicator,'definition':plan,'period':p,'lock':lock_state(db,dep,year,indicator['id'],p) if p else {'closed':0,'revision':0},
                        'records':pack_records(db,current),'history':pack_records(db,visible),'assigned':assigned if view=='review' else [],
                        'aggregate':aggregate(plan,records) if view=='review' else None,'legacy':legacy,'monitor':monitoring(plan,visible,min(datetime.now(timezone(timedelta(hours=9))).date().isoformat(),p['end'] if p else reference))})
    ys=db.execute('SELECT closed,revision FROM performance_years WHERE department=? AND year=?',(dep,year)).fetchone()
    return {'department_id':dep,'year':year,'date':reference,'items':results,'year_state':dict(ys) if ys else {'closed':0,'revision':0}}


def post(db,user,path,data,catalog,available):
    if 'revision' in data:data['revision']=integer(data['revision'],0,10**12)
    if path=='/api/performance/account':return account(db,user,data,catalog)
    if path in ('/api/performance/record','/api/performance/transition','/api/performance/preview') and data.get('employee_id',user['employee_id'])==user['employee_id'] and data.get('department_id')==user['department_id']:
        dep,year=user['department_id'],integer(data.get('year'))
    elif path=='/api/performance/grade':
        dep,year=data.get('department_id'),integer(data.get('year'))
        if not db.execute('SELECT 1 FROM performance_ratings WHERE department=? AND year=? AND indicator=? AND evaluator=?',(dep,year,data.get('indicator_id'),user['employee_id'])).fetchone():fail('지정된 평가자만 등급을 입력할 수 있습니다.',403)
    else:dep,year=scope(db,user,data,catalog)
    writable(db,dep,year) if path!='/api/performance/year' else None
    why=m.reason(data)
    if path=='/api/performance/copy-year':
        m.require_manager(user,dep);source=integer(data.get('source_year'))
        if source==year:fail('서로 다른 연도를 지정해 주세요.')
        if db.execute('SELECT 1 FROM performance_plans WHERE department=? AND year=?',(dep,year)).fetchone():fail('대상 연도에 설정이 있습니다. 덮어쓰지 않습니다.',409)
        added=0
        for row in db.execute('SELECT * FROM performance_plans WHERE department=? AND year=?',(dep,source)).fetchall():
            definition=json.loads(row['definition'])
            for k in ('start','end'):definition[k]=f'{year}'+definition[k][4:]
            for r in definition['schedule']:r['effective']=f'{year}'+r['effective'][4:]
            for r in definition['milestones']:r['date']=f'{year}'+r['date'][4:]
            item=indicator_for(db,dep,row['indicator'],available)
            definition=validate_plan(db,dep,year,item,definition)
            db.execute('INSERT INTO performance_plans VALUES(?,?,?,?,1)',(dep,year,row['indicator'],dumps(definition)));added+=1
        if not added:fail('복사할 연도의 설정이 없습니다.')
        m.audit(db,user['employee_id'],'평가연도 기준 복사',dep,year,{'source_year':source},{'count':added},why)
        return {'ok':True,'count':added}
    if path=='/api/performance/year':
        m.require_manager(user,dep);old=db.execute('SELECT * FROM performance_years WHERE department=? AND year=?',(dep,year)).fetchone();old=dict(old) if old else {'closed':0,'revision':0}
        if data.get('revision')!=old['revision']:fail('연도 상태가 변경되었습니다.',409)
        closed=data.get('closed')
        if not isinstance(closed,bool):fail('마감 상태를 확인해 주세요.')
        if closed==bool(old['closed']):fail('이미 같은 연도 마감 상태입니다.',409)
        report=annual(db,user,dep,year,catalog,available) if closed else None
        if closed and not report['ready']:fail('모든 지표를 설정하고 미확정 실적·등급·취합 기준을 확인해 주세요.',409)
        db.execute('INSERT INTO performance_years VALUES(?,?,?,?,?) ON CONFLICT(department,year) DO UPDATE SET closed=excluded.closed,revision=excluded.revision,report=excluded.report',(dep,year,int(closed),old['revision']+1,dumps(report) if report else None))
        m.audit(db,user['employee_id'],'연도 확정 보관' if closed else '연도 마감 해제',dep,year,old,{'closed':closed},why);return {'ok':True}
    item=indicator_for(db,dep,data.get('indicator_id'),available);key=item['id'];plan=get_plan(db,dep,year,item)
    if path=='/api/performance/plan':
        m.require_manager(user,dep)
        if data.get('revision')!=plan['revision']:fail('지표 설정이 변경되었습니다. 다시 조회해 주세요.',409)
        definition=validate_plan(db,dep,year,item,data.get('definition',{}))
        legacy=db.execute('SELECT month FROM monthly_actuals WHERE department_id=? AND year=? AND indicator_id=? ORDER BY month DESC LIMIT 1',(dep,year,key)).fetchone()
        if legacy and day(definition['start'])<=date(year,legacy['month'],calendar.monthrange(year,legacy['month'])[1]):fail('이전 월별 실적이 있습니다. 마지막 기록 월의 다음 달부터 새 관리기간을 시작해 주세요.',409)
        db.execute('INSERT INTO performance_plans VALUES(?,?,?,?,?) ON CONFLICT(department,year,indicator) DO UPDATE SET definition=excluded.definition,revision=excluded.revision',(dep,year,key,dumps(definition),plan['revision']+1))
        m.audit(db,user['employee_id'],'지표 관리주기·산식 설정',dep,f'{year}:{key}',plan,definition,why);return {'ok':True}
    if not plan['configured']:fail('관리자가 먼저 해당 지표의 관리주기·세부항목을 설정해야 합니다.',409)
    if path=='/api/performance/preview':
        source=plan
        if data.get('record_id'):
            record=record_access(db,user,data['record_id'],catalog)
            if record['department']!=dep or record['year']!=year or record['indicator']!=key:fail('실적과 지표가 일치하지 않습니다.')
            if not data.get('rebase'):source=json.loads(record['definition'])
        return {'calculation':calculate(source,data.get('values',{}))}
    if path=='/api/performance/grade':
        if plan['mode']!='text':fail('등급 입력은 비계량 지표에만 적용합니다.')
        row=db.execute('SELECT * FROM performance_ratings WHERE department=? AND year=? AND indicator=?',(dep,year,key)).fetchone()
        if not row or row['evaluator']!=user['employee_id']:fail('지정된 평가자만 등급을 입력할 수 있습니다.',403)
        if data.get('revision')!=row['revision']:fail('평가가 변경되었습니다.',409)
        grade=data.get('grade')
        if grade not in ('S','A','B','C','D'):fail('평가등급을 확인해 주세요.')
        db.execute('UPDATE performance_ratings SET grade=?,reason=?,revision=revision+1 WHERE department=? AND year=? AND indicator=?',(grade,why,dep,year,key))
        m.audit(db,user['employee_id'],'지정 평가자 등급 입력',dep,f'{year}:{key}',dict(row),{'grade':grade},why);return {'ok':True}
    if path=='/api/performance/rating':
        m.require_manager(user,dep)
        row=db.execute('SELECT * FROM performance_ratings WHERE department=? AND year=? AND indicator=?',(dep,year,key)).fetchone();before=dict(row) if row else {'revision':0}
        if data.get('revision')!=before['revision']:fail('평가가 변경되었습니다.',409)
        evaluator=data.get('evaluator','');person=db.execute("SELECT * FROM users WHERE employee_id=? AND account_status='active'",(evaluator,)).fetchone()
        if not person or person['department_id']!=dep:fail('평가자를 해당 근무지의 활성 계정에서 선택해 주세요.')
        grade=data.get('grade') or None
        if grade not in (None,'S','A','B','C','D'):fail('평가등급을 확인해 주세요.')
        if grade and plan['mode']!='text':fail('등급 입력은 비계량 지표에만 적용합니다.')
        if grade and (grade!=before.get('grade') or evaluator!=before.get('evaluator')) and evaluator!=user['employee_id']:fail('등급은 지정된 평가자 본인이 입력해야 합니다.',403)
        adjustment=m.number(data.get('adjustment','0'));excluded=data.get('excluded',False)
        if not isinstance(excluded,bool) or abs(dec(adjustment))>dec(plan['weight']):fail('가감점·결측 여부를 확인해 주세요.')
        db.execute('INSERT INTO performance_ratings VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(department,year,indicator) DO UPDATE SET evaluator=excluded.evaluator,grade=excluded.grade,adjustment=excluded.adjustment,excluded=excluded.excluded,reason=excluded.reason,revision=excluded.revision',(dep,year,key,evaluator,grade,adjustment,int(excluded),why,before['revision']+1))
        m.audit(db,user['employee_id'],'평가등급·가감점·결측 처리',dep,f'{year}:{key}',before,{'evaluator':evaluator,'grade':grade,'adjustment':adjustment,'excluded':excluded},why);return {'ok':True}
    p=period(plan,data.get('date'))
    if path=='/api/performance/lock':
        m.require_manager(user,dep);old=lock_state(db,dep,year,key,p)
        if data.get('revision')!=old['revision']:fail('마감 상태가 변경되었습니다.',409)
        closed=data.get('closed')
        if not isinstance(closed,bool):fail('마감 상태를 확인해 주세요.')
        if closed:
            rows=list(db.execute('SELECT state,employee FROM performance_records WHERE department=? AND year=? AND indicator=? AND start=? AND end=?',(dep,year,key,p['start'],p['end'])))
            expected={person['employee_id'] for person in db.execute("SELECT employee_id,indicators FROM users WHERE department_id=? AND account_status='active'",(dep,)) if key in json.loads(person['indicators'])}
            if not rows or any(r['state']!='approved' for r in rows) or not expected.issubset({r['employee'] for r in rows}):fail('모든 현재 담당자의 실적을 확인·확정한 후 마감해 주세요.',409)
        db.execute('INSERT INTO performance_locks VALUES(?,?,?,?,?,?,?) ON CONFLICT(department,year,indicator,start,end) DO UPDATE SET closed=excluded.closed,revision=excluded.revision',(dep,year,key,p['start'],p['end'],int(closed),old['revision']+1))
        m.audit(db,user['employee_id'],'지표 기간 마감' if closed else '지표 기간 마감 해제',dep,f"{year}:{key}:{p['start']}",old,{'closed':closed},why);return {'ok':True}
    owner=data.get('employee_id',user['employee_id'])
    if not manager(user) and owner!=user['employee_id']:fail('본인 실적만 입력할 수 있습니다.',403)
    person=db.execute('SELECT * FROM users WHERE employee_id=?',(owner,)).fetchone()
    if not person:fail('담당자를 확인해 주세요.')
    r=db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? AND employee=? AND start=? AND end=?',(dep,year,key,owner,p['start'],p['end'])).fetchone()
    before=dict(r) if r else None
    if data.get('revision')!=(r['revision'] if r else 0):fail('실적이 변경되었습니다. 다시 조회해 주세요.',409)
    if lock_state(db,dep,year,key,p)['closed']:fail('마감된 기간입니다.',409)
    if path=='/api/performance/record':
        if (not r or not manager(user)) and (person['department_id']!=dep or key not in json.loads(person['indicators']) or person['account_status']!='active'):fail('현재 소속의 활성 담당자만 새 실적을 입력할 수 있습니다.',403)
        if r:record_access(db,user,r['id'],catalog,True)
        definition=plan if not r or data.get('rebase') is True else json.loads(r['definition'])
        values=data.get('values',{});calculate(definition,values)
        narrative=text(data.get('narrative',''),10000);future=text(data.get('plan',''),5000);status=data.get('status')
        if status not in progress.STATUSES:fail('진행상태를 확인해 주세요.')
        db.execute('INSERT INTO performance_records(department,year,indicator,employee,start,end,cadence,definition,values_json,narrative,plan,status,state,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(department,year,indicator,employee,start,end) DO UPDATE SET definition=excluded.definition,values_json=excluded.values_json,narrative=excluded.narrative,plan=excluded.plan,status=excluded.status,state=\'draft\',revision=performance_records.revision+1,updated_at=excluded.updated_at',(dep,year,key,owner,p['start'],p['end'],p['cadence'],dumps(definition),dumps(values),narrative,future,status,'draft',m.stamp()))
    elif path=='/api/performance/transition':
        if not r:fail('먼저 실적을 저장해 주세요.',409)
        action=data.get('action');state=r['state']
        transitions={'submit':(('draft','returned'),'submitted'),'submit_on_behalf':(('draft','returned'),'submitted'),'approve':(('submitted',),'approved'),'return':(('submitted','approved'),'returned')}
        if action not in transitions or state not in transitions[action][0]:fail('현재 제출상태에서는 이 작업을 할 수 없습니다.',409)
        if action!='submit':m.require_manager(user,dep)
        elif r['employee']!=user['employee_id']:fail('실적 제출은 작성자 본인이 수행합니다.',403)
        definition=json.loads(r['definition'])
        if action in ('submit','submit_on_behalf'):
            if day(r['start'])>datetime.now(timezone(timedelta(hours=9))).date():fail('아직 시작하지 않은 기간은 제출할 수 없습니다.')
            calculate(definition,json.loads(r['values_json']),True)
            if definition['mode']=='text' and not r['narrative'].strip():fail('보고서 내용을 입력한 후 제출해 주세요.')
        if action=='return' and plan['mode']=='text':
            existing_rating=db.execute('SELECT * FROM performance_ratings WHERE department=? AND year=? AND indicator=?',(dep,year,key)).fetchone()
            if existing_rating and existing_rating['grade']:
                db.execute('UPDATE performance_ratings SET grade=NULL,revision=revision+1 WHERE department=? AND year=? AND indicator=?',(dep,year,key))
                m.audit(db,user['employee_id'],'보고서 반려로 등급 재평가 요청',dep,f'{year}:{key}',dict(existing_rating),{'grade':None},why)
        db.execute('UPDATE performance_records SET state=?,feedback=?,revision=revision+1,updated_at=? WHERE id=?',(transitions[action][1],why,m.stamp(),r['id']))
    else:fail('요청을 찾을 수 없습니다.',404)
    after=dict(db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? AND employee=? AND start=? AND end=?',(dep,year,key,owner,p['start'],p['end'])).fetchone())
    m.audit(db,user['employee_id'],'기간별 실적 '+('저장' if path.endswith('/record') else data['action']),dep,after['id'],before,after,why)
    return {'ok':True,'record':public_record(db,after)}


def account(db,user,data,catalog):
    import app,hmac
    action=data.get('action');why=m.reason(data);target=data.get('employee_id',user['employee_id'])
    row=db.execute('SELECT * FROM users WHERE employee_id=?',(target,)).fetchone()
    if not row:fail('직원을 찾을 수 없습니다.')
    if action=='password' and target==user['employee_id']:
        old=data.get('old_password','')
        if not isinstance(old,str) or not hmac.compare_digest(app.password_hash(old,row['password_hash'].split(':')[0]),row['password_hash']):fail('현재 비밀번호를 확인해 주세요.',403)
    else:
        if not manager(user):fail('관리자만 계정을 관리할 수 있습니다.',403)
        if row['role']!='employee' and user['role']!='superadmin':fail('관리자 계정은 수퍼관리자만 관리할 수 있습니다. 본인 비밀번호 변경은 현재 비밀번호를 확인해 주세요.',403)
        if user['role']!='superadmin' and row['department_id']!=user['admin_department_id']:fail('관리 부서 직원만 관리할 수 있습니다.',403)
    before={k:row[k] for k in ('department_id','indicators','account_status')}
    if action in ('password','reset-password'):
        password=data.get('password')
        if not isinstance(password,str) or not 10<=len(password)<=128:fail('새 비밀번호는 10~128자로 입력해 주세요.')
        db.execute('UPDATE users SET password_hash=? WHERE employee_id=?',(app.password_hash(password),target))
        after={'password_changed':True} # Never audit passwords or hashes.
    elif action=='status':
        status=data.get('status')
        if status not in ('active','inactive'):fail('계정 상태를 확인해 주세요.')
        if status=='inactive' and row['role']=='superadmin' and db.execute("SELECT COUNT(*) FROM users WHERE role='superadmin' AND account_status='active'").fetchone()[0]<=1:fail('마지막 활성 수퍼관리자는 비활성화할 수 없습니다.',409)
        if target==user['employee_id'] and status=='inactive':fail('본인 계정은 비활성화할 수 없습니다.',409)
        db.execute('UPDATE users SET account_status=? WHERE employee_id=?',(status,target));after={'account_status':status}
    elif action=='transfer':
        m.require_super(user)
        # app.selection reads app's catalog and validates IDs with current effective definitions.
        dep,ids=app.selection(data)
        if row['role']=='superadmin' and not m.can_be_superadmin(dep,catalog):fail('수퍼관리자 소속 부서를 확인해 주세요.')
        db.execute('UPDATE users SET department_id=?,indicators=?,revision=revision+1 WHERE employee_id=?',(dep,dumps(ids),target));after={'department_id':dep,'indicators':ids}
        db.execute('INSERT INTO performance_transfers(employee,from_department,to_department,before_json,after_json,reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(target,row['department_id'],dep,dumps(before),dumps(after),why,user['employee_id'],m.stamp()))
        # Historical ownership and managerial scope are not reassigned automatically.
    elif action=='handover':
        successor=db.execute("SELECT * FROM users WHERE employee_id=? AND account_status='active'",(data.get('successor'),)).fetchone()
        if not successor or successor['department_id']!=row['department_id']:fail('같은 근무지의 활성 후임자를 선택해 주세요.')
        keys=data.get('indicators')
        if not isinstance(keys,list) or not keys or set(keys)-set(json.loads(row['indicators'])):fail('기존 담당지표에서 인계 항목을 선택해 주세요.')
        assigned=sorted(set(json.loads(successor['indicators']))|set(keys))
        db.execute('UPDATE users SET indicators=?,revision=revision+1 WHERE employee_id=?',(dumps(assigned),successor['employee_id']))
        remaining=[k for k in json.loads(row['indicators']) if k not in keys]
        db.execute('UPDATE users SET indicators=?,revision=revision+1 WHERE employee_id=?',(dumps(remaining),target))
        for p in db.execute('SELECT * FROM performance_plans WHERE department=?',(row['department_id'],)).fetchall():
            definition=json.loads(p['definition'])
            if p['indicator'] in keys and definition['representative']==target:
                writable(db,p['department'],p['year']);definition['representative']=successor['employee_id']
                db.execute('UPDATE performance_plans SET definition=?,revision=revision+1 WHERE department=? AND year=? AND indicator=?',(dumps(definition),p['department'],p['year'],p['indicator']))
        after={'successor':successor['employee_id'],'indicators':keys,'historical_records':'original owner preserved'}
        db.execute('INSERT INTO performance_transfers(employee,from_department,to_department,before_json,after_json,reason,actor,created_at) VALUES(?,?,?,?,?,?,?,?)',(target,row['department_id'],row['department_id'],dumps(before),dumps(after),why,user['employee_id'],m.stamp()))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(successor['employee_id'],))
    else:fail('계정 작업을 확인해 주세요.')
    db.execute('DELETE FROM sessions WHERE employee_id=?',(target,))
    m.audit(db,user['employee_id'],'계정 '+action,row['department_id'],target,before,after,why)
    return {'ok':True}


def expected_percent(plan,as_of):
    a,b=day(plan['start']),day(plan['end']);d=day(as_of)
    if d<a:return Decimal(0)
    if d>=b:return Decimal(100)
    if plan['curve']=='milestones':
        knots=[(a,Decimal(0))]+[(day(r['date']),dec(r['percent'])) for r in plan['milestones']]
        for i in range(1,len(knots)):
            left,right=knots[i-1],knots[i]
            if d==right[0]:return right[1]
            if d<right[0]:return left[1]+(right[1]-left[1])*Decimal((d-left[0]).days)/max(1,(right[0]-left[0]).days)
    fraction=Decimal((d-a).days+1)/Decimal((b-a).days+1)
    if plan['curve']=='late':return fraction*10 if fraction<Decimal('.5') else 5+(fraction-Decimal('.5'))*190
    return fraction*100


def monitoring(plan,records,as_of):
    pace=expected_percent(plan,as_of);latest=max((r for r in records if r['start']<=as_of),key=lambda r:(r['end'],r['updated_at'],r['id']),default=None)
    code='pending';reason='지표 관리기준을 설정해 주세요.'
    if plan['configured']:
        if as_of<plan['start']:reason='관리기간 시작 전입니다.'
        elif latest is None:code='risk' if as_of>=plan['end'] else 'warning';reason='아직 실적이 입력되지 않았습니다.'
        elif plan['mode']=='text':
            code='good' if latest['status']=='완료' else 'risk' if as_of>=plan['end'] else 'warning';reason='서술형은 진행상태와 제출 마감으로 확인합니다.'
        else:
            rate=calculate(json.loads(latest['definition']),json.loads(latest['values_json']))['percent']
            if rate is None:reason='세부 실적 입력을 완료해 주세요.'
            elif dec(rate)>=100 or dec(rate)>=pace:code='good';reason='현재 계획 이상으로 진행하고 있습니다.'
            elif as_of>=plan['end'] or dec(rate)<pace*Decimal('.8'):code='risk';reason='현재 계획의 80% 미만이거나 마감 목표에 미달합니다.'
            else:code='warning';reason='현재 계획보다 지연되고 있습니다.'
    return {'code':code,'label':{'pending':'판정 대기','good':'양호','warning':'경고','risk':'위험'}[code],'reason':reason,'expected_percent':fmt(pace)}


def dashboard_item(db,user,year,month,indicator,as_of):
    """Adapt structured records to the existing monthly dashboard without duplicate totals."""
    import dashboard
    plan=get_plan(db,user['department_id'],year,indicator)
    if not plan['configured']:return None
    records=[dict(r) for r in db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? AND employee=? AND start<=? ORDER BY end,updated_at,id',(user['department_id'],year,indicator['id'],user['employee_id'],as_of.isoformat()))]
    latest=records[-1] if records else None
    history=[]
    for mm in range(1,12):
        matching=[r for r in records if day(r['end']).month==mm];r=matching[-1] if matching else None
        calculation=calculate(json.loads(r['definition']),json.loads(r['values_json'])) if r else {}
        history.append({'month':mm,'date':min(dashboard.month_end(year,mm),as_of).isoformat() if mm<=month else dashboard.month_end(year,mm).isoformat(),
                        'present':bool(r),'actual':calculation.get('score'),'percent':calculation.get('percent'),'status':r['status'] if r else None,
                        'target':json.loads(r['definition'])['weight'] if r else None,'unit':'점','narrative':r['narrative'][:280] if r else ''})
    calculation=calculate(json.loads(latest['definition']),json.loads(latest['values_json'])) if latest else {}
    try:window=period(plan,as_of.isoformat())
    except m.APIError:window=None
    current=next((r for r in reversed(records) if window and r['start']==window['start'] and r['end']==window['end']),None)
    filled=bool(current and (current['narrative'].strip() if plan['mode']=='text' else calculate(json.loads(current['definition']),json.loads(current['values_json']))['score'] is not None))
    input_state='입력완료' if filled else '임시저장' if current else '미입력';health=monitoring(plan,records,as_of.isoformat())
    settings={'target':plan['weight'] if plan['mode']=='numeric' else None,'weight':plan['weight'],'unit':'점','entry_mode':plan['mode'],'direction':'higher','dashboard_curve':plan['curve']}
    files=db.execute('SELECT COUNT(*) FROM performance_files WHERE record_id=? AND deleted_at IS NULL',(latest['id'],)).fetchone()[0] if latest else 0
    return {'indicator':indicator,'settings':settings,'health':health,'achievement_percent':calculation.get('percent'),'expected_percent':health['expected_percent'],
            'curve_label':{'linear':'균등 진행','late':'연말 집중','milestones':'중간목표'}[plan['curve']],
            'actual':calculation.get('score'),'source_month':day(latest['end']).month if latest else None,'status':latest['status'] if latest else '미착수',
            'input_state':input_state,'current_month_missing':not filled,'future_plan':latest['plan'][:500] if latest else '',
            'narrative':latest['narrative'][:280] if latest else '', 'updated_at':latest['updated_at'] if latest else None,'evidence_count':files,'history':history,
            'planned':[{'month':mm,'date':dashboard.month_end(year,mm).isoformat(),'percent':fmt(expected_percent(plan,dashboard.month_end(year,mm).isoformat()))} for mm in range(1,12)],
            'graph_mode':'status' if plan['mode']=='text' else 'percent','source':'periodic','workflow_state':latest['state'] if latest else None}
