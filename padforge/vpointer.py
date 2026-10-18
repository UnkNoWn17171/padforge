"""Virtual pointer (uinput) for smooth kinetic scrolling and 3-finger drag.

High-resolution wheel events give pixel-smooth scrolling in X applications,
which XTest's discrete buttons 4/5 cannot. Needs write access to /dev/uinput;
without it the daemon falls back to XTest.
"""
from __future__ import annotations

import os

NAME = "PadForge Virtual Pointer"
UINPUT = "/dev/uinput"

# Measured on xf86-input-libinput 1.4 (tests/integration): +120 REL_WHEEL_HI_RES
# moves the X vertical scroll valuator by -120 (wheel "up"), horizontal keeps
# its sign. X valuator positive = scroll down / right.
VERTICAL_SIGN = -1
HORIZONTAL_SIGN = 1


def available() -> bool:
    return os.access(UINPUT, os.R_OK | os.W_OK)


class VirtualPointer:
    def __init__(self, name: str = NAME):
        import evdev
        from evdev import ecodes as e
        self.e = e
        cap = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                       e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
        }
        self.ui = evdev.UInput(cap, name=name, vendor=0x1209, product=0x9adf, version=1,
                               bustype=e.BUS_VIRTUAL)
        self.name = name
        self._wheel_acc = [0, 0]      # hi-res units not yet sent as a full click
        self._rem = [0.0, 0.0]        # sub-pixel motion remainder

    def scroll_units(self, x_units: int, y_units: int) -> None:
        """Scroll by X valuator units (120 = one wheel click), positive = right/down."""
        e = self.e
        wrote = False
        if y_units:
            hi = VERTICAL_SIGN * int(y_units)
            self.ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, hi)
            self._wheel_acc[1] += hi
            clicks = int(self._wheel_acc[1] / 120)
            if clicks:
                self.ui.write(e.EV_REL, e.REL_WHEEL, clicks)
                self._wheel_acc[1] -= clicks * 120
            wrote = True
        if x_units:
            hi = HORIZONTAL_SIGN * int(x_units)
            self.ui.write(e.EV_REL, e.REL_HWHEEL_HI_RES, hi)
            self._wheel_acc[0] += hi
            clicks = int(self._wheel_acc[0] / 120)
            if clicks:
                self.ui.write(e.EV_REL, e.REL_HWHEEL, clicks)
                self._wheel_acc[0] -= clicks * 120
            wrote = True
        if wrote:
            self.ui.syn()

    def move(self, dx: float, dy: float) -> None:
        """Relative motion in screen pixels (fractions are carried over)."""
        e = self.e
        self._rem[0] += dx
        self._rem[1] += dy
        ix, iy = int(self._rem[0]), int(self._rem[1])
        if not ix and not iy:
            return
        self._rem[0] -= ix
        self._rem[1] -= iy
        if ix:
            self.ui.write(e.EV_REL, e.REL_X, ix)
        if iy:
            self.ui.write(e.EV_REL, e.REL_Y, iy)
        self.ui.syn()

    def button(self, button: int, down: bool) -> None:
        e = self.e
        code = {1: e.BTN_LEFT, 2: e.BTN_MIDDLE, 3: e.BTN_RIGHT}[button]
        self.ui.write(e.EV_KEY, code, 1 if down else 0)
        self.ui.syn()

    def close(self) -> None:
        try:
            self.ui.close()
        except Exception:
            pass
