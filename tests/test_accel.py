import pytest

from padforge import accel


def test_monotone_curve_hits_control_points():
    pts = accel.PRESETS["windows"]
    c = accel.MonotoneCurve(pts)
    for v, g in pts:
        assert c(v) == pytest.approx(g)


def test_monotone_curve_never_overshoots():
    pts = [(0, 5), (10, 5), (20, 30), (30, 30), (100, 31)]
    c = accel.MonotoneCurve(pts)
    prev = -1
    for i in range(0, 1001):
        v = i / 10
        y = c(v)
        assert 5 - 1e-9 <= y <= 31 + 1e-9
        assert y >= prev - 1e-9
        prev = y


def test_curve_is_constant_outside_range():
    c = accel.MonotoneCurve([(10, 2), (20, 4)])
    assert c(-5) == 2 and c(500) == 4


def test_gain_function_scale():
    g1 = accel.gain_function({"preset": "windows", "scale": 1.0})
    g2 = accel.gain_function({"preset": "windows", "scale": 1.5})
    assert g2(100) == pytest.approx(1.5 * g1(100))


@pytest.mark.parametrize("preset", list(accel.PRESETS))
def test_libinput_points_valid(preset):
    step, pts = accel.libinput_motion_points({"preset": preset, "scale": 1.0}, res=31)
    assert len(pts) == accel.LIBINPUT_MAX_POINTS
    assert step > 0
    assert pts[0] == 0
    assert all(b > a for a, b in zip(pts, pts[1:]))
    assert max(pts) <= accel.LIBINPUT_MAX_VALUE


def test_libinput_points_encode_gain():
    """f(u)/u at a sample equals gain/res: px per device unit."""
    cfg = {"preset": "windows", "scale": 1.0}
    res = 31.0
    step, pts = accel.libinput_motion_points(cfg, res)
    g = accel.gain_function(cfg)
    for i in (5, 20, 40, 63):
        u = i * step
        v = u * 1000 / res
        assert pts[i] / u == pytest.approx(g(v) / res, rel=1e-3)


def test_resolution_required():
    with pytest.raises(ValueError):
        accel.libinput_motion_points({"preset": "windows"}, 0)


def test_scroll_points_match_libinput_default():
    step, pts = accel.libinput_scroll_points(1.0, 0.0, res=31)
    # linear: ratio constant = stock libinput scroll px per mm / res
    want = accel.LIBINPUT_SCROLL_PX_PER_MM / 31
    for i in (3, 10, 31):
        assert pts[i] / (i * step) == pytest.approx(want, rel=1e-3)


def test_scroll_acceleration_increases_fast_gain():
    g = accel.scroll_gain(1.0, 1.0)
    assert g(20) == pytest.approx(accel.LIBINPUT_SCROLL_PX_PER_MM)
    assert g(500) == pytest.approx(2 * accel.LIBINPUT_SCROLL_PX_PER_MM)


@pytest.mark.parametrize("speed,want", [(1.0, 15), (2.0, 8), (0.5, 30), (100, 1), (0.01, 100)])
def test_scroll_pixel_distance(speed, want):
    assert accel.scroll_pixel_distance(speed) == want


def test_custom_preset_uses_points():
    cfg = {"preset": "custom", "points": [[0, 3], [500, 9]], "scale": 1.0}
    assert accel.curve_points(cfg) == [(0.0, 3.0), (500.0, 9.0)]
    g = accel.gain_function(cfg)
    assert g(0) == pytest.approx(3) and g(500) == pytest.approx(9)


def test_describe():
    d = accel.describe({"preset": "windows", "scale": 1.0}, 118, 1920)
    assert d["slow_px_per_mm"] < d["medium_px_per_mm"] < d["fast_px_per_mm"]
    assert 0.3 < d["strokes_per_screen"] < 2
