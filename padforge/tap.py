"""Zero-latency tap engine.

libinput, with tap-and-drag enabled, holds every tap's button release for
180 ms (it must wait to see whether a drag follows), so buttons and links react
late. This engine clicks the moment the fingers lift; a drag is started by
touching again within ``drag_window`` (Windows' "tap twice and drag").

Pure state machine: feed frames, call :meth:`tick` at :meth:`deadline`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mt import Frame

LEFT, MIDDLE, RIGHT = 1, 2, 3
DRAG_DECIDE_S = 0.04        # wait this long for a 2nd finger before pressing for a drag
DRAG_DECIDE_MM = 0.3        # ...or until the finger starts moving
# Measured on the ELAN0303 (2026-10-18): before most 3/4-finger swipes the hand
# touches and lifts for 29-68 ms first. Multi-finger taps therefore wait this
# long, and are dropped if the next touch turns into a gesture.
MULTI_CONFIRM_S = 0.08
MULTI_SETTLE_S = 0.08       # fingers of one hand land up to ~55 ms apart (measured)
MULTI_RESOLVE_S = 1.0       # swipes after a bounce were recognised up to 0.7 s later
MULTI_MOVE_FACTOR = 1.6     # fingers roll more in multi-finger taps (max seen 1.43 mm)


@dataclass
class TapEvent:
    kind: str               # click | press | release | action
    button: int = LEFT
    fingers: int = 1
    time: float = 0.0


@dataclass
class _Session:
    start: float
    max_fingers: int = 0
    max_move: float = 0.0
    button: bool = False
    gesture: bool = False
    typing: bool = False
    coasting: bool = False
    palm_only: bool = True


class TapEngine:
    IDLE, PENDING, DECIDE, DRAGGING, LOCKED = range(5)

    def __init__(self, button_map: str = "lrm", drag: bool = True, drag_lock: bool = False,
                 max_duration: float = 0.18, max_move_mm: float = 1.5,
                 drag_window: float = 0.20, drag_lock_time: float = 0.40,
                 typing_guard: float = 0.25, fingers: set[int] | None = None,
                 left_handed: bool = False):
        self.button_map = button_map
        self.drag = drag
        self.drag_lock = drag_lock
        self.max_duration = max_duration
        self.max_move = max_move_mm
        self.drag_window = drag_window
        self.drag_lock_time = drag_lock_time
        self.typing_guard = typing_guard
        self.fingers = set(fingers if fingers is not None else (1, 2, 3, 4))
        self.left_handed = left_handed
        self.state = self.IDLE
        self.session: _Session | None = None
        self.pending_until = 0.0     # PENDING: a new touch before this starts a drag
        self.decide_until = 0.0
        self.lock_until = 0.0
        self.pressed = False
        self._origin: dict[int, tuple[float, float]] = {}
        self.held: TapEvent | None = None      # multi-finger tap waiting for confirmation
        self.held_until = 0.0
        self.held_attached = False

    @classmethod
    def from_config(cls, tcfg: dict, left_handed: bool, padforge_engine: bool) -> "TapEngine":
        # libinput handles 1-3 finger taps itself; 4-finger taps are always ours.
        fingers = {1, 2, 3, 4} if (padforge_engine and tcfg["enabled"]) else {4}
        return cls(button_map=tcfg["button_map"],
                   drag=tcfg["drag"] and padforge_engine and tcfg["enabled"],
                   drag_lock=tcfg["drag_lock"],
                   max_duration=tcfg["max_duration_ms"] / 1000.0,
                   max_move_mm=tcfg["max_move_mm"],
                   drag_window=tcfg["drag_window_ms"] / 1000.0,
                   drag_lock_time=tcfg["drag_lock_ms"] / 1000.0,
                   typing_guard=tcfg["typing_guard_ms"] / 1000.0,
                   fingers=fingers, left_handed=left_handed)

    # ------------------------------------------------------------------------
    def button_for(self, fingers: int) -> int:
        if fingers == 1:
            b = LEFT
        elif fingers == 2:
            b = RIGHT if self.button_map == "lrm" else MIDDLE
        else:
            b = MIDDLE if self.button_map == "lrm" else RIGHT
        if self.left_handed and b in (LEFT, RIGHT):
            b = RIGHT if b == LEFT else LEFT
        return b

    @property
    def drag_button(self) -> int:
        return RIGHT if self.left_handed else LEFT

    def deadline(self) -> float | None:
        if self.held is not None and not self.held_attached:
            other = self._state_deadline()
            return self.held_until if other is None else min(other, self.held_until)
        return self._state_deadline()

    def _state_deadline(self) -> float | None:
        if self.state == self.PENDING:
            return self.pending_until
        if self.state == self.DECIDE:
            return self.decide_until
        if self.state == self.LOCKED:
            return self.lock_until
        return None

    def _release_held(self) -> list[TapEvent]:
        ev, self.held, self.held_attached = self.held, None, False
        return [ev] if ev is not None else []

    def tick(self, now: float) -> list[TapEvent]:
        out: list[TapEvent] = []
        if self.held is not None and not self.held_attached and now >= self.held_until:
            out += self._release_held()
        if self.state == self.PENDING and now >= self.pending_until:
            self.state = self.IDLE
        elif self.state == self.DECIDE and now >= self.decide_until:
            out += self._press(now)
        elif self.state == self.LOCKED and now >= self.lock_until and self.session is None:
            out += self._release(now)
        return out

    def cancel(self, now: float) -> list[TapEvent]:
        """Abort everything (device lost, engine disabled)."""
        out = self._release(now) if self.pressed else []
        self.state, self.session = self.IDLE, None
        self.held, self.held_attached = None, False
        return out

    # ------------------------------------------------------------------------
    def feed(self, f: Frame, last_key_time: float | None = None, gesture_active: bool = False,
             coasting: bool = False) -> list[TapEvent]:
        out: list[TapEvent] = []
        count = f.count
        now = f.time

        if self.session is None:
            if count == 0:
                return out
            # A new touch sequence begins.
            if self.held is not None:
                if now <= self.held_until:
                    self.held_attached = True      # decide when this touch shows what it is
                else:
                    out += self._release_held()     # the timer was late: it was a tap
            s = _Session(start=now, coasting=coasting)
            s.typing = (last_key_time is not None and self.typing_guard > 0
                        and 0 <= now - last_key_time < self.typing_guard)
            self.session = s
            self._origin = {t.tracking_id: (t.x, t.y) for t in f.fingers}
            if self.state == self.PENDING and now <= self.pending_until and count == 1:
                self.state = self.DECIDE
                self.decide_until = now + DRAG_DECIDE_S
            elif self.state == self.PENDING:
                self.state = self.IDLE
            elif self.state == self.LOCKED:
                self.state = self.DRAGGING           # continue the locked drag

        s = self.session
        for t in f.fingers:
            o = self._origin.setdefault(t.tracking_id, (t.x, t.y))
            s.max_move = max(s.max_move, ((t.x - o[0]) ** 2 + (t.y - o[1]) ** 2) ** 0.5)
        if f.fingers:
            s.palm_only = False
        s.max_fingers = max(s.max_fingers, count)
        s.button = s.button or f.button
        s.gesture = s.gesture or gesture_active

        if self.held is not None and self.held_attached:
            age = now - s.start
            if s.gesture:
                self.held, self.held_attached = None, False   # it was the start of a swipe
            elif (count == 0 or age >= MULTI_RESOLVE_S
                  or (age >= MULTI_SETTLE_S and count < self.held.fingers)):
                # Fewer fingers than the tap (e.g. pointer motion) or no gesture at all.
                out += self._release_held()

        if self.state == self.DECIDE:
            if count > 1 or s.button or s.gesture:
                self.state = self.IDLE               # second finger: not a drag after all
            elif s.max_move >= DRAG_DECIDE_MM or now >= self.decide_until:
                out += self._press(now)

        if count == 0:
            out += self._end(now)
        return out

    # ------------------------------------------------------------------------
    def _is_tap(self, s: _Session, now: float) -> bool:
        move_limit = self.max_move * (1.0 if s.max_fingers <= 1 else MULTI_MOVE_FACTOR)
        return (not s.palm_only and not s.button and not s.gesture and not s.typing
                and not s.coasting and now - s.start <= self.max_duration
                and s.max_move <= move_limit and 1 <= s.max_fingers <= 5)

    def _end(self, now: float) -> list[TapEvent]:
        s = self.session
        self.session = None
        out: list[TapEvent] = []
        tap = s is not None and self._is_tap(s, now)

        if self.state == self.DECIDE:
            # Touched again and lifted before we decided: a double tap.
            if tap:
                out += self._press(now) + self._release(now)
                self._arm_pending(now)
            else:
                self.state = self.IDLE
            return out

        if self.state == self.DRAGGING:
            if self.drag_lock and not tap:
                self.state = self.LOCKED
                self.lock_until = now + self.drag_lock_time
            else:
                out += self._release(now)
                if tap:
                    self._arm_pending(now)
            return out

        if tap and s.max_fingers in self.fingers:
            n = min(4, s.max_fingers)
            if n == 1:
                out.append(TapEvent("click", self.button_for(1), 1, now))
                self._arm_pending(now)
            else:
                ev = (TapEvent("action", fingers=4, time=now) if n == 4
                      else TapEvent("click", self.button_for(n), n, now))
                out += self._release_held()        # an earlier multi-tap is now certain
                self.held, self.held_until, self.held_attached = ev, now + MULTI_CONFIRM_S, False
        return out

    def _arm_pending(self, now: float) -> None:
        if self.drag:
            self.state = self.PENDING
            self.pending_until = now + self.drag_window
        else:
            self.state = self.IDLE

    def _press(self, now: float) -> list[TapEvent]:
        self.state = self.DRAGGING
        if self.pressed:
            return []
        self.pressed = True
        return [TapEvent("press", self.drag_button, 1, now)]

    def _release(self, now: float) -> list[TapEvent]:
        self.state = self.IDLE
        if not self.pressed:
            return []
        self.pressed = False
        return [TapEvent("release", self.drag_button, 1, now)]
