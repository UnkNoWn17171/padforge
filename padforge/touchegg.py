"""Migration from Touchégg: import its gestures, switch its client off.

Running Touchégg next to libinput tapping sends two clicks per 2/3-finger tap
(Touchégg's TAP gesture plus libinput's own tap), and its separate libinput
context can read one 2-finger movement as a pinch while Xorg reads it as a
scroll. PadForge replaces both, so the import maps Touchégg gestures onto
PadForge bindings and :func:`disable_client` stops the session client.
"""
from __future__ import annotations

import copy
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

USER_CONF = Path.home() / ".config" / "touchegg" / "touchegg.conf"
SYSTEM_AUTOSTART = Path("/etc/xdg/autostart/touchegg.desktop")
USER_AUTOSTART = Path.home() / ".config" / "autostart" / "touchegg.desktop"

DIRS = {"UP": "up", "DOWN": "down", "LEFT": "left", "RIGHT": "right", "IN": "in", "OUT": "out"}


def _keys_to_accel(mods: str, keys: str) -> str:
    out = ""
    for m in filter(None, (mods or "").split("+")):
        m = m.strip()
        name = {"Super_L": "Super", "Super_R": "Super", "Control_L": "Control",
                "Control_R": "Control", "Alt_L": "Alt", "Alt_R": "Alt", "Shift_L": "Shift",
                "Shift_R": "Shift"}.get(m, m)
        out += f"<{name}>"
    key = (keys or "").split("+")[-1].strip()
    if len(key) == 1:
        key = key.lower()
    return out + key


def _action_for(gtype: str, direction: str, act: ET.Element) -> dict | None:
    atype = act.get("type", "")
    text = {child.tag: (child.text or "").strip() for child in act}
    if atype == "CHANGE_DESKTOP":
        nxt = direction in ("left", "up", "in")
        return {"action": "workspace_next" if nxt else "workspace_prev"}
    if atype == "SHOW_DESKTOP":
        return {"action": "show_desktop"}
    if atype == "CLOSE_WINDOW":
        return {"action": "close_window"}
    if atype == "MINIMIZE_WINDOW":
        return {"action": "minimize"}
    if atype in ("MAXIMIZE_RESTORE_WINDOW", "TILE_WINDOW", "FULLSCREEN_WINDOW"):
        return {"action": "maximize"}
    if atype == "RUN_COMMAND" and text.get("command"):
        return {"action": "command", "arg": text["command"]}
    if atype == "MOUSE_CLICK":
        btn = text.get("button", "1")
        return {"action": {"2": "middle_click", "3": "right_click"}.get(btn, "none")}
    if atype == "SEND_KEYS":
        mods, keys = text.get("modifiers", ""), text.get("keys", "")
        accel = _keys_to_accel(mods, keys)
        repeat = text.get("repeat", "false").lower() == "true"
        low = accel.lower()
        if repeat and low.endswith("tab") and ("<super>" in low or "<alt>" in low):
            return {"action": "app_switch"}
        if low in ("<super>a",):
            return {"action": "app_grid"}
        if repeat and ("kp_add" in low or "kp_subtract" in low or "plus" in low):
            return {"action": "zoom"}
        return {"action": "keys", "arg": accel}
    return None


def _note(result: dict, text: str) -> None:
    if text not in result["notes"]:
        result["notes"].append(text)


def parse(path: Path = USER_CONF) -> dict:
    """Return {'bindings': {...}, 'pinch_zoom_2f': bool, 'skipped': [...], 'notes': [...]}"""
    root = ET.parse(path).getroot()
    result = {"bindings": {}, "pinch_zoom_2f": False, "skipped": [], "notes": []}
    for app in root.iter("application"):
        app_name = app.get("name", "All")
        for g in app.iter("gesture"):
            gtype = g.get("type", "").upper()
            fingers = g.get("fingers", "0")
            direction = DIRS.get(g.get("direction", "").upper(), "")
            act = g.find("action")
            if act is None:
                continue
            label = f"{gtype.lower()} {fingers} {direction or '-'} ({app_name})"
            if gtype == "TAP":
                _note(result, f"{fingers} parmak dokunma: PadForge/libinput zaten yapıyor "
                              "(Touchégg ile birlikte çift tık oluşuyordu)")
                continue
            if gtype == "PINCH" and fingers == "2":
                result["pinch_zoom_2f"] = True
                _note(result, f"2 parmak sıkıştırma ({app_name}) → Ctrl+tekerlek yakınlaştırma")
                continue
            if app_name != "All":
                result["skipped"].append(label + ": uygulamaya özel hareketler desteklenmiyor")
                continue
            if gtype not in ("SWIPE", "PINCH") or fingers not in ("3", "4") or not direction:
                result["skipped"].append(label)
                continue
            key = f"{gtype.lower()}{fingers}_{direction}"
            binding = _action_for(gtype, direction, act)
            if binding is None:
                result["skipped"].append(f"{label}: {act.get('type')}")
                continue
            result["bindings"][key] = binding
    return result


def merge_into(cfg: dict, imported: dict) -> dict:
    new = copy.deepcopy(cfg)
    for key, binding in imported["bindings"].items():
        if key in new["gestures"]["bindings"]:
            new["gestures"]["bindings"][key] = binding
    if imported["pinch_zoom_2f"]:
        new["gestures"]["pinch_zoom_2f"] = True
    new["gestures"]["enabled"] = True
    return new


def client_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "touchegg"],
                             capture_output=True, text=True)
        return bool(out.stdout.strip())
    except OSError:
        return False


def daemon_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "touchegg --daemon"], capture_output=True, text=True)
        return bool(out.stdout.strip())
    except OSError:
        return False


def disable_client() -> list[str]:
    """Stop Touchégg's session client and keep it from autostarting (no root needed).

    Undo: delete ~/.config/autostart/touchegg.desktop and run `touchegg &`.
    """
    done = []
    USER_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    body = ""
    if SYSTEM_AUTOSTART.exists():
        body = SYSTEM_AUTOSTART.read_text()
    if "[Desktop Entry]" not in body:
        body = "[Desktop Entry]\nType=Application\nName=Touchégg\nExec=touchegg\n"
    lines = [ln for ln in body.splitlines() if not ln.startswith("Hidden=")]
    out, inserted = [], False
    for ln in lines:
        out.append(ln)
        if ln.strip() == "[Desktop Entry]" and not inserted:
            out.append("Hidden=true")
            inserted = True
    USER_AUTOSTART.write_text("\n".join(out) + "\n")
    done.append(f"otomatik başlatma kapatıldı: {USER_AUTOSTART}")
    if client_running():
        subprocess.run(["pkill", "-u", str(os.getuid()), "-x", "touchegg"], check=False)
        done.append("Touchégg istemcisi durduruldu")
    return done


def enable_client() -> None:
    """Undo :func:`disable_client`."""
    try:
        USER_AUTOSTART.unlink()
    except FileNotFoundError:
        pass
    if not client_running():
        subprocess.Popen(["touchegg"], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def autostart_disabled() -> bool:
    try:
        return "Hidden=true" in USER_AUTOSTART.read_text()
    except OSError:
        return False
