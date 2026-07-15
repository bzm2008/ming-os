import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "assets" / "ming-performance-status.py"
PHONE_PATH = ROOT / "assets" / "ming-phone-desktop.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module(MODULE_PATH, "ming_performance_status_metrics")
        cls.phone = PHONE_PATH.read_text(encoding="utf-8")

    def test_metrics_snapshot_reports_memory_cpu_and_network_modes(self):
        values = {
            "/proc/meminfo": (
                "MemTotal:       1024000 kB\n"
                "MemAvailable:    614400 kB\n"
            ),
            "/proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\n",
            "/proc/net/dev": "  eth0: 102400 0 0 0 0 0 0 0 204800 0 0 0 0 0 0 0\n",
        }
        service = self.module.PerformanceStatus(
            runner=lambda _argv, _timeout: self.module.CommandResult(0, "", ""),
            read_text=lambda path: values.get(str(path)),
            globber=lambda _pattern: [],
        )

        result = service.metrics_snapshot(
            previous={"cpu": {"total": 1000, "idle": 700}, "network": {"eth0": {"bytes": 100000}},},
            interval_seconds=1.0,
        )

        self.assertEqual(1, result["schema_version"])
        self.assertEqual("memory", result["memory"]["mode"])
        self.assertTrue(result["memory"]["available"])
        self.assertEqual("cpu", result["cpu"]["mode"])
        self.assertEqual("network", result["network"]["mode"])
        self.assertIn("unit", result["network"])

    def test_phone_desktop_has_persistent_metric_button_and_cycle(self):
        for marker in (
            "metric_mode",
            "metric_button",
            "memory -> cpu -> network",
            "metrics_snapshot",
            "second-row-left",
        ):
            self.assertIn(marker, self.phone)


if __name__ == "__main__":
    unittest.main()
