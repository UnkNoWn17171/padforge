"""Multi-finger gesture recognizer (3/4-finger swipes and pinches, 3-finger drag).

Pure state machine over :class:`padforge.mt.Frame`. It never touches X; the
daemon maps the emitted :class:`GestureEvent` objects to actions.

Coordinates are millimetres with y growing towards the user, so fingers moving
"up" (away from the user) have negative dy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .mt import Frame

SWITCHERS = {"window_cycle", "app_switch", "tab_switch"}
SETTLE_S = 0.15          # time allowed for the remaining fingers to land
PINCH_MIN_MM = 5.0       # minimum absolute spread change for a pinch (2-finger zoom, drag)
# 3/4-finger pinch vs swipe. A pinch with the thumb held still also moves the
# centroid (the other fingers travel towards the thumb), so the old
# "spread change vs centroid travel" test read it as a swipe. Use the
# translation-free least-squares scale instead: its radial motion
# |ln s| * r_rms (mm) compared with the centroid travel. Recorded swipes
# (25 of them, 2026-10-18) never exceeded |s-1| = 0.18 or radial/travel = 0.17.
PINCH_RADIAL_MM = 3.5    # radial motion needed for a pinch
PINCH_DOMINANCE = 0.35   # radial / travel above this: pinch-like, never a swipe
DRAG_START_MM = 0.8      # 3-finger drag starts after this much centroid travel
ZOOM_STEP = 0.12         # relative spread change per zoom step (2-finger pinch)


@dataclass
class GestureEvent:
    kind: str                # swipe | pinch | begin | step | end | drag_begin | drag_move | drag_end | zoom
    fingers: int = 0
    direction: str = ""      # up/down/left/right/in/out
    step: int = 0            # +1 / -1 for step and zoom events
    dx: float = 0.0          # mm, drag_move
    dy: float = 0.0
    dt: float = 0.0
    time: float = 0.0
    key: str = ""            # binding key, e.g. "swipe3_left"

    def __post_init__(self):
        if not self.key and self.kind in ("swipe", "begin") and self.direction in (
                "up", "down", "left", "right"):
            self.key = f"swipe{self.fingers}_{self.direction}"
        elif not self.key and self.kind == "pinch":
            self.key = f"pinch{self.fingers}_{self.direction}"


def _centroid(points):
    n = len(points)
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n


def _spread(points):
    cx, cy = _centroid(points)
    return sum(math.hypot(x - cx, y - cy) for x, y in points) / len(points)


def ls_scale(a, b) -> tuple[float, float]:
    """Least-squares scale from point set ``a`` to ``b`` (translation removed).

    Returns (scale, rms radius of ``a`` in mm).
    """
    (cax, cay), (cbx, cby) = _centroid(a), _centroid(b)
    num = den = 0.0
    for (ax, ay), (bx, by) in zip(a, b):
        num += (bx - cbx) * (ax - cax) + (by - cby) * (ay - cay)
        den += (ax - cax) ** 2 + (ay - cay) ** 2
    if den <= 1e-9:
        return 1.0, 0.0
    return num / den, math.sqrt(den / len(a))


@dataclass
class _Track:
    n: int
    t0: float
    start: dict[int, tuple[float, float]]
    origin: dict[int, tuple[float, float]] = field(default_factory=dict)
    key: str = ""
    axis: str = ""           # x | y | s (spread)
    last_centroid: tuple[float, float] | None = None
    last_time: float = 0.0


class GestureRecognizer:
    IDLE, CANDIDATE, CONTINUOUS, DRAG, ZOOM, WAIT_LIFT = range(6)

    def __init__(self, swipe_threshold_mm: float = 12.0, pinch_threshold: float = 0.22,
                 step_mm: float = 16.0, three_finger_drag: bool = False,
                 pinch_zoom_2f: bool = False, continuous_keys: set[str] | None = None,
                 enabled: bool = True, step_overrides: dict[str, float] | None = None):
        self.swipe_threshold = swipe_threshold_mm
        self.pinch_threshold = pinch_threshold
        self.step_mm = step_mm
        self.three_finger_drag = three_finger_drag
        self.pinch_zoom_2f = pinch_zoom_2f
        self.continuous_keys = set(continuous_keys or ())
        self.step_overrides = dict(step_overrides or {})   # key -> mm per step
        self.enabled = enabled
        self.state = self.IDLE
        self.track: _Track | None = None
        self._recognized = False

    @classmethod
    def from_config(cls, gcfg: dict, continuous_keys: set[str]) -> "GestureRecognizer":
        # Switchers (window menu, app/tab switcher) step much finer than volume etc.:
        # recorded swipes are only 10-21 mm long.
        switch = {k for k, b in gcfg["bindings"].items() if b["action"] in SWITCHERS}
        return cls(swipe_threshold_mm=gcfg["swipe_threshold_mm"],
                   pinch_threshold=gcfg["pinch_threshold"], step_mm=gcfg["step_mm"],
                   three_finger_drag=gcfg["three_finger_drag"],
                   pinch_zoom_2f=gcfg["pinch_zoom_2f"], continuous_keys=continuous_keys,
                   enabled=gcfg["enabled"],
                   step_overrides={k: gcfg["switch_step_mm"] for k in switch})

    @property
    def active(self) -> bool:
        """True once a gesture was recognised in the current touch sequence.

        A 3-finger tap passes through CANDIDATE/WAIT_LIFT without being
        recognised, so it stays a tap.
        """
        return self._recognized or self.state in (self.CONTINUOUS, self.DRAG, self.ZOOM)

    def reset(self) -> list[GestureEvent]:
        out = self._finish(0.0)
        self.state, self.track, self._recognized = self.IDLE, None, False
        return out

    # ------------------------------------------------------------------------
    def feed(self, f: Frame) -> list[GestureEvent]:
        if not self.enabled:
            return []
        out = self._feed(f)
        if out:
            self._recognized = True
        if self.state == self.IDLE:
            self._recognized = False
        return out

    def _feed(self, f: Frame) -> list[GestureEvent]:
        count = f.count
        pts = {t.slot: (t.x, t.y) for t in f.fingers}
        if f.button:                       # physical click: never a gesture
            if self.state in (self.CANDIDATE, self.IDLE):
                self.state = self.WAIT_LIFT if count else self.IDLE
                self.track = None
            if self.state != self.DRAG:
                return []

        if self.state == self.IDLE:
            if count >= 3:
                self._start(count, f.time, pts)
                self.state = self.CANDIDATE
            elif count == 2 and self.pinch_zoom_2f:
                self._start(2, f.time, pts)
                self.state = self.CANDIDATE
            return []

        if self.state == self.WAIT_LIFT:
            if count == 0:
                self.state, self.track = self.IDLE, None
            return []

        tr = self.track
        assert tr is not None

        if self.state == self.CANDIDATE:
            if count == 0:
                self.state, self.track = self.IDLE, None
                return []
            if count > tr.n:
                self._start(count, f.time, pts)          # more fingers landed: restart
                return []
            if count < tr.n:
                # Fingers left before anything was recognised. A quick 4 -> 3
                # while landing is still a 3-finger gesture; anything else ends it.
                if count >= 3 and f.time - tr.t0 < SETTLE_S:
                    self._start(count, f.time, pts)
                else:
                    self.state = self.WAIT_LIFT if count else self.IDLE
                    self.track = None
                return []
            return self._classify(f, pts)

        if self.state == self.DRAG:
            if count == 0 or not pts:
                self.state, self.track = self.IDLE, None
                return [GestureEvent("drag_end", 3, time=f.time)]
            return self._drag_move(f, pts)

        if self.state in (self.CONTINUOUS, self.ZOOM):
            if count < 2:
                out = self._finish(f.time)
                self.state = self.IDLE if count == 0 else self.WAIT_LIFT
                self.track = None
                return out
            return self._continuous(f, pts)
        return []

    # ------------------------------------------------------------------------
    def _start(self, n: int, t: float, pts: dict[int, tuple[float, float]]) -> None:
        self.track = _Track(n=n, t0=t, start=dict(pts), origin=dict(pts))

    def _common(self, ref: dict, pts: dict) -> tuple[list, list]:
        slots = [s for s in ref if s in pts]
        return [ref[s] for s in slots], [pts[s] for s in slots]

    def _classify(self, f: Frame, pts) -> list[GestureEvent]:
        tr = self.track
        a, b = self._common(tr.start, pts)
        if len(a) < 2:
            self._start(f.count, f.time, pts)
            return []
        (ax, ay), (bx, by) = _centroid(a), _centroid(b)
        dx, dy = bx - ax, by - ay
        dist = math.hypot(dx, dy)
        s0, s1 = _spread(a), _spread(b)
        ds = s1 - s0

        if tr.n == 2:          # 2-finger pinch zoom only
            if s0 > 3.0 and abs(math.log(max(1e-3, s1) / s0)) >= math.log(1 + self.pinch_threshold) \
                    and abs(ds) > 1.5 * dist:
                self.state = self.ZOOM
                tr.axis, tr.origin, tr.key = "s", dict(pts), "zoom"
                return [GestureEvent("zoom", 2, "out" if ds > 0 else "in",
                                     step=1 if ds > 0 else -1, time=f.time)]
            if dist > 4.0:     # it is a scroll; leave it to libinput
                self.state, self.track = self.WAIT_LIFT, None
            return []

        if tr.n == 3 and self.three_finger_drag:
            if dist >= DRAG_START_MM and abs(ds) < max(PINCH_MIN_MM, dist):
                self.state = self.DRAG
                tr.last_centroid, tr.last_time = (bx, by), f.time
                return [GestureEvent("drag_begin", 3, time=f.time),
                        GestureEvent("drag_move", 3, dx=dx, dy=dy, dt=f.time - tr.t0,
                                     time=f.time)]
            return []

        scale, r0 = ls_scale(a, b)
        radial = abs(math.log(max(scale, 1e-3))) * r0
        pinch_like = radial >= PINCH_DOMINANCE * dist
        if (pinch_like and radial >= PINCH_RADIAL_MM
                and abs(scale - 1.0) >= 0.8 * self.pinch_threshold):
            direction = "out" if scale > 1.0 else "in"
            key = f"pinch{tr.n}_{direction}"
            if key in self.continuous_keys:
                self.state = self.CONTINUOUS
                tr.axis, tr.key, tr.origin = "s", key, dict(pts)
                return [GestureEvent("begin", tr.n, direction, key=key, time=f.time)]
            self.state, self.track = self.WAIT_LIFT, None
            return [GestureEvent("pinch", tr.n, direction, time=f.time)]

        if dist >= self.swipe_threshold and not pinch_like:
            if abs(dx) >= abs(dy):
                direction, axis = ("right" if dx > 0 else "left"), "x"
            else:
                direction, axis = ("down" if dy > 0 else "up"), "y"
            key = f"swipe{tr.n}_{direction}"
            if key in self.continuous_keys:
                self.state = self.CONTINUOUS
                tr.axis, tr.key, tr.origin = axis, key, dict(pts)
                return [GestureEvent("begin", tr.n, direction, key=key, time=f.time)]
            self.state, self.track = self.WAIT_LIFT, None
            return [GestureEvent("swipe", tr.n, direction, time=f.time)]
        return []

    def _drag_move(self, f: Frame, pts) -> list[GestureEvent]:
        tr = self.track
        cx, cy = _centroid(list(pts.values()))
        if tr.last_centroid is None or set(pts) != set(tr.origin):
            # Finger set changed: re-anchor without a jump.
            tr.origin, tr.last_centroid, tr.last_time = dict(pts), (cx, cy), f.time
            return []
        lx, ly = tr.last_centroid
        dt = f.time - tr.last_time
        tr.last_centroid, tr.last_time = (cx, cy), f.time
        if cx == lx and cy == ly:
            return []
        return [GestureEvent("drag_move", 3, dx=cx - lx, dy=cy - ly, dt=dt, time=f.time)]

    def _continuous(self, f: Frame, pts) -> list[GestureEvent]:
        tr = self.track
        a, b = self._common(tr.origin, pts)
        if len(a) < 2:
            tr.origin = dict(pts)
            return []
        out = []
        if tr.axis == "s":
            s0, s1 = _spread(a), _spread(b)
            if s0 <= 1e-3:
                return []
            ratio = s1 / s0
            step_ratio = 1 + (ZOOM_STEP if self.state == self.ZOOM else self.pinch_threshold / 2)
            n = int(math.log(ratio) / math.log(step_ratio))
            if n:
                kind = "zoom" if self.state == self.ZOOM else "step"
                for _ in range(abs(n)):
                    out.append(GestureEvent(kind, tr.n, step=1 if n > 0 else -1, key=tr.key,
                                            time=f.time))
                tr.origin = dict(pts)
            return out
        (ax, ay), (bx, by) = _centroid(a), _centroid(b)
        d = (bx - ax) if tr.axis == "x" else (by - ay)
        step_mm = self.step_overrides.get(tr.key, self.step_mm)
        n = int(d / step_mm)
        if n:
            for _ in range(abs(n)):
                out.append(GestureEvent("step", tr.n, step=1 if n > 0 else -1, key=tr.key,
                                        time=f.time))
            # Move the origin by whole steps only, so no distance is lost.
            shift = n * step_mm
            tr.origin = {s: ((x + shift, y) if tr.axis == "x" else (x, y + shift))
                         for s, (x, y) in tr.origin.items()}
        return out

    def _finish(self, t: float) -> list[GestureEvent]:
        tr = self.track
        if self.state == self.CONTINUOUS and tr:
            return [GestureEvent("end", tr.n, key=tr.key, time=t)]
        if self.state == self.ZOOM:
            return [GestureEvent("end", 2, key="zoom", time=t)]
        if self.state == self.DRAG:
            return [GestureEvent("drag_end", 3, time=t)]
        return []
