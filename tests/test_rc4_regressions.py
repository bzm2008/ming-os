import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
import os
from io import BytesIO


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
SETTINGS = (ROOT / "assets" / "ming-settings.py").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
STORE = (ROOT / "assets" / "ming-store.py").read_text(encoding="utf-8")
STORE_CONTROL = (ROOT / "assets" / "ming-store-control.py").read_text(encoding="utf-8")


class StoreAuthorizationContracts(unittest.TestCase):
    def test_store_policy_uses_one_scoped_helper_and_disables_nonactive_callers(self):
        policy = DESKTOP.split(
            "cat > /usr/share/polkit-1/actions/org.mingos.store.manage.policy << 'MINGSTOREPOLICY'",
            1,
        )[1].split("MINGSTOREPOLICY", 1)[0]
        self.assertIn("/usr/local/sbin/ming-store-control", policy)
        self.assertIn("<allow_any>no</allow_any>", policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", policy)
        self.assertIn("<allow_active>auth_admin_keep</allow_active>", policy)

    def test_store_ui_passes_only_action_and_request_id_to_authorization(self):
        self.assertIn('"/usr/local/bin/ming-authorized-action", "store", action', STORE)
        self.assertIn("REQUEST_ID.fullmatch(request_id)", STORE_CONTROL)
        self.assertNotIn("shell=True", STORE + STORE_CONTROL)
        self.assertNotIn("eval ", STORE + STORE_CONTROL)


class WifiDialogContracts(unittest.TestCase):
    def test_password_dialog_uses_compatibility_constructors_and_fallback_actions(self):
        connect = SETTINGS.split(
            "    def on_wifi_connect(self, _btn, network):", 1
        )[1].split("    def apply_wifi_connect_result", 1)[0]
        self.assertIn("MessageDialog.new", connect)
        self.assertIn("Gtk.PasswordEntry()", connect)
        self.assertIn("set_show_peek_icon", connect)
        self.assertIn("打开网络设置", connect)
        self.assertIn("重新扫描", connect)


class AptSourceContracts(unittest.TestCase):
    def test_runtime_source_selector_and_full_upgrade_convergence_are_deployed(self):
        self.assertIn("ming-apt-source-select", BASE)
        self.assertIn("apt-get full-upgrade", BASE)
        self.assertIn("InRelease", BASE)
        self.assertIn("deb.debian.org", BASE)
        self.assertIn("MING_DEBIAN_MIRROR", BUILD)


class DiagnosticsAndRc4Contracts(unittest.TestCase):
    @staticmethod
    def _run_diagnostic_validator(archive, destination):
        uploader = BASE.split(
            "cat > /usr/local/bin/ming-diagnostic-upload << 'DIAGUPLOAD'", 1
        )[1].split("DIAGUPLOAD", 1)[0]
        validator = uploader.split("<<'PY'", 1)[1]
        validator = validator.split("\n", 1)[1].split("\nPY\nthen", 1)[0]
        original_argv = sys.argv
        try:
            sys.argv = ["validator", str(archive), str(destination), "128", str(4 * 1024 * 1024), str(5 * 1024 * 1024)]
            exec(compile(validator, "diagnostic-validator", "exec"), {"__name__": "__main__"})
        finally:
            sys.argv = original_argv

    def test_diagnostic_validator_rejects_links_and_rewrites_regular_text_without_secrets(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = pathlib.Path(tempdir)
            archive = tempdir / "unsafe.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                link = tarfile.TarInfo("logs/linked.txt")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/shadow"
                output.addfile(link)
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, tempdir / "linked-output")

            archive = tempdir / "regular.tar.gz"
            payload = (
                b"password=hunter2\npassword hunter2\n"
                b"Authorization: Bearer SUPERSECRET\n"
                b"SSID=My Home Network\n"
                b"route via 192.168.31.88 dev wlan0\n"
                b"peer AA:BB:CC:DD:EE:FF\n"
                b"/home/alice/token=private\n"
            )
            with tarfile.open(archive, "w:gz") as output:
                item = tarfile.TarInfo("logs/runtime.txt")
                item.size = len(payload)
                output.addfile(item, BytesIO(payload))
            output_dir = tempdir / "regular-output"
            output_dir.mkdir()
            self._run_diagnostic_validator(archive, output_dir)
            content = (output_dir / "file-001.txt").read_text(encoding="utf-8")
            self.assertNotIn("hunter2", content)
            self.assertNotIn("SUPERSECRET", content)
            self.assertNotIn("My Home Network", content)
            self.assertNotIn("192.168.31.88", content)
            self.assertNotIn("AA:BB:CC:DD:EE:FF", content)
            self.assertNotIn("alice", content)
            self.assertNotIn("private", content)

    def test_diagnostic_bundle_shell_redaction_removes_multitoken_and_network_secrets(self):
        bash = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        if not bash.is_file():
            self.skipTest("Git Bash is unavailable")
        bundle = BASE.split(
            "cat > /usr/local/bin/ming-diagnostic-bundle << 'DIAGBUNDLE'", 1
        )[1].split("DIAGBUNDLE", 1)[0]
        function = bundle.split("stage_text_file() {", 1)[1].split("\n}", 1)[0]
        function = "stage_text_file() {" + function + "\n}"
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            script = root / "redact.sh"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            iconv = bin_dir / "iconv"
            iconv.write_text("#!/usr/bin/env bash\ncat\n", encoding="utf-8", newline="\n")
            iconv.chmod(0o755)
            source = root / "source.txt"
            source.write_text(
                "Authorization: Bearer SUPERSECRET\nSSID=My Home Network\n"
                "route 192.168.31.88 AA:BB:CC:DD:EE:FF\n",
                encoding="utf-8",
            )
            work = root / "work"
            work.mkdir()
            git_path = lambda path: "/%s%s" % (
                str(path.resolve()).replace("\\", "/")[0].lower(),
                str(path.resolve()).replace("\\", "/")[2:],
            )
            script.write_text(
                "#!/usr/bin/env bash\nset -uxo pipefail\n"
                "WORK='%s'\nSOURCE='%s'\nMAX_FILE_BYTES=$((4 * 1024 * 1024))\n"
                % (git_path(work), git_path(source))
                + "install() { mkdir -p \"${@: -1}\"; }\n"
                + "MAX_FILES=128\nMAX_TOTAL_BYTES=$((5 * 1024 * 1024))\n"
                "staged_files=0\nstaged_total=0\n"
                + function
                + "\nstage_text_file \"$SOURCE\" logs/result.txt\n"
                "cat \"$WORK/logs/result.txt\"\n",
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run(
                [str(bash), git_path(script)], capture_output=True, check=False,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                env={**os.environ, "PATH": git_path(bin_dir) + ":/usr/bin:/bin"},
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            content = completed.stdout
        for secret in ("SUPERSECRET", "My Home Network", "192.168.31.88", "AA:BB:CC:DD:EE:FF"):
            self.assertNotIn(secret, content)

    def test_diagnostic_validator_rejects_traversal_nested_binary_and_invalid_gzip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = pathlib.Path(tempdir)
            invalid = tempdir / "not-gzip.tar.gz"
            invalid.write_bytes(b"not gzip")
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(invalid, tempdir / "invalid-output")

            archive = tempdir / "malicious.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                traversal = tarfile.TarInfo("../escape.txt")
                traversal.size = 4
                output.addfile(traversal, BytesIO(b"text"))
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, tempdir / "traversal-output")

            archive = tempdir / "nested.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                nested = tarfile.TarInfo("logs/inner.zip")
                nested.size = 4
                output.addfile(nested, BytesIO(b"text"))
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, tempdir / "nested-output")

            archive = tempdir / "nested-magic.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                nested = tarfile.TarInfo("logs/runtime.txt")
                nested.size = 4
                output.addfile(nested, BytesIO(b"PK\x03\x04"))
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, tempdir / "nested-magic-output")

            archive = tempdir / "binary.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                binary = tarfile.TarInfo("logs/binary.txt")
                binary.size = 3
                output.addfile(binary, BytesIO(b"a\x00b"))
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, tempdir / "binary-output")

    def test_diagnostic_validator_enforces_member_and_total_limits(self):
        with tempfile.TemporaryDirectory() as tempdir:
            tempdir = pathlib.Path(tempdir)
            archive = tempdir / "too-many.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                for index in range(129):
                    item = tarfile.TarInfo("logs/%03d.txt" % index)
                    item.size = 1
                    output.addfile(item, BytesIO(b"x"))
            too_many_output = tempdir / "too-many-output"
            too_many_output.mkdir()
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, too_many_output)

            archive = tempdir / "too-large.tar.gz"
            payload = b"x" * (4 * 1024 * 1024 + 1)
            with tarfile.open(archive, "w:gz") as output:
                item = tarfile.TarInfo("logs/large.txt")
                item.size = len(payload)
                output.addfile(item, BytesIO(payload))
            too_large_output = tempdir / "too-large-output"
            too_large_output.mkdir()
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, too_large_output)

            archive = tempdir / "too-much.tar.gz"
            payload = b"x" * (2 * 1024 * 1024)
            with tarfile.open(archive, "w:gz") as output:
                for index in range(3):
                    item = tarfile.TarInfo("logs/chunk-%d.txt" % index)
                    item.size = len(payload)
                    output.addfile(item, BytesIO(payload))
            too_much_output = tempdir / "too-much-output"
            too_much_output.mkdir()
            with self.assertRaises(ValueError):
                self._run_diagnostic_validator(archive, too_much_output)


    def test_diagnostic_uploader_and_manual_confirmation_are_present(self):
        self.assertIn("ming-diagnostic-upload", BASE)
        self.assertIn("手动确认", SETTINGS)
        self.assertIn("ming.diagnostic.v1", BASE)

    def test_diagnostic_bundle_stages_text_only_and_upload_validates_archive_before_extracting(self):
        """Client diagnostics must never unpack or forward unsafe tar members."""
        bundle = BASE.split(
            "cat > /usr/local/bin/ming-diagnostic-bundle << 'DIAGBUNDLE'", 1
        )[1].split("DIAGBUNDLE", 1)[0]
        uploader = BASE.split(
            "cat > /usr/local/bin/ming-diagnostic-upload << 'DIAGUPLOAD'", 1
        )[1].split("DIAGUPLOAD", 1)[0]

        for marker in (
            "MAX_FILES=128",
            "MAX_FILE_BYTES=$((4 * 1024 * 1024))",
            "MAX_TOTAL_BYTES=$((5 * 1024 * 1024))",
            "MAX_ARCHIVE_BYTES=$((8 * 1024 * 1024))",
            'WORK="$(mktemp -d',
            'RAW_WORK="$(mktemp -d',
            'ARCHIVE="$(mktemp',
            "cleanup_diagnostic_bundle",
            "trap cleanup_diagnostic_bundle EXIT",
            "iconv -f UTF-8 -t UTF-8",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "stage_text_file",
            "stat -c '%h'",
            "! -L",
            "sed -E -i",
            "<redacted>",
        ):
            self.assertIn(marker, bundle)

        for marker in (
            "tarfile.open",
            "member.isreg()",
            "member.issym()",
            "member.islnk()",
            "member.isdir()",
            'mode="r:gz"',
            'raw.read(2) != b"\\x1f\\x8b"',
            "redact",
            'work="$(mktemp -d',
            'safe_archive="$(mktemp',
            "无法建立安全诊断",
        ):
            self.assertIn(marker, uploader)
        self.assertNotIn('tar -xzf "${archive}"', uploader)

    def test_build_is_rc4_and_requires_verified_xiahai_asset(self):
        self.assertIn('readonly MING_OS_BUILD_SUFFIX="rc4"', BUILD)
        self.assertIn("MING_XIAHAI_DEB_SOURCE", BUILD)
        self.assertIn("xiahai-xiaoming_0.0.2-beta_amd64.deb", BUILD)
        self.assertIn("dpkg-deb --info", BUILD)
        self.assertIn("sha256", BUILD.lower())


if __name__ == "__main__":
    unittest.main()
