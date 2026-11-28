import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from autosign.browser_runner import (
    _allowed_request, _browser_launch_options, _kill_browser_tree, _network_rules, _process_parents, _run_browser_in_process,
    run_browser, validate_browser_spec,
)


def sample(url='https://signin.example/checkin'):
    return {
        'engine': 'browser', 'site_id': 'custom', 'start_url': url,
        'allowed_hosts': ['signin.example'],
        'storage_state': {'cookies': [], 'origins': []},
        'steps': [{'action': 'click', 'selector': '#sign'}],
        'success_text': '签到成功', 'already_done_text': '今日已签到',
        'login_selector': '#login-required', 'verification_selector': '#verification-required',
    }


class ValidationTests(unittest.TestCase):
    def test_auto_success_allows_missing_site_specific_success_text(self):
        self.assertNotIn("auto_success", validate_browser_spec(sample()))
        spec = sample()
        spec["success_text"] = ""
        spec["auto_success"] = True
        normalized = validate_browser_spec(spec)
        self.assertEqual(normalized["success_text"], "")
        self.assertTrue(normalized["auto_success"])

        spec["auto_success"] = "yes"
        with self.assertRaises(ValueError):
            validate_browser_spec(spec)

    def test_new_site_ids_accept_browser_workflows_but_not_info_only_site(self):
        for site_id, url, host in (
            ("nodeseek", "https://www.nodeseek.com/board", "www.nodeseek.com"),
            ("hostloc", "https://hostloc.com/forum.php", "hostloc.com"),
        ):
            with self.subTest(site=site_id):
                spec = validate_browser_spec({
                    "engine": "browser", "site_id": site_id,
                    "start_url": url, "allowed_hosts": [host],
                    "storage_state": {"cookies": [], "origins": []},
                    "steps": [{"action": "click", "selector": "#checkin"}],
                    "success_text": "签到成功",
                })
                self.assertEqual(spec["site_id"], site_id)
        with self.assertRaises(ValueError):
            validate_browser_spec({
                "engine": "browser", "site_id": "linuxdo",
                "start_url": "https://linux.do/latest", "allowed_hosts": ["linux.do"],
                "storage_state": {"cookies": [], "origins": []},
                "steps": [], "success_text": "已访问",
            })

    def test_chromium_launch_explicitly_enables_sandbox(self):
        options = _browser_launch_options('MAP signin.example 93.184.216.34, MAP * ^NOTFOUND', 15000, '/usr/bin/chromium')
        self.assertIs(options['chromium_sandbox'], True)
        self.assertEqual(options['timeout'], 15000)
        self.assertEqual(options['executable_path'], '/usr/bin/chromium')
        self.assertIn('--host-resolver-rules=MAP signin.example 93.184.216.34, MAP * ^NOTFOUND', options['args'])

    def test_bundled_seccomp_profile_and_compose_security_are_restricted(self):
        project = Path(__file__).resolve().parents[1]
        profile_bytes = (project / 'seccomp_profile.json').read_bytes()
        self.assertEqual(hashlib.sha256(profile_bytes).hexdigest(), 'cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849')
        profile = json.loads(profile_bytes)
        self.assertEqual(profile['defaultAction'], 'SCMP_ACT_ERRNO')
        self.assertGreaterEqual(len(profile['syscalls']), 20)
        architectures = {entry['architecture'] for entry in profile['archMap']}
        self.assertIn('SCMP_ARCH_X86_64', architectures)
        self.assertIn('SCMP_ARCH_AARCH64', architectures)
        userns = next(rule for rule in profile['syscalls'] if rule.get('comment') == 'Allow create user namespaces')
        self.assertEqual(set(userns['names']), {'clone', 'setns', 'unshare'})
        self.assertEqual(userns['action'], 'SCMP_ACT_ALLOW')
        compose = (project / 'compose.yaml').read_text()
        dockerfile = (project / 'Dockerfile').read_text()
        self.assertIn('seccomp=${PWD}/seccomp_profile.json', compose)
        self.assertNotIn('seccomp:unconfined', compose)
        self.assertIn('COPY seccomp_profile.json /usr/share/autosign/seccomp_profile.json', dockerfile)
        self.assertIn('USER 10001:10001', dockerfile)
        self.assertNotIn('SYS_ADMIN', compose + dockerfile)
        self.assertNotIn('cap_add', compose + dockerfile)

    def test_timeout_cleanup_only_kills_groups_led_by_current_descendants(self):
        process = Mock(pid=100, returncode=None)
        process.poll.return_value = -9
        first = {100: 1, 101: 100, 200: 101, 201: 200, 300: 101, 400: 101, 900: 1}
        # PID 400 was a child at the first snapshot but was reused elsewhere.
        second = {**first, 400: 900}
        group_for = {100: 100, 101: 100, 200: 200, 201: 200, 300: 900, 400: 400}
        with patch('autosign.browser_runner._process_parents', side_effect=[first, second]), \
             patch('os.getpgid', side_effect=lambda pid: group_for[pid]), \
             patch('os.getpgrp', return_value=50), patch('os.killpg') as killpg:
            _kill_browser_tree(process)
        self.assertEqual(killpg.call_args_list[0].args, (100, signal.SIGSTOP))
        killed = {call.args[0] for call in killpg.call_args_list if call.args[1] == signal.SIGKILL}
        self.assertEqual(killed, {100, 200})
        self.assertEqual(killpg.call_args_list[-1].args, (100, signal.SIGKILL))

    def test_offline_normalization_and_cookie_scope(self):
        spec = sample()
        spec['storage_state']['cookies'] = [
            {'name': 'session', 'value': 'secret', 'domain': '.example', 'path': '/'},
            {'name': 'unrelated', 'value': 'do-not-import', 'domain': 'unrelated.invalid', 'path': '/'},
        ]
        with patch('socket.getaddrinfo', side_effect=AssertionError('validation queried DNS')):
            normalized = validate_browser_spec(spec)
        self.assertEqual(normalized['steps'][0]['timeout_ms'], 10000)
        self.assertEqual(len(normalized['storage_state']['cookies']), 1)
        self.assertEqual(normalized['storage_state']['cookies'][0]['name'], 'session')

    def test_browser_start_url_supports_spa_fragment(self):
        spec = sample('https://signin.example/app#/daily')
        self.assertEqual(validate_browser_spec(spec)['start_url'], 'https://signin.example/app#/daily')

    def test_rejects_code_wildcards_missing_success_or_malformed_state(self):
        cases = []
        for key, value in [
            ('start_url', 'file:///etc/passwd'),
            ('allowed_hosts', ['*.example']),
            ('allowed_hosts', ['signin.example:8080']),
            ('success_text', ''),
            ('site_id', 'arbitrary-plugin'),
            ('steps', [{'action': 'eval', 'selector': 'body', 'value': 'process.exit()'}]),
            ('steps', [{'action': 'click', 'selector': '#sign', 'timeout_ms': True}]),
            ('storage_state', {'cookies': 'not-a-list'}),
            ('storage_state', {'cookies': [{'name': 'x', 'value': 'secret', 'domain': 'signin.example', 'secure': 'yes'}]}),
        ]:
            spec = sample()
            spec[key] = value
            cases.append(spec)
        for spec in cases:
            with self.subTest(field=next(k for k in spec if spec[k] != sample().get(k))):
                with self.assertRaises(ValueError):
                    validate_browser_spec(spec)

    def test_scope_blocks_other_hosts_protocols_credentials_and_ports(self):
        spec = validate_browser_spec(sample())
        self.assertTrue(_allowed_request('https://signin.example/api', spec))
        for url in ('https://evil.example/', 'http://127.0.0.1/', 'file:///etc/passwd',
                    'https://user:secret@signin.example/', 'https://signin.example:6379/'):
            self.assertFalse(_allowed_request(url, spec))

    def test_resolver_rules_pin_every_host_and_block_other_dns(self):
        spec = validate_browser_spec(sample())
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', 443))
        with patch('autosign.browser_runner._resolve', return_value=address) as resolver:
            rules = _network_rules(spec, False)
        self.assertEqual(rules, 'MAP signin.example 93.184.216.34, MAP * ^NOTFOUND')
        resolver.assert_called_once_with('signin.example', 443, False)


class MockWebsite(BaseHTTPRequestHandler):
    posts = 0
    leaks = 0
    loads = 0
    authorizations = []

    def do_GET(self):
        type(self).loads += 1
        type(self).authorizations.append((self.path, self.headers.get('Authorization')))
        if self.path == '/leak':
            type(self).leaks += 1
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', f'http://localhost:{self.server.server_port}/leak')
            self.end_headers()
            return
        pages = {
            '/token-checkin': """<div id='login-required'>请登录</div><button id='sign' hidden>签到</button><p id='result'></p>
                <script>
                const token = localStorage.getItem('access_token') || localStorage.getItem('site_token');
                if (token === 'fixture-browser-token') {
                  document.querySelector('#login-required').remove();
                  const sign = document.querySelector('#sign'); sign.hidden = false;
                  sign.onclick = () => fetch('/api/token-sign', {method:'POST', headers:{Authorization:'Bearer '+token}})
                    .then(r => { if (r.ok) document.querySelector('#result').textContent='签到成功'; });
                }
                fetch('/public-asset').catch(()=>0);
                </script>""",
            '/checkin': "<button id='sign' onclick=\"fetch('/api/sign',{method:'POST'}).then(()=>document.querySelector('#result').textContent='签到成功')\">签到</button><p id='result'></p>",
            '/already': "<p>今日已签到</p><button id='sign' onclick=\"fetch('/api/sign',{method:'POST'})\">签到</button>",
            '/login': "<div id='login-required'>请登录</div><button id='sign'>签到</button>",
            '/verify': "<div id='verification-required'>请完成验证</div><button id='sign'>签到</button>",
            '/wordtrap': '<p>执行签到成功可以领取奖励，但这里尚未签到</p>',
            '/exfil': f"<script>fetch('http://localhost:{self.server.server_port}/leak').catch(()=>0)</script><p>签到成功</p>",
        }
        pages['/profile-token-checkin'] = pages['/token-checkin'].replace(
            "localStorage.getItem('access_token') || localStorage.getItem('site_token')",
            "JSON.parse(localStorage.getItem('auth_user') || '{}').id === 7 ? localStorage.getItem('auth_token') : null")
        body = ('<!doctype html><meta charset="utf-8">' + pages.get(self.path, '<p>页面</p>')).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        type(self).posts += 1
        type(self).authorizations.append((self.path, self.headers.get('Authorization')))
        body = b'{}'
        valid = self.path != '/api/token-sign' or self.headers.get('Authorization') == 'Bearer fixture-browser-token'
        self.send_response(200 if valid else 401)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'rotated=fresh-session; Path=/; HttpOnly; SameSite=Lax')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _find_browser_executable():
    configured = os.environ.get('AUTOSIGN_BROWSER_EXECUTABLE')
    if configured and Path(configured).is_file():
        return configured
    try:
        from playwright.sync_api import sync_playwright
        # Discover the installed browser without opening a browser or downloading.
        with sync_playwright() as playwright:
            bundled = playwright.chromium.executable_path
        if Path(bundled).is_file():
            return bundled
    except (ImportError, OSError, RuntimeError):
        pass
    return '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


CHROME = _find_browser_executable()


@unittest.skipUnless(Path(CHROME).is_file(), 'A local Chromium executable is needed for browser integration tests')
class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), MockWebsite)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        MockWebsite.posts = MockWebsite.leaks = MockWebsite.loads = 0
        MockWebsite.authorizations = []
        self.env = patch.dict(os.environ, {'AUTOSIGN_BROWSER_EXECUTABLE': CHROME})
        self.env.start()
        self.addCleanup(self.env.stop)

    def spec(self, path):
        spec = sample(f'http://127.0.0.1:{self.server.server_port}{path}')
        spec['allowed_hosts'] = ['127.0.0.1']
        return spec

    def test_click_signs_once_and_refreshed_session_is_private_metadata(self):
        result = run_browser(self.spec('/checkin'), allow_private=True)
        self.assertEqual(result['status'], 'success', result)
        self.assertTrue(result['ok'])
        self.assertEqual(MockWebsite.posts, 1)
        self.assertEqual(result['_storage_state']['cookies'][0]['value'], 'fresh-session')
        self.assertNotIn('fresh-session', result['message'])
        self.assertNotIn('/api/sign', result['message'])

    def test_token_local_storage_restores_login_without_global_authorization_headers(self):
        from autosign.browser_auth import token_storage_state
        for key in ('', 'site_token'):
            with self.subTest(key=key):
                MockWebsite.posts = 0
                MockWebsite.authorizations = []
                spec = self.spec('/token-checkin')
                spec['storage_state'], spec['token_login'] = token_storage_state('Bearer fixture-browser-token', spec['start_url'], key)
                result = run_browser(spec, allow_private=True)
                self.assertEqual(result['status'], 'success', result)
                self.assertEqual(MockWebsite.posts, 1)
                self.assertIn(('/api/token-sign', 'Bearer fixture-browser-token'), MockWebsite.authorizations)
                self.assertIn(('/public-asset', None), MockWebsite.authorizations)
                self.assertIn(('/token-checkin', None), MockWebsite.authorizations)
                entries = result['_storage_state']['origins'][0]['localStorage']
                self.assertIn({'name': key or 'access_token', 'value': 'fixture-browser-token'}, entries)
                self.assertNotIn('fixture-browser-token', result['message'])

    def test_profile_bootstrap_produces_user_cache_required_by_browser_guard(self):
        from autosign.browser_auth import prepare_token_session, token_storage_state
        spec = self.spec('/profile-token-checkin')
        spec['storage_state'], spec['token_login'] = token_storage_state('fixture-browser-token', spec['start_url'], 'auth_token')
        result = run_browser(spec, allow_private=True)
        self.assertEqual(result['status'], 'needs_login', result)
        self.assertEqual(MockWebsite.posts, 0)
        remote = dict(spec, start_url='https://api.xingchiapi.com/dashboard')
        remote['storage_state'], remote['token_login'] = token_storage_state('fixture-browser-token', remote['start_url'])
        state = prepare_token_session(remote, transport=lambda *_args: {
            'status_code': 200, 'body': json.dumps({'code': 0, 'data': {'id': 7, 'role': 'user'}})})
        state['origins'][0]['origin'] = f'http://127.0.0.1:{self.server.server_port}'
        spec['storage_state'] = state
        result = run_browser(spec, allow_private=True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(MockWebsite.posts, 1)
        self.assertNotIn('fixture-browser-token', result['message'])

    def test_already_done_does_not_click_again(self):
        result = run_browser(self.spec('/already'), allow_private=True)
        self.assertEqual(result['status'], 'already_done', result)
        self.assertTrue(result['ok'])
        self.assertEqual(MockWebsite.posts, 0)

    def test_login_or_verification_stops_before_mutation(self):
        for path, status in [('/login', 'needs_login'), ('/verify', 'needs_verification')]:
            with self.subTest(status=status):
                result = run_browser(self.spec(path), allow_private=True)
                self.assertEqual(result['status'], status, result)
                self.assertFalse(result['ok'])
                self.assertEqual(MockWebsite.posts, 0)

    def test_exact_text_required_and_generic_sentence_is_not_success(self):
        spec = self.spec('/wordtrap')
        spec['steps'] = []
        result = run_browser(spec, allow_private=True)
        self.assertEqual(result['status'], 'failed', result)

    def test_unlisted_hosts_blocked_for_resources_and_redirects(self):
        spec = self.spec('/exfil')
        spec['steps'] = []
        result = run_browser(spec, allow_private=True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(MockWebsite.leaks, 0)
        result = run_browser(self.spec('/redirect'), allow_private=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(MockWebsite.leaks, 0)

    def test_private_address_is_blocked_by_default(self):
        result = run_browser(self.spec('/checkin'))
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(MockWebsite.posts, 0)

    def test_missing_browser_is_actionable_without_exposing_path(self):
        with patch.dict(os.environ, {'AUTOSIGN_BROWSER_EXECUTABLE': '/does-not-exist/secret-path'}):
            result = run_browser(self.spec('/checkin'), allow_private=True)
        self.assertEqual(result['status'], 'unsupported', result)
        self.assertIn('Playwright', result['message'])
        self.assertNotIn('secret-path', result['message'])

    def test_total_deadline_includes_browser_startup(self):
        started = time.monotonic()
        with patch('autosign.browser_runner.MAX_RUN_SECONDS', 0.15):
            result = run_browser(self.spec('/checkin'), allow_private=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertLess(time.monotonic() - started, 3)

    def test_total_deadline_stops_a_running_browser_workflow(self):
        spec = self.spec('/checkin')
        spec['steps'] = [{'action': 'wait', 'selector': '#never-present', 'timeout_ms': 30000}]
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        tracked = set()

        def record_cleanup(process):
            parents = _process_parents()
            tracked.add(process.pid)
            while True:
                found = {pid for pid, parent in parents.items() if parent in tracked}
                if found <= tracked:
                    break
                tracked.update(found)
            _kill_browser_tree(process)

        started = time.monotonic()
        try:
            with patch('autosign.browser_runner.MAX_RUN_SECONDS', 2), \
                 patch('autosign.browser_runner._kill_browser_tree', side_effect=record_cleanup):
                result = run_browser(spec, allow_private=True)
            self.assertIsNone(unrelated.poll(), 'Unrelated process must remain alive')
        finally:
            unrelated.kill()
            unrelated.wait()
        self.assertEqual(result['status'], 'failed', result)
        self.assertGreater(MockWebsite.loads, 0, 'The browser should be active before termination')
        self.assertLess(time.monotonic() - started, 5)
        self.assertGreater(len(tracked), 2, 'The cleanup should include a real running browser tree')
        deadline = time.monotonic() + 1
        while tracked.intersection(_process_parents()) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(tracked.intersection(_process_parents()), 'No task browser process should remain')

    def test_chromium_host_rule_really_pins_synthetic_dns_name(self):
        spec = self.spec('/checkin')
        spec['start_url'] = f'http://autosign-pin.test:{self.server.server_port}/checkin'
        spec['allowed_hosts'] = ['autosign-pin.test']
        address = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))
        with patch('autosign.browser_runner._resolve', return_value=address):
            result = _run_browser_in_process(validate_browser_spec(spec), allow_private=True)
        self.assertEqual(result['status'], 'success', result)
        self.assertEqual(MockWebsite.posts, 1)


if __name__ == '__main__':
    unittest.main()
