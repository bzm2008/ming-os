import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "docs" / "design" / "ming-dock-style-preview.html"
RUNTIME_FILES = (
    ROOT / "assets" / "ming-phone-desktop.py",
    ROOT / "modules" / "03_desktop.sh",
)


class DockStylePreviewTests(unittest.TestCase):
    def test_preview_page_offers_four_non_runtime_dock_styles(self):
        self.assertTrue(PREVIEW.is_file(), "Dock style preview page is missing")
        html = PREVIEW.read_text(encoding="utf-8")
        for name in (
            "Calm Glass Rail",
            "Solid Focus Bar",
            "Compact Tiles",
            "Floating Shelf",
        ):
            self.assertIn(name, html)
        for state in ("正常", "悬停", "运行中", "最小化恢复", "低资源模式"):
            self.assertIn(state, html)
        self.assertIn("只作为设计预览", html)
        self.assertIn("不修改真实 Plank", html)
        for runtime in RUNTIME_FILES:
            self.assertTrue(runtime.is_file(), f"runtime file disappeared: {runtime}")


if __name__ == "__main__":
    unittest.main()
