"""Isolated, Unix-socket-only updater for the official Compose deployment.

Only this companion service has Docker access. Requests cannot supply images,
paths, shell commands, Compose arguments or registry credentials.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from .update_client import CONTROL_DIR


IMAGE = "ghcr.io/mini3535/multi-site-checkin"
REVISION = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
ACTIVE = {"downloading", "preflight", "stopping", "backing_up", "starting", "rolling_back"}


class UpdateError(Exception):
    pass


def registry_json(url, token=None, digest=None):
    headers = {"Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError()
    actual = "sha256:" + hashlib.sha256(body).hexdigest()
    if digest and actual != digest:
        raise ValueError()
    return json.loads(body), actual


def latest_release():
    """latest changes only after CI tests and publishes both architectures."""
    try:
        auth, _ = registry_json("https://ghcr.io/token?service=ghcr.io&scope=repository:mini3535/multi-site-checkin:pull")
        token = auth["token"]
        base = "https://ghcr.io/v2/mini3535/multi-site-checkin/"
        index, digest = registry_json(base + "manifests/latest", token)
        child = next(item for item in index["manifests"] if item.get("platform", {}).get("architecture") == "amd64"
                     and item.get("platform", {}).get("os") == "linux")
        child_digest = child["digest"]
        if not DIGEST.fullmatch(child_digest):
            raise ValueError()
        manifest, _ = registry_json(base + "manifests/" + child_digest, token, child_digest)
        config_digest = manifest["config"]["digest"]
        if not DIGEST.fullmatch(config_digest):
            raise ValueError()
        config, _ = registry_json(base + "blobs/" + config_digest, token, config_digest)
        env = dict(item.split("=", 1) for item in config["config"]["Env"] if "=" in item)
        revision = env.get("AUTOSIGN_BUILD_REVISION", "")
        if not REVISION.fullmatch(revision):
            raise ValueError()
        return {"revision": revision, "digest": digest,
                "protocol": env.get("AUTOSIGN_UPDATE_PROTOCOL", ""),
                "created": config.get("created", "")[:40]}
    except Exception:
        raise UpdateError("暂时无法连接镜像仓库，请稍后重新检查；当前服务保持运行。") from None


class Deployment:
    def __init__(self, root, data):
        self.root = Path(root)
        self.data = Path(data)
        if not self.root.is_absolute() or not (self.root / "compose.yaml").is_file():
            raise UpdateError("未找到原部署配置，请先在原安装目录完成一次终端升级。")

    def command(self, args, image=None, timeout=120):
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
               "HOME": "/tmp", "PWD": str(self.root)}
        if image:
            env["AUTOSIGN_IMAGE"] = image
        try:
            result = subprocess.run(["docker", "--host", "unix:///var/run/docker.sock", *args],
                                    cwd=self.root, env=env, capture_output=True, timeout=timeout)
            if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
                raise ValueError()
            return result.stdout.decode()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Docker/Compose output includes environment values; never expose it.
            raise UpdateError("部署操作未完成，请检查 Docker 状态、磁盘空间和镜像仓库连通性。") from None

    def compose(self, *args, image=None, timeout=120):
        return self.command(["compose", "--project-directory", str(self.root), "-f", str(self.root / "compose.yaml"), *args], image, timeout)

    def current(self):
        config = json.loads(self.compose("config", "--format", "json"))
        if config["services"]["app"]["image"] != IMAGE + ":latest":
            raise UpdateError("当前使用固定标签或自定义镜像，请在终端更新；网页更新仅支持官方 latest。")
        container = self.compose("ps", "-a", "-q", "app").strip()
        if not re.fullmatch(r"[a-f0-9]{12,64}", container):
            raise UpdateError("未找到唯一的助手容器，请检查部署状态。")
        info = json.loads(self.command(["container", "inspect", container]))[0]
        env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
        revision = env.get("AUTOSIGN_BUILD_REVISION", "")
        if not REVISION.fullmatch(revision):
            raise UpdateError("本地构建需在终端更新，网页更新仅支持官方发布镜像。")
        return {"revision": revision, "image": info["Image"], "tunnel": "tunnel" in config["services"]}

    def prepare(self, release):
        image = IMAGE + "@" + release["digest"]
        self.command(["pull", image], timeout=900)
        config = json.loads(self.command(["image", "inspect", image]))[0]["Config"]
        env = dict(item.split("=", 1) for item in config["Env"] if "=" in item)
        if env.get("AUTOSIGN_BUILD_REVISION") != release["revision"] or env.get("AUTOSIGN_UPDATE_PROTOCOL") != "1":
            raise UpdateError("镜像版本与更新信息不一致，已停止更新。")
        return image

    def preflight(self, image):
        self.compose("run", "--rm", "--no-deps", "--pull", "never", "-T", "app", "python", "-c",
                     "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, chromium_sandbox=True); b.close(); p.stop()",
                     image=image, timeout=90)

    def stop(self, tunnel):
        if tunnel:
            self.compose("stop", "--timeout", "10", "tunnel")
        self.compose("stop", "--timeout", "110", "app", timeout=130)

    def backup(self, job):
        directory = self.data / "backups" / "web-updates" / job
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        source = self.data / "autosign.db"
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(directory / "autosign.db") as dest:
                src.backup(dest)
        shutil.copyfile(self.root / ".env", directory / "config.env")
        for path in directory.iterdir():
            os.chmod(path, 0o600)
        return directory

    def restore(self, job):
        backup = self.data / "backups" / "web-updates" / job / "autosign.db"
        temporary = self.data / "update-restore.tmp"
        shutil.copyfile(backup, temporary)
        os.chmod(temporary, 0o600)
        os.chown(temporary, 10001, 10001)
        for suffix in ("-wal", "-shm"):
            (self.data / ("autosign.db" + suffix)).unlink(missing_ok=True)
        os.replace(temporary, self.data / "autosign.db")

    def start(self, image, tunnel):
        self.compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "90", "app", image=image, timeout=130)
        if tunnel:
            self.compose("up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never", "tunnel", image=image)
            deadline = time.monotonic() + 60
            while True:
                try:
                    status = json.loads(self.compose("exec", "-T", "tunnel", "tailscale", "status", "--json", timeout=10))
                    if status.get("BackendState") == "Running":
                        break
                except (UpdateError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise UpdateError("免域名 HTTPS 服务未恢复，请检查 Tailscale 连接和授权。")
                time.sleep(1)
            # Re-apply the persisted Funnel configuration after the network change.
            self.compose("exec", "-T", "tunnel", "tailscale", "funnel", "--bg", "--yes", "http://127.0.0.1:8080", timeout=60)
            self.compose("exec", "-T", "app", "python", "deployment.py", "verify-tunnel", timeout=140)


class Manager:
    def __init__(self, deployment, directory, fetch=latest_release):
        self.deployment = deployment
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fetch = fetch
        self.lock = threading.RLock()
        self.release = None
        self.current = None
        self.checked_at = 0
        self.state = {"phase": "idle", "message": "等待检查新版本。"}
        try:
            self.state = json.loads((self.directory / "state.json").read_text())
        except (OSError, ValueError):
            pass

    def save(self, **fields):
        with self.lock:
            self.state.update(fields)
            temporary = self.directory / "state.tmp"
            temporary.write_text(json.dumps(self.state))
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.directory / "state.json")
            if "phase" in fields:
                print("Update phase: " + fields["phase"], flush=True)

    def status(self):
        # Health/status must remain responsive during registry checks and reboots.
        state, release, current = dict(self.state), self.release, self.current
        busy = state["phase"] in ACTIVE
        available = bool(release and current and release["revision"] != current["revision"])
        return {"supported": current is not None, "available": available,
                    "busy": busy, "phase": state["phase"], "message": state["message"],
                    "current_revision": current["revision"] if current else "",
                    "latest_revision": release["revision"] if release else "",
                    "checked_at": self.checked_at,
                    "can_update": available and not busy and release["protocol"] == "1" and state["phase"] != "needs_recovery",
                    "backup": state.get("backup", "")}

    def check(self):
        with self.lock:
            if self.state["phase"] in ACTIVE or self.state["phase"] == "needs_recovery":
                return self.status()
            try:
                self.current = self.deployment.current()
                self.release = self.fetch()
                self.checked_at = int(time.time())
                message = "发现新版本，点击更新即可安装。" if self.release["revision"] != self.current["revision"] else "当前已是最新版本。"
                if self.release["protocol"] != "1":
                    message = "新版本包含部署方式变化，请按下方指引在终端升级一次。"
                self.save(message=message)
            except UpdateError as exc:
                self.release = None
                self.save(message=str(exc))
            except Exception:
                self.release = None
                self.save(message="检查更新未完成，请稍后重试。")
            return self.status()

    def begin(self, revision):
        with self.lock:
            if not isinstance(revision, str) or not REVISION.fullmatch(revision):
                raise UpdateError("更新版本无效，请刷新页面后重试。")
            self.check()
            if not self.status()["can_update"] or self.release["revision"] != revision:
                raise UpdateError("版本信息已变化或已有更新进行中，请刷新页面后重试。")
            # Persist recovery information before any container can be stopped.
            self.save(phase="downloading", message="正在下载新版本，当前助手继续运行。",
                      job=uuid.uuid4().hex, previous=self.current["image"], tunnel=self.current["tunnel"],
                      backup="", stopped=False, backed_up=False)
            threading.Thread(target=self.run, args=(dict(self.release),), daemon=True).start()
            return self.status()

    def rollback(self):
        self.save(phase="rolling_back", message="新版本未能启动，正在恢复旧版本和数据。")
        self.deployment.stop(self.state["tunnel"])
        if self.state["backed_up"]:
            self.deployment.restore(self.state["job"])
        self.deployment.start(self.state["previous"], self.state["tunnel"])
        self.current = self.deployment.current()
        self.save(phase="rolled_back", message="更新未完成，已恢复旧版本和数据库。可以稍后重试。")

    def run(self, release):
        marker = self.directory / "maintenance"
        try:
            image = self.deployment.prepare(release)
            self.save(phase="preflight", message="下载完成，正在检查浏览器运行环境。")
            self.deployment.preflight(image)
            marker.touch()
            self.save(phase="stopping", message="正在等待签到任务结束并停止服务。", stopped=True)
            self.deployment.stop(self.state["tunnel"])
            self.save(phase="backing_up", message="正在备份任务、密码和账号会话。")
            self.deployment.backup(self.state["job"])
            self.save(backed_up=True, backup="backups/web-updates/" + self.state["job"])
            self.save(phase="starting", message="正在启动新版本并检查健康状态，网页稍后恢复。")
            self.deployment.start(image, self.state["tunnel"])
            self.current = self.deployment.current()
            if self.current["revision"] != release["revision"]:
                raise UpdateError("运行版本与目标版本不一致。")
            self.save(phase="success", message="更新完成。浏览器扩展有变化时，请重新下载并更新扩展。")
        except Exception:
            if self.state.get("stopped"):
                try:
                    self.rollback()
                except Exception:
                    self.save(phase="needs_recovery", message="自动恢复未完成，计划任务已暂停。请在终端检查 Docker，并按更新指引恢复。")
            else:
                self.save(phase="failed", message="下载或运行环境检查失败，原版本继续运行。请检查网络和磁盘后重试。")
        finally:
            if self.state["phase"] != "needs_recovery":
                marker.unlink(missing_ok=True)

    def recover(self):
        if self.state["phase"] in ACTIVE:
            if self.state.get("stopped"):
                try:
                    self.rollback()
                except Exception:
                    self.save(phase="needs_recovery", message="更新中断且自动恢复未完成，请在终端检查部署状态。")
            else:
                self.save(phase="failed", message="上次更新在下载期间中断，原服务未更改，可以重新更新。")
        if self.state["phase"] != "needs_recovery":
            (self.directory / "maintenance").unlink(missing_ok=True)

    def retry_recovery(self):
        with self.lock:
            if self.state["phase"] != "needs_recovery":
                raise UpdateError("当前没有等待恢复的更新。")
            self.save(phase="rolling_back", message="正在重新尝试恢复旧版本和数据库。")
            threading.Thread(target=self.recover, daemon=True).start()
            return self.status()


def main():
    if sys.argv[1:] == ["--recover"]:
        from .update_client import call
        result = call("recover")
        print(result.get("message") or result.get("error") or "恢复请求已提交。")
        return
    directory = Path(os.environ.get("AUTOSIGN_UPDATE_DIR", CONTROL_DIR))
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    service_lock = open(directory / "service.lock", "a")
    fcntl.flock(service_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manager = Manager(Deployment(os.environ["AUTOSIGN_DEPLOY_DIR"], "/data"), directory)
    manager.recover()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(30)
            try:
                line = self.rfile.readline(1025)
                if len(line) > 1024:
                    raise ValueError()
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError()
                action = request.get("action")
                if set(request) - {"action", "revision"}:
                    raise ValueError()
                if action == "status":
                    result = manager.status()
                elif action == "check":
                    result = manager.check()
                elif action == "apply":
                    result = manager.begin(request.get("revision"))
                elif action == "recover":
                    result = manager.retry_recovery()
                else:
                    raise ValueError()
            except (ValueError, UpdateError, OSError):
                result = {"error": "操作未执行，请刷新页面并检查更新服务。"}
            self.wfile.write(json.dumps(result).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    path = directory / "control.sock"
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        os.chown(path, 10001, 10001)
        os.chmod(path, 0o600)

        def periodic():
            while True:
                manager.check()
                time.sleep(3600 if manager.current else 60)

        threading.Thread(target=periodic, daemon=True).start()
        server.serve_forever()


if __name__ == "__main__":
    main()
