import importlib.util
import ast
import os
import pathlib
import sys
import tempfile
import threading
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "assets" / "ming-files-model.py"
UI_PATH = ROOT / "assets" / "ming-files.py"


def load_model():
    spec = importlib.util.spec_from_file_location("ming_files_model", MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MingFilesAssetTests(unittest.TestCase):
    def test_ming_files_assets_exist(self):
        self.assertTrue(MODEL_PATH.is_file(), "missing ming-files-model.py")
        self.assertTrue(UI_PATH.is_file(), "missing ming-files.py")


class MingFilesPublicApiTests(unittest.TestCase):
    def test_model_exports_stable_runtime_contract(self):
        model = load_model()
        names = [
            "SortKey",
            "FileItem",
            "Breadcrumb",
            "OperationError",
            "OperationResult",
            "CancellationToken",
            "LocalFileBackend",
            "GioFileBackend",
            "LocationModel",
            "create_runtime_model",
            "migrate_location_state",
        ]
        missing = [name for name in names if not hasattr(model, name)]
        self.assertEqual(missing, [])


class LocationModelBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = load_model()

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = pathlib.Path(self.tempdir.name)
        self.backend = self.api.LocalFileBackend()
        self.model = self.api.LocationModel(
            self.backend, home_uri=self.root.as_uri()
        )

    def make_file(self, relative, content="x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def call(self, name, *args, **kwargs):
        try:
            return getattr(self.model, name)(*args, **kwargs)
        except NotImplementedError:
            self.fail("LocationModel.%s is not implemented" % name)

    def test_load_filters_hidden_entries_and_sorts_directories_first(self):
        (self.root / "Zoo").mkdir()
        self.make_file("alpha.txt", "a")
        self.make_file(".secret", "hidden")

        items = self.call("load")

        self.assertEqual([item.name for item in items], ["Zoo", "alpha.txt"])
        self.assertTrue(items[0].is_directory)
        self.call("set_show_hidden", True)
        self.assertEqual(
            [item.name for item in self.model.items],
            ["Zoo", ".secret", "alpha.txt"],
        )

    def test_sort_supports_size_modified_and_type(self):
        small = self.make_file("small.txt", "1")
        large = self.make_file("large.bin", "12345")
        os.utime(small, (100, 100))
        os.utime(large, (200, 200))
        self.call("load")

        self.call("set_sort", self.api.SortKey.SIZE, descending=True)
        self.assertEqual([item.name for item in self.model.items], ["large.bin", "small.txt"])
        self.call("set_sort", self.api.SortKey.MODIFIED)
        self.assertEqual([item.name for item in self.model.items], ["small.txt", "large.bin"])
        self.call("set_sort", self.api.SortKey.TYPE)
        self.assertEqual([item.name for item in self.model.items], ["large.bin", "small.txt"])

    def test_breadcrumbs_and_history_navigation_follow_real_directories(self):
        nested = self.root / "Documents" / "Project"
        nested.mkdir(parents=True)
        self.call("load")
        self.call("navigate", nested.as_uri())

        crumbs = self.call("breadcrumbs")
        self.assertEqual([crumb.label for crumb in crumbs[-2:]], ["Documents", "Project"])
        self.assertEqual(self.call("up"), (self.root / "Documents").as_uri())
        self.assertEqual(self.call("back"), nested.as_uri())
        self.assertEqual(self.call("forward"), (self.root / "Documents").as_uri())

    def test_out_of_order_navigation_queries_only_change_state_when_committed(self):
        slow = self.root / "Slow"
        fast = self.root / "Fast"
        slow.mkdir()
        fast.mkdir()
        self.make_file("Slow/old.txt")
        self.make_file("Fast/current.txt")
        slow_started = threading.Event()
        release_slow = threading.Event()

        class BlockingBackend(self.api.LocalFileBackend):
            def enumerate(inner_self, uri, token=None):
                if uri == slow.as_uri():
                    slow_started.set()
                    if not release_slow.wait(2):
                        raise TimeoutError("slow navigation was never released")
                return super().enumerate(uri, token)

        self.model = self.api.LocationModel(
            BlockingBackend(), home_uri=self.root.as_uri()
        )
        self.call("load")
        slow_query = self.call("prepare_navigate", slow.as_uri())
        slow_result = {}

        worker = threading.Thread(
            target=lambda: slow_result.setdefault(
                "value", self.call("execute_query", slow_query)
            )
        )
        worker.start()
        self.assertTrue(slow_started.wait(2), "slow navigation did not start")

        self.assertEqual(self.model.current_uri, self.root.as_uri())
        fast_query = self.call("prepare_navigate", fast.as_uri())
        current_result = self.call("execute_query", fast_query)
        self.call("commit_query", current_result)
        release_slow.set()
        worker.join(2)

        self.assertFalse(worker.is_alive(), "slow navigation did not finish")
        self.assertIn("value", slow_result)
        self.assertEqual(self.model.current_uri, fast.as_uri())
        self.assertEqual([item.name for item in self.model.items], ["current.txt"])
        self.assertEqual(self.model._back, [self.root.as_uri()])

    def test_search_is_case_insensitive_recursive_and_respects_hidden_toggle(self):
        wanted = self.make_file("Projects/Release Notes.TXT", "notes")
        hidden = self.make_file(".private/release-secret.txt", "secret")
        self.call("load")

        self.assertEqual([item.uri for item in self.call("search", "release")], [wanted.as_uri()])
        self.call("set_show_hidden", True)
        self.assertEqual(
            {item.uri for item in self.call("search", "RELEASE")},
            {wanted.as_uri(), hidden.as_uri()},
        )

    def test_copy_move_and_rename_change_real_files(self):
        source = self.make_file("source.txt", "payload")
        copies = self.root / "Copies"
        moved = self.root / "Moved"
        copies.mkdir()
        moved.mkdir()

        copied = self.call("copy", source.as_uri(), copies.as_uri())
        self.assertTrue(copied.success, copied.error)
        self.assertEqual((copies / "source.txt").read_text(encoding="utf-8"), "payload")

        moved_result = self.call("move", copied.target_uri, moved.as_uri())
        self.assertTrue(moved_result.success, moved_result.error)
        self.assertFalse((copies / "source.txt").exists())

        renamed = self.call("rename", moved_result.target_uri, "renamed.txt")
        self.assertTrue(renamed.success, renamed.error)
        self.assertTrue((moved / "renamed.txt").is_file())

    def test_cancelled_copy_has_structured_error_and_does_not_write(self):
        source = self.make_file("source.txt", "payload")
        target = self.root / "Target"
        target.mkdir()
        token = self.api.CancellationToken()
        token.cancel()

        result = self.call("copy", source.as_uri(), target.as_uri(), token=token)

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "cancelled")
        self.assertEqual(result.error.source_uri, source.as_uri())
        self.assertFalse((target / "source.txt").exists())

    def test_copy_cancelled_after_progress_removes_partial_target(self):
        source = self.root / "large.bin"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        target = self.root / "Target"
        target.mkdir()
        token = self.api.CancellationToken()

        result = self.call(
            "copy",
            source.as_uri(),
            target.as_uri(),
            token=token,
            progress=lambda current, _total: token.cancel() if current else None,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "cancelled")
        self.assertFalse((target / "large.bin").exists())

    def test_missing_source_and_unsupported_trash_return_structured_errors(self):
        missing = (self.root / "missing.txt").as_uri()

        copy_result = self.call("copy", missing, self.root.as_uri())
        trash_result = self.call("trash", missing)
        restore_result = self.call("restore", "trash:///missing")
        empty_result = self.call("empty_trash")

        self.assertEqual(copy_result.error.code, "not-found")
        self.assertEqual(trash_result.error.code, "unsupported")
        self.assertEqual(restore_result.error.code, "unsupported")
        self.assertEqual(empty_result.error.code, "unsupported")

    def test_create_folder_and_permanent_delete_change_real_directory(self):
        for name in ("create_folder", "delete"):
            self.assertTrue(
                callable(getattr(self.model, name, None)),
                "LocationModel.%s is missing" % name,
            )

        created = self.model.create_folder(self.root.as_uri(), "New Folder")
        self.assertTrue(created.success, created.error)
        folder = self.root / "New Folder"
        self.assertTrue(folder.is_dir())
        (folder / "nested.txt").write_text("payload", encoding="utf-8")

        deleted = self.model.delete(folder.as_uri())

        self.assertTrue(deleted.success, deleted.error)
        self.assertFalse(folder.exists())

    def test_state_migration_is_versioned_and_rejects_invalid_values(self):
        migrated = self.api.migrate_location_state(
            {
                "location": self.root.as_uri(),
                "history": [self.root.as_uri(), 7, ""],
                "show_hidden": 1,
                "sort": "not-a-sort",
                "descending": True,
                "view": "grid",
            },
            self.root.as_uri(),
        )

        self.assertEqual(
            set(migrated),
            {
                "version",
                "location_uri",
                "history",
                "show_hidden",
                "sort_key",
                "sort_descending",
                "view_mode",
            },
        )
        self.assertEqual(migrated["version"], 1)
        self.assertEqual(migrated["location_uri"], self.root.as_uri())
        self.assertEqual(migrated["history"], [self.root.as_uri()])
        self.assertTrue(migrated["show_hidden"])
        self.assertEqual(migrated["sort_key"], "name")
        self.assertTrue(migrated["sort_descending"])
        self.assertEqual(migrated["view_mode"], "grid")

    def test_gio_runtime_failure_is_explicit_when_bindings_are_missing(self):
        try:
            import gi  # noqa: F401
        except ImportError:
            with self.assertRaisesRegex(RuntimeError, "Gio/GVfs"):
                self.api.create_runtime_model()


class GioBackendSourceContractTests(unittest.TestCase):
    def test_runtime_backend_delegates_uri_and_trash_semantics_to_gio(self):
        source = MODEL_PATH.read_text(encoding="utf-8")
        for marker in [
            "Gio.File.new_for_uri",
            "Gio.File.new_for_path",
            ".trash(",
            '"trash:///"',
            "Gio.Cancellable",
        ]:
            self.assertIn(marker, source)

    def test_gio_backend_recurses_for_directory_copy_and_delete(self):
        source = MODEL_PATH.read_text(encoding="utf-8")
        self.assertIn("def _copy_directory", source)
        self.assertIn("def _delete_tree", source)


class GioCopySafetyTests(unittest.TestCase):
    class FakeCancellable:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class FakeInfo:
        def __init__(self, name, file_type):
            self.name = name
            self.file_type = file_type

        def get_name(self):
            return self.name

        def get_file_type(self):
            return self.file_type

    class FakeEnumerator:
        def __init__(self, infos):
            self.infos = list(infos)

        def next_file(self, _cancellable):
            return self.infos.pop(0) if self.infos else None

        def close(self, _cancellable):
            return True

    class FakeFile:
        def __init__(self, owner, name, file_type, exists=True):
            self.owner = owner
            self.name = name
            self.file_type = file_type
            self.exists = exists
            self.children = {}
            self.deleted = False
            self.deleted_with_cancelled = None
            self.copy_hook = None

        def get_basename(self):
            return self.name

        def get_uri(self):
            return "fake:///" + self.name

        def get_child(self, name):
            return self.children[name]

        def query_exists(self, _cancellable):
            return self.exists

        def query_info(self, *_args):
            return GioCopySafetyTests.FakeInfo(self.name, self.file_type)

        def enumerate_children(self, *_args):
            infos = [
                GioCopySafetyTests.FakeInfo(name, child.file_type)
                for name, child in self.children.items()
                if child.exists
            ]
            return GioCopySafetyTests.FakeEnumerator(infos)

        def make_directory(self, _cancellable):
            if self.exists:
                raise FileExistsError(self.name)
            self.exists = True

        def copy(self, target, *_args):
            if target.exists:
                raise FileExistsError(target.name)
            target.exists = True
            if self.copy_hook:
                self.copy_hook()

        def delete(self, cancellable):
            self.deleted = True
            self.exists = False
            self.deleted_with_cancelled = cancellable.cancelled

    @classmethod
    def make_backend(cls):
        api = load_model()

        class Constants:
            DIRECTORY = 1
            REGULAR = 2

        class NoneFlag:
            NONE = 0

        class FakeGio:
            FileType = Constants
            FileQueryInfoFlags = NoneFlag
            FileCopyFlags = NoneFlag
            Cancellable = cls.FakeCancellable

        backend = object.__new__(api.GioFileBackend)
        backend.Gio = FakeGio
        return api, backend, Constants

    def test_copy_failure_never_deletes_a_preexisting_target(self):
        api, backend, types = self.make_backend()
        source = self.FakeFile(backend, "report.txt", types.REGULAR)
        destination = self.FakeFile(backend, "destination", types.DIRECTORY)
        existing = self.FakeFile(backend, "report.txt", types.REGULAR)
        destination.children["report.txt"] = existing
        backend._file = lambda uri: source if uri == "source" else destination

        with self.assertRaises(FileExistsError):
            backend.copy("source", "destination", api.CancellationToken())

        self.assertTrue(existing.exists)
        self.assertFalse(existing.deleted)

    def test_cancelled_directory_copy_uses_fresh_cleanup_and_removes_partial(self):
        api, backend, types = self.make_backend()
        token = api.CancellationToken()
        source = self.FakeFile(backend, "source-dir", types.DIRECTORY)
        source_child = self.FakeFile(backend, "child.txt", types.REGULAR)
        source.children["child.txt"] = source_child
        destination = self.FakeFile(backend, "destination", types.DIRECTORY)
        partial_dir = self.FakeFile(backend, "source-dir", types.DIRECTORY, exists=False)
        partial_child = self.FakeFile(backend, "child.txt", types.REGULAR, exists=False)
        partial_dir.children["child.txt"] = partial_child
        destination.children["source-dir"] = partial_dir
        source_child.copy_hook = lambda: (token.cancel(), (_ for _ in ()).throw(api._OperationCancelled()))
        backend._file = lambda uri: source if uri == "source" else destination

        with self.assertRaises(Exception):
            backend.copy("source", "destination", token)

        self.assertFalse(partial_dir.exists)
        self.assertTrue(partial_dir.deleted)
        self.assertFalse(partial_dir.deleted_with_cancelled)


class MingFilesUiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = UI_PATH.read_text(encoding="utf-8")

    def test_ui_is_valid_python_and_exports_expected_window(self):
        ast.parse(self.source)
        self.assertIn("class MingFiles(", self.source)
        self.assertIn("Adw.ApplicationWindow", self.source)
        self.assertIn("Adw.NavigationSplitView", self.source)

    def test_sidebar_contains_standard_locations_and_gio_mounts(self):
        for marker in [
            '"Home"',
            '"Desktop"',
            '"Documents"',
            '"Downloads"',
            '"Trash"',
            "Gio.VolumeMonitor.get()",
            "volume.mount(",
            "unmount_with_operation(",
            "eject_with_operation(",
        ]:
            self.assertIn(marker, self.source)

    def test_sidebar_also_lists_unmounted_local_partitions_from_the_read_only_helper(self):
        """GVfs alone omits fixed internal volumes which are not mounted yet."""
        for marker in [
            "ming-storage-status",
            "本机分区",
            "def _refresh_local_partitions",
            "def _apply_local_partitions",
            "run_local_partition_snapshot",
            "threading.Thread(target=worker, daemon=True).start()",
        ]:
            self.assertIn(marker, self.source)

    def test_toolbar_and_views_cover_primary_file_workflows(self):
        for marker in [
            "Gtk.SearchEntry",
            "Gtk.ListView",
            "Gtk.GridView",
            "Gtk.Stack",
            "show-hidden",
            "go-previous-symbolic",
            "go-next-symbolic",
            "go-up-symbolic",
            "breadcrumbs",
            "Gio.AppInfo.launch_default_for_uri",
        ]:
            self.assertIn(marker, self.source)
        self.assertIn("Gtk.ToggleButton", self.source)

    def test_background_queries_pass_the_cancellation_token_to_gvfs(self):
        self.assertIn("execute_query(query, token)", self.source)
        self.assertIn("commit_query(result)", self.source)

    def test_navigation_cancels_the_debounced_search_reload(self):
        self.assertIn("def _clear_search_for_navigation", self.source)
        self.assertIn("GLib.source_remove(self.search_timer)", self.source)
        navigate_source = self.source.split("def navigate(self, uri):", 1)[1]
        navigate_source = navigate_source.split("def go_history", 1)[0]
        self.assertIn("self._clear_search_for_navigation()", navigate_source)

    def test_context_touch_and_cancellable_progress_are_present(self):
        for marker in [
            "Gtk.GestureClick",
            "Gtk.GestureLongPress",
            "Gtk.Popover",
            "Gtk.ProgressBar",
            "CancellationToken",
            ".cancel()",
            "perform_operation",
            "restore",
            "empty_trash",
        ]:
            self.assertIn(marker, self.source)

    def test_cli_documents_runtime_check_and_location_argument(self):
        for marker in [
            '"--check-runtime"',
            '"--version"',
            '"location"',
            "Gio/GVfs",
        ]:
            self.assertIn(marker, self.source)

    def test_cli_self_test_uses_gio_backend_file_operations(self):
        for marker in [
            '"--self-test"',
            "run_self_test",
            "backend.copy",
            "backend.rename",
            "backend.delete",
        ]:
            self.assertIn(marker, self.source)

    def test_destructive_actions_require_confirmation(self):
        for marker in [
            "_confirm_empty_trash",
            "_confirm_permanent_delete",
            "Adw.MessageDialog",
            "Adw.ResponseAppearance.DESTRUCTIVE",
        ]:
            self.assertIn(marker, self.source)

    def test_single_instance_forwards_locations_to_open_handler(self):
        for marker in [
            "Gio.ApplicationFlags.HANDLES_OPEN",
            "def do_open",
            "files[0].get_uri()",
            "run_argv",
        ]:
            self.assertIn(marker, self.source)

    def test_core_create_open_with_and_clipboard_actions_are_available(self):
        for marker in [
            "_new_folder_dialog",
            "_open_with",
            "Gtk.AppChooserDialog",
            "_set_clipboard",
            "_paste_clipboard",
            '"剪切"',
            '"复制"',
            '"粘贴"',
            '"永久删除"',
        ]:
            self.assertIn(marker, self.source)

    def test_location_model_exposes_navigation_and_file_operations(self):
        model = load_model()
        location_model = getattr(model, "LocationModel", None)
        methods = [
            "load",
            "navigate",
            "back",
            "forward",
            "up",
            "breadcrumbs",
            "search",
            "set_show_hidden",
            "set_sort",
            "copy",
            "move",
            "rename",
            "trash",
            "restore",
            "empty_trash",
            "create_folder",
            "delete",
        ]
        missing = methods if location_model is None else [
            name for name in methods if not callable(getattr(location_model, name, None))
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
