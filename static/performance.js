'use strict';
let perfTab='own',perfData=null,perfSequence=0,perfSubmit=null,perfBusy=false;
const perfMethods={ratio:'실적 ÷ 목표',lower:'목표 ÷ 실적',rate:'분자 ÷ 분모 × 100, 목표 대비',bands:'기준 이상 구간별 득점',bands_lower:'기준 이하 구간별 득점',points:'확인된 환산점수'};
const perfStates={draft:'임시저장',submitted:'제출·검토대기',returned:'반려',approved:'확정'};
function clearPerformance(){perfTab='own';perfData=null;perfSequence++;perfSubmit=null;$('perf-content').replaceChildren();$('perf-summary').replaceChildren();$('perf-dialog').close();$('perf-fields').replaceChildren();$('perf-reason').value='';}
function setupPerformance(){
  const select=$('perf-department'),old=select.value;select.replaceChildren();
  const scope=current.user.role==='superadmin'?evalDepartments():evalDepartments().filter(d=>d.id===(isManager()&&perfTab!=='own'?current.user.admin_department_id:current.user.department_id));
  for(const d of scope)select.append(new Option(d.name,d.id));if(scope.some(d=>d.id===old))select.value=old;select.disabled=current.user.role!=='superadmin';
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  if(!$('perf-date').value){$('perf-date').value=today;$('perf-year').value=today.slice(0,4);}
  for(const el of document.querySelectorAll('.perf-manager'))el.hidden=!isManager();$('perf-ops-tab').hidden=$('perf-overview-tab').hidden=current.user.role!=='superadmin';
  if((!isManager()&&['review','annual','accounts','audit','operations','overview'].includes(perfTab))||(current.user.role!=='superadmin'&&perfTab==='overview'))perfTab='own';
  if(location.hash==='#performance')loadPerformance();
}
function perfContext(){return {department_id:$('perf-department').value,year:Number($('perf-year').value),date:$('perf-date').value};}
function perfButton(label,run,primary=false){const b=node('button',primary?'primary':'secondary',label);b.type='button';b.onclick=run;return b;}
function perfNote(value){return node('p','notice',value);}
function perfInput(parent,label,value='',type='text',options=null){
  const wrapper=node('label','perf-field',label);let input;
  if(options){input=node('select');for(const [id,name] of options)input.append(new Option(name,id));}
  else if(type==='textarea'){input=node('textarea');input.rows=3;input.maxLength=10000;}
  else{input=node('input');input.type=type;if(type==='number'){input.step='any';input.inputMode='decimal';}if(type==='password')input.autocomplete='new-password';}
  input.setAttribute('aria-label',label);
  if(type==='checkbox'){input.checked=Boolean(value);}else input.value=value??'';wrapper.append(input);parent.append(wrapper);return input;
}
function perfTable(head,rows){const wrap=node('div','table-wrap'),table=node('table'),thead=node('thead'),tr=node('tr'),body=node('tbody');for(const h of head)tr.append(node('th','',h));thead.append(tr);table.append(thead,body);for(const values of rows){const row=node('tr');for(const v of values){const td=node('td');if(v instanceof Node)td.append(v);else td.textContent=v??'—';row.append(td);}body.append(row);}wrap.append(table);return wrap;}
function perfDialog(title,build,save,reason=''){
  $('perf-dialog-title').textContent=title;$('perf-dialog-error').textContent='';$('perf-fields').replaceChildren();$('perf-reason').value=reason;perfSubmit=save;build($('perf-fields'));$('perf-dialog').showModal();
}
async function perfPost(path,data){return api('/api/performance/'+path,{...perfContext(),...data,reason:data.reason||$('perf-reason').value||'기간별 실적 확인',csrf:current.csrf});}
async function loadPerformance(){
  if(!current)return;const sequence=++perfSequence,csrf=current.csrf;$('perf-error').textContent='';$('perf-refresh').disabled=true;
  const paths={overview:'/overview',own:'',review:'',annual:'/annual',accounts:'/users',tasks:'/assignments',history:'/history',audit:'/audit',operations:'/operations'};
  for(const b of document.querySelectorAll('#perf-tabs button'))b.setAttribute('aria-pressed',String(b.dataset.tab===perfTab));
  const exportKind=perfTab==='annual'?'annual':perfTab==='audit'?'audit':'records';$('perf-export').hidden=!isManager()||!['review','annual','audit'].includes(perfTab);$('perf-export').href='/api/performance.xlsx?'+new URLSearchParams({...perfContext(),kind:exportKind});$('perf-copy').hidden=!isManager();
  try{
    const response=await api('/api/performance'+paths[perfTab]+'?'+new URLSearchParams({...perfContext(),view:perfTab}));
    if(sequence!==perfSequence||current?.csrf!==csrf)return;perfData=response;$('perf-content').replaceChildren();$('perf-summary').replaceChildren();
    if(perfTab==='overview')renderPerfOverview(response);else if(['own','review'].includes(perfTab))renderPerfRecords(response);else if(perfTab==='annual')renderPerfAnnual(response);else if(perfTab==='accounts')renderPerfAccounts(response);else if(perfTab==='tasks')renderPerfTasks(response);else if(perfTab==='history')renderPerfHistory(response);else if(perfTab==='audit')renderPerfAudit(response);else renderPerfOps(response);
  }catch(e){if(sequence===perfSequence){perfData=null;$('perf-content').replaceChildren();$('perf-error').textContent=e.message;}}
  finally{if(sequence===perfSequence)$('perf-refresh').disabled=false;}
}
function perfChart(item){
  const wrap=node('div','perf-chart-wrap'),points=item.history.filter(r=>r.calculation.percent!=null).slice(-30),svg=svgNode('svg',{viewBox:'0 0 600 160',class:'perf-chart',role:'img','aria-label':item.indicator.name+' 기간별 득점률 추이'});
  for(const v of [0,50,100]){const y=135-v;svg.append(svgNode('line',{x1:40,x2:575,y1:y,y2:y,class:'chart-grid'}),svgNode('text',{x:2,y:y+4,class:'chart-axis'},v+'%'));}
  if(!points.length)svg.append(svgNode('text',{x:90,y:85,class:'chart-axis'},'세부 실적을 저장하면 기간별 추이가 표시됩니다.'));
  const xy=points.map((r,i)=>({r,x:55+i*510/Math.max(1,points.length-1),y:135-Math.max(0,Math.min(100,Number(r.calculation.percent)))}));
  if(xy.length>1)svg.append(svgNode('path',{d:xy.map((p,i)=>(i?'L':'M')+p.x+','+p.y).join(' '),class:'chart-actual-path'}));
  for(const {r,x,y} of xy){const circle=svgNode('circle',{cx:x,cy:y,r:5,class:'chart-point',tabindex:0});circle.append(svgNode('title',{},`${r.start}~${r.end} · ${r.employee} · ${perfStates[r.state]} · ${prettyNumber(r.calculation.percent)}%`));svg.append(circle);}
  wrap.append(svg);return wrap;
}
function perfHealth(item){return [item.monitor?.code||'pending',item.monitor?.label||'판정 대기'];}

function renderPerfRecords(data){
  const canManage=current.user.role==='superadmin'||current.user.role==='admin'&&current.user.admin_department_id===data.department_id;
  const total=data.items.length,all=data.items.flatMap(i=>i.records),summary=$('perf-summary');summary.append(node('strong','',`${total}개 지표`),node('span','',`임시저장 ${all.filter(r=>r.state==='draft').length} · 제출 ${all.filter(r=>r.state==='submitted').length} · 반려 ${all.filter(r=>r.state==='returned').length} · 확정 ${all.filter(r=>r.state==='approved').length}`));
  if(data.year_state.closed)summary.append(perfNote('확정 보관된 연도입니다. 수정하려면 관리자가 연도 마감을 해제해야 합니다.'));
  if(!total)$('perf-content').append(perfNote('현재 근무지의 담당지표가 없습니다. 나의 업무 홈에서 담당지표를 확인해 주세요.'));
  for(const item of data.items){
    const plan=item.definition,card=node('article','perf-card'),heading=node('div','perf-heading'),health=perfHealth(item);heading.append(node('h2','',item.indicator.name),node('span','health-badge '+health[0],health[1]));card.append(heading);
    card.append(node('p','muted',`${item.period?.label||'관리기간 밖'} · 배점 ${plan.weight}점 · 제출기한 ${item.period?.due||'—'}`));
    card.append(node('p','small',`공동 취합: ${{sum:'직원별 합산',ratio:'분모·분자 합산',average:'직원별 평균',representative:'대표 담당자'}[plan.aggregation]}${plan.representative?' ('+plan.representative+')':''} · 기간 취합: ${plan.time_aggregation==='latest'?'최신 누적값':plan.time_aggregation==='average'?'기간별 평균':'기간 증분 합산'}`));
    const actions=node('div','perf-actions');if(canManage)actions.append(perfButton('월별·주별 / 산식 설정',()=>editPerfPlan(item)));
    if(item.period&&plan.configured&&canManage){const b=perfButton(item.lock.closed?'기간 마감 해제':'기간 마감',()=>perfReasonDialog(item.lock.closed?'기간 마감 해제':'기간 마감','lock',{indicator_id:item.indicator.id,revision:item.lock.revision,closed:!item.lock.closed}));b.disabled=Boolean(data.year_state.closed);actions.append(b);}
    card.append(actions);if(!plan.configured){card.append(perfNote('관리자가 관리기간·주기·세부목표·취합 방법을 설정하면 입력할 수 있습니다.'));}
    else if(item.period){
      const people=perfTab==='review'?item.assigned:[{employee_id:current.user.employee_id,display_name:current.user.display_name}];
      const visible=[...people];for(const r of item.records)if(perfTab==='review'&&!visible.some(p=>p.employee_id===r.employee))visible.push({employee_id:r.employee,display_name:'이전 담당자'});
      const rows=visible.map(person=>{const r=item.records.find(r=>r.employee===person.employee_id),buttons=node('div','perf-actions');
        const edit=perfButton(r?'입력·수정':'실적 입력',()=>editPerfEntry(item,person.employee_id,r));edit.disabled=Boolean(data.year_state.closed||item.lock.closed||r&&!['draft','returned'].includes(r.state));buttons.append(edit);
        if(r){
          if(canManage&&r.employee!==current.user.employee_id&&['draft','returned'].includes(r.state)){const proxy=perfButton('대리제출',()=>perfReasonDialog('담당자 실적 대리제출','transition',{indicator_id:item.indicator.id,employee_id:r.employee,revision:r.revision,action:'submit_on_behalf'}));proxy.disabled=Boolean(item.lock.closed||data.year_state.closed);buttons.append(proxy);}
          buttons.append(perfButton('계산 근거',()=>perfCalculation(r)));
          const files=perfButton(`증빙 ${r.files.length}개`,()=>perfFiles(r));buttons.append(files);
          if(['draft','returned'].includes(r.state)&&r.employee===current.user.employee_id){const submit=perfButton('제출',()=>perfReasonDialog('실적 제출','transition',{indicator_id:item.indicator.id,employee_id:r.employee,revision:r.revision,action:'submit'}),true);submit.disabled=Boolean(item.lock.closed||data.year_state.closed);buttons.append(submit);}
          if(canManage&&['submitted','approved'].includes(r.state)){for(const [action,label] of (r.state==='submitted'?[['approve','확정'],['return','반려']]:[['return','확정 취소·반려']])){const b=perfButton(label,()=>perfReasonDialog(label,'transition',{indicator_id:item.indicator.id,employee_id:r.employee,revision:r.revision,action}));b.disabled=Boolean(item.lock.closed||data.year_state.closed);buttons.append(b);}}
        }
        return [(person.display_name||'')+' · '+person.employee_id,r?.status||'미착수',r?perfStates[r.state]:'미입력',r?.calculation.score!=null?prettyNumber(r.calculation.score)+'점 / '+prettyNumber(r.calculation.percent)+'%':'—',r?.feedback||'—',buttons];});
      card.append(perfTable(['담당자','업무 진행','제출상태','득점·득점률','검토 사유', '처리'],rows));
    }
    if(plan.configured&&plan.mode==='numeric')card.append(perfChart(item));
    const details=node('details'),ref=node('summary','','세부목표 · 계산 기준 · 기간 이력');details.append(ref);
    details.append(perfTable(['세부항목','목표','배점','계산','기본·상한'],plan.components.map(c=>[c.name,c.target+' '+c.unit,c.weight,perfMethods[c.method],`${Number(c.floor)*100}% ~ ${Number(c.cap)*100}%`])));
    details.append(perfTable(['기간','담당자','제출상태','득점','추진 내용','향후계획'],item.history.map(r=>[r.start+' ~ '+r.end,r.employee,perfStates[r.state],r.calculation.score,r.narrative,r.plan])));
    if(item.legacy.length)details.append(perfNote(`이전 월별 기록 ${item.legacy.length}건은 원래 기록으로 보존됩니다. 이전 월별 실적 화면 또는 나의 이전 실적에서 확인하세요.`));
    if(plan.reference){const refView=manualReference({manual_reference:plan.reference});if(refView)details.append(refView);}
    card.append(details);if(perfTab==='review'&&item.aggregate)card.append(perfNote(item.aggregate.ready?`확정 실적 취합 ${item.aggregate.calculation?.score??'비계량 평가 대기'}점 · 대상 기록 ${item.aggregate.sources.length}건`:item.aggregate.reason));
    $('perf-content').append(card);
  }
}
function perfReasonDialog(title,path,payload){perfDialog(title,box=>box.append(perfNote('처리 사유를 입력해 주세요. 작업 내용과 처리자는 변경이력에 기록됩니다.')),()=>perfPost(path,payload));}
function editPerfEntry(item,owner,record){
  let inputs={},narrative,future,status,rebase;let definition=record?.definition||item.definition;
  perfDialog(item.indicator.name+' · 실적 입력',box=>{
    box.append(perfNote(`${item.period.label} · 업무 진행상태와 제출상태는 별도로 관리합니다.`));
    if(record&&JSON.stringify(record.definition.components)!==JSON.stringify(item.definition.components)){rebase=perfInput(box,'관리자가 변경한 최신 계산 기준 적용',false,'checkbox');rebase.onchange=()=>{definition=rebase.checked?item.definition:record.definition;fields.replaceChildren();buildFields(fields);refreshPreview();};}
    const fields=node('div','perf-component-inputs');box.append(fields);
    function buildFields(parent){inputs={};for(const c of definition.components){const group=node('fieldset');group.append(node('legend','',`${c.name} · 목표 ${c.target}${c.unit} · 배점 ${c.weight}점`));const value=record?.values[c.id];if(c.method==='rate'){inputs[c.id]={numerator:perfInput(group,'분자',value?.numerator||'','number'),denominator:perfInput(group,'분모',value?.denominator||'','number')};}else inputs[c.id]=perfInput(group,c.method==='points'?'평가 기준에 따라 확인한 환산점수':`실적 (${c.unit})`,value??'','number');group.append(node('p','hint',`계산방식 ${perfMethods[c.method]} · 기본 ${Number(c.floor)*100}% · 상한 ${Number(c.cap)*100}%${c.bands?' · 구간 '+c.bands.map(b=>b.join(' → ')).join(', '):''}`));parent.append(group);}}
    const preview=node('p','achievement-preview','세부 실적을 입력하면 예상 득점과 득점률을 계산합니다.');box.append(preview);let previewTimer=null,previewSequence=0;
    const refreshPreview=()=>{clearTimeout(previewTimer);previewTimer=setTimeout(async()=>{if(!current||!$('perf-dialog').open)return;const sequence=++previewSequence,csrf=current.csrf,values={};for(const [id,input]of Object.entries(inputs))values[id]=input.numerator?{numerator:input.numerator.value,denominator:input.denominator.value}:input.value;try{const result=await perfPost('preview',{indicator_id:item.indicator.id,record_id:record?.id,rebase:Boolean(rebase?.checked),values});if(sequence!==previewSequence||current?.csrf!==csrf)return;const calc=result.calculation;preview.textContent=calc.score!=null?`예상 득점 ${prettyNumber(calc.score)} / ${definition.weight}점 · 득점률 ${prettyNumber(calc.percent)}% (저장 전)`:`세부항목 ${calc.missing?.length||0}개 입력 대기`;}catch(e){if(sequence===previewSequence)preview.textContent=e.message;}},250);};fields.oninput=refreshPreview;
    buildFields(fields);status=perfInput(box,'업무 진행상태',record?.status||'미착수','text',progressOptions());narrative=perfInput(box,definition.mode==='text'?'성과보고서 내용':'추진 내용·계산 근거',record?.narrative||'','textarea');future=perfInput(box,'향후계획',record?.plan||'','textarea');box.append(perfNote('저장한 뒤 증빙을 업로드하고 제출하세요. 제출·확정 후에는 반려를 통해 수정합니다.'));
  },()=>{const values={};for(const [id,input] of Object.entries(inputs))values[id]=input.numerator?{numerator:input.numerator.value,denominator:input.denominator.value}:input.value;return perfPost('record',{indicator_id:item.indicator.id,employee_id:owner,revision:record?.revision||0,values,narrative:narrative.value,plan:future.value,status:status.value,rebase:Boolean(rebase?.checked)});},'실적 작성');
}
function progressOptions(){return ['미착수','진행중','완료'].map(x=>[x,x]);}
function editPerfPlan(item){
  const initial=structuredClone(item.definition);let base={},componentRows=[],scheduleRows=[],milestoneRows=[];
  if(!initial.configured&&item.legacy.length){const last=Math.max(...item.legacy.map(r=>r.month));const next=new Date(Date.UTC(perfContext().year,last,1)).toISOString().slice(0,10);initial.start=next;initial.schedule=[{effective:next,cadence:'monthly'}];}
  perfDialog(item.indicator.name+' · 월별·주별 / 산식 설정',box=>{
    box.append(perfNote('기존 기록을 나누는 주기 변경은 제한됩니다. 다음 미입력 기간부터 적용하세요. 기본점수·구간·결측은 편람과 대조해 확정하세요.'));
    const grid=node('div','perf-form-grid');box.append(grid);
    base.mode=perfInput(grid,'입력 방식',initial.mode,'text',[['numeric','수치·세부항목'],['text','서술형·상대평가']]);base.weight=perfInput(grid,'지표 가중치(배점)',initial.weight,'number');base.start=perfInput(grid,'관리 시작일',initial.start,'date');base.end=perfInput(grid,'관리 종료일',initial.end,'date');base.due_days=perfInput(grid,'기간 종료 후 제출 여유일',initial.due_days,'number');
    base.aggregation=perfInput(grid,'여러 직원의 실적 취합',initial.aggregation,'text',[['representative','대표 담당자 실적'],['sum','직원별 수치 합산'],['average','직원별 단순평균(비율은 분모·분자 방식 권장)'],['ratio','분모·분자 합산']]);base.representative=perfInput(grid,'대표 담당자 사번',initial.representative||'');base.time_aggregation=perfInput(grid,'여러 기간의 실적 취합',initial.time_aggregation,'text',[['latest','최신 누적값 사용'],['sum','매 기간 증분 합산'],['average','기간별 단순평균']]);base.curve=perfInput(grid,'진행상태 판단 계획',initial.curve,'text',[['linear','균등 진행'],['late','연말 집중'],['milestones','중간목표 직접 설정']]);
    box.append(node('h3','','월별·주별 전환 일정'));const schedules=node('div','perf-editor-rows');box.append(schedules);
    function addSchedule(value={effective:base.start.value,cadence:'monthly'}){const row=node('div','perf-editor-row'),entry={};entry.effective=perfInput(row,'적용 시작일',value.effective,'date');entry.cadence=perfInput(row,'관리주기',value.cadence,'text',[['monthly','월별'],['weekly','주별']]);row.append(perfButton('삭제',()=>{scheduleRows=scheduleRows.filter(r=>r!==entry);row.remove();}));schedules.append(row);scheduleRows.push(entry);}
    initial.schedule.forEach(addSchedule);box.append(perfButton('전환 일정 추가',()=>addSchedule()));
    box.append(node('h3','','중간목표'));const milestones=node('div','perf-editor-rows');box.append(milestones);
    function addMilestone(value={date:base.end.value,percent:100}){const row=node('div','perf-editor-row'),entry={};entry.date=perfInput(row,'기준일',value.date,'date');entry.percent=perfInput(row,'계획 달성률(%)',value.percent,'number');row.append(perfButton('삭제',()=>{milestoneRows=milestoneRows.filter(r=>r!==entry);row.remove();}));milestones.append(row);milestoneRows.push(entry);}
    initial.milestones.forEach(addMilestone);box.append(perfButton('중간목표 추가',()=>addMilestone()));
    box.append(node('h3','','세부항목·계산 기준'));const components=node('div','perf-editor-rows');box.append(components);
    function addComponent(value={id:String(Date.now()),name:'새 세부항목',weight:'0',target:'100',unit:'%',method:'ratio',floor:'0',cap:'1'}){
      const fieldset=node('fieldset','perf-component-editor'),entry={id:value.id},grid=node('div','perf-form-grid');fieldset.append(grid);
      for(const [key,label] of [['name','세부항목명'],['weight','세부 배점'],['target','목표'],['unit','단위']])entry[key]=perfInput(grid,label,value[key],['weight','target'].includes(key)?'number':'text');
      entry.method=perfInput(grid,'계산 방식',value.method,'text',[['ratio','상향형: 실적 ÷ 목표'],['lower','하향형: 목표 ÷ 실적'],['rate','분자 ÷ 분모 × 100 → 목표 대비'],['bands','구간: 기준 이상'],['bands_lower','구간: 기준 이하'],['points','확인된 환산점수 직접 입력']]);
      entry.floor=perfInput(grid,'기본점수 비율(%)',Number(value.floor)*100,'number');entry.cap=perfInput(grid,'상한 비율(%)',Number(value.cap)*100,'number');entry.bands=perfInput(fieldset,'구간별 기준:득점비율(%) — 줄마다 하나',value.bands?.map(pair=>pair[0]+':'+Number(pair[1])*100).join('\n')||'','textarea');
      fieldset.append(perfButton('항목 삭제',()=>{componentRows=componentRows.filter(r=>r!==entry);fieldset.remove();}));components.append(fieldset);componentRows.push(entry);
    }
    initial.components.forEach(addComponent);box.append(perfButton('세부항목 추가',()=>addComponent()));
    if(initial.reference){const view=manualReference({manual_reference:initial.reference});if(view){view.open=true;box.append(view);}}
  },()=>{
    const definition={};for(const [key,input]of Object.entries(base))definition[key]=key==='due_days'?Number(input.value):input.value;
    definition.schedule=scheduleRows.map(r=>({effective:r.effective.value,cadence:r.cadence.value}));definition.milestones=milestoneRows.map(r=>({date:r.date.value,percent:r.percent.value}));
    definition.components=definition.mode==='text'?[]:componentRows.map(r=>({id:r.id,name:r.name.value,weight:r.weight.value,target:r.target.value,unit:r.unit.value,method:r.method.value,floor:String(Number(r.floor.value)/100),cap:String(Number(r.cap.value)/100),...(['bands','bands_lower'].includes(r.method.value)?{bands:r.bands.value.split('\n').filter(x=>x.trim()).map(line=>{const [v,p]=line.split(':');if(v===undefined||p===undefined)throw Error('구간 기준은 기준수치:득점비율 형식으로 입력해 주세요.');return [v.trim(),String(Number(p.trim())/100)];})}:{})}));
    return perfPost('plan',{indicator_id:item.indicator.id,revision:initial.revision,definition});
  },'관리주기·세부목표 설정');
}
function perfCalculation(record){
  perfDialog('실적·계산 근거 · '+record.start+' ~ '+record.end,box=>{
    box.append(perfNote(`${record.employee} · ${perfStates[record.state]} · ${record.status}`));
    const rows=record.definition.components.map(c=>{const value=record.values[c.id],part=record.calculation.components.find(p=>p.id===c.id);return [c.name,value&&typeof value==='object'?`${value.numerator} / ${value.denominator}`:value??'미입력',c.target+' '+c.unit,c.weight,part?.score??'미산정',perfMethods[c.method]];});
    if(rows.length)box.append(perfTable(['세부항목','입력 실적','목표','배점','득점','계산방식'],rows));
    box.append(node('h3','','추진 내용'),node('p','metric-plan',record.narrative||'내용 없음'),node('h3','','향후계획'),node('p','metric-plan',record.plan||'계획 없음'),perfNote(`검토 사유: ${record.feedback||'없음'}`));
    for(const file of record.files){const a=node('a','',file.original_name+' ');a.href='/api/performance/files/'+file.id;box.append(a);}
  },async()=>({ok:true}),'계산 근거 확인');
}
function perfFiles(record){
  perfDialog('증빙자료 · '+record.start+' ~ '+record.end,box=>{
    for(const file of record.files){const row=node('div','perf-actions'),link=node('a','',file.original_name);link.href='/api/performance/files/'+file.id;link.download=file.original_name;row.append(link);if(['draft','returned'].includes(record.state)&&(isManager()||file.uploaded_by===current.user.employee_id))row.append(perfButton('삭제',async()=>{try{await perfPost('files/delete',{file_id:file.id});await loadPerformance();const updated=perfData.items.flatMap(i=>i.records).find(r=>r.id===record.id);if(updated)perfFiles(updated);}catch(e){$('perf-dialog-error').textContent=e.message;}}));box.append(row);}
    if(['draft','returned'].includes(record.state)){const input=perfInput(box,'증빙파일 선택(파일당 10MB, 최대 10개)','','file');input.multiple=true;box.append(perfButton('업로드',async()=>{try{if(!input.files.length)throw Error('파일을 선택해 주세요.');for(const file of input.files){const response=await fetch('/api/performance/upload?record_id='+record.id,{method:'POST',headers:{'X-Requested-With':'PerformanceManager','X-CSRF-Token':current.csrf,'X-File-Name':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'},body:file});const result=await response.json();if(!response.ok)throw Error(result.error);}await loadPerformance();const updated=perfData.items.flatMap(i=>i.records).find(r=>r.id===record.id);if(updated)perfFiles(updated);}catch(e){$('perf-dialog-error').textContent=e.message;}},true));}else box.append(perfNote('제출·확정된 실적의 증빙은 반려 후 변경할 수 있습니다.'));
  },async()=>({ok:true}),'증빙 확인');
}
function renderPerfAnnual(data){
  $('perf-summary').append(node('strong','',`최종 득점 ${prettyNumber(data.score)} / ${prettyNumber(data.maximum)}점`),node('span','',data.year_state.closed?'확정 보관':data.ready?'연도 확정 가능':'미확정 항목 확인 필요'));
  const rows=data.items.map(item=>{const actions=node('div','perf-actions');actions.append(perfButton('평가자·등급·가감점',()=>editPerfRating(item)));return [item.indicator.name,item.definition.weight,item.score,item.rating.grade,item.rating.evaluator,item.rating.excluded?'결측 제외':item.reason,item.unconfirmed,actions];});
  $('perf-content').append(perfTable(['지표','배점','득점','등급','평가자','계산 근거','미확정 기록','처리'],rows));
  $('perf-content').append(perfNote('대표 담당자·직원 취합과 누적/증분 정책에 따라 확정된 실적만 반영합니다. 결측은 분모에서 제외하며 총점과 유효 배점을 함께 표시합니다. 비계량 S/A/B/C/D는 배점의 100/95/90/85/80%입니다. 점수가 없는 항목을 임의로 0점 확정하지 않습니다.'));
  const lock=perfButton(data.year_state.closed?'연도 마감 해제':'연도 확정 보관',()=>perfReasonDialog(data.year_state.closed?'연도 마감 해제':'연도 확정 보관','year',{closed:!data.year_state.closed,revision:data.year_state.revision}),true);lock.disabled=!data.year_state.closed&&!data.ready;$('perf-content').append(lock);
}
function editPerfRating(item){
  let evaluator,grade,adjustment,excluded;
  perfDialog(item.indicator.name+' · 최종 평가',box=>{
    box.append(perfNote('평가자를 지정한 뒤 해당 평가자가 내 평가 요청에서 등급을 입력합니다. 결측·가감점은 근거를 남겨야 합니다.'));
    evaluator=perfInput(box,'평가자 사번',item.rating.evaluator||current.user.employee_id);grade=perfInput(box,'평가등급(지정 평가자 본인 입력)',item.rating.grade||'','text',[['','미평가'],...['S','A','B','C','D'].map(x=>[x,x])]);grade.disabled=item.definition.mode!=='text';adjustment=perfInput(box,'가감점',item.rating.adjustment||'0','number');excluded=perfInput(box,'결측으로 제외',Boolean(item.rating.excluded),'checkbox');
    const c=item.aggregate.calculation;if(c)box.append(perfTable(['세부항목','실적','목표','득점'],c.components.map(r=>[r.name,r.actual,r.target,r.score])));
    box.append(perfNote(item.reason));
  },()=>perfPost('rating',{indicator_id:item.indicator.id,revision:item.rating.revision,evaluator:evaluator.value,grade:grade.value,adjustment:adjustment.value,excluded:excluded.checked}));
}
function renderPerfTasks(data){
  if(!data.tasks.length)$('perf-content').append(perfNote('지정된 평가 요청이 없습니다.'));
  for(const task of data.tasks){const r=task.rating,card=node('article','perf-card');card.append(node('h2','',`${r.year}년 · ${task.records[0]?.definition.name||r.indicator}`));for(const record of task.records)card.append(node('p','',record.narrative||'내용 없음'));
    card.append(perfButton('등급 입력',()=>{let grade;perfDialog('지정된 지표 등급 입력',box=>{grade=perfInput(box,'평가등급',r.grade||'A','text',['S','A','B','C','D'].map(x=>[x,x]));},()=>perfPost('grade',{department_id:r.department,year:r.year,indicator_id:r.indicator,revision:r.revision,grade:grade.value}));}));$('perf-content').append(card);}
}
function renderPerfAccounts(data){
  const rows=data.users.map(person=>{const actions=node('div','perf-actions');if(current.user.role!=='superadmin'&&person.role!=='employee'){actions.append(node('span','muted','수퍼관리자 관리 대상'));return [person.employee_id,person.display_name,departmentLabel(person.department_id),roleLabels[person.role],person.account_status==='active'?'활성':'비활성',actions];}for(const [action,label]of [['reset-password','비밀번호 초기화'],['status','활성·비활성'],['handover','담당지표 인계'],...(current.user.role==='superadmin'?[['transfer','근무지 이동']]:[])])actions.append(perfButton(label,()=>editPerfAccount(person,action,data.users)));return [person.employee_id,person.display_name,departmentLabel(person.department_id),roleLabels[person.role],person.account_status==='active'?'활성':'비활성',actions];});$('perf-content').append(perfTable(['사번','이름','근무지','권한','계정상태','처리'],rows));
}
function editPerfAccount(person,action,people=[]){
  let password,old,status,successor,department,checks=[];
  perfDialog(({password:'내 비밀번호 변경','reset-password':'비밀번호 초기화',status:'계정 상태 변경',handover:'담당지표 인수인계',transfer:'근무지 이동'})[action],box=>{
    box.append(perfNote(`${person.display_name||''} · ${person.employee_id} · 변경 시 기존 로그인 세션이 만료됩니다.`));
    if(action==='password')old=perfInput(box,'현재 비밀번호','','password');
    if(['password','reset-password'].includes(action))password=perfInput(box,'새 비밀번호(10~128자)','','password');
    if(action==='status')status=perfInput(box,'계정 상태',person.account_status||'active','text',[['active','활성'],['inactive','휴직·퇴직 등 비활성']]);
    if(action==='handover'){
      successor=perfInput(box,'후임자',null,'text',people.filter(p=>p.employee_id!==person.employee_id&&p.department_id===person.department_id&&p.account_status==='active').map(p=>[p.employee_id,p.display_name+' · '+p.employee_id]));
      for(const key of JSON.parse(person.indicators||'[]'))checks.push({key,input:perfInput(box,indicators(person.department_id).find(k=>k.id===key)?.name||key,true,'checkbox')});
      box.append(perfNote('후임자에게 담당지표를 넘기고 기존 담당에서는 해제하며 대표 담당자를 변경합니다. 기존 실적은 원래 소유자로 보존하고 후임자에게 이전 자료 조회 권한을 부여합니다. 전보가 완료되면 근무지 이동 또는 비활성화를 처리하세요.'));
    }
    if(action==='transfer'){
      department=perfInput(box,'이동 근무지',person.department_id,'text',catalog.departments.map(d=>[d.id,d.name]));const list=node('div','check-list');box.append(list);
      const fill=()=>{list.replaceChildren();checks=[];for(const k of indicators(department.value))checks.push({key:k.id,input:perfInput(list,k.name,department.value===person.department_id&&JSON.parse(person.indicators).includes(k.id),'checkbox')});};department.onchange=fill;fill();
    }
  },()=>perfPost('account',{employee_id:person.employee_id,action,...(password?{password:password.value}:{}),...(old?{old_password:old.value}:{}),...(status?{status:status.value}:{}),...(successor?{successor:successor.value}:{}),...(department?{department_id:department.value}:{}),...(['handover','transfer'].includes(action)?{indicators:checks.filter(c=>c.input.checked).map(c=>c.key)}:{})}));
}
function renderPerfHistory(data){
  $('perf-content').append(perfNote('이전 근무지 실적의 소유자와 귀속 부서는 보존됩니다. 이전 소속 실적은 조회용입니다.'));
  const fileLinks=r=>{const box=node('div');for(const f of r.files||[]){const a=node('a','',f.original_name+' ');a.href='/api/performance/files/'+f.id;box.append(a);}return box;};
  $('perf-content').append(perfTable(['연도','근무지','지표','기간','상태','증빙'],data.records.map(r=>[r.year,departmentLabel(r.department),r.indicator,r.start+' ~ '+r.end,perfStates[r.state],fileLinks(r)])));
  $('perf-content').append(node('h2','','이전 월별 기록'),perfTable(['연도','월','근무지','지표','실적','진행상태','증빙'],data.legacy.map(r=>{const links=node('div');for(const f of r.files||[]){const a=node('a','',f.original_name+' ');a.href='/api/evidence/'+f.id;links.append(a);}return [r.year,r.month,departmentLabel(r.department_id),r.indicator_id,r.actual,r.status,links];})));
}
function renderPerfAudit(data){$('perf-content').append(perfTable(['시각','처리자','작업','대상','사유'],data.events.map(r=>[r.created_at,r.actor,r.action,r.target,r.reason])));}
function renderPerfOverview(data){
  $('perf-summary').append(node('strong','',`전체 근무지 ${data.places.length}곳`));
  const rows=data.places.map(p=>[p.name,`${p.configured} / ${p.total}`,`${p.monthly} / ${p.weekly}`,p.states.draft||0,p.states.submitted||0,p.states.returned||0,p.states.approved||0,perfButton('직원 실적 확인',()=>{$('perf-department').value=p.department_id;perfTab='review';loadPerformance();})]);
  $('perf-content').append(perfNote('기준일에 적용되는 월별·주별 지표 수와 선택 연도의 전체 실적 상태를 표시합니다.'),perfTable(['근무지','설정 지표','월별 / 주별','임시저장','제출','반려','확정','상세'],rows));
}
function renderPerfOps(data){
  $('perf-content').append(perfNote(`자동 백업: ${data.status.ok?'정상':data.status.error||'아직 실행 전'} · 백업 ${data.backups.length}개. DB와 원본 증빙을 함께 보관하며 복원 시 세션을 초기화합니다.`));
  $('perf-content').append(perfButton('지금 백업',async()=>{try{await perfPost('backup',{reason:'수퍼관리자 수동 백업'});await loadPerformance();}catch(e){$('perf-error').textContent=e.message;}},true));
  $('perf-content').append(perfTable(['백업 파일','크기'],data.backups.map(r=>[r.name,prettyNumber(r.size/1024/1024)+' MB'])));
  $('perf-content').append(node('h2','','복구 절차'),perfNote('서버 운영자가 백업을 별도 보관하고 새 디렉터리에 검증 복원한 뒤 서버 DB 경로를 전환합니다. 운영 데이터는 웹에서 덮어쓰지 않습니다. 저장소 README에 백업·복원 명령과 검증 절차가 있습니다. 백업에는 개인정보가 포함되므로 공개 저장소에 올리지 마세요.'));
}
$('perf-form').onsubmit=async event=>{
  event.preventDefault();if(perfBusy||!perfSubmit)return;perfBusy=true;$('perf-save').disabled=true;$('perf-dialog-error').textContent='';
  try{await perfSubmit();$('perf-dialog').close();$('perf-fields').replaceChildren();perfSubmit=null;await loadPerformance();toast('처리했습니다.');}catch(e){$('perf-dialog-error').textContent=e.message;}
  finally{perfBusy=false;$('perf-save').disabled=false;}
};
$('perf-dialog-close').onclick=$('perf-cancel').onclick=()=>{if(!perfBusy){$('perf-dialog').close();$('perf-fields').replaceChildren();$('perf-reason').value='';perfSubmit=null;}};$('perf-dialog').addEventListener('cancel',e=>{if(perfBusy)e.preventDefault();else{$('perf-fields').replaceChildren();$('perf-reason').value='';perfSubmit=null;}});
$('perf-refresh').onclick=loadPerformance;for(const id of ['perf-department','perf-year','perf-date'])$(id).onchange=()=>{if(id==='perf-year')$('perf-date').value=$('perf-year').value+$('perf-date').value.slice(4);loadPerformance();};
for(const b of document.querySelectorAll('#perf-tabs button'))b.onclick=()=>{perfTab=b.dataset.tab;setupPerformance();};
$('perf-password').onclick=()=>editPerfAccount(current.user,'password');
$('perf-copy').onclick=()=>{let source,target;perfDialog('평가연도 기준 복사',box=>{source=perfInput(box,'복사할 원본 연도',perfContext().year,'number');target=perfInput(box,'설정을 생성할 대상 연도',perfContext().year+1,'number');box.append(perfNote('설정만 복사합니다. 실적·증빙·등급·마감 상태는 복사하지 않으며 대상 연도의 기존 설정은 덮어쓰지 않습니다.'));},()=>perfPost('copy-year',{source_year:Number(source.value),year:Number(target.value)}));};
start();
