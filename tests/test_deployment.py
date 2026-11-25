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
elif args == ["info"]:
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "info" else 0)
elif "down" in args:
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "down" else 0)
elif "pull" in args:
    sys.exit(1 if os.environ.get("FAIL_PHASE") == "pull" else 0)
elif "init_config.py" in args:
    if os.environ.get("FAIL_PHASE") == "init": sys.exit(1)
    print("AUTOSIGN_ADMIN_PASSWORD=test-config-only")
elif "deployment.py" in args and "configure" in args:
    import runpy
    script = os.path.join(os.environ["SOURCE"], "deployment.py")
    sys.argv = [script] + args[args.index("deployment.py") + 1:]
    runpy.run_path(script, run_name="__main__")
elif "deployment.py" in args and "summary" in args:
    print("网页链接：http://localhost:8080/login\n初始登录密码：test-config-only")
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
        for name in ("setup.sh", "install.sh", "uninstall.sh", "compose.yaml", "compose.build.yaml", "Dockerfile"):
            shutil.copy2(ROOT / name, self.project / name)
        docker = self.bin / "docker"
        docker.write_text("#!" + sys.executable + "\n" + DOCKER)
        docker.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "CALL_LOG": str(self.log), "COMPOSE_VERSION": "2.24.0", "FAIL_PHASE": "", "SOURCE": str(ROOT),
        }

    def run_setup(self, *args, **env):
        return subprocess.run(
            ["sh", str(self.project / "setup.sh"), *args], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True, timeout=15,
        )

    def run_interactive(self, command, answers, env=None):
        # A real terminal on stdin exercises the same -t check as an SSH shell.
        master, slave = os.openpty()
        process = None
        try:
            process = subprocess.Popen(
                command, cwd=self.root, env=env or self.env,
                stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            os.write(master, answers.encode())
            stdout, stderr = process.communicate(timeout=15)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
            os.close(master)
            os.close(slave)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_default_pulls_and_keeps_generated_config_private(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("pull" in call for call in calls))
        self.assertFalse(any("build" in call for call in calls))
        self.assertEqual((self.project / ".env").stat().st_mode & 0o777, 0o600)
        self.assertIn("初始登录密码：test-config-only", result.stdout)
        for call in calls:
            if "run" in call or "up" in call:
                if "up" in call:
                    self.assertIn("--no-build", call)
                else:
                    self.assertNotIn("--no-build", call)
                    self.assertNotIn("--build", call)
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

    def test_ip_choice_writes_only_access_config_and_prints_completion(self):
        result = self.run_setup("--no-domain", "203.0.113.8")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.project / ".env").read_text()
        self.assertIn("BIND_ADDRESS=0.0.0.0", config)
        self.assertIn("AUTOSIGN_PUBLIC_HOST=203.0.113.8", config)
        self.assertIn("网页链接", result.stdout)

    def test_domain_choice_enables_proxy_and_verifies_https(self):
        result = self.run_setup("--domain", "sign.example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.project / ".env").read_text()
        self.assertIn("AUTOSIGN_COOKIE_SECURE=true", config)
        self.assertIn("COMPOSE_PROFILES=web", config)
        self.assertTrue(any("pull" in c and "web" in c for c in self.calls()))
        self.assertTrue(any("verify-domain" in c for c in self.calls()))

    def test_invalid_host_does_not_overwrite_existing_secrets_or_start(self):
        source = "AUTOSIGN_SECRET_KEY=preserve-this-key\nAUTOSIGN_ADMIN_PASSWORD=preserve-this-password\n"
        (self.project / ".env").write_text(source)
        result = self.run_setup("--domain", "https://example.com/path")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.project / ".env").read_text(), source)
        self.assertFalse(any("up" in c for c in self.calls()))

    def test_conflicting_access_arguments_are_rejected(self):
        result = self.run_setup("--domain", "example.com", "--no-domain", "203.0.113.8")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls())

    def test_existing_proxy_mode_does_not_pull_or_start_caddy(self):
        result = self.run_setup("--domain", "sign.example.com", "--external-proxy")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.project / ".env").read_text()
        self.assertIn("AUTOSIGN_EXTERNAL_PROXY=true", config)
        self.assertNotIn("COMPOSE_PROFILES=web", config)
        self.assertFalse(any("pull" in call and "web" in call for call in self.calls()))
        self.assertTrue(any("verify-domain" in call for call in self.calls()))

    def test_fresh_terminal_install_offers_only_domain_and_ip(self):
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh")], "2\n203.0.113.8\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) 域名 HTTPS", result.stdout)
        self.assertIn("2) 无域名 IP＋端口", result.stdout)
        self.assertNotIn("3)", result.stdout)
        self.assertNotIn("默认 3", result.stdout)
        self.assertIn("AUTOSIGN_ACCESS_MODE=ip", (self.project / ".env").read_text())

    def test_existing_terminal_install_still_prompts_and_preserves_secrets_and_port(self):
        config = self.project / ".env"
        config.write_text(
            "AUTOSIGN_SECRET_KEY=preserve-key\nAUTOSIGN_ADMIN_PASSWORD=preserve-password\n"
            "AUTOSIGN_ACCESS_MODE=private\nBIND_ADDRESS=127.0.0.1\nPORT=18080\n"
        )
        data = self.project / "data"
        data.mkdir()
        database = data / "autosign.db"
        database.write_bytes(b"existing database sentinel")
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh")], "2\n203.0.113.8\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("请选择访问方式", result.stdout)
        updated = config.read_text()
        for line in ["AUTOSIGN_SECRET_KEY=preserve-key", "AUTOSIGN_ADMIN_PASSWORD=preserve-password",
                     "PORT=18080", "AUTOSIGN_ACCESS_MODE=ip", "BIND_ADDRESS=0.0.0.0"]:
            self.assertIn(line, updated)
        self.assertEqual(database.read_bytes(), b"existing database sentinel")
        self.assertFalse(any("init_config.py" in call or "down" in call for call in self.calls()))

    def test_existing_terminal_install_can_select_domain(self):
        (self.project / ".env").write_text("AUTOSIGN_ADMIN_PASSWORD=keep-password\n")
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh")], "1\nsign.example.com\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.project / ".env").read_text()
        self.assertIn("AUTOSIGN_DOMAIN=sign.example.com", config)
        self.assertIn("AUTOSIGN_ADMIN_PASSWORD=keep-password", config)
        self.assertTrue(any("verify-domain" in call for call in self.calls()))

    def test_terminal_menu_has_no_implicit_private_default_or_third_option(self):
        config = self.project / ".env"
        source = "AUTOSIGN_SECRET_KEY=preserve-key\n"
        config.write_text(source)
        for answer in ["\n", "3\n"]:
            with self.subTest(answer=answer):
                result = self.run_interactive(["sh", str(self.project / "setup.sh")], answer)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("已取消部署", result.stderr)
                self.assertEqual(config.read_text(), source)
                self.assertFalse(self.calls())

    def test_explicit_private_mode_remains_available_without_a_menu(self):
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh"), "--private"], "",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("请选择访问方式", result.stdout)
        config = (self.project / ".env").read_text()
        self.assertIn("AUTOSIGN_ACCESS_MODE=private", config)
        self.assertIn("BIND_ADDRESS=127.0.0.1", config)

    def test_explicit_ip_mode_skips_terminal_menu(self):
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh"), "--no-domain", "203.0.113.8"], "",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("请选择访问方式", result.stdout)
        self.assertIn("AUTOSIGN_PUBLIC_HOST=203.0.113.8", (self.project / ".env").read_text())

    def test_rerunning_quick_installer_with_existing_env_inherits_terminal_menu(self):
        target = self.root / "existing install"
        target.mkdir()
        (target / ".env").write_text("AUTOSIGN_SECRET_KEY=preserve-key\nAUTOSIGN_ACCESS_MODE=private\n")
        result = self.run_interactive(
            ["sh", str(ROOT / "install.sh"), str(target)],
            "2\n203.0.113.8\n", env=self.install_curl_stub(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("请选择访问方式", result.stdout)
        config = (target / ".env").read_text()
        self.assertIn("AUTOSIGN_SECRET_KEY=preserve-key", config)
        self.assertIn("AUTOSIGN_ACCESS_MODE=ip", config)
        self.assertFalse(list(target.glob(".autosign-install.*")))

    def test_uninstall_defaults_to_preserving_data_and_never_deploys(self):
        config = self.project / ".env"
        source = "AUTOSIGN_SECRET_KEY=preserve-key\nAUTOSIGN_ADMIN_PASSWORD=preserve-password\nCOMPOSE_PROFILES=web\n"
        config.write_text(source)
        data = self.project / "data"
        data.mkdir()
        database = data / "autosign.db"
        database.write_bytes(b"preserve database")
        for _ in range(2):
            result = self.run_setup("--uninstall")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), source)
            self.assertEqual(database.read_bytes(), b"preserve database")
            self.assertIn("卸载完成", result.stdout)
            self.assertNotIn("preserve-key", result.stdout + result.stderr)
            self.assertNotIn("preserve-password", result.stdout + result.stderr)
        removals = [call for call in self.calls() if "down" in call]
        self.assertEqual(len(removals), 2)
        self.assertTrue(all(call == ["compose", "-f", "compose.yaml", "--profile", "web", "down"] for call in removals))
        self.assertFalse(any(arg in call for call in self.calls() for arg in ["pull", "build", "up", "run", "--volumes", "--rmi", "prune", "--remove-orphans"]))

    def test_uninstall_entry_skips_terminal_access_selection(self):
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh"), "--uninstall"], "",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("请选择访问方式", result.stdout)
        self.assertFalse((self.project / ".env").exists())
        self.assertTrue(any("down" in call for call in self.calls()))

    def test_purge_refuses_noninteractive_deletion(self):
        config = self.project / ".env"
        config.write_text("AUTOSIGN_SECRET_KEY=preserve-key\n")
        result = self.run_setup("--uninstall", "--purge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("交互终端", result.stderr)
        self.assertTrue(config.exists())
        self.assertFalse(any("down" in call for call in self.calls()))

    def test_purge_incorrect_or_empty_confirmation_preserves_data(self):
        config = self.project / ".env"
        source = "AUTOSIGN_SECRET_KEY=preserve-key\n"
        config.write_text(source)
        for answer in ["\n", "delete\n", "yes\n"]:
            with self.subTest(answer=answer):
                result = self.run_interactive(
                    ["sh", str(self.project / "setup.sh"), "--uninstall", "--purge"], answer,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("已取消卸载", result.stderr)
                self.assertEqual(config.read_text(), source)
                self.assertFalse(any("down" in call for call in self.calls()))

    def test_confirmed_purge_removes_config_but_retains_sources_and_backups(self):
        config = self.project / ".env"
        config.write_text("AUTOSIGN_SECRET_KEY=old-key\n")
        backups = self.project / "backups"
        backups.mkdir()
        backup = backups / "autosign.env"
        backup.write_text("backup-key\n")
        result = self.run_interactive(
            ["sh", str(self.project / "setup.sh"), "--uninstall", "--purge"], "DELETE\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(config.exists())
        self.assertEqual(backup.read_text(), "backup-key\n")
        self.assertTrue((self.project / "compose.yaml").exists())
        self.assertTrue((self.project / "uninstall.sh").exists())
        self.assertIn("彻底卸载完成", result.stdout)
        self.assertEqual([call for call in self.calls() if "down" in call], [
            ["compose", "-f", "compose.yaml", "--profile", "web", "down", "--volumes"],
        ])

    def test_failed_compose_purge_keeps_decryption_key(self):
        config = self.project / ".env"
        source = "AUTOSIGN_SECRET_KEY=preserve-key\n"
        config.write_text(source)
        result = self.run_interactive(
            ["sh", str(self.project / "uninstall.sh"), "--purge"], "DELETE\n",
            env={**self.env, "FAIL_PHASE": "down"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(config.read_text(), source)
        self.assertIn("已保留 .env", result.stderr)
        self.assertNotIn("彻底卸载完成", result.stdout)

    def test_uninstall_preflight_failures_keep_config(self):
        config = self.project / ".env"
        config.write_text("AUTOSIGN_SECRET_KEY=preserve-key\n")
        for env in [{"FAIL_PHASE": "info"}, {"COMPOSE_VERSION": "2.23.3"}]:
            with self.subTest(env=env):
                result = self.run_setup("--uninstall", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(config.exists())
                self.assertFalse(any("down" in call for call in self.calls()))

    def test_uninstall_flags_cannot_mix_with_deployment_configuration(self):
        for args in [("--purge",), ("--uninstall", "--private"),
                     ("--loaded", "--uninstall"), ("--uninstall", "--domain", "sign.example.com")]:
            with self.subTest(args=args):
                result = self.run_setup(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls())

    def test_standalone_uninstaller_validates_arguments_and_deployment_directory(self):
        result = subprocess.run(
            ["sh", str(self.project / "uninstall.sh"), "--yes"],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls())
        (self.project / "compose.yaml").unlink()
        result = self.run_setup("--uninstall")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未找到 compose.yaml", result.stderr)
        self.assertFalse(self.calls())

    def test_quick_installer_can_install_uninstaller_without_starting_service(self):
        target = self.root / "existing install"
        target.mkdir()
        config = target / ".env"
        config.write_text("AUTOSIGN_SECRET_KEY=preserve-key\n")
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), str(target), "--uninstall"], cwd=self.root,
            env=self.install_curl_stub(), capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config.read_text(), "AUTOSIGN_SECRET_KEY=preserve-key\n")
        self.assertTrue((target / "uninstall.sh").is_file())
        self.assertTrue(any("down" in call for call in self.calls()))
        self.assertFalse(any(arg in call for call in self.calls() for arg in ["pull", "build", "run", "up"]))
