"""Main window: sidebar navigation, live config editing, daemon status."""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import config, device, diagnostics, touchegg  # noqa: E402
from . import service  # noqa: E402
from .pages import PAGES, esc  # noqa: E402

SAVE_DELAY_MS = 220
STATUS_POLL_MS = 2000


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="PadForge")
        self.set_default_size(1080, 780)
        self.set_size_request(380, 480)
        self.cfg = config.load()
        self.loading = 0
        self.status: dict | None = None
        self._save_id = 0
        self._current = None

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=260)

        # -- sidebar
        side_view = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        title = Adw.WindowTitle(title="PadForge", subtitle="touchpad")
        side_header.set_title_widget(title)
        side_view.add_top_bar(side_header)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("navigation-sidebar")
        side_view.set_content(Gtk.ScrolledWindow(child=self.nav, vexpand=True))
        self.split.set_sidebar(Adw.NavigationPage(title="PadForge", child=side_view))

        # -- content
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120)
        content_view = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar()
        self.content_title = Adw.WindowTitle(title="", subtitle="")
        self.content_header.set_title_widget(self.content_title)
        menu = Gio.Menu()
        for name, label in (("windows", "Windows+ profili"), ("macos", "macOS profili"),
                            ("linux", "Linux varsayılanı")):
            menu.append(label, f"win.profile::{name}")
        section = Gio.Menu()
        section.append("Hakkında", "win.about")
        menu.append_section(None, section)
        self.content_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                                    tooltip_text="Menü"))
        content_view.add_top_bar(self.content_header)
        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self._banner_clicked)
        content_view.add_top_bar(self.banner)
        content_view.set_content(self.stack)
        self.content_page = Adw.NavigationPage(title="", child=content_view)
        self.split.set_content(self.content_page)

        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.pages = []
        for cls in PAGES:
            page = cls(self)
            self.pages.append(page)
            self.stack.add_named(page.widget, page.id)
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6)
            box.append(Gtk.Image(icon_name=page.icon))
            box.append(Gtk.Label(label=page.title, xalign=0))
            row.set_child(box)
            row.page = page
            self.nav.append(row)
        self.nav.connect("row-selected", self._row_selected)

        act = Gio.SimpleAction.new("profile", GLib.VariantType.new("s"))
        act.connect("activate", lambda a, v: self.apply_profile(v.get_string()))
        self.add_action(act)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *a: self._about())
        self.add_action(about)

        for p in self.pages:
            p.refresh()
        self.nav.select_row(self.nav.get_row_at_index(0))
        self.poll_status()
        GLib.timeout_add(STATUS_POLL_MS, self._poll_tick)
        self.connect("close-request", self._closing)

    # -- navigation -------------------------------------------------------------------
    def _row_selected(self, box, row):
        if row is None:
            return
        page = row.page
        if self._current is not None and self._current is not page:
            self._current.hidden()
        self._current = page
        self.stack.set_visible_child_name(page.id)
        self.content_title.set_title(page.title)
        self.content_page.set_title(page.title)
        self.split.set_show_content(True)
        page.shown()

    def show_page(self, page_id: str) -> None:
        for i, p in enumerate(self.pages):
            if p.id == page_id:
                self.nav.select_row(self.nav.get_row_at_index(i))

    def _closing(self, *_):
        if self._current is not None:
            self._current.hidden()
        self._flush_save()
        return False

    # -- config -----------------------------------------------------------------------
    def get(self, path: str):
        return config.get(self.cfg, path)

    def set(self, path: str, value) -> None:
        if config.get(self.cfg, path) == value:
            return
        config.set_value(self.cfg, path, value)
        self._schedule_save()
        for p in self.pages:
            if p is self._current or p.id in ("pointer", "gestures"):
                # Pages with dependent widgets re-read their state.
                p.refresh()

    def _schedule_save(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(SAVE_DELAY_MS, self._flush_save)

    def _flush_save(self) -> bool:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        try:
            self.cfg = config.validate(self.cfg)
            config.save(self.cfg)
            self.content_title.set_subtitle("Kaydedildi · anında uygulandı" if self.status
                                            else "Kaydedildi · servis çalışmıyor")
        except OSError as exc:
            self.toast(f"Kaydedilemedi: {exc}")
        return False

    def replace_config(self, cfg: dict) -> None:
        self.cfg = config.validate(cfg)
        self._schedule_save()
        for p in self.pages:
            p.refresh()

    def apply_profile(self, name: str, title: str | None = None) -> None:
        self.replace_config(config.apply_profile(self.cfg, name))
        self.toast(f"{title or name} profili uygulandı")

    # -- daemon status ----------------------------------------------------------------------
    def poll_status(self) -> None:
        service.async_call(service.daemon_status, self._got_status)

    def _poll_tick(self) -> bool:
        self.poll_status()
        return True

    def _got_status(self, status) -> None:
        if isinstance(status, Exception):
            status = None
        self.status = status
        if getattr(self, "screenshot_mode", False):
            self.banner.set_revealed(False)
        elif status is None:
            self.banner.set_title("Servis çalışmıyor: hareketler ve gecikmesiz dokunma kapalı")
            self.banner.set_button_label("Başlat")
            self.banner.set_revealed(True)
            self._banner_action = "start"
        elif any(not p.get("readable") for p in status.get("pads", [])):
            self.banner.set_title("Touchpad ham verisine erişim yok: hareketler kapalı")
            self.banner.set_button_label("Kur")
            self.banner.set_revealed(True)
            self._banner_action = "helper"
        else:
            self.banner.set_revealed(False)
        for p in self.pages:
            p.status_changed(status)

    def _banner_clicked(self, *_):
        if getattr(self, "_banner_action", "") == "helper":
            self.run_helper([])
            return

        def done(r):
            rc, out = r if not isinstance(r, Exception) else (1, str(r))
            if rc != 0:
                self.toast("Servis başlatılamadı: install.sh ile kurun")
            GLib.timeout_add(800, lambda: (self.poll_status(), False)[1])
        service.async_call(lambda: service.systemctl("start", service.UNIT), done)

    # -- helpers used by pages --------------------------------------------------------------
    def readable_pad(self) -> device.TouchpadInfo | None:
        match = self.cfg["device"]["match"]
        for p in device.list_touchpads():
            if p.readable and (match == "auto" or match.lower() in p.name.lower()):
                return p
        return None

    def toast(self, text: str) -> None:
        t = Adw.Toast(title=esc(text))
        t.set_timeout(3)
        self.toasts.add_toast(t)

    def copy(self, text: str) -> None:
        service.copy_text(self, text)
        self.toast("Panoya kopyalandı")

    def run_action(self, action: str, arg: str | None) -> None:
        r = service.daemon_command({"cmd": "run", "action": action, "arg": arg})
        if r is None:
            from Xlib import display
            from ..actions import Executor
            try:
                ex = Executor(display.Display())
                ex.run(action, arg)
                ex.d.sync()
            except Exception as exc:
                self.toast(f"Çalıştırılamadı: {exc}")

    def run_helper(self, args: list[str]) -> None:
        script = diagnostics.helper_script()
        try:
            proc = Gio.Subprocess.new(["pkexec", "/bin/bash", script, *args],
                                      Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as exc:
            self.toast(f"pkexec çalıştırılamadı: {exc.message}")
            return

        def done(p, res):
            try:
                _, out, _ = p.communicate_utf8_finish(res)
            except GLib.Error as exc:
                out = exc.message
            if p.get_exit_status() == 0:
                self.toast("Tamamlandı")
            elif p.get_exit_status() in (126, 127):
                self.toast("İptal edildi")
            else:
                self.text_dialog("Kurulum çıktısı", out or "")
            GLib.timeout_add(1200, lambda: (self.poll_status(), [pg.shown() for pg in self.pages
                                                                 if pg is self._current], False)[2])
        proc.communicate_utf8_async(None, None, done)

    def fix_buttons(self, check) -> list[Gtk.Widget]:
        out = []
        if "Touchégg" in check.title:
            b = Gtk.Button(label="Aktar ve kapat", valign=Gtk.Align.CENTER)
            b.add_css_class("suggested-action")
            b.connect("clicked", lambda *_: self.import_touchegg())
            out.append(b)
        elif check.fix.startswith("sudo bash"):
            args = ["--power-fix"] if "--power-fix" in check.fix else []
            b = Gtk.Button(label="Düzelt", valign=Gtk.Align.CENTER,
                           tooltip_text="Yönetici şifresi istenir")
            b.add_css_class("suggested-action")
            b.connect("clicked", lambda *_: self.run_helper(args))
            out.append(b)
        elif check.fix.startswith("systemctl --user"):
            b = Gtk.Button(label="Başlat", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self._banner_clicked())
            out.append(b)
        if check.fix:
            c = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                           tooltip_text=check.fix)
            c.add_css_class("flat")
            c.connect("clicked", lambda *_: self.copy(check.fix))
            out.append(c)
        return out

    def import_touchegg(self) -> None:
        if not touchegg.USER_CONF.exists():
            self.toast("Touchégg ayarı bulunamadı")
            return
        try:
            imported = touchegg.parse(touchegg.USER_CONF)
        except Exception as exc:
            self.toast(f"Okunamadı: {exc}")
            return
        lines = [f"• {k}: {v['action']}{(' ' + v['arg']) if v.get('arg') else ''}"
                 for k, v in sorted(imported["bindings"].items())]
        lines += [f"• {n}" for n in imported["notes"]]
        dlg = Adw.AlertDialog(heading="Touchégg'den aktar",
                              body="Bu hareketler PadForge'a aktarılacak ve Touchégg istemcisi "
                                   "kapatılacak (oturum açılışında da başlamayacak). Geri almak "
                                   "için Gelişmiş › Touchégg › Yeniden aç.\n\n" + "\n".join(lines))
        dlg.add_response("cancel", "Vazgeç")
        dlg.add_response("import", "Yalnızca aktar")
        dlg.add_response("both", "Aktar ve Touchégg'i kapat")
        dlg.set_response_appearance("both", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("both")

        def chosen(d, res):
            r = d.choose_finish(res)
            if r == "cancel":
                return
            self.replace_config(touchegg.merge_into(self.cfg, imported))
            if r == "both":
                for line in touchegg.disable_client():
                    pass
                self.toast("Aktarıldı, Touchégg kapatıldı")
            else:
                self.toast("Hareketler aktarıldı")
            for p in self.pages:
                if p.id in ("overview", "advanced"):
                    p.shown() if p is self._current else None
        dlg.choose(self, None, chosen)

    def text_dialog(self, title: str, text: str) -> None:
        dlg = Adw.Dialog(title=title, content_width=760, content_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        tv = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          left_margin=12, right_margin=12, top_margin=12, bottom_margin=12)
        tv.get_buffer().set_text(text)
        view.set_content(Gtk.ScrolledWindow(child=tv, vexpand=True))
        dlg.set_child(view)
        dlg.present(self)

    def _about(self):
        from .. import __version__
        about = Adw.AboutDialog(application_name="PadForge", version=__version__,
                                application_icon="io.github.padforge.PadForge",
                                developer_name="Mustafa & Claude",
                                license_type=Gtk.License.MIT_X11,
                                comments="Linux (X11) için hassas touchpad ayarları, çok parmaklı "
                                         "hareketler, gecikmesiz dokunma, kinetik kaydırma ve "
                                         "donanım teşhisi.")
        about.present(self)
