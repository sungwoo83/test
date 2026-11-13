'use strict';
const $ = id => document.getElementById(id);
let catalog, current, mode = 'login', saving = false;
async function api(path, data) {
  const requestCsrf=current?.csrf;
  const options = data ? {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'PerformanceManager'},body:JSON.stringify(data)} : {};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login' && current?.csrf===requestCsrf) {
      current = null; clearSessionViews(); $('profile-dialog').close(); $('action-dialog').close(); $('evidence-dialog').close(); $('home').hidden = true; $('auth').hidden = false;
    }
    throw new Error(body.error || '요청에 실패했습니다.');
  }
  return body;
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function dept(id) { return catalog.departments.find(d => d.id === id); }
function indicators(id) { return catalog.indicators_by_department[id] || []; }
function populateSelect(id) {
  const select = $(id); select.replaceChildren(new Option('근무지를 선택해 주세요', ''));
  const groups = new Map();
  for (const d of catalog.departments) {
    if (!groups.has(d.group)) { const group = node('optgroup'); group.label = d.group; groups.set(d.group, group); select.append(group); }
    groups.get(d.group).append(new Option(d.name, d.id));
  }
}
function selected(prefix) { return [...$(prefix+'-indicators').querySelectorAll('input:checked')].map(c => c.value); }
function updateCount(prefix) { $(prefix+'-count').textContent = selected(prefix).length+'개 선택'; }
function choices(prefix, values = []) {
  const list = $(prefix+'-indicators'); list.replaceChildren();
  const items = indicators($(prefix+'-department').value);
  if (!items.length) list.append(node('p','empty',$(prefix+'-department').value==='SYS-PERFORMANCE' ? '평가총괄 부서는 담당지표 없이 가입합니다. 관리자 권한은 별도 지정됩니다.' : '근무지를 먼저 선택해 주세요.'));
  for (const item of items) {
    const label = node('label','check-row');
    const input = node('input'); input.type = 'checkbox'; input.value = item.id; input.checked = values.includes(item.id);
    input.addEventListener('change', () => updateCount(prefix));
    const content = node('span','check-copy'); content.append(node('span','',item.name),node('small','muted',item.category+' · '+item.type));
    label.append(input, content); list.append(label);
  }
  updateCount(prefix);
}
function setMode(value) {
  mode = value; const signup = value === 'register';
  $('login-tab').setAttribute('aria-selected', String(!signup)); $('register-tab').setAttribute('aria-selected',String(signup));
  $('register-fields').hidden = !signup; $('signup-name').hidden=!signup; $('register-name').required=signup; $('register-name').disabled=!signup;
  $('auth-title').textContent = signup ? '성과관리, 함께 시작해요' : '다시 만나 반갑습니다';
  $('auth-description').textContent = signup ? '이름, 근무지와 담당지표를 등록하면 준비가 끝납니다.' : '사번으로 로그인하고 나의 업무를 확인하세요.';
  $('auth-submit').textContent = signup ? '회원가입 후 시작하기 →' : '로그인 →';
  $('password').value = ''; $('password-confirm').value = '';
  $('password').minLength = signup ? 10 : 1; $('password').autocomplete = signup ? 'new-password' : 'current-password';
  $('password-confirm').required = signup; $('register-department').required = signup; $('auth-error').textContent = '';
}
async function refresh(landing=false) {
  const firstLoad=!current,response=await api('/api/me');if(firstLoad)clearSessionViews();current=response;
  if(landing){$('review-department').value='';location.hash=isManager()?'#review':'#dashboard';}
  else if(firstLoad&&isManager()&&(!location.hash||location.hash==='#home'))location.hash='#review';
  render(); displayPage(); await setupManagement();
}
function render() {
  $('auth').hidden = true; $('home').hidden = false;
  const u = current.user, d = dept(u.department_id), items = indicators(u.department_id).filter(k => u.indicators.includes(k.id));
  $('assignment-notice').hidden=Boolean(u.assignment_confirmed);
  $('header-employee').textContent = (u.display_name?u.display_name+' · ':'')+'사번 '+u.employee_id;
  $('department-name').textContent = d.name; $('department-group').textContent = d.group+' · 사번 '+u.employee_id;
  $('total-count').textContent = items.length;
  $('quant-count').textContent = items.filter(k => k.type === '계량').length;
  $('qual-count').textContent = items.filter(k => k.type === '비계량').length;
  const list = $('my-indicators'); list.replaceChildren();
  for (const [i,k] of items.entries()) {
    const row = node('div','indicator-row'), text = node('div','indicator-copy');
    text.append(node('strong','',k.name),node('span','muted small',`${k.id} · ${k.category} · 편람 ${k.page}쪽`));
    row.append(node('span','index',String(i+1).padStart(2,'0')),text,node('span',k.type === '계량' ? 'tag teal' : 'tag purple',k.type)); list.append(row);
  }

}
function displayPage(navigate=false){
  if(!current)return;
  const routes={
    '#performance':{view:'performance-view',nav:'performance-nav',label:'통합 성과관리'},
    '#dashboard':{view:'dashboard-view',nav:'dashboard-nav',label:'성과 대시보드'},
    '#monthly-panel':{view:'monthly-view',nav:'monthly-nav',label:'월별 실적 관리'},
    '#review':{view:'review-view',nav:'review-nav',label:'월별 실적 확인'},
    '#roles':{view:'roles-view',nav:'roles-nav',label:'관리자 권한 제어'},
    '#super-panel':{view:'admin-view',nav:'super-nav',label:'전체 지표 관리'}
  };
  const homeRoute={view:'work-view',nav:'work-nav',label:'나의 업무 홈'};
  let route=routes[location.hash]||(location.hash==='#home'?homeRoute:routes['#dashboard']);
  if(['admin-view','roles-view'].includes(route.view)&&current.user.role!=='superadmin')route=homeRoute;
  if(route.view==='review-view'&&!isManager())route=homeRoute;
  for(const id of ['performance-view','dashboard-view','work-view','monthly-view','review-view','admin-view','roles-view'])$(id).hidden=id!==route.view;
  document.querySelector('.breadcrumb').textContent='/ '+route.label;
  document.title=route.label+' | 내부평가 성과관리';
  for(const id of ['performance-nav','dashboard-nav','work-nav','monthly-nav','review-nav','super-nav','roles-nav']){
    $(id).classList.toggle('nav-active',id===route.nav);
    if(id===route.nav)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');
  }
  if(navigate){
    const heading=$(route.view).querySelector('.page-heading h1');
    if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
    window.scrollTo({top:0});
  }
}
window.addEventListener('hashchange',()=>{displayPage(true);if(current&&location.hash==='#dashboard')loadDashboard();if(current&&location.hash==='#performance')loadPerformance();});

function openProfile(title) {
  $('dialog-title').textContent = title; $('profile-error').textContent = '';
  $('change-department').value = current.user.department_id; $('change-department').disabled=current.user.role==='superadmin'; choices('change',current.user.indicators); $('profile-dialog').showModal();
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; setTimeout(() => $('toast').hidden=true, 4000); }
$('login-tab').onclick = () => setMode('login'); $('register-tab').onclick = () => setMode('register');
for (const prefix of ['register','change']) $(prefix+'-department').addEventListener('change', () => choices(prefix));
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault(); if (saving) return;
  $('auth-error').textContent='';
  if(mode==='register'&&!$('register-name').value.trim()){$('auth-error').textContent='이름을 입력해 주세요.';return;}
  if (mode === 'register' && $('password').value !== $('password-confirm').value) { $('auth-error').textContent='비밀번호 확인이 일치하지 않습니다.'; return; }
  if (mode === 'register' && $('register-department').value !== 'SYS-PERFORMANCE' && !selected('register').length) { $('auth-error').textContent='담당지표를 하나 이상 선택해 주세요.'; return; }
  saving=true; $('auth-submit').disabled=true;
  try {
    await api('/api/'+mode,{employee_id:$('employee').value.trim(),password:$('password').value,...(mode==='register'?{display_name:$('register-name').value.trim()}:{}),department_id:$('register-department').value,indicators:selected('register')});
    $('password').value=''; $('password-confirm').value=''; $('register-name').value=''; await refresh(true);
  } catch(e) { $('auth-error').textContent=e.message; }
  finally { saving=false; $('auth-submit').disabled=false; }
});
$('change-workplace').onclick=()=>openProfile('근무지 변경'); $('edit-indicators').onclick=()=>openProfile('담당지표 수정');
$('close-dialog').onclick=$('cancel-change').onclick=()=>$('profile-dialog').close();
$('profile-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(saving) return; $('profile-error').textContent='';
  if($('change-department').value !== 'SYS-PERFORMANCE' && !selected('change').length) { $('profile-error').textContent='담당지표를 하나 이상 선택해 주세요.'; return; }
  saving=true; $('save-profile').disabled=true;
  try { await api('/api/profile',{csrf:current.csrf,revision:current.user.revision,department_id:$('change-department').value,indicators:selected('change')}); await refresh(); $('profile-dialog').close(); toast('근무지와 담당지표를 저장했습니다.'); }
  catch(e) { $('profile-error').textContent=e.message; }
  finally { saving=false; $('save-profile').disabled=false; }
});
$('logout').onclick=async()=>{ try { await api('/api/logout',{csrf:current.csrf}); current=null;clearSessionViews(); $('home').hidden=true; $('auth').hidden=false; setMode('login'); } catch(e) { toast(e.message); } };
async function start(){
  $('auth-submit').disabled=true;
  try { catalog=await api('/api/catalog'); $('demo-notice').hidden=!catalog.demo; $('home-demo-notice').hidden=!catalog.demo; for(const prefix of ['register','change']) { populateSelect(prefix+'-department'); choices(prefix); }
    try { await refresh(); } catch(e) { if(e.message !== '로그인이 필요합니다.') $('auth-error').textContent=e.message; }
  } catch(e){ $('auth-error').textContent='서버에 연결할 수 없습니다. 새로고침해 주세요.'; return; }
  $('auth-submit').disabled=false;
}


const roleLabels={employee:'직원',admin:'관리자',superadmin:'수퍼관리자'};
let monthly=null,masterItems=[],actionRequest=null,monthlySequence=0;
function isManager(){return current && ['admin','superadmin'].includes(current.user.role);}
function evalDepartments(){return catalog.departments.filter(d=>d.kind!=='운영');}
function departmentLabel(id){return dept(id)?.name || id || '—';}
async function setupManagement(){
  const u=current.user;
  $('header-role').textContent=roleLabels[u.role] || '직원';
  $('super-panel').hidden=$('super-nav').hidden=$('roles-nav').hidden=u.role!=='superadmin';
  $('audit-panel').hidden=!isManager();$('review-nav').hidden=!isManager();
  $('scope-description').textContent=isManager()?"본인의 실적을 입력합니다. 직원 실적은 ‘월별 실적 확인’에서 관리하세요.":'본인 근무지의 담당지표 실적을 입력합니다.';
  const select=$('monthly-department'),old=select.value;
  select.replaceChildren();
  const scope=u.role==='superadmin'?evalDepartments():evalDepartments().filter(d=>d.id===(u.role==='admin'?u.admin_department_id:u.department_id));
  for(const d of scope)select.append(new Option(d.name,d.id));
  if(scope.some(d=>d.id===old))select.value=old;
  select.disabled=u.role!=='superadmin';
  if(!$('monthly-month').options.length){
    for(let m=1;m<=12;m++)$('monthly-month').append(new Option(m+'월',m));
    const localParts=new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',year:'numeric',month:'numeric'}).formatToParts(new Date());
    $('monthly-month').value=localParts.find(p=>p.type==='month').value;
    $('monthly-year').value=localParts.find(p=>p.type==='year').value;
  }
  await loadMonthly();await setupDashboard();setupPerformance();
  if(u.role==='superadmin'){await loadSuper();await loadRoleControl();}
  if(isManager()){await setupReview();await loadAudit();}
}
function monthContext(){return {department_id:$('monthly-department').value,year:Number($('monthly-year').value),month:Number($('monthly-month').value)};}
async function loadMonthly(){
  const sequence=++monthlySequence;
  $('monthly-error').textContent='';monthly=null;
  $('close-month').hidden=$('reopen-month').hidden=true;
  $('monthly-rows').replaceChildren();$('period-status').textContent='';
  if(!$('monthly-department').value){
    $('monthly-error').textContent='현재 소속에는 평가대상 지표가 없습니다. 운영 권한은 별도 지정됩니다.';return;
  }
  try{
    const context=monthContext(),q=new URLSearchParams(context),response=await api('/api/monthly?'+q);
    if(sequence!==monthlySequence)return;
    monthly={...response,context};
    const closed=monthly.period.status==='closed';
    $('period-status').textContent=closed?'마감':'입력 가능';$('period-status').className='tag '+(closed?'closed-tag':'open-tag');
    $('close-month').hidden=!isManager()||closed;
    $('reopen-month').hidden=!isManager()||!closed;
    for(const item of monthly.items){
      const k=item.indicator,r=item.record,config=r?JSON.parse(r.snapshot):item.settings,row=node('tr'),name=node('td','',k.name);
      name.append(node('small','',k.type+(config.unit?' · '+config.unit:'')+(r?' · 작성 당시: '+JSON.parse(r.snapshot).name:'')));
      const goal=node('td','',config.entry_mode==='text'?'서술형 · 달성률 미산정':config.target!=null?config.target+(config.unit?' '+config.unit:''):'목표 미설정');
      goal.append(node('small','',`가중치 ${config.weight ?? '미설정'}${config.weight!=null?'점':''}`));
      const refView=manualReference(config);if(refView)name.append(refView);
      const actual=node('td','',r?(config.entry_mode==='text'?(r.narrative||'내용 미입력'):(r.actual!==''?r.actual:'수치 미입력')):'미입력');actual.className='narrative-cell';
      if(r?.adjusted!=null)actual.append(node('small','',`조정: ${r.adjusted} · 반영: ${r.adjusted}`));
      const rate=node('td','',r?r.achievement.label:'—');if(r?.achievement.formula)rate.append(node('small','',r.achievement.formula));
      const status=node('td'),badge=node('span','tag '+({'미착수':'neutral','진행중':'teal','완료':'complete'}[r?.status||'미착수']),r?.status||'미착수');status.append(badge);
      const plans=node('td','plan-cell',r?.future_plan||'—'),files=node('td');
      const evidenceButton=node('button','secondary',`증빙 ${r?.attachments?.length||0}개`);evidenceButton.type='button';evidenceButton.disabled=!r;evidenceButton.onclick=()=>openEvidence(item);files.append(evidenceButton);
      const actions=node('td');
      const save=node('button','secondary','입력·수정');save.type='button';save.disabled=closed || r?.adjusted!=null || !current.user.assignment_confirmed;save.onclick=()=>openActual(item,false);actions.append(save);
      if(isManager()){
        if(current.user.role==='superadmin'){const setting=node('button','secondary','목표·가중치');setting.type='button';setting.disabled=closed;setting.onclick=()=>openSettings(item);actions.append(setting);}
        if(r&&config.entry_mode!=='text'){const adjust=node('button','secondary','조정');adjust.type='button';adjust.disabled=closed;adjust.onclick=()=>openActual(item,true);actions.append(adjust);}
      }
      row.append(name,goal,actual,rate,status,plans,files,actions);$('monthly-rows').append(row);
    }
    if(!monthly.items.length){const row=node('tr'),cell=node('td','empty-row','선택된 담당지표가 없습니다.');cell.colSpan=8;row.append(cell);$('monthly-rows').append(row);}
  }catch(e){if(sequence===monthlySequence)$('monthly-error').textContent=e.message;}
}
function field(id,label,value='',options={}){
  const wrapper=node('div'),caption=node('label','',label);caption.htmlFor=id;
  let input;
  if(options.options){input=node('select');for(const [v,text] of options.options)input.append(new Option(text,v));}
  else if(options.multiline){input=node('textarea');input.rows=3;input.maxLength=options.maxLength||3000;}
  else{input=node('input');input.type='text';input.maxLength=options.maxLength||200;if(options.numeric){input.type='number';input.step='0.000001';input.inputMode='decimal';}}
  input.id=id;input.value=value??'';if(options.required)input.required=true;
  wrapper.append(caption,input);$('action-fields').append(wrapper);return input;
}
function action(title,description,path,build,payload){
  $('action-title').textContent=title;$('action-description').textContent=description;$('action-error').textContent='';$('action-reason').value='';
  const actualEntry=path==='/api/monthly/actual';
  $('action-reason').hidden=actualEntry; $('action-reason').required=!actualEntry;
  document.querySelector('label[for="action-reason"]').hidden=actualEntry;
  if(actualEntry)$('action-reason').value='월별 실적 등록·수정';
  $('action-fields').replaceChildren();build();actionRequest={path,payload};$('action-dialog').showModal();
}
function previewAchievement(config,value){
  if(config.entry_mode==='text')return '서술형 지표 · 달성률 미산정';
  if(value==='')return '수치를 입력하면 목표 대비 달성률이 표시됩니다.';
  const actual=Number(value),target=Number(config.target);
  if(config.target==null||config.target===''||!Number.isFinite(target)||target<=0)return '목표치 미설정 또는 0 이하 · 달성률 산정 불가';
  if(!Number.isFinite(actual))return '올바른 수치를 입력해 주세요.';
  if(config.direction==='lower'&&actual<=0)return '실적 0 이하 · 별도 확인 필요';
  const percent=(config.direction==='lower'?target/actual:actual/target)*100;
  return `목표 대비 ${percent.toFixed(2)}% · ${config.direction==='lower'?'목표 ÷ 실적':'실적 ÷ 목표'} × 100 (저장 전 예상값)`;
}
function manualReference(config){
  const ref=config.manual_reference;if(!ref)return null;
  const details=node('details','notice manual-reference'),summary=node('summary','',`편람 세부목표 · ${ref.page}쪽 · 지표 가중치 ${ref.weight}점`);details.append(summary);
  const list=node('ul');for(const part of ref.components)list.append(node('li','',`${part.name}: ${part.goal} (세부가중치 ${part.weight}점)`));details.append(list,node('p','small',ref.note));return details;
}
function openActual(item,adjust,scope=monthly.context){
  const context={...scope,department_id:item.department_id||scope.department_id,employee_id:item.employee_id||item.record?.employee_id||current.user.employee_id,indicator_id:item.indicator.id,revision:item.record?.revision||0};
  const config=item.record?JSON.parse(item.record.snapshot):item.settings;
  action(adjust?'실적 조정':'실적 입력',item.indicator.name+(adjust?' · 원래 입력값을 보존합니다. 빈 값은 조정 해제입니다.':''),adjust?'/api/monthly/adjust':'/api/monthly/actual',()=>{
    $('action-fields').append(node('p','readonly',config.entry_mode==='text'?`서술형 · 수치 목표 없음 · 가중치: ${config.weight ?? '미설정'}점`:`목표: ${config.target ?? '미설정'} ${config.unit||''} · 가중치: ${config.weight ?? '미설정'}점`));
    if(adjust)$('action-fields').append(node('p','readonly','원래 입력 실적: '+item.record.actual));
    const refView=manualReference(config);if(refView){refView.open=true;$('action-fields').append(refView);}
    let valueInput;
    if(config.entry_mode!=='text'){
      valueInput=field('actual-value',adjust?'조정 실적 (비우면 조정 해제)':`해당 월 기준 ${config.unit==='환산점'?'환산점수 합계':'실적'}${config.unit?' ('+config.unit+')':''}`,adjust?item.record?.adjusted:item.record?.actual,{numeric:true,maxLength:30});
      if(config.unit==='%')$('action-fields').append(node('p','hint','예: 실적 20%는 20으로 입력합니다. 목표 대비 달성률은 별도로 계산합니다.'));
      const preview=node('p','achievement-preview');preview.textContent=previewAchievement(config,valueInput.value);valueInput.oninput=()=>preview.textContent=previewAchievement(config,valueInput.value);$('action-fields').append(preview);
    }
    if(!adjust){
      const status=field('actual-status','진행상태',item.record?.status||'미착수',{options:['미착수','진행중','완료'].map(s=>[s,s])});
      const narrative=field('actual-narrative',config.entry_mode==='text'?'실적 내용':'추진 내용 · 실적 설명',item.record?.narrative,{multiline:true,maxLength:10000});
      field('actual-plan','향후계획',item.record?.future_plan,{multiline:true,maxLength:5000});
      function validateComplete(){if(valueInput)valueInput.required=status.value==='완료';narrative.required=config.entry_mode==='text'&&status.value==='완료';}
      status.onchange=validateComplete;validateComplete();
      $('action-fields').append(node('p','hint','저장 후 목록의 증빙 버튼에서 해당 지표의 자료를 업로드할 수 있습니다.'));
    }
  },()=>({...context,value:$('actual-value')?.value||'',...(!adjust?{status:$('actual-status').value,narrative:$('actual-narrative').value,future_plan:$('actual-plan').value}:{})}));
  if(scope._source==='review'&&!adjust){
    $('action-reason').hidden=false;$('action-reason').required=true;$('action-reason').value='';document.querySelector('label[for="action-reason"]').hidden=false;
    $('action-description').textContent=`${item.employee_name||item.employee_id} · ${item.indicator.name}`+(item.record?.adjusted!=null?' · 수정 저장 시 기존 조정값이 해제되고 새 실적이 반영됩니다.':'');
  }
}
function openSettings(item,scope=monthly.context){
  if(current.user.role!=='superadmin')return;
  const config=item.settings,context={...scope,department_id:item.department_id||scope.department_id,indicator_id:item.indicator.id,revision:config.revision};
  action('지표 목표·가중치 설정',`${departmentLabel(context.department_id)} · ${context.year}년 · ${item.indicator.name}. 같은 연도의 미마감 실적에 반영하며 마감월은 보존합니다.`,'/api/monthly/settings',()=>{
    const refView=manualReference(config);if(refView){refView.open=true;$('action-fields').append(refView);}
    field('setting-mode','실적 입력 방식',config.entry_mode,{options:[['numeric','수치 입력'],['text','서술형 입력']]});
    field('setting-target','목표치',config.target,{numeric:true,maxLength:30});field('setting-weight','가중치 (배점)',config.weight,{numeric:true,maxLength:30});
    field('setting-unit','실적 단위 (건, 명, %, 원 등)',config.unit,{maxLength:30});
    field('setting-direction','달성률 계산 방식',config.direction,{options:[['higher','상향형: 실적 ÷ 목표 × 100'],['lower','하향형: 목표 ÷ 실적 × 100']]});
    field('setting-curve','대시보드 진행계획',config.dashboard_curve||'linear',{options:[['linear','균등 진행 (1월~11월)'],['late','연말 집중']]});
    $('action-fields').append(node('p','hint','서술형은 달성률을 산정하지 않습니다. 가중치는 달성률에 곱하지 않습니다.'));
  },()=>({...context,entry_mode:$('setting-mode').value,target:$('setting-target').value,weight:$('setting-weight').value,unit:$('setting-unit').value,direction:$('setting-direction').value,dashboard_curve:$('setting-curve').value}));
}
function changePeriod(status){
  if(!monthly)return;
  const context={...monthly.context,revision:monthly.period.revision,status};
  action(status==='closed'?'월 마감':'마감 해제',`${departmentLabel(context.department_id)} · ${context.year}년 ${context.month}월. 마감 후 실적 입력·조정이 차단됩니다.`,'/api/monthly/period',()=>{},()=>context);
}
async function loadSuper(){
  const sessionCsrf=current?.csrf;$('super-error').textContent='';
  try{
    const master=await api('/api/admin/indicators');if(current?.csrf!==sessionCsrf||current?.user.role!=='superadmin')return;
    masterItems=master.indicators;
    for(const [id,key,label] of [['master-kind','kind','전체 기관'],['master-type','type','전체 유형']]){
      const previous=$(id).value;$(id).replaceChildren(new Option(label,''));
      for(const value of [...new Set(masterItems.map(k=>k[key]))])$(id).append(new Option(value,value));
      if([...$(id).options].some(o=>o.value===previous))$(id).value=previous;
    }
    if(!$('bulk-year').value)$('bulk-year').value=$('monthly-year').value;
    clearBulkPreview();renderMasterList();
  }catch(e){$('super-error').textContent=e.message;}
}
function renderMasterList(){
  const search=$('master-search').value.trim().toLowerCase(),kind=$('master-kind').value,type=$('master-type').value;
  const items=masterItems.filter(k=>(!kind||k.kind===kind)&&(!type||k.type===type)&&(!search||[k.id,k.name,k.applies_to,k.kind].join(' ').toLowerCase().includes(search)));
  $('master-count').textContent=`전체 ${masterItems.length}개 중 ${items.length}개 표시`;$('master-rows').replaceChildren();
  for(const k of items){
    const row=node('tr'),scope=node('td','',k.applies_to);scope.append(node('small','',k.kind));
    const note=node('td','master-note'),notePreview=node('span','master-note-preview',k.formula_note||'—');note.append(notePreview);note.title=k.formula_note||'';
    const actionCell=node('td'),button=node('button','primary','수정');button.type='button';button.onclick=()=>openMaster(k.id);actionCell.append(button);
    row.append(node('td','',k.id),scope,node('td','',k.name),node('td','',k.type),node('td','',k.target!=null?k.target+(k.unit?' '+k.unit:''):'미설정'),node('td','',k.points!=null?k.points+'점':'미설정'),note,actionCell);$('master-rows').append(row);
  }
  if(!items.length)emptyTable('master-rows',8,'검색 조건에 해당하는 지표가 없습니다.');
}
async function loadRoleControl(){
  const sessionCsrf=current?.csrf;$('roles-error').textContent='';
  try{
    const {users}=await api('/api/admin/users');if(current?.csrf!==sessionCsrf||current?.user.role!=='superadmin')return;
    $('user-rows').replaceChildren();
    for(const u of users){const row=node('tr'),button=node('button','secondary','권한 변경');button.type='button';button.onclick=()=>openRole(u);
      const cell=node('td');cell.append(button);row.append(node('td','',u.employee_id+(u.display_name?' · '+u.display_name:'')),node('td','',departmentLabel(u.department_id)),node('td','',roleLabels[u.role]),node('td','',departmentLabel(u.admin_department_id)),cell);$('user-rows').append(row);}
  }catch(e){$('roles-error').textContent=e.message;}
}
function openMaster(indicatorId){
  const k=masterItems.find(k=>k.id===indicatorId);if(!k)return;
  action('전체 지표 수정',`${k.applies_to} · ${k.id}. 기존 실적의 작성 당시 지표 정의는 보존됩니다.`,'/api/admin/indicator',()=>{
    field('master-name','지표명',k.name,{required:true});field('master-unit','단위',k.unit,{maxLength:30});
    field('master-target','목표값 (미확정이면 공란)',k.target,{numeric:true,maxLength:30});field('master-points','배점 (미확정이면 공란)',k.points,{numeric:true,maxLength:30});
    field('master-note','산식·평가기준 설명',k.formula_note,{multiline:true});
  },()=>({indicator_id:k.id,revision:k.revision,name:$('master-name').value,unit:$('master-unit').value,target:$('master-target').value,points:$('master-points').value,formula_note:$('master-note').value}));
}
function openRole(u){
  action('직원 권한 변경',`사번 ${u.employee_id}. 권한 변경 후 해당 직원은 다시 로그인해야 합니다.`,'/api/admin/role',()=>{
    field('role-value','권한',u.role,{options:Object.entries(roleLabels)});
    field('role-department','관리할 부서 (관리자 권한에만 적용)',u.admin_department_id||u.department_id,{options:evalDepartments().map(d=>[d.id,d.name])});
  },()=>({employee_id:u.employee_id,role:$('role-value').value,admin_department_id:$('role-department').value}));
}
async function loadAudit(){
  const sessionCsrf=current?.csrf;
  try{
    const {events}=await api('/api/admin/audit');if(current?.csrf!==sessionCsrf)return;$('audit-rows').replaceChildren();
    if(!events.length)$('audit-rows').append(node('p','empty','기록된 관리 변경이 없습니다.'));
    for(const e of events){
      const details=node('details','audit-entry');details.append(node('summary','',`${e.action} · 사번 ${e.actor} · ${new Date(e.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}`));
      details.append(node('p','',`대상: ${e.target} · 사유: ${e.reason}`));
      details.append(node('pre','audit-detail',JSON.stringify({before:e.before_json?JSON.parse(e.before_json):null,after:JSON.parse(e.after_json)},null,2)));$('audit-rows').append(details);
    }
  }catch(e){toast(e.message);}
}
$('load-monthly').onclick=loadMonthly;
for(const id of ['monthly-department','monthly-month','monthly-year'])$(id).onchange=loadMonthly;
$('close-month').onclick=()=>changePeriod('closed');$('reopen-month').onclick=()=>changePeriod('open');
$('load-super').onclick=loadSuper;$('load-roles').onclick=loadRoleControl;$('load-audit').onclick=loadAudit;
$('master-search').oninput=renderMasterList;for(const id of ['master-kind','master-type'])$(id).onchange=renderMasterList;
$('close-action').onclick=$('cancel-action').onclick=()=>$('action-dialog').close();
$('action-form').addEventListener('submit',async event=>{
  event.preventDefault();if(saving||!actionRequest)return;
  saving=true;$('action-save').disabled=true;$('action-error').textContent='';
  try{
    const payload=actionRequest.payload(),selfRoleChange=actionRequest.path==='/api/admin/role'&&payload.employee_id===current.user.employee_id;
    await api(actionRequest.path,{...payload,reason:$('action-reason').value,csrf:current.csrf});
    $('action-dialog').close();
    if(selfRoleChange){current=null;clearSessionViews();$('home').hidden=true;$('auth').hidden=false;setMode('login');toast('권한을 저장했습니다. 다시 로그인해 주세요.');return;}
    catalog=await api('/api/catalog');await refresh();toast('변경사항을 저장했습니다.');
  }catch(e){$('action-error').textContent=e.message;}
  finally{saving=false;$('action-save').disabled=false;}
});


let evidenceRecord=null,evidenceBusy=false;
function openEvidence(item,scope=monthly.context){
  evidenceRecord={id:item.record.id,name:item.indicator.name,closed:(item.period||monthly.period).status==='closed',attachments:item.record.attachments,source:scope._source||'monthly'};
  renderEvidence();$('evidence-dialog').showModal();
}
function renderEvidence(){
  if(!evidenceRecord){$('evidence-list').replaceChildren();return;}
  const r=evidenceRecord;$('evidence-title').textContent=r.name;$('evidence-error').textContent='';$('evidence-files').value='';
  $('evidence-form').hidden=r.closed;$('evidence-lock').hidden=!r.closed;$('evidence-list').replaceChildren();
  if(!r.attachments.length)$('evidence-list').append(node('p','empty','등록된 증빙자료가 없습니다.'));
  for(const file of r.attachments){
    const row=node('div','evidence-row'),link=node('a','',file.original_name);link.href='/api/evidence/'+file.id;link.download=file.original_name;
    row.append(link,node('span','muted small',(file.size/1024).toFixed(1)+' KB'));
    if(!r.closed&&(isManager()||file.uploaded_by===current.user.employee_id)){
      const remove=node('button','text-button','삭제');remove.type='button';remove.disabled=evidenceBusy;
      remove.onclick=async()=>{
        if(evidenceBusy||!confirm('목록에서 이 증빙을 삭제할까요?'))return;
        evidenceBusy=true;let deleteError='';
        try{await api('/api/evidence/delete',{csrf:current.csrf,file_id:file.id});await reloadEvidence();}
        catch(e){deleteError=e.message;}
        finally{evidenceBusy=false;renderEvidence();$('evidence-error').textContent=deleteError;}
      };row.append(remove);
    }
    $('evidence-list').append(row);
  }
}
async function reloadEvidence(){
  if(!evidenceRecord)throw new Error('다시 로그인해 주세요.');
  if(evidenceRecord.source==='review')await loadReview();else await loadMonthly();
  const source=evidenceRecord.source==='review'?reviewData:monthly;
  const item=source?.items.find(i=>i.record?.id===evidenceRecord.id);
  if(!item)throw new Error('실적을 다시 불러올 수 없습니다. 창을 닫고 조회해 주세요.');
  evidenceRecord.attachments=item.record.attachments;evidenceRecord.closed=(item.period||monthly.period).status==='closed';renderEvidence();
}
$('close-evidence').onclick=()=>{if(!evidenceBusy)$('evidence-dialog').close();};
$('evidence-dialog').addEventListener('cancel',event=>{if(evidenceBusy)event.preventDefault();});
$('evidence-form').addEventListener('submit',async event=>{
  event.preventDefault();if(evidenceBusy)return;
  const files=[...$('evidence-files').files];$('evidence-error').textContent='';
  if(!files.length){$('evidence-error').textContent='업로드할 파일을 선택해 주세요.';return;}
  if(files.length+evidenceRecord.attachments.length>10){$('evidence-error').textContent='월별 지표당 증빙은 최대 10개입니다.';return;}
  if(files.some(f=>f.size===0||f.size>10*1024*1024)){$('evidence-error').textContent='파일당 0바이트 초과, 10MB 이하로 선택해 주세요.';return;}
  evidenceBusy=true;$('upload-evidence').disabled=true;let uploaded=0,error='';
  try{
    for(const file of files){
      const response=await fetch('/api/evidence/upload?record_id='+evidenceRecord.id,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Requested-With':'PerformanceManager','X-CSRF-Token':current.csrf,'X-File-Name':encodeURIComponent(file.name)},body:file});
      const body=await response.json();if(!response.ok)throw new Error(body.error||'증빙 업로드에 실패했습니다.');uploaded++;
    }
    toast(uploaded+'개 증빙자료를 저장했습니다.');
  }catch(e){error=`${uploaded}개 저장 완료. ${e.message}`;}
  finally{
    try{await reloadEvidence();}catch(e){error=error||e.message;}
    evidenceBusy=false;renderEvidence();$('upload-evidence').disabled=false;$('evidence-error').textContent=error;
  }
});


let reviewData=null,reviewSequence=0;
function clearSessionViews(){
  if(typeof clearPerformance==='function')clearPerformance();
  clearDashboard();monthly=null;monthlySequence++;reviewData=null;reviewSequence++;masterItems=[];actionRequest=null;evidenceRecord=null;
  for(const id of ['review-place-rows','review-employee-rows','review-detail-rows','monthly-rows','user-rows','audit-rows','my-indicators','action-fields','evidence-list','master-rows','bulk-change-rows'])$(id).replaceChildren();
  $('review-nav').hidden=$('super-nav').hidden=$('roles-nav').hidden=true;clearBulkPreview();$('download-review').disabled=true;
  for(const id of ['review-place-count','review-entered-count','review-assigned-count','review-missing-count','review-draft-count'])$(id).textContent='0';
}

async function setupReview(){
  const u=current.user,select=$('review-department'),previous=select.value;
  select.replaceChildren();
  if(u.role==='superadmin')select.append(new Option('전체 근무지',''));
  const workplaces=u.role==='superadmin'?evalDepartments():evalDepartments().filter(d=>d.id===u.admin_department_id);
  for(const d of workplaces)select.append(new Option(d.name,d.id));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
  select.disabled=u.role!=='superadmin';
  $('review-scope').textContent=u.role==='superadmin'?'전체 근무지의 직원 입력 현황과 실적을 확인합니다.':`${departmentLabel(u.admin_department_id)} 직원들의 실적을 확인하고 취합·마감합니다.`;
  if(!$('review-month').options.length){
    for(let m=1;m<=12;m++)$('review-month').append(new Option(m+'월',m));
    $('review-month').value=$('monthly-month').value;$('review-year').value=$('monthly-year').value;
  }
  await loadReview();
}
function reviewContext(){return {department_id:$('review-department').value,year:Number($('review-year').value),month:Number($('review-month').value),_source:'review'};}
function emptyTable(id,columns,text){const row=node('tr'),cell=node('td','empty-row',text);cell.colSpan=columns;row.append(cell);$(id).append(row);}
async function loadReview(){
  if(!isManager())return;
  const sequence=++reviewSequence,context=reviewContext();reviewData=null;$('review-error').textContent='';$('download-review').disabled=true;
  for(const id of ['review-place-rows','review-employee-rows','review-detail-rows'])$(id).replaceChildren();
  try{
    const response=await api('/api/review?'+new URLSearchParams(context));if(sequence!==reviewSequence)return;
    reviewData={...response,context};
    $('review-place-count').textContent=response.workplaces.length;
    for(const [id,key] of [['review-entered-count','entered_count'],['review-assigned-count','assigned_count'],['review-missing-count','missing_count'],['review-draft-count','draft_count']])$(id).textContent=response.workplaces.reduce((n,p)=>n+p[key],0);
    for(const place of response.workplaces){
      const row=node('tr'),actions=node('td'),closed=place.period.status==='closed';
      row.append(node('td','',place.department_name),node('td','',place.employee_count),node('td','',place.entered_count+' / '+place.assigned_count),node('td','',place.missing_count+' / '+place.draft_count),node('td','',place.average_achievement!=null?place.average_achievement+'%':'—'),node('td','',closed?'마감':'입력 가능'));
      const detail=node('button','secondary','상세');detail.type='button';detail.onclick=()=>{$('review-department').value=place.department_id;loadReview();};actions.append(detail);
      const close=node('button',closed?'secondary':'primary',closed?'마감 해제':'월 마감');close.type='button';close.onclick=()=>changeReviewPeriod(place,context);actions.append(close);row.append(actions);$('review-place-rows').append(row);
    }
    for(const e of response.employees){
      const row=node('tr'),employee=node('td','',e.employee_id+(e.display_name?' · '+e.display_name:''));if(!e.is_current_member)employee.append(node('small','','이전 소속'));
      row.append(node('td','',e.department_name),employee,node('td','',e.entry_state),node('td','',e.entered_count+' / '+e.assigned_count),node('td','',e.missing_count+' / '+e.draft_count),node('td','',e.completion_percent!=null?e.completion_percent+'%':'—'));$('review-employee-rows').append(row);
    }
    if(!response.employees.length)emptyTable('review-employee-rows',6,'해당 근무지에 등록된 직원이 없습니다.');
    for(const item of response.items){
      const r=item.record,config=r?JSON.parse(r.snapshot):item.settings,closed=item.period.status==='closed',scope={...context,department_id:item.department_id};
      const row=node('tr'),person=node('td','',item.employee_name||item.employee_id);person.append(node('small','',item.employee_id));
      if(r?.owner_inferred)person.append(node('small','','기존 담당자 추정'));
      const metric=node('td','',item.indicator.name);metric.append(node('small','',`목표 ${config.target ?? '미설정'} ${config.unit||''} · 가중치 ${config.weight ?? '미설정'}점`));
      const value=r?(config.entry_mode==='text'?r.narrative||'내용 미입력':r.actual!==''?r.actual:'수치 미입력'):'—',actual=node('td','narrative-cell',value);
      if(r?.adjusted!=null)actual.append(node('small','',`조정값: ${r.adjusted}`));
      const actions=node('td');
      const edit=node('button','secondary',r?'수정':'대리 입력');edit.type='button';edit.disabled=closed;edit.onclick=()=>openActual(item,false,scope);actions.append(edit);
      if(r){const files=node('button','secondary',`증빙 ${r.attachments.length}개`);files.type='button';files.onclick=()=>openEvidence(item,scope);actions.append(files);}
      if(current.user.role==='superadmin'){const goal=node('button','secondary','목표·가중치');goal.type='button';goal.disabled=closed;goal.onclick=()=>openSettings(item,scope);actions.append(goal);}
      if(r&&config.entry_mode!=='text'){const adjust=node('button','secondary','수치 조정');adjust.type='button';adjust.disabled=closed;adjust.onclick=()=>openActual(item,true,scope);actions.append(adjust);}
      row.append(node('td','',item.department_name),person,metric,node('td','',item.entry_state),actual,node('td','',r?r.achievement.label:'—'),node('td','',r?.status||'미착수'),node('td','plan-cell',r?.future_plan||'—'),actions);$('review-detail-rows').append(row);
    }
    if(!response.items.length)emptyTable('review-detail-rows',9,'등록된 담당지표가 없습니다.');
    $('download-review').disabled=false;
  }catch(e){if(sequence===reviewSequence)$('review-error').textContent=e.message;}
}
function changeReviewPeriod(place,context){
  const closed=place.period.status==='closed',payload={department_id:place.department_id,year:context.year,month:context.month,revision:place.period.revision,status:closed?'open':'closed'};
  action(closed?'월 마감 해제':'월별 취합 마감',`${place.department_name} · ${context.year}년 ${context.month}월. ${closed?'직원들의 수정·증빙 등록을 다시 허용합니다.':'소속 직원들의 실적 수정·증빙 등록을 잠급니다.'}`,'/api/monthly/period',()=>{},()=>payload);
}
$('load-review').onclick=loadReview;
for(const id of ['review-department','review-year','review-month'])$(id).onchange=loadReview;
$('download-review').onclick=async()=>{
  if(!reviewData)return;
  const context={...reviewData.context};$('download-review').disabled=true;
  try{
    const response=await fetch('/api/review.xlsx?'+new URLSearchParams(context));
    if(!response.ok){const body=await response.json();throw new Error(body.error||'다운로드에 실패했습니다.');}
    const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download=`월별실적_${context.year}_${String(context.month).padStart(2,'0')}.xlsx`;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){$('review-error').textContent=e.message;}
  finally{$('download-review').disabled=!reviewData;}
};


let bulkState=null,bulkBusy=false;
function clearBulkPreview(){
  bulkState=null;$('bulk-preview').hidden=true;$('bulk-change-rows').replaceChildren();$('bulk-reason').value='';$('bulk-file').value='';$('bulk-error').textContent='';$('apply-indicator-import').disabled=true;
}
function bulkButtons(busy){
  bulkBusy=busy;
  for(const id of ['download-indicator-template','preview-indicator-import','bulk-year','bulk-file'])$(id).disabled=busy;
  $('apply-indicator-import').disabled=busy||!bulkState||!(bulkState.master_count+bulkState.target_count);
}
function bulkValue(value){
  if(value==null||value==='')return '미설정';
  return {numeric:'수치형',text:'서술형',higher:'상향형',lower:'하향형'}[value]||String(value);
}
function renderBulkPreview(){
  $('bulk-preview').hidden=false;$('bulk-change-rows').replaceChildren();
  $('bulk-summary').textContent=`공통 지표 ${bulkState.master_count}건 · 부서별 목표 ${bulkState.target_count}건. 아직 반영되지 않았습니다.`;
  const labels={name:'지표명',unit:'단위',target:'목표치',points:'가중치(배점)',formula_note:'산식·평가기준',weight:'가중치(배점)',entry_mode:'입력방식',direction:'달성률방식'};
  for(const change of bulkState.changes){
    for(const key of Object.keys(change.after)){
      const before=change.before[key],after=change.after[key];
      if((before??'')===(after??'')||(['target','points','weight'].includes(key)&&before!=null&&after!=null&&Number(before)===Number(after)))continue;
      const row=node('tr'),label=node('td','',change.label);label.append(node('small','',change.key));
      row.append(node('td','',change.sheet+' · '+change.row+'행'),label,node('td','',labels[key]||key),node('td','bulk-diff-text',bulkValue(before)),node('td','bulk-diff-text',bulkValue(after)));$('bulk-change-rows').append(row);
    }
  }
  if(!bulkState.changes.length)emptyTable('bulk-change-rows',5,'수정된 값이 없습니다.');
}
$('bulk-year').onchange=()=>clearBulkPreview();
$('bulk-file').onchange=()=>{bulkState=null;$('bulk-preview').hidden=true;$('bulk-change-rows').replaceChildren();$('bulk-reason').value='';$('bulk-error').textContent='';$('apply-indicator-import').disabled=true;};
$('download-indicator-template').onclick=async()=>{
  if(bulkBusy)return;clearBulkPreview();bulkButtons(true);
  try{
    const year=$('bulk-year').value,response=await fetch('/api/admin/indicators/template.xlsx?'+new URLSearchParams({year}));
    if(!response.ok){const body=await response.json();throw new Error(body.error||'양식을 내려받을 수 없습니다.');}
    const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download=`전체지표_수정양식_${year}.xlsx`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(e){$('bulk-error').textContent=e.message;}
  finally{bulkButtons(false);}
};
$('bulk-upload-form').addEventListener('submit',async event=>{
  event.preventDefault();if(bulkBusy)return;
  const file=$('bulk-file').files[0];$('bulk-error').textContent='';
  if(!file||!file.name.toLowerCase().endsWith('.xlsx')||file.size===0||file.size>5*1024*1024){$('bulk-error').textContent='5MB 이하의 XLSX 수정 양식을 선택해 주세요.';return;}
  const csrf=current.csrf;bulkState=null;$('bulk-preview').hidden=true;bulkButtons(true);
  try{
    const response=await fetch('/api/admin/indicators/import-preview?'+new URLSearchParams({year:$('bulk-year').value}),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Requested-With':'PerformanceManager','X-CSRF-Token':csrf},body:file});
    const body=await response.json();if(!response.ok)throw new Error(body.error||'양식 검증에 실패했습니다.');
    if(current?.csrf!==csrf)return;bulkState=body;renderBulkPreview();
  }catch(e){if(current?.csrf===csrf)$('bulk-error').textContent=e.message;}
  finally{bulkButtons(false);}
});
$('bulk-apply-form').addEventListener('submit',async event=>{
  event.preventDefault();if(bulkBusy||!bulkState||saving)return;bulkButtons(true);$('bulk-error').textContent='';
  try{
    const result=await api('/api/admin/indicators/import-apply',{token:bulkState.token,csrf:current.csrf,reason:$('bulk-reason').value});
    clearBulkPreview();catalog=await api('/api/catalog');await refresh();toast(result.change_count+'건의 지표 변경을 반영했습니다.');
  }catch(e){$('bulk-error').textContent=e.message;}
  finally{bulkButtons(false);}
});

