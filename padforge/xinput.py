"""X11 / xf86-input-libinput backend.

Talks XInput2 through python-xlib: finds touchpads, reads and writes the
``libinput ...`` device properties, and parses the XI2 events python-xlib
leaves undecoded (raw events with valuators).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Iterable

from Xlib import X
from Xlib import display as xdisplay
from Xlib.ext import xinput

from . import accel


def _lenient_button_state(self, data, display, length, fmt):
    """python-xlib asserts that bit 0 ("button 0") of a button state is clear.

    Xorg sets it when a button mapped to 0 (hidden) is down and its mapping changes
    before the release; after that every device query raised AssertionError, which
    stopped the daemon's rescans (seen 2026-10-19). Bit 0 means nothing: drop it.
    """
    mask_len = 4 * ((((length + 7) >> 3) + 3) >> 2)
    mask_value = int.from_bytes(bytes(data[:mask_len]), "little")
    return xinput.ButtonMask(mask_value >> 1, length), data[mask_len:]


xinput.ButtonState.parse_binary_value = _lenient_button_state

P_TAP = "libinput Tapping Enabled"
P_TAP_DRAG = "libinput Tapping Drag Enabled"
P_TAP_DRAG_LOCK = "libinput Tapping Drag Lock Enabled"
P_TAP_MAP = "libinput Tapping Button Mapping Enabled"
P_NATURAL = "libinput Natural Scrolling Enabled"
P_DWT = "libinput Disable While Typing Enabled"
P_SCROLL_METHODS = "libinput Scroll Methods Available"
P_SCROLL_METHOD = "libinput Scroll Method Enabled"
P_CLICK_METHODS = "libinput Click Methods Available"
P_CLICK_METHOD = "libinput Click Method Enabled"
P_MIDDLE = "libinput Middle Emulation Enabled"
P_SPEED = "libinput Accel Speed"
P_PROFILES = "libinput Accel Profiles Available"
P_PROFILE = "libinput Accel Profile Enabled"
P_MOTION_POINTS = "libinput Accel Custom Motion Points"
P_MOTION_STEP = "libinput Accel Custom Motion Step"
P_SCROLL_POINTS = "libinput Accel Custom Scroll Points"
P_SCROLL_STEP = "libinput Accel Custom Scroll Step"
P_LEFT_HANDED = "libinput Left Handed Enabled"
P_HSCROLL = "libinput Horizontal Scroll Enabled"
P_SCROLL_DIST = "libinput Scrolling Pixel Distance"
P_HIRES = "libinput High Resolution Wheel Scroll Enabled"
P_NODE = "Device Node"
P_ENABLED = "Device Enabled"

# Properties PadForge owns; a change by someone else triggers a re-apply.
MANAGED = (P_TAP, P_TAP_DRAG, P_TAP_DRAG_LOCK, P_TAP_MAP, P_NATURAL, P_DWT, P_SCROLL_METHOD,
           P_CLICK_METHOD, P_MIDDLE, P_SPEED, P_PROFILE, P_MOTION_POINTS, P_MOTION_STEP,
           P_SCROLL_POINTS, P_SCROLL_STEP, P_LEFT_HANDED, P_HSCROLL, P_SCROLL_DIST)


@dataclass
class XDevice:
    id: int
    name: str
    node: str | None
    enabled: bool
    attachment: int
    use: int
    props: set[str] = field(default_factory=set)

    @property
    def is_touchpad(self) -> bool:
        return P_TAP in self.props

    @property
    def floating(self) -> bool:
        return self.use == xinput.FloatingSlave


@dataclass
class DeviceCaps:
    """What a given touchpad supports, from its "... Available" properties."""
    res: float = 0.0                          # units/mm, from evdev (0 = unknown)
    scroll_methods: tuple[int, int, int] = (1, 1, 0)
    click_methods: tuple[int, int] = (1, 1)
    profiles: tuple[int, ...] = (1, 1, 0)

    @property
    def has_custom(self) -> bool:
        return len(self.profiles) >= 3 and bool(self.profiles[2])


def float_to_card32(value: float) -> int:
    return struct.unpack("=I", struct.pack("=f", float(value)))[0]


def card32_to_float(value: int) -> float:
    return struct.unpack("=f", struct.pack("=I", value & 0xFFFFFFFF))[0]


def plan_properties(cfg: dict, caps: DeviceCaps) -> list[tuple[str, list[Any]]]:
    """Translate a validated config into an ordered list of (property, values).

    Pure function: no X connection needed, so it is unit-tested directly.
    """
    p: list[tuple[str, list[Any]]] = []
    pointer, tap, click, scroll = cfg["pointer"], cfg["tap"], cfg["click"], cfg["scroll"]

    profile = pointer["profile"]
    if profile == "custom" and not (caps.has_custom and caps.res > 0):
        profile = "adaptive"     # cannot encode without custom support / resolution
    vec = {"adaptive": [1, 0, 0], "flat": [0, 1, 0], "custom": [0, 0, 1]}[profile]
    p.append((P_PROFILE, vec[:len(caps.profiles)] if len(caps.profiles) < 3 else vec))
    if profile == "custom":
        step, pts = accel.libinput_motion_points(pointer["curve"], caps.res)
        p.append((P_MOTION_STEP, [step]))
        p.append((P_MOTION_POINTS, pts))
        sstep, spts = accel.libinput_scroll_points(scroll["speed"], scroll["acceleration"], caps.res)
        p.append((P_SCROLL_STEP, [sstep]))
        p.append((P_SCROLL_POINTS, spts))
        p.append((P_SCROLL_DIST, [15]))
    else:
        p.append((P_SPEED, [float(pointer["speed"])]))
        p.append((P_SCROLL_DIST, [accel.scroll_pixel_distance(scroll["speed"])]))

    libinput_tap = tap["enabled"] and tap["engine"] == "libinput"
    p.append((P_TAP, [int(libinput_tap)]))
    p.append((P_TAP_DRAG, [int(bool(tap["drag"]))]))
    p.append((P_TAP_DRAG_LOCK, [int(bool(tap["drag_lock"]))]))
    p.append((P_TAP_MAP, [1, 0] if tap["button_map"] == "lrm" else [0, 1]))

    avail = caps.scroll_methods
    method = scroll["method"]
    if method == "twofinger" and avail[0]:
        vec = [1, 0, 0]
    elif method == "edge" and avail[1]:
        vec = [0, 1, 0]
    else:
        vec = [0, 0, 0]
    p.append((P_SCROLL_METHOD, vec))
    p.append((P_NATURAL, [int(bool(scroll["natural"]))]))
    p.append((P_HSCROLL, [int(bool(scroll["horizontal"]))]))

    cm = caps.click_methods
    if click["method"] == "clickfinger" and cm[1]:
        p.append((P_CLICK_METHOD, [0, 1]))
    elif cm[0]:
        p.append((P_CLICK_METHOD, [1, 0]))
    p.append((P_MIDDLE, [int(bool(click["middle_emulation"]))]))
    p.append((P_LEFT_HANDED, [int(bool(click["left_handed"]))]))
    p.append((P_DWT, [int(bool(cfg["typing"]["disable_while_typing"]))]))
    return p


class XInput:
    """Thin wrapper around one python-xlib Display with XI 2.2 announced."""

    def __init__(self, display_name: str | None = None, dpy: xdisplay.Display | None = None):
        self.d = dpy or xdisplay.Display(display_name)
        if not self.d.has_extension(xinput.extname):
            raise RuntimeError("XInput eklentisi yok")
        self.opcode = self.d.display.get_extension_major(xinput.extname)
        # Announce XI 2.2 so raw events reach root regardless of grabs.
        xinput.XIQueryVersion(display=self.d.display, opcode=self.opcode,
                              major_version=2, minor_version=2)
        self._atoms: dict[str, int] = {}
        self._names: dict[int, str] = {}

    # -- atoms -----------------------------------------------------------------
    def atom(self, name: str, create: bool = False) -> int:
        if name not in self._atoms:
            a = self.d.intern_atom(name, only_if_exists=not create)
            self._atoms[name] = a
            if a:
                self._names[a] = name
        return self._atoms[name]

    def atom_name(self, atom: int) -> str:
        if atom not in self._names:
            try:
                self._names[atom] = self.d.get_atom_name(atom)
            except Exception:
                return ""
        return self._names[atom]

    # -- devices ---------------------------------------------------------------
    def devices(self) -> list[XDevice]:
        out = []
        for info in self.d.xinput_query_device(xinput.AllDevices).devices:
            if info.use not in (xinput.SlavePointer, xinput.FloatingSlave):
                continue
            props = {self.atom_name(a) for a in self.list_props(info.deviceid)}
            node = None
            if P_NODE in props:
                val = self.get_prop(info.deviceid, P_NODE)
                node = val if isinstance(val, str) else None
            name = info.name.decode() if isinstance(info.name, bytes) else info.name
            out.append(XDevice(info.deviceid, name, node, bool(info.enabled),
                               info.attachment, info.use, props))
        return out

    def touchpads(self) -> list[XDevice]:
        return [d for d in self.devices() if d.is_touchpad]

    def find(self, name: str) -> XDevice | None:
        for dev in self.devices():
            if dev.name == name:
                return dev
        return None

    def list_props(self, devid: int) -> list[int]:
        try:
            return list(self.d.xinput_list_device_properties(devid).atoms)
        except Exception:
            return []

    # -- properties --------------------------------------------------------------
    def _raw_prop(self, devid: int, name: str):
        atom = self.atom(name)
        if not atom:
            return None
        r = self.d.xinput_get_device_property(devid, atom, X.AnyPropertyType, 0, 4096)
        if not r.type:
            return None
        return r

    def get_prop(self, devid: int, name: str) -> Any:
        """Return list of ints/floats, or str for string properties, or None."""
        r = self._raw_prop(devid, name)
        if r is None or r.value is None:
            return None
        fmt, data = r.value
        tname = self.atom_name(r.type)
        if tname == "FLOAT":
            return [card32_to_float(v) for v in data]
        if fmt == 8:
            if tname == "STRING":
                return bytes(data).rstrip(b"\0").decode(errors="replace")
            return list(bytes(data))
        return list(data)

    def set_prop(self, devid: int, name: str, values: Iterable[Any]) -> None:
        r = self._raw_prop(devid, name)
        if r is None:
            raise KeyError(f"{name}: özellik yok")
        values = list(values)
        fmt = r.value[0] if r.value else 32
        tname = self.atom_name(r.type)
        if tname == "FLOAT":
            data = [float_to_card32(v) for v in values]
        else:
            mask = (1 << fmt) - 1
            data = [int(v) & mask for v in values]
        self.d.xinput_change_device_property(devid, self.atom(name), r.type, X.PropModeReplace,
                                             (fmt, data))

    def caps(self, devid: int, res: float) -> DeviceCaps:
        def tup(name, default):
            v = self.get_prop(devid, name)
            return tuple(int(x) for x in v) if isinstance(v, list) and v else default
        return DeviceCaps(res=res,
                          scroll_methods=tup(P_SCROLL_METHODS, (1, 1, 0)),
                          click_methods=tup(P_CLICK_METHODS, (1, 1)),
                          profiles=tup(P_PROFILES, (1, 1, 0)))

    def apply(self, devid: int, plan: list[tuple[str, list[Any]]]) -> list[str]:
        """Write a property plan; returns human-readable errors (empty = OK)."""
        errors = []
        for name, values in plan:
            try:
                current = self.get_prop(devid, name)
                if current is None:
                    continue           # not supported by this driver version
                if _same(current, values):
                    continue
                self.set_prop(devid, name, values)
                self.d.sync()
            except Exception as exc:   # BadValue etc. must not stop the rest
                errors.append(f"{name}: {exc.__class__.__name__}")
        return errors

    def snapshot(self, devid: int) -> dict[str, Any]:
        return {name: self.get_prop(devid, name) for name in MANAGED}

    def select(self, window, masks: list[tuple[int, int]]) -> None:
        window.xinput_select_events(masks)
        self.d.flush()

    def float_device(self, devid: int) -> None:
        """Detach a slave so its events never reach the core pointer (tests)."""
        XIChangeHierarchyDetach(display=self.d.display, opcode=self.opcode, deviceid=devid)
        self.d.sync()

    # -- per-device button mapping (XI 1) ----------------------------------------------
    def button_map(self, devid: int) -> list[int]:
        """The device's button map: entry i is what physical button i+1 sends (0 = nothing)."""
        r = GetDeviceButtonMapping(display=self.d.display, opcode=self.opcode, deviceid=devid)
        return list(r.map)

    def set_button_map(self, devid: int, mapping: list[int]) -> bool:
        """Set it; False while a button that changes is held down (X answers MappingBusy).

        A button mapped to 0 never reaches applications, but XI2 raw events still
        report it with its physical number (measured on this Xorg, 2026-10-19).
        """
        r = SetDeviceButtonMapping(display=self.d.display, opcode=self.opcode, deviceid=devid,
                                   map=list(mapping))
        return r.status == 0

    # -- gesture buttons -------------------------------------------------------------------
    def grab_button(self, devid: int, button: int) -> bool:
        """XI2 passive grab of ``button`` on that one device.

        Pressing it hands the whole device to us (motion, wheel, other buttons) and the
        X server gives it back by itself on the release, so nothing can stay stuck
        even if the daemon hangs. Meanwhile the device is detached: the cursor stands
        still, and on the release it jumps by the movement made (measured 2026-10-19).
        """
        r = xinput.passive_grab_device(
            self.d.screen().root, devid, X.CurrentTime, button, xinput.GrabtypeButton,
            xinput.GrabModeAsync, xinput.GrabModeAsync, False,
            xinput.ButtonPressMask | xinput.ButtonReleaseMask | xinput.MotionMask,
            [xinput.AnyModifier])
        return not list(r.modifiers)

    def ungrab_button(self, devid: int, button: int) -> None:
        xinput.passive_ungrab_device(self.d.screen().root, devid, button,
                                     xinput.GrabtypeButton, [xinput.AnyModifier])


def _same(current: Any, wanted: list[Any]) -> bool:
    if not isinstance(current, list) or len(current) != len(wanted):
        return False
    for a, b in zip(current, wanted):
        if isinstance(b, float) or isinstance(a, float):
            if abs(float(a) - float(b)) > max(1e-4, abs(float(b)) * 1e-5):
                return False
        elif int(a) != int(b):
            return False
    return True


# -- XI2 requests/events python-xlib does not implement --------------------------

from Xlib.protocol import rq  # noqa: E402


class XIChangeHierarchyDetach(rq.Request):
    """XIChangeHierarchy with a single XIDetachSlave (float) change."""
    _request = rq.Struct(
        rq.Card8("opcode"),
        rq.Opcode(43),
        rq.RequestLength(),
        rq.Card8("num_changes"),
        rq.Pad(3),
        # xXIDetachSlaveInfo: type(2)=4, length(2)=2, deviceid(2), pad(2)
        rq.Card16("type"),
        rq.Card16("length"),
        rq.Card16("deviceid"),
        rq.Pad(2),
    )

    def __init__(self, display, opcode, deviceid):
        super().__init__(display=display, opcode=opcode, num_changes=1, type=4, length=2,
                         deviceid=deviceid)


class GetDeviceButtonMapping(rq.ReplyRequest):
    _request = rq.Struct(
        rq.Card8("opcode"),
        rq.Opcode(28),
        rq.RequestLength(),
        rq.Card8("deviceid"),
        rq.Pad(3),
    )
    _reply = rq.Struct(
        rq.ReplyCode(),
        rq.Card8("xi_reply_type"),
        rq.Card16("sequence_number"),
        rq.ReplyLength(),
        rq.LengthOf("map", 1),
        rq.Pad(23),
        rq.List("map", rq.Card8Obj),
    )


class SetDeviceButtonMapping(rq.ReplyRequest):
    _request = rq.Struct(
        rq.Card8("opcode"),
        rq.Opcode(29),
        rq.RequestLength(),
        rq.Card8("deviceid"),
        rq.LengthOf("map", 1),
        rq.Pad(2),
        rq.List("map", rq.Card8Obj),
    )
    _reply = rq.Struct(
        rq.ReplyCode(),
        rq.Card8("xi_reply_type"),
        rq.Card16("sequence_number"),
        rq.ReplyLength(),
        rq.Card8("status"),          # 0 Success, 1 MappingBusy, 2 Failed
        rq.Pad(23),
    )


def _fp3232(buf: bytes, off: int) -> float:
    integral, frac = struct.unpack_from("=iI", buf, off)
    return integral + frac / 4294967296.0


@dataclass
class RawEvent:
    evtype: int
    deviceid: int
    time: int
    detail: int
    sourceid: int
    values: dict[int, float]
    raw: dict[int, float]


def parse_raw_event(evtype: int, data: bytes) -> RawEvent:
    """Decode an XI2 raw event body (``GenericEvent.data``: bytes after evtype)."""
    deviceid, time, detail, sourceid, nmask, _flags = struct.unpack_from("=HIIHHI", data, 0)
    mask = int.from_bytes(data[22:22 + 4 * nmask], "little")
    axes = [i for i in range(32 * nmask) if mask >> i & 1]
    off = 22 + 4 * nmask
    values = {a: _fp3232(data, off + 8 * k) for k, a in enumerate(axes)}
    off += 8 * len(axes)
    raw = {a: _fp3232(data, off + 8 * k) for k, a in enumerate(axes)}
    return RawEvent(evtype, deviceid, time, detail, sourceid, values, raw)
