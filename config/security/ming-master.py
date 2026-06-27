#!/usr/bin/env python3
"""Ming OS Security Manager.

A small YAD/Zenity front-end for common maintenance tasks.  It is intentionally
defensive: every action writes a log and every failure is shown to the user.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_NAME = "Ming 安全管家"
MING_USER = os.environ.get("SUDO_USER") or os.environ.get("USER") or "ming"
GARLIC_CLAW_PORT = 18789
LOG_FILE = Path("/tmp/ming-master.log")
CONFIG_DIR = Path.home() / ".config" / "ming-os"
CONFIG_FILE = CONFIG_DIR / "ming-master.json"


def append_log(title: str, body: str = "") -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n===== {title} =====\n")
        if body:
            handle.write(body.rstrip() + "\n")


def run_command(cmd: list[str] | str, shell: bool = False, timeout: int | None = None) -> tuple[int, str, str]:
    append_log("RUN", cmd if isinstance(cmd, str) else " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        append_log("STDOUT", result.stdout)
        append_log("STDERR", result.stderr)
        append_log("EXIT", str(result.returncode))
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:  # pragma: no cover - defensive UI path
        append_log("EXCEPTION", str(exc))
        return -1, "", str(exc)


def dialog_tool() -> str | None:
    for name in ("yad", "zenity"):
        path = shutil.which(name)
        if path:
            return path
    return None


def show_dialog(kind: str, title: str, text: str, width: int = 560) -> None:
    tool = dialog_tool()
    append_log(f"DIALOG {kind}", f"{title}\n{text}")
    if not tool:
        print(f"{title}\n{text}")
        return
    if Path(tool).name == "yad":
        run_command([tool, f"--{kind}", f"--title={title}", f"--text={text}", f"--width={width}"])
    else:
        zenity_kind = "info" if kind == "info" else "warning" if kind == "warning" else "error"
        run_command([tool, f"--{zenity_kind}", f"--title={title}", f"--text={text}", f"--width={width}"])


def show_info(title: str, text: str) -> None:
    show_dialog("info", title, text)


def show_warning(title: str, text: str) -> None:
    show_dialog("warning", title, text)


def show_error(title: str, text: str) -> None:
    show_dialog("error", title, text)


def show_text_file(title: str, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(content)
        temp_path = handle.name
    tool = dialog_tool()
    try:
        if tool and Path(tool).name == "yad":
            run_command([tool, "--text-info", f"--title={title}", f"--filename={temp_path}", "--width=840", "--height=620"])
        elif tool:
            run_command([tool, "--text-info", f"--title={title}", f"--filename={temp_path}", "--width=840", "--height=620"])
        else:
            print(content)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def require_tools(required: list[str]) -> bool:
    missing = [tool for tool in required if shutil.which(tool) is None]
    if not missing:
        return True
    show_warning(
        "缺少组件",
        "以下组件暂不可用：\n"
        + "\n".join(f"- {item}" for item in missing)
        + f"\n\n详细日志：{LOG_FILE}",
    )
    return False


def quick_clean() -> None:
    if not require_tools(["bleachbit"]):
        return
    code, out, err = run_command(["sudo", "-n", "bleachbit", "--clean", "system.cache", "system.trash", "system.tmp"])
    if code == 0:
        show_info("清理完成", "系统缓存和临时文件已清理。")
    else:
        show_error("清理失败", f"请查看日志：{LOG_FILE}\n\n{(err or out)[-900:]}")


def security_check() -> None:
    results: list[str] = []
    if shutil.which("rkhunter"):
        code, out, err = run_command(["sudo", "-n", "rkhunter", "--check", "--skip-keypress", "--rwo"], timeout=600)
        results.append("=== Rootkit 检测 (rkhunter) ===\n" + (out or err or f"退出码：{code}"))
    else:
        results.append("=== Rootkit 检测 (rkhunter) ===\n未安装。")

    if shutil.which("lynis"):
        code, out, err = run_command(["sudo", "-n", "lynis", "audit", "system", "--quiet"], timeout=900)
        results.append("=== 系统安全审计 (lynis) ===\n" + (out or err or f"退出码：{code}"))
    else:
        results.append("=== 系统安全审计 (lynis) ===\n未安装。")

    show_text_file("安全检查结果", "\n\n".join(results))


def firewall_status() -> None:
    if not require_tools(["nft"]):
        return
    code, out, err = run_command(["sudo", "-n", "nft", "list", "ruleset"])
    if code == 0:
        show_text_file("防火墙规则", out or "当前没有 nftables 规则。")
    else:
        show_error("获取失败", f"无法读取防火墙规则。\n\n{(err or out)[-900:]}\n\n日志：{LOG_FILE}")


def garlic_claw_status() -> None:
    status_info: list[str] = []
    code, out, _err = run_command(["systemctl", "--user", "is-active", "openclaw-gateway"])
    status_info.append("Gateway 服务：" + ("运行中" if code == 0 and out.strip() == "active" else "未运行"))

    code, out, _err = run_command(f"ss -tuln | grep ':{GARLIC_CLAW_PORT} '", shell=True)
    status_info.append(f"端口 {GARLIC_CLAW_PORT}：" + ("正在监听" if code == 0 else "未监听"))

    config_file = Path.home() / ".openclaw" / "config.json"
    if config_file.exists():
        try:
            provider = json.loads(config_file.read_text(encoding="utf-8")).get("provider", "未设置")
            status_info.append(f"AI 提供商：{provider}")
        except Exception as exc:
            status_info.append(f"AI 配置读取失败：{exc}")
    else:
        status_info.append("AI 提供商：未配置")

    show_info("Garlic Claw 状态", "\n".join(status_info))


def main_menu() -> None:
    tool = dialog_tool()
    if not tool:
        print("This tool needs yad or zenity.")
        return

    if Path(tool).name == "yad":
        menu_cmd: list[str] | str = [
            tool,
            "--list",
            f"--title={APP_NAME}",
            "--text=请选择要执行的操作：",
            "--column=操作",
            "--column=说明",
            "快速清理",
            "清理系统缓存和临时文件",
            "安全检查",
            "执行 Rootkit 检测和系统安全审计",
            "防火墙状态",
            "查看当前 nftables 防火墙规则",
            "Garlic Claw 状态",
            "查看 AI 助手服务运行状态",
            "--width=680",
            "--height=430",
            "--button=退出:1",
            "--button=执行:0",
        ]
    else:
        menu_cmd = [
            tool,
            "--list",
            f"--title={APP_NAME}",
            "--text=请选择要执行的操作：",
            "--column=操作",
            "--column=说明",
            "快速清理",
            "清理系统缓存和临时文件",
            "安全检查",
            "执行 Rootkit 检测和系统安全审计",
            "防火墙状态",
            "查看当前 nftables 防火墙规则",
            "Garlic Claw 状态",
            "查看 AI 助手服务运行状态",
            "--width=680",
            "--height=430",
        ]

    actions = {
        "快速清理": quick_clean,
        "安全检查": security_check,
        "防火墙状态": firewall_status,
        "Garlic Claw 状态": garlic_claw_status,
    }

    while True:
        code, out, err = run_command(menu_cmd)
        if code != 0:
            if err:
                append_log("MENU CLOSED", err)
            break
        selection = out.strip().split("|")[0] if out else ""
        action = actions.get(selection)
        if action:
            action()


def main() -> int:
    LOG_FILE.write_text("", encoding="utf-8")
    append_log("START", APP_NAME)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    missing = [tool for tool in ("yad", "rkhunter", "chkrootkit", "lynis", "bleachbit", "nft") if shutil.which(tool) is None]
    if missing:
        show_warning("部分功能不可用", "缺少组件：\n" + "\n".join(f"- {item}" for item in missing))
    main_menu()
    append_log("END", APP_NAME)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - top-level safety net
        append_log("FATAL", repr(exc))
        show_error("安全管家启动失败", f"{exc}\n\n日志：{LOG_FILE}")
        raise
