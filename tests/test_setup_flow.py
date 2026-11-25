"""Four-step onboarding against fixtures; no live site requests or real accounts."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from autosign.app import create_app
from autosign.browser_runner import cookie_storage_state
from autosign.storage import Store


SECRET = "private-fixture-cookie"


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = Fernet.generate_key().decode()
        self.app = create_app({"DATA_DIR": self.temp.name, "ENCRYPTION_KEY": self.key,
                               "ADMIN_PASSWORD": "setup-fixture-password", "TESTING": True})
        self.store = self.app.extensions["store"]
        self.client = self.app.test_client()
        self.client.get("/login")
        self.client.post("/login", data={"csrf_token": self.csrf(), "password": "setup-fixture-password"}, follow_redirects=True)

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self):
        with self.client.session_transaction() as session:
            return session["csrf"]

    def post(self, path, **fields):
        return self.client.post(path, data={"csrf_token": self.csrf(), **fields})

    def create(self, site="enshan", **changes):
        fields = {"name": "测试账号", "cookie": SECRET, "enabled": "on", **changes}
        if site == "workbuddy":
            fields.update(token=SECRET, uid="fixture-user")
        if site in ("custom", "nodeseek", "hostloc"):
            fields.update(start_url="https://forum.example/daily", browser_cookie="session=" + SECRET,
                          button_text="每日签到", success_text="签到成功")
        response = self.post(f"/sites/{site}/new", **fields)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        task = self.store.list_tasks()[0]
        self.assertTrue(response.location.endswith(f"/tasks/{task['id']}/verify"))
        self.assertFalse(task["enabled"])
        return task["id"]

    def finish(self, task_id, status="success", snapshot=None):
        self.assertTrue(self.store.enqueue_manual(task_id))
        run, task = self.store.claim_next()
        self.store.finish(run["id"], {"ok": status in ("success", "already_done"), "status": status,
                                      "message": "固定结果", "duration_ms": 1}, task_snapshot=snapshot or task)

    def test_every_runnable_type_uses_same_four_steps(self):
        for site in ("enshan", "workbuddy", "nodeseek", "hostloc", "custom"):
            with self.subTest(site=site):
                page = self.client.get(f"/sites/{site}/new").get_data(as_text=True)
                for label in ("选择网站", "连接账号", "验证签到", "每天自动执行"):
                    self.assertIn(label, page)
                self.assertNotIn('name="enabled"', page)
                task_id = self.create(site)
                verification = self.client.get(f"/tasks/{task_id}/verify")
                self.assertIn("开始验证签到", verification.get_data(as_text=True))
                self.assertNotIn(SECRET, verification.get_data(as_text=True))

    def test_cannot_skip_verification_using_schedule_toggle_or_forged_editor(self):
        task_id = self.create()
        for response in (self.client.get(f"/tasks/{task_id}/schedule"),
                         self.post(f"/tasks/{task_id}/schedule", daily_time="09:00", timezone="UTC"),
                         self.post(f"/tasks/{task_id}/toggle")):
            self.assertTrue(response.location.endswith(f"/tasks/{task_id}/verify"))
        self.post(f"/tasks/{task_id}/site-edit", name="改名", cookie="", enabled="on")
        self.assertFalse(self.store.get_task(task_id)["enabled"])
        self.assertEqual(self.store.recent_runs(), [])

    def test_verify_queues_once_get_never_executes_and_reports_only_metadata(self):
        task_id = self.create()
        self.client.get(f"/tasks/{task_id}/verify")
        self.assertEqual(self.store.recent_runs(), [])
        for _ in range(2):
            self.post(f"/tasks/{task_id}/verify")
        self.assertEqual(len(self.store.recent_runs()), 1)
        result = self.client.get(f"/tasks/{task_id}/verification-status")
        self.assertEqual(set(result.json), {"revision", "run_id", "status", "ready"})
        self.assertEqual(result.json["status"], "queued")
        self.assertFalse(result.json["ready"])
        self.assertNotIn(SECRET, result.get_data(as_text=True))
        page = self.client.get(f"/tasks/{task_id}/verify").get_data(as_text=True)
        self.assertIn("data-verification-url", page)

    def test_success_and_already_done_allow_explicit_plan_activation(self):
        for status in ("success", "already_done"):
            with self.subTest(status=status):
                task_id = self.create()
                self.finish(task_id, status)
                self.assertFalse(self.store.get_task(task_id)["enabled"])
                self.assertTrue(self.store.verification(task_id)["ready"])
                self.assertEqual(self.client.get(f"/tasks/{task_id}/schedule").status_code, 200)
                invalid = self.post(f"/tasks/{task_id}/schedule", daily_time="25:00", timezone="UTC")
                self.assertEqual(invalid.status_code, 400)
                self.assertFalse(self.store.get_task(task_id)["enabled"])
                self.post(f"/tasks/{task_id}/schedule", daily_time="09:15", timezone="Asia/Shanghai")
                task = self.store.get_task(task_id)
                self.assertTrue(task["enabled"])
                self.assertEqual(task["daily_time"], "09:15")
                self.assertEqual(task["timezone"], "Asia/Shanghai")

    def test_failure_cannot_enable_and_has_actionable_guidance(self):
        for status in ("failed", "needs_login", "needs_verification", "unsupported", "interrupted"):
            with self.subTest(status=status):
                task_id = self.create()
                self.finish(task_id, status)
                self.assertFalse(self.store.verification(task_id)["ready"])
                page = self.client.get(f"/tasks/{task_id}/verify").get_data(as_text=True)
                self.assertIn("更新账号或规则", page)
                self.assertNotIn("下一步：每天自动执行", page)
                self.assertNotIn(SECRET, page)

    def test_config_change_invalidates_success_and_cancels_waiting_run(self):
        task_id = self.create()
        self.finish(task_id)
        self.store.enable_schedule(task_id, "09:00", "UTC")
        self.store.enqueue_manual(task_id)
        self.post(f"/tasks/{task_id}/site-edit", name="账号", cookie="updated-session")
        self.assertFalse(self.store.get_task(task_id)["enabled"])
        self.assertFalse(self.store.verification(task_id)["ready"])
        self.assertEqual(self.store.recent_runs()[0]["status"], "interrupted")
        self.assertIsNone(self.store.claim_next())
        self.assertIsNone(self.store.verification(task_id)["run"])

    def test_old_running_success_cannot_validate_updated_credentials(self):
        task_id = self.create()
        self.store.enqueue_manual(task_id)
        run, old = self.store.claim_next()
        self.post(f"/tasks/{task_id}/site-edit", name="账号", cookie="updated-session")
        page = self.client.get(f"/tasks/{task_id}/verify").get_data(as_text=True)
        self.assertIn("等待上一轮执行结束", page)
        self.assertIn("data-verification-url", page)
        self.store.finish(run["id"], {"ok": True, "status": "success", "message": "固定成功", "duration_ms": 1}, old)
        self.assertFalse(self.store.verification(task_id)["ready"])
        with self.assertRaises(ValueError):
            self.store.enable_schedule(task_id, "09:00", "UTC")
        self.finish(task_id)
        self.assertTrue(self.store.verification(task_id)["ready"])

    def test_rename_and_blank_credentials_preserve_validation_and_enabled_plan(self):
        task_id = self.create()
        self.finish(task_id)
        self.store.enable_schedule(task_id, "09:00", "UTC")
        original = self.store.get_task(task_id)
        self.post(f"/tasks/{task_id}/site-edit", name="改名", cookie="")
        task = self.store.get_task(task_id)
        self.assertTrue(task["enabled"])
        self.assertTrue(self.store.verification(task_id)["ready"])
        self.assertEqual(task["config_revision"], original["config_revision"])
        self.assertEqual(task["daily_time"], "09:00")
        self.assertEqual(task["timezone"], "UTC")

    def test_rotated_browser_session_keeps_verification_but_manual_rule_change_resets_it(self):
        task_id = self.create("custom")
        self.store.enqueue_manual(task_id)
        run, snapshot = self.store.claim_next()
        spec = self.store.spec(snapshot)
        state = spec["storage_state"]
        state["cookies"][0]["value"] = "rotated-fixture"
        self.store.refresh_session(snapshot, state)
        self.store.finish(run["id"], {"ok": True, "status": "success", "message": "固定成功", "duration_ms": 1}, snapshot)
        self.assertTrue(self.store.verification(task_id)["ready"])
        self.post(f"/tasks/{task_id}/site-edit", name="论坛", start_url=spec["start_url"],
                  button_text="另一个按钮", success_text="签到成功")
        self.assertFalse(self.store.verification(task_id)["ready"])

    def test_cookie_import_scopes_to_start_host_and_rejects_conflicting_or_invalid_inputs(self):
        task_id = self.create("custom")
        state = self.store.spec(self.store.get_task(task_id))["storage_state"]
        self.assertEqual(state["origins"], [])
        self.assertEqual(state["cookies"][0]["domain"], "forum.example")
        self.assertNotIn(SECRET, self.store.get_task(task_id)["sealed_spec"])
        for cookie in ("Cookie: session=abc", "session=abc\nX-Foo: bar", "no-equals", "=empty", "a" * 17000):
            with self.assertRaises(ValueError):
                cookie_storage_state(cookie, "https://forum.example/daily")
        response = self.post("/sites/custom/new", name="论坛", start_url="https://forum.example/daily",
                             browser_cookie="session=" + SECRET, storage_state=json.dumps(state),
                             button_text="签到", success_text="签到成功")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_setup_routes_require_login_csrf_and_correct_task_type(self):
        task_id = self.create()
        outsider = self.app.test_client()
        for suffix in ("verify", "verification-status", "schedule"):
            self.assertTrue(outsider.get(f"/tasks/{task_id}/{suffix}").location.endswith("/login"))
        for suffix in ("verify", "schedule"):
            self.assertEqual(self.client.post(f"/tasks/{task_id}/{suffix}").status_code, 400)
        http_id = self.store.save_task({"name": "HTTP", "daily_time": "08:00", "timezone": "UTC", "enabled": False}, {"engine": "http"})
        for suffix in ("verify", "verification-status", "schedule"):
            self.assertEqual(self.client.get(f"/tasks/{http_id}/{suffix}").status_code, 404)
        self.assertEqual(self.client.get("/tasks/99999/verify").status_code, 404)


class SetupMigrationTests(unittest.TestCase):
    def test_existing_database_migrates_without_disabling_or_revalidating_old_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Fernet.generate_key()
            sealed = Fernet(key).encrypt(json.dumps({"engine": "site", "site_id": "enshan", "cookie": SECRET}).encode()).decode()
            path = Path(directory) / "autosign.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, daily_time TEXT NOT NULL, timezone TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, sealed_spec TEXT NOT NULL, next_run_at TEXT NOT NULL, created_at TEXT NOT NULL)")
                db.execute("INSERT INTO tasks VALUES (1,'原任务','08:00','UTC',1,?,'2026-11-26T08:00:00+00:00','2026-11-25T00:00:00+00:00')", (sealed,))
            store = Store(directory, key)
            Store(directory, key)  # Migration is repeatable.
            task = store.get_task(1)
            self.assertTrue(task["enabled"])
            self.assertEqual(task["config_revision"], 1)
            self.assertEqual(store.spec(task)["cookie"], SECRET)
            self.assertFalse(store.verification(1)["ready"])


if __name__ == "__main__":
    unittest.main()
