#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ming Tea native GTK4/libadwaita shell for DeepSeek Harness."""

import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys


CORE_PATH = pathlib.Path("/usr/local/lib/ming-os/ming-tea/ming-tea-core.py")
if not CORE_PATH.is_file():
    CORE_PATH = pathlib.Path(__file__).with_name("ming-tea-core.py")
SPEC = importlib.util.spec_from_file_location("ming_tea_core", CORE_PATH)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


def socket_path():
    return pathlib.Path(os.environ.get("MING_TEA_SOCKET", "/tmp/ming-tea.sock"))


class MingTeaClient:
    def request(self, payload):
        path = socket_path()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(path))
                client.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
                return json.loads(client.makefile("r", encoding="utf-8").readline())
        except (OSError, ValueError):
            return None


try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk
except (ImportError, ValueError):
    Adw = Gio = GLib = Gtk = None


# Native GTK surface: Gtk.Application / Adw.ApplicationWindow, never a web wrapper.
class MingTeaWindow(Adw.ApplicationWindow if Adw else object):
    def __init__(self, app):
        super().__init__(application=app, title="铭荼")
        self.set_default_size(1120, 720)
        self.set_size_request(860, 560)
        self.runtime = CORE.MingTeaRuntime()
        self.session = self.runtime.create_session("learning")
        self.scene = "learning"
        self.client = MingTeaClient()
        self._build_ui()

    def _build_ui(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="铭荼"))
        self.status = Gtk.Label(label="本地适配层就绪")
        self.status.add_css_class("dim-label")
        header.pack_end(self.status)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.append(header)
        body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_position(220)

        scene_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scene_box.set_margin_top(18)
        scene_box.set_margin_bottom(18)
        scene_box.set_margin_start(16)
        scene_box.set_margin_end(16)
        scene_title = Gtk.Label(label="工作场景", xalign=0)
        scene_title.add_css_class("heading")
        scene_box.append(scene_title)
        self.scene_buttons = {}
        for scene_id in ("office", "development", "learning"):
            button = Gtk.Button(label=CORE.SCENES[scene_id]["label"])
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", self._select_scene, scene_id)
            self.scene_buttons[scene_id] = button
            scene_box.append(button)
        scene_hint = Gtk.Label(
            label="场景内自动编排浏览器、文件、终端和办公工具。",
            wrap=True,
            xalign=0,
        )
        scene_hint.add_css_class("dim-label")
        scene_box.append(scene_hint)
        body.set_start_child(scene_box)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        center.set_margin_top(14)
        center.set_margin_bottom(14)
        center.set_margin_start(14)
        center.set_margin_end(14)
        self.transcript = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.transcript.add_css_class("card")
        transcript_scroll = Gtk.ScrolledWindow(vexpand=True)
        transcript_scroll.set_child(self.transcript)
        center.append(transcript_scroll)
        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry(placeholder_text="告诉铭荼你要完成什么…", hexpand=True)
        self.entry.connect("activate", self._send)
        composer.append(self.entry)
        send = Gtk.Button(label="发送")
        send.add_css_class("suggested-action")
        send.connect("clicked", self._send)
        composer.append(send)
        center.append(composer)
        body.set_end_child(center)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        side.set_margin_top(18)
        side.set_margin_bottom(18)
        side.set_margin_start(14)
        side.set_margin_end(14)
        plan_title = Gtk.Label(label="任务状态", xalign=0)
        plan_title.add_css_class("heading")
        side.append(plan_title)
        self.plan = Gtk.Label(label="尚未请求工具", wrap=True, xalign=0)
        self.plan.add_css_class("dim-label")
        side.append(self.plan)
        log_title = Gtk.Label(label="安全提示", xalign=0)
        log_title.add_css_class("heading")
        side.append(log_title)
        safety = Gtk.Label(
            label="读取和低风险诊断可自动执行。\n安装、sudo、删除、上传和提交会先请求确认。",
            wrap=True,
            xalign=0,
        )
        safety.add_css_class("dim-label")
        side.append(safety)
        body.set_resize_end_child(True)
        root.append(body)
        self.set_content(root)
        self._append("铭荼已启动。请选择一个场景开始。")
        self._select_scene(None, "learning")

    def _append(self, text):
        buffer = self.transcript.get_buffer()
        end = buffer.get_end_iter()
        buffer.insert(end, str(text).strip() + "\n\n")

    def _select_scene(self, _button, scene_id):
        self.scene = scene_id
        self.session = self.runtime.create_session(scene_id)
        for key, button in self.scene_buttons.items():
            button.set_sensitive(key != scene_id)
        self.status.set_text(CORE.SCENES[scene_id]["label"])
        self.plan.set_text(CORE.SCENES[scene_id]["prompt"])

    def _send(self, _button=None):
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self._append("我：" + text)
        response = self.client.request({"action": "ping"})
        if response and response.get("ok"):
            self.status.set_text("DSH 本地适配服务已连接")
        else:
            self.status.set_text("本地演示模式")
        if text.startswith("/终端 "):
            events = self.runtime.submit_tool_request(
                self.session["session_id"], "terminal.run", {"command": text[4:]})
            self.plan.set_text("\n".join(event["type"] for event in events))
            self._append("铭荼：该终端动作需要经过权限确认。")
            return
        self._append("铭荼：已记录任务，将按当前场景自动编排工具。DSH runtime 接入后会继续执行。")


class MingTeaApp(Adw.Application if Adw else object):
    def __init__(self):
        super().__init__(application_id="cn.mingos.MingTea", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window or MingTeaWindow(self)
        window.present()


def main():
    if Adw is None:
        print("铭荼需要 GTK4 和 libadwaita 运行时。", file=sys.stderr)
        return 1
    return MingTeaApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
