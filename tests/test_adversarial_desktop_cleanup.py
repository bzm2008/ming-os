import pathlib
import re
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
DRAWER = (ROOT / "assets" / "ming-app-drawer.py").read_text(encoding="utf-8")
FILES = (ROOT / "assets" / "ming-files.py").read_text(encoding="utf-8")
MODEL = (ROOT / "assets" / "ming-files-model.py").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")


def load_drawer_module():
    import importlib.util

    path = ROOT / "assets" / "ming-app-drawer.py"
    spec = importlib.util.spec_from_file_location("ming_app_drawer_adversarial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def heredoc(source, opener, marker):
    return source.split(opener, 1)[1].split(marker, 1)[0]


def last_heredoc(source, opener, marker):
    prefix, separator, tail = source.rpartition(opener)
    if not separator:
        raise AssertionError("missing heredoc opener: %s" % opener)
    return tail.split(marker, 1)[0]


class AdversarialDesktopCleanupTests(unittest.TestCase):
    def test_phone_desktop_hides_retired_xfce_utilities_from_new_layouts(self):
        self.assertIn("LEGACY_XFCE_LAUNCHERS", PHONE)
        self.assertIn("def is_legacy_xfce_entry", PHONE)
        self.assertIn("if is_legacy_xfce_entry(path)", PHONE)
        drawer = load_drawer_module()
        for basename in (
            "xfce4-appfinder.desktop",
            "xfce4-taskmanager.desktop",
            "xfce4-power-manager-settings.desktop",
            "xfce4-appearance-settings.desktop",
        ):
            self.assertTrue(drawer.is_legacy_system_entry(pathlib.Path(basename)))

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
        self.assertIn("self._attach_background_gestures(self.list_view)", FILES)
        self.assertIn("self._attach_background_gestures(self.grid_view)", FILES)

    def test_drawer_hides_all_retired_xfce_namespace_entries(self):
        drawer = load_drawer_module()
        for basename in (
            "xfce-ui-settings.desktop",
            "xfce-wm-settings.desktop",
            "xfce-wmtweaks-settings.desktop",
            "xfce-workspaces-settings.desktop",
            "xfce4-appfinder.desktop",
            "xfce4-taskmanager.desktop",
        ):
            with self.subTest(basename=basename):
                self.assertTrue(drawer.is_legacy_system_entry(pathlib.Path(basename)))
        self.assertFalse(
            drawer.is_legacy_system_entry(pathlib.Path("xfce4-terminal.desktop"))
        )

    def test_drawer_deduplicates_same_exec_and_startup_class(self):
        drawer = load_drawer_module()
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            for basename, name in (("vendor.desktop", "Vendor"), ("vendor-alias.desktop", "Vendor Alias")):
                (root / basename).write_text(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={name}\n"
                    "Exec=/bin/true --desktop\n"
                    "StartupWMClass=Vendor.Main\n"
                    "Categories=Utility;\n",
                    encoding="utf-8",
                )
            apps = drawer.discover_apps(paths=[root])
            self.assertEqual(1, len(apps), apps)

    def test_drawer_ignores_desktop_directory_even_when_explicitly_scanned(self):
        drawer = load_drawer_module()
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            desktop = home / "Desktop"
            apps = home / ".local" / "share" / "applications"
            desktop.mkdir(parents=True)
            apps.mkdir(parents=True)
            for target, name in ((desktop / "desktop-entry.desktop", "Desktop Entry"), (apps / "system-entry.desktop", "System Entry")):
                target.write_text(
                    "[Desktop Entry]\nType=Application\n"
                    f"Name={name}\nExec=/bin/true\nCategories=Utility;\n",
                    encoding="utf-8",
                )
            original_home = drawer.pathlib.Path.home
            drawer.pathlib.Path.home = staticmethod(lambda: home)
            try:
                found = drawer.discover_apps(paths=[desktop, apps])
            finally:
                drawer.pathlib.Path.home = original_home
            self.assertEqual(["system-entry.desktop"], [item.path.name for item in found])

    def test_finalize_removes_only_zero_byte_calamares_entries(self):
        self.assertIn("-size 0", FINALIZE)
        self.assertIn("calamares", FINALIZE.lower())
        self.assertIn("zero-byte", FINALIZE.lower())

    def test_finalize_main_calls_zero_byte_calamares_cleanup(self):
        call = "cleanup_zero_byte_calamares_entries || return 1"
        self.assertIn(call, FINALIZE)
        self.assertLess(
            FINALIZE.index(call),
            FINALIZE.index("retire_legacy_store_runtime || return 1"),
        )

    def test_desktop_autostart_entries_use_fixed_managed_scripts(self):
        """Autostart files must not compose commands through a shell string."""
        self.assertNotIn("Exec=sh -c", DESKTOP)
        for script in (
            "ming-migrate-all-disks",
            "ming-dock-only-init",
            "ming-desktop-organizer-session",
            "ming-onboard-defaults",
        ):
            self.assertIn(f"/usr/local/bin/{script}", DESKTOP)
            self.assertRegex(
                DESKTOP,
                rf"chmod (?:\+x|0755) /usr/local/bin/{re.escape(script)}",
            )

    def test_base_autostart_entries_do_not_compose_shell_commands(self):
        """Base-session entries must use fixed argv scripts, never sh -c."""
        self.assertNotIn("Exec=sh -c", BASE)
        self.assertIn("Exec=/usr/local/bin/ming-volume-automount --session --json", BASE)
        self.assertIn("Exec=/usr/local/bin/ming-classic-mode --session", BASE)


if __name__ == "__main__":
    unittest.main()
