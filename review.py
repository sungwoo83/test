"""Manager-only monthly workplace/employee review and spreadsheet exports."""
import io
import json
import re
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta
from xml.sax.saxutils import escape
import management as m
import progress


def require_reviewer(user):
    if user['role'] not in ('admin','superadmin'):raise m.APIError(403,'관리자와 수퍼관리자만 조회할 수 있습니다.')


def entry_state(record):
    if record is None:return '미입력'
    definition=json.loads(record['snapshot'])
    mode=definition.get('entry_mode','numeric')
    filled=bool(record['narrative'].strip()) if mode=='text' else (record['adjusted'] is not None or record['actual']!='')
    return '입력완료' if filled else '임시저장'


def report(db,user,params,catalog,available):
    require_reviewer(user)
    year,month=m.period_args(params)
    selected=params.get('department_id','')
    if user['role']=='admin':
        if selected and selected!=user['admin_department_id']:raise m.APIError(403,'지정된 관리 부서만 조회할 수 있습니다.')
        selected=user['admin_department_id']
    if selected:m.department_scope(user,selected,catalog)
    departments=[d for d in catalog['departments'] if d['kind']!='운영' and (not selected or d['id']==selected)]
    users={r['employee_id']:dict(r) for r in db.execute('SELECT employee_id,display_name,department_id,indicators,role FROM users')}
    all_items=[];employees=[];summaries=[]
    for department in departments:
        did=department['id'];period=m.period(db,did,year,month)
        indicators={k['id']:k for k in available(did,db)}
        saved={(r['employee_id'],r['indicator_id']):dict(r) for r in db.execute('SELECT * FROM monthly_actuals WHERE department_id=? AND year=? AND month=?',(did,year,month))}
        assignments={}
        member_ids=set()
        for employee in users.values():
            if employee['department_id']!=did:continue
            member_ids.add(employee['employee_id'])
            for indicator_id in json.loads(employee['indicators']):
                if indicator_id in indicators:assignments[(employee['employee_id'],indicator_id)]=True
        for key in saved:
            assignments.setdefault(key,False);member_ids.add(key[0])
        rows=[]
        for (employee_id,indicator_id),is_current in sorted(assignments.items()):
            record=saved.get((employee_id,indicator_id));employee=users.get(employee_id,{})
            indicator=indicators.get(indicator_id) or json.loads(record['snapshot'])
            if record:
                record['achievement']=progress.achievement(record)
                record['attachments']=progress.list_attachments(db,record['id'])
            row={'department_id':did,'department_name':department['name'],'employee_id':employee_id,
                 'employee_name':employee.get('display_name',''),'indicator':indicator,
                 'settings':progress.settings(db,did,year,indicator),'record':record,'period':period,
                 'entry_state':entry_state(record),'is_current_assignment':is_current}
            rows.append(row)
        employee_rows=[]
        for employee_id in sorted(member_ids):
            person=users.get(employee_id,{});own=[r for r in rows if r['employee_id']==employee_id]
            counts={state:sum(r['entry_state']==state for r in own) for state in ('미입력','임시저장','입력완료')}
            state='담당지표 없음' if not own else '입력완료' if counts['입력완료']==len(own) else '미입력' if counts['미입력']==len(own) else '입력중'
            employee_rows.append({'department_id':did,'department_name':department['name'],'employee_id':employee_id,
                'display_name':person.get('display_name',''),'assigned_count':len(own),'entered_count':counts['입력완료'],
                'missing_count':counts['미입력'],'draft_count':counts['임시저장'],'entry_state':state,
                'completion_percent':format(Decimal(counts['입력완료'])*100/len(own),'.2f') if own else None,
                'is_current_member':person.get('department_id')==did})
        rates=[Decimal(r['record']['achievement']['percent']) for r in rows if r['record'] and r['record']['achievement']['percent'] is not None]
        summary={'department_id':did,'department_name':department['name'],'group':department['group'],'employee_count':len(member_ids),
            'assigned_count':len(rows),'entered_count':sum(r['entry_state']=='입력완료' for r in rows),
            'missing_count':sum(r['entry_state']=='미입력' for r in rows),'draft_count':sum(r['entry_state']=='임시저장' for r in rows),
            'average_achievement':format((sum(rates)/len(rates)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'f') if rates else None,
            'period':period}
        summaries.append(summary);employees.extend(employee_rows);all_items.extend(rows)
    return {'year':year,'month':month,'department_id':selected,'workplaces':summaries,'employees':employees,'items':all_items}


def _column(index):
    name=''
    while index:index,remainder=divmod(index-1,26);name=chr(65+remainder)+name
    return name


def _safe(value):
    return re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]','',str(value))


def xlsx(sheets):
    """Minimal interoperable XLSX using inline text, not user-controlled formulas."""
    memory=io.BytesIO();ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as z:
        overrides=''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))
        z.writestr('[Content_Types].xml',f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{overrides}</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sheet_refs=''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,rows) in enumerate(sheets,1))
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_refs}</sheets></workbook>')
        relationships=''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{relationships}<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml',f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><name val="맑은 고딕"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="top" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i,(_,rows) in enumerate(sheets,1):
            xml=[]
            for row_index,row in enumerate(rows,1):
                cells=[]
                for col_index,value in enumerate(row,1):
                    cell=f'{_column(col_index)}{row_index}';style=1 if row_index==1 else 0
                    if isinstance(value,tuple) and value[0]=='percent':
                        cells.append(f'<c r="{cell}" s="2"><v>{Decimal(str(value[1]))/100}</v></c>')
                    elif isinstance(value,(int,Decimal)):
                        cells.append(f'<c r="{cell}" s="{style}"><v>{value}</v></c>')
                    else:
                        text='' if value is None else _safe(value)
                        cells.append(f'<c r="{cell}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
                xml.append(f'<row r="{row_index}">'+''.join(cells)+'</row>')
            count=max((len(r) for r in rows),default=1)
            z.writestr(f'xl/worksheets/sheet{i}.xml',f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="{count}" width="22" customWidth="1"/></cols><sheetData>'+''.join(xml)+f'</sheetData><autoFilter ref="A1:{_column(count)}{max(1,len(rows))}"/></worksheet>')
    return memory.getvalue()


def export(report):
    percent=lambda value:('percent',value) if value is not None else ''
    numeric=lambda value:Decimal(str(value)) if value not in (None,'') else ''
    places=[['근무지','평가군','직원 수','담당지표 건수','입력완료','미입력','임시저장','평균 달성률(단순평균·참고)','월 마감']]
    for d in report['workplaces']:places.append([d['department_name'],d['group'],d['employee_count'],d['assigned_count'],d['entered_count'],d['missing_count'],d['draft_count'],percent(d['average_achievement']),'마감' if d['period']['status']=='closed' else '입력 가능'])
    people=[['근무지','사번','성명','입력현황','담당지표 건수','입력완료','미입력','임시저장','입력률','현재 소속 여부']]
    for e in report['employees']:people.append([e['department_name'],e['employee_id'],e['display_name'],e['entry_state'],e['assigned_count'],e['entered_count'],e['missing_count'],e['draft_count'],percent(e['completion_percent']),'현재 소속' if e['is_current_member'] else '이전 소속'])
    details=[['연도','월','근무지','사번','성명','지표ID','지표명','입력 여부','목표','가중치(점)','단위','원래 실적','조정 실적','반영 실적','달성률','진행상태','실적 내용','향후계획','증빙파일','최종 수정자','최종 수정시각(KST)','월 마감','담당 변경 여부']]
    for item in report['items']:
        r=item['record'];config=json.loads(r['snapshot']) if r else item['settings']
        changed_at=''
        if r:
            changed_at=datetime.fromisoformat(r['updated_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        details.append([report['year'],report['month'],item['department_name'],item['employee_id'],item['employee_name'],item['indicator']['id'],item['indicator']['name'],item['entry_state'],numeric(config.get('target')),numeric(config.get('weight')),config.get('unit',''),numeric(r['actual']) if r else '',numeric(r['adjusted']) if r else '',numeric(r['adjusted'] if r['adjusted'] is not None else r['actual']) if r else '',percent(r['achievement']['percent']) if r else '',r['status'] if r else '미착수',r['narrative'] if r else '',r['future_plan'] if r else '', '\n'.join(f['original_name'] for f in r['attachments']) if r else '',r['updated_by'] if r else '',changed_at,'마감' if item['period']['status']=='closed' else '입력 가능','현재 담당' if item['is_current_assignment'] else '이전 담당'])
    return xlsx([('근무지현황',places),('직원입력현황',people),('지표실적',details)])
