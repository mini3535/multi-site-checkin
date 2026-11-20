"""Real local HTTP execution checks; no real account or external host is used."""
import http.server
import json
import signal
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from autosign.executors import MAX_PAYLOAD_BYTES, execute


SECRET = "private-cookie-never-show-this"


class LocalSite(http.server.BaseHTTPRequestHandler):
    slow_hits = 0
    stop_slow = threading.Event()

    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/slow":
            type(self).slow_hits += 1
            self.send_response(200)
            self.send_header("Content-Length", "512")
            self.end_headers()
            # Keep data flowing well within the ordinary socket timeout. Only
            # the executor's total process deadline can stop this request early.
            try:
                for _ in range(512):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    if self.stop_slow.wait(0.04):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        data = json.dumps({"ok": self.headers.get("Cookie") == SECRET,
                           "private": SECRET}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        LocalSite.stop_slow.clear()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalSite)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        LocalSite.stop_slow.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def spec(self, path="/check"):
        return {"url": self.base + path, "method": "POST",
                "headers": {"Cookie": SECRET}, "body": "private=" + SECRET,
                "success_type": "json", "success_path": "ok", "success_value": "true"}

    def test_real_local_http_success_isolated_and_safe(self):
        result = execute(self.spec(), allow_private=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["status_code"], 200)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_local_http_remains_blocked_without_private_target_setting(self):
        result = execute(self.spec())
        self.assertFalse(result["ok"])
        self.assertIsNone(result["status_code"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_site_without_session_returns_needs_login(self):
        result = execute({"engine": "site", "site_id": "enshan", "cookie": ""})
        self.assertEqual(result["status"], "needs_login")
        self.assertFalse(result["ok"])

    def test_slow_real_response_hits_total_deadline_without_retry_or_secret_output(self):
        LocalSite.slow_hits = 0
        processes, launches = [], []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            launches.append((args, kwargs))
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with patch("autosign.executors.MAX_RUN_SECONDS", 0.7), patch(
                "autosign.executors.subprocess.Popen", side_effect=launch):
            result = execute(self.spec("/slow"), allow_private=True)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(LocalSite.slow_hits, 1)
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)
        self.assertTrue(launches[0][1]["start_new_session"])
        self.assertNotIn(SECRET, str(launches[0][0]))
        self.assertFalse(result["ok"])
        self.assertIn("执行结果未知", result["message"])
        self.assertIn("先核对网站状态", result["message"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_bad_child_protocol_is_fixed_safe_failure(self):
        malformed = [
            SECRET.encode(), b"[]",
            json.dumps({"ok": "true", "message": SECRET}).encode(),
            json.dumps({"ok": True, "status": [], "message": SECRET}).encode(),
            json.dumps({"ok": False, "status": "success", "message": SECRET}).encode(),
            json.dumps({"ok": True, "status": "success", "status_code": True}).encode(),
            b"x" * (MAX_PAYLOAD_BYTES + 1),
        ]
        for stdout in malformed:
            with self.subTest(size=len(stdout)):
                process = Mock(returncode=0)
                process.communicate.return_value = (stdout, SECRET.encode())
                process.poll.return_value = 0
                with patch("autosign.executors.subprocess.Popen", return_value=process):
                    result = execute(self.spec(), allow_private=True)
                self.assertFalse(result["ok"])
                self.assertIn("执行结果未知", result["message"])
                self.assertNotIn(SECRET, json.dumps(result))

    def test_child_message_and_stderr_are_never_forwarded(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({
            "ok": True, "status": "success", "status_code": 200,
            "message": SECRET}).encode(), SECRET.encode())
        process.poll.return_value = 0
        with patch("autosign.executors.subprocess.Popen", return_value=process):
            result = execute(self.spec(), allow_private=True)
        self.assertTrue(result["ok"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_browser_uses_existing_isolation(self):
        expected = {"ok": True, "status": "success"}
        with patch("autosign.browser_runner.run_browser", return_value=expected) as browser, patch(
                "autosign.executors.subprocess.Popen") as launch:
            self.assertEqual(execute({"engine": "browser"}, True), expected)
        browser.assert_called_once_with({"engine": "browser"}, allow_private=True)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
