import {viewportReport} from './viewport.js?v=0.5.0';
import {chatScreen, loadThumbnail} from './chat.js?v=0.5.0';
import {providerScreen} from './provider-ui.js?v=0.5.0';
import {createPreview} from './preview.js?v=0.5.0';
import {connectionCard, taskRecord} from './chat-ui.js?v=0.5.0';
const $=s=>document.querySelector(s), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={jobs:[],providers:[],poll:null,search:'',filter:'all'};
const labels={pending:'等待处理',working:'进行中',cancelling:'正在停止',review:'待确认',confirmed:'已完成',needs_confirmation:'待确认尺寸',needs_input:'待补充',failed:'未完成',cancelled:'已取消',interrupted:'已中断'};
const stages={dimensions_review:'等待尺寸确认',dimensions_confirmed:'尺寸已确认，准备建模',pending:'等待分析',demo:'固定零件测试',generating:'分析需求并生成脚本',repairing:'正在修复脚本（第 1 次）',submitting:'提交到 FreeCAD',cad_queued:'等待 FreeCAD 建模',cad_unavailable:'FreeCAD 执行端暂不可用',building:'FreeCAD 正在建模',archiving:'保存模型并准备预览',review:'草稿已就绪',confirmed:'这一版已确认',needs_input:'需要你补充信息',failed:'本次生成未完成',cancelled:'任务已取消'};
const active=j=>['pending','working','cancelling'].includes(j.status);
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(state.toast);state.toast=setTimeout(()=>$('#toast').hidden=true,6000)}
async function api(path,options={}){
  const signal=options.signal||AbortSignal.timeout(path.endsWith('/test')?120000:20000);
  let response, data;
  try{
    response=await fetch('/api/'+path,{...options,signal,headers:{...(options.body&&!options.raw?{'Content-Type':'application/json'}:{}),...options.headers}});
    data=await response.json();
  }catch(error){
    if(error.name==='AbortError')throw error;
    if(response?.status===401)showLogin();
    const failure=Error('无法连接服务端');
    failure.status=response?.status;
    failure.connection=!(response?.status===401 || response?.status===403 || response?.status===413);
    if(!failure.connection)failure.message='请求未被接受，请检查登录状态和部署配置。';
    throw failure;
  }
  if(!response.ok){
    if(response.status===401)showLogin();
    const error=Error(data.error||'操作未完成');
    error.status=response.status;
    error.connection=response.status>=500;
    throw error;
  }
  return data;
}
const post=(path,body)=>api(path,{method:'POST',body:JSON.stringify(body)});
function showLogin(){
  ++routeEpoch;
  state.chatCleanup?.();state.chatCleanup=null;
  state.detailCleanup?.();state.detailCleanup=null;stopLibraryThumbnails();
  $('#login').hidden=false;$('#shell').hidden=true;$('#main').replaceChildren();
  clearTimeout(state.poll);clearInterval(state.healthTimer);
  if($('#preview-dialog').open)$('#preview-dialog').close();
}
function pill(job){return `<span class="pill ${job.status==='failed'?'failed':['needs_input','review'].includes(job.status)?'warn':''}">${esc(labels[job.status]||job.status)}</span>`}
function showDialog(html){$('#dialog-content').innerHTML=html;$('#dialog').showModal()}
$('#dialog-close').onclick=()=>$('#dialog').close();
$('#login-form').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await post('login',Object.fromEntries(new FormData(e.target)));await boot()}catch(error){toast(error.message)}finally{b.disabled=false}};
function backendStatus(session){const badge=$('#connection');badge.textContent=session.worker_ready?'任务后台在线':'任务后台未就绪';badge.classList.toggle('warn',!session.worker_ready);badge.title='此状态只表示移动端任务后台的心跳；FreeCAD 状态请到设置中检查。'}
async function refreshBackendStatus(){if(document.hidden||$('#shell').hidden)return;try{backendStatus(await api('session',{signal:AbortSignal.timeout(8000)}))}catch(error){$('#connection').textContent='后台连接中断';$('#connection').classList.add('warn')}}
async function boot(){
  try{
    const session=await api('session');
    $('#login').hidden=true;$('#shell').hidden=false;state.serverVersion=session.version;
    backendStatus(session);
    clearInterval(state.healthTimer);state.healthTimer=setInterval(refreshBackendStatus,20000);
    await route();
  }catch(error){
    if(error.connection){
      $('#login').hidden=true;$('#shell').hidden=false;
      renderUnavailable(boot);
    }else if(error.status!==401){showLogin();toast(error.message)}
  }
}
function options(selected){return `<option value="">请选择模型配置</option>`+state.providers.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)} · ${esc(p.model)}</option>`).join('')}
const recentKey='zaowu-recent-job';
let routeEpoch=0;
function recentJobId(){try{return localStorage.getItem(recentKey)}catch{return null}}
function hasPendingNew(){
  try{return !!JSON.parse(localStorage.getItem('zaowu-chat-draft-new')||'{}').pending}catch{return false}
}
function remember(job){
  state.explicitNew=false;
  try{localStorage.setItem(recentKey,job.id)}catch{}
}
async function findConversation(id){
  if(id){
    try{return await api('jobs/'+encodeURIComponent(id))}
    catch(error){if(error.status!==404)throw error}
  }
  const jobs=await api('jobs');
  return jobs[0]||null;
}
function renderUnavailable(retry,isChat=true){
  const host=$('#main');
  $('#connection').textContent='后台连接中断';
  $('#connection').classList.add('warn');
  host.classList.toggle('chat-page',isChat);
  host.classList.toggle('chat-home',isChat);
  host.innerHTML=isChat?'<section class="chat-shell"><div class="chat-heading"><h1>会话</h1></div><div class="messages"><div id="route-feedback"></div></div></section><aside class="chat-sidebar" hidden></aside>':'<div id="route-feedback"></div>';
  connectionCard($('#route-feedback'),'暂时无法载入页面。已有会话和任务已保留。',retry);
}
async function route(){
  if($('#shell').hidden)return;
  const epoch=++routeEpoch, originalHash=location.hash;
  const isCurrent=()=>epoch===routeEpoch&&location.hash===originalHash&&!$('#shell').hidden;
  clearTimeout(state.poll);
  state.chatCleanup?.();state.chatCleanup=null;
  stopLibraryThumbnails();
  state.detailCleanup?.();state.detailCleanup=null;
  $('#main').classList.remove('chat-page','chat-home');
  $('#main').innerHTML='<p class="muted" role="status">正在加载…</p>';
  const [page,id]=(originalHash.slice(1)||'conversation').split('/');
  const isChat=['conversation','recent','new','job','create'].includes(page);
  const nav=isChat?'conversation':page==='detail'?'library':page==='model'?'settings':page;
  for(const link of document.querySelectorAll('[data-nav]')){
    const selected=link.dataset.nav===nav;
    link.classList.toggle('active',selected);
    if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  }
  if(page==='new')state.explicitNew=true;
  if(page==='recent'||id&&isChat)state.explicitNew=false;
  try{
    if(isChat){
      const newChat=page==='new'||(!id&&page!=='recent'&&(!!state.explicitNew||hasPendingNew()));
      const [latest,providers]=await Promise.all([newChat?Promise.resolve(null):findConversation(id||recentJobId()),api('providers')]);
      if(!isCurrent())return;
      state.providers=providers;
      state.explicitNew=newChat;
      if(latest)remember(latest);
      state.chatCleanup=chatScreen({
        latest,recent:!!recentJobId(),providers,isCurrent,remember,api,post,toast,esc,options,stages,active,download,
        onSubmitted(job,visible){
          if(visible){remember(job);location.hash='job/'+job.id;return}
          if((latest&&recentJobId()===latest.id)||(!latest&&state.explicitNew)||!recentJobId())remember(job);
        }
      });
    }else if(page==='detail')await detail(id,isCurrent);
    else if(page==='model')await providerScreen({id,api,toast});
    else if(page==='settings')await settings(isCurrent);
    else await library(isCurrent);
  }catch(error){
    if(!isCurrent())return;
    if(error.connection)renderUnavailable(async()=>{await route();await refreshBackendStatus()},isChat);
    else if(error.status!==401){$('#main').replaceChildren();toast(error.message)}
  }
}
window.addEventListener('hashchange',route);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshBackendStatus()});
const libraryFilters = {all:'全部',active:'进行中',review:'待确认',confirmed:'已完成'};
function libraryMatch(job){
  const query=state.search.trim().toLowerCase();
  if(query&&!((job.name+' '+job.prompt).toLowerCase().includes(query)))return false;
  if(state.filter==='all')return true;
  if(state.filter==='active')return active(job);
  if(state.filter==='review')return ['needs_input','needs_confirmation','review'].includes(job.status);
  return job.status===state.filter;
}
async function library(isCurrent){
  const jobs=await api('jobs?view=projects');
  if(!isCurrent())return;
  state.jobs=jobs;
  $('#main').innerHTML=`<div class="page-head"><h1>模型库</h1></div>
    <div class="library-controls"><label for="search">搜索</label>
      <input id="search" type="search" placeholder="搜索模型名称或需求" value="${esc(state.search)}">
      <div class="library-filters" aria-label="筛选状态">${Object.entries(libraryFilters).map(([key,label])=>`<button type="button" data-filter="${key}" aria-pressed="${state.filter===key}">${label}</button>`).join('')}</div>
    </div><div id="model-list"></div>`;
  $('#search').oninput=e=>{state.search=e.target.value;cards()};
  for(const button of $('#main').querySelectorAll('[data-filter]')){
    button.onclick=()=>{
      state.filter=button.dataset.filter;
      for(const filter of $('#main').querySelectorAll('[data-filter]'))filter.setAttribute('aria-pressed',String(filter===button));
      cards();
    };
  }
  cards();
  async function poll(){
    try{
      const fresh=await api('jobs?view=projects');
      if(!isCurrent())return;
      state.jobs=fresh;cards();
      if(fresh.some(active))state.poll=setTimeout(poll,4000);
    }catch(error){
      if(isCurrent()&&error.connection)state.poll=setTimeout(poll,4000);
    }
  }
  if(jobs.some(active))state.poll=setTimeout(poll,4000);
}
function cards(){
  const filtered=state.jobs.filter(libraryMatch);
  $('#model-list').innerHTML=filtered.length?`<div class="cards library-cards">${filtered.map(j=>`<a class="card" href="#detail/${j.id}">
    <div class="card-preview" ${j.archived?`data-library-thumb="${j.id}"`:''}>${j.images.length?`<img src="/api/uploads/${j.images[0]}" alt="">`:'<div class="block" aria-hidden="true"></div>'}</div>
    <div class="card-body"><h3>${esc(j.name)}</h3><p class="muted">第 ${j.version} 版${j.report?.dimensions?' · '+esc(j.report.dimensions.map(v=>Number(v.toFixed(2))).join(' × '))+' mm':''}</p>${pill(j)}</div>
    </a>`).join('')}</div><p class="field-note library-note">同一项目的多个版本集中在项目内。</p>`
    :`<div class="empty"><h2>${state.jobs.length?'没有符合条件的模型':'模型库为空'}</h2>${state.jobs.length?'':'<a class="text-link" href="#new">新建对话</a>'}</div>`;
  watchLibraryThumbnails();
}
let libraryObserver;
function stopLibraryThumbnails(){libraryObserver?.disconnect();libraryObserver=null}
function watchLibraryThumbnails(){
  stopLibraryThumbnails();
  const targets=$('#model-list').querySelectorAll('[data-library-thumb]');
  if(!targets.length)return;
  libraryObserver=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(!entry.isIntersecting)continue;
      const target=entry.target;
      libraryObserver?.unobserve(target);
      const job=state.jobs.find(row=>row.id===target.dataset.libraryThumb);
      if(job)loadThumbnail(job,target,()=>target.isConnected).catch(()=>{});
    }
  },{rootMargin:'100px'});
  for(const target of targets)libraryObserver.observe(target);
}
async function detail(id,isCurrent){
  let job=await api('jobs/'+encodeURIComponent(id));
  const versions=(await api('jobs?project='+encodeURIComponent(job.project))).reverse();
  if(!isCurrent())return;
  $('#main').innerHTML=`<div class="page-head detail-heading"><h1>任务详情</h1><a class="text-link" href="#library">返回模型库</a></div>
    <div id="detail-state" class="detail-state"></div>
    <div class="page-head model-heading"><h2>${esc(job.name)}</h2><select id="versions" class="version-select" aria-label="选择模型版本">${versions.map(version=>
      `<option value="${esc(version.id)}" ${version.id===id?'selected':''}>第 ${version.version} 版 · ${esc(labels[version.status])}</option>`).join('')}</select></div>
    <div class="detail-layout detail-project">
      <section><div id="facts"></div><div id="project-preview"></div><div class="panel"><h3>需求与参考</h3><p>${esc(job.prompt).replace(/\n/g,'<br>')}</p>
        <div class="images">${job.images.map((image,i)=>`<a href="/api/uploads/${image}" target="_blank" rel="noopener"><img src="/api/uploads/${image}" width="80" height="70" style="object-fit:contain" alt="参考图 ${i+1}"></a>`).join('')}</div></div></section>
      <aside><div id="detail-feedback" hidden></div><div id="task-panel"></div></aside>
    </div>`;
  $('#versions').onchange=e=>location.hash='detail/'+e.target.value;
  const modify=()=>location.hash='job/'+job.id;
  const preview=createPreview({
    isCurrent,post,toast,esc,download,onContinue:modify,onConfirmed:update,
    onConnectionError:showFailure,
  });
  state.detailCleanup=preview.close;

  function update(next){
    if(!isCurrent())return;
    const open=[...$('#task-panel').querySelectorAll('details')].map(node=>node.open);
    job=next;
    const status=`${labels[job.status]} · 第 ${job.version} 版`;
    const selected=$('#versions').selectedOptions[0];
    if(selected)selected.textContent=`第 ${job.version} 版 · ${labels[job.status]}`;
    $('#detail-state').textContent=status;
    renderFacts(job);
    renderTask(job);
    $('#task-panel').querySelectorAll('details').forEach((node,i)=>{node.open=!!open[i]});
    $('#project-preview').innerHTML=job.archived?'<button class="primary" id="detail-preview">打开模型预览</button>':'';
    if(job.archived){
      $('#detail-preview').onclick=()=>preview.open(job);
      $('#detail-confirm')?.addEventListener('click',()=>preview.open(job));
    }
    $('#modify')?.addEventListener('click',modify);
    $('#cancel')?.addEventListener('click',async e=>{
      e.target.disabled=true;
      try{update(await post('jobs/'+job.id+'/cancel',{}))}
      catch(error){if(error.connection)showFailure('暂时无法确认取消请求是否送达。');else toast(error.message)}
      finally{e.target.disabled=false}
    });
  }
  function showFailure(message){
    if(!isCurrent())return;
    const target=$('#detail-feedback');
    if(target.hidden){
      target.hidden=false;
      connectionCard(target,message,poll);
    }
  }
  async function poll(){
    if(!isCurrent())return;
    try{
      const next=await api('jobs/'+encodeURIComponent(id));
      if(!isCurrent())return;
      $('#detail-feedback').hidden=true;
      update(next);
      if(active(next))state.poll=setTimeout(poll,2500);
    }catch(error){
      if(isCurrent()&&error.connection)showFailure('暂时无法获取任务状态。已有任务已保留。');
      else if(isCurrent())toast(error.message);
    }
  }
  update(job);
  if(active(job))state.poll=setTimeout(poll,2500);
}
function renderFacts(job){
  $('#facts').innerHTML=`${job.report?.dimensions?`<div class="panel"><h3>实际尺寸</h3><p class="muted">${job.report.dimensions.map((v,i)=>`${'XYZ'[i]} ${Number(v.toFixed(3))} mm`).join(' / ')}</p></div>`:''}
    ${job.plan?`<div class="panel"><h3>结构与假设</h3><p>${esc(job.plan.summary)}</p>
      ${job.plan.dimensions?.length?'<ul class="list">'+job.plan.dimensions.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>':''}
      ${job.plan.assumptions?.length?'<ul class="list">'+job.plan.assumptions.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>':''}</div>`:''}`;
}
function renderTask(job){
  const activeJob=active(job);
  const stateText=job.status==='cancelling'?'正在停止任务':stages[job.stage]||labels[job.status];
  $('#task-panel').innerHTML=`
    ${activeJob?`<div class="panel"><h3>${esc(stateText)}</h3>
      ${job.position?`<p class="muted">前方 ${Math.max(0,job.position-1)} 个任务</p>`:''}
      ${job.stage==='cad_unavailable'?'<p class="notice error">'+esc(job.cad_health?.message||'FreeCAD 执行端未就绪，正在等待恢复。')+'</p>':''}
      <button id="cancel">取消任务</button></div>`:''}
    ${taskRecord(job,{esc,active})}
    ${job.status==='review'?'<div class="panel"><p>请核对预览与尺寸，再确认这一版。</p><button class="primary" id="detail-confirm">查看并确认</button></div>':''}
    ${job.status==='needs_confirmation'?'<div class="panel"><p>关键尺寸已整理，请在会话中确认后开始建模。</p><button id="modify">打开尺寸确认卡片</button></div>':''}
    ${job.status==='needs_input'?'<div class="panel"><ul class="list">'+(job.plan?.questions||[]).map(q=>`<li>${esc(q)}</li>`).join('')+'</ul><button id="modify" class="primary">补充信息</button></div>':''}
    ${['failed','cancelled','interrupted'].includes(job.status)?'<div class="panel"><p>'+esc(job.status==='cancelled'?'任务已取消。':'本次生成未完成。')+'</p><button id="modify">修改并重新生成</button></div>':''}
    ${job.error?`<details class="project-disclosure"><summary>错误原因与技术详情</summary><pre>${esc(job.error)}</pre></details>`:''}
    ${job.status==='confirmed'?'<div class="panel"><h3>下载模型</h3><div class="actions">'+['model.stl','model.step','model.FCStd'].map(f=>`<button data-file="${f}">${f.split('.').pop()}</button>`).join('')+'</div></div>':''}
    <div class="panel"><h3>用量</h3><p class="muted">${usage(job.usage)}</p></div>
    <details class="project-disclosure"><summary>高级信息 · 生成脚本</summary><p class="mono">任务 ${esc(job.id)}<br>CAD ${esc(job.remote||'尚未提交')}</p>
      ${job.script?'<pre id="source-code">'+esc(job.script)+'</pre>':'<p class="muted">本次任务尚未生成可用脚本。</p>'}</details>
    <div class="project-actions">${job.script?'<button id="download-source">下载生成脚本</button>':''}<button id="rename">重命名项目</button><button id="delete" class="danger">删除项目</button></div>`;
  $('#download-source')?.addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([job.script],{type:'text/plain;charset=utf-8'}));
    const link=document.createElement('a');
    link.href=url;link.download='model.py';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),10000);
  });
  $('#rename').onclick=()=>{
    showDialog(`<h2>重命名模型</h2><form id="rename-form"><label>名称<input name="name" maxlength="80" value="${esc(job.name)}" required></label><button class="primary">保存</button></form>`);
    $('#rename-form').onsubmit=async e=>{
      e.preventDefault();
      await api('projects/'+job.project,{method:'PATCH',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});
      $('#dialog').close();
      route();
    };
  };
  $('#delete').onclick=()=>{
    showDialog('<h2>删除这个项目？</h2><p>将删除此项目所有版本的模型文件。正在处理的任务需要先取消。</p><button id="confirm-delete" class="danger">删除所有版本</button>');
    $('#confirm-delete').onclick=async()=>{
      try{await api('projects/'+job.project,{method:'DELETE'});$('#dialog').close();location.hash='library'}
      catch(error){toast(error.message)}
    };
  };
  $('#task-panel').querySelectorAll('[data-file]').forEach(button=>button.onclick=()=>download(job.id,button.dataset.file));
}
function usage(rows){if(!rows?.some(Boolean))return '服务商未提供统计，或本次没有调用模型。';let sum=0,known=false;for(const row of rows){if(row?.total_tokens!=null){sum+=row.total_tokens;known=true}}return known?`累计 ${sum.toLocaleString()} token · ${rows.length} 次调用`:'已记录原始用量，但该服务商未返回 total_tokens。'}
async function download(id,name){try{const r=await fetch(`/api/jobs/${id}/files/${name}`);if(!r.ok)throw Error((await r.json()).error);const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000)}catch(e){toast(e.message)}}
async function settings(isCurrent=()=>location.hash==='#settings'){
  const [providers,storage]=await Promise.all([api('providers'),api('storage')]);
  if(!isCurrent())return;
  state.providers=providers;
  const current=providers.find(p=>p.is_default)||providers[0];
  const editRows=providers.map(p=>`<div class="provider"><h3>${esc(p.name)}</h3><p class="mono">${esc(p.model)}</p>
    <p class="muted">${p.vision?'图片输入已启用':'仅文字'} · ${p.verified_vision?'图片请求已验证':p.verified_text?'文字请求已验证':'尚未验证'}</p>
    <div class="actions"><button data-edit="${p.id}">编辑</button><button data-test="${p.id}">文字测试</button>
      ${p.vision?`<button data-vision="${p.id}">图片测试</button>`:''}<button data-remove-provider="${p.id}" class="danger">删除</button></div></div>`).join('');
  const open=new Set([...document.querySelectorAll('.settings-disclosure[open]')].map(node=>node.id));
  $('#main').innerHTML=`<div class="settings-page"><div class="page-head"><h1>设置</h1></div><div class="settings-layout">
    <section class="panel settings-model"><h3>模型服务</h3>
      ${current?`<p class="muted">${esc(current.name)} · 默认<br>${esc(current.model)} · ${current.vision?'图片输入已启用':'仅文字'}</p>`:'<p class="muted">尚未添加模型</p>'}</section>
    <button class="primary" id="add-provider">＋ 添加模型</button>
    <details class="settings-disclosure" id="settings-providers"><summary>编辑模型服务</summary>
      <div id="providers" class="settings-body">${editRows||'<p class="muted">请先添加一个模型配置。</p>'}</div></details>
    <details class="settings-disclosure" id="settings-default"><summary>默认模型</summary><div class="settings-body">
      <label>新会话默认模型<select id="default-provider">${options(current?.id)}</select></label></div></details>
    <details class="settings-disclosure" id="settings-connection"><summary>连接与存储</summary><div class="settings-body">
      <p class="muted" id="cad-status">FreeCAD 连接尚未测试</p><button id="test-cad">检查 FreeCAD</button>
      <p class="muted">已归档模型约 ${(storage.bytes/1024/1024).toFixed(2)} MB。成功版本保留到手动删除。</p>
      <p class="muted" id="client-version">客户端 0.5.0 · 服务端 ${esc(state.serverVersion||'未知')}</p>
      <button id="refresh-interface">检查更新并刷新界面</button><details><summary>键盘与窗口诊断</summary><pre id="viewport-report">${esc(viewportReport())}</pre></details>
    </div></details>
    <details class="settings-disclosure" id="settings-password"><summary>修改密码</summary><div class="settings-body">
      <form id="password-form"><label>当前密码<input type="password" name="old" autocomplete="current-password" required></label>
      <label>新密码<input type="password" name="new" minlength="8" maxlength="128" autocomplete="new-password" required></label>
      <button>修改密码并重新登录</button></form>
    </div></details>
  </div><button id="logout" class="quiet">退出登录</button></div>`;
  for(const node of document.querySelectorAll('.settings-disclosure'))node.open=open.has(node.id);
  $('#refresh-interface').onclick=async()=>{if('serviceWorker' in navigator){try{const registration=await navigator.serviceWorker.getRegistration();await registration?.update();if(registration?.waiting){await new Promise(resolve=>{navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true});registration.waiting.postMessage('ACTIVATE_UPDATE');setTimeout(resolve,1500)})}}catch{}}location.reload()};$('#add-provider').onclick=()=>providerForm();document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>providerForm(state.providers.find(p=>p.id===b.dataset.edit)));document.querySelectorAll('[data-test],[data-vision]').forEach(b=>b.onclick=()=>{showDialog('<h2>测试模型连接</h2><p>将发送一次小型测试请求，会产生少量模型调用费用。</p><button id="run-test" class="primary">开始测试</button>');$('#run-test').onclick=async e=>{e.target.disabled=true;try{const result=await post('providers/'+(b.dataset.test||b.dataset.vision)+'/test',{vision:!!b.dataset.vision});$('#dialog-content').innerHTML='<h2>测试已返回</h2><p>'+esc(result.note)+'</p><pre>'+esc(result.answer)+'</pre>';await settings()}catch(error){toast(error.message);e.target.disabled=false}}});document.querySelectorAll('[data-remove-provider]').forEach(b=>b.onclick=()=>{showDialog('<h2>删除模型配置？</h2><p>历史任务会保留。已提交的任务使用提交时的配置快照。</p><button id="remove-provider" class="danger">确认删除</button>');$('#remove-provider').onclick=async()=>{await api('providers/'+b.dataset.removeProvider,{method:'DELETE'});$('#dialog').close();settings()}});$('#default-provider').onchange=async e=>{if(!e.target.value)return;try{await post('preferences',{default_provider:e.target.value});await settings();toast('默认模型已保存到 NAS')}catch(error){toast(error.message)}};$('#password-form').onsubmit=async e=>{e.preventDefault();try{await post('password',Object.fromEntries(new FormData(e.target)));showLogin();toast('密码已修改，请重新登录')}catch(error){toast(error.message)}};$('#logout').onclick=async()=>{await post('logout',{});showLogin()};$('#test-cad').onclick=async()=>{try{const result=await api('cad');$('#cad-status').textContent=result.ready?'FreeCAD 已就绪':'FreeCAD 暂未就绪，请检查其管理页'}catch(e){toast(e.message)}}}
function providerForm(p={}){location.hash='model/'+(p.id||'new')}
window.addEventListener('unhandledrejection',e=>{e.preventDefault();toast(e.reason?.message||'操作未完成，请重试')});
boot();
if('serviceWorker' in navigator&&window.isSecureContext)navigator.serviceWorker.register('/sw.js',{updateViaCache:'none'}).catch(()=>{});
