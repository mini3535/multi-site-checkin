import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken

from .schedule import next_run, stamp, utcnow


class Store:
    def __init__(self, data_dir, key):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "autosign.db"
        self.cipher = Fernet(key.encode() if isinstance(key, str) else key)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS admin_auth (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    password_hash TEXT NOT NULL, version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    daily_time TEXT NOT NULL, timezone TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, sealed_spec TEXT NOT NULL,
                    next_run_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                    task_name TEXT NOT NULL, kind TEXT NOT NULL, slot TEXT,
                    status TEXT NOT NULL, queued_at TEXT NOT NULL, started_at TEXT,
                    finished_at TEXT, message TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER, status_code INTEGER,
                    UNIQUE(task_id, slot)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_task
                    ON runs(task_id) WHERE status IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS runs_recent ON runs(id DESC);
            """)
            # Additive migration keeps existing installations and task history.
            for table, column, declaration in (
                ("tasks", "config_revision", "INTEGER NOT NULL DEFAULT 1"),
                ("tasks", "verified_revision", "INTEGER"),
                ("runs", "config_revision", "INTEGER"),
            ):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        os.chmod(self.path, 0o600)
        # Fail clearly if a persistent database is opened with a different key.
        for task in self.list_tasks():
            self._decrypt(task["sealed_spec"])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _decrypt(self, value):
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except InvalidToken:
            raise ValueError("数据密钥不匹配。请恢复原 AUTOSIGN_SECRET_KEY，不要重新生成。") from None

    def spec(self, task):
        return self._decrypt(task["sealed_spec"])

    def initialize_admin(self, password_hash, version):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO admin_auth VALUES (1,?,?)", (password_hash, version))

    def admin_auth(self):
        with self.connect() as db:
            row = db.execute("SELECT password_hash,version FROM admin_auth WHERE id=1").fetchone()
            return dict(row) if row else None

    def change_admin_password(self, old_version, password_hash, version):
        with self.connect() as db:
            return db.execute(
                "UPDATE admin_auth SET password_hash=?,version=? WHERE id=1 AND version=?",
                (password_hash, version, old_version),
            ).rowcount == 1

    def list_tasks(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY id DESC")]

    def get_task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def save_task(self, fields, spec, task_id=None, now=None, expected_revision=None):
        now = now or utcnow()
        sealed = self.cipher.encrypt(json.dumps(spec, ensure_ascii=False).encode()).decode()
        due = stamp(next_run(fields["daily_time"], fields["timezone"], now))
        values = (fields["name"], fields["daily_time"], fields["timezone"], int(fields["enabled"]), sealed, due)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id is not None else None
            if expected_revision is not None and (old is None or old["config_revision"] != expected_revision):
                raise ValueError("任务已更改或删除，请重新连接账号。")
            changed = old is not None and self.spec(old) != spec
            website = spec.get("engine", "http") != "http"
            enabled = bool(fields["enabled"])
            if website and (old is None or changed or (not old["enabled"] and old["verified_revision"] != old["config_revision"])):
                enabled = False
            values = values[:3] + (int(enabled),) + values[4:]
            if task_id is None:
                cursor = db.execute("INSERT INTO tasks (name,daily_time,timezone,enabled,sealed_spec,next_run_at,created_at) VALUES (?,?,?,?,?,?,?)", values + (stamp(now),))
                return cursor.lastrowid
            db.execute("UPDATE tasks SET name=?,daily_time=?,timezone=?,enabled=?,sealed_spec=?,next_run_at=? WHERE id=?", values + (task_id,))
            if changed:
                db.execute("UPDATE tasks SET config_revision=config_revision+1,verified_revision=NULL WHERE id=?", (task_id,))
                db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='账号或规则已更新，请重新验证。' WHERE task_id=? AND status='queued'", (stamp(now), task_id))
            if not enabled:
                # Editing or clearing credentials can pause a task too. A queued
                # scheduled run must not survive that pause; explicit manual
                # requests remain usable for testing a paused configuration.
                db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='任务已暂停，取消等待执行。' WHERE task_id=? AND kind='scheduled' AND status='queued'", (stamp(now), task_id))
            return task_id

    def toggle_task(self, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                if (not row["enabled"] and self.spec(row).get("engine", "http") != "http"
                        and not self._verified(db, row)):
                    raise ValueError("请先完成签到验证，再启用每日自动执行。")
                due = stamp(next_run(row["daily_time"], row["timezone"]))
                db.execute("UPDATE tasks SET enabled=?,next_run_at=? WHERE id=?", (int(not row["enabled"]), due, task_id))
                if row["enabled"]:
                    db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='任务已暂停，取消等待执行。' WHERE task_id=? AND status='queued'", (stamp(), task_id))

    @staticmethod
    def _verified(db, task):
        if task["verified_revision"] != task["config_revision"]:
            return False
        last = db.execute("SELECT status FROM runs WHERE task_id=? AND config_revision=? ORDER BY id DESC LIMIT 1",
                          (task["id"], task["config_revision"])).fetchone()
        return bool(last and last["status"] in ("success", "already_done"))

    def verification(self, task_id):
        with self.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                return {"ready": False, "run": None}
            last = db.execute("SELECT * FROM runs WHERE task_id=? AND config_revision=? ORDER BY id DESC LIMIT 1",
                              (task_id, task["config_revision"])).fetchone()
            if last is None:
                # A replaced account can still have an older request finishing.
                # Show that pending work until it releases the per-task queue.
                last = db.execute("SELECT * FROM runs WHERE task_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                                  (task_id,)).fetchone()
            return {"ready": self._verified(db, task), "run": dict(last) if last else None}

    def enable_schedule(self, task_id, daily_time, timezone):
        from .schedule import validate_schedule
        validate_schedule(daily_time, timezone)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or not self._verified(db, task):
                raise ValueError("请先完成当前账号和规则的签到验证。")
            db.execute("UPDATE tasks SET daily_time=?,timezone=?,enabled=1,next_run_at=? WHERE id=?",
                       (daily_time, timezone, stamp(next_run(daily_time, timezone)), task_id))

    def delete_task(self, task_id):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='任务已删除，取消等待执行。' WHERE task_id=? AND status='queued'", (stamp(), task_id))
            db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def enqueue_manual(self, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                return False
            active = db.execute("SELECT 1 FROM runs WHERE task_id=? AND status IN ('queued','running')", (task_id,)).fetchone()
            if active:
                return False
            db.execute("INSERT INTO runs(task_id,task_name,kind,status,queued_at,message,config_revision) VALUES (?,?,'manual','queued',?,'等待执行。',?)", (task_id, task["name"], stamp(), task["config_revision"]))
            return True

    def enqueue_due(self, now=None):
        now = now or utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            tasks = db.execute("SELECT * FROM tasks WHERE enabled=1 AND next_run_at<=?", (stamp(now),)).fetchall()
            for task in tasks:
                active = db.execute("SELECT 1 FROM runs WHERE task_id=? AND status IN ('queued','running')", (task["id"],)).fetchone()
                if active:
                    continue
                zone = ZoneInfo(task["timezone"])
                today = now.astimezone(zone).date()
                successes = db.execute("SELECT started_at FROM runs WHERE task_id=? AND status IN ('success','already_done') ORDER BY id DESC LIMIT 1", (task["id"],)).fetchone()
                done_today = bool(successes and datetime.fromisoformat(successes["started_at"]).astimezone(zone).date() == today)
                # One catch-up request after downtime; never replay every missed day.
                slot = str(today)
                if not done_today:
                    db.execute("INSERT OR IGNORE INTO runs(task_id,task_name,kind,slot,status,queued_at,message,config_revision) VALUES (?,?,'scheduled',?,'queued',?,'等待执行。',?)", (task["id"], task["name"], slot, stamp(now), task["config_revision"]))
                due = stamp(next_run(task["daily_time"], task["timezone"], now))
                db.execute("UPDATE tasks SET next_run_at=? WHERE id=?", (due, task["id"]))

    def claim_next(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE status='queued' AND task_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            task = db.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            db.execute("UPDATE runs SET status='running',started_at=?,message='正在执行。' WHERE id=?", (stamp(), row["id"]))
            return dict(row), dict(task)

    def finish(self, run_id, result, task_snapshot=None):
        statuses = {"success", "already_done", "needs_login", "needs_verification", "unsupported", "failed"}
        status = result.get("status", "success" if result["ok"] else "failed")
        if status not in statuses or (status in {"success", "already_done"}) != bool(result["ok"]):
            status = "failed"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE runs SET status=?,finished_at=?,message=?,duration_ms=?,status_code=? WHERE id=?", (status, stamp(), result["message"], result["duration_ms"], result.get("status_code"), run_id))
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run and run["kind"] == "manual" and status in {"success", "already_done"}:
                db.execute("UPDATE tasks SET verified_revision=config_revision WHERE id=? AND config_revision=?",
                           (run["task_id"], run["config_revision"]))
            if status in {"needs_login", "needs_verification"}:
                if task_snapshot is None:
                    db.execute("UPDATE tasks SET enabled=0 WHERE id=(SELECT task_id FROM runs WHERE id=?)", (run_id,))
                else:
                    # An old browser run may finish after the administrator has
                    # replaced its credentials and enabled the updated task.
                    db.execute("UPDATE tasks SET enabled=0 WHERE id=(SELECT task_id FROM runs WHERE id=?) AND sealed_spec=?", (run_id, task_snapshot["sealed_spec"]))
            db.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 1000) AND status NOT IN ('queued','running')")

    def refresh_session(self, task_snapshot, state):
        spec = self.spec(task_snapshot)
        if spec.get("engine") != "browser":
            return
        from .browser_runner import validate_browser_spec
        spec["storage_state"] = state
        spec = validate_browser_spec(spec, allow_private=True)
        sealed = self.cipher.encrypt(json.dumps(spec, ensure_ascii=False).encode()).decode()
        with self.connect() as db:
            # An administrator may replace the session while a request is running.
            db.execute("UPDATE tasks SET sealed_spec=? WHERE id=? AND sealed_spec=?",
                       (sealed, task_snapshot["id"], task_snapshot["sealed_spec"]))

    def recent_runs(self, limit=100):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]

    def latest_runs(self):
        with self.connect() as db:
            rows = db.execute("SELECT r.* FROM runs r JOIN (SELECT task_id,MAX(id) AS id FROM runs WHERE task_id IS NOT NULL GROUP BY task_id) latest ON latest.id=r.id")
            return {r["task_id"]: dict(r) for r in rows}

    def recover_runs(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted',finished_at=?,message='服务重启，执行结果未知。请确认网站状态后手动重试。' WHERE status IN ('queued','running')", (stamp(),))
