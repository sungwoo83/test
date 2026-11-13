 'use strict';
let dashboardData=null,dashboardSequence=0;
const svgNamespace='http://www.w3.org/2000/svg';
function clearDashboard(){dashboardData=null;dashboardSequence++;$('dash-metrics').replaceChildren();for(const id of ['dash-total','dash-input','dash-completed'])$(id).textContent='0';$('dash-average').textContent='—';$('dash-error').textContent='';}
async function setupDashboard(){if(!$('dash-month').options.length){for(let m=1;m<=11;m++)$('dash-month').append(new Option(m+'월',m));$('dash-month').value=Math.min(11,Number($('monthly-month').value)||1);$('dash-year').value=$('monthly-year').value;}await loadDashboard();}
async function loadDashboard(){if(!current)return;const sequence=++dashboardSequence,csrf=current.csrf;$('dash-reload').disabled=true;$('dash-error').textContent='';try{const data=await api('/api/dashboard?'+new URLSearchParams({year:Number($('dash-year').value),month:Number($('dash-month').value)}));if(sequence!==dashboardSequence||current?.csrf!==csrf)return;dashboardData=data;renderDashboard();}catch(e){if(sequence===dashboardSequence){$('dash-error').textContent=e.message;$('dash-metrics').replaceChildren();}}finally{if(sequence===dashboardSequence)$('dash-reload').disabled=false;}}
function prettyNumber(value,decimals=3){if(value==null||value==='')return '—';const n=Number(value);return Number.isFinite(n)?n.toLocaleString('ko-KR',{maximumFractionDigits:decimals}):String(value);}
function svgNode(tag,attributes={},text){const el=document.createElementNS(svgNamespace,tag);for(const [name,value]of Object.entries(attributes))el.setAttribute(name,String(value));if(text!==undefined)el.textContent=String(text);return el;}
function dayNumber(iso){return Date.parse(iso+'T00:00:00Z')/86400000;}
function renderDashboard(){const data=dashboardData,s=data.summary;$('dash-greeting').textContent=`${data.employee_name}님 · ${data.department_name}`;$('dash-reference').textContent=data.as_of.replaceAll('-','.');$('dash-days').textContent=!data.period_started?'시작 전':data.period_ended?'마감':`D-${data.remaining_days}`;$('dash-deadline').textContent=data.deadline.replaceAll('-','.');$('dash-total').textContent=s.indicator_count;$('dash-average').textContent=prettyNumber(s.average_achievement);$('dash-input').textContent=s.input_count;$('dash-input-total').textContent=`/ ${s.indicator_count}개`;$('dash-input-note').textContent=`미입력 ${s.missing_count}개 · 임시저장 ${s.draft_count}개`;$('dash-completed').textContent=s.completed_count;$('dash-calendar-note').textContent=`기간 경과 ${prettyNumber(data.elapsed_percent)}%`;$('dash-time-fill').setAttribute('width',Math.max(0,Math.min(100,Number(data.elapsed_percent)))*10);$('dash-enter').disabled=!s.indicator_count;$('dash-metrics').replaceChildren();for(const metric of data.metrics)renderNumericMetric(metric,data);}
function renderNumericMetric(metric,data){const card=node('article','metric-card');card.append(node('h3','',metric.indicator.name));const numbers=node('div','metric-numbers'),actual=node('div'),rate=node('div','metric-rate');actual.append(node('span','metric-label',metric.source==='periodic'?'저장 실적 집계 득점':'최근 실적'),node('strong','',metric.graph_mode==='status'?metric.status:prettyNumber(metric.actual)),node('span','',metric.graph_mode==='status'?'':metric.settings.unit||''));rate.append(node('span','metric-label','달성률·득점률'),node('strong','',metric.achievement_percent!=null?prettyNumber(metric.achievement_percent)+'%':'—'));numbers.append(actual,rate);card.append(numbers,node('p','metric-goal',`목표 ${prettyNumber(metric.settings.target)} ${metric.settings.unit||''} · 가중치 ${prettyNumber(metric.settings.weight)}점`));if(metric.source==='periodic')card.append(node('p','notice',`통합 실적 · ${{draft:'임시저장',submitted:'제출',returned:'반려',approved:'확정'}[metric.workflow_state]||'미입력'} · ${metric.totals?.basis||''} · ${metric.totals?.period_count||0}기간. 최신 누적값·증분 합산·기간 평균 설정을 반영합니다.`));const tooltip=node('p','chart-tooltip','그래프의 점을 눌러 수치를 확인하세요.');card.append(metricChart(metric,data,tooltip),tooltip);const actions=node('div','metric-card-foot'),edit=node('button','text-button','실적 입력 ↗');edit.type='button';edit.onclick=()=>goToDashboardEntry(metric.indicator.id);actions.append(node('span','input-state',`${data.month}월 ${metric.input_state}`),node('span','evidence-note',`증빙 ${metric.evidence_count}개`),edit);card.append(actions);const details=node('details','metric-details');details.append(node('summary','','월별 수치·향후계획'));for(const point of metric.history)details.append(node('p','',`${point.month}월: ${metric.graph_mode==='status'?point.status||'미입력':prettyNumber(point.actual)+' '+(point.unit||'')} · ${point.percent!=null?prettyNumber(point.percent)+'%':'—'}`));details.append(node('p','metric-plan',metric.future_plan||'향후계획 없음'));const reference=manualReference(metric.settings);if(reference)details.append(reference);card.append(details);$('dash-metrics').append(card);}
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
  if(!values.length)svg.append(svgNode('text',{x:left+pw/2,y:top+ph/2-10,'text-anchor':'middle',class:'chart-empty'},metric.totals?.fully_excluded?'해당 기간 세부항목이 결측이므로 득점률을 산정하지 않습니다.':metric.history.some(p=>p.actual!=null)?'해당 월 목표가 없어 달성률을 표시하지 않습니다.':'실적을 입력하면 그래프가 표시됩니다.'));
  if(metric.graph_mode==='percent'&&values.some(v=>v<min||v>max))tooltip.textContent='표시 범위를 넘는 값은 상·하단에 표시합니다. 점을 누르면 실제 달성률을 확인할 수 있습니다.';
  return svg;
}
async function goToDashboardEntry(indicatorId){
  if(!dashboardData)return;const data=dashboardData;
  if(indicatorId?data.metrics.some(m=>m.indicator.id===indicatorId&&m.source==='periodic'):data.metrics.some(m=>m.source==='periodic')){
    $('perf-year').value=data.year;$('perf-date').value=data.as_of;perfTab='own';location.hash='#performance';loadPerformance();return;
  }
  location.hash='#monthly-panel';
  $('monthly-year').value=data.year;$('monthly-month').value=data.month;
  if([...$('monthly-department').options].some(o=>o.value===data.department_id))$('monthly-department').value=data.department_id;
  await loadMonthly();if(indicatorId){const item=monthly?.items.find(i=>i.indicator.id===indicatorId);if(item&&monthly.period.status!=='closed')openActual(item,false);else if(monthly?.period.status==='closed')toast('마감된 월입니다. 관리자에게 확인해 주세요.');}
}
$('dash-reload').onclick=loadDashboard;for(const id of ['dash-year','dash-month'])$(id).onchange=loadDashboard;$('dash-enter').onclick=()=>goToDashboardEntry();
