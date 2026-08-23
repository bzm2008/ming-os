import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")


def function_body(source, name):
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start:end]


class XiahaiIntegrationContracts(unittest.TestCase):
    def test_xiahai_receipt_is_machine_readable(self):
        receipt = ROOT / "assets" / "vendor" / "xiahai-xiaoming" / "receipt.json"
        metadata = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual("xiahai-xiaoming", metadata["package"])
        self.assertEqual("0.0.2~beta", metadata["version"])
        self.assertEqual("amd64", metadata["architecture"])

    def test_apps_module_validates_xiahai_before_installing(self):
        installer = function_body(APPS, "install_xiahai_xiaoming")
        for marker in (
            "xiahai-xiaoming_0.0.2-beta_amd64.deb",
            "dpkg-deb --info",
            "dpkg-deb --contents",
            'dpkg-deb -f "${deb}" Package',
            'dpkg-deb -f "${deb}" Version',
            'dpkg-deb -f "${deb}" Architecture',
            "xiahai-xiaoming.desktop",
            "/opt/xiahai-xiaoming/xiahai-xiaoming",
        ):
            self.assertIn(marker, installer)
        self.assertIn("corrupt or incomplete", installer)

    def test_xiahai_is_a_required_apps_phase(self):
        main = function_body(APPS, "main")
        self.assertIn("run_required_step install_xiahai_xiaoming || return 1", main)
        self.assertLess(main.index("install_required_desktop_runtime"), main.index("install_xiahai_xiaoming"))
        self.assertLess(main.index("install_xiahai_xiaoming"), main.index("install_app_store"))

    def test_rootfs_gate_requires_xiahai_runtime_entrypoints(self):
        for marker in (
            "opt/xiahai-xiaoming/xiahai-xiaoming",
            "usr/share/applications/xiahai-xiaoming.desktop",
            "usr/share/icons/hicolor/128x128/apps/xiahai-xiaoming.png",
            "Exec=/opt/xiahai-xiaoming/xiahai-xiaoming",
            "Icon=xiahai-xiaoming",
        ):
            self.assertIn(marker, BUILD)

    def test_xiahai_replaces_papyrus_in_the_visible_default_entrypoints(self):
        for source in (DESKTOP, FINALIZE, BUILD):
            self.assertIn("xiahai-xiaoming.desktop", source)
        self.assertNotIn("papyrus.desktop", FINALIZE)
        self.assertNotIn("papyrus.desktop", BUILD)

    def test_old_papyrus_is_only_a_layout_compatibility_alias(self):
        phone = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
        drawer = (ROOT / "assets" / "ming-app-drawer.py").read_text(encoding="utf-8")
        self.assertIn('"papyrus.desktop": "agent"', phone)
        self.assertIn('"papyrus.desktop": "agent"', drawer)
        self.assertIn('"agent": "xiahai-xiaoming.desktop"', phone)


if __name__ == "__main__":
    unittest.main()
