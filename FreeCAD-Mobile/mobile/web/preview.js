// One preview dialog keeps the same model and controls across both tabs.
export function createPreview({isCurrent,post,toast,esc,download,onContinue,onConfirmed,onConnectionError}){
  const dialog=document.querySelector('#preview-dialog'), content=dialog.querySelector('.preview-content');
  let preview=null,previewEpoch=0;
  function closePreview(){++previewEpoch;preview?.dispose();preview=null;content.replaceChildren();dialog.close()}
  dialog.querySelector('[data-close-preview]').onclick=closePreview;
  dialog.oncancel=e=>{e.preventDefault();closePreview()};
  async function openPreview(job){
    closePreview();const epoch=++previewEpoch;
    content.innerHTML=`<h2 id="preview-title">第 ${job.version} 版 · 模型预览</h2><div class="tabs" role="tablist" aria-label="预览方式"><button id="preview-model" role="tab" aria-selected="true" aria-controls="preview-viewer" class="selected">三维预览</button><button id="preview-drawings" role="tab" aria-selected="false" aria-controls="preview-projections" tabindex="-1">三视图</button></div><div class="viewer" id="preview-viewer"><div class="viewer-message">正在加载模型…</div></div><div class="drawings" id="preview-projections" hidden></div><p class="scroll-note">拖动旋转 · 双指缩放</p>
      <p class="preview-dimensions">${job.report?.dimensions?esc(job.report.dimensions.map(v=>Number(v.toFixed(2))).join(" × "))+" mm":""}</p><p>${esc(job.plan?.summary||'')}</p>${job.plan?.assumptions?.length?`<div class="notice"><strong>请核对建模假设</strong><ul>${job.plan.assumptions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></div>`:''}
      ${job.status==='review'?'<p class="preview-instruction field-note">请核对结构与尺寸，再确认本版。</p>':''}<div class="preview-actions"></div>`;
    dialog.showModal();previewActions(job);
    for(const tab of content.querySelectorAll('[role=tab]'))tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const target=content.querySelector(e.key==='Home'?'#preview-model':e.key==='End'?'#preview-drawings':tab.id==='preview-model'?'#preview-drawings':'#preview-model');target.click();target.focus()}};
    function selectTab(drawings){for(const tab of content.querySelectorAll('[role=tab]')){const selected=(tab.id==='preview-drawings')===drawings;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1}}
    try{const {mountViewer}=await import('./viewer.js?v=0.5.1');if(!isCurrent()||epoch!==previewEpoch)return;
      const v=await mountViewer(content.querySelector('#preview-viewer'),`/api/jobs/${job.id}/files/model.stl`);
      if(!isCurrent()||epoch!==previewEpoch){v.dispose();return}preview=v;
    }catch(e){if(epoch===previewEpoch)content.querySelector('#preview-viewer').innerHTML=`<div class="viewer-message">${esc(e.message)}<button id="retry-preview">重试预览</button></div>`;content.querySelector('#retry-preview')?.addEventListener('click',()=>openPreview(job))}
    if(epoch!==previewEpoch)return;
    content.querySelector('#preview-model').onclick=()=>{content.querySelector('#preview-viewer').hidden=false;content.querySelector('#preview-projections').hidden=true;content.querySelector('#preview-model').classList.add('selected');content.querySelector('#preview-drawings').classList.remove('selected');selectTab(false)};
    content.querySelector('#preview-drawings').onclick=()=>{if(!preview)return toast('请等待模型加载完成');content.querySelector('#preview-projections').innerHTML=preview.drawings().map(d=>`<figure><img src="${d.url}" alt="${d.name}"><figcaption>${d.name}</figcaption></figure>`).join('');content.querySelector('#preview-viewer').hidden=true;content.querySelector('#preview-projections').hidden=false;content.querySelector('#preview-model').classList.remove('selected');content.querySelector('#preview-drawings').classList.add('selected');selectTab(true)};
  }
  function previewActions(job){
    const epoch=previewEpoch, actions=content.querySelector('.preview-actions');
    actions.innerHTML=`<div class="actions"><button id="continue-chat">返回对话修改</button>${job.status==='review'?'<button class="primary" id="confirm-model">确认本版</button>':job.status==='confirmed'?['model.stl','model.step','model.FCStd'].map(f=>`<button data-download="${f}">${f.split('.').pop()}</button>`).join(''):''}</div>`;
    actions.querySelector('#continue-chat').onclick=()=>{closePreview();onContinue(job)};
    actions.querySelector('#confirm-model')?.addEventListener('click',async e=>{
      e.target.disabled=true;
      try{const updated=await post('jobs/'+job.id+'/confirm',{});if(isCurrent()&&epoch===previewEpoch&&dialog.open){content.querySelector('.preview-instruction')?.remove();previewActions(updated);await onConfirmed(updated)}}
      catch(error){
        if(isCurrent()&&error.connection&&epoch===previewEpoch){
          closePreview();
          onConnectionError('暂时无法获取模型确认结果。请重试连接并核对任务状态。');
        }else if(isCurrent())toast(error.message);
        e.target.disabled=false;
      }
    });
    actions.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(job.id,b.dataset.download));
  }
  return {open:openPreview,close:closePreview};
}
