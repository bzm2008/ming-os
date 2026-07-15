import importlib.util
import pathlib
import stat
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMMON_PATH = ROOT / "assets" / "ming-shell-common.py"
LAUNCH_PATH = ROOT / "assets" / "ming-launch.py"


def load_common():
    spec = importlib.util.spec_from_file_location("ming_shell_common", COMMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_launch():
    spec = importlib.util.spec_from_file_location("ming_launch_trusted", LAUNCH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata(kind, mode=0o644, uid=0):
    return types.SimpleNamespace(st_mode=kind | mode, st_uid=uid)


def protected_directory(mode=0o755, uid=0):
    return metadata(stat.S_IFDIR, mode, uid)


def protected_regular_file(mode=0o644, uid=0):
    return metadata(stat.S_IFREG, mode, uid)


def stat_reader_for(entries):
    expected = {pathlib.Path(path): value for path, value in entries.items()}

    def reader(path):
        return expected[pathlib.Path(path)]

    return reader


class TrustedDesktopActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load_common()

    def test_accepts_protected_system_desktop_wrapper_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertTrue(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_user_desktop_entry_even_when_stat_looks_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            system_dir = root / "usr-share-applications"
            user_dir = root / "user-applications"
            system_dir.mkdir()
            user_dir.mkdir()
            desktop = user_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_candidate_when_resolution_indicates_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            target = system_dir / "target.desktop"
            source = system_dir / "store-wrapper.desktop"
            target.write_text("[Desktop Entry]\n", encoding="utf-8")
            source.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(path):
                path = pathlib.Path(path)
                if path == source:
                    return target
                return path.resolve(strict=True)

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                source,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    target: protected_regular_file(),
                }),
            ))

    def test_rejects_group_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(0o664),
                }),
            ))

    def test_rejects_other_writable_system_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(0o646),
                }),
            ))

    def test_rejects_nonstandard_desktop_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.DESKTOP"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_writable_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(0o775),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_other_writable_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(0o757),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_non_directory_system_path(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_regular_file(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_non_root_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(uid=1000),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_rejects_symlinked_system_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            system_dir = root / "applications"
            target_dir = root / "resolved-applications"
            system_dir.mkdir()
            target_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            target = target_dir / desktop.name
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            target.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(path):
                path = pathlib.Path(path)
                if path == system_dir:
                    return target_dir
                if path == desktop:
                    return target
                return path.resolve(strict=True)

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    target_dir: protected_directory(),
                    target: protected_regular_file(),
                }),
            ))

    def test_rejects_non_root_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(uid=1000),
                }),
            ))

    def test_rejects_non_regular_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: metadata(stat.S_IFDIR),
                }),
            ))

    def test_rejects_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "missing.desktop"

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({system_dir: protected_directory()}),
            ))

    def test_rejects_nested_desktop_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            nested_dir = system_dir / "nested"
            nested_dir.mkdir(parents=True)
            desktop = nested_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))

    def test_returns_false_when_path_resolution_hits_a_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            system_dir = pathlib.Path(directory) / "applications"
            system_dir.mkdir()
            desktop = system_dir / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            def resolver(_path):
                raise RuntimeError("symlink loop")

            self.assertFalse(self.common.is_system_desktop_activation_candidate(
                desktop,
                system_dir=system_dir,
                path_resolver=resolver,
                stat_reader=stat_reader_for({
                    system_dir: protected_directory(),
                    desktop: protected_regular_file(),
                }),
            ))


class TrustedLaunchRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = load_launch()

    def test_protected_system_shell_wrapper_selects_internal_app_info_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            request = self.launch.request_from_desktop_file(
                desktop,
                allowed_dirs=(applications,),
                candidate_verifier=lambda path: path == desktop.resolve(),
                trusted_verifier=lambda path: path == desktop.resolve(),
            )

            self.assertEqual("desktop_app_info", request.mode)
            self.assertEqual((), request.argv)
            self.assertEqual(str(desktop.resolve()), request.desktop_file)

    def test_shell_wrapper_does_not_select_internal_mode_without_final_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_desktop_file(
                    desktop,
                    allowed_dirs=(applications,),
                    candidate_verifier=lambda _path: True,
                    trusted_verifier=lambda _path: False,
                )

    def test_final_verifier_requires_an_exact_installed_package_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")
            calls = []

            def query(argv, timeout):
                calls.append((tuple(argv), timeout))
                if "-S" in argv:
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="ii \tstore-wrapper\n",
                    stderr="",
                )

            self.assertTrue(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=query,
                descriptor_revalidator=lambda path, parent: (
                    path == desktop.resolve() and parent == applications.resolve()
                ),
            ))
            self.assertEqual(2, len(calls))
            self.assertTrue(all(timeout <= 2 for _argv, timeout in calls))

    def test_final_verifier_rejects_unowned_ambiguous_mismatched_and_noninstalled_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            cases = {
                "unowned": (
                    types.SimpleNamespace(returncode=1, stdout="", stderr="not found"),
                    None,
                ),
                "ambiguous": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="first, second: {}\n".format(desktop.resolve()),
                        stderr="",
                    ),
                    None,
                ),
                "mismatched": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: /usr/share/applications/other.desktop\n",
                        stderr="",
                    ),
                    None,
                ),
                "not-installed": (
                    types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    ),
                    types.SimpleNamespace(returncode=0, stdout="hi \tstore-wrapper\n", stderr=""),
                ),
            }
            for name, (ownership, installation) in cases.items():
                with self.subTest(name=name):
                    def query(argv, timeout, ownership=ownership, installation=installation):
                        del timeout
                        return ownership if "-S" in argv else installation

                    self.assertFalse(self.launch.verify_package_owned_system_desktop(
                        desktop,
                        system_dir=applications,
                        command_runner=query,
                        descriptor_revalidator=lambda *_args: True,
                    ))

    def test_shell_wrapper_ipc_cannot_select_an_internal_launch_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Store Wrapper\n"
                "Exec=sh -c 'exec /opt/store-wrapper/run'\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_message({
                    "version": 1,
                    "action": "launch",
                    "desktop_file": str(desktop),
                    "source": "drawer",
                    "rect": None,
                    "mode": "desktop_app_info",
                }, allowed_dirs=(applications,))

    def test_non_shell_parse_failure_never_selects_desktop_app_info_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "broken-wrapper.desktop"
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Broken Wrapper\n"
                "Exec=sh -c 'unterminated\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                self.launch.request_from_desktop_file(
                    desktop,
                    allowed_dirs=(applications,),
                    candidate_verifier=lambda _path: True,
                )

    def test_final_verifier_rejects_query_timeouts_and_descriptor_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            applications = pathlib.Path(directory) / "applications"
            applications.mkdir()
            desktop = applications / "store-wrapper.desktop"
            desktop.write_text("[Desktop Entry]\n", encoding="utf-8")

            def timeout(_argv, timeout):
                self.assertLessEqual(timeout, 2)
                raise self.launch.subprocess.TimeoutExpired("dpkg-query", timeout)

            self.assertFalse(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=timeout,
                descriptor_revalidator=lambda *_args: self.fail("must not revalidate"),
            ))

            def installed_query(argv, timeout):
                del timeout
                if "-S" in argv:
                    return types.SimpleNamespace(
                        returncode=0,
                        stdout="store-wrapper: {}\n".format(desktop.resolve()),
                        stderr="",
                    )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="ii \tstore-wrapper\n",
                    stderr="",
                )

            self.assertFalse(self.launch.verify_package_owned_system_desktop(
                desktop,
                system_dir=applications,
                command_runner=installed_query,
                descriptor_revalidator=lambda *_args: False,
            ))


if __name__ == "__main__":
    unittest.main()
