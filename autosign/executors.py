"""Dispatch requests with a total deadline, including DNS and slow responses.

HTTP and fixed site adapters run in a private child process. Browser workflows
already provide their own process isolation and are dispatched directly.
"""
import json
import os
import signal
import subprocess
import sys
import time

from .runner import run_request, validate_request

MAX_RUN_SECONDS = 90
MAX_PAYLOAD_BYTES = 768 * 1024
_MESSAGES = {
    "success": "签到成功条件已满足。",
    "already_done": "网站显示今日已签到。",
    "needs_login": "登录已失效，请更新网站凭据。",
    "needs_verification": "网站需要人工验证，请完成正常验证后更新凭据。",
    "unsupported": "网站页面或接口暂不支持，请检查任务配置。",
    "failed": "未确认签到成功，请核对网站状态和任务配置。",
}


def validate_spec(spec, allow_private=False):
    if not isinstance(spec, dict):
        raise ValueError("任务配置必须是对象。")
    engine = spec.get("engine", "http")
    if engine == "http":
        return validate_request(spec, allow_private=allow_private)
    if engine == "site":
        from .site_adapters import validate_site_spec
        return validate_site_spec(spec, allow_private=allow_private)
    if engine == "browser":
        from .browser_runner import validate_browser_spec
        return validate_browser_spec(spec, allow_private=allow_private)
    raise ValueError("不支持的执行方式。")


def execute(spec, allow_private=False):
    started = time.monotonic()

    def result(status, code=None, message=None):
        return {"ok": status in {"success", "already_done"}, "status": status,
                "status_code": code, "message": message or _MESSAGES[status],
                "duration_ms": round((time.monotonic() - started) * 1000)}

    engine = spec.get("engine", "http") if isinstance(spec, dict) else None
    if engine == "browser":
        from .browser_runner import run_browser
        return run_browser(spec, allow_private=allow_private)
    if engine not in {"http", "site"}:
        return result("unsupported")
    try:
        normalized = validate_spec(spec, allow_private=allow_private)
        payload = json.dumps({"engine": engine, "spec": normalized,
                              "allow_private": bool(allow_private)},
                             ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        return result("failed", message="任务配置无效，请检查后再执行。")

    process = None
    try:
        process = subprocess.Popen([sys.executable, "-m", "autosign.executors", "--worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            stdout, _stderr = process.communicate(payload, timeout=MAX_RUN_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_request(process)
            process.communicate()
            return result("failed", message="执行超时，执行结果未知，请先核对网站状态。")
        if process.returncode or len(stdout) > MAX_PAYLOAD_BYTES:
            raise ValueError()
        output = json.loads(stdout)
        if not isinstance(output, dict) or type(output.get("ok")) is not bool:
            raise ValueError()
        status = output.get("status", "success" if output["ok"] else "failed")
        code = output.get("status_code")
        if (not isinstance(status, str) or status not in _MESSAGES
                or output["ok"] != (status in {"success", "already_done"})
                or code is not None and (type(code) is not int or not 100 <= code <= 599)):
            raise ValueError()
        # Do not trust or forward child text: protocol failures, exceptions and
        # remote content may contain the submitted Cookie or request body.
        return result(status, code)
    except (OSError, ValueError, TypeError, RecursionError):
        return result("failed", message="执行结果无效，执行结果未知，请先核对网站状态。")
    finally:
        if process is not None and process.poll() is None:
            _kill_request(process)
            process.communicate()


def _kill_request(process):
    # Only the newly created request process group is targeted.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _child_main():
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError()
        payload = json.loads(raw)
        engine = payload["engine"]
        spec = payload["spec"]
        allow_private = payload.get("allow_private") is True
        if engine == "http":
            output = run_request(spec, allow_private=allow_private)
        elif engine == "site":
            from .site_adapters import run_site
            output = run_site(spec, allow_private=allow_private)
        else:
            raise ValueError()
    except Exception:
        output = {"ok": False, "status": "failed", "status_code": None}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False))
    sys.stdout.flush()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _child_main()
