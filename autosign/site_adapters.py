"""Small, fixed-origin website adapters.

The Enshan request sequence was informed by Sitoi/dailycheckin; see
THIRD_PARTY_NOTICES.md. This implementation is independently structured and
deliberately stops at login challenges instead of attempting to bypass them.

An injected transport receives (method, url, headers, body) and returns a dict
with status_code, headers and body (bytes or text). It is intended for tests.
"""
from __future__ import annotations

import http.client
from html.parser import HTMLParser
import json
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from .runner import (
    MAX_RESPONSE_BYTES, _PinnedHTTPConnection, _PinnedHTTPSConnection,
    _resolve, validate_request,
)

ENSHAN_PAGE = "https://www.right.com.cn/forum/forum.php"
ENSHAN_SIGN = "https://www.right.com.cn/forum/plugin.php?id=erling_qd:action&action=sign"
_ALLOWED_REQUESTS = {("GET", ENSHAN_PAGE), ("POST", ENSHAN_SIGN)}
_MESSAGES = {
    "success": "恩山论坛签到成功。",
    "already_done": "恩山论坛今日已签到。",
    "needs_login": "恩山论坛登录已失效，请重新登录并更新 Cookie。",
    "needs_verification": "恩山论坛需要人工验证，请先在网站完成验证。",
    "unsupported": "恩山论坛页面或接口已变化，当前适配器无法确认结果。",
    "failed": "恩山论坛请求失败，请核对网站状态后再重试。",
}


class _UnsupportedResponse(Exception):
    pass


def validate_site_spec(spec: Any, allow_private: bool = False) -> dict[str, str]:
    """Validate an encrypted adapter configuration without making requests."""
    if not isinstance(spec, dict) or spec.get("engine", "site") != "site":
        raise ValueError("站点任务配置无效。")
    from .platforms import PLATFORMS, validate_platform_spec
    if spec.get("site_id") in PLATFORMS:
        return validate_platform_spec(spec, allow_private=allow_private)
    if spec.get("site_id") != "enshan":
        raise ValueError("当前站点尚未接入可运行的适配器。")
    cookie = spec.get("cookie")
    if not isinstance(cookie, str):
        raise ValueError("恩山论坛登录 Cookie 必须为文本。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cookie):
        raise ValueError("Cookie 不能包含换行或控制字符。")
    try:
        encoded = cookie.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError("Cookie 请使用网站提供的原始编码形式。") from None
    if len(encoded) > 16 * 1024:
        raise ValueError("Cookie 不能超过 16 KiB。")
    normalized = cookie.strip()
    # Share the ordinary executor's header validation; this remains offline.
    validate_request({
        "url": ENSHAN_PAGE, "method": "GET", "headers": {"Cookie": normalized},
        "body": "", "success_type": "text", "success_value": "adapter-internal",
    }, allow_private=allow_private)
    return {"engine": "site", "site_id": "enshan", "cookie": normalized}


def _fetch(method: str, url: str, headers: dict[str, str], body: str, *, allow_private: bool = False, allowed_requests=None) -> dict:
    """Execute a bounded request allowed by a fixed-site adapter."""
    if (method, url) not in (_ALLOWED_REQUESTS if allowed_requests is None else allowed_requests):
        raise ValueError("Adapter destination is not allowed")
    request = validate_request({
        "url": url, "method": method, "headers": headers, "body": body,
        "success_type": "text", "success_value": "adapter-internal",
    }, allow_private=allow_private)
    target = urlsplit(request["url"])
    port = target.port or (443 if target.scheme == "https" else 80)
    address = _resolve(target.hostname, port, allow_private)
    connection_type = _PinnedHTTPSConnection if target.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(target.hostname, port, address)
    outgoing = dict(request["headers"])
    outgoing["Accept-Encoding"] = "identity"
    outgoing["Connection"] = "close"
    try:
        path = target.path + ("?" + target.query if target.query else "")
        connection.request(method, path, body=body.encode("utf-8") if method == "POST" else None, headers=outgoing)
        response = connection.getresponse()
        if response.getheader("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
            raise _UnsupportedResponse()
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if not 0 <= int(length) <= MAX_RESPONSE_BYTES:
                    raise _UnsupportedResponse()
            except ValueError:
                raise _UnsupportedResponse() from None
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise _UnsupportedResponse()
        charset = response.headers.get_content_charset("utf-8")
        try:
            text = content.decode(charset, errors="replace")
        except LookupError:
            text = content.decode("utf-8", errors="replace")
        return {"status_code": response.status, "headers": dict(response.getheaders()), "body": text}
    finally:
        connection.close()


class _FormHashParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.formhash = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag.lower() == "input" and (attributes.get("name") or "").lower() == "formhash":
            value = attributes.get("value")
            if _safe_formhash(value):
                self.formhash = value


def _safe_formhash(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(32 < ord(char) < 127 for char in value)


def _extract_formhash(text):
    parser = _FormHashParser()
    parser.feed(text)
    if parser.formhash:
        return parser.formhash
    match = re.search(r"\bformhash\s*[:=]\s*(['\"])([^'\"\r\n]{1,128})\1", text, re.I)
    if match and _safe_formhash(match.group(2)):
        return match.group(2)
    return None


def _verification_required(text):
    lowered = text.lower()
    markers = ("cf_app_waf", "waf-nc-h5-mask", "cf-chl-", "challenge-platform",
               "请完成安全验证", "需要安全验证", "人机验证", "滑块验证", "请输入验证码",
               "请完成验证码", "验证码错误", "captcha required", "verify you are human")
    return any(marker in lowered for marker in markers)


def _login_required(text):
    phrases = ("请先登录", "请登录后", "您尚未登录", "您还未登录", "您还没有登录",
               "未登录", "登录已失效", "登录状态已失效", "登录已过期", "login required")
    if any(phrase in text.lower() for phrase in phrases):
        return True
    return bool(re.search(r"<form\b[^>]*(?:id|name)\s*=\s*['\"](?:loginform|login|logging)['\"]", text, re.I))


def _already_done(message):
    if not isinstance(message, str):
        return False
    return bool(re.match(r"^(?:您|你)?(?:今日|今天)(?:已经|已)签到(?:过)?(?:了)?(?:[！!。.,，；;\s]|$)", message.strip()))


def _response_parts(response):
    if not isinstance(response, dict):
        raise _UnsupportedResponse()
    status = response.get("status_code")
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise _UnsupportedResponse()
    headers = response.get("headers", {})
    if not isinstance(headers, dict):
        raise _UnsupportedResponse()
    content = response.get("body", "")
    if isinstance(content, bytes):
        if len(content) > MAX_RESPONSE_BYTES:
            raise _UnsupportedResponse()
        content = content.decode("utf-8", errors="replace")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise _UnsupportedResponse()
    return status, {str(key).lower(): value for key, value in headers.items()}, content


def run_site(spec: Any, allow_private: bool = False, transport=None) -> dict[str, Any]:
    """Dispatch fixed-site daily check-in and daily reward adapters."""
    from .platforms import PLATFORMS, run_platform
    if isinstance(spec, dict) and spec.get("site_id") in PLATFORMS:
        return run_platform(spec, allow_private=allow_private, transport=transport)
    started = time.monotonic()
    status_code = None

    def result(status, message=None):
        return {
            "ok": status in {"success", "already_done"}, "status": status,
            "status_code": status_code, "message": message or _MESSAGES[status],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    def receive(method, url, headers, body):
        nonlocal status_code
        response = transport(method, url, dict(headers), body) if transport is not None else _fetch(method, url, headers, body, allow_private=allow_private)
        status_code, reply_headers, text = _response_parts(response)
        if _verification_required(text):
            return result("needs_verification"), None
        if status_code == 401 or _login_required(text):
            return result("needs_login"), None
        if 300 <= status_code < 400:
            location = reply_headers.get("location", "")
            if isinstance(location, str) and any(word in location.lower() for word in ("login", "logging")):
                return result("needs_login"), None
            return result("unsupported"), None
        if not 200 <= status_code < 300:
            return result("failed", "恩山论坛返回 HTTP 错误，请到网站检查状态。"), None
        return None, text

    try:
        normalized = validate_site_spec(spec, allow_private=allow_private)
        if not normalized["cookie"]:
            return result("needs_login")
        headers = {"Cookie": normalized["cookie"], "Referer": ENSHAN_PAGE,
                   "User-Agent": "AutoSign/1.0", "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
        early, page = receive("GET", ENSHAN_PAGE, headers, "")
        if early:
            return early
        formhash = _extract_formhash(page)
        if not formhash:
            return result("unsupported")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        early, reply = receive("POST", ENSHAN_SIGN, headers, urlencode({"formhash": formhash}))
        if early:
            return early
        try:
            data = json.loads(reply)
        except (ValueError, RecursionError):
            return result("unsupported")
        if not isinstance(data, dict):
            return result("unsupported")
        message = data.get("message")
        if isinstance(message, str):
            if _verification_required(message):
                return result("needs_verification")
            if _login_required(message):
                return result("needs_login")
        if _already_done(data.get("message")):
            return result("already_done")
        if data.get("success") is True:
            return result("success")
        if data.get("success") is False:
            return result("failed", "恩山论坛返回签到失败，请到网站检查状态。")
        return result("unsupported")
    except (socket.timeout, TimeoutError):
        return result("failed", "恩山论坛请求超时，结果未知；请先确认网站签到状态。")
    except _UnsupportedResponse:
        return result("unsupported")
    except ValueError:
        return result("failed", "恩山论坛任务配置无效，请检查 Cookie。")
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return result("failed")
    except Exception:
        # Never stringify exceptions or remote responses: either may contain credentials.
        return result("failed")
