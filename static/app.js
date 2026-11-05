'use strict';
const $ = id => document.getElementById(id);
let catalog, current, mode = 'login', saving = false;
async function api(path, data) {
  const options = data ? {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'PerformanceManager'},body:JSON.stringify(data)} : {};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') {
      current = null; $('profile-dialog').close(); $('action-dialog').close(); $('home').hidden = true; $('auth').hidden = false;
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
  $('register-fields').hidden = !signup;
  $('auth-title').textContent = signup ? '성과관리, 함께 시작해요' : '다시 만나 반갑습니다';
  $('auth-description').textContent = signup ? '근무지와 담당지표를 등록하면 준비가 끝납니다.' : '사번으로 로그인하고 나의 업무를 확인하세요.';
  $('auth-submit').textContent = signup ? '회원가입 후 시작하기 →' : '로그인 →';
  $('password').value = ''; $('password-confirm').value = '';
  $('password').minLength = signup ? 10 : 1; $('password').autocomplete = signup ? 'new-password' : 'current-password';
  $('password-confirm').required = signup; $('register-department').required = signup; $('auth-error').textContent = '';
}
async function refresh() {
  current = await api('/api/me'); render(); await setupManagement();
}
function render() {
  $('auth').hidden = true; $('home').hidden = false;
  const u = current.user, d = dept(u.department_id), items = indicators(u.department_id).filter(k => u.indicators.includes(k.id));
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
  const history = $('history'); history.replaceChildren();
  for (const h of current.history) {
    const before = h.before_json ? JSON.parse(h.before_json) : null, after = JSON.parse(h.after_json);
    const row = node('div','history-row');
    const label = before ? (before.department_id !== after.department_id ? `${dept(before.department_id).name} → ${dept(after.department_id).name}` : `${dept(after.department_id).name} · 담당지표 변경`) : `${dept(after.department_id).name} · 최초 등록`;
    row.append(node('span','',label),node('span','muted small',`담당지표 ${after.indicators.length}개 · ${new Date(h.changed_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})}`)); history.append(row);
  }
}
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
  if (mode === 'register' && $('password').value !== $('password-confirm').value) { $('auth-error').textContent='비밀번호 확인이 일치하지 않습니다.'; return; }
  if (mode === 'register' && $('register-department').value !== 'SYS-PERFORMANCE' && !selected('register').length) { $('auth-error').textContent='담당지표를 하나 이상 선택해 주세요.'; return; }
  saving=true; $('auth-submit').disabled=true;
  try {
    await api('/api/'+mode,{employee_id:$('employee').value.trim(),password:$('password').value,department_id:$('register-department').value,indicators:selected('register')});
    $('password').value=''; $('password-confirm').value=''; await refresh();
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
$('logout').onclick=async()=>{ try { await api('/api/logout',{csrf:current.csrf}); current=null; $('home').hidden=true; $('auth').hidden=false; setMode('login'); } catch(e) { toast(e.message); } };
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
  $('super-panel').hidden=$('super-nav').hidden=u.role!=='superadmin';
  $('audit-panel').hidden=!isManager();
  $('scope-description').textContent=u.role==='superadmin'?'전체 부서 조회·실적 조정·월 마감 및 해제':u.role==='admin'?`관리 범위: ${departmentLabel(u.admin_department_id)} · 실적 조정과 월 마감`:'본인 근무지의 담당지표 실적을 입력합니다.';
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
  await loadMonthly();
  if(u.role==='superadmin')await loadSuper();
  if(isManager())await loadAudit();
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
    $('reopen-month').hidden=current.user.role!=='superadmin'||!closed;
    for(const item of monthly.items){
      const k=item.indicator,r=item.record,row=node('tr'),name=node('td','',k.name);
      name.append(node('small','',k.type+(k.unit?' · '+k.unit:'')+(r?' · 작성 당시: '+JSON.parse(r.snapshot).name:'')));
      const actual=r?.actual ?? '미입력',adjusted=r?.adjusted ?? '—',finalValue=r?(r.adjusted ?? r.actual):'—';
      row.append(name,node('td','',actual),node('td','',adjusted),node('td','',finalValue));
      const actions=node('td');
      const save=node('button','secondary','실적 입력');save.type='button';save.disabled=closed || r?.adjusted!=null;
      save.onclick=()=>openActual(item,false);actions.append(save);
      if(isManager()){
        const adjust=node('button','secondary','조정');adjust.type='button';adjust.disabled=closed||!r;adjust.onclick=()=>openActual(item,true);actions.append(adjust);
      }
      row.append(actions);$('monthly-rows').append(row);
    }
    if(!monthly.items.length){const row=node('tr'),cell=node('td','empty-row','선택된 담당지표가 없습니다.');cell.colSpan=5;row.append(cell);$('monthly-rows').append(row);}
  }catch(e){if(sequence===monthlySequence)$('monthly-error').textContent=e.message;}
}
function field(id,label,value='',options={}){
  const wrapper=node('div'),caption=node('label','',label);caption.htmlFor=id;
  let input;
  if(options.options){input=node('select');for(const [v,text] of options.options)input.append(new Option(text,v));}
  else if(options.multiline){input=node('textarea');input.rows=3;input.maxLength=options.maxLength||3000;}
  else{input=node('input');input.type='text';input.maxLength=options.maxLength||200;if(options.numeric)input.inputMode='decimal';}
  input.id=id;input.value=value??'';if(options.required)input.required=true;
  wrapper.append(caption,input);$('action-fields').append(wrapper);return input;
}
function action(title,description,path,build,payload){
  $('action-title').textContent=title;$('action-description').textContent=description;$('action-error').textContent='';$('action-reason').value='';
  $('action-fields').replaceChildren();build();actionRequest={path,payload};$('action-dialog').showModal();
}
function openActual(item,adjust){
  const context={...monthly.context,indicator_id:item.indicator.id,revision:item.record?.revision||0};
  action(adjust?'실적 조정':'월별 실적 입력',item.indicator.name+(adjust?' · 원래 입력값을 보존합니다. 빈 값은 조정 해제입니다.':''),adjust?'/api/monthly/adjust':'/api/monthly/actual',()=>{
    if(adjust)$('action-fields').append(node('p','readonly','원래 실적: '+item.record.actual));
    field('actual-value',adjust?'조정 실적 (비우면 조정 해제)':'실적',adjust?item.record?.adjusted:item.record?.actual,{numeric:true,required:!adjust,maxLength:30});
  },()=>({...context,value:$('actual-value').value}));
}
function changePeriod(status){
  if(!monthly)return;
  const context={...monthly.context,revision:monthly.period.revision,status};
  action(status==='closed'?'월 마감':'마감 해제',`${departmentLabel(context.department_id)} · ${context.year}년 ${context.month}월. 마감 후 실적 입력·조정이 차단됩니다.`,'/api/monthly/period',()=>{},()=>context);
}
async function loadSuper(){
  $('super-error').textContent='';
  try{
    const [master,users]=await Promise.all([api('/api/admin/indicators'),api('/api/admin/users')]);masterItems=master.indicators;
    const select=$('master-indicator'),old=select.value;select.replaceChildren();
    for(const k of masterItems)select.append(new Option(`${k.applies_to} · ${k.name} (${k.id})`,k.id));
    if(masterItems.some(k=>k.id===old))select.value=old;
    $('user-rows').replaceChildren();
    for(const u of users.users){const row=node('tr'),button=node('button','secondary','권한 변경');button.type='button';button.onclick=()=>openRole(u);
      const cell=node('td');cell.append(button);row.append(node('td','',u.employee_id+(u.display_name?' · '+u.display_name:'')),node('td','',departmentLabel(u.department_id)),node('td','',roleLabels[u.role]),node('td','',departmentLabel(u.admin_department_id)),cell);$('user-rows').append(row);}
  }catch(e){$('super-error').textContent=e.message;}
}
function openMaster(){
  const k=masterItems.find(k=>k.id===$('master-indicator').value);if(!k)return;
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
  try{
    const {events}=await api('/api/admin/audit');$('audit-rows').replaceChildren();
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
$('load-super').onclick=loadSuper;$('edit-master').onclick=openMaster;$('load-audit').onclick=loadAudit;
$('close-action').onclick=$('cancel-action').onclick=()=>$('action-dialog').close();
$('action-form').addEventListener('submit',async event=>{
  event.preventDefault();if(saving||!actionRequest)return;
  saving=true;$('action-save').disabled=true;$('action-error').textContent='';
  try{
    const payload=actionRequest.payload(),selfRoleChange=actionRequest.path==='/api/admin/role'&&payload.employee_id===current.user.employee_id;
    await api(actionRequest.path,{...payload,reason:$('action-reason').value,csrf:current.csrf});
    $('action-dialog').close();
    if(selfRoleChange){current=null;$('home').hidden=true;$('auth').hidden=false;setMode('login');toast('권한을 저장했습니다. 다시 로그인해 주세요.');return;}
    catalog=await api('/api/catalog');await refresh();toast('변경사항을 저장했습니다.');
  }catch(e){$('action-error').textContent=e.message;}
  finally{saving=false;$('action-save').disabled=false;}
});
start();
