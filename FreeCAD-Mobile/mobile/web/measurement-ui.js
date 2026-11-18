export function measurementCard(job,esc,canConfirm=false){
  const review=job.plan?.measurement_review;
  const parameters=review?.parameters||job.plan?.measurement?.parameters||[];
  const sources={user:'用户提供',drawing:'图纸标注',estimate:'建议 / 估算',unknown:'待测量'};
  const waiting=job.status==='needs_confirmation';
  return `<section class="measurement-card" aria-label="尺寸确认卡片"><h3>${waiting?'请确认本次建模尺寸':'还需要这些尺寸或结构信息'}</h3>
    ${parameters.length?`<div class="measurement-table"><table><thead><tr><th>参数</th><th>值</th><th>来源</th></tr></thead><tbody>${parameters.map(p=>`<tr class="${p.source==='estimate'?'estimated':''}"><td>${esc(p.name)}${p.required?'':'（可选）'}</td><td>${esc(p.value||'待补充')}</td><td>${esc(sources[p.source]||'待确认')}</td></tr>`).join('')}</tbody></table></div>`:''}
    ${!waiting?`<ol>${(job.plan?.questions||review?.questions||[]).map(q=>`<li>${esc(q)}</li>`).join('')}</ol>${parameters.filter(p=>p.required&&(!p.value||p.source==='unknown')).map(p=>`<div class="measurement-tip"><strong>${esc(p.name)}</strong><p>${esc(p.measurement_hint||'请用卡尺或直尺测量对应位置，并注明单位。')}</p></div>`).join('')}`:''}
    ${(job.plan?.assumptions||[]).length?`<details ${waiting?'open':''}><summary>建模假设与复核建议</summary><ul>${job.plan.assumptions.map(s=>`<li>${esc(s)}</li>`).join('')}</ul></details>`:''}
    ${waiting?`<p class="field-note">确认后按此清单建模。建议 / 估算值未经实测；如需修改，请在下方说明。</p><button class="primary" data-confirm-dimensions="${job.id}" ${canConfirm?'':'disabled'}>${canConfirm?'确认尺寸，开始建模':'已有后续版本'}</button>`:'<p class="field-note">请在下方回复测量结果或补充清晰图片，可分几次提供。</p>'}
    </section>`;
}
