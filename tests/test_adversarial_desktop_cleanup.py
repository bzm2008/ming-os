import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
DRAWER = (ROOT / "assets" / "ming-app-drawer.py").read_text(encoding="utf-8")
FILES = (ROOT / "assets" / "ming-files.py").read_text(encoding="utf-8")
MODEL = (ROOT / "assets" / "ming-files-model.py").read_text(encoding="utf-8")


def heredoc(source, opener, marker):
    return source.split(opener, 1)[1].split(marker, 1)[0]


def last_heredoc(source, opener, marker):
    prefix, separator, tail = source.rpartition(opener)
    if not separator:
        raise AssertionError("missing heredoc opener: %s" % opener)
    return tail.split(marker, 1)[0]


class AdversarialDesktopCleanupTests(unittest.TestCase):
    def test_active_plank_profiles_are_centered_without_horizontal_offset(self):
        for opener, marker in (
            ('cat > "${plank_dir}/settings" << \'PLANKSETTINGS\'', "PLANKSETTINGS"),
            ("cat >\"${settings}\" << 'PLANKRUNTIMESETTINGS'", "PLANKRUNTIMESETTINGS"),
        ):
            profile = heredoc(DESKTOP, opener, marker)
            active_offsets = re.findall(r"(?m)^Offset=(\d+)$", profile)
            self.assertTrue(active_offsets)
            self.assertTrue(all(value == "0" for value in active_offsets), profile)
        self.assertIn("offset=0", DESKTOP)
        self.assertIn('offset "${offset:-0}"', DESKTOP)

    def test_retired_custom_dock_can_never_be_reenabled(self):
        dock = last_heredoc(DESKTOP, "cat > /usr/local/bin/ming-dock << 'MINGDOCKRETIRED'", "MINGDOCKRETIRED")
        watchdog = heredoc(DESKTOP, "cat > /usr/local/bin/ming-dock-watchdog << 'MINGDOCKWATCH'", "MINGDOCKWATCH")
        self.assertNotIn("import gi", dock)
        self.assertIn("exit 0", dock)
        self.assertNotIn("MING_USE_LEGACY_MING_DOCK", watchdog)
        self.assertIn("exit 0", watchdog)

    def test_retired_dock_is_inert_before_any_legacy_payload_is_emitted(self):
        preseed = DESKTOP.index("cat > /usr/local/bin/ming-dock << 'MINGDOCKPRESEED'")
        legacy = DESKTOP.index("cat > /tmp/ming-dock-legacy << 'MINGDOCK'")
        final = DESKTOP.index("cat > /usr/local/bin/ming-dock << 'MINGDOCKRETIRED'")
        self.assertLess(preseed, legacy)
        self.assertLess(legacy, final)
        self.assertIn("chmod 0600 /tmp/ming-dock-legacy", DESKTOP)
        self.assertIn("rm -f /tmp/ming-dock-legacy", DESKTOP)

    def test_legacy_status_and_library_entries_are_non_display_wrappers(self):
        status = last_heredoc(DESKTOP, "cat > /usr/local/bin/ming-status-center << 'MINGSTATUSCOMPAT'", "MINGSTATUSCOMPAT")
        library = last_heredoc(DESKTOP, "cat > /usr/local/bin/ming-app-library << 'MINGAPPLIBCOMPAT'", "MINGAPPLIBCOMPAT")
        self.assertNotIn("import gi", status)
        self.assertNotIn("import gi", library)
        self.assertIn("ming-status-widget-toggle", status)
        self.assertIn("ming-app-drawer", library)
        entry = heredoc(DESKTOP, "cat > /usr/share/applications/ming-status-center.desktop << 'STATUSAPP'", "STATUSAPP")
        self.assertIn("NoDisplay=true", entry)

    def test_retired_status_and_library_are_preseeded_before_legacy_payload(self):
        for name, legacy_opener, final_opener in (
            ("status", "cat > /tmp/ming-status-center-legacy << 'STATUSCENTER'", "cat > /usr/local/bin/ming-status-center << 'MINGSTATUSCOMPAT'"),
            ("library", "cat > /tmp/ming-app-library-legacy << 'APPLIB'", "cat > /usr/local/bin/ming-app-library << 'MINGAPPLIBCOMPAT'"),
        ):
            with self.subTest(name=name):
                preseed = DESKTOP.index("MINGSTATUSPRESEED" if name == "status" else "MINGAPPLIBPRESEED")
                legacy = DESKTOP.index(legacy_opener)
                final = DESKTOP.index(final_opener)
                self.assertLess(preseed, legacy)
                self.assertLess(legacy, final)
        self.assertIn("chmod 0600 /tmp/ming-status-center-legacy", DESKTOP)
        self.assertIn("chmod 0600 /tmp/ming-app-library-legacy", DESKTOP)

    def test_drawer_filters_retired_xfce_utility_entries(self):
        self.assertIn("LEGACY_XFCE_LAUNCHERS", DRAWER)
        self.assertIn("xfce4-appfinder.desktop", DRAWER)
        self.assertIn("xfce4-taskmanager.desktop", DRAWER)
        self.assertIn("is_legacy_system_entry", DRAWER)
        self.assertIn("if is_legacy_system_entry(path)", DRAWER)

    def test_file_manager_has_blank_area_context_actions_and_file_creation(self):
        self.assertIn("_attach_background_gestures", FILES)
        self.assertIn("_show_background_menu", FILES)
        self.assertIn("新建空白文件", FILES)
        self.assertIn("create_file", FILES)
        self.assertIn("def create_file", MODEL)


if __name__ == "__main__":
    unittest.main()
