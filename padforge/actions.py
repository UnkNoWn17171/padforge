"""Gesture actions: catalogue, keyboard shortcut parsing and the X11 executor.

Window-manager actions go through EWMH client messages where possible (works on
any EWMH window manager), GNOME shortcuts are read from gsettings so a rebound
shortcut keeps working, and everything else is injected with XTest.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("padforge.actions")


@dataclass(frozen=True)
class ActionInfo:
    label: str
    group: str
    continuous: bool = False
    needs_arg: bool = False


ACTIONS: dict[str, ActionInfo] = {
    "none": ActionInfo("Hiçbiri", "Genel"),
    "overview": ActionInfo("Etkinlikler (genel bakış)", "Masaüstü"),
    "super_key": ActionInfo("Super tuşu (bas-bırak)", "Masaüstü"),
    "app_grid": ActionInfo("Uygulama ızgarası", "Masaüstü"),
    "show_desktop": ActionInfo("Masaüstünü göster / geri getir", "Masaüstü"),
    "workspace_next": ActionInfo("Sonraki çalışma alanı", "Masaüstü"),
    "workspace_prev": ActionInfo("Önceki çalışma alanı", "Masaüstü"),
    "window_cycle": ActionInfo("Pencere değiştir (Super+tekerlek menüsü)", "Pencere",
                               continuous=True),
    "app_switch": ActionInfo("Uygulama değiştir (kaydırdıkça ilerler)", "Pencere", continuous=True),
    "window_switch": ActionInfo("Sonraki uygulama (tek adım)", "Pencere"),
    "minimize": ActionInfo("Pencereyi küçült", "Pencere"),
    "maximize": ActionInfo("Pencereyi büyüt / geri al", "Pencere"),
    "close_window": ActionInfo("Pencereyi kapat", "Pencere"),
    "back": ActionInfo("Geri (tarayıcı, dosyalar)", "Gezinme"),
    "forward": ActionInfo("İleri", "Gezinme"),
    "new_tab": ActionInfo("Yeni sekme (Ctrl+T)", "Gezinme"),
    "close_tab": ActionInfo("Sekmeyi kapat (Ctrl+W)", "Gezinme"),
    "reopen_tab": ActionInfo("Kapatılan sekmeyi aç (Ctrl+Shift+T)", "Gezinme"),
    "tab_switch": ActionInfo("Sekme değiştir (kaydırdıkça)", "Gezinme", continuous=True),
    "zoom": ActionInfo("Yakınlaştır / uzaklaştır (sürekli)", "Gezinme", continuous=True),
    "zoom_in": ActionInfo("Yakınlaştır", "Gezinme"),
    "zoom_out": ActionInfo("Uzaklaştır", "Gezinme"),
    "volume": ActionInfo("Ses düzeyi (sürekli)", "Medya", continuous=True),
    "volume_up": ActionInfo("Sesi aç", "Medya"),
    "volume_down": ActionInfo("Sesi kıs", "Medya"),
    "mute": ActionInfo("Sessiz", "Medya"),
    "play_pause": ActionInfo("Oynat / duraklat", "Medya"),
    "next_track": ActionInfo("Sonraki parça", "Medya"),
    "prev_track": ActionInfo("Önceki parça", "Medya"),
    "brightness": ActionInfo("Ekran parlaklığı (sürekli)", "Medya", continuous=True),
    "screenshot": ActionInfo("Ekran görüntüsü", "Sistem"),
    "lock_screen": ActionInfo("Ekranı kilitle", "Sistem"),
    "middle_click": ActionInfo("Orta tık", "Fare"),
    "right_click": ActionInfo("Sağ tık", "Fare"),
    "keys": ActionInfo("Klavye kısayolu…", "Özel", needs_arg=True),
    "command": ActionInfo("Komut çalıştır…", "Özel", needs_arg=True),
}

CONTINUOUS = {k for k, v in ACTIONS.items() if v.continuous}

# For these, moving the fingers *down* means "next" (like turning a mouse wheel down);
# for the others up means more/next (volume, brightness, zoom).
VERTICAL_DOWN_IS_NEXT = {"window_cycle", "app_switch", "tab_switch"}

# The user's own GNOME extension "Super Scroll Switch": Super held + Step(+1/-1)
# shows its in-place window switcher; releasing Super activates the chosen window.
SSS_DEST = "org.gnome.Shell"
SSS_PATH = "/org/gnome/Shell/Extensions/SuperScrollSwitch"
SSS_IFACE = "org.gnome.Shell.Extensions.SuperScrollSwitch"
_bus = None


def super_scroll_step(direction: int) -> bool:
    """One step of the Super Scroll Switch menu; False if the extension is missing."""
    global _bus
    try:
        from gi.repository import Gio, GLib
        if _bus is None:
            _bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        _bus.call_sync(SSS_DEST, SSS_PATH, SSS_IFACE, "Step", GLib.Variant("(i)", (direction,)),
                       None, Gio.DBusCallFlags.NO_AUTO_START, 500, None)
        return True
    except Exception:
        return False

# Fallback shortcuts when a gsettings key is missing or empty.
GSETTINGS_KEYS: dict[str, tuple[str, str, str]] = {
    "overview": ("org.gnome.shell.keybindings", "toggle-overview", "<Super>"),
    "app_grid": ("org.gnome.shell.keybindings", "toggle-application-view", "<Super>a"),
    "app_switch": ("org.gnome.desktop.wm.keybindings", "switch-applications", "<Alt>Tab"),
    "window_switch": ("org.gnome.desktop.wm.keybindings", "switch-applications", "<Alt>Tab"),
    "minimize": ("org.gnome.desktop.wm.keybindings", "minimize", "<Super>h"),
    "lock_screen": ("org.gnome.settings-daemon.plugins.media-keys", "screensaver", "<Super>l"),
    "screenshot": ("org.gnome.shell.keybindings", "show-screenshot-ui", "Print"),
}

KEY_ACTIONS = {
    "new_tab": "<Control>t",
    "close_tab": "<Control>w",
    "reopen_tab": "<Control><Shift>t",
    "volume_up": "XF86AudioRaiseVolume",
    "volume_down": "XF86AudioLowerVolume",
    "mute": "XF86AudioMute",
    "play_pause": "XF86AudioPlay",
    "next_track": "XF86AudioNext",
    "prev_track": "XF86AudioPrev",
}

MODIFIERS = {
    "shift": "Shift_L", "control": "Control_L", "ctrl": "Control_L", "primary": "Control_L",
    "alt": "Alt_L", "mod1": "Alt_L", "super": "Super_L", "mod4": "Super_L", "win": "Super_L",
    "meta": "Super_L", "hyper": "Hyper_L", "altgr": "ISO_Level3_Shift",
}

# Friendly key names accepted in "Ctrl+Alt+T" style input.
KEY_ALIASES = {
    "esc": "Escape", "enter": "Return", "del": "Delete", "pgup": "Page_Up", "pgdn": "Page_Down",
    "pageup": "Page_Up", "pagedown": "Page_Down", "space": "space", "tab": "Tab",
    "left": "Left", "right": "Right", "up": "Up", "down": "Down", "home": "Home", "end": "End",
    "backspace": "BackSpace", "print": "Print", "ins": "Insert", "insert": "Insert",
    "+": "plus", "-": "minus", "plus": "plus", "minus": "minus",
}


def keysym_for(name: str) -> int:
    """X keysym for a key name (0 if unknown).

    GNOME and xdotool write ``XF86AudioPlay``; python-xlib only knows
    ``XF86_AudioPlay``, so every media key failed as "unknown key" before.
    """
    from Xlib import XK
    XK.load_keysym_group("xf86")
    keysym = XK.string_to_keysym(name)
    if not keysym and name.startswith("XF86") and not name.startswith("XF86_"):
        keysym = XK.string_to_keysym("XF86_" + name[4:])
    if not keysym and len(name) == 1:
        keysym = ord(name)
    return keysym


def parse_accelerator(text: str) -> tuple[list[str], str | None]:
    """Parse "<Super><Shift>Page_Up" or "Ctrl+Alt+T" into (modifier keysyms, key keysym).

    ``key`` is None for a modifier-only shortcut such as "<Super>".
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("boş kısayol")
    mods: list[str] = []
    key: str | None
    if text.startswith("<"):
        rest = text
        while rest.startswith("<"):
            end = rest.find(">")
            if end < 0:
                raise ValueError(f"kapanmamış '<': {text}")
            name = rest[1:end].lower()
            if name not in MODIFIERS:
                raise ValueError(f"bilinmeyen değiştirici: {name}")
            mods.append(MODIFIERS[name])
            rest = rest[end + 1:]
        key = rest.strip() or None
    else:
        parts = [p for p in text.replace(" ", "").split("+")]
        # "Ctrl++" -> ["Ctrl", "", ""]: the key is "+"
        if len(parts) >= 3 and parts[-1] == "" and parts[-2] == "":
            parts = parts[:-2] + ["+"]
        *mod_names, key = parts
        for name in mod_names:
            low = name.lower()
            if low not in MODIFIERS:
                raise ValueError(f"bilinmeyen değiştirici: {name}")
            mods.append(MODIFIERS[low])
        if key and key.lower() in MODIFIERS and not mod_names:
            mods.append(MODIFIERS[key.lower()])
            key = None
    if key:
        key = KEY_ALIASES.get(key.lower(), key)
        if len(key) == 1:
            key = key.lower() if key.isalpha() else key
    dedup: list[str] = []
    for m in mods:
        if m not in dedup:
            dedup.append(m)
    return dedup, key


def gsettings_shortcut(schema: str, key: str) -> str | None:
    """First accelerator of a GNOME keybinding (None if unset or no GNOME)."""
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(schema, True) is None:
            return None
        sch = source.lookup(schema, True)
        if not sch.has_key(key):
            return None
        value = Gio.Settings.new(schema).get_value(key).unpack()
    except Exception:
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value or None


def shortcut_for(action: str) -> str | None:
    if action in GSETTINGS_KEYS:
        schema, key, fallback = GSETTINGS_KEYS[action]
        found = gsettings_shortcut(schema, key)
        if action == "overview" and not found:
            # GNOME default: tapping the overlay key opens the overview.
            overlay = None
            try:
                from gi.repository import Gio
                overlay = Gio.Settings.new("org.gnome.mutter").get_string("overlay-key")
            except Exception:
                pass
            return f"<{'Super' if not overlay or overlay.startswith('Super') else 'Alt'}>"
        return found or fallback
    return KEY_ACTIONS.get(action)


class Executor:
    """Runs actions against an X display (python-xlib Display)."""

    def __init__(self, display, pointer=None, dry_run: bool = False,
                 shortcut_lookup: Callable[[str], str | None] = shortcut_for,
                 window_step: Callable[[int], bool] = super_scroll_step):
        from Xlib import X, XK
        from Xlib.ext import xtest
        self.X, self.XK, self.xtest = X, XK, xtest
        XK.load_keysym_group("xf86")
        self.d = display
        self.root = display.screen().root
        self.pointer = pointer           # VirtualPointer or None
        self.dry_run = dry_run
        self.shortcut_lookup = shortcut_lookup
        self.window_step = window_step
        self.log: list[str] = []         # recent actions (dry-run and GUI)
        self._held: list[int] = []       # keycodes held by a continuous action
        self._cont: str | None = None
        self._spare_keycode: int | None = None

    # -- low level -------------------------------------------------------------
    def _record(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-50]
        log.debug(text)

    def _keycode(self, keysym_name: str) -> tuple[int, bool]:
        """Keycode for a keysym name, and whether Shift is needed for it."""
        keysym = keysym_for(keysym_name)
        if not keysym:
            raise ValueError(f"bilinmeyen tuş: {keysym_name}")
        best = None
        for code, index in self.d.keysym_to_keycodes(keysym):
            if index in (0, 1) and (best is None or index < best[1]):
                best = (code, index)
        if best is not None:
            return best[0], best[1] == 1
        return self._remap(keysym), False

    def _remap(self, keysym: int) -> int:
        """Bind an unmapped keysym to a spare keycode (what xdotool does)."""
        if self._spare_keycode is None:
            lo, hi = self.d.display.info.min_keycode, self.d.display.info.max_keycode
            mapping = self.d.get_keyboard_mapping(lo, hi - lo + 1)
            for i in range(len(mapping) - 1, -1, -1):
                if not any(mapping[i]):
                    self._spare_keycode = lo + i
                    break
            if self._spare_keycode is None:
                raise ValueError("boş tuş kodu yok")
        self.d.change_keyboard_mapping(self._spare_keycode, [(keysym,) * 2])
        self.d.sync()
        return self._spare_keycode

    def _key(self, code: int, down: bool) -> None:
        self.xtest.fake_input(self.d, self.X.KeyPress if down else self.X.KeyRelease, code)

    def send_shortcut(self, accel: str) -> None:
        mods, key = parse_accelerator(accel)
        self._record(f"tuş: {accel}")
        if self.dry_run:
            return
        mod_codes = [self._keycode(m)[0] for m in mods]
        key_code, shift = self._keycode(key) if key else (None, False)
        if shift and "Shift_L" not in mods:
            mod_codes.append(self._keycode("Shift_L")[0])
        for c in mod_codes:
            self._key(c, True)
        if key_code is not None:
            self._key(key_code, True)
            self._key(key_code, False)
        for c in reversed(mod_codes):
            self._key(c, False)
        self.d.sync()

    def button(self, button: int, down: bool) -> None:
        self._record(f"düğme {button} {'bas' if down else 'bırak'}")
        if self.dry_run:
            return
        self.xtest.fake_input(self.d, self.X.ButtonPress if down else self.X.ButtonRelease, button)
        self.d.sync()

    def click(self, button: int) -> None:
        self._record(f"tık {button}")
        if self.dry_run:
            return
        self.xtest.fake_input(self.d, self.X.ButtonPress, button)
        self.xtest.fake_input(self.d, self.X.ButtonRelease, button)
        self.d.sync()

    def _wheel(self, up: bool, ctrl: bool = False) -> None:
        if self.dry_run:
            self._record(f"tekerlek {'yukarı' if up else 'aşağı'}{' +Ctrl' if ctrl else ''}")
            return
        ctrl_code = self._keycode("Control_L")[0] if ctrl else None
        if ctrl_code:
            self._key(ctrl_code, True)
        self.click(4 if up else 5)
        if ctrl_code:
            self._key(ctrl_code, False)
        self.d.sync()

    # -- EWMH --------------------------------------------------------------------
    def _atom(self, name: str) -> int:
        return self.d.intern_atom(name)

    def _root_card(self, name: str) -> int | None:
        prop = self.root.get_full_property(self._atom(name), 0)
        return int(prop.value[0]) if prop and len(prop.value) else None

    def _client_message(self, window, msg_type: str, data: list[int]) -> None:
        from Xlib.protocol import event
        data = (list(data) + [0] * 5)[:5]
        ev = event.ClientMessage(window=window, client_type=self._atom(msg_type),
                                 data=(32, data))
        mask = self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask
        self.root.send_event(ev, event_mask=mask)
        self.d.sync()

    def _active_window(self):
        wid = self._root_card("_NET_ACTIVE_WINDOW")
        return self.d.create_resource_object("window", wid) if wid else None

    def workspace(self, delta: int) -> None:
        self._record(f"çalışma alanı {delta:+d}")
        if self.dry_run:
            return
        cur, num = self._root_card("_NET_CURRENT_DESKTOP"), self._root_card("_NET_NUMBER_OF_DESKTOPS")
        if cur is None or not num:
            return
        target = min(num - 1, max(0, cur + delta))
        if target != cur:
            self._client_message(self.root, "_NET_CURRENT_DESKTOP", [target, self.X.CurrentTime])

    def show_desktop(self) -> None:
        self._record("masaüstünü göster")
        if self.dry_run:
            return
        showing = self._root_card("_NET_SHOWING_DESKTOP") or 0
        self._client_message(self.root, "_NET_SHOWING_DESKTOP", [0 if showing else 1])

    def window_state(self, what: str) -> None:
        self._record(f"pencere: {what}")
        if self.dry_run:
            return
        win = self._active_window()
        if win is None:
            return
        if what == "close":
            self._client_message(win, "_NET_CLOSE_WINDOW", [self.X.CurrentTime, 2])
        elif what == "maximize":
            self._client_message(win, "_NET_WM_STATE", [
                2, self._atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                self._atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 2])
        elif what == "minimize":
            self._client_message(win, "WM_CHANGE_STATE", [3])   # IconicState

    # -- public ------------------------------------------------------------------
    def run(self, action: str, arg: str | None = None) -> None:
        """Run a one-shot action. Continuous actions run a single step."""
        try:
            self._run(action, arg)
        except Exception as exc:
            log.warning("eylem %s başarısız: %s", action, exc)
            self._record(f"HATA {action}: {exc}")

    def _run(self, action: str, arg: str | None) -> None:
        if action == "none":
            return
        if action in CONTINUOUS:
            self.begin(action, arg, +1)
            self.end()
            return
        if action == "workspace_next":
            self.workspace(+1)
        elif action == "workspace_prev":
            self.workspace(-1)
        elif action == "show_desktop":
            self.show_desktop()
        elif action == "super_key":
            # Exactly what tapping the key does (GNOME: the overlay key opens Activities).
            self.send_shortcut("<Super>")
        elif action == "maximize":
            self.window_state("maximize")
        elif action == "close_window":
            self.window_state("close")
        elif action == "minimize":
            accel = self.shortcut_lookup("minimize")
            if accel:
                self.send_shortcut(accel)
            else:
                self.window_state("minimize")
        elif action == "back":
            self.click(8)
        elif action == "forward":
            self.click(9)
        elif action == "zoom_in":
            self._wheel(True, ctrl=True)
        elif action == "zoom_out":
            self._wheel(False, ctrl=True)
        elif action == "middle_click":
            self.click(2)
        elif action == "right_click":
            self.click(3)
        elif action == "keys":
            if arg:
                self.send_shortcut(arg)
        elif action == "command":
            if arg:
                self._record(f"komut: {arg}")
                if not self.dry_run:
                    subprocess.Popen(["/bin/sh", "-c", arg], start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=_clean_env())
        else:
            accel = self.shortcut_lookup(action)
            if not accel:
                raise ValueError(f"bilinmeyen eylem: {action}")
            self.send_shortcut(accel)

    # Continuous actions: begin(+1/-1) counts as the first step.
    def begin(self, action: str, arg: str | None, inc: int) -> None:
        try:
            self.end()
            self._cont = action
            if action == "window_cycle":
                self._record("pencere menüsü açıldı (Super+tekerlek)")
                if not self.dry_run:
                    self._held = [self._keycode("Super_L")[0]]
                    self._key(self._held[0], True)
                    self.d.sync()
                    if not self.window_step(1 if inc > 0 else -1):
                        # Extension not installed: fall back to GNOME's own switcher.
                        self._key(self._held[0], False)
                        self.d.sync()
                        self._held, self._cont = [], None
                        self._record("Super Scroll Switch yok, GNOME değiştiricisi kullanılıyor")
                        return self.begin("app_switch", arg, inc)
                else:
                    self._record("pencere " + ("sonraki" if inc > 0 else "önceki"))
                return
            if action == "app_switch":
                accel = self.shortcut_lookup("app_switch") or "<Alt>Tab"
                mods, key = parse_accelerator(accel)
                self._record(f"uygulama değiştirici açıldı ({accel})")
                self._switch_key = key or "Tab"
                if not self.dry_run:
                    self._held = [self._keycode(m)[0] for m in mods]
                    for c in self._held:
                        self._key(c, True)
                    self.d.sync()
            self.step(inc)
        except Exception as exc:
            log.warning("sürekli eylem %s başarısız: %s", action, exc)
            self._record(f"HATA {action}: {exc}")

    def step(self, inc: int) -> None:
        action = self._cont
        if action is None:
            return
        try:
            if action == "window_cycle":
                self._record("pencere " + ("sonraki" if inc > 0 else "önceki"))
                if not self.dry_run:
                    self.window_step(1 if inc > 0 else -1)
                return
            if action == "app_switch":
                self._record("uygulama " + ("ileri" if inc > 0 else "geri"))
                if self.dry_run:
                    return
                code, _ = self._keycode(self._switch_key)
                shift = self._keycode("Shift_L")[0] if inc < 0 else None
                if shift:
                    self._key(shift, True)
                self._key(code, True)
                self._key(code, False)
                if shift:
                    self._key(shift, False)
                self.d.sync()
            elif action == "volume":
                self.send_shortcut("XF86AudioRaiseVolume" if inc > 0 else "XF86AudioLowerVolume")
            elif action == "brightness":
                self.send_shortcut("XF86MonBrightnessUp" if inc > 0 else "XF86MonBrightnessDown")
            elif action == "zoom":
                self._wheel(inc > 0, ctrl=True)
            elif action == "tab_switch":
                self.send_shortcut("<Control>Page_Down" if inc > 0 else "<Control>Page_Up")
        except Exception as exc:
            log.warning("adım başarısız: %s", exc)

    def end(self) -> None:
        if self._cont is None:
            return
        if self._held:
            self._record("seçilen pencere açıldı")
            if not self.dry_run:
                for c in reversed(self._held):
                    self._key(c, False)
                self.d.sync()
        self._held = []
        self._cont = None


def _clean_env() -> dict[str, str]:
    """Environment for user commands without PadForge's own interpreter tweaks."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def describe_binding(binding: dict) -> str:
    info = ACTIONS.get(binding.get("action", "none"))
    if info is None:
        return binding.get("action", "?")
    if info.needs_arg and binding.get("arg"):
        return f"{info.label.rstrip('…')}: {binding['arg']}"
    return info.label


def command_preview(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return [cmd]
