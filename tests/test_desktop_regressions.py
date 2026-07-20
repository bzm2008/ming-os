import ast
import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE_DESKTOP = ROOT / "assets" / "ming-phone-desktop.py"
APP_DRAWER = ROOT / "assets" / "ming-app-drawer.py"
SETTINGS = ROOT / "assets" / "ming-settings.py"
APPS_MODULE = ROOT / "modules" / "02_apps.sh"
DESKTOP_MODULE = ROOT / "modules" / "03_desktop.sh"
OTA_MODULE = ROOT / "modules" / "06_ota_update.sh"
BASE_MODULE = ROOT / "modules" / "01_base.sh"
BUILD_SCRIPT = ROOT / "build_onion_os.sh"


def load_interaction_state():
    source = PHONE_DESKTOP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == "InteractionState"
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(PHONE_DESKTOP), "exec"), namespace)
    return namespace["InteractionState"]


class InteractionStateTests(unittest.TestCase):
    def test_small_pointer_movement_activates_once(self):
        state = load_interaction_state()(drag_threshold=12)
        state.begin("mouse", 10, 10, 1000)
        state.update(17, 14)
        self.assertEqual("activate", state.finish(17, 14, 1010))
        self.assertIsNone(state.finish(17, 14, 1011))

    def test_large_pointer_movement_is_drag(self):
        state = load_interaction_state()(drag_threshold=12)
        state.begin("touch", 10, 10, 1000)
        state.update(30, 10)
        self.assertEqual("drag", state.finish(30, 10, 1100))

    def test_cancel_never_activates(self):
        state = load_interaction_state()(drag_threshold=12)
        state.begin("touch", 10, 10, 1000)
        state.cancel()
        self.assertIsNone(state.finish(10, 10, 1010))

    def test_touch_suppresses_compatibility_mouse_event(self):
        state = load_interaction_state()(drag_threshold=12)
        state.begin("touch", 10, 10, 1000)
        self.assertEqual("activate", state.finish(10, 10, 1010))
        self.assertTrue(state.should_ignore_mouse(1200))
        self.assertFalse(state.should_ignore_mouse(1800))


class DesktopSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.phone = PHONE_DESKTOP.read_text(encoding="utf-8")
        cls.drawer = APP_DRAWER.read_text(encoding="utf-8")
        cls.desktop = DESKTOP_MODULE.read_text(encoding="utf-8")

    def test_tile_text_is_bounded(self):
        for marker in [
            "label.set_size_request(LABEL_W, LABEL_H)",
            "label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)",
            "label.set_ellipsize(Pango.EllipsizeMode.END)",
            "label.set_lines(2)",
        ]:
            self.assertIn(marker, self.phone)

    def test_android_desktop_is_enabled(self):
        self.assertIn("Exec=/usr/local/bin/ming-session-healthcheck --session", self.desktop)
        self.assertIn("X-Ming-Managed-Components=phone-desktop;plank;picom", self.desktop)
        self.assertIn("X-GNOME-Autostart-enabled=true", self.desktop)
        self.assertIn("Exec=/usr/bin/true", self.desktop)
        self.assertIn("ming-phone-desktop --sync", self.desktop)

    def test_gtk3_shell_entries_lock_gdk3_before_importing_gdk(self):
        for source in (self.phone, self.drawer):
            require = 'gi.require_version("Gdk", "3.0")'
            imported = "from gi.repository import Gdk"
            self.assertIn(require, source)
            self.assertLess(source.index(require), source.index(imported))

    def test_plank_watchdog_is_session_long_and_visible(self):
        self.assertIn("while true; do", self.desktop)
        self.assertIn("plank_window_visible", self.desktop)
        self.assertIn("ming-plank-watchdog --session", self.desktop)

    def test_plank_window_lookup_uses_wm_class_column(self):
        self.assertNotIn("tolower($4) ~ /plank/", self.desktop)
        self.assertGreaterEqual(self.desktop.count("tolower($3) ~ /plank/"), 2)

    def test_app_drawer_is_focusable_opaque_and_explicitly_closable(self):
        self.assertNotIn("WindowTypeHint.DOCK", self.drawer)
        self.assertNotIn(
            "window#ming-app-drawer { background: transparent; }",
            self.drawer,
        )
        self.assertIn('Gtk.Button(label="关闭")', self.drawer)
        self.assertIn('window.connect("delete-event"', self.drawer)

    def test_app_drawer_activates_on_explicit_primary_release(self):
        self.assertIn('button.connect("button-release-event", self._activate_button, app)', self.drawer)
        self.assertIn("def _activate_button", self.drawer)
        self.assertNotIn('button.connect("clicked", self.launch, app, button)', self.drawer)

    def test_drawer_keeps_context_menu_open_and_deduplicates_system_wrappers(self):
        self.assertNotIn('window.connect("focus-out-event"', self.drawer)
        for marker in [
            "def canonical_identity(app):",
            "def deduplicate_apps(apps):",
            '"ming-control-center.desktop": "settings"',
            '"ming-files.desktop": "files"',
            '"ming-terminal.desktop": "terminal"',
            '"ming-edge.desktop": "edge"',
            'basename == "ming-update.desktop"',
        ]:
            self.assertIn(marker, self.drawer)

    def test_desktop_preserves_last_known_good_layout_and_has_a_blank_area_menu(self):
        for marker in [
            "LAST_GOOD_LAYOUT_PATH",
            "def layout_is_valid",
            "primary layout invalid; restoring last known-good layout",
            "app discovery was transiently empty; keeping last known-good layout",
            "def show_desktop_context_menu",
            "刷新桌面",
            "打开应用抽屉",
            "Ming 设置",
            "终端",
        ]:
            self.assertIn(marker, self.phone)

    def test_desktop_does_not_rebuild_all_tiles_on_a_fixed_timer(self):
        self.assertNotIn(
            "GLib.timeout_add_seconds(8, self.refresh_from_apps)",
            self.phone,
        )
        self.assertIn("def refresh_if_apps_changed", self.phone)

    def test_layer_enforcement_is_one_shot_and_non_blocking(self):
        self.assertIn("threading.Thread(target=self.apply_desktop_layer", self.phone)
        self.assertNotIn(
            "GLib.timeout_add_seconds(4, self.enforce_desktop_layer)",
            self.phone,
        )

    def test_shell_launches_use_socket_ack_and_broker_fallback(self):
        common = (ROOT / "assets" / "ming-shell-common.py").read_text(encoding="utf-8")
        self.assertIn("def send_launch_request", common)
        self.assertIn("def broker_fallback_argv", common)
        self.assertIn("COMMON = load_shell_common()", self.phone)
        self.assertIn('sender(path, "desktop", source_rect)', self.phone)
        self.assertIn('fallback(path, "desktop")', self.phone)
        self.assertIn("COMMON.send_launch_request", self.drawer)
        self.assertIn("COMMON.broker_fallback_argv", self.drawer)
        self.assertIn("无法打开此应用", self.drawer)

    def test_phone_desktop_uses_async_broker_requests(self):
        open_item = self.phone[
            self.phone.index("    def open_item(self, item):"):
            self.phone.index("    def show_folder", self.phone.index("    def open_item(self, item):"))
        ]
        self.assertIn("def launch_item_async", self.phone)
        self.assertIn("launch_item_async(item, source_rect", open_item)

    def test_phone_async_legacy_false_result_starts_safe_broker_fallback_without_success_ack(self):
        """A partially updated old broker may recover safely, but has not launched yet."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        launch = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "launch_item_async"
        )
        calls = []
        results = []
        path = "/usr/share/applications/store-wrapper.desktop"
        expected = ("ming-launch", "--server")

        class Common:
            ASYNC_LAUNCH_REQUEST_TIMEOUT = 12.0

            @staticmethod
            def send_launch_request_async(desktop_file, source_name, rect, callback, timeout):
                self.assertEqual((path, "desktop", None, 12.0), (
                    desktop_file, source_name, rect, timeout))
                callback(False)
                return True

            @staticmethod
            def broker_fallback_argv(desktop_file, source_name):
                self.assertEqual((path, "desktop"), (desktop_file, source_name))
                return expected

        namespace = {
            "COMMON": Common(),
            "log": lambda _message: None,
            "subprocess": types.SimpleNamespace(
                Popen=lambda argv, shell=False: calls.append((argv, shell)) or object(),
            ),
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[launch], type_ignores=[])),
                str(PHONE_DESKTOP),
                "exec",
            ),
            namespace,
        )

        self.assertTrue(namespace["launch_item_async"](
            {"path": path, "diagnostic": ""}, on_result=results.append))
        self.assertEqual([(expected, False)], calls)
        self.assertEqual([False], results)

    def test_phone_desktop_ignores_repeat_click_until_async_launch_settles(self):
        """A pending desktop launch must not enqueue a second broker request."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        desktop = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        open_item = next(
            node for node in desktop.body
            if isinstance(node, ast.FunctionDef) and node.name == "open_item"
        )
        requests = []
        feedback = []

        class ImmediateGLib:
            @staticmethod
            def idle_add(callback):
                callback()
                return True

        def launch_async(item, _rect, on_result):
            requests.append(on_result)
            return True

        namespace = {
            "GLib": ImmediateGLib(),
            "Gtk": types.SimpleNamespace(),
            "PAD_X": 20,
            "PAD_Y": 20,
            "log": lambda _message: None,
            "scaled_tile_metrics": lambda _scale: (96, 96),
            "launch_item_async": launch_async,
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[open_item], type_ignores=[])),
                str(PHONE_DESKTOP),
                "exec",
            ),
            namespace,
        )
        desktop = types.SimpleNamespace(
            appearance={"desktop_icon_scale": 1.0},
            window_origin=(0, 0),
            launch_feedback=types.SimpleNamespace(begin=lambda item: feedback.append(("begin", item["path"]))),
        )
        item = {"path": "/usr/share/applications/store.desktop"}

        namespace["open_item"](desktop, item)
        namespace["open_item"](desktop, item)
        self.assertEqual(1, len(requests))
        self.assertEqual([("begin", item["path"])], feedback)

        requests[0](True)
        namespace["open_item"](desktop, item)
        self.assertEqual(2, len(requests))

    def test_phone_desktop_does_not_touch_gtk_when_idle_queue_rejects_a_worker_result(self):
        """A GLib idle scheduling failure may clear state, but cannot render UI on the worker."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        phone_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PhoneDesktop"
        )
        open_item = next(
            node for node in phone_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "open_item"
        )
        requests = []
        feedback = []
        dialogs = []

        class RejectedGLib:
            @staticmethod
            def idle_add(_callback):
                return False

        class Dialog:
            def __init__(self, **kwargs):
                dialogs.append(kwargs.get("text"))

            def format_secondary_text(self, _text):
                return None

            def run(self):
                return None

            def destroy(self):
                return None

        def launch_async(item, _rect, on_result):
            requests.append(on_result)
            return True

        namespace = {
            "GLib": RejectedGLib(),
            "Gtk": types.SimpleNamespace(
                MessageDialog=Dialog,
                MessageType=types.SimpleNamespace(ERROR="error"),
                ButtonsType=types.SimpleNamespace(CLOSE="close"),
            ),
            "PAD_X": 20,
            "PAD_Y": 20,
            "log": lambda _message: None,
            "scaled_tile_metrics": lambda _scale: (96, 96),
            "launch_item_async": launch_async,
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[open_item], type_ignores=[])),
                str(PHONE_DESKTOP),
                "exec",
            ),
            namespace,
        )
        desktop = types.SimpleNamespace(
            appearance={"desktop_icon_scale": 1.0},
            window_origin=(0, 0),
            launch_feedback=types.SimpleNamespace(
                begin=lambda item: feedback.append(("begin", item["path"])),
                finish=lambda: feedback.append(("finish",)),
            ),
        )
        item = {"path": "/usr/share/applications/rejected-store.desktop"}

        namespace["open_item"](desktop, item)
        requests.pop()(False)
        self.assertEqual([("begin", item["path"])], feedback)
        self.assertEqual([], dialogs)
        self.assertEqual(set(), desktop._pending_launch_paths)
        namespace["open_item"](desktop, item)
        self.assertEqual(1, len(requests))

    def test_phone_fallback_starts_only_the_broker_without_reparsing_exec(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        launch = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "launch_item"
        )
        calls = []
        logs = []
        path = "/usr/share/applications/store-wrapper.desktop"
        expected = ("ming-launch", "--server")
        responses = iter((False, True))

        class Common:
            @staticmethod
            def send_launch_request(desktop_file, source_name, rect, timeout=None):
                self.assertEqual((path, "desktop", None), (desktop_file, source_name, rect))
                self.assertIn(timeout, (None, 1.0))
                return next(responses)

            @staticmethod
            def broker_fallback_argv(desktop_file, source_name):
                self.assertEqual((path, "desktop"), (desktop_file, source_name))
                return expected

            @staticmethod
            def retry_launch_request_after_broker_start(desktop_file, source_name, rect):
                self.assertEqual((path, "desktop", None), (desktop_file, source_name, rect))
                return True

        namespace = {
            "COMMON": Common(),
            "log": logs.append,
            "subprocess": types.SimpleNamespace(
                Popen=lambda argv, shell=False: calls.append((argv, shell)) or object(),
            ),
        }
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[launch], type_ignores=[])),
                str(PHONE_DESKTOP),
                "exec",
            ),
            namespace,
        )

        self.assertTrue(namespace["launch_item"]({"path": path, "diagnostic": ""}))
        self.assertEqual([(expected, False)], calls)
        self.assertTrue(any("source=desktop" in message for message in logs))
        function_source = ast.get_source_segment(source, launch)
        self.assertNotIn("parse_desktop_file", function_source)
        self.assertNotIn("entry.argv", function_source)

    def test_status_panel_fills_its_allocated_width(self):
        self.assertIn("box.set_halign(Gtk.Align.FILL)", self.phone)
        self.assertIn("box.set_hexpand(True)", self.phone)
        self.assertIn("controls.attach(self.volume_scale, 0, 1, 3, 1)", self.phone)
        self.assertIn("controls.attach(self.brightness_scale, 0, 3, 3, 1)", self.phone)

    def test_desktop_uses_cairo_for_the_single_tile_visual_source(self):
        fallback = self.phone[
            self.phone.index("def draw_icon_fallback"):
            self.phone.index("def item_at")
        ]
        self.assertNotIn("if self.tiles:", fallback)
        self.assertIn("self.set_opacity(0.0)", self.phone)

    def test_desktop_file_sync_has_manifest_marker_contract(self):
        self.assertIn("desktop-generated-manifest.json", self.phone)
        self.assertIn("X-Ming-Managed", self.phone)
        self.assertIn("def load_desktop_manifest", self.phone)
        self.assertIn("def save_desktop_manifest", self.phone)

    def test_desktop_render_creates_transparent_hit_targets(self):
        render = self.phone[self.phone.index("    def render(self):"):
                            self.phone.index("    def place_overlays", self.phone.index("    def render(self):"))]
        self.assertIn("DesktopTile(self, item)", render)
        self.assertIn("self.fixed.put(tile", render)
        self.assertIn("draw_icon_fallback", self.phone)

    def test_legacy_shell_common_keeps_valid_desktop_launchers_usable(self):
        """A partial hot deployment must not turn every desktop icon inert."""
        self.assertIn("def legacy_desktop_entry", self.phone)
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        body = [node for node in tree.body if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)]
        body.extend(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module != "gi.repository")
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "legacy_desktop_entry")
        namespace = {
            "Path": pathlib.Path,
            "configparser": __import__("configparser"),
            "os": os,
            "re": __import__("re"),
            "shlex": __import__("shlex"),
            "shutil": shutil,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher = pathlib.Path(temp_dir) / "terminal.desktop"
            launcher.write_text(
                "[Desktop Entry]\nType=Application\nName=Terminal\nExec=python -V %U\n",
                encoding="utf-8",
            )
            with mock.patch.object(shutil, "which", return_value="/usr/bin/python"):
                entry = namespace["legacy_desktop_entry"](launcher)
        self.assertEqual(["python", "-V"], entry["argv"])
        self.assertEqual("", entry["diagnostic"])
        self.assertIn('legacy_argv = item.get("legacy_argv")', self.phone)

    def test_layout_migration_keeps_positions_and_folder_children(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id",
            "_item_id",
            "migrate_layout",
            "empty_layout",
            "layout_is_valid",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {
            "Path": pathlib.Path,
            "json": json,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_DESKTOP),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)
        migrate_layout = namespace["migrate_layout"]
        old = {
            "version": 2,
            "items": [
                {"id": "app-a", "type": "app", "path": "/tmp/a.desktop", "x": 417, "y": 233},
                {"id": "folder-a", "type": "folder", "name": "工具", "x": 721, "y": 355,
                 "children": ["/tmp/a.desktop", "/tmp/b.desktop"]},
            ],
        }
        migrated = migrate_layout(old)
        self.assertEqual(417, migrated["items"][0]["x"])
        self.assertEqual(233, migrated["items"][0]["y"])
        self.assertEqual("工具", migrated["items"][1]["name"])
        self.assertEqual(["/tmp/a.desktop", "/tmp/b.desktop"], migrated["items"][1]["children"])
        self.assertEqual(7, migrated["version"])

    def test_core_layout_path_migration_keeps_position_and_removes_stale_duplicates(self):
        """A core app may move from a system launcher to a Ming-managed launcher after an upgrade."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"app_id", "canonicalize_core_layout_item"}
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {
            "Path": pathlib.Path,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_DESKTOP),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)

        canonical = {
            "ming-trash.desktop": {
                "id": "canonical-trash",
                "type": "app",
                "path": "/home/user/Desktop/ming-trash.desktop",
                "basename": "ming-trash.desktop",
                "name": "回收站",
            },
        }
        seen = set()
        old = {
            "id": "old-trash-position",
            "type": "app",
            "path": "/usr/share/applications/ming-trash.desktop",
            "x": 218,
            "y": 200,
            "pinned": True,
        }
        migrated = namespace["canonicalize_core_layout_item"](old, canonical, seen)
        duplicate = namespace["canonicalize_core_layout_item"](
            {**old, "id": "second-trash", "path": "/home/user/.local/share/applications/ming-trash.desktop"},
            canonical,
            seen,
        )

        self.assertEqual("/home/user/Desktop/ming-trash.desktop", migrated["path"])
        self.assertEqual("old-trash-position", migrated["id"])
        self.assertEqual((218, 200), (migrated["x"], migrated["y"]))
        self.assertIsNone(duplicate)

    def test_legacy_settings_and_edge_aliases_migrate_to_one_canonical_launcher(self):
        """Old Desktop copies must not survive as broker-rejected duplicate core tiles."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"app_id", "canonicalize_core_layout_item"}
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {
            "Path": pathlib.Path,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_DESKTOP),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)

        canonical = {
            "ming-settings.desktop": {
                "id": "canonical-settings", "type": "app",
                "path": "/usr/share/applications/ming-settings.desktop",
                "basename": "ming-settings.desktop", "name": "Ming 设置",
            },
            "ming-edge.desktop": {
                "id": "canonical-edge", "type": "app",
                "path": "/usr/share/applications/ming-edge.desktop",
                "basename": "ming-edge.desktop", "name": "Microsoft Edge",
            },
        }
        seen = set()
        settings = namespace["canonicalize_core_layout_item"]({
            "id": "old-settings", "type": "app",
            "path": "/home/user/Desktop/ming-control-center.desktop",
            "x": 218, "y": 200, "pinned": True,
        }, canonical, seen)
        edge = namespace["canonicalize_core_layout_item"]({
            "id": "old-edge", "type": "app",
            "path": "/home/user/.local/share/applications/microsoft-edge-stable.desktop",
            "x": 320, "y": 200,
        }, canonical, seen)
        duplicate_settings = namespace["canonicalize_core_layout_item"]({
            "id": "second-settings", "type": "app",
            "path": "/usr/share/applications/ming-settings.desktop",
        }, canonical, seen)

        self.assertEqual("/usr/share/applications/ming-settings.desktop", settings["path"])
        self.assertEqual((218, 200), (settings["x"], settings["y"]))
        self.assertEqual("/usr/share/applications/ming-edge.desktop", edge["path"])
        self.assertEqual((320, 200), (edge["x"], edge["y"]))
        self.assertIsNone(duplicate_settings)

    def test_core_discovery_prefers_the_protected_system_launcher_over_a_desktop_seed(self):
        """Live-image Desktop copies are display aids, never the canonical broker target."""
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        block = source.split("def add_core_app(apps_by_basename, basename):", 1)[1].split(
            "\ndef load_apps", 1
        )[0]
        self.assertIn("SYSTEM_APPLICATION_DIR / basename", block)
        self.assertIn("DESKTOP_DIR / basename", block)
        self.assertLess(
            block.index("SYSTEM_APPLICATION_DIR / basename"),
            block.index("DESKTOP_DIR / basename"),
        )
        self.assertLess(
            block.index("CORE_FALLBACKS.get"),
            block.index("DESKTOP_DIR / basename"),
        )

    def test_layout_save_is_atomic_and_bad_primary_keeps_last_good(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id", "_item_id", "empty_layout", "layout_is_valid", "migrate_layout",
            "read_layout", "load_layout", "_atomic_write_json", "save_layout",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {
            "Path": pathlib.Path,
            "json": json,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_DESKTOP),
            "log": lambda *_args: None,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = pathlib.Path(temp_dir)
            namespace["STATE_DIR"] = state_dir
            namespace["LAYOUT_PATH"] = state_dir / "desktop-layout.json"
            namespace["LAST_GOOD_LAYOUT_PATH"] = state_dir / "desktop-layout.last-good.json"
            good = {"version": 7, "items": [{"id": "good", "type": "app", "path": "/tmp/g.desktop", "x": 88, "y": 99}]}
            namespace["LAST_GOOD_LAYOUT_PATH"].write_text(json.dumps(good), encoding="utf-8")
            namespace["LAYOUT_PATH"].write_text("{not json", encoding="utf-8")
            loaded = namespace["load_layout"]()
            self.assertEqual("good", loaded["items"][0]["id"])
            self.assertTrue(namespace["save_layout"](good))
            written = json.loads(namespace["LAYOUT_PATH"].read_text(encoding="utf-8"))
            self.assertEqual(7, written["version"])
            self.assertEqual((88, 99), (written["items"][0]["x"], written["items"][0]["y"]))
            # An empty refresh must not destroy the last known-good snapshot.
            self.assertTrue(namespace["save_layout"]({"version": 7, "items": []}))
            backup = json.loads(namespace["LAST_GOOD_LAYOUT_PATH"].read_text(encoding="utf-8"))
            self.assertEqual("good", backup["items"][0]["id"])
            self.assertFalse(any(state_dir.glob("*.tmp")))

    def test_sync_files_preserves_user_desktop_entries_and_removes_only_managed(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id", "safe_name", "legacy_desktop_entry", "read_app", "_desktop_has_marker", "_manifest_relative",
            "_mark_desktop_file", "copy_desktop",
            "empty_desktop_manifest", "load_desktop_manifest", "save_desktop_manifest",
            "_atomic_write_json", "sync_files",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names)
        )
        body.extend(
            node for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module != "gi.repository"
        )
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {
            "Path": pathlib.Path,
            "load_shell_common": lambda: None,
            "__file__": str(PHONE_DESKTOP),
            "log": lambda *_args: None,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            desktop = root / "Desktop"
            source_dir = root / "source"
            source_dir.mkdir()
            app = source_dir / "alpha.desktop"
            app.write_text(
                "[Desktop Entry]\nType=Application\nName=Alpha\nExec=alpha\nIcon=utilities-terminal\n",
                encoding="utf-8",
            )
            desktop.mkdir()
            user = desktop / "my-own.desktop"
            user.write_text("[Desktop Entry]\nType=Application\nName=Mine\nExec=mine\n", encoding="utf-8")
            stale = desktop / "stale.desktop"
            stale.write_text(
                "[Desktop Entry]\nType=Application\nName=Stale\nExec=stale\nX-Ming-Managed=true\n",
                encoding="utf-8",
            )
            manifest = root / "desktop-generated-manifest.json"
            manifest.write_text(json.dumps({"version": 1, "marker": "X-Ming-Managed", "managed_files": ["stale.desktop"]}), encoding="utf-8")
            namespace["DESKTOP_DIR"] = desktop
            namespace["DESKTOP_MANIFEST_PATH"] = manifest
            layout = {"items": [{"id": "alpha", "type": "app", "path": str(app), "name": "Alpha", "pinned": True}]}
            namespace["sync_files"](layout)
            self.assertTrue(user.exists())
            self.assertFalse(stale.exists())
            generated = desktop / "Alpha.desktop"
            self.assertTrue(generated.exists())
            self.assertIn("X-Ming-Managed=true", generated.read_text(encoding="utf-8"))
            self.assertIsNotNone(namespace["read_app"](generated))
            saved_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("Alpha.desktop", saved_manifest["managed_files"])

    def test_drag_position_snaps_to_grid_and_stays_inside_workarea(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "clamp_grid_position")
        namespace = {"Path": pathlib.Path, "load_shell_common": lambda: None, "__file__": str(PHONE_DESKTOP)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)
        clamp_grid_position = namespace["clamp_grid_position"]
        x, y = clamp_grid_position(-40, 9999, width=640, height=480)
        self.assertGreaterEqual(x, 34)
        self.assertGreaterEqual(y, 92)
        self.assertLessEqual(x, 640 - 82 - 34)
        self.assertLessEqual(y, 480 - 96 - 34)
        self.assertEqual((218, 292), clamp_grid_position(224, 311, width=640, height=480))

    def test_folder_merge_remains_pinned_and_keeps_children(self):
        self.assertIn('"pinned": True', self.phone[self.phone.index("def create_or_merge_folder"):self.phone.index("    def open_item", self.phone.index("def create_or_merge_folder"))])
        self.assertIn('target.setdefault("children", [])', self.phone)
        self.assertIn('"type": "folder"', self.phone)

    def test_cairo_draws_folder_visuals_and_folder_children_are_interactive(self):
        draw = self.phone[self.phone.index("    def draw_icon_fallback"):
                          self.phone.index("    def item_at", self.phone.index("    def draw_icon_fallback"))]
        self.assertIn('item.get("type") == "folder"', draw)
        self.assertIn('icon_name = "folder"', draw)
        folder = self.phone[self.phone.index("    def show_folder"):
                            self.phone.index("    def child_menu", self.phone.index("    def show_folder"))]
        self.assertIn('button.connect("clicked"', folder)
        self.assertIn('button.connect("button-press-event"', folder)

    def test_normal_windows_are_opaque(self):
        for forbidden in [
            "inactive-opacity = 0.92",
            "active-opacity = 0.98",
            "frame-opacity = 0.90",
            '"85:class_g = \'Microsoft-edge\'"',
            '"90:class_g = \'Thunar\'"',
            '"90:class_g = \'Xfce4-terminal\'"',
            "xfce4-panel --quit",
        ]:
            self.assertNotIn(forbidden, self.desktop)
        for marker in [
            "inactive-opacity = 1.0",
            "active-opacity = 1.0",
            "frame-opacity = 1.0",
            "X-Ming-Managed-Components=phone-desktop;plank;picom",
            "Exec=/usr/local/bin/ming-session-healthcheck --session",
        ]:
            self.assertIn(marker, self.desktop)


class DesktopPolishContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.phone = PHONE_DESKTOP.read_text(encoding="utf-8")
        cls.drawer = APP_DRAWER.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.apps = APPS_MODULE.read_text(encoding="utf-8")
        cls.desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        cls.ota = OTA_MODULE.read_text(encoding="utf-8")

    def test_plank_is_the_single_primary_dock(self):
        self.assertIn("Exec=/usr/local/bin/ming-session-healthcheck --session", self.desktop)
        self.assertIn("plank_window_visible", self.desktop)
        self.assertIn("IndicatorSize=4", self.desktop)
        self.assertIn("UrgentBounceTime=600", self.desktop)
        self.assertNotIn("Exec=/usr/local/bin/ming-dock-watchdog --session", self.desktop)

    def test_virtualbox_parent_click_fallback_is_deduplicated(self):
        self.assertIn('self.fixed.connect("button-release-event", self.on_fixed_button_release)', self.phone)
        self.assertIn("dispatch_activation", self.phone)
        self.assertIn("activation_consumed", self.phone)

    def test_desktop_canvas_owns_mouse_and_touch_hit_testing(self):
        self.assertIn('self.fixed.connect("touch-event", self.on_fixed_touch)', self.phone)
        self.assertIn("def on_fixed_touch", self.phone)
        self.assertIn("self.fixed_touch_state = InteractionState()", self.phone)
        render = self.phone[self.phone.index("    def render(self):"):
                            self.phone.index("    def place_overlays", self.phone.index("    def render(self):"))]
        self.assertIn("DesktopTile(self, item)", render)
        self.assertIn("self.fixed.put(tile", render)

    def test_parent_click_fallback_translates_child_window_coordinates(self):
        self.assertIn("def fixed_event_coords", self.phone)
        self.assertIn("event_window.get_origin()", self.phone)
        self.assertIn("fixed_window.get_origin()", self.phone)
        self.assertIn('self.fixed.connect("button-press-event", self.on_fixed_button_press)', self.phone)
        self.assertIn('self.fixed.connect("motion-notify-event", self.on_fixed_motion)', self.phone)

    def test_root_window_events_fall_back_to_canvas_for_virtualbox_input(self):
        """No-window Gtk.Fixed children must not make the desktop inert.

        VirtualBox/Xrender can deliver an icon click to the toplevel desktop
        window instead of the transparent EventBox or Gtk.Fixed.  The root
        window therefore needs an explicit mouse/touch route to the same
        canvas state machine, including a live Cairo drag preview.
        """
        init = self.phone[
            self.phone.index("class PhoneDesktop"):
            self.phone.index("    @property\n    def window_origin")
        ]
        for marker in [
            'self.connect("button-press-event", self.on_window_button_press)',
            'self.connect("motion-notify-event", self.on_window_motion)',
            'self.connect("button-release-event", self.on_window_button_release)',
            'self.connect("touch-event", self.on_window_touch)',
        ]:
            self.assertIn(marker, init)
        self.assertIn("def on_window_button_press", self.phone)
        self.assertIn("def event_targets_root_canvas", self.phone)
        self.assertIn("def preview_drag", self.phone)
        fixed_motion = self.phone[
            self.phone.index("    def on_fixed_motion"):
            self.phone.index("    def on_fixed_button_release", self.phone.index("    def on_fixed_motion"))
        ]
        self.assertIn("self.preview_drag", fixed_motion)

    def test_compatibility_mouse_activation_is_deduplicated_by_item(self):
        self.assertIn("ACTIVATION_DEDUP_MS", self.phone)
        self.assertIn("item_key", self.phone)
        self.assertIn("event_time == previous_event_time", self.phone)

    def test_desktop_has_one_ming_trash_tile_and_filters_wrapper_aliases(self):
        self.assertIn('"ming-trash.desktop"', self.phone)
        self.assertIn('"ming-control-center.desktop"', self.phone)
        self.assertIn("DESKTOP_WRAPPER_ALIASES", self.phone)
        self.assertIn("user-trash", self.desktop)
        self.assertIn("trash:///", self.desktop)

    def test_launch_feedback_has_a_bounded_window_aware_lifetime(self):
        self.assertIn("LAUNCH_FEEDBACK_TIMEOUT_MS = 4000", self.phone)
        self.assertIn("class LaunchFeedbackOverlay", self.phone)
        self.assertIn("window_is_ready", self.phone)
        self.assertIn("启动时间较长，应用会继续在后台打开", self.phone)

    def test_launch_feedback_window_probe_does_not_block_gtk(self):
        self.assertIn("def start_window_probe", self.phone)
        self.assertIn("threading.Thread(target=self.check_window_ready", self.phone)
        self.assertIn("GLib.idle_add(self.apply_window_probe", self.phone)
        self.assertIn("self.launch_feedback.set_sensitive(False)", self.phone)

    def test_launch_feedback_reveals_and_hides_with_a_bounded_transition(self):
        """A successful click must retain visible launch feedback instead of popping in and out."""
        overlay = self.phone[
            self.phone.index("class LaunchFeedbackOverlay"):
            self.phone.index("class StatusSlider")
        ]
        self.assertIn("Gtk.Revealer()", overlay)
        self.assertIn("set_transition_duration(180)", overlay)
        self.assertIn("set_reveal_child(True)", overlay)
        self.assertIn("set_reveal_child(False)", overlay)

    def test_render_keeps_idle_launch_feedback_hidden(self):
        self.assertIn("if not self.launch_feedback.item:", self.phone)
        self.assertIn("self.launch_feedback.hide()", self.phone)

    def test_desktop_launch_error_dialog_is_an_opaque_feedback_surface(self):
        open_item = self.phone[
            self.phone.index("    def open_item(self, item):"):
            self.phone.index("    def show_folder", self.phone.index("    def open_item(self, item):"))
        ]
        self.assertIn('add_class("ming-launch-error-dialog")', open_item)
        self.assertIn(".ming-launch-error-dialog", self.phone)

    def test_status_widget_exposes_radio_battery_and_settings(self):
        self.assertIn("class StatusWidget", self.phone)
        for marker in ["nmcli", "bluetoothctl", "upower", "ming-control-center"]:
            self.assertIn(marker, self.phone)

    def test_status_wifi_button_uses_ming_diagnostics_not_empty_nm_editor(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        self.assertIn('self.wifi_button = self.action_button("Wi-Fi --", "ming-control-center")', status)
        self.assertNotIn('"nm-connection-editor"', status)

    def test_status_widget_exposes_safe_power_menu(self):
        self.assertIn("self.power_button", self.phone)
        self.assertIn("xfce4-session-logout", self.phone)
        self.assertIn("gnome-session-quit", self.phone)

    def test_spark_daemonized_zero_exit_is_success(self):
        self.assertIn('if [[ "${rc}" -eq 0 ]]; then', self.apps)
        self.assertIn("Spark Store launcher daemonized successfully", self.apps)
        self.assertIn("pgrep -f", self.apps)
        self.assertIn("wmctrl -lx", self.apps)

    def test_settings_and_app_drawer_fit_the_monitor_workarea(self):
        self.assertIn("responsive_window_size", self.settings)
        self.assertNotIn("self.set_default_size(1000, 700)", self.settings)
        self.assertIn("monitor.get_workarea()", self.drawer)
        self.assertIn("geometry = drawer_geometry(self._workarea())", self.drawer)
        self.assertIn("self.window.resize(int(geometry.width), int(geometry.height))", self.drawer)

        spec = importlib.util.spec_from_file_location("ming_app_drawer_workarea_contract", APP_DRAWER)
        drawer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drawer)
        # The GDK workarea excludes the Dock and any other reserved screen edge.
        dock_reserved_workarea = {"x": 8, "y": 24, "width": 1350, "height": 680}
        geometry = drawer.drawer_geometry(dock_reserved_workarea)

        self.assertEqual(8.0, geometry.x)
        self.assertEqual(1350.0, geometry.width)
        self.assertEqual(round(680 * drawer.DRAWER_HEIGHT_RATIO), geometry.height)
        self.assertEqual(704.0, geometry.y + geometry.height)
        self.assertLess(geometry.y + geometry.height, 768.0)

    def test_ota_resolves_home_before_user_paths(self):
        home_resolution = 'HOME="${HOME:-$(resolve_home)}"'
        user_config = 'readonly USER_CONFIG_DIR="${HOME}/.config/ming-update"'
        self.assertIn("resolve_home()", self.ota)
        self.assertIn(home_resolution, self.ota)
        self.assertLess(self.ota.index(home_resolution), self.ota.index(user_config))

    def test_privileged_ota_install_finds_unprivileged_manifest(self):
        self.assertIn("find_cached_manifest()", self.ota)
        self.assertIn("/home/*/.cache/ming-update/update_info.json", self.ota)
        major = self.ota[self.ota.index("major_install_with_home_backup()"):
                         self.ota.index('case "${1:-help}" in')]
        self.assertIn("manifest=$(find_cached_manifest)", major)

    def test_edge_and_spark_have_vm_safe_wrappers(self):
        for marker in [
            "homepage=/usr/share/ming-os/homepage/index.html",
            'if [[ "$#" -eq 0 ]]',
            "ming-spark-store",
            "MING_SPARK_LOG",
            "--ozone-platform=x11",
            "--disable-gpu",
        ]:
            self.assertIn(marker, self.apps)

    def test_edge_is_excluded_from_compositor_borders(self):
        self.assertGreaterEqual(self.desktop.count("class_g = 'Microsoft-edge'"), 3)
        self.assertIn("shadow-exclude", self.desktop)
        self.assertIn("rounded-corners-exclude", self.desktop)


class InstallerBootContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = BASE_MODULE.read_text(encoding="utf-8")
        cls.desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        cls.build = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_installer_never_ejects_the_live_root_before_reboot(self):
        start = self.base.index("cat > /usr/local/sbin/ming-finish-install-reboot")
        end = self.base.index("FINISHREBOOT", start + 64)
        finish_script = self.base[start:end]
        self.assertNotIn("eject ", finish_script)
        self.assertIn("systemctl -i reboot", finish_script)
        self.assertIn("must not eject the mounted live medium", self.build)

    def test_identity_and_root_uuid_are_finalized_before_grub_install(self):
        expected = (
            "  - shellprocess@ming-identity\n"
            "  - shellprocess@ming-installed-desktop-gate\n"
            "  - shellprocess@ming-bootloader"
        )
        self.assertIn(expected, self.desktop)
        self.assertGreaterEqual(self.desktop.count(expected), 2)
        self.assertIn("installed desktop verification must pass before GRUB installation", self.build)

    def test_bios_grub_uses_target_environment_and_rejects_bad_config(self):
        start = self.base.index("install_bios_grub()")
        end = self.base.index("prefer_ming_uefi_boot()", start)
        bios_function = self.base[start:end]
        self.assertLess(
            bios_function.index('chroot "${root}" /usr/sbin/grub-install'),
            bios_function.index("command -v grub-install"),
        )
        self.assertIn("grub-script-check", self.base)
        self.assertIn("exit 22", self.base)

    def test_uefi_install_never_falls_back_to_bios_grub(self):
        start = self.base.index("if [ -d /sys/firmware/efi ]; then", self.base.index("install_uefi_grub()"))
        end = self.base.index("# A GRUB core", start)
        firmware_branch = self.base[start:end]
        uefi_branch, bios_branch = firmware_branch.split("\nelse\n", 1)
        self.assertNotIn("install_bios_grub", uefi_branch)
        self.assertNotIn("falling back to BIOS", uefi_branch)
        self.assertIn("install_uefi_grub", uefi_branch)
        self.assertIn("install_bios_grub", bios_branch)

    def test_bios_grub_requires_one_recursive_physical_disk_ancestor(self):
        self.assertIn("resolve_boot_disk()", self.base)
        start = self.base.index("resolve_boot_disk()")
        end = self.base.index("install_uefi_grub()", start)
        resolver = self.base[start:end]
        self.assertIn('lsblk -s -nrpo NAME,TYPE "${root_source}"', resolver)
        self.assertIn("physical_disks", resolver)
        self.assertIn('"${#physical_disks[@]}" -ne 1', resolver)
        self.assertIn('boot_disk="${physical_disks[0]}"', resolver)


class HardwareAndWirelessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = BASE_MODULE.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.build = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_core_wifi_firmware_is_mandatory_and_validated(self):
        self.assertIn("install_required_wifi_firmware", self.base)
        for package in [
            "firmware-iwlwifi",
            "firmware-realtek",
            "firmware-atheros",
            "firmware-brcm80211",
        ]:
            self.assertIn(package, self.base)
            self.assertIn(package, self.build)

    def test_network_page_explains_empty_wifi_state(self):
        for marker in [
            "wifi_diagnostic_snapshot",
            "未检测到无线网卡",
            "硬件无线开关或 BIOS",
            "缺少固件",
            "rfkill",
        ]:
            self.assertIn(marker, self.settings)
        wifi_helper = self.settings[self.settings.index("def wifi_diagnostic_snapshot"):
                                    self.settings.index("class MingSettings")]
        self.assertIn("lsusb", wifi_helper)
        self.assertIn("USB", wifi_helper)

    def test_hardware_page_lists_platform_and_bound_drivers(self):
        for marker in [
            "硬件状态",
            "设备卡片",
            "ming-hardware-status",
            "型号：%s · 驱动：%s · 建议：%s",
            "正常、注意或失败",
            "原始诊断",
        ]:
            self.assertIn(marker, self.settings)


if __name__ == "__main__":
    unittest.main()
