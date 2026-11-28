const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const code = 'pairing01.abcdefghijklmnopqrstuvwxyz';
const source = readFileSync(join(__dirname, 'popup.js'), 'utf8');

async function popup(options = {}) {
  const elements = new Map();
  const requests = [];
  const permissions = [];
  const website = options.website || 'https://forum.example.com/daily';
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: true, hidden: true, dataset: {},
      addEventListener() {},
    });
    return elements.get(id);
  };
  const context = vm.createContext({
    URL, URLSearchParams, TextEncoder, AbortSignal,
    document: {querySelector: element},
    chrome: {
      tabs: {query: async () => [{id: 7, url: website}]},
      permissions: {request: async (value) => {
        permissions.push(value);
        return options.grant ? options.grant() : true;
      }},
      scripting: {executeScript: async ({func}) => [{result: func.toString().includes('resourceHosts')
        ? {pageTitle: '论坛', buttonTexts: ['每日签到'], successTexts: [], alreadyDoneTexts: [], resourceHosts: []}
        : {origin: new URL(website).origin, localStorage: [{name: 'token', value: 'fixture-token'}], sessionStorageCount: 0}}]},
      cookies: {getAll: async () => [{name: 'session', value: 'fixture-cookie', domain: 'forum.example.com', path: '/', secure: true, httpOnly: true}]},
    },
    fetch: async (url, request) => {
      requests.push({url, request});
      return {ok: true, json: async () => options.payload || {status: 'connected'}};
    },
  });
  vm.runInContext(source, context);
  await context.loadPage();
  const setLink = (origin) => {
    element('#pairing-link').value = origin + '/#signin-pairing=' + code;
    context.updatePairingLink();
  };
  return {context, element, requests, permissions, setLink};
}

test('HTTPS and exact HTTP loopback assistant addresses accept their configured port', async () => {
  const ui = await popup();
  for (const origin of ['https://assistant.example.com', 'https://assistant.example.com:8443',
    'http://localhost:8080', 'http://127.0.0.1:8080']) {
    ui.setLink(origin);
    assert.equal(ui.element('#connect').disabled, false, origin);
  }
});

test('public HTTP and lookalike loopback names remain blocked', async () => {
  const ui = await popup();
  for (const origin of ['http://assistant.example.com', 'http://192.168.1.5:8080',
    'http://127.0.0.2:8080', 'http://localhost.evil.example',
    'http://127.0.0.1.evil.example', 'http://user:pass@localhost:8080']) {
    ui.setLink(origin);
    assert.equal(ui.element('#connect').disabled, true, origin);
  }
});

test('invalid links cannot retain a previously valid destination', async () => {
  const ui = await popup();
  for (const link of ['', 'not a link', 'https://assistant.example.com/#signin-pairing=bad',
    'https://assistant.example.com/path/#signin-pairing=' + code,
    'https://assistant.example.com/?query=1#signin-pairing=' + code]) {
    ui.setLink('https://assistant.example.com');
    ui.element('#pairing-link').value = link;
    ui.context.updatePairingLink();
    await ui.context.connectSession();
    assert.equal(ui.element('#connect').disabled, true);
    assert.equal(ui.requests.length, 0);
  }
});

test('loopback connection sends the website session only to the chosen assistant', async () => {
  const ui = await popup();
  ui.setLink('http://127.0.0.1:8080');
  await ui.context.connectSession();
  assert.equal(ui.requests.length, 1);
  const {url, request} = ui.requests[0];
  assert.equal(url, 'http://127.0.0.1:8080/api/browser-session-pairings/complete');
  assert.equal(request.credentials, 'omit');
  assert.equal(request.redirect, 'error');
  assert.deepEqual(Array.from(ui.permissions[0].origins), ['https://forum.example.com/*', 'http://127.0.0.1/*']);
  const body = JSON.parse(request.body);
  assert.equal(body.code, code);
  assert.equal(body.page_url, 'https://forum.example.com/daily');
  assert.equal(body.storage_state.cookies[0].value, 'fixture-cookie');
  assert.equal(ui.element('#connect').textContent, '已连接');
  assert.equal(ui.element('#connect').disabled, true);
});

test('the assistant tab itself cannot be mistaken for the target website', async () => {
  const ui = await popup({website: 'https://assistant.example.com/sites'});
  ui.setLink('https://assistant.example.com');
  await ui.context.connectSession();
  assert.equal(ui.requests.length, 0);
  assert.match(ui.element('#status').textContent, /切回已登录的目标网站/);
});

test('task creation errors are shown instead of reporting a successful addition', async () => {
  const ui = await popup({payload: {task_created: false, error: '自动创建任务失败'}});
  ui.setLink('https://assistant.example.com');
  await ui.context.connectSession();
  assert.equal(ui.element('#status').dataset.state, 'error');
  assert.equal(ui.element('#status').textContent, '自动创建任务失败');
});

test('a pending permission prompt locks the link and prevents duplicate uploads', async () => {
  let release;
  const permission = new Promise((resolve) => { release = resolve; });
  const ui = await popup({grant: () => permission});
  ui.setLink('https://assistant.example.com');
  const pending = ui.context.connectSession();
  assert.equal(ui.permissions.length, 1, 'permissions must be requested directly in the click handler');
  await Promise.resolve();
  assert.equal(ui.element('#pairing-link').disabled, true);
  await ui.context.connectSession();
  release(true);
  await pending;
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.element('#pairing-link').disabled, false);
});
