"""External mouse buttons: map bookkeeping and the daemon side, without an X server."""
import struct
from types import SimpleNamespace

from padforge import config, device, mouse
from padforge.xinput import xinput as XI

IDENT = list(range(1, 21))          # the Bolt receiver's mouse reports 20 buttons


def test_wanted_map_switches_off_only_assigned_buttons():
    assert mouse.wanted_map([1, 2, 3, 4, 5, 6, 7, 8, 9], {8, 2}) == [1, 0, 3, 4, 5, 6, 7, 0, 9]
    assert mouse.wanted_map([1, 2, 3], {8}) == [1, 2, 3], "a button the mouse lacks is ignored"


def test_base_map_undoes_our_zeros_but_keeps_the_users():
    # 8 was switched off by a daemon that crashed; 2 by the user with xinput (no middle paste)
    assert mouse.base_map([1, 0, 3, 4, 5, 6, 7, 0, 9], ours={8}) == [1, 0, 3, 4, 5, 6, 7, 8, 9]


def test_labels():
    assert mouse.label(8) == "Yan tuş · Geri" and mouse.label(13) == "Ek tuş 13"


def test_declared_buttons_follow_the_x_driver_numbering():
    info = device.MouseInfo("/dev/input/event9", "m", "/sys/x",
                            key_codes={0x110, 0x111, 0x112, 0x113, 0x114})
    assert info.declared_buttons == [2, 8, 9]


def test_config_keeps_only_assignable_buttons():
    c = config.validate({"mouse": {"buttons": {"8": {"action": "super_key"}, "9": "back",
                                               "1": "none", "3": "none", "5": "none",
                                               "x": "none", "99": "none", "2": 42}}})
    assert c["mouse"]["buttons"] == {"8": {"action": "super_key"}, "9": {"action": "back"}}
    assert config.defaults()["mouse"]["buttons"] == {}


# -- daemon --------------------------------------------------------------------------
class FakeX:
    def __init__(self, maps):
        self.maps, self.busy, self.writes = maps, False, []
        self.grabs = set()
        self.d = SimpleNamespace(sync=lambda: None, flush=lambda: None)

    def grab_button(self, xid, button):
        self.grabs.add((xid, button))
        return True

    def ungrab_button(self, xid, button):
        self.grabs.discard((xid, button))

    def button_map(self, xid):
        return list(self.maps[xid])

    def set_button_map(self, xid, m):
        if self.busy:
            return False
        self.writes.append((xid, list(m)))
        self.maps[xid] = list(m)
        return True


class FakeExecutor:
    def __init__(self):
        self.ran, self._cont = [], None

    def run(self, action, arg=None):
        self.ran.append((action, arg))

    def hold_key(self, key, down):
        self.ran.append(("hold", key, down))

    def begin(self, action, arg, inc):
        self._cont = action
        self.ran.append(("begin", action, inc))

    def step(self, inc):
        self.ran.append(("step", inc))

    def end(self):
        if self._cont is not None:
            self.ran.append(("end", self._cont))
        self._cont = None


INFO = device.MouseInfo("/dev/input/event9", "Logitech USB Receiver Mouse", "/sys/devices/usb")


def make(buttons, current=None):
    from test_daemon_logic import bare
    d = bare()
    d.cfg = config.validate({"mouse": {"buttons": buttons}})
    d.cache, d.pause_clients, d.executor = {}, set(), FakeExecutor()
    d.x = FakeX({9: list(current or IDENT)})
    d.mice = {}
    d.notes = []
    d._timer = lambda name, ms, fn: d._timers.__setitem__(name, fn)   # run them by hand
    d.warps = []
    d.root = SimpleNamespace(warp_pointer=lambda x, y: d.warps.append((x, y)))
    d._notify = lambda summary, body: d.notes.append(summary)
    d._scan_mice([SimpleNamespace(id=9, node=INFO.node, floating=False)])
    return d


def scan_with(monkeypatch):
    monkeypatch.setattr(device, "list_mice", lambda: [INFO])


def test_assigned_button_is_hidden_and_runs_its_action(monkeypatch):
    scan_with(monkeypatch)
    d = make({"8": {"action": "super_key"}})
    d._apply_mice()
    assert d.x.maps[9][7] == 0 and d.x.maps[9][8] == 9, "only button 8 is hidden"
    d._mouse_button(d.mice[9], 8)
    d._mouse_button(d.mice[9], 9)                  # not assigned: the app gets it
    assert d.executor.ran == [("super_key", None)]
    assert d.cache["mice"][INFO.name]["seen"] == [8, 9], "the real buttons are learned"
    assert d.cache["mice"][INFO.name]["zeroed"] == [8]


def test_nothing_runs_while_the_map_could_not_be_set(monkeypatch):
    """Otherwise the application would get the click *and* the action would run."""
    scan_with(monkeypatch)
    d = make({"8": {"action": "super_key"}})
    d.x.busy = True                                # button held while applying
    d._apply_mice()
    assert "mice" in d._timers, "retried shortly"
    d._mouse_button(d.mice[9], 8)
    assert d.executor.ran == []


def test_unassigning_gives_the_button_back(monkeypatch):
    scan_with(monkeypatch)
    d = make({"8": {"action": "super_key"}})
    d._apply_mice()
    d.cfg = config.defaults()
    d._apply_mice()
    assert d.x.maps[9] == IDENT and d.cache["mice"][INFO.name]["zeroed"] == []


def test_restore_on_exit_and_after_a_crash(monkeypatch):
    scan_with(monkeypatch)
    d = make({"9": {"action": "overview"}})
    d._apply_mice()
    d._restore_mice()
    assert d.x.maps[9] == IDENT
    # a crash left 9 switched off: the next daemon gives it back before anything else
    crashed = list(IDENT)
    crashed[8] = 0
    d2 = make({}, current=crashed)
    d2.cache = {"mice": {INFO.name: {"zeroed": [9], "announced": True}}}
    d2._scan_mice([SimpleNamespace(id=9, node=INFO.node, floating=False)])
    d2._apply_mice()
    assert d2.x.maps[9] == IDENT


def test_new_mouse_is_announced_once_and_not_during_start_up(monkeypatch):
    scan_with(monkeypatch)
    d = make({})
    assert d.notes == [], "a notification can stall X: never on the start-up path"
    d._timers[f"duyuru {INFO.name}"]()
    assert d.notes == ["Fare bulundu: Logitech USB Receiver Mouse"]
    d.mice = {}                                    # unplugged and plugged in again
    d._scan_mice([SimpleNamespace(id=9, node=INFO.node, floating=False)])
    assert len(d.notes) == 1


def raw_press(deviceid, detail, sourceid=None):
    data = struct.pack("=HIIHHI", deviceid, 0, detail, sourceid or deviceid, 0, 0) + b"\0" * 4
    return SimpleNamespace(evtype=XI.RawButtonPress, data=data)


def test_raw_press_from_the_mouse_runs_the_action_but_our_own_xtest_click_does_not(monkeypatch):
    scan_with(monkeypatch)
    d = make({"8": {"action": "back"}})
    d._apply_mice()
    d.xtest_ids, d.vp_xid, d.pads = {4}, None, {}
    d._stop_coast = lambda: None
    d._x_event(raw_press(9, 8))
    d._x_event(raw_press(4, 8))                    # PadForge's own XTest "back" click
    assert d.executor.ran == [("back", None)]


def test_paused_by_the_gui_runs_nothing(monkeypatch):
    scan_with(monkeypatch)
    d = make({"8": {"action": "back"}})
    d._apply_mice()
    d.pause_clients = {7}
    d._mouse_button(d.mice[9], 8)
    assert d.executor.ran == []
    assert "çalıştırılmadı" in d.events[-1][1], "the log says why nothing happened"


# -- gesture button: pure state machine ----------------------------------------------------
GB = mouse.gesture_defaults(9)


def test_gesture_defaults_keep_the_buttons_own_job_on_tap():
    assert GB["tap"] == {"action": "forward"} and mouse.gesture_defaults(8)["tap"] == {"action": "back"}
    assert GB["wheel"] == {"action": "window_cycle"}
    assert config.validate({"mouse": {"buttons": {"9": GB}}})["mouse"]["buttons"]["9"] == GB


def test_tap():
    g = mouse.GestureHold(9, GB, 500, 400, t=10.0)
    assert g.release(10.15) == [("run", {"action": "forward"})]


def test_long_press_with_nothing_done_is_nothing():
    g = mouse.GestureHold(9, GB, 500, 400, t=10.0)
    assert g.release(11.0) == []


def test_wheel_drives_the_window_menu_and_release_picks():
    g = mouse.GestureHold(9, GB, 500, 400, t=10.0)
    assert g.wheel(down=True) == [("begin", {"action": "window_cycle"}, 1)], "down = next window"
    assert g.wheel(down=True) == [("step", 1)]
    assert g.wheel(down=False) == [("step", -1)]
    assert g.release(12.0) == [("end",)], "letting go selects the window, no tap"


def test_wheel_volume_up_is_louder():
    g = mouse.GestureHold(8, dict(GB, wheel={"action": "volume"}), 0, 0, t=0.0)
    assert g.wheel(down=False) == [("begin", {"action": "volume"}, 1)]
    assert g.wheel(down=True) == [("step", -1)]


def test_flick_fires_once_and_is_not_a_tap():
    g = mouse.GestureHold(9, GB, 500, 400, t=10.0)
    assert g.move(510, 360) == [], "the first motion only sets where flicks start"
    assert g.move(515, 360 - mouse.FLICK_PX - 5) == [("run", {"action": "overview"})]
    assert g.move(515, 100) == []
    assert g.release(10.2) == []


def test_flick_directions():
    for (dx, dy), part in (((-100, 0), "left"), ((100, 0), "right"), ((0, 100), "down")):
        g = mouse.GestureHold(9, GB, 0, 0, t=0.0)
        g.move(0, 0)
        assert g.move(dx, dy) == [("run", GB[part])]


def test_jump_of_the_detached_pointer_is_no_flick():
    """Real mouse, 2026-10-24: pressed at x=2240, the first event came at x=1920."""
    g = mouse.GestureHold(9, GB, 2240, 500, t=0.0)
    assert g.move(1920, 540) == []
    assert g.move(1925, 541) == []
    assert g.release(0.2) == [("run", {"action": "forward"})], "still a tap"


def test_no_surprise_flick_while_turning_the_wheel():
    g = mouse.GestureHold(9, GB, 0, 0, t=0.0)
    g.wheel(down=True)
    assert g.move(0, -300) == []


# -- gesture button and hold keys in the daemon --------------------------------------------
def xev(detail=0, x=500.0, y=400.0):
    return SimpleNamespace(deviceid=9, detail=detail, root_x=x, root_y=y)


def test_gesture_button_is_grabbed_not_hidden(monkeypatch):
    scan_with(monkeypatch)
    d = make({"9": GB, "8": {"action": "back"}})
    d._apply_mice()
    assert d.x.grabs == {(9, 9)}
    assert d.x.maps[9][8] == 9 and d.x.maps[9][7] == 0, "9 stays mapped (grabbed), 8 hidden"
    d.cfg = config.validate({"mouse": {"buttons": {"9": {"action": "overview"}}}})
    d._apply_mice()
    assert d.x.grabs == set() and d.x.maps[9][8] == 0


def test_gesture_flow_in_the_daemon(monkeypatch):
    scan_with(monkeypatch)
    d = make({"9": GB})
    d._apply_mice()
    m, X = d.mice[9], XI
    d._gesture_event(m, X.ButtonPress, xev(9))
    d._gesture_event(m, X.ButtonPress, xev(5))         # wheel down
    d._gesture_event(m, X.ButtonPress, xev(5))
    d._gesture_event(m, X.Motion, xev(x=530))           # the detached mouse moved a little
    d._gesture_event(m, X.ButtonRelease, xev(9, x=530))
    assert d.executor.ran == [("begin", "window_cycle", 1), ("step", 1), ("end", "window_cycle")]
    assert d.warps == [(500, 400)], "the cursor goes back where the press was"
    assert d._ghold is None


def test_touchpad_lift_and_watchdog_leave_the_mouse_window_menu_alone(monkeypatch):
    scan_with(monkeypatch)
    d = make({"9": GB})
    d._apply_mice()
    m = d.mice[9]
    d._drag_pressed = False
    d._gesture_event(m, XI.ButtonPress, xev(9))
    d._gesture_event(m, XI.ButtonPress, xev(5))
    d._release_held("parmaklar kalktı")                 # fingers lifted off the touchpad
    d._flush_x = lambda: None
    d._watchdog()
    assert d.executor._cont == "window_cycle", "still browsing windows with the mouse"
    d._ghold.t0 -= mouse.MAX_HOLD_S + 1                 # a release we never saw
    d._watchdog()
    assert d.executor._cont is None and d._ghold is None


def test_hold_super_button(monkeypatch):
    scan_with(monkeypatch)
    d = make({"9": {"action": "hold_super"}})
    d._apply_mice()
    m = d.mice[9]
    assert m.hidden(9)
    m.held.add(9)
    d._mouse_button(m, 9)
    d._mouse_button(m, 9)                               # a repeat does not press twice
    d._mouse_button_up(m, 9)
    assert d.executor.ran == [("hold", "Super_L", True), ("hold", "Super_L", False)]


def test_never_remap_a_button_while_it_is_down(monkeypatch):
    """Doing so left Xorg with a phantom 'button 0' down (seen 2026-10-19)."""
    scan_with(monkeypatch)
    d = make({})
    d.mice[9].held.add(8)
    d.cfg = config.validate({"mouse": {"buttons": {"8": {"action": "back"}}}})
    d._apply_mice()
    assert d.x.writes == [] and "mice" in d._timers
    d.mice[9].held.clear()
    d._timers["mice"]()
    assert d.x.maps[9][7] == 0


def test_release_summary_tells_a_short_press_from_a_lost_wheel():
    g = mouse.GestureHold(9, GB, 0, 0, t=0.0)
    g.move(3, 4)
    line = g.summary(0.3, g.release(0.3))
    assert "tekerlek 0 çentik" in line and "forward" in line
    g = mouse.GestureHold(9, GB, 0, 0, t=0.0)
    g.wheel(down=True)
    g.wheel(down=True)
    assert "tekerlek 2 çentik" in g.summary(1.2, g.release(1.2))
    g = mouse.GestureHold(9, GB, 0, 0, t=0.0)
    assert "uzun" in g.summary(1.5, g.release(1.5))
