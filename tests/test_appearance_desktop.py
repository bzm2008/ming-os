import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import ast


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "assets/ming-shell-common.py"
CONTROL = ROOT / "assets/ming-appearance-control.py"


def load_common():
    spec = importlib.util.spec_from_file_location("ming_shell_common_appearance", COMMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IconResolverTests(unittest.TestCase):
    def test_absolute_png_and_svg_are_accepted_but_oversize_is_rejected(self):
        common = load_common()
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            png = root / "wechat.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
            svg = root / "wechat.svg"
            svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
            huge = root / "huge.png"
            huge.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 1024)
            self.assertEqual(str(png), common.resolve_icon(str(png), max_bytes=128))
            self.assertEqual(str(svg), common.resolve_icon(str(svg), max_bytes=128))
            self.assertEqual("application-x-executable", common.resolve_icon(str(huge), max_bytes=128))

    def test_theme_extension_pixmaps_and_missing_fallback(self):
        common = load_common()
        with tempfile.TemporaryDirectory() as temp:
            pixmaps = pathlib.Path(temp)
            icon = pixmaps / "wechat.png"
            icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 8)
            self.assertEqual("utilities-terminal", common.resolve_icon("utilities-terminal"))
            self.assertEqual(str(icon), common.resolve_icon("wechat.png", pixmap_dirs=[pixmaps]))
            self.assertEqual("fallback", common.resolve_icon("missing.png", fallback="fallback", pixmap_dirs=[pixmaps]))


class AppearanceControlTests(unittest.TestCase):
    def run_control(self, home, *args):
        env = dict(os.environ, HOME=str(home), MING_APPEARANCE_NO_APPLY="1")
        return subprocess.run(
            ["python", str(CONTROL), *args, "--json"], env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_apply_persists_and_status_reads_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            result = self.run_control(home, "apply", "--theme", "dark", "--font-size", "12", "--desktop-icon-scale", "1.25", "--dock-icon-size", "52")
            self.assertEqual(0, result.returncode, result.stderr)
            status = self.run_control(home, "status")
            payload = json.loads(status.stdout)
            self.assertEqual("dark", payload["theme"])
            self.assertEqual(12, payload["font_size"])
            self.assertEqual(1.25, payload["desktop_icon_scale"])
            self.assertEqual(52, payload["dock_icon_size"])
            self.assertFalse(any((home / ".config/ming-os").glob("*.tmp")))

    def test_invalid_custom_wallpaper_falls_back_without_replacing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            bad = home / "not-image.txt"
            bad.write_text("not an image", encoding="utf-8")
            result = self.run_control(home, "apply", "--wallpaper", str(bad))
            self.assertNotEqual(0, result.returncode)
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual("default", status["wallpaper"])


class DeploymentContractTests(unittest.TestCase):
    def test_shell_consumers_use_shared_icon_resolver(self):
        for name in ("ming-phone-desktop.py", "ming-app-drawer.py", "ming-launch.py", "ming-files.py"):
            text = (ROOT / "assets" / name).read_text(encoding="utf-8")
            self.assertIn("resolve_icon", text, name)

    def test_appearance_pointer_and_compatibility_pages_are_deployed(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        self.assertIn("外观与桌面", settings)
        self.assertIn("鼠标与触控板", settings)
        self.assertIn("libinput", settings)
        for allowed in ("xfce4-mouse-settings", "xfce4-keyboard-settings", "xfce4-accessibility-settings", "thunar-volman-settings"):
            self.assertIn(allowed, settings)
        for forbidden in ("xfce4-panel --preferences", "xfdesktop-settings", "xfce4-display-settings"):
            self.assertNotIn(forbidden, settings)
        self.assertIn("ming-appearance-control.py", desktop)

    def test_session_and_package_contract_has_one_ming_shell(self):
        apps = (ROOT / "modules/02_apps.sh").read_text(encoding="utf-8")
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        install_block = apps[apps.index("install_xfce_desktop()"):apps.index("# ========================", apps.index("install_xfce_desktop()") + 10)]
        for removed in ("xfce4-panel", "xfce4-appfinder", "xfce4-whiskermenu-plugin", "xfce4-power-manager-plugins"):
            self.assertNotIn(removed, install_block)
        self.assertIn("xfce4-notifyd", install_block)
        self.assertNotIn("Exec=nm-applet", desktop)
        self.assertNotIn("Exec=volumeicon", desktop)
        self.assertIn("8", desktop[desktop.index("ming-phone-desktop did not publish ready marker") - 1800:desktop.index("ming-phone-desktop did not publish ready marker")])

    def test_build_gate_rejects_duplicate_shell_runtimes(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        for process in ("xfce4-panel", "xfce4-appfinder", "whiskermenu", "volumeicon", "nm-applet", "xfdesktop"):
            self.assertIn(process, build)
        self.assertIn("retired duplicate shell runtime must not be installed", build)
        self.assertIn("normal session starts duplicate shell process", build)


class DesktopAppearanceBehaviorTests(unittest.TestCase):
    def phone_functions(self, wanted):
        path = ROOT / "assets/ming-phone-desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) and not (
            (isinstance(node, ast.Import) and any(alias.name == "gi" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "gi.repository"))]
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {"Path": pathlib.Path, "json": json, "os": os, "__file__": str(path)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), "exec"), namespace)
        return namespace

    def test_icon_scale_reflows_grid_preserving_order_folders_and_relative_position(self):
        ns = self.phone_functions({"reflow_layout_for_icon_scale"})
        layout = {"items": [
            {"id": "a", "type": "app", "x": 34, "y": 92},
            {"id": "folder", "type": "folder", "children": ["a.desktop", "b.desktop"], "x": 126, "y": 200},
        ]}
        result = ns["reflow_layout_for_icon_scale"](layout, 1.0, 1.5, 800, 600)
        self.assertEqual(["a", "folder"], [item["id"] for item in result["items"]])
        self.assertEqual(["a.desktop", "b.desktop"], result["items"][1]["children"])
        self.assertLess(result["items"][0]["x"], result["items"][1]["x"])

    def test_custom_wallpaper_is_selected_only_while_valid(self):
        ns = self.phone_functions({"appearance_wallpaper_paths"})
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            image = root / "wall.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)
            self.assertEqual(image, ns["appearance_wallpaper_paths"]({"wallpaper": str(image)}, [root / "fallback"])[0])
            image.unlink()
            self.assertEqual(root / "fallback", ns["appearance_wallpaper_paths"]({"wallpaper": str(image)}, [root / "fallback"])[0])


if __name__ == "__main__":
    unittest.main()
