"""Short-lived, login-only browsers. No page action recording or password storage.

Browser operations run in one private child process; Flask threads exchange
bounded messages over pipes. Only the save endpoint can obtain storage_state.
"""
import base64
import json
import math
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from .browser_runner import (_allowed_request, _browser_launch_options, _kill_browser_tree,
                             _network_rules, _state, validate_browser_spec)
from .xingchi import HOST, ORIGIN, LOGIN_URL

LIFETIME = 10 * 60
MAX_MESSAGE = 2 * 1024 * 1024
MAX_COMMAND = 32 * 1024
LOGIN_HOSTS = [HOST, "challenges.cloudflare.com"]
KEYS = {"Tab", "Enter", "Backspace", "ControlOrMeta+A", "Escape"}


class LoginError(ValueError):
    pass


def credentials(email, password):
    if (not isinstance(email, str) or not 3 <= len(email) <= 254 or "@" not in email
            or any(ord(c) < 33 or ord(c) == 127 for c in email)):
        raise LoginError("请填写有效的星驰登录邮箱。")
    if (not isinstance(password, str) or not 1 <= len(password) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in password)):
        raise LoginError("请填写星驰账号的登录密码，最多 256 字符。")
    return email, password


def website_credentials(username, password):
    if (not isinstance(username, str) or not 1 <= len(username) <= 254
            or any(ord(c) < 32 or ord(c) == 127 for c in username)):
        raise LoginError("请填写网站的用户名、邮箱或手机号，最多 254 字符。")
    if (not isinstance(password, str) or not 1 <= len(password) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in password)):
        raise LoginError("请填写网站的登录密码，最多 256 字符。")
    return username, password


def connection_config(spec):
    spec = validate_browser_spec(spec)
    if "browser_login" not in spec:
        raise LoginError("请填写网站登录页地址。")
    return {"start_url": spec["start_url"], "login_url": spec["browser_login"]["url"],
            "allowed_hosts": spec["allowed_hosts"]}


def website_request_allowed(method, url, config):
    # Generic sites have different authentication endpoints. Only user-selected
    # HTTPS hosts and the normal Cloudflare challenge are reachable. No recording
    # or daily task steps run in this interactive login browser.
    hosts = list(dict.fromkeys(config["allowed_hosts"] + ["challenges.cloudflare.com"]))
    if not _allowed_request(url, {"start_url": config["login_url"], "allowed_hosts": hosts}):
        return False
    return urlsplit(url).scheme == "https" and method in {"GET", "HEAD", "OPTIONS", "POST"}


def login_request_allowed(method, url):
    """Allow normal site reads and auth writes, never a check-in/reward write."""
    if not _allowed_request(url, {"start_url": LOGIN_URL, "allowed_hosts": LOGIN_HOSTS}):
        return False
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return False
    if parsed.hostname == "challenges.cloudflare.com":
        return method in {"GET", "POST"}
    if method == "GET":
        return True
    return method == "POST" and parsed.path in {
        "/api/v1/auth/login", "/api/v1/auth/login/2fa", "/api/v1/auth/refresh",
    }


def authenticated_state(state, url):
    """Accept only the exact site's completed dashboard session."""
    from .browser_auth import normalize_token
    if urlsplit(url)._replace(query="", fragment="").geturl() != ORIGIN + "/dashboard":
        raise LoginError("请先在星驰完成登录及验证，进入仪表盘后再保存。")
    state = _state(state, [HOST])
    for item in state["origins"]:
        if item["origin"] != ORIGIN:
            continue
        entries = {entry["name"]: entry["value"] for entry in item["localStorage"]}
        try:
            normalize_token(entries.get("auth_token"))
            user = json.loads(entries.get("auth_user", "null"))
        except (ValueError, TypeError):
            break
        if isinstance(user, dict) and type(user.get("id")) is int and user["id"] > 0:
            # Do not retain unrelated local preferences or any submitted password.
            item["localStorage"] = [entry for entry in item["localStorage"]
                                    if entry["name"] in {"auth_token", "auth_user", "refresh_token", "token_expires_at"}]
            state["origins"] = [item]
            return state
    raise LoginError("网站尚未确认登录，请检查账号密码或完成网站要求的验证。")


class LoginWindow:
    def __init__(self, config=None):
        from playwright.sync_api import sync_playwright
        self.playwright = self.browser = self.context = None
        self.revision = 0
        self.closed = False
        self.config = config
        self.login_url = config["login_url"] if config else LOGIN_URL
        try:
            hosts = list(dict.fromkeys(config["allowed_hosts"] + ["challenges.cloudflare.com"])) if config else LOGIN_HOSTS
            mapping = _network_rules({"allowed_hosts": hosts}, False)
            self.playwright = sync_playwright().start()
            options = _browser_launch_options(mapping, 20000, os.environ.get("AUTOSIGN_BROWSER_EXECUTABLE"))
            self.browser = self.playwright.chromium.launch(**options)
            self.context = self.browser.new_context(viewport={"width": 1024, "height": 720},
                locale="zh-CN", timezone_id="Asia/Shanghai", service_workers="block", accept_downloads=False)
            self.context.set_default_timeout(5000)
            self.context.set_default_navigation_timeout(20000)
            permitted = ((lambda method, url: website_request_allowed(method, url, config))
                         if config else login_request_allowed)
            self.context.route("**/*", lambda route: route.continue_() if
                               permitted(route.request.method, route.request.url) else route.abort("blockedbyclient"))
            self.context.route_web_socket("**/*", lambda ws: ws.close())
            self.context.add_init_script("""
                for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection']) {
                  try { Object.defineProperty(globalThis, name, {value: undefined, configurable: false, writable: false}); } catch (_) {}
                }
            """)
            self.page = self.context.new_page()
            self.page.on("dialog", lambda dialog: dialog.dismiss())
            self.context.on("page", lambda popup: popup.close())
            response = self.page.goto(self.login_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise LoginError("网站登录页无法打开，请稍后重试。")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        for resource in (self.context, self.browser, self.playwright):
            if resource is not None:
                try:
                    resource.stop() if resource is self.playwright else resource.close()
                except Exception:
                    pass

    def logged_in(self):
        if self.config:
            # A cookie or URL change alone does not establish authentication for
            # an arbitrary website. The user confirms; check-in is verified later.
            return False
        try:
            authenticated_state(self.context.storage_state(), self.page.url)
            return True
        except LoginError:
            return False

    def frame(self):
        self.page.wait_for_timeout(30)
        screenshot = self.page.screenshot(type="jpeg", quality=65, timeout=5000)
        self.revision += 1
        return {"image": base64.b64encode(screenshot).decode(), "width": 1024, "height": 720,
                "revision": self.revision, "ready": self.logged_in(),
                "manual_confirmation": bool(self.config)}

    def website_state(self):
        if not website_request_allowed("GET", self.page.url, self.config) or urlsplit(self.page.url).hostname not in self.config["allowed_hosts"]:
            raise LoginError("请在指定网站完成登录后再保存。")
        passwords = self.page.locator('input[type="password"]:visible')
        if passwords.count():
            raise LoginError("页面仍显示密码输入框，请先完成网站登录。")
        state = _state(self.context.storage_state(), self.config["allowed_hosts"])
        # Password form data is never part of storage_state. Discard common
        # password fields if a site itself writes them to browser storage.
        secret_names = {"password", "passwd", "pwd", "user_password", "login_password"}
        state["cookies"] = [item for item in state["cookies"] if item["name"].lower() not in secret_names]
        for origin in state["origins"]:
            origin["localStorage"] = [item for item in origin["localStorage"] if item["name"].lower() not in secret_names]
        if not state["cookies"] and not any(item["localStorage"] for item in state["origins"]):
            raise LoginError("尚未获得可保存的 Cookie 或本地存储。请完成登录；仅使用 sessionStorage 等方式的网站需要单独适配。")
        return state

    def fill_website_credentials(self, username, password):
        # Fill only a single visible password field and a single username field
        # in the same form. Ambiguous/custom forms remain under user control.
        try:
            field = self.page.locator('input[type="password"]:visible')
            field.first.wait_for(state="visible", timeout=5000)
            if field.count() != 1:
                return
            form = field.locator("xpath=ancestor::form[1]")
            scope = form if form.count() == 1 else self.page
            for selector in ('input[autocomplete="username"]:visible', 'input[type="email"]:visible',
                             'input[type="tel"]:visible',
                             'input[name="username"]:visible, input[name="email"]:visible, input[name="account"]:visible',
                             'input[type="text"]:visible, input:not([type]):visible'):
                account = scope.locator(selector)
                if account.count() == 1:
                    account.fill(username, timeout=1500)
                    field.fill(password, timeout=1500)
                    break
        except Exception:
            pass  # The user can operate unsupported forms in the login view.

    def command(self, payload):
        action = payload.get("action")
        if action == "credentials":
            if self.config:
                username, password = website_credentials(payload.get("email"), payload.get("password"))
                if not website_request_allowed("GET", self.page.url, self.config):
                    raise LoginError("登录页面已变化，请重新连接。")
                # Never submit or accept a verification/agreement automatically.
                self.fill_website_credentials(username, password)
                return self.frame()
            email, password = credentials(payload.get("email"), payload.get("password"))
            if self.page.url != LOGIN_URL:
                raise LoginError("登录页面已变化，请重新打开连接窗口。")
            self.page.locator('input[type="email"]').fill(email)
            self.page.locator('input[type="password"]').fill(password)
            # Only a normal login button. No CAPTCHA or agreement is automated.
            button = self.page.get_by_role("button", name="登录", exact=True)
            if button.count() == 1 and button.is_enabled():
                try:
                    button.click(timeout=1200)
                except Exception:
                    pass  # A visible CAPTCHA/agreement may still require the user.
            return self.frame()
        if action == "frame":
            return self.frame()
        if action == "finish":
            if self.config:
                return {"state": self.website_state()}
            return {"state": authenticated_state(self.context.storage_state(), self.page.url)}
        if payload.get("revision") != self.revision:
            raise LoginError("画面已更新，请在最新画面中操作。")
        if self.logged_in():
            raise LoginError("登录已完成，请保存账号并进入签到验证。")
        if action == "click":
            x, y = payload.get("x"), payload.get("y")
            if (type(x) not in (int, float) or type(y) not in (int, float)
                    or not math.isfinite(x) or not math.isfinite(y) or not 0 <= x < 1024 or not 0 <= y < 720):
                raise LoginError("点击位置无效。")
            self.page.mouse.click(x, y)
        elif action == "type":
            text = payload.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 256 or any(ord(c) < 32 for c in text):
                raise LoginError("请输入最多 256 字符的验证内容。")
            self.page.keyboard.insert_text(text)
        elif action == "key" and payload.get("key") in KEYS:
            self.page.keyboard.press(payload["key"])
        elif action == "scroll" and payload.get("direction") in ("up", "down"):
            self.page.mouse.wheel(0, -400 if payload["direction"] == "up" else 400)
        else:
            raise LoginError("不支持的登录操作。")
        return self.frame()


class _Session:
    def __init__(self, owner, name, task_id, revision):
        self.owner, self.name, self.task_id, self.revision = owner, name, task_id, revision
        self.expires = time.monotonic() + LIFETIME
        self.lock = threading.RLock()
        self.closed = False
        self.responses = queue.Queue(maxsize=2)
        env = {k: v for k, v in os.environ.items() if k not in {"DEBUG", "PWDEBUG", "DEBUG_FILE"}}
        self.process = subprocess.Popen([sys.executable, "-m", "autosign.account_login", "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, env=env)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_MESSAGE + 1)
                if not line or len(line) > MAX_MESSAGE:
                    break
                self.responses.put_nowait(json.loads(line))
        except Exception:
            pass
        finally:
            try:
                self.responses.put_nowait(None)
            except queue.Full:
                pass

    def send(self, command):
        with self.lock:
            if self.closed or time.monotonic() >= self.expires:
                self.close()
                raise LoginError("登录窗口已过期，请重新连接账号。")
            raw = json.dumps(command, ensure_ascii=False).encode() + b"\n"
            if len(raw) > MAX_COMMAND:
                raise LoginError("登录操作过长。")
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
                reply = self.responses.get(timeout=30)
                if not isinstance(reply, dict):
                    raise ValueError()
            except Exception:
                self.close()
                raise LoginError("登录窗口暂时不可用，请重新连接；也可使用备用 Token 方式。") from None
            if reply.get("error"):
                raise LoginError(reply["error"])
            return reply

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            if self.process.poll() is None:
                _kill_browser_tree(self.process)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for stream in (self.process.stdin, self.process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass


class LoginManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.stop = threading.Event()
        self.started = False
        self.attempts = []

    def _discard_inactive(self):
        for key, item in list(self.sessions.items()):
            process = getattr(item, "process", None)
            if (item.closed or time.monotonic() >= item.expires
                    or (process is not None and process.poll() is not None)):
                self.sessions.pop(key).close()

    def pending(self, owner):
        with self.lock:
            self._discard_inactive()
            for key, item in self.sessions.items():
                # Another admin login can release the occupied browser, but
                # never read or operate the other session's page or credentials.
                return {"key": key, "owned": item.owner == owner}
        return None

    def release(self, key):
        with self.lock:
            self._discard_inactive()
            item = self.sessions.pop(key, None)
            if item is not None:
                item.close()

    def _reap(self):
        while not self.stop.wait(5):
            with self.lock:
                self._discard_inactive()

    def start(self, owner, name, email, password, task_id=None, revision=None, spec=None):
        config = connection_config(spec) if spec is not None else None
        (website_credentials if config else credentials)(email, password)
        with self.lock:
            if self.stop.is_set():
                raise LoginError("登录服务正在关闭，请稍后重试。")
            now = time.monotonic()
            self.attempts = [stamp for stamp in self.attempts if stamp > now - 300]
            if len(self.attempts) >= 5:
                raise LoginError("连接次数过多，请在 5 分钟后重试。")
            self._discard_inactive()
            if self.sessions:
                raise LoginError("已有一个登录窗口，请先完成或取消它，再连接其他账号。")
            self.attempts.append(now)
            if not self.started:
                self.started = True
                threading.Thread(target=self._reap, daemon=True).start()
            item = _Session(owner, name, task_id, revision)
            item.spec = spec
            key = secrets.token_urlsafe(24)
            try:
                command = {"action": "credentials", "email": email, "password": password}
                if config:
                    command["config"] = config
                item.send(command)
            except BaseException:
                item.close()
                raise
            self.sessions[key] = item
            return key

    def get(self, key, owner):
        with self.lock:
            item = self.sessions.get(key)
            if item is None or item.owner != owner:
                raise LoginError("登录窗口不存在或不属于当前登录。")
            if item.closed or time.monotonic() >= item.expires:
                self.sessions.pop(key).close()
                raise LoginError("登录窗口已过期，请重新连接。")
            return item

    def cancel(self, key, owner):
        with self.lock:
            item = self.get(key, owner)
            self.sessions.pop(key)
            item.close()

    def cancel_owner(self, owner):
        if owner is None:
            return
        with self.lock:
            for key, item in list(self.sessions.items()):
                if item.owner == owner:
                    self.sessions.pop(key).close()

    def cancel_all(self):
        with self.lock:
            for item in self.sessions.values():
                item.close()
            self.sessions.clear()

    def close(self):
        self.stop.set()
        self.cancel_all()


def _worker():
    window = None
    class Expired(BaseException):
        pass
    def expire(signum, frame):
        raise Expired()
    signal.signal(signal.SIGALRM, expire)
    signal.alarm(LIFETIME + 30)
    try:
        while True:
            raw = sys.stdin.buffer.readline(MAX_COMMAND + 1)
            if not raw or len(raw) > MAX_COMMAND:
                break
            try:
                command = json.loads(raw)
                if window is None:
                    if command.get("action") != "credentials":
                        break
                    window = LoginWindow(command.get("config"))
                result = window.command(command)
            except LoginError as exc:
                result = {"error": str(exc)}
            except Exception:
                # Playwright exception text can include typed credentials.
                result = {"error": "网页登录操作未完成，请检查当前画面，或重新打开登录窗口。"}
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            command = None
            raw = b""
    except BaseException:
        pass
    finally:
        if window:
            window.close()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker()
