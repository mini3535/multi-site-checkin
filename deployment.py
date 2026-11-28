"""Deployment configuration and completion output; never prints the data key."""

import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from werkzeug.security import check_password_hash


def validate_host(mode, host):
    if mode == "domain":
        host = host.lower()
        labels = host.split(".")
        if (len(host) > 253 or len(labels) < 2
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or labels[-1].isdigit()):
            raise ValueError("域名格式无效，请只输入已解析到此服务器的域名，例如 sign.example.com。")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return host
        raise ValueError("域名模式不能填写 IP，请选择无域名模式。")
    if mode == "ip":
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("无域名模式请填写服务器实际可访问的 IPv4 或 IPv6 地址。") from None
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_link_local:
            raise ValueError("请填写客户端可访问的服务器 IP，不能填写本机、通配或链路本地地址。")
        return str(address)
    if mode not in ("private", "tunnel"):
        raise ValueError("未知访问方式。")
    return ""


def configure(source, mode, host, external_proxy=False):
    host = validate_host(mode, host)
    values = {}
    for line in source.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    profiles = [p for p in values.get("COMPOSE_PROFILES", "").split(",") if p and p not in ("web", "tunnel")]
    if mode == "domain" and not external_proxy:
        profiles.append("web")
    if mode == "tunnel":
        profiles.append("tunnel")
    updates = {
        "AUTOSIGN_ACCESS_MODE": mode,
        "AUTOSIGN_DOMAIN": host if mode == "domain" else "",
        "AUTOSIGN_PUBLIC_HOST": host if mode == "ip" else "",
        "AUTOSIGN_EXTERNAL_PROXY": "true" if external_proxy and mode == "domain" else "false",
        "BIND_ADDRESS": ("::" if ":" in host else "0.0.0.0") if mode == "ip" else "127.0.0.1",
        "AUTOSIGN_COOKIE_SECURE": "true" if mode in ("domain", "tunnel") else "false",
        "AUTOSIGN_TUNNEL_URL": values.get("AUTOSIGN_TUNNEL_URL", "") if mode == "tunnel" else "",
        "COMPOSE_PROFILES": ",".join(profiles),
    }
    if mode == "tunnel":
        hostname = values.get("AUTOSIGN_TUNNEL_HOSTNAME") or "autosign-" + secrets.token_hex(4)
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
            raise ValueError("免域名访问的设备名称无效，请检查 AUTOSIGN_TUNNEL_HOSTNAME。")
        updates["AUTOSIGN_TUNNEL_HOSTNAME"] = hostname
    lines = []
    for line in source.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            continue
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in updates.items())
    return "\n".join(lines) + "\n"


def access_url(env):
    mode = env.get("AUTOSIGN_ACCESS_MODE", "private")
    port = env.get("PORT", "8080")
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("PORT 必须为 1 至 65535 的端口号。")
    if mode == "domain":
        return f"https://{validate_host(mode, env.get('AUTOSIGN_DOMAIN', ''))}/login"
    if mode == "tunnel":
        return validate_tunnel_url(env.get("AUTOSIGN_TUNNEL_URL", "")) + "/login"
    if mode == "ip":
        host = validate_host(mode, env.get("AUTOSIGN_PUBLIC_HOST", ""))
        return f"http://{'[' + host + ']' if ':' in host else host}:{port}/login"
    return f"http://localhost:{port}/login"


def completion(env):
    url = access_url(env)
    mode = env.get("AUTOSIGN_ACCESS_MODE", "private")
    db_path = (Path(env.get("AUTOSIGN_DATA_DIR", "/data")) / "autosign.db").resolve()
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT password_hash FROM admin_auth WHERE id=1").fetchone()
    if row is None:
        raise ValueError("管理员账号尚未初始化，不能生成完成信息。")
    lines = [f"网页链接：{url}"]
    if mode == "private":
        port = env.get("PORT", "8080")
        lines.extend(["此链接需先建立 SSH 隧道（或在 Netcatty 的主机浏览器打开）：",
                      f"ssh -L {port}:127.0.0.1:{port} 用户@服务器IP"])
    elif mode == "ip":
        lines.append("无域名 HTTP 访问：密码和会话未经 TLS 加密，请仅在可信网络使用；推荐配置域名 HTTPS 或 SSH 隧道。")
        lines.append("此地址不支持扩展自动连接。没有域名或 80/443 已被占用，可运行 sh setup.sh --tunnel，按提示授权免域名 HTTPS 访问。")
        lines.append("请确认服务器防火墙和云安全组允许所选端口；脚本不会修改防火墙。")
    elif mode == "tunnel":
        lines.append("免域名 HTTPS 访问已就绪：通过此地址打开助手即可使用浏览器扩展，不需要 SSH 隧道。")
        lines.append("入口由你授权的 Tailscale Funnel 提供；设备身份保存在项目数据卷，重启和升级时保留。")
    initial = env.get("AUTOSIGN_ADMIN_PASSWORD", "")
    if initial and check_password_hash(row[0], initial):
        lines.append(f"初始登录密码：{initial}")
    else:
        lines.append("登录密码：已在后台修改，请使用修改后的密码；初始密码已失效。")
    lines.append("登录后点击顶部「修改密码」。修改后所有设备重新登录，重启和升级不会恢复初始密码。")
    return "\n".join(lines)


def verify_domain(env):
    url = access_url(env).removesuffix("login") + "healthz"
    for _ in range(18):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200 and response.geturl() == url and json.loads(response.read(1024)) == {"status": "ok"}:
                    print("域名 HTTPS 与健康接口检查通过。")
                    return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(2)
    raise ValueError("域名 HTTPS 尚未通过检查。请确认 A/AAAA 记录指向本机、80/443 可达且未被其他服务占用，再重新运行 setup.sh。应用数据已保留。")


def validate_tunnel_url(value):
    """Accept only the HTTPS origin reported by this node, never a custom proxy."""
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        raise ValueError("免域名 HTTPS 地址无效，请重新运行 sh setup.sh --tunnel。")
    url = urlsplit(value)
    host = url.hostname or ""
    if (url.scheme != "https" or url.netloc != host or url.path or url.query or url.fragment
            or not host.endswith(".ts.net") or host == "ts.net"):
        raise ValueError("免域名 HTTPS 地址尚未就绪，请重新运行 sh setup.sh --tunnel 完成授权。")
    validate_host("domain", host)
    return value


def tunnel_status(source):
    """Return one validated URL; do not expose peer lists or node keys."""
    try:
        status = json.loads(source)
        if not isinstance(status, dict):
            return ""
        if status.get("BackendState") == "Running":
            host = status.get("Self", {}).get("DNSName", "").rstrip(".")
            return validate_tunnel_url("https://" + host)
        auth_url = status.get("AuthURL", "")
        url = urlsplit(auth_url)
        if (not any(ord(char) <= 32 for char in auth_url)
                and url.scheme == "https" and url.netloc == "login.tailscale.com"
                and url.path.startswith("/a/") and not url.fragment):
            return auth_url
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def set_tunnel_url(source, url):
    url = validate_tunnel_url(url)
    lines = [line for line in source.splitlines() if not line.startswith("AUTOSIGN_TUNNEL_URL=")]
    return "\n".join([*lines, "AUTOSIGN_TUNNEL_URL=" + url]) + "\n"


def verify_tunnel(env):
    url = validate_tunnel_url(env.get("AUTOSIGN_TUNNEL_URL", "")) + "/healthz"
    for _ in range(18):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if (response.status == 200 and response.geturl() == url
                        and json.loads(response.read(1024)) == {"status": "ok"}):
                    print("免域名 HTTPS 与健康接口检查通过。")
                    return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(2)
    raise ValueError("免域名 HTTPS 尚未通过检查，未报告安装成功。请检查 Tailscale 授权、Funnel 权限及服务器出站网络；新域名 DNS 生效可能需要几分钟。稍后重新运行 sh setup.sh --tunnel，任务和账号数据已保留。")


def config_from_stdin():
    env = dict(os.environ)
    for line in sys.stdin.read().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    return env


def main():
    try:
        command = sys.argv[1]
        if command == "configure":
            print(configure(sys.stdin.read(), sys.argv[2], sys.argv[3], "--external-proxy" in sys.argv[4:]), end="")
        elif command == "summary":
            print(completion(config_from_stdin() if "--config-stdin" in sys.argv[2:] else os.environ))
        elif command == "verify-domain":
            verify_domain(os.environ)
        elif command == "tunnel-status":
            print(tunnel_status(sys.stdin.read()))
        elif command == "set-tunnel-url":
            print(set_tunnel_url(sys.stdin.read(), sys.argv[2]), end="")
        elif command == "verify-tunnel":
            verify_tunnel(config_from_stdin())
        else:
            raise ValueError("未知部署命令。")
    except (ValueError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
