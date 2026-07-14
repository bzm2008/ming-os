#!/usr/bin/env python3
"""Privileged Ming OS security policy controller."""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


STATE_PATH = pathlib.Path("/etc/ming-security/control.json")
RULES_PATH = pathlib.Path("/etc/nftables.conf")
APT_AUTO_UPGRADES_PATH = pathlib.Path("/etc/apt/apt.conf.d/20auto-upgrades")
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
        return result.returncode, result.stdout.rstrip("\r\n"), result.stderr.strip()
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


def save_text_atomic(content, path, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
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
destroy table inet ming_filter
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
    snapshot_rc, snapshot, snapshot_error = runner(
        ["nft", "list", "table", "inet", "ming_filter"])
    if snapshot_rc != 0:
        return {"ok": False, "rolled_back": False,
                "error": snapshot_error or "unable to snapshot firewall"}
    check_rc, _output, check_error = runner(["nft", "-c", "-f", "-"], candidate)
    if check_rc != 0:
        return {"ok": False, "rolled_back": False,
                "error": check_error or "firewall validation failed"}
    apply_rc, _output, apply_error = runner(["nft", "-f", "-"], candidate)
    if apply_rc == 0:
        return {"ok": True, "rolled_back": False, "error": "", "snapshot": snapshot}
    rollback = "destroy table inet ming_filter\n" + snapshot.lstrip()
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


def apt_periodic_config(enabled):
    value = "1" if enabled else "0"
    return (
        'APT::Periodic::Enable "%s";\n' % value
        + 'APT::Periodic::Update-Package-Lists "%s";\n' % value
        + 'APT::Periodic::Unattended-Upgrade "%s";\n' % value
    )


def apt_periodic_enabled(path=APT_AUTO_UPGRADES_PATH):
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(
        re.search(r'APT::Periodic::Enable\s+"1"\s*;', content)
        and re.search(r'APT::Periodic::Unattended-Upgrade\s+"1"\s*;', content))


def update_probes(runner=run_command, apt_path=APT_AUTO_UPGRADES_PATH):
    return {
        "updates_configured": apt_periodic_enabled(apt_path),
        "updates_enabled": _probe(
            runner, ["systemctl", "is-enabled", "apt-daily-upgrade.timer"], "enabled"),
        "updates_active": _probe(
            runner, ["systemctl", "is-active", "apt-daily-upgrade.timer"], "active"),
    }


def runtime_probes(runner=run_command, apt_path=APT_AUTO_UPGRADES_PATH):
    nft_rc, nft_output, _nft_error = runner(
        ["nft", "list", "table", "inet", "ming_filter"])
    policy_match = re.search(r"\bpolicy\s+(drop|accept)\s*;", nft_output)
    ssh_rc, ssh_output, _ssh_error = runner(
        ["dpkg-query", "-W", "-f=${db:Status-Abbrev}", "openssh-server"])
    probes = {
        "ssh_installed": ssh_rc == 0 and ssh_output == "ii ",
        "ssh_enabled": _probe(runner, ["systemctl", "is-enabled", "ssh.service"], "enabled"),
        "ssh_active": _probe(runner, ["systemctl", "is-active", "ssh.service"], "active"),
        "ssh_firewall_allowed": nft_rc == 0 and "tcp dport 22 accept" in nft_output,
        "nftables_enabled": _probe(
            runner, ["systemctl", "is-enabled", "nftables.service"], "enabled"),
        "nftables_active": _probe(
            runner, ["systemctl", "is-active", "nftables.service"], "active"),
        "nft_rules_loaded": nft_rc == 0,
        "nft_policy": policy_match.group(1) if policy_match else "unknown",
        "effective_profile": (
            "home" if nft_rc == 0 and "udp dport 5353" in nft_output
            else "public" if nft_rc == 0 else "unknown"),
    }
    probes.update(update_probes(runner, apt_path))
    return probes


def build_status(state=None, probes=None):
    state = dict(DEFAULT_STATE if state is None else state)
    probes = probes or {}
    firewall_configured = bool(state["firewall"])
    nft_policy = probes.get("nft_policy", "unknown")
    firewall_effective = bool(
        firewall_configured and probes.get("nftables_active")
        and probes.get("nft_rules_loaded") and nft_policy == "drop")
    updates_configured = bool(
        probes.get("updates_configured", state["security_updates"]))
    updates_effective = bool(
        updates_configured and probes.get("updates_enabled") and probes.get("updates_active"))
    return {
        "ok": True,
        "firewall": {
            "configured": firewall_configured,
            "service_enabled": bool(probes.get("nftables_enabled")),
            "service_active": bool(probes.get("nftables_active")),
            "rules_loaded": bool(probes.get("nft_rules_loaded")),
            "policy": nft_policy,
            "effective": firewall_effective,
        },
        "profile": {
            "configured": state["profile"],
            "effective": probes.get("effective_profile", "unknown"),
        },
        "security_updates": {
            "configured": updates_configured,
            "enabled": bool(probes.get("updates_enabled")),
            "active": bool(probes.get("updates_active")),
            "effective": updates_effective,
        },
        "ssh": {
            "installed": bool(probes.get("ssh_installed")),
            "enabled": bool(probes.get("ssh_enabled")),
            "active": bool(probes.get("ssh_active")),
            "firewall_allowed": bool(probes.get("ssh_firewall_allowed", state["ssh"])),
        },
    }


def status(path=STATE_PATH, runner=run_command, apt_path=APT_AUTO_UPGRADES_PATH):
    state = load_state(path)
    probes = runtime_probes(runner, apt_path)
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


def configure_updates(enabled, runner=run_command, apt_path=APT_AUTO_UPGRADES_PATH):
    try:
        save_text_atomic(apt_periodic_config(enabled), apt_path)
    except OSError as exc:
        return False, str(exc)
    command = ["systemctl", "enable" if enabled else "disable", "--now",
               "apt-daily-upgrade.timer"]
    rc, _out, error = runner(command)
    return rc == 0, error


def restore_file(snapshot, path, mode):
    existed, content = snapshot
    if existed:
        save_text_atomic(content, path, mode)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def file_snapshot(path):
    try:
        return True, path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, ""


def rollback_configuration(old, kind, rules_path, runner, persist_rules=False,
                           apt_path=APT_AUTO_UPGRADES_PATH, apt_snapshot=None,
                           updates_enabled=None, state_path=None, state_snapshot=None,
                           nft_snapshot=None, rules_snapshot=None):
    errors = []
    if kind == "ssh":
        service_ok, service_error = configure_sshd(old["ssh"], runner=runner)
        if not service_ok:
            errors.append("ssh service: %s" % (service_error or "restore failed"))
    if kind == "security-updates":
        command = ["systemctl", "enable" if updates_enabled else "disable", "--now",
                   "apt-daily-upgrade.timer"]
        rc, _output, service_error = runner(command)
        if rc != 0:
            errors.append("security updates: %s" % (service_error or "restore failed"))
        if apt_snapshot is not None:
            try:
                restore_file(apt_snapshot, apt_path, 0o644)
            except OSError as exc:
                errors.append("APT configuration: %s" % exc)
    else:
        restore_rules = (
            "destroy table inet ming_filter\n" + nft_snapshot.lstrip()
            if nft_snapshot is not None else firewall_rules(old))
        firewall_result = apply_firewall_atomic(restore_rules, runner=runner)
        if not firewall_result.get("ok"):
            errors.append("firewall: %s" % (
                firewall_result.get("error") or "restore failed"))
        if persist_rules:
            try:
                if rules_snapshot is not None:
                    restore_file(rules_snapshot, rules_path, 0o600)
                else:
                    save_rules_atomic(firewall_rules(old), rules_path)
            except OSError as exc:
                errors.append("rules file: %s" % exc)
    if state_path is not None and state_snapshot is not None:
        try:
            restore_file(state_snapshot, state_path, 0o600)
        except OSError as exc:
            errors.append("state file: %s" % exc)
    return {"ok": not errors, "error": "; ".join(errors)}


def desired_matches(kind, desired, observed):
    if kind == "firewall":
        firewall = observed["firewall"]
        if desired:
            return firewall["effective"]
        return bool(
            firewall["service_active"] and firewall["rules_loaded"]
            and firewall["policy"] == "accept")
    if kind == "profile":
        profile = observed["profile"]
        firewall = observed["firewall"]
        return profile["configured"] == desired and (
            not firewall["configured"] or profile["effective"] == desired)
    if kind == "ssh":
        ssh = observed["ssh"]
        if desired:
            return bool(ssh["enabled"] and ssh["active"] and ssh["firewall_allowed"])
        return not any((ssh["enabled"], ssh["active"], ssh["firewall_allowed"]))
    if kind == "security-updates":
        updates = observed["security_updates"]
        active = bool(updates["configured"] and updates["enabled"] and updates["active"])
        return active if desired else not any(
            (updates["configured"], updates["enabled"], updates["active"]))
    return False


def update_status(state, runner, apt_path):
    probes = update_probes(runner, apt_path)
    return build_status(state, probes)


def mutate(kind, value, path=STATE_PATH, rules_path=RULES_PATH,
           apt_path=APT_AUTO_UPGRADES_PATH, runner=run_command):
    state = load_state(path)
    if kind == "profile" and value not in {"home", "public"}:
        return {"ok": False, "error": "invalid profile"}
    if kind != "profile" and value not in {"on", "off"}:
        return {"ok": False, "error": "expected on or off"}
    desired = value if kind == "profile" else value == "on"
    old = dict(state)
    state_snapshot = file_snapshot(path)
    apt_snapshot = file_snapshot(apt_path) if kind == "security-updates" else None
    rules_snapshot = file_snapshot(rules_path) if kind != "security-updates" else None
    old_updates_enabled = None
    nft_snapshot = None
    if kind == "security-updates":
        old_updates_enabled = update_probes(runner, apt_path)["updates_enabled"]
    state[kind.replace("security-updates", "security_updates")] = desired
    candidate = None
    if kind != "security-updates":
        candidate = firewall_rules(state)
        result = apply_firewall_atomic(candidate, runner=runner)
        if not result["ok"]:
            return result
        nft_snapshot = result["snapshot"]
    if kind == "ssh":
        ok, error = configure_sshd(desired, runner=runner)
    elif kind == "security-updates":
        ok, error = configure_updates(desired, runner=runner, apt_path=apt_path)
    else:
        ok, error = True, ""
    if not ok:
        rollback = rollback_configuration(
            old, kind, rules_path, runner, apt_path=apt_path,
            apt_snapshot=apt_snapshot, updates_enabled=old_updates_enabled,
            state_path=path, state_snapshot=state_snapshot,
            nft_snapshot=nft_snapshot, rules_snapshot=rules_snapshot)
        message = error or "service update failed"
        if not rollback["ok"]:
            message += "; rollback failed: " + rollback["error"]
        return {"ok": False, "error": message, "rolled_back": rollback["ok"],
                "rollback_error": rollback["error"]}
    try:
        if candidate is not None:
            save_rules_atomic(candidate, rules_path)
        save_state(state, path)
    except OSError as exc:
        rollback = rollback_configuration(
            old, kind, rules_path, runner, persist_rules=candidate is not None,
            apt_path=apt_path, apt_snapshot=apt_snapshot,
            updates_enabled=old_updates_enabled, state_path=path,
            state_snapshot=state_snapshot, nft_snapshot=nft_snapshot,
            rules_snapshot=rules_snapshot)
        message = "unable to persist security state: %s" % exc
        if not rollback["ok"]:
            message += "; rollback failed: " + rollback["error"]
        return {"ok": False, "error": message, "rolled_back": rollback["ok"],
                "rollback_error": rollback["error"]}
    observed = (update_status(state, runner, apt_path) if kind == "security-updates"
                else status(path, runner=runner, apt_path=apt_path))
    if not desired_matches(kind, desired, observed):
        rollback = rollback_configuration(
            old, kind, rules_path, runner, persist_rules=candidate is not None,
            apt_path=apt_path, apt_snapshot=apt_snapshot,
            updates_enabled=old_updates_enabled, state_path=path,
            state_snapshot=state_snapshot, nft_snapshot=nft_snapshot,
            rules_snapshot=rules_snapshot)
        message = "readback mismatch after applying %s" % kind
        if not rollback["ok"]:
            message += "; rollback failed: " + rollback["error"]
        return {"ok": False, "error": message, "rolled_back": rollback["ok"],
                "rollback_error": rollback["error"]}
    return observed


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
        "firewall_enabled": current["firewall"]["effective"],
        "public_default": (
            current["profile"]["configured"] == "public"
            and current["profile"]["effective"] == "public"),
        "root_login_disabled": sshd_values.get("permitrootlogin") == "no",
        "empty_passwords_disabled": sshd_values.get("permitemptypasswords") == "no",
        "root_account_locked": root_rc == 0 and len(root_fields) > 1 and root_fields[1] in {"L", "LK"},
        "security_updates_enabled": current["security_updates"]["effective"],
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
