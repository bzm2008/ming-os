import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


class XfceLeafTrimContractTests(unittest.TestCase):
    def test_base_image_keeps_core_runtime_but_drops_leaf_apps(self):
        install = APPS.split("install_xfce_desktop() {", 1)[1].split(
            "    apt install -y --no-install-recommends \\\n        picom", 1
        )[0]
        for package in ("xfce4-appfinder", "xfce4-whiskermenu-plugin", "xfce4-taskmanager", "mousepad", "ristretto"):
            self.assertNotRegex(install, rf"^\s+{re.escape(package)}\s*\\?$", package)
        for package in ("xfdesktop4", "xfce4-session", "xfce4-settings", "xfce4-terminal", "thunar", "xfconf"):
            self.assertIn(package, install)
        self.assertNotRegex(install, r"^\s+xfce4\s*\\?$", "Xfce meta package can pull trimmed leaves")
        self.assertNotIn("thunar-archive-plugin", install)
        self.assertNotIn("thunar-media-tags-plugin", install)

    def test_trimmed_stock_entries_are_hidden_without_removing_install_capability(self):
        self.assertIn("hide_trimmed_xfce_entries()", DESKTOP)
        function = DESKTOP.split("hide_trimmed_xfce_entries() {", 1)[1].split("\n}", 1)[0]
        for entry in (
            "xfce4-appfinder.desktop",
            "xfce4-taskmanager.desktop",
            "mousepad.desktop",
            "ristretto.desktop",
        ):
            self.assertIn(entry, function)
        self.assertIn("NoDisplay=true", function)
        self.assertNotIn("apt", function)

    def test_templates_contract_seeds_blank_document_and_preserves_new_folder(self):
        self.assertIn("/etc/skel/Templates", DESKTOP)
        self.assertIn("新建文本文档.txt", DESKTOP)
        self.assertIn("mkdir -p", DESKTOP)
        self.assertIn("configure_user_templates", DESKTOP)

    def test_thunar_admin_editor_uses_an_installed_terminal_editor(self):
        self.assertNotIn("pkexec mousepad %f", DESKTOP)
        self.assertEqual(
            2,
            DESKTOP.count(
                "xfce4-terminal --disable-server --execute /usr/local/bin/ming-authorized-action edit %f"
            ),
        )
        bridge = DESKTOP.split(
            "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'", 1
        )[1].split("\nMINGAUTHORIZE", 1)[0]
        self.assertIn("edit)", bridge)
        self.assertIn("/usr/bin/nano", bridge)

    def test_thunar_admin_open_uses_the_authorization_bridge(self):
        self.assertNotIn("pkexec thunar %f", DESKTOP)
        self.assertEqual(2, DESKTOP.count("/usr/local/bin/ming-authorized-action open %f"))
        bridge = DESKTOP.split(
            "cat > /usr/local/bin/ming-authorized-action << 'MINGAUTHORIZE'", 1
        )[1].split("\nMINGAUTHORIZE", 1)[0]
        self.assertIn("open)", bridge)
        self.assertIn("/usr/bin/thunar", bridge)


if __name__ == "__main__":
    unittest.main()
