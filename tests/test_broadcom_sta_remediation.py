import ast
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
SETTINGS_TREE = ast.parse(SETTINGS)


def shell_function(source, name):
    match = re.search(r"^%s\(\) \{" % re.escape(name), source, re.MULTILINE)
    if not match:
        raise AssertionError("missing shell function: %s" % name)
    next_header = source.find("\n# ========================", match.end())
    return source[match.start():next_header if next_header >= 0 else len(source)]


def python_function(name, class_name=None):
    nodes = SETTINGS_TREE.body
    if class_name:
        nodes = next(node.body for node in nodes
                     if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(node for node in nodes
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)
    return ast.get_source_segment(SETTINGS, node)


class BroadcomStaRemediationContracts(unittest.TestCase):
    def test_base_system_does_not_ship_sta_dkms_cache_or_manager(self):
        installer = shell_function(BASE, "install_base_packages")

        dkms_package_line = r"(?m)^[ \t]+dkms[ \t]*\\$"
        self.assertRegex("        dkms \\", dkms_package_line)
        self.assertNotRegex(installer, dkms_package_line)
        self.assertNotIn("cache_broadcom_sta_driver", BASE)
        self.assertNotIn("deploy_broadcom_driver_manager", BASE)
        self.assertNotIn("apt-get download broadcom-sta-dkms", BASE)
        self.assertNotIn("cat > /usr/local/sbin/ming-broadcom-driver", BASE)
        self.assertNotIn("dkms status", BASE)
        driver = BASE[BASE.index("cat > /usr/local/bin/ming-driver-diagnose"):]
        self.assertIn("mokutil --sb-state", driver)
        self.assertNotIn("dkms status", driver)
        self.assertIn("dpkg --purge --force-all broadcom-sta-dkms dkms", installer)
        self.assertIn("rm -rf /usr/share/ming-os/driver-cache/broadcom", installer)
        self.assertIn("rm -rf /usr/src/broadcom-sta*", installer)
        self.assertIn("/var/cache/apt/archives/broadcom-sta-dkms_*.deb", installer)
        self.assertIn("/var/cache/apt/archives/dkms_*.deb", installer)
        self.assertIn("rm -f /usr/local/sbin/ming-broadcom-driver", installer)
        self.assertIn("firmware-b43-installer firmware-b43legacy-installer", installer)
        for package in [
                "wireless-regdb", "bluez-firmware", "firmware-mediatek",
                "firmware-libertas", "firmware-misc-nonfree", "firmware-iwlwifi",
                "firmware-realtek", "firmware-atheros", "firmware-brcm80211"]:
            self.assertIn(package, BASE)

    def test_rootfs_gate_rejects_sta_dkms_and_stale_broadcom_artifacts(self):
        rootfs_gate = BUILD.split("validate_r4_compatibility() {", 1)[1]

        self.assertNotIn("Broadcom offline cache must contain", rootfs_gate)
        self.assertNotIn("broadcom_manager = require_file", rootfs_gate)
        self.assertIn('re.split(r"\\n\\s*\\n", dpkg_status)', rootfs_gate)
        self.assertNotIn("(?ms)^Package:", rootfs_gate)
        for marker in [
                "broadcom-sta-dkms", "dkms",
                "usr/share/ming-os/driver-cache/broadcom",
                "usr/local/sbin/ming-broadcom-driver",
                "var/lib/dkms", "broadcom-sta*", "usr/src", "rootfs_glob",
                "var/cache/apt/archives",
                "updates/dkms/wl.*"]:
            with self.subTest(marker=marker):
                self.assertIn(marker, rootfs_gate)
        self.assertIn("must not", rootfs_gate)

    def test_settings_uses_async_read_only_compatibility_help(self):
        self.assertIn("def read_compatibility_help_snapshot", SETTINGS)
        snapshot = python_function("read_compatibility_help_snapshot")
        hardware_snapshot = python_function("hardware_status_snapshot")
        hardware_probe = python_function("hardware_probe_snapshot")
        build_hardware = python_function("build_hardware", "MingSettings")
        action = python_function("on_compatibility_help", "MingSettings")

        self.assertIn('["ming-device-control", "compatibility-help"]', snapshot)
        self.assertNotIn("pkexec", snapshot)
        self.assertNotIn("install", snapshot.lower())
        self.assertNotIn("download", snapshot.lower())
        self.assertIn('if "ok" in result', snapshot)
        self.assertIn("read_compatibility_help_snapshot", hardware_snapshot)
        self.assertIn("read_compatibility_help_snapshot", hardware_probe)
        self.assertIn("on_compatibility_help", build_hardware)
        self.assertIn("不下载或安装", build_hardware)
        self.assertIn("self.compatibility_probe_state", SETTINGS)
        self.assertIn("self.compatibility_probe_active", SETTINGS)
        self.assertNotIn("pkexec", action)
        self.assertIn("self.compatibility_probe_state.accept(generation)", action)
        self.assertNotIn("install", action.lower())
        self.assertNotIn("download", action.lower())
        self.assertNotIn("ming-broadcom-driver", SETTINGS)
        self.assertNotIn("broadcom-sta", SETTINGS.lower())
        self.assertNotIn("dkms", SETTINGS.lower())


if __name__ == "__main__":
    unittest.main()
