"""End-to-end tests against the running Xorg + libinput, using uinput devices.

Run with PADFORGE_INTEGRATION=1. The devices are floated, so the user's
cursor and windows are not affected.
"""
import os
import time

import pytest

INTEGRATION = os.environ.get("PADFORGE_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.skipif(not INTEGRATION, reason="PADFORGE_INTEGRATION=1 değil"),
    pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="X yok"),
    pytest.mark.skipif(not os.access("/dev/uinput", os.W_OK), reason="/dev/uinput yazılamıyor"),
]

if INTEGRATION:
    from padforge import accel, config, device, kinetic, touchegg, virtual, vpointer
    from padforge import xinput as xi
    from padforge.gestures import GestureRecognizer


@pytest.fixture
def x():
    return xi.XInput()


@pytest.fixture
def pad(x):
    vt = virtual.VirtualTouchpad(name=f"{virtual.TEST_PREFIX} Touchpad {os.getpid()}")
    dev = virtual.wait_x_device(x, vt.name)
    assert dev is not None, "X sanal touchpad'i görmedi"
    time.sleep(0.6)                      # let mutter apply its settings first
    x.float_device(dev.id)
    assert x.find(vt.name).floating
    yield vt, dev
    vt.close()


def test_properties_roundtrip(x, pad):
    vt, dev = pad
    caps = x.caps(dev.id, vt.geo.res)
    assert caps.has_custom
    plan = xi.plan_properties(config.defaults(), caps)
    assert x.apply(dev.id, plan) == []
    for name, values in plan:
        got = x.get_prop(dev.id, name)
        assert xi._same(got, values), f"{name}: {got} != {values}"


@pytest.mark.parametrize("speed", [20, 60, 150, 400])
def test_custom_curve_gain_matches_design(x, pad, speed):
    """The pixels libinput produces per mm equal the designed gain curve."""
    vt, dev = pad
    cfg = config.defaults()
    assert x.apply(dev.id, xi.plan_properties(cfg, x.caps(dev.id, vt.geo.res))) == []
    coll = virtual.RawCollector(x, dev.id)
    dist = 40.0
    got = virtual.stroke(vt, coll, 1, dist, 0, speed)
    px_per_mm = got.get(0, 0.0) / dist
    want = accel.gain_function(cfg["pointer"]["curve"])(speed)
    assert px_per_mm == pytest.approx(want, rel=0.08), (px_per_mm, want)


def test_custom_profile_keeps_scroll_speed(x, pad):
    """Without compensation the custom profile scrolls ~3x faster; PadForge keeps it equal."""
    vt, dev = pad
    coll = virtual.RawCollector(x, dev.id)
    cfg_stock = config.validate({"pointer": {"profile": "adaptive", "speed": 0.0}})
    assert x.apply(dev.id, xi.plan_properties(cfg_stock, x.caps(dev.id, vt.geo.res))) == []
    stock = abs(virtual.stroke(vt, coll, 2, 0, 30, 80).get(3, 0.0))
    cfg = config.defaults()
    assert x.apply(dev.id, xi.plan_properties(cfg, x.caps(dev.id, vt.geo.res))) == []
    custom = abs(virtual.stroke(vt, coll, 2, 0, 30, 80).get(3, 0.0))
    assert stock > 500
    assert custom == pytest.approx(stock, rel=0.15), (custom, stock)


def test_scroll_speed_setting_scales(x, pad):
    vt, dev = pad
    coll = virtual.RawCollector(x, dev.id)
    base = None
    for speed in (1.0, 2.0):
        cfg = config.validate({"scroll": {"speed": speed}})
        assert x.apply(dev.id, xi.plan_properties(cfg, x.caps(dev.id, vt.geo.res))) == []
        units = abs(virtual.stroke(vt, coll, 2, 0, 30, 80).get(3, 0.0))
        if base is None:
            base = units
        else:
            assert units == pytest.approx(2 * base, rel=0.15)


def test_theory_units_per_mm_close_to_measured(x, pad):
    """The kinetic fallback ratio matches what libinput really produces."""
    vt, dev = pad
    coll = virtual.RawCollector(x, dev.id)
    cfg = config.defaults()
    assert x.apply(dev.id, xi.plan_properties(cfg, x.caps(dev.id, vt.geo.res))) == []
    got = virtual.stroke(vt, coll, 2, 0, 40, 100).get(3, 0.0) / 40
    theory = accel.LIBINPUT_SCROLL_PX_PER_MM / 15 * kinetic.UNITS_PER_CLICK
    natural = x.get_prop(dev.id, xi.P_NATURAL) == [1]
    assert abs(got) == pytest.approx(theory, rel=0.15)
    assert (got < 0) == natural


def test_virtual_pointer_wheel_and_motion(x):
    vp = vpointer.VirtualPointer(name=f"{virtual.TEST_PREFIX} Pointer {os.getpid()}")
    try:
        dev = virtual.wait_x_device(x, vp.name)
        assert dev is not None
        time.sleep(0.6)
        x.float_device(dev.id)
        assert x.apply(dev.id, [(xi.P_PROFILE, [0, 1, 0]), (xi.P_SPEED, [0.0]),
                                (xi.P_NATURAL, [0])]) == []
        coll = virtual.RawCollector(x, dev.id)
        vp.scroll_units(0, 120)
        assert coll.drain().get(3) == pytest.approx(120)
        vp.scroll_units(-60, 0)
        vp.scroll_units(-60, 0)
        assert coll.drain().get(2) == pytest.approx(-120)
        for _ in range(10):
            vp.move(2.5, -1.25)
            time.sleep(0.01)
        got = coll.drain()
        assert got.get(0) == pytest.approx(25) and got.get(1) == pytest.approx(-12.5, abs=1)
    finally:
        vp.close()


def test_evdev_pipeline_recognizes_swipe(x):
    vt = virtual.VirtualTouchpad(name=f"{virtual.TEST_PREFIX} Evdev {os.getpid()}")
    try:
        dev = virtual.wait_x_device(x, vt.name)
        time.sleep(0.3)
        if dev:
            x.float_device(dev.id)
        node = vt.node
        if not node or not os.access(node, os.R_OK):
            pytest.skip("sanal touchpad okunamıyor (udev kuralı kurulu değil)")
        if touchegg.client_running():
            pytest.skip("Touchégg çalışıyor: 3 parmak hareketini o da işlerdi")
        info = next(p for p in device.list_touchpads() if p.node == node)
        reader = device.TouchpadReader(info)
        assert reader.resolution == vt.geo.res
        rec = GestureRecognizer()
        vt.put((30, 30), (45, 30), (60, 30))
        vt.move(0, -20, 0.15)
        vt.lift()
        time.sleep(0.05)
        events = []
        for f in reader.frames():
            events += rec.feed(f)
        assert [e.key for e in events] == ["swipe3_up"]
        reader.close()
    finally:
        vt.close()


def test_hidden_mouse_button_still_reported_raw(x):
    """What PadForge's mouse buttons rely on: a button mapped to 0 never reaches
    applications, yet XI2 raw events still report it with its physical number."""
    import select
    from evdev import UInput
    from evdev import ecodes as e
    from padforge import mouse
    ui = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA],
                 e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]},
                name=f"{virtual.TEST_PREFIX} Mouse {os.getpid()}", vendor=0x1209,
                product=0x7e58, bustype=e.BUS_USB)
    try:
        dev = virtual.wait_x_device(x, ui.name)
        assert dev is not None
        time.sleep(0.5)
        x.float_device(dev.id)
        X = xi.xinput
        x.select(x.d.screen().root, [(dev.id, X.RawButtonPressMask | X.ButtonPressMask)])

        def press(code):
            ui.write(e.EV_KEY, code, 1)
            ui.syn()
            ui.write(e.EV_KEY, code, 0)
            ui.syn()
            got, end = [], time.monotonic() + 0.3
            while time.monotonic() < end:
                select.select([x.d.fileno()], [], [], 0.02)
                while x.d.pending_events():
                    ev = x.d.next_event()
                    if getattr(ev, "evtype", None) == X.RawButtonPress:
                        got.append(("raw", xi.parse_raw_event(ev.evtype, ev.data).detail))
                    elif getattr(ev, "evtype", None) == X.ButtonPress:
                        got.append(("app", ev.data.detail))
            return got

        base = x.button_map(dev.id)
        assert base[:9] == list(range(1, 10))
        assert x.set_button_map(dev.id, mouse.wanted_map(base, {8}))
        assert x.button_map(dev.id)[7] == 0
        assert press(e.BTN_SIDE) == [("raw", 8)], "hidden from applications, still seen raw"
        assert press(e.BTN_EXTRA) == [("raw", 9), ("app", 9)], "other buttons untouched"
        assert x.set_button_map(dev.id, base)
        assert press(e.BTN_SIDE) == [("raw", 8), ("app", 8)], "given back"
    finally:
        ui.close()


def test_gesture_button_grab_hands_the_mouse_over_and_back(x):
    """A gesture button's passive grab: while held the mouse is ours (motion, wheel) and
    detached; the release ends the grab by itself and reattaches it."""
    import select
    from evdev import UInput
    from evdev import ecodes as e
    X = xi.xinput
    ui = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE],
                 e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES]},
                name=f"{virtual.TEST_PREFIX} Gesture {os.getpid()}", vendor=0x1209,
                product=0x7e5a, bustype=e.BUS_USB)
    try:
        dev = virtual.wait_x_device(x, ui.name)
        assert dev is not None
        time.sleep(0.5)
        root = x.d.screen().root
        start = root.query_pointer()             # the user's cursor: put back at the end
        assert x.grab_button(dev.id, 8)

        def pump():
            got, end = [], time.monotonic() + 0.25
            while time.monotonic() < end:
                select.select([x.d.fileno()], [], [], 0.02)
                while x.d.pending_events():
                    ev = x.d.next_event()
                    k = getattr(ev, "evtype", None)
                    if k in (X.ButtonPress, X.ButtonRelease) and ev.data.deviceid == dev.id:
                        got.append(("press" if k == X.ButtonPress else "release", ev.data.detail))
                    elif k == X.Motion and ev.data.deviceid == dev.id:
                        got.append(("motion",))
            return got

        ui.write(e.EV_KEY, e.BTN_SIDE, 1)
        ui.syn()
        assert ("press", 8) in pump()
        assert x.find(ui.name).floating, "detached while held: the cursor stands still"
        ui.write(e.EV_REL, e.REL_X, 40)
        ui.syn()
        assert ("motion",) in pump()
        ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, -120)
        ui.write(e.EV_REL, e.REL_WHEEL, -1)
        ui.syn()
        assert ("press", 5) in pump()
        ui.write(e.EV_KEY, e.BTN_SIDE, 0)
        ui.syn()
        assert ("release", 8) in pump()
        assert not x.find(ui.name).floating, "X ended the grab on the release by itself"
        x.ungrab_button(dev.id, 8)
        root.warp_pointer(start.root_x, start.root_y)
        x.d.sync()
    finally:
        ui.close()
