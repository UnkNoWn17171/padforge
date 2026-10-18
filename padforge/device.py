"""Touchpad discovery (sysfs + udev database) and the evdev reader.

Discovery works without any permission; reading touches needs read access to
``/dev/input/eventN`` (see packaging/install-udev.sh).
"""
from __future__ import annotations

import array
import fcntl
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from . import mt

SYS_INPUT = Path("/sys/class/input")
UDEV_DATA = Path("/run/udev/data")

EVIOCSCLOCKID = 0x400445A0          # _IOW('E', 0xa0, int)
CLOCK_MONOTONIC = time.CLOCK_MONOTONIC


def _ioc_read(nr: int, size: int) -> int:
    # _IOC(_IOC_READ, 'E', nr, size)
    return (2 << 30) | (size << 16) | (ord("E") << 8) | nr


def _bits(hexwords: str) -> set[int]:
    """Decode a sysfs capability bitmap ("e520 10000 0 0") into bit numbers."""
    out: set[int] = set()
    words = hexwords.split()
    for idx, word in enumerate(reversed(words)):
        val = int(word, 16)
        bit = 0
        while val:
            if val & 1:
                out.add(idx * 64 + bit)
            val >>= 1
            bit += 1
    return out


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return default


def udev_properties(major: int, minor: int, root: Path = UDEV_DATA) -> dict[str, str]:
    props = {}
    try:
        for line in (root / f"c{major}:{minor}").read_text().splitlines():
            if line.startswith("E:") and "=" in line:
                key, _, val = line[2:].partition("=")
                props[key] = val
    except OSError:
        pass
    return props


@dataclass
class TouchpadInfo:
    node: str                      # /dev/input/eventN
    name: str
    sysfs: str                     # resolved device path in /sys/devices
    vendor: int = 0
    product: int = 0
    bustype: int = 0
    phys: str = ""
    width_mm: float = 0.0
    height_mm: float = 0.0
    abs_codes: set[int] = field(default_factory=set)
    key_codes: set[int] = field(default_factory=set)
    msc_codes: set[int] = field(default_factory=set)
    props_bits: set[int] = field(default_factory=set)
    udev: dict[str, str] = field(default_factory=dict)

    @property
    def bus_name(self) -> str:
        return {0x03: "USB", 0x05: "Bluetooth", 0x11: "PS/2", 0x18: "I2C", 0x1C: "Sanal"}.get(
            self.bustype, f"0x{self.bustype:02x}")

    @property
    def max_fingers(self) -> int:
        n = 1
        for code, count in mt.TOOL_COUNTS.items():
            if code in self.key_codes:
                n = max(n, count)
        return n

    @property
    def has_pressure(self) -> bool:
        return mt.ABS_MT_PRESSURE in self.abs_codes

    @property
    def has_touch_size(self) -> bool:
        return mt.ABS_MT_TOUCH_MAJOR in self.abs_codes

    @property
    def has_hw_timestamp(self) -> bool:
        return mt.MSC_TIMESTAMP in self.msc_codes

    @property
    def has_palm_tool(self) -> bool:
        return mt.ABS_MT_TOOL_TYPE in self.abs_codes

    @property
    def is_clickpad(self) -> bool:
        return 2 in self.props_bits          # INPUT_PROP_BUTTONPAD

    @property
    def is_internal(self) -> bool:
        return self.bustype in (0x11, 0x18)  # PS/2 or I2C

    @property
    def readable(self) -> bool:
        return os.access(self.node, os.R_OK)

    def driver_chain(self) -> list[str]:
        """Kernel drivers from the input node up to the bus (e.g. hid-multitouch, i2c_hid_acpi)."""
        chain = []
        p = Path(self.sysfs)
        for _ in range(8):
            drv = p / "driver"
            if drv.exists():
                name = os.path.basename(os.path.realpath(drv))
                if name not in chain:
                    chain.append(name)
            if p.parent == p:
                break
            p = p.parent
        return chain

    def power_parents(self) -> list[tuple[str, str, str]]:
        """(path, runtime_status, control) for ancestors with runtime PM (I2C controller etc.)."""
        out = []
        p = Path(self.sysfs)
        while str(p).startswith("/sys/devices/") and p != Path("/sys/devices"):
            st, ctl = p / "power" / "runtime_status", p / "power" / "control"
            if st.exists() and ctl.exists():
                status = _read(st)
                if status != "unsupported":
                    out.append((str(p), status, _read(ctl)))
            p = p.parent
        return out


def list_touchpads(sys_input: Path = SYS_INPUT, udev_root: Path = UDEV_DATA) -> list[TouchpadInfo]:
    pads = []
    try:
        entries = sorted(sys_input.glob("event*"), key=lambda p: int(p.name[5:] or 0))
    except OSError:
        return pads
    for ev in entries:
        dev = ev / "device"
        try:
            major, minor = (int(x) for x in _read(ev / "dev", "0:0").split(":"))
        except ValueError:
            continue
        udev = udev_properties(major, minor, udev_root)
        abs_codes = _bits(_read(dev / "capabilities" / "abs", "0"))
        key_codes = _bits(_read(dev / "capabilities" / "key", "0"))
        is_pad = udev.get("ID_INPUT_TOUCHPAD") == "1"
        if not udev and mt.ABS_MT_POSITION_X in abs_codes and mt.BTN_TOOL_FINGER in key_codes:
            # No udev database (containers): MT device that is not a touchscreen.
            is_pad = 1 not in _bits(_read(dev / "properties", "0"))   # INPUT_PROP_DIRECT
        if not is_pad:
            continue

        def hexfile(name):
            try:
                return int(_read(dev / "id" / name, "0"), 16)
            except ValueError:
                return 0

        pads.append(TouchpadInfo(
            node=f"/dev/input/{ev.name}",
            name=_read(dev / "name"),
            sysfs=os.path.realpath(dev),
            vendor=hexfile("vendor"), product=hexfile("product"), bustype=hexfile("bustype"),
            phys=_read(dev / "phys"),
            width_mm=float(udev.get("ID_INPUT_WIDTH_MM", 0) or 0),
            height_mm=float(udev.get("ID_INPUT_HEIGHT_MM", 0) or 0),
            abs_codes=abs_codes, key_codes=key_codes,
            msc_codes=_bits(_read(dev / "capabilities" / "msc", "0")),
            props_bits=_bits(_read(dev / "properties", "0")),
            udev=udev,
        ))
    # Internal touchpad first.
    pads.sort(key=lambda p: (not p.is_internal, p.node))
    return pads


def irq_count(name: str) -> tuple[int | None, int]:
    """(irq number, total count) for an /proc/interrupts line containing ``name``."""
    try:
        with open("/proc/interrupts") as fh:
            for line in fh:
                if name in line:
                    parts = line.split()
                    irq = parts[0].rstrip(":")
                    total = sum(int(p) for p in parts[1:] if p.isdigit())
                    return (int(irq) if irq.isdigit() else None), total
    except OSError:
        pass
    return None, 0


def irq_line(name: str) -> str:
    try:
        with open("/proc/interrupts") as fh:
            for line in fh:
                if name in line:
                    return " ".join(p for p in line.split() if not p.isdigit())
    except OSError:
        pass
    return ""


class TouchpadReader:
    """Opens a touchpad event node and yields parsed frames."""

    def __init__(self, info: TouchpadInfo):
        import evdev                      # python3-evdev
        self.info = info
        self.dev = evdev.InputDevice(info.node)
        try:
            fcntl.ioctl(self.dev.fd, EVIOCSCLOCKID, struct.pack("i", CLOCK_MONOTONIC))
            self.clock = "monotonic"
        except OSError:
            self.clock = "realtime"
        caps = dict(self.dev.capabilities(absinfo=True)).get(mt.EV_ABS, [])
        absinfo = {code: ai for code, ai in caps}
        self.absinfo = absinfo
        ax, ay = absinfo[mt.ABS_MT_POSITION_X], absinfo[mt.ABS_MT_POSITION_Y]
        rx = ax.resolution or ((ax.max - ax.min) / info.width_mm if info.width_mm else 1)
        ry = ay.resolution or ((ay.max - ay.min) / info.height_mm if info.height_mm else rx)
        self.axis_x = mt.Axis(ax.min, ax.max, float(rx))
        self.axis_y = mt.Axis(ay.min, ay.max, float(ry))
        slot = absinfo.get(mt.ABS_MT_SLOT)
        self.nslots = (slot.max + 1) if slot else 1
        self.parser = mt.MTParser(self.axis_x, self.axis_y, self.nslots)
        self.read_errors = 0

    @property
    def fd(self) -> int:
        return self.dev.fd

    @property
    def resolution(self) -> float:
        """Units/mm on X — what the libinput custom profile works in."""
        return self.axis_x.res

    def frames(self) -> Iterator[mt.Frame]:
        """Drain *all* pending events; yields frames. Non-blocking.

        python-evdev returns at most 64 events per read(), i.e. only ~6 frames
        of a three-finger touch, so keep reading until the kernel buffer is empty.
        """
        while True:
            try:
                events = list(self.dev.read())
            except BlockingIOError:
                return
            except OSError:
                self.read_errors += 1
                raise
            if not events:
                return
            for ev in events:
                ts = ev.sec + ev.usec / 1e6
                frame = self.parser.feed(ev.type, ev.code, ev.value, ts)
                if frame is not None:
                    if frame.resynced and self.parser.dropped_count:
                        frame = self._resync(ts)
                    yield frame

    def _resync(self, ts: float) -> mt.Frame:
        codes = (mt.ABS_MT_TRACKING_ID, mt.ABS_MT_POSITION_X, mt.ABS_MT_POSITION_Y,
                 mt.ABS_MT_TOOL_TYPE)
        values: dict[int, list[int]] = {}
        for code in codes:
            if code not in self.absinfo:
                continue
            buf = array.array("i", [code] + [0] * self.nslots)
            try:
                fcntl.ioctl(self.dev.fd, _ioc_read(0x0A, len(buf) * 4), buf, True)
                values[code] = list(buf[1:])
            except OSError:
                pass
        try:
            keys = set(self.dev.active_keys())
        except OSError:
            keys = set()
        return self.parser.resync(values, keys, ts)

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass
