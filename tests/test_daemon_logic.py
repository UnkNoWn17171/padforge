"""Daemon bookkeeping that does not need an X server."""
from padforge import daemon


def bare():
    from padforge.boost import Boost
    d = daemon.Daemon.__new__(daemon.Daemon)
    d.boost = Boost(call=lambda *a: 1)
    from padforge.wmdrag import SuperDrag
    d.wmdrag = SuperDrag()
    d.errors = []
    d.events = []
    d.mice, d._mouse_holds, d._ghold, d._raw_down, d._grab_end = {}, {}, None, {}, {}
    d._grab_logged = set()
    d._timers = {}
    d._reapply_times = []
    d.scheduled = 0

    def fake_timer(name, ms, fn):
        d._timers[name] = 1
        d.scheduled += 1
    d._timer = fake_timer
    return d


def test_burst_of_property_events_is_one_reapply():
    """A device re-add makes mutter write ~12 properties: that is not a fight."""
    d = bare()
    for _ in range(14):
        d._schedule_reapply("x")
    assert d.scheduled == 1
    assert not any("sürekli" in e for e in d.errors)


def test_real_fight_is_stopped():
    d = bare()
    for _ in range(daemon.MAX_REAPPLY_PER_10S + 3):
        d._timers.clear()               # each apply ran, then someone changed it again
        d._schedule_reapply("x")
    assert d.scheduled == daemon.MAX_REAPPLY_PER_10S
    assert sum("sürekli" in e for e in d.errors) == 1


def test_errors_are_deduplicated_and_clearable():
    d = bare()
    for _ in range(5):
        d.error("/dev/input/event5 okunamıyor — x")
    assert len(d.errors) == 1
    d.error("Touchégg çalışıyor: y")
    d.clear_errors("event5 okunamıyor")
    assert len(d.errors) == 1 and "Touchégg" in d.errors[0]


def test_effective_config_stands_down_for_touchegg():
    from padforge import config
    d = bare()
    d.cfg = config.defaults()
    d.touchegg = False
    assert d.effective_config() is d.cfg
    d.touchegg = True
    eff = d.effective_config()
    assert eff["gestures"]["enabled"] is False and eff["tap"]["engine"] == "libinput"
    assert eff["scroll"]["kinetic"]["enabled"] is True, "kinetic scrolling does not clash"
    assert d.cfg["gestures"]["enabled"] is True, "user config is untouched"


# -- held buttons / modifiers (the 2026-10-18 "frozen desktop") -----------------------
class FakeVP:
    def __init__(self):
        self.calls = []
        self.down = False

    def button(self, b, down):
        self.calls.append((b, down))
        self.down = down

    def move(self, dx, dy):
        pass


class FakeExec:
    def __init__(self):
        self._cont = None
        self.log = []

    def begin(self, action, arg, inc):
        self._cont = action

    def step(self, inc):
        pass

    def end(self):
        self._cont = None

    def run(self, *a):
        self.log.append(a)

    def click(self, b):
        pass

    def button(self, b, down):
        pass


def dragging_daemon(hand):
    from padforge import config, device
    from padforge.gestures import GestureRecognizer
    d = bare()
    d.cfg = config.defaults()
    d.cfg["gestures"]["three_finger_drag"] = True
    d.touchegg = False
    d.eff = d.cfg
    d.continuous_keys = set()
    d.pause_clients = set()
    d.vp = FakeVP()
    d.executor = FakeExec()
    d._drag_pressed = False
    d.gain = lambda v: 10.0
    d.coaster = __import__("padforge.kinetic", fromlist=["Coaster"]).Coaster()
    d.last_key_time = None
    pad = daemon.Pad(device.TouchpadInfo("/dev/input/event99", "test", "/sys/x"), None)
    pad.recognizer = GestureRecognizer(three_finger_drag=True)
    d.pads = {pad.info.node: pad}
    for f in hand.put((40, 30), (55, 30), (70, 30)) + hand.move(10, 0, 0.1):
        for g in pad.recognizer.feed(f):
            d._gesture(g)
        pad.last_count, pad.last_time = f.count, f.time
    assert d.vp.down and d._drag_pressed
    return d, pad


def test_reload_during_three_finger_drag_releases_button(hand):
    d, pad = dragging_daemon(hand)
    d._build_engines(pad)                 # what a config reload does
    assert not d.vp.down and not d._drag_pressed


def test_reload_during_app_switch_releases_modifier(hand):
    d, pad = dragging_daemon(hand)
    d.executor._cont = "app_switch"
    d._build_engines(pad)
    assert d.executor._cont is None


def test_watchdog_releases_when_no_finger(hand):
    d, pad = dragging_daemon(hand)
    pad.last_count = 0                    # e.g. a lost lift event
    d._flush_x = lambda: None
    d._watchdog()
    assert not d.vp.down


def test_watchdog_keeps_a_real_drag(hand):
    import time
    d, pad = dragging_daemon(hand)
    pad.last_time = time.monotonic()      # fingers still reporting
    d._flush_x = lambda: None
    d._watchdog()
    assert d.vp.down


def test_reload_keeps_running_config_while_file_is_half_written(tmp_path):
    from padforge import config
    d = bare()
    d.config_file = tmp_path / "config.json"
    d.config_file.write_text('{"scroll": {"nat')
    d.cfg = config.defaults()
    d.reload()
    assert d.cfg == config.defaults()
    assert d.config_file.exists() and not (tmp_path / "config.json.broken").exists()
    assert any("ayar dosyası okunamadı" in e for e in d.errors)


class DeadDisplay:
    def flush(self):
        from Xlib import error
        raise error.ConnectionClosedError("server")


class FakeLoop:
    def __init__(self):
        self.quits = 0

    def quit(self):
        self.quits += 1


def test_lost_x_connection_quits_instead_of_spinning():
    """A dead X socket stays readable: the HIGH priority watch would starve SIGTERM."""
    from gi.repository import GLib
    d = bare()
    d.x = type("X", (), {"d": DeadDisplay()})()
    d.x_alive, d.loop = True, FakeLoop()
    d._flush_x()
    d._flush_x()
    assert d.x_alive is False and d.loop.quits == 1
    assert d._on_x(0, GLib.IOCondition.IN) is False, "the X watch removes itself"


def test_hangup_on_x_socket_quits():
    from gi.repository import GLib
    d = bare()
    d.x_alive, d.loop = True, FakeLoop()
    assert d._on_x(0, GLib.IOCondition.HUP) is False and d.loop.quits == 1


def test_cleanup_without_x_still_frees_power_hold_and_pointer():
    d = bare()
    d.x_alive, d.x = False, None            # any X call would raise
    freed = []
    d.boost.release = lambda: freed.append("boost")
    d.vp = type("VP", (), {"close": lambda self: freed.append("vp")})()
    d._server = None
    d.cleanup()
    assert freed == ["boost", "vp"]


class FakeExecutor:
    def __init__(self):
        self._cont = "window_cycle"           # Super held for the window menu
        self.ended = 0

    def end(self):
        self.ended += 1
        self._cont = None


def test_dropping_a_pad_mid_window_menu_lets_go_of_super():
    from padforge import config, device
    from padforge.gestures import GestureRecognizer, _Track
    d = bare()
    d.cfg = config.defaults()
    d.executor, d._drag_pressed, d.pause_clients = FakeExecutor(), False, set()
    r = GestureRecognizer(continuous_keys={"swipe3_down"})
    r.state, r.track = r.CONTINUOUS, _Track(n=3, t0=0.0, start={}, key="swipe3_down")
    pad = daemon.Pad(device.TouchpadInfo(node="/dev/input/event99", name="test", sysfs="/x"), None)
    pad.recognizer, pad.last_count = r, 3
    d.pads = {pad.info.node: pad}
    d._drop_pad(pad)
    assert d.executor.ended == 1 and d.executor._cont is None and d.pads == {}
