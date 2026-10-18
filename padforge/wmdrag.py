"""Super + touch window handling.

GNOME moves a window with Super + left-drag and resizes it with Super +
middle-drag (or right-drag), from anywhere inside the window. With Super held
on the keyboard:

* one finger down and moving  -> left button held: the window follows the finger
* three fingers down          -> resize button held, the pointer follows the
                                 fingers' centre (libinput does not move the
                                 pointer for three fingers, the daemon does)

Pure state machine over frames; the daemon presses buttons and moves the pointer.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mt import Frame

SETTLE_S = 0.06       # wait this long for more fingers before deciding "move"
MOVE_START_MM = 0.8   # ...unless the single finger already moves this far
TWO_GIVE_UP_S = 0.15  # two fingers that stay two fingers: not ours (scroll)


@dataclass
class WmEvent:
    kind: str          # press | release | move
    button: int = 0
    dx: float = 0.0    # mm (move)
    dy: float = 0.0
    dt: float = 0.0


def _centroid(fingers):
    n = len(fingers)
    return sum(t.x for t in fingers) / n, sum(t.y for t in fingers) / n


class SuperDrag:
    IDLE, PENDING, MOVE, RESIZE, OTHER = range(5)

    def __init__(self, move: bool = True, resize: bool = True, move_button: int = 1,
                 resize_button: int = 2):
        self.move_enabled = move
        self.resize_enabled = resize
        self.move_button = move_button
        self.resize_button = resize_button
        self.state = self.IDLE
        self.t0 = 0.0
        self.start: dict[int, tuple[float, float]] = {}
        self.last: tuple[float, float] | None = None
        self.last_ids: frozenset = frozenset()
        self.last_t = 0.0
        self.pressed: int | None = None

    @property
    def active(self) -> bool:
        """True while this touch sequence belongs to Super handling."""
        return self.state in (self.PENDING, self.MOVE, self.RESIZE)

    def feed(self, f: Frame, super_held) -> list[WmEvent]:
        """``super_held`` is called once, when a touch sequence starts."""
        count = f.count
        fingers = f.fingers
        if self.state == self.IDLE:
            if count == 0:
                return []
            if (self.move_enabled or self.resize_enabled) and super_held():
                self.state, self.t0 = self.PENDING, f.time
                self.start = {t.tracking_id: (t.x, t.y) for t in fingers}
            else:
                self.state = self.OTHER
            if self.state == self.OTHER:
                return []
        if self.state == self.OTHER:
            if count == 0:
                self.state = self.IDLE
            return []
        if count == 0:
            out = self._release()
            self.state = self.IDLE
            return out

        if self.state == self.PENDING:
            age = f.time - self.t0
            if count >= 3 and self.resize_enabled:
                self.state = self.RESIZE
                self._anchor(f)
                return self._press(self.resize_button)
            if count == 1 and self.move_enabled:
                t = fingers[0] if fingers else None
                o = self.start.get(t.tracking_id) if t else None
                moved = ((t.x - o[0]) ** 2 + (t.y - o[1]) ** 2) ** 0.5 if (t and o) else 0.0
                if age >= SETTLE_S or moved >= MOVE_START_MM:
                    self.state = self.MOVE
                    return self._press(self.move_button)
            if count == 2 and age >= TWO_GIVE_UP_S:
                self.state = self.OTHER
            return []

        if self.state == self.RESIZE and fingers:
            ids = frozenset(t.tracking_id for t in fingers)
            c = _centroid(fingers)
            if ids != self.last_ids or self.last is None:
                self._anchor(f)                    # a finger came or went: no jump
                return []
            dx, dy = c[0] - self.last[0], c[1] - self.last[1]
            dt = f.time - self.last_t
            self.last, self.last_t = c, f.time
            if dx or dy:
                return [WmEvent("move", dx=dx, dy=dy, dt=dt)]
        return []

    def _anchor(self, f: Frame) -> None:
        fingers = f.fingers
        self.last = _centroid(fingers) if fingers else None
        self.last_ids = frozenset(t.tracking_id for t in fingers)
        self.last_t = f.time

    def _press(self, button: int) -> list[WmEvent]:
        self.pressed = button
        return [WmEvent("press", button)]

    def _release(self) -> list[WmEvent]:
        if self.pressed is None:
            return []
        b, self.pressed = self.pressed, None
        return [WmEvent("release", b)]

    def cancel(self) -> list[WmEvent]:
        out = self._release()
        self.state = self.IDLE
        return out
