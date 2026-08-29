import importlib.util
import gzip
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
UI_PATH = ROOT / "assets" / "ming-store.py"
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")


def load_core():
    spec = importlib.util.spec_from_file_location("ming_store_core_appstream", CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_ui():
    spec = importlib.util.spec_from_file_location("ming_store_ui_appstream", UI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingStoreAppStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load_core()

    def appstream_xml(self, count=1):
        components = []
        for index in range(count):
            components.append(
                """
                <component type="desktop-application">
                  <id>org.example.App{0}.desktop</id>
                  <name>Example App {0}</name>
                  <summary>Verified application {0}</summary>
                  <pkgname>example-app-{0}</pkgname>
                  <launchable type="desktop-id">org.example.App{0}.desktop</launchable>
                  <categories><category>Utility</category></categories>
                  <project_license>GPL-3.0-or-later</project_license>
                </component>
                """.format(index)
            )
        return "<components>" + "".join(components) + "</components>"

    def test_appstream_parser_builds_installable_amd64_items(self):
        items = self.core.parse_appstream_xml(self.appstream_xml())
        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("example-app-0", item["package_name"])
        self.assertEqual(["amd64"], item["architectures"])
        self.assertEqual("apt", item["install_method"])
        self.assertEqual(["org.example.App0.desktop"], item["desktop_ids"])
        self.assertEqual("apt-repository-signature", item["identity"]["type"])

    def test_debian_provider_uses_appstream_when_metadata_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "components.xml"
            path.write_text(self.appstream_xml(1001), encoding="utf-8")
            provider = self.core.DebianAptProvider(
                catalog_root=ROOT / "assets" / "ming-store-catalog",
                appstream_paths=[path],
            )
            items = provider.refresh_catalog()
        self.assertGreaterEqual(len(items), 1001)
        self.assertEqual(
            len({item["package_name"] for item in items}), len(items),
        )

    def test_rootfs_appstream_inventory_scans_real_metadata_and_deduplicates_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            metainfo = root / "usr" / "share" / "metainfo"
            cache = root / "var" / "cache" / "app-info" / "xmls"
            metainfo.mkdir(parents=True)
            cache.mkdir(parents=True)
            metainfo.joinpath("one.xml").write_text(
                self.appstream_xml(2), encoding="utf-8",
            )
            # The second file repeats one package and adds one new package.
            duplicate = self.appstream_xml(1).replace(
                "Example App 0", "Duplicate Example 0",
            )
            with gzip.open(cache / "two.xml.gz", "wt", encoding="utf-8") as stream:
                stream.write(duplicate)
            inventory = self.core.scan_appstream_rootfs(root)

        self.assertEqual(2, inventory["count"])
        self.assertEqual(2, len(inventory["paths"]))
        self.assertGreater(inventory["metadata_bytes"], 0)
        self.assertEqual(
            {"example-app-0", "example-app-1"},
            {item["package_name"] for item in inventory["items"]},
        )

    def test_rootfs_appstream_inventory_ignores_catalog_json_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            metainfo = root / "usr" / "share" / "metainfo"
            catalog = root / "usr" / "share" / "ming-os" / "store" / "catalog"
            metainfo.mkdir(parents=True)
            catalog.mkdir(parents=True)
            catalog.joinpath("debian-apt.json").write_text(
                json.dumps({"applications": [{"package_name": "fake"}]}) + "\n",
                encoding="utf-8",
            )
            source = metainfo / "source.xml"
            source.write_text(self.appstream_xml(), encoding="utf-8")
            (metainfo / "link.xml").symlink_to(source)
            inventory = self.core.scan_appstream_rootfs(root)

        self.assertEqual(1, inventory["count"])
        self.assertEqual(1, len(inventory["paths"]))

    def test_rootfs_appstream_gate_requires_real_metadata_and_1000_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with self.assertRaises(self.core.InvalidCatalog):
                self.core.validate_appstream_rootfs(root)

            metainfo = root / "usr" / "share" / "metainfo"
            metainfo.mkdir(parents=True)
            metainfo.joinpath("components.xml").write_text(
                self.appstream_xml(999), encoding="utf-8",
            )
            with self.assertRaises(self.core.InvalidCatalog):
                self.core.validate_appstream_rootfs(root)

            metainfo.joinpath("components.xml").write_text(
                self.appstream_xml(1000), encoding="utf-8",
            )
            inventory = self.core.validate_appstream_rootfs(root)
            self.assertEqual(1000, inventory["count"])

    def test_appstream_rejects_non_desktop_and_non_linux_architecture(self):
        xml = """
        <components>
          <component type="console-application"><id>console</id><name>Console</name><pkgname>console</pkgname></component>
          <component type="desktop-application"><id>arm</id><name>Arm</name><pkgname>arm</pkgname><architecture>arm64</architecture></component>
        </components>
        """
        self.assertEqual([], self.core.parse_appstream_xml(xml))

    def test_domestic_mirror_candidates_include_fast_china_sources_and_official_fallback(self):
        self.assertIn("mirrors.aliyun.com/debian", BASE)
        self.assertIn("mirrors.ustc.edu.cn/debian", BASE)
        self.assertIn("mirrors.tuna.tsinghua.edu.cn/debian", BASE)
        self.assertIn("deb.debian.org/debian", BASE)
        self.assertIn("InRelease", BASE)

    def test_target_image_installs_appstream_metadata_runtime(self):
        self.assertRegex(BASE, r"\bappstream\b")
        self.assertNotRegex(BASE, r"\bappstream-data\b")
        self.assertIn("appstreamcli refresh-cache --force", BASE)

    def test_build_gate_checks_actual_rootfs_appstream_inventory(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("scan_appstream_rootfs(root)", build)
        self.assertIn("module.validate_appstream_rootfs(", build)
        self.assertIn("MIN_ROOTFS_APPSTREAM_APPS", build)
        self.assertIn("rootfs AppStream metadata", build)

    def test_large_catalog_inventory_is_bounded_but_remains_searchable(self):
        class Catalog:
            def __init__(self):
                self.items = [
                    {
                        "app_id": "app-%d" % index,
                        "package_name": "app-%d" % index,
                        "name": "Application %d" % index,
                        "source_id": "debian-apt",
                    }
                    for index in range(1001)
                ]
                self.state_reads = []

            def search(self, query, source_id=None):
                query = str(query or "").casefold()
                return [item for item in self.items if query in item["name"].casefold()]

            def installed_state(self, source_id, app_id):
                self.state_reads.append(app_id)
                return {"installed": False, "version": None, "architecture": None}

        catalog = Catalog()
        controller = load_ui().StoreController(catalog=catalog)
        page = controller.inventory_page("", limit=80)
        self.assertEqual(1001, page["total"])
        self.assertEqual(80, len(page["items"]))
        self.assertEqual(80, len(catalog.state_reads))
        searched = controller.inventory_page("Application 999", limit=80)
        self.assertEqual(["app-999"], [item["app_id"] for item in searched["items"]])


if __name__ == "__main__":
    unittest.main()
