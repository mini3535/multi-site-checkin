import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .runner import validate_request
from .catalog import CATALOG_SITES, SITES
from .executors import validate_spec
from .schedule import display_time, validate_schedule
from .storage import Store


def env_bool(name, default=False):
    value = os.environ.get(name, str(default)).lower()
    if value not in ("true", "false", "1", "0"):
        raise ValueError(f"{name} 必须为 true 或 false。")
    return value in ("true", "1")


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(
        DATA_DIR=os.environ.get("AUTOSIGN_DATA_DIR", "data"),
        ADMIN_PASSWORD=os.environ.get("AUTOSIGN_ADMIN_PASSWORD", ""),
        ENCRYPTION_KEY=os.environ.get("AUTOSIGN_SECRET_KEY", ""),
        DEFAULT_TIMEZONE=os.environ.get("AUTOSIGN_TIMEZONE", "Asia/Shanghai"),
        ALLOW_PRIVATE_TARGETS=env_bool("AUTOSIGN_ALLOW_PRIVATE_TARGETS"),
        SESSION_COOKIE_SECURE=env_bool("AUTOSIGN_COOKIE_SECURE"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=768 * 1024,
        TRUSTED_HOSTS=None,
    )
    if overrides:
        app.config.update(overrides)
    password = app.config["ADMIN_PASSWORD"]
    key = app.config["ENCRYPTION_KEY"]
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise ValueError("请设置 12 至 256 字符的 AUTOSIGN_ADMIN_PASSWORD，或先执行 setup.sh。")
    if not key:
        raise ValueError("请设置 AUTOSIGN_SECRET_KEY，或先执行 setup.sh。")
    validate_schedule("08:00", app.config["DEFAULT_TIMEZONE"])
    store = Store(app.config["DATA_DIR"], key)
    app.config["SECRET_KEY"] = hashlib.sha256(("autosign-session:" + key).encode()).digest()
    password_hash = generate_password_hash(password)
    app.extensions["store"] = store
    attempts = {}
    attempt_lock = threading.Lock()

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect():
        if request.endpoint not in ("login", "static", "healthz") and not session.get("authenticated"):
            return redirect(url_for("login"))
        if request.method == "POST":
            token = request.form.get("csrf_token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
                abort(400, description="页面已过期，请刷新页面后重试。")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz():
        with store.connect() as db:
            db.execute("SELECT 1")
        worker = app.extensions.get("worker")
        healthy = worker is None or worker.healthy()
        return {"status": "ok" if healthy else "unavailable"}, 200 if healthy else 503

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            ip = request.remote_addr or "unknown"
            now = time.monotonic()
            with attempt_lock:
                for old_ip in list(attempts):
                    attempts[old_ip] = [t for t in attempts[old_ip] if t > now - 300]
                    if not attempts[old_ip]:
                        del attempts[old_ip]
                blocked = len(attempts.get(ip, [])) >= 5
                # Count before password verification so parallel attempts cannot bypass the limit.
                if not blocked:
                    if len(attempts) >= 10000 and ip not in attempts:
                        blocked = True
                    else:
                        attempts.setdefault(ip, []).append(now)
            supplied = request.form.get("password", "")
            if blocked:
                error = "尝试次数过多，请在 5 分钟后重试。"
            elif len(supplied) <= 256 and check_password_hash(password_hash, supplied):
                with attempt_lock:
                    attempts.pop(ip, None)
                session.clear()
                session["authenticated"] = True
                session.permanent = True
                return redirect(url_for("index"))
            else:
                error = "登录密码不正确。"
        return render_template("login.html", error=error), 429 if error and "5 分钟" in error else 200

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        tasks = store.list_tasks()
        latest = store.latest_runs()
        for task in tasks:
            spec = store.spec(task)
            engine = spec.get("engine", "http")
            site = SITES.get(spec.get("site_id"), {})
            parsed = urlsplit(spec.get("url") or spec.get("start_url") or site.get("start_url", ""))
            # Query parameters may contain tokens; keep them off the dashboard.
            task["url"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            task["method"] = spec.get("method", "网页" if engine == "browser" else "站点")
            task["engine"] = engine
            task["site_name"] = site.get("name", "HTTP")
            task["edit_endpoint"] = "task_edit" if engine == "http" else "site_edit"
            task["next_run_display"] = display_time(task["next_run_at"], task["timezone"])
            task["last_run"] = latest.get(task["id"])
            if task["last_run"]:
                run = task["last_run"]
                run["started_display"] = display_time(run["started_at"] or run["queued_at"], task["timezone"])
                run["ok"] = run["status"] in ("success", "already_done")
        logs = store.recent_runs(50)
        for run in logs:
            run["started_display"] = display_time(run["started_at"] or run["queued_at"], app.config["DEFAULT_TIMEZONE"])
        stats = {"total": len(tasks), "enabled": sum(bool(t["enabled"]) for t in tasks),
                 "success": sum(r["status"] in ("success", "already_done") for r in latest.values()),
                 "failed": sum(r["status"] in ("failed", "interrupted", "needs_login", "needs_verification", "unsupported") for r in latest.values())}
        return render_template("index.html", tasks=tasks, logs=logs, stats=stats, log_timezone=app.config["DEFAULT_TIMEZONE"])

    def required_task(task_id):
        task = store.get_task(task_id)
        if task is None:
            abort(404, description="任务不存在。")
        return task

    def task_form(task_id=None):
        task = required_task(task_id) if task_id is not None else {}
        original = store.spec(task) if task else {}
        if original.get("engine", "http") != "http":
            return redirect(url_for("site_edit", task_id=task_id), code=303)
        form = {"name": task.get("name", ""), "url": original.get("url", ""),
                "method": original.get("method", "POST"), "headers": "", "body": "",
                "success_type": original.get("success_type", "json"),
                "success_path": original.get("success_path", ""),
                "success_value": original.get("success_value", ""),
                "daily_time": task.get("daily_time", "08:00"),
                "timezone": task.get("timezone", app.config["DEFAULT_TIMEZONE"]),
                "enabled": bool(task.get("enabled", True))}
        error = None
        if request.method == "POST":
            for name in form:
                form[name] = request.form.get(name, "")
            form["enabled"] = request.form.get("enabled") in ("on", "true", "1")
            try:
                form["name"] = form["name"].strip()
                if not 1 <= len(form["name"]) <= 80 or any(ord(c) < 32 for c in form["name"]):
                    raise ValueError("任务名称需为 1 至 80 字符。")
                validate_schedule(form["daily_time"], form["timezone"])
                clear = request.form.get("clear_credentials") in ("on", "true", "1")
                if clear:
                    headers, body = {}, ""
                else:
                    headers = json.loads(form["headers"]) if form["headers"].strip() else original.get("headers", {})
                    body = form["body"] if form["body"] else original.get("body", "")
                spec = validate_request({"url": form["url"].strip(), "method": form["method"], "headers": headers, "body": body,
                                         "success_type": form["success_type"], "success_path": form["success_path"].strip(),
                                         "success_value": form["success_value"]}, allow_private=app.config["ALLOW_PRIVATE_TARGETS"])
                store.save_task(form, spec, task_id)
                flash("任务已保存。可先点击「立即执行」验证配置。", "success")
                return redirect(url_for("index"))
            except json.JSONDecodeError:
                error = "请求头必须为 JSON 对象，例如 {\"Cookie\": \"你的 Cookie\"}。"
            except (ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, ValueError) else "任务配置格式不正确。"
            # Never echo submitted secrets back into an error page.
            form["headers"] = ""
            form["body"] = ""
        return render_template("form.html", task=task, editing=bool(task), form=form, error=error,
                               has_credentials=bool(original.get("headers") or original.get("body"))), 400 if error else 200

    @app.route("/tasks/new", methods=["GET", "POST"])
    def task_new():
        return task_form()

    @app.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    def task_edit(task_id):
        return task_form(task_id)

    @app.get("/sites")
    def site_catalog():
        return render_template("site_catalog.html", sites=CATALOG_SITES)

    def site_form(site_id, task_id=None):
        site = SITES.get(site_id)
        if site is None or site.get("engine") == "info":
            abort(404)
        task = required_task(task_id) if task_id is not None else {}
        original = store.spec(task) if task else {}
        engine = site["engine"]
        form = {"name": task.get("name", site["name"] + " · 主账号"),
                "cookie": "", "start_url": original.get("start_url", site.get("start_url", "")),
                "allowed_hosts": "\n".join(original.get("allowed_hosts", site.get("allowed_hosts", []))),
                "steps": json.dumps(original.get("steps", []), ensure_ascii=False, indent=2),
                "success_text": original.get("success_text", ""),
                "already_done_text": original.get("already_done_text", ""),
                "login_selector": original.get("login_selector", ""),
                "verification_selector": original.get("verification_selector", ""),
                "storage_state": "",
                "daily_time": task.get("daily_time", "08:00"),
                "timezone": task.get("timezone", app.config["DEFAULT_TIMEZONE"]),
                # New website rules need a deliberate manual validation before scheduling.
                "enabled": bool(task.get("enabled", False))}
        error = None
        if request.method == "POST":
            for name in form:
                form[name] = request.form.get(name, "")
            form["enabled"] = request.form.get("enabled") in ("1", "true", "on")
            try:
                form["name"] = form["name"].strip()
                if not 1 <= len(form["name"]) <= 80 or any(ord(c) < 32 for c in form["name"]):
                    raise ValueError("任务名称需为 1 至 80 字符。")
                validate_schedule(form["daily_time"], form["timezone"])
                clear = request.form.get("clear_session") in ("1", "true", "on")
                if clear:
                    form["enabled"] = False
                if engine == "site":
                    cookie = "" if clear else form["cookie"] or original.get("cookie", "")
                    spec = {"engine": "site", "site_id": site_id, "cookie": cookie}
                else:
                    upload = request.files.get("storage_state_file")
                    if upload and upload.filename and form["storage_state"].strip():
                        raise ValueError("会话文件与会话文本请只填写一项。")
                    if clear:
                        state = {"cookies": [], "origins": []}
                    elif upload and upload.filename:
                        raw = upload.read(512 * 1024 + 1)
                        if len(raw) > 512 * 1024:
                            raise ValueError("会话文件最多为 512 KiB。")
                        state = json.loads(raw.decode("utf-8-sig"))
                    elif form["storage_state"].strip():
                        state = json.loads(form["storage_state"])
                    else:
                        state = original.get("storage_state", {"cookies": [], "origins": []})
                    spec = {"engine": "browser", "site_id": site_id,
                            "start_url": form["start_url"].strip(),
                            "allowed_hosts": [h.strip() for h in form["allowed_hosts"].splitlines() if h.strip()],
                            "steps": json.loads(form["steps"]), "storage_state": state,
                            "success_text": form["success_text"].strip(),
                            "already_done_text": form["already_done_text"].strip(),
                            "login_selector": form["login_selector"].strip(),
                            "verification_selector": form["verification_selector"].strip()}
                spec = validate_spec(spec, allow_private=app.config["ALLOW_PRIVATE_TARGETS"])
                if not task:
                    if engine == "site" and not spec.get("cookie"):
                        raise ValueError("新建网站任务时，请填写有效的登录 Cookie。")
                    state = spec.get("storage_state", {})
                    if engine == "browser" and not (state.get("cookies") or state.get("origins")):
                        raise ValueError("新建网站任务时，请导入包含该网站登录状态的会话 JSON。")
                store.save_task(form, spec, task_id)
                flash("网站任务已保存。请先手动执行，并到网站核对签到状态。", "success")
                return redirect(url_for("index"))
            except (json.JSONDecodeError, UnicodeError):
                error = "步骤或会话不是有效 JSON。请重新选择会话文件；登录凭据不会在页面中回显。"
            except (ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, ValueError) else "网站配置格式不正确。"
            form["cookie"] = ""
            form["storage_state"] = ""
        saved_state = original.get("storage_state", {})
        has_session = bool(original.get("cookie") or saved_state.get("cookies") or saved_state.get("origins"))
        return render_template("site_form.html", site=site, form=form, task=task, editing=bool(task),
                               engine=engine, error=error, has_session=has_session), 400 if error else 200

    @app.route("/sites/<site_id>/new", methods=["GET", "POST"])
    def site_new(site_id):
        return site_form(site_id)

    @app.route("/tasks/<int:task_id>/site-edit", methods=["GET", "POST"])
    def site_edit(task_id):
        task = required_task(task_id)
        spec = store.spec(task)
        if spec.get("engine", "http") == "http":
            return redirect(url_for("task_edit", task_id=task_id), code=303)
        return site_form(spec.get("site_id"), task_id)

    @app.post("/tasks/<int:task_id>/toggle")
    def task_toggle(task_id):
        task = required_task(task_id)
        store.toggle_task(task_id)
        flash("任务已暂停。正在发送的请求会继续完成。" if task["enabled"] else "任务已启用。", "success")
        return redirect(url_for("index"))

    @app.post("/tasks/<int:task_id>/run")
    def task_run(task_id):
        required_task(task_id)
        accepted = store.enqueue_manual(task_id)
        flash("已加入执行队列，稍后刷新查看结果。" if accepted else "任务已在执行或等待中。", "success" if accepted else "info")
        return redirect(url_for("index"))

    @app.post("/tasks/<int:task_id>/delete")
    def task_delete(task_id):
        required_task(task_id)
        store.delete_task(task_id)
        flash("任务已删除。", "success")
        return redirect(url_for("index"))

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def expected_error(exc):
        messages = {400: "请求无效，请刷新页面后重试。", 404: "页面或任务不存在。", 413: "配置过大，提交内容最多为 768 KiB。"}
        return render_template("error.html", message=messages.get(exc.code, "请求失败。"), code=exc.code), exc.code

    return app
