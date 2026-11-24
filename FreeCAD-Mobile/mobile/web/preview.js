// A same-URL history entry lets the browser Back action close the preview first.
const PREVIEW_ENTRY = 'zaowuPreview';
const MODEL_FILES = [['model.stl','STL'], ['model.step','STEP'], ['model.FCStd','FCStd']];

export function modelDownloadButtons(){
  return MODEL_FILES.map(([file,label])=>`<button type="button" data-download="${file}">${label}</button>`).join('');
}

export function createPreview({isCurrent,toast,esc,download,onContinue}){
  const dialog=document.querySelector('#preview-dialog');
  const content=dialog.querySelector('.preview-content');
  const closeButton=dialog.querySelector('[data-close-preview]');
  let viewer, lastJob, epoch=0, dismissing=false, afterDismiss;

  function hide(){
    ++epoch;
    viewer?.dispose();
    viewer=null;
    dialog.close();
    content.replaceChildren();
  }

  function dismiss(next){
    if(!dialog.open||dismissing)return;
    if(history.state?.[PREVIEW_ENTRY]===lastJob.id){
      dismissing=true;
      afterDismiss=next;
      history.back();
    }else{
      hide();
      next?.();
    }
  }

  function onPopState(){
    if(!isCurrent())return;
    const hasEntry=lastJob&&history.state?.[PREVIEW_ENTRY]===lastJob.id;
    if(hasEntry){
      if(!dialog.open)show(lastJob);
      return;
    }
    if(dialog.open)hide();
    const next=afterDismiss;
    afterDismiss=null;
    dismissing=false;
    next?.();
  }

  closeButton.onclick=()=>dismiss();
  dialog.oncancel=event=>{
    event.preventDefault();
    dismiss();
  };
  window.addEventListener('popstate',onPopState);

  function open(job){
    if(!isCurrent()||!job?.archived||dialog.open)return;
    history.pushState({...history.state,[PREVIEW_ENTRY]:job.id},'');
    show(job);
  }

  function restore(jobs){
    if(!isCurrent()||dialog.open)return;
    const id=history.state?.[PREVIEW_ENTRY];
    if(!id)return;
    const job=jobs.find(item=>item.id===id&&item.archived);
    if(job)show(job);
  }

  function show(job){
    lastJob=job;
    const generation=++epoch;
    content.innerHTML=`
      <h2 id="preview-title">第 ${esc(job.version)} 版 · 模型预览</h2>
      <div class="tabs" role="tablist" aria-label="预览方式">
        <button type="button" id="preview-model" role="tab" aria-selected="true" aria-controls="preview-viewer" class="selected">三维预览</button>
        <button type="button" id="preview-drawings" role="tab" aria-selected="false" aria-controls="preview-projections" tabindex="-1">三视图</button>
      </div>
      <div class="viewer" id="preview-viewer"></div>
      <div class="drawings" id="preview-projections" hidden></div>
      <p class="scroll-note">拖动旋转 · 双指缩放</p>
      ${job.report?.dimensions?`<p class="preview-dimensions">${esc(job.report.dimensions.map(v=>Number(v.toFixed(2))).join(' × '))} mm</p>`:''}
      ${job.plan?.summary?`<p>${esc(job.plan.summary)}</p>`:''}
      ${job.plan?.assumptions?.length?`<div class="notice"><strong>建模假设</strong><ul>${job.plan.assumptions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></div>`:''}
      <div class="preview-actions">
        <div class="preview-downloads" role="group" aria-label="下载模型">${modelDownloadButtons()}</div>
        <button type="button" id="continue-chat">返回会话修改</button>
      </div>`;

    dialog.showModal();
    content.querySelector('#continue-chat').onclick=()=>dismiss(()=>onContinue(job));
    for(const button of content.querySelectorAll('[data-download]')){
      button.onclick=()=>download(job.id,button.dataset.download);
    }

    const modelTab=content.querySelector('#preview-model'), drawingsTab=content.querySelector('#preview-drawings');
    const view=content.querySelector('#preview-viewer'), projections=content.querySelector('#preview-projections');
    const tabs=[modelTab,drawingsTab];
    function select(tab){
      const drawings=tab===drawingsTab;
      if(drawings&&!viewer){
        toast('请等待三维模型加载完成');
        return;
      }
      if(drawings){
        projections.innerHTML=viewer.drawings().map(d=>`<figure><img src="${d.url}" alt="${d.name}"><figcaption>${d.name}</figcaption></figure>`).join('');
      }
      view.hidden=drawings;
      projections.hidden=!drawings;
      for(const item of tabs){
        const selected=item===tab;
        item.classList.toggle('selected',selected);
        item.setAttribute('aria-selected',String(selected));
        item.tabIndex=selected?0:-1;
      }
    }
    for(const tab of tabs){
      tab.onclick=()=>select(tab);
      tab.onkeydown=event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();
        const target=event.key==='Home'?modelTab:event.key==='End'?drawingsTab:tabs.find(item=>item!==tab);
        select(target);
        target.focus();
      };
    }

    async function load(){
      view.innerHTML='<div class="viewer-message">正在加载模型…</div>';
      const current=()=>isCurrent()&&generation===epoch;
      try{
        const {mountViewer}=await import('./viewer.js?v=0.6.0');
        if(!current())return;
        const loaded=await mountViewer(view,`/api/jobs/${job.id}/files/model.stl`);
        if(current())viewer=loaded;else loaded.dispose();
      }catch(error){
        if(!current())return;
        view.innerHTML=`<div class="viewer-message">${esc(error.message)}<button type="button" id="retry-preview">重试预览</button></div>`;
        view.querySelector('#retry-preview').onclick=load;
      }
    }
    load();
  }

  function dispose(){
    window.removeEventListener('popstate',onPopState);
    closeButton.onclick=null;
    dialog.oncancel=null;
    afterDismiss=null;
    hide();
  }
  return {open,restore,dispose};
}
