"""Real Docker upgrade and failed-migration rollback on isolated CI fixtures.

Registry lookup is replaced by local candidate images; container replacement,
health checks, Compose, database backup and rollback use production code.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
OLD = "0" * 40
BAD = "f" * 40


def inside(good_image, bad_image, revision):
    sys.path.insert(0, str(ROOT))
    from autosign.updater import Deployment, Manager

    class LocalCandidate(Deployment):
        candidate = good_image

        def prepare(self, release):
            # CI has not published these images. Use their immutable local IDs.
            info = json.loads(self.command(["image", "inspect", self.candidate]))[0]
            env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
            assert env["AUTOSIGN_BUILD_REVISION"] == release["revision"]
            return self.candidate

    deployment = LocalCandidate(os.environ["AUTOSIGN_DEPLOY_DIR"], "/data")
    assert deployment.current()["revision"] == OLD
    release = {"revision": revision, "protocol": "1", "digest": "sha256:" + "1" * 64}
    manager = Manager(deployment, "/run/autosign-update", fetch=lambda: dict(release))

    def transact(expected):
        manager.begin(release["revision"])
        deadline = time.monotonic() + 210
        while manager.status()["busy"] and time.monotonic() < deadline:
            time.sleep(0.2)
        assert manager.status()["phase"] == expected, manager.status()
        with sqlite3.connect("/data/autosign.db") as db:
            assert db.execute("SELECT value FROM update_fixture").fetchone()[0] == "preserved"
        assert not Path("/run/autosign-update/maintenance").exists()

    transact("success")
    assert deployment.current()["revision"] == revision
    release["revision"] = BAD
    deployment.candidate = bad_image
    transact("rolled_back")
    assert deployment.current()["revision"] == revision
    print("Real Docker update and failed-migration rollback passed.")


def outside():
    image = os.environ["AUTOSIGN_IMAGE"]
    revision = os.environ["GITHUB_SHA"]
    fixture_id = secrets.token_hex(4)
    baseline = "ghcr.io/mini3535/multi-site-checkin:latest"
    bad_tag = "autosign-bad-update:" + fixture_id
    env = {key: value for key, value in os.environ.items() if key not in ("AUTOSIGN_IMAGE", "COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME")}
    with tempfile.TemporaryDirectory(prefix="autosign-update-") as directory:
        root = Path(directory)
        env["PWD"] = str(root)

        def run(args, timeout=240):
            result = subprocess.run(args, cwd=root, env=env, capture_output=True, timeout=timeout)
            if result.returncode:
                # Only fixed Docker commands are run; never display Compose config.
                raise RuntimeError(result.stderr.decode()[-5000:])
            return result.stdout.decode().strip()

        def compose(*args):
            return run(["docker", "compose", "--project-directory", str(root), "-f", str(root / "compose.yaml"), *args])

        for name in ("compose.yaml", "seccomp_profile.json"):
            shutil.copyfile(ROOT / name, root / name)
        source_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
        baseline_file = root / "Baseline.Dockerfile"
        baseline_file.write_text("FROM " + image + "\nENV AUTOSIGN_BUILD_REVISION=" + OLD + "\n")
        run(["docker", "build", "-f", str(baseline_file), "-t", baseline, "."])
        bad_file = root / "Bad.Dockerfile"
        bad_command = "import sqlite3; c=sqlite3.connect('/data/autosign.db'); c.execute(\"UPDATE update_fixture SET value='bad-migration'\"); c.commit(); raise SystemExit(1)"
        bad_file.write_text("FROM " + image + "\nENV AUTOSIGN_BUILD_REVISION=" + BAD + "\nCMD " + json.dumps(["python", "-c", bad_command]) + "\n")
        run(["docker", "build", "-f", str(bad_file), "-t", bad_tag, "."])
        bad_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", bad_tag])
        config = run(["docker", "run", "--rm", image, "python", "init_config.py"])
        (root / ".env").write_text(config + "\nCOMPOSE_PROJECT_NAME=autosign-update-" + fixture_id + "\nPORT=18080\n")
        os.chmod(root / ".env", 0o600)
        try:
            compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "90")
            compose("exec", "-T", "app", "python", "-c", "import sqlite3; c=sqlite3.connect('/data/autosign.db'); c.execute('CREATE TABLE update_fixture(value TEXT)'); c.execute(\"INSERT INTO update_fixture VALUES ('preserved')\"); c.commit()")
            # The isolated helper replaces the normal companion during this test.
            compose("stop", "updater")
            output = compose("run", "--rm", "--no-deps", "--pull", "never", "-T", "updater", "python", "tools/smoke_update.py", "--inside", source_id, bad_id, revision)
            print(output)
        finally:
            compose("down", "--volumes", "--remove-orphans")
            run(["docker", "image", "rm", bad_tag, baseline])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--inside"]:
        inside(*sys.argv[2:])
    else:
        outside()
