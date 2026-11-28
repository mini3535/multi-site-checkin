"""User-defined account/password connections, with no external accounts."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from autosign.account_login import (LoginError, LoginManager, LoginWindow,
                                    connection_config, website_credentials, website_request_allowed)
from autosign.app import create_app
from autosign.browser_runner import _run_browser_in_process, validate_browser_spec
import test_account_login as base_tests
from test_account_login import FakeSession, PASSWORD

ORIGIN = 'https://forum.example'


def website_state():
    return {'cookies': [], 'origins': [{'origin': ORIGIN, 'localStorage': [
        {'name': 'session', 'value': 'fixture-website-session'}]}]}


def spec():
    return validate_browser_spec({'engine': 'browser', 'site_id': 'custom',
        'start_url': ORIGIN + '/account', 'allowed_hosts': ['forum.example'],
        'browser_login': {'url': ORIGIN + '/signin'}, 'storage_state': {'cookies': [], 'origins': []},
        'steps': [{'action': 'click', 'selector': 'button:has-text("每日签到")', 'timeout_ms': 1000}],
        'success_text': '签到成功', 'already_done_text': '今天已经签到',
        'login_selector': 'input[type="password"]', 'verification_selector': ''})


class GenericFakeSession(FakeSession):
    def send(self, command):
        result = super().send(command)
        if command['action'] == 'finish':
            result['state'] = website_state()
        else:
            result['ready'] = False
            result['manual_confirmation'] = True
        return result


class WebsiteLoginTests(unittest.TestCase):
    csrf = base_tests.LoginAppTests.csrf
    post = base_tests.LoginAppTests.post

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'DATA_DIR': self.temp.name, 'ADMIN_PASSWORD': 'fixture-admin-password',
                               'ENCRYPTION_KEY': Fernet.generate_key().decode(), 'TESTING': True})
        self.store = self.app.extensions['store']
        self.manager = self.app.extensions['account_login']
        self.client = self.app.test_client()
        self.client.get('/login')
        self.post('/login', password='fixture-admin-password')
        self.client.get('/')
        self.stub = patch('autosign.account_login._Session', GenericFakeSession)
        self.stub.start()

    def tearDown(self):
        self.manager.close()
        self.stub.stop()
        self.temp.cleanup()

    def fields(self, **changes):
        fields = {'name': '自己的论坛', 'connection_method': 'password',
                  'start_url': ORIGIN + '/account', 'login_url': ORIGIN + '/signin',
                  'website_username': 'ordinary-user', 'website_password': PASSWORD,
                  'button_text': '每日签到', 'success_text': '签到成功',
                  'already_done_text': '今天已经签到', 'login_selector': 'input[type="password"]'}
        return {**fields, **changes}

    def start(self, path='/sites/custom/new', **changes):
        response = self.post(path, **self.fields(**changes))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        key = response.location.rsplit('/', 1)[-1]
        return key, self.manager.sessions[key]

    def test_custom_entry_has_password_option_and_catalog_has_no_pinned_xingchi(self):
        catalog = self.client.get('/sites').get_data(as_text=True)
        self.assertIn('/sites/custom/new', catalog)
        self.assertNotIn('/sites/xingchi/new', catalog)
        page = self.client.get('/sites/custom/new').get_data(as_text=True)
        for field in ('connection_method', 'login_url', 'website_username', 'website_password'):
            self.assertIn('name="' + field + '"', page)
        self.assertIn('value="session" selected', page)
        for method in ('cookie', 'token', 'session'):
            self.assertIn('value="' + method + '"', page)
        self.assertNotIn(PASSWORD, page)

    def test_own_website_session_is_saved_only_after_confirmation_and_stays_paused(self):
        key, item = self.start()
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(item.spec['site_id'], 'custom')
        self.assertEqual(item.spec['start_url'], ORIGIN + '/account')
        self.assertNotIn(PASSWORD, json.dumps(item.spec))
        page = self.client.get('/account-logins/' + key).get_data(as_text=True)
        self.assertIn('name="confirmed"', page)
        self.assertNotIn('星驰', page)
        item.ready = True
        self.post(f'/account-logins/{key}/save')
        self.assertEqual(self.store.list_tasks(), [])
        self.assertNotIn('finish', item.calls)
        self.post(f'/account-logins/{key}/save', confirmed='1')
        task = self.store.list_tasks()[0]
        saved = self.store.spec(task)
        self.assertFalse(task['enabled'])
        self.assertFalse(self.store.verification(task['id'])['ready'])
        self.assertEqual(saved['storage_state'], website_state())
        self.assertEqual(saved['browser_login'], {'url': ORIGIN + '/signin'})
        self.assertNotIn('website_username', json.dumps(saved))
        self.assertNotIn(PASSWORD.encode(), self.store.path.read_bytes())
        self.post(f'/account-logins/{key}/save', confirmed='1')
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertTrue(self.client.get(f'/tasks/{task["id"]}/schedule').location.endswith('/verify'))

    def test_existing_task_preserves_rules_and_can_reconnect_or_keep_session(self):
        original = {**spec(), 'storage_state': website_state()}
        task_id = self.store.save_task({'name': 'old', 'enabled': False, 'daily_time': '08:00', 'timezone': 'UTC'}, original)
        key, item = self.start(f'/tasks/{task_id}/site-edit')
        item.ready = True
        self.post(f'/account-logins/{key}/save', confirmed='1')
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(self.store.spec(self.store.get_task(task_id))['steps'][0]['selector'], 'button:has-text("每日签到")')
        response = self.post(f'/tasks/{task_id}/site-edit', **self.fields(website_username='', website_password=''))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.spec(self.store.get_task(task_id))['storage_state'], website_state())
        response = self.post(f'/tasks/{task_id}/site-edit', **self.fields(website_username='', website_password='', login_url=ORIGIN + '/other'))
        self.assertEqual(response.status_code, 400)
        response = self.post(f'/tasks/{task_id}/site-edit', **self.fields(connection_method='cookie', browser_cookie='session=new', website_username='', website_password=''))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('browser_login', self.store.spec(self.store.get_task(task_id)))

    def test_invalid_urls_credentials_and_mixed_modes_never_open_browser_or_echo_password(self):
        for changes in ({'login_url': 'https://evil.example/login'}, {'login_url': 'http://forum.example/login'},
                        {'website_username': ''}, {'website_password': ''}, {'browser_token': 'fixture-token'},
                        {'connection_method': 'cookie'}, {'button_text': ''}):
            with self.subTest(changes=changes):
                response = self.post('/sites/custom/new', **self.fields(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(PASSWORD, response.get_data(as_text=True))
                self.assertEqual(self.manager.sessions, {})
                self.assertEqual(self.store.list_tasks(), [])

    def test_other_admin_session_can_release_without_viewing_its_login(self):
        key, item = self.start()
        outsider = self.app.test_client()
        outsider.get('/login')
        outsider.post('/login', data={'csrf_token': self.csrf(outsider), 'password': 'fixture-admin-password'})
        page = outsider.get('/sites/custom/new').get_data(as_text=True)
        self.assertIn('关闭占用窗口，重新连接', page)
        self.assertNotIn('/account-logins/' + key, page)
        self.assertNotIn(PASSWORD, page)
        self.assertEqual(outsider.get('/account-logins/' + key).status_code, 404)
        self.assertEqual(outsider.post('/account-logins/release', data={'login_id': key}).status_code, 400)
        self.assertFalse(item.closed)
        response = outsider.post('/account-logins/release', data={'csrf_token': self.csrf(outsider), 'login_id': key})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(item.closed)
        self.assertEqual(self.manager.sessions, {})
        self.assertEqual(self.store.list_tasks(), [])
        next_key, next_item = self.start()
        # An old release submission must not close a newer window.
        self.post('/account-logins/release', login_id=key)
        self.assertFalse(next_item.closed)
        self.assertIn(next_key, self.manager.sessions)

    def test_owned_window_has_continue_and_release_actions(self):
        key, item = self.start()
        page = self.client.get('/sites/custom/new').get_data(as_text=True)
        self.assertIn('/account-logins/' + key, page)
        self.assertIn('关闭占用窗口，重新连接', page)
        self.post('/account-logins/release', login_id=key)
        self.assertTrue(item.closed)
        next_key, _ = self.start()
        self.assertNotEqual(key, next_key)

    def test_dead_worker_does_not_block_a_new_connection(self):
        from unittest.mock import Mock
        key, item = self.start()
        item.process = Mock()
        item.process.poll.return_value = 1
        self.assertIsNone(self.manager.pending('another-owner'))
        self.assertTrue(item.closed)
        self.assertNotIn(key, self.manager.sessions)
        next_key, next_item = self.start()
        self.assertFalse(next_item.closed)
        self.assertNotEqual(key, next_key)

    def test_cancel_returns_to_custom_form_and_preserves_existing_task(self):
        key, item = self.start()
        response = self.post(f'/account-logins/{key}/cancel')
        self.assertTrue(response.location.endswith('/sites/custom/new'))
        self.assertTrue(item.closed)
        self.assertEqual(self.store.list_tasks(), [])


LOGIN_HTML = '''<!doctype html><meta charset="utf-8"><form onsubmit="login(event)">
<label>用户名<input name="username" autocomplete="username"></label>
<label>密码<input type="password"></label><button type="submit">登录</button></form>
<script>async function login(e){e.preventDefault(); await fetch('/session',{method:'POST'});location.href='/account'}</script>'''
DASHBOARD_HTML = '''<!doctype html><meta charset="utf-8"><p>已登录</p><button onclick="sign()">每日签到</button>
<script>localStorage.setItem('session','fixture-session');
if(localStorage.getItem('done'))document.querySelector('button').outerHTML='<p>今天已经签到</p>';
async function sign(){await fetch('/daily',{method:'POST'});localStorage.setItem('done','yes');document.querySelector('button').outerHTML='<p>签到成功</p>'}</script>'''


def fixture_worker():
    from autosign.account_login import _worker
    from playwright.sync_api import BrowserContext
    original_route = BrowserContext.route
    def install(context, pattern, handler, **kwargs):
        def reply(route):
            if urlsplit(route.request.url).hostname != 'forum.example':
                return route.abort()
            if route.request.method == 'POST':
                return route.fulfill(status=200, content_type='application/json', body='{}')
            return route.fulfill(status=200, content_type='text/html', body=LOGIN_HTML if urlsplit(route.request.url).path == '/signin' else DASHBOARD_HTML)
        return original_route(context, pattern, reply, **kwargs)
    with patch('autosign.account_login._network_rules', return_value='MAP * ^NOTFOUND'), patch.object(BrowserContext, 'route', install):
        _worker()


class WebsiteBrowserTests(unittest.TestCase):
    def test_non_xingchi_form_fills_without_submitting_or_recording_then_runs_custom_rules(self):
        from playwright.sync_api import BrowserContext
        original_route = BrowserContext.route
        writes = []
        def install(context, pattern, handler, **kwargs):
            def reply(route):
                url = route.request.url
                if not website_request_allowed(route.request.method, url, connection_config(spec())):
                    return route.abort()
                if route.request.method == 'POST':
                    writes.append(urlsplit(url).path)
                    return route.fulfill(status=200, content_type='application/json', body='{}')
                return route.fulfill(status=200, content_type='text/html', body=LOGIN_HTML if urlsplit(url).path == '/signin' else DASHBOARD_HTML)
            return original_route(context, pattern, reply, **kwargs)
        env = {}
        chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        if os.path.exists(chrome):
            env['AUTOSIGN_BROWSER_EXECUTABLE'] = chrome
        with patch.dict(os.environ, env), patch('autosign.account_login._network_rules', return_value='MAP * ^NOTFOUND'), patch('autosign.browser_runner._network_rules', return_value='MAP * ^NOTFOUND'), patch.object(BrowserContext, 'route', install):
            window = LoginWindow(connection_config(spec()))
            try:
                result = window.command({'action': 'credentials', 'email': 'ordinary-user', 'password': PASSWORD})
                self.assertTrue(result['manual_confirmation'])
                self.assertFalse(result['ready'])
                self.assertEqual(window.page.locator('[name=username]').input_value(), 'ordinary-user')
                self.assertEqual(window.page.locator('[type=password]').input_value(), PASSWORD)
                self.assertEqual(writes, [])
                with self.assertRaises(LoginError):
                    window.command({'action': 'finish'})
                window.page.get_by_role('button', name='登录', exact=True).click()
                window.page.wait_for_url(ORIGIN + '/account')
                window.page.wait_for_selector('text=已登录')
                saved = window.command({'action': 'finish'})['state']
                self.assertNotIn(PASSWORD, json.dumps(saved))
                self.assertEqual(writes, ['/session'])
            finally:
                window.close()
            result = _run_browser_in_process({**spec(), 'storage_state': saved})
            self.assertEqual(result['status'], 'success')
            self.assertEqual(writes, ['/session', '/daily'])
            again = _run_browser_in_process({**spec(), 'storage_state': result['_storage_state']})
            self.assertEqual(again['status'], 'already_done')
            self.assertEqual(writes, ['/session', '/daily'])

    def test_config_bounds_credential_hosts_and_does_not_store_login_password(self):
        config = connection_config(spec())
        website_credentials('username', PASSWORD)
        self.assertTrue(website_request_allowed('POST', ORIGIN + '/session', config))
        for method, url in (('POST', 'https://evil.example/session'), ('GET', 'http://forum.example/signin'),
                            ('GET', 'https://127.0.0.1/'), ('DELETE', ORIGIN + '/account'),
                            ('POST', 'https://user:password@forum.example/session')):
            self.assertFalse(website_request_allowed(method, url, config))
        for metadata in ({'url': ORIGIN + '/signin', 'password': PASSWORD}, {'url': 'https://evil.example/login'}):
            with self.assertRaises(ValueError):
                validate_browser_spec({**spec(), 'browser_login': metadata})
