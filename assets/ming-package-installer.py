#!/usr/bin/env python3
"""Safely inspect and install local Debian packages for Ming OS."""

import argparse
import configparser
import contextlib
import hashlib
import json
import pathlib
import re
import stat
import subprocess
import os
import shlex
import shutil
import sys
import tempfile
from datetime import datetime

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows test host fallback
    fcntl = None


SUPPORTED_ARCHITECTURES = {"amd64", "all"}
PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z.+:~\-]+$")
OPT_APPS_ROOT = pathlib.Path("/opt/apps")
OPT_PROXY_DIR = pathlib.Path("/usr/local/share/applications")
OPT_PROXY_MANIFEST = pathlib.Path("/var/lib/ming-os/desktop-proxies/manifest-v1.json")
OPT_PROXY_GENERATION = "ming-opt-desktop-proxies-v1"
OPT_PROXY_NAME_PATTERN = re.compile(r"^ming-opt-[0-9a-f]{64}\.desktop$")


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
    def __init__(self, runner=None, log_path=None, uid_getter=None, logger=None,
                 opt_apps_root=OPT_APPS_ROOT, proxy_dir=OPT_PROXY_DIR,
                 proxy_manifest=OPT_PROXY_MANIFEST):
        self.runner = runner or _run
        self.log_path = pathlib.Path(log_path or "/var/log/ming-package-installer.log")
        self.uid_getter = uid_getter or getattr(os, "geteuid", lambda: 1)
        self.logger = logger
        self.opt_apps_root = pathlib.Path(opt_apps_root)
        self.proxy_dir = pathlib.Path(proxy_dir)
        self.proxy_manifest = pathlib.Path(proxy_manifest)

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
            "error_code": "",
            "installed": False,
            "launch_ready": False,
            "log_path": str(self.log_path),
            "launchers": [],
            "launcher_warnings": [],
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

    @staticmethod
    def _metadata_fields(output):
        expected = ("Package", "Version", "Architecture")
        labeled = {}
        for line in output.splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() in expected:
                labeled[name.strip()] = value.strip()
        if all(labeled.get(name) for name in expected):
            return tuple(labeled[name] for name in expected)
        fields = tuple(line.strip() for line in output.splitlines() if line.strip())
        return fields if len(fields) == len(expected) else ()

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
        return (
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "Dpkg::Lock::Timeout=30", "install", str(package_file),
        )

    @staticmethod
    def _apt_fix_command():
        return (
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0",
            "-o", "Dpkg::Lock::Timeout=30", "-f", "install",
        )

    @staticmethod
    def _apt_reinstall_command(package):
        return (
            "apt-get", "-y", "-o", "Dpkg::Use-Pty=0", "--reinstall",
            "-o", "Dpkg::Lock::Timeout=30", "install", package,
        )

    @staticmethod
    def _install_failure(error):
        """Classify apt failures without exposing repository URLs or raw output."""
        text = (error or "").lower()
        if "lock" in text or "could not get" in text:
            return "package_busy", "E_PACKAGE_BUSY", "软件安装器正在被其他任务使用，请稍后重试。"
        return "install_failed", "E_RESOLVER_FAILED", "无法满足软件包依赖或完成安装，请检查软件包和网络后重试。"

    def _installed(self, package):
        command = ("dpkg-query", "-W", "-f=${db:Status-Abbrev}", package)
        returncode, output, _error = self._call(command, timeout=20)
        return returncode == 0 and output.strip().startswith("ii")

    def _refresh_caches(self):
        refresh = {}
        desktop_results = []
        for directory in (self.proxy_dir, pathlib.Path("/usr/share/applications")):
            if directory == self.proxy_dir and not directory.exists():
                continue
            command = ("update-desktop-database", directory.as_posix())
            returncode, _output, _error = self._call(command, timeout=30)
            desktop_results.append(returncode == 0)
        refresh["desktop_database"] = all(desktop_results)
        returncode, _output, _error = self._call(
            ("gtk-update-icon-cache", "-f", "-t", "/usr/share/icons/hicolor"), timeout=30)
        refresh["icon_cache"] = returncode == 0
        refresh["desktop_state"] = self._refresh_desktop_state()
        return refresh

    def _refresh_desktop_state(self):
        helper = shutil.which("ming-refresh-desktop-state")
        if not helper:
            return False
        returncode, _output, _error = self._call((helper,), timeout=20)
        return returncode == 0

    @staticmethod
    def _desktop_program(argv):
        """Resolve the executable position without interpreting shell syntax."""
        values = list(argv or ())
        if not values:
            return "", "启动器没有有效的启动命令。"
        command = pathlib.PurePath(values[0]).name
        if command in {"sh", "bash", "dash", "zsh", "fish"} and "-c" in values[1:]:
            return "", "为保护系统安全，不支持通过 shell -c 启动的软件入口。"
        if command == "env":
            offset = 1
            while offset < len(values) and (
                    values[offset].startswith("-") or "=" in values[offset]):
                offset += 1
            values = values[offset:]
        if not values:
            return "", "启动器没有有效的启动程序。"
        return values[0], ""

    def _launcher_record(self, path):
        path = pathlib.Path(path)
        record = {"path": str(path), "name": path.stem, "ok": False, "error": ""}
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
            entry_type = entry.get("Type", "Application").strip()
            hidden = entry.get("Hidden", "").strip().lower() == "true"
            no_display = entry.get("NoDisplay", "").strip().lower() == "true"
            if entry_type != "Application" or hidden or no_display:
                record.update(ok=True, ignored=True, launchable=False)
                return record
            exec_line = entry.get("Exec", "").strip()
            if not exec_line:
                record["error"] = "启动器没有 Exec 启动命令。"
                return record
            try:
                argv = [item for item in shlex.split(exec_line) if not item.startswith("%")]
            except ValueError:
                record["error"] = "启动器的 Exec 格式无法解析。"
                return record
            program, error = self._desktop_program(argv)
            if error:
                record["error"] = error
                return record
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
            record.update(ok=True, launchable=True)
            return record
        except (OSError, configparser.Error) as exc:
            record["error"] = "无法读取启动器：%s" % exc
            return record

    def _package_launchers(self, package):
        """Validate package-owned visible desktop launchers after installation."""
        returncode, output, _error = self._call(("dpkg-query", "-L", package), timeout=20)
        if returncode != 0:
            return []
        records = []
        opt_sources = []
        for value in output.splitlines():
            path = pathlib.Path(value.strip())
            if path.suffix == ".desktop":
                try:
                    path.relative_to(self.opt_apps_root)
                    under_opt_apps = True
                except ValueError:
                    under_opt_apps = False
                if under_opt_apps and not self._safe_opt_apps_source(path):
                    records.append({
                        "path": str(path), "name": path.stem, "ok": False,
                        "error": "/opt/apps 启动入口路径或权限不安全。",
                    })
                elif under_opt_apps:
                    opt_sources.append(path)
                else:
                    record = self._launcher_record(path)
                    if not record.get("ignored"):
                        records.append(record)
        if opt_sources or self.proxy_manifest.exists():
            proxy_records, proxy_error = self.sync_opt_app_proxies(package, opt_sources)
            records.extend(proxy_records)
            if proxy_error and not proxy_records:
                records.append({"path": str(opt_sources[0]), "name": opt_sources[0].stem,
                                "ok": False, "error": proxy_error})
        return records

    def _safe_opt_apps_source(self, path):
        try:
            source = pathlib.Path(path)
            relative = source.relative_to(self.opt_apps_root)
        except (OSError, TypeError, ValueError):
            return False
        parts = relative.parts
        if (len(parts) != 4 or parts[1:3] != ("entries", "applications")
                or source.suffix != ".desktop" or any(part in {"", ".", ".."} for part in parts)):
            return False
        try:
            metadata = source.lstat()
            chain = [source.parent]
            while chain[-1] != self.opt_apps_root.parent:
                parent = chain[-1].parent
                if parent == chain[-1]:
                    return False
                chain.append(parent)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                return False
            if os.name != "nt" and (metadata.st_uid != 0 or metadata.st_mode & 0o022):
                return False
            return all(
                stat.S_ISDIR(item.lstat().st_mode)
                and (os.name == "nt" or (item.lstat().st_uid == 0 and not item.lstat().st_mode & 0o022))
                for item in chain
            )
        except OSError:
            return False

    @staticmethod
    def _sha256_bytes(value):
        return hashlib.sha256(value).hexdigest()

    def proxy_receipt_path(self):
        return self.proxy_manifest.with_name(self.proxy_manifest.name + ".receipt.json")

    def proxy_lock_path(self):
        return self.proxy_manifest.with_name(self.proxy_manifest.name + ".lock")

    @contextlib.contextmanager
    def _proxy_manifest_lock(self):
        """Serialize manifest read-modify-write transactions across installers."""
        lock_path = self.proxy_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as stream:
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                return

            import msvcrt  # pylint: disable=import-outside-toplevel
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def _managed_proxy_path(self, value):
        """Return a stale proxy path only when it is owned by our proxy namespace."""
        try:
            candidate = pathlib.Path(value)
            if not candidate.is_absolute() or not OPT_PROXY_NAME_PATTERN.fullmatch(candidate.name):
                return None
            proxy_root = self.proxy_dir.resolve(strict=False)
            if candidate.parent.resolve(strict=False) != proxy_root:
                return None
            if candidate.is_symlink():
                return None
            return candidate
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _atomic_bytes(self, path, content, mode=0o644):
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def sync_opt_app_proxies(self, package, source_paths):
        """Publish /opt/apps desktop proxies and their verified completion receipt."""
        package = package.strip() if isinstance(package, str) else ""
        if not PACKAGE_PATTERN.fullmatch(package):
            return [], "软件包名称格式无效。"
        sources = sorted({pathlib.Path(path) for path in source_paths}, key=str)
        if any(not self._safe_opt_apps_source(path) for path in sources):
            return [], "/opt/apps 启动入口路径或权限不安全。"
        prepared = []
        for source in sources:
            record = self._launcher_record(source)
            if record.get("ignored"):
                continue
            if not record.get("ok"):
                return [record], record.get("error") or "启动入口校验失败。"
            content = source.read_bytes()
            proxy_name = "ming-opt-%s.desktop" % self._sha256_bytes(str(source).encode("utf-8"))
            proxy = self.proxy_dir / proxy_name
            prepared.append((source, proxy, content, record))
        try:
            with self._proxy_manifest_lock():
                self.proxy_dir.mkdir(parents=True, exist_ok=True)
                existing_entries = []
                stale_entries = []
                if self.proxy_manifest.exists():
                    try:
                        existing = json.loads(self.proxy_manifest.read_text(encoding="utf-8"))
                        if (existing.get("schema_version") != 1
                                or existing.get("generation") != OPT_PROXY_GENERATION
                                or not isinstance(existing.get("entries"), list)):
                            raise ValueError("existing proxy manifest schema is invalid")
                        for entry in existing["entries"]:
                            if not isinstance(entry, dict) or not all(
                                    isinstance(entry.get(key), str) for key in (
                                        "proxy_path", "source_path", "package",
                                        "source_sha256", "proxy_sha256")):
                                raise ValueError("existing proxy manifest entry is invalid")
                            if entry["package"] == package:
                                stale_entries.append(entry)
                            else:
                                existing_entries.append(entry)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        return [], "无法读取已有启动入口代理清单：%s" % exc
                entries = list(existing_entries)
                for source, proxy, content, _record in prepared:
                    entries.append({
                        "proxy_path": str(proxy), "source_path": str(source), "package": package,
                        "source_sha256": self._sha256_bytes(content),
                        "proxy_sha256": self._sha256_bytes(content),
                    })
                manifest_core = {"schema_version": 1, "generation": OPT_PROXY_GENERATION,
                                 "entries": entries}
                manifest_core_bytes = (json.dumps(
                    manifest_core, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")) + "\n").encode("utf-8")
                manifest = dict(manifest_core, sha256=self._sha256_bytes(manifest_core_bytes))
                manifest_bytes = (json.dumps(
                    manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
                receipt = {"schema_version": 1, "generation": OPT_PROXY_GENERATION,
                           "manifest_sha256": self._sha256_bytes(manifest_bytes)}
                receipt_bytes = (json.dumps(
                    receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

                retained_proxy_paths = {entry["proxy_path"] for entry in entries}
                stale_paths = []
                for entry in stale_entries:
                    if entry["proxy_path"] in retained_proxy_paths:
                        continue
                    stale = self._managed_proxy_path(entry["proxy_path"])
                    if stale is not None:
                        stale_paths.append(stale)
                targets = [proxy for _source, proxy, _content, _record in prepared]
                targets.extend(stale_paths)
                targets.extend([self.proxy_manifest, self.proxy_receipt_path()])
                snapshot = {}
                try:
                    for target in targets:
                        snapshot[target] = target.read_bytes() if target.exists() else None
                    for _source, proxy, content, _record in prepared:
                        self._atomic_bytes(proxy, content)
                    for stale in stale_paths:
                        stale.unlink(missing_ok=True)
                    self._atomic_bytes(self.proxy_manifest, manifest_bytes)
                    self._atomic_bytes(self.proxy_receipt_path(), receipt_bytes)
                except (OSError, ValueError) as exc:
                    for target, content in snapshot.items():
                        try:
                            if content is None:
                                target.unlink(missing_ok=True)
                            else:
                                self._atomic_bytes(target, content)
                        except OSError:
                            pass
                    return [], "启动入口代理发布失败：%s" % exc
        except OSError as exc:
            return [], "启动入口代理锁定失败：%s" % exc
        records = []
        for source, proxy, _content, record in prepared:
            updated = dict(record, path=str(proxy), proxy_path=str(proxy), source_path=str(source),
                           activation="desktop_proxy", ok=True)
            records.append(updated)
        return records, ""

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
            state, error_code, message = self._install_failure(error)
            return self._result(
                False,
                action="install",
                state=state,
                error_code=error_code,
                dependency_repair_attempted=dependency_repair_attempted,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error=message,
            )
        if not self._installed(inspected["package"]):
            self._log("package verification failed for %s" % inspected["package"])
            return self._result(
                False,
                action="install",
                state="verification_failed",
                error_code="E_INSTALL_NOT_VERIFIED",
                dependency_repair_attempted=dependency_repair_attempted,
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
                error="软件包安装后未处于已安装状态。",
            )
        launchers = self._package_launchers(inspected["package"])
        refresh = self._refresh_caches()
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        launch_ready = not launcher_warnings
        if not launch_ready:
            self._log("launcher verification failed for %s" % inspected["package"])
            return self._result(
                False,
                action="install",
                state="installed_without_launcher",
                error_code="E_LAUNCH_NOT_READY",
                installed=True,
                launch_ready=False,
                dependency_repair_attempted=dependency_repair_attempted,
                refresh=refresh,
                launchers=launchers,
                launcher_warnings=launcher_warnings,
                error="软件包已安装，但没有可验证的图形启动器。",
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
            )
        refresh_failures = [name for name, ready in refresh.items() if not ready]
        if refresh_failures:
            self._log("desktop refresh failed for %s: %s" % (
                inspected["package"], ", ".join(refresh_failures)))
            return self._result(
                False,
                action="install",
                state="installed_with_refresh_warning",
                error_code="E_DESKTOP_REFRESH_FAILED",
                installed=True,
                launch_ready=True,
                dependency_repair_attempted=dependency_repair_attempted,
                refresh=refresh,
                launchers=launchers,
                launcher_warnings=[],
                error="软件包已安装，但桌面刷新失败（%s）。请在应用库点击刷新/重试。" %
                      ", ".join(refresh_failures),
                **{key: inspected[key] for key in ("file", "package", "version", "architecture")},
            )
        self._log("installed %s from %s" % (inspected["package"], inspected["file"]))
        return self._result(
            True,
            action="install",
            state="installed",
            installed=True,
            launch_ready=True,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
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
        launchers = self._package_launchers(package)
        refresh = self._refresh_caches()
        launcher_warnings = [record for record in launchers if not record.get("ok")]
        self._log("repaired %s" % package)
        return self._result(
            True,
            action="repair",
            state="repaired_with_launch_warning" if launcher_warnings else "repaired",
            package=package,
            dependency_repair_attempted=dependency_repair_attempted,
            refresh=refresh,
            launchers=launchers,
            launcher_warnings=launcher_warnings,
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
