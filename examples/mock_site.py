"""Local demonstration only: python examples/mock_site.py. Not a real website."""
import json
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    browser_signins = 0

    def do_GET(self):
        if self.path != "/browser-checkin":
            self.send_error(404)
            return
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        session = cookies.get("demo_session")
        value = session.value if session else ""
        if value == "captcha":
            content = '<h1 id=verification>请完成安全验证</h1>'
        elif value != "valid":
            content = '<h1 id=login-panel>请先登录</h1>'
        elif cookies.get("demo_done"):
            content = '<h1 id=result>今日已签到</h1>'
        else:
            content = '''<h1>本地模拟签到页面</h1><button id=daily-checkin>每日签到</button><p id=result></p>
                <script>document.querySelector('#daily-checkin').onclick=async()=>{
                const response=await fetch('/api/browser-checkin',{method:'POST'});
                const data=await response.json();
                document.querySelector('#result').textContent=data.message;};</script>'''
        payload = ('<!doctype html><meta charset=utf-8><title>本地模拟签到</title>'+content).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path == "/api/browser-checkin":
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            valid = cookies.get("demo_session") and cookies["demo_session"].value == "valid"
            if valid:
                type(self).browser_signins += 1
            payload = json.dumps({"message": "签到成功" if valid else "登录已过期"}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if valid:
                self.send_header("Set-Cookie", "demo_done=yes; Path=/; SameSite=Lax")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path != "/api/checkin":
            self.send_error(404)
            return
        valid = self.headers.get("Cookie") == "demo_session=valid"
        payload = json.dumps({"code": 0 if valid else 401, "message": "签到成功" if valid else "登录已过期"}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print("本地模拟网站：http://127.0.0.1:8099/api/checkin（仅 POST）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
