import ast
import json
import os
import pathlib
import shutil
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PHONE_DESKTOP = ROOT / "assets" / "ming-phone-desktop.py"
APP_DRAWER = ROOT / "assets" / "ming-app-drawer.py"
SETTINGS = ROOT / "assets" / "ming-settings.py"
APPS_MODULE = ROOT / "modules" / "02_apps.sh"
DESKTOP_MODULE = ROOT / "modules" / "03_desktop.sh"
FINALIZE_MODULE = ROOT / "modules" / "07_finalize.sh"
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

    def test_visible_cairo_desktop_labels_use_the_noto_family(self):
        fallback = self.phone[
            self.phone.index("def draw_icon_fallback"):
            self.phone.index("def item_at")
        ]
        self.assertIn('DESKTOP_LABEL_FONT = "Noto Sans CJK SC Medium 10"', self.phone)
        self.assertIn("Pango.FontDescription(DESKTOP_LABEL_FONT)", fallback)

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

    def test_shell_typography_uses_noto_and_lighter_readable_weights(self):
        css = self.phone[self.phone.index('CSS = b"""'):self.phone.index('"""\n\n\ndef log')]
        self.assertIn('font-family: "Noto Sans CJK SC", sans-serif;', css)
        self.assertIn("font-weight: 500;", css)
        self.assertIn("padding: 8px 12px;", css)
        self.assertIn("padding: 12px 16px;", css)
        self.assertNotIn("font-weight: 800;", css)

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
            '"ming-firefox.desktop": "browser"',
            '"firefox-esr.desktop": "browser"',
            '"papyrus.desktop": "agent"',
            'basename == "ming-update.desktop"',
        ]:
            self.assertIn(marker, self.drawer)
        self.assertNotIn("garlic-claw.desktop", self.drawer)

    def test_phone_desktop_deduplicates_core_application_families(self):
        for marker in (
            "CANONICAL_LAUNCHERS",
            '"ming-firefox.desktop": "browser"',
            '"firefox-esr.desktop": "browser"',
            '"papyrus.desktop": "agent"',
            "def deduplicate_apps(apps):",
        ):
            self.assertIn(marker, self.phone)

    def test_finalizer_clears_all_managed_desktop_state_for_live_and_skel_users(self):
        finalizer = FINALIZE_MODULE.read_text(encoding="utf-8")
        cleanup = finalizer[
            finalizer.index("constrain_default_desktop()"):
            finalizer.index("repair_default_user_ownership()")
        ]
        for state_file in (
            "desktop-layout.json",
            "desktop-layout.last-good.json",
            "desktop-generated-manifest.json",
        ):
            self.assertIn(state_file, cleanup)
        self.assertIn('${USER_HOME}/.config/ming-os', cleanup)
        self.assertIn('/etc/skel/.config/ming-os', cleanup)

    def test_organizer_does_not_create_a_second_settings_launcher(self):
        organizer_start = self.desktop.index("cat > /usr/local/bin/ming-desktop-organizer")
        organizer_body_start = self.desktop.index("\n", organizer_start) + 1
        organizer = self.desktop[organizer_body_start:self.desktop.index("\nDESKORG", organizer_body_start)]
        self.assertNotIn('cat > "${desktop}/Ming 设置.desktop"', organizer)
        self.assertNotIn('ln -sfn "${item}"', organizer)
        self.assertIn('legacy_common_settings="${common_dir}/Ming 设置.desktop"', organizer)
        self.assertIn('[[ "${legacy_settings_is_managed}" == true && -L "${legacy_common_settings}" ]]', organizer)
        self.assertIn('readlink -- "${legacy_common_settings}"', organizer)
        self.assertIn('[[ "${legacy_common_target}" == "${legacy_settings}" ]]', organizer)

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

    def test_shell_launches_use_socket_ack_and_direct_fallback(self):
        common = (ROOT / "assets" / "ming-shell-common.py").read_text(encoding="utf-8")
        self.assertIn("def send_launch_request", common)
        self.assertIn("COMMON = load_shell_common()", self.phone)
        self.assertIn("COMMON.send_launch_request", self.phone)
        self.assertIn("COMMON.send_launch_request", self.drawer)
        self.assertIn("无法打开此应用", self.drawer)

    def test_power_button_uses_ming_menu_before_session_logout_actions(self):
        power_menu = self.phone[
            self.phone.index("    def open_power_menu"):
            self.phone.index("    def refresh", self.phone.index("    def open_power_menu"))
        ]
        self.assertIn("show_ming_power_menu", self.phone)
        self.assertIn("include_update", self.phone)
        self.assertNotIn('["xfce4-session-logout"]', power_menu)
        self.assertNotIn("gnome-session-quit", power_menu)
        self.assertNotIn("mate-session-save", power_menu)
        self.assertNotIn("lxqt-leave", power_menu)

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
            "layout_item_identity",
            "retired_layout_item",
            "deduplicate_layout_items",
            "is_system_application_path",
            "managed_desktop_source_path",
            "layout_effective_path",
            "_desktop_has_marker",
            "desktop_entry_identity_fields",
            "legacy_managed_source_path",
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
                {"id": "app-c", "type": "app", "path": "/tmp/c.desktop", "x": 417, "y": 233},
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

    def test_bad_primary_restores_deduplicated_last_good_without_dropping_custom_entries(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id", "_item_id", "empty_layout", "layout_is_valid", "migrate_layout",
            "read_layout", "load_layout", "canonical_identity", "layout_item_identity",
            "deduplicate_layout_items", "retired_layout_item", "is_system_application_path",
            "managed_desktop_source_path", "layout_effective_path", "_desktop_has_marker",
            "desktop_entry_identity_fields", "legacy_managed_source_path",
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
            root = pathlib.Path(temp_dir)
            state_dir = root / "state"
            system_dir = root / "usr" / "share" / "applications"
            desktop = root / "home" / "user" / "Desktop"
            local_apps = root / "home" / "user" / ".local" / "share" / "applications"
            for directory in (state_dir, system_dir, desktop, local_apps):
                directory.mkdir(parents=True, exist_ok=True)
            namespace["SYSTEM_APPLICATION_DIR"] = system_dir
            namespace["DESKTOP_DIR"] = desktop
            namespace["LAYOUT_PATH"] = state_dir / "desktop-layout.json"
            namespace["LAST_GOOD_LAYOUT_PATH"] = state_dir / "desktop-layout.last-good.json"
            namespace["LAYOUT_PATH"].write_text("{not json", encoding="utf-8")

            system_firefox = system_dir / "ming-firefox.desktop"
            system_papyrus = system_dir / "papyrus.desktop"
            system_settings = system_dir / "ming-settings.desktop"
            system_edge = system_dir / "microsoft-edge.desktop"
            system_agent = system_dir / "openclaw.desktop"
            for launcher in (system_firefox, system_papyrus, system_settings, system_edge, system_agent):
                launcher.write_text("[Desktop Entry]\nType=Application\nName=Test\nExec=python -V\n", encoding="utf-8")

            browser_copy = desktop / "Firefox ESR.desktop"
            agent_copy = desktop / "Papyrus.desktop"
            settings_copy = desktop / "Ming 设置.desktop"
            for proxy, source_path in (
                (browser_copy, system_firefox),
                (agent_copy, system_papyrus),
                (settings_copy, system_settings),
            ):
                proxy.write_text(
                    "[Desktop Entry]\nType=Application\nName=Proxy\nExec=python -V\n"
                    "X-Ming-Managed=true\n",
                    encoding="utf-8",
                )

            user_papyrus = local_apps / "papyrus.desktop"
            user_edge = local_apps / "microsoft-edge.desktop"
            user_agent = local_apps / "openclaw.desktop"
            for launcher in (user_papyrus, user_edge, user_agent):
                launcher.write_text("[Desktop Entry]\nType=Application\nName=User\nExec=python -V\n", encoding="utf-8")
            modified_dir = desktop / "modified"
            modified_dir.mkdir()
            modified_legacy = modified_dir / "Ming 设置.desktop"
            modified_legacy.write_text(
                "[Desktop Entry]\nType=Application\nName=User Settings\nExec=python -c changed\nX-Ming-Managed=true\n",
                encoding="utf-8",
            )
            modified_source = modified_dir / "Firefox ESR.desktop"
            modified_source.write_text(
                "[Desktop Entry]\nType=Application\nName=User Browser\nExec=python -c changed\n"
                "X-Ming-Managed=true\nX-Ming-Source-Desktop=%s\n" % system_firefox,
                encoding="utf-8",
            )
            unmarked_desktop = modified_dir / "Papyrus.desktop"
            unmarked_desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=User Agent\nExec=python -c custom\n",
                encoding="utf-8",
            )
            last_good = {
                "version": 7,
                "items": [
                    {"id": "browser-position", "type": "app", "path": str(system_firefox), "x": 34, "y": 92},
                    {"id": "browser-copy", "type": "app", "path": str(browser_copy), "x": 126, "y": 92},
                    {"id": "agent-position", "type": "app", "path": str(system_papyrus), "x": 218, "y": 92},
                    {"id": "agent-copy", "type": "app", "path": str(agent_copy), "x": 310, "y": 92},
                    {"id": "settings-position", "type": "app", "path": str(system_settings), "x": 402, "y": 92},
                    {"id": "settings-copy", "type": "app", "path": str(settings_copy), "x": 494, "y": 92},
                    {"id": "user-papyrus", "type": "app", "path": str(user_papyrus), "x": 34, "y": 200},
                    {"id": "user-edge", "type": "app", "path": str(user_edge), "x": 126, "y": 200},
                    {"id": "user-agent", "type": "app", "path": str(user_agent), "x": 218, "y": 200},
                    {"id": "modified-legacy", "type": "app", "path": str(modified_legacy), "x": 310, "y": 200},
                    {"id": "modified-source", "type": "app", "path": str(modified_source), "x": 402, "y": 200},
                    {"id": "unmarked-desktop", "type": "app", "path": str(unmarked_desktop), "x": 494, "y": 200},
                    {"id": "retired-edge", "type": "app", "path": str(system_edge), "x": 310, "y": 200},
                    {"id": "retired-agent", "type": "app", "path": str(system_agent), "x": 402, "y": 200},
                ],
            }
            namespace["LAST_GOOD_LAYOUT_PATH"].write_text(json.dumps(last_good), encoding="utf-8")
            restored = namespace["load_layout"]()
            namespace["LAST_GOOD_LAYOUT_PATH"].write_text(json.dumps({
                "version": 7,
                "items": [{
                    "id": "single-managed-copy",
                    "type": "app",
                    "path": str(browser_copy),
                    "x": 919,
                    "y": 515,
                    "pinned": True,
                }],
            }), encoding="utf-8")
            single_restored = namespace["load_layout"]()

        self.assertEqual(
            ["browser-copy", "agent-copy", "settings-copy", "user-papyrus", "user-edge", "user-agent",
             "modified-legacy", "modified-source", "unmarked-desktop"],
            [item["id"] for item in restored["items"]],
        )
        self.assertEqual(str(system_firefox), restored["items"][0]["path"])
        self.assertEqual((126, 92), (restored["items"][0]["x"], restored["items"][0]["y"]))
        self.assertEqual(str(system_papyrus), restored["items"][1]["path"])
        self.assertEqual(str(system_settings), restored["items"][2]["path"])
        restored_by_id = {item["id"]: item for item in restored["items"]}
        self.assertEqual(str(modified_legacy), restored_by_id["modified-legacy"]["path"])
        self.assertEqual(str(modified_source), restored_by_id["modified-source"]["path"])
        self.assertEqual(str(unmarked_desktop), restored_by_id["unmarked-desktop"]["path"])
        self.assertEqual("single-managed-copy", single_restored["items"][0]["id"])
        self.assertEqual(str(system_firefox), single_restored["items"][0]["path"])
        self.assertEqual((919, 515, True), (
            single_restored["items"][0]["x"],
            single_restored["items"][0]["y"],
            single_restored["items"][0]["pinned"],
        ))

    def test_sync_layout_canonicalizes_old_managed_copies_without_losing_positions(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id", "legacy_desktop_entry", "read_app", "add_app_from_path",
            "canonical_identity", "deduplicate_apps", "load_apps", "empty_layout",
            "layout_is_valid", "_item_id", "layout_item_identity", "retired_layout_item",
            "deduplicate_layout_items", "migrate_layout", "read_layout", "load_layout",
            "_atomic_write_json", "save_layout", "next_position", "sync_layout",
            "_desktop_has_marker", "is_system_application_path", "managed_desktop_source_path",
            "layout_effective_path", "canonicalize_core_layout_item",
            "desktop_entry_identity_fields", "legacy_managed_source_path",
        }
        body = [node for node in tree.body if isinstance(node, ast.Assign)]
        body.extend(node for node in tree.body if isinstance(node, ast.Import) and all(alias.name != "gi" for alias in node.names))
        body.extend(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module != "gi.repository")
        body.extend(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted)
        namespace = {"Path": pathlib.Path, "load_shell_common": lambda: None, "__file__": str(PHONE_DESKTOP), "log": lambda *_args: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PHONE_DESKTOP), "exec"), namespace)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            system_dir = root / "usr" / "share" / "applications"
            desktop = root / "home" / "user" / "Desktop"
            state_dir = root / "state"
            for directory in (system_dir, desktop, state_dir):
                directory.mkdir(parents=True, exist_ok=True)

            def write_launcher(path, name):
                path.write_text("[Desktop Entry]\nType=Application\nName=%s\nExec=python -V\n" % name, encoding="utf-8")

            system_firefox = system_dir / "ming-firefox.desktop"
            system_papyrus = system_dir / "papyrus.desktop"
            write_launcher(system_firefox, "Firefox ESR")
            write_launcher(system_papyrus, "Papyrus")
            browser_copy = desktop / "Firefox ESR.desktop"
            agent_copy = desktop / "Papyrus.desktop"
            for proxy, source_path, name in (
                (browser_copy, system_firefox, "Firefox ESR"),
                (agent_copy, system_papyrus, "Papyrus"),
            ):
                write_launcher(proxy, name)
                with proxy.open("a", encoding="utf-8") as handle:
                    handle.write("X-Ming-Managed=true\n")

            managed_first = [
                    {"id": "old-browser", "type": "app", "path": str(browser_copy), "x": 777, "y": 333, "pinned": True},
                    {"id": "tools", "type": "folder", "name": "工具", "children": [str(agent_copy)], "x": 600, "y": 410, "pinned": True},
                    {"id": "new-browser", "type": "app", "path": str(system_firefox), "x": 34, "y": 92, "pinned": False},
                    {"id": "new-agent", "type": "app", "path": str(system_papyrus), "x": 126, "y": 92, "pinned": False},
            ]
            managed_later = [
                    {"id": "new-browser", "type": "app", "path": str(system_firefox), "x": 34, "y": 92, "pinned": False},
                    {"id": "new-agent", "type": "app", "path": str(system_papyrus), "x": 126, "y": 92, "pinned": False},
                    {"id": "old-browser", "type": "app", "path": str(browser_copy), "x": 777, "y": 333, "pinned": True},
                    {"id": "tools", "type": "folder", "name": "工具", "children": [str(agent_copy)], "x": 600, "y": 410, "pinned": True},
            ]
            namespace["SYSTEM_APPLICATION_DIR"] = system_dir
            namespace["DESKTOP_DIR"] = desktop
            namespace["APP_DIRS"] = [desktop, system_dir]
            namespace["LAYOUT_PATH"] = state_dir / "desktop-layout.json"
            namespace["LAST_GOOD_LAYOUT_PATH"] = state_dir / "desktop-layout.last-good.json"
            namespace["sync_files"] = lambda _layout: None
            for order, items in (("managed-first", managed_first), ("managed-later", managed_later)):
                with self.subTest(order=order):
                    layout = {"version": 7, "catalog_paths": [], "items": items}
                    namespace["LAYOUT_PATH"].write_text(json.dumps(layout), encoding="utf-8")
                    restored = namespace["sync_layout"]()
                    self.assertEqual(["old-browser", "tools"], [item["id"] for item in restored["items"]])
                    self.assertEqual(str(system_firefox), restored["items"][0]["path"])
                    self.assertEqual((777, 333, True), (restored["items"][0]["x"], restored["items"][0]["y"], restored["items"][0]["pinned"]))
                    self.assertEqual((600, 410), (restored["items"][1]["x"], restored["items"][1]["y"]))
                    self.assertEqual([str(system_papyrus)], restored["items"][1]["children"])

    def test_layout_save_is_atomic_and_bad_primary_keeps_last_good(self):
        source = PHONE_DESKTOP.read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {
            "app_id", "_item_id", "empty_layout", "layout_is_valid", "migrate_layout",
            "read_layout", "load_layout", "_atomic_write_json", "save_layout",
            "layout_item_identity", "retired_layout_item", "deduplicate_layout_items",
            "is_system_application_path", "managed_desktop_source_path",
            "layout_effective_path", "_desktop_has_marker",
            "desktop_entry_identity_fields", "legacy_managed_source_path",
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
            "_mark_desktop_file", "copy_desktop", "is_system_application_path",
            "managed_desktop_source_path", "write_desktop_source_marker",
            "desktop_entry_identity_fields", "legacy_managed_source_path",
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
            namespace["SYSTEM_APPLICATION_DIR"] = source_dir
            namespace["DESKTOP_MANIFEST_PATH"] = manifest
            layout = {"items": [{"id": "alpha", "type": "app", "path": str(app), "name": "Alpha", "pinned": True}]}
            namespace["sync_files"](layout)
            self.assertTrue(user.exists())
            self.assertFalse(stale.exists())
            generated = desktop / "Alpha.desktop"
            self.assertTrue(generated.exists())
            self.assertIn("X-Ming-Managed=true", generated.read_text(encoding="utf-8"))
            self.assertIn("X-Ming-Source-Desktop=%s" % app.resolve(), generated.read_text(encoding="utf-8"))
            self.assertEqual(app.resolve(), namespace["managed_desktop_source_path"](generated))
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

    def test_cairo_icon_renderer_falls_back_when_an_app_icon_is_missing(self):
        draw = self.phone[self.phone.index("    def draw_icon_fallback"):
                          self.phone.index("    def item_at", self.phone.index("    def draw_icon_fallback"))]
        self.assertIn('fallback_icon = "application-x-executable"', draw)
        self.assertIn("for candidate in (icon_name, fallback_icon):", draw)
        self.assertIn("pixbuf = icon_theme.load_icon(candidate, ICON_SIZE", draw)

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
            'pkill -TERM -u "$(id -u)" -x xfce4-panel',
        ]:
            self.assertIn(marker, self.desktop)


class DesktopPolishContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.phone = PHONE_DESKTOP.read_text(encoding="utf-8")
        cls.settings = SETTINGS.read_text(encoding="utf-8")
        cls.apps = APPS_MODULE.read_text(encoding="utf-8")
        cls.desktop = DESKTOP_MODULE.read_text(encoding="utf-8")
        cls.ota = OTA_MODULE.read_text(encoding="utf-8")

    def test_plank_is_the_single_primary_dock(self):
        self.assertIn("Exec=/usr/local/bin/ming-session-healthcheck --session", self.desktop)
        self.assertIn("plank_window_visible", self.desktop)
        self.assertIn("IndicatorSize=4", self.desktop)
        self.assertIn("UrgentBounceTime=420", self.desktop)
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

    def test_launch_feedback_has_a_bounded_window_aware_lifetime(self):
        self.assertIn("LAUNCH_FEEDBACK_TIMEOUT_MS = 4000", self.phone)
        self.assertIn("class LaunchFeedbackOverlay", self.phone)
        self.assertIn("window_is_ready", self.phone)
        self.assertIn("启动时间较长，应用会继续在后台打开", self.phone)

    def test_launch_feedback_bounds_long_titles_and_details_inside_its_fixed_area(self):
        overlay = self.phone[
            self.phone.index("class LaunchFeedbackOverlay"):
            self.phone.index("class StatusWidget")
        ]
        self.assertIn("self.title.set_ellipsize(Pango.EllipsizeMode.END)", overlay)
        self.assertIn("self.title.set_max_width_chars(20)", overlay)
        self.assertIn("self.detail.set_lines(2)", overlay)
        self.assertIn("self.detail.set_ellipsize(Pango.EllipsizeMode.END)", overlay)

    def test_launch_feedback_window_probe_does_not_block_gtk(self):
        self.assertIn("def start_window_probe", self.phone)
        self.assertIn("threading.Thread(target=self.check_window_ready", self.phone)
        self.assertIn("GLib.idle_add(self.apply_window_probe", self.phone)
        self.assertIn("self.launch_feedback.set_sensitive(False)", self.phone)

    def test_render_keeps_idle_launch_feedback_hidden(self):
        self.assertIn("if not self.launch_feedback.item:", self.phone)
        self.assertIn("self.launch_feedback.hide()", self.phone)

    def test_status_widget_exposes_radio_battery_and_settings(self):
        self.assertIn("class StatusWidget", self.phone)
        for marker in ["nmcli", "bluetoothctl", "upower", "ming-control-center"]:
            self.assertIn(marker, self.phone)

    def test_status_widget_shows_battery_only_for_portable_host(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        self.assertIn("self.header_battery_label", status)
        self.assertIn("self.compact_battery_label", status)
        self.assertIn('battery.get("portable")', status)
        self.assertIn("self.header_battery_label.set_visible(show_battery)", status)
        self.assertIn("self.compact_battery_label.set_visible(show_battery)", status)
        self.assertIn("self.header_battery_label.set_text(battery_text)", status)
        self.assertNotIn("self.battery_label = self.resource_label", status)

    def test_collapsed_status_widget_refreshes_only_laptop_battery_in_background(self):
        status = self.phone[self.phone.index("class StatusWidget"):
                            self.phone.index("class WallpaperCanvas")]
        refresh = status[status.index("    def refresh(self):"):
                         status.index("    def collect_status", status.index("    def refresh(self):"))]
        self.assertIn("self.refresh_battery_status()", refresh)
        self.assertIn("def refresh_battery_status", status)
        self.assertIn("threading.Thread(target=self.collect_battery_status, daemon=True).start()", status)
        self.assertIn("self.device_controller.battery_status()", status)

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

    def test_settings_and_app_library_fit_the_monitor_workarea(self):
        self.assertIn("responsive_window_size", self.settings)
        self.assertNotIn("self.set_default_size(1000, 700)", self.settings)
        self.assertIn("responsive_window_size", self.desktop)
        self.assertNotIn("self.set_default_size(840, 560)", self.desktop)

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
                         self.ota.index("auto_shutdown_update()")]
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

    def test_firefox_is_excluded_from_compositor_borders(self):
        self.assertGreaterEqual(self.desktop.count("class_g = 'Firefox'"), 3)
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
        expected = "  - shellprocess@ming-identity\n  - shellprocess@ming-bootloader"
        self.assertIn(expected, self.base)
        self.assertGreaterEqual(self.desktop.count(expected), 2)

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
