"""Daily reward protocols informed by workbuddy-signin (MIT); see notices.

Only daily claims are supported. No credential discovery, automatic challenge
solving, chat generation, purchases or additional reward tasks are performed.
"""
from datetime import datetime
import hashlib
import json
import re
import time
from urllib.parse import urlencode, quote, unquote
from zoneinfo import ZoneInfo


def field(key, label, help_text, secret=True):
    return {"key": key, "label": label, "help": help_text, "secret": secret}


PLATFORMS = {
    "workbuddy": {
        "name": "WorkBuddy", "monogram": "WB", "start_url": "https://www.workbuddy.cn/",
        "description": "查询每日状态并领取签到积分；部分账号需要先在官方客户端完成当天对话。",
        "fields": [field("token", "Access Token", "使用已登录账号的 accessToken，不是 AI API Key。"),
                   field("uid", "账号 UID", "与 accessToken 同一账号的 account.uid。")],
    },
    "qianfan": {
        "name": "百度千帆", "monogram": "千", "start_url": "https://console.bce.baidu.com/",
        "description": "领取百度搭子 DuMate 的每日登录积分；不等同于千帆模型调用额度。",
        "fields": [field("cookie", "百度智能云登录 Cookie", "从 console.bce.baidu.com 请求中复制，需包含 BDUSS 和 bce-user-info；自动提取 CSRF，无需模型 API Key。")],
    },
    "minimax": {
        "name": "MiniMax Code", "monogram": "M", "start_url": "https://agent.minimax.cn/",
        "description": "使用网页登录会话查询签到面板并领取，不使用模型 API Key。",
        "fields": [field("token", "网页登录 Token", "agent.minimax.cn 登录后的 localStorage._token。"),
                   field("user_id", "用户 ID", "从自己的签到请求 user_id 参数复制。"),
                   field("device_id", "设备 ID", "从自己的签到请求 device_id 参数复制。"),
                   field("uuid", "会话 UUID", "从自己的签到请求 uuid 参数复制，不使用他人的设备参数。")],
    },
    "linkai": {
        "name": "Link AI", "monogram": "L", "start_url": "https://link-ai.tech/console/account",
        "description": "使用账号 Token 请求签到；网站要求验证码时暂停，需到官网人工完成。",
        "fields": [field("token", "账号 Token", "在 Link AI 账号页面获取登录令牌。")],
    },
    "qoder": {
        "name": "Qoder", "monogram": "Q", "start_url": "https://qoder.com/",
        "description": "仅接入国内版积分活动；活动关闭或无可领积分时显示暂不支持，国际版设备校验暂未接入。",
        "status": "国内版接口 · 活动视账号开放",
        "fields": [field("token", "国内版账号 Token", "使用自己 Qoder 国内版客户端的登录 Token，不是国际版或模型 API Key。"),
                   field("machine_id", "设备 Machine ID", "使用自己国内版客户端的 auth.machine-id，不生成随机设备身份。")],
    },
    "lingxi": {
        "name": "WPS 灵犀", "monogram": "W", "start_url": "https://lingxi.wps.cn/",
        "description": "读取每日任务，领取 daily_check_in 奖励，并再次查询确认结果。",
        "fields": [field("cookie", "登录 Cookie", "正常登录灵犀后，从网站请求中复制 Cookie 请求头。")],
    },
    "trae": {
        "name": "Trae Work", "monogram": "T", "start_url": "https://work.trae.cn/",
        "description": "使用登录 Cookie 换取短期 Token，再以自己的真实设备 ID 请求签到。",
        "fields": [field("cookie", "登录 Cookie", "从已登录的 work.trae.cn 会话请求中复制。"),
                   field("device_id", "真实设备 ID", "复制自己 Trae 客户端请求的 x-device-id；随机值可能被拒绝。")],
    },
    "coze": {
        "name": "Coze 扣子", "monogram": "C", "start_url": "https://www.coze.cn/",
        "description": "领取每日登录奖励并检查领取标记，不自动聊天或执行其他积分任务。",
        "fields": [field("cookie", "登录 Cookie", "从已登录的 www.coze.cn 请求中复制 Cookie 请求头。")],
    },
    "huawei": {
        "name": "华为码道", "monogram": "华",
        "start_url": "https://devcloud.cn-north-4.huaweicloud.com/chat/home",
        "description": "查询每日活动、领取并确认；需要 devcloud 会话，门户 Cookie 不通用。",
        "fields": [field("cookie", "码道登录 Cookie", "从 devcloud.cn-north-4.huaweicloud.com 请求中复制，包含 devclouddevuibjtcftk。")],
    },
}


def platform_sites():
    sites = {key: {**value, "id": key, "engine": "site", "visible": True,
                   "platform": True, "status": value.get("status", "接口已接入 · 待账号验证")}
             for key, value in PLATFORMS.items()}
    sites.update({
        "doubao": {"id": "doubao", "name": "豆包 Doubao", "monogram": "豆", "engine": "info",
                   "visible": True, "start_url": "https://www.doubao.com/", "status": "参考实现受限 · 暂不自动签到",
                   "description": "两个参考项目对浏览器签名和写接口可用性记录不一致，其中一个已停用；当前提供官网入口。"},
    })
    return sites


def validate_platform_spec(spec, allow_private=False):
    if not isinstance(spec, dict) or spec.get("engine") != "site" or spec.get("site_id") not in PLATFORMS:
        raise ValueError("平台配置无效。")
    normalized = {"engine": "site", "site_id": spec["site_id"]}
    for item in PLATFORMS[spec["site_id"]]["fields"]:
        value = spec.get(item["key"], "")
        if (not isinstance(value, str) or len(value) > 16384
                or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise ValueError(item["label"] + "格式无效，请复制原始值，不要包含换行。")
        normalized[item["key"]] = value.strip()
    return normalized


def has_platform_session(spec):
    return any(spec.get(f["key"]) for f in PLATFORMS[spec["site_id"]]["fields"] if f["secret"])


class _Stop(Exception):
    def __init__(self, status):
        self.status = status


def _object(value):
    if not isinstance(value, dict):
        raise _Stop("unsupported")
    return value


def _zero(value):
    return type(value) is int and value == 0


def run_platform(spec, allow_private=False, transport=None):
    from .site_adapters import (_fetch, _response_parts, _verification_required,
                                _login_required, _UnsupportedResponse)
    started = time.monotonic()
    status_code = None

    def result(status):
        messages = {"success": "平台已确认每日奖励领取成功。", "already_done": "平台已确认今日已领取。",
                    "needs_login": "平台登录失效或缺少凭据，请重新登录更新。",
                    "needs_verification": "平台要求人工验证，请前往官网完成后再试。",
                    "unsupported": "活动、返回格式或前置条件不符合要求，请到官网检查。",
                    "failed": "未确认领取成功，请先到官网核实；不会自动重试领取。"}
        return {"ok": status in {"success", "already_done"}, "status": status,
                "status_code": status_code, "message": messages[status],
                "duration_ms": round((time.monotonic() - started) * 1000)}

    def request(method, url, headers, payload=None):
        nonlocal status_code
        body = json.dumps(payload) if payload is not None else ""
        headers = {"Accept": "application/json", "User-Agent":
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", **headers}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        # Only code-defined routes below may call this closure. Reuse pinned
        # Reuse DNS/TLS validation, size limits, and no redirects.
        reply = (transport(method, url, headers, body) if transport is not None else
                 _fetch(method, url, headers, body, allow_private=allow_private,
                        allowed_requests={(method, url)}))
        status_code, response_headers, text = _response_parts(reply)
        if status_code == 401:
            raise _Stop("needs_login")
        if 300 <= status_code < 400:
            location = str(response_headers.get("location", "")).lower()
            raise _Stop("needs_login" if any(s in location for s in ("login", "signin", "passport")) else "unsupported")
        if _verification_required(text):
            raise _Stop("needs_verification")
        if _login_required(text):
            raise _Stop("needs_login")
        if not 200 <= status_code < 300:
            raise _Stop("failed")
        try:
            data = _object(json.loads(text))
        except (ValueError, RecursionError):
            raise _Stop("unsupported") from None
        code = data.get("code")
        if type(code) is int and code in (401, 1001, 20113, 710012001):
            raise _Stop("needs_login")
        if code in ("UNAUTHORIZED", "TOKEN_EXPIRED", "NOT_LOGGED_IN"):
            raise _Stop("needs_login")
        return data

    try:
        spec = validate_platform_spec(spec, allow_private)
        key = spec["site_id"]
        if any(not spec.get(f["key"]) for f in PLATFORMS[key]["fields"]):
            return result("needs_login")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        cookie_headers = {"Cookie": spec.get("cookie", "")}
        if key == "workbuddy":
            base = "https://copilot.tencent.com/v2/billing/meter"
            headers = {"Authorization": "Bearer " + spec["token"], "X-User-Id": spec["uid"],
                       "X-Domain": "copilot.tencent.com"}
            before = request("POST", base + "/checkin-activity-status", headers, {})
            if not _zero(before.get("code")):
                raise _Stop("unsupported")
            if _object(before.get("data")).get("today_checked_in") is True:
                return result("already_done")
            if before["data"].get("today_checked_in") is not False:
                raise _Stop("unsupported")
            claimed = request("POST", base + "/daily-checkin", headers, {})
            if type(claimed.get("code")) is int and claimed["code"] == 10001:
                return result("already_done")
            if not _zero(claimed.get("code")):
                raise _Stop("failed")
            after = request("POST", base + "/checkin-activity-status", headers, {})
            return result("success" if _zero(after.get("code")) and
                          _object(after.get("data")).get("today_checked_in") is True else "failed")
        if key == "qianfan":
            cookies = dict(part.strip().split("=", 1) for part in spec["cookie"].split(";") if "=" in part)
            csrf = cookies.get("bce-user-info", "").strip('"')
            if not cookies.get("BDUSS") or not csrf:
                raise _Stop("needs_login")
            base = "https://console.bce.baidu.com/api/dumate/points/"
            headers = {**cookie_headers, "csrftoken": csrf, "X-Requested-With": "XMLHttpRequest",
                       "Origin": "https://console.bce.baidu.com", "Referer": "https://console.bce.baidu.com/"}

            def issued():
                data = request("GET", base + "loginBonusInfo", headers)
                if data.get("success") is not True:
                    raise _Stop("needs_login" if isinstance(data.get("message"), dict) and
                                data["message"].get("redirect") else "unsupported")
                state = _object(data.get("result", data.get("data"))).get("hasIssued")
                if type(state) is not bool:
                    raise _Stop("unsupported")
                return state

            if issued() is True:
                return result("already_done")
            claim = request("POST", base + "loginBonus", headers, {})
            if claim.get("success") is not True:
                raise _Stop("failed")
            return result("success" if issued() is True else "failed")
        if key == "qoder":
            base = "https://openapi.qoder.com.cn/sash/api/v1/me/campaigns"
            headers = {"Authorization": "Bearer " + spec["token"], "Cosy-MachineId": spec["machine_id"],
                       "Cosy-ClientType": "10", "Cosy-Version": "0.3.3", "Cosy-MachineOS": "windows",
                       "User-Agent": "Qoder"}

            def campaigns():
                data = request("GET", base + "?clientType=10", headers)
                if "code" in data and not _zero(data["code"]):
                    raise _Stop("failed")
                items = data.get("campaigns")
                if not isinstance(items, list):
                    raise _Stop("unsupported")
                return [c for c in items if isinstance(c, dict) and
                        c.get("actionType") == "CLAIM_BENEFIT" and
                        isinstance(c.get("benefit"), dict) and c["benefit"].get("kind") == "CREDITS"]

            items = campaigns()
            camp = next((c for c in items if c.get("claimStatus") == "CLAIMABLE"), None)
            if camp is None:
                return result("already_done" if any(c.get("claimStatus") == "CLAIMED" for c in items) else "unsupported")
            cid = camp.get("campaignId")
            if type(cid) not in (str, int) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(cid)):
                raise _Stop("unsupported")
            claim = request("POST", base + "/" + quote(str(cid), safe="") + "/claim", headers, {})
            if ("code" in claim and not _zero(claim["code"])) or claim.get("error"):
                raise _Stop("failed")
            confirmed = any(c.get("campaignId") == cid and c.get("claimStatus") == "CLAIMED" for c in campaigns())
            return result("success" if confirmed else "failed")
        if key == "minimax":
            def mm(path, payload=None):
                now = time.time()
                seconds = int(now)
                body = json.dumps(payload) if payload is not None else ""
                signature = hashlib.md5((str(seconds) + "I*7Cf%WZ#S&%1RlZJ&C2" + body).encode()).hexdigest()
                params = {"device_platform": "web", "biz_id": "3", "app_id": "3001",
                          "version_code": "22201", "unix": str(int(now * 1000)),
                          "timezone_offset": "28800", "sys_language": "zh", "lang": "zh",
                          "client": "web", "region": "cn",
                          **{k: spec[k] for k in ("token", "user_id", "device_id", "uuid")}}
                data = request("POST" if payload is not None else "GET",
                               "https://agent.minimax.cn/minimax-cloud/api/v1" + path + "?" + urlencode(params),
                               {"x-timestamp": str(seconds), "x-signature": signature}, payload)
                if not _zero(_object(data.get("base_resp")).get("status_code")):
                    raise _Stop("failed")
                return _object(data.get("data"))

            days = mm("/signin/status").get("days")
            if not isinstance(days, list):
                raise _Stop("unsupported")
            current = next((d for d in days if isinstance(d, dict) and d.get("is_today") is True), None)
            if current is None:
                raise _Stop("unsupported")
            if type(current.get("status")) is int and current["status"] == 3:
                return result("already_done")
            if type(current.get("status")) is not int or current["status"] != 2:
                raise _Stop("unsupported")
            claimed = mm("/signin/claim", {}).get("claim_result")
            return result({1: "success", 2: "already_done"}.get(claimed, "failed")
                          if type(claimed) is int else "failed")
        if key == "linkai":
            data = request("GET", "https://link-ai.tech/api/chat/web/app/user/sign/in",
                           {"Authorization": "Bearer " + spec["token"], "Referer": PLATFORMS[key]["start_url"]})
            if data.get("success") is True:
                return result("success")
            code = data.get("code")
            if type(code) is int and code == 834:
                return result("already_done")
            if type(code) is int and code == 870:
                return result("needs_verification")
            raise _Stop("failed")
        if key == "lingxi":
            base = "https://lingxi.wps.cn/api/public/v1/tasks"
            date = "?date=" + today

            def daily():
                data = request("GET", base + date, cookie_headers)
                tasks = _object(data.get("data")).get("tasks")
                if not isinstance(tasks, list):
                    raise _Stop("unsupported")
                task = next((t for t in tasks if isinstance(t, dict) and t.get("task_key") == "daily_check_in"), None)
                if task is None:
                    raise _Stop("unsupported")
                return task

            before = daily().get("status")
            if before == "claimed":
                return result("already_done")
            if before not in ("incomplete", "completed", "claimable"):
                raise _Stop("unsupported")
            request("POST", base + "/daily_check_in/claim" + date, cookie_headers, {})
            return result("success" if daily().get("status") == "claimed" else "failed")
        if key == "trae":
            headers = {**cookie_headers, "Origin": "https://work.trae.cn", "Referer": "https://work.trae.cn/"}
            auth = request("POST", "https://api.trae.cn/cloudide/api/v3/common/GetUserToken", headers)
            token = _object(auth.get("Result")).get("Token")
            if (not isinstance(token, str) or not token or len(token) > 16384
                    or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                raise _Stop("needs_login")
            headers = {"Authorization": "Cloud-IDE-JWT " + token, "x-device-id": spec["device_id"],
                       "Origin": "https://work.trae.cn", "Referer": "https://work.trae.cn/"}
            base = "https://api.trae.cn/trae/api/v2/ug/checkin_credits/"
            status = request("POST", base + "status", headers, {"req_source": 2})
            if not _zero(status.get("code")):
                raise _Stop("unsupported")
            if status.get("checked_in") is True:
                return result("already_done")
            if status.get("checked_in") is not False:
                raise _Stop("unsupported")
            claimed = request("POST", base + "claim", headers, {"req_source": 2})
            return result("success" if _zero(claimed.get("code")) else "failed")
        if key == "coze":
            data = request("POST", "https://www.coze.cn/api/marketplace/incentive/activity/reward",
                           {**cookie_headers, "Referer": "https://www.coze.cn/"},
                           {"activity_types": [2], "source": "web_new"})
            if not _zero(data.get("code")):
                raise _Stop("failed")
            grants = _object(data.get("data")).get("activity_grant_infos")
            if not isinstance(grants, list):
                raise _Stop("unsupported")
            daily = next((g for g in grants if isinstance(g, dict) and type(g.get("activity_type")) is int and g["activity_type"] == 2), None)
            if daily and daily.get("granted_this_time") is True:
                return result("success")
            if daily and daily.get("granted_today") is True:
                return result("already_done")
            raise _Stop("unsupported")
        if key == "huawei":
            headers = {**cookie_headers, "agent-type": "PromptCenter", "x-language": "zh-cn",
                       "language": "zh-cn", "x-requested-with": "XMLHttpRequest",
                       "x-codearts-doer-scenario-type": "web", "Content-Type": "application/json",
                       "Referer": PLATFORMS[key]["start_url"]}
            for part in spec["cookie"].split(";"):
                name, _, value = part.strip().partition("=")
                if name == "devclouddevuibjtcftk":
                    headers["cftk"] = unquote(value)
            if not headers.get("cftk"):
                raise _Stop("needs_login")
            base = "https://devcloud.cn-north-4.huaweicloud.com/chat/PromptCenterService"

            def delivery():
                data = request("GET", base + "/v1/ops/delivery?channel=WEB&_=" + str(int(time.time() * 1000)), headers)
                if not _zero(data.get("code")):
                    raise _Stop("failed")
                items = _object(data.get("data")).get("items")
                if not isinstance(items, list):
                    raise _Stop("unsupported")
                return items

            done_states = ("CLAIMED", "CONFIRMED", "CONSUMED")

            def done(camp):
                actions = camp.get("claimActions")
                if actions:
                    return (isinstance(actions, list) and all(isinstance(a, dict) and
                            a.get("status") in done_states for a in actions))
                return camp.get("status") in done_states

            daily_types = ("USER_LOGIN", "DAILY_CLAIM", "DAILY_CHECKIN", "DAILY_CHECK_IN", "DAILY_SIGN_IN", "USER_LOGIN_DAILY")
            camp = next((c for c in delivery() if isinstance(c, dict) and c.get("type") in daily_types), None)
            if camp is None:
                raise _Stop("unsupported")
            cid = camp.get("campaignId")
            if type(cid) is not int or not 0 < cid < 1000000:
                raise _Stop("unsupported")
            if done(camp):
                return result("already_done")
            actions = camp.get("claimActions") or []
            if not isinstance(actions, list) or len(actions) > 6:
                raise _Stop("unsupported")
            benefits = []
            for action in actions:
                if not isinstance(action, dict) or action.get("status") not in (*done_states, "ELIGIBLE"):
                    raise _Stop("unsupported")
                if action["status"] == "ELIGIBLE":
                    bid = action.get("benefitId")
                    if type(bid) not in (str, int) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(bid)):
                        raise _Stop("unsupported")
                    benefits.append(bid)
            if not actions and camp.get("status") not in ("ELIGIBLE", "AVAILABLE"):
                raise _Stop("unsupported")
            for bid in benefits if actions else [None]:
                payload = {"campaignId": cid, "idempotentKey": f"claim_{cid}_{bid}_{int(time.time() * 1000)}", "channel": "WEB"}
                if bid is not None:
                    payload["benefitId"] = bid
                claim = request("POST", base + "/v1/ops/claim", headers, payload)
                if not _zero(claim.get("code")):
                    raise _Stop("failed")
            confirm = request("POST", base + "/v1/ops/confirm", headers, {"campaignId": cid})
            if not _zero(confirm.get("code")):
                raise _Stop("failed")
            refreshed = next((c for c in delivery() if isinstance(c, dict) and c.get("campaignId") == cid), None)
            return result("success" if refreshed and done(refreshed) else "failed")
        return result("unsupported")
    except _Stop as exc:
        return result(exc.status)
    except _UnsupportedResponse:
        return result("unsupported")
    except Exception:
        # Neither exception details nor remote text may reach logs or the UI.
        return result("failed")
