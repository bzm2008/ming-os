import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = ROOT / "assets" / "ming-session-profile.py"


def load_profile():
    spec = importlib.util.spec_from_file_location("ming_session_profile", PROFILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SessionProfileTests(unittest.TestCase):
    def test_low_memory_and_old_cpu_disable_expensive_effects(self):
        module = load_profile()
        profile = module.SessionProfile(
            read_text=lambda path: {
                "/proc/meminfo": "MemTotal:        3891200 kB\n",
                "/proc/cpuinfo": "flags : fpu sse sse2 ssse3\n",
            }.get(str(path)),
            path_exists=lambda path: str(path) == "/dev/dri",
            runner=lambda argv, timeout=2: module.CommandResult(
                0, "OpenGL renderer string: llvmpipe (LLVM 15.0.0)\n", ""
            ) if tuple(argv) == ("glxinfo", "-B") else module.CommandResult(127, "", ""),
        )
        result = profile.collect()
        self.assertTrue(result["low_memory"])
        self.assertFalse(result["avx2"])
        self.assertEqual("off", result["compositor_profile"])
        self.assertFalse(result["animations"])
        self.assertFalse(result["dock_zoom"])

    def test_modern_hardware_keeps_effects_but_uses_xrender_without_dri(self):
        module = load_profile()
        profile = module.SessionProfile(
            read_text=lambda path: {
                "/proc/meminfo": "MemTotal:        8192000 kB\n",
                "/proc/cpuinfo": "flags : fpu sse sse2 avx avx2\n",
            }.get(str(path)),
            path_exists=lambda _path: False,
            runner=lambda _argv, timeout=2: module.CommandResult(
                0, "OpenGL renderer string: Mesa Intel(R) UHD Graphics\n", ""
            ),
        )
        result = profile.collect()
        self.assertFalse(result["low_memory"])
        self.assertTrue(result["avx2"])
        self.assertEqual("xrender", result["compositor_profile"])
        self.assertTrue(result["animations"])
        self.assertTrue(result["dock_zoom"])

    def test_cli_output_has_stable_schema_and_no_shell_probe(self):
        source = PROFILE.read_text(encoding="utf-8")
        self.assertIn('"schema_version": 1', source)
        self.assertNotIn("shell=True", source)
        self.assertIn('args != ["status", "--json"]', source)


if __name__ == "__main__":
    unittest.main()
