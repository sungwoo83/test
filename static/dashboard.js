'use strict';
let dashboardData=null,dashboardSequence=0,dashboardFilter='all';
const svgNamespace='http://www.w3.org/2000/svg';
const healthLabels={good:'양호',warning:'경고',risk:'위험',pending:'판정 대기'};
function clearDashboard(){
  dashboardData=null;dashboardSequence++;dashboardFilter='all';$('dash-metrics').replaceChildren();$('dash-attention-list').replaceChildren();$('dash-attention').hidden=true;
  for(const id of ['dash-total','dash-input','dash-attention-count','dash-risk','dash-warning'])$(id).textContent='0';
  $('dash-average').textContent='—';$('dash-error').textContent='';
}
async function setupDashboard(){
  if(!$('dash-month').options.length){for(let m=1;m<=11;m++)$('dash-month').append(new Option(m+'월',m));$('dash-month').value=Math.min(11,Number($('monthly-month').value)||1);$('dash-year').value=$('monthly-year').value;}
  await loadDashboard();
}
function dashboardContext(){return {year:Number($('dash-year').value),month:Number($('dash-month').value)};}
async function loadDashboard(){
  if(!current)return;const sequence=++dashboardSequence,csrf=current.csrf,context=dashboardContext();$('dash-error').textContent='';$('dash-reload').disabled=true;
  try{
    const data=await api('/api/dashboard?'+new URLSearchParams(context));if(sequence!==dashboardSequence||current?.csrf!==csrf)return;
    dashboardData=data;renderDashboard();
  }catch(e){if(sequence===dashboardSequence){dashboardData=null;$('dash-metrics').replaceChildren();$('dash-attention').hidden=true;$('dash-enter').disabled=true;for(const id of ['dash-total','dash-average','dash-input','dash-attention-count','dash-risk','dash-warning'])$(id).textContent='—';$('dash-error').textContent=e.message;}}
  finally{if(sequence===dashboardSequence)$('dash-reload').disabled=false;}
}
function prettyNumber(value,decimals=1){if(value==null||value==='')return '—';const n=Number(value);return Number.isFinite(n)?n.toLocaleString('ko-KR',{maximumFractionDigits:decimals}):String(value);}
function svgNode(tag,attributes={},text){const el=document.createElementNS(svgNamespace,tag);for(const [name,value] of Object.entries(attributes))el.setAttribute(name,String(value));if(text!==undefined)el.textContent=String(text);return el;}
function dayNumber(iso){return Date.parse(iso+'T00:00:00Z')/86400000;}
function renderDashboard(){
  const data=dashboardData,s=data.summary;
  $('dash-greeting').textContent=`${data.employee_name}님 · ${data.department_name}의 담당지표를 확인하세요.`;
  $('dash-reference').textContent=data.as_of.replaceAll('-','.')+' 기준';$('dash-days').textContent=!data.period_started?'시작 전':data.period_ended?'마감':`D-${data.remaining_days}`;
  $('dash-deadline').textContent=data.deadline.replaceAll('-','.');$('dash-total').textContent=s.indicator_count;
  $('dash-average').textContent=prettyNumber(s.average_achievement);$('dash-input').textContent=s.input_count;$('dash-input-total').textContent=`/ ${s.indicator_count}개`;
  $('dash-input-note').textContent=`${data.month}월 미입력 ${s.missing_count}개 · 임시저장 ${s.draft_count}개`;
  $('dash-attention-count').textContent=s.warning_count+s.risk_count;$('dash-risk').textContent=s.risk_count;$('dash-warning').textContent=s.warning_count;
  $('dash-calendar-note').textContent=`기간 경과 ${prettyNumber(data.elapsed_percent)}% · 조회 시점 기준`;
  $('dash-time-fill').setAttribute('width',Math.max(0,Math.min(100,Number(data.elapsed_percent)))*10);
  for(const code of ['good','warning','risk','pending'])$('dash-filter-'+code).textContent=s[code+'_count'];$('dash-filter-all').textContent=s.indicator_count;
  const attention=data.metrics.filter(m=>['risk','warning'].includes(m.health.code)||m.current_month_missing).sort((a,b)=>({risk:0,warning:1,pending:2,good:3}[a.health.code]-{risk:0,warning:1,pending:2,good:3}[b.health.code])).slice(0,3);
  $('dash-attention').hidden=!attention.length;$('dash-attention-list').replaceChildren();
  for(const metric of attention){
    const button=node('button','attention-item');button.type='button';button.append(node('span','health-badge '+metric.health.code,metric.health.label),node('strong','',metric.indicator.name),node('span','attention-reason',metric.current_month_missing?`${data.month}월 실적 입력을 확인해 주세요.`:metric.health.reason),node('span','','↗'));
    button.onclick=()=>{dashboardFilter='all';renderMetricCards();document.getElementById('dash-card-'+metric.indicator.id)?.scrollIntoView({behavior:'smooth',block:'center'});};$('dash-attention-list').append(button);
  }
  $('dash-enter').disabled=s.indicator_count===0;$('dash-enter').textContent=data.month+'월 실적 입력 ↗';renderMetricCards();
}
function renderMetricCards(){
  const data=dashboardData;if(!data)return;$('dash-metrics').replaceChildren();
  for(const button of document.querySelectorAll('#dash-filters button'))button.setAttribute('aria-pressed',String(button.dataset.health===dashboardFilter));
  const metrics=data.metrics.filter(m=>dashboardFilter==='all'||m.health.code===dashboardFilter);
  for(const metric of metrics){
    const card=node('article','metric-card '+metric.health.code);card.id='dash-card-'+metric.indicator.id;
    const header=node('div','metric-card-heading'),heading=node('div');heading.append(node('span','metric-kicker',`${metric.indicator.category||'공통'} · ${metric.indicator.type}`),node('h3','',metric.indicator.name));
    header.append(heading,node('span','health-badge '+metric.health.code,metric.health.label));card.append(header,node('p','health-reason',metric.health.reason));
    const numbers=node('div','metric-numbers'),actual=node('div'),rate=node('div','metric-rate');
    actual.append(node('span','metric-label',metric.settings.entry_mode==='text'?'최근 진행상태':'최근 실적'));
    const actualLine=node('div','metric-actual');actualLine.append(node('strong','',metric.settings.entry_mode==='text'?metric.status:prettyNumber(metric.actual)),node('span','',metric.settings.entry_mode==='text'?'':metric.settings.unit||''));actual.append(actualLine);
    actual.append(node('small','muted',metric.source_month?`${metric.source_month}월 기준${metric.source_month!==data.month?' · 이전 실적 사용':''}`:'실적 입력 전'));
    rate.append(node('span','metric-label','목표 대비 달성률'),node('strong','',metric.achievement_percent!=null?prettyNumber(metric.achievement_percent)+'%':'—'));
    numbers.append(actual,rate);card.append(numbers);
    const goal=node('div','metric-goal');goal.append(node('span','',metric.settings.entry_mode==='text'?'완료 상태로 제출':`목표 ${prettyNumber(metric.settings.target)} ${metric.settings.unit||''}`),node('span','',`가중치 ${prettyNumber(metric.settings.weight)}점`));card.append(goal);
    const refView=manualReference(metric.settings);if(refView)card.append(refView);
    const chartCaption=node('div','chart-caption');chartCaption.append(node('span','',metric.graph_mode==='status'?'월별 진행상태':metric.graph_mode==='actual'?'월별 실적 · 목표 미설정':'월별 목표 대비 달성률'),node('small','',metric.graph_mode==='status'?'상태·마감 기준':`${metric.curve_label} · 시점 계획 ${prettyNumber(metric.expected_percent)}%`));card.append(chartCaption);
    const tooltip=node('p','chart-tooltip','그래프의 점에 마우스를 올리거나 눌러 상세 수치를 확인하세요.');tooltip.setAttribute('aria-live','polite');
    card.append(metricChart(metric,data,tooltip),tooltip);
    const legend=node('div','chart-legend');legend.append(node('span','legend-actual','● '+(metric.graph_mode==='status'?'진행상태':'입력 실적')));if(metric.graph_mode==='percent')legend.append(node('span','legend-plan','┄ 시기별 계획'));card.append(legend);
    const foot=node('div','metric-card-foot'),input=node('span',metric.current_month_missing?'input-state input-needed':'input-state',`${data.month}월 ${metric.input_state}`),edit=node('button','text-button','실적 입력 ↗');edit.type='button';edit.onclick=()=>goToDashboardEntry(metric.indicator.id);foot.append(input,node('span','evidence-note',`증빙 ${metric.evidence_count}개`),edit);card.append(foot);
    const details=node('details','metric-details');details.append(node('summary','','월별 수치 · 향후계획 보기'));
    const table=node('table','metric-month-table'),thead=node('thead'),head=node('tr');for(const label of ['월','실적/상태','달성률'])head.append(node('th','',label));thead.append(head);table.append(thead);
    const tbody=node('tbody');for(const point of metric.history){const row=node('tr');row.append(node('td','',point.month+'월'),node('td','',point.present?(metric.graph_mode==='status'?point.status:prettyNumber(point.actual)+(point.unit?' '+point.unit:'')):'미입력'),node('td','',point.percent!=null?prettyNumber(point.percent)+'%':'—'));tbody.append(row);}table.append(tbody);details.append(table,node('p','metric-plan-label','향후계획'),node('p','metric-plan',metric.future_plan||'등록된 향후계획이 없습니다.'));card.append(details);$('dash-metrics').append(card);
  }
  if(!metrics.length){
    const empty=node('div','dashboard-empty');empty.append(node('span','empty-symbol','◫'),node('h3','',data.summary.indicator_count?'해당 상태의 지표가 없습니다.':'담당지표를 설정하면 대시보드가 시작됩니다.'),node('p','muted',data.summary.indicator_count?'다른 상태 필터를 선택해 주세요.':'나의 업무 홈에서 담당지표를 선택하고, 관리자에게 목표 설정을 요청하세요.'));
    const button=node('a','secondary','나의 업무 홈으로');button.href='#home';if(current.user.department_id!=='SYS-PERFORMANCE')empty.append(button);else{empty.querySelector('h3').textContent='전체 근무지 실적은 월별 실적 확인에서 확인하세요.';empty.querySelector('p').textContent='이 대시보드는 본인의 담당지표를 표시합니다. 성과혁신실은 평가총괄 운영 부서입니다.';button.href='#review';button.textContent='월별 실적 확인으로';empty.append(button);}$('dash-metrics').append(empty);
  }
}
function metricChart(metric,data,tooltip){
  const width=600,height=235,left=48,right=18,top=20,bottom=32,pw=width-left-right,ph=height-top-bottom;
  const start=dayNumber(data.year+'-01-01'),end=dayNumber(data.deadline),asOf=dayNumber(data.as_of),x=iso=>left+Math.max(0,Math.min(1,(dayNumber(iso)-start)/(end-start)))*pw;
  const statusValues={'미착수':0,'진행중':1,'완료':2};
  const value=point=>metric.graph_mode==='status'?(point.present?statusValues[point.status]:null):metric.graph_mode==='percent'?(point.percent!=null?Number(point.percent):null):(point.actual!=null?Number(point.actual):null);
  const values=metric.history.map(value).filter(v=>v!=null&&Number.isFinite(v));let min=0,max=100;
  if(metric.graph_mode==='status')max=2;
  else if(metric.graph_mode==='percent'){max=Math.min(200,Math.max(100,Math.ceil(Math.max(0,...values)/20)*20));min=Math.max(-100,Math.min(0,Math.floor(Math.min(0,...values)/20)*20));}
  else{max=Math.max(1,Math.max(0,...values)*1.15);min=Math.min(0,Math.min(0,...values)*1.15);}
  const y=n=>top+ph-(Math.max(min,Math.min(max,n))-min)/(max-min)*ph;
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,class:'metric-chart',role:'img','aria-label':metric.indicator.name+'의 1월부터 11월까지 월별 추이'});
  const ticks=metric.graph_mode==='status'?[0,1,2]:[min,min+(max-min)/2,max];
  for(const tick of ticks){svg.append(svgNode('line',{x1:left,y1:y(tick),x2:width-right,y2:y(tick),class:'chart-grid'}),svgNode('text',{x:left-9,y:y(tick)+4,'text-anchor':'end',class:'chart-axis'},metric.graph_mode==='status'?['미착수','진행중','완료'][tick]:(Math.abs(tick)>=10000?tick.toExponential(0):prettyNumber(tick))+(metric.graph_mode==='percent'?'%':'')));}
  for(const point of metric.history)svg.append(svgNode('text',{x:x(data.year+'-'+String(point.month).padStart(2,'0')+'-'+new Date(Date.UTC(data.year,point.month,0)).getUTCDate()),y:height-9,'text-anchor':'middle',class:'chart-axis chart-month','data-month':point.month},point.month+'월'));
  if(metric.graph_mode==='percent'){
    const planned=[{date:data.year+'-01-01',percent:'0'},...metric.planned];
    if(asOf>=start&&asOf<=end&&!planned.some(p=>p.date===data.as_of))planned.push({date:data.as_of,percent:metric.expected_percent});
    planned.sort((a,b)=>a.date.localeCompare(b.date));svg.append(svgNode('path',{d:planned.map((p,i)=>(i?'L':'M')+x(p.date)+','+y(Number(p.percent))).join(' '),class:'chart-plan-path'}));
  }
  if(asOf>=start&&asOf<=end)svg.append(svgNode('line',{x1:x(data.as_of),x2:x(data.as_of),y1:top,y2:top+ph,class:'chart-reference'}));
  let segment=[];const flush=()=>{if(segment.length>1)svg.append(svgNode('path',{d:segment.map((p,i)=>(i?'L':'M')+x(p.date)+','+y(value(p))).join(' '),class:'chart-actual-path'}));segment=[];};
  for(const point of metric.history){if(value(point)==null){flush();continue;}segment.push(point);}flush();
  const clickable=[];
  for(const point of metric.history){const v=value(point);if(v==null||!Number.isFinite(v))continue;
    const label=metric.graph_mode==='status'?`${point.month}월 ${point.status}`:`${point.month}월 실적 ${prettyNumber(point.actual)} ${point.unit||''}${point.percent!=null?' · 달성률 '+prettyNumber(point.percent)+'%':''}${point.target!=null?' · 당시 목표 '+prettyNumber(point.target):''}`;
    clickable.push({date:point.date,label:label+(point.narrative?' · '+point.narrative:'')});
    const circle=svgNode('circle',{cx:x(point.date),cy:y(v),r:5,class:'chart-point',tabindex:'0',role:'button','aria-label':label});circle.append(svgNode('title',{},label));
    for(const event of ['mouseenter','focus','click'])circle.addEventListener(event,()=>tooltip.textContent=label+(point.narrative?' · '+point.narrative:''));svg.append(circle);
  }
  svg.addEventListener('click',event=>{if(!clickable.length)return;const box=svg.getBoundingClientRect(),at=(event.clientX-box.left)/box.width*width;const nearest=clickable.reduce((a,b)=>Math.abs(x(a.date)-at)<=Math.abs(x(b.date)-at)?a:b);tooltip.textContent=nearest.label;});
  if(!values.length)svg.append(svgNode('text',{x:left+pw/2,y:top+ph/2-10,'text-anchor':'middle',class:'chart-empty'},metric.history.some(p=>p.actual!=null)?'해당 월 목표가 없어 달성률을 표시하지 않습니다.':'실적을 입력하면 그래프가 표시됩니다.'));
  if(metric.graph_mode==='percent'&&values.some(v=>v<min||v>max))tooltip.textContent='표시 범위를 넘는 값은 상·하단에 표시합니다. 점을 누르면 실제 달성률을 확인할 수 있습니다.';
  return svg;
}
async function goToDashboardEntry(indicatorId){
  if(!dashboardData)return;const data=dashboardData;location.hash='#monthly-panel';
  $('monthly-year').value=data.year;$('monthly-month').value=data.month;
  if([...$('monthly-department').options].some(o=>o.value===data.department_id))$('monthly-department').value=data.department_id;
  await loadMonthly();if(indicatorId){const item=monthly?.items.find(i=>i.indicator.id===indicatorId);if(item&&monthly.period.status!=='closed')openActual(item,false);else if(monthly?.period.status==='closed')toast('마감된 월입니다. 관리자에게 확인해 주세요.');}
}
$('dash-reload').onclick=loadDashboard;for(const id of ['dash-year','dash-month'])$(id).onchange=loadDashboard;
$('dash-enter').onclick=()=>goToDashboardEntry();
for(const button of document.querySelectorAll('#dash-filters button'))button.onclick=()=>{dashboardFilter=button.dataset.health;renderMetricCards();};
start();
