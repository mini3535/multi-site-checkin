"""Integration checks for site setup, credential handling and run bookkeeping.

All website inputs are fixtures. These tests never contact a real website or
launch a browser, so account credentials are neither needed nor accepted.
"""
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet
from flask import template_rendered

from autosign.app import create_app
from autosign.browser_runner import validate_browser_spec
from autosign.storage import Store


PASSWORD = "sites-integration-test-password"
COOKIE_SECRET = "enshan-session-secret-do-not-display"
BROWSER_SECRET = "browser-session-secret-do-not-display"
LOCAL_SECRET = "browser-local-storage-secret-do-not-display"
UTC = timezone.utc


def session_state(value=BROWSER_SECRET):
    return {
        "cookies": [{"name": "session", "value": value,
                     "domain": ".example.com", "path": "/", "secure": True,
                     "httpOnly": True, "sameSite": "Lax", "expires": -1}],
        "origins": [{"origin": "https://example.com", "localStorage": [
            {"name": "login-token", "value": LOCAL_SECRET}]}],
    }


def browser_spec(value=BROWSER_SECRET):
    return validate_browser_spec({
        "engine": "browser", "site_id": "custom",
        "start_url": "https://example.com/daily",
        "allowed_hosts": ["example.com"], "storage_state": session_state(value),
        "steps": [{"action": "click", "selector": "#daily-checkin", "timeout_ms": 5000}],
        "success_text": "签到已完成", "already_done_text": "今天已签到",
        "login_selector": "#login-form", "verification_selector": "#captcha",
    })


class SiteAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "DATA_DIR": self.temp.name, "ADMIN_PASSWORD": PASSWORD,
            "ENCRYPTION_KEY": Fernet.generate_key().decode(), "TESTING": True,
            "ALLOW_PRIVATE_TARGETS": False,
        })
        self.client = self.app.test_client()
        self.store = self.app.extensions["store"]
        self.client.get("/login")
        response = self.client.post("/login", data={
            "password": PASSWORD, "csrf_token": self.csrf()}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session.get("csrf", "")

    def enshan_fields(self, **changes):
        fields = {
            "csrf_token": self.csrf(), "name": "恩山 · 测试账号",
            "cookie": COOKIE_SECRET, "daily_time": "08:30",
            "timezone": "Asia/Shanghai", "enabled": "on",
        }
        fields.update(changes)
        return fields

    def browser_fields(self, **changes):
        spec = browser_spec()
        fields = {
            "csrf_token": self.csrf(), "name": "论坛 · 测试账号",
            "start_url": spec["start_url"], "allowed_hosts": "example.com",
            "steps": json.dumps(spec["steps"]), "success_text": spec["success_text"],
            "already_done_text": spec["already_done_text"],
            "login_selector": spec["login_selector"],
            "verification_selector": spec["verification_selector"],
            "storage_state": json.dumps(session_state()), "daily_time": "08:30",
            "timezone": "Asia/Shanghai", "enabled": "on",
        }
        fields.update(changes)
        return fields

    def create_enshan(self, **changes):
        response = self.client.post("/sites/enshan/new", data=self.enshan_fields(**changes))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return self.store.list_tasks()[0]["id"]

    def create_browser(self, **changes):
        response = self.client.post("/sites/custom/new", data=self.browser_fields(**changes))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return self.store.list_tasks()[0]["id"]

    def assert_secret_absent(self, response, *secrets):
        for secret in secrets:
            self.assertNotIn(secret.encode(), response.data)

    def test_catalog_lists_requested_sites_and_custom_without_claiming_validation(self):
        response = self.client.get("/sites")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for site_id, name in [("nodeseek", "NodeSeek"), ("hostloc", "hostloc"),
                              ("linuxdo", "LINUX DO"), ("custom", "其他论坛或网站")]:
            with self.subTest(site=site_id):
                self.assertIn(name, page)
                if site_id == "linuxdo":
                    self.assertIn("https://linux.do/latest", page)
                    self.assertEqual(self.client.get(f"/sites/{site_id}/new").status_code, 404)
                else:
                    self.assertIn(f'/sites/{site_id}/new', page)
                    self.assertEqual(self.client.get(f"/sites/{site_id}/new").status_code, 200)
        for old_name in ("恩山无线论坛", "京东京豆", "什么值得买"):
            self.assertNotIn(old_name, page)
        self.assertIn("待配置与验证", page)
        self.assertIn("尚未确认有独立签到按钮", page)
        self.assertIn("浏览器流程 · 待配置与验证", page)
        self.assertIn("浏览器流程 · 待配置与验证", page)
        self.assertEqual(self.client.get("/sites/unknown/new").status_code, 404)

    def test_sites_require_authentication_and_post_csrf(self):
        outsider = self.app.test_client()
        for path in ("/sites", "/sites/enshan/new", "/sites/custom/new"):
            with self.subTest(path=path):
                response = outsider.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/login"))
        fields = self.enshan_fields()
        fields.pop("csrf_token")
        self.assertEqual(self.client.post("/sites/enshan/new", data=fields).status_code, 400)
        self.assertEqual(self.store.list_tasks(), [])

    def test_enshan_cookie_is_encrypted_and_never_echoed_in_pages(self):
        task_id = self.create_enshan()
        task = self.store.get_task(task_id)
        spec = self.store.spec(task)
        self.assertEqual(spec, {"engine": "site", "site_id": "enshan", "cookie": COOKIE_SECRET})
        self.assertNotIn(COOKIE_SECRET, task["sealed_spec"])
        self.assertNotIn(COOKIE_SECRET.encode(), self.store.path.read_bytes())
        for path in ("/", f"/tasks/{task_id}/site-edit"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assert_secret_absent(response, COOKIE_SECRET)

    def test_enshan_blank_edit_preserves_session_and_updates_metadata(self):
        task_id = self.create_enshan()
        response = self.client.post(f"/tasks/{task_id}/site-edit", data=self.enshan_fields(
            cookie="", name="恩山 · 改名", daily_time="10:15"))
        self.assertEqual(response.status_code, 302)
        task = self.store.get_task(task_id)
        self.assertEqual(task["name"], "恩山 · 改名")
        self.assertEqual(task["daily_time"], "10:15")
        self.assertEqual(self.store.spec(task)["cookie"], COOKIE_SECRET)

    def test_invalid_enshan_cookie_error_does_not_echo_raw_cookie(self):
        response = self.client.post("/sites/enshan/new", data=self.enshan_fields(
            cookie=COOKIE_SECRET + "\r\nnot-a-header"))
        self.assertEqual(response.status_code, 400)
        self.assert_secret_absent(response, COOKIE_SECRET)
        self.assertEqual(self.store.list_tasks(), [])

    def test_new_enshan_requires_nonempty_normalized_cookie(self):
        for cookie in ("", "   "):
            with self.subTest(cookie=repr(cookie)):
                response = self.client.post("/sites/enshan/new", data=self.enshan_fields(cookie=cookie))
                self.assertEqual(response.status_code, 400)
                self.assertIn("新建网站任务时", response.get_data(as_text=True))
        self.assertEqual(self.store.list_tasks(), [])

    def test_clearing_enshan_session_removes_cookie_and_pauses_task(self):
        task_id = self.create_enshan()
        response = self.client.post(f"/tasks/{task_id}/site-edit", data=self.enshan_fields(
            cookie="", clear_session="1", enabled="on"))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        task = self.store.get_task(task_id)
        self.assertFalse(task["enabled"])
        self.assertEqual(self.store.spec(task)["cookie"], "")
        page = self.client.get(f"/tasks/{task_id}/site-edit").get_data(as_text=True)
        self.assertNotIn('class="credential-note"', page)
        self.assertNotIn("已保存登录会话", page)

    def test_browser_multipart_state_upload_is_encrypted_and_blank_edit_preserves_it(self):
        fields = self.browser_fields(storage_state="")
        fields["storage_state_file"] = (
            io.BytesIO(json.dumps(session_state()).encode()), "account-session.json")
        response = self.client.post("/sites/custom/new", data=fields,
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        task = self.store.list_tasks()[0]
        task_id = task["id"]
        before = self.store.spec(task)
        self.assertEqual(before["storage_state"]["cookies"][0]["value"], BROWSER_SECRET)
        self.assertEqual(before["storage_state"]["origins"][0]["localStorage"][0]["value"], LOCAL_SECRET)
        for secret in (BROWSER_SECRET, LOCAL_SECRET):
            self.assertNotIn(secret, task["sealed_spec"])
            self.assertNotIn(secret.encode(), self.store.path.read_bytes())
        for path in ("/", f"/tasks/{task_id}/site-edit"):
            self.assert_secret_absent(self.client.get(path), BROWSER_SECRET, LOCAL_SECRET)
        response = self.client.post(f"/tasks/{task_id}/site-edit", data=self.browser_fields(
            storage_state="", name="论坛 · 改名"), content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        current = self.store.get_task(task_id)
        self.assertEqual(current["name"], "论坛 · 改名")
        self.assertEqual(self.store.spec(current), before)

    def test_bad_session_json_and_conflicting_inputs_never_echo_session_material(self):
        cases = [
            {"storage_state": '{"cookies":' + BROWSER_SECRET},
            {"storage_state": "", "storage_state_file": (
                io.BytesIO(("invalid " + BROWSER_SECRET).encode()), "session.json")},
            {"storage_state_file": (io.BytesIO(json.dumps(session_state()).encode()), "session.json")},
            {"steps": "not JSON", "storage_state": json.dumps(session_state())},
        ]
        for changes in cases:
            with self.subTest(changes=list(changes)):
                response = self.client.post("/sites/custom/new", data=self.browser_fields(**changes),
                                            content_type="multipart/form-data")
                self.assertEqual(response.status_code, 400)
                self.assert_secret_absent(response, BROWSER_SECRET, LOCAL_SECRET)
        self.assertEqual(self.store.list_tasks(), [])

    def test_new_browser_requires_session_after_filtering_unrelated_domains(self):
        unrelated_cookie = session_state()
        unrelated_cookie["cookies"][0]["domain"] = ".unrelated.example"
        unrelated_cookie["origins"] = []
        unrelated_origin = {"cookies": [], "origins": [{
            "origin": "https://unrelated.example", "localStorage": [
                {"name": "session", "value": BROWSER_SECRET}]}]}
        for state in ("", "{}", '{"cookies": [], "origins": []}',
                      json.dumps(unrelated_cookie), json.dumps(unrelated_origin)):
            with self.subTest(state=state[:40]):
                response = self.client.post("/sites/custom/new", data=self.browser_fields(storage_state=state))
                self.assertEqual(response.status_code, 400)
                self.assertIn("新建网站任务时", response.get_data(as_text=True))
                self.assert_secret_absent(response, BROWSER_SECRET, LOCAL_SECRET)
        self.assertEqual(self.store.list_tasks(), [])

    def test_new_browser_accepts_origin_only_login_state(self):
        state = session_state()
        state["cookies"] = []
        task_id = self.create_browser(storage_state=json.dumps(state))
        saved = self.store.spec(self.store.get_task(task_id))["storage_state"]
        self.assertEqual(saved["cookies"], [])
        self.assertTrue(saved["origins"])

    def test_clearing_browser_session_removes_state_and_pauses_task(self):
        task_id = self.create_browser()
        response = self.client.post(f"/tasks/{task_id}/site-edit", data=self.browser_fields(
            storage_state="", clear_session="1", enabled="on"))
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        task = self.store.get_task(task_id)
        self.assertFalse(task["enabled"])
        self.assertEqual(self.store.spec(task)["storage_state"], {"cookies": [], "origins": []})
        page = self.client.get(f"/tasks/{task_id}/site-edit").get_data(as_text=True)
        self.assertNotIn('class="credential-note"', page)
        self.assertNotIn("已保存登录会话", page)
        for field in ("success_text", "already_done_text"):
            self.assertRegex(page, rf'id="{field}"[^>]*maxlength="256"')

    def test_edit_routes_preserve_existing_browser_and_legacy_http_tasks(self):
        browser_id = self.create_browser()
        browser_before = self.store.spec(self.store.get_task(browser_id))
        response = self.client.get(f"/tasks/{browser_id}/edit")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith(f"/tasks/{browser_id}/site-edit"))
        http_fields = {
            "csrf_token": self.csrf(), "name": "旧版 HTTP 任务", "url": "https://example.com/checkin",
            "method": "POST", "headers": json.dumps({"Cookie": COOKIE_SECRET}), "body": "",
            "success_type": "json", "success_path": "ok", "success_value": "true",
            "daily_time": "08:30", "timezone": "Asia/Shanghai", "enabled": "on",
        }
        self.assertEqual(self.client.post("/tasks/new", data=http_fields).status_code, 302)
        http_id = self.store.list_tasks()[0]["id"]
        http_before = self.store.spec(self.store.get_task(http_id))
        # A POST to the wrong kind of editor redirects before changing the task.
        response = self.client.post(f"/tasks/{browser_id}/edit", data=http_fields)
        self.assertEqual(response.status_code, 303)
        response = self.client.post(f"/tasks/{http_id}/site-edit", data=self.enshan_fields())
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith(f"/tasks/{http_id}/edit"))
        self.assertEqual(self.store.spec(self.store.get_task(browser_id)), browser_before)
        self.assertEqual(self.store.spec(self.store.get_task(http_id)), http_before)
        self.assertEqual(self.client.get(f"/tasks/{http_id}/edit").status_code, 200)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn(f'/tasks/{browser_id}/site-edit', page)
        self.assertIn(f'/tasks/{http_id}/edit', page)

    def test_dashboard_counts_already_done_as_success_and_labels_attention_states(self):
        records = []
        for status in ("success", "already_done", "needs_login", "needs_verification", "unsupported"):
            task_id = self.create_enshan(name=status)
            self.store.enqueue_manual(task_id)
            run, _task = self.store.claim_next()
            self.store.finish(run["id"], {
                "ok": status in ("success", "already_done"), "status": status,
                "message": "安全结果说明", "duration_ms": 1, "status_code": None,
            })

        def capture(_sender, template, context, **_extra):
            records.append((template.name, dict(context)))

        with template_rendered.connected_to(capture, self.app):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(records[-1][1]["stats"], {"total": 5, "enabled": 3, "success": 2, "failed": 3})
        page = response.get_data(as_text=True)
        for label in ("今日已签到", "需要重新登录", "需要人工验证", "需要调整配置"):
            self.assertIn(label, page)


class SiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, Fernet.generate_key().decode())
        self.now = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
        self.fields = {"name": "站点任务", "daily_time": "08:30", "timezone": "Asia/Shanghai", "enabled": True}

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **changes):
        fields = dict(self.fields, **changes)
        return self.store.save_task(fields, browser_spec(), now=self.now)

    def finish(self, task_id, status, ok=None):
        self.assertTrue(self.store.enqueue_manual(task_id))
        with patch("autosign.schedule.utcnow", return_value=self.now):
            run, _task = self.store.claim_next()
            self.store.finish(run["id"], {
                "ok": status in ("success", "already_done") if ok is None else ok,
                "status": status, "message": "固定测试结果", "duration_ms": 4,
                "status_code": None,
            })
        return run["id"]

    def test_result_categories_preserve_status_and_pause_only_login_or_verification(self):
        for status in ("success", "already_done", "needs_login", "needs_verification", "unsupported", "failed"):
            with self.subTest(status=status):
                task_id = self.save(name=status)
                run_id = self.finish(task_id, status)
                result = next(r for r in self.store.recent_runs() if r["id"] == run_id)
                self.assertEqual(result["status"], status)
                self.assertEqual(bool(self.store.get_task(task_id)["enabled"]),
                                 status not in ("needs_login", "needs_verification"))
        # Inconsistent executor flags must never become a successful record.
        task_id = self.save(name="不一致结果")
        self.finish(task_id, "already_done", ok=False)
        self.assertEqual(self.store.latest_runs()[task_id]["status"], "failed")

    def test_already_done_suppresses_same_day_automatic_run_but_allows_tomorrow(self):
        task_id = self.save()
        self.finish(task_id, "already_done")
        due = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.store.enqueue_due(due)
        self.store.enqueue_due(datetime(2026, 11, 19, 5, 0, tzinfo=UTC))
        self.assertEqual(len(self.store.recent_runs()), 1)
        self.assertEqual(self.store.get_task(task_id)["next_run_at"], "2026-11-20T00:30:00+00:00")
        self.store.enqueue_due(datetime(2026, 11, 20, 0, 30, tzinfo=UTC))
        self.assertEqual(len(self.store.recent_runs()), 2)
        self.assertEqual(self.store.recent_runs()[0]["slot"], "2026-11-20")
        self.assertEqual(self.store.recent_runs()[0]["status"], "queued")

    def test_refreshed_session_preserves_schedule_and_rejects_stale_snapshot(self):
        task_id = self.save()
        snapshot = self.store.get_task(task_id)
        self.store.refresh_session(snapshot, session_state("rotated-session-value"))
        rotated = self.store.get_task(task_id)
        self.assertEqual(rotated["next_run_at"], snapshot["next_run_at"])
        self.assertEqual(rotated["daily_time"], snapshot["daily_time"])
        self.assertEqual(rotated["enabled"], snapshot["enabled"])
        self.assertEqual(self.store.spec(rotated)["storage_state"]["cookies"][0]["value"], "rotated-session-value")
        self.assertNotIn("rotated-session-value", rotated["sealed_spec"])

        # Simulate an administrator replacing credentials while that browser run
        # still holds its original snapshot. The old run cannot overwrite them.
        admin_fields = dict(self.fields, name="管理员更新", daily_time="11:45", enabled=False)
        self.store.save_task(admin_fields, browser_spec("new-admin-session"), task_id, now=self.now)
        admin_task = self.store.get_task(task_id)
        self.store.refresh_session(rotated, session_state("late-browser-session"))
        current = self.store.get_task(task_id)
        self.assertEqual(current["sealed_spec"], admin_task["sealed_spec"])
        self.assertEqual(current["next_run_at"], admin_task["next_run_at"])
        self.assertEqual(current["name"], "管理员更新")
        self.assertFalse(current["enabled"])
        self.assertEqual(self.store.spec(current)["storage_state"]["cookies"][0]["value"], "new-admin-session")


if __name__ == "__main__":
    unittest.main()
