document.querySelectorAll('form[data-confirm]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
document.querySelectorAll('form[data-busy]').forEach((form) => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button[type="submit"]');
    if (button) { button.disabled = true; button.textContent = form.dataset.busy; }
  });
});
const successType = document.getElementById('success_type');
const pathField = document.getElementById('success-path-field');
const successValue = document.getElementById('success_value');
if (successType && pathField) {
  const update = () => {
    const json = successType.value === 'json';
    pathField.hidden = !json;
    if (successValue) successValue.placeholder = json ? '例如：true、0 或 "成功"' : '例如：签到成功';
  };
  successType.addEventListener('change', update);
  update();
}

const connectionMethod = document.getElementById('connection_method');
if (connectionMethod) {
  const updateConnection = () => {
    document.querySelectorAll('[data-connection-panel]').forEach((panel) => {
      const active = panel.dataset.connectionPanel === connectionMethod.value;
      panel.hidden = !active;
      panel.querySelectorAll('input, textarea, select').forEach((input) => { input.disabled = !active; });
    });
  };
  connectionMethod.addEventListener('change', updateConnection);
  updateConnection();
}

const tokenSiteNote = document.getElementById('token-site-note');
const startUrl = document.getElementById('start_url');
const tokenStorageKey = document.getElementById('token_storage_key');
if (tokenSiteNote && startUrl) {
  const updateTokenSite = () => {
    let xingchi = false;
    try { xingchi = new URL(startUrl.value).origin === 'https://api.xingchiapi.com'; } catch {}
    const key = tokenStorageKey ? tokenStorageKey.value.trim() : '';
    tokenSiteNote.textContent = xingchi && (!key || key === 'auth_token')
      ? '星驰 API：存储字段已自动设为 auth_token。执行时会读取账号资料以恢复网页登录，无需另外复制账号资料。'
      : '默认写入当前网站的 localStorage.access_token；字段名不同时，请在高级设置填写网站实际使用的键名。部分网站仍需专用适配或完整会话。';
  };
  startUrl.addEventListener('input', updateTokenSite);
  if (tokenStorageKey) tokenStorageKey.addEventListener('input', updateTokenSite);
  updateTokenSite();
}

const verificationPanel = document.querySelector('[data-verification-url]');
if (verificationPanel) {
  let failures = 0;
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    try {
      const response = await fetch(verificationPanel.dataset.verificationUrl, {
        headers: {Accept: 'application/json'}, cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      if (response.redirected) { window.location.reload(); return; }
      if (!response.ok) throw new Error('status unavailable');
      const result = await response.json();
      failures = 0;
      if (String(result.revision) !== verificationPanel.dataset.revision ||
          String(result.run_id) !== verificationPanel.dataset.runId ||
          result.status !== verificationPanel.dataset.runStatus) {
        window.location.reload();
        return;
      }
    } catch {
      failures += 1;
      const note = verificationPanel.querySelector('.poll-note');
      if (note) note.textContent = '暂时无法更新结果，可点击“刷新结果”重试。';
      if (failures >= 3) return;
    }
    if (!stopped) window.setTimeout(poll, 2500);
  };
  window.addEventListener('pagehide', () => { stopped = true; });
  window.setTimeout(poll, 2500);
}
