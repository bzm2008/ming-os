import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_asset():
    path = ROOT / "assets" / "ming-launch.py"
    spec = importlib.util.spec_from_file_location("ming_launch_trusted_2641", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TrustedSystemLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_asset()

    def test_system_desktop_uses_verified_desktop_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory)
            desktop = system_dir / "example.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Example\n"
                "Exec=/bin/echo complex %U\n",
                encoding="utf-8")

            request = self.launch.request_from_desktop_file(
                desktop, source="desktop", allowed_dirs=[system_dir],
                system_dir=system_dir)

        self.assertEqual("desktop_app_info", request.mode)
        self.assertEqual((), request.argv)

    def test_broker_revalidates_immediately_before_desktop_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop_path = str(pathlib.Path(directory) / "example.desktop")
        events = []
        request = self.launch.LaunchRequest(
            (), desktop_file=desktop_path,
            mode="desktop_app_info")
        broker = self.launch.LaunchBroker(
            trusted_verifier=lambda path: events.append(("verify", path)) or True,
            desktop_activator=lambda path: events.append(("activate", path)) or True,
            record_event=lambda *_args: None,
            report_error=lambda *_args: None,
            reduced_motion=lambda: True,
        )

        self.assertTrue(broker.launch(request))
        self.assertEqual(
            [("verify", request.desktop_file), ("activate", request.desktop_file)],
            events)

    def test_broker_fails_closed_when_system_desktop_verification_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop_path = str(pathlib.Path(directory) / "example.desktop")
        activated = []
        request = self.launch.LaunchRequest(
            (), desktop_file=desktop_path,
            mode="desktop_app_info")
        broker = self.launch.LaunchBroker(
            trusted_verifier=lambda _path: False,
            desktop_activator=lambda path: activated.append(path) or True,
            record_event=lambda *_args: None,
            report_error=lambda *_args: None,
            reduced_motion=lambda: True,
        )

        self.assertFalse(broker.launch(request))
        self.assertEqual([], activated)

    def test_system_desktop_requires_one_installed_package_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "example.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            desktop.chmod(0o644)
            calls = []

            def runner(command, timeout=2):
                calls.append((command, timeout))
                if command[:2] == ("-S", "--"):
                    return 0, "example-package: %s\n" % desktop, ""
                if command[:2] == ("-W", "-f=${db:Status-Abbrev}"):
                    return 0, "ii ", ""
                return 1, "", "unexpected query"

            self.assertTrue(self.launch.verify_package_owned_system_desktop(
                desktop, system_dir=root, command_runner=runner,
                descriptor_revalidator=lambda *_args: True))
            self.assertEqual(2, len(calls))

    def test_system_desktop_rejects_multiple_package_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "example.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            desktop.chmod(0o644)

            def runner(_command, timeout=2):
                return 0, "one: %s\ntwo: %s\n" % (desktop, desktop), ""

            self.assertFalse(self.launch.verify_package_owned_system_desktop(
                desktop, system_dir=root, command_runner=runner,
                descriptor_revalidator=lambda *_args: True))

    def test_user_desktop_keeps_strict_argv_path(self):
        with tempfile.TemporaryDirectory() as directory:
            user_dir = pathlib.Path(directory) / "user-apps"
            system_dir = pathlib.Path(directory) / "system-apps"
            user_dir.mkdir()
            system_dir.mkdir()
            desktop = user_dir / "example.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Example\nExec=/bin/echo hello\n",
                encoding="utf-8")

            request = self.launch.request_from_desktop_file(
                desktop, allowed_dirs=[user_dir, system_dir], system_dir=system_dir)

        self.assertEqual("argv", request.mode)
        self.assertEqual("/bin/echo", request.argv[0])

    def test_image_owned_system_launchers_have_read_only_receipts(self):
        module = (ROOT / "modules" / "07_finalize.sh").read_text(encoding="utf-8")
        build = (ROOT / "build_onion_os.sh").read_text(encoding="utf-8")
        self.assertIn("/var/lib/ming-os/trusted-desktops", module)
        self.assertIn("trusted-desktops", build)


if __name__ == "__main__":
    unittest.main()
