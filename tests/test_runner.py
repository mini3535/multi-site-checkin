"""Exercise real HTTP behavior without contacting any external website."""

import http.server
import json
import socket
import threading
import unittest
from unittest.mock import patch

from autosign.runner import MAX_RESPONSE_BYTES, run_request, validate_request


SECRET = "secret-cookie-that-must-not-appear-in-results"


class Site(http.server.BaseHTTPRequestHandler):
    redirect_hits = 0

    def log_message(self, _format, *_args):
        pass

    def reply(self, status, data, **headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/json":
            body = json.dumps({"data": {"checked": True}, "items": [{"ok": 1}], "secret": SECRET}).encode()
            self.reply(200, body, Content_Type="application/json; charset=utf-8")
        elif self.path == "/text":
            self.reply(200, ("签到成功 " + SECRET).encode(), Content_Type="text/plain; charset=utf-8")
        elif self.path == "/failure":
            self.reply(401, SECRET.encode())
        elif self.path == "/redirect":
            self.reply(302, b"", Location="/destination")
        elif self.path == "/destination":
            Site.redirect_hits += 1
            self.reply(200, b"ok")
        elif self.path == "/large":
            self.reply(200, b"x" * (MAX_RESPONSE_BYTES + 1))
        elif self.path == "/cookie":
            data = b"ok" if self.headers.get("Cookie") == SECRET else b"no"
            self.reply(200, data)
        elif self.path == "/compressed":
            self.reply(200, b"fake gzip", Content_Encoding="gzip")
        else:
            self.reply(200, b"please login")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.reply(200, body, Content_Type="application/json")


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def spec(self, path="/text", **changes):
        value = {
            "url": self.base + path, "method": "GET", "headers": {}, "body": "",
            "success_type": "text", "success_value": "签到成功", "success_path": "",
        }
        value.update(changes)
        return value

    def test_text_condition_and_no_response_leak(self):
        result = run_request(self.spec(), allow_private=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(set(result), {"ok", "status_code", "message", "duration_ms"})

    def test_json_nested_path_and_list(self):
        for path, expected in [("data.checked", "true"), ("items.0.ok", "1")]:
            with self.subTest(path=path):
                result = run_request(self.spec("/json", success_type="json", success_path=path, success_value=expected), True)
                self.assertTrue(result["ok"])

    def test_json_true_is_not_number_one(self):
        result = run_request(self.spec("/json", success_type="json", success_path="data.checked", success_value="1"), True)
        self.assertFalse(result["ok"])

    def test_post_preserves_json_body(self):
        result = run_request(self.spec("/echo", method="POST", body='{"ok":true}', headers={"Content-Type": "application/json"}, success_type="json", success_path="ok", success_value="true"), True)
        self.assertTrue(result["ok"])

    def test_200_does_not_imply_success(self):
        result = run_request(self.spec("/login"), True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 200)

    def test_json_missing_path_or_invalid_response_fails(self):
        for endpoint, path in [("/json", "data.missing"), ("/text", "data.checked")]:
            with self.subTest(endpoint=endpoint):
                self.assertFalse(run_request(self.spec(endpoint, success_type="json", success_path=path, success_value="true"), True)["ok"])

    def test_http_error_and_redirect_are_failures(self):
        Site.redirect_hits = 0
        for path, code in [("/failure", 401), ("/redirect", 302)]:
            result = run_request(self.spec(path), True)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status_code"], code)
            self.assertNotIn(SECRET, result["message"])
        self.assertEqual(Site.redirect_hits, 0)

    def test_large_response_rejected(self):
        result = run_request(self.spec("/large"), True)
        self.assertFalse(result["ok"])
        self.assertIn("1 MiB", result["message"])

    def test_cookie_can_be_configured_without_logging_it(self):
        result = run_request(self.spec("/cookie", headers={"Cookie": SECRET}, success_value="ok"), True)
        self.assertTrue(result["ok"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_private_target_blocked_by_default(self):
        result = run_request(self.spec())
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status_code"])
        self.assertIn("公网", result["message"])

    def test_mixed_dns_answer_blocked(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80)),
        ]
        with patch("autosign.runner.socket.getaddrinfo", return_value=answers):
            result = run_request(self.spec())
        self.assertFalse(result["ok"])
        self.assertIn("公网", result["message"])

    def test_multicast_blocked(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("224.0.0.1", 80))]
        with patch("autosign.runner.socket.getaddrinfo", return_value=answers):
            self.assertFalse(run_request(self.spec())["ok"])

    def test_socket_uses_validated_address_without_dns_again(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80))
        with patch("autosign.runner.socket.getaddrinfo", return_value=[public]) as resolve, patch("autosign.runner._connect_pinned", side_effect=OSError()) as connect:
            result = run_request(self.spec(url="http://example.test/path"))
        self.assertFalse(result["ok"])
        self.assertEqual(resolve.call_count, 1)
        connect.assert_called_once_with(public, 15)

    def test_compressed_response_not_accepted(self):
        self.assertFalse(run_request(self.spec("/compressed"), True)["ok"])

    def test_validation_is_offline_and_normalizes_unicode_path(self):
        with patch("autosign.runner.socket.getaddrinfo", side_effect=AssertionError("validation used DNS")):
            normalized = validate_request(self.spec(url="https://example.com/签到?q=你好"))
        self.assertIn("%E7%AD%BE", normalized["url"])

    def test_unsafe_or_missing_configuration_raises_value_error(self):
        bad = [
            {"success_value": ""}, {"success_type": []},
            {"success_type": "json", "success_value": "NaN"},
            {"success_type": "json", "success_path": "data..ok", "success_value": "true"},
            {"url": "file:///etc/passwd"}, {"url": "http://user:pass@example.com/"},
            {"url": "https://example.com/#part"}, {"url": "https://example.com:99999/"},
            {"headers": {"Cookie": "hello\r\nInjected: yep"}},
            {"headers": {"Host": "example.com"}}, {"headers": {"Accept-Encoding": "gzip"}},
            {"headers": {"cookie": "a", "Cookie": "b"}}, {"body": "forbidden on GET"},
            {"method": "DELETE"}, {"method": "POST", "body": "x" * 65537},
        ]
        for changes in bad:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ValueError):
                    validate_request(self.spec(**changes))


if __name__ == "__main__":
    unittest.main()
