"""Login-only onboarding tested with fixture credentials and an intercepted site."""
import json
import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from cryptography.fernet import Fernet

from autosign.account_login import (LoginError, LoginManager, LoginWindow,
                                    authenticated_state, credentials, login_request_allowed)
from autosign.app import create_app
from autosign.browser_runner import _run_browser_in_process, validate_browser_spec
from autosign.xingchi import HOST, ORIGIN, task_spec

PASSWORD = "fixture-website-password"
TOKEN = "fixture-account-token"


def state():
    return {"cookies": [], "origins": [{"origin": ORIGIN, "localStorage": [
        {"name": "auth_token", "value": TOKEN},
        {"name": "auth_user", "value": json.dumps({"id": 7})},
        {"name": "refresh_token", "value": "fixture-refresh-token"},
        {"name": "irrelevant-preference", "value": "discard-me"},
    ]}]}


class PolicyTests(unittest.TestCase):
    def test_auth_writes_are_fixed_origin_https_and_never_claim_rewards(self):
        for method, path in (("GET", "/login"), ("GET", "/assets/login.js"),
                             ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/login/2fa")):
            self.assertTrue(login_request_allowed(method, ORIGIN + path))
        self.assertTrue(login_request_allowed("POST", "https://challenges.cloudflare.com/challenge"))
        for method, url in (("POST", ORIGIN + "/api/v1/checkin"),
                            ("POST", ORIGIN + "/api/v1/keys"),
                            ("GET", "https://evil.example/"), ("GET", "http://" + HOST + "/login"),
                            ("GET", "https://" + HOST + ":8443/login"),
                            ("GET", "https://user:pass@" + HOST + "/login"),
                            ("GET", "http://127.0.0.1/")):
            self.assertFalse(login_request_allowed(method, url))

    def test_credentials_and_authenticated_state_do_not_accept_incomplete_login(self):
        self.assertEqual(credentials("fixture@example.com", PASSWORD), ("fixture@example.com", PASSWORD))
        for email, password in (("bad", PASSWORD), ("fixture@example.com", ""),
                                ("fixture@example.com", PASSWORD + "\n")):
            with self.assertRaises(LoginError) as error:
                credentials(email, password)
            self.assertNotIn(PASSWORD, str(error.exception))
        clean = authenticated_state(state(), ORIGIN + "/dashboard")
        self.assertNotIn("discard-me", json.dumps(clean))
        for url in (ORIGIN + "/login", "https://evil.example/dashboard", ORIGIN + ":8443/dashboard"):
            with self.assertRaises(LoginError):
                authenticated_state(state(), url)
        invalid = state()
        invalid["origins"][0]["localStorage"][1]["value"] = '{"id":true}'
        with self.assertRaises(LoginError):
            authenticated_state(invalid, ORIGIN + "/dashboard")

    def test_xingchi_rules_cannot_be_replaced_or_redirected_by_posted_configuration(self):
        spec = validate_browser_spec(task_spec(authenticated_state(state(), ORIGIN + "/dashboard")))
        for key, value in (("start_url", "https://evil.example/"), ("steps", []),
                           ("success_text", "always succeeds"), ("allowed_hosts", [HOST, "evil.example"])):
            modified = deepcopy(spec)
            modified[key] = value
            with self.assertRaises(ValueError):
                validate_browser_spec(modified)


class FakeSession:
    def __init__(self, owner, name, task_id, revision):
        self.owner, self.name, self.task_id, self.revision = owner, name, task_id, revision
        self.expires = time.monotonic() + 600
        self.lock = threading.RLock()
        self.closed, self.ready = False, False
        self.calls = []

    def send(self, command):
        self.calls.append(command["action"])
        if command["action"] == "finish":
            if not self.ready:
                raise LoginError("请完成登录。")
            return {"state": authenticated_state(state(), ORIGIN + "/dashboard")}
        return {"image": "fixture-jpeg", "width": 1024, "height": 720, "revision": 1, "ready": self.ready}

    def close(self):
        self.closed = True


class LoginAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"DATA_DIR": self.temp.name, "ADMIN_PASSWORD": "fixture-admin-password",
                               "ENCRYPTION_KEY": Fernet.generate_key().decode(), "TESTING": True})
        self.store = self.app.extensions["store"]
        self.manager = self.app.extensions["account_login"]
        self.client = self.app.test_client()
        self.client.get("/login")
        self.post("/login", password="fixture-admin-password")
        self.client.get("/")
        self.stub = patch("autosign.account_login._Session", FakeSession)
        self.stub.start()

    def tearDown(self):
        self.manager.close()
        self.stub.stop()
        self.temp.cleanup()

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session["csrf"]

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf_token": self.csrf(), **fields})

    def start(self, path="/sites/xingchi/new"):
        response = self.post(path, name="星驰 · 测试", email="fixture@example.com", website_password=PASSWORD)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        key = response.location.rsplit("/", 1)[-1]
        return key, self.manager.sessions[key]

    def test_password_form_needs_no_rules_tokens_or_recording(self):
        catalog = self.client.get("/sites").get_data(as_text=True)
        self.assertNotIn("/sites/xingchi/new", catalog)
        page = self.client.get("/sites/xingchi/new").get_data(as_text=True)
        self.assertIn('name="email"', page)
        self.assertIn('name="website_password"', page)
        for label in ("选择网站", "登录账号", "验证签到", "每天自动执行"):
            self.assertIn(label, page)
        for name in ("browser_token", "button_text", "steps", "success_text"):
            self.assertNotIn('name="' + name + '"', page)
        self.assertNotIn("录制", page)
        self.assertEqual(self.manager.sessions, {})
        self.assertEqual(self.store.list_tasks(), [])

    def test_invalid_input_never_echoes_credentials_or_creates_task(self):
        response = self.post("/sites/xingchi/new", name="test", email="bad", website_password=PASSWORD)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.manager.sessions, {})

    def test_login_endpoints_enforce_csrf_owner_and_private_save(self):
        key, item = self.start()
        page = self.client.get(f"/account-logins/{key}").get_data(as_text=True)
        self.assertNotIn(PASSWORD, page)
        self.assertNotIn("fixture@example.com", page)
        outsider = self.app.test_client()
        outsider.get("/login")
        outsider.post("/login", data={"csrf_token": self.csrf(outsider), "password": "fixture-admin-password"})
        self.assertEqual(outsider.get(f"/account-logins/{key}").status_code, 404)
        self.assertEqual(outsider.get(f"/account-logins/{key}/frame").status_code, 400)
        for action in ("finish", "credentials", "begin", "record"):
            self.assertEqual(self.post(f"/account-logins/{key}/action", action=action).status_code, 400)
        self.assertEqual(self.client.post(f"/account-logins/{key}/save").status_code, 400)
        frame = self.client.get(f"/account-logins/{key}/frame")
        self.assertNotIn(TOKEN, frame.get_data(as_text=True))
        self.assertNotIn("state", frame.json)
        self.assertEqual(self.store.list_tasks(), [])
        self.post(f"/account-logins/{key}/save")
        self.assertEqual(self.store.list_tasks(), [])

    def test_save_encrypts_only_session_pauses_and_requires_real_verification(self):
        key, item = self.start()
        item.ready = True
        response = self.post(f"/account-logins/{key}/save")
        self.assertEqual(response.status_code, 302)
        task = self.store.list_tasks()[0]
        self.assertTrue(response.location.endswith(f"/tasks/{task['id']}/verify"))
        self.assertFalse(task["enabled"])
        self.assertFalse(self.store.verification(task["id"])["ready"])
        self.assertTrue(item.closed)
        self.assertEqual(self.manager.sessions, {})
        spec = self.store.spec(task)
        self.assertEqual(spec["site_id"], "xingchi")
        self.assertNotIn(PASSWORD, json.dumps(spec))
        self.assertNotIn(TOKEN.encode(), self.store.path.read_bytes())
        self.assertNotIn(PASSWORD.encode(), self.store.path.read_bytes())
        self.post(f"/account-logins/{key}/save")
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertTrue(self.client.get(f"/tasks/{task['id']}/schedule").location.endswith("/verify"))
        self.assertTrue(self.store.enqueue_manual(task["id"]))
        run, snapshot = self.store.claim_next()
        self.store.finish(run["id"], {"ok": True, "status": "already_done", "message": "fixture", "duration_ms": 1}, snapshot)
        self.post(f"/tasks/{task['id']}/schedule", daily_time="09:15", timezone="Asia/Shanghai")
        self.assertTrue(self.store.get_task(task["id"])["enabled"])
        response = self.post(f"/tasks/{task['id']}/site-edit", name="改名", keep_session="1")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.store.get_task(task["id"])["enabled"])

    def test_changed_task_is_not_overwritten_by_open_login_window(self):
        task_id = self.store.save_task({"name": "fixture", "enabled": False, "daily_time": "08:00", "timezone": "UTC"},
                                       task_spec(authenticated_state(state(), ORIGIN + "/dashboard")))
        key, item = self.start(f"/tasks/{task_id}/site-edit")
        item.ready = True
        new_state = state()
        new_state["origins"][0]["localStorage"][0]["value"] = "new-fixture-token"
        self.store.save_task({"name": "changed", "enabled": False, "daily_time": "08:00", "timezone": "UTC"},
                             task_spec(new_state), task_id)
        self.post(f"/account-logins/{key}/save")
        self.assertEqual(self.store.get_task(task_id)["name"], "changed")
        self.assertNotIn("finish", item.calls)

    def test_existing_custom_xingchi_task_can_reconnect_without_creating_duplicate(self):
        spec = task_spec(authenticated_state(state(), ORIGIN + "/dashboard"))
        spec['site_id'] = 'custom'
        task_id = self.store.save_task({"name": "原账号", "enabled": False, "daily_time": "08:00", "timezone": "UTC"}, spec)
        page = self.client.get(f'/tasks/{task_id}/site-edit').get_data(as_text=True)
        self.assertIn('value="password"', page)
        key, item = self.start(f'/tasks/{task_id}/account-login')
        item.ready = True
        self.post(f'/account-logins/{key}/save')
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(self.store.spec(self.store.get_task(task_id))['site_id'], 'xingchi')
        self.assertFalse(self.store.get_task(task_id)['enabled'])

    def test_expiry_cancel_logout_and_password_change_release_windows(self):
        key, item = self.start()
        self.post(f"/account-logins/{key}/cancel")
        self.assertTrue(item.closed)
        key, item = self.start()
        item.expires = time.monotonic() - 1
        self.assertEqual(self.client.get(f"/account-logins/{key}").status_code, 404)
        self.assertTrue(item.closed)
        key, item = self.start()
        self.post("/settings/password", current_password="fixture-admin-password",
                  new_password="new-fixture-admin-password", confirm_password="new-fixture-admin-password")
        self.assertTrue(item.closed)

    def test_single_window_limit_is_safe_when_session_has_no_owner(self):
        key, item = self.start()
        with self.assertRaises(LoginError):
            self.manager.start("other-owner", "fixture", "fixture@example.com", PASSWORD)
        self.manager.cancel_owner(None)
        self.assertFalse(item.closed)
        self.post("/logout")
        self.assertTrue(item.closed)


LOGIN_HTML = r'''<!doctype html><meta charset="utf-8"><label>邮箱<input type="email"></label>
<label>密码<input type="password"></label><button id="login" onclick="login()">登录</button>
<div id="second" hidden>输入二次验证码<input id="otp"><button onclick="finish()">验证</button></div>
<script>
async function login(){await fetch('/api/v1/auth/login',{method:'POST',body:'fixture'});document.getElementById('second').hidden=false;}
async function finish(){if(document.getElementById('otp').value!=='123456')return;await fetch('/api/v1/auth/login/2fa',{method:'POST',body:'fixture'});localStorage.setItem('auth_token','fixture-account-token');localStorage.setItem('auth_user','{"id":7}');localStorage.setItem('refresh_token','fixture-refresh-token');location.href='/dashboard';}
</script>'''
DASHBOARD_HTML = r'''<!doctype html><meta charset="utf-8"><h1>仪表盘</h1><div id="checkin"></div>
<script>setTimeout(()=>{document.getElementById('checkin').innerHTML=localStorage.getItem('signed')==='yes'?'<button disabled>今日已签到</button>':'<button onclick="done()">签到领 300</button>';},220);function done(){fetch('/api/v1/checkin',{method:'POST'}).then(()=>{localStorage.setItem('signed','yes');document.getElementById('checkin').innerHTML='<button disabled>今日已签到</button>';});}</script>'''


class LoginBrowserTests(unittest.TestCase):
    def test_real_browser_login_2fa_private_export_and_daily_execution(self):
        from playwright.sync_api import BrowserContext
        original_route = BrowserContext.route
        writes = []

        def install_routes(context, pattern, handler, **kwargs):
            def fixture(route):
                url = route.request.url
                if url.startswith(ORIGIN):
                    if not login_request_allowed(route.request.method, url) and route.request.method != "GET":
                        # The daily runner may claim; login-only context may not.
                        if getattr(context, '_login_test', False):
                            return handler(route)
                    path = url.split(ORIGIN, 1)[1]
                    if route.request.method == 'POST':
                        writes.append(path)
                        return route.fulfill(status=200, content_type='application/json', body='{}')
                    return route.fulfill(status=200, content_type='text/html',
                                         body=LOGIN_HTML if path == '/login' else DASHBOARD_HTML)
                return route.abort()
            return original_route(context, pattern, fixture, **kwargs)

        env = {}
        if not os.environ.get("AUTOSIGN_BROWSER_EXECUTABLE") and os.path.exists('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'):
            env['AUTOSIGN_BROWSER_EXECUTABLE'] = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        with patch.dict(os.environ, env), patch('autosign.account_login._network_rules', return_value='MAP * ^NOTFOUND'), \
             patch('autosign.browser_runner._network_rules', return_value='MAP * ^NOTFOUND'), \
             patch.object(BrowserContext, 'route', install_routes):
            window = LoginWindow()
            try:
                window.context._login_test = True
                result = window.command({'action': 'credentials', 'email': 'fixture@example.com', 'password': PASSWORD})
                self.assertFalse(result['ready'])
                self.assertIn('/api/v1/auth/login', writes)
                with self.assertRaises(LoginError):
                    window.command({'action': 'finish'})
                with self.assertRaises(LoginError):
                    window.command({'action': 'click', 'revision': -1, 'x': 10, 'y': 10})
                # User finishes a normal 2FA form; no automated solver is involved.
                window.page.locator('#otp').fill('123456')
                window.page.get_by_role('button', name='验证', exact=True).click()
                window.page.wait_for_url(ORIGIN + '/dashboard')
                window.page.wait_for_selector('button:has-text("签到领")')
                self.assertTrue(window.frame()['ready'])
                # Even arbitrary clicks cannot perform a check-in in a login window.
                before = list(writes)
                window.page.get_by_role('button', name='签到领 300', exact=True).click()
                window.page.wait_for_timeout(50)
                self.assertEqual(writes, before)
                spec = validate_browser_spec(task_spec(window.command({'action': 'finish'})['state']))
                self.assertNotIn(PASSWORD, json.dumps(spec))
            finally:
                window.close()
            result = _run_browser_in_process(spec)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(writes.count('/api/v1/checkin'), 1)
            signed = result['_storage_state']
            # Fixture uses a localStorage flag; add it only to this replay fixture.
            spec['storage_state'] = signed
            again = _run_browser_in_process(spec)
            self.assertEqual(again['status'], 'already_done')
            self.assertEqual(writes.count('/api/v1/checkin'), 1)


def fixture_worker():
    """A test-only child entry point; production has no fixture switch."""
    from playwright.sync_api import BrowserContext
    from autosign.account_login import _worker
    original_route = BrowserContext.route

    def route_fixture(context, pattern, handler, **kwargs):
        def reply(route):
            request = route.request
            if not login_request_allowed(request.method, request.url):
                return route.abort()
            if request.method == 'POST':
                return route.fulfill(status=200, content_type='application/json', body='{}')
            if request.url.startswith(ORIGIN):
                return route.fulfill(status=200, content_type='text/html',
                                     body=LOGIN_HTML if urlsplit_path(request.url) == '/login' else DASHBOARD_HTML)
            return route.abort()
        return original_route(context, pattern, reply, **kwargs)

    with patch('autosign.account_login._network_rules', return_value='MAP * ^NOTFOUND'), \
         patch.object(BrowserContext, 'route', route_fixture):
        _worker()


def urlsplit_path(url):
    from urllib.parse import urlsplit
    return urlsplit(url).path


class LoginProcessTests(unittest.TestCase):
    def test_real_private_child_expires_and_has_no_credential_in_argv(self):
        import subprocess
        from pathlib import Path
        original_popen = subprocess.Popen
        test_file = str(Path(__file__).resolve())
        launches = []

        def launch(argv, **kwargs):
            self.assertNotIn(PASSWORD, repr(argv))
            self.assertNotIn('fixture@example.com', repr(argv))
            self.assertTrue(kwargs['start_new_session'])
            self.assertNotIn('PWDEBUG', kwargs['env'])
            launches.append(argv)
            return original_popen([sys_executable(), '-c',
                'import runpy; runpy.run_path(' + repr(test_file) + ')["fixture_worker"]()'], **kwargs)

        env = {'PWDEBUG': '1'}
        if os.path.exists('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'):
            env['AUTOSIGN_BROWSER_EXECUTABLE'] = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        manager = LoginManager()
        try:
            with patch.dict(os.environ, env), patch('autosign.account_login.subprocess.Popen', launch):
                key = manager.start('owner', 'fixture', 'fixture@example.com', PASSWORD)
            item = manager.get(key, 'owner')
            frame = item.send({'action': 'frame'})
            self.assertFalse(frame['ready'])
            self.assertTrue(frame['image'].startswith('/9j/'))
            self.assertEqual(len(launches), 1)
            item.expires = time.monotonic() - 1
            with self.assertRaises(LoginError):
                manager.get(key, 'owner')
            self.assertTrue(item.closed)
            self.assertIsNotNone(item.process.poll())
        finally:
            manager.close()


def sys_executable():
    import sys
    return sys.executable


if __name__ == '__main__':
    unittest.main()
