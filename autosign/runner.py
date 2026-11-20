"""Bounded HTTP requests with explicit success checks and safe result messages.

This module deliberately does not execute arbitrary Python, shell, or JavaScript.
It follows no redirects, never returns response bodies, and resolves and pins the
destination for each execution so public-target checks survive DNS rebinding.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_HEADER_BYTES = 32 * 1024
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BLOCKED_HEADERS = {
    "host", "content-length", "connection", "transfer-encoding",
    "proxy-connection", "proxy-authorization", "upgrade", "trailer",
    "te", "accept-encoding",
}


class _TargetBlocked(Exception):
    pass


class _ResponseTooLarge(Exception):
    pass


def _reject_constant(_value: str) -> None:
    raise ValueError("JSON 不支持非有限数值")


def _load_json(value: str) -> Any:
    return json.loads(value, parse_constant=_reject_constant)


def _normalize_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        raise ValueError("请填写有效的网址（最多 4096 个字符）")
    url = url.strip()
    if any(ord(char) < 33 or ord(char) == 127 for char in url):
        raise ValueError("网址不能包含空白或控制字符")
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("网址仅支持 HTTP 或 HTTPS")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("网址必须包含主机名，不能包含账号密码")
        if parsed.fragment:
            raise ValueError("网址不能包含 # 片段")
        hostname = parsed.hostname
        if "%" in hostname or "\\" in hostname:
            raise ValueError("主机名格式无效")
        try:
            address = ipaddress.ip_address(hostname)
            host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        except ValueError:
            host = hostname.encode("idna").decode("ascii").lower()
            if len(host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
                raise ValueError("主机名格式无效")
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("端口必须在 1 到 65535 之间")
        netloc = host if port is None else f"{host}:{port}"
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("网址格式无效，请检查协议、主机名和端口") from exc


def validate_request(spec: Any, allow_private: bool = False) -> dict[str, Any]:
    """Validate configuration without DNS queries or other network activity.

    ``allow_private`` is accepted to keep the public interface stable, but target
    addresses are always checked at execution time, after resolving the host.
    ``success_value`` is a text substring or a JSON literal encoded as a string.
    ``success_path`` uses dots, for example ``data.checked`` or ``items.0.ok``.
    """
    del allow_private
    if not isinstance(spec, dict):
        raise ValueError("请求配置必须是对象")
    url = _normalize_url(spec.get("url"))
    method = spec.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in {"GET", "POST"}:
        raise ValueError("请求方法仅支持 GET 或 POST")
    method = method.upper()
    incoming = spec.get("headers", {})
    if not isinstance(incoming, dict) or len(incoming) > 40:
        raise ValueError("请求头必须是对象，且最多 40 项")
    headers: dict[str, str] = {}
    seen = set()
    total = 0
    for name, value in incoming.items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name) or len(name) > 128:
            raise ValueError("请求头名称格式无效")
        lowered = name.lower()
        if lowered in _BLOCKED_HEADERS:
            raise ValueError("部分连接控制请求头不能手动设置")
        if lowered in seen:
            raise ValueError("请求头名称不能重复")
        if not isinstance(value, str) or len(value) > 16384:
            raise ValueError("请求头的值必须是字符串，单项最多 16384 个字符")
        if any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
            raise ValueError("请求头不能包含换行或控制字符")
        try:
            encoded = value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("请求头的值请使用 Latin-1 编码或网站提供的转义形式") from exc
        total += len(name) + len(encoded) + 4
        headers[name] = value
        seen.add(lowered)
    if total > MAX_HEADER_BYTES:
        raise ValueError("请求头总大小不能超过 32 KiB")
    body = spec.get("body", "")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("请求体必须是字符串，且不能超过 64 KiB")
    if method == "GET" and body:
        raise ValueError("GET 请求不能设置请求体")
    success_type = spec.get("success_type")
    if not isinstance(success_type, str) or success_type not in {"text", "json"}:
        raise ValueError("请选择文本或 JSON 成功条件")
    success_value = spec.get("success_value")
    if not isinstance(success_value, str) or not success_value.strip() or len(success_value) > 4096:
        raise ValueError("必须填写成功条件，最多 4096 个字符")
    success_path = spec.get("success_path", "")
    if not isinstance(success_path, str) or len(success_path) > 512:
        raise ValueError("JSON 路径格式无效或过长")
    if success_type == "json":
        if success_path and any(not part or len(part) > 100 for part in success_path.split(".")):
            raise ValueError("JSON 路径请使用 data.checked 或 items.0.ok 的形式")
        try:
            _load_json(success_value)
        except (ValueError, RecursionError) as exc:
            raise ValueError("JSON 期望值必须是有效的 JSON，例如 true、0 或 \"成功\"") from exc
    else:
        success_path = ""
    return {
        "url": url, "method": method, "headers": headers, "body": body,
        "success_type": success_type, "success_value": success_value,
        "success_path": success_path,
    }


def _resolve(host: str, port: int, allow_private: bool) -> tuple:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not addresses:
        raise socket.gaierror()
    # Reject the entire answer if any address is nonpublic; do not merely select
    # a public address out of a mixed DNS answer. IPv4-mapped IPv6 is checked too.
    for family, _socktype, _proto, _canonname, sockaddr in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise _TargetBlocked()
        address = ipaddress.ip_address(sockaddr[0])
        candidate = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else address
        candidate = candidate or address
        if not allow_private and (
            not candidate.is_global or candidate.is_multicast or candidate.is_reserved
            or candidate.is_loopback or candidate.is_link_local or candidate.is_unspecified
        ):
            raise _TargetBlocked()
    return addresses[0]


def _connect_pinned(address: tuple, timeout: float) -> socket.socket:
    family, socktype, proto, _canonname, sockaddr = address
    sock = socket.socket(family, socktype, proto)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        return sock
    except BaseException:
        sock.close()
        raise


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: tuple):
        super().__init__(host, port=port, timeout=TIMEOUT_SECONDS)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = _connect_pinned(self._pinned_address, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: tuple):
        self._tls_context = ssl.create_default_context()
        super().__init__(host, port=port, timeout=TIMEOUT_SECONDS, context=self._tls_context)
        self._pinned_address = address

    def connect(self) -> None:
        raw = _connect_pinned(self._pinned_address, self.timeout)
        try:
            self.sock = self._tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _json_equal(actual: Any, expected: Any) -> bool:
    # JSON booleans must not compare equal to numbers (Python's True == 1).
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _matches(response: bytes, charset: str, spec: dict[str, Any]) -> bool:
    try:
        content = response.decode(charset, errors="replace")
    except LookupError:
        content = response.decode("utf-8", errors="replace")
    if spec["success_type"] == "text":
        return spec["success_value"] in content
    try:
        value = _load_json(content)
        expected = _load_json(spec["success_value"])
        if spec["success_path"]:
            for part in spec["success_path"].split("."):
                if isinstance(value, dict):
                    value = value[part]
                elif isinstance(value, list) and part.isascii() and part.isdigit():
                    value = value[int(part)]
                else:
                    return False
        return _json_equal(value, expected)
    except (ValueError, KeyError, IndexError, TypeError, RecursionError):
        return False


def run_request(spec: Any, allow_private: bool = False) -> dict[str, Any]:
    """Execute one request; return only safe metadata, never remote content.

    A successful HTTP status alone never marks a check-in successful. Both 2xx
    and a matching explicit success condition are required. There is no retry: a
    lost response can occur after a remote POST has already changed state.
    """
    started = time.monotonic()
    status = None
    connection = None

    def result(ok: bool, message: str) -> dict[str, Any]:
        return {
            "ok": ok, "status_code": status, "message": message,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    try:
        request = validate_request(spec, allow_private=allow_private)
        parsed = urlsplit(request["url"])
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = _resolve(hostname, port, allow_private)
        connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        connection = connection_type(hostname, port, address)
        headers = dict(request["headers"])
        if not any(name.lower() == "user-agent" for name in headers):
            headers["User-Agent"] = "AutoSign/1.0"
        headers["Accept-Encoding"] = "identity"
        headers["Connection"] = "close"
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        body = request["body"].encode("utf-8") if request["method"] == "POST" else None
        connection.request(request["method"], path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        if not 200 <= status < 300:
            return result(False, f"HTTP {status}，未满足签到成功条件")
        encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        if encoding not in {"", "identity"}:
            return result(False, "服务器返回了不支持的压缩响应")
        content_length = response.getheader("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    raise _ResponseTooLarge()
            except ValueError:
                return result(False, "服务器返回的响应长度无效")
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge()
        charset = response.headers.get_content_charset("utf-8")
        if _matches(content, charset, request):
            return result(True, f"成功条件已满足 · HTTP {status}")
        return result(False, f"响应未满足成功条件 · HTTP {status}")
    except _TargetBlocked:
        return result(False, "目标地址不允许访问（默认仅支持公网地址）")
    except _ResponseTooLarge:
        return result(False, "响应超过 1 MiB，已停止读取")
    except (socket.timeout, TimeoutError):
        return result(False, "请求超时；请先确认网站是否已签到，再决定是否重试")
    except socket.gaierror:
        return result(False, "无法解析目标网站地址")
    except ssl.SSLError:
        return result(False, "HTTPS 证书验证或安全连接失败")
    except ValueError:
        return result(False, "请求配置无效")
    except (OSError, http.client.HTTPException):
        return result(False, "网络请求失败；请先确认网站是否已签到，再决定是否重试")
    except Exception:
        # Do not stringify exceptions: libraries may include URLs, header values,
        # or remote response content in their exception messages.
        return result(False, "请求执行失败，请检查配置或网站状态")
    finally:
        if connection is not None:
            connection.close()
