import pytest

from padforge.gestures import GestureRecognizer

from conftest import Hand, feed_all

THREE = [(40, 40), (55, 38), (70, 40)]
FOUR = [(35, 40), (50, 38), (65, 38), (80, 40)]


def kinds(events):
    return [(e.kind, e.key or e.direction) for e in events]


@pytest.mark.parametrize("pts,n", [(THREE, 3), (FOUR, 4)])
@pytest.mark.parametrize("dx,dy,direction", [(0, -20, "up"), (0, 20, "down"),
                                             (-25, 0, "left"), (25, 0, "right")])
def test_swipes(pts, n, dx, dy, direction):
    h, r = Hand(), GestureRecognizer()
    ev = feed_all(r, h.put(*pts) + h.move(dx, dy, 0.15) + h.lift())
    assert kinds(ev) == [("swipe", f"swipe{n}_{direction}")]


def test_swipe_fires_during_motion_not_on_lift():
    h, r = Hand(), GestureRecognizer(swipe_threshold_mm=12)
    feed_all(r, h.put(*THREE))
    ev = feed_all(r, h.move(0, -15, 0.1))
    assert ev and ev[0].kind == "swipe"
    assert feed_all(r, h.move(0, -20, 0.1) + h.lift()) == [], "fires once per touch sequence"


def test_below_threshold_is_nothing():
    h, r = Hand(), GestureRecognizer(swipe_threshold_mm=12)
    assert feed_all(r, h.put(*THREE) + h.move(0, -8, 0.1) + h.lift()) == []


def test_diagonal_picks_dominant_axis():
    h, r = Hand(), GestureRecognizer()
    ev = feed_all(r, h.put(*THREE) + h.move(-20, 9, 0.15) + h.lift())
    assert kinds(ev) == [("swipe", "swipe3_left")]


@pytest.mark.parametrize("factor,direction", [(0.5, "in"), (1.8, "out")])
@pytest.mark.parametrize("pts,n", [(THREE, 3), (FOUR, 4)])
def test_pinch(pts, n, factor, direction):
    h, r = Hand(), GestureRecognizer()
    ev = feed_all(r, h.put(*pts) + h.spread(factor, 0.2) + h.lift())
    assert kinds(ev) == [("pinch", f"pinch{n}_{direction}")]


def test_two_fingers_are_left_to_libinput():
    h, r = Hand(), GestureRecognizer()
    assert feed_all(r, h.put((40, 40), (55, 40)) + h.move(0, 30, 0.2) + h.lift()) == []


def test_three_finger_tap_is_not_a_gesture():
    h, r = Hand(), GestureRecognizer()
    ev = feed_all(r, h.put(*THREE) + h.hold(0.05))
    assert ev == [] and not r.active
    ev = feed_all(r, h.lift())
    assert ev == [] and not r.active


def test_staggered_landing_counts_all_fingers():
    h, r = Hand(), GestureRecognizer()
    frames = h.put((40, 40)) + h.put((55, 38)) + h.put((70, 40)) + h.put((85, 40))
    ev = feed_all(r, frames + h.move(25, 0, 0.15) + h.lift())
    assert kinds(ev) == [("swipe", "swipe4_right")]


def test_quick_four_to_three_is_three_finger_swipe():
    h, r = Hand(), GestureRecognizer()
    frames = h.put(*FOUR) + h.lift([3]) + h.move(0, 20, 0.15) + h.lift()
    assert kinds(feed_all(r, frames)) == [("swipe", "swipe3_down")]


def test_physical_click_blocks_gesture():
    h, r = Hand(), GestureRecognizer()
    feed_all(r, h.put(*THREE))
    h.button = True
    ev = feed_all(r, h.move(0, -20, 0.15))
    h.button = False
    assert ev == [] and feed_all(r, h.lift()) == []


def test_continuous_swipe_steps_both_ways():
    h = Hand()
    r = GestureRecognizer(step_mm=10, continuous_keys={"swipe3_left", "swipe3_right"})
    ev = feed_all(r, h.put(*THREE) + h.move(15, 0, 0.1))
    assert kinds(ev)[0] == ("begin", "swipe3_right")
    # Steps count from where the swipe was recognised (~12.5 mm), not from touch down.
    ev = feed_all(r, h.move(25, 0, 0.2))          # at 40 mm: +27.5 from origin
    assert [e.step for e in ev if e.kind == "step"] == [1, 1]
    ev = feed_all(r, h.move(-45, 0, 0.3))         # at -5 mm: -37.5 from the moved origin
    assert [e.step for e in ev if e.kind == "step"] == [-1, -1, -1]
    ev = feed_all(r, h.lift())
    assert kinds(ev) == [("end", "swipe3_right")]


def test_continuous_pinch():
    h = Hand()
    r = GestureRecognizer(pinch_threshold=0.2, continuous_keys={"pinch4_out"})
    ev = feed_all(r, h.put(*FOUR) + h.spread(2.0, 0.3))
    assert ev[0].kind == "begin" and ev[0].key == "pinch4_out"
    assert any(e.kind == "step" and e.step == 1 for e in ev)
    assert kinds(feed_all(r, h.lift()))[0][0] == "end"


def test_three_finger_drag():
    h = Hand()
    r = GestureRecognizer(three_finger_drag=True)
    ev = feed_all(r, h.put(*THREE) + h.move(10, 5, 0.1))
    assert ev[0].kind == "drag_begin"
    moves = [e for e in ev if e.kind == "drag_move"]
    assert sum(e.dx for e in moves) == pytest.approx(10, abs=0.01)
    assert sum(e.dy for e in moves) == pytest.approx(5, abs=0.01)
    ev = feed_all(r, h.lift([0]) + h.move(4, 0, 0.05, slots=[1, 2]))
    assert sum(e.dx for e in ev if e.kind == "drag_move") == pytest.approx(4, abs=0.1)
    assert kinds(feed_all(r, h.lift())) == [("drag_end", "")]


def test_three_finger_drag_still_allows_four_finger_swipe():
    h = Hand()
    r = GestureRecognizer(three_finger_drag=True)
    ev = feed_all(r, h.put(*FOUR) + h.move(0, -20, 0.15) + h.lift())
    assert kinds(ev) == [("swipe", "swipe4_up")]


def test_two_finger_pinch_zoom_optional():
    h = Hand()
    r = GestureRecognizer(pinch_zoom_2f=True, pinch_threshold=0.2)
    ev = feed_all(r, h.put((40, 40), (60, 40)) + h.spread(2.0, 0.3))
    zooms = [e for e in ev if e.kind == "zoom"]
    assert zooms and all(e.step == 1 for e in zooms)
    feed_all(r, h.lift())
    # A plain 2-finger scroll is not a zoom.
    ev = feed_all(r, h.put((40, 40), (60, 40)) + h.move(0, 25, 0.2) + h.lift())
    assert ev == []


def test_disabled_recognizer():
    h, r = Hand(), GestureRecognizer(enabled=False)
    assert feed_all(r, h.put(*THREE) + h.move(0, -20, 0.15) + h.lift()) == []


def test_palms_do_not_count():
    h, r = Hand(), GestureRecognizer()
    frames = h.put((40, 40), (55, 40)) + h.put((100, 60), palm=True)
    assert feed_all(r, frames + h.move(0, -20, 0.15)) == []


def test_from_config():
    from padforge import config
    r = GestureRecognizer.from_config(config.defaults()["gestures"], {"swipe3_left"})
    assert r.swipe_threshold == 12.0 and "swipe3_left" in r.continuous_keys
