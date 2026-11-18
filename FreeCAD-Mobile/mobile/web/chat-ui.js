// Shared presentation for connection failures and persisted task progress.
export const icon = name => `<img class="design-icon" src="/static/icons/${name}.svg" alt="" aria-hidden="true">`;

export function connectionCard(target, message, retry) {
  target.innerHTML = `<section class="connection-card" aria-label="服务端连接中断">
    <h2>${icon('connection-warning')}无法连接服务端</h2>
    <p role="status"></p>
    <p class="field-note">请检查网络和 NAS 上的移动端容器。通过域名访问时，也请检查 Caddy 反代。</p>
    <button type="button" class="primary connection-retry">${icon('retry')}重试连接</button>
  </section>`;
  const status = target.querySelector('[role=status]');
  const button = target.querySelector('button');
  status.textContent = message;
  button.onclick = async () => {
    button.disabled = true;
    button.innerHTML = icon('retry-muted') + '正在重试…';
    status.textContent = '正在检查连接…';
    try {
      await retry();
    } finally {
      button.disabled = false;
      button.innerHTML = icon('retry') + '重试连接';
      status.textContent = message;
    }
  };
}

const eventLabels = {
  pending: '任务已提交', demo: '固定零件测试已提交',
  generating: '分析需求与尺寸', repairing: '修复建模脚本',
  needs_input: '等待补充尺寸', dimensions_review: '生成尺寸确认清单',
  dimensions_confirmed: '用户确认尺寸', submitting: '提交给 FreeCAD',
  cad_queued: '进入 FreeCAD 队列', cad_unavailable: '等待 FreeCAD 执行端',
  building: '建模与检查', archiving: '保存模型文件',
  review: '模型已就绪', confirmed: '用户确认模型',
  failed: '生成未完成', cancelled: '任务已取消',
};

function eventLabel(event) {
  if (event.status === 'cancelling') return '已请求取消';
  if (event.status === 'cancelled') return '任务已取消';
  if (event.status === 'interrupted') return '任务已中断';
  if (event.status === 'pending' && !['pending', 'demo', 'dimensions_confirmed'].includes(event.stage)) {
    return '等待恢复执行';
  }
  return eventLabels[event.stage] || '任务状态已更新';
}

export function taskRecord(job, {esc, active}, expanded = false) {
  const events = job.events || [];
  const last = events.at(-1);
  const hasCompleteHistory = events[0]?.at === job.created;
  const timeFormat = new Intl.DateTimeFormat('zh-CN', {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false});
  const duration = last && hasCompleteHistory && !active(job)
    ? ' · ' + Math.max(0, Math.round(last.at - job.created)) + ' 秒' : '';
  const summary = events.length ? `${events.length} 条记录${duration}` : '仅保留最新状态';
  return `<details class="task-record" data-record="${esc(job.id)}"${expanded ? ' open' : ''}>
    <summary><span><strong>任务记录</strong><small>${summary}</small></span><span class="record-toggle">${icon('expand')}${icon('collapse')}</span></summary>
    <div class="record-body">
      ${!hasCompleteHistory ? '<p class="field-note">历史任务的早期步骤未记录。</p>' : ''}
      ${events.length ? `<ol>${events.map((event, i) => {
        const current = active(job) && i === events.length - 1;
        const outcome = ['failed','interrupted','cancelled','cancelling'].includes(event.status) || event.stage === 'cad_unavailable';
        const date = new Date(event.at * 1000);
        return `<li class="${current ? 'current' : ''}">${current || outcome ? '<span class="record-dot"></span>' : icon('step-complete')}
          <span>${eventLabel(event)}</span><time datetime="${date.toISOString()}">${timeFormat.format(date)}</time></li>`;
      }).join('')}</ol>` : `<p>${esc(eventLabel(job))}</p>`}
    </div>
  </details>`;
}
