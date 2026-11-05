'use strict';
const $ = id => document.getElementById(id);
let catalog, current, mode = 'login', saving = false;
async function api(path, data) {
  const options = data ? {method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'PerformanceManager'},body:JSON.stringify(data)} : {};
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') {
      current = null; $('profile-dialog').close(); $('home').hidden = true; $('auth').hidden = false;
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
  if (!items.length) list.append(node('p','empty','근무지를 먼저 선택해 주세요.'));
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
  current = await api('/api/me'); render();
}
function render() {
  $('auth').hidden = true; $('home').hidden = false;
  const u = current.user, d = dept(u.department_id), items = indicators(u.department_id).filter(k => u.indicators.includes(k.id));
  $('header-employee').textContent = '사번 '+u.employee_id;
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
  $('change-department').value = current.user.department_id; choices('change',current.user.indicators); $('profile-dialog').showModal();
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; setTimeout(() => $('toast').hidden=true, 4000); }
$('login-tab').onclick = () => setMode('login'); $('register-tab').onclick = () => setMode('register');
for (const prefix of ['register','change']) $(prefix+'-department').addEventListener('change', () => choices(prefix));
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault(); if (saving) return;
  $('auth-error').textContent='';
  if (mode === 'register' && $('password').value !== $('password-confirm').value) { $('auth-error').textContent='비밀번호 확인이 일치하지 않습니다.'; return; }
  if (mode === 'register' && !selected('register').length) { $('auth-error').textContent='담당지표를 하나 이상 선택해 주세요.'; return; }
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
  if(!selected('change').length) { $('profile-error').textContent='담당지표를 하나 이상 선택해 주세요.'; return; }
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
start();
