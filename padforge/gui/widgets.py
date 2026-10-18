"""Custom drawing widgets: the acceleration curve editor and the live touch view."""
from __future__ import annotations

import math
import time
from collections import deque

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from .. import accel  # noqa: E402

VMAX = 800.0          # mm/s shown on the x axis
SLOT_COLORS = [(0.21, 0.52, 0.89), (0.90, 0.38, 0.0), (0.18, 0.62, 0.31),
               (0.57, 0.25, 0.67), (0.75, 0.11, 0.16), (0.39, 0.39, 0.39)]


def _fg(widget) -> tuple[float, float, float]:
    c = widget.get_color()
    return c.red, c.green, c.blue


def _accent(widget) -> tuple[float, float, float]:
    try:                                   # libadwaita >= 1.6: system accent colour
        from gi.repository import Adw
        c = Adw.StyleManager.get_default().get_accent_color_rgba()
        return c.red, c.green, c.blue
    except Exception:
        pass
    try:
        ok, c = widget.get_style_context().lookup_color("accent_bg_color")
        if ok:
            return c.red, c.green, c.blue
    except Exception:
        pass
    return 0.21, 0.52, 0.89


def _text(widget, cr, text: str, x: float, y: float, size: float = 9.5, bold: bool = False,
          align: str = "left", valign: str = "baseline") -> tuple[float, float]:
    """Draw text with the GTK font (Pango handles fallback glyphs such as arrows)."""
    layout = widget.create_pango_layout(text)
    desc = widget.get_pango_context().get_font_description().copy()
    desc.set_size(int(size * Pango.SCALE))
    if bold:
        desc.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(desc)
    _ink, logical = layout.get_pixel_extents()
    w, h = logical.width, logical.height
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    if valign == "baseline":
        y -= layout.get_baseline() / Pango.SCALE
    elif valign == "middle":
        y -= h / 2
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    return w, h


class CurveEditor(Gtk.DrawingArea):
    """Drag control points of the gain curve (x: finger speed, y: px per mm).

    Double-click adds a point, right-click removes one. ``on_change(points)``
    receives the new list of (mm/s, px/mm) when the user edits.
    """

    def __init__(self, on_change=None):
        super().__init__()
        self.set_content_height(300)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        self.points: list[tuple[float, float]] = list(accel.PRESETS["windows"])
        self.scale = 1.0
        self.reference = accel.MonotoneCurve(accel.PRESETS["linux"])
        self.on_change = on_change
        self.marker_speed: float | None = None
        self._drag: int | None = None
        self._hover: int | None = None
        self._ymax = 60.0
        self._margin = (46, 14, 14, 34)   # left, top, right, bottom

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._clicked)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)
        self.set_cursor_from_name("crosshair")

    # -- mapping ---------------------------------------------------------------
    def _area(self):
        w, h = self.get_width(), self.get_height()
        ml, mt, mr, mb = self._margin
        return ml, mt, max(10, w - ml - mr), max(10, h - mt - mb)

    def _to_px(self, v: float, g: float) -> tuple[float, float]:
        x0, y0, w, h = self._area()
        return (x0 + math.sqrt(max(0.0, v) / VMAX) * w, y0 + h - (g / self._ymax) * h)

    def _from_px(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, w, h = self._area()
        fx = min(1.0, max(0.0, (x - x0) / w))
        return fx * fx * VMAX, max(0.0, (y0 + h - y) / h * self._ymax)

    def set_curve(self, points, scale: float) -> None:
        self.points = sorted((float(v), float(g)) for v, g in points)
        self.scale = scale
        top = max(g for _, g in self.points) * scale
        self._ymax = max(30.0, math.ceil(top * 1.25 / 10) * 10)
        self.queue_draw()

    def set_marker(self, speed: float | None) -> None:
        self.marker_speed = speed
        self.queue_draw()

    # -- interaction ---------------------------------------------------------------
    def _nearest(self, x: float, y: float, radius: float = 14.0) -> int | None:
        best, best_d = None, radius
        for i, (v, g) in enumerate(self.points):
            px, py = self._to_px(v, g * self.scale)
            d = math.hypot(px - x, py - y)
            if d <= best_d:
                best, best_d = i, d
        return best

    def _motion(self, ctl, x, y):
        idx = self._nearest(x, y)
        if idx != self._hover:
            self._hover = idx
            self.set_cursor_from_name("grab" if idx is not None else "crosshair")
            self.queue_draw()

    def _drag_begin(self, gesture, x, y):
        self._drag = self._nearest(x, y)
        self._start = (x, y)
        if self._drag is not None:
            self.set_cursor_from_name("grabbing")

    def _drag_update(self, gesture, dx, dy):
        if self._drag is None:
            return
        x, y = self._start[0] + dx, self._start[1] + dy
        v, g = self._from_px(x, y)
        i = self._drag
        lo = self.points[i - 1][0] + 1.0 if i > 0 else 0.0
        hi = self.points[i + 1][0] - 1.0 if i < len(self.points) - 1 else VMAX * 1.25
        if i == 0:
            v = 0.0                                      # the first point anchors v = 0
        v = min(hi, max(lo, v))
        self.points[i] = (round(v, 1), round(max(0.5, g / self.scale), 2))
        self.queue_draw()

    def _drag_end(self, gesture, dx, dy):
        if self._drag is not None and (abs(dx) > 1 or abs(dy) > 1):
            self._emit()
        self._drag = None
        self.set_cursor_from_name("crosshair")

    def _clicked(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        idx = self._nearest(x, y)
        if button == Gdk.BUTTON_SECONDARY and idx is not None and len(self.points) > 2 and idx > 0:
            del self.points[idx]
            self._emit()
        elif button == Gdk.BUTTON_PRIMARY and n_press == 2 and idx is None:
            v, g = self._from_px(x, y)
            if v > 1.0:
                self.points.append((round(v, 1), round(g / self.scale, 2)))
                self.points.sort()
                self._emit()
        self.queue_draw()

    def _emit(self) -> None:
        if self.on_change:
            self.on_change([[v, g] for v, g in self.points])

    # -- drawing ---------------------------------------------------------------------
    def _draw(self, area, cr, width, height):
        fg = _fg(self)
        acc = _accent(self)
        x0, y0, w, h = self._area()
        cr.set_line_width(1)
        # grid + labels
        for v in (0, 25, 50, 100, 200, 400, 800):
            px, _ = self._to_px(v, 0)
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(px + 0.5, y0)
            cr.line_to(px + 0.5, y0 + h)
            cr.stroke()
            cr.set_source_rgba(*fg, 0.55)
            _text(self, cr, f"{v}", px, y0 + h + 15, align="center")
        step = 10 if self._ymax <= 80 else 20
        g = 0
        while g <= self._ymax + 0.1:
            _, py = self._to_px(0, g)
            cr.set_source_rgba(*fg, 0.10)
            cr.move_to(x0, py + 0.5)
            cr.line_to(x0 + w, py + 0.5)
            cr.stroke()
            cr.set_source_rgba(*fg, 0.55)
            _text(self, cr, f"{g}", x0 - 8, py, align="right", valign="middle")
            g += step
        cr.set_source_rgba(*fg, 0.55)
        _text(self, cr, "parmak hızı (mm/sn) →", x0 + w, y0 + h + 30, align="right")
        cr.save()
        cr.translate(14, y0 + h / 2)
        cr.rotate(-math.pi / 2)
        _text(self, cr, "piksel / mm →", 0, 0, align="center", valign="middle")
        cr.restore()

        # reference: stock libinput (measured)
        cr.set_source_rgba(*fg, 0.35)
        cr.set_dash([4, 4])
        cr.set_line_width(1.5)
        for i in range(0, 161):
            v = (i / 160) ** 2 * VMAX
            px, py = self._to_px(v, self.reference(v))
            (cr.move_to if i == 0 else cr.line_to)(px, py)
        cr.stroke()
        cr.set_dash([])

        # the curve
        curve = accel.MonotoneCurve(self.points)
        cr.set_source_rgb(*acc)
        cr.set_line_width(2.5)
        for i in range(0, 241):
            v = (i / 240) ** 2 * VMAX
            px, py = self._to_px(v, curve(v) * self.scale)
            (cr.move_to if i == 0 else cr.line_to)(px, py)
        cr.stroke()

        # live finger speed marker
        if self.marker_speed is not None:
            v = min(VMAX, self.marker_speed)
            px, py = self._to_px(v, curve(v) * self.scale)
            cr.set_source_rgba(0.90, 0.38, 0.0, 0.85)
            cr.set_line_width(1.5)
            cr.move_to(px, y0)
            cr.line_to(px, y0 + h)
            cr.stroke()
            cr.arc(px, py, 5, 0, 2 * math.pi)
            cr.fill()

        # control points
        for i, (v, g) in enumerate(self.points):
            px, py = self._to_px(v, g * self.scale)
            r = 7 if i in (self._hover, self._drag) else 5.5
            cr.arc(px, py, r, 0, 2 * math.pi)
            cr.set_source_rgb(*acc)
            cr.fill_preserve()
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2)
            cr.stroke()
            if i in (self._hover, self._drag):
                cr.set_source_rgba(*fg, 0.9)
                txt = f"{v:.0f} mm/sn → {g * self.scale:.1f} px/mm"
                tx = min(max(x0 + 70, px), x0 + w - 70)
                _text(self, cr, txt, tx, max(y0 + 14, py - 12), bold=True, align="center")

        # legend
        lx, ly = x0 + 12, y0 + 16
        cr.set_source_rgb(*acc)
        cr.set_line_width(2.5)
        cr.move_to(lx, ly - 4)
        cr.line_to(lx + 18, ly - 4)
        cr.stroke()
        cr.set_source_rgba(*fg, 0.8)
        _text(self, cr, "PadForge eğrisi", lx + 24, ly - 4, valign="middle")
        cr.set_source_rgba(*fg, 0.35)
        cr.set_dash([4, 4])
        cr.set_line_width(1.5)
        cr.move_to(lx, ly + 12)
        cr.line_to(lx + 18, ly + 12)
        cr.stroke()
        cr.set_dash([])
        cr.set_source_rgba(*fg, 0.8)
        _text(self, cr, "libinput varsayılanı (bu makinede ölçülen)", lx + 24, ly + 12,
              valign="middle")


class TouchView(Gtk.DrawingArea):
    """Live drawing of the touchpad surface, true to scale."""

    TRAIL_S = 0.6

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_content_height(320)
        self.set_draw_func(self._draw)
        self.width_mm, self.height_mm = 118.0, 72.0
        self.frame = None
        self.trails: dict[int, deque] = {}
        self.label = ""
        self.label_time = 0.0

    def set_size_mm(self, w: float, h: float) -> None:
        if w > 0 and h > 0:
            self.width_mm, self.height_mm = w, h
            self.queue_draw()

    def push(self, frame) -> None:
        self.frame = frame
        now = frame.time
        for t in frame.touches.values():
            trail = self.trails.setdefault(t.tracking_id, deque())
            trail.append((now, t.x, t.y))
        for tid in list(self.trails):
            trail = self.trails[tid]
            while trail and now - trail[0][0] > self.TRAIL_S:
                trail.popleft()
            if not trail:
                del self.trails[tid]
        self.queue_draw()

    def flash(self, text: str) -> None:
        self.label = text
        self.label_time = time.monotonic()
        if not getattr(self, "_ticking", False):
            self._ticking = True
            self.add_tick_callback(self._fade)
        self.queue_draw()

    def _fade(self, widget, clock) -> bool:
        self.queue_draw()
        if time.monotonic() - self.label_time > 1.7:
            self._ticking = False
            return False
        return True

    def _rect(self, width, height):
        m = 16
        aw, ah = width - 2 * m, height - 2 * m
        s = min(aw / self.width_mm, ah / self.height_mm)
        w, h = self.width_mm * s, self.height_mm * s
        return (width - w) / 2, (height - h) / 2, w, h, s

    def _draw(self, area, cr, width, height):
        fg = _fg(self)
        acc = _accent(self)
        x0, y0, w, h, s = self._rect(width, height)
        r = 14
        cr.new_sub_path()
        cr.arc(x0 + w - r, y0 + r, r, -math.pi / 2, 0)
        cr.arc(x0 + w - r, y0 + h - r, r, 0, math.pi / 2)
        cr.arc(x0 + r, y0 + h - r, r, math.pi / 2, math.pi)
        cr.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        pressed = self.frame is not None and self.frame.button
        cr.set_source_rgba(*acc, 0.16 if pressed else 0.07)
        cr.fill_preserve()
        cr.set_source_rgba(*(acc if pressed else fg), 0.9 if pressed else 0.25)
        cr.set_line_width(2 if pressed else 1.2)
        cr.stroke()
        # 10 mm grid
        cr.set_source_rgba(*fg, 0.06)
        cr.set_line_width(1)
        mm = 10
        while mm < self.width_mm:
            cr.move_to(x0 + mm * s + 0.5, y0 + 4)
            cr.line_to(x0 + mm * s + 0.5, y0 + h - 4)
            mm += 10
        mm = 10
        while mm < self.height_mm:
            cr.move_to(x0 + 4, y0 + mm * s + 0.5)
            cr.line_to(x0 + w - 4, y0 + mm * s + 0.5)
            mm += 10
        cr.stroke()
        cr.set_source_rgba(*fg, 0.45)
        _text(self, cr, f"{self.width_mm:.0f} × {self.height_mm:.0f} mm", x0 + 12, y0 + h - 10)

        if self.frame is None:
            cr.set_source_rgba(*fg, 0.5)
            _text(self, cr, "Touchpad'e dokun…", x0 + w / 2, y0 + h / 2, size=14,
                  align="center", valign="middle")
            return

        # trails
        for tid, trail in self.trails.items():
            if len(trail) < 2:
                continue
            col = SLOT_COLORS[tid % len(SLOT_COLORS)]
            cr.set_line_width(3)
            for (t1, xa, ya), (t2, xb, yb) in zip(trail, list(trail)[1:]):
                age = (self.frame.time - t2) / self.TRAIL_S
                cr.set_source_rgba(*col, max(0.0, 0.6 * (1 - age)))
                cr.move_to(x0 + xa * s, y0 + ya * s)
                cr.line_to(x0 + xb * s, y0 + yb * s)
                cr.stroke()
        # fingers
        for t in self.frame.touches.values():
            col = (0.85, 0.2, 0.2) if t.palm else SLOT_COLORS[t.tracking_id % len(SLOT_COLORS)]
            cx, cy = x0 + t.x * s, y0 + t.y * s
            rad = max(10.0, 5.5 * s)
            cr.arc(cx, cy, rad, 0, 2 * math.pi)
            cr.set_source_rgba(*col, 0.28)
            cr.fill_preserve()
            cr.set_source_rgba(*col, 0.95)
            cr.set_line_width(2.5)
            cr.stroke()
            cr.set_source_rgba(*col, 1)
            label = "avuç" if t.palm else str(t.slot + 1)
            _text(self, cr, label, cx, cy, size=10.5, bold=True, align="center", valign="middle")
        # recognised gesture flash
        if self.label and time.monotonic() - self.label_time < 1.6:
            alpha = 1.0 - (time.monotonic() - self.label_time) / 1.6
            layout = self.create_pango_layout(self.label)
            desc = self.get_pango_context().get_font_description().copy()
            desc.set_size(int(15 * Pango.SCALE))
            desc.set_weight(Pango.Weight.BOLD)
            layout.set_font_description(desc)
            _ink, log = layout.get_pixel_extents()
            bw, bh = log.width + 28, log.height + 14
            bx, by = x0 + (w - bw) / 2, y0 + 14
            r = bh / 2
            cr.new_sub_path()
            cr.arc(bx + bw - r, by + r, r, -math.pi / 2, math.pi / 2)
            cr.arc(bx + r, by + r, r, math.pi / 2, 3 * math.pi / 2)
            cr.close_path()
            cr.set_source_rgba(*acc, 0.92 * alpha)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, alpha)
            cr.move_to(bx + 14, by + 7)
            PangoCairo.show_layout(cr, layout)
