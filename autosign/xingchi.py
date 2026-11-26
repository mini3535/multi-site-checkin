"""Fixed Xingchi page rules, shared by login and daily execution."""
ORIGIN = "https://api.xingchiapi.com"
HOST = "api.xingchiapi.com"
LOGIN_URL = ORIGIN + "/login"
DASHBOARD_URL = ORIGIN + "/dashboard"
DONE_SELECTOR = 'button:text-is("今日已签到")'


def task_spec(state):
    return {
        "engine": "browser", "site_id": "xingchi", "start_url": DASHBOARD_URL,
        "allowed_hosts": [HOST], "storage_state": state,
        "steps": [{"action": "click", "selector": 'button:has-text("签到领")', "timeout_ms": 10000}],
        "success_text": "今日已签到", "already_done_text": "",
        "login_selector": 'input[type="password"]',
        "verification_selector": 'iframe[src*="challenges.cloudflare.com"]',
    }
