"""vol-osd — GTK4 layer-shell per-app volume OSD daemon."""

from __future__ import annotations

import os
import socket
import sys
import threading
from ctypes import CDLL

try:
    CDLL("libgtk4-layer-shell.so")
except OSError:
    try:
        CDLL("libgtk4-layer-shell.so.0")
    except OSError:
        print(
            "ERROR: libgtk4-layer-shell not found.\n"
            "  sudo apt install libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 gir1.2-gtk-4.0",
            file=sys.stderr,
        )
        sys.exit(1)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import GLib, Gtk, Gdk
from gi.repository import Gtk4LayerShell as LayerShell

from vol_osd.audio import (
    SOCKET_PATH,
    _invalidate_cache,
    get_default_sink,
    get_focus,
    get_streams,
    get_sinks,
)

HIDE_DELAY = 1800  # ms

CSS = """
* {
  font-family: "JetBrains Mono Nerd Font", monospace;
  font-size: 15px;
}
window { background: transparent; }

#osd-box {
  background-color: #1e1e2e;
  border-radius: 1rem;
  padding: 0.6rem 1rem;
  min-width: 280px;
}

.app-row {
  border-radius: 0.5rem;
  padding: 0.25rem 0.5rem;
  margin: 1px 0;
}
.app-row.focused { background-color: #313244; }

.sink-row { padding: 0.15rem 0.5rem; }
.sink-row.default { background-color: #313244; }

.app-name         { color: #cdd6f4; min-width: 130px; }
.app-name.focused { color: #cba6f7; }
.app-name.default { color: #a6e3a1; }
.app-muted        { color: #585b70; }

.bar-track {
  background-color: #313244;
  border-radius: 0.4rem;
  min-height: 6px;
  min-width: 100px;
}
.bar-fill         { border-radius: 0.4rem; min-height: 6px; background-color: #b4befe; }
.bar-fill.focused { background-color: #cba6f7; }
.bar-fill.default { background-color: #a6e3a1; }

.vol-label         { color: #6c7086; min-width: 36px; }
.vol-label.focused { color: #a6adc8; }

.sink-label {
  color: #6c7086;
  font-size: 12px;
  padding: 0 0.5rem 0.2rem 2rem;
}
"""


def _load_css() -> None:
    gi.require_version("Gdk", "4.0")

    display = Gdk.Display.get_default()
    if not display:
        return
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


class OsdWindow:
    def __init__(self, app: Gtk.Application) -> None:
        self.hide_timer: int | None = None

        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_default_size(320, 200)

        LayerShell.init_for_window(self.win)
        LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.NONE)
        LayerShell.set_anchor(self.win, LayerShell.Edge.RIGHT, True)
        LayerShell.set_anchor(self.win, LayerShell.Edge.BOTTOM, True)
        LayerShell.set_margin(self.win, LayerShell.Edge.RIGHT, 24)
        LayerShell.set_margin(self.win, LayerShell.Edge.BOTTOM, 60)

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.outer.set_name("osd-box")
        self.win.set_child(self.outer)
        self.win.set_visible(False)

    def _clear(self) -> None:
        child = self.outer.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.outer.remove(child)
            child = nxt

    def show(
        self, streams: list[dict], focus_id: int | None, mode: str = "apps"
    ) -> None:
        self._clear()

        if mode == "apps":
            if not streams:
                lbl = Gtk.Label(label="  no audio streams")
                lbl.add_css_class("app-name")
                self.outer.append(lbl)
            else:
                for s in streams:
                    focused = s["id"] == focus_id
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    row.add_css_class("app-row")
                    if focused:
                        row.add_css_class("focused")

                    icon = (
                        "\U000f0581 "
                        if s["muted"]
                        else ("\U000f057e " if focused else "\U000f0580 ")
                    )
                    icon_lbl = Gtk.Label(label=icon)
                    icon_lbl.add_css_class("app-muted" if s["muted"] else "app-name")
                    if focused:
                        icon_lbl.add_css_class("focused")
                    row.append(icon_lbl)

                    name = s["name"]
                    if len(name) > 20:
                        name = name[:19] + "\u2026"
                    name_lbl = Gtk.Label(label=name, xalign=0)
                    name_lbl.set_hexpand(True)
                    name_lbl.add_css_class("app-name")
                    if focused:
                        name_lbl.add_css_class("focused")
                    row.append(name_lbl)

                    pct = min(1.0, max(0.0, s["vol"]))
                    track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                    track.add_css_class("bar-track")
                    track.set_valign(Gtk.Align.CENTER)
                    fill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                    fill.add_css_class("bar-fill")
                    if focused:
                        fill.add_css_class("focused")
                    fill.set_size_request(int(100 * pct), -1)
                    track.append(fill)
                    row.append(track)

                    pct_lbl = Gtk.Label(label=f"{int(pct * 100):3d}%")
                    pct_lbl.add_css_class("vol-label")
                    if focused:
                        pct_lbl.add_css_class("focused")
                    row.append(pct_lbl)

                    self.outer.append(row)

                    if focused:
                        sink = s.get("sink_name", "")
                        if len(sink) > 34:
                            sink = sink[:33] + "\u2026"
                        sink_lbl = Gtk.Label(label=f"  \u21aa {sink}", xalign=0)
                        sink_lbl.add_css_class("sink-label")
                        self.outer.append(sink_lbl)

        elif mode == "sinks":
            default_sink = get_default_sink()
            sinks = get_sinks()
            for sink in sinks:
                is_default = sink["id"] == default_sink
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.add_css_class("sink-row")
                if is_default:
                    row.add_css_class("default")

                icon = (
                    "\U000f0581 "
                    if sink["muted"]
                    else ("\U000f057e " if is_default else "\U000f0580 ")
                )
                icon_lbl = Gtk.Label(label=icon)
                if sink["muted"]:
                    icon_lbl.add_css_class("app-muted")
                elif is_default:
                    icon_lbl.add_css_class("app-name")
                    icon_lbl.add_css_class("default")
                else:
                    icon_lbl.add_css_class("app-name")
                row.append(icon_lbl)

                name = sink["name"]
                if len(name) > 20:
                    name = name[:19] + "\u2026"
                name_lbl = Gtk.Label(label=name, xalign=0)
                name_lbl.set_hexpand(True)
                name_lbl.add_css_class("app-name")
                if is_default:
                    name_lbl.add_css_class("default")
                row.append(name_lbl)

                pct = min(1.0, max(0.0, sink["vol"]))
                track = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                track.add_css_class("bar-track")
                track.set_valign(Gtk.Align.CENTER)
                fill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                fill.add_css_class("bar-fill")
                if is_default:
                    fill.add_css_class("default")
                fill.set_size_request(int(100 * pct), -1)
                track.append(fill)
                row.append(track)

                pct_lbl = Gtk.Label(label=f"{int(pct * 100):3d}%")
                pct_lbl.add_css_class("vol-label")
                if is_default:
                    pct_lbl.add_css_class("default")
                row.append(pct_lbl)

                self.outer.append(row)

        self.win.set_visible(True)
        self.win.present()
        self._reset_timer()

    def _reset_timer(self) -> None:
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
        self.hide_timer = GLib.timeout_add(HIDE_DELAY, self._hide)

    def _hide(self) -> bool:
        self.win.set_visible(False)
        self.hide_timer = None
        return False


class VolOsdApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="io.github.vol-osd")
        self.osd: OsdWindow | None = None

    def do_activate(self) -> None:
        _load_css()
        self.osd = OsdWindow(self)
        self._start_socket()
        self.hold()

    def _start_socket(self) -> None:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        sock.listen(5)
        threading.Thread(target=self._loop, args=(sock,), daemon=True).start()

    def _loop(self, sock: socket.socket) -> None:
        while True:
            try:
                conn, _ = sock.accept()
                msg = conn.recv(256).decode().strip()
                conn.close()
                GLib.idle_add(self._handle, msg)
            except Exception:
                pass

    def _handle(self, msg: str) -> bool:
        if not self.osd:
            return False
        if msg.startswith("show"):
            _invalidate_cache()
            parts = msg.split()
            mode = (
                parts[1] if len(parts) > 1 and parts[1] in ("apps", "sinks") else "apps"
            )
            streams = get_streams()
            try:
                focus_id = int(get_focus())
            except (ValueError, TypeError):
                focus_id = None
            self.osd.show(streams, focus_id, mode=mode)
        elif msg == "hide":
            self.osd._hide()
        return False


def main() -> None:
    app = VolOsdApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
