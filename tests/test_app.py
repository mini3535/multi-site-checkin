import json
import tempfile
import unittest

from cryptography.fernet import Fernet

from autosign.app import create_app, display_build_revision


PASSWORD = "integration-test-password"
COOKIE_SECRET = "cookie-secret-never-store-in-plaintext"
BODY_SECRET = "request-body-secret-never-store-in-plaintext"


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "DATA_DIR": self.temp.name,
            "ADMIN_PASSWORD": PASSWORD,
            "ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "TESTING": True,
            "ALLOW_PRIVATE_TARGETS": True,
        })
        self.client = self.app.test_client()
        self.store = self.app.extensions["store"]

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            return session.get("csrf", "")

    def login(self):
        self.client.get("/login")
        response = self.client.post("/login", data={"password": PASSWORD, "csrf_token": self.csrf()}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertTrue(session["authenticated"])
        return response

    def fields(self, **changes):
        data = {
            "csrf_token": self.csrf(), "name": "测试站点", "url": "https://example.com/checkin",
            "method": "POST", "headers": json.dumps({"Cookie": COOKIE_SECRET}),
            "body": BODY_SECRET, "success_type": "json", "success_path": "data.checked",
            "success_value": "true", "daily_time": "08:30", "timezone": "Asia/Shanghai", "enabled": "on",
        }
        data.update(changes)
        return data

    def create_task(self, **changes):
        response = self.client.post("/tasks/new", data=self.fields(**changes))
        self.assertEqual(response.status_code, 302)
        return self.store.list_tasks()[0]["id"]

    def test_login_requires_valid_csrf_and_rotates_session(self):
        self.client.get("/login")
        before = self.csrf()
        self.assertTrue(before)
        response = self.client.post("/login", data={"password": PASSWORD})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/login", data={"password": PASSWORD, "csrf_token": "wrong"})
        self.assertEqual(response.status_code, 400)
        self.login()
        self.assertNotEqual(self.csrf(), before)

    def test_unauthenticated_clients_cannot_read_or_mutate_tasks(self):
        for method, path in [("get", "/"), ("get", "/tasks/new"), ("post", "/tasks/new"),
                             ("get", "/dashboard-status"), ("post", "/tasks/1/run"), ("post", "/tasks/1/delete")]:
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(self.store.list_tasks(), [])

    def test_dashboard_status_exposes_only_run_id_and_status(self):
        self.login()
        task_id = self.create_task()
        self.assertTrue(self.store.enqueue_manual(task_id))

        queued = self.client.get("/dashboard-status")
        self.assertEqual(queued.status_code, 200)
        queued_run = queued.json["runs"][str(task_id)]
        self.assertEqual(queued_run["status"], "queued")
        self.assertEqual(set(queued_run), {"id", "status"})

        run, _task = self.store.claim_next()
        self.store.finish(run["id"], {
            "ok": True, "status": "success", "message": "private run details",
            "duration_ms": 1, "status_code": 200,
        })
        updated = self.client.get("/dashboard-status")
        self.assertEqual(updated.json["runs"][str(task_id)]["status"], "success")
        self.assertNotIn(b"private run details", updated.data)

    def test_login_throttles_repeated_bad_passwords(self):
        self.client.get("/login")
        for _ in range(5):
            response = self.client.post("/login", data={"password": "wrong", "csrf_token": self.csrf()})
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/login", data={"password": PASSWORD, "csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 429)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("authenticated", False))

    def test_create_encrypts_all_request_details(self):
        self.login()
        task_id = self.create_task()
        task = self.store.get_task(task_id)
        request = self.store.spec(task)
        self.assertEqual(request["headers"]["Cookie"], COOKIE_SECRET)
        self.assertEqual(request["body"], BODY_SECRET)
        with self.store.connect() as db:
            raw = db.execute("SELECT sealed_spec FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        self.assertNotIn(COOKIE_SECRET, raw)
        self.assertNotIn(BODY_SECRET, raw)
        self.assertNotIn(b"example.com", self.store.path.read_bytes())
        response = self.client.get("/")
        self.assertNotIn(COOKIE_SECRET.encode(), response.data)
        self.assertNotIn(BODY_SECRET.encode(), response.data)

    def test_edit_blank_secrets_preserves_then_clear_removes(self):
        self.login()
        task_id = self.create_task()
        page = self.client.get(f"/tasks/{task_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(COOKIE_SECRET.encode(), page.data)
        self.assertNotIn(BODY_SECRET.encode(), page.data)
        response = self.client.post(f"/tasks/{task_id}/edit", data=self.fields(name="改名", headers="", body=""))
        self.assertEqual(response.status_code, 302)
        spec = self.store.spec(self.store.get_task(task_id))
        self.assertEqual(spec["headers"]["Cookie"], COOKIE_SECRET)
        self.assertEqual(spec["body"], BODY_SECRET)
        response = self.client.post(f"/tasks/{task_id}/edit", data=self.fields(headers="", body="", clear_credentials="on"))
        self.assertEqual(response.status_code, 302)
        spec = self.store.spec(self.store.get_task(task_id))
        self.assertEqual(spec["headers"], {})
        self.assertEqual(spec["body"], "")

    def test_invalid_json_and_get_body_fail_without_echoing_secrets(self):
        self.login()
        for changes in [{"headers": "not JSON " + COOKIE_SECRET}, {"method": "GET"}, {"success_value": ""}]:
            with self.subTest(changes=list(changes)):
                response = self.client.post("/tasks/new", data=self.fields(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(COOKIE_SECRET.encode(), response.data)
                self.assertNotIn(BODY_SECRET.encode(), response.data)
        self.assertEqual(self.store.list_tasks(), [])

    def test_actions_require_csrf_and_manual_enqueue_deduplicates(self):
        self.login()
        task_id = self.create_task()
        self.assertEqual(self.client.post(f"/tasks/{task_id}/run", data={}).status_code, 400)
        self.assertEqual(self.store.recent_runs(), [])
        for _ in range(2):
            self.assertEqual(self.client.post(f"/tasks/{task_id}/run", data={"csrf_token": self.csrf()}).status_code, 302)
        self.assertEqual(len(self.store.recent_runs()), 1)
        self.assertEqual(self.store.recent_runs()[0]["status"], "queued")

    def test_toggle_cancels_queued_run_and_delete_preserves_safe_history(self):
        self.login()
        task_id = self.create_task()
        self.client.post(f"/tasks/{task_id}/run", data={"csrf_token": self.csrf()})
        self.client.post(f"/tasks/{task_id}/toggle", data={"csrf_token": self.csrf()})
        self.assertFalse(self.store.get_task(task_id)["enabled"])
        self.assertEqual(self.store.recent_runs()[0]["status"], "interrupted")
        self.assertEqual(self.client.get(f"/tasks/{task_id}/delete").status_code, 405)
        self.client.post(f"/tasks/{task_id}/delete", data={"csrf_token": self.csrf()})
        self.assertIsNone(self.store.get_task(task_id))
        self.assertIsNone(self.store.recent_runs()[0]["task_id"])

    def test_logout_requires_csrf_then_invalidates_access(self):
        self.login()
        self.assertEqual(self.client.post("/logout", data={}).status_code, 400)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": self.csrf()}).status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_health_public_and_security_headers(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok"})
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_build_revision_is_short_and_only_visible_after_login(self):
        self.app.config["BUILD_REVISION"] = "0123456789abcdef0123456789abcdef01234567"
        login_page = self.client.get("/login")
        self.assertNotIn("构建版本".encode(), login_page.data)
        self.login()
        dashboard = self.client.get("/")
        self.assertIn("构建版本 0123456789ab".encode(), dashboard.data)
        self.assertNotIn("0123456789abcdef".encode(), dashboard.data)

    def test_build_revision_is_bounded_and_uses_local_default(self):
        self.assertEqual(display_build_revision(""), "local")
        self.assertEqual(display_build_revision("v1.2.3+build.4"), "v1.2.3+build")
        self.assertEqual(display_build_revision("private/value?token=secret"), "unknown")


if __name__ == "__main__":
    unittest.main()

    def password_fields(self, **changes):
        fields = {"csrf_token": self.csrf(), "current_password": PASSWORD,
                  "new_password": "new-admin-password-12345", "confirm_password": "new-admin-password-12345"}
        fields.update(changes)
        return fields

    def test_password_page_and_post_require_authentication_and_csrf(self):
        self.assertEqual(self.client.get("/settings/password").status_code, 302)
        self.assertEqual(self.client.post("/settings/password").status_code, 302)
        self.login()
        page = self.client.get("/settings/password")
        self.assertEqual(page.status_code, 200)
        self.assertIn("当前密码".encode(), page.data)
        self.assertEqual(self.client.post("/settings/password", data=self.password_fields(csrf_token="wrong")).status_code, 400)

    def test_password_change_validates_current_length_and_confirmation_without_echo(self):
        self.login()
        original = self.store.admin_auth()
        for changes in [{"current_password": "wrong"},
                        {"new_password": "short", "confirm_password": "short"},
                        {"new_password": "x" * 257, "confirm_password": "x" * 257},
                        {"confirm_password": "a-different-password"},
                        {"new_password": PASSWORD, "confirm_password": PASSWORD}]:
            with self.subTest(changes=list(changes)):
                response = self.client.post("/settings/password", data=self.password_fields(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(PASSWORD.encode(), response.data)
                self.assertNotIn(b"new-admin-password-12345", response.data)
                self.assertEqual(self.store.admin_auth(), original)

    def test_password_change_persists_invalidates_all_sessions_and_preserves_task_key(self):
        self.login()
        task_id = self.create_task()
        spec = self.store.spec(self.store.get_task(task_id))
        other = self.app.test_client()
        other.get("/login")
        other.post("/login", data={"password": PASSWORD, "csrf_token": self.csrf(other)})
        self.assertEqual(other.get("/").status_code, 200)
        old_auth = self.store.admin_auth()
        response = self.client.post("/settings/password", data=self.password_fields())
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(other.get("/").status_code, 302)
        self.assertNotEqual(old_auth["version"], self.store.admin_auth()["version"])
        self.assertNotIn("new-admin-password-12345", self.store.admin_auth()["password_hash"])
        self.assertEqual(self.store.spec(self.store.get_task(task_id)), spec)
        restarted = create_app({**self.app.config, "ADMIN_PASSWORD": "another-bootstrap-password"}).test_client()
        for password, expected in [(PASSWORD, False), ("another-bootstrap-password", False), ("new-admin-password-12345", True)]:
            restarted.get("/login")
            restarted.post("/login", data={"password": password, "csrf_token": self.csrf(restarted)})
            with restarted.session_transaction() as session:
                self.assertEqual(bool(session.get("authenticated")), expected)

    def test_password_change_throttles_current_password_guesses(self):
        self.login()
        for _ in range(5):
            self.assertEqual(self.client.post("/settings/password", data=self.password_fields(current_password="wrong")).status_code, 400)
        self.assertEqual(self.client.post("/settings/password", data=self.password_fields()).status_code, 429)

    def test_password_compare_and_swap_does_not_overwrite_a_concurrent_change(self):
        auth = self.store.admin_auth()
        self.assertTrue(self.store.change_admin_password(auth["version"], "first-hash", "first-version"))
        self.assertFalse(self.store.change_admin_password(auth["version"], "stale-hash", "stale-version"))
        self.assertEqual(self.store.admin_auth()["password_hash"], "first-hash")
