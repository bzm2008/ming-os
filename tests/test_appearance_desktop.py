import importlib.util
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest
import ast
import base64
import struct
import threading
import time
import sys


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
            png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            svg = root / "wechat.svg"
            svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16'/>", encoding="utf-8")
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
            icon.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            self.assertEqual("utilities-terminal", common.resolve_icon("utilities-terminal"))
            self.assertEqual(str(icon), common.resolve_icon("wechat.png", pixmap_dirs=[pixmaps]))
            self.assertEqual("fallback", common.resolve_icon("missing.png", fallback="fallback", pixmap_dirs=[pixmaps]))

    def test_real_png_dimensions_and_malicious_svg_are_bounded(self):
        common = load_common()
        real_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            good = root / "good.png"
            good.write_bytes(real_png)
            huge = root / "huge.png"
            data = bytearray(real_png)
            data[16:24] = struct.pack(">II", 100000, 100000)
            huge.write_bytes(data)
            svg = root / "bad.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="999999" height="999999"/>', encoding="utf-8")
            self.assertEqual(str(good), common.resolve_icon(str(good)))
            self.assertEqual("fallback", common.resolve_icon(str(huge), fallback="fallback"))
            self.assertEqual("fallback", common.resolve_icon(str(svg), fallback="fallback"))


class AppearanceControlTests(unittest.TestCase):
    def run_control(self, home, *args):
        env = dict(os.environ, HOME=str(home), MING_APPEARANCE_NO_APPLY="1")
        return subprocess.run(
            [sys.executable, str(CONTROL), *args, "--json"], env=env, text=True,
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

    def test_visual_profile_round_trips_and_migrates_legacy_values(self):
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            legacy_path = home / ".config/ming-os/appearance.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text(json.dumps({
                "desktop_icon_scale": 1.25,
                "compositor_profile": "software",
            }), encoding="utf-8")

            result = self.run_control(
                home, "apply", "--desktop-icon-size", "56", "--motion", "reduced",
                "--compositor-profile", "compat")
            self.assertEqual(0, result.returncode, result.stderr)
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual(2, status["version"])
            self.assertEqual("Noto Sans CJK SC", status["font_family"])
            self.assertEqual(56, status["desktop_icon_size"])
            self.assertEqual("reduced", status["motion"])
            self.assertEqual("compat", status["compositor_profile"])
            self.assertEqual(1.25, status["desktop_icon_scale"])

    def test_invalid_primary_appearance_uses_last_known_good_config(self):
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            result = self.run_control(home, "apply", "--theme", "dark", "--font-size", "14")
            self.assertEqual(0, result.returncode, result.stderr)
            primary = home / ".config/ming-os/appearance.json"
            backup = home / ".config/ming-os/appearance.last-good.json"
            self.assertTrue(backup.is_file())
            primary.write_text("{not valid json", encoding="utf-8")
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual("dark", status["theme"])
            self.assertEqual(14, status["font_size"])

    def test_import_wallpaper_creates_a_persistent_thumbnail_and_reset(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            source = home / "source.png"
            source.write_bytes(png)
            imported = self.run_control(home, "import-wallpaper", str(source))
            self.assertEqual(0, imported.returncode, imported.stderr)
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual(
                str(home / ".local/share/backgrounds/ming-os/custom-wallpaper.png"),
                status["wallpaper"],
            )
            self.assertTrue((home / ".cache/ming-os/wallpaper/custom-wallpaper-thumb.png").is_file())

            reset = self.run_control(home, "reset")
            self.assertEqual(0, reset.returncode, reset.stderr)
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual("default", status["wallpaper"])
            self.assertEqual("normal", status["motion"])
            self.assertEqual("auto", status["compositor_profile"])

    def test_invalid_custom_wallpaper_falls_back_without_replacing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            home = pathlib.Path(temp)
            bad = home / "not-image.txt"
            bad.write_text("not an image", encoding="utf-8")
            result = self.run_control(home, "apply", "--wallpaper", str(bad))
            self.assertNotEqual(0, result.returncode)
            status = json.loads(self.run_control(home, "status").stdout)
            self.assertEqual("default", status["wallpaper"])

    def test_atomic_writes_cleanup_and_fsync_parent_directory(self):
        source = CONTROL.read_text(encoding="utf-8")
        self.assertIn("def fsync_directory", source)
        self.assertIn('getattr(os, "O_DIRECTORY"', source)
        self.assertGreaterEqual(source.count("fsync_directory("), 3)
        atomic_text = source[source.index("def atomic_write_text"):source.index("def _replace_setting_line")]
        self.assertIn("finally:", atomic_text)
        self.assertIn("os.unlink(temporary)", atomic_text)

    def test_wallpaper_rejects_animation_and_oversized_png_metadata(self):
        spec = importlib.util.spec_from_file_location("appearance_wallpaper", CONTROL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            good = root / "good.png"
            good.write_bytes(real_png)
            huge = root / "huge.png"
            data = bytearray(real_png)
            data[16:24] = struct.pack(">II", 100000, 100000)
            huge.write_bytes(data)
            gif = root / "animated.gif"
            gif.write_bytes(b"GIF89a" + b"x" * 32)
            self.assertEqual((1, 1), module.safe_wallpaper_dimensions(good))
            with self.assertRaises(ValueError):
                module.safe_wallpaper_dimensions(huge)
            with self.assertRaises(ValueError):
                module.safe_wallpaper_dimensions(gif)
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        loader = phone[phone.index("    def load_wallpaper"):
                       phone.index("    def on_draw", phone.index("    def load_wallpaper"))]
        self.assertIn("new_from_file_at_scale", loader)
        self.assertNotIn("new_from_file(", loader)

    def test_runtime_font_updates_system_chrome_without_content_scaling(self):
        source = CONTROL.read_text(encoding="utf-8")
        runtime = source[source.index("def apply_runtime("):source.index("def parser():")]
        for marker in (
            "sync_gtk_font_settings",
            "org.gnome.desktop.interface",
            '"font-name"',
            "/general/title_font",
        ):
            self.assertIn(marker, runtime)
        for marker in (
            "gtk-font-name",
            ".config/gtk-3.0/settings.ini",
            ".config/gtk-4.0/settings.ini",
            ".gtkrc-2.0",
        ):
            self.assertIn(marker, source)
        for forbidden in ("text-scaling-factor", "document-font-name"):
            self.assertNotIn(forbidden, runtime)

    def test_ming_shell_font_metric_is_shared_and_bounded(self):
        common = load_common()
        self.assertEqual(9, common.appearance_font_size({"font_size": 3}))
        self.assertEqual(14, common.appearance_font_size({"font_size": 14}))
        self.assertEqual(18, common.appearance_font_size({"font_size": 99}))
        for path in ("assets/ming-phone-desktop.py", "assets/ming-app-drawer.py"):
            self.assertIn("COMMON.appearance_font_size", (ROOT / path).read_text(encoding="utf-8"))

    def test_dock_runtime_syncs_dconf_and_requests_bounded_single_instance_reload(self):
        control = CONTROL.read_text(encoding="utf-8")
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        runtime = control[control.index("def sync_dock_runtime(config"):
                          control.index("def parser():")]
        for marker in (
            "dconf",
            "/net/launchpad/plank/docks/dock1/icon-size",
            "dconf read",
            "--reload-dock",
            "ming-session-healthcheck",
        ):
            self.assertIn(marker, runtime)
        health_start = desktop.index("cat > /usr/local/bin/ming-session-healthcheck")
        health = desktop[health_start:desktop.index("\nMINGSESSIONHEALTH\n", health_start)]
        for marker in ("--reload-dock", "reload_dock()", "start_plank_dock", "PLANK_STARTUP_DEADLINE=8"):
            self.assertIn(marker, health)


class DeploymentContractTests(unittest.TestCase):
    def test_shell_consumers_use_shared_icon_resolver(self):
        for name in ("ming-phone-desktop.py", "ming-app-drawer.py", "ming-launch.py"):
            text = (ROOT / "assets" / name).read_text(encoding="utf-8")
            self.assertIn("resolve_icon", text, name)

    def test_rootfs_gate_scans_real_user_homes_and_skeleton(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("MING_USER"', build)
        self.assertIn('root / "home/user/.config/autostart"', build)
        self.assertIn('root / "etc/skel/.config/autostart"', build)
        self.assertNotIn('root / "home/ming/.config/autostart"', build)

    def test_xinput_is_required_and_rootfs_gated(self):
        apps = (ROOT / "modules/02_apps.sh").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("xinput", apps.split("REQUIRED_DESKTOP_RUNTIME_PACKAGES=(", 1)[1].split(")", 1)[0])
        self.assertIn('"usr/bin/xinput"', build)

    def test_autostart_gate_uses_structured_desktop_semantics(self):
        common = load_common()
        active = "[Desktop Entry]\nType=Application\nExec=nm-applet\n"
        hidden = active + "Hidden=true\n"
        disabled = active + "X-GNOME-Autostart-enabled=false\n"
        other = active + "OnlyShowIn=GNOME;\n"
        self.assertEqual("nm-applet", common.autostart_exec(active, "XFCE"))
        self.assertIsNone(common.autostart_exec(hidden, "XFCE"))
        self.assertIsNone(common.autostart_exec(disabled, "XFCE"))
        self.assertIsNone(common.autostart_exec(other, "XFCE"))
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("autostart_processes", build)

    def test_autostart_gate_matches_executable_not_arguments(self):
        common = load_common()
        echo = "[Desktop Entry]\nType=Application\nExec=echo xfdesktop\n"
        direct = "[Desktop Entry]\nType=Application\nExec=/usr/bin/xfdesktop --replace\n"
        shell = "[Desktop Entry]\nType=Application\nExec=sh -c 'xfdesktop --replace'\n"
        shell_exec = "[Desktop Entry]\nType=Application\nExec=sh -c 'exec xfdesktop --replace'\n"
        self.assertEqual((), common.autostart_processes(echo, "XFCE"))
        self.assertEqual(("xfdesktop",), common.autostart_processes(direct, "XFCE"))
        self.assertEqual(("xfdesktop",), common.autostart_processes(shell, "XFCE"))
        self.assertEqual(("xfdesktop",), common.autostart_processes(shell_exec, "XFCE"))
        later = "[Desktop Entry]\nType=Application\nExec=sh -c 'sleep 1; exec xfdesktop --replace'\n"
        chained = "[Desktop Entry]\nType=Application\nExec=sh -c 'echo ready && xfce4-panel'\n"
        wrapped = "[Desktop Entry]\nType=Application\nExec=env X=1 timeout 2 xfdesktop\n"
        self.assertEqual(("xfdesktop",), common.autostart_processes(later, "XFCE"))
        self.assertEqual(("xfce4-panel",), common.autostart_processes(chained, "XFCE"))
        self.assertEqual(("xfdesktop",), common.autostart_processes(wrapped, "XFCE"))
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("autostart_processes", build)

    def test_builtin_wallpapers_use_deployed_names_everywhere(self):
        paths = (
            "assets/ming-phone-desktop.py", "assets/ming-appearance-control.py",
            "assets/ming-settings.py", "modules/03_desktop.sh", "build_onion_os.sh")
        sources = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in paths)
        self.assertNotIn('/ming-os/light.png', sources)
        self.assertNotIn('/ming-os/dark.png', sources)
        for path in paths:
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn("default-light.png", source, path)
            self.assertIn("default-dark.png", source, path)
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        self.assertIn('[[ -s /usr/share/backgrounds/ming-os/default-light.png ]]', desktop)
        self.assertIn('[[ -s /usr/share/backgrounds/ming-os/default-dark.png ]]', desktop)

    def test_default_wallpaper_stages_resolution_cache_variants(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        setup = desktop[desktop.index("setup_wallpaper() {"):
                        desktop.index("# ======================== Xfce 顶部菜单栏", desktop.index("setup_wallpaper() {"))]
        for name in (
            "default-2640.png",
            "default-2633.png",
            "default-1366x768.png",
            "default-1920x1080.png",
            "default-3840x2160.png",
        ):
            self.assertIn(name, setup)
        self.assertIn('cp "${primary}" /usr/share/backgrounds/ming-os/default-3840x2160.png', setup)

        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("def require_png_dimensions", build)
        for dimensions in ("1366, 768", "1920, 1080", "3840, 2160"):
            self.assertIn(dimensions, build)
        self.assertIn("missing required 26.4.0 default wallpaper", setup)
        self.assertIn("setup_wallpaper || return 1", desktop)

    def test_retired_panel_and_whisker_configuration_is_not_generated(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        self.assertNotIn("whiskermenu-1.rc", desktop)
        self.assertNotIn('value="whiskermenu"', desktop)
        self.assertEqual(1, desktop.count('cat > "${xfconf_dir}/xfce4-panel.xml"'))

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

    def test_display_page_has_no_legacy_competing_interface_scale_writer(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        for forbidden in (
            "def apply_interface_scale",
            "def on_interface_scale_changed",
            "text-scaling-factor",
            "pkill plank",
            "save_scale_preference(percent)",
        ):
            self.assertNotIn(forbidden, settings)

    def test_rootfs_gate_requires_dock_reload_and_importable_performance_status(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        for marker in (
            "usr/local/lib/ming-os/ming-performance-status.py",
            "usr/local/sbin/ming-performance-status",
            "--reload-dock",
            "reload_dock()",
        ):
            self.assertIn(marker, build)

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

    def test_appearance_enforcer_only_reapplies_preferences(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        enforcer = desktop[desktop.index("configure_appearance_enforcer() {"):
                            desktop.index("# ======================== 触屏手势", desktop.index("configure_appearance_enforcer() {"))]
        self.assertIn("ming-appearance-control reapply", enforcer)
        for forbidden in (
            "ming-phone-desktop-watchdog",
            "ming-plank-watchdog",
            "ming-session-healthcheck --session",
        ):
            self.assertNotIn(forbidden, enforcer)

    def test_default_session_hides_legacy_power_manager_and_keeps_shell_palette_bounded(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        autostart = desktop[desktop.index("configure_autostart() {"):
                             desktop.index("setup_welcome_wizard() {", desktop.index("configure_autostart() {"))]
        self.assertIn('xfce4-power-manager.desktop', autostart)
        self.assertIn("Hidden=true", autostart)
        self.assertNotIn("Exec=xfce4-power-manager", autostart)
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        drawer = (ROOT / "assets/ming-app-drawer.py").read_text(encoding="utf-8")
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        for source in (phone, drawer):
            self.assertIn("COMMON.shell_visual_profile", source)
            self.assertNotIn("border-radius: 12px", source)
            self.assertNotIn("border-radius: 14px", source)
        self.assertIn("#D8E2DD", settings)
        self.assertNotIn("background: linear-gradient(to bottom, #F7FAF6", settings)

    def test_compat_runtime_profile_makes_shell_opaque_without_mutating_auto_preference(self):
        common = load_common()
        with tempfile.TemporaryDirectory() as temp:
            cache = pathlib.Path(temp) / "shell-visual.json"
            cache.write_text(json.dumps({"effective_profile": "compat"}), encoding="utf-8")
            effective = common.apply_runtime_shell_profile(
                {"compositor_profile": "auto", "motion": "normal"}, cache)
            self.assertEqual("compat", effective["compositor_profile"])
            profile = common.shell_visual_profile(effective)
            self.assertEqual(1.0, profile["surface_alpha"])
            self.assertEqual(33, profile["interval_ms"])
            explicit = common.apply_runtime_shell_profile(
                {"compositor_profile": "off"}, cache)
            self.assertEqual("off", explicit["compositor_profile"])

    def test_compat_picom_and_legacy_scaler_cannot_override_appearance(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        picom = desktop.split("cat > /usr/local/bin/ming-picom << 'MINGPICOM'\n", 1)[1].split(
            "\nMINGPICOM\n", 1)[0]
        self.assertIn("runtime_profile_file", picom)
        self.assertIn("render_node", picom)
        self.assertIn("reason=\"low-memory-${mem_mb}mb\"", picom)
        self.assertNotIn("config=\"${lowmem_conf}\"", picom)
        lowmem = desktop.split(
            "cat > /etc/xdg/picom/picom-lowmem.conf << 'PICOMLOWMEM'\n", 1)[1].split(
                "\nPICOMLOWMEM\n", 1)[0]
        self.assertIn('backend = "xrender"', lowmem)
        self.assertIn("fading = false", lowmem)
        self.assertNotIn("corner-radius", lowmem)
        autostart = desktop[desktop.index("configure_autostart() {"):
                             desktop.index("setup_welcome_wizard() {", desktop.index("configure_autostart() {"))]
        scale_entry = autostart[autostart.index("ming-scale.desktop"):autostart.index("# Picom", autostart.index("ming-scale.desktop"))]
        self.assertIn("Hidden=true", scale_entry)
        self.assertIn("X-GNOME-Autostart-enabled=false", scale_entry)
        main = desktop[desktop.index("main() {"):]
        self.assertIn("configure_hidpi_autoscale", main)
        self.assertNotIn("configure_legacy_hidpi_scaler_reference", main)
        scaler = desktop[desktop.index("configure_hidpi_autoscale() {"):
                         desktop.index("# ======================== Ming OS 品牌图标生成", desktop.index("configure_hidpi_autoscale() {"))]
        self.assertIn("未更改现有外观设置", scaler)
        for forbidden in ("xfconf-query", "dconf write", "IconSize=", "sed -i"):
            self.assertNotIn(forbidden, scaler)
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("retired ming-scale must not mutate appearance settings", build)

    def test_compact_status_widget_keeps_a_visible_shared_header_and_expand_affordance(self):
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        status = phone[phone.index("class StatusWidget"):
                       phone.index("class WallpaperCanvas", phone.index("class StatusWidget"))]
        self.assertIn('STATUS_WIDGET_COMPACT_HEIGHT = 58', phone)
        self.assertIn('STATUS_WIDGET_EXPANDED_HEIGHT = 248', phone)
        self.assertIn('self.status_toggle_label = Gtk.Label(label="展开 ▾")', status)
        self.assertIn("self.status_toggle_label.set_text", status)
        self.assertIn("self.set_valign(Gtk.Align.START)", status)
        self.assertIn("box.set_vexpand(False)", status)
        self.assertIn("self.pack_start(box, False, False, 0)", status)
        self.assertIn("box.pack_start(header, False, False, 0)", status)
        self.assertNotIn("self.compact_button = Gtk.Button()", status)

    def test_compact_status_widget_switches_card_and_header_styles_with_state(self):
        """The shared header must remain visible inside the compact height."""
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        status = phone[phone.index("class StatusWidget"):
                       phone.index("class WallpaperCanvas", phone.index("class StatusWidget"))]
        apply_state = status[status.index("    def apply_collapsed_state"):]
        self.assertIn("self.header = header", status)
        self.assertIn('add_class("status-widget-compact")', apply_state)
        self.assertIn('remove_class("status-widget-compact")', apply_state)
        self.assertIn('add_class("status-compact-pill")', apply_state)
        self.assertIn('remove_class("status-compact-pill")', apply_state)

    def test_compact_status_header_request_fits_inside_compact_widget_height(self):
        """A compact header must not request more height than its fixed card."""
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        compact_height = int(re.search(
            r"STATUS_WIDGET_COMPACT_HEIGHT = (\d+)", phone).group(1))
        compact_rule = re.search(
            r"\.status-compact-pill \{([^}]*)\}", phone, re.DOTALL).group(1)
        min_height = int(re.search(r"min-height: (\d+)px", compact_rule).group(1))
        vertical_padding = int(re.search(
            r"padding: (\d+)px \d+px", compact_rule).group(1))
        self.assertLessEqual(min_height + vertical_padding * 2 + 2, compact_height)

    def test_build_gate_rejects_duplicate_shell_runtimes(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        for process in ("xfce4-panel", "xfce4-appfinder", "whiskermenu", "volumeicon", "nm-applet", "xfdesktop", "xfce4-power-manager"):
            self.assertIn(process, build)
        self.assertIn("retired duplicate shell runtime must not be installed", build)
        self.assertIn("normal session starts duplicate shell process", build)

    def test_build_gate_requires_only_the_unified_shell_coordinator_at_login(self):
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("ming-session-healthcheck.desktop", build)
        self.assertIn("X-Ming-Managed-Components=phone-desktop;plank;picom", build)
        self.assertIn("legacy shell lifecycle autostart must stay disabled", build)
        self.assertNotIn(
            'require_file("home/user/.config/autostart/ming-dock.desktop", "ming-plank-watchdog --session")',
            build,
        )
        self.assertNotIn(
            'require_file("home/user/.config/autostart/ming-phone-desktop.desktop", "ming-phone-desktop-watchdog --session")',
            build,
        )


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
        ns = self.phone_functions({"reflow_layout_for_icon_scale", "scaled_tile_metrics"})
        layout = {"items": [
            {"id": "a", "type": "app", "x": 34, "y": 92},
            {"id": "folder", "type": "folder", "children": ["a.desktop", "b.desktop"], "x": 126, "y": 200},
        ]}
        result = ns["reflow_layout_for_icon_scale"](layout, 1.0, 1.5, 800, 600)
        self.assertEqual(["a", "folder"], [item["id"] for item in result["items"]])
        self.assertEqual(["a.desktop", "b.desktop"], result["items"][1]["children"])
        self.assertLess(result["items"][0]["x"], result["items"][1]["x"])

    def test_reflow_keeps_all_scaled_tiles_inside_each_edge(self):
        ns = self.phone_functions({"reflow_layout_for_icon_scale", "scaled_tile_metrics"})
        for scale in (0.75, 1.4):
            layout = {"items": [
                {"id": "tl", "type": "app", "x": -500, "y": -500},
                {"id": "br", "type": "folder", "children": ["a", "b"], "x": 99999, "y": 99999},
            ]}
            result = ns["reflow_layout_for_icon_scale"](layout, 1.0, scale, 800, 600)
            tile_w, tile_h = ns["scaled_tile_metrics"](scale)
            for item in result["items"]:
                self.assertGreaterEqual(item["x"], 34)
                self.assertGreaterEqual(item["y"], 92)
                self.assertLessEqual(item["x"] + tile_w, 800 - 34)
                self.assertLessEqual(item["y"] + tile_h, 600 - 92)
            self.assertEqual(["a", "b"], result["items"][1]["children"])

    def test_custom_wallpaper_is_selected_only_while_valid(self):
        ns = self.phone_functions({"appearance_wallpaper_paths", "safe_wallpaper_dimensions"})
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            image = root / "wall.png"
            image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            self.assertEqual(image, ns["appearance_wallpaper_paths"]({"wallpaper": str(image)}, [root / "fallback"])[0])
            image.unlink()
            self.assertEqual(root / "fallback", ns["appearance_wallpaper_paths"]({"wallpaper": str(image)}, [root / "fallback"])[0])

    def test_cached_wallpaper_uses_the_smallest_suitable_variant(self):
        path = ROOT / "assets/ming-phone-desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            (node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == "choose_cached_wallpaper_variant"),
            None,
        )
        self.assertIsNotNone(function)
        namespace = {"Path": pathlib.Path}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                     str(path), "exec"), namespace)
        choose = namespace["choose_cached_wallpaper_variant"]
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            cache = []
            for width, height in ((1366, 768), (1920, 1080), (3840, 2160)):
                item = root / ("default-%dx%d.png" % (width, height))
                item.write_bytes(b"cache")
                cache.append((item, width, height))
            self.assertEqual(cache[0][0], choose(cache, 1024, 768))
            self.assertEqual(cache[1][0], choose(cache, 1600, 900))
            self.assertEqual(cache[2][0], choose(cache, 2560, 1440))

    def test_shell_visual_profile_is_shared_and_compat_uses_opaque_short_animations(self):
        common = load_common()
        auto = common.shell_visual_profile({"theme": "light", "compositor_profile": "auto", "motion": "normal"})
        compat = common.shell_visual_profile({"theme": "dark", "compositor_profile": "software", "motion": "normal"})
        reduced = common.shell_animation_timing({"compositor_profile": "compat", "motion": "reduced"})
        self.assertEqual(0.96, auto["surface_alpha"])
        self.assertEqual("compat", compat["compositor_profile"])
        self.assertEqual(1.0, compat["surface_alpha"])
        self.assertEqual(33, compat["interval_ms"])
        self.assertEqual({"duration_ms": 0, "interval_ms": 0}, reduced)

    def test_phone_uses_shared_shell_visual_profile_and_cached_wallpaper_frame(self):
        phone = (ROOT / "assets/ming-phone-desktop.py").read_text(encoding="utf-8")
        canvas = phone[phone.index("class WallpaperCanvas"):
                       phone.index("class PhoneDesktop", phone.index("class WallpaperCanvas"))]
        draw = canvas[canvas.index("    def on_draw"):]
        for marker in ("COMMON.shell_visual_profile", "COMMON.shell_animation_timing", "css_for_appearance"):
            self.assertIn(marker, phone)
        for marker in ("self._render_key", 'connect("size-allocate"', "ensure_render_cache"):
            self.assertIn(marker, canvas)
        self.assertNotIn("scale_simple", draw)

    def test_settings_maps_persisted_appearance_without_emitting_apply(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        tree = ast.parse(settings)
        constants = [node for node in tree.body if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("APPEARANCE_") for target in node.targets)]
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "appearance_control_values")
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=constants + [function], type_ignores=[])), settings, "exec"), namespace)
        values = namespace["appearance_control_values"]({
            "theme": "dark", "font_family": "Noto Serif", "font_size": 14,
            "desktop_icon_size": 56, "dock_icon_size": 56, "wallpaper": "light",
            "motion": "reduced", "compositor_profile": "compat",
        })
        self.assertEqual((2, 1, 3, 3, 3, 1, 1, 1), values)
        self.assertIn("self.appearance_loading", settings)
        self.assertIn('"status", "--json"', settings)
        self.assertIn("恢复默认壁纸", settings)

    def test_pointer_probes_never_run_blocking_runner_on_gtk_callback(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        refresh = settings[settings.index("    def refresh_pointer_status"):
                           settings.index("    def on_pointer_toggle", settings.index("    def refresh_pointer_status"))]
        toggle = settings[settings.index("    def on_pointer_toggle"):
                          settings.index("    # ---- 高级设置", settings.index("    def on_pointer_toggle"))]
        self.assertIn("run_task_async(pointer_device_snapshot", refresh)
        self.assertIn("pointer_probe_state", refresh)
        self.assertNotIn("snapshot = pointer_device_snapshot()", refresh)
        self.assertIn("run_task_async", toggle)

    def test_pointer_mutations_serialize_and_latest_generation_wins(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        tree = ast.parse(settings)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PointerMutationSerial")
        namespace = {"threading": threading}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), settings, "exec"), namespace)
        serial = namespace["PointerMutationSerial"]()
        writes = []
        key = ("mouse-1", "left_handed")
        old = serial.begin(key)
        new = serial.begin(key)
        threads = [
            threading.Thread(target=lambda: serial.apply(key, old, lambda: writes.append("old"))),
            threading.Thread(target=lambda: serial.apply(key, new, lambda: writes.append("new"))),
        ]
        threads[0].start(); time.sleep(0.02); threads[1].start()
        for thread in threads:
            thread.join()
        self.assertEqual(["new"], writes)

    def test_pointer_mutations_do_not_cancel_different_settings(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        tree = ast.parse(settings)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PointerMutationSerial")
        namespace = {"threading": threading}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), settings, "exec"), namespace)
        serial = namespace["PointerMutationSerial"]()
        writes = []
        left = ("touchpad-1", "left_handed")
        natural = ("touchpad-1", "natural_scroll")
        left_generation = serial.begin(left)
        natural_generation = serial.begin(natural)
        threads = [
            threading.Thread(target=lambda: serial.apply(left, left_generation, lambda: writes.append("left"))),
            threading.Thread(target=lambda: serial.apply(natural, natural_generation, lambda: writes.append("natural"))),
        ]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertCountEqual(["left", "natural"], writes)


class MingFilesIconBehaviorTests(unittest.TestCase):
    def load_module(self):
        path = ROOT / "assets/ming-files.py"
        spec = importlib.util.spec_from_file_location("ming_files_icons", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_file_icon_widget_consumes_absolute_theme_and_fallback(self):
        module = self.load_module()
        class Image:
            def __init__(self):
                self.file = None
                self.name = None
                self.size = None
                self.pixbuf = None
            def set_from_file(self, value): self.file = value
            def set_from_icon_name(self, value): self.name = value
            def set_pixel_size(self, value): self.size = value
            def set_from_pixbuf(self, value): self.pixbuf = value
        with tempfile.TemporaryDirectory() as temp:
            icon = pathlib.Path(temp) / "file.png"
            icon.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
            module.COMMON.load_icon_pixbuf = lambda _theme, _icon, _size: "scaled-pixbuf"
            absolute = Image()
            module.set_resolved_icon(absolute, str(icon), 32)
            self.assertEqual("scaled-pixbuf", absolute.pixbuf)
            themed = Image()
            module.set_resolved_icon(themed, "folder-symbolic", 24)
            self.assertEqual("folder-symbolic", themed.name)
            missing = Image()
            module.set_resolved_icon(missing, str(icon.with_name("missing.png")), 24)
            self.assertEqual("application-x-executable", missing.name)

    def test_absolute_icons_are_scaled_before_gtk_image_consumes_them(self):
        source = (ROOT / "assets/ming-files.py").read_text(encoding="utf-8")
        setter = source[source.index("def set_resolved_icon"):source.index("try:\n    import gi")]
        self.assertIn("load_icon_pixbuf", setter)
        self.assertIn("set_from_pixbuf", setter)
        self.assertNotIn("set_from_file", setter)


class FeedbackSurfaceReadabilityTests(unittest.TestCase):
    def render_phone_css(self, profile):
        path = ROOT / "assets/ming-phone-desktop.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "css_for_appearance"
        )

        class Common:
            @staticmethod
            def shell_visual_profile(_appearance):
                return dict(profile)

        namespace = {"COMMON": Common}
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                str(path),
                "exec",
            ),
            namespace,
        )
        return namespace["css_for_appearance"]({}).decode("utf-8")

    def test_feedback_surfaces_stay_opaque_when_desktop_glass_is_enabled(self):
        """Readable feedback must not inherit the optional desktop glass alpha."""
        opaque = "#f8faf9"
        css = self.render_phone_css({
            "theme": "light",
            "surface_alpha": 0.35,
            "surface_base": "#eef2ef",
            "surface_raised": opaque,
            "surface_sunken": "#e7eee9",
            "border_soft": "#bdc9c0",
            "text_primary": "#101814",
            "text_secondary": "#354238",
            "accent": "#238a72",
        })
        for selector in (
            ".clock-widget, .status-widget, .launch-feedback",
            ".status-compact-pill",
            ".notification-panel",
        ):
            block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.S)
            self.assertIsNotNone(block, selector)
            self.assertIn("background-color: " + opaque, block.group(1))
            self.assertNotRegex(block.group(1), r"background(?:-color)?:\s*rgba\(")

    def test_notification_backends_do_not_reduce_feedback_opacity(self):
        desktop = (ROOT / "modules/03_desktop.sh").read_text(encoding="utf-8")
        profile = desktop.split("cat > /home/${MING_USER}/.config/picom/picom.conf << 'PICOMCFG'", 1)[1].split("PICOMCFG", 1)[0]
        self.assertIn("notification = { shadow = true; opacity = 1.0; };", profile)
        self.assertIn('<property name="initial-opacity" type="double" value="1.0"/>', desktop)

    def test_settings_feedback_dialog_styles_cover_the_adwaita_window_and_content_nodes(self):
        settings = (ROOT / "assets/ming-settings.py").read_text(encoding="utf-8")
        for selector in (
            "window.ming-feedback-dialog",
            "window.ming-feedback-dialog .dialog-vbox",
            "window.ming-feedback-dialog .dialog-action-area",
        ):
            self.assertIn(selector, settings)
        self.assertIn("background-image: none", settings)


if __name__ == "__main__":
    unittest.main()
