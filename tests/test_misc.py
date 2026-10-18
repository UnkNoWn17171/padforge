"""gnome sync, Touchégg import, stats and device discovery."""
import textwrap

import pytest

from padforge import config, device, gnome, stats, touchegg

from conftest import Hand


# -- gnome ---------------------------------------------------------------------------
def test_to_gsettings_defaults():
    g = gnome.to_gsettings(config.defaults())
    assert g["tap-to-click"] is True and g["natural-scroll"] is True
    assert g["two-finger-scrolling-enabled"] and not g["edge-scrolling-enabled"]
    assert g["click-method"] == "areas" and g["left-handed"] == "mouse"
    assert "speed" not in g, "custom profile: GNOME speed is left alone"


def test_to_gsettings_stock_profile_writes_speed():
    cfg = config.validate({"pointer": {"profile": "flat", "speed": 0.25}})
    g = gnome.to_gsettings(cfg)
    assert g["accel-profile"] == "flat" and g["speed"] == 0.25


@pytest.mark.parametrize("key,value,path,want", [
    ("natural-scroll", False, ("scroll", "natural"), False),
    ("tap-to-click", False, ("tap", "enabled"), False),
    ("click-method", "fingers", ("click", "method"), "clickfinger"),
    ("left-handed", "left", ("click", "left_handed"), True),
    ("edge-scrolling-enabled", True, ("scroll", "method"), "edge"),
    ("disable-while-typing", False, ("typing", "disable_while_typing"), False),
])
def test_adopt(key, value, path, want):
    new = gnome.adopt(config.defaults(), key, value)
    assert new is not None and new[path[0]][path[1]] == want


def test_adopt_no_change_or_unknown():
    cfg = config.defaults()
    assert gnome.adopt(cfg, "natural-scroll", True) is None
    assert gnome.adopt(cfg, "send-events", "disabled") is None
    assert gnome.adopt(cfg, "speed", 0.5) is None, "custom curve ignores GNOME speed"


def test_seed_from_gnome_keeps_user_settings():
    values = {"natural-scroll": False, "tap-to-click": True, "speed": 0.38, "click-method": "fingers",
              "disable-while-typing": False, "left-handed": "mouse",
              "two-finger-scrolling-enabled": True, "edge-scrolling-enabled": False}
    out = gnome.seed(config.defaults(), values)
    assert out["scroll"]["natural"] is False and out["pointer"]["speed"] == 0.38
    assert out["click"]["method"] == "clickfinger"
    assert out["typing"]["disable_while_typing"] is False
    assert out["scroll"]["method"] == "twofinger"
    assert out["pointer"]["profile"] == "custom"
    assert out == config.validate(out)


def test_two_finger_off_turns_scrolling_off():
    new = gnome.adopt(config.defaults(), "two-finger-scrolling-enabled", False)
    assert new["scroll"]["method"] == "none"


# -- touchegg ------------------------------------------------------------------------
TOUCHEGG_XML = textwrap.dedent("""\
    <touchégg>
      <settings><property name="animation_delay">150</property></settings>
      <application name="All">
        <gesture type="PINCH" fingers="3" direction="IN"><action type="CLOSE_WINDOW"/></gesture>
        <gesture type="PINCH" fingers="4" direction="IN">
          <action type="SEND_KEYS"><modifiers>Super_L</modifiers><keys>A</keys></action>
        </gesture>
        <gesture type="TAP" fingers="2" direction="UNKNOWN">
          <action type="MOUSE_CLICK"><button>3</button></action>
        </gesture>
        <gesture type="SWIPE" fingers="3" direction="LEFT"><action type="CHANGE_DESKTOP"/></gesture>
        <gesture type="SWIPE" fingers="3" direction="RIGHT"><action type="CHANGE_DESKTOP"/></gesture>
        <gesture type="SWIPE" fingers="3" direction="DOWN"><action type="MINIMIZE_WINDOW"/></gesture>
        <gesture type="SWIPE" fingers="3" direction="UP">
          <action type="SEND_KEYS"><modifiers>Super_L</modifiers><keys>Tab</keys>
            <repeat>true</repeat></action>
        </gesture>
        <gesture type="SWIPE" fingers="4" direction="UP">
          <action type="RUN_COMMAND"><command>gnome-terminal</command></action>
        </gesture>
        <gesture type="SWIPE" fingers="4" direction="DOWN">
          <action type="SEND_KEYS"><modifiers>Control_L+Alt_L</modifiers><keys>Delete</keys></action>
        </gesture>
      </application>
      <application name="google-chrome">
        <gesture type="PINCH" fingers="2" direction="OUT">
          <action type="SEND_KEYS"><repeat>true</repeat><modifiers>Control_L</modifiers>
            <keys>KP_Add</keys></action>
        </gesture>
        <gesture type="SWIPE" fingers="3" direction="UP"><action type="CLOSE_WINDOW"/></gesture>
      </application>
    </touchégg>
""")


def test_touchegg_import(tmp_path):
    p = tmp_path / "touchegg.conf"
    p.write_text(TOUCHEGG_XML)
    r = touchegg.parse(p)
    b = r["bindings"]
    assert b["pinch3_in"] == {"action": "close_window"}
    assert b["pinch4_in"] == {"action": "app_grid"}
    assert b["swipe3_left"] == {"action": "workspace_next"}
    assert b["swipe3_right"] == {"action": "workspace_prev"}
    assert b["swipe3_down"] == {"action": "minimize"}
    assert b["swipe3_up"] == {"action": "app_switch"}
    assert b["swipe4_up"] == {"action": "command", "arg": "gnome-terminal"}
    assert b["swipe4_down"] == {"action": "keys", "arg": "<Control><Alt>Delete"}
    assert r["pinch_zoom_2f"] is True
    assert any("dokunma" in n for n in r["notes"])
    assert any("google-chrome" in s for s in r["skipped"])
    merged = touchegg.merge_into(config.defaults(), r)
    assert merged["gestures"]["bindings"]["swipe3_up"] == {"action": "app_switch"}
    assert merged == config.validate(merged)


def test_touchegg_disable_and_enable(tmp_path, monkeypatch):
    sysfile = tmp_path / "sys.desktop"
    sysfile.write_text("[Desktop Entry]\nType=Application\nExec=touchegg\n")
    user = tmp_path / "autostart" / "touchegg.desktop"
    monkeypatch.setattr(touchegg, "SYSTEM_AUTOSTART", sysfile)
    monkeypatch.setattr(touchegg, "USER_AUTOSTART", user)
    monkeypatch.setattr(touchegg, "client_running", lambda: False)
    touchegg.disable_client()
    assert "Hidden=true" in user.read_text() and touchegg.autostart_disabled()
    monkeypatch.setattr(touchegg, "client_running", lambda: True)   # do not spawn
    touchegg.enable_client()
    assert not user.exists()


# -- stats ---------------------------------------------------------------------------
def test_report_rate_and_noise():
    h, s = Hand(hz=125), stats.TouchStats()
    for f in h.put((50, 30)) + h.hold(0.5) + h.lift():
        s.feed(f, f.time + 0.0004)
    out = s.summary()
    assert out.rate_hz == pytest.approx(125, rel=0.01)
    assert out.read_latency_ms == pytest.approx(0.4, abs=0.01)
    assert out.noise_mm == pytest.approx(0.0, abs=1e-6)
    assert out.wake_delay_ms == pytest.approx(0.0, abs=0.01)


def test_wake_delay_detects_bunched_first_reports():
    h, s = Hand(hz=125), stats.TouchStats()
    frames = h.put((50, 30)) + h.move(5, 0, 0.12) + h.lift()
    # First 3 reports reach the kernel late (controller waking up) and in a burst.
    delay = 0.012
    for i, f in enumerate(frames):
        if i < 3:
            f.time = frames[3].time - 0.0005 * (3 - i)
        s.feed(f)
    assert s.summary().wake_delay_ms == pytest.approx(delay * 1000 + 0, abs=12)
    assert s.summary().wake_delay_ms > 10


# -- device --------------------------------------------------------------------------
def test_bits_decoding():
    assert device._bits("e520 10000 0 0 0 0") == {
        272, 325, 328, 330, 333, 334, 335}
    assert device._bits("3") == {0, 1}
    assert device._bits("0") == set()


def _fake_sysfs(tmp_path):
    ev = tmp_path / "class" / "event5"
    dev = tmp_path / "devices" / "i2c-ELAN0303:00" / "input9"
    (dev / "capabilities").mkdir(parents=True)
    (dev / "id").mkdir()
    ev.mkdir(parents=True)
    (ev / "device").symlink_to(dev)
    (ev / "dev").write_text("13:69\n")
    (dev / "name").write_text("ELAN0303:00 04F3:31FF Touchpad\n")
    (dev / "phys").write_text("i2c-ELAN0303:00\n")
    (dev / "properties").write_text("5\n")
    (dev / "capabilities" / "abs").write_text("2e0800000000003\n")
    (dev / "capabilities" / "key").write_text("e520 10000 0 0 0 0\n")
    (dev / "capabilities" / "msc").write_text("20\n")
    for k, v in {"vendor": "04f3", "product": "31ff", "bustype": "0018"}.items():
        (dev / "id" / k).write_text(v + "\n")
    udev = tmp_path / "udev"
    udev.mkdir()
    (udev / "c13:69").write_text("E:ID_INPUT=1\nE:ID_INPUT_TOUCHPAD=1\n"
                                 "E:ID_INPUT_WIDTH_MM=118\nE:ID_INPUT_HEIGHT_MM=72\n")
    return tmp_path / "class", udev


def test_list_touchpads_from_fake_sysfs(tmp_path):
    sys_input, udev = _fake_sysfs(tmp_path)
    pads = device.list_touchpads(sys_input, udev)
    assert len(pads) == 1
    p = pads[0]
    assert p.name.startswith("ELAN0303") and p.node == "/dev/input/event5"
    assert (p.vendor, p.product, p.bus_name) == (0x04F3, 0x31FF, "I2C")
    assert p.max_fingers == 5 and not p.has_pressure and p.has_hw_timestamp
    assert p.is_clickpad and p.is_internal and p.has_palm_tool
    assert (p.width_mm, p.height_mm) == (118.0, 72.0)


def test_non_touchpad_is_ignored(tmp_path):
    sys_input, udev = _fake_sysfs(tmp_path)
    (udev / "c13:69").write_text("E:ID_INPUT=1\nE:ID_INPUT_MOUSE=1\n")
    assert device.list_touchpads(sys_input, udev) == []
