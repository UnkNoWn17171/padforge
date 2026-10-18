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
