#!/usr/bin/env python3
"""Structured, user-facing hardware diagnostics for Ming OS."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def run_command(command, timeout=8):
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=timeout, env=environment)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


MING_LEGACY_INTEL_XORG_HEADER = "# Managed by Ming OS legacy Intel Xorg setup"
KNOWN_DRM_DRIVERS = ("i915", "amdgpu", "radeon", "nouveau", "zx", "zhaoxin")


class HardwareStatus:
    def __init__(self, runner=run_command, render_nodes=None,
                 xorg_config_path=None, render_access=None, xorg_log_reader=None):
        self.runner = runner
        self.render_nodes = render_nodes or (lambda: sorted(Path("/dev/dri").glob("renderD*")))
        self.xorg_config_path = Path(xorg_config_path or "/etc/X11/xorg.conf.d/20-intel.conf")
        self.render_access = render_access or self._can_access_render_node
        self.xorg_log_reader = xorg_log_reader or self._read_xorg_logs

    def _run(self, command, timeout=8):
        return self.runner(command, timeout=timeout)

    @staticmethod
    def _gpu_block(lspci_output):
        blocks, current = [], []
        for line in (lspci_output or "").splitlines():
            if line and not line[0].isspace():
                if current:
                    blocks.append(current)
                current = [line]
            elif current:
                current.append(line)
        if current:
            blocks.append(current)
        for block in blocks:
            text = "\n".join(block)
            if re.search(r"VGA compatible controller|3D controller|Display controller", text, re.I):
                return text
        return ""

    @staticmethod
    def _gpu_model(block):
        header = (block or "").splitlines()
        if not header:
            return "未检测到显卡"
        return re.sub(r"^[0-9a-f:.]+\s+(?:VGA compatible controller|3D controller|Display controller):\s*",
                      "", header[0], flags=re.I)

    @staticmethod
    def _codec_state(vainfo_output, names):
        return "available" if any(re.search(name, vainfo_output or "", re.I) for name in names) else "unsupported"

    @staticmethod
    def _can_access_render_node(path):
        return os.access(path, os.R_OK | os.W_OK)

    def _legacy_intel_config_present(self):
        try:
            config = self.xorg_config_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return MING_LEGACY_INTEL_XORG_HEADER in config or all(re.search(pattern, config, re.M)
                                                               for pattern in (
            r'^\s*Identifier\s+"Intel Graphics"\s*$',
            r'^\s*Driver\s+"intel"\s*$',
            r'^\s*Option\s+"TearFree"\s+"true"\s*$',
            r'^\s*Option\s+"AccelMethod"\s+"sna"\s*$',
            r'^\s*Option\s+"DRI"\s+"3"\s*$',
            r'^\s*Option\s+"TripleBuffer"\s+"true"\s*$',
        ))

    @staticmethod
    def _read_xorg_logs():
        paths = [
            Path("/var/log/Xorg.0.log"),
            Path("/var/log/Xorg.1.log"),
            Path.home() / ".local/share/xorg/Xorg.0.log",
            Path.home() / ".local/share/xorg/Xorg.1.log",
        ]
        chunks = []
        for path in paths:
            try:
                if path.is_file():
                    chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        return "\n".join(chunks)

    @staticmethod
    def _xorg_backend(legacy_config, xorg_log):
        if legacy_config or re.search(r'LoadModule:\s*"intel"|intel_drv\.so|\bintel\(\d+\)', xorg_log, re.I):
            return "legacy-intel-ddx"
        if re.search(r'LoadModule:\s*"modesetting"|modesetting_drv\.so|\bmodeset(?:ting)?\(\d+\)', xorg_log, re.I):
            return "modesetting"
        if re.search(r'LoadModule:\s*"(?:amdgpu|ati|radeon|nouveau|zx)"|'
                     r'(?:amdgpu|radeon|nouveau|zx)(?:_drv)?\.so|'
                     r'\b(?:amdgpu|radeon|nouveau|zx)\(\d+\)', xorg_log, re.I):
            return "native-ddx"
        return "unknown"

    def graphics_status(self):
        _pci_rc, lspci, pci_error = self._run(["lspci", "-nnk"])
        _modules_rc, modules, modules_error = self._run(["lsmod"])
        virt_rc, virt_output, _virt_error = self._run(["systemd-detect-virt", "--quiet"])
        _cmdline_rc, cmdline, _cmdline_error = self._run(["cat", "/proc/cmdline"])
        _glx_rc, glxinfo, glx_error = self._run(["glxinfo", "-B"])
        va_rc, vainfo, va_error = self._run(["vainfo", "--display", "drm"])
        block = self._gpu_block(lspci)
        model = self._gpu_model(block)
        driver_match = re.search(r"Kernel driver in use:\s*(\S+)", block, re.I)
        driver = driver_match.group(1) if driver_match else next(
            (name for name in KNOWN_DRM_DRIVERS if re.search(r"^%s\s" % re.escape(name), modules, re.M)),
            "未绑定",
        )
        renderer_match = re.search(r"OpenGL renderer string:\s*(.+)", glxinfo, re.I)
        renderer = renderer_match.group(1).strip() if renderer_match else "未检测到 Mesa 渲染器"
        virtual = virt_rc == 0
        safe_graphics = bool(re.search(
            r"(?:^|\s)nomodeset(?:\s|$)|(?:i915|amdgpu|radeon|nouveau)\.modeset=0", cmdline))
        software = bool(re.search(r"llvmpipe|softpipe|software rasterizer", renderer, re.I))
        render_paths = list(self.render_nodes())
        render_node = bool(render_paths)
        # Paths returned by the production glob always exist.  Keep a distinct
        # unknown result for mocked/raced paths rather than falsely claiming a
        # permission failure in exported diagnostics.
        render_access = None
        if render_paths and all(path.exists() for path in render_paths):
            render_access = all(self.render_access(path) for path in render_paths)
        legacy_intel_config = self._legacy_intel_config_present()
        xorg_log = self.xorg_log_reader() or ""
        xorg_backend = self._xorg_backend(legacy_intel_config, xorg_log)
        codecs = {
            "h264": self._codec_state(vainfo, [r"H264", r"H\.264"]),
            "vp9": self._codec_state(vainfo, [r"VP9"]),
            # HD 620/Gen9 lacks AV1 hardware decode; this is a capability, not a failure.
            "av1": self._codec_state(vainfo, [r"AV1"]),
        }
        # GPU compositing is a separate capability from hardware video.  A
        # Radeon, AMDGPU or Zhaoxin device with working KMS/Mesa must not lose
        # its desktop GPU merely because one browser codec is unavailable.
        desktop_rendering = bool(
            block and driver != "未绑定" and render_node and render_access is True
            and xorg_backend in {"modesetting", "native-ddx"}
            and not virtual and not safe_graphics and not software)
        # Browsers can use the codecs that a particular GPU actually exposes;
        # requiring both H.264 and VP9 incorrectly disables supported AMD and
        # older Intel paths.
        va_ok = va_rc == 0 and any(
            codecs[name] == "available" for name in ("h264", "vp9"))
        vaapi_error = "" if va_rc == 0 else (va_error or vainfo or "vainfo returned an error")
        edge_hardware_video = desktop_rendering and va_ok

        if virtual:
            state = "attention"
            recommendation = "检测到虚拟机，浏览器使用软件渲染以保证稳定。"
        elif safe_graphics:
            state = "attention"
            recommendation = "当前处于安全显卡模式；重启到普通模式后可恢复硬件视频。"
        elif software:
            state = "attention"
            recommendation = "Mesa 正在软件渲染；请检查内核显卡驱动和 render 节点。"
        elif not block:
            state = "failure"
            recommendation = "未检测到可用显卡，请导出诊断信息。"
        elif driver == "未绑定":
            state = "failure"
            recommendation = "显卡未绑定主线 DRM 驱动；请检查内核日志、固件和显卡型号。"
        elif xorg_backend == "legacy-intel-ddx":
            state = "attention"
            recommendation = "内核 i915 已绑定，但桌面 Xorg 仍检测到旧的 Ming Intel DDX 配置；请运行兼容迁移后使用 modesetting。"
        elif xorg_backend == "unknown":
            state = "attention"
            recommendation = "已检测到内核 %s，但未获得 Xorg 后端证据；请重新登录后查看诊断。" % driver
        elif render_node and render_access is False:
            state = "attention"
            recommendation = "内核 %s 与 Xorg %s 已检测到，但当前用户没有 render 节点访问权限；请确认账户属于 render 组后重新登录。" % (driver, xorg_backend)
        elif vaapi_error:
            state = "attention"
            recommendation = "内核 %s 与 Xorg %s 已检测到，但 VA-API 检查失败：%s" % (driver, xorg_backend, vaapi_error)
        elif edge_hardware_video:
            state = "normal"
            recommendation = "桌面渲染和可用的视频硬解已通过验证；缺少的编解码能力属于硬件限制。"
        elif desktop_rendering:
            state = "normal"
            recommendation = "桌面 GPU 渲染正常；浏览器将为不支持的编解码自动使用软件解码。"
        else:
            state = "attention"
            recommendation = "桌面图形路径尚未完整验证，Edge 会自动使用稳定的回退配置。"

        evidence = [value for value in (pci_error, modules_error, glx_error, va_error) if value]
        return {
            "model": model,
            "driver": driver,
            "kernel_driver": driver,
            "xorg_backend": xorg_backend,
            "xorg_log_evidence": bool(xorg_log),
            "legacy_intel_config": legacy_intel_config,
            "state": state,
            "recommendation": recommendation,
            "renderer": renderer,
            "render_node": render_node,
            "render_access": render_access,
            "desktop_rendering": desktop_rendering,
            "virtual_machine": virtual,
            "safe_graphics": safe_graphics,
            "vaapi": va_ok,
            "vaapi_error": vaapi_error,
            "codecs": codecs,
            "edge_hardware_video": edge_hardware_video,
            "evidence": evidence,
        }

    def audio_status(self):
        rc, output, error = self._run(["aplay", "-l"])
        model = output.splitlines()[0] if rc == 0 and output else "未检测到 ALSA 声卡"
        return {
            "model": model,
            "driver": "ALSA/PulseAudio",
            "state": "normal" if rc == 0 and output else "attention",
            "recommendation": "可在‘声音与通话’中测试内置麦克风和扬声器。"
            if rc == 0 and output else "未检测到声卡，请检查固件或导出诊断。",
            "evidence": [] if rc == 0 else [error],
        }

    def network_status(self):
        rc, output, error = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
        wifi = [line for line in output.splitlines() if ":wifi:" in line]
        return {
            "model": wifi[0] if wifi else "未检测到无线接口",
            "driver": "NetworkManager",
            "state": "normal" if wifi else "attention",
            "recommendation": "无线接口可用，可扫描 2.4GHz/5GHz 网络。" if wifi
            else "未检测到无线接口；虚拟机通常没有虚拟无线网卡。",
            "evidence": [] if rc == 0 else [error],
        }

    def status(self):
        devices = {
            "graphics": self.graphics_status(),
            "audio": self.audio_status(),
            "network": self.network_status(),
        }
        return {"devices": devices}

    def export(self):
        return json.dumps(self.status(), ensure_ascii=False, indent=2, sort_keys=True)


def main(argv=None, service=None, stdout=None):
    parser = argparse.ArgumentParser(prog="ming-hardware-status")
    subparsers = parser.add_subparsers(dest="action", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--json", action="store_true")
    subparsers.add_parser("export")
    args = parser.parse_args(argv)
    service = service or HardwareStatus()
    stdout = stdout or sys.stdout
    if args.action == "status":
        print(json.dumps(service.status(), ensure_ascii=False, sort_keys=True), file=stdout)
    else:
        print(service.export(), file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
