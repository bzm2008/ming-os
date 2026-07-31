#!/usr/bin/env python3
"""Install AppImage files into the current user's application directory."""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import stat
import sys


MAX_APPIMAGE_BYTES = 8 * 1024 * 1024 * 1024
APPIMAGE_HEADER_SIZE = 20
APPIMAGE_MAGIC_OFFSET = 8
APPIMAGE_MAGIC = b"AI\x02"


class AppImageInstaller:
    def __init__(self, home=None, uid_getter=None):
        self.home = pathlib.Path(home or pathlib.Path.home())
        self.uid_getter = uid_getter or getattr(os, "geteuid", lambda: 1)

    @staticmethod
    def _result(ok, **values):
        result = {
            "ok": bool(ok), "action": "install", "state": "", "error": "",
            "source": "", "installed_file": "", "desktop_file": "",
            "fuse_ready": False, "run_mode": "",
        }
        result.update(values)
        return result

    @staticmethod
    def _safe_source(source):
        path = pathlib.Path(source).expanduser()
        try:
            resolved = path.resolve(strict=True)
            details = resolved.stat()
        except (OSError, RuntimeError):
            return None, "找不到 AppImage 文件。"
        if path.is_symlink() or not stat.S_ISREG(details.st_mode):
            return None, "只能安装普通 AppImage 文件。"
        if resolved.suffix.lower() != ".appimage":
            return None, "只能安装 .AppImage 文件。"
        if not 0 < details.st_size <= MAX_APPIMAGE_BYTES:
            return None, "AppImage 文件大小无效。"
        try:
            # Read only the fixed ELF/AppImage header.  AppImages can be several
            # gigabytes; reading the whole file just to validate its type can
            # exhaust memory before the copy even starts.
            with resolved.open("rb") as handle:
                header = handle.read(APPIMAGE_HEADER_SIZE)
        except OSError:
            return None, "无法读取 AppImage 文件。"
        if (
            len(header) < APPIMAGE_HEADER_SIZE
            or header[:4] != b"\x7fELF"
            or header[4] != 2
            or header[18:20] != b"\x3e\x00"
            or header[APPIMAGE_MAGIC_OFFSET:APPIMAGE_MAGIC_OFFSET + len(APPIMAGE_MAGIC)]
            != APPIMAGE_MAGIC
        ):
            return None, "该 AppImage 不是受支持的 amd64 可执行文件。"
        return resolved, ""

    @staticmethod
    def _digest(path):
        checksum = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
        return checksum.hexdigest()

    @staticmethod
    def _desktop_escape(value):
        return str(value).replace("\\", "\\\\").replace(" ", "\\ ")

    @staticmethod
    def fuse_ready():
        fuse = pathlib.Path("/dev/fuse")
        return fuse.exists() and os.access(fuse, os.R_OK | os.W_OK)

    @classmethod
    def run_mode(cls):
        """Return the launcher mode that works in this runtime environment."""
        return "direct" if cls.fuse_ready() else "extract-and-run"

    def install(self, source):
        if self.uid_getter() == 0:
            return self._result(False, state="permission_denied", error="AppImage 必须由当前用户安装，不能用管理员权限运行。")
        resolved, error = self._safe_source(source)
        if error:
            return self._result(False, state="validation_failed", error=error)
        target_dir = self.home / ".local" / "opt" / "appimages"
        desktop_dir = self.home / ".local" / "share" / "applications"
        digest = self._digest(resolved)
        stem = "".join(char if char.isalnum() or char in "._-" else "-" for char in resolved.stem).strip(".-") or "appimage"
        target = target_dir / (stem + "-" + digest[:12] + ".AppImage")
        desktop = desktop_dir / ("ming-appimage-" + digest[:12] + ".desktop")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            desktop_dir.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_name(target.name + ".tmp")
                shutil.copyfile(resolved, temporary)
                os.chmod(temporary, 0o700)
                os.replace(temporary, target)
            os.chmod(target, 0o700)
            desktop.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=%s\n"
                "Comment=User-installed AppImage\n"
                "Exec=/usr/local/bin/ming-appimage-run %s\n"
                "Icon=application-x-executable\n"
                "Terminal=false\n"
                "Categories=Utility;\n"
                "StartupNotify=true\n" % (stem, self._desktop_escape(target)),
                encoding="utf-8",
            )
            # Desktop catalogs and launch brokers need to read this entry after
            # installation; keep it user-owned but readable to the session.
            os.chmod(desktop, 0o644)
        except OSError as exception:
            return self._result(False, state="install_failed", source=str(resolved), error="无法安装 AppImage：%s" % exception)
        return self._result(
            True, state="installed", source=str(resolved), installed_file=str(target),
            desktop_file=str(desktop), fuse_ready=self.fuse_ready(),
            run_mode=self.run_mode(),
        )


def main(argv=None, installer=None, stdout=None):
    parser = argparse.ArgumentParser(prog="ming-appimage-installer")
    parser.add_argument("file")
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    result = (installer or AppImageInstaller()).install(args.file)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout or sys.stdout)
    return 0 if result["ok"] else 3 if result["state"] == "permission_denied" else 2


if __name__ == "__main__":
    raise SystemExit(main())
