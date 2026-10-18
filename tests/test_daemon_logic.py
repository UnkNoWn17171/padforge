"""Daemon bookkeeping that does not need an X server."""
from padforge import daemon


def bare():
    d = daemon.Daemon.__new__(daemon.Daemon)
    d.errors = []
    d.events = []
    d._timers = {}
    d._reapply_times = []
    d.scheduled = 0

    def fake_timer(name, ms, fn):
        d._timers[name] = 1
        d.scheduled += 1
    d._timer = fake_timer
    return d


def test_burst_of_property_events_is_one_reapply():
    """A device re-add makes mutter write ~12 properties: that is not a fight."""
    d = bare()
    for _ in range(14):
        d._schedule_reapply("x")
    assert d.scheduled == 1
    assert not any("sürekli" in e for e in d.errors)


def test_real_fight_is_stopped():
    d = bare()
    for _ in range(daemon.MAX_REAPPLY_PER_10S + 3):
        d._timers.clear()               # each apply ran, then someone changed it again
        d._schedule_reapply("x")
    assert d.scheduled == daemon.MAX_REAPPLY_PER_10S
    assert sum("sürekli" in e for e in d.errors) == 1


def test_errors_are_deduplicated_and_clearable():
    d = bare()
    for _ in range(5):
        d.error("/dev/input/event5 okunamıyor — x")
    assert len(d.errors) == 1
    d.error("Touchégg çalışıyor: y")
    d.clear_errors("event5 okunamıyor")
    assert len(d.errors) == 1 and "Touchégg" in d.errors[0]


def test_effective_config_stands_down_for_touchegg():
    from padforge import config
    d = bare()
    d.cfg = config.defaults()
    d.touchegg = False
    assert d.effective_config() is d.cfg
    d.touchegg = True
    eff = d.effective_config()
    assert eff["gestures"]["enabled"] is False and eff["tap"]["engine"] == "libinput"
    assert eff["scroll"]["kinetic"]["enabled"] is True, "kinetic scrolling does not clash"
    assert d.cfg["gestures"]["enabled"] is True, "user config is untouched"
