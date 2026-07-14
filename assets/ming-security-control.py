#!/usr/bin/env python3
"""Privileged Ming OS security policy controller."""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile


STATE_PATH = pathlib.Path("/etc/ming-security/control.json")
RULES_PATH = pathlib.Path("/etc/nftables.conf")
DEFAULT_STATE = {
    "firewall": True,
    "profile": "public",
    "ssh": False,
    "security_updates": True,
}


def run_command(command, input_text=None):
    try:
        result = subprocess.run(
            command, input=input_text, capture_output=True, text=True,
            errors="replace", timeout=20)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def load_state(path=STATE_PATH):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    state = dict(DEFAULT_STATE)
    state.update({key: value[key] for key in state if key in value})
    if state["profile"] not in {"home", "public"}:
        state["profile"] = "public"
    return state


def save_state(state, path=STATE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".control-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def save_rules_atomic(content, path=RULES_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".nftables-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def firewall_rules(state):
    policy = "drop" if state["firewall"] else "accept"
    ssh_rules = ""
    if state["firewall"] and state["ssh"]:
        ssh_rules = """
        ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } tcp dport 22 accept
        ip6 saddr { fc00::/7, fe80::/10 } tcp dport 22 accept"""
    discovery_rules = ""
    if state["firewall"] and state["profile"] == "home":
        discovery_rules = """
        ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 } udp dport 5353 accept
        ip6 saddr { fc00::/7, fe80::/10 } udp dport 5353 accept
        ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } udp dport 1900 accept"""
    return """#!/usr/sbin/nft -f
flush ruleset
table inet ming_filter {
    chain input {
        type filter hook input priority filter; policy %s;
        iifname lo accept
        ct state established,related accept
        ct state invalid drop
        ip protocol icmp accept
        ip6 nexthdr icmpv6 accept
        udp sport 67-68 udp dport 67-68 accept
        tcp dport 18789 ip saddr 127.0.0.1 accept
        tcp dport 18789 ip6 saddr ::1 accept%s%s
    }
    chain forward { type filter hook forward priority filter; policy drop; }
    chain output { type filter hook output priority filter; policy accept; }
}
""" % (policy, ssh_rules, discovery_rules)


def apply_firewall_atomic(candidate, runner=run_command):
    snapshot_rc, snapshot, snapshot_error = runner(["nft", "list", "ruleset"])
    if snapshot_rc != 0:
        return {"ok": False, "rolled_back": False,
                "error": snapshot_error or "unable to snapshot firewall"}
    check_rc, _output, check_error = runner(["nft", "-c", "-f", "-"], candidate)
    if check_rc != 0:
        return {"ok": False, "rolled_back": False,
                "error": check_error or "firewall validation failed"}
    apply_rc, _output, apply_error = runner(["nft", "-f", "-"], candidate)
    if apply_rc == 0:
        return {"ok": True, "rolled_back": False, "error": ""}
    rollback = "flush ruleset\n" + snapshot.lstrip()
    rollback_rc, _output, rollback_error = runner(["nft", "-f", "-"], rollback)
    return {
        "ok": False,
        "rolled_back": rollback_rc == 0,
        "error": apply_error or "firewall commit failed",
        "rollback_error": "" if rollback_rc == 0 else rollback_error,
    }


def _probe(runner, command, expected=None):
    rc, output, _error = runner(command)
    return rc == 0 and (expected is None or output.strip() == expected)


def runtime_probes(runner=run_command):
    return {
        "ssh_installed": _probe(runner, ["systemctl", "list-unit-files", "ssh.service"]),
        "ssh_enabled": _probe(runner, ["systemctl", "is-enabled", "ssh.service"], "enabled"),
        "ssh_active": _probe(runner, ["systemctl", "is-active", "ssh.service"], "active"),
        "ssh_firewall_allowed": False,
    }


def build_status(state=None, probes=None):
    state = dict(DEFAULT_STATE if state is None else state)
    probes = probes or {}
    return {
        "ok": True,
        "firewall": bool(state["firewall"]),
        "profile": state["profile"],
        "security_updates": bool(state["security_updates"]),
        "ssh": {
            "installed": bool(probes.get("ssh_installed")),
            "enabled": bool(probes.get("ssh_enabled")),
            "active": bool(probes.get("ssh_active")),
            "firewall_allowed": bool(probes.get("ssh_firewall_allowed", state["ssh"])),
        },
    }


def status(path=STATE_PATH, runner=run_command):
    state = load_state(path)
    probes = runtime_probes(runner)
    probes["ssh_firewall_allowed"] = state["firewall"] and state["ssh"]
    return build_status(state, probes)


def configure_sshd(enabled, runner=run_command):
    if enabled:
        dropin = pathlib.Path("/etc/ssh/sshd_config.d/60-ming-security.conf")
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "PermitRootLogin no\nPermitEmptyPasswords no\nPasswordAuthentication yes\n",
            encoding="utf-8")
        rc, _out, error = runner(["systemctl", "enable", "--now", "ssh.service"])
    else:
        rc, _out, error = runner(["systemctl", "disable", "--now", "ssh.service"])
    return rc == 0, error


def configure_updates(enabled, runner=run_command):
    command = ["systemctl", "enable" if enabled else "disable", "--now",
               "unattended-upgrades.service"]
    rc, _out, error = runner(command)
    return rc == 0, error


def mutate(kind, value, path=STATE_PATH, rules_path=RULES_PATH, runner=run_command):
    state = load_state(path)
    if kind == "profile" and value not in {"home", "public"}:
        return {"ok": False, "error": "invalid profile"}
    if kind != "profile" and value not in {"on", "off"}:
        return {"ok": False, "error": "expected on or off"}
    desired = value if kind == "profile" else value == "on"
    old = dict(state)
    state[kind.replace("security-updates", "security_updates")] = desired
    candidate = firewall_rules(state)
    result = apply_firewall_atomic(candidate, runner=runner)
    if not result["ok"]:
        return result
    if kind == "ssh":
        ok, error = configure_sshd(desired, runner=runner)
    elif kind == "security-updates":
        ok, error = configure_updates(desired, runner=runner)
    else:
        ok, error = True, ""
    if not ok:
        if kind == "ssh":
            configure_sshd(old["ssh"], runner=runner)
        elif kind == "security-updates":
            configure_updates(old["security_updates"], runner=runner)
        apply_firewall_atomic(firewall_rules(old), runner=runner)
        return {"ok": False, "error": error or "service update failed", "rolled_back": True}
    try:
        save_rules_atomic(candidate, rules_path)
        save_state(state, path)
    except OSError as exc:
        if kind == "ssh":
            configure_sshd(old["ssh"], runner=runner)
        elif kind == "security-updates":
            configure_updates(old["security_updates"], runner=runner)
        apply_firewall_atomic(firewall_rules(old), runner=runner)
        try:
            save_rules_atomic(firewall_rules(old), rules_path)
        except OSError:
            pass
        return {"ok": False, "error": "unable to persist firewall: %s" % exc,
                "rolled_back": True}
    return status(path, runner=runner)


def quick_check(path=STATE_PATH, runner=run_command):
    current = status(path, runner=runner)
    sshd_rc, sshd_output, _sshd_error = runner(["sshd", "-T"])
    sshd_values = {}
    if sshd_rc == 0:
        for line in sshd_output.splitlines():
            key, _space, value = line.partition(" ")
            sshd_values[key.strip().lower()] = value.strip().lower()
    root_rc, root_output, _root_error = runner(["passwd", "-S", "root"])
    root_fields = root_output.split()
    checks = {
        "firewall_enabled": current["firewall"],
        "public_default": current["profile"] in {"home", "public"},
        "root_login_disabled": sshd_values.get("permitrootlogin") == "no",
        "empty_passwords_disabled": sshd_values.get("permitemptypasswords") == "no",
        "root_account_locked": root_rc == 0 and len(root_fields) > 1 and root_fields[1] in {"L", "LK"},
        "security_updates_enabled": current["security_updates"],
    }
    return {"ok": all(checks.values()), "checks": checks}


def build_parser():
    parser = argparse.ArgumentParser(prog="ming-security-control")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "quick-check"):
        command = sub.add_parser(action)
        command.add_argument("--json", action="store_true")
    for action, choices in (
            ("firewall", ("on", "off")), ("profile", ("home", "public")),
            ("ssh", ("on", "off")), ("security-updates", ("on", "off"))):
        command = sub.add_parser(action)
        command.add_argument("value", choices=choices)
    return parser


def main(argv=None, stdout=None):
    args = build_parser().parse_args(argv)
    stdout = stdout or sys.stdout
    if args.action == "status":
        result = status()
    elif args.action == "quick-check":
        result = quick_check()
    elif os.geteuid() != 0:
        result = {"ok": False, "error": "authorization required"}
    else:
        result = mutate(args.action, args.value)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
