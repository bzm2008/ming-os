import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "modules" / "03_desktop.sh"
BUILD = ROOT / "build_onion_os.sh"


def generated_block(source, marker):
    start = source.index(marker)
    body_start = source.index("\n", start) + 1
    end = source.index("\nMINGANDROIDROOT", body_start)
    return source[body_start:end]


class AndroidDependencyInstallContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = DESKTOP.read_text(encoding="utf-8")
        cls.helper = generated_block(cls.source, "cat > /usr/local/sbin/ming-android-runtime << 'MINGANDROIDROOT'")
        cls.policy = cls.source.split(
            "cat > /usr/share/polkit-1/actions/org.ming.android.runtime.policy << 'MINGANDROIDPOLICY'",
            1,
        )[1].split("MINGANDROIDPOLICY", 1)[0]

    def test_install_deps_is_limited_to_debian_trixie_and_validates_architecture(self):
        self.assertIn('VERSION_CODENAME:-}" == "trixie"', self.helper)
        self.assertIn("dpkg --print-architecture", self.helper)
        self.assertIn('amd64', self.helper)
        self.assertIn("repo.waydro.id", self.helper)

    def test_install_deps_uses_pinned_waydroid_key_material(self):
        self.assertRegex(self.helper, r"waydroid\.gpg")
        self.assertRegex(self.helper, r"71FE05D735C812E15FE229BF10106B02B62561BE8AA5280D63A58E25A5C0C5E2")
        self.assertIn("signed-by=${key_file}", self.helper)
        self.assertIn('-f "${key_file}"', self.helper)
        self.assertIn('! -L "${key_file}"', self.helper)

    def test_waydroid_repository_is_pinned_away_from_debian_dependencies(self):
        self.assertIn("ming-waydroid.pref", self.helper)
        self.assertIn("Package: waydroid", self.helper)
        self.assertIn("Package: *", self.helper)
        self.assertIn("Pin-Priority: -1", self.helper)
        self.assertIn("backup_preferences", self.helper)

    def test_install_deps_installs_android_tools_from_deterministic_packages(self):
        self.assertRegex(self.helper, r"install[^\n]*\bwaydroid\b")
        self.assertRegex(self.helper, r"\bcage\b")
        self.assertRegex(self.helper, r"\blxc\b")
        self.assertTrue("aapt" in self.helper or "apkanalyzer" in self.helper)
        self.assertIn("dpkg-query -W", self.helper)

    def test_install_deps_restores_sources_and_key_on_update_or_install_failure(self):
        self.assertIn("trap", self.helper)
        self.assertIn("restore", self.helper)
        self.assertIn("apt-get", self.helper)
        self.assertIn(" update", self.helper)
        self.assertIn("apt-get -y", self.helper)

    def test_android_polkit_disallows_any_and_inactive_callers(self):
        self.assertIn("<allow_any>no</allow_any>", self.policy)
        self.assertIn("<allow_inactive>no</allow_inactive>", self.policy)

    def test_android_helper_has_no_shell_interpolation_or_remote_pipe_execution(self):
        self.assertNotIn("eval ", self.helper)
        self.assertNotIn("sh -c", self.helper)
        self.assertNotRegex(self.helper, r"curl[^\n]*\|[^\n]*sh")

    def test_build_gate_checks_android_dependency_trust_contract(self):
        build = BUILD.read_text(encoding="utf-8")
        for marker in (
            "repo.waydro.id",
            "ming-waydroid.gpg",
            "71FE05D735C812E15FE229BF10106B02B62561BE8AA5280D63A58E25A5C0C5E2",
            "Pin-Priority: -1",
            "allow_inactive>no",
        ):
            self.assertIn(marker, build)


if __name__ == "__main__":
    unittest.main()
