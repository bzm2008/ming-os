import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
APPS = (ROOT / "modules" / "02_apps.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")


FORBIDDEN_NORMAL = re.compile(
    r"(?:^|\s)(?:nomodeset|i915\.modeset=0|pcie_aspm=off|pci=nomsi|acpi_osi=Linux)(?:\s|$)"
)


def menuentry_blocks(source):
    return re.findall(r"menuentry\s+(?:\"[^\"]+\"|'[^']+').*?\n\s*}\n", source, re.S)


class GrubPerformanceContracts(unittest.TestCase):
    def test_normal_grub_entries_use_kernel_modesetting_and_safe_entry_is_explicit(self):
        for source in (BUILD, BASE):
            blocks = menuentry_blocks(source)
            self.assertTrue(blocks, "expected generated GRUB menu entries")
            safe_blocks = [block for block in blocks if any(marker in block for marker in (
                "Safe Graphics", "安全显卡模式", "Radeon Legacy", "Radeon GCN",
            ))]
            self.assertTrue(safe_blocks, "safe graphics fallback must remain labelled")
            self.assertTrue(any("nomodeset" in block for block in safe_blocks))
            for block in blocks:
                if block in safe_blocks:
                    continue
                self.assertIsNone(
                    FORBIDDEN_NORMAL.search(block),
                    f"normal GRUB entry still forces compatibility flags:\n{block}",
                )

    def test_isolinux_default_does_not_disable_kernel_modesetting(self):
        section = BUILD[BUILD.index("cat > \"${iso_workdir}/isolinux/isolinux.cfg\""):]
        install = re.search(r"LABEL install.*?(?=\nLABEL safe)", section, re.S)
        self.assertIsNotNone(install)
        self.assertIsNone(FORBIDDEN_NORMAL.search(install.group(0)))
        safe = re.search(r"LABEL safe.*?(?=\nLABEL oldpc|ISOLINUXCFG)", section, re.S)
        self.assertIsNotNone(safe)
        self.assertIn("nomodeset", safe.group(0))


class PicomPerformanceContracts(unittest.TestCase):
    def test_generated_picom_profiles_keep_windows_opaque_and_disable_unredirect(self):
        for source in (APPS, DESKTOP):
            self.assertGreaterEqual(source.count("unredir-if-possible = false;"), 2)
            self.assertIn("inactive-opacity = 1.0;", source)
            self.assertIn("active-opacity = 1.0;", source)
            self.assertIn("frame-opacity = 1.0;", source)
            self.assertNotIn('blur-method = "dual_kawase";', source)
            self.assertNotIn("blur-background = true;", source)

    def test_fallback_profile_has_no_heavy_shadow_and_only_dock_notification_transparency(self):
        fallback = re.search(r"cat > /etc/xdg/picom/picom-fallback\.conf << 'PICOMFALLBACK'.*?PICOMFALLBACK", DESKTOP, re.S)
        self.assertIsNotNone(fallback)
        config = fallback.group(0)
        self.assertIn("backend = \"xrender\";", config)
        self.assertIn("shadow = false;", config)
        self.assertIn("fading = false;", config)
        self.assertNotIn("fade-in-step", config)
        self.assertNotIn("fade-out-step", config)
        self.assertIn("dock = {", config)
        self.assertIn("notification = {", config)


class FontPerformanceContracts(unittest.TestCase):
    def test_noto_packages_and_fontconfig_policy_are_deployed(self):
        for package in ("fonts-noto-core", "fonts-noto-cjk", "fonts-noto-mono"):
            self.assertIn(package, APPS)
        self.assertIn("/etc/fonts/conf.d/99-ming-os-fonts.conf", APPS)
        self.assertIn("Noto Sans CJK SC", APPS)
        self.assertIn("antialias", APPS)
        self.assertIn("hintstyle", APPS)

    def test_desktop_defaults_use_noto_with_wenquanyi_only_as_fontconfig_fallback(self):
        self.assertIn("/Gtk/FontName -s \"Noto Sans CJK SC", DESKTOP)
        self.assertIn("gtk-font-name=Noto Sans CJK SC", DESKTOP)
        self.assertIn('gtk-font-name="Noto Sans CJK SC', DESKTOP)
        self.assertIn('title_font" type="string" value="Noto Sans CJK SC', DESKTOP)
        self.assertIn('FontName" type="string" value="Noto Sans CJK SC', DESKTOP)

    def test_gtk_theme_keeps_noto_readable_with_static_compact_spacing(self):
        start = DESKTOP.index("cat > /usr/share/themes/Ming-Glass/gtk-3.0/gtk.css")
        end = DESKTOP.index("\nMINGGLASSCSS", start)
        theme = DESKTOP[start:end]
        self.assertIn('font-family: "Noto Sans CJK SC", sans-serif;', theme)
        self.assertIn("font-weight: 400;", theme)
        self.assertIn("padding: 6px 12px;", theme)
        self.assertIn("padding: 8px 12px;", theme)
        self.assertNotIn("animation:", theme)
        self.assertIn('title_font" type="string" value="Noto Sans CJK SC Medium 11"', DESKTOP)

    def test_display_scaler_never_reduces_noto_below_ten_point(self):
        start = DESKTOP.index("cat > /usr/local/bin/ming-scale")
        end = DESKTOP.index("\nMINGSCALE", start)
        scaler = DESKTOP[start:end]
        self.assertNotIn("FONT_SIZE=9", scaler)
        self.assertIn("FONT_SIZE=$((FONT_SIZE > 10 ? FONT_SIZE - 1 : 10))", scaler)
        self.assertIn('"Noto Sans CJK SC ${FONT_SIZE}"', scaler)
        self.assertIn("SCALE_POLICY_VERSION=2", scaler)
        self.assertIn('grep -Fxq "font-policy=${SCALE_POLICY_VERSION}" "${SCALE_CONFIG}"', scaler)
        self.assertIn('printf "font-policy=%s\\n" "${SCALE_POLICY_VERSION}" > "${SCALE_CONFIG}"', scaler)
        self.assertNotIn('if [[ -f "${SCALE_CONFIG}" ]]; then', scaler)

    def test_fontconfig_does_not_override_explicit_monospace_requests(self):
        start = APPS.index("cat > /etc/fonts/conf.d/99-ming-os-fonts.conf << 'MINGFONTS'")
        end = APPS.index("\nMINGFONTS", start)
        config = APPS[start:end]
        self.assertIn('<test name="family"', config)
        self.assertIn('<string>monospace</string>', config)
        self.assertIn('<string>Noto Sans Mono</string>', config)
        self.assertIn("fc-match monospace", BUILD)

    def test_build_gate_resolves_chinese_sans_and_fcitx_candidate_fonts(self):
        self.assertIn("fc-match 'sans:lang=zh'", BUILD)
        self.assertIn("fc-match 'Noto Sans CJK SC'", BUILD)
        self.assertIn("Font=Noto Sans CJK SC 15", BUILD)
        self.assertIn("MenuFont=Noto Sans CJK SC 16", BUILD)


class FirefoxPerformanceContracts(unittest.TestCase):
    def test_firefox_wrapper_uses_a_small_startup_path_and_audio_preflight(self):
        start = APPS.index("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'")
        end = APPS.index("\nMINGFIREFOX", start + len("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'"))
        wrapper = APPS[start:end]
        self.assertIn("firefox_args=(--new-instance)", wrapper)
        self.assertIn("ming-audio-session ensure --json", wrapper)
        self.assertIn("timeout 3", wrapper)
        self.assertIn("exec firefox-esr", wrapper)

    def test_firefox_wrapper_does_not_inherit_chromium_only_gpu_switches(self):
        start = APPS.index("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'")
        end = APPS.index("\nMINGFIREFOX", start + len("cat > /usr/local/bin/ming-firefox << 'MINGFIREFOX'"))
        wrapper = APPS[start:end]
        self.assertNotIn("VaapiVideoDecodeLinuxGL", wrapper)
        self.assertNotIn("UseMultiPlaneFormatForHardwareVideo", wrapper)
        self.assertNotIn("--disable-gpu-compositing", wrapper)
        self.assertNotIn("edge_hardware_video", wrapper)

    def test_build_gate_requires_intel_and_radeon_vaapi_backends_and_font_matching(self):
        self.assertIn("i965_drv_video.so", BUILD)
        self.assertIn("iHD_drv_video.so", BUILD)
        self.assertIn("radeonsi_drv_video.so", BUILD)
        self.assertIn("fc-match", BUILD)
        self.assertIn("fonts-noto-core", BUILD)
        self.assertIn("fonts-noto-cjk", BUILD)
        self.assertIn("fonts-noto-mono", BUILD)

    def test_amd_graphics_stack_is_required_and_i2c_piix4_is_not_blacklisted(self):
        for package in (
            "firmware-amd-graphics", "amd64-microcode", "libgl1-mesa-dri",
            "mesa-va-drivers", "mesa-vdpau-drivers", "mesa-vulkan-drivers", "lm-sensors",
        ):
            self.assertIn(package, BASE)
        self.assertNotIn("blacklist i2c_piix4", BASE)

    def test_amd_recovery_menu_entries_are_explicit_and_default_entries_remain_clean(self):
        self.assertIn("Radeon Legacy", BUILD)
        self.assertIn("radeon.modeset=1 amdgpu.modeset=0", BUILD)
        self.assertIn("Radeon GCN", BUILD)
        self.assertIn("amdgpu.si_support=1 radeon.si_support=0", BUILD)

    def test_grub_gate_checks_default_entry_without_rejecting_safe_fallback(self):
        self.assertIn("default_entry", BUILD)
        self.assertIn("Safe Graphics", BUILD)
        self.assertIn("must keep a safe-graphics entry", BUILD)

    def test_official_debian_grub_generator_remains_executable_for_kernel_fallback(self):
        self.assertIn('"${noisy_grub}" == "10_linux"', BASE)
        self.assertIn('chmod 0755 "${target}/etc/grub.d/${noisy_grub}"', BASE)
        self.assertIn('etc/grub.d/10_linux', BUILD)


if __name__ == "__main__":
    unittest.main()
