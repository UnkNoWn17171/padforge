import pytest

from padforge import mt

AX = mt.Axis(0, 3100, 31.0)
AY = mt.Axis(0, 2000, 31.0)


def run(parser, events, t=1.0):
    frames = []
    for e in events:
        f = parser.feed(*e, t)
        if f is not None:
            frames.append(f)
        t += 0.001
    return frames


def ev_abs(code, v):
    return (mt.EV_ABS, code, v)


def ev_key(code, v):
    return (mt.EV_KEY, code, v)


SYN = (mt.EV_SYN, mt.SYN_REPORT, 0)


def test_single_touch_lifecycle_in_mm():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 7),
        ev_abs(mt.ABS_MT_POSITION_X, 310), ev_abs(mt.ABS_MT_POSITION_Y, 620),
        ev_key(mt.BTN_TOUCH, 1), ev_key(mt.BTN_TOOL_FINGER, 1),
        (mt.EV_MSC, mt.MSC_TIMESTAMP, 8000), SYN,
        ev_abs(mt.ABS_MT_POSITION_X, 341), SYN,
        ev_abs(mt.ABS_MT_TRACKING_ID, -1), ev_key(mt.BTN_TOUCH, 0), ev_key(mt.BTN_TOOL_FINGER, 0), SYN,
    ])
    assert len(frames) == 3
    f0, f1, f2 = frames
    assert f0.began == (0,) and f0.count == 1 and f0.hw_time == 8000
    t = f0.touches[0]
    assert (t.x, t.y) == (pytest.approx(10.0), pytest.approx(20.0))
    assert f1.touches[0].x == pytest.approx(11.0) and f1.touches[0].moved() == pytest.approx(1.0)
    assert f2.ended == (0,) and f2.count == 0 and f2.ended_touches[0].tracking_id == 7


def test_two_slots_and_tool_count():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1),
        ev_abs(mt.ABS_MT_POSITION_X, 100), ev_abs(mt.ABS_MT_POSITION_Y, 100),
        ev_abs(mt.ABS_MT_SLOT, 1), ev_abs(mt.ABS_MT_TRACKING_ID, 2),
        ev_abs(mt.ABS_MT_POSITION_X, 600), ev_abs(mt.ABS_MT_POSITION_Y, 100),
        ev_key(mt.BTN_TOOL_DOUBLETAP, 1), SYN,
    ])
    assert frames[-1].count == 2 and set(frames[-1].began) == {0, 1}


def test_tool_count_beyond_slots():
    """Devices with 2 slots report a third finger only via BTN_TOOL_TRIPLETAP."""
    p = mt.MTParser(AX, AY, 2)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1),
        ev_abs(mt.ABS_MT_SLOT, 1), ev_abs(mt.ABS_MT_TRACKING_ID, 2),
        ev_key(mt.BTN_TOOL_TRIPLETAP, 1), SYN,
    ])
    assert frames[-1].count == 3


def test_palm_is_excluded_and_sticky():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1),
        ev_abs(mt.ABS_MT_TOOL_TYPE, mt.MT_TOOL_PALM), ev_key(mt.BTN_TOOL_FINGER, 1), SYN,
        ev_abs(mt.ABS_MT_TOOL_TYPE, mt.MT_TOOL_FINGER), SYN,
    ])
    assert frames[0].count == 0 and frames[0].touches[0].palm
    assert frames[1].touches[0].palm, "a palm stays a palm for its whole life"


def test_tracking_id_change_without_lift_restarts_touch():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1), SYN,
        ev_abs(mt.ABS_MT_TRACKING_ID, 2), SYN,
    ])
    assert frames[1].ended == (0,) and frames[1].began == (0,)
    assert frames[1].touches[0].tracking_id == 2


def test_syn_dropped_skips_until_report():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1), SYN,
        (mt.EV_SYN, mt.SYN_DROPPED, 0),
        ev_abs(mt.ABS_MT_POSITION_X, 3000),       # garbage, must be ignored
        SYN,
    ])
    assert p.dropped_count == 1
    assert frames[-1].resynced and frames[-1].touches[0].x == pytest.approx(0.0)


def test_resync_applies_slot_state():
    p = mt.MTParser(AX, AY, 3)
    f = p.resync({mt.ABS_MT_TRACKING_ID: [5, -1, 9], mt.ABS_MT_POSITION_X: [310, 0, 620]},
                 {mt.BTN_TOOL_DOUBLETAP}, 2.0)
    assert set(f.touches) == {0, 2} and f.touches[2].x == pytest.approx(20.0) and f.count == 2


def test_button_state():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [ev_key(mt.BTN_LEFT, 1), SYN, ev_key(mt.BTN_LEFT, 0), SYN])
    assert frames[0].button and not frames[1].button


def test_frames_are_snapshots():
    p = mt.MTParser(AX, AY, 5)
    frames = run(p, [
        ev_abs(mt.ABS_MT_SLOT, 0), ev_abs(mt.ABS_MT_TRACKING_ID, 1),
        ev_abs(mt.ABS_MT_POSITION_X, 310), SYN,
        ev_abs(mt.ABS_MT_POSITION_X, 620), SYN,
    ])
    assert frames[0].touches[0].x == pytest.approx(10.0)
    assert frames[1].touches[0].x == pytest.approx(20.0)
