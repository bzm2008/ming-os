#!/usr/bin/env python3
"""Safely inspect and install local Debian packages for Ming OS."""

import argparse
import configparser
import hashlib
import importlib.util
import json
import pathlib
import re
import stat
import subprocess
import os
import sys
import types
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit


SUPPORTED_ARCHITECTURES = {"amd64", "all"}
PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+:~\-]+$")
SYSTEM_APPLICATION_DIR = pathlib.Path("/usr/share/applications")
SHELL_WRAPPER_REJECTION = "shell command wrappers are not allowed"
PACKAGE_INSTALLER_PATH = "/usr/local/sbin/ming-package-installer"
PACKAGE_INSTALLER_CONTRACT = "ming-package-installer-26.4.0-v2"
REQUIRED_COMMON_SHA256 = "cc2e34b62e6ab9cac74164dd51c2b5218d016f42500f80017931ce7d6f3b6ad1"
PACKAGE_MANAGER_LOCK = "/run/lock/ming-package-manager.lock"
PACKAGE_MANAGER_LOCK_TIMEOUT = 30
DPKG_LOCK_TIMEOUT = 60
PACKAGE_MANAGER_BUSY_EXIT = 75
E_PACKAGE_BUSY = "E_PACKAGE_BUSY"
E_RESOLVER_FAILED = "E_RESOLVER_FAILED"
E_LAUNCH_NOT_READY = "E_LAUNCH_NOT_READY"
E_PACKAGE_FAILED = "E_PACKAGE_FAILED"
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(password|passwd|token|access[_-]?token|refresh[_-]?token|auth[_-]?token)\b"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_SECRET_OPTION = re.compile(
    r"(--(?:password|passwd|token|access-token|refresh-token|auth-token)(?:\s+|=))"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"\b(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+",
    re.IGNORECASE,
)


def _redacted_url(match):
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,);]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        if not hostname:
            return "[REDACTED_URL]" + trailing
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[%s]" % hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = hostname if port is None else "%s:%s" % (hostname, port)
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", "")) + trailing
    except (AttributeError, TypeError, ValueError):
        return "[REDACTED_URL]" + trailing


def _redact_sensitive(value):
    text = str(value or "").replace("\x00", " ")
    text = _URL.sub(_redacted_url, text)
    text = _AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = _SECRET_OPTION.sub(r"\1[REDACTED]", text)
    return _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)


def _redact_json_value(value):
    if isinstance(value, str):
        return _redact_sensitive(value)
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_json_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_json_value(item) for key, item in value.items()}
    return value


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
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != REQUIRED_COMMON_SHA256:
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
        self.application_dir = SYSTEM_APPLICATION_DIR
        desktop_ids = getattr(COMMON, "current_desktop_ids", None)
        try:
            self.current_desktops = set(desktop_ids()) if callable(desktop_ids) else {"xfce"}
        except (AttributeError, OSError, TypeError, ValueError):
            self.current_desktops = {"xfce"}
        if not self.current_desktops:
            self.current_desktops = {"xfce"}

    def _result(self, ok, **values):
        result = {
            "ok": bool(ok),
            "action": "inspect",
            "file": "",
            "package": "",
            "version": "",
            "architecture": "",
            "error": "",
            "error_code": "",
            "state": "",
            "log_path": str(self.log_path),
            "launchers": [],
            "launcher_warnings": [],
            "launch_ready": False,
            "repair_argv": [],
        }
        result.update(values)
        return _redact_json_value(result)

    def _log(self, message):
        line = "[%s] %s" % (
            datetime.now().strftime("%F %T"), _redact_sensitive(message))
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
            return 127, "", _redact_sensitive(exception)
        return (
            int(returncode),
            _redact_sensitive(output),
            _redact_sensitive(error),
        )

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

    @staticmethod
    def _metadata_fields(output):
        """Parse the exact labeled fields emitted by multi-field dpkg-deb."""
        expected = ("Package", "Version", "Architecture")
        values = {}
        for line in output.splitlines():
            field, separator, value = line.partition(":")
            value = value.strip()
            if (
                    not separator
                    or field not in expected
                    or field in values
                    or not value):
                return None
            values[field] = value
        if set(values) != set(expected):
            return None
        return tuple(values[field] for field in expected)

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
        fields = self._metadata_fields(output)
        if fields is None:
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
    def _locked_apt_command(*arguments):
        return (
            "flock", "--exclusive", "--timeout", str(PACKAGE_MANAGER_LOCK_TIMEOUT),
            "--conflict-exit-code", str(PACKAGE_MANAGER_BUSY_EXIT),
            PACKAGE_MANAGER_LOCK,
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "DPkg::Lock::Timeout=%s" % DPKG_LOCK_TIMEOUT,
            *tuple(str(value) for value in arguments),
        )

    @staticmethod
    def _apt_install_command(package_file):
        return PackageInstaller._locked_apt_command("install", package_file)

    @staticmethod
    def _apt_fix_command():
        return PackageInstaller._locked_apt_command("-f", "install")

    @staticmethod
    def _apt_reinstall_command(package):
        return PackageInstaller._locked_apt_command("--reinstall", "install", package)

    @staticmethod
    def _apt_failure_code(returncode, output="", error=""):
        detail = "%s\n%s" % (output or "", error or "")
        lock_markers = (
            "could not get lock /var/lib/dpkg/",
            "could not open lock file /var/lib/dpkg/",
            "unable to acquire the dpkg frontend lock",
            "unable to lock the administration directory",
        )
        if int(returncode) == PACKAGE_MANAGER_BUSY_EXIT or any(
                marker in detail.casefold() for marker in lock_markers):
            return E_PACKAGE_BUSY
        resolver_markers = (
            "unmet dependencies", "held broken packages",
            "unable to correct problems", "pkgproblemresolver",
            "dependency problems", "dependency error", "depends:", "conflicts:",
        )
        if any(marker in detail.casefold() for marker in resolver_markers):
            return E_RESOLVER_FAILED
        return E_PACKAGE_FAILED

    @staticmethod
    def _apt_failure_message(error_code):
        if error_code == E_PACKAGE_BUSY:
            return "软件包管理器正忙，请稍后重试。"
        if error_code == E_RESOLVER_FAILED:
            return "软件包依赖解析失败，请检查软件源后重试。"
        return "软件包安装过程失败，请查看受保护的系统日志。"

    @staticmethod
    def _apt_log_detail(output="", error=""):
        detail = " ".join(("%s\n%s" % (output or "", error or "")).split())
        return _redact_sensitive(detail)[:2048] or "apt-get failed without diagnostics"

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

    def _package_owns_launcher(self, path, package_paths, package=""):
        if str(path) not in package_paths:
            return False
        owner_lookup = getattr(COMMON, "installed_package_owner", None)
        if not callable(owner_lookup):
            return False
        try:
            owner = owner_lookup(
                path,
                command_runner=self._dpkg_query_runner,
                expected_package=package or None,
            )
            if not owner:
                return False
            # Keep the installer fail-closed even if a future shared helper is
            # replaced by an adapter that returns an unexpected installed owner.
            if package and str(owner).split(":", 1)[0] != package.split(":", 1)[0]:
                return False
            return True
        except (AttributeError, OSError, TypeError, ValueError, subprocess.TimeoutExpired):
            return False

    def _protected_package_wrapper(self, path, package_paths, package=""):
        if not self._package_owns_launcher(path, package_paths, package):
            return False
        try:
            return bool(self.desktop_candidate_verifier(path))
        except Exception:
            return False

    def _dpkg_query_runner(self, arguments, timeout=2):
        """Adapt the installer's tuple runner for shared package ownership checks."""
        returncode, output, error = self._call(
            ("dpkg-query", *tuple(arguments)), timeout=timeout)
        return types.SimpleNamespace(
            returncode=returncode,
            stdout=output,
            stderr=error,
        )

    def _catalog_entry_status(self, path):
        """Read catalog visibility only; launch validity comes from COMMON."""
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            with pathlib.Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                parser.read_file(handle)
            section = parser["Desktop Entry"]
            visible = getattr(COMMON, "desktop_entry_is_visible", None)
            if not callable(visible):
                return False, "启动器校验组件不可用。"
            if visible(
                    section, current_desktops=self.current_desktops,
                    respect_desktop_environment=True):
                return True, ""
            if (
                section.getboolean("Hidden", fallback=False)
                or section.getboolean("NoDisplay", fallback=False)
            ):
                return False, "启动器已隐藏（Hidden 或 NoDisplay）。"
            only = {
                item.strip().casefold()
                for item in section.get("OnlyShowIn", "").split(";") if item.strip()
            }
            excluded = {
                item.strip().casefold()
                for item in section.get("NotShowIn", "").split(";") if item.strip()
            }
            if only and not self.current_desktops.intersection(only):
                return False, "启动器不适用于当前桌面（OnlyShowIn）。"
            if self.current_desktops.intersection(excluded):
                return False, "启动器已在当前桌面禁用（NotShowIn）。"
            return False, "启动器不适用于当前桌面。"
        except (KeyError, OSError, ValueError, configparser.Error):
            return True, ""

    @staticmethod
    def _diagnostic_from_common(path, fallback):
        diagnostic = getattr(COMMON, "diagnose_desktop_file", None)
        if callable(diagnostic):
            try:
                entry = diagnostic(path)
                message = getattr(entry, "diagnostic", "") if entry is not None else ""
                if message:
                    return str(message)
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        return fallback

    def _parse_error(self, path, error):
        return self._diagnostic_from_common(
            path, "启动器配置无效：%s" % (str(error) or "未知错误"))

    def _launcher_record(self, path, package_paths=(), package=""):
        path = pathlib.Path(path)
        record = {
            "path": str(path), "name": path.stem, "ok": False, "error": "",
            "visible": True, "activation": "",
        }
        try:
            if path.is_symlink():
                record["error"] = "启动器路径不能是符号链接。"
                return record
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("启动器文件不存在或为空")
            parser = getattr(COMMON, "parse_desktop_file", None)
            if not callable(parser):
                record["error"] = "启动器校验组件不可用。"
                return record
            record["visible"], catalog_error = self._catalog_entry_status(path)
            if not record["visible"]:
                record["error"] = catalog_error
                return record
            try:
                entry = parser(path)
            except ValueError as exc:
                if (
                        str(exc) == SHELL_WRAPPER_REJECTION
                        and self._protected_package_wrapper(
                            path, set(package_paths), package)):
                    record.update(ok=True, activation="desktop_app_info")
                    return record
                record["error"] = self._parse_error(path, exc)
                return record
            if entry is None:
                record["error"] = self._diagnostic_from_common(
                    path, "启动器不是可用的图形应用，或其 TryExec 依赖不可用。")
                return record
            record["name"] = entry.name or path.stem
            record["activation"] = "direct"
            diagnostic = getattr(COMMON, "desktop_launch_diagnostic", None)
            program_getter = getattr(COMMON, "desktop_exec_program", None)
            if not callable(diagnostic) or not callable(program_getter):
                record["error"] = "启动器校验组件不可用。"
                return record
            error = diagnostic(entry.argv)
            if error:
                record["error"] = str(error)
                return record
            program = program_getter(entry.argv)
            candidate = pathlib.Path(program)
            executable = candidate if candidate.is_absolute() else None
            try:
                if executable is None:
                    import shutil
                    found = shutil.which(program)
                    executable = pathlib.Path(found) if found else None
                with executable.open("rb") as handle:
                    is_elf = handle.read(4) == b"\x7fELF"
            except (AttributeError, OSError, TypeError, ValueError):
                is_elf = False
            if is_elf:
                _rc, output, _error = self._call(("ldd", str(executable)), timeout=20)
                missing = [line.strip() for line in output.splitlines() if "not found" in line]
                if missing:
                    record["error"] = "缺少运行库：%s" % "; ".join(missing[:3])
                    return record
            if package and not self._package_owns_launcher(
                    path, set(package_paths), package):
                record["error"] = "启动器所有权无法验证。"
                return record
            if package:
                try:
                    protected = bool(self.desktop_candidate_verifier(path))
                except Exception:
                    protected = False
                if not protected:
                    record["error"] = "启动器保护状态无法验证。"
                    return record
            record["ok"] = True
            return record
        except (OSError, configparser.Error) as exc:
            record["error"] = "无法读取启动器：%s" % exc
            return record

    def _catalog_desktop_path(self, value):
        try:
            path = pathlib.Path(str(value).strip())
            application_dir = pathlib.Path(self.application_dir)
        except (TypeError, ValueError):
            return None
        if (
                not path.is_absolute()
                or path.suffix != ".desktop"
                or path.parent != application_dir):
            return None
        return path

    def _package_launchers(self, package):
        """Validate package-owned direct children of the system app catalog."""
        returncode, output, command_error = self._call(
            ("dpkg-query", "-L", package), timeout=20)
        if returncode != 0:
            detail = command_error.strip() or "dpkg-query -L 失败"
            return [], "无法枚举软件包图形启动器：%s" % detail
        package_paths = {
            str(path) for value in output.splitlines()
            for path in (self._catalog_desktop_path(value),)
            if path is not None
        }
        return ([
            self._launcher_record(
                pathlib.Path(value), package_paths=package_paths, package=package)
            for value in sorted(package_paths)
        ], "")

    def _launch_readiness(self, launchers, completed_state, enumeration_error=""):
        if enumeration_error:
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但无法确认图形启动器：%s。请查看日志：%s" % (
                    enumeration_error, self.log_path),
            )
        if not launchers:
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但未发现可验证的系统图形启动器。请查看日志：%s" % (
                    self.log_path,
                ),
            )
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
        if launchers and not visible:
            details = []
            for record in launchers[:3]:
                name = str(record.get("name") or pathlib.Path(record.get("path", "")).stem)
                reason = str(record.get("error") or "启动器不适用于当前桌面")
                details.append("%s（%s）" % (
                    name.replace("\n", " ")[:80], reason.replace("\n", " ")[:120]))
            return (
                "%s_with_launch_problem" % completed_state,
                False,
                "软件已安装，但没有当前桌面可用的图形启动器：%s。请查看日志：%s" % (
                    "；".join(details), self.log_path),
            )
        if any(record.get("activation") == "desktop_app_info" for record in visible):
            return "%s_with_desktop_activation" % completed_state, True, ""
        return completed_state, True, ""

    @staticmethod
    def _repair_argv(package):
        return [PACKAGE_INSTALLER_PATH, "repair", package]

    def _log_launch_readiness(self, package, warnings, enumeration_error=""):
        if enumeration_error:
            self._log("launcher enumeration failed for %s: %s" % (
                package, enumeration_error))
        for warning in warnings:
            self._log("launcher readiness problem for %s: %s: %s" % (
                package,
                warning.get("path") or "<enumeration>",
                warning.get("error") or "启动器不可用",
            ))

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
        returncode, output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        failure_code = self._apt_failure_code(returncode, output, error)
        if returncode != 0 and failure_code == E_RESOLVER_FAILED:
            dependency_repair_attempted = True
            fix_code, fix_output, fix_error = self._call(self._apt_fix_command(), timeout=180)
            if fix_code == 0:
                returncode, output, error = self._call(command, timeout=180)
            else:
                returncode, output = fix_code, fix_output
                error = fix_error or error
        if returncode != 0:
            failure_code = self._apt_failure_code(returncode, output, error)
            self._log("install failed for %s: %s" % (
                inspected["file"], self._apt_log_detail(output, error)))
            return self._result(
                False,
                action="install",
                state="install_failed",
                error_code=failure_code,
                dependency_repair_attempted=dependency_repair_attempted,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error=self._apt_failure_message(failure_code),
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
        launchers, enumeration_error = self._package_launchers(inspected["package"])
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        if enumeration_error:
            launcher_warnings.append({
                "path": "", "name": inspected["package"], "ok": False,
                "visible": True, "activation": "", "error": enumeration_error,
            })
        state, launch_ready, launch_error = self._launch_readiness(
            launchers, "installed", enumeration_error)
        self._log("installed %s from %s" % (inspected["package"], inspected["file"]))
        self._log_launch_readiness(
            inspected["package"], launcher_warnings, enumeration_error)
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
            error_code=E_LAUNCH_NOT_READY if not launch_ready else "",
            repair_argv=(self._repair_argv(inspected["package"])
                         if not launch_ready else []),
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
        returncode, output, error = self._call(command, timeout=180)
        dependency_repair_attempted = False
        failure_code = self._apt_failure_code(returncode, output, error)
        if returncode != 0 and failure_code == E_RESOLVER_FAILED:
            dependency_repair_attempted = True
            fix_code, fix_output, fix_error = self._call(self._apt_fix_command(), timeout=180)
            if fix_code == 0:
                returncode, output, error = self._call(command, timeout=180)
            else:
                returncode, output = fix_code, fix_output
                error = fix_error or error
        if returncode != 0:
            failure_code = self._apt_failure_code(returncode, output, error)
            self._log("repair failed for %s: %s" % (
                package, self._apt_log_detail(output, error)))
            return self._result(
                False,
                action="repair",
                state="repair_failed",
                error_code=failure_code,
                package=package,
                dependency_repair_attempted=dependency_repair_attempted,
                error=self._apt_failure_message(failure_code),
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
        launchers, enumeration_error = self._package_launchers(package)
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        if enumeration_error:
            launcher_warnings.append({
                "path": "", "name": package, "ok": False,
                "visible": True, "activation": "", "error": enumeration_error,
            })
        state, launch_ready, launch_error = self._launch_readiness(
            launchers, "repaired", enumeration_error)
        self._log("repaired %s" % package)
        self._log_launch_readiness(package, launcher_warnings, enumeration_error)
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
            error_code=E_LAUNCH_NOT_READY if not launch_ready else "",
            repair_argv=self._repair_argv(package) if not launch_ready else [],
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
