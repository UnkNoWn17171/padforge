"""PadForge daemon.

One GLib main loop, single-threaded:

* applies the config to every touchpad's libinput properties (and keeps GNOME's
  keys in sync), re-applying when a device appears or someone overwrites them;
* reads raw touches from evdev and runs the gesture recognizer, the
  zero-latency tap engine and kinetic scrolling;
* serves a small JSON-lines socket for the GUI (status, pause, reload, test).
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402
from Xlib import error as xerror  # noqa: E402

from . import APP_ID, __version__, accel, config, device, gnome, kinetic, touchegg, vpointer  # noqa: E402
from . import mouse, wm  # noqa: E402
from .boost import Boost  # noqa: E402
from .wmdrag import SuperDrag  # noqa: E402
from . import xinput as xi  # noqa: E402
from .actions import CONTINUOUS, VERTICAL_DOWN_IS_NEXT, Executor  # noqa: E402
from .gestures import GestureEvent, GestureRecognizer  # noqa: E402
from .stats import TouchStats  # noqa: E402
from .tap import TapEngine, TapEvent  # noqa: E402

log = logging.getLogger("padforge")

REAPPLY_DELAY_MS = 120
RESCAN_DELAY_MS = 350
COAST_TICK_MS = 7                # ~144 Hz, matches the panel
MAX_REAPPLY_PER_10S = 10


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "padforge" / "devices.json"


def device_key(info: device.TouchpadInfo) -> str:
    return f"{info.vendor:04x}:{info.product:04x}:{info.name}"


def load_cache() -> dict:
    try:
        return json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return {}


def save_cache(data: dict) -> None:
    try:
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        cache_path().write_text(json.dumps(data, indent=2))
    except OSError:
        pass


class Pad:
    """One physical touchpad: its reader and per-device engines."""

    def __init__(self, info: device.TouchpadInfo, reader: device.TouchpadReader | None):
        self.info = info
        self.reader = reader
        self.watch: int | None = None
        self.xid: int | None = None
        self.stats = TouchStats()
        self.recognizer: GestureRecognizer | None = None
        self.tap: TapEngine | None = None
        self.tracker: kinetic.ScrollTracker | None = None
        self.ratio: kinetic.RatioMeter | None = None
        self.error = ""
        self.last_count = 0
        self.last_time = 0.0

    @property
    def res(self) -> float:
        return self.reader.resolution if self.reader else 0.0


class Daemon:
    def __init__(self, dry_run: bool = False, config_file: Path | None = None):
        self.dry_run = dry_run
        self.config_file = config_file or config.config_path()
        self.cfg = config.load(self.config_file)
        self.loop = GLib.MainLoop()
        self.started = time.monotonic()
        self.errors: list[str] = []
        self.events: list[tuple[float, str]] = []
        self.pads: dict[str, Pad] = {}
        self.cache = load_cache()
        self.last_key_time: float | None = None
        self.pause_clients: set[int] = set()
        self._self_write_until = 0.0
        self._reapply_times: list[float] = []
        self._timers: dict[str, int] = {}
        self._clients: dict[int, tuple[socket.socket, int, bytearray]] = {}
        self._server: socket.socket | None = None
        self._applied_tapping_off = False
        self.vp: vpointer.VirtualPointer | None = None
        self.vp_xid: int | None = None
        self.coaster = kinetic.Coaster()
        self.boost = Boost()
        self.wmdrag = SuperDrag()
        self._coast_pad: Pad | None = None
        self._xtest_wheel = [0.0, 0.0]
        self._drag_pressed = False
        self.mice: dict[int, mouse.Mouse] = {}      # external mice by X device id

        self.x = xi.XInput()
        self.x_alive = True
        self.root = self.x.d.screen().root
        self.xtest_ids: set[int] = set()
        self.executor = Executor(self.x.d, dry_run=dry_run)
        self.gnome = gnome.GnomeSync(self._gnome_changed)
        self.touchegg = touchegg.client_running()
        self._update_engine_params()

    # ------------------------------------------------------------------ utils
    def error(self, msg: str) -> None:
        """Record a warning once; repeats (every rescan) are not re-logged."""
        if any(e.endswith(msg) for e in self.errors):
            return
        log.warning(msg)
        self.errors.append(f"{time.strftime('%H:%M:%S')} {msg}")
        del self.errors[:-30]

    def clear_errors(self, needle: str) -> None:
        """Drop warnings that no longer apply (e.g. access granted, Touchégg closed)."""
        self.errors = [e for e in self.errors if needle not in e]

    def event(self, msg: str) -> None:
        log.info(msg)
        self.events.append((time.time(), msg))
        del self.events[:-40]

    def _timer(self, name: str, ms: int, fn) -> None:
        """(Re)schedule a one-shot named timer."""
        old = self._timers.pop(name, None)
        if old:
            GLib.source_remove(old)

        def fire():
            self._timers.pop(name, None)
            try:
                fn()
            except Exception:
                log.exception("zamanlayıcı %s", name)
            self._flush_x()
            return False
        self._timers[name] = GLib.timeout_add(max(0, int(ms)), fire)

    def _flush_x(self) -> None:
        """python-xlib may queue events while waiting for replies; process them."""
        if not self.x_alive:
            return
        try:
            self.x.d.flush()
            while self.x.d.pending_events():
                self._x_event(self.x.d.next_event())
        except xerror.ConnectionClosedError:
            self._x_lost()
        except Exception:
            log.exception("X olayları")

    def _x_lost(self) -> None:
        """The X server went away (logout, crash): quit instead of spinning on a dead socket.

        The X watch is HIGH priority: an always-readable dead socket would also starve
        the SIGTERM handler and hold up the logout until systemd's stop timeout.
        """
        if self.x_alive:
            self.x_alive = False
            log.warning("X bağlantısı koptu; servis kapanıyor")
            self.loop.quit()

    # ----------------------------------------------------------------- startup
    def start(self) -> None:
        # First: a second copy must stop before it touches devices or settings.
        self._start_server()
        for dev in self.x.d.xinput_query_device(xi.xinput.AllDevices).devices:
            if "XTEST" in dev.name:
                self.xtest_ids.add(dev.deviceid)
        self._select_x()
        GLib.io_add_watch(self.x.d.fileno(), GLib.PRIORITY_HIGH,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_x)
        self.rescan()
        self._ensure_vpointer()
        self.apply_all()
        self._watch_config()
        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.stop)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGHUP, self._sighup)
        GLib.timeout_add_seconds(10, self._check_touchegg)
        GLib.timeout_add(1000, self._watchdog)
        if self.touchegg:
            self.error("Touchégg çalışıyor: çift tetiklenmeyi önlemek için PadForge hareketleri "
                       "ve gecikmesiz dokunma bekletildi. Kapatmak: padforge disable-touchegg")
        self.event(f"PadForge {__version__} başladı ({len(self.pads)} touchpad)")

    def run(self) -> None:
        self.start()
        try:
            self.loop.run()
        finally:
            self.cleanup()

    def stop(self) -> bool:
        self.loop.quit()
        return False

    def _sighup(self) -> bool:
        self.reload()
        return True

    def cleanup(self) -> None:
        if self.x_alive:            # with X gone there is nothing left to release or restore
            try:
                self._stop_coast()
                for pad in self.pads.values():
                    if pad.tap:
                        self._tap_events(pad, pad.tap.cancel(time.monotonic()))
                self._release_held("kapanış")
                self._restore_mice()         # the extra buttons work as usual again
                # Leave the user with working taps when PadForge is not running.
                if self._applied_tapping_off and self.cfg["tap"]["enabled"]:
                    for pad in self.pads.values():
                        if pad.xid is not None:
                            try:
                                self.x.set_prop(pad.xid, xi.P_TAP, [1])
                            except Exception:
                                pass
                self.x.d.sync()
            except xerror.ConnectionClosedError:
                log.warning("X bağlantısı kapanırken koptu")
            except Exception:
                log.exception("temizlik")
        self.boost.release()
        if self.vp:
            self.vp.close()
        if self._server:
            try:
                self._server.close()
                os.unlink(config.socket_path())
            except OSError:
                pass

    # ------------------------------------------------------------------ config
    def effective_config(self) -> dict:
        """The config as actually run: Touchégg running means it owns gestures and taps."""
        if not self.touchegg:
            return self.cfg
        eff = config.deep_merge(self.cfg, {"gestures": {"enabled": False,
                                                         "three_finger_drag": False},
                                           "tap": {"engine": "libinput"}})
        return eff

    def _check_touchegg(self) -> bool:
        running = touchegg.client_running()
        if running != self.touchegg:
            self.touchegg = running
            if not running:
                self.clear_errors("Touchégg çalışıyor")
            self.event("Touchégg " + ("çalışıyor: PadForge hareketleri bekletildi" if running
                                      else "kapandı: PadForge hareketleri etkin"))
            self._update_engine_params()
            self.apply_all()
        return True

    def _update_engine_params(self) -> None:
        self.eff = c = self.effective_config()
        self.boost.enabled = bool(c["gestures"]["enabled"] and c["gestures"]["boost"])
        move_b, resize_b = wm.wm_buttons()
        for e in self.wmdrag.cancel():
            self._wm_event(e)
        self.wmdrag = SuperDrag(move=c["window"]["super_move"], resize=c["window"]["super_resize"],
                                move_button=move_b, resize_button=resize_b)
        if not self.boost.enabled:
            self.boost.release()
        k = c["scroll"]["kinetic"]
        self.coaster.friction = k["friction"]
        self.coaster.max_duration = k["max_duration"]
        self.gain = accel.gain_function(
            c["pointer"]["curve"] if c["pointer"]["profile"] == "custom"
            else {"preset": "linux", "scale": 1.0})
        bindings = c["gestures"]["bindings"]
        self.continuous_keys = {key for key, b in bindings.items() if b["action"] in CONTINUOUS}
        for pad in self.pads.values():
            self._build_engines(pad)

    def _build_engines(self, pad: Pad) -> None:
        c = self.eff
        if pad.tap:
            self._tap_events(pad, pad.tap.cancel(time.monotonic()))
        if pad.recognizer is not None:
            # A reload during a 3-finger drag or app switch must not leave the
            # button / modifier held (this froze the desktop once: 2026-10-18).
            for g in pad.recognizer.reset():
                self._gesture(g)
            self._release_held("ayar yeniden yüklendi")
        pad.recognizer = GestureRecognizer.from_config(c["gestures"], self.continuous_keys)
        pad.tap = TapEngine.from_config(c["tap"], c["click"]["left_handed"],
                                        c["tap"]["engine"] == "padforge")
        if c["window"]["title_drag"]:
            pad.tap.title_probe = self._title_probe
        pad.tracker = kinetic.ScrollTracker(c["scroll"]["kinetic"]["min_speed"])
        pad.ratio = kinetic.RatioMeter(self._theory_units_per_mm(pad))

    def _theory_units_per_mm(self, pad: Pad) -> float:
        s = self.cfg["scroll"]
        k = accel.LIBINPUT_SCROLL_PX_PER_MM * s["speed"] / 15.0 * kinetic.UNITS_PER_CLICK
        return -k if s["natural"] else k

    def reload(self) -> None:
        try:
            new = config.read(self.config_file)
        except (OSError, ValueError) as exc:
            # Half-written by an editor, or broken: keep running the last good config.
            self.error(f"ayar dosyası okunamadı, önceki ayarla devam: {exc}")
            return
        self.clear_errors("ayar dosyası okunamadı")
        if new == self.cfg:
            return
        self.cfg = new
        self._reapply_times.clear()
        self.clear_errors("sürekli değiştiriyor")
        self._update_engine_params()
        self._ensure_vpointer()
        self.apply_all()
        self.event("ayarlar yeniden yüklendi")

    def _watch_config(self) -> None:
        f = Gio.File.new_for_path(str(self.config_file))
        self._monitor = f.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", lambda *a: self._timer("reload", 150, self.reload))

    def _gnome_changed(self, key: str, value: Any) -> None:
        new = gnome.adopt(self.cfg, key, value)
        if new is None:
            return
        self.event(f"GNOME ayarı benimsendi: {key}={value}")
        self.cfg = config.validate(new)
        try:
            config.save(self.cfg, self.config_file)
        except OSError as exc:
            self.error(f"ayar kaydedilemedi: {exc}")
        self._update_engine_params()
        self._timer("apply", REAPPLY_DELAY_MS, self.apply_all)

    # --------------------------------------------------------------- devices
    def _matches(self, name: str) -> bool:
        if name.startswith("PadForge"):      # our own virtual/test devices
            return False
        m = self.cfg["device"]["match"]
        return m == "auto" or m.lower() in name.lower()

    def rescan(self) -> None:
        infos = {i.node: i for i in device.list_touchpads()}
        alldev = self.x.devices()
        xdevs = {d.node: d for d in alldev if d.is_touchpad and d.node}
        # Drop pads that disappeared.
        for node in list(self.pads):
            if node not in infos:
                self._drop_pad(self.pads[node])
        for node, info in infos.items():
            if not self._matches(info.name):
                continue
            pad = self.pads.get(node)
            if pad is None or pad.reader is None:
                reader, err = None, ""
                if info.readable:
                    try:
                        reader = device.TouchpadReader(info)
                    except Exception as exc:
                        err = f"{info.name}: açılamadı ({exc})"
                else:
                    from .diagnostics import helper_script
                    err = (f"{info.name}: {node} okunamıyor — dokunma özellikleri kapalı. "
                           f"Kurulum: sudo bash {helper_script()}")
                if pad is None:
                    pad = Pad(info, reader)
                    self.pads[node] = pad
                    self._build_engines(pad)
                else:
                    pad.reader = reader
                pad.error = err
                if err:
                    self.error(err)
                else:
                    self.clear_errors(f"{node} okunamıyor")
                if reader is not None:
                    pad.watch = GLib.io_add_watch(
                        reader.fd, GLib.PRIORITY_HIGH, GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                        self._on_pad, pad)
                    self.cache[device_key(info)] = {
                        "res": reader.resolution, "res_y": reader.axis_y.res,
                        "width_mm": reader.axis_x.size_mm, "height_mm": reader.axis_y.size_mm,
                        "slots": reader.nslots}
                    save_cache(self.cache)
                    self.event(f"{info.name}: {reader.axis_x.size_mm:.0f}×"
                               f"{reader.axis_y.size_mm:.0f} mm, {reader.resolution:g} birim/mm")
            xd = xdevs.get(node)
            pad.xid = xd.id if xd else None
        self._scan_mice(alldev)
        self._select_x()

    def _drop_pad(self, pad: Pad) -> None:
        if pad.watch:
            GLib.source_remove(pad.watch)
            pad.watch = None
        if pad.tap:
            self._tap_events(pad, pad.tap.cancel(time.monotonic()))
        if pad.recognizer is not None:
            for g in pad.recognizer.reset():      # e.g. window menu open: let go of Super now
                self._gesture(g)
        if pad.reader:
            pad.reader.close()
        self.pads.pop(pad.info.node, None)
        if all(p.last_count == 0 for p in self.pads.values()):
            self._release_held(f"{pad.info.name} ayrıldı")
        self.event(f"{pad.info.name} ayrıldı")

    # ------------------------------------------------------------------- mice
    def _mouse_cache(self, name: str) -> dict:
        return self.cache.setdefault("mice", {}).setdefault(name, {})

    def _scan_mice(self, alldev) -> None:
        """Find the external mice and read their button maps (start, plug, unplug)."""
        infos = {m.node: m for m in device.list_mice()}
        found: dict[int, mouse.Mouse] = {}
        for d in alldev:
            info = infos.get(d.node or "")
            if info is None or d.floating:
                continue
            try:
                current = self.x.button_map(d.id)
            except Exception as exc:
                self.error(f"{info.name}: tuş eşlemesi okunamadı ({exc})")
                continue
            mc = self._mouse_cache(info.name)
            found[d.id] = mouse.Mouse(info, d.id, mouse.base_map(current, set(mc.get("zeroed", []))),
                                      current)
            if d.id not in self.mice:
                self.event(f"fare: {info.name} ({len(current)} tuş)")
                if not mc.get("announced"):
                    mc["announced"] = True
                    save_cache(self.cache)
                    # Later, not on the start-up path: GNOME's first notification after a
                    # while held the X server for 2.2 s here (measured 2026-10-19).
                    self._timer(f"duyuru {info.name}", 5000, lambda n=info.name: self._notify(
                        f"Fare bulundu: {n}",
                        "Yan tuşlarına ve tekerlek tıklamasına görev atayabilirsin: "
                        "PadForge › Fare. Tuşa bas, hangisi olduğunu PadForge bulur."))
        self.mice = found

    def _apply_mice(self) -> None:
        """Hide the buttons that have an action from applications, on each external mouse."""
        assigned = {int(k) for k in self.cfg["mouse"]["buttons"]}
        busy = False
        for m in self.mice.values():
            want = mouse.wanted_map(m.base, assigned)
            if want == m.applied:
                continue
            try:
                ok = self.x.set_button_map(m.xid, want)
            except Exception as exc:
                self.error(f"{m.info.name}: tuş eşlemesi yazılamadı ({exc})")
                continue
            if not ok:
                busy = True              # a button that changes is held down right now
                continue
            m.applied = want
            self._remember_zeroed(m)
        if busy:
            self._timer("mice", 500, self._apply_mice)

    def _restore_mice(self) -> None:
        for m in self.mice.values():
            if m.applied != m.base:
                try:
                    if self.x.set_button_map(m.xid, m.base):
                        m.applied = list(m.base)
                        self._remember_zeroed(m)
                except Exception:
                    pass

    def _remember_zeroed(self, m: mouse.Mouse) -> None:
        """Note the buttons we switched off, so a crash leftover is undone next time."""
        zeroed = [i + 1 for i, (a, b) in enumerate(zip(m.applied, m.base)) if a == 0 and b]
        mc = self._mouse_cache(m.info.name)
        if mc.get("zeroed", []) != zeroed:
            mc["zeroed"] = zeroed
            save_cache(self.cache)

    def _mouse_button(self, m: mouse.Mouse, button: int) -> None:
        if button == 2 or button >= 8:           # learn which extra buttons really exist
            mc = self._mouse_cache(m.info.name)
            if button not in mc.get("seen", []):
                mc["seen"] = sorted(set(mc.get("seen", [])) | {button})
                save_cache(self.cache)
        b = self.cfg["mouse"]["buttons"].get(str(button))
        if b is None or not m.hidden(button):
            return                               # the application gets it as usual
        self.event(f"fare: {mouse.label(button)} → {b['action']}")
        if not self.pause_clients:
            self.boost.hold()
            self.executor.run(b["action"], b.get("arg"))

    def _notify(self, summary: str, body: str) -> None:
        """Desktop notification; best effort, never waits for an answer."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", ("PadForge", 0, APP_ID, summary, body,
                                                      [], {}, -1)),
                     None, Gio.DBusCallFlags.NONE, 2000, None, None, None)
        except Exception as exc:
            log.debug("bildirim gönderilemedi: %s", exc)

    def _ensure_vpointer(self) -> None:
        # Only useful when some touchpad's raw touches are readable.
        need = ((self.eff["scroll"]["kinetic"]["enabled"] or self.eff["gestures"]["three_finger_drag"])
                and any(p.reader is not None for p in self.pads.values()))
        if need and self.vp is None and vpointer.available() and not self.dry_run:
            try:
                self.vp = vpointer.VirtualPointer()
                self.executor.pointer = self.vp
            except Exception as exc:
                self.error(f"sanal işaretçi oluşturulamadı: {exc}")
        elif not need and self.vp is not None:
            self.vp.close()
            self.vp, self.vp_xid, self.executor.pointer = None, None, None

    def _setup_vpointer_x(self) -> None:
        if self.vp is None:
            return
        dev = self.x.find(self.vp.name)
        if dev is None:
            return
        self.vp_xid = dev.id
        plan = [(xi.P_PROFILE, [0, 1, 0]), (xi.P_SPEED, [0.0]), (xi.P_NATURAL, [0]),
                (xi.P_HIRES, [1]), (xi.P_LEFT_HANDED, [0]), (xi.P_MIDDLE, [0])]
        self._self_write_until = time.monotonic() + 0.4
        errs = self.x.apply(dev.id, plan)
        for e in errs:
            self.error(f"sanal işaretçi: {e}")

    # ----------------------------------------------------------------- apply
    def apply_all(self) -> None:
        self._self_write_until = time.monotonic() + 0.5
        if self.gnome.available:
            try:
                self.gnome.write(gnome.to_gsettings(self.cfg))
            except Exception as exc:
                self.error(f"GNOME ayarları yazılamadı: {exc}")
        tapping_off = False
        cfg = self.eff
        for xd in self.x.touchpads():
            if not self._matches(xd.name):
                continue
            pad = self.pads.get(xd.node or "")
            res = pad.res if pad else 0.0
            if not res and pad:
                res = float(self.cache.get(device_key(pad.info), {}).get("res", 0.0))
            caps = self.x.caps(xd.id, res)
            plan = xi.plan_properties(cfg, caps)
            if (pad is None or pad.reader is None) and cfg["tap"]["engine"] == "padforge":
                # Our tap engine cannot see this pad: let libinput tap instead.
                plan = [(n, [int(cfg["tap"]["enabled"])] if n == xi.P_TAP else v)
                        for n, v in plan]
            if dict(plan).get(xi.P_TAP) == [0] and cfg["tap"]["enabled"]:
                tapping_off = True
            errs = self.x.apply(xd.id, plan)
            for e in errs:
                self.error(f"{xd.name}: {e}")
            if pad:
                pad.xid = xd.id
        self._applied_tapping_off = tapping_off
        self._setup_vpointer_x()
        self._apply_mice()
        self._self_write_until = time.monotonic() + 0.3

    def _schedule_reapply(self, why: str) -> None:
        if "apply" in self._timers:
            return              # one pending apply covers every change in the burst
        now = time.monotonic()
        self._reapply_times = [t for t in self._reapply_times if now - t < 10]
        if len(self._reapply_times) >= MAX_REAPPLY_PER_10S:
            if len(self._reapply_times) == MAX_REAPPLY_PER_10S:
                self.error("başka bir program touchpad ayarlarını sürekli değiştiriyor; "
                           "yeniden uygulama durduruldu")
                self._reapply_times.append(now)
            return
        self._reapply_times.append(now)
        log.debug("yeniden uygula: %s", why)
        self._timer("apply", REAPPLY_DELAY_MS, self.apply_all)

    # -------------------------------------------------------------------- X
    def _select_x(self) -> None:
        masks = [(xi.xinput.AllDevices,
                  xi.xinput.HierarchyChangedMask | xi.xinput.PropertyEventMask
                  | xi.xinput.RawKeyPressMask | xi.xinput.RawButtonPressMask)]
        for pad in self.pads.values():
            if pad.xid is not None:
                masks.append((pad.xid, xi.xinput.RawMotionMask))
        try:
            self.x.select(self.root, masks)
        except Exception as exc:
            self.error(f"XI2 olay seçimi: {exc}")

    def _on_x(self, fd, cond) -> bool:
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            self._x_lost()
        else:
            self._flush_x()
        return self.x_alive

    def _x_event(self, ev) -> None:
        evtype = getattr(ev, "evtype", None)
        if evtype is None:
            return
        X = xi.xinput
        if evtype == X.HierarchyChanged:
            self._timer("rescan", RESCAN_DELAY_MS, self._rescan_and_apply)
        elif evtype == X.PropertyEvent:
            data = ev.data
            if time.monotonic() < self._self_write_until:
                return
            name = self.x.atom_name(data.property)
            if name not in xi.MANAGED and name not in (xi.P_HIRES,):
                return
            ours = {p.xid for p in self.pads.values() if p.xid is not None}
            if data.deviceid in ours or data.deviceid == self.vp_xid:
                self._schedule_reapply(f"{name} değişti (aygıt {data.deviceid})")
        elif evtype in (X.RawKeyPress, X.RawButtonPress, X.RawMotion):
            raw = xi.parse_raw_event(evtype, ev.data)
            if raw.sourceid in self.xtest_ids or raw.deviceid in self.xtest_ids:
                return
            if self.vp_xid is not None and raw.deviceid == self.vp_xid:
                return
            now = time.monotonic()
            if evtype == X.RawKeyPress:
                self.last_key_time = now          # only the time; key codes are never read
                self._stop_coast()
            elif evtype == X.RawButtonPress:
                if raw.detail in (1, 2, 3):
                    self._stop_coast()
                m = self.mice.get(raw.deviceid)
                if m is not None:
                    self._mouse_button(m, raw.detail)
            else:
                for pad in self.pads.values():
                    if pad.xid == raw.deviceid and pad.ratio is not None:
                        dx, dy = raw.values.get(2, 0.0), raw.values.get(3, 0.0)
                        if dx or dy:
                            pad.ratio.add_units(now, dx, dy)

    def _rescan_and_apply(self) -> None:
        self.rescan()
        self._ensure_vpointer()
        self.apply_all()

    # ---------------------------------------------------------------- touches
    def _on_pad(self, fd, cond, pad: Pad) -> bool:
        if cond & (GLib.IO_ERR | GLib.IO_HUP):
            self._drop_pad(pad)
            return False
        read_time = time.monotonic()
        try:
            for frame in pad.reader.frames():
                self._frame(pad, frame, read_time)
        except OSError as exc:
            self.error(f"{pad.info.name}: okuma hatası {exc}")
            self._drop_pad(pad)
            return False
        except Exception:
            log.exception("dokunma işleme")
        self._schedule_tap_timer()
        self._flush_x()
        return True

    def _frame(self, pad: Pad, f, read_time: float) -> None:
        pad.stats.feed(f, read_time)
        was_coasting = self.coaster.active
        if was_coasting and (f.began or f.button):
            self._stop_coast()
        if f.count >= 3 and f.began:
            self.boost.hold()          # ramp CPU/GPU up before the animation starts
        wev = self.wmdrag.feed(f, self._super_held)
        for e in wev:
            self._wm_event(e)
        if self.wmdrag.active or wev:
            # Super + touch moves/resizes windows: no taps, gestures or coasting meanwhile.
            pad.last_count, pad.last_time = f.count, f.time
            return
        gest = pad.recognizer.feed(f) if pad.recognizer else []
        taps = pad.tap.feed(f, self.last_key_time, pad.recognizer.active,
                            coasting=was_coasting) if pad.tap else []
        k = self.eff["scroll"]["kinetic"]
        fling = None
        if k["enabled"] and self.eff["scroll"]["method"] == "twofinger":
            pad.ratio.add_frame(f)
            fling = pad.tracker.feed(f)
        for g in gest:
            self._gesture(g)
        self._tap_events(pad, taps)
        if fling is not None and not pad.recognizer.active:
            self._start_coast(pad, fling)
        pad.last_count, pad.last_time = f.count, f.time
        if f.count == 0 and all(p.last_count == 0 for p in self.pads.values()):
            self._release_held("parmaklar kalktı")

    def _super_held(self) -> bool:
        """Super down on the keyboard (not our own window-menu Super press)."""
        if self.executor._cont is not None:
            return False
        try:
            return wm.super_held(self.x.d)
        except Exception:
            return False

    def _title_probe(self) -> bool:
        return wm.pointer_on_title_bar(self.x.d)

    def _wm_event(self, e) -> None:
        if e.kind == "press":
            self.event("Super ile pencere " + ("taşıma" if e.button == 1 else "boyutlandırma"))
            self._pointer_button(e.button, True)
        elif e.kind == "release":
            self._pointer_button(e.button, False)
        elif e.kind == "move":
            dist = math.hypot(e.dx, e.dy)
            gain = self.gain(dist / e.dt if e.dt > 1e-4 else 0.0)
            self._pointer_move(e.dx * gain, e.dy * gain)

    def _release_held(self, why: str) -> None:
        """Let go of anything a gesture holds: the 3-finger drag button, the app switcher."""
        for e in self.wmdrag.cancel():
            log.info("Super sürüklemesi bırakıldı (%s)", why)
            self._wm_event(e)
        if self._drag_pressed:
            log.info("sürükleme düğmesi bırakıldı (%s)", why)
            self._pointer_button(1, False)
            self._drag_pressed = False
        if self.executor._cont is not None:
            log.info("sürekli eylem bitirildi (%s)", why)
            self.executor.end()

    def _watchdog(self) -> bool:
        """Every second: nothing may stay held while no finger is on any pad."""
        self.boost.tick()
        if self._drag_pressed or self.executor._cont is not None or self.wmdrag.pressed:
            now = time.monotonic()
            idle = all(p.last_count == 0 or now - p.last_time > 2.0 for p in self.pads.values())
            if idle:
                self._release_held("bekçi")
                self._flush_x()
        return True

    def _schedule_tap_timer(self) -> None:
        deadlines = [p.tap.deadline() for p in self.pads.values() if p.tap]
        deadlines = [d for d in deadlines if d is not None]
        if not deadlines:
            return
        delay = max(0.0, min(deadlines) - time.monotonic())
        self._timer("tap", int(math.ceil(delay * 1000)) + 1, self._tap_tick)

    def _tap_tick(self) -> None:
        now = time.monotonic()
        for pad in self.pads.values():
            if pad.tap:
                self._tap_events(pad, pad.tap.tick(now))
        self._schedule_tap_timer()

    def _tap_events(self, pad: Pad, events: list[TapEvent]) -> None:
        for t in events:
            if t.kind == "click":
                self.executor.click(t.button)
                self.event(f"dokunma: {t.fingers} parmak → düğme {t.button}")
            elif t.kind == "press":
                self.executor.button(t.button, True)
            elif t.kind == "release":
                self.executor.button(t.button, False)
            elif t.kind == "action":
                b = self.cfg["gestures"]["bindings"]["tap4"]
                self.event(f"dokunma: 4 parmak → {b['action']}")
                if not self.pause_clients:
                    self.executor.run(b["action"], b.get("arg"))

    # --------------------------------------------------------------- gestures
    @staticmethod
    def _inc_for(direction: str, action: str = "") -> int:
        """+1 = next/more. Up means more, except for switchers where down is next."""
        if action in VERTICAL_DOWN_IS_NEXT and direction in ("up", "down"):
            return 1 if direction == "down" else -1
        return 1 if direction in ("up", "right", "out") else -1

    def _gesture(self, g: GestureEvent) -> None:
        bindings = self.cfg["gestures"]["bindings"]
        if g.kind in ("swipe", "pinch", "begin", "step"):
            self.boost.hold()
        if g.kind in ("swipe", "pinch"):
            b = bindings.get(g.key, {"action": "none"})
            self.event(f"hareket: {g.key} → {b['action']}")
            if not self.pause_clients:
                self.executor.run(b["action"], b.get("arg"))
        elif g.kind == "begin":
            b = bindings.get(g.key, {"action": "none"})
            self.event(f"hareket: {g.key} → {b['action']} (sürekli)")
            self._cont_key = g.key
            if not self.pause_clients:
                self._cont_action = b["action"]
                self.executor.begin(b["action"], b.get("arg"),
                                    self._inc_for(g.direction, b["action"]))
        elif g.kind == "step":
            vertical = g.key.endswith(("_up", "_down"))
            down_is_next = getattr(self, "_cont_action", "") in VERTICAL_DOWN_IS_NEXT
            inc = -g.step if (vertical and not down_is_next) else g.step
            if not self.pause_clients:
                self.executor.step(inc)
        elif g.kind == "end":
            self.executor.end()
        elif g.kind == "zoom":
            if not self.pause_clients:
                self.executor.run("zoom_in" if g.step > 0 else "zoom_out")
        elif g.kind == "drag_begin":
            self.event("3 parmakla sürükleme")
            self._drag_pressed = True
            self._pointer_button(1, True)
        elif g.kind == "drag_move":
            dist = math.hypot(g.dx, g.dy)
            v = dist / g.dt if g.dt > 1e-4 else 0.0
            gain = self.gain(v)
            self._pointer_move(g.dx * gain, g.dy * gain)
        elif g.kind == "drag_end":
            if self._drag_pressed:
                self._pointer_button(1, False)
                self._drag_pressed = False

    def _pointer_button(self, button: int, down: bool) -> None:
        if self.vp is not None:
            self.vp.button(button, down)
        else:
            self.executor.button(button, down)

    def _pointer_move(self, dx: float, dy: float) -> None:
        if self.vp is not None:
            self.vp.move(dx, dy)
            return
        # XTest fallback: relative motion, integer pixels.
        acc = getattr(self, "_xt_rem", [0.0, 0.0])
        acc[0] += dx
        acc[1] += dy
        ix, iy = int(acc[0]), int(acc[1])
        acc[0] -= ix
        acc[1] -= iy
        self._xt_rem = acc
        if ix or iy:
            from Xlib import X
            from Xlib.ext import xtest
            xtest.fake_input(self.x.d, X.MotionNotify, detail=True, x=ix, y=iy)
            self.x.d.flush()

    # ---------------------------------------------------------------- kinetic
    def _start_coast(self, pad: Pad, fling: kinetic.Fling) -> None:
        k = self.cfg["scroll"]["kinetic"]
        rx, ry = pad.ratio.ratio()
        vx = fling.vx * rx * k["gain"] if self.cfg["scroll"]["horizontal"] else 0.0
        vy = fling.vy * ry * k["gain"]
        # libinput locks the scroll to one axis; follow the dominant one.
        if abs(fling.vx) < 0.5 * abs(fling.vy):
            vx = 0.0
        elif abs(fling.vy) < 0.5 * abs(fling.vx):
            vy = 0.0
        self.coaster.start(vx, vy, time.monotonic())
        if self.coaster.active:
            self._coast_pad = pad
            self.event(f"kinetik kaydırma: {math.hypot(vx, vy) / 120:.0f} tık/sn")
            self._timer("coast", COAST_TICK_MS, self._coast_tick)

    def _coast_tick(self) -> None:
        if not self.coaster.active:
            return
        ix, iy = self.coaster.step(time.monotonic())
        if ix or iy:
            self._emit_scroll(ix, iy)
        if self.coaster.active:
            self._timer("coast", COAST_TICK_MS, self._coast_tick)

    def _emit_scroll(self, ux: int, uy: int) -> None:
        if self.dry_run:
            return
        if self.vp is not None:
            self.vp.scroll_units(ux, uy)
            return
        from Xlib import X
        from Xlib.ext import xtest
        self._xtest_wheel[0] += ux
        self._xtest_wheel[1] += uy
        for axis, (neg, pos) in ((0, (6, 7)), (1, (4, 5))):
            while abs(self._xtest_wheel[axis]) >= 120:
                b = pos if self._xtest_wheel[axis] > 0 else neg
                xtest.fake_input(self.x.d, X.ButtonPress, b)
                xtest.fake_input(self.x.d, X.ButtonRelease, b)
                self._xtest_wheel[axis] -= 120 if self._xtest_wheel[axis] > 0 else -120
        self.x.d.flush()

    def _stop_coast(self) -> None:
        if self.coaster.active:
            self.coaster.stop()
        self._xtest_wheel = [0.0, 0.0]

    # ------------------------------------------------------------------ socket
    def _start_server(self) -> None:
        path = config.socket_path()
        try:
            if path.exists():
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.connect(str(path))
                    probe.close()
                    raise SystemExit("PadForge daemon zaten çalışıyor")
                except ConnectionRefusedError:
                    path.unlink()
                finally:
                    probe.close()
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK)
            srv.bind(str(path))
            os.chmod(path, 0o600)
            srv.listen(4)
            self._server = srv
            GLib.io_add_watch(srv.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._accept)
        except OSError as exc:
            self.error(f"soket açılamadı: {exc}")

    def _accept(self, fd, cond) -> bool:
        try:
            conn, _ = self._server.accept()
        except OSError:
            return True
        conn.setblocking(False)
        wid = GLib.io_add_watch(conn.fileno(), GLib.PRIORITY_DEFAULT,
                                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._client_io)
        self._clients[conn.fileno()] = (conn, wid, bytearray())
        return True

    def _client_io(self, fd, cond) -> bool:
        conn, wid, buf = self._clients.get(fd, (None, None, None))
        if conn is None:
            return False
        try:
            data = conn.recv(65536) if cond & GLib.IO_IN else b""
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            data = b""
        if not data:
            self._close_client(fd)
            return False
        buf.extend(data)
        while b"\n" in buf:
            line, _, rest = bytes(buf).partition(b"\n")
            buf[:] = rest
            try:
                reply = self.handle_command(json.loads(line), fd)
            except Exception as exc:
                reply = {"ok": False, "error": str(exc)}
            try:
                conn.sendall((json.dumps(reply, ensure_ascii=False) + "\n").encode())
            except OSError:
                self._close_client(fd)
                return False
        self._flush_x()
        return True

    def _close_client(self, fd: int) -> None:
        conn, wid, _ = self._clients.pop(fd, (None, None, None))
        self.pause_clients.discard(fd)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def handle_command(self, msg: dict, client: int | None = None) -> dict:
        cmd = msg.get("cmd")
        if cmd == "status":
            return {"ok": True, "status": self.status()}
        if cmd == "reload":
            self.reload()
            return {"ok": True}
        if cmd == "pause":
            if msg.get("value", True):
                self.pause_clients.add(client if client is not None else -1)
            else:
                self.pause_clients.discard(client if client is not None else -1)
            return {"ok": True, "paused": bool(self.pause_clients)}
        if cmd == "run":
            self.boost.hold()
            self.executor.run(str(msg.get("action", "none")), msg.get("arg"))
            return {"ok": True}
        if cmd == "reset_stats":
            for pad in self.pads.values():
                pad.stats = TouchStats()
            return {"ok": True}
        return {"ok": False, "error": f"bilinmeyen komut: {cmd}"}

    def status(self) -> dict:
        pads = []
        for pad in self.pads.values():
            s = pad.stats.summary(pad.reader.parser.dropped_count if pad.reader else 0)
            pads.append({
                "name": pad.info.name, "node": pad.info.node, "xid": pad.xid,
                "readable": pad.reader is not None, "res": pad.res, "error": pad.error,
                "width_mm": pad.reader.axis_x.size_mm if pad.reader else pad.info.width_mm,
                "height_mm": pad.reader.axis_y.size_mm if pad.reader else pad.info.height_mm,
                "stats": s.as_dict(),
            })
        return {
            "version": __version__, "pid": os.getpid(),
            "uptime": time.monotonic() - self.started,
            "config": str(self.config_file), "pads": pads,
            "uinput": self.vp is not None, "gnome": self.gnome.available,
            "paused": bool(self.pause_clients), "dry_run": self.dry_run,
            "touchegg": self.touchegg, "boost": self.boost.active,
            "mice": [{"name": m.info.name, "xid": m.xid, "buttons": len(m.base),
                      "declared": m.info.declared_buttons,
                      "seen": self._mouse_cache(m.info.name).get("seen", []),
                      "hidden": [i + 1 for i, v in enumerate(m.applied) if v == 0]}
                     for m in self.mice.values()],
            "errors": self.errors[-10:],
            "events": [{"t": t, "msg": m} for t, m in self.events[-15:]],
        }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="padforge daemon")
    ap.add_argument("--dry-run", action="store_true", help="eylemleri çalıştırma, yalnızca yaz")
    ap.add_argument("--config", type=Path, help="ayar dosyası")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("DISPLAY"):
        log.error("PadForge şimdilik yalnızca X11 oturumlarında çalışır.")
        return 2
    try:
        d = Daemon(dry_run=args.dry_run, config_file=args.config)
    except Exception as exc:
        log.error("başlatılamadı: %s", exc)
        return 1
    d.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
