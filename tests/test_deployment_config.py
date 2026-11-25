import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from autosign.app import create_app
from deployment import access_url, completion, configure, validate_host, verify_domain


class DeploymentConfigTests(unittest.TestCase):
    def test_domain_verification_requires_https_origin_and_valid_health(self):
        env = {"AUTOSIGN_ACCESS_MODE": "domain", "AUTOSIGN_DOMAIN": "sign.example.com"}
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = "https://sign.example.com/healthz"
        response.read.return_value = b'{ "status": "ok" }'
        with patch("deployment.urllib.request.urlopen", return_value=response), patch("deployment.time.sleep"), patch("builtins.print"):
            verify_domain(env)
            for wrong_url in ["http://sign.example.com/healthz", "https://other.example.com/healthz"]:
                response.geturl.return_value = wrong_url
                with self.assertRaises(ValueError):
                    verify_domain(env)
            response.geturl.return_value = "https://sign.example.com/healthz"
            response.read.return_value = b"not-json"
            with self.assertRaises(ValueError):
                verify_domain(env)

    def test_access_configuration_preserves_secrets_and_unrelated_settings(self):
        source = '''# keep comment
AUTOSIGN_SECRET_KEY=do-not-rotate
AUTOSIGN_ADMIN_PASSWORD=keep-password
PORT=18080
COMPOSE_PROFILES=extra,web
AUTOSIGN_ACCESS_MODE=domain
AUTOSIGN_ACCESS_MODE=domain
'''
        configured = configure(source, 'ip', '203.0.113.8')
        self.assertIn('AUTOSIGN_SECRET_KEY=do-not-rotate', configured)
        self.assertIn('AUTOSIGN_ADMIN_PASSWORD=keep-password', configured)
        self.assertIn('PORT=18080', configured)
        self.assertIn('COMPOSE_PROFILES=extra', configured)
        self.assertEqual(configured.count('AUTOSIGN_ACCESS_MODE='), 1)
        self.assertIn('BIND_ADDRESS=0.0.0.0', configured)
        self.assertNotIn('AUTOSIGN_COOKIE_SECURE=true', configured)

    def test_domain_and_private_modes(self):
        domain = configure('', 'domain', 'Sign.Example.com')
        self.assertIn('AUTOSIGN_DOMAIN=sign.example.com', domain)
        self.assertIn('COMPOSE_PROFILES=web', domain)
        self.assertIn('AUTOSIGN_COOKIE_SECURE=true', domain)
        self.assertIn('BIND_ADDRESS=127.0.0.1', domain)
        external = configure(domain, 'domain', 'sign.example.com', external_proxy=True)
        self.assertIn('AUTOSIGN_EXTERNAL_PROXY=true', external)
        self.assertIn('COMPOSE_PROFILES=', external)
        private = configure(domain, 'private', '')
        self.assertIn('COMPOSE_PROFILES=', private)
        self.assertIn('AUTOSIGN_DOMAIN=', private)
        self.assertIn('AUTOSIGN_COOKIE_SECURE=false', private)

    def test_invalid_hosts_and_injection_are_rejected(self):
        for mode, host in [('domain', 'https://example.com'), ('domain', '127.0.0.1'),
                           ('domain', 'example.com\nBIND_ADDRESS=0.0.0.0'), ('domain', 'a..com'),
                           ('domain', 'bad_name.com'), ('domain', '-bad.com'),
                           ('ip', 'example.com'), ('ip', '0.0.0.0'), ('ip', '127.0.0.1'),
                           ('ip', '::'), ('ip', 'fe80::1'), ('ip', '224.0.0.1')]:
            with self.subTest(mode=mode, host=host):
                with self.assertRaises(ValueError):
                    validate_host(mode, host)

    def test_urls_match_mode_and_port_including_ipv6(self):
        self.assertEqual(access_url({}), 'http://localhost:8080/login')
        self.assertEqual(access_url({'AUTOSIGN_ACCESS_MODE': 'ip', 'AUTOSIGN_PUBLIC_HOST': '203.0.113.8', 'PORT': '18080'}), 'http://203.0.113.8:18080/login')
        self.assertEqual(access_url({'AUTOSIGN_ACCESS_MODE': 'ip', 'AUTOSIGN_PUBLIC_HOST': '2001:db8::1'}), 'http://[2001:db8::1]:8080/login')
        self.assertEqual(access_url({'AUTOSIGN_ACCESS_MODE': 'domain', 'AUTOSIGN_DOMAIN': 'sign.example.com'}), 'https://sign.example.com/login')
        for port in ['0', '65536', 'http', '１２３']:
            with self.assertRaises(ValueError):
                access_url({'PORT': port})

    def test_summary_displays_only_valid_bootstrap_password_never_data_key(self):
        with tempfile.TemporaryDirectory() as data:
            password = 'initial-test-password'
            key = Fernet.generate_key().decode()
            app = create_app({'DATA_DIR': data, 'ADMIN_PASSWORD': password, 'ENCRYPTION_KEY': key, 'TESTING': True})
            env = {'AUTOSIGN_DATA_DIR': data, 'AUTOSIGN_ADMIN_PASSWORD': password, 'AUTOSIGN_SECRET_KEY': key}
            summary = completion(env)
            self.assertIn('初始登录密码：' + password, summary)
            self.assertIn('SSH', summary)
            self.assertNotIn(key, summary)
            store = app.extensions['store']
            auth = store.admin_auth()
            from werkzeug.security import generate_password_hash
            store.change_admin_password(auth['version'], generate_password_hash('new-login-password'), 'new-version')
            summary = completion(env)
            self.assertIn('初始密码已失效', summary)
            self.assertNotIn(password, summary)
            self.assertNotIn('new-login-password', summary)
            self.assertNotIn(key, summary)
