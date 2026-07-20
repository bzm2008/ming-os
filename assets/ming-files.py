#!/usr/bin/env python3
"""Ming Files, a GTK4/libadwaita file manager backed by Gio/GVfs."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading


VERSION = "1.0"
STORAGE_STATUS_HELPER = "/usr/local/bin/ming-storage-status"


def run_local_partition_snapshot():
    """Read the authoritative local partition view without blocking GTK."""
    try:
        completed = subprocess.run(
            [STORAGE_STATUS_HELPER, "partitions", "--json"],
            capture_output=True,
            check=False,
            text=True,
            timeout=4,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "partitions": [], "error": "本机分区检测超时。"}
    except OSError as error:
        return {"ok": False, "partitions": [], "error": str(error)}
    if completed.returncode != 0:
        return {
            "ok": False,
            "partitions": [],
            "error": completed.stderr.strip() or completed.stdout.strip()
            or "本机分区检测工具没有返回结果。",
        }
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return {"ok": False, "partitions": [], "error": "本机分区检测返回了无效数据。"}
    partitions = result.get("partitions") if isinstance(result, dict) else None
    if not isinstance(result, dict) or result.get("ok") is not True or not isinstance(partitions, list):
        return {
            "ok": False,
            "partitions": [],
            "error": (result.get("error") if isinstance(result, dict) else "")
            or "本机分区检测没有返回分区列表。",
        }
    return {"ok": True, "partitions": partitions, "error": ""}


def _load_model_module():
    candidates = [
        Path(__file__).with_name("ming-files-model.py"),
        Path("/usr/local/lib/ming-os/ming-files-model.py"),
    ]
    model_path = next((path for path in candidates if path.is_file()), None)
    if model_path is None:
        raise RuntimeError("Ming Files model is not installed")
    spec = importlib.util.spec_from_file_location("ming_files_model", model_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODEL = _load_model_module()
CancellationToken = MODEL.CancellationToken


def _load_shell_common():
    candidates = [
        Path(__file__).with_name("ming-shell-common.py"),
        Path("/usr/local/lib/ming-os/ming-shell-common.py"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("ming_shell_common_for_files", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Ming shell common runtime is missing")


COMMON = _load_shell_common()


def resolve_icon(icon):
    return COMMON.resolve_icon(icon)


def set_resolved_icon(image, icon, pixel_size=None):
    """Set a validated absolute image or a safe GTK theme name."""
    resolved = resolve_icon(icon)
    if Path(resolved).is_absolute():
        pixbuf = COMMON.load_icon_pixbuf(None, resolved, pixel_size or 48)
        if pixbuf is not None:
            image.set_from_pixbuf(pixbuf)
        else:
            image.set_from_icon_name("application-x-executable")
    else:
        image.set_from_icon_name(resolved)
    if pixel_size is not None:
        image.set_pixel_size(int(pixel_size))
    return resolved

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gio", "2.0")
    from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

    GTK_AVAILABLE = True
    GTK_IMPORT_ERROR = None
except (ImportError, ValueError) as error:
    GTK_AVAILABLE = False
    GTK_IMPORT_ERROR = error


BUILTIN_LOCATIONS = (
    ("Home", "user-home-symbolic"),
    ("Desktop", "user-desktop-symbolic"),
    ("Documents", "folder-documents-symbolic"),
    ("Downloads", "folder-download-symbolic"),
    ("Trash", "user-trash-symbolic"),
)


if GTK_AVAILABLE:
    class FileObject(GObject.Object):
        def __init__(self, item):
            super().__init__()
            self.item = item


    class MingFiles(Adw.ApplicationWindow):
        def __init__(self, application, initial_uri=None):
            super().__init__(application=application, title="Ming 文件")
            self.set_default_size(1020, 680)
            self.set_size_request(620, 440)
            self.model = MODEL.create_runtime_model()
            self.query_token = None
            self.query_generation = 0
            self.operation_token = None
            self.clipboard_uri = None
            self.clipboard_cut = False
            self.search_timer = 0
            self.current_popover = None
            self.volume_monitor = Gio.VolumeMonitor.get()
            self.volume_signal_ids = []
            self.local_partition_generation = 0
            self.local_partition_rows = []
            self._build_ui()
            self._install_css()
            self._watch_volumes()
            self.refresh_sidebar()
            self.reload(initial_uri)

        def _build_ui(self):
            self.toast_overlay = Adw.ToastOverlay()
            self.split = Adw.NavigationSplitView()
            self.toast_overlay.set_child(self.split)
            self.set_content(self.toast_overlay)

            sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            sidebar_box.add_css_class("ming-sidebar")
            sidebar_header = Adw.HeaderBar()
            sidebar_header.set_title_widget(Adw.WindowTitle(title="Ming 文件"))
            sidebar_box.append(sidebar_header)
            self.sidebar = Gtk.ListBox()
            self.sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.sidebar.add_css_class("navigation-sidebar")
            self.sidebar.connect("row-activated", self._on_sidebar_activated)
            sidebar_scroll = Gtk.ScrolledWindow()
            sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sidebar_scroll.set_child(self.sidebar)
            sidebar_scroll.set_vexpand(True)
            sidebar_box.append(sidebar_scroll)
            self.split.set_sidebar(
                Adw.NavigationPage(title="位置", child=sidebar_box)
            )

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            content.add_css_class("ming-content")
            header = Adw.HeaderBar()
            content.append(header)

            self.back_button = self._icon_button("go-previous-symbolic", "后退")
            self.back_button.connect("clicked", lambda _button: self.go_history("back"))
            header.pack_start(self.back_button)
            self.forward_button = self._icon_button("go-next-symbolic", "前进")
            self.forward_button.connect("clicked", lambda _button: self.go_history("forward"))
            header.pack_start(self.forward_button)
            self.up_button = self._icon_button("go-up-symbolic", "上级目录")
            self.up_button.connect("clicked", lambda _button: self.go_history("up"))
            header.pack_start(self.up_button)

            self.search = Gtk.SearchEntry(placeholder_text="搜索当前文件夹")
            self.search.set_hexpand(True)
            self.search.set_max_width_chars(34)
            self.search.connect("search-changed", self._on_search_changed)
            header.set_title_widget(self.search)

            self.list_toggle = Gtk.ToggleButton(icon_name="view-list-symbolic")
            self.list_toggle.set_tooltip_text("列表视图")
            self.list_toggle.add_css_class("flat")
            self.list_toggle.set_active(True)
            self.list_toggle.connect(
                "toggled",
                lambda button: self.set_view("list") if button.get_active() else None,
            )
            header.pack_end(self.list_toggle)
            self.grid_toggle = Gtk.ToggleButton(icon_name="view-grid-symbolic")
            self.grid_toggle.set_tooltip_text("网格视图")
            self.grid_toggle.add_css_class("flat")
            self.grid_toggle.set_group(self.list_toggle)
            self.grid_toggle.connect(
                "toggled",
                lambda button: self.set_view("grid") if button.get_active() else None,
            )
            header.pack_end(self.grid_toggle)
            header.pack_end(self._build_menu())

            crumb_scroll = Gtk.ScrolledWindow()
            crumb_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            crumb_scroll.add_css_class("ming-breadcrumb-scroll")
            self.crumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            self.crumb_box.set_margin_start(10)
            self.crumb_box.set_margin_end(10)
            self.crumb_box.set_margin_top(6)
            self.crumb_box.set_margin_bottom(6)
            crumb_scroll.set_child(self.crumb_box)
            content.append(crumb_scroll)

            self.store = Gio.ListStore(item_type=FileObject)
            self.selection = Gtk.SingleSelection(model=self.store)
            self.selection.set_autoselect(False)
            self.selection.set_can_unselect(True)
            self.view_stack = Gtk.Stack()
            self.view_stack.set_vexpand(True)
            self.list_view = Gtk.ListView(
                model=self.selection, factory=self._make_list_factory()
            )
            self.list_view.set_single_click_activate(False)
            self.list_view.connect("activate", self._on_item_activated)
            list_scroll = Gtk.ScrolledWindow(child=self.list_view)
            self.view_stack.add_named(list_scroll, "list")
            self.grid_view = Gtk.GridView(
                model=self.selection, factory=self._make_grid_factory()
            )
            self.grid_view.set_max_columns(10)
            self.grid_view.set_min_columns(2)
            self.grid_view.set_single_click_activate(False)
            self.grid_view.connect("activate", self._on_item_activated)
            grid_scroll = Gtk.ScrolledWindow(child=self.grid_view)
            self.view_stack.add_named(grid_scroll, "grid")
            self.view_stack.set_visible_child_name("list")
            content.append(self.view_stack)

            self.empty_state = Adw.StatusPage(
                icon_name="folder-symbolic",
                title="这个文件夹是空的",
                description="文件会显示在这里",
            )
            self.empty_state.set_visible(False)
            content.append(self.empty_state)

            self.progress_revealer = Gtk.Revealer()
            progress_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=10
            )
            progress_box.add_css_class("ming-progress")
            self.progress_label = Gtk.Label(label="正在处理文件", xalign=0)
            self.progress_label.set_hexpand(True)
            progress_box.append(self.progress_label)
            self.progress = Gtk.ProgressBar()
            self.progress.set_size_request(220, -1)
            progress_box.append(self.progress)
            cancel = self._icon_button("process-stop-symbolic", "取消操作")
            cancel.connect("clicked", self._cancel_operation)
            progress_box.append(cancel)
            self.progress_revealer.set_child(progress_box)
            content.append(self.progress_revealer)

            self.split.set_content(
                Adw.NavigationPage(title="文件", child=content)
            )

        @staticmethod
        def _icon_button(icon_name, tooltip):
            button = Gtk.Button(icon_name=icon_name)
            button.set_tooltip_text(tooltip)
            button.add_css_class("flat")
            return button

        def _build_menu(self):
            menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
            menu_button.set_tooltip_text("文件显示选项")
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.set_margin_start(12)
            box.set_margin_end(12)
            self.hidden_check = Gtk.CheckButton(label="显示隐藏文件")
            self.hidden_check.set_name("show-hidden")
            self.hidden_check.connect("toggled", self._on_hidden_toggled)
            box.append(self.hidden_check)
            box.append(Gtk.Separator())
            sort_label = Gtk.Label(label="排序", xalign=0)
            sort_label.add_css_class("dim-label")
            box.append(sort_label)
            self.sort_dropdown = Gtk.DropDown.new_from_strings(
                ["名称", "大小", "修改时间", "类型"]
            )
            self.sort_dropdown.connect("notify::selected", self._on_sort_changed)
            box.append(self.sort_dropdown)
            self.descending_check = Gtk.CheckButton(label="倒序")
            self.descending_check.connect("toggled", self._on_sort_changed)
            box.append(self.descending_check)
            box.append(Gtk.Separator())
            new_folder_button = Gtk.Button(label="新建文件夹")
            new_folder_button.connect(
                "clicked", lambda _button: self._new_folder_dialog()
            )
            box.append(new_folder_button)
            self.paste_button = Gtk.Button(label="粘贴")
            self.paste_button.set_sensitive(False)
            self.paste_button.connect(
                "clicked", lambda _button: self._paste_clipboard()
            )
            box.append(self.paste_button)
            empty_button = Gtk.Button(label="清空回收站")
            empty_button.connect(
                "clicked", lambda _button: self._confirm_empty_trash()
            )
            box.append(empty_button)
            popover.set_child(box)
            menu_button.set_popover(popover)
            return menu_button

        def _make_list_factory(self):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._setup_list_cell)
            factory.connect("bind", self._bind_list_cell)
            return factory

        def _setup_list_cell(self, _factory, list_item):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_start(14)
            row.set_margin_end(14)
            row.set_margin_top(7)
            row.set_margin_bottom(7)
            row.icon = Gtk.Image(pixel_size=28)
            row.append(row.icon)
            row.name_label = Gtk.Label(xalign=0, ellipsize=3)
            row.name_label.set_hexpand(True)
            row.append(row.name_label)
            row.type_label = Gtk.Label(xalign=0)
            row.type_label.add_css_class("dim-label")
            row.type_label.set_size_request(150, -1)
            row.append(row.type_label)
            row.size_label = Gtk.Label(xalign=1)
            row.size_label.add_css_class("dim-label")
            row.size_label.set_size_request(90, -1)
            row.append(row.size_label)
            self._attach_context_gestures(row)
            list_item.set_child(row)

        def _bind_list_cell(self, _factory, list_item):
            row = list_item.get_child()
            file_object = list_item.get_item()
            row.file_object = file_object
            item = file_object.item
            set_resolved_icon(row.icon, item.icon_name, 28)
            row.name_label.set_label(item.display_name)
            row.type_label.set_label("文件夹" if item.is_directory else item.content_type)
            row.size_label.set_label("" if item.is_directory else self._format_size(item.size))

        def _make_grid_factory(self):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._setup_grid_cell)
            factory.connect("bind", self._bind_grid_cell)
            return factory

        def _setup_grid_cell(self, _factory, list_item):
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tile.add_css_class("ming-file-tile")
            tile.set_size_request(112, 112)
            tile.icon = Gtk.Image(pixel_size=48)
            tile.append(tile.icon)
            tile.name_label = Gtk.Label(
                xalign=0.5, justify=Gtk.Justification.CENTER, wrap=True, lines=2
            )
            tile.name_label.set_ellipsize(3)
            tile.append(tile.name_label)
            self._attach_context_gestures(tile)
            list_item.set_child(tile)

        def _bind_grid_cell(self, _factory, list_item):
            tile = list_item.get_child()
            file_object = list_item.get_item()
            tile.file_object = file_object
            set_resolved_icon(tile.icon, file_object.item.icon_name, 48)
            tile.name_label.set_label(file_object.item.display_name)

        def _attach_context_gestures(self, widget):
            click = Gtk.GestureClick(button=3)
            click.connect(
                "pressed",
                lambda _gesture, _count, x, y: self._show_item_menu(widget, x, y),
            )
            widget.add_controller(click)
            long_press = Gtk.GestureLongPress()
            long_press.connect(
                "pressed", lambda _gesture, x, y: self._show_item_menu(widget, x, y)
            )
            widget.add_controller(long_press)

        def refresh_sidebar(self):
            self.local_partition_generation += 1
            while True:
                row = self.sidebar.get_row_at_index(0)
                if row is None:
                    break
                self.sidebar.remove(row)
            home = Path.home()
            special = {
                "Home": home,
                "Desktop": Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP) or home / "Desktop"),
                "Documents": Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or home / "Documents"),
                "Downloads": Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or home / "Downloads"),
            }
            for name, icon in BUILTIN_LOCATIONS:
                uri = MODEL.TRASH_URI if name == "Trash" else Gio.File.new_for_path(str(special[name])).get_uri()
                self.sidebar.append(self._sidebar_row(name, icon, uri=uri))
            self.sidebar.append(self._sidebar_heading("设备与网络位置"))
            mounted_volumes = set()
            for mount in self.volume_monitor.get_mounts():
                volume = mount.get_volume()
                if volume:
                    mounted_volumes.add(volume)
                self.sidebar.append(
                    self._sidebar_row(
                        mount.get_name(),
                        "drive-removable-media-symbolic",
                        uri=mount.get_root().get_uri(),
                        mount=mount,
                        volume=volume,
                    )
                )
            for volume in self.volume_monitor.get_volumes():
                if volume not in mounted_volumes and volume.get_mount() is None:
                    self.sidebar.append(
                        self._sidebar_row(
                            volume.get_name(),
                            "drive-removable-media-symbolic",
                            volume=volume,
                        )
                    )

            self.sidebar.append(self._sidebar_heading("本机分区"))
            loading = self._sidebar_row(
                "正在读取本机分区",
                "drive-harddisk-symbolic",
                subtitle="将显示已挂载和未挂载的本机分区。",
            )
            self.sidebar.append(loading)
            self.local_partition_rows = [loading]
            self._refresh_local_partitions()

        @staticmethod
        def _sidebar_heading(title):
            separator = Gtk.ListBoxRow(selectable=False, activatable=False)
            section = Gtk.Label(label=title, xalign=0)
            section.add_css_class("heading")
            section.set_margin_start(14)
            section.set_margin_top(12)
            section.set_margin_bottom(6)
            separator.set_child(section)
            return separator

        def _refresh_local_partitions(self):
            generation = self.local_partition_generation

            def worker():
                snapshot = run_local_partition_snapshot()
                GLib.idle_add(self._apply_local_partitions, generation, snapshot)

            threading.Thread(target=worker, daemon=True).start()

        def _apply_local_partitions(self, generation, snapshot):
            if generation != self.local_partition_generation:
                return False
            for row in self.local_partition_rows:
                if row.get_parent() is self.sidebar:
                    self.sidebar.remove(row)
            self.local_partition_rows = []

            if not snapshot.get("ok"):
                row = self._sidebar_row(
                    "无法读取本机分区",
                    "dialog-warning-symbolic",
                    subtitle=snapshot.get("error") or "请刷新文件管理器后重试。",
                )
                self.sidebar.append(row)
                self.local_partition_rows.append(row)
                return False

            partitions = snapshot.get("partitions") or []
            if not partitions:
                row = self._sidebar_row(
                    "未检测到可显示分区",
                    "drive-harddisk-symbolic",
                    subtitle="系统没有返回本机块设备。",
                )
                self.sidebar.append(row)
                self.local_partition_rows.append(row)
                return False

            state_labels = {
                "mounted": "已挂载",
                "unmounted": "未挂载",
                "swap": "交换空间",
            }
            for partition in partitions:
                path = str(partition.get("path") or "未知设备")
                label = str(partition.get("label") or "").strip()
                title = "%s（%s）" % (label, path) if label else path
                mounts = [str(item) for item in partition.get("mountpoints", []) if item]
                mount = next((item for item in mounts if item.startswith("/")), "")
                state = state_labels.get(partition.get("state"), "状态未知")
                fstype = str(partition.get("fstype") or "未格式化")
                subtitle = "%s · %s" % (fstype, state)
                if mount:
                    subtitle += " · %s" % mount
                uri = Gio.File.new_for_path(mount).get_uri() if mount else None
                row = self._sidebar_row(
                    title,
                    "drive-harddisk-symbolic",
                    uri=uri,
                    subtitle=subtitle,
                )
                self.sidebar.append(row)
                self.local_partition_rows.append(row)
            return False

        def _sidebar_row(self, name, icon, uri=None, mount=None, volume=None, subtitle=""):
            active = bool(uri or mount is not None or volume is not None)
            row = Gtk.ListBoxRow(selectable=active, activatable=active)
            row.location_uri = uri
            row.mount_object = mount
            row.volume_object = volume
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(12)
            box.set_margin_end(8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.append(Gtk.Image.new_from_icon_name(icon))
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            labels.set_hexpand(True)
            label = Gtk.Label(label=name, xalign=0, ellipsize=3)
            labels.append(label)
            if subtitle:
                detail = Gtk.Label(label=subtitle, xalign=0, ellipsize=3)
                detail.add_css_class("dim-label")
                labels.append(detail)
            box.append(labels)
            if mount:
                button = self._icon_button("media-eject-symbolic", "卸载或弹出")
                button.connect("clicked", lambda _button: self._show_mount_menu(row, button))
                box.append(button)
            row.set_child(box)
            return row

        def _watch_volumes(self):
            for signal in (
                "mount-added",
                "mount-removed",
                "volume-added",
                "volume-removed",
            ):
                self.volume_signal_ids.append(
                    self.volume_monitor.connect(
                        signal, lambda *_args: GLib.idle_add(self.refresh_sidebar)
                    )
                )

        def _on_sidebar_activated(self, _listbox, row):
            if row.location_uri:
                self.navigate(row.location_uri)
                return
            volume = row.volume_object
            if volume:
                operation = Gtk.MountOperation(parent=self)

                def mounted(source, result):
                    try:
                        source.mount_finish(result)
                        mount = source.get_mount()
                        if mount:
                            self.navigate(mount.get_root().get_uri())
                    except GLib.Error as error:
                        self.toast(str(error))

                volume.mount(0, operation, None, mounted)

        def _show_mount_menu(self, row, button):
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(8)
            box.set_margin_end(8)
            unmount = Gtk.Button(label="卸载")
            unmount.connect("clicked", lambda _button: self._unmount(row.mount_object))
            box.append(unmount)
            if row.mount_object.can_eject():
                eject = Gtk.Button(label="弹出")
                eject.connect("clicked", lambda _button: self._eject(row.mount_object))
                box.append(eject)
            popover.set_child(box)
            popover.set_parent(button)
            popover.popup()

        def _unmount(self, mount):
            operation = Gtk.MountOperation(parent=self)
            mount.unmount_with_operation(
                Gio.MountUnmountFlags.NONE,
                operation,
                None,
                lambda source, result: self._finish_mount_action(
                    source, result, "unmount_with_operation_finish"
                ),
            )

        def _eject(self, mount):
            operation = Gtk.MountOperation(parent=self)
            mount.eject_with_operation(
                Gio.MountUnmountFlags.NONE,
                operation,
                None,
                lambda source, result: self._finish_mount_action(
                    source, result, "eject_with_operation_finish"
                ),
            )

        def _finish_mount_action(self, source, result, finish_method):
            try:
                getattr(source, finish_method)(result)
                self.navigate(self.model.home_uri)
            except GLib.Error as error:
                self.toast(str(error))

        def navigate(self, uri):
            self._clear_search_for_navigation()
            self._run_query(self.model.prepare_navigate(uri))

        def go_history(self, direction):
            self._clear_search_for_navigation()
            self._run_query(getattr(self.model, "prepare_" + direction)())

        def _clear_search_for_navigation(self):
            self.search.set_text("")
            if self.search_timer:
                GLib.source_remove(self.search_timer)
                self.search_timer = 0

        def reload(self, uri=None):
            self._run_query(self.model.prepare_load(uri))

        def _run_query(self, query):
            if self.query_token:
                self.query_token.cancel()
            self.query_token = CancellationToken()
            token = self.query_token
            self.query_generation += 1
            generation = self.query_generation

            def worker():
                try:
                    result = self.model.execute_query(query, token)
                    error = None
                except Exception as caught:
                    result = None
                    error = caught
                GLib.idle_add(self._finish_query, generation, token, result, error)

            threading.Thread(target=worker, daemon=True).start()

        def _finish_query(self, generation, token, result, error):
            if generation != self.query_generation or token.cancelled:
                return False
            if error:
                self.toast("无法读取此位置：%s" % error)
                return False
            items = self.model.commit_query(result)
            self._render_items(items)
            self._render_breadcrumbs()
            self.back_button.set_sensitive(bool(self.model._back))
            self.forward_button.set_sensitive(bool(self.model._forward))
            self.up_button.set_sensitive(bool(self.model.backend.parent(self.model.current_uri)))
            return False

        def _render_items(self, items):
            self.store.remove_all()
            for item in items:
                self.store.append(FileObject(item))
            empty = not items
            self.empty_state.set_visible(empty)
            self.view_stack.set_visible(not empty)

        def _render_breadcrumbs(self):
            while child := self.crumb_box.get_first_child():
                self.crumb_box.remove(child)
            for index, crumb in enumerate(self.model.breadcrumbs()):
                if index:
                    self.crumb_box.append(
                        Gtk.Image.new_from_icon_name("go-next-symbolic")
                    )
                button = Gtk.Button(label=crumb.label)
                button.add_css_class("flat")
                button.connect(
                    "clicked", lambda _button, uri=crumb.uri: self.navigate(uri)
                )
                self.crumb_box.append(button)

        def _on_search_changed(self, entry):
            if self.search_timer:
                GLib.source_remove(self.search_timer)
            self.search_timer = GLib.timeout_add(260, self._begin_search, entry.get_text())

        def _begin_search(self, query):
            self.search_timer = 0
            self._run_query(self.model.prepare_search(query))
            return False

        def _on_hidden_toggled(self, check):
            self.model.show_hidden = check.get_active()
            self.reload()

        def _on_sort_changed(self, *_args):
            keys = list(MODEL.SortKey)
            selected = min(self.sort_dropdown.get_selected(), len(keys) - 1)
            items = self.model.set_sort(
                keys[selected], self.descending_check.get_active()
            )
            self._render_items(items)

        def set_view(self, mode):
            self.view_stack.set_visible_child_name(mode)

        def _on_item_activated(self, _view, position):
            file_object = self.store.get_item(position)
            if file_object:
                self.open_item(file_object.item)

        def open_item(self, item):
            if item.is_directory:
                self.navigate(item.uri)
                return
            try:
                Gio.AppInfo.launch_default_for_uri(item.uri, None)
            except GLib.Error as error:
                self.toast("无法打开：%s" % error)

        def _show_item_menu(self, widget, x, y):
            file_object = getattr(widget, "file_object", None)
            if file_object is None:
                return
            item = file_object.item
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(8)
            box.set_margin_end(8)
            actions = [("打开", lambda: self.open_item(item))]
            if not item.is_directory:
                actions.append(("打开方式…", lambda: self._open_with(item)))
            if self.model.current_uri == MODEL.TRASH_URI:
                actions.extend(
                    [
                        ("恢复", lambda: self.perform_operation("restore", item.uri)),
                        ("永久删除", lambda: self._confirm_permanent_delete(item)),
                    ]
                )
            else:
                actions.extend(
                    [
                        ("剪切", lambda: self._set_clipboard(item, True)),
                        ("复制", lambda: self._set_clipboard(item, False)),
                        ("复制到…", lambda: self._choose_destination("copy", item.uri)),
                        ("移动到…", lambda: self._choose_destination("move", item.uri)),
                        ("重命名", lambda: self._rename_dialog(item)),
                        ("移到回收站", lambda: self.perform_operation("trash", item.uri)),
                    ]
                )
            for label, callback in actions:
                button = Gtk.Button(label=label)
                button.connect("clicked", lambda _button, action=callback: (popover.popdown(), action()))
                box.append(button)
            popover.set_child(box)
            popover.set_parent(widget)
            popover.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
            popover.popup()
            self.current_popover = popover

        def _open_with(self, item):
            file = Gio.File.new_for_uri(item.uri)
            dialog = Gtk.AppChooserDialog(
                transient_for=self,
                modal=True,
                file=file,
            )

            def response(chooser, response_id):
                if response_id == Gtk.ResponseType.OK:
                    app_info = chooser.get_app_info()
                    if app_info:
                        try:
                            app_info.launch([file], None)
                        except GLib.Error as error:
                            self.toast("无法打开：%s" % error)
                chooser.destroy()

            dialog.connect("response", response)
            dialog.present()

        def _set_clipboard(self, item, cut):
            self.clipboard_uri = item.uri
            self.clipboard_cut = bool(cut)
            self.paste_button.set_sensitive(True)
            self.toast("已剪切" if cut else "已复制")

        def _paste_clipboard(self):
            if not self.clipboard_uri:
                return
            if self.model.current_uri == MODEL.TRASH_URI:
                self.toast("不能粘贴到回收站")
                return
            operation = "move" if self.clipboard_cut else "copy"
            self.perform_operation(
                operation, self.clipboard_uri, self.model.current_uri
            )

        def _new_folder_dialog(self):
            dialog = Gtk.Dialog(title="新建文件夹", transient_for=self, modal=True)
            dialog.add_button("取消", Gtk.ResponseType.CANCEL)
            dialog.add_button("创建", Gtk.ResponseType.OK)
            entry = Gtk.Entry(text="新建文件夹", activates_default=True)
            entry.set_margin_top(18)
            entry.set_margin_bottom(18)
            entry.set_margin_start(18)
            entry.set_margin_end(18)
            dialog.get_content_area().append(entry)
            dialog.set_default_response(Gtk.ResponseType.OK)

            def response(_dialog, response_id):
                if response_id == Gtk.ResponseType.OK:
                    self.perform_operation(
                        "create_folder", self.model.current_uri, entry.get_text()
                    )
                dialog.destroy()

            dialog.connect("response", response)
            dialog.present()

        def _confirm_empty_trash(self):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="清空回收站？",
                body="其中的文件将被永久删除，此操作无法撤销。",
            )
            dialog.add_response("cancel", "取消")
            dialog.add_response("delete", "永久删除")
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE
            )
            dialog.connect(
                "response",
                lambda _dialog, response: self.perform_operation("empty_trash")
                if response == "delete"
                else None,
            )
            dialog.present()

        def _confirm_permanent_delete(self, item):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="永久删除“%s”？" % item.display_name,
                body="此操作无法撤销。",
            )
            dialog.add_response("cancel", "取消")
            dialog.add_response("delete", "永久删除")
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.set_response_appearance(
                "delete", Adw.ResponseAppearance.DESTRUCTIVE
            )
            dialog.connect(
                "response",
                lambda _dialog, response: self.perform_operation("delete", item.uri)
                if response == "delete"
                else None,
            )
            dialog.present()

        def _choose_destination(self, operation, source_uri):
            chooser = Gtk.FileChooserNative(
                title="选择目标文件夹",
                transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
                accept_label="选择",
                cancel_label="取消",
            )

            def response(dialog, response_id):
                if response_id == Gtk.ResponseType.ACCEPT and dialog.get_file():
                    self.perform_operation(
                        operation, source_uri, dialog.get_file().get_uri()
                    )
                dialog.destroy()

            chooser.connect("response", response)
            chooser.show()

        def _rename_dialog(self, item):
            dialog = Gtk.Dialog(title="重命名", transient_for=self, modal=True)
            dialog.add_button("取消", Gtk.ResponseType.CANCEL)
            dialog.add_button("重命名", Gtk.ResponseType.OK)
            entry = Gtk.Entry(text=item.name, activates_default=True)
            entry.set_margin_top(18)
            entry.set_margin_bottom(18)
            entry.set_margin_start(18)
            entry.set_margin_end(18)
            dialog.get_content_area().append(entry)
            dialog.set_default_response(Gtk.ResponseType.OK)

            def response(_dialog, response_id):
                if response_id == Gtk.ResponseType.OK:
                    self.perform_operation("rename", item.uri, entry.get_text())
                dialog.destroy()

            dialog.connect("response", response)
            dialog.present()

        def perform_operation(self, name, *args):
            if self.operation_token:
                self.operation_token.cancel()
            token = CancellationToken()
            self.operation_token = token
            self.progress.set_fraction(0.0)
            self.progress.set_pulse_step(0.08)
            self.progress_label.set_label("正在处理文件")
            self.progress_revealer.set_reveal_child(True)

            def progress(current, total):
                GLib.idle_add(self._set_progress, current, total)

            def worker():
                method = getattr(self.model, name)
                kwargs = {"token": token}
                if name in {"copy", "move", "empty_trash", "delete"}:
                    kwargs["progress"] = progress
                result = method(*args, **kwargs)
                GLib.idle_add(self._finish_operation, token, result)

            threading.Thread(target=worker, daemon=True).start()

        def _set_progress(self, current, total):
            if total:
                self.progress.set_fraction(min(1.0, current / total))
            else:
                self.progress.pulse()
            return False

        def _finish_operation(self, token, result):
            if token is not self.operation_token:
                return False
            self.operation_token = None
            self.progress_revealer.set_reveal_child(False)
            if result.success:
                if (
                    result.operation == "move"
                    and result.source_uri == self.clipboard_uri
                    and self.clipboard_cut
                ):
                    self.clipboard_uri = None
                    self.clipboard_cut = False
                    self.paste_button.set_sensitive(False)
                self.toast("操作完成")
                self.reload()
            elif result.error.code != "cancelled":
                self.toast("操作失败：%s" % result.error.message)
            return False

        def _cancel_operation(self, _button):
            if self.operation_token:
                self.operation_token.cancel()
                self.progress_label.set_label("正在取消…")

        def toast(self, message):
            self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

        @staticmethod
        def _format_size(size):
            value = float(size)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if value < 1024 or unit == "TB":
                    return ("%.1f %s" % (value, unit)) if unit != "B" else ("%d B" % value)
                value /= 1024

        def _install_css(self):
            css = b"""
            window { background: #f6f8f7; color: #18221f; }
            .ming-sidebar { background: #edf2ef; border-right: 1px solid alpha(#18221f, .08); }
            .ming-content { background: #ffffff; }
            .ming-breadcrumb-scroll { border-bottom: 1px solid alpha(#18221f, .08); }
            .ming-file-tile { padding: 10px; }
            .ming-file-tile:hover { background: alpha(#2f8a7d, .08); }
            .ming-progress { padding: 10px 14px; background: #edf5f2; border-top: 1px solid alpha(#18221f, .08); }
            progressbar progress { background: #2f8a7d; }
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )


    class MingFilesApplication(Adw.Application):
        def __init__(self, new_window=False):
            flags = Gio.ApplicationFlags.HANDLES_OPEN
            if new_window:
                flags |= Gio.ApplicationFlags.NON_UNIQUE
            super().__init__(application_id="org.mingos.Files", flags=flags)

        def do_activate(self):
            window = self.props.active_window
            if window is None:
                window = MingFiles(self)
            window.present()

        def do_open(self, files, _n_files, _hint):
            uri = files[0].get_uri() if files else None
            window = self.props.active_window
            if window is None:
                window = MingFiles(self, uri)
            elif uri:
                window.navigate(uri)
            window.present()

else:
    class MingFiles:
        """Import placeholder; main() reports the missing GTK/Gio runtime."""


def run_self_test(parent_path, backend):
    import shutil
    import tempfile

    parent = Path(parent_path).expanduser().resolve()
    if not parent.is_dir():
        raise ValueError("self-test parent must be an existing directory")
    root = Path(tempfile.mkdtemp(prefix="ming-files-self-test-", dir=parent))
    try:
        source = root / "source.txt"
        destination = root / "destination"
        source.write_text("ming-files-self-test\n", encoding="utf-8")
        destination.mkdir()
        copied_uri = backend.copy(source.as_uri(), destination.as_uri())
        renamed_uri = backend.rename(copied_uri, "renamed.txt")
        renamed = destination / "renamed.txt"
        if renamed.read_text(encoding="utf-8") != "ming-files-self-test\n":
            raise RuntimeError("copied file content mismatch")
        backend.delete(renamed_uri)
        backend.delete(source.as_uri())
        if renamed.exists() or source.exists():
            raise RuntimeError("Gio delete did not remove self-test files")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def build_parser():
    parser = argparse.ArgumentParser(description="Ming OS file manager")
    parser.add_argument("location", nargs="?", help="URI or local path to open")
    parser.add_argument("--new-window", action="store_true", help="open a separate window")
    parser.add_argument("--check-runtime", action="store_true", help="check GTK4 and Gio/GVfs")
    parser.add_argument("--self-test", metavar="DIR", help="exercise Gio copy, rename and delete in DIR")
    parser.add_argument("--version", action="store_true", help="show version")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.version:
        print("Ming Files %s" % VERSION)
        return 0
    if not GTK_AVAILABLE:
        print(
            "Ming Files requires GTK4, libadwaita and Gio/GVfs: %s" % GTK_IMPORT_ERROR,
            file=sys.stderr,
        )
        return 2
    try:
        backend = MODEL.GioFileBackend()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.check_runtime:
        print("GTK4, libadwaita and Gio/GVfs are available")
        return 0
    if args.self_test:
        try:
            return run_self_test(args.self_test, backend)
        except Exception as error:
            print("Ming Files self-test failed: %s" % error, file=sys.stderr)
            return 1
    app = MingFilesApplication(args.new_window)
    run_argv = [sys.argv[0]]
    if args.location:
        run_argv.append(args.location)
    return app.run(run_argv)


if __name__ == "__main__":
    raise SystemExit(main())
