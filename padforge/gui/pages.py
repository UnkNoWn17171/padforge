"""The settings pages."""
from __future__ import annotations

import os
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import __version__, accel, config, device, diagnostics, touchegg  # noqa: E402
from ..actions import ACTIONS  # noqa: E402
from . import service  # noqa: E402
from .widgets import CurveEditor, TouchView  # noqa: E402


def esc(text: str) -> str:
    return GLib.markup_escape_text(str(text))


def fmt(v, unit="", nd=1) -> str:
    return f"{v:.{nd}f}{unit}" if isinstance(v, (int, float)) else "—"


class Page:
    id = ""
    title = ""
    icon = ""

    def __init__(self, win):
        self.win = win
        self.binders: list = []
        self.widget = self.build()

    def build(self) -> Gtk.Widget:
        raise NotImplementedError

    def refresh(self) -> None:
        self.win.loading += 1
        try:
            for b in self.binders:
                b()
            self.after_refresh()
        finally:
            self.win.loading -= 1

    def after_refresh(self) -> None:
        pass

    def shown(self) -> None:
        pass

    def hidden(self) -> None:
        pass

    def status_changed(self, status: dict | None) -> None:
        pass

    # -- row helpers bound to config paths ----------------------------------------
    def group(self, page, title: str, description: str | None = None) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=esc(title))
        if description:
            g.set_description(esc(description))
        page.add(g)
        return g

    def switch(self, parent, title, path, subtitle=None) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=esc(title))
        if subtitle:
            row.set_subtitle(esc(subtitle))

        def changed(r, _p):
            if not self.win.loading:
                self.win.set(path, r.get_active())
        row.connect("notify::active", changed)
        self.binders.append(lambda: row.set_active(bool(self.win.get(path))))
        parent.add(row) if not isinstance(parent, Adw.ExpanderRow) else parent.add_row(row)
        return row

    def combo(self, parent, title, path, options, subtitle=None) -> Adw.ComboRow:
        values = [v for v, _ in options]
        row = Adw.ComboRow(title=esc(title), model=Gtk.StringList.new([lbl for _, lbl in options]))
        if subtitle:
            row.set_subtitle(esc(subtitle))

        def changed(r, _p):
            if not self.win.loading and 0 <= r.get_selected() < len(values):
                self.win.set(path, values[r.get_selected()])

        def load():
            v = self.win.get(path)
            row.set_selected(values.index(v) if v in values else 0)
        row.connect("notify::selected", changed)
        self.binders.append(load)
        parent.add(row) if not isinstance(parent, Adw.ExpanderRow) else parent.add_row(row)
        return row

    def spin(self, parent, title, path, lo, hi, step, digits=0, subtitle=None) -> Adw.SpinRow:
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(esc(title))
        row.set_digits(digits)
        if subtitle:
            row.set_subtitle(esc(subtitle))
        is_int = digits == 0 and isinstance(config.get(config.DEFAULTS, path), int)

        def changed(r, _p):
            if not self.win.loading:
                v = r.get_value()
                self.win.set(path, int(round(v)) if is_int else round(v, digits))
        row.connect("notify::value", changed)
        self.binders.append(lambda: row.set_value(float(self.win.get(path))))
        parent.add(row) if not isinstance(parent, Adw.ExpanderRow) else parent.add_row(row)
        return row

    def slider(self, parent, title, path, lo, hi, step, digits=2, subtitle=None, marks=(),
               formatter=None) -> Adw.ActionRow:
        row = Adw.ActionRow(title=esc(title))
        if subtitle:
            row.set_subtitle(esc(subtitle))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_digits(digits)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.LEFT)
        scale.set_size_request(280, -1)
        scale.set_valign(Gtk.Align.CENTER)
        if formatter:
            scale.set_format_value_func(lambda s, v: formatter(v))
        for value, label in marks:
            scale.add_mark(value, Gtk.PositionType.BOTTOM, label)

        def changed(s):
            if not self.win.loading:
                self.win.set(path, round(s.get_value(), max(digits, 2)))
        scale.connect("value-changed", changed)
        self.binders.append(lambda: scale.set_value(float(self.win.get(path))))
        row.add_suffix(scale)
        row.scale = scale
        parent.add(row) if not isinstance(parent, Adw.ExpanderRow) else parent.add_row(row)
        return row

    def button(self, label, callback, style=None, icon=None, tooltip=None) -> Gtk.Button:
        b = Gtk.Button(label=label) if not icon else Gtk.Button(icon_name=icon)
        if icon and label:
            content = Adw.ButtonContent(icon_name=icon, label=label)
            b.set_child(content)
        b.set_valign(Gtk.Align.CENTER)
        if style:
            for s in style.split():
                b.add_css_class(s)
        if tooltip:
            b.set_tooltip_text(tooltip)
        b.connect("clicked", lambda *_: callback())
        return b


# ============================================================================ overview
LEVEL_ICON = {"ok": ("emblem-ok-symbolic", "success"), "info": ("dialog-information-symbolic", "accent"),
              "warn": ("dialog-warning-symbolic", "warning"), "error": ("dialog-error-symbolic", "error")}


class OverviewPage(Page):
    id, title, icon = "overview", "Genel bakış", "padforge-overview-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        self.pads_group = self.group(page, "Touchpad", "Çekirdekten okunan donanım bilgisi")
        self.pad_rows: list = []

        prof = self.group(page, "Hızlı başlangıç",
                          "Bir profil seçin, sonra istediğiniz ayrıntıyı değiştirin. "
                          "Değişiklikler anında uygulanır.")
        box = Gtk.Box(spacing=12, homogeneous=True, margin_top=4)
        for name, title, desc in (
                ("windows", "Windows+", "Windows hassasiyeti, gecikmesiz dokunma, kinetik kaydırma, "
                                       "3/4 parmak hareketler"),
                ("macos", "macOS benzeri", "Yavaşta çok hassas, güçlü ivme, 3 parmakla sürükleme, "
                                           "parmak sayısıyla tıklama"),
                ("linux", "Linux varsayılanı", "libinput'un kendi davranışı; hareketler ve kinetik "
                                               "kaydırma kapalı")):
            btn = Gtk.Button()
            btn.add_css_class("card")
            btn.add_css_class("profile-card")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                            margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
            t = Gtk.Label(label=title, xalign=0)
            t.add_css_class("title-3")
            d = Gtk.Label(label=desc, xalign=0, wrap=True)
            d.add_css_class("dim-label")
            d.add_css_class("caption")
            inner.append(t)
            inner.append(d)
            btn.set_child(inner)
            btn.connect("clicked", lambda _b, n=name, tt=title: self.win.apply_profile(n, tt))
            box.append(btn)
        prof.add(box)

        self.checks_group = self.group(page, "Sağlık kontrolü",
                                       "Gecikme ve yanlış algılamanın bilinen nedenleri")
        refresh = self.button(None, self.run_checks, "flat", "view-refresh-symbolic", "Yenile")
        self.checks_group.set_header_suffix(refresh)
        self.check_rows: list = []
        self.run_checks()
        return page

    def status_changed(self, status):
        self._fill_pads(status)

    def shown(self):
        self.run_checks()

    def _fill_pads(self, status):
        for r in self.pad_rows:
            self.pads_group.remove(r)
        self.pad_rows = []
        stats = {p["node"]: p for p in (status or {}).get("pads", [])}
        for pad in device.list_touchpads():
            st = stats.get(pad.node, {}).get("stats", {})
            parts = [pad.bus_name, f"{pad.width_mm:.0f}×{pad.height_mm:.0f} mm",
                     f"{pad.max_fingers} parmak", ", ".join(pad.driver_chain()[:2])]
            if st.get("rate_hz"):
                parts.append(f"{st['rate_hz']:.0f} Hz")
            row = Adw.ActionRow(title=esc(pad.name), subtitle=esc(" · ".join(parts)))
            row.add_prefix(Gtk.Image(icon_name="padforge-overview-symbolic", pixel_size=24))
            badge = Gtk.Label(label="dahili" if pad.is_internal else "harici")
            badge.add_css_class("pill-label")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
            self.pads_group.add(row)
            self.pad_rows.append(row)
        if not self.pad_rows:
            row = Adw.ActionRow(title="Touchpad bulunamadı")
            self.pads_group.add(row)
            self.pad_rows.append(row)

    def run_checks(self):
        def work():
            return diagnostics.run_checks(service.daemon_status(), self.win.cfg)
        service.async_call(work, self._show_checks)

    def _show_checks(self, checks):
        if isinstance(checks, Exception):
            checks = [diagnostics.Check("error", "Kontrol başarısız", str(checks))]
        for r in self.check_rows:
            self.checks_group.remove(r)
        self.check_rows = []
        order = {"error": 0, "warn": 1, "info": 2, "ok": 3}
        for c in sorted(checks, key=lambda c: order[c.level]):
            row = Adw.ActionRow(title=esc(c.title), subtitle=esc(c.detail))
            icon, css = LEVEL_ICON[c.level]
            img = Gtk.Image(icon_name=icon)
            img.add_css_class(css)
            row.add_prefix(img)
            for widget in self.win.fix_buttons(c):
                row.add_suffix(widget)
            self.checks_group.add(row)
            self.check_rows.append(row)


# ============================================================================= pointer
class PointerPage(Page):
    id, title, icon = "pointer", "İmleç", "padforge-pointer-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        g = self.group(page, "İvme", "Parmak hızına göre imlecin ne kadar yol alacağı")
        self.profile_row = self.combo(g, "İvme profili", "pointer.profile", [
            ("custom", "PadForge eğrisi (önerilen)"),
            ("adaptive", "libinput uyarlanabilir"),
            ("flat", "libinput düz (ivmesiz)")])
        self.profile_row.connect("notify::selected", lambda *_: self._visibility())
        self.speed_row = self.slider(g, "libinput hızı", "pointer.speed", -1.0, 1.0, 0.01, 2,
                                     marks=[(0.0, None)])

        self.curve_group = self.group(page, "Hız eğrisi",
                                      "Noktaları sürükleyin · boş yere çift tıklayarak nokta "
                                      "ekleyin · sağ tıkla silin")
        self.preset_row = self.combo(self.curve_group, "Hazır eğri", "pointer.curve.preset",
                                     [(k, accel.PRESET_LABELS[k]) for k in
                                      ("windows", "macos", "linux", "precise", "custom")])
        self.preset_row.connect("notify::selected", lambda *_: self._load_curve())
        self.scale_row = self.slider(self.curve_group, "Genel hız", "pointer.curve.scale", 0.4, 2.5,
                                     0.01, 2, formatter=lambda v: f"{v:.2f}×",
                                     marks=[(1.0, None)])
        self.scale_row.scale.connect("value-changed", lambda *_: self._load_curve())
        self.editor = CurveEditor(on_change=self._curve_edited)
        frame = Gtk.Frame(child=self.editor, margin_top=8)
        frame.add_css_class("view")
        self.curve_group.add(frame)
        self.summary = Gtk.Label(xalign=0, wrap=True, margin_top=10)
        self.summary.add_css_class("dim-label")
        self.curve_group.add(self.summary)
        self.live_hint = Gtk.Label(xalign=0, wrap=True, margin_top=4)
        self.live_hint.add_css_class("caption")
        self.live_hint.add_css_class("dim-label")
        self.curve_group.add(self.live_hint)
        self.reader = None
        return page

    def after_refresh(self):
        self._visibility()
        self._load_curve()

    def _visibility(self):
        custom = self.win.get("pointer.profile") == "custom"
        self.curve_group.set_visible(custom)
        self.speed_row.set_visible(not custom)

    def _load_curve(self):
        c = self.win.get("pointer.curve")
        shown = getattr(self, "_shown_points", None)
        if c["preset"] == "custom" and len(c.get("points") or []) < 2 and shown:
            # "Özel eğri" picked from the list: start from the curve on screen.
            self.win.set("pointer.curve.points", [[v, g] for v, g in shown])
            return
        pts = accel.curve_points(c)
        self._shown_points = pts
        self.editor.set_curve(pts, c["scale"])
        pad_w = 118.0
        pads = device.list_touchpads()
        if pads and pads[0].width_mm:
            pad_w = pads[0].width_mm
        screen_w = 1920
        try:
            mon = self.widget.get_display().get_monitors().get_item(0)
            screen_w = mon.get_geometry().width * mon.get_scale_factor()
        except Exception:
            pass
        d = accel.describe(c, pad_w, screen_w)
        self.summary.set_label(
            f"Yavaş (20 mm/sn): {d['slow_px_per_mm']:.1f} px/mm — 1 piksel için "
            f"{d['precision_mm_per_px'] * 1000:.0f} µm parmak hareketi · "
            f"Orta (100): {d['medium_px_per_mm']:.1f} px/mm · Hızlı (400): {d['fast_px_per_mm']:.1f} px/mm · "
            f"Orta hızda ekranı geçmek: pad genişliğinin %{min(999, 100 * d['strokes_per_screen']):.0f}'i")

    def _curve_edited(self, points):
        # Go through win.set so the change is detected and saved.
        self.win.set("pointer.curve.points", [[float(v), float(g)] for v, g in points])
        self.win.set("pointer.curve.preset", "custom")

    def shown(self):
        pad = self.win.readable_pad()
        if pad is None:
            self.live_hint.set_label("Parmak hızı göstergesi için ham dokunma erişimi gerekir.")
            return
        try:
            self.reader = service.LiveReader(pad, self.win.cfg, self._frame, lambda *a, **k: None)
            self.live_hint.set_label("Touchpad'de parmağını gezdir: turuncu çizgi şu anki hızın.")
        except Exception as exc:
            self.live_hint.set_label(f"Canlı okuma açılamadı: {exc}")

    def _frame(self, f):
        if self.reader is not None:
            self.editor.set_marker(self.reader.finger_speed)

    def hidden(self):
        if self.reader:
            self.reader.close()
            self.reader = None
            self.editor.set_marker(None)


# ================================================================================= tap
class TapPage(Page):
    id, title, icon = "tap", "Dokunma ve tıklama", "padforge-tap-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        g = self.group(page, "Dokunarak tıklama")
        self.switch(g, "Dokunarak tıkla", "tap.enabled")
        self.engine = self.combo(g, "Dokunma motoru", "tap.engine", [
            ("padforge", "Gecikmesiz — PadForge (önerilen)"),
            ("libinput", "Standart — libinput")],
            subtitle="libinput, dokun-sürükle açıkken her tıklamayı ~180 ms bekletir. "
                     "PadForge parmak kalktığı anda tıklar.")
        self.combo(g, "Çok parmakla dokunma", "tap.button_map", [
            ("lrm", "2 parmak: sağ tık · 3 parmak: orta tık"),
            ("lmr", "2 parmak: orta tık · 3 parmak: sağ tık")])
        self.switch(g, "Dokun ve sürükle", "tap.drag",
                    "Dokun, hemen tekrar dokunup kaydır: sürükler (Windows'taki gibi)")
        self.switch(g, "Sürükleme kilidi", "tap.drag_lock",
                    "Sürüklerken parmağı kaldırıp yeniden konumlandırabilirsin; bitirmek için dokun")
        fine = Adw.ExpanderRow(title="İnce ayar",
                               subtitle="Dokunmanın ne zaman tıklama sayılacağı")
        g.add(fine)
        self.spin(fine, "En uzun dokunma süresi (ms)", "tap.max_duration_ms", 60, 400, 5)
        self.spin(fine, "En fazla kayma (mm)", "tap.max_move_mm", 0.3, 6.0, 0.1, 1)
        self.spin(fine, "Sürükleme için ikinci dokunma süresi (ms)", "tap.drag_window_ms", 80, 600, 10)
        self.spin(fine, "Sürükleme kilidi bekleme (ms)", "tap.drag_lock_ms", 100, 2000, 50)
        self.spin(fine, "Yazdıktan sonra dokunmayı yok say (ms)", "tap.typing_guard_ms", 0, 2000, 25,
                  subtitle="Yazarken avuç değmesi tıklama olmasın")

        g2 = self.group(page, "Fiziksel tıklama")
        self.combo(g2, "Sağ tık nasıl yapılır", "click.method", [
            ("buttonareas", "Sağ alt köşeye bas"),
            ("clickfinger", "İki parmakla bas (macOS gibi)")])
        self.switch(g2, "Orta tık öykünmesi", "click.middle_emulation",
                    "Sol ve sağa birlikte basınca orta tık")
        self.switch(g2, "Solak kullanım", "click.left_handed")

        g3 = self.group(page, "Yazarken")
        self.switch(g3, "Yazarken touchpad'i kapat", "typing.disable_while_typing",
                    "libinput her tuştan sonra 0,2–0,5 sn imleci kilitler. Yazdıktan hemen sonra "
                    "imleç tepki vermiyorsa kapatın.")
        return page


# ============================================================================== scroll
class ScrollPage(Page):
    id, title, icon = "scroll", "Kaydırma", "padforge-scroll-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        g = self.group(page, "Kaydırma")
        self.combo(g, "Yöntem", "scroll.method", [
            ("twofinger", "İki parmakla"), ("edge", "Kenardan"), ("none", "Kapalı")])
        self.switch(g, "Doğal kaydırma", "scroll.natural", "İçerik parmağı takip eder")
        self.switch(g, "Yatay kaydırma", "scroll.horizontal")
        self.slider(g, "Kaydırma hızı", "scroll.speed", 0.25, 3.0, 0.05, 2,
                    formatter=lambda v: f"{v:.2f}×", marks=[(1.0, None)])
        self.slider(g, "Kaydırma ivmesi", "scroll.acceleration", 0.0, 2.0, 0.05, 2,
                    subtitle="Hızlı kaydırınca daha uzağa gider (yalnızca PadForge eğrisiyle)",
                    marks=[(0.0, None)])
        k = self.group(page, "Kinetik kaydırma",
                       "X11 uygulamaları parmak kalkınca kaydırmayı kendileri sürdürmez. PadForge "
                       "parmağın kalkış hızını ölçer ve kaydırmayı yumuşakça yavaşlatarak sürdürür; "
                       "touchpad'e dokunmak veya bir tuşa basmak durdurur.")
        self.switch(k, "Kinetik kaydırma", "scroll.kinetic.enabled")
        self.slider(k, "Sürtünme", "scroll.kinetic.friction", 0.8, 8.0, 0.1, 1,
                    subtitle="Düşük = uzun süzülme", marks=[(2.6, None)])
        self.slider(k, "Fırlatma gücü", "scroll.kinetic.gain", 0.3, 2.5, 0.05, 2,
                    formatter=lambda v: f"{v:.2f}×", marks=[(1.0, None)])
        self.spin(k, "Başlama eşiği (mm/sn)", "scroll.kinetic.min_speed", 5, 500, 5,
                  subtitle="Parmak kalkarken bundan yavaşsa süzülme olmaz")
        self.spin(k, "En uzun süzülme (sn)", "scroll.kinetic.max_duration", 0.3, 15.0, 0.1, 1)
        return page


# ============================================================================ gestures
GESTURE_LABELS = {
    "swipe3_up": "Yukarı kaydır", "swipe3_down": "Aşağı kaydır", "swipe3_left": "Sola kaydır",
    "swipe3_right": "Sağa kaydır", "swipe4_up": "Yukarı kaydır", "swipe4_down": "Aşağı kaydır",
    "swipe4_left": "Sola kaydır", "swipe4_right": "Sağa kaydır",
    "pinch3_in": "3 parmak içe sıkıştır", "pinch3_out": "3 parmak dışa aç",
    "pinch4_in": "4 parmak içe sıkıştır", "pinch4_out": "4 parmak dışa aç",
    "tap4": "4 parmakla dokun",
}
ACTION_IDS = list(ACTIONS)
SHORTCUT_HINT = "Kısayol (ör. <Super>Page_Down veya Ctrl+Alt+T)"


class GesturesPage(Page):
    id, title, icon = "gestures", "Hareketler", "padforge-gesture-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        g = self.group(page, "Genel")
        self.switch(g, "Çok parmaklı hareketler", "gestures.enabled",
                    "GNOME'un X11 oturumunda touchpad hareketi yoktur; PadForge ekler")
        self.three_drag = self.switch(g, "3 parmakla sürükle", "gestures.three_finger_drag",
                                      "macOS gibi: 3 parmakla pencere/metin sürükle "
                                      "(3 parmak kaydırma hareketlerinin yerini alır)")
        self.switch(g, "2 parmak sıkıştırma = yakınlaştır", "gestures.pinch_zoom_2f",
                    "Ctrl+tekerlek gönderir (tarayıcı, belge, görüntü)")
        fine = Adw.ExpanderRow(title="Hassasiyet")
        g.add(fine)
        self.spin(fine, "Kaydırma eşiği (mm)", "gestures.swipe_threshold_mm", 3, 60, 0.5, 1,
                  subtitle="Hareketin tanınması için gereken yol")
        self.spin(fine, "Sürekli eylem adımı (mm)", "gestures.step_mm", 4, 80, 1, 0,
                  subtitle="Uygulama değiştirme, ses gibi eylemlerde her adım")
        self.slider(fine, "Sıkıştırma eşiği", "gestures.pinch_threshold", 0.05, 0.9, 0.01, 2,
                    formatter=lambda v: f"%{v * 100:.0f}")

        self.binding_rows: dict[str, tuple] = {}
        groups = [("3 parmak", ["swipe3_up", "swipe3_down", "swipe3_left", "swipe3_right"]),
                  ("4 parmak", ["swipe4_up", "swipe4_down", "swipe4_left", "swipe4_right"]),
                  ("Sıkıştırma", ["pinch3_in", "pinch3_out", "pinch4_in", "pinch4_out"]),
                  ("Dokunma", ["tap4"])]
        for title, keys in groups:
            grp = self.group(page, title)
            if title == "3 parmak":
                self.three_group = grp
                if touchegg.USER_CONF.exists():
                    grp.set_header_suffix(self.button("Touchégg'den aktar", self.win.import_touchegg,
                                                      "flat"))
            for key in keys:
                self._binding_row(grp, key)
        return page

    def _binding_row(self, grp, key):
        labels = [ACTIONS[a].label + ("  ⟳" if ACTIONS[a].continuous else "") for a in ACTION_IDS]
        row = Adw.ComboRow(title=esc(GESTURE_LABELS[key]), model=Gtk.StringList.new(labels),
                           enable_search=True)
        test = self.button(None, lambda k=key: self._test(k), "flat", "media-playback-start-symbolic",
                           "Bu eylemi şimdi çalıştır")
        row.add_suffix(test)
        entry = Adw.EntryRow(title=esc(SHORTCUT_HINT), show_apply_button=True)
        entry.set_visible(False)

        def changed(r, _p):
            if self.win.loading:
                return
            action = ACTION_IDS[r.get_selected()]
            b = {"action": action}
            old = self.win.get(f"gestures.bindings.{key}")
            if ACTIONS[action].needs_arg and old.get("action") == action and old.get("arg"):
                b["arg"] = old["arg"]
            self.win.set(f"gestures.bindings.{key}", b)
            self._entry_state(key)

        def applied(e):
            b = dict(self.win.get(f"gestures.bindings.{key}"))
            text = e.get_text().strip()
            if text:
                b["arg"] = text
            else:
                b.pop("arg", None)
            self.win.set(f"gestures.bindings.{key}", b)
            self.win.toast("Kaydedildi")

        def load():
            b = self.win.get(f"gestures.bindings.{key}")
            a = b.get("action", "none")
            row.set_selected(ACTION_IDS.index(a) if a in ACTION_IDS else 0)
            entry.set_text(b.get("arg", ""))
            self._entry_state(key)
        row.connect("notify::selected", changed)
        entry.connect("apply", applied)
        self.binders.append(load)
        grp.add(row)
        grp.add(entry)
        self.binding_rows[key] = (row, entry)

    def _entry_state(self, key):
        row, entry = self.binding_rows[key]
        b = self.win.get(f"gestures.bindings.{key}")
        info = ACTIONS.get(b.get("action", "none"))
        needs = bool(info and info.needs_arg)
        entry.set_visible(needs)
        if needs:
            entry.set_title(esc("Komut (ör. gnome-terminal)" if b["action"] == "command"
                                else SHORTCUT_HINT))

    def after_refresh(self):
        drag = self.win.get("gestures.three_finger_drag")
        for key in ("swipe3_up", "swipe3_down", "swipe3_left", "swipe3_right"):
            self.binding_rows[key][0].set_sensitive(not drag)
            self.binding_rows[key][0].set_subtitle("3 parmakla sürükleme açık" if drag else "")

    def _test(self, key):
        b = self.win.get(f"gestures.bindings.{key}")
        self.win.run_action(b.get("action", "none"), b.get("arg"))


# ================================================================================ live
class LivePage(Page):
    id, title, icon = "live", "Canlı test", "padforge-live-symbolic"

    def build(self):
        self.stack = Gtk.Stack()
        # -- no access
        status = Adw.StatusPage(icon_name="padforge-live-symbolic",
                                title="Ham dokunma verisine erişim yok",
                                description="Canlı görünüm, hareketler, gecikmesiz dokunma ve kinetik "
                                            "kaydırma touchpad'in ham verisini okumayı gerektirir. "
                                            "Bir kez yönetici olarak kurun:")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        cmd = f"sudo bash {diagnostics.helper_script()}"
        lbl = Gtk.Label(label=cmd, selectable=True, wrap=True)
        lbl.add_css_class("monospace")
        lbl.add_css_class("card")
        lbl.set_margin_bottom(6)
        box.append(lbl)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        btns.append(self.button("Yönetici olarak kur", lambda: self.win.run_helper([]),
                                "suggested-action pill"))
        btns.append(self.button("Komutu kopyala", lambda: self.win.copy(cmd), "pill"))
        btns.append(self.button("Demoyu göster", self.start_demo, "pill"))
        box.append(btns)
        status.set_child(box)
        self.stack.add_named(status, "noaccess")

        # -- live view
        paned = Gtk.Box(spacing=0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=6, hexpand=True)
        self.view = TouchView()
        self.view.set_size_request(420, 280)
        left.append(self.view)
        self.hint = Gtk.Label(xalign=0, wrap=True, margin_top=8)
        self.hint.add_css_class("dim-label")
        self.hint.add_css_class("caption")
        left.append(self.hint)
        paned.append(left)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12,
                       margin_bottom=12, margin_start=6, margin_end=12)
        stats_group = Adw.PreferencesGroup(title="Ölçümler")
        self.stat_rows = {}
        for key, title in (("fingers", "Parmak"), ("rate", "Rapor hızı"), ("jitter", "Aralık sapması"),
                           ("wake", "İlk rapor gecikmesi"), ("noise", "Duran parmak titreşimi"),
                           ("latency", "Çekirdek → uygulama"), ("frames", "Kare / dokunuş")):
            row = Adw.ActionRow(title=title)
            val = Gtk.Label(label="—")
            val.add_css_class("numeric")
            row.add_suffix(val)
            stats_group.add(row)
            self.stat_rows[key] = val
        side.append(stats_group)
        self.run_switch = Adw.SwitchRow(title="Eylemleri çalıştır",
                                        subtitle="Kapalıyken yalnızca burada gösterilir")
        sw_group = Adw.PreferencesGroup()
        sw_group.add(self.run_switch)
        self.run_switch.connect("notify::active", lambda *_: self._pause_state())
        side.append(sw_group)
        log_title = Gtk.Label(label="Tanınanlar", xalign=0)
        log_title.add_css_class("heading")
        side.append(log_title)
        self.log = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.log.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.log)
        scroller.set_min_content_height(120)
        side.append(scroller)
        side.append(self.button("Ölçümleri sıfırla", self.reset, "pill"))
        side_scroll = Gtk.ScrolledWindow(child=side, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_width=False, hexpand=False)
        side_scroll.set_size_request(340, -1)
        paned.append(side_scroll)
        self.stack.add_named(paned, "live")
        self.reader = None
        self.pause = service.PauseHandle()
        self._timer = 0
        self._demo = 0
        return self.stack

    def shown(self):
        pad = self.win.readable_pad()
        if pad is None:
            if not self._demo:
                self.stack.set_visible_child_name("noaccess")
            return
        self.stop_demo()
        self.stack.set_visible_child_name("live")
        try:
            self.reader = service.LiveReader(pad, self.win.cfg, self._frame, self._event)
        except Exception as exc:
            self.hint.set_label(f"Açılamadı: {exc}")
            return
        self.view.set_size_mm(self.reader.reader.axis_x.size_mm, self.reader.reader.axis_y.size_mm)
        self.hint.set_label(f"{pad.name} · {self.reader.reader.resolution:g} birim/mm · "
                            "parmaklarını koy, kaydır, sıkıştır; sabit tutarak titreşimi ölç")
        self._pause_state()
        self._timer = GLib.timeout_add(400, self._update_stats)

    def hidden(self):
        if self.reader:
            self.reader.close()
            self.reader = None
        self.pause.release()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.stop_demo()

    def _pause_state(self):
        if self.run_switch.get_active():
            self.pause.release()
        elif self.reader is not None:
            self.pause.acquire()

    def _frame(self, f):
        self.view.push(f)

    def _event(self, text, flash=None):
        row = Gtk.Label(label=f"{time.strftime('%H:%M:%S')}  {text}", xalign=0, wrap=True,
                        wrap_mode=2, max_width_chars=34,
                        margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
        row.add_css_class("caption")
        self.log.prepend(row)
        child = self.log.get_row_at_index(40)
        if child is not None:
            self.log.remove(child)
        if flash:
            self.view.flash(flash)

    def _update_stats(self):
        if not self.reader:
            return False
        s = self.reader.stats.summary(self.reader.reader.parser.dropped_count)
        f = self.view.frame
        self.stat_rows["fingers"].set_label(f"{f.count if f else 0} (en çok {s.max_fingers})")
        self.stat_rows["rate"].set_label(fmt(s.rate_hz, " Hz", 0))
        jit = fmt(s.interval_jitter_ms, " ms", 2)
        if s.gap_percent is not None:
            jit += f" · boşluk %{s.gap_percent:.1f}"
        self.stat_rows["jitter"].set_label(jit)
        wake = fmt(s.wake_delay_ms, " ms", 1)
        if s.wake_delay_max_ms is not None:
            wake += f" (en çok {s.wake_delay_max_ms:.1f})"
        self.stat_rows["wake"].set_label(wake)
        self.stat_rows["noise"].set_label(fmt(s.noise_mm, " mm", 3))
        self.stat_rows["latency"].set_label(fmt(s.read_latency_ms, " ms", 2))
        self.stat_rows["frames"].set_label(f"{s.frames} / {s.sessions}")
        return True

    def reset(self):
        if self.reader:
            from ..stats import TouchStats
            self.reader.stats = TouchStats()
            self._update_stats()

    # -- demo (no hardware access, and README screenshots) ----------------------------
    def start_demo(self):
        from ..mt import Frame, Touch
        self.stack.set_visible_child_name("live")
        self.hint.set_label("Demo: sentetik 3 parmak kaydırma. Gerçek veri için erişimi kurun.")
        self.view.set_size_mm(118, 72)
        t0 = time.monotonic()

        def step():
            t = time.monotonic() - t0
            phase = t % 2.4
            touches = {}
            if phase < 1.6:
                p = min(1.0, phase / 1.2)
                for i in range(3):
                    x = 38 + 16 * i
                    y = 52 - 30 * (p * p * (3 - 2 * p))
                    touches[i] = Touch(i, 100 + int(t // 2.4) * 3 + i, x, y, t0, x, 52)
            self.view.push(Frame(time.monotonic(), None, touches, len(touches), False))
            if 0.75 < phase < 0.8 and not getattr(self, "_flashed", False):
                self._flashed = True
                self.view.flash("3↑  Etkinlikler (genel bakış)")
                self._event("3 parmak kaydırma ↑ → Etkinlikler (genel bakış)")
            if phase > 1.6:
                self._flashed = False
            return True
        self.stop_demo()
        self._demo = GLib.timeout_add(16, step)

    def stop_demo(self):
        if self._demo:
            GLib.source_remove(self._demo)
            self._demo = 0


# ============================================================================ advanced
class AdvancedPage(Page):
    id, title, icon = "advanced", "Gelişmiş", "padforge-advanced-symbolic"

    def build(self):
        page = Adw.PreferencesPage()
        g = self.group(page, "Arka plan servisi",
                       "Ayarları uygular, hareketleri ve kinetik kaydırmayı çalıştırır")
        self.svc_row = Adw.ActionRow(title="Durum", subtitle="…")
        self.svc_row.add_suffix(self.button("Yeniden başlat", lambda: self._svc("restart"), "flat"))
        self.svc_toggle = self.button("Başlat", lambda: self._svc("start"), "flat")
        self.svc_row.add_suffix(self.svc_toggle)
        g.add(self.svc_row)
        self.autostart = Adw.SwitchRow(title="Oturum açılınca başlat")
        self.autostart.connect("notify::active", self._autostart_changed)
        g.add(self.autostart)
        logs = Adw.ActionRow(title="Servis günlüğü")
        logs.add_suffix(self.button("Göster", self._show_journal, "flat"))
        g.add(logs)

        d = self.group(page, "Aygıt")
        names = [("auto", "Tüm touchpad'ler")] + [(p.name, p.name) for p in device.list_touchpads()]
        self.combo(d, "Ayarların uygulanacağı touchpad", "device.match", names)

        fx = self.group(page, "Sistem düzeltmeleri", "Yönetici şifresi isteyen tek seferlik adımlar")
        self.fix_rows = []
        self.fix_group = fx

        f = self.group(page, "Ayar dosyası", str(config.config_path()))
        row = Adw.ActionRow(title="Yedekle veya geri yükle")
        row.add_suffix(self.button("Dışa aktar", self._export, "flat", "document-save-symbolic"))
        row.add_suffix(self.button("İçe aktar", self._import, "flat", "document-open-symbolic"))
        f.add(row)
        reset = Adw.ActionRow(title="Varsayılanlara dön", subtitle="Windows+ profili")
        reset.add_suffix(self.button("Sıfırla", self._reset, "flat destructive-action"))
        f.add(reset)

        a = self.group(page, "Hakkında")
        about = Adw.ActionRow(title=f"PadForge {__version__}",
                              subtitle="X11 için touchpad ayarları, hareketler ve teşhis · MIT lisansı")
        about.add_suffix(self.button("Kendi kendini test et", self._selftest, "flat"))
        a.add(about)
        return page

    def shown(self):
        self._refresh_service()
        self._refresh_fixes()

    def status_changed(self, status):
        self._refresh_service(status)

    def _refresh_service(self, status=None):
        def done(st):
            if isinstance(st, Exception):
                return
            self.win.loading += 1
            try:
                self.autostart.set_active(st["enabled"])
            finally:
                self.win.loading -= 1
            running = st["active"] or self.win.status is not None
            sub = "Çalışıyor" if running else ("Durdu" if st["installed"] else
                                              "Kurulu değil — install.sh çalıştırın")
            if self.win.status:
                sub += f" · pid {self.win.status.get('pid')} · " \
                       f"{len(self.win.status.get('pads', []))} touchpad"
            self.svc_row.set_subtitle(esc(sub))
            self.svc_toggle.set_label("Durdur" if running else "Başlat")
            self._running = running
        service.async_call(service.unit_state, done)

    def _svc(self, what):
        if what == "start" and getattr(self, "_running", False):
            what = "stop"
        service.async_call(lambda: service.systemctl(what, service.UNIT),
                           lambda r: (self.win.toast(r[1] if r[0] else "Tamam"),
                                      GLib.timeout_add(700, lambda: (self.win.poll_status(), False)[1])))

    def _autostart_changed(self, row, _p):
        if self.win.loading:
            return
        verb = "enable" if row.get_active() else "disable"
        service.async_call(lambda: service.systemctl(verb, service.UNIT),
                           lambda r: self.win.toast(r[1] if r[0] else "Tamam"))

    def _refresh_fixes(self):
        for r in self.fix_rows:
            self.fix_group.remove(r)
        self.fix_rows = []
        pads = device.list_touchpads()
        readable = all(p.readable for p in pads) if pads else False
        row = Adw.ActionRow(title="Touchpad ham veri erişimi",
                            subtitle="Kurulu" if readable else "Kurulu değil (hareketler için gerekli)")
        if not readable:
            row.add_suffix(self.button("Yönetici olarak kur", lambda: self.win.run_helper([]),
                                       "suggested-action"))
        else:
            row.add_suffix(self.button("Kaldır", lambda: self.win.run_helper(["--remove"]), "flat"))
        self.fix_group.add(row)
        self.fix_rows.append(row)
        sleepy = any(ctl == "auto" for p in pads for _, _, ctl in p.power_parents())
        row = Adw.ActionRow(title="I2C denetleyici uykusu",
                            subtitle="Açık: ilk dokunuşta gecikme olabilir" if sleepy
                            else "Kapalı (ilk dokunuş gecikmesiz)")
        if sleepy:
            row.add_suffix(self.button("Kapat", lambda: self.win.run_helper(["--power-fix"]), "flat"))
        self.fix_group.add(row)
        self.fix_rows.append(row)
        if touchegg.USER_CONF.exists() or touchegg.client_running():
            on = touchegg.client_running() and not touchegg.autostart_disabled()
            row = Adw.ActionRow(title="Touchégg",
                                subtitle="Çalışıyor — PadForge ile çakışır" if on else "Kapalı")
            if on:
                row.add_suffix(self.button("Aktar ve kapat", self.win.import_touchegg, "flat"))
            else:
                row.add_suffix(self.button("Yeniden aç", self._touchegg_on, "flat"))
            self.fix_group.add(row)
            self.fix_rows.append(row)

    def _touchegg_on(self):
        touchegg.enable_client()
        self.win.toast("Touchégg yeniden açıldı")
        self._refresh_fixes()

    def _show_journal(self):
        service.async_call(service.journal, lambda text: self.win.text_dialog("Servis günlüğü", text))

    def _selftest(self):
        import subprocess
        import sys

        def work():
            p = subprocess.run([sys.executable, "-m", "padforge", "selftest"], capture_output=True,
                               text=True, timeout=120, cwd=os.path.dirname(os.path.dirname(
                                   os.path.dirname(os.path.abspath(__file__)))))
            return p.stdout + p.stderr
        self.win.toast("Test çalışıyor… (~15 sn)")
        service.async_call(work, lambda out: self.win.text_dialog("Kendi kendini test", str(out)))

    def _export(self):
        dlg = Gtk.FileDialog(title="Ayarları dışa aktar", initial_name="padforge-ayarlar.json")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            config.save(self.win.cfg, Path(f.get_path()))
            self.win.toast("Dışa aktarıldı")
        dlg.save(self.win, None, done)

    def _import(self):
        dlg = Gtk.FileDialog(title="Ayarları içe aktar")

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            self.win.replace_config(config.load(Path(f.get_path())))
            self.win.toast("İçe aktarıldı")
        dlg.open(self.win, None, done)

    def _reset(self):
        self.win.replace_config(config.defaults())
        self.win.toast("Varsayılanlara dönüldü")


PAGES = [OverviewPage, PointerPage, TapPage, ScrollPage, GesturesPage, LivePage, AdvancedPage]
