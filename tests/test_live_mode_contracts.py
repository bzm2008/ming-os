import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
BASE = (ROOT / "modules" / "01_base.sh").read_text(encoding="utf-8")
DESKTOP = (ROOT / "modules" / "03_desktop.sh").read_text(encoding="utf-8")
PHONE = (ROOT / "assets" / "ming-phone-desktop.py").read_text(encoding="utf-8")
FINALIZE = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")


class LiveModeContracts(unittest.TestCase):
    def test_default_iso_entry_starts_a_live_desktop(self):
        """The default BIOS and UEFI paths must expose a usable Live session."""
        grub = BUILD.split("write_grub_config() {", 1)[1].split("build_iso() {", 1)[0]
        live_entry = grub.split('menuentry "体验 Ming OS ${MING_OS_VERSION} (Live Mode)" {', 1)[1].split("}\n", 1)[0]
        install_entry = grub.split('menuentry "安装 Ming OS ${MING_OS_VERSION} (Install Ming OS)" {', 1)[1].split("}\n", 1)[0]

        self.assertNotIn("ming.installer=1", live_entry)
        self.assertIn("ming.installer=1", install_entry)
        self.assertIn("DEFAULT live", BUILD)
        self.assertIn("LABEL live", BUILD)

    def test_live_desktop_keeps_an_explicit_visible_installer_launcher(self):
        """Live mode must not autostart Calamares, but its install tile must be obvious."""
        installer = DESKTOP.split("deploy_live_installer() {", 1)[1].split("# ======================== Xfce", 1)[0]
        autostart = DESKTOP.split("configure_autostart() {", 1)[1].split("# ======================== 首次启动", 1)[0]

        self.assertIn("/home/${MING_USER}/Desktop/Install Ming OS.desktop", installer)
        self.assertIn("Icon=ming-os-install", installer)
        self.assertIn("X-Ming-Live-Only=true", installer)
        self.assertIn("ming-live-install.svg", installer)
        self.assertIn(
            'Exec=env MING_LIVE_INSTALL_REQUEST=1 /usr/local/bin/ming-live-installer.sh',
            BUILD,
        )
        self.assertIn("calamares-live.desktop", autostart)
        self.assertIn("X-GNOME-Autostart-enabled=false", autostart)
        self.assertNotIn("installer-only image", installer)
        self.assertIn('"Install Ming OS.desktop",', PHONE)
        calamares = BASE.split(
            "cat > /usr/share/applications/calamares.desktop << 'CALAMARESDESKTOP'\n", 1
        )[1].split("\nCALAMARESDESKTOP", 1)[0]
        self.assertIn("NoDisplay=true", calamares)

    def test_live_installer_is_a_first_run_core_tile(self):
        """An empty Live layout must render the explicit installer entry."""
        tree = ast.parse(PHONE)
        core_names = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "CORE_NAMES" for target in node.targets)
        )
        namespace = {}
        exec(compile(ast.Module(body=[core_names], type_ignores=[]), str(ROOT / "assets" / "ming-phone-desktop.py"), "exec"), namespace)
        self.assertIn("Install Ming OS.desktop", namespace["CORE_NAMES"])

    def test_live_desktop_never_autostarts_the_installer(self):
        """Only the explicit installer boot entry may start Calamares automatically."""
        installer = DESKTOP.split("deploy_live_installer() {", 1)[1].split("# ======================== Xfce", 1)[0]
        service = installer.split("cat > /etc/systemd/system/ming-live-installer.service", 1)[1].split("systemctl disable ming-live-installer.service", 1)[0]
        self.assertIn("ConditionKernelCommandLine=ming.installer=1", service)
        self.assertNotIn("ConditionKernelCommandLine=|boot=live", service)
        self.assertNotIn("ConditionKernelCommandLine=|live-config", service)

    def test_regular_live_mode_requires_an_explicit_desktop_install_request(self):
        """A stale autostart must not turn an ordinary Live desktop into Calamares."""
        installer = DESKTOP.split("deploy_live_installer() {", 1)[1].split("# ======================== Xfce", 1)[0]
        launcher = installer.split("cat > /usr/local/bin/ming-live-installer.sh << 'LIVEINSTALLER'\n", 1)[1].split("\nLIVEINSTALLER", 1)[0]
        live_tile = installer.split("cat > \"/usr/share/applications/Install Ming OS.desktop\" << 'LIVEINSTALLDESKTOP'\n", 1)[1].split("\nLIVEINSTALLDESKTOP", 1)[0]

        self.assertIn("MING_LIVE_INSTALL_REQUEST", launcher)
        self.assertIn("MING_LIVE_INSTALL_REQUEST=1", live_tile)

    def test_live_installer_launcher_is_registered_with_the_launch_broker(self):
        """The build-created Live launcher must be trusted just like packaged entries."""
        installer = DESKTOP.split("deploy_live_installer() {", 1)[1].split("# ======================== Xfce", 1)[0]
        identity = BASE[
            BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            BASE.index("MINGIDENTITY\n", BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]

        self.assertIn("/var/lib/ming-os/trusted-desktops", installer)
        self.assertIn("Install Ming OS.desktop", installer)
        self.assertIn("chmod 0644", installer)
        self.assertIn("trusted-desktops/Install Ming OS.desktop", identity)
        self.assertIn("live_launcher_receipt = require_file(", BUILD)
        self.assertIn("var/lib/ming-os/trusted-desktops/Install Ming OS.desktop", BUILD)

    def test_installed_identity_removes_every_live_installer_entry(self):
        identity = BASE[
            BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            BASE.index("MINGIDENTITY\n", BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]

        self.assertIn('"${target}"/home/*/Desktop/"Install Ming OS.desktop"', identity)
        self.assertIn('"${target}/etc/skel/Desktop/Install Ming OS.desktop"', identity)
        self.assertIn('"${target}/usr/share/icons/hicolor/scalable/apps/ming-os-install.svg"', identity)

    def test_live_layout_backup_cannot_restore_an_installer_tile_after_install(self):
        """Both the staged seed and final target must remove the last-good Live layout."""
        identity = BASE[
            BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"):
            BASE.index("MINGIDENTITY\n", BASE.index("cat > /usr/local/sbin/ming-fix-installed-identity"))
        ]

        self.assertIn("desktop-layout.json", FINALIZE)
        self.assertIn("desktop-layout.last-good.json", FINALIZE)
        self.assertIn('"/etc/skel/.config/ming-os/desktop-layout.last-good.json"', FINALIZE)
        self.assertIn('"${target}"/home/*/.config/ming-os/desktop-layout.json', identity)
        self.assertIn('"${target}"/home/*/.config/ming-os/desktop-layout.last-good.json', identity)
        self.assertIn('"${target}/etc/skel/.config/ming-os/desktop-layout.json"', identity)
        self.assertIn('"${target}/etc/skel/.config/ming-os/desktop-layout.last-good.json"', identity)

    def test_finalizer_keeps_the_live_installer_tile_until_installation(self):
        """Desktop cleanup must not delete the only Live-mode install entry."""
        launchers = FINALIZE.split("readonly DESKTOP_LAUNCHERS=(", 1)[1].split(")", 1)[0]

        self.assertIn('"Install Ming OS.desktop"', launchers)
        self.assertIn('/usr/share/applications/Install Ming OS.desktop', DESKTOP)


if __name__ == "__main__":
    unittest.main()
