import importlib.util
import pathlib
import threading
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "assets" / "ming-store-core.py"
UI_PATH = ROOT / "assets" / "ming-store.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoreLoadingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = load("ming_store_core_loading_regressions", CORE_PATH)
        cls.ui = load("ming_store_ui_loading_regressions", UI_PATH)

    def test_missing_system_package_query_is_a_status_not_a_page_crash(self):
        class Provider:
            source_id = "debian-apt"

            def search(self, _query):
                return [{
                    "app_id": "demo",
                    "package_name": "demo",
                    "name": "Demo",
                    "source_id": self.source_id,
                    "enabled": True,
                }]

            def installed_state(self, _app_id):
                raise FileNotFoundError("dpkg-query is not installed")

        class Registry:
            def get(self, source_id):
                self.provider.source_id = source_id
                return self.provider

            provider = Provider()

        class Catalog:
            registry = Registry()

            def search(self, query, source_id=None):
                return self.registry.get(source_id or "debian-apt").search(query)

            def installed_state(self, source_id, app_id):
                return self.registry.get(source_id).installed_state(app_id)

        page = self.ui.StoreController(catalog=Catalog()).inventory_page(
            "", source_id="debian-apt", limit=20,
        )
        self.assertEqual(1, len(page["items"]))
        self.assertEqual("status_unavailable", page["items"][0]["_installed_state"]["state"])

    def test_default_runner_reports_missing_binary_without_raising(self):
        result = self.core._default_runner(("ming-command-that-does-not-exist",), timeout=1)
        self.assertEqual(127, result[0])
        self.assertTrue(result[2])

    def test_spark_refresh_has_a_hard_deadline_for_a_stalled_fetcher(self):
        gate = threading.Event()

        def stalled_fetcher(_url, _headers=None):
            gate.wait(10)
            return {"status": 200, "headers": {}, "body": "[]"}

        started = time.monotonic()
        provider = self.core.SparkPublicProvider(
            categories=("tools",), fetcher=stalled_fetcher,
            fetch_timeout=0.03, refresh_timeout=0.08,
        )
        with self.assertRaises(self.core.ProviderUnavailable):
            provider.refresh_catalog()
        self.assertLess(time.monotonic() - started, 0.5)
        gate.set()

    def test_spark_refresh_does_not_wait_for_a_stalled_category_worker(self):
        """A category timeout must return without joining the worker thread."""
        gate = threading.Event()

        def stalled_category(*_args, **_kwargs):
            gate.wait(2)
            return {"status": 200, "headers": {}, "body": "[]", "raw_body": b"[]"}

        provider = self.core.SparkPublicProvider(
            categories=("tools",), refresh_timeout=0.05,
        )
        provider._fetch_resource = stalled_category
        started = time.monotonic()
        with self.assertRaises(self.core.ProviderUnavailable):
            provider.refresh_catalog()
        elapsed = time.monotonic() - started
        gate.set()
        self.assertLess(
            elapsed, 0.25,
            "refresh timeout must not wait for ThreadPoolExecutor shutdown",
        )

    def test_home_empty_presentation_distinguishes_refreshing_unavailable_and_empty(self):
        self.assertEqual(
            ("正在读取软件目录", "正在使用已有缓存或等待来源刷新结果。"),
            self.ui.home_empty_presentation([], [], refreshing=True),
        )
        self.assertEqual(
            ("来源暂不可用", "网络不可用，且没有可用的目录缓存。"),
            self.ui.home_empty_presentation(
                [], [{"ok": False, "using_cache": False,
                      "message": "网络不可用，且没有可用的目录缓存。"}],
                refreshing=False,
            ),
        )
        self.assertEqual(
            ("没有找到软件", "请更换关键词或来源。"),
            self.ui.home_empty_presentation([], [{"ok": True}], refreshing=False),
        )

    def test_source_selector_is_scoped_to_the_active_store_section(self):
        spark = self.ui.source_options_for_section("spark")
        sources = self.ui.source_options_for_section("sources")
        self.assertEqual(("all", "spark-public"), spark)
        self.assertEqual(
            ("all", "ming-official", "debian-apt", "vendor-official", "wine-official"),
            sources,
        )
        self.assertNotIn("spark-public", sources)

    def test_source_selector_maps_display_index_without_crossing_sections(self):
        self.assertEqual("all", self.ui.source_id_for_selection("sources", 0))
        self.assertEqual("debian-apt", self.ui.source_id_for_selection("sources", 2))
        self.assertEqual("spark-public", self.ui.source_id_for_selection("spark", 1))
        self.assertEqual("all", self.ui.source_id_for_selection("sources", 99))

    def test_store_pages_have_a_bounded_loading_state_and_truthful_timeout(self):
        source = UI_PATH.read_text(encoding="utf-8")
        self.assertIn("STORE_PAGE_TIMEOUT_SECONDS", source)
        self.assertIn("GLib.timeout_add_seconds", source)
        self.assertIn("页面加载超时", source)
        self.assertEqual(
            ("页面加载超时", "软件来源响应时间过长，请检查网络后重试。"),
            self.ui.page_load_presentation(timed_out=True),
        )

    def test_refresh_completion_renders_before_marking_generation_finished(self):
        source = UI_PATH.read_text(encoding="utf-8")
        callback = source.split("        def apply_refresh():", 1)[1].split(
            "\n            GLib.idle_add(apply_refresh)", 1
        )[0]
        self.assertIn("render_home_page(refreshed", callback)
        self.assertLess(
            callback.index("render_home_page(refreshed"),
            callback.index('finished["value"] = True'),
            "refresh results are suppressed by the finished guard",
        )


if __name__ == "__main__":
    unittest.main()
