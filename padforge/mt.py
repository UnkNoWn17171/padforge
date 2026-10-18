"""Linux multitouch protocol B -> per-report frames in millimetres.

Pure parser: feed it ``(type, code, value, timestamp)`` tuples, get a
:class:`Frame` at every ``SYN_REPORT``. The device wrapper in ``device.py`` turns
evdev reads into those tuples; tests feed synthetic ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# evdev codes used here (linux/input-event-codes.h)
EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0x00, 0x01, 0x03, 0x04
SYN_REPORT, SYN_DROPPED = 0, 3
MSC_TIMESTAMP = 0x05
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOOL_FINGER, BTN_TOOL_QUINTTAP = 0x145, 0x148
BTN_TOUCH = 0x14A
BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP = 0x14D, 0x14E, 0x14F
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_TOUCH_MAJOR = 0x30
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39
ABS_MT_PRESSURE = 0x3A
MT_TOOL_FINGER, MT_TOOL_PALM = 0, 2

TOOL_COUNTS = {
    BTN_TOOL_FINGER: 1, BTN_TOOL_DOUBLETAP: 2, BTN_TOOL_TRIPLETAP: 3,
    BTN_TOOL_QUADTAP: 4, BTN_TOOL_QUINTTAP: 5,
}


@dataclass
class Axis:
    minimum: int
    maximum: int
    res: float          # units per mm (never 0 after normalisation)

    def to_mm(self, value: int) -> float:
        return (value - self.minimum) / self.res

    @property
    def size_mm(self) -> float:
        return (self.maximum - self.minimum) / self.res


@dataclass
class Touch:
    slot: int
    tracking_id: int
    x: float            # mm from the left edge
    y: float            # mm from the top edge
    t_down: float
    x0: float = 0.0     # position at touch down
    y0: float = 0.0
    palm: bool = False
    pressure: int | None = None
    major: int | None = None

    def moved(self) -> float:
        return ((self.x - self.x0) ** 2 + (self.y - self.y0) ** 2) ** 0.5


@dataclass
class Frame:
    time: float                     # kernel timestamp, seconds
    hw_time: int | None             # MSC_TIMESTAMP (µs, device clock) or None
    touches: dict[int, Touch]       # active slots (copies; safe to keep)
    tool_count: int                 # fingers reported by BTN_TOOL_* (can exceed slots)
    button: bool                    # physical clickpad button
    began: tuple[int, ...] = ()
    ended: tuple[int, ...] = ()
    ended_touches: tuple[Touch, ...] = ()
    resynced: bool = False

    @property
    def fingers(self) -> list[Touch]:
        return [t for t in self.touches.values() if not t.palm]

    @property
    def count(self) -> int:
        """Best estimate of fingers on the pad, palms excluded."""
        n = len(self.fingers)
        if self.tool_count > len(self.touches):   # more fingers than tracked slots
            n += self.tool_count - len(self.touches)
        return n


@dataclass
class _Slot:
    tracking_id: int = -1
    x: int = 0
    y: int = 0
    tool: int = MT_TOOL_FINGER
    pressure: int | None = None
    major: int | None = None
    dirty: bool = False


@dataclass
class MTParser:
    ax: Axis
    ay: Axis
    nslots: int = 10
    slots: list[_Slot] = field(default_factory=list)
    touches: dict[int, Touch] = field(default_factory=dict)

    def __post_init__(self):
        self.slots = [_Slot() for _ in range(self.nslots)]
        self.cur = 0
        self.keys: dict[int, int] = {}
        self.hw_time: int | None = None
        self.dropped = False
        self.dropped_count = 0
        self._began: list[int] = []
        self._ended: list[int] = []

    def feed(self, etype: int, code: int, value: int, ts: float) -> Frame | None:
        if self.dropped:
            if etype == EV_SYN and code == SYN_REPORT:
                self.dropped = False
                return self._frame(ts, resynced=True)
            return None
        if etype == EV_ABS:
            self._abs(code, value)
        elif etype == EV_KEY:
            self.keys[code] = value
        elif etype == EV_MSC and code == MSC_TIMESTAMP:
            self.hw_time = value
        elif etype == EV_SYN:
            if code == SYN_REPORT:
                return self._frame(ts)
            if code == SYN_DROPPED:
                # Events until the next SYN_REPORT are garbage; the device
                # wrapper re-reads slot state through resync().
                self.dropped = True
                self.dropped_count += 1
        return None

    def _abs(self, code: int, value: int) -> None:
        if code == ABS_MT_SLOT:
            self.cur = value if 0 <= value < self.nslots else self.cur
            return
        s = self.slots[self.cur]
        if code == ABS_MT_TRACKING_ID:
            s.tracking_id = value
            s.dirty = True
        elif code == ABS_MT_POSITION_X:
            s.x = value
            s.dirty = True
        elif code == ABS_MT_POSITION_Y:
            s.y = value
            s.dirty = True
        elif code == ABS_MT_TOOL_TYPE:
            s.tool = value
            s.dirty = True
        elif code == ABS_MT_PRESSURE:
            s.pressure = value
        elif code == ABS_MT_TOUCH_MAJOR:
            s.major = value

    def resync(self, slot_values: dict[int, list[int]], keys: set[int], ts: float) -> Frame:
        """Replace state after SYN_DROPPED with values read via EVIOCGMTSLOTS/EVIOCGKEY."""
        for code, vals in slot_values.items():
            for i, v in enumerate(vals[: self.nslots]):
                saved = self.cur
                self.cur = i
                self._abs(code, v)
                self.cur = saved
        self.keys = {k: 1 for k in keys}
        return self._frame(ts, resynced=True)

    def _frame(self, ts: float, resynced: bool = False) -> Frame:
        began, ended, ended_touches = [], [], []
        for i, s in enumerate(self.slots):
            t = self.touches.get(i)
            if s.tracking_id < 0:
                if t is not None:
                    ended.append(i)
                    ended_touches.append(t)
                    del self.touches[i]
                continue
            x, y = self.ax.to_mm(s.x), self.ay.to_mm(s.y)
            if t is None or t.tracking_id != s.tracking_id:
                if t is not None:          # id changed without -1 in between
                    ended.append(i)
                    ended_touches.append(t)
                t = Touch(i, s.tracking_id, x, y, ts, x0=x, y0=y)
                self.touches[i] = t
                began.append(i)
            else:
                t.x, t.y = x, y
            t.palm = t.palm or s.tool == MT_TOOL_PALM   # once a palm, always a palm
            t.pressure, t.major = s.pressure, s.major
            s.dirty = False
        tool_count = 0
        for code, n in TOOL_COUNTS.items():
            if self.keys.get(code):
                tool_count = max(tool_count, n)
        if not tool_count and self.keys.get(BTN_TOUCH) and self.touches:
            tool_count = len(self.touches)
        snapshot = {i: Touch(**vars(t)) for i, t in self.touches.items()}
        return Frame(ts, self.hw_time, snapshot, tool_count, bool(self.keys.get(BTN_LEFT)),
                     tuple(began), tuple(ended), tuple(ended_touches), resynced)
