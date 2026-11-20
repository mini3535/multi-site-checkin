"""Export only a fresh browser context after the user completes normal login."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

SITES = {
    "nodeseek": "https://www.nodeseek.com/board",
    "hostloc": "https://hostloc.com/forum.php",
}


def main():
    parser = argparse.ArgumentParser(description="在你自己的电脑登录网站，然后导出供服务器使用的会话。")
    parser.add_argument("--site", choices=sorted(SITES), help="登录的网站")
    parser.add_argument("--url", help="其他网站的 HTTP/HTTPS 登录地址")
    parser.add_argument("--output", required=True, help="保存的会话 JSON 文件（包含登录凭据）")
    parser.add_argument("--browser", choices=["chromium", "chrome"], default="chromium")
    args = parser.parse_args()
    url = args.url or SITES.get(args.site)
    parsed = urlsplit(url or "")
    if not url or parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        parser.error("请提供 --site 或有效的 --url。")
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        parser.error("输出文件已存在，请先自行备份或选择新文件名。")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error("请先安装项目依赖，再运行 python -m playwright install chromium。")
    print("浏览器会打开一个新的独立会话。请手动正常登录网站，完成网站要求的验证。")
    print("登录完成后回到终端按 Enter；本工具不会代填密码或绕过验证。")
    try:
        with sync_playwright() as playwright:
            launch = {"headless": False}
            if args.browser == "chrome":
                launch["channel"] = "chrome"
            elif os.environ.get("AUTOSIGN_BROWSER_EXECUTABLE"):
                launch["executable_path"] = os.environ["AUTOSIGN_BROWSER_EXECUTABLE"]
            browser = playwright.chromium.launch(**launch)
            try:
                context = browser.new_context(locale="zh-CN")
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                input("确认已在网页正常登录后，按 Enter 导出：")
                state = context.storage_state()
                output.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, ensure_ascii=False)
            finally:
                browser.close()
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 1
    except Exception:
        print("会话导出失败。请确认浏览器已安装、网络正常，且输出文件可写。", file=sys.stderr)
        return 1
    print(f"会话已保存：{output}")
    print("此文件包含登录凭据，请只在自己的签到管理页导入，勿发送给其他人。")
    print("页面操作规则还需按真实网站核对；导出会话本身不代表签到适配已验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
