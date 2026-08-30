import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
STORE_UI_PATH = ROOT / "assets" / "ming-store.py"
DESKTOP_PATH = ROOT / "modules" / "03_desktop.sh"
DRAWER_PATH = ROOT / "assets" / "ming-app-drawer.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Rc4RemainingRepairTests(unittest.TestCase):
    def test_default_catalog_uses_deployed_catalog_root(self):
        """A deployed core must find catalogs installed outside its script dir."""
        core = load_module("ming_store_core_remaining_repairs", CORE_PATH)
        with tempfile.TemporaryDirectory() as tempdir:
            deployed_root = pathlib.Path(tempdir) / "catalog"
            deployed_root.mkdir()
            with mock.patch.object(core, "SYSTEM_CATALOG_ROOT", deployed_root), \
                    mock.patch.object(core, "__file__", str(pathlib.Path(tempdir) / "ming-store-core.py")):
                self.assertEqual(deployed_root, core._catalog_root())

    def test_store_results_are_a_multi_column_grid(self):
        source = STORE_UI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("results.set_min_children_per_line(1)", source)
        self.assertRegex(source, r"results\.set_min_children_per_line\([2-9][0-9]*\)")
        self.assertIn("child.set_halign(Gtk.Align.START)", source)
        self.assertIn("child.set_hexpand(False)", source)
        self.assertIn("card.set_hexpand(False)", source)

    def test_active_icon_theme_is_ming_mint_in_xsettings_template(self):
        source = DESKTOP_PATH.read_text(encoding="utf-8")
        start = source.index('cat > "/home/${MING_USER}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml"')
        body_start = source.index("\n", start) + 1
        template = source[body_start:source.index("\nXSETTINGSCFG", body_start)]
        self.assertIn('<property name="IconThemeName" type="string" value="Ming-Mint"/>', template)

    def test_dconf_dock_fallback_keeps_smart_hide(self):
        source = DESKTOP_PATH.read_text(encoding="utf-8")
        self.assertIn('dconf write /net/launchpad/plank/docks/dock1/hide-mode "${hide_mode:-1}"', source)
        self.assertNotIn('hide-mode "${hide_mode:-0}"', source)

    def test_drawer_filters_unlisted_xfce_namespace_entries(self):
        drawer = load_module("ming_app_drawer_remaining_repairs", DRAWER_PATH)
        for basename in (
            "xfdesktop-settings.desktop",
            "xfwm4-settings.desktop",
            "exo-preferred-applications.desktop",
        ):
            with self.subTest(basename=basename):
                self.assertTrue(drawer.is_legacy_system_entry(pathlib.Path(basename)))

    def test_managed_user_launcher_cannot_shadow_canonical_system_launcher(self):
        drawer = load_module("ming_app_drawer_canonical_precedence", DRAWER_PATH)
        with tempfile.TemporaryDirectory() as tempdir:
            root = pathlib.Path(tempdir)
            system = root / "system"
            user = root / "user"
            system.mkdir()
            user.mkdir()
            canonical = system / "ming-store.desktop"
            shadow = user / "ming-store.desktop"
            canonical.write_text(
                "[Desktop Entry]\nType=Application\nName=Ming Store\nExec=/usr/bin/ming-store\n",
                encoding="utf-8",
            )
            shadow.write_text(
                "[Desktop Entry]\nType=Application\nName=Old Store\nExec=/usr/bin/old-store\n"
                "X-Ming-Managed=true\n",
                encoding="utf-8",
            )
            self.assertTrue(drawer.is_managed_desktop_copy(shadow))
            self.assertTrue(drawer.canonical_copy_should_win(shadow, canonical))


if __name__ == "__main__":
    unittest.main()
