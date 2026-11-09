"""Verified 2026 AI department reference and one-time test settings.

Composite indicators use manually verified converted scores in the existing
single-value entry form. This module does not calculate official evaluation scores.
"""
import json
import management as m

DEPARTMENT='ORG-004'
YEAR=2026
SOURCE='붙임4. 2026년도 성과관리편람'
# (name, overall weight, printed page, component name / weight / original goal)
INDICATORS={
 'KPI-024':('데이터 정부평가 결과','10','71',[
     ('공공데이터 제공 운영 실태 평가 결과','5','74.8점'),
     ('데이터기반행정 실태 점검 결과','5','72.9점')]),
 'KPI-025':('AI 전환 추진 실적','30','72',[
     ('AI 인프라 체계 확립 이행률','10','20%'),
     ('AI 기반 일하는 방식 혁신 과제 추진','10','3건'),
     ('AI 전문교육 이수 인원','10','10명')]),
 'KPI-026':('종합성과보고서','46.5','73',[
     ('소관분야 추진성과','26.5','5등급 상대평가'),
     ('중점사업 추진성과','20','5등급 상대평가')]),
 'KPI-027':('사회적 책임 실현','3','74~75',[
     ('혁신과제 추진 실적','0.5','계획수립·결과보고 제출'),
     ('혁신활동 수상 실적','0.5','수상·학술대회 실적, 최대 0.5점'),
     ('경영공시 매뉴얼 준수율','1','100%, 담당 4개 항목 미준수 0건'),
     ('동반성장 활동','1','계획 수립·시행 0.4점, 상생결제 1건 이상 0.4점, 자율과제 0.2점')]),
 'KPI-028':('정보보안 관리 적시율','5','76',[
     ('내PC지키미 대응지수','1.5','90% 이상, 5~11월'),
     ('보안점검 결과 조치지수','1','90% 이상, 5~11월'),
     ('해킹메일 대응지수','2.5','상세평가 기준 90점 이상 (구성표에는 90%로 기재)')]),
 'KPI-029':('정부평가 이행점검 실적','0.5','77',[
     ('체크리스트에 따른 계획 수립 및 이행','0.25','100%'),
     ('체크리스트 기반 자체 모니터링 실적 제출','0.25','구성표 100%, 상세 득점표 4회 수행 시 0.25점')]),
 'KPI-030':('청렴문화지수','5','78',[
     ('부패·불공정요인 제도개선','3','2건 이상'),
     ('감사 지적사항 적시 이행률','1','100%, 지적사항 없으면 1점'),
     ('내부통제 강화·개선 노력도','1','계획 수립·결과 제출·통제 활동 운영, 세부과제 별도 시행')]),
}


def reference(department,year,indicator):
    item=INDICATORS.get(indicator['id'])
    if department!=DEPARTMENT or year!=YEAR or not item or indicator.get('applies_to')!='AI정보융합실':return None
    name,weight,page,components=item
    return {'source':SOURCE,'page':page,'weight':weight,'name':name,
        'components':[{'name':name,'weight':weight,'goal':goal} for name,weight,goal in components],
        'note':'세부목표 원문 참고입니다. 복합지표의 자동 초기설정은 평가부서가 확인한 환산점수 합계(환산점) 입력 기준입니다. 세부항목별 원시 실적의 자동 환산 기능은 포함하지 않습니다. 기본점수·상한·결측·가감점은 편람에 따라 별도 확인하세요.'}


def seed(db,catalog):
    """Fill absent settings once; preserve explicit settings and all snapshots."""
    department=next((d for d in catalog['departments'] if d['id']==DEPARTMENT and d['name']=='AI정보융합실'),None)
    if not department:return 0
    added=0
    for indicator in catalog['indicators']:
        ref=reference(DEPARTMENT,YEAR,indicator)
        if not ref:continue
        key=indicator['id'];weight=ref['weight'];text=key=='KPI-026'
        definition={'target':None if text else weight,'weight':weight,'unit':'' if text else '환산점',
                    'entry_mode':'text' if text else 'numeric','direction':'higher','dashboard_curve':'linear'}
        # INSERT OR IGNORE is atomic across concurrent server starts.
        cursor=db.execute('INSERT OR IGNORE INTO department_indicator_settings VALUES(?,?,?,?,1)',
                          (DEPARTMENT,key,YEAR,json.dumps(definition,ensure_ascii=False)))
        if cursor.rowcount:
            added+=1
            m.audit(db,'system','편람 시험용 초기값 적용',DEPARTMENT,f'{YEAR}:{key}',None,
                    {**definition,'reference':ref},'사용자 요청: 2026 AI정보융합실 편람 기준 시험용 초기값, 기존 월별 기록 보존')
    return added
