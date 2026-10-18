import struct

import pytest

from padforge import config
from padforge import xinput as xi


def plan_dict(cfg, **caps):
    return dict(xi.plan_properties(cfg, xi.DeviceCaps(**caps)))


def test_custom_profile_with_resolution():
    p = plan_dict(config.defaults(), res=31, profiles=(1, 1, 1))
    assert p[xi.P_PROFILE] == [0, 0, 1]
    assert len(p[xi.P_MOTION_POINTS]) == 64 and p[xi.P_MOTION_STEP][0] > 0
    assert len(p[xi.P_SCROLL_POINTS]) == 32
    assert p[xi.P_SCROLL_DIST] == [15]
    assert xi.P_SPEED not in p


def test_custom_falls_back_without_resolution():
    p = plan_dict(config.defaults(), res=0, profiles=(1, 1, 1))
    assert p[xi.P_PROFILE] == [1, 0, 0]
    assert xi.P_MOTION_POINTS not in p and xi.P_SPEED in p


def test_custom_falls_back_without_driver_support():
    p = plan_dict(config.defaults(), res=31, profiles=(1, 1))
    assert p[xi.P_PROFILE][:2] == [1, 0]


def test_order_profile_then_step_then_points():
    names = [n for n, _ in xi.plan_properties(config.defaults(), xi.DeviceCaps(res=31, profiles=(1, 1, 1)))]
    assert names.index(xi.P_PROFILE) < names.index(xi.P_MOTION_STEP) < names.index(xi.P_MOTION_POINTS)


def test_flat_profile_speed_and_scroll_distance():
    cfg = config.validate({"pointer": {"profile": "flat", "speed": -0.5}, "scroll": {"speed": 2.0}})
    p = plan_dict(cfg, res=31, profiles=(1, 1, 1))
    assert p[xi.P_PROFILE] == [0, 1, 0]
    assert p[xi.P_SPEED] == [-0.5]
    assert p[xi.P_SCROLL_DIST] == [8]


def test_padforge_tap_engine_disables_libinput_tapping():
    p = plan_dict(config.defaults(), res=31)
    assert p[xi.P_TAP] == [0]
    cfg = config.validate({"tap": {"engine": "libinput"}})
    assert plan_dict(cfg, res=31)[xi.P_TAP] == [1]
    cfg = config.validate({"tap": {"engine": "libinput", "enabled": False}})
    assert plan_dict(cfg, res=31)[xi.P_TAP] == [0]


def test_button_map_and_methods():
    cfg = config.validate({"tap": {"button_map": "lmr"}, "click": {"method": "clickfinger"},
                           "scroll": {"method": "edge"}})
    p = plan_dict(cfg, res=31)
    assert p[xi.P_TAP_MAP] == [0, 1]
    assert p[xi.P_CLICK_METHOD] == [0, 1]
    assert p[xi.P_SCROLL_METHOD] == [0, 1, 0]


def test_unavailable_scroll_method_turns_scrolling_off():
    cfg = config.validate({"scroll": {"method": "edge"}})
    p = plan_dict(cfg, res=31, scroll_methods=(1, 0, 0))
    assert p[xi.P_SCROLL_METHOD] == [0, 0, 0]


@pytest.mark.parametrize("v", [0.0, 1.0, -0.38, 123.456])
def test_float_roundtrip(v):
    assert xi.card32_to_float(xi.float_to_card32(v)) == pytest.approx(v, rel=1e-6)


def test_same_compares_with_tolerance():
    assert xi._same([0.38167938], [0.38167938931297707])
    assert not xi._same([1, 0], [0, 1])
    assert not xi._same(None, [1])
    assert not xi._same([1], [1, 0])


def _fp(v):
    i = int(v // 1)
    return struct.pack("=iI", i, int(round((v - i) * 4294967296)) & 0xFFFFFFFF)


def test_parse_raw_event():
    # deviceid, time, detail, sourceid, valuators_len, flags, pad
    head = struct.pack("=HIIHHII", 8, 12345, 0, 8, 1, 0, 0)
    mask = struct.pack("=I", (1 << 0) | (1 << 3))
    body = _fp(2.5) + _fp(-120.0) + _fp(3.0) + _fp(-15.0)
    ev = xi.parse_raw_event(17, head + mask + body)
    assert ev.deviceid == 8 and ev.time == 12345
    assert ev.values == {0: pytest.approx(2.5), 3: pytest.approx(-120.0)}
    assert ev.raw == {0: pytest.approx(3.0), 3: pytest.approx(-15.0)}
