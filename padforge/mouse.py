"""External mouse buttons: give the extra ones (side buttons, wheel click) an action.

A button whose entry in the device's button map is 0 never reaches applications, but
XI2 raw events still report it with its physical number (measured on this Xorg,
2026-10-19). So the daemon zeroes only the buttons that have an action, only on the
external mice, and runs the action from the raw event. Nothing is grabbed, no extra
permission is needed, and restoring the map gives the button back.
"""
from __future__ import annotations

from dataclasses import dataclass

from .device import MouseInfo

LABELS = {2: "Orta tuş (tekerlek tıklaması)", 8: "Yan tuş · Geri", 9: "Yan tuş · İleri"}


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

    def hidden(self, button: int) -> bool:
        """Is ``button`` hidden from applications (so the daemon runs its action)?"""
        return 1 <= button <= len(self.applied) and self.applied[button - 1] == 0
