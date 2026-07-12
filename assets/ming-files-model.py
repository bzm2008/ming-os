#!/usr/bin/env python3
"""File and navigation model for Ming Files.

The production factory requires Gio/GVfs.  ``LocalFileBackend`` exists so the
model can be tested on systems without PyGObject; applications must call
``create_runtime_model`` instead of selecting that backend themselves.
"""

from dataclasses import dataclass, field
from enum import Enum
import mimetypes
import os
from pathlib import Path
import shutil
import threading
from typing import Callable, Mapping
from urllib.parse import unquote, urlparse


STATE_VERSION = 1
TRASH_URI = "trash:///"
INFO_ATTRIBUTES = ",".join(
    [
        "standard::name",
        "standard::display-name",
        "standard::type",
        "standard::is-hidden",
        "standard::size",
        "standard::content-type",
        "standard::icon",
        "time::modified",
        "trash::orig-path",
    ]
)


class SortKey(str, Enum):
    NAME = "name"
    SIZE = "size"
    MODIFIED = "modified"
    TYPE = "type"


@dataclass(frozen=True)
class FileItem:
    uri: str
    name: str
    display_name: str
    is_directory: bool
    is_hidden: bool = False
    size: int = 0
    modified: float = 0.0
    content_type: str = "application/octet-stream"
    icon_name: str = "text-x-generic-symbolic"
    original_uri: str = ""


@dataclass(frozen=True)
class Breadcrumb:
    label: str
    uri: str


@dataclass(frozen=True)
class OperationError(Exception):
    code: str
    message: str
    source_uri: str = ""
    target_uri: str = ""
    recoverable: bool = True

    def __str__(self):
        return self.message


@dataclass(frozen=True)
class OperationResult:
    operation: str
    success: bool
    source_uri: str = ""
    target_uri: str = ""
    error: OperationError | None = None


@dataclass(frozen=True)
class LocationQuery:
    uri: str
    back: tuple[str, ...]
    forward: tuple[str, ...]
    search_text: str
    show_hidden: bool
    sort_key: SortKey
    sort_descending: bool


@dataclass(frozen=True)
class LocationQueryResult:
    query: LocationQuery
    items: tuple[FileItem, ...]


class _OperationCancelled(Exception):
    pass


@dataclass
class CancellationToken:
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _callbacks: list[Callable[[], None]] = field(default_factory=list, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def cancel(self):
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            callback()

    @property
    def cancelled(self):
        return self._event.is_set()

    def connect(self, callback):
        with self._lock:
            if self._event.is_set():
                call_now = True
            else:
                self._callbacks.append(callback)
                call_now = False
        if call_now:
            callback()

    def raise_if_cancelled(self):
        if self.cancelled:
            raise _OperationCancelled("operation cancelled")


class LocalFileBackend:
    """Portable local-filesystem adapter used only by model tests."""

    @staticmethod
    def _path(uri):
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError("LocalFileBackend accepts only file:// URIs")
        path = unquote(parsed.path)
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return Path(path)

    @staticmethod
    def _item(path):
        stat = path.stat()
        content_type = "inode/directory" if path.is_dir() else (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        return FileItem(
            uri=path.as_uri(),
            name=path.name,
            display_name=path.name,
            is_directory=path.is_dir(),
            is_hidden=path.name.startswith("."),
            size=0 if path.is_dir() else stat.st_size,
            modified=stat.st_mtime,
            content_type=content_type,
            icon_name="folder-symbolic" if path.is_dir() else "text-x-generic-symbolic",
        )

    def enumerate(self, uri, token=None):
        token = token or CancellationToken()
        directory = self._path(uri)
        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        result = []
        for path in directory.iterdir():
            token.raise_if_cancelled()
            result.append(self._item(path))
        return result

    def search(self, uri, query, show_hidden=False, token=None):
        token = token or CancellationToken()
        root = self._path(uri)
        if not root.exists():
            raise FileNotFoundError(root)
        needle = query.casefold()
        result = []
        for current, directories, files in os.walk(root):
            token.raise_if_cancelled()
            if not show_hidden:
                directories[:] = [name for name in directories if not name.startswith(".")]
            for name in directories + files:
                token.raise_if_cancelled()
                if not show_hidden and name.startswith("."):
                    continue
                if needle in name.casefold():
                    result.append(self._item(Path(current) / name))
        return result

    def parent(self, uri):
        path = self._path(uri)
        parent = path.parent
        return None if parent == path else parent.as_uri()

    def breadcrumbs(self, uri):
        path = self._path(uri).resolve()
        parts = path.parts
        if not parts:
            return []
        current = Path(parts[0])
        label = parts[0] if parts[0] != os.sep else os.sep
        result = [Breadcrumb(label, current.as_uri())]
        for part in parts[1:]:
            current = current / part
            result.append(Breadcrumb(part, current.as_uri()))
        return result

    def copy(self, source_uri, destination_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._path(source_uri)
        destination_dir = self._path(destination_uri)
        if not source.exists():
            raise FileNotFoundError(source)
        if not destination_dir.is_dir():
            raise NotADirectoryError(destination_dir)
        target = destination_dir / source.name
        if target.exists():
            raise FileExistsError(target)
        try:
            if source.is_dir():
                shutil.copytree(
                    source,
                    target,
                    copy_function=lambda src, dst: self._copy_file(
                        Path(src), Path(dst), token, progress
                    ),
                )
            else:
                self._copy_file(source, target, token, progress)
        except Exception:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            raise
        return target.as_uri()

    @staticmethod
    def _copy_file(source, target, token, progress):
        total = source.stat().st_size
        copied = 0
        if progress:
            progress(0, total)
        with source.open("rb") as reader, target.open("xb") as writer:
            while True:
                token.raise_if_cancelled()
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
                if progress:
                    progress(copied, total)
        shutil.copystat(source, target)
        return str(target)

    def move(self, source_uri, destination_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._path(source_uri)
        destination_dir = self._path(destination_uri)
        if not source.exists():
            raise FileNotFoundError(source)
        if not destination_dir.is_dir():
            raise NotADirectoryError(destination_dir)
        target = destination_dir / source.name
        if target.exists():
            raise FileExistsError(target)
        shutil.move(str(source), str(target))
        if progress:
            progress(1, 1)
        return target.as_uri()

    def rename(self, source_uri, new_name, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        if not new_name or new_name in {".", ".."} or Path(new_name).name != new_name:
            raise ValueError("new name must be one filename")
        source = self._path(source_uri)
        if not source.exists():
            raise FileNotFoundError(source)
        target = source.with_name(new_name)
        if target.exists():
            raise FileExistsError(target)
        source.rename(target)
        return target.as_uri()

    def create_folder(self, parent_uri, name, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("folder name must be one filename")
        parent = self._path(parent_uri)
        if not parent.is_dir():
            raise NotADirectoryError(parent)
        target = parent / name
        target.mkdir()
        return target.as_uri()

    def delete(self, source_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._path(source_uri)
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()
        if progress:
            progress(1, 1)
        return source_uri

    def trash(self, source_uri, token=None):
        raise NotImplementedError("Trash requires Gio/GVfs")

    def restore(self, source_uri, target_uri=None, token=None):
        raise NotImplementedError("Trash restore requires Gio/GVfs")

    def empty_trash(self, token=None, progress=None):
        raise NotImplementedError("Empty trash requires Gio/GVfs")


class GioFileBackend:
    """Production URI backend. All filesystem semantics are delegated to Gio."""

    def __init__(self):
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as error:
            raise RuntimeError(
                "Gio/GVfs runtime is unavailable; install python3-gi and GVfs"
            ) from error
        self.Gio = Gio
        self.GLib = GLib

    def _file(self, uri):
        return self.Gio.File.new_for_uri(uri)

    def home_uri(self):
        return self.Gio.File.new_for_path(os.path.expanduser("~")).get_uri()

    def _cancellable(self, token):
        cancellable = self.Gio.Cancellable()
        if token:
            token.connect(cancellable.cancel)
        return cancellable

    def _item(self, file, info):
        Gio = self.Gio
        icon = info.get_icon()
        icon_name = icon.to_string() if icon else "text-x-generic-symbolic"
        original_path = info.get_attribute_byte_string("trash::orig-path") or ""
        original_uri = (
            Gio.File.new_for_path(original_path).get_uri() if original_path else ""
        )
        return FileItem(
            uri=file.get_uri(),
            name=info.get_name(),
            display_name=info.get_display_name() or info.get_name(),
            is_directory=info.get_file_type() == Gio.FileType.DIRECTORY,
            is_hidden=info.get_is_hidden(),
            size=info.get_size(),
            modified=float(info.get_attribute_uint64("time::modified")),
            content_type=info.get_content_type() or "application/octet-stream",
            icon_name=icon_name,
            original_uri=original_uri,
        )

    def enumerate(self, uri, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        cancellable = self._cancellable(token)
        root = self._file(uri)
        enumerator = root.enumerate_children(
            INFO_ATTRIBUTES, self.Gio.FileQueryInfoFlags.NONE, cancellable
        )
        result = []
        try:
            while True:
                token.raise_if_cancelled()
                info = enumerator.next_file(cancellable)
                if info is None:
                    break
                result.append(self._item(root.get_child(info.get_name()), info))
        finally:
            enumerator.close(cancellable)
        return result

    def search(self, uri, query, show_hidden=False, token=None):
        token = token or CancellationToken()
        needle = query.casefold()
        pending = [uri]
        result = []
        while pending:
            token.raise_if_cancelled()
            for item in self.enumerate(pending.pop(), token):
                if not show_hidden and item.is_hidden:
                    continue
                if needle in item.display_name.casefold():
                    result.append(item)
                if item.is_directory:
                    pending.append(item.uri)
        return result

    def parent(self, uri):
        parent = self._file(uri).get_parent()
        return parent.get_uri() if parent else None

    def breadcrumbs(self, uri):
        result = []
        node = self._file(uri)
        while node:
            label = node.get_basename() or node.get_parse_name()
            result.append(Breadcrumb(label, node.get_uri()))
            node = node.get_parent()
        result.reverse()
        return result

    def _progress(self, callback):
        if callback is None:
            return None
        return lambda current, total, *_args: callback(current, total)

    def copy(self, source_uri, destination_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._file(source_uri)
        target = self._file(destination_uri).get_child(source.get_basename())
        if target.query_exists(self._cancellable(token)):
            raise FileExistsError(target.get_uri())
        info = source.query_info(
            "standard::type",
            self.Gio.FileQueryInfoFlags.NONE,
            self._cancellable(token),
        )
        if info.get_file_type() == self.Gio.FileType.DIRECTORY:
            self._copy_directory(source, target, token, progress)
        else:
            try:
                source.copy(
                    target,
                    self.Gio.FileCopyFlags.NONE,
                    self._cancellable(token),
                    self._progress(progress),
                    None,
                )
            except Exception as error:
                if not self._is_exists_error(error):
                    self._cleanup_partial(target)
                raise
        return target.get_uri()

    def _is_exists_error(self, error):
        if isinstance(error, FileExistsError):
            return True
        glib = getattr(self, "GLib", None)
        io_error = getattr(self.Gio, "IOErrorEnum", None)
        matcher = getattr(error, "matches", None)
        if glib is None or io_error is None or matcher is None:
            return False
        return matcher(self.Gio.io_error_quark(), io_error.EXISTS)

    def _cleanup_partial(self, target):
        cleanup_cancellable = self.Gio.Cancellable()
        try:
            if target.query_exists(cleanup_cancellable):
                self._delete_tree(target, cancellable=cleanup_cancellable)
        except Exception:
            pass

    def _copy_directory(self, source, target, token, progress=None):
        token.raise_if_cancelled()
        target.make_directory(self._cancellable(token))
        try:
            enumerator = source.enumerate_children(
                "standard::name,standard::type",
                self.Gio.FileQueryInfoFlags.NONE,
                self._cancellable(token),
            )
            try:
                while True:
                    token.raise_if_cancelled()
                    info = enumerator.next_file(self._cancellable(token))
                    if info is None:
                        break
                    source_child = source.get_child(info.get_name())
                    target_child = target.get_child(info.get_name())
                    if info.get_file_type() == self.Gio.FileType.DIRECTORY:
                        self._copy_directory(source_child, target_child, token, progress)
                    else:
                        source_child.copy(
                            target_child,
                            self.Gio.FileCopyFlags.NONE,
                            self._cancellable(token),
                            self._progress(progress),
                            None,
                        )
            finally:
                enumerator.close(self._cancellable(token))
        except Exception:
            self._cleanup_partial(target)
            raise

    def _delete_tree(self, file, token=None, cancellable=None):
        if token:
            token.raise_if_cancelled()
        active = cancellable or self._cancellable(token or CancellationToken())
        info = file.query_info(
            "standard::type",
            self.Gio.FileQueryInfoFlags.NONE,
            active,
        )
        if info.get_file_type() == self.Gio.FileType.DIRECTORY:
            enumerator = file.enumerate_children(
                "standard::name",
                self.Gio.FileQueryInfoFlags.NONE,
                active,
            )
            try:
                while True:
                    if token:
                        token.raise_if_cancelled()
                    child_info = enumerator.next_file(active)
                    if child_info is None:
                        break
                    self._delete_tree(
                        file.get_child(child_info.get_name()), token, active
                    )
            finally:
                enumerator.close(active)
        file.delete(active)

    def move(self, source_uri, destination_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._file(source_uri)
        target = self._file(destination_uri).get_child(source.get_basename())
        source.move(
            target,
            self.Gio.FileCopyFlags.NONE,
            self._cancellable(token),
            self._progress(progress),
            None,
        )
        return target.get_uri()

    def rename(self, source_uri, new_name, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        if not new_name or new_name in {".", ".."} or "/" in new_name:
            raise ValueError("new name must be one filename")
        source = self._file(source_uri)
        parent = source.get_parent()
        if parent is None:
            raise ValueError("root locations cannot be renamed")
        target = parent.get_child(new_name)
        source.move(
            target,
            self.Gio.FileCopyFlags.NONE,
            self._cancellable(token),
            None,
            None,
        )
        return target.get_uri()

    def create_folder(self, parent_uri, name, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError("folder name must be one filename")
        target = self._file(parent_uri).get_child(name)
        target.make_directory(self._cancellable(token))
        return target.get_uri()

    def delete(self, source_uri, token=None, progress=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        self._delete_tree(self._file(source_uri), token)
        if progress:
            progress(1, 1)
        return source_uri

    def trash(self, source_uri, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._file(source_uri)
        source.trash(self._cancellable(token))
        return TRASH_URI

    def restore(self, source_uri, target_uri=None, token=None):
        token = token or CancellationToken()
        token.raise_if_cancelled()
        source = self._file(source_uri)
        if target_uri:
            target = self._file(target_uri)
        else:
            info = source.query_info(
                "trash::orig-path",
                self.Gio.FileQueryInfoFlags.NONE,
                self._cancellable(token),
            )
            original_path = info.get_attribute_byte_string("trash::orig-path")
            if not original_path:
                raise ValueError("trash item has no original location")
            target = self.Gio.File.new_for_path(original_path)
        source.move(
            target,
            self.Gio.FileCopyFlags.NONE,
            self._cancellable(token),
            None,
            None,
        )
        return target.get_uri()

    def empty_trash(self, token=None, progress=None):
        token = token or CancellationToken()
        items = self.enumerate(TRASH_URI, token)
        total = len(items)
        for index, item in enumerate(items, start=1):
            token.raise_if_cancelled()
            self._delete_tree(self._file(item.uri), token)
            if progress:
                progress(index, total)
        return TRASH_URI


class LocationModel:
    def __init__(self, backend=None, home_uri=""):
        self.backend = backend or LocalFileBackend()
        self.home_uri = home_uri
        self.current_uri = home_uri
        self.items = []
        self.show_hidden = False
        self.sort_key = SortKey.NAME
        self.sort_descending = False
        self._back = []
        self._forward = []
        self._state_lock = threading.RLock()

    def _sorted(self, items):
        return self._sort_items(items, self.sort_key, self.sort_descending)

    @staticmethod
    def _sort_items(items, sort_key, sort_descending):
        key_functions = {
            SortKey.NAME: lambda item: item.display_name.casefold(),
            SortKey.SIZE: lambda item: item.size,
            SortKey.MODIFIED: lambda item: item.modified,
            SortKey.TYPE: lambda item: (item.content_type.casefold(), item.display_name.casefold()),
        }
        key = key_functions[sort_key]
        directories = sorted(
            (item for item in items if item.is_directory),
            key=key,
            reverse=sort_descending,
        )
        files = sorted(
            (item for item in items if not item.is_directory),
            key=key,
            reverse=sort_descending,
        )
        return directories + files

    def _prepare_query(self, uri, back, forward, search_text=""):
        return LocationQuery(
            uri=uri or self.home_uri,
            back=tuple(back),
            forward=tuple(forward),
            search_text=search_text,
            show_hidden=self.show_hidden,
            sort_key=self.sort_key,
            sort_descending=self.sort_descending,
        )

    def prepare_load(self, uri=None):
        with self._state_lock:
            return self._prepare_query(
                uri if uri is not None else self.current_uri,
                self._back,
                self._forward,
            )

    def prepare_navigate(self, uri, record=True):
        with self._state_lock:
            current_uri = self.current_uri or self.home_uri
            back = list(self._back)
            forward = list(self._forward)
            if record and current_uri and uri != current_uri:
                back.append(current_uri)
                forward.clear()
            return self._prepare_query(uri, back, forward)

    def prepare_back(self):
        with self._state_lock:
            if not self._back:
                return self._prepare_query(
                    self.current_uri, self._back, self._forward
                )
            back = list(self._back)
            forward = list(self._forward)
            target = back.pop()
            if self.current_uri:
                forward.append(self.current_uri)
            return self._prepare_query(target, back, forward)

    def prepare_forward(self):
        with self._state_lock:
            if not self._forward:
                return self._prepare_query(
                    self.current_uri, self._back, self._forward
                )
            back = list(self._back)
            forward = list(self._forward)
            target = forward.pop()
            if self.current_uri:
                back.append(self.current_uri)
            return self._prepare_query(target, back, forward)

    def prepare_up(self):
        with self._state_lock:
            parent = self.backend.parent(self.current_uri)
            return (
                self.prepare_navigate(parent)
                if parent
                else self._prepare_query(
                    self.current_uri, self._back, self._forward
                )
            )

    def prepare_search(self, query):
        search_text = query.strip()
        if not search_text:
            return self.prepare_load()
        with self._state_lock:
            return self._prepare_query(
                self.current_uri, self._back, self._forward, search_text
            )

    def execute_query(self, query, token=None):
        token = token or CancellationToken()
        if query.search_text:
            items = self.backend.search(
                query.uri, query.search_text, query.show_hidden, token
            )
        else:
            items = self.backend.enumerate(query.uri, token)
            if not query.show_hidden:
                items = [item for item in items if not item.is_hidden]
        items = self._sort_items(items, query.sort_key, query.sort_descending)
        return LocationQueryResult(query, tuple(items))

    def commit_query(self, result):
        with self._state_lock:
            self.current_uri = result.query.uri
            self._back = list(result.query.back)
            self._forward = list(result.query.forward)
            self.items = list(result.items)
            return self.items

    def load(self, uri=None, token=None):
        return self.commit_query(
            self.execute_query(self.prepare_load(uri), token)
        )

    def navigate(self, uri, record=True, token=None):
        self.commit_query(
            self.execute_query(self.prepare_navigate(uri, record), token)
        )
        return self.current_uri

    def back(self, token=None):
        self.commit_query(self.execute_query(self.prepare_back(), token))
        return self.current_uri

    def forward(self, token=None):
        self.commit_query(self.execute_query(self.prepare_forward(), token))
        return self.current_uri

    def up(self, token=None):
        self.commit_query(self.execute_query(self.prepare_up(), token))
        return self.current_uri

    def breadcrumbs(self):
        return self.backend.breadcrumbs(self.current_uri)

    def search(self, query, token=None):
        return self.commit_query(
            self.execute_query(self.prepare_search(query), token)
        )

    def set_show_hidden(self, show):
        self.show_hidden = bool(show)
        if self.current_uri:
            self.load()
        return self.show_hidden

    def set_sort(self, key, descending=False):
        self.sort_key = key if isinstance(key, SortKey) else SortKey(key)
        self.sort_descending = bool(descending)
        self.items = self._sorted(self.items)
        return self.items

    @staticmethod
    def _error_code(error):
        if isinstance(error, _OperationCancelled):
            return "cancelled"
        if isinstance(error, FileNotFoundError):
            return "not-found"
        if isinstance(error, FileExistsError):
            return "already-exists"
        if isinstance(error, PermissionError):
            return "permission-denied"
        if isinstance(error, NotImplementedError):
            return "unsupported"
        if isinstance(error, (ValueError, NotADirectoryError)):
            return "invalid-argument"
        message = str(error).casefold()
        if "cancelled" in message or "canceled" in message:
            return "cancelled"
        return "io-error"

    def _operation(self, operation, source_uri, target_uri, function, token=None):
        token = token or CancellationToken()
        try:
            token.raise_if_cancelled()
            result_uri = function(token)
            return OperationResult(
                operation, True, source_uri, result_uri or target_uri
            )
        except Exception as error:
            structured = OperationError(
                self._error_code(error),
                str(error) or error.__class__.__name__,
                source_uri,
                target_uri,
                not isinstance(error, PermissionError),
            )
            return OperationResult(
                operation, False, source_uri, target_uri, structured
            )

    def copy(self, source_uri, destination_uri, token=None, progress=None):
        return self._operation(
            "copy",
            source_uri,
            destination_uri,
            lambda active: self.backend.copy(
                source_uri, destination_uri, active, progress
            ),
            token,
        )

    def move(self, source_uri, destination_uri, token=None, progress=None):
        return self._operation(
            "move",
            source_uri,
            destination_uri,
            lambda active: self.backend.move(
                source_uri, destination_uri, active, progress
            ),
            token,
        )

    def rename(self, source_uri, new_name, token=None):
        return self._operation(
            "rename",
            source_uri,
            new_name,
            lambda active: self.backend.rename(source_uri, new_name, active),
            token,
        )

    def create_folder(self, parent_uri, name, token=None):
        return self._operation(
            "create-folder",
            parent_uri,
            name,
            lambda active: self.backend.create_folder(parent_uri, name, active),
            token,
        )

    def delete(self, source_uri, token=None, progress=None):
        return self._operation(
            "delete",
            source_uri,
            "",
            lambda active: self.backend.delete(source_uri, active, progress),
            token,
        )

    def trash(self, source_uri, token=None):
        return self._operation(
            "trash",
            source_uri,
            TRASH_URI,
            lambda active: self.backend.trash(source_uri, active),
            token,
        )

    def restore(self, source_uri, target_uri=None, token=None):
        return self._operation(
            "restore",
            source_uri,
            target_uri or "",
            lambda active: self.backend.restore(source_uri, target_uri, active),
            token,
        )

    def empty_trash(self, token=None, progress=None):
        return self._operation(
            "empty-trash",
            TRASH_URI,
            TRASH_URI,
            lambda active: self.backend.empty_trash(active, progress),
            token,
        )


def create_runtime_model(home_uri=None):
    backend = GioFileBackend()
    return LocationModel(backend, home_uri or backend.home_uri())


def _valid_uri(value):
    return isinstance(value, str) and bool(value) and "://" in value


def migrate_location_state(raw: Mapping[str, object] | None, home_uri: str):
    """Migrate persisted UI state only; this function never moves user files."""
    raw = raw if isinstance(raw, Mapping) else {}
    location = raw.get("location_uri", raw.get("location", home_uri))
    if not _valid_uri(location):
        location = home_uri
    history = raw.get("history", [])
    if not isinstance(history, (list, tuple)):
        history = []
    history = [value for value in history if _valid_uri(value)]
    sort_key = raw.get("sort_key", raw.get("sort", SortKey.NAME.value))
    if sort_key not in {item.value for item in SortKey}:
        sort_key = SortKey.NAME.value
    view_mode = raw.get("view_mode", raw.get("view", "list"))
    if view_mode not in {"list", "grid"}:
        view_mode = "list"
    return {
        "version": STATE_VERSION,
        "location_uri": location,
        "history": history,
        "show_hidden": bool(raw.get("show_hidden", False)),
        "sort_key": sort_key,
        "sort_descending": bool(raw.get("sort_descending", raw.get("descending", False))),
        "view_mode": view_mode,
    }
