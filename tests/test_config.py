import json

from padforge import config


def test_defaults_are_valid_and_complete():
    d = config.defaults()
    assert d == config.validate(d)
    assert set(d["gestures"]["bindings"]) == set(config.GESTURE_KEYS)


def test_validate_clamps_and_drops_unknown():
    doc = {"pointer": {"speed": 7, "bogus": 1}, "tap": {"max_duration_ms": 5},
           "scroll": {"speed": "fast"}, "extra": True}
    out = config.validate(doc)
    assert out["pointer"]["speed"] == 1.0
    assert "bogus" not in out["pointer"] and "extra" not in out
    assert out["tap"]["max_duration_ms"] == 60
    assert out["scroll"]["speed"] == config.DEFAULTS["scroll"]["speed"]


def test_bool_is_not_accepted_as_number():
    out = config.validate({"pointer": {"speed": True}})
    assert out["pointer"]["speed"] == 0.0


def test_choices_are_enforced():
    out = config.validate({"tap": {"engine": "magic"}, "click": {"method": "x"}})
    assert out["tap"]["engine"] == "padforge"
    assert out["click"]["method"] == "buttonareas"


def test_bindings_normalized():
    out = config.validate({"gestures": {"bindings": {
        "swipe3_up": "workspace_next",
        "swipe3_down": {"action": "keys", "arg": "<Super>d"},
        "swipe4_up": 42,
        "unknown_key": {"action": "none"},
    }}})
    b = out["gestures"]["bindings"]
    assert b["swipe3_up"] == {"action": "workspace_next"}
    assert b["swipe3_down"] == {"action": "keys", "arg": "<Super>d"}
    assert b["swipe4_up"] == {"action": "none"}
    assert "unknown_key" not in b


def test_custom_curve_points_sorted_and_deduped():
    out = config.validate({"pointer": {"curve": {"preset": "custom",
                                                 "points": [[100, 20], [0, 5], [100, 25], [9e9, 1], "x"]}}})
    assert out["pointer"]["curve"]["points"] == [[0.0, 5.0], [100.0, 25.0]]
    assert out["pointer"]["curve"]["preset"] == "custom"


def test_custom_curve_needs_two_points():
    out = config.validate({"pointer": {"curve": {"preset": "custom", "points": [[0, 5]]}}})
    assert out["pointer"]["curve"]["preset"] == "windows"


def test_profiles_apply():
    for name in config.PROFILES:
        out = config.apply_profile(config.defaults(), name)
        assert out == config.validate(out)
    mac = config.apply_profile(config.defaults(), "macos")
    assert mac["gestures"]["three_finger_drag"] is True
    lin = config.apply_profile(config.defaults(), "linux")
    assert lin["tap"]["engine"] == "libinput" and lin["pointer"]["profile"] == "adaptive"


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "c.json"
    d = config.defaults()
    d["scroll"]["speed"] = 1.7
    config.save(d, p)
    assert config.load(p)["scroll"]["speed"] == 1.7
    assert json.loads(p.read_text())["version"] == config.CONFIG_VERSION


def test_broken_file_falls_back_and_is_kept(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert config.load(p) == config.defaults()
    assert (tmp_path / "c.json.broken").exists()


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.json") == config.defaults()


def test_deep_merge_does_not_mutate():
    a = {"x": {"y": 1, "z": 2}}
    b = {"x": {"y": 3}}
    out = config.deep_merge(a, b)
    assert out == {"x": {"y": 3, "z": 2}} and a == {"x": {"y": 1, "z": 2}}
