"""Server-side roles, monthly actuals and audited indicator definitions."""
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status

def stamp():
    return datetime.now(timezone.utc).isoformat()

def initialize(db):
    columns = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
    for name, declaration in [('role', "TEXT NOT NULL DEFAULT 'employee'"), ('admin_department_id', 'TEXT'), ('display_name', "TEXT NOT NULL DEFAULT ''")]:
        if name not in columns:
            db.execute(f'ALTER TABLE users ADD COLUMN {name} {declaration}')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS indicator_definitions (
      indicator_id TEXT PRIMARY KEY, definition TEXT NOT NULL, revision INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS monthly_periods (
      department_id TEXT NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
      status TEXT NOT NULL, revision INTEGER NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
      PRIMARY KEY(department_id,year,month));
    CREATE TABLE IF NOT EXISTS monthly_actuals (
      id INTEGER PRIMARY KEY, department_id TEXT NOT NULL, indicator_id TEXT NOT NULL,
      year INTEGER NOT NULL, month INTEGER NOT NULL, actual TEXT NOT NULL, adjusted TEXT,
      adjustment_reason TEXT, snapshot TEXT NOT NULL, revision INTEGER NOT NULL,
      updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(department_id,indicator_id,year,month));
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY, action TEXT NOT NULL, actor TEXT NOT NULL, department_id TEXT,
      target TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL, reason TEXT NOT NULL,
      created_at TEXT NOT NULL);
    ''')
    import progress, evidence
    progress.initialize(db)
    evidence.initialize(db)
    import submissions
    submissions.migrate(db)
    import indicator_bulk
    indicator_bulk.initialize(db)

def audit(db, actor, action, department, target, before, after, reason):
    db.execute('INSERT INTO audit_log(action,actor,department_id,target,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (action, actor, department, str(target), json.dumps(before,ensure_ascii=False) if before is not None else None,
         json.dumps(after,ensure_ascii=False), reason, stamp()))

def effective_catalog(db, base):
    overrides = {r['indicator_id']: (json.loads(r['definition']),r['revision']) for r in db.execute('SELECT * FROM indicator_definitions')}
    items=[]
    for item in base['indicators']:
        definition, revision = overrides.get(item['id'], ({},0))
        items.append({**item, **definition, 'revision':revision})
    return {**base,'indicators':items}

def can_be_superadmin(department_id, catalog):
    return any(d['id'] == department_id and d['name'] in ('성과혁신실', 'AI정보융합실')
               for d in catalog['departments'])

def require_super(user):
    if user['role'] != 'superadmin':
        raise APIError(403,'수퍼관리자만 사용할 수 있습니다.')

def require_manager(user, department):
    if user['role'] == 'superadmin': return
    if user['role'] != 'admin' or user['admin_department_id'] != department:
        raise APIError(403,'지정된 관리 부서에서만 처리할 수 있습니다.')

def department_scope(user, department, catalog):
    if not any(d['id']==department and d['kind']!='운영' for d in catalog['departments']):
        raise APIError(400,'평가대상 부서를 선택해 주세요.')
    if user['role']=='superadmin': return
    allowed = user['admin_department_id'] if user['role']=='admin' else user['department_id']
    if department != allowed:
        raise APIError(403,'접근할 수 없는 부서입니다.')

def period_args(data):
    try:
        y,m=int(data.get('year','')),int(data.get('month',''))
        if isinstance(data.get('year'),bool) or isinstance(data.get('month'),bool): raise ValueError()
        if str(y)!=str(data.get('year')) or str(m)!=str(data.get('month')): raise ValueError()
        if not 2020 <= y <= 2100 or not 1 <= m <= 12: raise ValueError()
        return y,m
    except (TypeError,ValueError):
        raise APIError(400,'평가연도와 월을 확인해 주세요.')

def reason(data):
    value=data.get('reason','')
    if not isinstance(value,str) or not 2<=len(value.strip())<=1000:
        raise APIError(400,'처리 사유를 2~1,000자로 입력해 주세요.')
    return value.strip()

def number(value, optional=False):
    if optional and (value is None or value==''): return None
    if not isinstance(value,(str,int)) or isinstance(value,bool):
        raise APIError(400,'숫자를 입력해 주세요.')
    try:
        d=Decimal(str(value))
        if not d.is_finite() or abs(d)>Decimal('1000000000000') or d.as_tuple().exponent < -6:
            raise InvalidOperation()
        return format(d,'f')
    except (InvalidOperation,ValueError):
        raise APIError(400,'실적은 소수점 6자리 이내, 절댓값 1조 이하의 숫자로 입력해 주세요.')

def period(db, department, year, month):
    row=db.execute('SELECT * FROM monthly_periods WHERE department_id=? AND year=? AND month=?',(department,year,month)).fetchone()
    return dict(row) if row else {'department_id':department,'year':year,'month':month,'status':'open','revision':0}

def get(db,user,path,params,catalog,available):
    if path=='/api/dashboard':
        import dashboard
        return dashboard.report(db,user,params,catalog,available)
    if path=='/api/review':
        import review
        return review.report(db,user,params,catalog,available)
    if path=='/api/admin/users':
        require_super(user)
        return {'users':[dict(r) for r in db.execute('SELECT employee_id,display_name,department_id,role,admin_department_id FROM users ORDER BY employee_id')]}
    if path=='/api/admin/indicators':
        require_super(user)
        return {'indicators':catalog['indicators']}
    if path=='/api/admin/audit':
        if user['role']=='superadmin':
            rows=db.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 100')
        elif user['role']=='admin':
            rows=db.execute('SELECT * FROM audit_log WHERE department_id=? ORDER BY id DESC LIMIT 100',(user['admin_department_id'],))
        else: raise APIError(403,'관리자만 조회할 수 있습니다.')
        return {'events':[dict(r) for r in rows]}
    if path=='/api/monthly':
        department=params.get('department_id','');department_scope(user,department,catalog)
        y,m=period_args(params)
        items=available(department,db)
        if user['role']=='employee':
            assigned=set(json.loads(user['indicators']));items=[k for k in items if k['id'] in assigned]
        owner=params.get('employee_id',user['employee_id'])
        if user['role']=='employee' and owner!=user['employee_id']:raise APIError(403,'본인의 실적만 조회할 수 있습니다.')
        records={r['indicator_id']:dict(r) for r in db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND year=? AND month=? AND employee_id=?',(department,y,m,owner))}
        import progress
        return {'period':period(db,department,y,m),'items':progress.enrich_items(db,department,y,items,records)}
    raise APIError(404,'요청을 찾을 수 없습니다.')

def indicator_definition(data):
    name,unit,note=data.get('name'),data.get('unit',''),data.get('formula_note','')
    if not isinstance(name,str) or not 1<=len(name.strip())<=200 or not isinstance(unit,str) or len(unit)>30 or not isinstance(note,str) or len(note)>3000:
        raise APIError(400,'지표명·단위·산식 설명의 길이를 확인해 주세요.')
    definition={'name':name.strip(),'unit':unit.strip(),'target':number(data.get('target'),True),'points':number(data.get('points'),True),'formula_note':note}
    if definition['points'] is not None and Decimal(definition['points'])<0:raise APIError(400,'배점은 0 이상이어야 합니다.')
    return definition

def post(db,user,path,data,catalog,available):
    actor=user['employee_id']
    if path=='/api/monthly/settings':require_super(user)
    if path.startswith('/api/monthly/'):
        import performance,calendar
        department=data.get('department_id');year,month=period_args(data);performance.writable(db,department,year)
        key=data.get('indicator_id')
        rows=db.execute('SELECT definition FROM performance_plans WHERE department=? AND year=? AND (? IS NULL OR indicator=?)',(department,year,key,key)).fetchall()
        first=f'{year}-{month:02d}-01';last=f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]:02d}'
        if any(json.loads(r[0])['start']<=last and json.loads(r[0])['end']>=first for r in rows):raise APIError(409,'해당 기간은 통합 성과관리에서 관리합니다. 이전 월별 화면은 조회에 사용해 주세요.')
    if path=='/api/admin/indicators/import-apply':
        import indicator_bulk
        return indicator_bulk.apply(db,user,data,catalog,available)
    if path=='/api/monthly/settings':
        require_super(user)
        import progress
        return progress.save_settings(db,user,data,catalog,available)
    if path=='/api/evidence/delete':
        import evidence
        return evidence.delete(db,user,data.get('file_id'),catalog)
    if path=='/api/admin/role':
        require_super(user);why=reason(data)
        target=db.execute('SELECT * FROM users WHERE employee_id=?',(data.get('employee_id'),)).fetchone()
        role=data.get('role');department=data.get('admin_department_id') if role=='admin' else None
        if not target or role not in ('employee','admin','superadmin'):
            raise APIError(400,'직원과 권한을 확인해 주세요.')
        if role=='admin' and not any(d['id']==department and d['kind']!='운영' for d in catalog['departments']):
            raise APIError(400,'관리할 평가대상 부서를 지정해 주세요.')
        if role=='superadmin' and not can_be_superadmin(target['department_id'], catalog):
            raise APIError(400,'수퍼관리자는 성과혁신실 또는 AI정보융합실 소속 계정에 지정해 주세요.')
        if target['role']=='superadmin' and target['account_status']=='active' and role!='superadmin' and db.execute("SELECT COUNT(*) FROM users WHERE role='superadmin' AND account_status='active'").fetchone()[0]<=1:
            raise APIError(409,'마지막 수퍼관리자는 해제할 수 없습니다.')
        before={k:target[k] for k in ('role','admin_department_id')};after={'role':role,'admin_department_id':department}
        db.execute('UPDATE users SET role=?,admin_department_id=? WHERE employee_id=?',(role,department,target['employee_id']))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(target['employee_id'],))
        audit(db,actor,'권한 변경',None,target['employee_id'],before,after,why)
        return {'ok':True}
    if path=='/api/admin/indicator':
        require_super(user);why=reason(data)
        original=next((k for k in catalog['indicators'] if k['id']==data.get('indicator_id')),None)
        if not original: raise APIError(400,'지표를 확인해 주세요.')
        if data.get('revision')!=original['revision']: raise APIError(409,'지표가 변경되었습니다. 목록을 다시 불러와 주세요.')
        definition=indicator_definition(data)
        rev=original['revision']+1
        db.execute('INSERT INTO indicator_definitions VALUES(?,?,?) ON CONFLICT(indicator_id) DO UPDATE SET definition=excluded.definition,revision=excluded.revision',(original['id'],json.dumps(definition,ensure_ascii=False),rev))
        audit(db,actor,'지표 수정',None,original['id'],original,{**original,**definition,'revision':rev},why)
        return {'ok':True}
    if path in ('/api/monthly/actual','/api/monthly/adjust','/api/monthly/period'):
        department=data.get('department_id');department_scope(user,department,catalog)
        y,m=period_args(data);p=period(db,department,y,m)
        if path=='/api/monthly/period':
            require_manager(user,department);why=reason(data);status=data.get('status')
            if status not in ('open','closed'): raise APIError(400,'마감 상태를 확인해 주세요.')
            if data.get('revision')!=p['revision']: raise APIError(409,'마감 상태가 변경되었습니다. 다시 불러와 주세요.')
            if status==p['status']: raise APIError(409,'이미 동일한 마감 상태입니다.')
            after={**p,'status':status,'revision':p['revision']+1,'updated_by':actor,'updated_at':stamp()}
            db.execute('INSERT INTO monthly_periods VALUES(?,?,?,?,?,?,?) ON CONFLICT(department_id,year,month) DO UPDATE SET status=excluded.status,revision=excluded.revision,updated_by=excluded.updated_by,updated_at=excluded.updated_at',(department,y,m,status,after['revision'],actor,after['updated_at']))
            audit(db,actor,'월 마감' if status=='closed' else '마감 해제',department,f'{y}-{m:02d}',p,after,why)
            return {'ok':True}
        if p['status']=='closed': raise APIError(409,'마감된 월입니다. 관리자의 마감 해제 후 수정할 수 있습니다.')
        indicator=next((k for k in available(department,db) if k['id']==data.get('indicator_id')),None)
        if not indicator: raise APIError(400,'해당 부서의 지표를 선택해 주세요.')
        owner=data.get('employee_id',user['employee_id'])
        import office
        existing=db.execute('SELECT id FROM monthly_actuals WHERE department_id=? AND indicator_id=? AND year=? AND month=? AND employee_id=?',(department,indicator['id'],y,m,owner)).fetchone()
        if existing:office.guard(db,'legacy',existing['id'])
        else:office.guard_period(db,department,y,f'{y}-{m:02d}-01')
        if owner==user['employee_id'] or user['role']=='employee':office.assignment(db,user,department,indicator['id'])
        if not isinstance(owner,str):raise APIError(400,'직원을 선택해 주세요.')
        if user['role']=='employee' and owner!=user['employee_id']:raise APIError(403,'다른 직원의 실적은 수정할 수 없습니다.')
        if path=='/api/monthly/adjust' and 'employee_id' not in data:
            candidates=list(db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND indicator_id=? AND year=? AND month=?',(department,indicator['id'],y,m)))
            if len(candidates)>1:raise APIError(400,'조정할 직원을 선택해 주세요.')
            if candidates:owner=candidates[0]['employee_id']
        row=db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND indicator_id=? AND year=? AND month=? AND employee_id=?',(department,indicator['id'],y,m,owner)).fetchone()
        if user['role']!='employee' and owner!=user['employee_id'] and row is None:
            target=db.execute('SELECT * FROM users WHERE employee_id=? AND department_id=?',(owner,department)).fetchone()
            if not target or indicator['id'] not in json.loads(target['indicators']):raise APIError(400,'해당 직원의 담당지표를 선택해 주세요.')
        before=dict(row) if row else None
        if data.get('revision')!=(row['revision'] if row else 0): raise APIError(409,'실적이 변경되었습니다. 다시 불러와 주세요.')
        why=reason(data)
        if path=='/api/monthly/adjust':
            require_manager(user,department)
            if not row: raise APIError(400,'입력된 실적이 있어야 조정할 수 있습니다.')
            if json.loads(row['snapshot']).get('entry_mode')=='text':
                raise APIError(400,'서술형 실적은 실적 입력 화면에서 내용을 수정해 주세요.')
            value=number(data.get('value'),True)
            db.execute('UPDATE monthly_actuals SET adjusted=?,adjustment_reason=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?',(value,why,actor,stamp(),row['id']))
            action='실적 조정'
        else:
            import progress
            progress.save_actual(db,user,{**data,'employee_id':owner},indicator,row,y,m,department)
            action='실적 입력'
        after=dict(db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND indicator_id=? AND year=? AND month=? AND employee_id=?',(department,indicator['id'],y,m,owner)).fetchone())
        audit(db,actor,action,department,after['id'],before,after,why)
        return {'ok':True,'record_id':after['id'],'revision':after['revision']}
    raise APIError(404,'요청을 찾을 수 없습니다.')
