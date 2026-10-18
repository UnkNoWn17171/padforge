"""GUI side helpers: talk to the daemon, control the systemd unit, read touches live."""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

from gi.repository import GLib

from .. import config, device, diagnostics
from ..gestures import GestureRecognizer
from ..stats import TouchStats
from ..tap import TapEngine

UNIT = "padforge.service"


def async_call(fn, callback) -> None:
    """Run ``fn()`` in a worker thread, deliver the result to ``callback`` on the GLib loop."""
    def work():
        try:
            res = fn()
        except Exception as exc:          # noqa: BLE001 - surfaced to the UI
            res = exc
        GLib.idle_add(lambda: (callback(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


def daemon_status() -> dict | None:
    r = diagnostics.daemon_request({"cmd": "status"}, timeout=0.5)
    return r.get("status") if r and r.get("ok") else None


def daemon_command(msg: dict) -> dict | None:
    return diagnostics.daemon_request(msg, timeout=0.8)


def systemctl(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                           timeout=8)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def unit_state() -> dict:
    rc_active, active = systemctl("is-active", UNIT)
    rc_enabled, enabled = systemctl("is-enabled", UNIT)
    return {"active": active == "active", "enabled": enabled == "enabled",
            "installed": enabled not in ("", "not-found") and "No such file" not in enabled
            and "not-found" not in enabled}


def journal(lines: int = 120) -> str:
    try:
        p = subprocess.run(["journalctl", "--user", "-u", UNIT, "-n", str(lines), "--no-pager",
                            "-o", "short-iso"], capture_output=True, text=True, timeout=8)
        return p.stdout.strip() or p.stderr.strip() or "(boş)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)


class PauseHandle:
    """Keeps the daemon from running gesture actions while the connection is open."""

    def __init__(self):
        self.sock: socket.socket | None = None

    def acquire(self) -> bool:
        if self.sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(str(config.socket_path()))
            s.sendall(b'{"cmd": "pause", "value": true}\n')
            s.recv(4096)
            self.sock = s
            return True
        except OSError:
            return False

    def release(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class LiveReader:
    """Reads one touchpad in the GUI process and runs dry-run recognisers on it."""

    def __init__(self, info: device.TouchpadInfo, cfg: dict, on_frame, on_event):
        self.info = info
        self.reader = device.TouchpadReader(info)
        self.stats = TouchStats()
        self.on_frame, self.on_event = on_frame, on_event
        self.configure(cfg)
        self.watch = GLib.io_add_watch(self.reader.fd, GLib.PRIORITY_HIGH,
                                       GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._io)
        self._tap_timer = 0
        self.finger_speed: float | None = None
        self._last_single: tuple[float, float, float, int] | None = None

    def configure(self, cfg: dict) -> None:
        from ..actions import CONTINUOUS
        keys = {k for k, b in cfg["gestures"]["bindings"].items() if b["action"] in CONTINUOUS}
        self.recognizer = GestureRecognizer.from_config(cfg["gestures"], keys)
        self.tap = TapEngine.from_config(cfg["tap"], cfg["click"]["left_handed"],
                                         cfg["tap"]["engine"] == "padforge")
        if not (cfg["tap"]["enabled"] and cfg["tap"]["engine"] == "padforge"):
            self.tap.fingers = {1, 2, 3, 4}     # still show what a tap would be
        self.cfg = cfg

    def _io(self, fd, cond) -> bool:
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self.on_event("Touchpad bağlantısı koptu")
            self.watch = 0                  # returning False removes it; close() must not
            return False
        now = time.monotonic()
        try:
            for f in self.reader.frames():
                self.stats.feed(f, now)
                self._speed(f)
                for g in self.recognizer.feed(f):
                    self._gesture(g)
                for t in self.tap.feed(f, None, self.recognizer.active):
                    self._tap(t)
                self.on_frame(f)
        except OSError:
            self.on_event("Okuma hatası")
            self.watch = 0
            return False
        self._schedule_tap()
        return True

    def _speed(self, f) -> None:
        fingers = f.fingers
        if len(fingers) != 1:
            self._last_single = None
            self.finger_speed = None
            return
        t = fingers[0]
        if self._last_single and self._last_single[3] == t.tracking_id:
            lt, lx, ly, _ = self._last_single
            dt = f.time - lt
            if dt > 0:
                v = ((t.x - lx) ** 2 + (t.y - ly) ** 2) ** 0.5 / dt
                self.finger_speed = v if self.finger_speed is None else 0.6 * self.finger_speed + 0.4 * v
        self._last_single = (f.time, t.x, t.y, t.tracking_id)

    def _schedule_tap(self) -> None:
        d = self.tap.deadline()
        if d is None or self._tap_timer:
            return
        delay = max(1, int((d - time.monotonic()) * 1000) + 1)

        def fire():
            self._tap_timer = 0
            for t in self.tap.tick(time.monotonic()):
                self._tap(t)
            self._schedule_tap()
            return False
        self._tap_timer = GLib.timeout_add(delay, fire)

    def _gesture(self, g) -> None:
        from ..actions import ACTIONS
        names = {"swipe": "kaydırma", "pinch": "sıkıştırma", "begin": "sürekli"}
        if g.kind in ("swipe", "pinch", "begin"):
            b = self.cfg["gestures"]["bindings"].get(g.key, {"action": "none"})
            label = ACTIONS.get(b["action"]).label if b["action"] in ACTIONS else b["action"]
            arrow = {"up": "↑", "down": "↓", "left": "←", "right": "→", "in": "⇲", "out": "⇱"}
            self.on_event(f"{g.fingers} parmak {names[g.kind]} {arrow.get(g.direction, '')} → {label}",
                          flash=f"{g.fingers}{arrow.get(g.direction, '')}  {label}")
        elif g.kind == "step":
            self.on_event(f"  adım {'+' if g.step > 0 else '−'}")
        elif g.kind == "drag_begin":
            self.on_event("3 parmakla sürükleme başladı", flash="3 parmak sürükleme")
        elif g.kind == "zoom":
            self.on_event("yakınlaştır" if g.step > 0 else "uzaklaştır", flash="yakınlaştırma")

    def _tap(self, t) -> None:
        names = {1: "sol tık", 2: "orta tık", 3: "sağ tık"}
        if t.kind == "click":
            self.on_event(f"{t.fingers} parmak dokunma → {names.get(t.button, t.button)}",
                          flash=f"dokunma: {names.get(t.button, '')}")
        elif t.kind == "press":
            self.on_event("dokun-sürükle: basılı", flash="sürükleme")
        elif t.kind == "release":
            self.on_event("dokun-sürükle: bırakıldı")
        elif t.kind == "action":
            self.on_event("4 parmak dokunma", flash="4 parmak dokunma")

    def close(self) -> None:
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = 0
        if self._tap_timer:
            GLib.source_remove(self._tap_timer)
            self._tap_timer = 0
        self.reader.close()


def copy_text(widget, text: str) -> None:
    widget.get_clipboard().set(text)


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
