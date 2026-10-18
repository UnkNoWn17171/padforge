"""``padforge selftest``: verify the whole stack with a virtual touchpad.

Prints measured numbers (not just pass/fail) so a user can see what libinput
actually does with PadForge's settings on this machine.
"""
from __future__ import annotations

import os
import time

from . import accel, config, device, kinetic, touchegg, virtual, vpointer
from . import xinput as xi
from .diagnostics import helper_script
from .gestures import GestureRecognizer


class Report:
    def __init__(self):
        self.failed = 0

    def line(self, ok: bool | None, title: str, detail: str = "") -> None:
        mark = {True: "✔", False: "✖", None: "–"}[ok]
        if ok is False:
            self.failed += 1
        print(f"{mark} {title}" + (f"  ({detail})" if detail else ""), flush=True)


def main(verbose: bool = False) -> int:
    rep = Report()
    if not os.environ.get("DISPLAY"):
        rep.line(False, "X oturumu yok")
        return 1
    if not vpointer.available():
        rep.line(False, "/dev/uinput erişimi yok", "sanal aygıt oluşturulamıyor")
        return 1
    x = xi.XInput()
    vt = virtual.VirtualTouchpad(name=f"{virtual.TEST_PREFIX} Selftest")
    try:
        dev = virtual.wait_x_device(x, vt.name)
        if dev is None:
            rep.line(False, "Xorg sanal touchpad'i görmedi")
            return 1
        time.sleep(0.6)
        x.float_device(dev.id)
        rep.line(x.find(vt.name).floating, "Sanal touchpad oluşturuldu ve ekrandan ayrıldı",
                 f"X aygıtı {dev.id}")
        caps = x.caps(dev.id, vt.geo.res)
        rep.line(caps.has_custom, "libinput özel ivme profilini destekliyor")
        cfg = config.load()
        plan = xi.plan_properties(cfg, caps)
        errs = x.apply(dev.id, plan)
        rep.line(not errs, "Ayarlar libinput'a yazıldı", ", ".join(errs) or f"{len(plan)} özellik")
        coll = virtual.RawCollector(x, dev.id)

        if cfg["pointer"]["profile"] == "custom":
            g = accel.gain_function(cfg["pointer"]["curve"])
            for speed in (20, 60, 150, 400):
                got = virtual.stroke(vt, coll, 1, 40, 0, speed).get(0, 0.0) / 40
                want = g(speed)
                rep.line(abs(got - want) <= 0.12 * want,
                         f"İmleç kazancı @ {speed} mm/s: {got:.1f} px/mm",
                         f"hedef {want:.1f}")
        units = virtual.stroke(vt, coll, 2, 0, 30, 80).get(3, 0.0) / 30
        want = accel.LIBINPUT_SCROLL_PX_PER_MM * cfg["scroll"]["speed"] / 15 * kinetic.UNITS_PER_CLICK
        rep.line(abs(abs(units) - want) <= 0.15 * want,
                 f"İki parmak kaydırma: {abs(units) / 120:.2f} tık/mm",
                 f"hedef {want / 120:.2f}, yön {'doğal' if units < 0 else 'klasik'}")
    finally:
        vt.close()

    vp = vpointer.VirtualPointer(name=f"{virtual.TEST_PREFIX} Selftest Pointer")
    try:
        dev = virtual.wait_x_device(x, vp.name)
        time.sleep(0.6)
        x.float_device(dev.id)
        x.apply(dev.id, [(xi.P_PROFILE, [0, 1, 0]), (xi.P_SPEED, [0.0]), (xi.P_NATURAL, [0])])
        coll = virtual.RawCollector(x, dev.id)
        vp.scroll_units(0, 120)
        got = coll.drain().get(3, 0.0)
        rep.line(abs(got - 120) < 1, "Kinetik kaydırma çıkışı (yüksek çözünürlüklü tekerlek)",
                 f"+120 → {got:+.0f}")
    finally:
        vp.close()

    vt = virtual.VirtualTouchpad(name=f"{virtual.TEST_PREFIX} Selftest Evdev")
    try:
        dev = virtual.wait_x_device(x, vt.name)
        time.sleep(0.3)
        if dev:
            x.float_device(dev.id)
        node = vt.node
        if not node or not os.access(node, os.R_OK):
            rep.line(None, "Ham dokunma okuma testi atlandı",
                     f"udev kuralı kurulu değil: sudo bash {helper_script()}")
        elif touchegg.client_running():
            rep.line(None, "Hareket tanıma testi atlandı", "Touchégg çalışıyor")
        else:
            info = next(p for p in device.list_touchpads() if p.node == node)
            reader = device.TouchpadReader(info)
            rec = GestureRecognizer()
            vt.put((30, 30), (45, 30), (60, 30))
            vt.move(0, -20, 0.15)
            vt.lift()
            time.sleep(0.05)
            keys = []
            for f in reader.frames():
                keys += [e.key for e in rec.feed(f)]
            rep.line(keys == ["swipe3_up"], "Ham dokunma → hareket tanıma",
                     f"{reader.resolution:g} birim/mm, tanınan: {keys}")
            reader.close()
    finally:
        vt.close()

    print()
    print("Sonuç:", "BAŞARILI" if not rep.failed else f"{rep.failed} HATA")
    return 1 if rep.failed else 0
