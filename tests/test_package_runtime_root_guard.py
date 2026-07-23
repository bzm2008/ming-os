import os
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD = ROOT / "assets" / "ming-package-runtime-root-guard.sh"
WSL = ["wsl.exe", "-d", "Ubuntu", "--cd", "/", "--"]


def wsl_path(value):
    path = pathlib.Path(value).resolve()
    drive = path.drive.rstrip(":").lower()
    return "/mnt/%s/%s" % (drive, path.as_posix().split(":/", 1)[1])


class PackageRuntimeRootGuardTests(unittest.TestCase):
    def test_guard_validates_the_filesystem_root_before_components(self):
        source = GUARD.read_text(encoding="utf-8")
        self.assertIn('validate_parent "/"', source)
        self.assertLess(
            source.index('validate_parent "/"'),
            source.index('IFS=/ read -r -a components'),
        )

    def run_harness(self, body):
        if os.name == "nt":
            if not shutil.which("wsl.exe"):
                self.skipTest("WSL is unavailable")
            command = [*WSL, "bash", "-s", "--", wsl_path(GUARD)]
        else:
            if not hasattr(os, "geteuid") or os.geteuid() != 0:
                self.skipTest("root privileges are required for ownership checks")
            command = ["bash", "-s", "--", str(GUARD)]
        script = (
            "set -euo pipefail\n"
            "guard=$1\n"
            "base=$(mktemp -d /run/ming-runtime-root-test.XXXXXX)\n"
            "trap 'rm -rf -- \"$base\"' EXIT\n"
            "chmod 0755 \"$base\"\n"
            + body
        )
        return subprocess.run(
            command,
            input=script.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=60,
        )

    def test_creates_an_exact_root_owned_0755_runtime_directory(self):
        completed = self.run_harness(
            "runtime=$base/usr/local/lib/ming-os/package-installer-runtimes\n"
            "bash \"$guard\" \"$runtime\"\n"
            "test \"$(stat -c '%a:%u:%g' \"$runtime\")\" = 755:0:0\n"
            "test ! -L \"$runtime\"\n"
        )

        self.assertEqual(0, completed.returncode, completed.stderr.decode(errors="replace"))

    def test_rejects_symlink_writable_and_non_root_runtime_parents(self):
        cases = {
            "symlink": (
                "mkdir -p \"$base/usr/local/lib/ming-os\" \"$base/escape\"\n"
                "ln -s \"$base/escape\" \"$base/usr/local/lib/ming-os/package-installer-runtimes\"\n"
            ),
            "writable": (
                "mkdir -p \"$base/usr/local/lib\"\n"
                "chmod 0777 \"$base/usr/local/lib\"\n"
            ),
            "non-root": (
                "mkdir -p \"$base/usr/local/lib\"\n"
                "chown 65534:65534 \"$base/usr/local/lib\"\n"
            ),
        }
        for label, setup in cases.items():
            with self.subTest(case=label):
                completed = self.run_harness(
                    setup
                    + "runtime=$base/usr/local/lib/ming-os/package-installer-runtimes\n"
                    + "if bash \"$guard\" \"$runtime\"; then exit 91; fi\n"
                )
                self.assertEqual(
                    0, completed.returncode,
                    completed.stderr.decode(errors="replace"),
                )


if __name__ == "__main__":
    unittest.main()
