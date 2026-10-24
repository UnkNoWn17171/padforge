"""External mouse buttons: give the extra ones (side buttons, wheel click) an action.

A button whose entry in the device's button map is 0 never reaches applications, but
XI2 raw events still report it with its physical number (measured on this Xorg,
2026-10-19). So the daemon zeroes only the buttons that have an action, only on the
external mice, and runs the action from the raw event. Nothing is grabbed, no extra
permission is needed, and restoring the map gives the button back.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .actions import CONTINUOUS, VERTICAL_DOWN_IS_NEXT
from .device import MouseInfo

LABELS = {2: "Orta tuş (tekerlek tıklaması)", 8: "Yan tuş · Geri", 9: "Yan tuş · İleri"}

# "Hold" buttons act as a key while held: Forward as Super gives Super + wheel (window
# menu), Super + drag (move a window), and a plain tap opens Activities.
HOLD_KEYS = {"hold_super": "Super_L", "hold_ctrl": "Control_L", "hold_shift": "Shift_L",
             "hold_alt": "Alt_L"}
MODE_LABELS = {
    "gesture": "Hareket tuşu (dokun · basılı tut + tekerlek / yön)",
    "hold_super": "Basılıyken Super (tekerlek = pencere menüsü, sürükle = taşı)",
    "hold_ctrl": "Basılıyken Ctrl (tekerlek = yakınlaştır, tık = yeni sekme)",
    "hold_shift": "Basılıyken Shift (tekerlek = yatay kaydır)",
    "hold_alt": "Basılıyken Alt",
}
PART_LABELS = {"tap": "Dokununca", "wheel": "Basılı tut + tekerlek",
               "up": "Basılı tut + yukarı it", "down": "Basılı tut + aşağı it",
               "left": "Basılı tut + sola it", "right": "Basılı tut + sağa it"}

FLICK_PX = 80        # cursor travel that turns a hold into a flick (up/down/left/right)
TAP_S = 0.5          # a press this short with nothing else done is a tap
MAX_HOLD_S = 30.0    # safety net: a hold this long means its release was missed


def gesture_defaults(button: int) -> dict:
    """A gesture button that still does its own job when tapped."""
    tap = {8: "back", 9: "forward", 2: "middle_click"}.get(button, "none")
    return {"action": "gesture", "tap": {"action": tap}, "wheel": {"action": "window_cycle"},
            "up": {"action": "overview"}, "down": {"action": "show_desktop"},
            "left": {"action": "workspace_prev"}, "right": {"action": "workspace_next"}}


def label(button: int) -> str:
    return LABELS.get(button, f"Ek tuş {button}")


def base_map(current: list[int], ours: set[int]) -> list[int]:
    """The map to give back: ``current`` with the buttons we switched off restored.

    ``ours`` comes from the cache, so zeros left by a crashed daemon are undone too,
    while a button the user switched off with ``xinput set-button-map`` stays off.
    """
    return [i + 1 if i + 1 in ours else v for i, v in enumerate(current)]


def wanted_map(base: list[int], assigned: set[int]) -> list[int]:
    """``base`` with the assigned physical buttons switched off (0)."""
    return [0 if i + 1 in assigned else v for i, v in enumerate(base)]


@dataclass
class Mouse:
    info: MouseInfo
    xid: int
    base: list[int]              # map to give back
    applied: list[int]           # map the X server has now

    held: set = field(default_factory=set)       # physical buttons down right now
    grabbed: set = field(default_factory=set)    # gesture buttons with a passive grab

    def hidden(self, button: int) -> bool:
        """Is ``button`` hidden from applications (so the daemon runs its action)?"""
        return 1 <= button <= len(self.applied) and self.applied[button - 1] == 0


class GestureHold:
    """One press of a gesture button, from press to release (pure; the daemon runs it).

    The methods return what to do: ``("run", binding)``, ``("begin", binding, inc)``,
    ``("step", inc)`` or ``("end",)`` for a continuous action that the release closes.
    """

    def __init__(self, button: int, binding: dict, x: float, y: float, t: float):
        self.button, self.binding = button, binding
        self.x0, self.y0, self.t0 = x, y, t
        self.used = False            # wheel turned or flicked: the release is no tap
        self.flicked = False
        self.wheeled = False
        self.cont = False            # a continuous action began (window menu, volume...)
        self.notches = 0             # for the log line at the release
        self.travel = 0.0
        self.others = 0              # other buttons pressed meanwhile
        # Flicks are measured from the first motion event, not from the press: the
        # detached mouse's own pointer starts elsewhere, and its first event jumped
        # 320 px on the real mouse (seen 2026-10-24) and fired a bogus "left".
        self.mx0: float | None = None
        self.my0: float | None = None
        self.trail: list[str] = []   # event trail for the log ("M+15ms", "W5+120ms", ...)

    def note(self, what: str, t: float) -> None:
        if len(self.trail) < 24:
            self.trail.append(f"{what}+{(t - self.t0) * 1000:.0f}ms")

    def _part(self, name: str) -> dict:
        return self.binding.get(name) or {"action": "none"}

    def move(self, x: float, y: float) -> list[tuple]:
        if self.mx0 is None:
            self.mx0, self.my0 = x, y
            return []
        dx, dy = x - self.mx0, y - self.my0
        self.travel = max(self.travel, abs(dx), abs(dy))
        if self.flicked or self.wheeled:       # no surprise flick while turning the wheel
            return []
        if max(abs(dx), abs(dy)) < FLICK_PX:
            return []
        self.flicked = self.used = True
        if abs(dx) >= abs(dy):
            part = "right" if dx > 0 else "left"
        else:
            part = "down" if dy > 0 else "up"
        b = self._part(part)
        return [("run", b)] if b["action"] != "none" else []

    def wheel(self, down: bool) -> list[tuple]:
        self.used = self.wheeled = True
        self.notches += 1
        b = self._part("wheel")
        action = b["action"]
        if action == "none":
            return []
        if action not in CONTINUOUS:
            return [("run", b)]
        if action in VERTICAL_DOWN_IS_NEXT:
            inc = 1 if down else -1            # like a list: down = next
        else:
            inc = -1 if down else 1            # volume, zoom: up = more
        if not self.cont:
            self.cont = True
            return [("begin", b, inc)]
        return [("step", inc)]

    def summary(self, t: float, acts: list[tuple]) -> str:
        """One log line per press: enough to tell a short press from a lost wheel."""
        if acts and acts[0][0] == "end":
            result = "sürekli eylem bitti (ör. pencere seçildi)"
        elif acts:
            result = f"{acts[0][1]['action']}"
        elif self.flicked or self.notches:
            result = "yön/tekerlek işlendi"
        else:
            result = "hiçbir şey (dokunma için uzun)" if t - self.t0 > TAP_S else "hiçbir şey"
        return (f"{t - self.t0:.2f} sn · tekerlek {self.notches} çentik · hareket "
                f"{self.travel:.0f} px · başka tuş {self.others} → {result} · "
                f"olaylar: {' '.join(self.trail) or '-'}")

    def release(self, t: float) -> list[tuple]:
        if self.cont:
            return [("end",)]                  # e.g. let go of Super: the menu picks
        if not self.used and t - self.t0 <= TAP_S:
            b = self._part("tap")
            return [("run", b)] if b["action"] != "none" else []
        return []
