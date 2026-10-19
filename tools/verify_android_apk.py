"""Inspect the built APK and prove embedded assets match the current source.

Run with CPython 3.13, matching Chaquopy bytecode.
"""
from __future__ import annotations

import hashlib
import argparse
import io
import json
import marshal
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "android/app/src/main/python"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="4.0.1")
    parser.add_argument("--version-code", type=int, default=58)
    args = parser.parse_args()
    REPORT = ROOT / f"reports/android-{args.version}"
    APK = ROOT / f"dist/NovelGenerator-v{args.version}-arm64-debug.apk"
    assert sys.version_info[:2] == (3, 13)
    python_files, web_files = {}, {}
    with zipfile.ZipFile(APK) as package:
        assert package.testzip() is None
        with zipfile.ZipFile(io.BytesIO(package.read("assets/chaquopy/app.imy"))) as app:
            names = app.namelist()
            assert not any(name.startswith("core/interactive/") or name == "api/routers/interactive.pyc" for name in names)
            assert not any(name.startswith(("novels/", "_roleplay/", "_settings/")) or name.endswith(".env") for name in names)
            for source in SOURCE.rglob("*.py"):
                if "__pycache__" in source.parts:
                    continue
                relative = source.relative_to(SOURCE).as_posix()
                packaged = relative + "c"
                code = marshal.loads(app.read(packaged)[16:])
                current = compile(source.read_bytes(), code.co_filename, "exec", dont_inherit=True, optimize=0)
                assert code == current, f"Stale bytecode: {relative}"
                python_files[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
            for source in (ROOT / "web").rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(ROOT).as_posix()
                assert app.read(relative) == source.read_bytes(), f"Stale frontend: {relative}"
                web_files[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
            assert "SUPPORTED_EXPORT_FORMATS" in marshal.loads(app.read("config.pyc")[16:]).co_names

        library_paths = [name for name in package.namelist() if name.startswith("lib/") and name.endswith(".so")]
        assert library_paths and all(name.startswith("lib/arm64-v8a/") for name in library_paths)
        with zipfile.ZipFile(io.BytesIO(package.read("assets/chaquopy/requirements-common.imy"))) as requirements:
            assert any(name.startswith("socksio/") for name in requirements.namelist())

    new_signature = (REPORT / "signature-new.txt").read_text(encoding="utf-8-sig")
    old_signature = (REPORT / "signature-old.txt").read_text(encoding="utf-8-sig")
    pattern = r"certificate SHA-256 digest: ([0-9a-f]+)"
    certificate = re.search(pattern, new_signature).group(1)
    assert certificate == re.search(pattern, old_signature).group(1)
    assert "Verified using v2 scheme (APK Signature Scheme v2): true" in new_signature
    badging = (REPORT / "apk-badging.txt").read_text(encoding="utf-8-sig")
    assert f"name='com.novelgen.app' versionCode='{args.version_code}' versionName='{args.version}'" in badging
    assert "minSdkVersion:'26'" in badging and "native-code: 'arm64-v8a'" in badging
    layout_path = REPORT / "main-layout.txt"
    if layout_path.exists():
        layout = layout_path.read_text(encoding="utf-8-sig")
        assert not any(value in layout for value in ("Toolbar", "connection_status", "connection_dot", "app_name"))
    digest = hashlib.sha256(APK.read_bytes()).hexdigest()
    receipt = {"apk": APK.relative_to(ROOT).as_posix(), "bytes": APK.stat().st_size, "sha256": digest,
               "version": args.version, "version_code": args.version_code, "application_id": "com.novelgen.app",
               "min_sdk": 26, "target_sdk": 35, "abis": ["arm64-v8a"], "signing": "Android Debug / APK v2",
               "signer_sha256": certificate, "same_signer_as_local_2_5_68": True,
               "embedded_python_files_verified": len(python_files), "embedded_frontend_files_verified": len(web_files),
               "legacy_interactive_present": False, "user_data_or_credentials_packaged": False,
               "android_exports": ["txt"], "deferred_exports": ["pdf", "epub"],
               "native_titlebar_removed": layout_path.exists(),
               "android_device": "NOT_RUN", "real_provider": "NOT_RUN",
               "python_source_sha256": python_files, "frontend_sha256": web_files}
    (REPORT / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    checksum = APK.with_suffix(".apk.sha256")
    checksum.write_text(f"{digest}  {APK.name}\n", encoding="ascii")
    print(json.dumps({key: value for key, value in receipt.items() if not key.endswith("_sha256") or isinstance(value, str)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
