import pytest

from padforge import actions
from padforge.actions import Executor, parse_accelerator


@pytest.mark.parametrize("text,mods,key", [
    ("<Super>Page_Up", ["Super_L"], "Page_Up"),
    ("<Primary><Shift>t", ["Control_L", "Shift_L"], "t"),
    ("<Super>", ["Super_L"], None),
    ("Ctrl+Alt+T", ["Control_L", "Alt_L"], "t"),
    ("ctrl+shift+Tab", ["Control_L", "Shift_L"], "Tab"),
    ("Ctrl++", ["Control_L"], "plus"),
    ("Super", ["Super_L"], None),
    ("XF86AudioPlay", [], "XF86AudioPlay"),
    ("Alt+PgUp", ["Alt_L"], "Page_Up"),
    ("<Control><Control>w", ["Control_L"], "w"),
])
def test_parse_accelerator(text, mods, key):
    assert parse_accelerator(text) == (mods, key)


@pytest.mark.parametrize("bad", ["", "<Bogus>a", "<Super", "Foo+a"])
def test_parse_accelerator_errors(bad):
    with pytest.raises(ValueError):
        parse_accelerator(bad)


def test_catalogue_consistency():
    assert actions.CONTINUOUS == {"app_switch", "tab_switch", "zoom", "volume", "brightness"}
    for key in actions.KEY_ACTIONS:
        assert key in actions.ACTIONS
    for key in actions.GSETTINGS_KEYS:
        assert key in actions.ACTIONS


def test_describe_binding():
    assert actions.describe_binding({"action": "keys", "arg": "<Super>d"}) == "Klavye kısayolu: <Super>d"
    assert actions.describe_binding({"action": "overview"}) == "Etkinlikler (genel bakış)"


class FakeRoot:
    pass


class FakeScreen:
    root = FakeRoot()


class FakeDisplay:
    def screen(self):
        return FakeScreen()


def make_exec():
    return Executor(FakeDisplay(), dry_run=True,
                    shortcut_lookup=lambda a: {"app_switch": "<Super>Tab",
                                               "overview": "<Super>"}.get(a))


def test_dry_run_one_shot_actions():
    ex = make_exec()
    ex.run("workspace_next")
    ex.run("show_desktop")
    ex.run("overview")
    ex.run("keys", "<Control>t")
    ex.run("back")
    assert ex.log == ["çalışma alanı +1", "masaüstünü göster", "tuş: <Super>",
                      "tuş: <Control>t", "tık 8"]


def test_dry_run_continuous_app_switch():
    ex = make_exec()
    ex.begin("app_switch", None, +1)
    ex.step(+1)
    ex.step(-1)
    ex.end()
    assert ex.log == ["uygulama değiştirici açıldı (<Super>Tab)", "uygulama ileri",
                      "uygulama ileri", "uygulama geri"]


def test_continuous_action_bound_to_one_shot_runs_one_step():
    ex = make_exec()
    ex.run("volume")
    assert ex.log == ["tuş: XF86AudioRaiseVolume"]


def test_unknown_action_is_logged_not_raised():
    ex = make_exec()
    ex.run("no_such_action")
    assert ex.log and ex.log[-1].startswith("HATA")


def test_none_does_nothing():
    ex = make_exec()
    ex.run("none")
    assert ex.log == []
