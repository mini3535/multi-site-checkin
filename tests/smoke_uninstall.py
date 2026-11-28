"""Exercise removal against real Docker on a fresh GitHub Actions runner only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
APP = "ghcr.io/mini3535/multi-site-checkin:uninstall-ci-app"
OLD = "ghcr.io/mini3535/multi-site-checkin:uninstall-ci-old"
PROXY = "autosign-uninstall-ci:proxy"
SHARED = "autosign-uninstall-ci-shared"


def docker(*args, check=True, source=None):
    result = subprocess.run(["docker", *args], input=source, text=True,
                            capture_output=True, timeout=60)
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(args)} failed: {result.stderr}")
    return result


def purge(directory):
    master, slave = os.openpty()
    try:
        process = subprocess.Popen(["sh", str(directory / "uninstall.sh"), "--purge"],
                                   stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            os.write(master, b"DELETE\n")
            stdout, stderr = process.communicate(timeout=60)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        if process.returncode:
            raise RuntimeError(stdout + stderr)
        return stdout
    finally:
        os.close(master)
        os.close(slave)


def prepare(directory):
    directory.mkdir()
    for name in ("uninstall.sh", "setup.sh"):
        shutil.copy2(ROOT / name, directory / name)
    (directory / "compose.yaml").write_text(json.dumps({
        "name": "autosign",
        "services": {
            "app": {"image": APP, "volumes": ["data:/data", "tailscale_data:/var/lib/tailscale"]},
            "web": {"image": PROXY},
        },
        "volumes": {"data": {}, "tailscale_data": {}},
    }))
    (directory / ".env").write_text("AUTOSIGN_SECRET_KEY=ci-fixture-only\n")
    (directory / "backups").mkdir()
    (directory / "backups" / "fixture.txt").write_text("backup fixture")
    docker("compose", "-f", str(directory / "compose.yaml"), "create", "--pull", "never")


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Run only on a fresh GitHub Actions Docker runner.")
    # Never reuse a daemon that already has this project or these test fixtures.
    for kind, arguments in (("containers", ("ps", "-aq")),
                            ("volumes", ("volume", "ls", "-q")),
                            ("networks", ("network", "ls", "-q"))):
        if docker(*arguments, "--filter", "label=com.docker.compose.project=autosign").stdout.strip():
            raise SystemExit(f"Refusing to touch existing autosign {kind}.")
    if docker("image", "ls", "-q", "--filter", "reference=ghcr.io/mini3535/multi-site-checkin:*").stdout.strip():
        raise SystemExit("Refusing to touch existing application images.")
    for tag in (APP, OLD, PROXY):
        if docker("image", "inspect", tag, check=False).returncode == 0:
            raise SystemExit("Refusing to reuse existing test images.")
    with tempfile.TemporaryDirectory(prefix="autosign-uninstall-ci-") as temporary:
        directory = Path(temporary) / "installation with spaces"
        outside = Path(temporary) / "outside-backup.txt"
        outside.write_text("preserved")
        try:
            # FROM scratch needs no pull, network, shell, or executable: containers stay stopped.
            for tag, role in ((APP, "app"), (PROXY, "proxy")):
                docker("build", "-q", "-t", tag, "-",
                       source=f'FROM scratch\nLABEL fixture.role={role}\nCMD ["/unused-fixture"]\n')
            docker("tag", APP, OLD)
            prepare(directory)
            result = subprocess.run(["sh", str(directory / "uninstall.sh")],
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr
            assert (directory / ".env").exists()
            docker("volume", "inspect", "autosign_data")
            docker("volume", "inspect", "autosign_tailscale_data")
            docker("image", "inspect", APP)
            docker("compose", "-f", str(directory / "compose.yaml"), "create", "--pull", "never")
            docker("create", "--name", SHARED, PROXY)
            stdout = purge(directory)
            assert "保留共享镜像" in stdout, stdout
            assert not directory.exists()
            for tag in (APP, OLD):
                assert docker("image", "inspect", tag, check=False).returncode != 0
            docker("container", "inspect", SHARED)
            docker("image", "inspect", PROXY)
            assert docker("volume", "inspect", "autosign_data", check=False).returncode != 0
            assert docker("volume", "inspect", "autosign_tailscale_data", check=False).returncode != 0
            assert docker("network", "inspect", "autosign_default", check=False).returncode != 0
            docker("rm", SHARED)
            docker("tag", PROXY, APP)
            prepare(directory)
            stdout = purge(directory)
            assert "彻底卸载完成" in stdout, stdout
            assert not directory.exists()
            assert outside.read_text() == "preserved"
            for tag in (APP, PROXY):
                assert docker("image", "inspect", tag, check=False).returncode != 0
            print("PASS: normal uninstall preserves data; purge removes images, volumes, network and installation; shared image and outside files survive.")
        finally:
            if directory.exists():
                docker("compose", "-f", str(directory / "compose.yaml"), "down", "--volumes", check=False)
            docker("rm", "-f", SHARED, check=False)
            for tag in (APP, OLD, PROXY):
                docker("image", "rm", tag, check=False)


if __name__ == "__main__":
    main()
