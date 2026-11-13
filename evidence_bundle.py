"""Superadmin-only evidence ZIPs for offline, document-based verification."""
import hashlib
import io
import re
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
import management as m
import performance as p
import review


def safe(value):
    value=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',str(value)).strip(' .')[:100]
    return value or '미지정'


def letter(year,recipient,rows):
    paragraphs=['공문 초안 — 문서번호·시행일·회신기한 확인 후 시행',f'수신: {recipient}','문서번호: [입력]    시행일: [입력]    발신: [입력]',f'제목: {year}년도 내부평가 실적 증빙자료 검증 협조 요청',
      f'1. {year}년도 성과관리편람에 따라 붙임 실적자료에 대한 문서 검증을 요청합니다.',
      '2. 해당 지표의 평가대상 기간, 실적 산정기준, 대상 없음·결측 사유 및 증빙을 확인하여 주시기 바랍니다.',
      '3. 검증결과는 회신기한 [입력]까지 공문으로 회신하여 주시기 바랍니다.',
      '4. 이 파일은 검증결과 또는 승인 문서가 아닌 의뢰 공문 초안입니다.',
      f'붙임: 실적·증빙 목록 1부 및 증빙자료 {len(rows)}건. 끝.']
    document='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:rPr><w:rFonts w:eastAsia="맑은 고딕"/></w:rPr><w:t xml:space="preserve">'+escape(t)+'</w:t></w:r></w:p>' for t in paragraphs)+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',document)
    return buffer.getvalue()


def export(db,user,params,catalog,available,storage):
    m.require_super(user);year=p.integer(params.get('year'));group_by=params.get('group_by','department');state=params.get('state','submitted')
    if group_by not in ('department','validator') or state not in ('all','submitted','official'):p.fail('증빙 묶음 구분을 확인해 주세요.')
    dep=params.get('department_id') or 'all'
    if dep!='all' and not any(d['id']==dep for d in catalog['departments']):p.fail('근무지를 확인해 주세요.')
    directory={d['id']:d['name'] for d in catalog['departments']};names={k['id']:k['name'] for k in catalog['indicators']};people={r['employee_id']:r['display_name'] for r in db.execute('SELECT employee_id,display_name FROM users')}
    groups={};count=0;unique=set();headers=['원소속 부서','검증부서','지표','관리기간','담당 사번','성명','제출상태','원본 파일명','압축파일 내 경로','원본 SHA-256','자료구분','실적내용·예외사유']
    def recipients(department,key):
        indicator=next((k for k in available(department,db) if k['id']==key),None)
        if not indicator:return ['미지정']
        override=db.execute('SELECT recipients FROM evidence_recipient_rules WHERE indicator=?',(key,)).fetchone()
        if override:
            import json
            return json.loads(override['recipients'])
        plan=p.get_plan(db,department,year,indicator);values=plan.get('verification_departments')
        if values:return list(dict.fromkeys(values))
        name=indicator['name']
        return ['AI정보융합실'] if '정보보안' in name else ['감사실'] if '청렴' in name else ['성과혁신실','환경협력처'] if '사회적 책임' in name else ['성과혁신실']
    bundle=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
    try:
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for kind,table,record_table in [('periodic','performance_files','performance_records'),('legacy','evidence_files','monthly_actuals')]:
                department='department' if kind=='periodic' else 'department_id';owner='employee' if kind=='periodic' else 'employee_id';indicator='indicator' if kind=='periodic' else 'indicator_id'
                sql=f'SELECT f.*,r.{department} origin,r.{owner} owner,r.{indicator} metric,r.year FROM {table} f JOIN {record_table} r ON r.id=f.record_id WHERE r.year=? AND f.deleted_at IS NULL'
                args=[year]
                if dep!='all':sql+=f' AND r.{department}=?';args.append(dep)
                for file in db.execute(sql,args).fetchall():
                    record=dict(db.execute(f'SELECT * FROM {record_table} WHERE id=?',(file['record_id'],)).fetchone())
                    if state=='official' and not db.execute("SELECT 1 FROM office_submission_sources x JOIN office_submissions s ON s.id=x.submission_id WHERE x.kind=? AND x.record_id=? AND s.state='submitted'",(kind,file['record_id'])).fetchone():continue
                    if state=='submitted':
                        if kind=='periodic' and record['state'] not in ('submitted','approved'):continue
                        if kind=='legacy' and m.period(db,file['origin'],year,record['month'])['status']!='closed':continue
                    path=Path(storage)/file['storage_name']
                    if not path.is_file():p.fail('저장된 증빙파일이 누락되었습니다. 백업·복원 상태를 확인해 주세요.',409)
                    digest=hashlib.sha256()
                    with path.open('rb') as proof:
                        for block in iter(lambda:proof.read(1024*1024),b''):digest.update(block)
                    if digest.hexdigest()!=file['sha256']:p.fail('증빙 원본의 무결성 확인에 실패했습니다.',409)
                    origin=directory.get(file['origin'],file['origin']);title=names.get(file['metric'],file['metric']);period=(record['start']+'_'+record['end']) if kind=='periodic' else f'{year}-{record["month"]:02d}'
                    status={'draft':'임시저장','submitted':'제출','returned':'반려','approved':'확정'}.get(record.get('state'),'이전 월별')
                    description=record.get('narrative','')+' '+(record.get('values_json','') if kind=='periodic' else str(record.get('adjusted') if record.get('adjusted') is not None else record.get('actual','')))
                    selected=recipients(file['origin'],file['metric']) if group_by=='validator' else [origin]
                    for recipient in selected:
                        folder=safe(recipient)[:35]+'_'+hashlib.sha256(recipient.encode()).hexdigest()[:6]
                        prefix=f"{folder}/{safe(file['origin']+'_'+origin)[:40]}" if group_by=='validator' else folder
                        filename=f"{prefix}/{safe(file['metric']+'_'+title)[:50]}/{kind}_{file['id']}_{(safe(Path(file['original_name']).stem)[:60]+Path(file['original_name']).suffix.lower())}"
                        archive.write(path,filename)
                        row=[origin,recipient if group_by=='validator' else '원소속 기준',title,period,file['owner'],people.get(file['owner'],''),status,file['original_name'],filename,file['sha256'],'통합 실적' if kind=='periodic' else '이전 월별',description]
                        groups.setdefault(folder,{'name':recipient,'rows':[]})['rows'].append(row);count+=1
                    unique.add((kind,file['id']))
            all_rows=[]
            for folder,group in groups.items():
                all_rows.extend(group['rows']);archive.writestr(folder+'/실적증빙목록.xlsx',review.xlsx([('증빙목록',[headers]+group['rows'])]))
                archive.writestr(folder+'/검증의뢰_공문초안.docx',letter(year,group['name'],group['rows']))
            archive.writestr('전체_증빙목록.xlsx',review.xlsx([('증빙목록',[headers]+all_rows)]))
            archive.writestr('사용안내.txt','공문 검증용 증빙 묶음입니다.\n원본 증빙과 이전 월별 자료를 포함합니다. 공문 초안의 문서번호·시행일·발신·회신기한을 확인한 뒤 시행하세요.\n검증부서별 묶음은 같은 원본이 여러 검증부서에 공유될 수 있습니다.\n검증 결과는 시스템에서 자동 확정하지 않습니다.\n')
        bundle.seek(0);return bundle,{'files':len(unique),'distributed_files':count,'groups':len(groups),'year':year,'group_by':group_by,'state':state}
    except BaseException:bundle.close();raise
