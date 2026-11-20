import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from autosign.schedule import display_time, next_run, stamp, validate_schedule
from autosign.storage import Store
from autosign.worker import Worker


UTC = timezone.utc


class ScheduleTests(unittest.TestCase):
    def test_shanghai_converts_to_utc_and_exact_time_moves_to_tomorrow(self):
        now = datetime(2026, 11, 19, 0, 29, tzinfo=UTC)
        self.assertEqual(next_run("08:30", "Asia/Shanghai", now), datetime(2026, 11, 19, 0, 30, tzinfo=UTC))
        exact = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.assertEqual(next_run("08:30", "Asia/Shanghai", exact), datetime(2026, 11, 20, 0, 30, tzinfo=UTC))
        self.assertEqual(display_time(stamp(exact), "Asia/Shanghai"), "11-19 08:30:00")

    def test_dst_spring_gap_runs_after_clock_jump(self):
        now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
        due = next_run("02:30", "America/New_York", now)
        self.assertEqual(due, datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        self.assertEqual(due.astimezone(ZoneInfo("America/New_York")).hour, 3)

    def test_dst_fall_uses_first_occurrence_once(self):
        now = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
        first = next_run("01:30", "America/New_York", now)
        self.assertEqual(first, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        self.assertEqual(next_run("01:30", "America/New_York", first), datetime(2026, 11, 2, 6, 30, tzinfo=UTC))

    def test_rejects_bad_schedule(self):
        for value in ["24:00", "08:60", "8:30", "08:3", "", "08:30:00"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_schedule(value, "Asia/Shanghai")
        with self.assertRaises(ValueError):
            validate_schedule("08:30", "No/Such_Zone")


class StoreSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.key = Fernet.generate_key().decode()
        self.store = Store(self.temp.name, self.key)
        self.now = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
        self.fields = {"name": "定时站点", "daily_time": "08:30", "timezone": "Asia/Shanghai", "enabled": True}
        self.spec = {"url": "https://example.com/checkin", "method": "POST", "headers": {"Cookie": "secret"},
                     "body": "", "success_type": "json", "success_path": "ok", "success_value": "true"}
        self.task_id = self.store.save_task(self.fields, self.spec, now=self.now)
        self.success = {"ok": True, "status_code": 200, "message": "成功条件已满足", "duration_ms": 1}

    def tearDown(self):
        self.temp.cleanup()

    def test_not_due_then_due_enqueues_only_once_and_claim_is_atomic(self):
        self.store.enqueue_due(self.now)
        self.assertEqual(self.store.recent_runs(), [])
        due = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.store.enqueue_due(due)
        self.store.enqueue_due(due)
        self.assertEqual(len(self.store.recent_runs()), 1)
        with patch("autosign.storage.utcnow", return_value=due):
            job = self.store.claim_next()
            self.assertIsNotNone(job)
            self.assertIsNone(self.store.claim_next())
        self.assertEqual(self.store.recent_runs()[0]["status"], "running")
        self.assertFalse(self.store.enqueue_manual(self.task_id))

    def test_today_manual_success_suppresses_scheduled_repeat(self):
        self.assertTrue(self.store.enqueue_manual(self.task_id))
        with patch("autosign.schedule.utcnow", return_value=self.now):
            run, _task = self.store.claim_next()
            self.store.finish(run["id"], self.success)
        self.store.enqueue_due(datetime(2026, 11, 19, 0, 30, tzinfo=UTC))
        self.assertEqual(len(self.store.recent_runs()), 1)
        self.assertEqual(self.store.recent_runs()[0]["kind"], "manual")
        self.assertEqual(self.store.get_task(self.task_id)["next_run_at"], "2026-11-20T00:30:00+00:00")

    def test_failed_schedule_does_not_auto_retry_same_day(self):
        due = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.store.enqueue_due(due)
        with patch("autosign.storage.utcnow", return_value=due):
            run, _task = self.store.claim_next()
            self.store.finish(run["id"], dict(self.success, ok=False))
        self.store.enqueue_due(datetime(2026, 11, 19, 1, 0, tzinfo=UTC))
        self.assertEqual(len(self.store.recent_runs()), 1)
        self.assertTrue(self.store.enqueue_manual(self.task_id))

    def test_long_downtime_catches_up_only_current_day(self):
        current = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
        self.store.enqueue_due(current)
        self.store.enqueue_due(current)
        runs = self.store.recent_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["slot"], "2026-11-25")
        self.assertEqual(self.store.get_task(self.task_id)["next_run_at"], "2026-11-26T00:30:00+00:00")

    def test_recovery_interrupts_queued_and_running_without_resending(self):
        self.store.enqueue_manual(self.task_id)
        with patch("autosign.schedule.utcnow", return_value=self.now):
            self.store.claim_next()
        other_id = self.store.save_task(dict(self.fields, name="第二个任务"), self.spec, now=self.now)
        self.store.enqueue_manual(other_id)
        self.store.recover_runs()
        self.assertEqual({row["status"] for row in self.store.recent_runs()}, {"interrupted"})
        self.assertIsNone(self.store.claim_next())
        self.assertTrue(self.store.enqueue_manual(self.task_id))

    def test_paused_task_is_not_scheduled(self):
        self.store.toggle_task(self.task_id)
        self.store.enqueue_due(datetime(2026, 11, 25, 0, 30, tzinfo=UTC))
        self.assertEqual(self.store.recent_runs(), [])

    def test_saving_pause_cancels_waiting_schedule_but_allows_manual_execution(self):
        due = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.store.enqueue_due(due)
        self.store.save_task(dict(self.fields, enabled=False), self.spec, self.task_id, now=due)
        self.assertEqual(self.store.recent_runs()[0]["status"], "interrupted")
        self.assertIsNone(self.store.claim_next())
        self.assertTrue(self.store.enqueue_manual(self.task_id))
        run, task = self.store.claim_next()
        self.assertEqual(run["kind"], "manual")
        self.assertFalse(task["enabled"])

    def test_saving_cleared_credentials_cancels_waiting_schedule(self):
        due = datetime(2026, 11, 19, 0, 30, tzinfo=UTC)
        self.store.enqueue_due(due)
        cleared = dict(self.spec, headers={}, body="")
        self.store.save_task(dict(self.fields, enabled=False), cleared, self.task_id, now=due)
        current = self.store.get_task(self.task_id)
        self.assertFalse(current["enabled"])
        self.assertEqual(self.store.spec(current)["headers"], {})
        self.assertEqual(self.store.recent_runs()[0]["status"], "interrupted")
        self.assertIsNone(self.store.claim_next())

    def test_old_attention_result_cannot_pause_replaced_session(self):
        for status in ("needs_login", "needs_verification"):
            with self.subTest(status=status):
                self.assertTrue(self.store.enqueue_manual(self.task_id))
                run, task = self.store.claim_next()
                updated = dict(self.spec, headers={"Cookie": "new-admin-session"})

                def runner(_spec, **_kwargs):
                    # Replace credentials while the executor still holds the old
                    # task snapshot, then return the old session's failure.
                    self.store.save_task(self.fields, updated, self.task_id, now=self.now)
                    return {"ok": False, "status": status, "status_code": None,
                            "message": "旧会话需要处理", "duration_ms": 1}

                worker = Worker(self.store, runner=runner)
                try:
                    worker.execute(run, task)
                finally:
                    worker.pool.shutdown()
                current = self.store.get_task(self.task_id)
                self.assertTrue(current["enabled"])
                self.assertEqual(self.store.spec(current)["headers"], updated["headers"])
                self.assertEqual(self.store.recent_runs()[0]["status"], status)

    def test_attention_result_still_pauses_unchanged_configuration(self):
        self.assertTrue(self.store.enqueue_manual(self.task_id))
        run, task = self.store.claim_next()
        worker = Worker(self.store, runner=lambda *_args, **_kwargs: {
            "ok": False, "status": "needs_login", "status_code": None,
            "message": "登录需要更新", "duration_ms": 1})
        try:
            worker.execute(run, task)
        finally:
            worker.pool.shutdown()
        self.assertFalse(self.store.get_task(self.task_id)["enabled"])

    def test_wrong_encryption_key_fails_before_serving(self):
        with self.assertRaises(ValueError):
            Store(self.temp.name, Fernet.generate_key().decode())

    def test_worker_runs_injected_runner_and_records_result(self):
        self.store.enqueue_manual(self.task_id)
        invoked = threading.Event()
        seen = []

        def fake_runner(spec, allow_private):
            seen.append((spec, allow_private))
            invoked.set()
            return self.success

        worker = Worker(self.store, allow_private=True, runner=fake_runner)
        worker.start()
        try:
            self.assertTrue(invoked.wait(3))
        finally:
            worker.stop()
        self.assertEqual(seen, [(self.spec, True)])
        self.assertEqual(self.store.recent_runs()[0]["status"], "success")

    def test_worker_hides_runner_exception_details(self):
        self.store.enqueue_manual(self.task_id)
        run, task = self.store.claim_next()

        def broken_runner(_spec, **_kwargs):
            raise RuntimeError("Cookie: very-secret-value")

        worker = Worker(self.store, runner=broken_runner)
        worker.execute(run, task)
        worker.pool.shutdown()
        result = self.store.recent_runs()[0]
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("very-secret-value", result["message"])


if __name__ == "__main__":
    unittest.main()
