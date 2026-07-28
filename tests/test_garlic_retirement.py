import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class GarlicRetirementContracts(unittest.TestCase):
    def test_retired_garlic_build_scripts_are_removed(self):
        self.assertFalse((ROOT / "modules" / "04_garlic_claw.sh").exists())
        self.assertFalse((ROOT / "complete_garlic_claw.sh").exists())

    def test_runtime_build_surfaces_do_not_install_garlic_or_openclaw(self):
        paths = (
            ROOT / "build_onion_os.sh",
            ROOT / "resume_build.sh",
            ROOT / "continue_build.sh",
            ROOT / "modules" / "03_desktop.sh",
            ROOT / "modules" / "07_finalize.sh",
            ROOT / "assets" / "ming-phone-desktop.py",
            ROOT / "config" / "security" / "ming-master.py",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8").lower()
            with self.subTest(path=path.name):
                self.assertNotIn("garlic-claw", source)
                self.assertNotIn("garlic claw", source)
                self.assertNotIn("openclaw", source)

    def test_desktop_cleanup_removes_retired_agent_files_from_reused_rootfs(self):
        desktop = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8").lower()
        for marker in (
            "for retired_root in /usr/local/bin",
            'find "${retired_root}"',
            "-iname '*claw*'",
            "-iname 'open*claw*'",
            "/usr/share/icons",
        ):
            self.assertIn(marker, desktop)
