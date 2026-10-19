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
