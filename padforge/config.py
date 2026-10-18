"""Settings schema, defaults, presets and persistence.

The config is a plain JSON document so the GUI, the daemon and the user can all
read and edit it. ``load()`` always returns a complete, validated document:
unknown keys are dropped, missing keys are filled from ``DEFAULTS`` and
out-of-range values are clamped.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

CONFIG_VERSION = 1

GESTURE_KEYS = [
    f"swipe{n}_{d}" for n in (3, 4) for d in ("up", "down", "left", "right")
] + [f"pinch{n}_{d}" for n in (3, 4) for d in ("in", "out")] + ["tap4"]

# The recommended layout, as simple as possible:
#   3 fingers, any direction = window switcher (the user's Super+wheel menu);
#     keep moving to step through windows, lift to open the chosen one
#   4 fingers left/right = switch workspace, up = see everything, down = desktop
#   2-finger pinch = zoom; no 3/4-finger pinches, no 4-finger tap
# Nothing destructive (no "close window") sits on an easily triggered gesture.
RECOMMENDED_BINDINGS: dict[str, dict] = {
    "swipe3_up": {"action": "window_cycle"},
    "swipe3_down": {"action": "window_cycle"},
    "swipe3_left": {"action": "window_cycle"},
    "swipe3_right": {"action": "window_cycle"},
    "swipe4_up": {"action": "overview"},
    "swipe4_down": {"action": "show_desktop"},
    "swipe4_left": {"action": "workspace_next"},
    "swipe4_right": {"action": "workspace_prev"},
    "pinch3_in": {"action": "none"},
    "pinch3_out": {"action": "none"},
    "pinch4_in": {"action": "none"},
    "pinch4_out": {"action": "none"},
    "tap4": {"action": "none"},
}

DEFAULTS: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "device": {
        # "auto" = every touchpad; otherwise a substring of the device name.
        "match": "auto",
    },
    "pointer": {
        # custom = PadForge curve; adaptive/flat = stock libinput profiles.
        "profile": "custom",
        "speed": 0.0,          # libinput speed, -1..1 (adaptive/flat only)
        "curve": {
            "preset": "windows",
            "scale": 1.0,      # overall gain multiplier for the curve
            "points": [],      # [[finger mm/s, px/mm], ...] when preset == "custom"
        },
    },
    "tap": {
        # padforge = zero-latency tap engine in the daemon; libinput = stock tapping.
        "engine": "padforge",
        "enabled": True,
        "button_map": "lrm",   # 1/2/3 fingers -> left/right/middle ("lmr" swaps)
        "drag": True,          # tap, then touch again and move = drag
        "drag_lock": False,
        "max_duration_ms": 180,
        "max_move_mm": 1.5,
        "drag_window_ms": 200,
        "drag_lock_ms": 400,
        "typing_guard_ms": 250,
    },
    "click": {
        "method": "buttonareas",   # buttonareas | clickfinger
        "middle_emulation": False,
        "left_handed": False,
    },
    "typing": {
        "disable_while_typing": True,
    },
    "window": {
        "title_drag": True,       # tap a title bar, touch again and slide: move the window
        "super_move": True,       # Super + one finger: move the window under the pointer
        "super_resize": True,     # Super + three fingers: resize it
    },
    "scroll": {
        "method": "twofinger",     # twofinger | edge | none
        "natural": True,
        "speed": 1.0,              # 1.0 = libinput default distance per mm
        "acceleration": 0.0,       # 0 = linear, 1 = strong scroll acceleration
        "horizontal": True,
        "kinetic": {
            "enabled": True,
            "friction": 2.6,       # exponential decay rate (1/s)
            "min_speed": 50.0,     # finger speed at lift-off (mm/s) needed to coast
            "gain": 1.0,           # multiplier on lift-off velocity
            "max_duration": 4.0,   # seconds
        },
    },
    "gestures": {
        "enabled": True,
        "swipe_threshold_mm": 8.0,     # user's swipes: 9.8-21 mm; taps drift <= 1.4 mm
        "pinch_threshold": 0.22,     # relative spread change
        "step_mm": 16.0,             # distance per step for continuous actions
        "switch_step_mm": 4.5,       # finer steps for window/app/tab switchers
        "three_finger_drag": False,  # macOS-style; replaces 3-finger swipes
        "pinch_zoom_2f": True,       # 2-finger pinch -> Ctrl+wheel zoom
        "boost": True,               # brief "performance" power profile while gestures animate
        "bindings": None,          # filled with RECOMMENDED_BINDINGS below
    },
}

DEFAULTS["gestures"]["bindings"] = copy.deepcopy(RECOMMENDED_BINDINGS)

# Numeric limits: dotted path -> (min, max)
LIMITS: dict[str, tuple[float, float]] = {
    "pointer.speed": (-1.0, 1.0),
    "pointer.curve.scale": (0.2, 4.0),
    "tap.max_duration_ms": (60, 400),
    "tap.max_move_mm": (0.3, 6.0),
    "tap.drag_window_ms": (80, 600),
    "tap.drag_lock_ms": (100, 2000),
    "tap.typing_guard_ms": (0, 2000),
    "scroll.speed": (0.1, 5.0),
    "scroll.acceleration": (0.0, 3.0),
    "scroll.kinetic.friction": (0.5, 12.0),
    "scroll.kinetic.min_speed": (5.0, 500.0),
    "scroll.kinetic.gain": (0.2, 4.0),
    "scroll.kinetic.max_duration": (0.3, 15.0),
    "gestures.swipe_threshold_mm": (3.0, 60.0),
    "gestures.pinch_threshold": (0.05, 0.9),
    "gestures.step_mm": (4.0, 80.0),
    "gestures.switch_step_mm": (2.0, 30.0),
}

CHOICES: dict[str, tuple[str, ...]] = {
    "pointer.profile": ("custom", "adaptive", "flat"),
    "pointer.curve.preset": ("windows", "macos", "linux", "precise", "custom"),
    "tap.engine": ("padforge", "libinput"),
    "tap.button_map": ("lrm", "lmr"),
    "click.method": ("buttonareas", "clickfinger"),
    "scroll.method": ("twofinger", "edge", "none"),
}

# One-click starting points. Each is a partial document merged over the
# current config.
PROFILES: dict[str, dict[str, Any]] = {
    # Recommended: Windows-like feel plus the layout above.
    "windows": {
        "pointer": {"profile": "custom", "curve": {"preset": "windows", "scale": 1.0}},
        "tap": {"engine": "padforge", "enabled": True, "drag": True, "drag_lock": False,
                "button_map": "lrm"},
        "click": {"method": "buttonareas", "middle_emulation": False},
        "scroll": {"method": "twofinger", "natural": True, "speed": 1.0, "acceleration": 0.3,
                   "horizontal": True,
                   "kinetic": {"enabled": True, "friction": 2.6, "gain": 1.0}},
        "window": {"title_drag": True, "super_move": True, "super_resize": True},
        "gestures": {"enabled": True, "three_finger_drag": False, "pinch_zoom_2f": True,
                     "swipe_threshold_mm": 8.0, "step_mm": 16.0, "switch_step_mm": 4.5,
                     "bindings": copy.deepcopy(RECOMMENDED_BINDINGS)},
    },
    "macos": {
        "pointer": {"profile": "custom", "curve": {"preset": "macos", "scale": 1.0}},
        "tap": {"engine": "padforge", "enabled": True, "drag": False, "button_map": "lrm"},
        "click": {"method": "clickfinger"},
        "scroll": {"method": "twofinger", "natural": True, "speed": 0.9, "acceleration": 0.6,
                   "kinetic": {"enabled": True, "friction": 1.8}},
        "gestures": {"enabled": True, "three_finger_drag": True},
    },
    "linux": {
        "pointer": {"profile": "adaptive", "speed": 0.0, "curve": {"preset": "linux"}},
        "tap": {"engine": "libinput", "enabled": True, "drag": True},
        "click": {"method": "buttonareas"},
        "scroll": {"method": "twofinger", "speed": 1.0, "acceleration": 0.0,
                   "kinetic": {"enabled": False}},
        "gestures": {"enabled": False},
    },
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "padforge"


def config_path() -> Path:
    return config_dir() / "config.json"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/padforge-{os.getuid()}"
    path = Path(base) / "padforge"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def socket_path() -> Path:
    return runtime_dir() / "daemon.sock"


def deep_merge(base: dict, over: dict) -> dict:
    """Return ``base`` updated recursively with ``over`` (neither is modified)."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _get(doc: dict, path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        cur = cur[part]
    return cur


def _set(doc: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def get(doc: dict, path: str, default: Any = None) -> Any:
    try:
        return _get(doc, path)
    except (KeyError, TypeError):
        return default


def set_value(doc: dict, path: str, value: Any) -> None:
    _set(doc, path, value)


def _normalize(template: Any, value: Any) -> Any:
    """Coerce ``value`` to the shape and types of ``template``."""
    if isinstance(template, dict):
        if not isinstance(value, dict):
            return copy.deepcopy(template)
        if template is DEFAULTS["gestures"]["bindings"]:
            out = {}
            for key in GESTURE_KEYS:
                out[key] = _normalize_binding(value.get(key, template[key]))
            return out
        return {k: _normalize(t, value.get(k, t)) for k, t in template.items()}
    if isinstance(template, bool):
        return value if isinstance(value, bool) else template
    if isinstance(template, (int, float)) and not isinstance(template, bool):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return template
        return type(template)(value) if isinstance(template, float) else int(round(value))
    if isinstance(template, str):
        return value if isinstance(value, str) else template
    if isinstance(template, list):
        return value if isinstance(value, list) else []
    return value


def _normalize_binding(value: Any) -> dict:
    if isinstance(value, str):
        value = {"action": value}
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        return {"action": "none"}
    out = {"action": value["action"]}
    if isinstance(value.get("arg"), str) and value["arg"]:
        out["arg"] = value["arg"]
    return out


def _normalize_points(points: Any) -> list[list[float]]:
    out = []
    if not isinstance(points, list):
        return out
    for p in points:
        if (isinstance(p, (list, tuple)) and len(p) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)):
            v, g = float(p[0]), float(p[1])
            if 0.0 <= v <= 5000.0 and 0.0 <= g <= 500.0:
                out.append([v, g])
    out.sort(key=lambda p: p[0])
    dedup: list[list[float]] = []
    for p in out:
        if dedup and abs(dedup[-1][0] - p[0]) < 1e-6:
            dedup[-1] = p
        else:
            dedup.append(p)
    return dedup[:32]


def validate(doc: Any) -> dict:
    """Return a complete, clamped copy of ``doc``."""
    if not isinstance(doc, dict):
        doc = {}
    out = _normalize(DEFAULTS, doc)
    out["version"] = CONFIG_VERSION
    for path, (lo, hi) in LIMITS.items():
        val = _get(out, path)
        _set(out, path, type(val)(min(hi, max(lo, val))))
    for path, choices in CHOICES.items():
        if _get(out, path) not in choices:
            _set(out, path, _get(DEFAULTS, path))
    out["pointer"]["curve"]["points"] = _normalize_points(out["pointer"]["curve"]["points"])
    if out["pointer"]["curve"]["preset"] == "custom" and len(out["pointer"]["curve"]["points"]) < 2:
        out["pointer"]["curve"]["preset"] = "windows"
    return out


def defaults() -> dict:
    return validate(copy.deepcopy(DEFAULTS))


def apply_profile(doc: dict, name: str) -> dict:
    if name not in PROFILES:
        raise KeyError(name)
    return validate(deep_merge(doc, PROFILES[name]))


def load(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return defaults()
    except (OSError, ValueError):
        # A broken file must never take the touchpad down; keep a copy and fall back.
        try:
            os.replace(path, str(path) + ".broken")
        except OSError:
            pass
        return defaults()
    return validate(raw)


def save(doc: dict, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(validate(doc), indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
