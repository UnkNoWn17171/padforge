import math

import pytest

from padforge import kinetic

from conftest import Hand


def flings(tracker, frames):
    out = []
    for f in frames:
        r = tracker.feed(f)
        if r is not None:
            out.append(r)
    return out


def test_flick_produces_fling_with_velocity():
    h, t = Hand(), kinetic.ScrollTracker(min_speed_mm_s=50)
    frames = h.put((40, 30), (60, 30)) + h.move(0, 20, 0.2) + h.lift()
    out = flings(t, frames)
    assert len(out) == 1
    assert out[0].vy == pytest.approx(100, rel=0.05) and abs(out[0].vx) < 1


def test_slow_scroll_does_not_coast():
    h, t = Hand(), kinetic.ScrollTracker(min_speed_mm_s=50)
    assert flings(t, h.put((40, 30), (60, 30)) + h.move(0, 6, 0.3) + h.lift()) == []


def test_stop_before_lift_does_not_coast():
    h, t = Hand(), kinetic.ScrollTracker()
    frames = h.put((40, 30), (60, 30)) + h.move(0, 20, 0.15) + h.hold(0.15) + h.lift()
    assert flings(t, frames) == []


def test_pinch_does_not_coast():
    h, t = Hand(), kinetic.ScrollTracker()
    assert flings(t, h.put((40, 30), (60, 30)) + h.spread(1.8, 0.2) + h.lift()) == []


def test_staggered_lift_still_coasts():
    h, t = Hand(), kinetic.ScrollTracker()
    frames = h.put((40, 30), (60, 30)) + h.move(0, 20, 0.2) + h.lift([0]) + h.lift()
    assert len(flings(t, frames)) == 1


def test_remaining_finger_moving_cancels():
    h, t = Hand(), kinetic.ScrollTracker()
    frames = h.put((40, 30), (60, 30)) + h.move(0, 20, 0.2) + h.lift([0])
    frames += h.move(10, 0, 0.05, slots=[1]) + h.lift()
    assert flings(t, frames) == []


def test_single_finger_never_flings():
    h, t = Hand(), kinetic.ScrollTracker()
    assert flings(t, h.put((40, 30)) + h.move(0, 30, 0.2) + h.lift()) == []


def test_coaster_total_distance_is_exact():
    c = kinetic.Coaster(friction=3.0, max_duration=100, stop_speed=1e-6)
    c.start(0, 1200.0, now=0.0)
    total, t = 0, 0.0
    while c.active:
        t += 0.007
        total += c.step(t)[1]
    assert total == pytest.approx(1200.0 / 3.0, abs=2)


def test_coaster_decays_and_stops():
    c = kinetic.Coaster(friction=3.0, max_duration=10)
    c.start(0, 2400.0, now=0.0)
    assert c.velocity(1.0)[1] == pytest.approx(2400 * math.exp(-3))
    t = 0.0
    while c.active and t < 20:
        t += 0.01
        c.step(t)
    assert not c.active and t < 3.0


def test_coaster_negative_direction_and_stop():
    c = kinetic.Coaster(friction=2.0)
    c.start(-600.0, 0.0, now=0.0)
    dx, dy = c.step(0.05)
    assert dx < 0 and dy == 0
    c.stop()
    assert c.step(0.1) == (0, 0)


def test_coaster_ignores_tiny_velocity():
    c = kinetic.Coaster()
    c.start(0, 10.0, now=0.0)
    assert not c.active


def test_ratio_meter_uses_observed_units():
    h = Hand()
    m = kinetic.RatioMeter(fallback_units_per_mm=50.0)
    frames = h.put((40, 30), (60, 30)) + h.move(0, 10, 0.1)
    for f in frames:
        m.add_frame(f)
        m.add_units(f.time, 0.0, -83.0 * (10 / len(frames)))
    rx, ry = m.ratio()
    assert ry == pytest.approx(-83.0 * len(frames) / (len(frames) - 1), rel=0.1)
    assert rx == ry


def test_ratio_meter_fallback():
    m = kinetic.RatioMeter(fallback_units_per_mm=-93.0)
    assert m.ratio() == (-93.0, -93.0)
