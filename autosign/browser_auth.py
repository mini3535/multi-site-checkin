"""Import browser Tokens without exposing them in pages, logs or global headers."""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit


XINGCHI_ORIGIN = "https://api.xingchiapi.com"


def origin(url):
    parsed = urlsplit(url)
    host = parsed.hostname
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    suffix = f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{host}{suffix}"


def storage_key(value, start_url):
    if not isinstance(value, str):
        raise ValueError("Token 存储字段名格式无效。")
    value = value.strip() or ("auth_token" if origin(start_url) == XINGCHI_ORIGIN else "access_token")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("Token 存储字段名需为 1 至 128 个字母、数字、下划线、点、冒号或连字符。")
    return value


def normalize_token(value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("Token 格式无效，请复制完整值，不要包含换行。")
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if value.lower() == "bearer" or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise ValueError("请粘贴登录 Token 或 Bearer 后的完整值，不要包含 Authorization: 字段名。")
    return value


def token_storage_state(value, start_url, key=""):
    from .browser_runner import _start_url
    start_url = _start_url(start_url)
    key = storage_key(key, start_url)
    return {"cookies": [], "origins": [{"origin": origin(start_url), "localStorage": [
        {"name": key, "value": normalize_token(value)}]}]}, {"storage_key": key}


def token_entry(state, start_url, key):
    for item in state["origins"]:
        if item["origin"] == origin(start_url):
            return next((entry for entry in item["localStorage"] if entry["name"] == key), None)
    return None


class TokenLoginError(Exception):
    def __init__(self, status):
        self.status = status


def prepare_token_session(spec, allow_private=False, transport=None):
    """Xingchi also requires cached user data, obtained from its fixed read-only API.

    Other sites receive only origin-scoped localStorage. Tokens are never added
    to context-wide headers, so allowed resource hosts do not inherit them.
    """
    state = spec["storage_state"]
    login = spec.get("token_login")
    if not login or origin(spec["start_url"]) != XINGCHI_ORIGIN or login["storage_key"] != "auth_token":
        return state
    entry = token_entry(state, spec["start_url"], login["storage_key"])
    if not entry:
        raise TokenLoginError("needs_login")
    from .site_adapters import _fetch, _response_parts, _verification_required
    url = XINGCHI_ORIGIN + "/api/v1/auth/me"
    headers = {"Authorization": "Bearer " + normalize_token(entry["value"]), "Accept": "application/json"}
    reply = (transport("GET", url, headers, "") if transport else
             _fetch("GET", url, headers, "", allow_private=allow_private, allowed_requests={("GET", url)}))
    status, _headers, text = _response_parts(reply)
    if _verification_required(text):
        raise TokenLoginError("needs_verification")
    if status in (401, 403):
        raise TokenLoginError("needs_login")
    if status != 200:
        raise TokenLoginError("failed")
    try:
        response = json.loads(text)
    except (ValueError, RecursionError):
        raise TokenLoginError("failed") from None
    if isinstance(response, dict) and response.get("code") in (401, "UNAUTHORIZED", "TOKEN_EXPIRED"):
        raise TokenLoginError("needs_login")
    user = response.get("data") if isinstance(response, dict) else None
    if (not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] != 0
            or not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0):
        raise TokenLoginError("failed")
    state = deepcopy(state)
    for item in state["origins"]:
        if item["origin"] == XINGCHI_ORIGIN:
            item["localStorage"] = [e for e in item["localStorage"] if e["name"] != "auth_user"]
            item["localStorage"].append({"name": "auth_user", "value": json.dumps(user, ensure_ascii=False)})
    return state
