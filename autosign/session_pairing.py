"""Short-lived, one-use pairing for importing the active browser session."""
import copy
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .browser_runner import _host, _start_url, _state

PAIRING_TTL = 5 * 60
MAX_PAIRINGS = 100
MAX_ATTEMPTS = 5
_ALREADY_DONE_HINT = (
    r"(今日|今天).{0,12}(已经|已)(完成)?(签到|打卡)|(已经|已)(签到|打卡)|"
    r"already.{0,20}(checked|signed|claim)|(checked|signed)\s+in.{0,16}already"
)
_SUCCESS_HINT = (
    r"(签到|打卡|领取).{0,8}(成功|完成)|(成功|完成).{0,8}(签到|打卡|领取)|"
    r"check[\s-]?in.{0,12}(success|complete)|(success|complete).{0,12}check[\s-]?in|"
    r"claim.{0,12}(success|complete)"
)


def _https_host(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("请先填写有效的 HTTPS 签到页面。")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if (parsed.scheme.lower() != "https" or not hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)):
            raise ValueError()
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise ValueError("请先填写有效的 HTTPS 签到页面。") from None


class SessionPairingManager:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._items = {}
        self._lock = threading.Lock()

    def _prune(self, now):
        self._items = {key: item for key, item in self._items.items()
                       if item["expires_at"] > now and item["attempts"] < MAX_ATTEMPTS}

    def create(self, owner, start_url, allowed_hosts):
        if not isinstance(owner, str) or not owner:
            raise ValueError("登录状态已过期，请刷新页面后重试。")
        host = _https_host(start_url)
        hosts = []
        for item in allowed_hosts if isinstance(allowed_hosts, list) else []:
            if isinstance(item, str) and item.strip():
                hosts.append(_host(item.strip().lstrip(".")))
        hosts = list(dict.fromkeys([host, *hosts]))
        pairing_id = secrets.token_urlsafe(12)
        token = secrets.token_urlsafe(24)
        now = self.clock()
        with self._lock:
            self._prune(now)
            if len(self._items) >= MAX_PAIRINGS:
                raise ValueError("当前连接请求较多，请稍后再试。")
            self._items[pairing_id] = {
                "owner": owner,
                "mode": "session",
                "token_hash": hashlib.sha256(token.encode()).digest(),
                "host": host,
                "hosts": hosts,
                "expires_at": now + PAIRING_TTL,
                "attempts": 0,
                "state": None,
            }
        return {"id": pairing_id, "code": pairing_id + "." + token,
                "host": host, "expires_in": PAIRING_TTL}

    def create_discovery(self, owner):
        if not isinstance(owner, str) or not owner:
            raise ValueError("登录状态已过期，请刷新页面后重试。")
        pairing_id = secrets.token_urlsafe(12)
        token = secrets.token_urlsafe(24)
        now = self.clock()
        with self._lock:
            self._prune(now)
            if len(self._items) >= MAX_PAIRINGS:
                raise ValueError("当前连接请求较多，请稍后再试。")
            self._items[pairing_id] = {
                "owner": owner,
                "mode": "discovery",
                "token_hash": hashlib.sha256(token.encode()).digest(),
                "host": None,
                "hosts": [],
                "expires_at": now + PAIRING_TTL,
                "attempts": 0,
                "state": None,
                "discovery_claimed": False,
                "task_id": None,
                "discovery_error": None,
            }
        return {"id": pairing_id, "code": pairing_id + "." + token,
                "expires_in": PAIRING_TTL}

    def complete(self, code, page_url, state, hints=None, page_title=None):
        if not isinstance(code, str) or len(code) > 128 or code.count(".") != 1:
            raise ValueError("连接码无效或已过期，请重新创建。")
        pairing_id, token = code.split(".", 1)
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(pairing_id)
            if item is None:
                raise ValueError("连接码无效或已过期，请重新创建。")
            if not hmac.compare_digest(item["token_hash"], hashlib.sha256(token.encode()).digest()):
                item["attempts"] += 1
                raise ValueError("连接码不正确，请检查后重试。")
            if item["state"] is not None or item.get("discovery_claimed"):
                return {"status": "already_connected"}
            page_host = _https_host(page_url)
            if item["mode"] == "session" and page_host != item["host"]:
                item["attempts"] += 1
                raise ValueError("当前浏览器页面与连接码指定的网站不一致。")
            if item["mode"] == "discovery":
                item["host"] = page_host
                item["hosts"] = [page_host]
                item["start_url"] = _start_url(page_url)
                if isinstance(page_title, str):
                    title = " ".join(page_title.split())
                    item["page_title"] = "".join(char for char in title if ord(char) >= 32)[:120]
                else:
                    item["page_title"] = ""
            normalized = _state(state, item["hosts"])
            if not normalized["cookies"] and not any(origin["localStorage"] for origin in normalized["origins"]):
                raise ValueError("没有找到此网站可用的登录状态。请确认已登录并打开签到页面。")
            item["state"] = normalized
            item["hints"] = self._normalize_hints(hints)
            item["ready_at"] = now
            return {"status": "connected", "host": item["host"]}

    @staticmethod
    def _normalize_hints(value):
        if not isinstance(value, dict):
            return {"button_texts": [], "success_texts": [], "already_done_texts": [], "resource_hosts": []}
        keys = ("button_texts", "success_texts", "already_done_texts")
        result = {key: [] for key in keys}
        result["resource_hosts"] = []
        cleaned_by_key = {}
        for key in keys:
            candidates = value.get(key, [])
            if not isinstance(candidates, list):
                candidates = []
            cleaned = []
            for candidate in candidates[:30]:
                if (isinstance(candidate, str) and 0 < len(candidate.strip()) <= 160
                        and not any(ord(char) < 32 for char in candidate)):
                    text = candidate.strip()
                    if text not in cleaned:
                        cleaned.append(text)
            cleaned_by_key[key] = cleaned
        resources = value.get("resource_hosts", [])
        if isinstance(resources, list):
            for candidate in resources[:100]:
                if not isinstance(candidate, str):
                    continue
                try:
                    host = _host(candidate.strip().lstrip("."))
                except ValueError:
                    continue
                if host not in result["resource_hosts"]:
                    result["resource_hosts"].append(host)
        result["button_texts"] = cleaned_by_key["button_texts"]
        for key in ("success_texts", "already_done_texts"):
            for text in cleaned_by_key[key]:
                if re.search(_ALREADY_DONE_HINT, text, re.IGNORECASE):
                    target = "already_done_texts"
                elif re.search(_SUCCESS_HINT, text, re.IGNORECASE):
                    target = "success_texts"
                else:
                    continue
                if text not in result[target]:
                    result[target].append(text)

        return result

    def status(self, pairing_id, owner):
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(pairing_id)
            if item is None or not hmac.compare_digest(item["owner"], str(owner or "")):
                return {"status": "expired"}
            connected = (item["state"] is not None or item.get("task_id") is not None
                         or item.get("discovery_error") is not None)
            response = {"status": "connected" if connected else "waiting",
                        "host": item["host"]}
            if item.get("mode") == "discovery":
                response.update({"page_title": item.get("page_title", ""),
                                 "task_id": item.get("task_id"),
                                 "discovery_error": item.get("discovery_error")})
            elif item["state"] is not None:
                response["hints"] = item.get("hints", self._normalize_hints(None))
            return response

    def claim_discovery(self, pairing_id, code):
        with self._lock:
            item = self._items.get(pairing_id)
            if (item is None or item.get("mode") != "discovery" or item["state"] is None
                    or not self._matches_capability(item, pairing_id, code)
                    or item.get("discovery_claimed")):
                return None
            item["discovery_claimed"] = True
            return {"start_url": item["start_url"], "host": item["host"],
                    "page_title": item.get("page_title", ""),
                    "state": copy.deepcopy(item["state"]),
                    "hints": copy.deepcopy(item.get("hints", {}))}

    def finish_discovery(self, pairing_id, code, task_id=None, error=None):
        with self._lock:
            item = self._items.get(pairing_id)
            if (item is None or item.get("mode") != "discovery"
                    or not self._matches_capability(item, pairing_id, code)):
                return False
            item["task_id"] = task_id
            item["discovery_error"] = error
            item["state"] = None
            item.pop("hints", None)
            return True

    def discovery_result(self, pairing_id, code):
        with self._lock:
            item = self._items.get(pairing_id)
            if (item is None or item.get("mode") != "discovery"
                    or not self._matches_capability(item, pairing_id, code)):
                return None
            return {"task_id": item.get("task_id"),
                    "error": item.get("discovery_error"),
                    "page_title": item.get("page_title", ""),
                    "host": item.get("host", "")}

    @staticmethod
    def _matches_capability(item, pairing_id, code):
        if not isinstance(code, str) or len(code) > 128 or code.count(".") != 1:
            return False
        supplied_id, token = code.split(".", 1)
        if supplied_id != pairing_id or not token:
            return False
        return hmac.compare_digest(item["token_hash"], hashlib.sha256(token.encode()).digest())

    def get_session(self, pairing_id, owner, start_url):
        host = _https_host(start_url)
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(pairing_id)
            if (item is None or item["state"] is None
                    or not hmac.compare_digest(item["owner"], str(owner or ""))
                    or item["host"] != host):
                return None
            return copy.deepcopy(item["state"])

    def consume(self, pairing_id, owner, start_url):
        host = _https_host(start_url)
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(pairing_id)
            if (item is None or item["state"] is None
                    or not hmac.compare_digest(item["owner"], str(owner or ""))
                    or item["host"] != host):
                return None
            self._items.pop(pairing_id, None)
            return copy.deepcopy(item["state"])

    def discard_owner(self, owner):
        with self._lock:
            self._items = {key: item for key, item in self._items.items() if item["owner"] != owner}

    def discard_all(self):
        with self._lock:
            self._items.clear()
