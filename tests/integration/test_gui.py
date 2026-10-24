"""GUI behaviour: widgets write the config, profiles and the curve editor round-trip.

Needs a display: run with PADFORGE_GUI_DISPLAY=:N (e.g. a Xephyr server) so no window
appears on the user's screen.
"""
import json
import os
import time

import pytest

GUI_DISPLAY = os.environ.get("PADFORGE_GUI_DISPLAY")
pytestmark = pytest.mark.skipif(not GUI_DISPLAY, reason="PADFORGE_GUI_DISPLAY yok")


@pytest.fixture(scope="module")
def app():
    old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = GUI_DISPLAY
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio
    Adw.init()
    a = Adw.Application(application_id="io.github.padforge.Test",
                        flags=Gio.ApplicationFlags.NON_UNIQUE)
    a.register(None)
    yield a
    # Later tests (X11 integration) must talk to the real display again.
    if old_display is None:
        os.environ.pop("DISPLAY", None)
    else:
        os.environ["DISPLAY"] = old_display


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    from padforge import config
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "config_path", lambda: cfg_file)
    from gi.repository import GLib
    from padforge.gui import service
    from padforge.gui.window import MainWindow
    monkeypatch.setattr(service, "daemon_status", lambda: None)
    w = MainWindow(app)
    w.present()
    ctx = GLib.MainContext.default()

    def spin(seconds=0.4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.01)
    w.spin = spin
    w.cfg_file = cfg_file
    spin(0.3)
    yield w
    w.close()
    spin(0.1)


def saved(w):
    w.spin(0.5)
    return json.loads(w.cfg_file.read_text())


def page(w, pid):
    return next(p for p in w.pages if p.id == pid)


def test_switch_row_writes_config(win):
    from gi.repository import Adw
    scroll = page(win, "scroll")
    rows = []

    def walk(widget):
        if isinstance(widget, Adw.SwitchRow):
            rows.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(scroll.widget)
    natural = next(r for r in rows if r.get_title() == "Doğal yön")
    before = win.get("scroll.natural")
    natural.set_active(not before)
    assert saved(win)["scroll"]["natural"] is (not before)


def test_profile_and_refresh(win):
    win.apply_profile("macos", "macOS")
    data = saved(win)
    assert data["gestures"]["three_finger_drag"] is True
    gestures = page(win, "gestures")
    assert gestures.three_drag.get_active() is True
    # 3-finger swipe rows are disabled while 3-finger drag is on
    assert not gestures.binding_rows["swipe3_up"][0].get_sensitive()


def test_curve_editor_edit_makes_custom_curve(win):
    pointer = page(win, "pointer")
    pts = [[0, 6.0], [100, 20.0], [600, 45.0]]
    pointer.editor.on_change(pts)
    data = saved(win)
    assert data["pointer"]["curve"]["preset"] == "custom"
    assert data["pointer"]["curve"]["points"] == [[0.0, 6.0], [100.0, 20.0], [600.0, 45.0]]
    assert pointer.preset_row.get_selected() == 4


def test_binding_with_argument(win):
    gestures = page(win, "gestures")
    row, entry = gestures.binding_rows["swipe4_up"]
    from padforge.gui.pages import ACTION_IDS
    row.set_selected(ACTION_IDS.index("keys"))
    win.spin(0.1)
    assert entry.get_visible()
    entry.set_text("<Super>t")
    entry.emit("apply")
    data = saved(win)
    assert data["gestures"]["bindings"]["swipe4_up"] == {"action": "keys", "arg": "<Super>t"}
    row.set_selected(ACTION_IDS.index("overview"))
    win.spin(0.1)
    assert not entry.get_visible()
    assert saved(win)["gestures"]["bindings"]["swipe4_up"] == {"action": "overview"}


def test_spin_row_integer_stays_integer(win):
    from gi.repository import Adw
    tap = page(win, "tap")
    found = []

    def walk(widget):
        if isinstance(widget, Adw.SpinRow) and "En uzun dokunuş" in widget.get_title():
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(tap.widget)
    found[0].set_value(150)
    v = saved(win)["tap"]["max_duration_ms"]
    assert v == 150 and isinstance(v, int)


def test_picking_custom_preset_starts_from_shown_curve(win):
    from padforge import accel
    pointer = page(win, "pointer")
    pointer.preset_row.set_selected(1)          # macOS
    win.spin(0.1)
    pointer.preset_row.set_selected(4)          # Özel eğri
    data = saved(win)
    assert data["pointer"]["curve"]["preset"] == "custom"
    assert [tuple(p) for p in data["pointer"]["curve"]["points"]] == accel.PRESETS["macos"]


def test_change_made_elsewhere_is_picked_up_not_overwritten(win):
    """E.g. the daemon adopting a GNOME Settings change: the next save here must keep it."""
    from padforge import config
    from padforge.gui.pages import ACTION_IDS
    cfg = config.defaults()
    cfg["gestures"]["bindings"]["swipe3_up"] = {"action": "minimize"}
    config.save(cfg, win.cfg_file)
    win.spin(0.6)
    assert win.get("gestures.bindings.swipe3_up") == {"action": "minimize"}
    row, _ = page(win, "gestures").binding_rows["swipe3_up"]
    assert row.get_selected() == ACTION_IDS.index("minimize")
    win.set("scroll.natural", not win.get("scroll.natural"))     # an unrelated edit here
    assert saved(win)["gestures"]["bindings"]["swipe3_up"] == {"action": "minimize"}


def test_closing_does_not_write_back_an_old_copy(win):
    """Closing with nothing edited must not overwrite a change made elsewhere a moment ago."""
    from padforge import config
    cfg = config.defaults()
    cfg["gestures"]["bindings"]["swipe3_up"] = {"action": "minimize"}
    config.save(cfg, win.cfg_file)
    win._closing()                      # before the file monitor had a chance to read it
    win.spin(0.3)
    assert json.loads(win.cfg_file.read_text())["gestures"]["bindings"]["swipe3_up"] == \
        {"action": "minimize"}


def test_mouse_page_learns_a_pressed_button_and_assigns_it(win):
    from padforge.gui.pages import ACTION_IDS, MOUSE_MODES
    mp = page(win, "mouse")
    win.show_page("mouse")
    mp.status_changed({"mice": [{"name": "Logitech USB Receiver Mouse", "seen": [9]}]})
    assert 9 in mp.rows, "buttons the service has seen get a row"
    mp.pressed(8)                                    # the user presses the back button
    row = mp.rows[8]["row"]
    assert "az önce" in row.get_subtitle()
    row.set_selected(1 + len(MOUSE_MODES) + ACTION_IDS.index("super_key"))
    assert saved(win)["mouse"]["buttons"] == {"8": {"action": "super_key"}}
    row.set_selected(0)                              # back to "default"
    assert saved(win)["mouse"]["buttons"] == {}


def test_mouse_page_shortcut_argument(win):
    from padforge.gui.pages import ACTION_IDS, MOUSE_MODES
    mp = page(win, "mouse")
    mp.pressed(9)
    row, entry = mp.rows[9]["row"], mp.rows[9]["entry"]
    row.set_selected(1 + len(MOUSE_MODES) + ACTION_IDS.index("keys"))
    assert entry.get_visible()
    entry.set_text("<Super>Page_Down")
    entry.emit("apply")
    assert saved(win)["mouse"]["buttons"]["9"] == {"action": "keys", "arg": "<Super>Page_Down"}


def test_mouse_page_does_not_hold_back_actions(win, monkeypatch):
    """The first version paused the service on this page: a freshly assigned button
    then did nothing at all, which looked like the setting had not been applied."""
    from padforge.gui import service
    calls = []
    monkeypatch.setattr(service.PauseHandle, "acquire", lambda self: calls.append(1) or True)
    win.show_page("mouse")
    win.spin(0.2)
    assert calls == []



def test_mouse_page_gesture_button(win):
    from padforge import mouse
    from padforge.gui.pages import MOUSE_MODES, WHEEL_IDS
    mp = page(win, "mouse")
    mp.pressed(9)
    r = mp.rows[9]
    r["row"].set_selected(1 + MOUSE_MODES.index("gesture"))
    assert saved(win)["mouse"]["buttons"]["9"] == mouse.gesture_defaults(9)
    assert r["parts"].get_visible()
    wheel_row, ids = r["part_rows"]["wheel"]
    assert ids[wheel_row.get_selected()] == "window_cycle"
    wheel_row.set_selected(WHEEL_IDS.index("volume"))
    assert saved(win)["mouse"]["buttons"]["9"]["wheel"] == {"action": "volume"}
    r["row"].set_selected(1 + MOUSE_MODES.index("hold_super"))
    assert saved(win)["mouse"]["buttons"]["9"] == {"action": "hold_super"}
    assert not r["parts"].get_visible() and not r["test"].get_sensitive()
