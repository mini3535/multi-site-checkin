"""Small, bounded browser workflows; site rules are configuration, never code.

The browser lives in a separate process group so the parent can enforce a total
deadline, including DNS and browser startup. Credentials travel over its private
stdin/stdout pipes and are never placed in command-line arguments or messages.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

from .runner import _normalize_url, _resolve, _TargetBlocked

MAX_RUN_SECONDS = 90
MAX_PAYLOAD_BYTES = 600 * 1024
MAX_STATE_BYTES = 512 * 1024
STATUSES = {"success", "already_done", "needs_login", "needs_verification", "unsupported", "failed"}
MESSAGES = {
    "success": "网页签到成功条件已满足。",
    "already_done": "网页显示今日已签到，无需重复执行。",
    "needs_login": "登录已失效，请重新连接网站账号，或更新 Cookie、Token 或完整会话。",
    "needs_verification": "网站要求人工验证，请在本机完成后更新会话。",
    "unsupported": "浏览器环境不可用，请按部署说明安装 Playwright 和 Chromium。",
    "failed": "网页步骤或成功条件未满足，请检查登录状态、选择器和允许的域名。",
}


def _text(value, label, *, required=False, limit=1024):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label}格式无效或过长。")
    if required and not value.strip():
        raise ValueError(f"请填写{label}。")
    return value


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253 or any(c in value for c in "/@?#,* \\") or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError("允许的域名必须为明确的主机名，不能包含端口、通配符或路径。")
    value = value.rstrip(".").lower()
    candidate = value.strip("[]")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        if ":" in value:
            raise ValueError("允许的域名不能包含端口。") from None
        try:
            ascii_host = value.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("允许的域名格式无效。") from None
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", ascii_host) or ".." in ascii_host:
            raise ValueError("允许的域名格式无效。")
        return ascii_host


def _belongs(domain, hosts):
    return any(host == domain or host.endswith("." + domain) for host in hosts)


def _start_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("开始网址格式无效或过长。")
    parsed = urlsplit(value)
    base = _normalize_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")))
    fragment = _text(parsed.fragment, "网页路径片段", limit=1024)
    return base + ("#" + quote(fragment, safe="/%?:@!$&'()*+,;=-._~") if fragment else "")


def _state(value, hosts):
    if not isinstance(value, dict) or set(value) - {"cookies", "origins"}:
        raise ValueError("会话文件必须是浏览器导出的 storage_state JSON。")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise ValueError("会话 JSON 格式无效。") from None
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("会话文件不能超过 512 KiB。")
    cookies, origins = value.get("cookies", []), value.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list) or len(cookies) > 500 or len(origins) > 50:
        raise ValueError("会话中的 Cookie 或存储条目格式无效。")
    safe_cookies = []
    for item in cookies:
        if not isinstance(item, dict) or not isinstance(item.get("domain"), str):
            raise ValueError("会话 Cookie 格式无效。")
        domain = _host(item["domain"].lstrip("."))
        if not _belongs(domain, hosts):
            continue
        cookie = {"name": _text(item.get("name"), "Cookie 名称", required=True, limit=1024),
                  "value": _text(item.get("value"), "Cookie 值", limit=16384),
                  "domain": ("." if item["domain"].startswith(".") else "") + domain,
                  "path": _text(item.get("path", "/"), "Cookie 路径", required=True, limit=2048)}
        if not cookie["path"].startswith("/"):
            raise ValueError("Cookie 路径必须以 / 开头。")
        for name in ("httpOnly", "secure"):
            v = item.get(name, False)
            if type(v) is not bool:
                raise ValueError("Cookie 开关必须为布尔值。")
            cookie[name] = v
        expires = item.get("expires", -1)
        if type(expires) not in (int, float) or not math.isfinite(expires) or expires < -1:
            raise ValueError("Cookie 到期时间无效。")
        cookie["expires"] = expires
        same_site = item.get("sameSite", "Lax")
        if same_site not in ("Strict", "Lax", "None"):
            raise ValueError("Cookie 的 sameSite 值无效。")
        cookie["sameSite"] = same_site
        # New Playwright versions can export partitioned cookies.
        if "partitionKey" in item:
            partition = _normalize_url(item["partitionKey"])
            parsed = urlsplit(partition)
            if parsed.hostname in hosts and parsed.path == "/" and not parsed.query:
                cookie["partitionKey"] = partition.rstrip("/")
        safe_cookies.append(cookie)
    safe_origins = []
    for item in origins:
        if not isinstance(item, dict) or not isinstance(item.get("origin"), str):
            raise ValueError("浏览器存储来源格式无效。")
        origin = _normalize_url(item["origin"])
        parsed = urlsplit(origin)
        if parsed.hostname not in hosts:
            continue
        if parsed.path != "/" or parsed.query:
            raise ValueError("浏览器存储来源必须是网站源地址。")
        entries = item.get("localStorage", [])
        if not isinstance(entries, list) or len(entries) > 500:
            raise ValueError("浏览器本地存储格式无效。")
        local = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("浏览器本地存储格式无效。")
            # localStorage values can contain newlines (tokens/JSON), unlike selectors.
            name, content = entry.get("name"), entry.get("value")
            if not isinstance(name, str) or not isinstance(content, str) or not name or len(name) > 1024 or len(content) > 128 * 1024:
                raise ValueError("浏览器本地存储条目无效或过长。")
            local.append({"name": name, "value": content})
        safe_origins.append({"origin": origin.rstrip("/"), "localStorage": local})
    return {"cookies": safe_cookies, "origins": safe_origins}


def cookie_storage_state(cookie, start_url):
    """Import a request Cookie header for the start host only, never sibling hosts."""
    parsed = urlsplit(_start_url(start_url))
    if (not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 16384
            or any(ord(char) < 32 or ord(char) > 126 for char in cookie)):
        raise ValueError("Cookie 格式无效，请复制原始请求头的值，不要包含换行。")
    pairs = [part.strip().partition("=") for part in cookie.split(";") if part.strip()]
    if not pairs or len(pairs) > 300 or any(
        not separator or not re.fullmatch(r"[!#$%&'*+.^_\x60|~0-9A-Za-z-]+", name)
        for name, separator, _value in pairs
    ):
        raise ValueError("请粘贴 Cookie 请求头的值，例如 session=…；无需包含 Cookie: 前缀。")
    return {"cookies": [{"name": name, "value": value, "domain": parsed.hostname,
                          "path": "/", "secure": parsed.scheme == "https", "httpOnly": True,
                          "sameSite": "Lax", "expires": -1} for name, _separator, value in pairs], "origins": []}


def validate_browser_spec(spec, allow_private=False):
    """Validate workflow and session offline. DNS is checked in the isolated run."""
    del allow_private
    if not isinstance(spec, dict) or spec.get("engine", "browser") != "browser":
        raise ValueError("浏览器任务配置无效。")
    site_id = spec.get("site_id", "custom")
    if site_id not in ("custom", "jd", "smzdm", "nodeseek", "hostloc", "xingchi"):
        raise ValueError("不支持的浏览器站点类型。")
    start_url = _start_url(spec.get("start_url"))
    incoming = spec.get("allowed_hosts")
    if not isinstance(incoming, list) or not 1 <= len(incoming) <= 30:
        raise ValueError("请填写 1 至 30 个明确允许的域名。")
    hosts = list(dict.fromkeys(_host(item) for item in incoming))
    if urlsplit(start_url).hostname not in hosts:
        raise ValueError("开始网址的域名必须包含在允许的域名中。")
    state = _state(spec.get("storage_state", {"cookies": [], "origins": []}), hosts)
    steps = spec.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 20:
        raise ValueError("网页任务最多包含 20 个步骤。")
    safe_steps = []
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"action", "selector", "value", "timeout_ms"}:
            raise ValueError("网页步骤格式无效。")
        action = step.get("action")
        if action not in ("click", "fill", "wait"):
            raise ValueError("网页步骤只支持 click、fill 和 wait。")
        selector = _text(step.get("selector"), "步骤选择器", required=True)
        timeout = step.get("timeout_ms", 10000)
        if type(timeout) is not int or not 500 <= timeout <= 30000:
            raise ValueError("步骤等待时间需为 500 至 30000 毫秒。")
        normalized = {"action": action, "selector": selector, "timeout_ms": timeout}
        if action == "fill":
            value = step.get("value", "")
            if not isinstance(value, str) or len(value) > 16384 or "\x00" in value:
                raise ValueError("填写内容必须是字符串，最多 16384 字符。")
            normalized["value"] = value
        safe_steps.append(normalized)
    auto_success = spec.get("auto_success", False)
    if type(auto_success) is not bool:
        raise ValueError("自动识别签到结果设置无效。")
    success = _text(spec.get("success_text", ""), "签到成功的完整文字",
                    required=not auto_success, limit=256)
    already = _text(spec.get("already_done_text", ""), "今日已签到的完整文字", limit=256)
    if already and already == success:
        raise ValueError("签到成功和今日已签到的文字需不同，以便区分执行结果。")
    normalized = {"engine": "browser", "site_id": site_id, "start_url": start_url,
            "allowed_hosts": hosts, "storage_state": state, "steps": safe_steps,
            "success_text": success, "already_done_text": already,
            "login_selector": _text(spec.get("login_selector", ""), "登录失效选择器"),
            "verification_selector": _text(spec.get("verification_selector", ""), "人工验证选择器")}
    if auto_success:
        normalized["auto_success"] = True
    if "token_login" in spec:
        from .browser_auth import normalize_token, storage_key, token_entry
        login = spec["token_login"]
        if not isinstance(login, dict) or set(login) != {"storage_key"}:
            raise ValueError("Token 登录配置无效。")
        key = storage_key(login["storage_key"], start_url)
        entry = token_entry(state, start_url, key)
        if not entry:
            raise ValueError("未找到签到页面对应的 Token。更换页面来源后，请重新粘贴登录 Token。")
        entry["value"] = normalize_token(entry["value"])
        normalized["token_login"] = {"storage_key": key}
    if "browser_login" in spec:
        login = spec["browser_login"]
        if not isinstance(login, dict) or set(login) != {"url"}:
            raise ValueError("账号密码登录配置无效。")
        login_url = _start_url(login["url"])
        if (urlsplit(login_url).scheme != "https" or urlsplit(start_url).scheme != "https"
                or not _allowed_request(login_url, normalized)):
            raise ValueError("账号密码登录需要 HTTPS 地址，登录页域名必须包含在允许访问的域名中。")
        if "token_login" in normalized:
            raise ValueError("账号密码与 Token 连接方式不能同时使用。")
        normalized["browser_login"] = {"url": login_url}
    if site_id == "xingchi":
        from .xingchi import task_spec
        expected = task_spec(state)
        if normalized != expected:
            raise ValueError("星驰签到地址与规则已预设，请通过星驰账号入口连接。")
    return normalized


def _network_rules(spec, allow_private):
    rules = []
    for host in spec["allowed_hosts"]:
        address = _resolve(host, 443, allow_private)
        ip = address[4][0]
        if ":" in ip:
            ip = f"[{ip}]"
        rules.append(f"MAP {host} {ip}")
    # Chromium's HostMappingRules uses ^NOTFOUND to force ERR_NAME_NOT_RESOLVED.
    rules.append("MAP * ^NOTFOUND")
    return ", ".join(rules)


def _allowed_request(url, spec):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.hostname not in spec["allowed_hosts"]:
            return False
        start = urlsplit(spec["start_url"])
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return port in {80, 443} or (parsed.hostname == start.hostname and port == start.port)
    except (ValueError, AttributeError):
        return False


def _browser_launch_options(mapping, browser_timeout, executable=None):
    options = {
        "headless": True,
        "timeout": browser_timeout,
        "chromium_sandbox": True,
        "args": [f"--host-resolver-rules={mapping}", "--no-proxy-server",
                 "--disable-background-networking", "--disable-quic",
                 "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
    }
    if executable:
        options["executable_path"] = executable
    return options


def _process_parents():
    """Read only process identifiers, never command lines or environment values."""
    proc = Path('/proc')
    if proc.is_dir():
        pairs = {}
        for path in proc.iterdir():
            if path.name.isdecimal():
                try:
                    # comm is parenthesized and can itself contain spaces or ')'.
                    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                    pairs[int(path.name)] = int(fields[1])
                except (OSError, ValueError, IndexError):
                    continue
        return pairs
    try:
        output = subprocess.check_output(['ps', '-e', '-o', 'pid=,ppid='], timeout=2)
        return {int(pid): int(ppid) for pid, ppid in (line.split() for line in output.splitlines())}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _kill_browser_tree(process):
    # Playwright starts Chromium in a detached group. Killing only our Python
    # group would miss it. Freeze our worker/driver group before enumerating so
    # the driver cannot launch another browser after the snapshot. Do not reap
    # the worker here: its unreaped PID keeps our group identifier from reuse.
    if process.returncode is not None:
        return
    try:
        if os.getpgid(process.pid) != process.pid:
            process.kill()
            return
        os.killpg(process.pid, signal.SIGSTOP)
    except ProcessLookupError:
        return
    parents = _process_parents()
    descendants = {process.pid}
    while True:
        found = {pid for pid, parent in parents.items() if parent in descendants}
        if found <= descendants:
            break
        descendants.update(found)
    groups = {process.pid}
    for pid in descendants:
        try:
            group = os.getpgid(pid)
            # Only groups whose own leader belongs to this run are eligible. A
            # child sharing an external/inherited group must never widen scope.
            if group in descendants:
                groups.add(group)
        except ProcessLookupError:
            pass
    groups.discard(os.getpgrp())
    # Recheck each detached group's ancestry while the original worker remains
    # unreaped. This rejects a PID recycled outside our tree between snapshots.
    current = _process_parents()
    current_descendants = {process.pid}
    while True:
        found = {pid for pid, parent in current.items() if parent in current_descendants}
        if found <= current_descendants:
            break
        current_descendants.update(found)
    groups.intersection_update(current_descendants)
    # Kill detached child groups first. No executable-name matching is used.
    for group in sorted(groups, key=lambda group: group == process.pid):
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def _run_browser_in_process(spec, allow_private=False):
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
    except ImportError:
        return {"status": "unsupported"}
    browser = context = None
    deadline = time.monotonic() + MAX_RUN_SECONDS - 5

    def timeout(limit=30000):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise TimeoutError()
        return min(limit, remaining)

    def visible_text(page, text):
        if not text:
            return False
        matches = page.get_by_text(text, exact=True)
        return any(matches.nth(i).is_visible() for i in range(min(matches.count(), 20)))

    def page_status(page, before=False):
        # Verification and login always take precedence over success-like text.
        for key, status in (("verification_selector", "needs_verification"), ("login_selector", "needs_login")):
            if spec[key]:
                matches = page.locator(spec[key])
                if any(matches.nth(i).is_visible() for i in range(min(matches.count(), 20))):
                    return status
        if visible_text(page, spec["already_done_text"]):
            return "already_done"
        if spec.get("auto_success"):
            lines = page.locator("body").inner_text(timeout=timeout(3000)).splitlines()
            for line in lines[:2000]:
                text = line.strip()
                if len(text) > 160:
                    continue
                if re.search(r"(今日|今天).{0,12}(已经|已)(完成)?(签到|打卡)|"
                             r"(已经|已)(签到|打卡)|already.{0,20}(checked|signed|claim)|"
                             r"(checked|signed)\s+in.{0,16}already", text, re.IGNORECASE):
                    return "already_done"
                if re.search(r"(签到|打卡|领取).{0,8}(成功|完成)|(成功|完成).{0,8}(签到|打卡|领取)|"
                             r"check[\s-]?in.{0,12}(success|complete)|(success|complete).{0,12}check[\s-]?in|"
                             r"claim.{0,12}(success|complete)", text, re.IGNORECASE):
                    return "already_done" if before else "success"
        if spec["site_id"] == "xingchi":
            from .xingchi import DONE_SELECTOR
            if page.locator(DONE_SELECTOR).first.is_visible():
                return "already_done" if before else "success"
        return None

    try:
        from .browser_auth import prepare_token_session, TokenLoginError
        try:
            state = _state(prepare_token_session(spec, allow_private), spec["allowed_hosts"])
        except TokenLoginError as exc:
            return {"status": exc.status}
        mapping = _network_rules(spec, allow_private)
        with sync_playwright() as playwright:
            executable = os.environ.get("AUTOSIGN_BROWSER_EXECUTABLE")
            launch = _browser_launch_options(mapping, timeout(), executable)
            try:
                browser = playwright.chromium.launch(**launch)
            except BrowserError:
                return {"status": "unsupported"}
            try:
                context = browser.new_context(storage_state=state, service_workers="block",
                                              accept_downloads=False, locale="zh-CN",
                                              timezone_id="Asia/Shanghai",
                                              **({"viewport": {"width": 1024, "height": 720}}
                                                 if spec["site_id"] == "xingchi" else {}))
                context.set_default_timeout(10000)
                context.set_default_navigation_timeout(20000)

                def route_request(route):
                    if _allowed_request(route.request.url, spec):
                        route.continue_()
                    else:
                        route.abort("blockedbyclient")

                context.route("**/*", route_request)
                context.route_web_socket("**/*", lambda ws: ws.close())
                # The workflow needs normal web JavaScript, but never peer networking.
                context.add_init_script("""
                    for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection']) {
                      try { Object.defineProperty(globalThis, name, {value: undefined, configurable: false, writable: false}); } catch (_) {}
                    }
                """)
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.dismiss())
                context.on("page", lambda popup: popup.close())
                response = page.goto(spec["start_url"], wait_until="domcontentloaded", timeout=timeout())
                if response is not None and response.status >= 400:
                    return {"status": "failed"}
                for step in spec["steps"]:
                    target = page.locator(step["selector"])
                    limit = timeout(step["timeout_ms"])
                    ready_deadline = time.monotonic() + limit / 1000
                    while True:
                        status = page_status(page, before=True)
                        if status:
                            result = {"status": status}
                            if status == "already_done":
                                result["_storage_state"] = _state(context.storage_state(), spec["allowed_hosts"])
                            return result
                        if target.first.is_visible() or time.monotonic() >= ready_deadline:
                            break
                        page.wait_for_timeout(min(100, timeout()))
                    limit = timeout(max(1, int((ready_deadline - time.monotonic()) * 1000)))
                    if step["action"] == "click":
                        target.click(timeout=limit)
                    elif step["action"] == "fill":
                        target.fill(step["value"], timeout=limit)
                    else:
                        target.wait_for(state="visible", timeout=limit)
                verification_deadline = min(deadline, time.monotonic() + 5)
                while True:
                    status = page_status(page)
                    if not status and spec["success_text"] and visible_text(page, spec["success_text"]):
                        status = "success"
                    if status:
                        result = {"status": status}
                        if status in ("success", "already_done"):
                            result["_storage_state"] = _state(context.storage_state(), spec["allowed_hosts"])
                        return result
                    if time.monotonic() >= verification_deadline:
                        return {"status": "failed"}
                    page.wait_for_timeout(min(150, timeout()))
            finally:
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    except (_TargetBlocked, socket.gaierror, OSError, ValueError, TimeoutError, BrowserTimeout, BrowserError):
        return {"status": "failed"}
    except Exception:
        # Remote HTML, selectors and browser exception strings can contain secrets.
        return {"status": "failed"}


def run_browser(spec, allow_private=False):
    started = time.monotonic()

    def result(status, state=None):
        answer = {"ok": status in ("success", "already_done"), "status": status,
                  "status_code": None, "message": MESSAGES[status],
                  "duration_ms": round((time.monotonic() - started) * 1000)}
        if state is not None and status in ("success", "already_done"):
            answer["_storage_state"] = state
        return answer

    try:
        spec = validate_browser_spec(spec, allow_private=allow_private)
        payload = json.dumps({"spec": spec, "allow_private": bool(allow_private)}, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > MAX_PAYLOAD_BYTES:
            return result("failed")
    except (ValueError, TypeError):
        return result("failed")
    env = {name: value for name, value in os.environ.items() if name not in {"DEBUG", "PWDEBUG", "DEBUG_FILE"}}
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-m", "autosign.browser_runner", "--worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env=env)
        try:
            stdout, _stderr = process.communicate(payload, timeout=MAX_RUN_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_browser_tree(process)
            process.communicate()
            return result("failed")
        if process.returncode or len(stdout) > MAX_PAYLOAD_BYTES:
            return result("failed")
        output = json.loads(stdout)
        status = output.get("status")
        if status not in STATUSES:
            return result("failed")
        state = _state(output["_storage_state"], spec["allowed_hosts"]) if "_storage_state" in output else None
        return result(status, state)
    except (OSError, ValueError, TypeError, RecursionError):
        return result("failed")
    finally:
        if process is not None and process.poll() is None:
            _kill_browser_tree(process)
            process.communicate()


def _child_main():
    try:
        raw = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise ValueError()
        payload = json.loads(raw)
        spec = validate_browser_spec(payload["spec"])
        output = _run_browser_in_process(spec, allow_private=payload.get("allow_private") is True)
    except Exception:
        output = {"status": "failed"}
    sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False))
    sys.stdout.flush()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _child_main()
