"""Short "performance" holds while gestures animate (power-profiles-daemon).

Measured on 2026-10-18 on battery with the balanced profile: GNOME's workspace
and overview animations skipped 7-28 % of the 144 Hz refreshes, because the CPU
and GPU ramp up slowly. Holding the "performance" profile for the duration made
the same animations perfect (0 skipped refreshes). The hold starts when three or
more fingers land, ends a few seconds after the last gesture, and is tied to our
D-Bus connection, so it also ends if the daemon dies.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger("padforge.boost")

APIS = (
    ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
     "org.freedesktop.UPower.PowerProfiles"),
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
)
APP_ID = "io.github.padforge.PadForge"
REASON = "Touchpad hareketi animasyonu"


_SYSTEM_BUS = None


def system_bus():
    """The system bus connection, kept alive for the whole process.

    power-profiles-daemon ties a hold to the caller's connection. GIO keeps only a
    weak reference to the shared connection, so a local variable is not enough:
    when it went out of scope the connection closed and the hold was dropped at
    once (seen 2026-10-18: cookie returned, profile stayed "balanced").
    """
    global _SYSTEM_BUS
    if _SYSTEM_BUS is None:
        from gi.repository import Gio
        _SYSTEM_BUS = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    return _SYSTEM_BUS


def _dbus_call(api: tuple[str, str, str], method: str, args: tuple):
    from gi.repository import Gio, GLib
    bus = system_bus()
    sig = "(sss)" if method == "HoldProfile" else "(u)"
    reply = bus.call_sync(api[0], api[1], api[2], method, GLib.Variant(sig, args), None,
                          Gio.DBusCallFlags.NONE, 400, None)
    return reply.unpack()[0] if reply is not None and reply.n_children() else None


class Boost:
    def __init__(self, seconds: float = 8.0, call: Callable = _dbus_call,
                 clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self.call = call
        self.clock = clock
        self.cookie: int | None = None
        self.api = None
        self.until = 0.0
        self.available = True
        self.enabled = True

    @property
    def active(self) -> bool:
        return self.cookie is not None

    def hold(self) -> None:
        """Ask for performance now, or extend a running hold."""
        if not (self.enabled and self.available):
            return
        self.until = self.clock() + self.seconds
        if self.cookie is not None:
            return
        for api in APIS:
            try:
                self.cookie = self.call(api, "HoldProfile", ("performance", REASON, APP_ID))
                self.api = api
                log.debug("performans tutuldu (%s)", api[0])
                return
            except Exception as exc:          # noqa: BLE001 - try the next name
                last = exc
        self.available = False
        log.info("güç profili tutulamadı, hızlandırma kapalı: %s", last)

    def tick(self) -> None:
        """Release the hold once the gestures are over."""
        if self.cookie is not None and self.clock() >= self.until:
            self.release()

    def release(self) -> None:
        if self.cookie is None:
            return
        cookie, self.cookie = self.cookie, None
        try:
            self.call(self.api, "ReleaseProfile", (cookie,))
            log.debug("performans bırakıldı")
        except Exception as exc:              # noqa: BLE001 - ppd drops it with us anyway
            log.debug("bırakılamadı: %s", exc)
