import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
LAUNCH = (ROOT / "assets" / "ming-launch.py").read_text(encoding="utf-8")


class DesktopDeliveryContracts(unittest.TestCase):
    def test_toolbox_is_seeded_to_the_localized_desktop(self):
        self.assertIn('"ming-toolbox.desktop"', FINALIZE)
        self.assertIn("ming-toolbox.desktop)", FINALIZE)

    def test_installed_cleanup_removes_calamares_from_every_desktop_seed(self):
        for marker in (
            'home/*/Desktop/calamares-install-debian.desktop',
            'home/*/桌面/calamares-install-debian.desktop',
            'etc/skel/Desktop/calamares-install-debian.desktop',
            'etc/skel/桌面/calamares-install-debian.desktop',
        ):
            self.assertIn(marker, BASE)

    def test_dock_profile_is_responsive_offset_and_no_rc3_marker(self):
        self.assertNotIn("2640-legacy-centered", DESKTOP)
        self.assertIn("Offset=12", DESKTOP)
        for marker in (
            "short_side <= 720",
            "short_side <= 900",
            "icon_size=32",
            "icon_size=36",
            "icon_size=40",
        ):
            self.assertIn(marker, DESKTOP)
        self.assertIn("ZoomPercent=148", DESKTOP)

    def test_normal_windows_reserve_workarea_but_fullscreen_and_drawer_hide_dock(self):
        self.assertIn("reserve_bottom_workarea", DESKTOP)
        self.assertIn("_NET_WM_STRUT_PARTIAL", DESKTOP)
        self.assertIn("_NET_WM_STATE_FULLSCREEN", DESKTOP)
        self.assertIn("drawer-open", DESKTOP)
        self.assertIn("_NET_WM_WINDOW_TYPE_DOCK", DESKTOP)

    def test_dock_state_is_reapplied_after_a_late_window_or_plank_restart(self):
        coordinator = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck", 1
        )[1].split("MINGSESSIONHEALTH", 2)[1]
        function = coordinator.split("apply_dock_immersive_state() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("dock_immersive_window_id", coordinator)
        self.assertIn('valid_window_id "${window_id}" || return 0', function)
        self.assertIn(
            '"${window_id}" == "${dock_immersive_window_id}"', function
        )
        invalid_guard = function.index('valid_window_id "${window_id}" || return 0')
        state_write = function.index('dock_immersive_state="${desired}"')
        self.assertLess(invalid_guard, state_write)

    def test_immersive_dock_reclears_struts_after_the_health_check(self):
        coordinator = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck", 1
        )[1].split("MINGSESSIONHEALTH", 2)[1]
        function = coordinator.split("apply_dock_immersive_state() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn('"${desired}" == normal', function)
        self.assertIn("-remove _NET_WM_STRUT", function)

    def test_fullscreen_dialog_keeps_its_transient_fullscreen_parent(self):
        coordinator = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck", 1
        )[1].split("MINGSESSIONHEALTH", 2)[1]
        self.assertIn("_NET_WM_TRANSIENT_FOR", coordinator)
        self.assertIn("transient_fullscreen_parent", coordinator)

    def test_non_transient_dialog_still_detects_fullscreen_on_current_workspace(self):
        coordinator = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck", 1
        )[1].split("MINGSESSIONHEALTH", 2)[1]
        self.assertIn("current_workspace_fullscreen_window", coordinator)
        self.assertIn("_NET_CLIENT_LIST_STACKING", coordinator)
        self.assertIn("_NET_CURRENT_DESKTOP", coordinator)
        self.assertIn("_NET_WM_DESKTOP", coordinator)
        self.assertIn("_NET_WM_STATE_HIDDEN", coordinator)

    def test_stale_drawer_pid_is_removed_before_deciding_dock_visibility(self):
        coordinator = DESKTOP.split(
            "cat > /usr/local/bin/ming-session-healthcheck", 1
        )[1].split("MINGSESSIONHEALTH", 2)[1]
        function = coordinator.split("drawer_window_visible() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn('read -r drawer_pid <"${drawer_state_file}"', function)
        self.assertIn('kill -0 "${drawer_pid}"', function)
        self.assertIn('rm -f "${drawer_state_file}"', function)

    def test_xiahai_repairs_sandbox_and_uses_bounded_compatibility_retry(self):
        installer = APPS.split("install_xiahai_xiaoming() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("chown root:root /opt/xiahai-xiaoming/chrome-sandbox", installer)
        self.assertIn("chmod 4755 /opt/xiahai-xiaoming/chrome-sandbox", installer)
        self.assertIn("xiahai", LAUNCH.lower())
        self.assertIn("--disable-gpu", LAUNCH)
        self.assertIn("--no-sandbox", LAUNCH)
        self.assertIn("sandbox", LAUNCH.lower())
        self.assertIn("retry", LAUNCH.lower())

    def test_build_identity_requires_commit_object_to_resolve(self):
        capture = BUILD.split("capture_build_identity() {", 1)[1].split("\n}", 1)[0]
        verify = BUILD.split("verify_build_identity() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("git_build cat-file -e", capture)
        self.assertIn('^{commit}', capture)
        self.assertIn("git_build cat-file -e", verify)


if __name__ == "__main__":
    unittest.main()
