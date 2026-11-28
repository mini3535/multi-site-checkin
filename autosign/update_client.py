"""The web process can request fixed update operations, never Docker commands."""
import json
import os
from pathlib import Path
import socket


CONTROL_DIR = "/run/autosign-update"


def maintenance(directory=None):
    return (Path(directory or os.environ.get("AUTOSIGN_UPDATE_DIR", CONTROL_DIR)) / "maintenance").exists()


def call(action, revision="", directory=None):
    directory = directory or os.environ.get("AUTOSIGN_UPDATE_DIR", CONTROL_DIR)
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(55)
            connection.connect(str(Path(directory) / "control.sock"))
            connection.sendall(json.dumps({"action": action, "revision": revision}).encode() + b"\n")
            with connection.makefile("rb") as stream:
                data = stream.readline(32769)
            if len(data) > 32768:
                raise ValueError()
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except (OSError, ValueError):
        return {"supported": False, "available": False, "busy": False,
                "phase": "unavailable", "message": "网页更新服务尚未就绪，请按下方指引完成一次终端升级。"}
