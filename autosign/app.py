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
from .account_login import LoginError, LoginManager, credentials, website_credentials
from .xingchi import task_spec as xingchi_spec


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
    store.initialize_admin(generate_password_hash(password), secrets.token_urlsafe(32))
    app.extensions["store"] = store
    account_login = LoginManager()
    app.extensions["account_login"] = account_login
    attempts = {}
    attempt_lock = threading.Lock()

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect():
        if session.get("authenticated") and session.get("auth_version") != store.admin_auth()["version"]:
            account_login.cancel_owner(session.get("login_owner"))
            session.clear()
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
            else:
                auth = store.admin_auth()
                if len(supplied) <= 256 and check_password_hash(auth["password_hash"], supplied):
                    with attempt_lock:
                        attempts.pop(ip, None)
                    session.clear()
                    session["authenticated"] = True
                    session["auth_version"] = auth["version"]
                    session.permanent = True
                    return redirect(url_for("index"))
                error = "登录密码不正确。"
        return render_template("login.html", error=error), 429 if error and "5 分钟" in error else 200

    @app.route("/settings/password", methods=["GET", "POST"])
    def change_password():
        error = None
        status = 200
        if request.method == "POST":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirmation = request.form.get("confirm_password", "")
            auth = store.admin_auth()
            now = time.monotonic()
            # Bound current-password guesses, including across authenticated sessions.
            with attempt_lock:
                recent = [t for t in attempts.get("password-change", []) if t > now - 300]
                blocked = len(recent) >= 5
                if not blocked:
                    recent.append(now)
                attempts["password-change"] = recent
            if blocked:
                error, status = "尝试次数过多，请在 5 分钟后重试。", 429
            elif len(current) > 256 or not check_password_hash(auth["password_hash"], current):
                error = "当前密码不正确。"
            elif not 12 <= len(new) <= 256:
                error = "新密码需为 12 至 256 字符。"
            elif new != confirmation:
                error = "两次输入的新密码不一致。"
            elif hmac.compare_digest(current.encode(), new.encode()):
                error = "新密码不能与当前密码相同。"
            elif not store.change_admin_password(auth["version"], generate_password_hash(new), secrets.token_urlsafe(32)):
                session.clear()
                return redirect(url_for("login"))
            else:
                with attempt_lock:
                    attempts.pop("password-change", None)
                account_login.cancel_all()
                session.clear()
                flash("密码已修改，所有设备已退出。请使用新密码重新登录。", "success")
                return redirect(url_for("login"))
            if status == 200:
                status = 400
        return render_template("password.html", error=error), status

    @app.post("/logout")
    def logout():
        account_login.cancel_owner(session.get("login_owner"))
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
            task["verified"] = store.verification(task["id"])["ready"] if engine != "http" else False
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
        return render_template("site_catalog.html", sites=CATALOG_SITES, flow_step=1)

    def login_owner():
        if "login_owner" not in session:
            session["login_owner"] = secrets.token_urlsafe(32)
        return session["login_owner"]

    def xingchi_form(task_id=None):
        task = required_task(task_id) if task_id is not None else {}
        original = store.spec(task) if task else {}
        if task and (original.get("engine") != "browser"
                     or original.get("start_url") != SITES["xingchi"]["start_url"]):
            abort(404)
        form = {"name": task.get("name", "星驰 API · 主账号")}
        error = None
        if request.method == "POST":
            form["name"] = request.form.get("name", "").strip()
            try:
                if not 1 <= len(form["name"]) <= 80 or any(ord(c) < 32 for c in form["name"]):
                    raise LoginError("任务名称需为 1 至 80 字符。")
                if request.form.get("keep_session") == "1" and task:
                    store.save_task({**task, "name": form["name"]}, original, task_id)
                    return redirect(url_for("site_verify", task_id=task_id))
                email, password = credentials(request.form.get("email", "").strip(), request.form.get("website_password", ""))
                key = account_login.start(login_owner(), form["name"], email, password,
                                          task_id, task.get("config_revision"))
                return redirect(url_for("account_login_page", login_id=key))
            except LoginError as exc:
                error = str(exc)
            except (ValueError, OSError):
                # Fixed error messages only; browser/OS details may include credentials.
                error = "无法连接账号。请检查邮箱和密码；若已有登录窗口，请先完成或取消。浏览器启动失败时请检查服务器环境。"
        pending_login = account_login.pending(login_owner())
        return render_template("account_login_new.html", site=SITES["xingchi"], form=form,
                               task=task, editing=bool(task), error=error, flow_step=2,
                               pending_login=pending_login), 400 if error else 200

    @app.route("/tasks/<int:task_id>/account-login", methods=["GET", "POST"])
    def task_account_login(task_id):
        return xingchi_form(task_id)

    @app.get("/account-logins/<login_id>")
    def account_login_page(login_id):
        try:
            item = account_login.get(login_id, login_owner())
        except LoginError:
            abort(404)
        return render_template("account_login.html", login_id=login_id, name=item.name,
                               site=SITES[getattr(item, "spec", None)["site_id"]] if getattr(item, "spec", None) else SITES["xingchi"],
                               generic=bool(getattr(item, "spec", None)), flow_step=2)

    @app.get("/account-logins/<login_id>/frame")
    def account_login_frame(login_id):
        try:
            return account_login.get(login_id, login_owner()).send({"action": "frame"})
        except LoginError as exc:
            return {"error": str(exc)}, 400

    @app.post("/account-logins/<login_id>/action")
    def account_login_action(login_id):
        try:
            action = request.form.get("action")
            if action not in {"click", "type", "key", "scroll"}:
                raise LoginError("不支持的登录操作。")
            command = {"action": action, "revision": int(request.form.get("revision", "-1"))}
            if action == "click":
                command.update(x=float(request.form.get("x", "-1")), y=float(request.form.get("y", "-1")))
            for key in ("text", "key", "direction"):
                if key in request.form:
                    command[key] = request.form[key]
            return account_login.get(login_id, login_owner()).send(command)
        except (LoginError, ValueError) as exc:
            return {"error": str(exc) if isinstance(exc, LoginError) else "登录参数无效。"}, 400

    @app.post("/account-logins/<login_id>/save")
    def account_login_save(login_id):
        try:
            with account_login.lock:
                item = account_login.get(login_id, login_owner())
                with item.lock:
                    task = required_task(item.task_id) if item.task_id is not None else {}
                    if task and task["config_revision"] != item.revision:
                        raise LoginError("任务已修改，请取消此窗口并重新连接账号。")
                    base_spec = getattr(item, "spec", None)
                    if base_spec and request.form.get("confirmed") != "1":
                        raise LoginError("请先在登录画面确认网站已登录。")
                    result = item.send({"action": "finish"})
                    spec = validate_spec({**base_spec, "storage_state": result["state"]} if base_spec else xingchi_spec(result["state"]))
                    fields = {"name": item.name, "enabled": False,
                              "daily_time": task.get("daily_time", "08:00"),
                              "timezone": task.get("timezone", app.config["DEFAULT_TIMEZONE"])}
                    saved_id = store.save_task(fields, spec, item.task_id,
                                               expected_revision=item.revision)
                    account_login.cancel(login_id, login_owner())
            flash("登录会话已加密保存。下一步验证签到，成功后设置每日执行。", "success")
            return redirect(url_for("site_verify", task_id=saved_id))
        except (ValueError, KeyError):
            flash("账号尚未登录、窗口已过期或任务已更改，请检查登录画面或重新连接。", "error")
            return redirect(url_for("account_login_page", login_id=login_id))

    @app.post("/account-logins/release")
    def account_login_release():
        # Explicit admin action; release exactly the window displayed by the
        # form so a stale tab cannot cancel a newly opened login window.
        account_login.release(request.form.get("login_id", ""))
        flash("占用的登录窗口已关闭。已保存的任务和账号会话不受影响，请重新连接。", "success")
        task_id = request.form.get("task_id", type=int)
        if task_id is not None and store.get_task(task_id):
            return redirect(url_for("site_edit", task_id=task_id))
        return redirect(url_for("site_new", site_id="custom"))

    @app.post("/account-logins/<login_id>/cancel")
    def account_login_cancel(login_id):
        site_id = "custom"
        task_id = None
        try:
            item = account_login.get(login_id, login_owner())
            site_id = item.spec["site_id"] if getattr(item, "spec", None) else "xingchi"
            task_id = item.task_id
            account_login.cancel(login_id, login_owner())
        except LoginError:
            pass
        if task_id is not None and store.get_task(task_id):
            return redirect(url_for("site_edit", task_id=task_id))
        return redirect(url_for("site_new", site_id=site_id))

    def site_form(site_id, task_id=None):
        if site_id == "xingchi":
            return xingchi_form(task_id)
        site = SITES.get(site_id)
        if site is None or site.get("engine") == "info":
            abort(404)
        task = required_task(task_id) if task_id is not None else {}
        original = store.spec(task) if task else {}
        engine = site["engine"]
        platform = bool(site.get("platform"))
        credential_fields = site.get("fields", [])
        form = {"name": task.get("name", site["name"] + " · 主账号"),
                "cookie": "", "start_url": original.get("start_url", site.get("start_url", "")),
                "allowed_hosts": "\n".join(original.get("allowed_hosts", site.get("allowed_hosts", []))),
                "button_text": "",
                "steps": json.dumps(original.get("steps", []), ensure_ascii=False, indent=2),
                "success_text": original.get("success_text", ""),
                "already_done_text": original.get("already_done_text", ""),
                "login_selector": original.get("login_selector", ""),
                "verification_selector": original.get("verification_selector", ""),
                "storage_state": "",
                "browser_cookie": "",
                "browser_token": "",
                "login_url": original.get("browser_login", {}).get("url", ""),
                "token_storage_key": original.get("token_login", {}).get("storage_key", ""),
                "connection_method": ("password" if original.get("browser_login") else "token" if original.get("token_login") else
                                      "session" if original.get("storage_state", {}).get("origins") else "password" if not task else "cookie"),
                "daily_time": task.get("daily_time", "08:00"),
                "timezone": task.get("timezone", app.config["DEFAULT_TIMEZONE"]),
                # New website rules need a deliberate manual validation before scheduling.
                "enabled": bool(task.get("enabled", False))}
        if platform:
            for item in credential_fields:
                form[item["key"]] = "" if item["secret"] else original.get(item["key"], "")
        if original.get("steps"):
            first = original["steps"][0]
            selector = first.get("selector", "") if isinstance(first, dict) else ""
            if first.get("action") == "click" and selector.startswith("button:has-text("):
                try:
                    form["button_text"] = json.loads(selector[len("button:has-text("):-1])
                except (json.JSONDecodeError, TypeError):
                    pass
        error = None
        if request.method == "POST":
            for name in form:
                form[name] = request.form.get(name, "")
            # Scheduling is a separate step after a successful verification.
            form["enabled"] = bool(task.get("enabled", False))
            form["daily_time"] = request.form.get("daily_time", task.get("daily_time", "08:00"))
            form["timezone"] = request.form.get("timezone", task.get("timezone", app.config["DEFAULT_TIMEZONE"]))
            try:
                form["name"] = form["name"].strip()
                if not 1 <= len(form["name"]) <= 80 or any(ord(c) < 32 for c in form["name"]):
                    raise ValueError("任务名称需为 1 至 80 字符。")
                validate_schedule(form["daily_time"], form["timezone"])
                clear = request.form.get("clear_session") in ("1", "true", "on")
                if clear:
                    form["enabled"] = False
                if platform:
                    if not task:
                        form["enabled"] = False
                    spec = {"engine": "site", "site_id": site_id}
                    for item in credential_fields:
                        key = item["key"]
                        if clear and item["secret"]:
                            spec[key] = ""
                        elif item["secret"]:
                            spec[key] = form[key] or original.get(key, "")
                        else:
                            spec[key] = form[key]
                    if not clear and any(not spec.get(item["key"]) for item in credential_fields):
                        raise ValueError("请填写此平台所需的全部登录凭据和连接信息。")
                elif engine == "site":
                    cookie = "" if clear else form["cookie"] or original.get("cookie", "")
                    spec = {"engine": "site", "site_id": site_id, "cookie": cookie}
                else:
                    start_url = form["start_url"].strip()
                    allowed_hosts = [h.strip() for h in form["allowed_hosts"].splitlines() if h.strip()]
                    if not allowed_hosts:
                        hostname = urlsplit(start_url).hostname
                        if hostname:
                            allowed_hosts = [hostname]
                    button_text = form["button_text"].strip()
                    steps = ([{"action": "click",
                               "selector": "button:has-text(" + json.dumps(button_text, ensure_ascii=False) + ")",
                               "timeout_ms": 10000}] if button_text else json.loads(form["steps"] or "[]"))
                    if not steps:
                        raise ValueError("请填写签到按钮文字，或在高级设置中填写页面步骤。")
                    upload = request.files.get("storage_state_file")
                    supplied_cookie = form["browser_cookie"].strip()
                    supplied_token = form["browser_token"].strip()
                    supplied_file = bool(upload and upload.filename)
                    supplied_text = bool(form["storage_state"].strip())
                    if sum((bool(supplied_cookie), bool(supplied_token), supplied_file, supplied_text)) > 1:
                        raise ValueError("Cookie、Token、会话文件和会话文本请只填写一项。")
                    method = form["connection_method"]
                    if method and method not in ("password", "cookie", "token", "session"):
                        raise ValueError("请选择有效的账号连接方式。")
                    supplied_method = ("cookie" if supplied_cookie else "token" if supplied_token else
                                       "session" if supplied_file or supplied_text else "")
                    username = request.form.get("website_username", "").strip()
                    password = request.form.get("website_password", "")
                    if method == "password" and (username or password):
                        if supplied_method or clear:
                            raise ValueError("账号密码登录时，请勿同时提供其他凭据或清空会话。")
                        website_credentials(username, password)
                        supplied_method = "password"
                    elif method != "password" and (username or password):
                        raise ValueError("登录凭据与所选连接方式不一致。")
                    if method and supplied_method and method != supplied_method:
                        raise ValueError("登录凭据与所选连接方式不一致，请只填写所选方式。")
                    original_method = ("password" if original.get("browser_login") else "token" if original.get("token_login") else
                                       "session" if original.get("storage_state", {}).get("origins") else "cookie")
                    if task and not clear and not supplied_method and method and method != original_method:
                        raise ValueError("切换连接方式时，请填写新的登录凭据。")
                    if (task and original.get("token_login") and not clear and not supplied_method
                            and form["token_storage_key"].strip()
                            and form["token_storage_key"].strip() != original["token_login"]["storage_key"]):
                        raise ValueError("更改 Token 存储字段时，请重新粘贴登录 Token。")
                    form["connection_method"] = method or supplied_method or original_method
                    if method == "password" and not clear and not supplied_method and not task:
                        raise ValueError("请填写网站账号和密码。")
                    if (method == "password" and not clear and not supplied_method and task
                            and form["login_url"].strip() != original.get("browser_login", {}).get("url")):
                        raise ValueError("更改登录页后，请重新输入账号和密码连接。")
                    token_login = None
                    if clear:
                        state = {"cookies": [], "origins": []}
                    elif supplied_method == "password":
                        state = {"cookies": [], "origins": []}
                    elif supplied_token:
                        from .browser_auth import token_storage_state
                        state, token_login = token_storage_state(supplied_token, start_url, form["token_storage_key"])
                    elif supplied_cookie:
                        from .browser_runner import cookie_storage_state
                        state = cookie_storage_state(supplied_cookie, start_url)
                    elif upload and upload.filename:
                        raw = upload.read(512 * 1024 + 1)
                        if len(raw) > 512 * 1024:
                            raise ValueError("会话文件最多为 512 KiB。")
                        state = json.loads(raw.decode("utf-8-sig"))
                    elif form["storage_state"].strip():
                        state = json.loads(form["storage_state"])
                    else:
                        state = original.get("storage_state", {"cookies": [], "origins": []})
                        token_login = original.get("token_login")
                    spec = {"engine": "browser", "site_id": site_id,
                            "start_url": start_url,
                            "allowed_hosts": allowed_hosts,
                            "steps": steps, "storage_state": state,
                            "success_text": form["success_text"].strip(),
                            "already_done_text": form["already_done_text"].strip(),
                            "login_selector": form["login_selector"].strip(),
                            "verification_selector": form["verification_selector"].strip()}
                    if token_login is not None:
                        spec["token_login"] = token_login
                    if form["connection_method"] == "password":
                        spec["browser_login"] = {"url": form["login_url"].strip()}
                spec = validate_spec(spec, allow_private=app.config["ALLOW_PRIVATE_TARGETS"])
                if engine == "browser" and supplied_method == "password":
                    key = account_login.start(login_owner(), form["name"], username, password,
                                              task_id, task.get("config_revision"), spec=spec)
                    return redirect(url_for("account_login_page", login_id=key))
                if platform and not clear and any(not spec.get(item["key"]) for item in credential_fields):
                    raise ValueError("请填写此平台所需的全部登录凭据和连接信息。")
                if not task:
                    if platform and clear:
                        raise ValueError("新建平台任务时，请先填写登录凭据。")
                    if engine == "site" and not platform and not spec.get("cookie"):
                        raise ValueError("新建网站任务时，请填写有效的登录 Cookie。")
                    state = spec.get("storage_state", {})
                    if engine == "browser" and not (state.get("cookies") or state.get("origins")):
                        if supplied_cookie or (upload and upload.filename) or form["storage_state"].strip():
                            raise ValueError("新建网站任务时，提供的登录状态需包含允许访问域名的 Cookie 或本地存储。请检查签到页面与允许访问的域名，再重新连接账号。")
                        raise ValueError("新建网站任务时，请先粘贴该网站的登录 Cookie 或 Token；也可选择导入完整会话。无需填写 JSON。")
                saved_id = store.save_task(form, spec, task_id)
                flash("账号已保存，下一步验证签到。验证会执行一次真实签到或奖励领取。", "success")
                return redirect(url_for("site_verify", task_id=saved_id))
            except (json.JSONDecodeError, UnicodeError):
                error = "步骤或会话不是有效 JSON。请重新选择会话文件；登录凭据不会在页面中回显。"
            except (OSError, RuntimeError):
                error = "无法打开网站登录窗口，请检查浏览器环境后重试。"
            except (ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, ValueError) else "网站配置格式不正确。"
            form["cookie"] = ""
            form["storage_state"] = ""
            form["browser_cookie"] = ""
            form["browser_token"] = ""
            for item in credential_fields:
                if item["secret"]:
                    form[item["key"]] = ""
        saved_state = original.get("storage_state", {})
        has_session = bool(original.get("cookie") or saved_state.get("cookies") or saved_state.get("origins"))
        if platform and original:
            from .platforms import has_platform_session
            has_session = has_platform_session(original)
        pending_login = account_login.pending(login_owner())
        return render_template("platform_form.html" if platform else "site_form.html", site=site, form=form, task=task, editing=bool(task),
                               engine=engine, error=error, has_session=has_session, flow_step=2,
                               pending_login=pending_login), 400 if error else 200

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

    def website_task(task_id):
        task = required_task(task_id)
        spec = store.spec(task)
        if spec.get("engine", "http") == "http":
            abort(404)
        return task, SITES.get(spec.get("site_id"), {"name": "网站", "start_url": ""})

    @app.route("/tasks/<int:task_id>/verify", methods=["GET", "POST"])
    def site_verify(task_id):
        task, site = website_task(task_id)
        if request.method == "POST":
            accepted = store.enqueue_manual(task_id)
            flash("已开始验证，请稍候。" if accepted else "正在验证，请等待本次结果。", "success" if accepted else "info")
            return redirect(url_for("site_verify", task_id=task_id))
        verification = store.verification(task_id)
        return render_template("site_verify.html", task=task, site=site, verification=verification, flow_step=3,
                               generic=bool(store.spec(task).get("browser_login")))

    @app.get("/tasks/<int:task_id>/verification-status")
    def site_verification_status(task_id):
        task, _site = website_task(task_id)
        verification = store.verification(task_id)
        run = verification["run"]
        # Poll only metadata. Account credentials and upstream responses stay off the wire.
        return {"revision": task["config_revision"], "run_id": run["id"] if run else None,
                "status": run["status"] if run else "idle", "ready": verification["ready"]}

    @app.route("/tasks/<int:task_id>/schedule", methods=["GET", "POST"])
    def site_schedule(task_id):
        task, site = website_task(task_id)
        if not store.verification(task_id)["ready"]:
            flash("请先完成当前账号和规则的签到验证，再设置每日自动执行。", "warning")
            return redirect(url_for("site_verify", task_id=task_id))
        form = {"daily_time": task["daily_time"], "timezone": task["timezone"]}
        error = None
        if request.method == "POST":
            form = {key: request.form.get(key, "").strip() for key in form}
            try:
                store.enable_schedule(task_id, form["daily_time"], form["timezone"])
                flash("设置完成！每天会按计划自动签到，登录失效或需要人工验证时会提示你处理。", "success")
                return redirect(url_for("index"))
            except ValueError as exc:
                error = str(exc)
        return render_template("site_schedule.html", task=task, site=site, form=form, error=error, flow_step=4), 400 if error else 200

    @app.post("/tasks/<int:task_id>/toggle")
    def task_toggle(task_id):
        task = required_task(task_id)
        try:
            store.toggle_task(task_id)
        except ValueError as exc:
            flash(str(exc), "warning")
            return redirect(url_for("site_verify", task_id=task_id))
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
