"""Kinetic (inertial) two-finger scrolling.

X11 applications get no "scroll stopped" event from libinput, so nothing on X
coasts after a flick. :class:`ScrollTracker` watches the raw touches for a
two-finger scroll and estimates the finger velocity at lift-off;
:class:`Coaster` turns that into an exponentially decaying scroll that the
daemon emits through a virtual wheel.

The finger velocity (mm/s, precise kernel timestamps) is converted to X scroll
units with the ratio actually observed during the gesture (X units per finger
mm, from XI2 raw events), so speed, acceleration and natural-scrolling settings
are all carried over automatically.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .mt import Frame

UNITS_PER_CLICK = 120.0            # X scroll valuator increment / hi-res wheel click
WINDOW_S = 0.08                    # velocity estimation window before lift-off
MIN_SCROLL_MM = 3.0                # the gesture must have scrolled at least this far
MAX_PINCH_RATIO = 0.18             # relative spread change allowed (else it is a pinch)
LIFT_GAP_S = 0.08                  # second finger must lift within this time
STOP_UNITS_S = 0.6 * UNITS_PER_CLICK


@dataclass
class Fling:
    vx: float          # finger mm/s
    vy: float
    time: float

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)


def _slope(samples: list[tuple[float, float]]) -> float:
    """Least-squares slope of (t, value)."""
    n = len(samples)
    if n < 2:
        return 0.0
    mt_ = sum(t for t, _ in samples) / n
    mv = sum(v for _, v in samples) / n
    den = sum((t - mt_) ** 2 for t, _ in samples)
    if den <= 1e-12:
        return 0.0
    return sum((t - mt_) * (v - mv) for t, v in samples) / den


class ScrollTracker:
    """Recognises the end of a two-finger scroll and its lift-off velocity."""

    def __init__(self, min_speed_mm_s: float = 50.0):
        self.min_speed = min_speed_mm_s
        self.hist: deque[tuple[float, float, float]] = deque()
        self.start: tuple[float, float] | None = None
        self.spread0 = 0.0
        self.pinch = False
        self.button = False
        self.travel = 0.0
        self.t_start = 0.0
        self.candidate: Fling | None = None
        self.cand_pos: tuple[float, float] | None = None
        self.cand_time = 0.0
        self.last_count = 0

    def _reset(self) -> None:
        self.hist.clear()
        self.start = None
        self.pinch = self.button = False
        self.travel = 0.0

    def feed(self, f: Frame) -> Fling | None:
        fingers = f.fingers
        count = f.count
        out = None

        if self.candidate is not None:
            if count == 0:
                if f.time - self.cand_time <= LIFT_GAP_S:
                    out = self.candidate
                self.candidate = None
            elif count == 1 and fingers and self.cand_pos is not None:
                t = fingers[0]
                moved = math.hypot(t.x - self.cand_pos[0], t.y - self.cand_pos[1])
                if moved > 2.0 or f.time - self.cand_time > LIFT_GAP_S:
                    self.candidate = None     # user went on moving the pointer
            else:
                self.candidate = None

        if count == 2 and len(fingers) == 2:
            (a, b) = fingers
            cx, cy = (a.x + b.x) / 2, (a.y + b.y) / 2
            spread = math.hypot(a.x - b.x, a.y - b.y)
            if self.start is None or self.last_count != 2:
                self._reset()
                self.start, self.spread0, self.t_start = (cx, cy), spread, f.time
            if f.button:
                self.button = True
            if self.spread0 > 1.0 and abs(spread - self.spread0) / self.spread0 > MAX_PINCH_RATIO:
                self.pinch = True
            if self.hist:
                _, px, py = self.hist[-1]
                self.travel += math.hypot(cx - px, cy - py)
            self.hist.append((f.time, cx, cy))
            while self.hist and f.time - self.hist[0][0] > WINDOW_S:
                self.hist.popleft()
        elif self.last_count == 2 and count < 2:
            fling = self._fling(f.time)
            if fling is not None:
                if count == 0:
                    out = fling
                else:
                    self.candidate = fling
                    self.cand_time = f.time
                    self.cand_pos = (fingers[0].x, fingers[0].y) if fingers else None
            self._reset()
        elif count != 2:
            self._reset()
        self.last_count = count
        return out

    def _fling(self, now: float) -> Fling | None:
        if self.start is None or self.pinch or self.button or len(self.hist) < 3:
            return None
        if self.travel < MIN_SCROLL_MM:
            return None
        # Ignore a gesture that already stopped before lifting (no flick).
        if now - self.hist[-1][0] > 0.05:
            return None
        vx = _slope([(t, x) for t, x, _ in self.hist])
        vy = _slope([(t, y) for t, _, y in self.hist])
        fling = Fling(vx, vy, now)
        return fling if fling.speed >= self.min_speed else None


class RatioMeter:
    """X scroll units per finger millimetre, measured during the current scroll."""

    def __init__(self, fallback_units_per_mm: float):
        self.fallback = fallback_units_per_mm
        self.units = deque()      # (t, dx_units, dy_units)
        self.mm = deque()         # (t, dx_mm, dy_mm)
        self.last_finger: tuple[float, float] | None = None

    def add_units(self, t: float, dx: float, dy: float) -> None:
        self.units.append((t, dx, dy))
        self._trim(t)

    def add_frame(self, f: Frame) -> None:
        fingers = f.fingers
        if f.count != 2 or len(fingers) != 2:
            self.last_finger = None
            return
        cx = (fingers[0].x + fingers[1].x) / 2
        cy = (fingers[0].y + fingers[1].y) / 2
        if self.last_finger is not None:
            self.mm.append((f.time, cx - self.last_finger[0], cy - self.last_finger[1]))
        self.last_finger = (cx, cy)
        self._trim(f.time)

    def _trim(self, now: float) -> None:
        for q in (self.units, self.mm):
            while q and now - q[0][0] > 0.25:
                q.popleft()

    def ratio(self) -> tuple[float, float]:
        ux = sum(d for _, d, _ in self.units)
        uy = sum(d for _, _, d in self.units)
        mx = sum(d for _, d, _ in self.mm)
        my = sum(d for _, _, d in self.mm)
        rx = ux / mx if abs(mx) > 1.0 and abs(ux) > 1.0 else None
        ry = uy / my if abs(my) > 1.0 and abs(uy) > 1.0 else None
        # Use whichever axis is reliable for both (scroll ratio is isotropic).
        if ry is None and rx is not None:
            ry = rx
        if rx is None and ry is not None:
            rx = ry
        if rx is None:
            rx = ry = self.fallback
        return rx, ry


class Coaster:
    """Exponentially decaying scroll, integrated exactly between ticks."""

    def __init__(self, friction: float = 2.6, max_duration: float = 4.0,
                 stop_speed: float = STOP_UNITS_S):
        self.friction = friction
        self.max_duration = max_duration
        self.stop_speed = stop_speed
        self.active = False
        self.v0x = self.v0y = 0.0
        self.t0 = 0.0
        self.done_x = self.done_y = 0.0
        self.rem_x = self.rem_y = 0.0

    def start(self, vx: float, vy: float, now: float) -> None:
        """Start coasting with velocities in X scroll units per second."""
        self.v0x, self.v0y, self.t0 = vx, vy, now
        self.done_x = self.done_y = 0.0
        self.rem_x = self.rem_y = 0.0
        self.active = math.hypot(vx, vy) > self.stop_speed

    def stop(self) -> None:
        self.active = False

    def velocity(self, now: float) -> tuple[float, float]:
        k = math.exp(-self.friction * max(0.0, now - self.t0))
        return self.v0x * k, self.v0y * k

    def step(self, now: float) -> tuple[int, int]:
        """Integer units to emit now (hi-res wheel units, 120 per click)."""
        if not self.active:
            return 0, 0
        t = max(0.0, now - self.t0)
        frac = (1.0 - math.exp(-self.friction * t)) / self.friction
        tx, ty = self.v0x * frac, self.v0y * frac
        dx = tx - self.done_x + self.rem_x
        dy = ty - self.done_y + self.rem_y
        self.done_x, self.done_y = tx, ty
        ix, iy = int(dx), int(dy)            # truncate toward zero
        self.rem_x, self.rem_y = dx - ix, dy - iy
        vx, vy = self.velocity(now)
        if math.hypot(vx, vy) < self.stop_speed or t >= self.max_duration:
            self.active = False
        return ix, iy

    def total_distance(self) -> tuple[float, float]:
        """Distance the whole coast will cover (units), for tests and the GUI."""
        return self.v0x / self.friction, self.v0y / self.friction
