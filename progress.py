"""Department/year indicator settings and progress entry; no scoring engine."""
import json
from decimal import Decimal, ROUND_HALF_UP
import management as m

STATUSES=('미착수','진행중','완료')

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS department_indicator_settings (
        department_id TEXT NOT NULL, indicator_id TEXT NOT NULL, year INTEGER NOT NULL,
        definition TEXT NOT NULL, revision INTEGER NOT NULL,
        PRIMARY KEY(department_id,indicator_id,year))''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(monthly_actuals)')}
    for name,definition in [('status',"TEXT NOT NULL DEFAULT '진행중'"),('future_plan',"TEXT NOT NULL DEFAULT ''"),('narrative',"TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:db.execute(f'ALTER TABLE monthly_actuals ADD COLUMN {name} {definition}')
    # Earlier versions accepted numeric values for every indicator type. Preserve them.
    for row in db.execute('SELECT id,snapshot FROM monthly_actuals').fetchall():
        old=json.loads(row['snapshot'])
        if 'entry_mode' not in old:
            old.update({'entry_mode':'numeric','unit':old.get('unit',''),'weight':old.get('points'),'direction':'higher'})
            db.execute('UPDATE monthly_actuals SET snapshot=? WHERE id=?',(json.dumps(old,ensure_ascii=False),row['id']))

def settings(db,department,year,indicator):
    row=db.execute('SELECT * FROM department_indicator_settings WHERE department_id=? AND indicator_id=? AND year=?',(department,indicator['id'],year)).fetchone()
    base={'target':indicator.get('target'),'weight':indicator.get('points'),'unit':indicator.get('unit',''),
          'entry_mode':'numeric' if indicator['type']=='계량' else 'text','direction':'higher','dashboard_curve':'linear','revision':0}
    if row:base.update(json.loads(row['definition']));base['revision']=row['revision']
    import manual_defaults
    ref=manual_defaults.reference(department,year,indicator)
    if ref:base['manual_reference']=ref
    return base

def snapshot(indicator,config):
    return {**indicator,**{k:v for k,v in config.items() if k!='revision'},'settings_revision':config['revision']}

def achievement(record):
    definition=json.loads(record['snapshot'])
    mode=definition.get('entry_mode','numeric' if definition.get('type')=='계량' else 'text')
    if mode=='text':return {'percent':None,'label':'비계량 · 달성률 미산정'}
    raw=record['adjusted'] if record['adjusted'] is not None else record['actual']
    if raw in (None,''):return {'percent':None,'label':'실적 미입력'}
    target=definition.get('target')
    if target in (None,''):return {'percent':None,'label':'목표치 미설정'}
    actual,t=Decimal(raw),Decimal(str(target))
    if t<=0:return {'percent':None,'label':'목표치 0 이하 · 산정 불가'}
    lower=definition.get('direction')=='lower'
    if lower and actual<=0:return {'percent':None,'label':'실적 0 이하 · 별도 확인'}
    ratio=(t/actual if lower else actual/t)*100
    value=format(ratio.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f')
    return {'percent':value,'label':value+'%','formula':'목표 ÷ 실적 × 100' if lower else '실적 ÷ 목표 × 100'}

def list_attachments(db,record_id):
    return [dict(r) for r in db.execute('SELECT id,record_id,original_name,size,sha256,uploaded_by,created_at FROM evidence_files WHERE record_id=? AND deleted_at IS NULL ORDER BY id',(record_id,))]

def enrich_items(db,department,year,items,records):
    result=[]
    for indicator in items:
        record=records.get(indicator['id'])
        if record:
            record['achievement']=achievement(record)
            record['attachments']=list_attachments(db,record['id'])
        result.append({'indicator':indicator,'settings':settings(db,department,year,indicator),'record':record})
    return result

def save_settings(db,user,data,catalog,available):
    department=data.get('department_id');m.department_scope(user,department,catalog);m.require_super(user)
    year,month=m.period_args(data)
    import performance
    performance.writable(db,department,year)
    if m.period(db,department,year,month)['status']=='closed':raise m.APIError(409,'마감된 월에서는 목표·가중치를 수정할 수 없습니다.')
    indicator=next((k for k in available(department,db) if k['id']==data.get('indicator_id')),None)
    if not indicator:raise m.APIError(400,'해당 부서의 지표를 선택해 주세요.')
    before=settings(db,department,year,indicator)
    if data.get('revision')!=before['revision']:raise m.APIError(409,'목표·가중치가 변경되었습니다. 다시 조회해 주세요.')
    why=m.reason(data)
    unit=data.get('unit','');mode=data.get('entry_mode','numeric');direction=data.get('direction','higher');curve=data.get('dashboard_curve',before.get('dashboard_curve','linear'))
    if not isinstance(unit,str) or len(unit)>30 or mode not in ('numeric','text') or direction not in ('higher','lower') or curve not in ('linear','late'):
        raise m.APIError(400,'단위와 입력 방식을 확인해 주세요.')
    target=m.number(data.get('target'),True);weight=m.number(data.get('weight'),True)
    if target is not None and Decimal(target)<0:raise m.APIError(400,'목표치는 0 이상으로 입력해 주세요.')
    if weight is not None and Decimal(weight)<0:raise m.APIError(400,'가중치는 0 이상으로 입력해 주세요.')
    definition={'target':target,'weight':weight,'unit':unit.strip(),'entry_mode':mode,'direction':direction,'dashboard_curve':curve}
    # Do not reinterpret already entered text as a number or silently erase numerical data.
    rows=list(db.execute('''SELECT a.* FROM monthly_actuals a LEFT JOIN monthly_periods p
        ON p.department_id=a.department_id AND p.year=a.year AND p.month=a.month
        WHERE a.department_id=? AND a.indicator_id=? AND a.year=? AND COALESCE(p.status,'open')!='closed' ''',(department,indicator['id'],year)))
    import office
    for row in rows:
        office.guard(db,'legacy',row['id'])
        old=json.loads(row['snapshot']);old_mode=old.get('entry_mode','numeric' if old.get('type')=='계량' else 'text')
        if mode!=old_mode and (row['actual'] not in ('',None) or row['narrative'] or row['adjusted'] is not None):
            raise m.APIError(409,'실적이 있는 지표의 수치/서술 입력 방식은 변경할 수 없습니다.')
    rev=before['revision']+1
    db.execute('INSERT INTO department_indicator_settings VALUES(?,?,?,?,?) ON CONFLICT(department_id,indicator_id,year) DO UPDATE SET definition=excluded.definition,revision=excluded.revision',(department,indicator['id'],year,json.dumps(definition,ensure_ascii=False),rev))
    for row in rows:
        previous=json.loads(row['snapshot']);updated=snapshot(previous,{**definition,'revision':rev})
        db.execute('UPDATE monthly_actuals SET snapshot=?,revision=revision+1 WHERE id=?',(json.dumps(updated,ensure_ascii=False),row['id']))
        m.audit(db,user['employee_id'],'미마감 실적 목표 갱신',department,row['id'],{'snapshot':previous},{'snapshot':updated},why)
    after={**definition,'revision':rev,'updated_open_records':len(rows)}
    m.audit(db,user['employee_id'],'목표·가중치 수정',department,f"{year}:{indicator['id']}",before,after,why)
    return {'ok':True,'updated_open_records':len(rows)}

def save_actual(db,user,data,indicator,row,year,month,department):
    actor=user['employee_id'];why=m.reason(data)
    if user['role']=='employee' and indicator['id'] not in json.loads(user['indicators']):raise m.APIError(403,'담당지표만 입력할 수 있습니다.')
    if row and row['adjusted'] is not None and user['role']=='employee':raise m.APIError(409,'관리자가 조정을 해제한 후 입력해 주세요.')
    config=settings(db,department,year,indicator)
    status=data.get('status',row['status'] if row else '진행중')
    plan=data.get('future_plan',row['future_plan'] if row else '')
    narrative=data.get('narrative',row['narrative'] if row else '')
    if status not in STATUSES or not isinstance(plan,str) or len(plan)>5000 or not isinstance(narrative,str) or len(narrative)>10000:
        raise m.APIError(400,'진행상태·향후계획·실적 내용을 확인해 주세요.')
    if config['entry_mode']=='text':
        value=''
        if status=='완료' and not narrative.strip():raise m.APIError(400,'완료 상태에는 실적 내용을 입력해 주세요.')
    else:
        raw=data.get('value')
        value='' if raw in (None,'') else m.number(raw)
        if status=='완료' and value=='':raise m.APIError(400,'완료 상태에는 수치 실적을 입력해 주세요.')
    definition=snapshot(indicator,config)
    db.execute('''INSERT INTO monthly_actuals(department_id,indicator_id,year,month,actual,snapshot,revision,updated_by,updated_at,status,future_plan,narrative,employee_id)
        VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?) ON CONFLICT(department_id,indicator_id,year,month,employee_id) DO UPDATE SET
        actual=excluded.actual,revision=monthly_actuals.revision+1,updated_by=excluded.updated_by,updated_at=excluded.updated_at,
        status=excluded.status,future_plan=excluded.future_plan,narrative=excluded.narrative,adjusted=NULL,adjustment_reason=NULL''',
        (department,indicator['id'],year,month,value,json.dumps(definition,ensure_ascii=False),actor,m.stamp(),status,plan,narrative,data.get('employee_id',actor)))
