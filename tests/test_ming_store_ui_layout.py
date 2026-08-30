import pathlib
import unittest
import importlib.util


ROOT = pathlib.Path(__file__).resolve().parents[1]
STORE_UI = ROOT / "assets" / "ming-store.py"


def load_store_ui():
    spec = importlib.util.spec_from_file_location("ming_store_ui_layout", STORE_UI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MingStoreUiLayoutTests(unittest.TestCase):
    def test_store_uses_card_grid_with_icon_name_and_summary(self):
        source = STORE_UI.read_text(encoding="utf-8")
        self.assertRegex(source, r"Gtk\.(?:FlowBox|Grid)")
        self.assertIn("icon_url", source)
        self.assertIn("summary", source)
        self.assertIn("description", source)
        self.assertIn("ming-store-card", source)

    def test_store_cards_keep_stable_dimensions_and_readable_background(self):
        source = STORE_UI.read_text(encoding="utf-8")
        self.assertIn("min-width", source)
        self.assertIn("min-height", source)
        self.assertIn("background: #ffffff", source)
        self.assertIn("set_size_request", source)

    def test_stale_index_warning_is_exposed_to_the_store_status(self):
        ui = load_store_ui()

        class Provider:
            catalog_state = "stale"
            cache_warning = "缓存目录（签名索引距发布时间约 2.0 天）"

            def refresh_catalog(self):
                return [{"app_id": "spark-demo"}]

        class Registry:
            def get(self, _source_id):
                return Provider()

        controller = ui.StoreController(catalog=type("Catalog", (), {"registry": Registry()})())
        status = controller.refresh_section("spark")[0]
        self.assertIn("2.0", status["message"])


if __name__ == "__main__":
    unittest.main()
