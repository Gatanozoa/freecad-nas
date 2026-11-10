const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export async function providerScreen({id,api,toast}){
  const list=await api('providers'),p=list.find(x=>x.id===id)||{};
  if(id!=='new'&&!p.id)throw Error('模型配置不存在，请返回设置页重新选择');
  document.querySelector('#main').innerHTML=`<a class="back" href="#settings">‹ 返回设置</a><div class="page-head"><div><h1>${p.id?'编辑模型服务':'添加模型服务'}</h1></div></div><div class="split"><form class="panel" id="provider-form"><div class="notice error" id="provider-error" role="alert" hidden></div><label>配置名称<input name="name" value="${esc(p.name)}" placeholder="例如：日常零件建模" maxlength="200" required></label><label>API 地址<input name="url" type="url" inputmode="url" value="${esc(p.url)}" placeholder="https://服务商地址/v1" required></label><p class="field-note">可填写基础地址，也可粘贴完整的 /chat/completions 地址。当前支持兼容 Chat Completions 的协议。</p><label>模型名称<input name="model" value="${esc(p.model)}" placeholder="填写服务商提供的准确模型 ID" maxlength="200" autocapitalize="none" spellcheck="false" required></label><label>API Key<input name="key" type="password" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="${p.id?'已保存在 NAS；留空保持当前 Key':'粘贴 API Key'}" ${p.id?'':'required'}></label><p class="field-note">${p.id?'当前 Key 已保存，不回显。填写新 Key 将替换旧 Key。':'Key 将随保存请求发送到 NAS，在后端加密存储。'}</p><label class="check"><input name="vision" type="checkbox" ${p.vision?'checked':''}>这个模型支持图片输入</label><label class="check"><input name="make_default" type="checkbox" ${p.is_default||!list.length?'checked':''}>设为新任务的默认模型</label><p class="field-note">保存不会调用模型、不会产生模型费用。保存后可在设置页主动测试连接。</p><div class="actions"><button type="button" id="cancel-provider">返回设置</button><button class="primary" id="save-provider">保存到 NAS</button></div></form><aside><div class="panel"><h2>使用配置</h2><p>保存后，在创建页选择此模型。支持图片的模型需勾选“支持图片输入”。</p><p class="muted">Key 保存在 NAS。请通过 HTTPS 访问，以保护 Key 的提交过程。</p></div></aside></div>`;
  document.querySelector('#cancel-provider').onclick=()=>location.hash='settings';
  document.querySelector('#provider-form').onsubmit=async event=>{
    event.preventDefault();const button=document.querySelector('#save-provider'),error=document.querySelector('#provider-error');
    button.disabled=true;button.textContent='正在保存到 NAS…';error.hidden=true;
    try{
      const data=Object.fromEntries(new FormData(event.target));data.vision=!!data.vision;data.make_default=!!data.make_default;if(p.id)data.id=p.id;
      await api('providers',{method:'POST',body:JSON.stringify(data)});
      event.target.elements.key.value='';toast('模型配置已保存到 NAS，可在创建页选择使用');location.hash='settings';
    }catch(exc){error.textContent=exc.message;error.hidden=false;error.scrollIntoView({block:'center',behavior:'smooth'});button.disabled=false;button.textContent='保存到 NAS'}
  };
}
