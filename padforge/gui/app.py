"""GTK application entry point (``padforge`` or ``padforge gui``)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID  # noqa: E402

HERE = Path(__file__).resolve().parent




class App(Adw.Application):
    def __init__(self, screenshot_dir: str | None = None, page: str | None = None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if screenshot_dir else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=APP_ID, flags=flags)
        self.screenshot_dir = screenshot_dir
        self.start_page = page

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(HERE / "icons"))
        packaging = HERE.parent.parent / "packaging" / "icons"
        if packaging.exists():
            theme.add_search_path(str(packaging))
        # The neon theme is dark-only.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(HERE / "theme.css"))
        Gtk.StyleContext.add_provider_for_display(display, provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        from .window import MainWindow
        win = self.props.active_window
        if win is None:
            win = MainWindow(self)
        win.present()
        if self.start_page:
            win.show_page(self.start_page)
        if self.screenshot_dir:
            GLib.timeout_add(900, lambda: Screenshotter(win, self.screenshot_dir).start() and False)


class Screenshotter:
    """Renders every page of the window to PNG (README images, visual checks)."""

    def __init__(self, win, out_dir: str):
        self.win = win
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.queue = [p.id for p in win.pages]

    def start(self) -> bool:
        # Images show the interface, not this machine's service state.
        self.win.screenshot_mode = True
        self.win.banner.set_revealed(False)
        self._next()
        return True

    def _next(self):
        if not self.queue:
            self.win.get_application().quit()
            return
        page_id = self.queue.pop(0)
        self.win.show_page(page_id)
        if page_id == "live":            # pictures show fingers, not an empty pad
            live = next(p for p in self.win.pages if p.id == "live")
            live.hidden()
            live.start_demo()
        GLib.timeout_add(1100, lambda: (self._shoot(page_id), self._next(), False)[2])

    def _shoot(self, name: str):
        """Grab the window's pixels from the X server (GTK cannot paint a toplevel offscreen)."""
        from Xlib import X
        from Xlib import display as xdisplay
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        surface = self.win.get_surface()
        xid = surface.get_xid()
        sx, sy = self.win.get_surface_transform()
        w, h = self.win.get_width(), self.win.get_height()
        d = xdisplay.Display()
        xwin = d.create_resource_object("window", xid)
        img = xwin.get_image(int(sx), int(sy), w, h, X.ZPixmap, 0xFFFFFFFF)
        data = img.data
        n = w * h
        rgba = bytearray(n * 4)
        rgba[0::4] = data[2::4]
        rgba[1::4] = data[1::4]
        rgba[2::4] = data[0::4]
        rgba[3::4] = b"\xff" * n
        pix = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB,
                                              True, 8, w, h, w * 4)
        pix.savev(str(self.out / f"{name}.png"), "png", [], [])
        d.close()
        print(f"ekran görüntüsü: {self.out / (name + '.png')}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="padforge gui")
    ap.add_argument("--screenshot", metavar="DIR", help="tüm sayfaların PNG görüntüsünü al ve çık")
    ap.add_argument("--page", help="açılış sayfası (overview, pointer, tap, scroll, gestures, live, advanced)")
    args = ap.parse_args(argv or [])
    app = App(args.screenshot, args.page)
    return app.run([sys.argv[0]])
