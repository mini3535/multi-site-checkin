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
