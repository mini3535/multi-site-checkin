(() => {
  const notice = document.querySelector('[data-update-notice]');
  if (!notice) return;
  const panel = document.querySelector('[data-update-panel]');
  const message = panel?.querySelector('[data-update-message]');
  const apply = panel?.querySelector('[data-update-apply]');
  const check = panel?.querySelector('[data-update-check]');
  let state = {};
  let polling = false;
  let sending = false;
  const short = value => (value || '').slice(0, 12);
  const show = result => {
    state = result;
    notice.hidden = !result.available && !result.busy && result.phase !== 'needs_recovery';
    notice.querySelector('[data-update-notice-text]').textContent = result.busy ? '正在更新服务，请稍候。'
      : result.phase === 'needs_recovery' ? '更新需要人工恢复，请查看系统更新。' : '发现新版本 ' + short(result.latest_revision);
    if (!panel) return;
    message.textContent = result.error || result.message || '更新服务暂时不可用，请稍后重试。';
    panel.querySelector('[data-update-latest]').textContent = result.latest_revision ? '最新版本 ' + short(result.latest_revision) : '';
    panel.querySelector('[data-update-checked]').textContent = result.checked_at ? '上次检查：' + new Date(result.checked_at * 1000).toLocaleString() : '';
    apply.disabled = sending || !result.can_update;
    apply.textContent = result.busy ? '正在更新…' : '立即更新';
    check.disabled = sending || result.busy;
    const backup = panel.querySelector('[data-update-backup]');
    backup.hidden = !result.backup;
    backup.textContent = '本次备份（在服务器数据卷内）：' + (result.backup || '');
    panel.querySelector('[data-update-reload]').hidden = result.phase !== 'success' || short(result.current_revision) === panel.dataset.current;
    if (!result.supported && !result.busy) document.querySelector('[data-update-manual]').open = true;
    const order = ['downloading', 'preflight', 'stopping', 'backing_up', 'starting', 'success'];
    panel.querySelectorAll('[data-phase]').forEach(item => {
      item.classList.toggle('is-active', item.dataset.phase === result.phase || (item.dataset.phase === 'backing_up' && result.phase === 'stopping'));
      item.classList.toggle('is-done', order.indexOf(result.phase) > order.indexOf(item.dataset.phase));
    });
  };
  const request = async (action, values = {}) => {
    const options = action === 'status' ? {} : {method: 'POST', body: new URLSearchParams({csrf_token: panel.dataset.csrf, ...values})};
    const response = await fetch('/system/update/' + action, {...options, cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(60000)});
    if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('unavailable');
    const result = await response.json();
    if (!response.ok && !result.error) throw new Error('unavailable');
    return result;
  };
  const poll = async () => {
    if (polling || sending) return;
    polling = true;
    try { show(await request('status')); }
    catch { if (message && state.busy) message.textContent = '服务正在重启，等待网页恢复…'; }
    finally { polling = false; }
  };
  const submit = async action => {
    if (sending) return;
    if (action === 'apply' && !window.confirm('现在更新？系统会备份数据并短暂重启，启动失败时自动恢复旧版。')) return;
    sending = true;
    apply.disabled = true; check.disabled = true;
    message.textContent = action === 'check' ? '正在连接官方镜像仓库…' : '正在核对版本并开始更新…';
    try { show(await request(action, {revision: state.latest_revision || ''})); }
    catch { message.textContent = '暂时无法连接更新服务，请稍后刷新页面查看状态。'; }
    finally { sending = false; apply.disabled = !state.can_update; check.disabled = !!state.busy; }
  };
  check?.addEventListener('click', () => submit('check'));
  apply?.addEventListener('click', () => submit('apply'));
  panel?.querySelector('[data-update-reload]').addEventListener('click', () => window.location.reload());
  poll();
  setInterval(poll, panel ? 4000 : 60000);
})();
