#!/usr/bin/env python3
"""Safely inspect and install local Debian packages for Ming OS."""

import argparse
import configparser
import importlib.util
import json
import pathlib
import re
import stat
import subprocess
import os
import shlex
import shutil
import sys
from datetime import datetime


SUPPORTED_ARCHITECTURES = {"amd64", "all"}
PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+:~\-]+$")


def _common_paths(program_path=None, install_prefix=None):
    program = pathlib.Path(program_path or __file__)
    prefix = pathlib.Path(install_prefix or "/usr/local")
    candidates = (
        program.with_name("ming-shell-common.py"),
        prefix / "lib" / "ming-os" / "ming-shell-common.py",
        prefix / "bin" / "ming-shell-common.py",
    )
    return tuple(dict.fromkeys(candidates))


def _load_common(program_path=None, install_prefix=None):
    for path in _common_paths(program_path, install_prefix):
        try:
            path = path.resolve(strict=True)
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location(
                "ming_shell_common_for_package_installer", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except (AttributeError, ImportError, OSError, TypeError, ValueError):
            continue
    return None


COMMON = _load_common()


def _run(command, timeout=20):
    completed = subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


class PackageInstaller:
    def __init__(
            self, runner=None, log_path=None, uid_getter=None, logger=None,
            desktop_candidate_verifier=None):
        self.runner = runner or _run
        self.log_path = pathlib.Path(log_path or "/var/log/ming-package-installer.log")
        self.uid_getter = uid_getter or getattr(os, "geteuid", lambda: 1)
        self.logger = logger
        self.desktop_candidate_verifier = (
            desktop_candidate_verifier
            or getattr(COMMON, "is_system_desktop_activation_candidate", None)
            or (lambda _path: False)
        )

    def _result(self, ok, **values):
        result = {
            "ok": bool(ok),
            "action": "inspect",
            "file": "",
            "package": "",
            "version": "",
            "architecture": "",
            "error": "",
            "state": "",
            "log_path": str(self.log_path),
            "launchers": [],
            "launcher_warnings": [],
            "launch_ready": False,
        }
        result.update(values)
        return result

    def _log(self, message):
        line = "[%s] %s" % (datetime.now().strftime("%F %T"), message)
        try:
            if self.logger is not None:
                self.logger(line)
            else:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            pass

    def _call(self, command, timeout):
        try:
            returncode, output, error = self.runner(command, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", "命令执行超时。"
        except OSError as exception:
            return 127, "", str(exception)
        return int(returncode), output or "", error or ""

    def _package_file(self, package_file):
        path = pathlib.Path(package_file).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None, "找不到本地 DEB 软件包。"
        try:
            mode = resolved.stat().st_mode
        except OSError:
            return None, "无法读取本地 DEB 软件包。"
        if path.is_symlink() or not stat.S_ISREG(mode):
            return None, "只能安装普通本地 DEB 文件。"
        if resolved.suffix.lower() != ".deb":
            return None, "只能安装 .deb 软件包。"
        return resolved, ""

    def inspect(self, package_file):
        path, error = self._package_file(package_file)
        if error:
            return self._result(False, state="validation_failed", error=error)
        command = ("dpkg-deb", "--field", str(path), "Package", "Version", "Architecture")
        returncode, output, command_error = self._call(command, timeout=20)
        if returncode != 0:
            return self._result(
                False,
                file=str(path),
                state="validation_failed",
                error="无法读取 DEB 软件包元数据：%s" % (command_error.strip() or "dpkg-deb 失败"),
            )
        fields = [line.strip() for line in output.splitlines()]
        if len(fields) != 3 or not all(fields):
            return self._result(
                False, file=str(path), state="validation_failed",
                error="DEB 软件包元数据不完整。")
        name, version, architecture = fields
        if not PACKAGE_PATTERN.fullmatch(name) or not VERSION_PATTERN.fullmatch(version):
            return self._result(
                False, file=str(path), state="validation_failed",
                error="DEB 软件包元数据格式无效。")
        if architecture not in SUPPORTED_ARCHITECTURES:
            return self._result(
                False,
                file=str(path),
                package=name,
                version=version,
                architecture=architecture,
                state="validation_failed",
                error="该 DEB 软件包不是 amd64 或 all 架构。",
            )
        return self._result(
            True,
            file=str(path),
            package=name,
            version=version,
            architecture=architecture,
            state="inspected",
        )

    @staticmethod
    def _apt_install_command(package_file):
        return ("apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "install", str(package_file))

    @staticmethod
    def _apt_fix_command():
        return ("apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "-f", "install")

    @staticmethod
    def _apt_reinstall_command(package):
        return (
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "--reinstall",
            "install", package,
        )

    def _installed(self, package):
        command = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", package)
        returncode, output, _error = self._call(command, timeout=20)
        return returncode == 0 and output.strip().startswith("ii")

    def _refresh_caches(self):
        refresh = {}
        for name, command in (
            ("desktop_database", ("update-desktop-database", "/usr/share/applications")),
            ("icon_cache", ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor")),
        ):
            returncode, _output, _error = self._call(command, timeout=30)
            refresh[name] = returncode == 0
        return refresh

    @staticmethod
    def _desktop_values(argv):
        values = list(argv or ())
        if values and pathlib.PurePath(values[0]).name == "env":
            offset = 1
            while offset < len(values) and (
                    values[offset].startswith("-") or "=" in values[offset]):
                offset += 1
            values = values[offset:]
        return values

    @classmethod
    def _is_shell_wrapper(cls, argv):
        values = cls._desktop_values(argv)
        return bool(
            values
            and pathlib.PurePath(values[0]).name in {"sh", "bash", "dash", "zsh", "fish"}
            and "-c" in values[1:]
        )

    @classmethod
    def _desktop_program(cls, argv):
        """Resolve the executable position without interpreting shell syntax."""
        values = cls._desktop_values(argv)
        if not values:
            return "", "启动器没有有效的启动程序。"
        if cls._is_shell_wrapper(values):
            return "", "为保护系统安全，不支持通过 shell -c 启动的软件入口。"
        return values[0], ""

    @staticmethod
    def _desktop_visible(entry):
        return not any(
            str(entry.get(key, "")).strip().casefold() in {"1", "true", "yes"}
            for key in ("Hidden", "NoDisplay")
        )

    def _protected_package_wrapper(self, path, package_paths):
        if str(path) not in package_paths:
            return False
        try:
            return bool(self.desktop_candidate_verifier(path))
        except Exception:
            return False

    def _launcher_record(self, path, package_paths=()):
        path = pathlib.Path(path)
        record = {
            "path": str(path), "name": path.stem, "ok": False, "error": "",
            "visible": False, "activation": "",
        }
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("启动器文件不存在或为空")
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                parser.read_file(handle)
            if not parser.has_section("Desktop Entry"):
                record["error"] = "启动器缺少 Desktop Entry 配置。"
                return record
            entry = parser["Desktop Entry"]
            record["name"] = entry.get("Name[zh_CN]") or entry.get("Name") or path.stem
            record["visible"] = self._desktop_visible(entry)
            exec_line = entry.get("Exec", "").strip()
            if not exec_line:
                record["error"] = "启动器没有 Exec 启动命令。"
                return record
            try:
                argv = [item for item in shlex.split(exec_line) if not item.startswith("%")]
            except ValueError:
                record["error"] = "启动器的 Exec 格式无法解析。"
                return record
            if self._is_shell_wrapper(argv):
                if self._protected_package_wrapper(path, set(package_paths)):
                    record.update(ok=True, activation="desktop_app_info")
                    return record
                record["error"] = "为保护系统安全，不支持通过 shell -c 启动的软件入口。"
                return record
            program, error = self._desktop_program(argv)
            if error:
                record["error"] = error
                return record
            record["activation"] = "direct"
            candidate = pathlib.Path(program)
            if program.startswith("/") or candidate.is_absolute():
                executable = candidate
            elif "/" in program or "\\" in program:
                record["error"] = "启动程序路径无效：%s" % program
                return record
            else:
                found = shutil.which(program)
                executable = pathlib.Path(found) if found else None
            if not executable or not executable.is_file():
                record["error"] = "找不到启动程序：%s" % program
                return record
            if not os.access(str(executable), os.X_OK):
                record["error"] = "启动程序没有执行权限：%s" % executable
                return record
            try:
                with executable.open("rb") as handle:
                    is_elf = handle.read(4) == b"\x7fELF"
            except OSError:
                is_elf = False
            if is_elf:
                _rc, output, _error = self._call(("ldd", str(executable)), timeout=20)
                missing = [line.strip() for line in output.splitlines() if "not found" in line]
                if missing:
                    record["error"] = "缺少运行库：%s" % "; ".join(missing[:3])
                    return record
            record["ok"] = True
            return record
        except (OSError, configparser.Error) as exc:
            record["error"] = "无法读取启动器：%s" % exc
            return record

    def _package_launchers(self, package):
        """Validate only the package's own desktop launchers after installation."""
        returncode, output, _error = self._call(("dpkg-query", "-L", package), timeout=20)
        if returncode != 0:
            return []
        package_paths = {
            value.strip() for value in output.splitlines()
            if pathlib.Path(value.strip()).suffix == ".desktop"
        }
        return [
            self._launcher_record(pathlib.Path(value), package_paths=package_paths)
            for value in sorted(package_paths)
        ]

    def _launch_readiness(self, launchers, completed_state):
        visible = [record for record in launchers if record.get("visible")]
        visible_problems = [record for record in visible if not record.get("ok")]
        if visible_problems:
            details = []
            for record in visible_problems[:3]:
                name = str(record.get("name") or pathlib.Path(record.get("path", "")).stem)
                reason = str(record.get("error") or "启动器不可用")
                details.append("%s（%s）" % (name.replace("\n", " ")[:80], reason.replace("\n", " ")[:120]))
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但以下可见启动器无法启动：%s。请查看日志：%s" % (
                    "；".join(details), self.log_path),
            )
        if any(record.get("activation") == "desktop_app_info" for record in visible):
            return "%s_with_desktop_activation" % completed_state, True, ""
        return completed_state, True, ""

    def install(self, package_file):
        inspected = self.inspect(package_file)
        if not inspected["ok"]:
            inspected.update(action="install", state="validation_failed")
            return inspected
        if self.uid_getter() != 0:
            return self._result(
                False,
                action="install",
                state="permission_denied",
                file=inspected["file"],
                package=inspected["package"],
                version=inspected["version"],
                architecture=inspected["architecture"],
                error="安装 DEB 软件包需要管理员权限。",
            )

        command = self._apt_install_command(inspected["file"])
        returncode, _output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        if returncode != 0:
            dependency_repair_attempted = True
            fix_code, _fix_output, fix_error = self._call(self._apt_fix_command(), timeout=180)
            if fix_code == 0:
                returncode, _output, error = self._call(command, timeout=180)
            else:
                error = fix_error or error
        if returncode != 0:
            self._log("install failed for %s: %s" % (inspected["file"], error.strip()))
            return self._result(
                False,
                action="install",
                state="install_failed",
                dependency_repair_attempted=dependency_repair_attempted,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error="安装 DEB 软件包失败：%s" % (error.strip() or "apt-get 失败"),
            )
        if not self._installed(inspected["package"]):
            self._log("package verification failed for %s" % inspected["package"])
            return self._result(
                False,
                action="install",
                state="verification_failed",
                dependency_repair_attempted=dependency_repair_attempted,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error="软件包安装后未处于已安装状态。",
            )
        refresh = self._refresh_caches()
        launchers = self._package_launchers(inspected["package"])
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        state, launch_ready, launch_error = self._launch_readiness(launchers, "installed")
        self._log("installed %s from %s" % (inspected["package"], inspected["file"]))
        return self._result(
            True,
            action="install",
            state=state,
            launch_ready=launch_ready,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
            error=launch_error,
            **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
        )

    def repair(self, package):
        package = package.strip() if isinstance(package, str) else ""
        if not PACKAGE_PATTERN.fullmatch(package):
            return self._result(
                False,
                action="repair",
                state="validation_failed",
                error="软件包名称格式无效。",
            )
        if self.uid_getter() != 0:
            return self._result(
                False,
                action="repair",
                state="permission_denied",
                package=package,
                error="修复软件包需要管理员权限。",
            )
        command = self._apt_reinstall_command(package)
        returncode, _output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        if returncode != 0:
            dependency_repair_attempted = True
            fix_code, _fix_output, fix_error = self._call(self._apt_fix_command(), timeout=180)
            if fix_code == 0:
                returncode, _output, error = self._call(command, timeout=180)
            else:
                error = fix_error or error
        if returncode != 0:
            self._log("repair failed for %s: %s" % (package, error.strip()))
            return self._result(
                False,
                action="repair",
                state="repair_failed",
                package=package,
                dependency_repair_attempted=dependency_repair_attempted,
                error="修复软件包失败：%s" % (error.strip() or "apt-get 失败"),
            )
        if not self._installed(package):
            self._log("repair verification failed for %s" % package)
            return self._result(
                False,
                action="repair",
                state="verification_failed",
                package=package,
                dependency_repair_attempted=dependency_repair_attempted,
                error="软件包修复后未处于已安装状态。",
            )
        refresh = self._refresh_caches()
        launchers = self._package_launchers(package)
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        state, launch_ready, launch_error = self._launch_readiness(launchers, "repaired")
        self._log("repaired %s" % package)
        return self._result(
            True,
            action="repair",
            state=state,
            launch_ready=launch_ready,
            package=package,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
            error=launch_error,
        )


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-package-installer")
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect")
    inspect.add_argument("file")
    inspect.add_argument("--json", action="store_true")
    install = actions.add_parser("install")
    install.add_argument("file")
    repair = actions.add_parser("repair")
    repair.add_argument("package")
    return parser


def result_exit_code(result):
    if result.get("ok"):
        return 0
    if result.get("state") == "permission_denied":
        return 3
    if result.get("state") == "validation_failed":
        return 2
    return 4


def main(argv=None, installer=None, stdout=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    installer = installer or PackageInstaller()
    stdout = stdout or sys.stdout
    if args.action == "inspect":
        result = installer.inspect(args.file)
    elif args.action == "install":
        result = installer.install(args.file)
    else:
        result = installer.repair(args.package)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
