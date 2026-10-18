from padforge.boost import APIS, Boost


class FakePPD:
    def __init__(self, fail_names=()):
        self.calls = []
        self.fail = set(fail_names)
        self.next = 41

    def __call__(self, api, method, args):
        if api[0] in self.fail:
            raise RuntimeError("no such service")
        self.calls.append((api[0], method, args))
        if method == "HoldProfile":
            self.next += 1
            return self.next
        return None


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def test_hold_extends_and_releases_after_quiet_period():
    ppd, clock = FakePPD(), Clock()
    b = Boost(seconds=8, call=ppd, clock=clock)
    b.hold()
    clock.t += 5
    b.hold()                      # second gesture: no new hold, just extended
    clock.t += 7
    b.tick()
    assert b.active, "8 s after the *last* gesture, not the first"
    clock.t += 2
    b.tick()
    assert not b.active
    assert [c[1] for c in ppd.calls] == ["HoldProfile", "ReleaseProfile"]
    assert ppd.calls[0][2][0] == "performance" and ppd.calls[1][2] == (42,)


def test_falls_back_to_old_bus_name():
    ppd = FakePPD(fail_names={APIS[0][0]})
    b = Boost(call=ppd, clock=Clock())
    b.hold()
    assert b.active and ppd.calls[0][0] == APIS[1][0]


def test_without_power_profiles_daemon_it_quietly_turns_off():
    ppd = FakePPD(fail_names={a[0] for a in APIS})
    b = Boost(call=ppd, clock=Clock())
    b.hold()
    b.hold()
    assert not b.active and not b.available


def test_disabled_never_holds():
    ppd = FakePPD()
    b = Boost(call=ppd, clock=Clock())
    b.enabled = False
    b.hold()
    assert ppd.calls == []


def test_system_bus_connection_is_kept_alive(monkeypatch):
    """A dropped connection made ppd cancel the hold at once (2026-10-18)."""
    from gi.repository import Gio
    from padforge import boost
    made = []
    monkeypatch.setattr(boost, "_SYSTEM_BUS", None)
    monkeypatch.setattr(Gio, "bus_get_sync", lambda *a: made.append(object()) or made[-1])
    first = boost.system_bus()
    assert boost.system_bus() is first and len(made) == 1
    assert boost._SYSTEM_BUS is first, "held at module level, not only in a local"
