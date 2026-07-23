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
        self.assertIn("ming.safe_graphics=1", safe.group(0))


class PicomPerformanceContracts(unittest.TestCase):
    def test_desktop_is_the_single_picom_configuration_generator(self):
        self.assertNotIn("cat > /etc/xdg/picom/picom.conf", APPS)
        self.assertNotIn("cat > /usr/local/bin/ming-picom", APPS)

    def test_generated_picom_profiles_keep_windows_opaque_and_disable_unredirect(self):
        for source in (DESKTOP,):
            self.assertGreaterEqual(source.count("unredir-if-possible = false;"), 2)
            self.assertIn("inactive-opacity = 1.0;", source)
            self.assertIn("active-opacity = 1.0;", source)
            self.assertIn("frame-opacity = 1.0;", source)
            self.assertIn("detect-client-opacity = false;", source)
            self.assertNotIn('blur-method = "dual_kawase";', source)
            self.assertNotIn("blur-background = true;", source)

    def test_every_generated_picom_profile_disables_client_opacity(self):
        profiles = (
            ("PICOMCFG", "cat > /home/${MING_USER}/.config/picom/picom.conf"),
            ("PICOMFALLBACK", "cat > /etc/xdg/picom/picom-fallback.conf"),
            ("PICOMLOWMEM", "cat > /etc/xdg/picom/picom-lowmem.conf"),
        )
        for delimiter, marker in profiles:
            profile = DESKTOP.split(marker, 1)[1].split("\n" + delimiter, 1)[0]
            self.assertIn("detect-client-opacity = false;", profile)

    def test_fallback_profile_has_no_heavy_shadow_and_only_dock_notification_transparency(self):
        fallback = re.search(r"cat > /etc/xdg/picom/picom-fallback\.conf << 'PICOMFALLBACK'.*?PICOMFALLBACK", DESKTOP, re.S)
        self.assertIsNotNone(fallback)
        config = fallback.group(0)
        self.assertIn("backend = \"xrender\";", config)
        self.assertIn("shadow = false;", config)
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


class EdgeVaapiContracts(unittest.TestCase):
    def test_edge_wrapper_uses_explicit_verified_vaapi_flags_and_software_fallback(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge << 'MINGEDGE'")
        end = APPS.index("\nMINGEDGE", start + len("cat > /usr/local/bin/ming-edge << 'MINGEDGE'"))
        wrapper = APPS[start:end]
        self.assertIn("--enable-accelerated-video-decode", wrapper)
        self.assertIn("VaapiVideoDecodeLinuxGL", wrapper)
        self.assertNotIn("UseMultiPlaneFormatForHardwareVideo", wrapper)
        self.assertNotIn("--use-gl=egl", wrapper)
        self.assertIn("--disable-gpu", wrapper)
        self.assertIn("--disable-gpu-compositing", wrapper)
        self.assertIn("ming-edge-graphics test-video", wrapper)
        self.assertIn('"render_access": true', wrapper)

    def test_edge_wrapper_uses_generic_capability_result_not_intel_only_driver_name(self):
        start = APPS.index("cat > /usr/local/bin/ming-edge << 'MINGEDGE'")
        end = APPS.index("\nMINGEDGE", start + len("cat > /usr/local/bin/ming-edge << 'MINGEDGE'"))
        wrapper = APPS[start:end]
        self.assertIn('"desktop_rendering": true', wrapper)
        self.assertNotIn('"driver": "i915"', wrapper)
        self.assertIn("(i915|amdgpu|radeon|nouveau)\\.modeset=0", wrapper)

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

    def test_safe_graphics_selection_persists_without_polluting_normal_boot(self):
        """Out-of-range KMS recovery must survive the first installed reboot."""
        for source in (BUILD, BASE):
            blocks = menuentry_blocks(source)
            # The build script embeds Python release validation snippets that
            # mention menu titles. Only inspect real menu blocks with a kernel
            # command, not those quoted implementation details.
            safe_blocks = [
                block for block in blocks
                if "Safe Graphics" in block
                and re.search(r"^\s*(?:linux|APPEND)\s", block, re.M)
            ]
            self.assertTrue(safe_blocks)
            self.assertTrue(all("ming.safe_graphics=1" in block for block in safe_blocks))
            normal_blocks = [
                block for block in blocks
                if re.search(r"^\s*(?:linux|APPEND)\s", block, re.M)
                and "Safe Graphics" not in block
            ]
            self.assertTrue(all("ming.safe_graphics=1" not in block for block in normal_blocks))

        for marker in (
            "ming-safe-graphics-persist",
            'GRUB_DEFAULT="Ming OS (Safe Graphics)"',
            "safe_graphics_requested",
            "ming.safe_graphics=1",
            "multi-user.target",
        ):
            self.assertIn(marker, BASE)

    def test_safe_graphics_install_logic_belongs_to_installed_identity_not_ota_preflight(self):
        """Safe-graphics selection is an installer setting, not an OTA gate."""
        preflight = BASE.split("cat > /usr/local/sbin/ming-ota-preflight << 'MINGOTAPREFLIGHT'", 1)[1].split(
            "\nMINGOTAPREFLIGHT", 1
        )[0]
        identity = BASE.split("cat > /usr/local/sbin/ming-fix-installed-identity << 'MINGIDENTITY'", 1)[1].split(
            "\nMINGIDENTITY", 1
        )[0]
        self.assertNotIn("configure_safe_graphics_default", preflight)
        for marker in (
            "safe_graphics_requested()",
            "ota_install_requested()",
            "configure_safe_graphics_default()",
            "configure_safe_graphics_default",
        ):
            self.assertIn(marker, identity)

    def test_official_debian_grub_generator_remains_executable_for_kernel_fallback(self):
        self.assertIn('"${noisy_grub}" == "10_linux"', BASE)
        self.assertIn('chmod 0755 "${target}/etc/grub.d/${noisy_grub}"', BASE)
        self.assertIn('etc/grub.d/10_linux', BUILD)


if __name__ == "__main__":
    unittest.main()
