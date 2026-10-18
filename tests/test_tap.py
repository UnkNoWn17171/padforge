import pytest

from padforge import config
from padforge.tap import LEFT, MIDDLE, MULTI_CONFIRM_S, RIGHT, TapEngine

from conftest import Hand, feed_all


def kinds(events):
    return [(e.kind, e.button) for e in events]


def tap(h, eng, *pts, hold=0.05, **kw):
    return feed_all(eng, h.put(*pts) + h.hold(hold) + h.lift(), **kw)


def settle(eng, h):
    """Let the multi-finger confirmation window pass."""
    return eng.tick(h.t + MULTI_CONFIRM_S + 0.001)


def test_one_finger_tap_clicks_at_lift():
    h, eng = Hand(), TapEngine()
    assert feed_all(eng, h.put((50, 30)) + h.hold(0.05)) == []
    lift = h.lift()
    ev = feed_all(eng, lift)
    assert kinds(ev) == [("click", LEFT)]
    assert ev[0].time == lift[0].time, "no delay: the click happens on the lift frame"


@pytest.mark.parametrize("pts,button", [
    ([(50, 30), (65, 30)], RIGHT), ([(40, 30), (55, 30), (70, 30)], MIDDLE)])
def test_multi_finger_taps_click_after_short_confirmation(pts, button):
    h, eng = Hand(), TapEngine()
    assert tap(h, eng, *pts) == []
    assert eng.deadline() == pytest.approx(h.t - h.dt + MULTI_CONFIRM_S, abs=0.01)
    assert kinds(settle(eng, h)) == [("click", button)]


def test_bounce_before_swipe_is_not_a_middle_click():
    """Recorded: 3 fingers touch ~110 ms, lift for 29-68 ms, then swipe."""
    h, eng = Hand(), TapEngine()
    three = [(40, 30), (55, 30), (70, 30)]
    assert tap(h, eng, *three, hold=0.11) == []
    h.idle(0.04)
    ev = feed_all(eng, h.put(*three))
    ev += feed_all(eng, h.move(0, -15, 0.15), gesture_active=True)
    ev += feed_all(eng, h.lift(), gesture_active=True)
    ev += eng.tick(h.t + 1)
    assert ev == []


def test_quick_double_three_finger_tap_is_two_clicks():
    """Recorded: intentional 3-finger double taps 59-66 ms apart."""
    h, eng = Hand(), TapEngine()
    three = [(40, 30), (55, 30), (70, 30)]
    first = tap(h, eng, *three, hold=0.1)
    h.idle(0.06)
    second = tap(h, eng, *three, hold=0.1)
    assert first == []
    assert kinds(second) == [("click", MIDDLE)]
    assert kinds(settle(eng, h)) == [("click", MIDDLE)]


def test_multi_tap_then_pointer_motion_still_clicks():
    h, eng = Hand(), TapEngine()
    tap(h, eng, (50, 30), (65, 30))
    h.idle(0.03)
    ev = feed_all(eng, h.put((50, 30)) + h.move(20, 0, 0.4))
    assert kinds(ev) == [("click", RIGHT)], "released once the next touch is clearly no gesture"


def test_multi_finger_tap_allows_more_roll():
    h, eng = Hand(), TapEngine(max_move_mm=1.5)
    ev = feed_all(eng, h.put((40, 30), (55, 30), (70, 30)) + h.move(2.0, 0, 0.05) + h.lift())
    ev += settle(eng, h)
    assert kinds(ev) == [("click", MIDDLE)]
    ev = feed_all(eng, h.put((50, 30)) + h.move(2.0, 0, 0.05) + h.lift())
    assert ev == [], "one finger keeps the strict limit"


def test_lmr_mapping_and_left_handed():
    h, eng = Hand(), TapEngine(button_map="lmr")
    tap(h, eng, (50, 30), (65, 30))
    assert kinds(settle(eng, h)) == [("click", MIDDLE)]
    h, eng = Hand(), TapEngine(left_handed=True)
    assert kinds(tap(h, eng, (50, 30))) == [("click", RIGHT)]


def test_long_touch_is_not_a_tap():
    h, eng = Hand(), TapEngine(max_duration=0.18)
    assert tap(h, eng, (50, 30), hold=0.3) == []


def test_moving_touch_is_not_a_tap():
    h, eng = Hand(), TapEngine(max_move_mm=1.5)
    ev = feed_all(eng, h.put((50, 30)) + h.move(3, 0, 0.05) + h.lift())
    assert ev == []


def test_small_jitter_is_still_a_tap():
    h, eng = Hand(), TapEngine(max_move_mm=1.5)
    ev = feed_all(eng, h.put((50, 30)) + h.move(0.6, 0.4, 0.05) + h.lift())
    assert kinds(ev) == [("click", LEFT)]


def test_typing_guard():
    h, eng = Hand(), TapEngine(typing_guard=0.25)
    assert tap(h, eng, (50, 30), last_key_time=h.t - 0.1) == []
    h.idle(1.0)
    assert kinds(tap(h, eng, (50, 30), last_key_time=h.t - 0.5)) == [("click", LEFT)]


def test_tap_and_drag():
    h, eng = Hand(), TapEngine(drag=True, drag_window=0.2)
    assert kinds(tap(h, eng, (50, 30))) == [("click", LEFT)]
    h.idle(0.1)
    ev = feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.2))
    assert kinds(ev) == [("press", LEFT)]
    assert kinds(feed_all(eng, h.lift())) == [("release", LEFT)]


def test_drag_press_waits_for_a_possible_second_finger():
    h, eng = Hand(), TapEngine(drag=True)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    # second touch lands as two fingers: that is a 2-finger gesture, no drag
    ev = feed_all(eng, h.put((50, 30), (65, 30)) + h.move(0, 10, 0.2) + h.lift())
    assert ("press", LEFT) not in kinds(ev)


def test_drag_press_after_hold_via_tick():
    h, eng = Hand(), TapEngine(drag=True)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    feed_all(eng, h.put((50, 30)))
    deadline = eng.deadline()
    assert deadline is not None
    assert kinds(eng.tick(deadline + 0.001)) == [("press", LEFT)]


def test_double_tap_is_two_clicks():
    h, eng = Hand(), TapEngine(drag=True)
    first = tap(h, eng, (50, 30), hold=0.03)
    h.idle(0.08)
    second = feed_all(eng, h.put((50, 30)) + h.lift())
    assert kinds(first) == [("click", LEFT)]
    assert kinds(second) == [("press", LEFT), ("release", LEFT)]


def test_touch_after_window_is_not_a_drag():
    h, eng = Hand(), TapEngine(drag=True, drag_window=0.2)
    tap(h, eng, (50, 30))
    h.idle(0.3)
    eng.tick(h.t)
    ev = feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.2) + h.lift())
    assert ev == []


def test_drag_disabled():
    h, eng = Hand(), TapEngine(drag=False)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    assert feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.2) + h.lift()) == []


def test_drag_lock_continues_and_tap_releases():
    h, eng = Hand(), TapEngine(drag=True, drag_lock=True, drag_lock_time=0.4)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    ev = feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.2) + h.lift())
    assert kinds(ev) == [("press", LEFT)], "lifting during a locked drag keeps the button down"
    h.idle(0.1)
    ev = feed_all(eng, h.put((60, 30)) + h.move(10, 0, 0.2) + h.lift())
    assert ev == [], "re-touch continues the drag"
    h.idle(0.1)
    ev = tap(h, eng, (70, 30), hold=0.03)
    assert kinds(ev) == [("release", LEFT)]


def test_drag_lock_times_out():
    h, eng = Hand(), TapEngine(drag=True, drag_lock=True, drag_lock_time=0.4)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.2) + h.lift())
    assert kinds(eng.tick(eng.deadline() + 0.01)) == [("release", LEFT)]


def test_four_finger_tap_action():
    h, eng = Hand(), TapEngine(fingers={4})
    ev = tap(h, eng, (30, 30), (45, 30), (60, 30), (75, 30)) + settle(eng, h)
    assert [e.kind for e in ev] == ["action"]
    # with fingers={4}, normal taps are left to libinput
    assert tap(h, eng, (50, 30)) == []


def test_not_a_tap_during_gesture_button_palm_or_coast():
    h, eng = Hand(), TapEngine()
    assert tap(h, eng, (40, 30), (55, 30), (70, 30), gesture_active=True) == []
    h.button = True
    assert tap(h, eng, (50, 30)) == []
    h.button = False
    assert feed_all(eng, h.put((50, 30), palm=True) + h.lift()) == []
    assert tap(h, eng, (50, 30), coasting=True) == []


def test_cancel_releases_held_button():
    h, eng = Hand(), TapEngine(drag=True)
    tap(h, eng, (50, 30))
    h.idle(0.05)
    feed_all(eng, h.put((50, 30)) + h.move(10, 0, 0.1))
    assert kinds(eng.cancel(h.t)) == [("release", LEFT)]


def test_from_config():
    c = config.defaults()
    eng = TapEngine.from_config(c["tap"], False, padforge_engine=True)
    assert eng.fingers == {1, 2, 3, 4} and eng.drag
    eng = TapEngine.from_config(c["tap"], False, padforge_engine=False)
    assert eng.fingers == {4} and not eng.drag


def test_resting_thumb_does_not_turn_a_tap_into_a_right_click():
    """Thumb parked in the corner the whole time; the index finger taps twice."""
    h, eng = Hand(), TapEngine(drag=False)
    ev = feed_all(eng, h.put((3, 66), edge=True) + h.hold(0.5))
    for _ in range(2):
        ev += feed_all(eng, h.put((60, 30)) + h.hold(0.05) + h.lift([1]))
        h.idle(0.3)
    assert kinds(ev) == [("click", LEFT), ("click", LEFT)]


def test_thumb_tapping_alone_in_the_corner_clicks():
    h, eng = Hand(), TapEngine()
    ev = feed_all(eng, h.put((3, 66), edge=True) + h.hold(0.05) + h.lift())
    assert kinds(ev) == [("click", LEFT)]
