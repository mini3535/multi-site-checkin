(() => {
  const panel = document.querySelector('[data-account-login]');
  if (!panel) return;
  const base = panel.dataset.accountLogin;
  const screen = document.getElementById('account-screen');
  const status = panel.querySelector('[role="status"]');
  const csrf = panel.querySelector('[name="csrf_token"]').value;
  const input = document.getElementById('login-input');
  const save = document.getElementById('account-save');
  const refresh = document.getElementById('account-refresh');
  const zoom = document.getElementById('account-zoom');
  const controls = document.getElementById('account-controls');
  let revision = null, ready = false, busy = false, stopped = false, failures = 0, timer;
  const update = () => {
    panel.setAttribute('aria-busy', String(busy));
    controls.hidden = ready;
    controls.querySelectorAll('button, input').forEach(el => { el.disabled = busy || ready || revision === null; });
    refresh.disabled = busy;
    save.disabled = busy || !ready;
  };
  const request = async (action, fields = {}) => {
    if (busy || stopped) return;
    window.clearTimeout(timer);
    busy = true; update();
    try {
      const options = {cache: 'no-store', headers: {Accept: 'application/json'}, signal: AbortSignal.timeout(35000)};
      let url = base + '/frame';
      if (action) {
        url = base + '/action'; options.method = 'POST';
        options.body = new URLSearchParams({csrf_token: csrf, action, revision, ...fields});
      }
      const response = await fetch(url, options);
      if (response.redirected) { stopped = true; window.location.reload(); return; }
      const result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || '无法读取登录画面，请重新连接。');
      screen.src = 'data:image/jpeg;base64,' + result.image;
      await screen.decode();
      screen.hidden = false;
      revision = result.revision;
      ready = result.ready === true;
      status.textContent = ready ? '登录已完成。请保存账号，进入签到验证。' : '请完成网站登录及验证。';
      failures = 0;
    } catch (error) {
      status.textContent = error.message || '暂时无法连接，请刷新画面或重新连接账号。';
      failures += 1;
    } finally {
      busy = false; update();
      if (!stopped && !ready && failures < 3) timer = window.setTimeout(() => request(), 2000);
    }
  };
  screen.addEventListener('click', event => {
    if (busy || ready || revision === null) return;
    const rect = screen.getBoundingClientRect();
    request('click', {x: (event.clientX - rect.left) / rect.width * 1024, y: (event.clientY - rect.top) / rect.height * 720});
  });
  document.getElementById('account-input').addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !input.value) return;
    const text = input.value; input.value = '';
    request('type', {text});
  });
  controls.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click', () => request('key', {key: button.dataset.key})));
  controls.querySelectorAll('[data-scroll]').forEach(button => button.addEventListener('click', () => request('scroll', {direction: button.dataset.scroll})));
  refresh.addEventListener('click', () => { failures = 0; request(); });
  zoom.addEventListener('click', () => {
    const enlarged = screen.parentElement.classList.toggle('is-zoomed');
    zoom.setAttribute('aria-pressed', String(enlarged));
    zoom.textContent = enlarged ? '画面适应宽度' : '放大登录画面';
  });
  window.addEventListener('pagehide', () => { stopped = true; window.clearTimeout(timer); input.value = ''; });
  request();
})();
