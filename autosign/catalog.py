"""Visible site templates and retained legacy templates."""
from .platforms import platform_sites

SITES = {
    "nodeseek": {
        "id": "nodeseek", "name": "NodeSeek", "monogram": "N",
        "engine": "browser", "visible": True,
        "description": "中文云服务器社区；签到页面和操作步骤需按当前站点配置并验证。",
        "status": "浏览器流程 · 待配置与验证",
        "start_url": "https://www.nodeseek.com/board",
        "allowed_hosts": ["www.nodeseek.com", "nodeseek.com"],
    },
    "hostloc": {
        "id": "hostloc", "name": "hostloc", "monogram": "H",
        "engine": "browser", "visible": True,
        "description": "全球主机交流论坛；先确认账号页面是否有可自动执行的每日签到。",
        "status": "浏览器流程 · 待配置与验证",
        "start_url": "https://hostloc.com/forum.php",
        "allowed_hosts": ["hostloc.com"],
    },
    "linuxdo": {
        "id": "linuxdo", "name": "LINUX DO", "monogram": "DO",
        "engine": "info", "visible": True,
        "description": "新的理想型社区；目前尚未确认有独立签到按钮或签到奖励。",
        "status": "签到功能未确认",
        "start_url": "https://linux.do/latest",
        "allowed_hosts": ["linux.do"],
    },
    "custom": {
        "id": "custom", "name": "其他论坛或网站", "monogram": "＋",
        "engine": "browser", "visible": True,
        "description": "填写签到页，用 Cookie 或 Token 连接账号，再设置签到按钮和成功提示。",
        "status": "自定义网页流程", "start_url": "", "allowed_hosts": [],
    },
    # Keep old tasks editable after replacing the default site catalog.
    "enshan": {
        "id": "enshan", "name": "恩山无线论坛（旧任务）", "engine": "site",
        "visible": False, "description": "旧版任务兼容入口。",
        "status": "旧版适配器", "start_url": "https://www.right.com.cn/forum/forum.php",
    },
    "jd": {
        "id": "jd", "name": "京东京豆（旧任务）", "engine": "browser",
        "visible": False, "description": "旧版浏览器任务兼容入口。",
        "status": "旧版任务", "start_url": "https://m.jd.com/",
        "allowed_hosts": ["m.jd.com", "bean.m.jd.com", "plogin.m.jd.com", "passport.jd.com", "api.m.jd.com", "st.360buyimg.com", "wq.360buyimg.com", "storage.360buyimg.com"],
    },
    "smzdm": {
        "id": "smzdm", "name": "什么值得买（旧任务）", "engine": "browser",
        "visible": False, "description": "旧版浏览器任务兼容入口。",
        "status": "旧版任务", "start_url": "https://zhiyou.smzdm.com/user/",
        "allowed_hosts": ["zhiyou.smzdm.com", "www.smzdm.com", "m.smzdm.com", "user-api.smzdm.com", "res.smzdm.com", "login.smzdm.com", "passport.smzdm.com"],
    },
}

SITES = {**platform_sites(), **SITES}
CATALOG_SITES = [site for site in SITES.values() if site.get("visible", False)]
