"""Build the one supported browser extension bundle reproducibly."""
import argparse
from io import BytesIO
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("manifest.json", "popup.html", "popup.css", "popup.js", "PRIVACY.md", "README.md")


def build_bundle(source: Path) -> bytes:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if f"v{version}" not in (source / "popup.html").read_text(encoding="utf-8"):
        raise ValueError("扩展弹窗与 manifest.json 的版本不一致。")
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in FILES:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (source / name).read_bytes(), compresslevel=9)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description="打包或校验与当前源码一致的浏览器扩展。")
    parser.add_argument("--check", action="store_true", help="只检查，不修改 ZIP")
    args = parser.parse_args()
    bundle = ROOT / "signin-session-exporter.zip"
    try:
        expected = build_bundle(ROOT / "browser-extension")
        if args.check:
            if not bundle.is_file() or bundle.read_bytes() != expected:
                parser.exit(1, "扩展 ZIP 与源码不一致，请运行 python tools/package_extension.py 后提交更新。\n")
            print("扩展版本、源码与 ZIP 一致。")
        else:
            bundle.write_bytes(expected)
            print(f"已更新 {bundle.name}。")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
