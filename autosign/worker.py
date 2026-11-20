import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .executors import execute

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, allow_private=False, runner=execute):
        self.store = store
        self.allow_private = allow_private
        self.runner = runner
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="autosign-scheduler", daemon=True)
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="autosign-request")
        self.pending = set()
        self.last_tick = 0

    def start(self):
        self.store.recover_runs()
        self.thread.start()

    def healthy(self):
        return self.thread.is_alive() and time.monotonic() - self.last_tick < 30

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        self.pool.shutdown(wait=True, cancel_futures=False)

    def execute(self, run, task):
        try:
            result = self.runner(self.store.spec(task), allow_private=self.allow_private)
            refreshed = result.pop("_storage_state", None)
            if refreshed is not None and result.get("ok"):
                try:
                    self.store.refresh_session(task, refreshed)
                except Exception:
                    logger.error("Browser session refresh failed; check the task before its next run.")
        except Exception:
            # Exception strings may contain credentials. Store only a fixed message.
            result = {"ok": False, "status_code": None, "message": "执行异常，请检查任务配置。", "duration_ms": 0}
        self.store.finish(run["id"], result, task_snapshot=task)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.pending = {f for f in self.pending if not f.done()}
                self.store.enqueue_due()
                while len(self.pending) < 3 and not self.stop_event.is_set():
                    job = self.store.claim_next()
                    if job is None:
                        break
                    self.pending.add(self.pool.submit(self.execute, *job))
                self.last_tick = time.monotonic()
            except Exception:
                logger.error("Scheduler iteration failed; inspect database availability.")
            self.stop_event.wait(1)
