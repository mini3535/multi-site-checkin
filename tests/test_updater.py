"""Update transactions, recovery and web authorization without a Docker daemon."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from autosign.app import create_app
from autosign.update_client import call
from autosign.updater import Deployment, IMAGE, Manager, UpdateError, latest_release, registry_json


OLD = "a" * 40
NEW = "b" * 40
OLD_IMAGE = "sha256:" + "c" * 64
RELEASE = {"revision": NEW, "digest": "sha256:" + "d" * 64, "protocol": "1", "created": "2026-11-28"}


class FakeDeployment:
    def __init__(self, failure=""):
        self.failure = failure
        self.calls = []
        self.revision = OLD

    def current(self):
        return {"revision": self.revision, "image": OLD_IMAGE, "tunnel": True}

    def prepare(self, release):
        self.calls.append("download")
        if self.failure == "download":
            raise UpdateError("secret registry error")
        return IMAGE + "@" + release["digest"]

    def preflight(self, image):
        self.calls.append("preflight")
        if self.failure == "preflight":
            raise UpdateError("secret sandbox path")

    def stop(self, tunnel):
        self.calls.append("stop")

    def backup(self, job):
        self.calls.append("backup")
        if self.failure == "backup":
            raise OSError("disk full")

    def restore(self, job):
        self.calls.append("restore")

    def start(self, image, tunnel):
        self.calls.append("old" if image == OLD_IMAGE else "new")
        if self.failure in ("start", "recovery") and image != OLD_IMAGE or self.failure == "recovery":
            raise UpdateError("secret container env")
        self.revision = OLD if image == OLD_IMAGE else NEW


class UpdateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manager(self, failure="", release=None):
        return Manager(FakeDeployment(failure), self.root, fetch=lambda: dict(release or RELEASE))

    def begin(self, manager):
        # Keep the transaction synchronous while testing all persisted phases.
        with patch("autosign.updater.threading.Thread") as thread:
            manager.begin(NEW)
            thread.return_value.start.assert_called_once()

    def test_success_downloads_before_stopping_and_checks_new_revision(self):
        manager = self.manager()
        self.begin(manager)
        self.assertTrue(manager.status()["busy"])
        with self.assertRaises(UpdateError):
            manager.begin(NEW)
        manager.run(RELEASE)
        self.assertEqual(manager.deployment.calls, ["download", "preflight", "stop", "backup", "new"])
        self.assertEqual(manager.status()["phase"], "success")
        self.assertFalse(manager.status()["available"])
        self.assertFalse((self.root / "maintenance").exists())
        self.assertEqual(json.loads((self.root / "state.json").read_text())["previous"], OLD_IMAGE)

    def test_download_and_preflight_failure_do_not_stop_original(self):
        for failure in ("download", "preflight"):
            with self.subTest(failure=failure):
                manager = self.manager(failure)
                self.begin(manager)
                manager.run(RELEASE)
                self.assertNotIn("stop", manager.deployment.calls)
                self.assertEqual(manager.status()["phase"], "failed")
                self.assertNotIn("secret", manager.status()["message"])

    def test_new_image_failure_restores_database_before_starting_old_image(self):
        manager = self.manager("start")
        self.begin(manager)
        manager.run(RELEASE)
        self.assertEqual(manager.deployment.calls[-4:], ["new", "stop", "restore", "old"])
        self.assertEqual(manager.status()["phase"], "rolled_back")
        self.assertFalse((self.root / "maintenance").exists())

    def test_backup_failure_restarts_old_image_without_restoring_incomplete_backup(self):
        manager = self.manager("backup")
        self.begin(manager)
        manager.run(RELEASE)
        self.assertNotIn("restore", manager.deployment.calls)
        self.assertEqual(manager.deployment.calls[-1], "old")
        self.assertEqual(manager.status()["phase"], "rolled_back")

    def test_failed_recovery_keeps_scheduler_paused(self):
        manager = self.manager("recovery")
        self.begin(manager)
        manager.run(RELEASE)
        self.assertEqual(manager.status()["phase"], "needs_recovery")
        self.assertTrue((self.root / "maintenance").exists())
        self.assertFalse(manager.check()["can_update"])

    def test_explicit_retry_recovers_after_external_failure_is_fixed(self):
        manager = self.manager("recovery")
        self.begin(manager)
        manager.run(RELEASE)
        manager.deployment.failure = ""
        with patch("autosign.updater.threading.Thread") as thread:
            self.assertTrue(manager.retry_recovery()["busy"])
            thread.return_value.start.assert_called_once()
        manager.recover()
        self.assertEqual(manager.status()["phase"], "rolled_back")
        self.assertFalse((self.root / "maintenance").exists())
        with self.assertRaises(UpdateError):
            manager.retry_recovery()

    def test_restart_recovers_persisted_transaction_and_database(self):
        first = self.manager()
        self.begin(first)
        first.save(phase="starting", stopped=True, backed_up=True)
        (self.root / "maintenance").touch()
        second = self.manager()
        second.recover()
        self.assertEqual(second.deployment.calls, ["stop", "restore", "old"])
        self.assertEqual(second.status()["phase"], "rolled_back")
        self.assertFalse((self.root / "maintenance").exists())

    def test_restart_during_download_does_not_touch_live_service(self):
        first = self.manager()
        self.begin(first)
        second = self.manager()
        second.recover()
        self.assertEqual(second.deployment.calls, [])
        self.assertEqual(second.status()["phase"], "failed")

    def test_version_changes_and_future_protocol_cannot_be_applied(self):
        manager = self.manager(release={**RELEASE, "revision": "e" * 40})
        with self.assertRaises(UpdateError):
            manager.begin(NEW)
        manager = self.manager(release={**RELEASE, "protocol": "2"})
        with self.assertRaises(UpdateError):
            manager.begin(NEW)
        for value in ("latest", "../escape", [NEW], NEW + "; shell"):
            with self.assertRaises(UpdateError):
                manager.begin(value)

    def test_status_remains_responsive_during_slow_registry_check(self):
        manager = self.manager()
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return RELEASE
        manager.fetch = slow
        thread = threading.Thread(target=manager.check)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(manager.status()["phase"], "idle")
        finally:
            release.set()
            thread.join()

    def test_real_sqlite_backup_contains_wal_records_and_restore_is_private(self):
        deployment = Deployment.__new__(Deployment)
        deployment.root = self.root / "install"
        deployment.root.mkdir()
        (deployment.root / ".env").write_text("AUTOSIGN_SECRET_KEY=fixture-key")
        deployment.data = self.root / "data"
        deployment.data.mkdir()
        with sqlite3.connect(deployment.data / "autosign.db") as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE fixture (value TEXT)")
            db.execute("INSERT INTO fixture VALUES ('preserved')")
            db.commit()
            backup = deployment.backup("test-job")
            db.execute("UPDATE fixture SET value='new-migration'")
        with patch("autosign.updater.os.chown"):
            deployment.restore("test-job")
        with sqlite3.connect(deployment.data / "autosign.db") as db:
            self.assertEqual(db.execute("SELECT value FROM fixture").fetchone()[0], "preserved")
        self.assertEqual((backup / "config.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((deployment.data / "autosign.db").stat().st_mode & 0o777, 0o600)

    def test_candidate_image_is_pinned_and_mismatched_metadata_rejected(self):
        deployment = Deployment.__new__(Deployment)
        with patch.object(deployment, "command", return_value='[{"Config":{"Env":[]}}]') as command:
            with self.assertRaises(UpdateError):
                deployment.prepare(RELEASE)
            self.assertEqual(command.call_args_list[0].args[0], ["pull", IMAGE + "@" + RELEASE["digest"]])

    def test_tunnel_recreation_waits_for_backend_then_restores_and_verifies_https(self):
        deployment = Deployment.__new__(Deployment)
        answers = ["", "", UpdateError("not ready"), '{"BackendState":"Starting"}', '{"BackendState":"Running"}', "", ""]
        with patch.object(deployment, "compose", side_effect=answers) as compose, patch("autosign.updater.time.sleep"):
            deployment.start(OLD_IMAGE, True)
        self.assertIn("--force-recreate", compose.call_args_list[1].args)
        self.assertEqual(compose.call_args_list[-2].args[0:5], ("exec", "-T", "tunnel", "tailscale", "funnel"))
        self.assertEqual(compose.call_args_list[-1].args[-1], "verify-tunnel")

    def test_docker_errors_never_return_output_or_environment(self):
        deployment = Deployment.__new__(Deployment)
        deployment.root = self.root
        with patch("autosign.updater.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = b"AUTOSIGN_SECRET_KEY=secret"
            with self.assertRaisesRegex(UpdateError, "部署操作未完成") as error:
                deployment.command(["info"])
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(run.call_args.args[0][:3], ["docker", "--host", "unix:///var/run/docker.sock"])


class UpdateWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = Path(self.temp.name) / "control"
        self.control.mkdir()
        self.app = create_app({"DATA_DIR": str(Path(self.temp.name) / "data"), "UPDATE_DIR": str(self.control),
                               "ADMIN_PASSWORD": "fixture-long-password", "ENCRYPTION_KEY": Fernet.generate_key().decode(), "TESTING": True})
        self.client = self.app.test_client()

    def login(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf"]
        self.client.post("/login", data={"csrf_token": csrf, "password": "fixture-long-password"})
        self.client.get("/system/update")
        with self.client.session_transaction() as session:
            return session["csrf"]

    def test_endpoints_require_login_and_csrf_before_requesting_updater(self):
        with patch("autosign.app.update_client.call") as request:
            for path in ("/system/update", "/system/update/status"):
                self.assertEqual(self.client.get(path).status_code, 302)
            for path in ("/system/update/check", "/system/update/apply"):
                self.assertEqual(self.client.post(path).status_code, 302)
            csrf = self.login()
            self.assertEqual(self.client.post("/system/update/apply", data={"revision": NEW}).status_code, 400)
            self.assertEqual(self.client.get("/system/update/apply").status_code, 405)
            request.assert_not_called()
            request.return_value = {"supported": True, "busy": True}
            self.assertEqual(self.client.post("/system/update/apply", data={"csrf_token": csrf, "revision": NEW, "image": "attacker"}).status_code, 202)
            request.assert_called_once_with("apply", NEW, str(self.control))

    def test_missing_companion_shows_manual_bootstrap_instead_of_fake_success(self):
        self.login()
        result = self.client.get("/system/update/status")
        self.assertFalse(result.json["supported"])
        page = self.client.get("/system/update").get_data(as_text=True)
        self.assertIn("首次启用", page)
        self.assertIn("data-update-apply disabled", page)
        self.assertEqual(call("status", directory=str(self.control))["phase"], "unavailable")

    def test_maintenance_stops_writes_while_status_and_login_remain_available(self):
        csrf = self.login()
        (self.control / "maintenance").touch()
        with patch("autosign.app.update_client.call", return_value={"busy": True}):
            self.assertEqual(self.client.get("/system/update/status").status_code, 200)
        self.assertEqual(self.client.post("/tasks/new", data={"csrf_token": csrf}).status_code, 503)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.app.extensions["store"].list_tasks(), [])


if __name__ == "__main__":
    unittest.main()
