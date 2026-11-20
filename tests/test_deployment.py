"""Exercise deployment failure paths without a Docker daemon or network."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args == ["compose", "version", "--short"]:
    print(os.environ.get("COMPOSE_VERSION", "2.24.0"))
elif "pull" in args:
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "pull" else 0)
elif "init_config.py" in args:
    if os.environ.get("FAIL_PHASE") == "init": sys.exit(1)
    print("AUTOSIGN_ADMIN_PASSWORD=test-config-only")
elif "run" in args and "chromium.launch" in " ".join(args):
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "sandbox" else 0)
elif "up" in args:
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "health" else 0)
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        for name in ("setup.sh", "install.sh", "compose.yaml", "compose.build.yaml", "Dockerfile"):
            shutil.copy2(ROOT / name, self.project / name)
        docker = self.bin / "docker"
        docker.write_text("#!" + sys.executable + "\n" + DOCKER)
        docker.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CALL_LOG": str(self.log), "COMPOSE_VERSION": "2.24.0", "FAIL_PHASE": "",
        }

    def run_setup(self, *args, **env):
        return subprocess.run(
            ["sh", str(self.project / "setup.sh"), *args], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True, timeout=15,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_default_pulls_and_keeps_generated_config_private(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("pull" in call for call in calls))
        self.assertFalse(any("build" in call for call in calls))
        self.assertEqual((self.project / ".env").stat().st_mode & 0o777, 0o600)
        self.assertNotIn("test-config-only", result.stdout)
        for call in calls:
            if "run" in call or "up" in call:
                self.assertIn("--no-build", call)
                self.assertEqual(call[call.index("--pull") + 1], "never")

    def test_pull_failure_does_not_build_write_config_or_start(self):
        result = self.run_setup(FAIL_PHASE="pull")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".env").exists())
        self.assertFalse(any(any(arg in call for arg in ("build", "run", "up")) for call in self.calls()))

    def test_update_preserves_config_and_data(self):
        config = self.project / ".env"
        original = b"AUTOSIGN_SECRET_KEY=preserve-this-existing-key\n"
        config.write_bytes(original)
        data = self.project / "data"
        data.mkdir()
        database = data / "autosign.db"
        database.write_bytes(b"existing database sentinel")
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual(database.read_bytes(), b"existing database sentinel")
        self.assertFalse(any("init_config.py" in call or "down" in call for call in self.calls()))

    def test_local_build_is_explicit(self):
        result = self.run_setup("--local")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("build" in call for call in calls))
        self.assertFalse(any("pull" in call for call in calls))
        self.assertTrue(all("compose.build.yaml" in call for call in calls if "-f" in call))

    def test_loaded_mode_never_fetches_or_builds(self):
        result = self.run_setup("--loaded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("pull" in call or "build" in call for call in self.calls()))

    def test_failed_initialization_removes_partial_config(self):
        result = self.run_setup(FAIL_PHASE="init")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".env").exists())
        self.assertFalse(list(self.project.glob(".env.tmp.*")))
        self.assertFalse(any("up" in call for call in self.calls()))

    def test_failed_sandbox_stops_before_service_start(self):
        result = self.run_setup(FAIL_PHASE="sandbox")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chromium", result.stderr)
        self.assertTrue((self.project / ".env").exists())
        self.assertFalse(any("up" in call for call in self.calls()))

    def test_old_compose_rejected_before_mutation(self):
        result = self.run_setup(COMPOSE_VERSION="v2.23.3")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.project / ".env").exists())
        self.assertEqual(len(self.calls()), 1)

    def install_curl_stub(self, fail_file=""):
        curl = self.bin / "curl"
        curl.write_text(
            "#!" + sys.executable + "\n"
            "import os, pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "url = next(a for a in args if a.startswith('https://'))\n"
            "name = url.rsplit('/', 1)[1]\n"
            "if name == os.environ.get('FAIL_FILE'): sys.exit(22)\n"
            "shutil.copyfile(pathlib.Path(os.environ['SOURCE']) / name, args[args.index('-o') + 1])\n"
        )
        curl.chmod(0o755)
        return {**self.env, "SOURCE": str(ROOT), "FAIL_FILE": fail_file}

    def test_installer_preserves_config_and_uses_image(self):
        target = self.root / "new install"
        target.mkdir()
        config = target / ".env"
        config.write_text("existing-secret-key-and-password\n")
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), str(target)], cwd=self.root,
            env=self.install_curl_stub(), capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), "existing-secret-key-and-password\n")
        self.assertTrue(any("pull" in call for call in self.calls()))
        self.assertFalse(any("build" in call for call in self.calls()))
        self.assertFalse(list(target.glob(".autosign-install.*")))

    def test_partial_download_does_not_replace_deployment(self):
        target = self.root / "existing install"
        target.mkdir()
        compose = target / "compose.yaml"
        compose.write_text("keep existing deployment\n")
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), str(target)], cwd=self.root,
            env=self.install_curl_stub("setup.sh"), capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(compose.read_text(), "keep existing deployment\n")
        self.assertFalse(self.calls())
        self.assertFalse(list(target.glob(".autosign-install.*")))
