"""Pointer acceleration curves and their libinput "custom" profile encoding.

A curve is a list of control points ``(v, g)``: finger speed ``v`` in mm/s and
cursor gain ``g`` in screen pixels per millimetre of finger travel. Between the
control points the gain is interpolated with a monotone cubic (Fritsch-Carlson),
so a non-decreasing set of points never produces overshoot.

libinput's custom profile wants something else: ``f(u)`` sampled at
``u = i * step`` where ``u`` is the input speed in *device units per ms* and
``f`` the output speed in pixels per ms. Measured on xf86-input-libinput 1.4 /
libinput 1.25 with a virtual touchpad: ``f(u) = u`` moves the cursor exactly one
pixel per device unit, i.e. no 1000-dpi normalisation and no touchpad
"magic slowdown" is applied in this mode. Hence

    u = v * res / 1000,   f = g(v) * v / 1000

with ``res`` the touchpad resolution in units/mm.
"""
from __future__ import annotations

import bisect
import math
from typing import Sequence

Point = tuple[float, float]

# libinput limits (filter-custom.c)
LIBINPUT_MAX_POINTS = 64
LIBINPUT_MAX_VALUE = 10000.0

# Stock libinput 2-finger scroll distance: 1000 dpi normalisation * TP_MAGIC_SLOWDOWN.
LIBINPUT_SCROLL_PX_PER_MM = 1000.0 / 25.4 * 0.2968

PRESETS: dict[str, list[Point]] = {
    # Precise when slow, ramps smoothly, saturates; close to the Windows
    # Precision Touchpad feel at the default sensitivity.
    "windows": [(0, 7.0), (25, 10.0), (60, 16.0), (120, 24.0), (250, 34.0), (500, 44.0), (1000, 50.0)],
    # Slow and very precise at low speed, strong acceleration for flicks.
    "macos": [(0, 4.5), (30, 6.5), (80, 13.0), (160, 25.0), (320, 44.0), (640, 68.0), (1000, 80.0)],
    # Measured libinput "adaptive" at speed 0.38 on this machine (for comparison).
    "linux": [(0, 17.5), (20, 18.3), (60, 21.2), (150, 22.5), (250, 36.0), (400, 62.8), (1000, 70.0)],
    # No acceleration at all.
    "precise": [(0, 14.0), (1000, 14.0)],
}

PRESET_LABELS = {
    "windows": "Windows benzeri",
    "macos": "macOS benzeri",
    "linux": "Linux varsayılanı (ölçülen)",
    "precise": "Düz (ivmesiz)",
    "custom": "Özel eğri",
}


def curve_points(curve_cfg: dict) -> list[Point]:
    """Control points for a ``pointer.curve`` config section (without scale)."""
    preset = curve_cfg.get("preset", "windows")
    if preset == "custom":
        pts = [tuple(p) for p in curve_cfg.get("points", [])]
        if len(pts) >= 2:
            return sorted((float(v), float(g)) for v, g in pts)
        preset = "windows"
    return list(PRESETS.get(preset, PRESETS["windows"]))


class MonotoneCurve:
    """Monotone piecewise-cubic interpolation, constant outside the range."""

    def __init__(self, points: Sequence[Point]):
        pts = sorted((float(x), float(y)) for x, y in points)
        if not pts:
            pts = [(0.0, 1.0)]
        xs, ys = [], []
        for x, y in pts:
            if xs and x - xs[-1] < 1e-9:
                ys[-1] = y
                continue
            xs.append(x)
            ys.append(y)
        self.xs, self.ys = xs, ys
        n = len(xs)
        if n == 1:
            self.ms = [0.0]
            return
        d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
        m = [0.0] * n
        m[0], m[-1] = d[0], d[-1]
        for i in range(1, n - 1):
            m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0
        for i in range(n - 1):
            if d[i] == 0.0:
                m[i] = m[i + 1] = 0.0
                continue
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9.0:
                t = 3.0 / math.sqrt(s)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
        self.ms = m

    def __call__(self, x: float) -> float:
        xs, ys, ms = self.xs, self.ys, self.ms
        if len(xs) == 1 or x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_right(xs, x) - 1
        h = xs[i + 1] - xs[i]
        t = (x - xs[i]) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * ms[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * ms[i + 1])


def gain_function(curve_cfg: dict):
    """Return ``g(v)`` (px/mm at finger speed v mm/s) including the scale factor."""
    curve = MonotoneCurve(curve_points(curve_cfg))
    scale = float(curve_cfg.get("scale", 1.0))
    return lambda v: max(0.0, curve(v)) * scale


def _monotone_samples(values: list[float]) -> list[float]:
    out = []
    prev = -1.0
    for i, v in enumerate(values):
        v = min(LIBINPUT_MAX_VALUE, max(0.0, v))
        if i and v <= prev:
            v = min(LIBINPUT_MAX_VALUE, prev + 1e-4)
        out.append(round(v, 5))
        prev = out[-1]
    return out


def libinput_motion_points(curve_cfg: dict, res: float, vmax: float = 1000.0,
                           npoints: int = LIBINPUT_MAX_POINTS) -> tuple[float, list[float]]:
    """Encode a pointer curve as (step, points) for the libinput custom profile."""
    if res <= 0:
        raise ValueError("touchpad resolution must be positive")
    g = gain_function(curve_cfg)
    npoints = max(2, min(LIBINPUT_MAX_POINTS, npoints))
    step_v = vmax / (npoints - 1)           # mm/s between samples
    step_u = step_v * res / 1000.0          # device units/ms between samples
    values = [g(i * step_v) * (i * step_v) / 1000.0 for i in range(npoints)]
    return round(step_u, 6), _monotone_samples(values)


def scroll_gain(speed: float, acceleration: float):
    """Scroll gain in libinput scroll px per finger mm, as a function of mm/s."""
    base = LIBINPUT_SCROLL_PX_PER_MM * speed

    def g(v: float) -> float:
        t = min(1.0, max(0.0, (v - 40.0) / 360.0))
        smooth = t * t * (3 - 2 * t)
        return base * (1.0 + acceleration * smooth)
    return g


def libinput_scroll_points(speed: float, acceleration: float, res: float,
                           vmax: float = 800.0, npoints: int = 32) -> tuple[float, list[float]]:
    """Encode the scroll curve for the libinput custom profile ("scroll" type).

    Without this, switching the pointer to the custom profile makes 2-finger
    scrolling ~3x faster (measured), because the stock scroll path applies the
    magic slowdown and the custom one does not.
    """
    if res <= 0:
        raise ValueError("touchpad resolution must be positive")
    g = scroll_gain(speed, acceleration)
    step_v = vmax / (npoints - 1)
    step_u = step_v * res / 1000.0
    values = [g(i * step_v) * (i * step_v) / 1000.0 for i in range(npoints)]
    return round(step_u, 6), _monotone_samples(values)


def scroll_pixel_distance(speed: float) -> int:
    """libinput 'Scrolling Pixel Distance' for the stock profiles (15 = default)."""
    return int(min(100, max(1, round(15.0 / max(0.05, speed)))))


def describe(curve_cfg: dict, pad_width_mm: float, screen_width_px: int) -> dict:
    """Human-oriented summary used by the GUI curve editor."""
    g = gain_function(curve_cfg)
    slow, medium, fast = g(20.0), g(100.0), g(400.0)
    return {
        "slow_px_per_mm": slow,
        "medium_px_per_mm": medium,
        "fast_px_per_mm": fast,
        # How many full-pad strokes to cross the screen at medium speed.
        "strokes_per_screen": screen_width_px / max(1e-6, medium * pad_width_mm),
        "precision_mm_per_px": 1.0 / max(1e-6, slow),
    }
