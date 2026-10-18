import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from padforge.mt import Frame, Touch, park_resting  # noqa: E402


class Hand:
    """Builds synthetic frames: fingers land, move, pinch and lift in mm/seconds."""

    def __init__(self, t: float = 100.0, hz: float = 125.0):
        self.t = t
        self.dt = 1.0 / hz
        self.hw = 0
        self.touches: dict[int, Touch] = {}
        self.next_id = 1
        self.button = False

    def _frame(self, began=(), ended=(), ended_touches=()):
        park_resting(self.touches.values())
        snap = {s: Touch(**vars(t)) for s, t in self.touches.items()}
        f = Frame(self.t, self.hw, snap, len(self.touches), self.button,
                  tuple(began), tuple(ended), tuple(ended_touches))
        self.t += self.dt
        self.hw += int(self.dt * 1e6)
        return f

    def put(self, *pts, palm=False, edge=False):
        began = []
        for x, y in pts:
            slot = next(i for i in range(10) if i not in self.touches)
            self.touches[slot] = Touch(slot, self.next_id, x, y, self.t, x0=x, y0=y, palm=palm,
                                       edge=edge)
            self.next_id += 1
            began.append(slot)
        return [self._frame(began=began)]

    def converge(self, target, factor, duration, slots):
        """Move ``slots`` a fraction ``factor`` of the way towards ``target`` (thumb pinch)."""
        steps = max(1, int(round(duration / self.dt)))
        start = {s: (self.touches[s].x, self.touches[s].y) for s in slots}
        out = []
        for k in range(1, steps + 1):
            f = factor * k / steps
            for s in slots:
                x0, y0 = start[s]
                self.touches[s].x = x0 + (target[0] - x0) * f
                self.touches[s].y = y0 + (target[1] - y0) * f
            out.append(self._frame())
        return out

    def move(self, dx, dy, duration, slots=None):
        slots = list(self.touches) if slots is None else slots
        steps = max(1, int(round(duration / self.dt)))
        start = {s: (self.touches[s].x, self.touches[s].y) for s in slots}
        out = []
        for k in range(1, steps + 1):
            for s in slots:
                x0, y0 = start[s]
                self.touches[s].x = x0 + dx * k / steps
                self.touches[s].y = y0 + dy * k / steps
            out.append(self._frame())
        return out

    def spread(self, factor, duration):
        steps = max(1, int(round(duration / self.dt)))
        start = {s: (t.x, t.y) for s, t in self.touches.items()}
        cx = sum(p[0] for p in start.values()) / len(start)
        cy = sum(p[1] for p in start.values()) / len(start)
        out = []
        for k in range(1, steps + 1):
            f = 1 + (factor - 1) * k / steps
            for s, (x0, y0) in start.items():
                self.touches[s].x = cx + (x0 - cx) * f
                self.touches[s].y = cy + (y0 - cy) * f
            out.append(self._frame())
        return out

    def hold(self, duration):
        steps = max(1, int(round(duration / self.dt)))
        return [self._frame() for _ in range(steps)]

    def lift(self, slots=None):
        slots = list(self.touches) if slots is None else slots
        ended_touches = [self.touches[s] for s in slots]
        for s in slots:
            del self.touches[s]
        return [self._frame(ended=slots, ended_touches=ended_touches)]

    def idle(self, duration):
        """Time passes with nothing on the pad (no frames)."""
        self.t += duration


@pytest.fixture
def hand():
    return Hand()


def feed_all(engine, frames, **kw):
    out = []
    for f in frames:
        out += engine.feed(f, **kw)
    return out


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    os.makedirs(tmp_path / "run", exist_ok=True)
