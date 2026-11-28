"""Keep the downloadable extension reproducible and in sync with its source."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import package_extension


class ExtensionBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "browser-extension"
        shutil.copytree(package_extension.ROOT / "browser-extension", self.source)

    def test_bundle_is_reproducible_and_excludes_development_files(self):
        first = package_extension.build_bundle(self.source)
        for path in self.source.iterdir():
            os.utime(path, (1700000000, 1700000000))
        (self.source / "local-session.json").write_text("private fixture")
        self.assertEqual(first, package_extension.build_bundle(self.source))
        with zipfile.ZipFile(BytesIO(first)) as archive:
            self.assertEqual(set(archive.namelist()), set(package_extension.FILES))
            for name in archive.namelist():
                self.assertEqual(archive.read(name), (self.source / name).read_bytes())

    def test_check_rejects_stale_bundle_without_overwriting_it(self):
        target = self.root / "signin-session-exporter.zip"
        target.write_bytes(package_extension.build_bundle(self.source))
        previous = target.read_bytes()
        with (self.source / "README.md").open("a") as document:
            document.write("\nUpdated fixture instructions.\n")
        with patch.object(package_extension, "ROOT", self.root):
            with patch("sys.argv", ["package_extension.py", "--check"]), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    package_extension.main()
            self.assertEqual(error.exception.code, 1)
            self.assertEqual(target.read_bytes(), previous)
            with patch("sys.argv", ["package_extension.py"]), redirect_stdout(StringIO()):
                package_extension.main()
            self.assertNotEqual(target.read_bytes(), previous)
            with patch("sys.argv", ["package_extension.py", "--check"]), redirect_stdout(StringIO()):
                package_extension.main()

    def test_mismatched_popup_version_cannot_be_published(self):
        (self.source / "manifest.json").write_text('{"version": "999.0.0"}')
        with self.assertRaisesRegex(ValueError, "版本不一致"):
            package_extension.build_bundle(self.source)
