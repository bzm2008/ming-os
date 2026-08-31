import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.test_installer_reliability import create_installed_root, write


ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "modules" / "01_base.sh"
DESKTOP = ROOT / "modules" / "03_desktop.sh"
FINALIZE = ROOT / "modules" / "07_finalize.sh"
VERIFY = ROOT / "assets" / "ming-installer-verify.py"
BUILD = ROOT / "build_onion_os.sh"


class InstalledDesktopMigrationTests(unittest.TestCase):
    def test_identity_reconciles_the_actual_primary_home_from_a_canonical_profile(self):
        source = BASE.read_text(encoding="utf-8")
        identity = source.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1]
        self.assertIn("migrate_installed_ming_profile", identity)
        self.assertIn("MING_PRIMARY_HOME", identity)
        self.assertIn("/etc/skel/.config/autostart/ming-phone-desktop.desktop", identity)
        self.assertIn("/etc/skel/.config/autostart/ming-apply-appearance.desktop", identity)
        self.assertIn("/etc/skel/.config/plank/dock1/settings", identity)

    def test_migration_preserves_unmanaged_user_configuration(self):
        source = BASE.read_text(encoding="utf-8")
        identity = source.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1]
        self.assertIn("X-Ming-Managed", identity)
        self.assertIn("preserve", identity.casefold())
        self.assertIn("is_managed_profile_file", identity)

    def test_installed_gate_checks_ming_profile_and_mint_theme(self):
        verifier = VERIFY.read_text(encoding="utf-8")
        self.assertIn("ming-apply-appearance.desktop", verifier)
        self.assertIn("ming-phone-desktop.desktop", verifier)
        self.assertIn("plank/dock1/settings", verifier)
        self.assertIn("Ming-Mint", verifier)

    def test_finalize_uses_ming_mint_as_the_appearance_gate(self):
        source = FINALIZE.read_text(encoding="utf-8")
        gate = source.split("verify_appearance_assets()", 1)[1]
        self.assertIn("/usr/share/themes/Ming-Mint/gtk-3.0/gtk.css", gate)
        self.assertNotIn("/usr/share/themes/Ming-Glass/gtk-3.0/gtk.css", gate)

    def test_desktop_profile_seed_marks_session_files_as_ming_managed(self):
        source = DESKTOP.read_text(encoding="utf-8")
        self.assertIn("X-Ming-Managed=true", source)
        self.assertIn("ming-apply-appearance.desktop", source)

    def test_identity_migration_is_user_home_agnostic_and_symlink_safe(self):
        source = BASE.read_text(encoding="utf-8")
        identity = source.split("cat > /usr/local/sbin/ming-fix-installed-identity", 1)[1].split(
            "\nMINGIDENTITY", 1
        )[0]
        self.assertIn("migrate_installed_ming_profile", identity)
        self.assertIn("resolve_user_home", identity)
        self.assertIn("/etc/skel", identity)
        self.assertIn("readlink -f", identity)
        self.assertIn("X-Ming-Managed", identity)
        self.assertNotIn("/home/user", identity)

    def test_verifier_accepts_the_actual_installed_user_profile(self):
        spec = __import__("importlib.util").util.spec_from_file_location(
            "ming_installer_verify_profile", VERIFY
        )
        verifier = __import__("importlib.util").util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(verifier)
        uuid = "790ec0ef-1111-2222-3333-444444444444"
        with __import__("tempfile").TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "target"
            create_installed_root(target, uuid)
            (target / "home/user").rename(target / "home/alice")
            passwd = (target / "etc/passwd").read_text(encoding="utf-8").replace(
                "user:x:1000:1000:Ming OS User:/home/user:/bin/bash",
                "alice:x:1000:1000:Ming OS User:/home/alice:/bin/bash",
            )
            (target / "etc/passwd").write_text(passwd, encoding="utf-8")
            (target / "etc/group").write_text(
                "root:x:0:\n\nalice:x:1000:\nsudo:x:27:alice\n", encoding="utf-8"
            )
            write(
                target,
                "home/alice/.config/autostart/ming-apply-appearance.desktop",
                "[Desktop Entry]\nExec=/usr/local/bin/ming-apply-appearance\n"
                "Hidden=false\nX-GNOME-Autostart-enabled=true\n",
            )
            write(
                target,
                "home/alice/.config/autostart/ming-session-healthcheck.desktop",
                "[Desktop Entry]\nExec=/usr/local/bin/ming-session-healthcheck --session\n"
                "Hidden=false\nX-GNOME-Autostart-enabled=true\n",
            )
            write(
                target,
                "home/alice/.config/plank/dock1/settings",
                "[PlankDockPreferences]\nTheme=Ming\nDockItems=ming-settings.dockitem\n",
            )
            write(target, "home/alice/.config/ming-os/ming-mint-theme", "Ming-Mint\n")
            write(
                target,
                "home/alice/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml",
                "IconThemeName=Ming-Mint\n",
            )
            for launcher in (
                "ming-settings.desktop",
                "ming-files.desktop",
                "ming-terminal.desktop",
                "ming-store.desktop",
                "ming-toolbox.desktop",
            ):
                write(target, f"usr/share/applications/{launcher}", "[Desktop Entry]\nType=Application\n")
            result = verifier.verify_installed(target, require_desktop_profile=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual("alice", result["primary_user"])
        self.assertEqual("/home/alice", result["primary_home"])

    def test_verifier_requires_managed_session_and_appearance_entries_for_actual_user(self):
        spec = __import__("importlib.util").util.spec_from_file_location(
            "ming_installer_verify_profile_missing", VERIFY
        )
        verifier = __import__("importlib.util").util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(verifier)
        with __import__("tempfile").TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "target"
            create_installed_root(target)
            (target / "home/user/.config/autostart/ming-session-healthcheck.desktop").unlink()
            result = verifier.verify_installed(target, require_desktop_profile=True)
        self.assertFalse(result["ok"], result)
        self.assertTrue(
            any("session-healthcheck" in item or "desktop profile" in item for item in result["errors"]),
            result,
        )

    def test_profile_gate_is_explicit_for_receipt_cli_and_does_not_break_legacy_receipt_checks(self):
        source = VERIFY.read_text(encoding="utf-8")
        self.assertIn("installed.add_argument(\"--require-desktop-profile\", action=\"store_true\")", source)
        self.assertIn("require_desktop_profile=args.require_desktop_profile", source)
        self.assertIn("ming-installer-verify installed --receipt --require-desktop-profile", BASE.read_text(encoding="utf-8"))
        self.assertNotIn("require_desktop_profile = target_mode == \"receipt\" or final_boot", source)

    def test_generated_installed_gate_yaml_keeps_root_keys_unindented(self):
        source = BASE.read_text(encoding="utf-8")
        block = source.split("cat > /etc/calamares/modules/ming-installed-desktop-gate.conf", 1)[1]
        block = block.split("\nINSTALLEDDESKTOPGATECONF", 1)[0]
        self.assertIn("\ndontChroot: true\n", block)
        self.assertIn("\ntimeout: 30\n", block)
        self.assertNotIn("\n  dontChroot: true\n", block)

    def test_identity_migration_checks_all_components_and_uses_exclusive_temporary_files(self):
        identity = BASE.read_text(encoding="utf-8").split(
            "cat > /usr/local/sbin/ming-fix-installed-identity", 1
        )[1].split("\nMINGIDENTITY", 1)[0]
        self.assertIn("profile_source_path_is_safe", identity)
        self.assertIn("IFS='/' read -r -a", identity)
        self.assertIn("_profile_root_components", identity)
        self.assertIn("root_component", identity)
        self.assertIn("root_component component relative", identity)
        self.assertIn("local -a _profile_root_components _profile_components", identity)
        self.assertIn("local -a _source_components", identity)
        self.assertIn("mktemp \"${destination}.tmp.XXXXXX\"", identity)
        self.assertIn("mktemp \"${manifest}.tmp.XXXXXX\"", identity)
        self.assertNotIn("(?:By|Components|Profile)", identity)

    def test_seed_skel_is_link_safe_and_does_not_recursive_replace_user_data(self):
        finalize = FINALIZE.read_text(encoding="utf-8")
        helper = finalize.split("copy_skel_item() {", 1)[1].split(
            "\n}\n\nseed_skel()", 1
        )[0]
        seed = finalize.split("seed_skel() {", 1)[1].split(
            "\n}\n\n# ======================== 关键美化文件自检", 1
        )[0]
        self.assertIn('finalize_path_is_safe "/etc/skel"', seed)
        self.assertIn('finalize_path_is_safe "${USER_HOME}"', seed)
        self.assertIn("mktemp", helper)
        self.assertIn("--no-dereference", helper)
        self.assertNotIn('rm -rf "/etc/skel/${item}"', seed)
        self.assertNotIn('cp -a "${src}" "/etc/skel/${item}"', seed)

    def test_installed_profile_gate_is_used_by_all_calamares_and_build_checks(self):
        desktop = DESKTOP.read_text(encoding="utf-8")
        self.assertIn(
            "/usr/local/sbin/ming-installer-verify installed --receipt --require-desktop-profile",
            desktop,
        )
        build = BUILD.read_text(encoding="utf-8")
        self.assertIn("require-desktop-profile", build)
        self.assertIn("startswith(\"/usr/local/sbin/ming-installer-verify installed --receipt\")", build)

    def test_identity_migration_checks_dock_parents_and_preserves_leaf_symlinks(self):
        identity = BASE.read_text(encoding="utf-8").split(
            "cat > /usr/local/sbin/ming-fix-installed-identity", 1
        )[1].split("\nMINGIDENTITY", 1)[0]
        dock_check = 'profile_path_is_safe "${dock_dir}"'
        dock_mkdir = 'mkdir -p "${dock_dir}"'
        self.assertIn(dock_check, identity)
        self.assertLess(identity.index(dock_check), identity.index(dock_mkdir))
        destination_link = 'if [[ -L "${destination}" ]]; then'
        destination_safe = 'profile_path_is_safe "${destination}"'
        self.assertIn(destination_link, identity)
        self.assertLess(identity.index(destination_link), identity.index(destination_safe))
        self.assertIn('destination_parent="$(dirname -- "${destination}")"', identity)
        self.assertIn('profile_path_is_safe "${destination_parent}"', identity)
        self.assertLess(
            identity.index('profile_path_is_safe "${destination_parent}"'),
            identity.index(destination_link),
        )
        self.assertIn('chroot "${target}" chown --no-dereference', identity)

    def test_known_ming_profile_paths_force_replace_unmarked_legacy_rc3_state(self):
        """RC3-era unmarked Ming files must not win over the canonical profile."""
        identity = BASE.read_text(encoding="utf-8").split(
            "cat > /usr/local/sbin/ming-fix-installed-identity", 1
        )[1].split("\nMINGIDENTITY", 1)[0]
        self.assertIn('force="${3:-false}"', identity)
        self.assertIn('[[ "${force}" != true && -e "${destination}" ]]', identity)
        # Explicitly managed files (theme/session/Dock) opt into the force path;
        # arbitrary user desktop files remain marker-gated.
        self.assertIn('copy_profile_file "${source}" "${relative}" true', identity)
        self.assertIn('copy_profile_file "${profile_source}" ".local/share/applications/${launcher}" true', identity)
        self.assertIn('copy_profile_file "${source}" "${relative}" || return 1', identity)

    def test_finalize_preserves_leaf_symlinked_launcher_before_full_path_guard(self):
        finalize = FINALIZE.read_text(encoding="utf-8")
        helper = finalize.split("copy_default_launcher() {", 1)[1].split(
            "\n}\n\nis_managed_desktop_file", 1
        )[0]
        self.assertIn('finalize_path_is_safe "${target_dir}"', helper)
        self.assertIn('if [[ -L "${target}" ]]; then', helper)
        self.assertLess(
            helper.index('if [[ -L "${target}" ]]; then'),
            helper.index('finalize_path_is_safe "${target}"'),
        )

    def test_seed_skel_checks_destination_parent_before_preserving_leaf_links(self):
        finalize = FINALIZE.read_text(encoding="utf-8")
        path_guard = finalize.split("finalize_path_is_safe() {", 1)[1].split(
            "\n}\n\nremove_staged_tree", 1
        )[0]
        self.assertIn("local path=\"$1\" current component index last_index", path_guard)
        self.assertIn("local -a _finalize_components", path_guard)
        helper = finalize.split("copy_skel_item() {", 1)[1].split(
            "\n}\n\nseed_skel()", 1
        )[0]
        self.assertIn('parent="$(dirname -- "${destination}")"', helper)
        self.assertIn('finalize_path_is_safe "${parent}"', helper)
        self.assertLess(
            helper.index('finalize_path_is_safe "${parent}"'),
            helper.index('if [[ -L "${destination}" ]]; then'),
        )

    def test_finalize_panel_restore_checks_user_boundary_before_writes(self):
        source = FINALIZE.read_text(encoding="utf-8")
        helper = source.split("disable_phone_panel_restore() {", 1)[1].split(
            "\n}\n\n# Old image layers", 1
        )[0]
        self.assertIn('finalize_path_is_safe "${USER_HOME}"', helper)
        self.assertIn('mktemp "${xfconf_dir}/.xfce4-session.xml.XXXXXX"', helper)
        self.assertIn('mktemp "${autostart_dir}/.xfce4-panel.desktop.XXXXXX"', helper)
        self.assertLess(
            helper.index('finalize_path_is_safe "${USER_HOME}"'),
            helper.index("mkdir -p"),
        )

    def test_finalize_state_cleanup_checks_paths_before_rm(self):
        source = FINALIZE.read_text(encoding="utf-8")
        helper = source.split("constrain_default_desktop() {", 1)[1].split(
            "\n}\n\nrepair_default_user_ownership", 1
        )[0]
        self.assertIn("finalize_path_is_safe", helper)
        self.assertIn("desktop-layout.json", helper)
        self.assertIn("remove_managed_state_file", helper)

    def test_finalize_fails_closed_when_cache_parent_is_symlink(self):
        """An unsafe cache parent must stop finalization before later writes run."""
        bash_path = pathlib.Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(bash_path) if bash_path.exists() else shutil.which("bash")
        if bash is None:
            self.skipTest("Bash is required to exercise the shell helper")

        source = FINALIZE.read_text(encoding="utf-8")
        path_guard_start = source.index("finalize_path_is_safe() {")
        path_guard_end = source.index("\n}\n\nremove_managed_state_file", path_guard_start) + 2
        repair_start = source.index("repair_default_user_ownership() {")
        repair_end = source.index("\n}\n\ndisable_phone_panel_restore", repair_start) + 2
        main_start = source.index("main() {")
        main_end = source.index("\n}\n\nmain", main_start) + 2

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            outside = root / "outside"
            home.mkdir()
            outside.mkdir()
            marker = root / "after-unsafe-ownership-repair"
            try:
                (home / ".cache").symlink_to(outside, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"directory symlinks are unavailable: {error}")

            if os.name == "nt":
                converted = subprocess.run(
                    [bash, "-lc", 'cygpath -u -- "$1"', "--", str(root)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                if converted.returncode != 0:
                    self.skipTest("Bash could not convert the temporary Windows path")
                root_for_bash = converted.stdout.strip()
            else:
                root_for_bash = str(root)

            script = root / "exercise-repair-ownership.sh"
            script.write_text(
                "\n".join(
                    (
                        "#!/usr/bin/env bash",
                        "set +e",
                        source[path_guard_start:path_guard_end],
                        source[repair_start:repair_end],
                        "refresh_dock_launchers() { return 0; }",
                        "seed_trusted_desktop_receipts() { :; }",
                        "verify_other_os_detector() { return 0; }",
                        "disable_phone_panel_restore() { :; }",
                        "cleanup_zero_byte_calamares_entries() { return 0; }",
                        "retire_legacy_store_runtime() { return 0; }",
                        "seed_skel() { :; }",
                        "constrain_default_desktop() { :; }",
                        'verify_appearance_assets() { : > "${MING_TEST_MARKER}"; }',
                        "converge_package_state() { return 0; }",
                        source[main_start:main_end],
                        "main",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["USER_HOME"] = f"{root_for_bash}/home"
            environment["MING_USER"] = "ming-test"
            environment["MING_OS_VERSION"] = "test"
            environment["MING_TEST_MARKER"] = f"{root_for_bash}/after-unsafe-ownership-repair"
            result = subprocess.run(
                [bash, f"{root_for_bash}/exercise-repair-ownership.sh"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=10,
            )

            self.assertNotEqual(
                result.returncode,
                0,
                f"finalization continued after an unsafe cache parent:\n"
                f"stdout={result.stdout}\nstderr={result.stderr}",
            )
            self.assertFalse(marker.exists(), "later finalization steps ran after an unsafe cache parent")
            self.assertFalse((outside / "ming-os").exists())
            self.assertFalse((outside / "sessions").exists())


if __name__ == "__main__":
    unittest.main()
