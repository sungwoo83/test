"""Employee monthly performance monitoring. Not an official evaluation score."""
import calendar
import json
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,ROUND_HALF_UP
import management as m
import progress

CURVES={'linear':'균등 진행','late':'연말 집중'}
LATE=(0,0,0,0,5,10,15,25,40,65,100)


def month_end(year,month):return date(year,month,calendar.monthrange(year,month)[1])

def rounded(value):return format(Decimal(value).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f')

def expected(day,year,curve='linear'):
    start,end=date(year,1,1),date(year,11,30)
    if day<start:return Decimal(0)
    if day>=end:return Decimal(100)
    if curve=='linear':return Decimal((day-start).days+1)*100/Decimal((end-start).days+1)
    previous_day=start-timedelta(days=1);previous_value=Decimal(0)
    for month,value in enumerate(LATE,1):
        end_day=month_end(year,month);value=Decimal(value)
        if day<=end_day:
            return previous_value+(value-previous_value)*Decimal((day-previous_day).days)/Decimal((end_day-previous_day).days)
        previous_day,previous_value=end_day,value
    return Decimal(100)


def report(db,user,params,catalog,available,today=None):
    year,month=m.period_args(params)
    if month>11:raise m.APIError(400,'대시보드는 1~11월 성과제출 기간을 조회합니다.')
    if params.get('employee_id',user['employee_id'])!=user['employee_id'] or params.get('department_id',user['department_id'])!=user['department_id']:
        raise m.APIError(403,'본인의 담당지표 대시보드만 조회할 수 있습니다.')
    today=today or datetime.now(timezone(timedelta(hours=9))).date()
    as_of=min(month_end(year,month),today);end=date(year,11,30);department=user['department_id']
    workplace=next((d for d in catalog['departments'] if d['id']==department),None)
    assigned=set(json.loads(user['indicators']));indicators=[k for k in available(department,db) if k['id'] in assigned]
    records={}
    for row in db.execute('SELECT * FROM monthly_actuals WHERE employee_id=? AND department_id=? AND year=? AND month BETWEEN 1 AND 11',(user['employee_id'],department,year)):
        if row['month']<=month and date(year,row['month'],1)<=as_of:records[(row['indicator_id'],row['month'])]=dict(row)
    metrics=[]
    for indicator in indicators:
        import performance
        structured=performance.dashboard_item(db,user,year,month,indicator,as_of)
        if structured:
            metrics.append(structured);continue
        selected=records.get((indicator['id'],month));config=progress.settings(db,department,year,indicator)
        if selected:
            saved=json.loads(selected['snapshot'])
            for key in ('target','weight','unit','entry_mode','direction','dashboard_curve'):
                if key in saved:config[key]=saved[key]
        config.setdefault('dashboard_curve','linear')
        if config['dashboard_curve'] not in CURVES:config['dashboard_curve']='linear'
        history=[];latest_record=None;latest_value=None;source_month=None
        for mm in range(1,12):
            record=records.get((indicator['id'],mm))
            point={'month':mm,'date':min(month_end(year,mm),as_of).isoformat() if date(year,mm,1)<=as_of else month_end(year,mm).isoformat(),'present':record is not None,'actual':None,'percent':None,'status':None,'target':None,'unit':config.get('unit',''),'narrative':''}
            if record:
                latest_record=record
                raw=record['adjusted'] if record['adjusted'] is not None else record['actual']
                saved=json.loads(record['snapshot']);point.update({'actual':raw if raw!='' else None,'percent':progress.achievement(record)['percent'],'status':record['status'],'target':saved.get('target'),'unit':saved.get('unit',''),'narrative':record['narrative'][:280]})
                if config['entry_mode']=='text':latest_value=record['status'];source_month=mm
                elif raw!='':latest_value=raw;source_month=mm
            history.append(point)
        percent=None
        if config['entry_mode']=='numeric' and latest_value is not None:
            synthetic={'snapshot':json.dumps(config),'actual':latest_value,'adjusted':None}
            percent=progress.achievement(synthetic)['percent']
        state_record=selected or latest_record;status=state_record['status'] if state_record else None
        actual_present=latest_value is not None
        planned=[{'month':mm,'date':month_end(year,mm).isoformat(),'percent':rounded(expected(month_end(year,mm),year,config['dashboard_curve']))} for mm in range(1,12)]
        if selected:
            filled=bool(selected['narrative'].strip()) if config['entry_mode']=='text' else selected['actual']!='' or selected['adjusted'] is not None
            input_state='입력완료' if filled else '임시저장'
        else:input_state='미입력'
        files=db.execute('SELECT COUNT(*) FROM evidence_files WHERE record_id=? AND deleted_at IS NULL',(state_record['id'],)).fetchone()[0] if state_record else 0
        metrics.append({'indicator':indicator,'settings':config,'achievement_percent':percent,'expected_percent':rounded(expected(as_of,year,config['dashboard_curve'])),'curve_label':CURVES[config['dashboard_curve']],
            'actual':latest_value,'source_month':source_month,'status':status or '미착수','input_state':input_state,'current_month_missing':input_state!='입력완료',
            'future_plan':state_record['future_plan'][:500] if state_record else '', 'narrative':state_record['narrative'][:280] if state_record else '',
            'updated_at':state_record['updated_at'] if state_record else None,'evidence_count':files,'history':history,'planned':planned,
            'graph_mode':'status' if config['entry_mode']=='text' else 'percent' if config.get('target') not in (None,'') and Decimal(str(config['target']))>0 else 'actual'})
    rates=[Decimal(x['achievement_percent']) for x in metrics if x['achievement_percent'] is not None]
    summary={'indicator_count':len(metrics),'average_achievement':rounded(sum(rates)/len(rates)) if rates else None,'input_count':sum(x['input_state']=='입력완료' for x in metrics),
        'draft_count':sum(x['input_state']=='임시저장' for x in metrics),'missing_count':sum(x['input_state']=='미입력' for x in metrics),'completed_count':sum(x['status']=='완료' for x in metrics)}
    return {'year':year,'month':month,'as_of':as_of.isoformat(),'today':today.isoformat(),'deadline':end.isoformat(),'remaining_days':max(0,(end-as_of).days),
        'period_started':as_of>=date(year,1,1),'period_ended':as_of>=end,'elapsed_percent':rounded(expected(as_of,year)),
        'department_id':department,'department_name':workplace['name'] if workplace else department,'employee_name':user['display_name'] or user['employee_id'],'summary':summary,'metrics':metrics,
        'note':'실적·달성률·입력 및 제출 상태만 표시하며 자동 상태 판정을 하지 않습니다.'}
