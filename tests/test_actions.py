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
    assert actions.CONTINUOUS == {"window_cycle", "app_switch", "tab_switch", "zoom", "volume",
                                  "brightness", "workspace"}
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


# -- Super Scroll Switch window menu ----------------------------------------------------
class KeyRecorder(Executor):
    """Executor whose key presses are recorded instead of sent to X."""

    def __init__(self, steps_ok=True):
        self.calls = []
        super().__init__(FakeDisplay(), dry_run=False,
                         shortcut_lookup=lambda a: {"app_switch": "<Super>Tab"}.get(a),
                         window_step=self._step)
        self.steps_ok = steps_ok
        self.d.sync = lambda: None

    def _step(self, direction):
        self.calls.append(("step", direction))
        return self.steps_ok

    def _keycode(self, name):
        return {"Super_L": 133, "Tab": 23, "Shift_L": 50}[name], False

    def _key(self, code, down):
        self.calls.append(("key", code, down))


def test_window_cycle_holds_super_and_steps():
    ex = KeyRecorder()
    ex.begin("window_cycle", None, +1)
    ex.step(+1)
    ex.step(-1)
    ex.end()
    assert ex.calls == [("key", 133, True), ("step", 1), ("step", 1), ("step", -1),
                        ("key", 133, False)]


def test_window_cycle_falls_back_to_gnome_switcher_without_extension():
    ex = KeyRecorder(steps_ok=False)
    ex.begin("window_cycle", None, +1)
    ex.end()
    keys = [c for c in ex.calls if c[0] == "key"]
    assert keys[:2] == [("key", 133, True), ("key", 133, False)], "Super released first"
    assert ("key", 23, True) in keys, "then GNOME's Super+Tab switcher"
    assert keys[-1] == ("key", 133, False), "and nothing stays held"


def test_vertical_direction_for_switchers():
    from padforge.daemon import Daemon
    assert Daemon._inc_for("down", "window_cycle") == 1
    assert Daemon._inc_for("up", "window_cycle") == -1
    assert Daemon._inc_for("right", "window_cycle") == 1
    assert Daemon._inc_for("up", "volume") == 1, "up is still louder"


# -- media keys and the Super key --------------------------------------------------------
@pytest.mark.parametrize("name", sorted(
    {v for v in actions.KEY_ACTIONS.values() if v.startswith("XF86")}
    | {"XF86AudioRaiseVolume", "XF86AudioLowerVolume", "XF86MonBrightnessUp",
       "XF86MonBrightnessDown"}))
def test_media_key_names_resolve(name):
    """python-xlib spells them XF86_AudioPlay; the GNOME spelling failed as "unknown key"."""
    assert actions.keysym_for(name), name


def test_keysym_for_known_values():
    assert actions.keysym_for("XF86AudioPlay") == 0x1008FF14
    assert actions.keysym_for("XF86_AudioPlay") == 0x1008FF14
    assert actions.keysym_for("Super_L") == 0xFFEB
    assert actions.keysym_for("a") == ord("a")
    assert actions.keysym_for("XF86NoSuchKey") == 0


def test_super_key_taps_super():
    ex = KeyRecorder()
    ex.run("super_key")
    assert ex.calls == [("key", 133, True), ("key", 133, False)]


def test_super_key_is_a_one_shot_action():
    assert "super_key" in actions.ACTIONS and "super_key" not in actions.CONTINUOUS
    ex = make_exec()
    ex.run("super_key")
    assert ex.log == ["tuş: <Super>"]


def test_hold_key_and_release_keys():
    ex = KeyRecorder()
    ex.hold_key("Super_L", True)
    ex.hold_key("Super_L", False)
    ex.release_keys(["Shift_L"])
    assert ex.calls == [("key", 133, True), ("key", 133, False), ("key", 50, False)]


def test_continuous_workspace_steps():
    ex = make_exec()
    ex.begin("workspace", None, +1)
    ex.step(-1)
    ex.end()
    assert ex.log == ["çalışma alanı +1", "çalışma alanı -1"]
