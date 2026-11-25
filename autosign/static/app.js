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
