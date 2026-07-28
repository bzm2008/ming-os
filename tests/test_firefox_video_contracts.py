import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")


class FirefoxVideoContracts(unittest.TestCase):
    def test_firefox_wrapper_prefers_esr_and_uses_a_safe_runtime_fallback(self):
        start = APPS.index("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'")
        end = APPS.index(
            "\nMINGFIREFOX",
            start + len("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'"),
        )
        wrapper = APPS[start:end]
        self.assertIn("command -v firefox-esr", wrapper)
        self.assertIn('exec firefox-esr "${firefox_args[@]}" "$@"', wrapper)
        self.assertIn("command -v firefox", wrapper)
        self.assertIn('exec firefox "${firefox_args[@]}" "$@"', wrapper)
        self.assertNotIn("microsoft-edge", wrapper)
        self.assertNotIn("--enable-accelerated-video-decode", wrapper)
        self.assertNotIn("--disable-gpu", wrapper)
