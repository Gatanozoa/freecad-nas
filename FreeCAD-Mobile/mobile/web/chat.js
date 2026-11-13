// Conversation state is reconstructed from saved project versions, not browser-only messages.
const thumbnailCache=new Map(); // In-memory only; private model images never enter persistent caches.
export async function chatScreen({id,api,post,toast,esc,options,stages,active,download}) {
  const host=document.querySelector('#main'), route=location.hash;
  let disposed=false,timer,latest=id?await api('jobs/'+id):null,images=[],uploading=false,sending=false;
  let preview=null,previewEpoch=0,requestId=newId(),signature='',thumbEpoch=0;
  const providers=await api('providers');
  const draftKey='zaowu-chat-draft-'+(latest?.project||'new');
  let draft={};try{draft=JSON.parse(localStorage.getItem(draftKey)||'{}')}catch{}
  if(location.hash!==route)return ()=>{};
  host.classList.add('chat-page');host.classList.toggle('chat-home',!latest);
  host.innerHTML=`<section class="chat-shell" aria-label="建模对话">
    <div class="chat-heading"><h1>${latest?esc(latest.name):'创建模型'}</h1>${latest?'<a class="text-link" href="#create">新建对话</a>':''}</div>
    <div id="messages" class="messages"></div><p id="chat-connection" class="field-note" role="status" hidden></p>
    <div class="composer-dock"><form id="create-form" class="composer">
      <div id="images" class="images" hidden></div>
      <label class="sr-only" for="prompt">描述结构与尺寸</label>
      <textarea id="prompt" required maxlength="12000" rows="2" placeholder="描述结构与尺寸，或补充修改要求…">${esc(draft.prompt||'')}</textarea>
      <div class="composer-tools"><button type="button" id="attach" class="icon-button" aria-label="添加文件或图片" title="添加 JPG、PNG 或 WebP 参考图"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button>
        <input id="image-input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>
        <label class="sr-only" for="provider">本次使用的模型</label><select id="provider">${options(draft.provider||latest?.provider||providers.find(p=>p.is_default)?.id)}</select>
        <button class="primary send-button" id="generate" aria-label="发送建模需求" title="发送"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg></button></div>
    </form><div class="composer-shortcuts"><button id="demo" class="quiet">运行固定零件测试</button><span id="composer-note" class="field-note"></span></div></div>
  </section><aside class="chat-sidebar" aria-label="本次建模" hidden></aside>`;
  const $=s=>host.querySelector(s), messages=$('#messages');
  const dialog=document.querySelector('#preview-dialog'), content=dialog.querySelector('.preview-content');
  function newId(){return globalThis.crypto?.randomUUID?.()||Date.now()+'-'+Math.random()}
  function persist(){try{localStorage.setItem(draftKey,JSON.stringify({prompt:$('#prompt').value,provider:$('#provider').value}))}catch{}}
  function resizeInput(){const el=$('#prompt');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,180)+'px'}
  function controls(){
    const busy=!!latest&&active(latest);
    $('#generate').disabled=busy||uploading||sending;$('#demo').disabled=busy||uploading||sending;
    $('#attach').disabled=uploading||sending;
    $('#composer-note').textContent=uploading?'正在上传…':busy?'任务进行中，可先填写下一步修改要求':images.length?'最多 4 张 · JPG / PNG / WebP · 每张 10MB':!providers.length?'请先到设置中添加模型，或运行固定测试':latest?'发送后生成新版本，原版本保留':'';
  }
  $('#prompt').oninput=()=>{resizeInput();persist()};$('#provider').onchange=persist;
  $('#prompt').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();if(!$('#generate').disabled)$('#create-form').requestSubmit()}};
  $('#attach').onclick=()=>$('#image-input').click();
  $('#image-input').onchange=async e=>{
    uploading=true;controls();
    try{for(const file of e.target.files){
      if(images.length>=4)throw Error('最多添加 4 张参考图');
      if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('目前支持 JPG、PNG、WebP 图片文件；PDF 或 CAD 文件请先转换为图片');
      if(file.size>10*1024*1024)throw Error('每张图片最大 10MB');
      const result=await api('uploads',{method:'POST',raw:true,headers:{'Content-Type':file.type},body:file});
      if(disposed)return;images.push(result.id);renderImages();
    }}catch(error){if(!disposed)toast(error.message)}finally{if(!disposed){uploading=false;e.target.value='';controls()}}
  };
  function renderImages(){
    $('#images').hidden=!images.length;
    $('#images').innerHTML=images.map((img,i)=>`<div class="image-tile"><img src="/api/uploads/${img}" alt="参考图 ${i+1}"><button type="button" data-remove="${i}" aria-label="移除参考图 ${i+1}">移除</button></div>`).join('');
    host.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{images.splice(Number(b.dataset.remove),1);renderImages();controls()});
  }
  async function submit(demo){
    if(disposed||location.hash!==route||sending||uploading||(latest&&active(latest)))return;
    const text=$('#prompt').value.trim(),provider=$('#provider').value;
    if(!demo&&!text)return toast('请描述结构、尺寸或修改要求');
    if(!demo&&!provider)return toast('请到设置中添加模型，然后在下拉框中选择');
    sending=true;controls();
    try{
      const prompt=demo?'创建长60mm、宽30mm、厚5mm的矩形板，中心直径6mm贯穿孔':(latest?'原需求：'+latest.prompt+'\n本次补充或修改：\n':'')+text;
      const job=await post('jobs',{prompt,provider,images:demo?[]:images,parent:demo?null:latest?.id,demo,request_id:requestId});
      if(disposed)return;
      if(!demo)localStorage.removeItem(draftKey);
      location.hash='job/'+job.id;
    }catch(error){if(!disposed)toast(error.message)}finally{if(!disposed){sending=false;controls()}}
  }
  $('#create-form').onsubmit=e=>{e.preventDefault();submit(false)};$('#demo').onclick=()=>submit(true);
  function userText(job){const marker='\n本次补充或修改：\n';return job.parent&&job.prompt.includes(marker)?job.prompt.split(marker).pop():job.prompt}
  function response(job){
    if(active(job))return `<div class="thinking" role="status"><span class="activity-dot"></span><strong>${esc(job.status==='cancelling'?'正在停止任务':stages[job.stage]||'等待处理')}</strong></div>
      <details class="progress-details"><summary>查看生成进度</summary><ol>${['分析需求并生成脚本','进入 FreeCAD 队列','执行与检查模型','保存模型文件'].map((s,i)=>`<li class="${i===stageIndex(job)?'current':''}">${s}</li>`).join('')}</ol></details>
      ${job.stage==='cad_unavailable'?`<p class="notice error">${esc(job.cad_health?.message||'FreeCAD 执行端暂未就绪，恢复后会继续。')}</p>`:''}
      ${job.position?`<p class="field-note">前方 ${Math.max(0,job.position-1)} 个任务</p>`:''}<button class="quiet" data-cancel="${job.id}">取消任务</button>`;
    if(job.archived)return `<p>${job.status==='confirmed'?'这一版已确认。':'模型已生成，请打开预览核对尺寸和结构。'}</p>
      <button class="model-message" data-preview="${job.id}" aria-label="打开第 ${job.version} 版三维预览"><div class="model-thumbnail" data-thumb="${job.id}"><span>正在准备模型缩略图…</span></div><span class="model-caption"><strong>第 ${job.version} 版</strong><span>${esc(job.report?.dimensions?.map(v=>Number(v.toFixed(2))).join(' × ')||'三维模型')}${job.report?.dimensions?' mm':''}</span><span class="preview-link">打开三维预览 <svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 18 18 6M6 6h12v12"/></svg></span></span></button>`;
    if(job.status==='needs_input')return `<p>还需要补充以下信息：</p><ul>${(job.plan?.questions||[]).map(q=>`<li>${esc(q)}</li>`).join('')}</ul><p class="field-note">在下方回复后，生成新版本。</p>`;
    return `<p>${job.status==='cancelled'?'任务已取消。':job.status==='interrupted'?'任务已中断，请重新发送需求。':'本次生成未完成，可以修改需求后重试。'}</p>${job.error?`<details><summary>查看错误原因</summary><pre>${esc(job.error)}</pre></details>`:''}`;
  }
  function stageIndex(job){return ['generating','repairing','pending','demo'].includes(job.stage)?0:['submitting','cad_queued','cad_unavailable'].includes(job.stage)?1:job.stage==='building'?2:3}
  async function thumbnails(jobs,epoch){
    const {mountViewer}=await import('./viewer.js');
    for(const job of jobs.filter(j=>j.archived)){
      if(disposed||epoch!==thumbEpoch)return;
      const target=messages.querySelector(`[data-thumb="${job.id}"]`);if(!target)continue;
      if(thumbnailCache.has(job.id)){target.innerHTML=`<img src="${thumbnailCache.get(job.id)}" alt="第 ${job.version} 版模型缩略图">`;continue}
      const staging=document.createElement('div');staging.className='thumbnail-stage';host.append(staging);
      let viewer;
      try{viewer=await mountViewer(staging,`/api/jobs/${job.id}/files/model.stl`);const url=viewer.snapshot();
        if(!disposed&&epoch===thumbEpoch){target.innerHTML=`<img src="${url}" alt="第 ${job.version} 版模型缩略图">`;thumbnailCache.set(job.id,url);if(thumbnailCache.size>24)thumbnailCache.delete(thumbnailCache.keys().next().value)}
      }catch{if(!disposed&&epoch===thumbEpoch)target.innerHTML='<span>缩略图暂不可用 · 点击重试预览</span>'}
      finally{viewer?.dispose();staging.remove()}
    }
  }
  async function refresh(){
    try{
      let jobs=[];
      if(latest){const all=await api('jobs?project='+encodeURIComponent(latest.project));if(disposed||location.hash!==route)return;jobs=all.filter(j=>j.project===latest.project).sort((a,b)=>a.version-b.version);if(!jobs.length)throw Error('项目已被删除');latest=jobs.at(-1)}
      const sig=JSON.stringify(jobs.map(j=>[j.id,j.status,j.stage,j.position,j.archived,j.error,j.cad_health,j.name]));
      if(sig!==signature){
        const nearBottom=messages.scrollTop+messages.clientHeight>=messages.scrollHeight-160;
        signature=sig;
        messages.innerHTML=jobs.length?jobs.map(j=>`<article class="chat-turn"><div class="user-message"><p>${esc(userText(j))}</p>${j.images.length?`<div class="message-images">${j.images.map((img,i)=>`<a href="/api/uploads/${img}" target="_blank" rel="noopener"><img src="/api/uploads/${img}" alt="参考图 ${i+1}"></a>`).join('')}</div>`:''}</div><div class="assistant-message">${response(j)}<a class="task-details" href="#detail/${j.id}">任务详情 · 第 ${j.version} 版</a></div></article>`).join(''):'<div class="chat-empty"><h2>今天想做什么零件？</h2></div>';
        host.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>openPreview(jobs.find(j=>j.id===b.dataset.preview)));
        host.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await post('jobs/'+b.dataset.cancel+'/cancel',{});await refresh()}catch(e){toast(e.message);b.disabled=false}});
        thumbnails(jobs,++thumbEpoch).catch(()=>{});
        if(nearBottom&&jobs.length)messages.scrollTop=messages.scrollHeight;
      }
      const sidebar=host.querySelector('.chat-sidebar');sidebar.hidden=!latest;
      if(latest){sidebar.innerHTML=`<h2>本次建模</h2><span class="pill">第 ${latest.version} 版 · ${esc(stages[latest.stage]||latest.status)}</span>${latest.report?.dimensions?`<div class="panel"><h3>实际尺寸</h3><p class="muted">${esc(latest.report.dimensions.map(v=>Number(v.toFixed(2))).join(' × '))} mm</p></div>`:''}${latest.archived?'<button class="primary" id="sidebar-preview">打开三维预览</button>':''}<a class="text-link" href="#detail/${latest.id}">查看任务详情 <svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></a>`;sidebar.querySelector('#sidebar-preview')?.addEventListener('click',()=>openPreview(latest))}
      $('#chat-connection').hidden=true;controls();
      if(latest&&active(latest)){clearTimeout(timer);timer=setTimeout(refresh,2500)}
    }catch(error){if(disposed)return;$('#chat-connection').textContent=error.message+'，正在重试获取状态';$('#chat-connection').hidden=false;clearTimeout(timer);timer=setTimeout(refresh,4000)}
  }
  function closePreview(){++previewEpoch;preview?.dispose();preview=null;content.replaceChildren();dialog.close()}
  dialog.querySelector('[data-close-preview]').onclick=closePreview;
  dialog.oncancel=e=>{e.preventDefault();closePreview()};
  async function openPreview(job){
    closePreview();const epoch=++previewEpoch;
    content.innerHTML=`<h2 id="preview-title">第 ${job.version} 版 · 模型预览</h2><div class="tabs" role="tablist" aria-label="预览方式"><button id="preview-model" role="tab" aria-selected="true" aria-controls="preview-viewer" class="selected">三维模型</button><button id="preview-drawings" role="tab" aria-selected="false" aria-controls="preview-projections" tabindex="-1">三视图</button></div><div class="viewer" id="preview-viewer"><div class="viewer-message">正在加载模型…</div></div><div class="drawings" id="preview-projections" hidden></div><p class="scroll-note">拖动旋转，双指缩放。三视图用于结构核对，不是加工图。</p>
      <p class="preview-dimensions">${job.report?.dimensions?esc(job.report.dimensions.map(v=>Number(v.toFixed(2))).join(" × "))+" mm":""}</p><p>${esc(job.plan?.summary||'')}</p>${job.plan?.assumptions?.length?`<div class="notice"><strong>请核对建模假设</strong><ul>${job.plan.assumptions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></div>`:''}
      <div class="preview-actions"></div>`;
    dialog.showModal();previewActions(job);
    for(const tab of content.querySelectorAll('[role=tab]'))tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const target=content.querySelector(e.key==='Home'?'#preview-model':e.key==='End'?'#preview-drawings':tab.id==='preview-model'?'#preview-drawings':'#preview-model');target.click();target.focus()}};
    function selectTab(drawings){for(const tab of content.querySelectorAll('[role=tab]')){const selected=(tab.id==='preview-drawings')===drawings;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1}}
    try{const {mountViewer}=await import('./viewer.js');if(disposed||epoch!==previewEpoch)return;
      const v=await mountViewer(content.querySelector('#preview-viewer'),`/api/jobs/${job.id}/files/model.stl`);
      if(disposed||epoch!==previewEpoch){v.dispose();return}preview=v;
    }catch(e){if(epoch===previewEpoch)content.querySelector('#preview-viewer').innerHTML=`<div class="viewer-message">${esc(e.message)}<button id="retry-preview">重试预览</button></div>`;content.querySelector('#retry-preview')?.addEventListener('click',()=>openPreview(job))}
    if(epoch!==previewEpoch)return;
    content.querySelector('#preview-model').onclick=()=>{content.querySelector('#preview-viewer').hidden=false;content.querySelector('#preview-projections').hidden=true;content.querySelector('#preview-model').classList.add('selected');content.querySelector('#preview-drawings').classList.remove('selected');selectTab(false)};
    content.querySelector('#preview-drawings').onclick=()=>{if(!preview)return toast('请等待模型加载完成');content.querySelector('#preview-projections').innerHTML=preview.drawings().map(d=>`<figure><img src="${d.url}" alt="${d.name}"><figcaption>${d.name}</figcaption></figure>`).join('');content.querySelector('#preview-viewer').hidden=true;content.querySelector('#preview-projections').hidden=false;content.querySelector('#preview-model').classList.remove('selected');content.querySelector('#preview-drawings').classList.add('selected');selectTab(true)};
  }
  function previewActions(job){
    const actions=content.querySelector('.preview-actions');
    actions.innerHTML=`<div class="actions"><button id="continue-chat">继续修改</button>${job.status==='review'?'<button class="primary" id="confirm-model">确认本版</button>':job.status==='confirmed'?['model.stl','model.step','model.FCStd'].map(f=>`<button data-download="${f}">${f.split('.').pop()}</button>`).join(''):''}</div>`;
    actions.querySelector('#continue-chat').onclick=()=>{closePreview();$('#prompt').focus()};
    actions.querySelector('#confirm-model')?.addEventListener('click',async e=>{e.target.disabled=true;try{await post('jobs/'+job.id+'/confirm',{});job.status='confirmed';previewActions(job);await refresh()}catch(error){toast(error.message);e.target.disabled=false}});
    actions.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(job.id,b.dataset.download));
  }
  resizeInput();controls();await refresh();
  return ()=>{disposed=true;clearTimeout(timer);++thumbEpoch;closePreview();host.classList.remove('chat-page','chat-home')};
}
