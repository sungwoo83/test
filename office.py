"""Confirmed assignments and immutable workplace submissions for paper verification."""
import json
import hashlib
from datetime import date
import management as m
import performance as p
import progress


def initialize(db):
    cols={r['name'] for r in db.execute('PRAGMA table_info(users)')}
    if 'assignment_confirmed' not in cols:db.execute('ALTER TABLE users ADD COLUMN assignment_confirmed INTEGER NOT NULL DEFAULT 1')
    db.executescript('''CREATE TABLE IF NOT EXISTS evidence_recipient_rules(indicator TEXT PRIMARY KEY,recipients TEXT NOT NULL,revision INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS office_submissions(id INTEGER PRIMARY KEY,department TEXT NOT NULL,year INTEGER NOT NULL,as_of TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,snapshot TEXT NOT NULL,sha256 TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(department,year,as_of,version));
    CREATE UNIQUE INDEX IF NOT EXISTS office_submission_active ON office_submissions(department,year,as_of) WHERE state='submitted';
    CREATE TABLE IF NOT EXISTS office_submission_sources(submission_id INTEGER REFERENCES office_submissions(id),kind TEXT,record_id INTEGER,record_revision INTEGER,PRIMARY KEY(submission_id,kind,record_id));''')


def roster(db, dep):
    return [dict(r) for r in db.execute('SELECT employee_id,display_name,role,indicators,assignment_confirmed,revision,account_status FROM users WHERE department_id=? ORDER BY employee_id',(dep,))]


def roster_version(db, dep):
    return hashlib.sha256(p.dumps(roster(db,dep)).encode()).hexdigest()


def owners(db, dep, key, people=None):
    # Inactive accounts keep their reservation until an explicit reassignment.
    return [r for r in (roster(db,dep) if people is None else people) if r['assignment_confirmed'] and key in json.loads(r['indicators'])]


def available_selection(db, dep, keys, employee=None, releasing=None):
    people=roster(db,dep)
    for key in keys:
        if any(r['employee_id'] not in (employee,releasing) for r in owners(db,dep,key,people)):
            p.fail('다른 직원에게 배정 확정된 지표입니다. 다시 조회하거나 관리자에게 담당자 조정을 요청해 주세요.',409)


def options(db,user,dep,catalog,available):
    if not any(d['id']==dep for d in catalog['departments']):p.fail('근무지를 선택해 주세요.')
    privileged=user and (user['role']=='superadmin' or (user['role']=='admin' and user['admin_department_id']==dep))
    result=[];staff=roster(db,dep)
    for k in available(dep,db):
        people=owners(db,dep,k['id'],staff)
        item={'indicator_id':k['id'],'taken':bool(people),'own':bool(user and len(people)==1 and people[0]['employee_id']==user['employee_id']),'conflict':len(people)>1}
        if privileged:item['owners']=[{'employee_id':r['employee_id'],'display_name':r['display_name']} for r in people]
        result.append(item)
    return {'items':result,**({'revision':roster_version(db,dep)} if privileged else {})}


def reassign(db,user,data,available):
    dep=data.get('department_id');m.require_manager(user,dep)
    key=data.get('indicator_id');p.indicator_for(db,dep,key,available)
    if data.get('roster_revision')!=roster_version(db,dep):p.fail('담당자 배정이 변경되었습니다. 다시 조회해 주세요.',409)
    target=data.get('employee_id') or None
    people=roster(db,dep);successor=next((r for r in people if r['employee_id']==target),None)
    if target and (not successor or successor['account_status']!='active'):p.fail('같은 근무지의 활성 직원을 선택해 주세요.')
    affected=[r for r in people if key in json.loads(r['indicators']) or r['employee_id']==target]
    if user['role']!='superadmin' and any(r['role']=='superadmin' for r in affected):p.fail('수퍼관리자 담당지표는 수퍼관리자만 조정할 수 있습니다.',403)
    before=[{'employee_id':r['employee_id'],'indicators':json.loads(r['indicators']),'confirmed':r['assignment_confirmed']} for r in affected]
    previous=[r['employee_id'] for r in owners(db,dep,key)]
    for person in affected:
        keys=set(json.loads(person['indicators']));keys.discard(key)
        confirmed=person['assignment_confirmed']
        if person['employee_id']==target:
            # A reassignment confirms only this key for a previously pending account.
            if not confirmed:keys=set()
            keys.add(key);confirmed=1
        db.execute('UPDATE users SET indicators=?,assignment_confirmed=?,revision=revision+1 WHERE employee_id=?',(p.dumps(sorted(keys)),confirmed,person['employee_id']))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(person['employee_id'],))
    if target:
        for plan in db.execute('SELECT * FROM performance_plans WHERE department=? AND indicator=?',(dep,key)).fetchall():
            state=db.execute('SELECT closed FROM performance_years WHERE department=? AND year=?',(dep,plan['year'])).fetchone()
            definition=json.loads(plan['definition'])
            if (not state or not state['closed']) and definition.get('representative') in previous:
                definition['representative']=target
                db.execute('UPDATE performance_plans SET definition=?,revision=revision+1 WHERE department=? AND year=? AND indicator=?',(p.dumps(definition),dep,plan['year'],key))
    m.audit(db,user['employee_id'],'지표 담당자 조정',dep,key,before,{'employee_id':target,'historical_records':'original owner preserved'},m.reason(data))
    return {'ok':True}


def assignment(db,user,dep,key):
    if user['department_id']!=dep or key not in json.loads(user['indicators']) or not user['assignment_confirmed']:
        p.fail('관리자가 담당지표 배정을 확정한 후 입력·제출할 수 있습니다.',403)
    available_selection(db,dep,[key],user['employee_id'])


def guard(db,kind,record_id):
    if db.execute("SELECT 1 FROM office_submission_sources x JOIN office_submissions s ON s.id=x.submission_id WHERE x.kind=? AND x.record_id=? AND s.state='submitted'",(kind,record_id)).fetchone():
        p.fail('근무지 공식 제출본에 포함된 자료입니다. 제출을 취소한 후 수정해 주세요.',409)


def guard_period(db,dep,year,start):
    if db.execute("SELECT 1 FROM office_submissions WHERE department=? AND year=? AND as_of>=? AND state='submitted'",(dep,year,start)).fetchone():p.fail('해당 기간의 근무지 공식 제출본이 있습니다. 제출 취소 후 입력해 주세요.',409)


def preview(db,user,params,catalog,available):
    dep,year=p.scope(db,user,params,catalog);m.require_manager(user,dep);as_of=params.get('date')
    if p.day(as_of).year!=year:p.fail('집계 기준일의 연도를 확인해 주세요.')
    items=[];ready=True;sources=[]
    for indicator in available(dep,db):
        definition=p.get_plan(db,dep,year,indicator)
        records=[dict(r) for r in db.execute('SELECT * FROM performance_records WHERE department=? AND year=? AND indicator=? AND start<=? ORDER BY end,id',(dep,year,indicator['id'],as_of))]
        raw_legacy=[dict(r) for r in db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND year=? AND indicator_id=? ORDER BY month,id',(dep,year,indicator['id'])) if date(year,r['month'],1)<=p.day(as_of)]
        result=p.aggregate(definition,records) if definition['configured'] else {'ready':False,'reason':'수퍼관리자 기준 설정 필요','sources':[],'calculation':None}
        if any(r['state']!='approved' for r in records):result={**result,'ready':False,'reason':'임시저장·제출·반려 자료가 남아 있습니다.'}
        expected={r['employee_id'] for r in db.execute("SELECT employee_id,indicators FROM users WHERE department_id=? AND account_status='active' AND assignment_confirmed=1 AND substr(created_at,1,10)<=?",(dep,as_of)) if indicator['id'] in json.loads(r['indicators'])}
        if (not expected and not records) or not expected.issubset({r['employee'] for r in records}):result={**result,'ready':False,'reason':'확정된 담당자 배정 또는 담당자의 실적이 필요합니다.'}
        if not result['ready']:ready=False
        source=[{'id':r['id'],'employee':r['employee'],'start':r['start'],'end':r['end'],'state':r['state'],'revision':r['revision'],'values':json.loads(r['values_json']),'narrative':r['narrative'],'plan':r['plan'],'files':[dict(f) for f in db.execute('SELECT id,original_name,size,sha256,storage_name FROM performance_files WHERE record_id=? AND deleted_at IS NULL',(r['id'],))]} for r in records]
        legacy=[{'id':r['id'],'employee':r['employee_id'],'month':r['month'],'actual':r['actual'],'adjusted':r['adjusted'],'revision':r['revision'],'snapshot':json.loads(r['snapshot']),'files':[dict(f) for f in db.execute('SELECT id,original_name,size,sha256,storage_name FROM evidence_files WHERE record_id=? AND deleted_at IS NULL',(r['id'],))]} for r in raw_legacy]
        sources.extend({'kind':'periodic','id':r['id'],'revision':r['revision']} for r in records)
        sources.extend({'kind':'legacy','id':r['id'],'revision':r['revision']} for r in raw_legacy)
        items.append({'indicator':indicator,'definition':definition,'aggregate':result,'records':source,'legacy':legacy})
    versions=[{k:r[k] for k in ('id','as_of','version','state','sha256','actor','reason','created_at')} for r in db.execute('SELECT * FROM office_submissions WHERE department=? AND year=? ORDER BY as_of DESC,version DESC',(dep,year))]
    latest=max((r['version'] for r in versions if r['as_of']==as_of),default=0)
    return {'department_id':dep,'year':year,'as_of':as_of,'items':items,'ready':bool(items) and ready,'sources':sources,'submissions':versions,'version':latest}


def post(db,user,data,catalog,available):
    action=data.get('action');why=m.reason(data)
    if action=='reassign':return reassign(db,user,data,available)
    if action=='assignment':
        target=db.execute('SELECT * FROM users WHERE employee_id=?',(data.get('employee_id'),)).fetchone()
        if not target:p.fail('직원을 확인해 주세요.')
        dep=target['department_id'];m.require_manager(user,dep)
        if target['role']=='superadmin' and user['role']!='superadmin':p.fail('수퍼관리자 계정의 담당 배정은 수퍼관리자만 변경할 수 있습니다.',403)
        if data.get('revision')!=target['revision']:p.fail('담당지표가 변경되었습니다. 다시 조회해 주세요.',409)
        keys=data.get('indicators');allowed={k['id'] for k in available(dep,db)}
        if not isinstance(keys,list) or any(not isinstance(k,str) or k not in allowed for k in keys):p.fail('해당 근무지의 담당지표를 선택해 주세요.')
        available_selection(db,dep,keys,target['employee_id'])
        before={'indicators':json.loads(target['indicators']),'confirmed':bool(target['assignment_confirmed'])}
        db.execute('UPDATE users SET indicators=?,assignment_confirmed=1,revision=revision+1 WHERE employee_id=?',(p.dumps(sorted(set(keys))),target['employee_id']))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(target['employee_id'],))
        m.audit(db,user['employee_id'],'담당지표 배정 확정',dep,target['employee_id'],before,{'indicators':sorted(set(keys)),'confirmed':True},why)
        return {'ok':True}
    report=preview(db,user,data,catalog,available);dep=report['department_id'];year=report['year']
    if action=='withdraw':
        row=db.execute('SELECT * FROM office_submissions WHERE id=? AND department=? AND year=?',(data.get('submission_id'),dep,year)).fetchone()
        if not row or row['state']!='submitted':p.fail('취소할 제출본을 확인해 주세요.',409)
        db.execute("UPDATE office_submissions SET state='withdrawn' WHERE id=?",(row['id'],))
        m.audit(db,user['employee_id'],'근무지 공식 제출 취소',dep,row['id'],{'sha256':row['sha256']},{'state':'withdrawn'},why);return {'ok':True}
    if action!='submit':p.fail('제출 작업을 확인해 주세요.')
    p.writable(db,dep,year)
    if data.get('version')!=report['version']:p.fail('제출본 버전이 변경되었습니다.',409)
    if not report['ready']:p.fail('모든 지표의 기준과 실적 확정을 확인해 주세요.',409)
    dispositions=data.get('legacy_dispositions',{})
    if not isinstance(dispositions,dict):p.fail('이전 월별 자료 검토 결과를 입력해 주세요.')
    for item in report['items']:
        if not item['legacy']:continue
        key=item['indicator']['id'];decision=dispositions.get(key,{})
        if not isinstance(decision,dict) or decision.get('mode') not in ('included','reference') or not isinstance(decision.get('reason'),str) or not 2<=len(decision['reason'].strip())<=1000:p.fail('이전 월별 자료마다 누계 반영 여부와 근거를 확인해 주세요.')
        if decision['mode']=='included':
            if item['definition']['time_aggregation']!='latest':p.fail('기존 자료의 누계 반영은 최신 누적값 기준에서 확인해 주세요. 증분 합산·평균은 원시값을 기간별로 옮겨 재검토해야 합니다.')
            if decision.get('record_id') not in item['aggregate']['sources']:p.fail('이전 실적을 포함한 확정 누적 기록을 지정해 주세요.')
        item['legacy_review']=decision
    report['legacy_dispositions']=dispositions;report['actor']=user['employee_id'];report['reason']=why
    snapshot=p.dumps(report);digest=hashlib.sha256(snapshot.encode()).hexdigest()
    try:
        cursor=db.execute('INSERT INTO office_submissions(department,year,as_of,version,state,snapshot,sha256,actor,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(dep,year,report['as_of'],report['version']+1,'submitted',snapshot,digest,user['employee_id'],why,m.stamp()))
    except Exception as e:
        import sqlite3
        if isinstance(e,sqlite3.IntegrityError):p.fail('해당 기준일의 공식 제출본이 있습니다. 취소 후 재제출해 주세요.',409)
        raise
    for r in report['sources']:db.execute('INSERT INTO office_submission_sources VALUES(?,?,?,?)',(cursor.lastrowid,r['kind'],r['id'],r['revision']))
    m.audit(db,user['employee_id'],'근무지 공식 실적 제출',dep,cursor.lastrowid,None,{'version':report['version']+1,'sha256':digest,'source_count':len(report['sources'])},why)
    return {'ok':True,'id':cursor.lastrowid,'version':report['version']+1,'sha256':digest}


def snapshot(db,user,params,catalog):
    dep,year=p.scope(db,user,params,catalog);m.require_manager(user,dep)
    row=db.execute('SELECT * FROM office_submissions WHERE id=? AND department=? AND year=?',(p.integer(params.get('submission_id'),1,10**12),dep,year)).fetchone()
    if not row:p.fail('공식 제출본을 찾을 수 없습니다.',404)
    return {**json.loads(row['snapshot']),'submission_id':row['id'],'submission_version':row['version'],'submission_state':row['state'],'sha256':row['sha256']}
