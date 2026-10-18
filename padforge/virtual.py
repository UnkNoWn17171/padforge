"""A virtual multitouch touchpad (uinput) for self-tests and integration tests.

The device goes through the real kernel, udev, Xorg and libinput. Tests float
its X device so nothing moves on the user's screen, then read what libinput
produced from XI2 raw events.
"""
from __future__ import annotations

import select
import time
from dataclasses import dataclass

from . import xinput as xi

TEST_PREFIX = "PadForge Test"


@dataclass
class Geometry:
    width_mm: float = 118.6
    height_mm: float = 72.0
    res: int = 31             # units/mm, like the ELAN0303 class of devices

    @property
    def xmax(self) -> int:
        return int(self.width_mm * self.res)

    @property
    def ymax(self) -> int:
        return int(self.height_mm * self.res)


class VirtualTouchpad:
    def __init__(self, name: str = f"{TEST_PREFIX} Touchpad", geometry: Geometry | None = None,
                 hz: float = 125.0):
        from evdev import AbsInfo, UInput
        from evdev import ecodes as e
        self.e = e
        self.geo = g = geometry or Geometry()
        self.hz = hz
        self.name = name
        cap = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_TOOL_FINGER, e.BTN_TOOL_QUINTTAP, e.BTN_TOUCH,
                       e.BTN_TOOL_DOUBLETAP, e.BTN_TOOL_TRIPLETAP, e.BTN_TOOL_QUADTAP],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(0, 0, g.xmax, 0, 0, g.res)),
                (e.ABS_Y, AbsInfo(0, 0, g.ymax, 0, 0, g.res)),
                (e.ABS_MT_SLOT, AbsInfo(0, 0, 4, 0, 0, 0)),
                (e.ABS_MT_POSITION_X, AbsInfo(0, 0, g.xmax, 0, 0, g.res)),
                (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, g.ymax, 0, 0, g.res)),
                (e.ABS_MT_TOOL_TYPE, AbsInfo(0, 0, 2, 0, 0, 0)),
                (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
            ],
            e.EV_MSC: [e.MSC_TIMESTAMP],
        }
        self.ui = UInput(cap, name=name, vendor=0x1209, product=0x7e57, version=1,
                         bustype=e.BUS_I2C,
                         input_props=[e.INPUT_PROP_POINTER, e.INPUT_PROP_BUTTONPAD])
        self.fingers: dict[int, tuple[float, float]] = {}
        self._tid = 1000
        self.hw_us = 0

    # -- low level ----------------------------------------------------------------
    def _sync(self) -> None:
        e = self.e
        if self.fingers:
            first = self.fingers[min(self.fingers)]
            self.ui.write(e.EV_ABS, e.ABS_X, int(first[0] * self.geo.res))
            self.ui.write(e.EV_ABS, e.ABS_Y, int(first[1] * self.geo.res))
        self.ui.write(e.EV_MSC, e.MSC_TIMESTAMP, self.hw_us)
        self.ui.syn()
        self.hw_us += int(1e6 / self.hz)

    def _tools(self, n: int) -> None:
        e = self.e
        tools = [e.BTN_TOOL_FINGER, e.BTN_TOOL_DOUBLETAP, e.BTN_TOOL_TRIPLETAP,
                 e.BTN_TOOL_QUADTAP, e.BTN_TOOL_QUINTTAP]
        for i, code in enumerate(tools):
            self.ui.write(e.EV_KEY, code, 1 if n == i + 1 else 0)
        self.ui.write(e.EV_KEY, e.BTN_TOUCH, 1 if n else 0)

    def _pos(self, slot: int, x: float, y: float) -> None:
        e = self.e
        self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
        self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, int(round(x * self.geo.res)))
        self.ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, int(round(y * self.geo.res)))

    def frame_sleep(self) -> None:
        time.sleep(1.0 / self.hz)

    # -- high level -------------------------------------------------------------------
    def put(self, *points: tuple[float, float]) -> None:
        """Land fingers at the given mm positions (in free slots)."""
        e = self.e
        for x, y in points:
            slot = next(i for i in range(5) if i not in self.fingers)
            self._tid += 1
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, self._tid)
            self._pos(slot, x, y)
            self.fingers[slot] = (x, y)
        self._tools(len(self.fingers))
        self._sync()
        self.frame_sleep()

    def move(self, dx: float, dy: float, duration: float, slots=None) -> None:
        """Move fingers by (dx, dy) mm over ``duration`` seconds at the frame rate."""
        slots = list(self.fingers) if slots is None else list(slots)
        steps = max(1, int(round(duration * self.hz)))
        start = {s: self.fingers[s] for s in slots}
        for k in range(1, steps + 1):
            for s in slots:
                x0, y0 = start[s]
                x, y = x0 + dx * k / steps, y0 + dy * k / steps
                self._pos(s, x, y)
                self.fingers[s] = (x, y)
            self._sync()
            self.frame_sleep()

    def spread(self, factor: float, duration: float) -> None:
        """Scale finger distances around their centroid (pinch out > 1 > pinch in)."""
        steps = max(1, int(round(duration * self.hz)))
        start = dict(self.fingers)
        cx = sum(p[0] for p in start.values()) / len(start)
        cy = sum(p[1] for p in start.values()) / len(start)
        for k in range(1, steps + 1):
            f = 1 + (factor - 1) * k / steps
            for s, (x0, y0) in start.items():
                x, y = cx + (x0 - cx) * f, cy + (y0 - cy) * f
                self._pos(s, x, y)
                self.fingers[s] = (x, y)
            self._sync()
            self.frame_sleep()

    def hold(self, duration: float) -> None:
        steps = max(1, int(round(duration * self.hz)))
        for _ in range(steps):
            self._sync()
            self.frame_sleep()

    def lift(self, slots=None) -> None:
        e = self.e
        slots = list(self.fingers) if slots is None else list(slots)
        for s in slots:
            self.ui.write(e.EV_ABS, e.ABS_MT_SLOT, s)
            self.ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
            del self.fingers[s]
        self._tools(len(self.fingers))
        self._sync()
        self.frame_sleep()

    @property
    def node(self) -> str | None:
        dev = getattr(self.ui, "device", None)
        return dev.path if dev else None

    def close(self) -> None:
        try:
            self.ui.close()
        except Exception:
            pass


def wait_x_device(x: xi.XInput, name: str, timeout: float = 5.0) -> xi.XDevice | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        dev = x.find(name)
        if dev is not None:
            return dev
        time.sleep(0.05)
    return None


class RawCollector:
    """Sums XI2 raw valuator deltas from one (floated) device."""

    def __init__(self, x: xi.XInput, devid: int):
        self.x, self.devid = x, devid
        x.select(x.d.screen().root, [(devid, xi.xinput.RawMotionMask)])
        self.drain(0.1)

    def drain(self, timeout: float = 0.15) -> dict[int, float]:
        tot: dict[int, float] = {}
        end = time.monotonic() + timeout
        d = self.x.d
        while True:
            left = end - time.monotonic()
            if left <= 0 and not d.pending_events():
                break
            if not d.pending_events():
                select.select([d.fileno()], [], [], max(0.0, min(0.02, left)))
            while d.pending_events():
                ev = d.next_event()
                if getattr(ev, "evtype", None) == xi.xinput.RawMotion:
                    r = xi.parse_raw_event(ev.evtype, ev.data)
                    if r.deviceid == self.devid:
                        for a, v in r.values.items():
                            tot[a] = tot.get(a, 0.0) + v
        return tot


def stroke(vt: VirtualTouchpad, coll: RawCollector, fingers: int, dx: float, dy: float,
           speed: float, warmup_mm: float = 8.0) -> dict[int, float]:
    """Move ``fingers`` by (dx, dy) mm at a constant speed and return libinput's output.

    libinput estimates speed from the last few events, so the first frames of a
    stroke come out slower. A short warm-up at the same speed is moved first
    and not counted: the result is the steady-state response.
    """
    dist = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / dist, dy / dist
    vt.put(*[(15.0 + 14 * i, 15.0) for i in range(fingers)])
    vt.move(ux * warmup_mm, uy * warmup_mm, warmup_mm / speed)
    coll.drain(0.0)
    vt.move(dx, dy, dist / speed)
    out = coll.drain(0.04)        # finger still down and still: no new events, just latency
    vt.lift()
    coll.drain(0.15)
    return out

