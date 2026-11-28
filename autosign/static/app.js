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

const taskToolbar = document.querySelector('[data-task-toolbar]');
const taskGrid = document.querySelector('.task-grid');
if (taskToolbar && taskGrid) {
  const cards = [...taskGrid.querySelectorAll('[data-task-card]')];
  const filters = [...taskToolbar.querySelectorAll('[data-task-filter]')];
  const search = taskToolbar.querySelector('[data-task-search]');
  const count = taskToolbar.querySelector('[data-task-count]');
  const empty = document.querySelector('[data-task-empty]');
  let activeFilter = 'all';

  const updateTasks = () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    cards.forEach((card) => {
      const matchesFilter = activeFilter === 'all'
        || (activeFilter === 'attention' && card.dataset.taskAttention === 'true')
        || (activeFilter === 'enabled' && card.dataset.taskEnabled === 'true')
        || (activeFilter === 'paused' && card.dataset.taskEnabled === 'false');
      const matchesSearch = !query || card.dataset.taskSearch.includes(query);
      card.hidden = !(matchesFilter && matchesSearch);
      if (!card.hidden) visible += 1;
    });
    count.textContent = '显示 ' + visible + ' / ' + cards.length + ' 项';
    empty.hidden = visible > 0;
  };

  filters.forEach((button) => button.addEventListener('click', () => {
    activeFilter = button.dataset.taskFilter;
    filters.forEach((item) => {
      const selected = item === button;
      item.classList.toggle('is-active', selected);
      item.setAttribute('aria-pressed', String(selected));
    });
    updateTasks();
  }));
  search.addEventListener('input', updateTasks);
  taskToolbar.hidden = false;
  updateTasks();

  const activeRun = (status) => status === 'queued' || status === 'running';
  const pendingCards = cards.filter((card) => activeRun(card.dataset.taskRunStatus));
  if (pendingCards.length) {
    const initialRuns = new Map(cards.map((card) => [
      card.dataset.taskId,
      card.dataset.taskRunId + ':' + card.dataset.taskRunStatus,
    ]));
    let stopped = false;
    let failures = 0;
    const deadline = Date.now() + 120000;
    const poll = async () => {
      if (stopped || Date.now() >= deadline) return;
      try {
        const response = await fetch(taskToolbar.dataset.dashboardStatusUrl, {
          headers: {Accept: 'application/json'}, cache: 'no-store',
          signal: AbortSignal.timeout(10000),
        });
        if (response.redirected) { window.location.reload(); return; }
        if (!response.ok) throw new Error('status unavailable');
        const payload = await response.json();
        failures = 0;
        const changed = cards.some((card) => {
          const run = payload.runs[card.dataset.taskId];
          const current = run ? run.id + ':' + run.status : ':idle';
          return current !== initialRuns.get(card.dataset.taskId);
        });
        if (changed) { window.location.reload(); return; }
      } catch {
        failures += 1;
        if (failures >= 4) return;
      }
      if (!stopped && Date.now() < deadline) window.setTimeout(poll, 2500);
    };
    window.addEventListener('pagehide', () => { stopped = true; });
    window.setTimeout(poll, 2500);
  }
}

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

const autoSuccess = document.getElementById('auto_success');
const successText = document.getElementById('success_text');
if (autoSuccess && successText) {
  const updateSuccessRequirement = () => { successText.required = !autoSuccess.checked; };
  autoSuccess.addEventListener('change', updateSuccessRequirement);
  updateSuccessRequirement();
}

const connectionMethod = document.getElementById('connection_method');
if (connectionMethod) {
  const updateConnection = () => {
    const save = document.getElementById('site-save');
    if (save) save.textContent = connectionMethod.value === 'password' ? '登录账号，下一步 →' : '保存账号，下一步 →';
    const step = document.querySelector('.setup-progress li:nth-child(2) strong');
    if (step) step.textContent = connectionMethod.value === 'password' ? '登录账号' : '连接账号';
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

const assistantPairingOriginAllowed = () => {
  const url = new URL(window.location.href);
  return url.protocol === 'https:' || (url.protocol === 'http:'
    && ['localhost', '127.0.0.1'].includes(url.hostname));
};
const unsupportedAssistantOriginMessage = '当前公网 HTTP 地址不支持扩展连接。请点击“查看连接设置”，使用免自有域名 HTTPS 或已有反向代理配置安全入口。';
document.querySelectorAll('[data-connection-access-help]').forEach((panel) => {
  panel.hidden = assistantPairingOriginAllowed();
});
document.querySelectorAll('[data-connection-access-ready]').forEach((panel) => {
  panel.hidden = !assistantPairingOriginAllowed();
});

const discoveryPanel = document.getElementById('site-discovery');
if (discoveryPanel) {
  const startButton = document.getElementById('site-discovery-start');
  const copyButton = document.getElementById('site-discovery-copy');
  const resultPanel = document.getElementById('site-discovery-result');
  const linkField = document.getElementById('site-discovery-link');
  const status = document.getElementById('site-discovery-status');
  const statusUrl = (id) => discoveryPanel.dataset.statusTemplate.replace('PAIRING_ID', encodeURIComponent(id));
  let activePairing = '';
  let timer = 0;
  let deadline = 0;

  const setStatus = (message, error = false) => {
    status.replaceChildren(document.createTextNode(message));
    status.dataset.state = error ? 'error' : 'ok';
  };

  const poll = async (id) => {
    if (id !== activePairing) return;
    try {
      const response = await fetch(statusUrl(id), {
        headers: {Accept: 'application/json'}, cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      const payload = await response.json();
      if (!response.ok || payload.status === 'expired') {
        setStatus('识别链接已失效，请重新生成。', true);
        startButton.disabled = false;
        return;
      }
      if (payload.discovery_error) {
        setStatus('网页已连接，但自动创建任务失败：' + payload.discovery_error, true);
        startButton.disabled = false;
        return;
      }
      if (payload.task_id && payload.verify_url) {
        setStatus((payload.page_title || payload.host || '网站') + ' 已自动添加。');
        const link = document.createElement('a');
        const target = new URL(payload.verify_url, window.location.href);
        if (target.origin === window.location.origin) {
          link.href = target.href;
          link.textContent = '打开任务并验证';
          link.className = 'pairing-task-link';
          status.append(' ', link);
        }
        startButton.disabled = false;
        startButton.textContent = '识别另一个网站';
        return;
      }
      setStatus(payload.status === 'connected'
        ? '已读取网页，正在自动创建任务…'
        : '等待扩展识别。请在已登录的网站标签页中粘贴上方链接。');
    } catch {
      setStatus('暂时无法读取识别状态，请检查连接后重试。', true);
      startButton.disabled = false;
      return;
    }
    if (Date.now() < deadline) timer = window.setTimeout(() => poll(id), 1500);
    else {
      setStatus('等待超时，请重新生成识别链接。', true);
      startButton.disabled = false;
    }
  };

  startButton.addEventListener('click', async () => {
    window.clearTimeout(timer);
    activePairing = '';
    startButton.disabled = true;
    resultPanel.hidden = true;
    if (!assistantPairingOriginAllowed()) {
      setStatus(unsupportedAssistantOriginMessage, true);
      startButton.disabled = false;
      return;
    }
    setStatus('正在生成一次性识别链接…');
    try {
      const response = await fetch(discoveryPanel.dataset.createUrl, {
        method: 'POST',
        body: new URLSearchParams({csrf_token: discoveryPanel.dataset.csrf}),
        headers: {Accept: 'application/json'},
        credentials: 'same-origin', cache: 'no-store',
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '识别链接创建失败。');
      activePairing = payload.id;
      const pairingUrl = new URL('/', window.location.origin);
      pairingUrl.hash = 'signin-pairing=' + encodeURIComponent(payload.code);
      linkField.value = pairingUrl.href;
      resultPanel.hidden = false;
      deadline = Date.now() + (payload.expires_in || 300) * 1000;
      setStatus('链接已生成。打开已登录的网站扩展，粘贴链接并点击识别。');
      await poll(activePairing);
    } catch (error) {
      setStatus(error.message || '识别链接创建失败，请刷新页面后重试。', true);
      startButton.disabled = false;
    }
  });

  copyButton?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(linkField.value);
      setStatus('识别链接已复制。它只允许使用一次，请勿发给他人。');
    } catch {
      linkField.focus();
      linkField.select();
      setStatus('浏览器不允许自动复制，已选中链接，请按 Ctrl+C 或 ⌘C。');
    }
  });
}

const pairingPanel = document.getElementById('session-pairing');
if (pairingPanel) {
  const startButton = document.getElementById('session-pairing-start');
  const copyButton = document.getElementById('session-pairing-copy');
  const resultPanel = document.getElementById('session-pairing-result');
  const linkField = document.getElementById('session-pairing-link');
  const status = document.getElementById('session-pairing-status');
  const pairingField = document.getElementById('browser_pairing_id');
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
  let pollTimer = 0;
  let activePairing = '';

  const setStatus = (message, failed = false) => {
    status.textContent = message;
    status.dataset.state = failed ? 'error' : 'ok';
  };
  const apiUrl = (template, id) => template.replace('PAIRING_ID', encodeURIComponent(id));
  const applyHints = (hints) => {
    const fields = [
      {id: 'button_text', key: 'button_texts', list: 'button-text-suggestions', label: '签到按钮'},
      {id: 'success_text', key: 'success_texts', list: 'success-text-suggestions', label: '签到成功提示'},
      {id: 'already_done_text', key: 'already_done_texts', list: 'already-done-text-suggestions', label: '今日已签到提示'},
    ];
    const filled = [];
    for (const item of fields) {
      const input = document.getElementById(item.id);
      const list = document.getElementById(item.list);
      const values = Array.isArray(hints?.[item.key])
        ? [...new Set(hints[item.key].filter((value) => typeof value === 'string' && value.trim()))]
        : [];
      if (list) list.replaceChildren(...values.map((value) => {
        const option = document.createElement('option');
        option.value = value;
        return option;
      }));
      if (input && !input.value.trim() && values.length) {
        input.value = values[0];
        filled.push(`${item.label}“${values[0]}”`);
      }
    }
    return {filled};
  };

  const pollPairing = async (id) => {
    if (id !== activePairing) return;
    try {
      const response = await fetch(apiUrl(pairingPanel.dataset.statusTemplate, id), {
        headers: {Accept: 'application/json'}, cache: 'no-store',
        signal: AbortSignal.timeout(10000),
      });
      const payload = await response.json();
      if (!response.ok || payload.status === 'expired') throw new Error('连接码已过期，请重新生成。');
      if (payload.status === 'connected') {
        pairingField.value = id;
        const applied = applyHints(payload.hints);
        startButton.disabled = false;
        startButton.textContent = '重新生成连接链接';
        const buttonField = document.getElementById('button_text');
        const successField = document.getElementById('success_text');
        const notes = ['浏览器账号已连接。'];
        if (applied.filled.length) notes.push(`已自动填入${applied.filled.join('、')}。`);
        if (buttonField && !buttonField.value.trim()) notes.push('当前页面未识别到签到按钮，请确认打开的是签到页后手动填写。');
        if (successField && !successField.value.trim()) notes.push(autoSuccess?.checked
          ? '已启用常见结果自动识别，可先保存并验证；识别不准时再填写网站的准确提示。'
          : '当前页面没有显示真实的签到成功提示，请填写网站实际提示，或启用常见结果自动识别。');
        setStatus(notes.join(' '));
        return;
      }
      setStatus('等待扩展连接。请在已登录的网站标签页中粘贴上方链接。');
    } catch (error) {
      setStatus(error.message || '暂时无法读取连接状态，请刷新后重试。', true);
      startButton.disabled = false;
      return;
    }
    pollTimer = window.setTimeout(() => pollPairing(id), 1500);
  };

  startButton.addEventListener('click', async () => {
    window.clearTimeout(pollTimer);
    pairingField.value = '';
    activePairing = '';
    startButton.disabled = true;
    if (!assistantPairingOriginAllowed()) {
      setStatus(unsupportedAssistantOriginMessage, true);
      startButton.disabled = false;
      return;
    }
    setStatus('正在创建短时连接请求…');
    try {
      const data = new URLSearchParams({
        csrf_token: csrf,
        start_url: document.getElementById('start_url')?.value || '',
        allowed_hosts: document.getElementById('allowed_hosts')?.value || '',
      });
      const response = await fetch(pairingPanel.dataset.createUrl, {
        method: 'POST', body: data, headers: {Accept: 'application/json'},
        credentials: 'same-origin', cache: 'no-store',
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '连接请求创建失败。');
      activePairing = payload.id;
      const pairingUrl = new URL('/', window.location.origin);
      pairingUrl.hash = 'signin-pairing=' + encodeURIComponent(payload.code);
      linkField.value = pairingUrl.href;
      resultPanel.hidden = false;
      startButton.textContent = '重新生成连接链接';
      setStatus('连接链接有效 5 分钟。请只粘贴到你安装的签到助手扩展中。');
      await pollPairing(activePairing);
    } catch (error) {
      setStatus(error.message || '连接请求创建失败，请检查起始页面地址。', true);
    } finally {
      startButton.disabled = false;
    }
  });

  copyButton?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(linkField.value);
      setStatus('连接链接已复制。此链接可导入一次登录会话，请勿发给他人。');
    } catch {
      linkField.focus();
      linkField.select();
      setStatus('浏览器不允许自动复制，已选中链接，请按 Ctrl+C 或 ⌘C。');
    }
  });
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
