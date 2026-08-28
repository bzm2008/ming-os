import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ming_wine_installer", ROOT / "assets" / "ming-wine-installer.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WineInstallerTests(unittest.TestCase):
    def test_runtime_requires_wine_and_executes_safe_wineboot_probe(self):
        calls = []

        def runner(command, timeout=20, env=None):
            calls.append(tuple(command))
            if tuple(command) == ("wine", "--version"):
                return 0, "wine-9.0\n", ""
            if tuple(command) == ("wineboot", "--help"):
                return 0, "usage", ""
            return 1, "", "missing"

        result = MODULE.WineInstaller(
            runner=runner, executable=lambda name: "/usr/bin/" + name if name == "wineboot" else None
        ).detect_runtime()
        self.assertTrue(result["ok"])
        self.assertEqual(["wine", "--version"], list(calls[0]))
        self.assertIn(("wineboot", "--help"), calls)

    def test_prefix_uses_per_application_apps_directory_and_metadata_path(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = MODULE.WineInstaller(home=directory)
            prefix = installer.prefix_for("Contoso Office.exe")
            self.assertEqual("prefix", prefix.name)
            self.assertTrue(prefix.parent.name.startswith("Contoso-Office-"))
            self.assertEqual(pathlib.Path(directory) / ".local/share/ming-wine/apps" / prefix.parent.name / "prefix", prefix)
            self.assertEqual(prefix.parent / "metadata.json", installer.metadata_for("Contoso Office.exe"))

    def test_sanitized_name_collisions_get_distinct_application_ids(self):
        installer = MODULE.WineInstaller(home="C:/Users/example")
        self.assertNotEqual(installer.app_id("foo bar.exe"), installer.app_id("foo@bar.exe"))

    def test_replacing_an_existing_source_path_with_different_content_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "sample.exe"
            source.write_bytes(b"MZ first")

            def runner(command, timeout=20, env=None):
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Sample/sample.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            self.assertTrue(installer.install(source)["ok"])
            source.write_bytes(b"MZ second")
            result = installer.install(source)
            self.assertFalse(result["ok"])
            self.assertEqual("source_conflict", result["state"])

    def test_install_msi_uses_wine_msiexec_and_writes_metadata_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "sample.msi"
            source.write_bytes(b"MSI")
            calls = []

            def runner(command, timeout=20, env=None):
                calls.append((tuple(command), env))
                if tuple(command)[:3] == ("wine", "msiexec", "/i"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Sample/sample.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "installed", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            result = installer.install(source)
            self.assertTrue(result["ok"])
            install_call = next(item for item in calls if item[0][:3] == ("wine", "msiexec", "/i"))
            self.assertTrue(install_call[0][3].endswith("installer.msi"))
            self.assertIn(".local", install_call[0][3])
            self.assertEqual(str(installer.prefix_for(source)), install_call[1]["WINEPREFIX"])
            metadata = json.loads(installer.metadata_for(source).read_text(encoding="utf-8"))
            self.assertEqual("installed", metadata["state"])
            self.assertTrue(metadata["launch_target"].replace("\\", "/").endswith("Sample/sample.exe"))
            events = [json.loads(line) for line in installer.log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual("install", events[-1]["action"])

    def test_managed_install_uses_catalog_app_id_and_declared_launch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "downloaded-artifact.exe"
            source.write_bytes(b"MZ managed")
            managed_id = "notepad-plus-plus-wine"
            declared_target = "drive_c/Program Files/Notepad++/notepad++.exe"

            def runner(command, timeout=20, env=None):
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / declared_target
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            result = installer.install_managed(
                source, managed_id, declared_target, "Notepad++（Wine）")

            self.assertTrue(result["ok"])
            self.assertEqual(managed_id, result["app_id"])
            metadata_path = root / "home/.local/share/ming-wine/apps" / managed_id / "metadata.json"
            self.assertTrue(metadata_path.is_file())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(managed_id, metadata["app_id"])
            self.assertTrue(metadata["launch_target"].replace("\\", "/").endswith("Notepad++/notepad++.exe"))
            self.assertFalse((root / "home/.local/share/ming-wine/apps/downloaded-artifact").exists())

    def test_managed_install_rejects_missing_declared_launch_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "downloaded-artifact.exe"
            source.write_bytes(b"MZ managed")

            installer = MODULE.WineInstaller(
                home=root / "home", runner=lambda *args, **kwargs: (0, "", ""))
            result = installer.install_managed(
                source, "managed-app", "drive_c/Program Files/Missing/app.exe", "Managed App")

            self.assertFalse(result["ok"])
            self.assertEqual("installed_needs_launcher", result["state"])
            metadata = json.loads(
                (root / "home/.local/share/ming-wine/apps/managed-app/metadata.json").read_text(
                    encoding="utf-8"))
            self.assertEqual("managed-app", metadata["app_id"])

    def test_managed_install_persists_refresh_warning_in_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "downloaded-artifact.exe"
            source.write_bytes(b"MZ managed")
            declared_target = "drive_c/Program Files/Managed/managed.exe"

            def runner(command, timeout=20, env=None):
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / declared_target
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            installer._refresh_desktop = lambda: False
            result = installer.install_managed(
                source, "managed-app", declared_target, "Managed App")

            self.assertTrue(result["ok"])
            self.assertEqual("installed_with_refresh_warning", result["state"])
            metadata = json.loads(
                (root / "home/.local/share/ming-wine/apps/managed-app/metadata.json").read_text(
                    encoding="utf-8"))
            self.assertEqual("installed_with_refresh_warning", metadata["state"])

    def test_install_rejects_symlink_and_shell_like_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "payload.exe"
            source.write_bytes(b"MZ")
            link = root / "link.exe"
            try:
                link.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            installer = MODULE.WineInstaller(home=root / "home", runner=lambda *args, **kwargs: (0, "", ""))
            result = installer.install(link)
            self.assertFalse(result["ok"])
            self.assertEqual("validation_failed", result["state"])
            self.assertFalse(installer.install("sh -c evil.exe")["ok"])

    def test_32_bit_pe_uses_win32_prefix_and_requires_wine32(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "legacy.exe"
            payload = bytearray(b"MZ" + b"\0" * 0x3c)
            payload[0x3c:0x3c + 4] = (0x40).to_bytes(4, "little")
            payload.extend(b"\0" * (0x40 - len(payload)))
            payload.extend(b"PE\0\0" + (0x14C).to_bytes(2, "little"))
            source.write_bytes(payload)
            def runner(command, timeout=20, env=None):
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Legacy/legacy.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "", ""

            installer = MODULE.WineInstaller(
                home=root / "home", runner=runner,
                executable=lambda name: "/usr/bin/" + name if name in {"wineboot", "wine32"} else None,
            )
            result = installer.install(source)
            self.assertEqual("win32", result["architecture"])
            self.assertEqual("win32", result["metadata"]["architecture"])

    def test_managed_32_bit_install_fails_before_creating_prefix_without_wine32(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "legacy.exe"
            payload = bytearray(b"MZ" + b"\0" * 0x3c)
            payload[0x3c:0x3c + 4] = (0x40).to_bytes(4, "little")
            payload.extend(b"\0" * (0x40 - len(payload)))
            payload.extend(b"PE\0\0" + (0x14C).to_bytes(2, "little"))
            source.write_bytes(payload)
            installer = MODULE.WineInstaller(
                home=root / "home", runner=lambda *_args, **_kwargs: (0, "", ""),
                executable=lambda name: "/usr/bin/" + name if name == "wineboot" else None,
            )

            result = installer.install_managed(
                source, "legacy-managed", "drive_c/Legacy/legacy.exe", "Legacy")

            self.assertFalse(result["ok"])
            self.assertEqual("runtime_32_unavailable", result["state"])
            self.assertFalse((root / "home/.local/share/ming-wine/apps/legacy-managed").exists())

    def test_managed_install_rejects_symlinked_application_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "managed.exe"
            source.write_bytes(b"MZ managed")
            apps_root = root / "home/.local/share/ming-wine/apps"
            apps_root.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            app_dir = apps_root / "managed-app"
            try:
                app_dir.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            installer = MODULE.WineInstaller(
                home=root / "home", runner=lambda *_args, **_kwargs: (0, "", ""))
            result = installer.install_managed(
                source, "managed-app", "drive_c/Managed/managed.exe", "Managed")

            self.assertFalse(result["ok"])
            self.assertEqual("path_security_failed", result["state"])
            self.assertFalse((outside / "metadata.json").exists())

    def test_local_install_rejects_symlinked_application_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            source = root / "managed.exe"
            source.write_bytes(b"MZ managed")
            installer = MODULE.WineInstaller(
                home=home, runner=lambda *_args, **_kwargs: (0, "", ""))
            apps_root = home / ".local/share/ming-wine/apps"
            apps_root.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            app_dir = apps_root / installer.app_id(source)
            try:
                app_dir.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            result = installer.install(source)

            self.assertFalse(result["ok"])
            self.assertEqual("path_security_failed", result["state"])
            self.assertFalse((outside / "source").exists())
            self.assertFalse((outside / "prefix").exists())

    def test_local_install_rejects_symlinked_prefix_before_wine_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            home = root / "home"
            source = root / "managed.exe"
            source.write_bytes(b"MZ managed")
            installer = MODULE.WineInstaller(
                home=home, runner=lambda *_args, **_kwargs: (0, "", ""))
            app_dir = installer.app_dir_for(source)
            (app_dir / "source").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            try:
                (app_dir / "prefix").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            result = installer.install(source)

            self.assertFalse(result["ok"])
            self.assertEqual("path_security_failed", result["state"])
            self.assertFalse((outside / "drive_c").exists())


    def test_uninstall_removes_only_the_application_prefix_and_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "sample.exe"
            source.write_bytes(b"MZ")
            calls = []

            def runner(command, timeout=20, env=None):
                calls.append((tuple(command), env))
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Sample/sample.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                if tuple(command)[:2] == ("wine", "uninstaller"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Sample/sample.exe"
                    target.unlink(missing_ok=True)
                return 0, "ok", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            installed = installer.install(source)
            entry = pathlib.Path(installed["desktop_file"])
            self.assertTrue(entry.is_file())
            result = installer.uninstall(source)
            self.assertTrue(result["ok"])
            self.assertFalse(installer.prefix_for(source).parent.exists())
            self.assertFalse(entry.exists())

    def test_visible_desktop_entry_uses_ming_launch_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "sample.exe"
            source.write_bytes(b"MZ")

            def runner(command, timeout=20, env=None):
                if len(command) > 1 and command[0] == "wine" and str(command[1]).lower().endswith(".exe"):
                    target = pathlib.Path(env["WINEPREFIX"]) / "drive_c/Program Files/Sample/sample.exe"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"MZ app")
                return 0, "", ""

            installer = MODULE.WineInstaller(home=root / "home", runner=runner)
            result = installer.install(source)
            self.assertTrue(result["ok"])
            text = pathlib.Path(result["desktop_file"]).read_text(encoding="utf-8")
            self.assertIn("/usr/local/bin/ming-launch", text)
            target = root / "home/.local/share/ming-wine/targets"
            target_file = next(target.glob("*.desktop"))
            target_text = target_file.read_text(encoding="utf-8")
            self.assertIn("ming-wine-installer run", target_text)
            launch_spec = importlib.util.spec_from_file_location(
                "ming_launch_for_wine_entry", ROOT / "assets/ming-launch.py"
            )
            launch = importlib.util.module_from_spec(launch_spec)
            launch_spec.loader.exec_module(launch)
            request = launch.request_from_desktop_file(
                str(target_file), "desktop", allowed_dirs=(target,)
            )
            self.assertEqual("argv", request.mode)
            self.assertIn("ming-wine-installer", request.argv[0])

    def test_launch_requires_a_stable_wine_process(self):
        class FakeProcess:
            pid = 4321

            def __init__(self, values):
                self.values = iter(values)

            def poll(self):
                return next(self.values, None)

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-wine/apps/demo"
            prefix = app_dir / "prefix"
            target = prefix / "drive_c/Program Files/Demo/demo.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ")
            (app_dir / "metadata.json").write_text(json.dumps({
                "app_id": "demo", "state": "installed", "launch_target": str(target),
                "architecture": "win64",
            }), encoding="utf-8")

            stable = MODULE.WineInstaller(
                home=root, spawner=lambda *args, **kwargs: FakeProcess([None] * 8),
                sleeper=lambda _seconds: None,
            ).launch("demo")
            self.assertTrue(stable["ok"])
            self.assertEqual("started", stable["state"])

            fast_exit = MODULE.WineInstaller(
                home=root, spawner=lambda *args, **kwargs: FakeProcess([9]),
                sleeper=lambda _seconds: None,
            ).launch("demo")
            self.assertFalse(fast_exit["ok"])
            self.assertEqual("launch_failed", fast_exit["state"])

    def test_launch_rejects_target_outside_its_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-wine/apps/demo"
            (app_dir / "prefix").mkdir(parents=True)
            outside = root / "outside.exe"
            outside.write_bytes(b"MZ")
            (app_dir / "metadata.json").write_text(json.dumps({
                "app_id": "demo", "state": "installed", "launch_target": str(outside),
                "architecture": "win64",
            }), encoding="utf-8")
            result = MODULE.WineInstaller(home=root).launch("demo")
            self.assertFalse(result["ok"])
            self.assertEqual("launch_target_invalid", result["state"])

    def test_launch_rejects_symlinked_intermediate_prefix_component(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-wine/apps/demo"
            prefix = app_dir / "prefix"
            prefix.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            try:
                (prefix / "drive_c").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            target = prefix / "drive_c/Program Files/Demo/demo.exe"
            (outside / "Program Files/Demo").mkdir(parents=True)
            (outside / "Program Files/Demo/demo.exe").write_bytes(b"MZ")
            (app_dir / "metadata.json").write_text(json.dumps({
                "app_id": "demo", "state": "installed", "launch_target": str(target),
                "architecture": "win64",
            }), encoding="utf-8")

            result = MODULE.WineInstaller(home=root).launch("demo")

            self.assertFalse(result["ok"])
            self.assertEqual("launch_target_invalid", result["state"])

    def test_launch_rejects_symlinked_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-wine/apps/demo"
            prefix = app_dir / "prefix"
            target = prefix / "drive_c/Program Files/Demo/demo.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ")
            outside = root / "metadata.json"
            outside.write_text(json.dumps({
                "app_id": "demo", "state": "installed", "launch_target": str(target),
                "architecture": "win64",
            }), encoding="utf-8")
            try:
                (app_dir / "metadata.json").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")

            result = MODULE.WineInstaller(home=root).launch("demo")

            self.assertFalse(result["ok"])
            self.assertEqual("not_installed", result["state"])

    def test_lab_options_apply_only_to_selected_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            app_dir = root / ".local/share/ming-wine/apps/demo"
            target = app_dir / "prefix/drive_c/Program Files/Demo/demo.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"MZ")
            (app_dir / "metadata.json").write_text(json.dumps({
                "app_id": "demo", "state": "installed", "launch_target": str(target),
                "architecture": "win64",
            }), encoding="utf-8")
            lab = root / ".local/share/ming-wine/lab"
            lab.mkdir(parents=True)
            (lab / "demo.json").write_text(json.dumps({
                "dxvk": True, "proton": False, "game_mode": True, "verbose_logs": True,
            }), encoding="utf-8")
            calls = []
            installer = MODULE.WineInstaller(
                home=root, executable=lambda name: "/usr/bin/" + name,
                execer=lambda program, argv, env: calls.append((program, argv, env)),
            )
            result = installer.run_foreground("demo")
            self.assertTrue(result["ok"])
            self.assertEqual("/usr/bin/gamemoderun", calls[0][0])
            self.assertEqual("dxgi,d3d11=n,b", calls[0][2]["WINEDLLOVERRIDES"])
            self.assertIn("+timestamp", calls[0][2]["WINEDEBUG"])


if __name__ == "__main__":
    unittest.main()
