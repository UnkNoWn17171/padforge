"""Two-way sync with GNOME's touchpad settings (org.gnome.desktop.peripherals.touchpad).

Mutter writes the libinput properties from these keys whenever they change or a
device appears. Keeping them equal to PadForge's config means GNOME Settings
shows the same values and mutter never fights the daemon; when the user flips a
switch in GNOME Settings, :func:`adopt` folds it back into the PadForge config.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

SCHEMA = "org.gnome.desktop.peripherals.touchpad"

# GNOME's "default" already means these libinput defaults; do not rewrite it.
DEFAULT_MEANS = {"tap-button-map": "lrm", "click-method": "areas", "accel-profile": "adaptive"}


def to_gsettings(cfg: dict) -> dict[str, Any]:
    tap, scroll, click = cfg["tap"], cfg["scroll"], cfg["click"]
    out: dict[str, Any] = {
        "tap-to-click": bool(tap["enabled"]),
        "tap-and-drag": bool(tap["drag"]),
        "tap-and-drag-lock": bool(tap["drag_lock"]),
        "tap-button-map": tap["button_map"],
        "natural-scroll": bool(scroll["natural"]),
        "two-finger-scrolling-enabled": scroll["method"] == "twofinger",
        "edge-scrolling-enabled": scroll["method"] == "edge",
        "disable-while-typing": bool(cfg["typing"]["disable_while_typing"]),
        "middle-click-emulation": bool(click["middle_emulation"]),
        "click-method": "fingers" if click["method"] == "clickfinger" else "areas",
        # "mouse" (follow the mouse setting) is GNOME's default for right-handed.
        "left-handed": "left" if click["left_handed"] else "mouse",
    }
    profile = cfg["pointer"]["profile"]
    if profile in ("adaptive", "flat"):
        out["accel-profile"] = profile
        out["speed"] = float(cfg["pointer"]["speed"])
    return out


def adopt(cfg: dict, key: str, value: Any) -> dict | None:
    """Return an updated config if a GNOME key change should be adopted, else None."""
    new = copy.deepcopy(cfg)
    if key == "tap-to-click":
        new["tap"]["enabled"] = bool(value)
    elif key == "tap-and-drag":
        new["tap"]["drag"] = bool(value)
    elif key == "tap-and-drag-lock":
        new["tap"]["drag_lock"] = bool(value)
    elif key == "tap-button-map":
        if value in ("lrm", "lmr"):
            new["tap"]["button_map"] = value
    elif key == "natural-scroll":
        new["scroll"]["natural"] = bool(value)
    elif key == "two-finger-scrolling-enabled":
        if value:
            new["scroll"]["method"] = "twofinger"
        elif new["scroll"]["method"] == "twofinger":
            new["scroll"]["method"] = "none"
    elif key == "edge-scrolling-enabled":
        if value:
            new["scroll"]["method"] = "edge"
        elif new["scroll"]["method"] == "edge":
            new["scroll"]["method"] = "none"
    elif key == "disable-while-typing":
        new["typing"]["disable_while_typing"] = bool(value)
    elif key == "middle-click-emulation":
        new["click"]["middle_emulation"] = bool(value)
    elif key == "click-method":
        if value == "fingers":
            new["click"]["method"] = "clickfinger"
        elif value in ("areas", "default"):
            new["click"]["method"] = "buttonareas"
    elif key == "left-handed":
        new["click"]["left_handed"] = value == "left"
    elif key == "speed":
        if new["pointer"]["profile"] in ("adaptive", "flat"):
            new["pointer"]["speed"] = max(-1.0, min(1.0, float(value)))
    elif key == "accel-profile":
        if value in ("adaptive", "flat") and new["pointer"]["profile"] != "custom":
            new["pointer"]["profile"] = value
    else:
        return None
    return new if new != cfg else None


def seed(cfg: dict, values: dict[str, Any]) -> dict:
    """Fold the user's current GNOME touchpad settings into a fresh config.

    Used on first install so PadForge starts from what the user already has; the
    speed is kept for the stock-profile fallback even when the curve is active.
    """
    out = copy.deepcopy(cfg)
    for key in ("tap-to-click", "tap-and-drag", "tap-and-drag-lock", "tap-button-map",
                "natural-scroll", "two-finger-scrolling-enabled", "edge-scrolling-enabled",
                "disable-while-typing", "middle-click-emulation", "click-method", "left-handed"):
        if key in values:
            new = adopt(out, key, values[key])
            if new is not None:
                out = new
    if isinstance(values.get("speed"), (int, float)):
        out["pointer"]["speed"] = max(-1.0, min(1.0, float(values["speed"])))
    return out


class GnomeSync:
    """Gio.Settings wrapper; silently inactive when the schema is not installed."""

    def __init__(self, on_external_change: Callable[[str, Any], None] | None = None):
        self.settings = None
        self._writing = False
        self._handler = None
        try:
            from gi.repository import Gio
            source = Gio.SettingsSchemaSource.get_default()
            if source is not None and source.lookup(SCHEMA, True) is not None:
                self.settings = Gio.Settings.new(SCHEMA)
        except Exception:
            self.settings = None
        if self.settings is not None and on_external_change is not None:
            self._handler = self.settings.connect("changed", self._changed, on_external_change)

    @property
    def available(self) -> bool:
        return self.settings is not None

    def _changed(self, settings, key, callback) -> None:
        if self._writing:
            return
        try:
            callback(key, settings.get_value(key).unpack())
        except Exception:
            pass

    def read(self) -> dict[str, Any]:
        if self.settings is None:
            return {}
        out = {}
        for key in self.settings.props.settings_schema.list_keys():
            try:
                out[key] = self.settings.get_value(key).unpack()
            except Exception:
                pass
        return out

    def write(self, values: dict[str, Any]) -> list[str]:
        """Write keys that differ; returns the keys actually changed."""
        if self.settings is None:
            return []
        from gi.repository import GLib
        changed = []
        schema = self.settings.props.settings_schema
        self._writing = True
        try:
            for key, value in values.items():
                if not schema.has_key(key):
                    continue
                current = self.settings.get_value(key)
                if current.get_type_string() == "s" and current.get_string() == "default" \
                        and DEFAULT_MEANS.get(key) == value:
                    continue
                vtype = current.get_type_string()
                new = GLib.Variant(vtype, value)
                if not current.equal(new):
                    self.settings.set_value(key, new)
                    changed.append(key)
            if changed:
                from gi.repository import Gio
                Gio.Settings.sync()
        finally:
            self._writing = False
        return changed
