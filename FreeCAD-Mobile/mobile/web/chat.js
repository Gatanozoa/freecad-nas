import {measurementCard} from './measurement-ui.js?v=0.5.1';
import {trackChatViewport} from './viewport.js?v=0.5.1';
import {connectionCard, icon, taskRecord} from './chat-ui.js?v=0.5.1';
import {createPreview} from './preview.js?v=0.5.1';
// Conversation state is reconstructed from saved project versions, not browser-only messages.
const thumbnailCache=new Map(); // In-memory only; private model images never enter persistent caches.
export async function loadThumbnail(job,target,current){
  if(!current())return;
  try{
    let url=thumbnailCache.get(job.id);
    if(!url){
      const {mountViewer}=await import('./viewer.js?v=0.5.1');
      if(!current())return;
      const staging=document.createElement('div');
      staging.className='thumbnail-stage';
      document.body.append(staging);
      let viewer;
      try{
        viewer=await mountViewer(staging,`/api/jobs/${job.id}/files/model.stl`);
        url=viewer.snapshot();
        thumbnailCache.set(job.id,url);
        if(thumbnailCache.size>24)thumbnailCache.delete(thumbnailCache.keys().next().value);
      }finally{
        viewer?.dispose();
        staging.remove();
      }
    }
    if(current()){
      const image=new Image();
      image.src=url;
      image.alt='第 '+job.version+' 版模型缩略图';
      target.replaceChildren(image);
    }
  }catch{
    if(current()&&target.hasAttribute('data-thumb'))target.textContent='点击预览';
  }
}

const chatPositions = new Map();

export function chatScreen({latest, recent, providers, isCurrent, remember, onSubmitted, api, post, toast, esc, options, stages, active, download}) {
  const host=document.querySelector('#main');
  let disposed=false,timer,uploading=false,sending=false,refreshing=false;
  let signature='',thumbEpoch=0,initialized=!latest;
  const scope=latest?.project || 'new';
  const draftKey='zaowu-chat-draft-'+scope;
  let draft={};try{draft=JSON.parse(localStorage.getItem(draftKey)||'{}')}catch{}
  let images=draft.images||[], pending=draft.pending||null;
  const expanded = new Set(chatPositions.get(scope)?.expanded);
  const alive=()=>!disposed && isCurrent();
  host.classList.add('chat-page');host.classList.toggle('chat-home',!latest);
  host.innerHTML=`<section class="chat-shell" aria-label="建模对话">
    <div class="chat-heading"><h1>${latest?esc(latest.name):'新建对话'}</h1>${latest?`<a class="text-link chat-new" href="#new">${icon('new-conversation')}新建对话</a>`:recent?'<a class="text-link chat-recent" href="#recent">最近会话</a>':''}</div>
    <div id="messages" class="messages"><div id="chat-turns" class="chat-turns"></div><div id="chat-feedback" hidden></div></div>
    <div class="composer-dock"><form id="create-form" class="composer">
      <div id="images" class="images" hidden></div>
      <label class="sr-only" for="prompt">描述结构与尺寸</label>
      <textarea id="prompt" maxlength="12000" rows="2" placeholder="描述结构与尺寸，或添加参考图…">${esc(draft.prompt||'')}</textarea>
      <button type="button" id="edit-prompt" class="composer-prompt-trigger" aria-label="编辑建模需求" aria-haspopup="dialog"><span></span></button>
      <div class="composer-tools"><button type="button" id="attach" class="icon-button" aria-label="添加文件或图片" title="添加 JPG、PNG 或 WebP 参考图">${icon('attach')}</button>
        <input id="image-input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>
        <label class="sr-only" for="provider">本次使用的模型</label><select id="provider">${options(draft.provider||latest?.provider||providers.find(p=>p.is_default)?.id)}</select>
        <button class="primary send-button" id="generate" aria-label="发送建模需求" title="发送">${icon('send')}</button></div>
    </form><div class="composer-shortcuts"><button id="demo" class="quiet">运行固定零件测试</button><span id="composer-note" class="field-note"></span></div></div>
  </section><aside class="chat-sidebar" aria-label="本次建模" hidden></aside>
  <div id="keyboard-editor" class="keyboard-editor" role="dialog" aria-modal="true" aria-label="编辑建模需求" hidden>
    <div class="keyboard-entry">
      <button type="button" id="close-keyboard" aria-label="收起输入栏" title="收起输入栏">${icon('expand')}</button>
      <textarea id="keyboard-prompt" rows="1" maxlength="12000" aria-label="描述结构与尺寸" placeholder="描述结构与尺寸…"></textarea>
      <button type="button" class="primary send-button" id="keyboard-send" aria-label="发送建模需求" title="发送">${icon('send')}</button>
    </div>
  </div>`;
  const $=s=>host.querySelector(s), messages=$('#messages'), turns=$('#chat-turns'), feedback=$('#chat-feedback');
  const editor=$('#keyboard-editor'), keyboardInput=$('#keyboard-prompt'), prompt=$('#prompt'), trigger=$('#edit-prompt');
  const shell=host.closest('#shell');
  const editorBackground=[$('.chat-shell'),shell.querySelector('header'),shell.querySelector('nav')];
  let editorScroll;
  function scrollToEnd(){messages.scrollTop=messages.scrollHeight}
  function setEditorVisibility(open){
    editor.hidden=!open;
    for(const node of editorBackground)node.inert=open;
    viewport.schedule();
  }
  function openEditor(){
    if(prompt.readOnly)return;
    if(!matchMedia('(max-width:800px)').matches){prompt.focus();return}
    if(!editor.hidden){keyboardInput.focus();return}
    editorScroll={top:messages.scrollTop,atEnd:messages.scrollTop+messages.clientHeight>=messages.scrollHeight-24};
    keyboardInput.value=prompt.value;
    editor.classList.add('keyboard-unmeasured');
    setEditorVisibility(true);
    resizeInput(keyboardInput);
    keyboardInput.focus({preventScroll:true});
    keyboardInput.setSelectionRange(keyboardInput.value.length,keyboardInput.value.length);
  }
  function closeEditor(restoreFocus=true){
    if(editor.hidden)return;
    setEditorVisibility(false);
    if(!restoreFocus)return;
    if(matchMedia('(max-width:800px)').matches)trigger.focus({preventScroll:true});
    if(editorScroll.atEnd)scrollToEnd();else messages.scrollTop=editorScroll.top;
  }
  const viewport=trackChatViewport(editor,closeEditor);
  trigger.onclick=openEditor;
  $('#close-keyboard').onclick=()=>closeEditor();
  editor.onpointerdown=e=>{if(e.target===editor||e.target.closest('button'))e.preventDefault()};
  editor.onclick=e=>{if(e.target===editor)closeEditor()};
  editor.onfocusout=e=>{
    if(!editor.hidden&&!editor.contains(e.relatedTarget))closeEditor();
  };
  editor.onkeydown=e=>{
    if(e.key==='Escape'&&!e.isComposing){e.preventDefault();closeEditor()}
    if(e.key==='Tab'){
      const buttons=[...editor.querySelectorAll('button:not(:disabled), textarea')];
      const end=e.shiftKey?buttons[0]:buttons.at(-1);
      if(document.activeElement===end){e.preventDefault();(e.shiftKey?buttons.at(-1):buttons[0]).focus()}
    }
  };
  function clearFeedback(){feedback.hidden=true;feedback.replaceChildren()}
  function showFailure(message,retry=refresh){
    if(!alive())return;
    if(pending){
      message='暂时无法确认消息是否送达。你的内容已保留。';
      retry=retrySubmission;
    }
    feedback.hidden=false;
    feedback.dataset.request=pending?.request_id||'';
    const target=document.createElement('div');
    feedback.replaceChildren();
    if(pending){
      const bubble=document.createElement('div');
      bubble.className='user-message pending-message';
      bubble.textContent=userText(pending);
      feedback.append(bubble);
    }
    feedback.append(target);
    connectionCard(target,message,retry);
    scrollToEnd();
  }
  const preview=createPreview({
    isCurrent:alive,post,toast,esc,download,onContinue:openEditor,
    onConfirmed:refresh,onConnectionError:showFailure,
  });
  function newId(){return globalThis.crypto?.randomUUID?.()||Date.now()+'-'+Math.random()}
  function persist(){
    try{localStorage.setItem(draftKey,JSON.stringify({prompt:$('#prompt').value,provider:$('#provider').value,images,pending}))}catch{}
  }
  function resizeInput(input){
    if(!input.offsetWidth){input.style.removeProperty('height');return}
    input.style.height='auto';
    input.style.height=input.scrollHeight+'px';
  }
  function updatePrompt(){
    trigger.firstElementChild.textContent=prompt.value||prompt.placeholder;
    trigger.classList.toggle('placeholder',!prompt.value);
    resizeInput(prompt);
  }
  function controls(){
    const busy=!!latest&&active(latest);
    $('#generate').disabled=!initialized||busy||uploading||sending||!!pending;
    $('#demo').disabled=!initialized||busy||uploading||sending||!!pending;
    $('#attach').disabled=uploading||sending||!!pending;
    prompt.readOnly=sending||!!pending;
    keyboardInput.readOnly=prompt.readOnly;
    trigger.disabled=prompt.readOnly;
    $('#keyboard-send').disabled=$('#generate').disabled;
    $('#provider').disabled=sending||!!pending;
    $('#images').querySelectorAll('button').forEach(button=>button.disabled=sending||!!pending);
    $('#composer-note').textContent=uploading?'正在上传…':busy?'任务进行中，可以先填写下一步需求':!providers.length?'请到设置添加模型，或先运行固定测试':'';
  }
  prompt.oninput=()=>{updatePrompt();persist()};
  keyboardInput.oninput=()=>{
    prompt.value=keyboardInput.value;
    updatePrompt();
    resizeInput(keyboardInput);
    persist();
  };
  $('#provider').onchange=persist;
  function submitFromKeyboard(){
    if(!$('#generate').disabled)submit(false);
  }
  function submitShortcut(e){
    if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){
      e.preventDefault();
      submitFromKeyboard();
    }
  }
  prompt.onkeydown=keyboardInput.onkeydown=submitShortcut;
  $('#keyboard-send').onclick=submitFromKeyboard;
  $('#attach').onclick=()=>$('#image-input').click();
  $('#image-input').onchange=async e=>{
    uploading=true;controls();
    try{for(const file of e.target.files){
      if(images.length>=4)throw Error('最多添加 4 张参考图');
      if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('目前支持 JPG、PNG、WebP 图片文件；PDF 或 CAD 文件请先转换为图片');
      if(file.size>10*1024*1024)throw Error('每张图片最大 10MB');
      const result=await api('uploads',{method:'POST',raw:true,headers:{'Content-Type':file.type},body:file});
      if(!alive())return;images.push(result.id);persist();renderImages();
    }}catch(error){
      if(alive()){
        if(error.connection)showFailure('图片上传中断。连接恢复后，请重新选择尚未上传的图片。');
        else toast(error.message);
      }
    }finally{if(alive()){uploading=false;e.target.value='';controls()}}
  };
  function renderImages(){
    $('#images').hidden=!images.length;
    $('#images').innerHTML=images.map((img,i)=>`<div class="image-tile"><img src="/api/uploads/${img}" alt="参考图 ${i+1}"><button type="button" data-remove="${i}" aria-label="移除参考图 ${i+1}">移除</button></div>`).join('');
    host.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{images.splice(Number(b.dataset.remove),1);persist();renderImages();controls()});
  }
  async function retrySubmission(){
    if(sending||!pending||!alive())return false;
    const request=pending;
    sending=true;
    controls();
    try{
      const job=await post('jobs',request);
      // A later view may already have recovered the same request.
      try{
        const saved=JSON.parse(localStorage.getItem(draftKey)||'{}');
        if(saved.pending?.request_id===request.request_id){
          if(request.demo){
            delete saved.pending;
            localStorage.setItem(draftKey,JSON.stringify(saved));
          }else localStorage.removeItem(draftKey);
        }
      }catch{}
      if(alive())scrollToEnd();
      onSubmitted(job,alive());
      return true;
    }catch(error){
      if(!alive())return false;
      if(error.connection){
        if(feedback.dataset.request!==request.request_id||feedback.hidden)showFailure('暂时无法确认消息是否送达。你的内容已保留。',retrySubmission);
      }else if(error.status!==401){
        pending=null;
        persist();
        clearFeedback();
        toast(error.message);
      }
      return false;
    }finally{
      sending=false;
      if(alive())controls();
    }
  }
  function submit(demo){
    if(!alive()||!initialized||pending||sending||uploading||(latest&&active(latest)))return;
    const text=$('#prompt').value.trim(),provider=$('#provider').value;
    if(!demo&&!text)return toast('请描述结构、尺寸或修改要求');
    if(!demo&&!provider)return toast('请到设置中添加模型，然后在下拉框中选择');
    const prompt=demo?'创建长60mm、宽30mm、厚5mm的矩形板，中心直径6mm贯穿孔':(latest?'原需求：'+latest.prompt+'\n本次补充或修改：\n':'')+text;
    pending={prompt,provider,images:demo?[]:[...images],parent:demo?null:latest?.id,demo,request_id:newId()};
    closeEditor();
    persist();
    retrySubmission();
  }
  $('#create-form').onsubmit=e=>{e.preventDefault();submit(false)};$('#demo').onclick=()=>submit(true);
  function userText(job){const marker='\n本次补充或修改：\n';return job.parent&&job.prompt.includes(marker)?job.prompt.split(marker).pop():job.prompt}
  function response(job){
    if(active(job))return `<div class="thinking">
      <span class="activity-dot" aria-hidden="true"></span><strong role="status">${esc(job.status==='cancelling'?'正在停止任务':stages[job.stage]||'等待处理')}</strong>
      ${job.status==='cancelling'?'':`<button type="button" class="cancel-task" data-cancel="${job.id}">取消任务</button>`}
    </div>
      ${job.stage==='cad_unavailable'?`<p class="notice error">${esc(job.cad_health?.message||'FreeCAD 执行端暂未就绪，恢复后会继续。')}</p>`:''}
      ${job.position?`<p class="field-note">前方 ${Math.max(0,job.position-1)} 个任务</p>`:''}`;
    if(job.archived)return `<button class="model-message" data-preview="${job.id}" aria-label="打开第 ${job.version} 版三维预览">
      <span class="model-thumbnail" data-thumb="${job.id}"><span>加载中</span></span>
      <span class="model-caption"><strong>${job.status==='confirmed'?'模型已确认':'模型已生成'} · 第 ${job.version} 版</strong>
      <span class="preview-link">${job.report?.dimensions?esc(job.report.dimensions.map(v=>Number(v.toFixed(2))).join(' × '))+' mm · ':''}${job.status==='confirmed'?'查看与下载':'打开三维预览'}</span></span></button>`;
    if(['needs_input','needs_confirmation'].includes(job.status))return measurementCard(job,esc,job.id===latest?.id);
    return `<p>${job.status==='cancelled'?'任务已取消。':job.status==='interrupted'?'任务已中断，请重新发送需求。':'本次生成未完成，可以修改需求后重试。'}</p>${job.error?`<details><summary>查看错误原因</summary><pre>${esc(job.error)}</pre></details>`:''}`;
  }
  async function thumbnails(jobs,epoch){
    for(const job of jobs.filter(j=>j.archived)){
      if(!alive()||epoch!==thumbEpoch)return;
      const target=turns.querySelector(`[data-thumb="${job.id}"]`);
      if(target)await loadThumbnail(job,target,()=>alive()&&epoch===thumbEpoch);
    }
  }
  async function refresh(){
    if(!alive()||refreshing)return;
    refreshing=true;
    clearTimeout(timer);
    try{
      let jobs=[];
      if(latest){const all=await api('jobs?project='+encodeURIComponent(latest.project));if(!alive())return;jobs=all;
        if(!jobs.length){location.hash='recent';return}
        latest=jobs.at(-1);remember(latest);
        $('.chat-heading h1').textContent=latest.name;
      }
      if(!latest)await api('session');
      if(!alive())return;
      const sig=JSON.stringify(jobs.map(j=>[j.id,j.status,j.stage,j.position,j.archived,j.error,j.cad_health,j.name,j.plan,j.events]));
      if(sig!==signature){
        const nearBottom=messages.scrollTop+messages.clientHeight>=messages.scrollHeight-160;
        signature=sig;
        for(const record of turns.querySelectorAll('[data-record]')){
          if(record.open)expanded.add(record.dataset.record);else expanded.delete(record.dataset.record);
        }
        const previous = chatPositions.get(scope);
        const firstRender = turns.childElementCount===0;
        turns.innerHTML=jobs.length?jobs.map(j=>`<article class="chat-turn"><div class="user-message"><p>${esc(userText(j))}</p>${j.images.length?`<div class="message-images">${j.images.map((img,i)=>`<a href="/api/uploads/${img}" target="_blank" rel="noopener"><img src="/api/uploads/${img}" alt="参考图 ${i+1}"></a>`).join('')}</div>`:''}</div><div class="assistant-message">${active(j)?response(j):''}${taskRecord(j,{esc,active},expanded.has(j.id))}${active(j)?'':response(j)}<a class="task-details" href="#detail/${j.id}">任务详情 · 查看版本</a></div></article>`).join(''):'<div class="chat-empty"><h2>今天想做什么零件？</h2></div>';
        turns.querySelectorAll('[data-confirm-dimensions]').forEach(b=>b.onclick=()=>jobAction(b,b.dataset.confirmDimensions,'confirm-dimensions'));
        host.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>preview.open(jobs.find(j=>j.id===b.dataset.preview)));
        turns.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=()=>jobAction(b,b.dataset.cancel,'cancel'));
        thumbnails(jobs,++thumbEpoch).catch(()=>{});
        if(firstRender&&previous&&!previous.atEnd)messages.scrollTop=previous.scrollTop;
        else if(nearBottom&&jobs.length)scrollToEnd();
      }
      const sidebar=host.querySelector('.chat-sidebar');sidebar.hidden=!latest;
      if(latest){sidebar.innerHTML=`<h2>本次建模</h2><span class="pill">第 ${latest.version} 版 · ${esc(stages[latest.stage]||latest.status)}</span>${latest.report?.dimensions?`<div class="panel"><h3>实际尺寸</h3><p class="muted">${esc(latest.report.dimensions.map(v=>Number(v.toFixed(2))).join(' × '))} mm</p></div>`:''}${latest.archived?'<button class="primary" id="sidebar-preview">打开三维预览</button>':''}<a class="text-link" href="#detail/${latest.id}">查看任务详情 <svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></a>`;sidebar.querySelector('#sidebar-preview')?.addEventListener('click',()=>preview.open(latest))}
      initialized=true;
      if(!pending)clearFeedback();
      controls();
      if(latest&&active(latest))timer=setTimeout(refresh,2500);
    }catch(error){
      if(!alive())return;
      if(error.connection){
        if(feedback.hidden)showFailure('暂时无法获取会话或任务状态。已有需求和任务已保留。');
      }else toast(error.message);
    }finally{refreshing=false}
  }
  async function jobAction(button,id,action){
    button.disabled=true;
    try{
      await post('jobs/'+id+'/'+action,{});
      if(alive())await refresh();
    }catch(error){
      if(!alive())return;
      if(error.connection)showFailure('暂时无法确认这项操作是否送达。请先核对任务状态。');
      else toast(error.message);
    }finally{button.disabled=false}
  }
  updatePrompt();renderImages();controls();
  if(pending)showFailure('暂时无法确认消息是否送达。你的内容已保留。',retrySubmission);
  refresh();
  return ()=>{
    if(signature)chatPositions.set(scope,{expanded:new Set([...turns.querySelectorAll('[data-record][open]')].map(node=>node.dataset.record)),scrollTop:messages.scrollTop,atEnd:messages.scrollTop+messages.clientHeight>=messages.scrollHeight-24});
    closeEditor(false);viewport.dispose();disposed=true;clearTimeout(timer);++thumbEpoch;preview.close();
    host.classList.remove('chat-page','chat-home');
  };
}
