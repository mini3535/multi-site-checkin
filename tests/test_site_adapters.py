import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from autosign.site_adapters import (ENSHAN_PAGE, ENSHAN_SIGN, _fetch,
                                    run_site, validate_site_spec)


COOKIE = "session=private-cookie-value"
SPEC = {"engine": "site", "site_id": "enshan", "cookie": COOKIE}
PAGE = '<html><input type="hidden" value="a1b2c3d4" name="formhash"></html>'


def response(body, status=200, headers=None):
    return {"status_code": status, "headers": headers or {}, "body": body}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("Unexpected request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class SiteAdapterTests(unittest.TestCase):
    def test_spec_validation_is_offline_and_preserves_only_allowed_fields(self):
        with patch('autosign.site_adapters._resolve', side_effect=AssertionError):
            spec = validate_site_spec({**SPEC, "url": "http://127.0.0.1/", "cookie": "  " + COOKIE + "  "})
        self.assertEqual(spec, SPEC)
        for cookie in (None, 7, "a=1\r\nHost: evil", "a=1\tb=2", "x" * 16385, "中文"):
            with self.subTest(cookie_type=type(cookie).__name__), self.assertRaises(ValueError):
                validate_site_spec({**SPEC, "cookie": cookie})
        for spec in ({**SPEC, "site_id": "jd"}, {**SPEC, "engine": "browser"}, []):
            with self.assertRaises(ValueError):
                validate_site_spec(spec)

    def test_cleared_cookie_normalizes_and_needs_login_without_network(self):
        for cookie in ("", " ", "   "):
            with self.subTest(cookie=repr(cookie)):
                normalized = validate_site_spec({**SPEC, "cookie": cookie})
                self.assertEqual(normalized["cookie"], "")
                transport = FakeTransport()
                with patch('autosign.site_adapters._resolve', side_effect=AssertionError('must not resolve')):
                    result = run_site(normalized, transport=transport)
                self.assertEqual(result["status"], "needs_login")
                self.assertFalse(result["ok"])
                self.assertIsNone(result["status_code"])
                self.assertEqual(transport.calls, [])

    def test_two_step_signin_and_fixed_origin_safe_headers(self):
        transport = FakeTransport(response(PAGE), response(json.dumps({"success": True, "message": "签到成功", "continuous_days": 3})))
        result = run_site({**SPEC, "url": "https://malicious.invalid/"}, transport=transport)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["status_code"], 200)
        self.assertEqual([(call[0], call[1]) for call in transport.calls], [("GET", ENSHAN_PAGE), ("POST", ENSHAN_SIGN)])
        for method, url, headers, body in transport.calls:
            self.assertEqual(headers["Cookie"], COOKIE)
            self.assertEqual(headers["Referer"], ENSHAN_PAGE)
            self.assertNotIn("Host", headers)
            self.assertNotIn("Connection", headers)
        self.assertEqual(parse_qs(transport.calls[1][3]), {"formhash": ["a1b2c3d4"]})
        self.assertNotIn(COOKIE, json.dumps(result))
        self.assertNotIn("continuous_days", result["message"])

    def test_javascript_formhash_is_supported(self):
        transport = FakeTransport(response("<script>var formhash = 'nonce_9';</script>"), response('{"success":true}'))
        self.assertEqual(run_site(SPEC, transport=transport)["status"], "success")
        self.assertEqual(parse_qs(transport.calls[1][3]), {"formhash": ["nonce_9"]})

    def test_already_done_requires_explicit_today_phrase(self):
        for message in ("今日已签到", "今天已经签到", "您今天已经签到过了，请明天再来", "今日已签到。"):
            with self.subTest(message=message):
                transport = FakeTransport(response(PAGE), response(json.dumps({"success": False, "message": message})))
                result = run_site(SPEC, transport=transport)
                self.assertEqual(result["status"], "already_done")
                self.assertTrue(result["ok"])
        for message in ("签到", "未签到", "今日签到失败", "未签到成功", "不是今日已签到"):
            with self.subTest(message=message):
                transport = FakeTransport(response(PAGE), response(json.dumps({"success": False, "message": message})))
                result = run_site(SPEC, transport=transport)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["ok"])

    def test_login_expiration_stops_before_post(self):
        for page in ('<form id="loginform"></form>', "请先登录后再操作", "登录已失效"):
            with self.subTest(page=page):
                transport = FakeTransport(response(page))
                result = run_site(SPEC, transport=transport)
                self.assertEqual(result["status"], "needs_login")
                self.assertFalse(result["ok"])
                self.assertEqual(len(transport.calls), 1)
        transport = FakeTransport(response("", 302, {"Location": "member.php?mod=logging&action=login"}))
        self.assertEqual(run_site(SPEC, transport=transport)["status"], "needs_login")
        self.assertEqual(len(transport.calls), 1)

    def test_captcha_and_current_waf_markers_stop_before_post(self):
        for page in ("请完成人机验证", "请输入验证码", '<script>CF_APP_WAF={};</script>', '<div id="waf-nc-h5-mask">Request ID</div>'):
            with self.subTest(page=page):
                transport = FakeTransport(response(page))
                result = run_site(SPEC, transport=transport)
                self.assertEqual(result["status"], "needs_verification")
                self.assertEqual(len(transport.calls), 1)

    def test_post_json_login_and_verification_message_are_classified(self):
        for message, expected in (("您尚未登录", "needs_login"), ("请完成验证码", "needs_verification")):
            transport = FakeTransport(response(PAGE), response(json.dumps({"success": False, "message": message})))
            self.assertEqual(run_site(SPEC, transport=transport)["status"], expected)

    def test_missing_formhash_is_unsupported_without_post(self):
        transport = FakeTransport(response("<html>forum has changed</html>"))
        result = run_site(SPEC, transport=transport)
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(len(transport.calls), 1)

    def test_unknown_or_malformed_json_never_implies_success(self):
        for payload in ('not-json', '[]', '{}', '{"success":1}', '{"success":"true"}', '{"message":"签到成功"}'):
            with self.subTest(payload=payload):
                transport = FakeTransport(response(PAGE), response(payload))
                result = run_site(SPEC, transport=transport)
                self.assertEqual(result["status"], "unsupported")
                self.assertFalse(result["ok"])

    def test_error_status_and_unexpected_redirect_are_not_success(self):
        for reply in (response('{"success":true}', 500), response("", 302, {"Location": "https://elsewhere.invalid/"})):
            transport = FakeTransport(response(PAGE), reply)
            result = run_site(SPEC, transport=transport)
            self.assertFalse(result["ok"])
            self.assertIn(result["status"], {"failed", "unsupported"})
            self.assertEqual(len(transport.calls), 2)

    def test_exception_and_response_secrets_are_not_exposed(self):
        secret = "private-remote-body-" + COOKIE
        for fault in (TimeoutError(secret), OSError(secret), ValueError(secret), RuntimeError(secret)):
            transport = FakeTransport(response(PAGE), fault)
            result = run_site(SPEC, transport=transport)
            self.assertFalse(result["ok"])
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(COOKIE, json.dumps(result))
        transport = FakeTransport(response(PAGE), response(json.dumps({"success": False, "message": secret})))
        self.assertNotIn(secret, json.dumps(run_site(SPEC, transport=transport)))

    def test_response_size_cap_and_fixed_transport_destination(self):
        transport = FakeTransport(response("a" * (1024 * 1024 + 1)))
        self.assertEqual(run_site(SPEC, transport=transport)["status"], "unsupported")
        with patch('autosign.site_adapters._resolve', side_effect=AssertionError('must not resolve')):
            with self.assertRaises(ValueError):
                _fetch("GET", "https://malicious.invalid/", {}, "")


if __name__ == '__main__':
    unittest.main()
