"""Integration checks for site setup, credential handling and run bookkeeping.

All website inputs are fixtures. These tests never contact a real website or
launch a browser, so account credentials are neither needed nor accepted.
"""
import io
import json
import tempfile
import unittest
import zipfile
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

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as session:
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
        self.assertIn("从当前网页自动识别并添加", page)
        self.assertIn("浏览器流程 · 待配置与验证", page)
        self.assertEqual(self.client.get("/sites/unknown/new").status_code, 404)

    def test_quick_add_prefills_an_unlisted_https_website_without_creating_a_task(self):
        response = self.client.post("/sites/quick-add", data={
            "csrf_token": self.csrf(), "website_url": "forum.example.com/checkin",
        })
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('value="https://forum.example.com/checkin"', page)
        self.assertIn(">forum.example.com</textarea>", page)
        self.assertIn('value="https://forum.example.com/"', page)
        self.assertIn('value="forum.example.com · 主账号"', page)
        self.assertIn("签到按钮文字", page)
        self.assertEqual(self.store.list_tasks(), [])

    def test_extension_download_is_authenticated_and_matches_current_interface(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get("/downloads/signin-session-exporter.zip").status_code, 302)

        response = self.client.get("/downloads/signin-session-exporter.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn("attachment; filename=signin-session-exporter.zip", response.headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            popup = archive.read("popup.html").decode()
        self.assertEqual(manifest["version"], "1.3.0")
        self.assertIn("识别网页并连接账号", popup)

    def test_quick_add_recognizes_supported_platform_and_site_templates(self):
        platform = self.client.post("/sites/quick-add", data={
            "csrf_token": self.csrf(), "website_url": "workbuddy.cn",
        })
        self.assertEqual(platform.status_code, 200)
        self.assertIn("WorkBuddy", platform.get_data(as_text=True))
        self.assertIn("Access Token", platform.get_data(as_text=True))

        forum = self.client.post("/sites/quick-add", data={
            "csrf_token": self.csrf(), "website_url": "nodeseek.com/board",
        })
        self.assertEqual(forum.status_code, 200)
        self.assertIn('value="https://nodeseek.com/board"', forum.get_data(as_text=True))
        self.assertIn("NodeSeek浏览器流程需要配置", forum.get_data(as_text=True))
        self.assertEqual(self.store.list_tasks(), [])

    def test_quick_add_keeps_known_information_only_sites_explicit(self):
        response = self.client.post("/sites/quick-add", data={
            "csrf_token": self.csrf(), "website_url": "linux.do/latest",
        })
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("目前没有确认可自动执行的独立签到", page)
        self.assertIn('value="https://linux.do/latest"', page)

    def test_quick_add_rejects_non_https_credentials_and_nonstandard_ports(self):
        for website_url in ("http://forum.example.com", "https://user:quick-add-secret@example.com",
                            "https://forum.example.com:8443/checkin"):
            with self.subTest(website_url=website_url):
                response = self.client.post("/sites/quick-add", data={
                    "csrf_token": self.csrf(), "website_url": website_url,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("有效的 HTTPS 网站地址", response.get_data(as_text=True))
                self.assertNotIn(b"quick-add-secret", response.data)
        self.assertEqual(self.store.list_tasks(), [])

    def test_browser_pairing_imports_once_and_suggests_page_text_without_echoing_session(self):
        start = self.client.post("/browser-session-pairings", data={
            "csrf_token": self.csrf(), "start_url": "https://example.com/daily",
            "allowed_hosts": "example.com",
        })
        self.assertEqual(start.status_code, 200, start.get_data(as_text=True))
        pairing = start.get_json()
        waiting = self.client.get(f"/browser-session-pairings/{pairing['id']}")
        self.assertEqual(waiting.get_json()["status"], "waiting")

        secret_state = session_state("pairing-secret-must-not-be-returned")
        complete = self.client.post("/api/browser-session-pairings/complete", json={
            "code": pairing["code"], "page_url": "https://example.com/daily",
            "storage_state": secret_state,
            "hints": {"button_texts": ["每日签到"], "success_texts": ["签到成功"],
                       "already_done_texts": ["今天已签到"]},
        })
        self.assertEqual(complete.status_code, 201, complete.get_data(as_text=True))
        self.assertNotIn("pairing-secret-must-not-be-returned", complete.get_data(as_text=True))
        connected = self.client.get(f"/browser-session-pairings/{pairing['id']}").get_json()
        self.assertEqual(connected["status"], "connected")
        self.assertEqual(connected["hints"]["button_texts"], ["每日签到"])
        self.assertEqual(connected["hints"]["success_texts"], ["签到成功"])
        self.assertEqual(connected["hints"]["already_done_texts"], ["今天已签到"])

        fields = self.browser_fields(storage_state="", browser_pairing_id=pairing["id"],
                                     connection_method="password", website_username="",
                                     website_password="", steps="[]", button_text="每日签到")
        saved = self.client.post("/sites/custom/new", data=fields)
        self.assertEqual(saved.status_code, 302, saved.get_data(as_text=True))
        task = self.store.get_task(self.store.list_tasks()[0]["id"])
        spec = self.store.spec(task)
        self.assertEqual(spec["storage_state"]["cookies"][0]["value"], "pairing-secret-must-not-be-returned")
        self.assertFalse(task["enabled"])
        self.assertEqual(self.client.get(f"/browser-session-pairings/{pairing['id']}").get_json()["status"], "expired")

    def test_auto_discovery_creates_paused_browser_task_from_current_page(self):
        start = self.client.post("/sites/auto-add", data={"csrf_token": self.csrf()})
        self.assertEqual(start.status_code, 200, start.get_data(as_text=True))
        pairing = start.get_json()
        self.assertEqual(self.client.get(f"/browser-session-pairings/{pairing['id']}").get_json()["status"], "waiting")

        secret = "auto-discovery-session-secret"
        payload = {
            "code": pairing["code"],
            "page_url": "https://forum.example.com/daily?tab=today",
            "page_title": "Example Forum - Daily Check-in",
            "storage_state": session_state(secret),
            "hints": {"button_texts": ["每日签到"], "success_texts": [],
                      "already_done_texts": ["今天已签到"],
                      "resource_hosts": ["api.example.com", "cdn.unrelated.test", "bad host"]},
        }
        extension = self.app.test_client()
        connected = extension.post("/api/browser-session-pairings/complete", json=payload)
        self.assertEqual(connected.status_code, 201, connected.get_data(as_text=True))
        result = connected.get_json()
        self.assertTrue(result["task_created"])
        self.assertNotIn(secret, connected.get_data(as_text=True))
        task = self.store.get_task(result["task_id"])
        spec = self.store.spec(task)
        self.assertEqual(task["name"], "Example Forum - Daily Check-in · 签到")
        self.assertNotIn(secret, task["sealed_spec"])
        self.assertFalse(task["enabled"])
        self.assertEqual(spec["start_url"], payload["page_url"])
        self.assertEqual(spec["allowed_hosts"], ["forum.example.com", "api.example.com"])
        self.assertEqual(spec["steps"][0]["selector"], 'button:has-text("每日签到")')
        self.assertEqual(spec["success_text"], "")
        self.assertTrue(spec["auto_success"])
        self.assertEqual(spec["already_done_text"], "今天已签到")
        self.assertEqual(spec["storage_state"]["cookies"][0]["value"], secret)
        status = self.client.get(f"/browser-session-pairings/{pairing['id']}").get_json()
        self.assertEqual(status["task_id"], result["task_id"])
        self.assertEqual(status["verify_url"], f"/tasks/{result['task_id']}/verify")

        replay = extension.post("/api/browser-session-pairings/complete", json=payload)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.get_json()["task_id"], result["task_id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_auto_discovery_without_button_adds_task_that_requires_rule_review(self):
        start = self.client.post("/sites/auto-add", data={"csrf_token": self.csrf()})
        pairing = start.get_json()
        extension = self.app.test_client()
        connected = extension.post("/api/browser-session-pairings/complete", json={
            "code": pairing["code"], "page_url": "https://forum.example.com/",
            "page_title": "Example Forum", "storage_state": session_state(),
            "hints": {"button_texts": [], "success_texts": [], "already_done_texts": []},
        })
        self.assertEqual(connected.status_code, 201, connected.get_data(as_text=True))
        task_id = connected.get_json()["task_id"]
        spec = self.store.spec(self.store.get_task(task_id))
        self.assertEqual(spec["steps"], [])
        verify = self.client.get(f"/tasks/{task_id}/verify")
        self.assertEqual(verify.status_code, 200)
        self.assertIn("没有找到可用的签到按钮", verify.get_data(as_text=True))

    def test_browser_pairing_is_scoped_to_login_and_cleared_on_logout(self):
        start = self.client.post("/browser-session-pairings", data={
            "csrf_token": self.csrf(), "start_url": "https://example.com/daily",
            "allowed_hosts": "example.com",
        })
        pairing = start.get_json()
        with self.client.session_transaction() as browser_session:
            owner = browser_session["login_owner"]
        outsider = self.app.test_client()
        outsider.get("/login")
        outsider.post("/login", data={"csrf_token": self.csrf(outsider), "password": PASSWORD})
        self.assertEqual(outsider.get(f"/browser-session-pairings/{pairing['id']}").get_json()["status"], "expired")
        self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.assertEqual(self.app.extensions["session_pairings"].status(pairing["id"], owner)["status"], "expired")

    def test_sites_require_authentication_and_post_csrf(self):
        outsider = self.app.test_client()
        for path in ("/sites", "/sites/enshan/new", "/sites/custom/new", "/sites/quick-add", "/sites/auto-add"):
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

    def test_simple_custom_form_derives_host_and_button_step(self):
        fields = self.browser_fields(allowed_hosts="", steps="[]", button_text="每日签到", enabled="")
        response = self.client.post("/sites/custom/new", data=fields)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        task = self.store.list_tasks()[0]
        spec = self.store.spec(task)
        self.assertEqual(spec["allowed_hosts"], ["example.com"])
        self.assertEqual(spec["steps"], [{
            "action": "click", "selector": 'button:has-text("每日签到")',
            "timeout_ms": 10000,
        }])
        self.assertFalse(task["enabled"])

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
        task_ids = {}
        for status in ("success", "already_done", "needs_login", "needs_verification", "unsupported"):
            task_id = self.create_enshan(name=status)
            task_ids[status] = task_id
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
        # Verification never implicitly opts a new website into scheduling.
        self.assertEqual(records[-1][1]["stats"], {"total": 5, "enabled": 0, "success": 2, "failed": 3, "attention": 3})
        page = response.get_data(as_text=True)
        for label in ("今日已签到", "需要重新登录", "需要人工验证", "需要调整配置"):
            self.assertIn(label, page)
        self.assertIn('data-task-filter="attention"', page)
        self.assertIn('data-task-search', page)
        self.assertIn(f"/tasks/{task_ids['needs_login']}/site-edit", page)
        self.assertIn(f"/tasks/{task_ids['needs_verification']}/site-edit", page)
        self.assertIn("先在网站完成验证并更新会话", page)

    def test_dashboard_does_not_flag_a_queued_first_verification_as_attention(self):
        self.create_enshan(name="正在验证的任务")
        self.store.enqueue_manual(self.store.list_tasks()[0]["id"])

        response = self.client.get("/")
        page = response.get_data(as_text=True)
        self.assertIn('data-task-attention="false"', page)
        self.assertNotIn('class="task-guidance"', page)
        self.assertIn("等待执行", page)


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
                # Model an existing scheduled task, retained by the migration.
                with self.store.connect() as db:
                    db.execute("UPDATE tasks SET enabled=1 WHERE id=?", (task_id,))
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
        with patch("autosign.schedule.utcnow", return_value=self.now):
            self.store.toggle_task(task_id)
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
