"""Window-manager helpers on X11: title-bar hit test, Super key state.

On GNOME almost every app draws its own title bar (GTK header bars, Electron),
so the title bar is not a separate X window. The hit test uses the visible
window rectangle (window minus ``_GTK_FRAME_EXTENTS`` shadows) and treats its
top ``TITLE_PX`` pixels as the title bar; for server-side decorated windows the
frame's top extent is used instead.
"""
from __future__ import annotations

from Xlib import X

TITLE_PX = 48
SKIP_TYPES = ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")


def _prop(d, w, name: str):
    try:
        p = w.get_full_property(d.intern_atom(name), X.AnyPropertyType)
    except Exception:
        return None
    return p.value if p is not None else None


def _client_of(d, top):
    """``top`` itself when it is a client window, else its WM_STATE child (SSD frame)."""
    if _prop(d, top, "WM_STATE") is not None:
        return top, False
    try:
        for c in top.query_tree().children:
            if _prop(d, c, "WM_STATE") is not None:
                return c, True
    except Exception:
        pass
    return None, False


def title_band(d, top, root) -> tuple[int, int, int, int] | None:
    """(x0, x1, y0, y1) of the title bar of top-level window ``top`` in root coords."""
    client, framed = _client_of(d, top)
    if client is None:
        return None
    types = _prop(d, client, "_NET_WM_WINDOW_TYPE")
    if types is not None:
        names = {d.get_atom_name(a) for a in types}
        if names & set(SKIP_TYPES):
            return None
    try:
        g = top.get_geometry()
        tr = top.translate_coords(root, 0, 0)
    except Exception:
        return None
    x, y = -tr.x, -tr.y
    if framed:
        ext = _prop(d, client, "_NET_FRAME_EXTENTS")
        top_h = int(ext[2]) if ext is not None and len(ext) == 4 and ext[2] else 32
        return x, x + g.width, y, y + top_h
    ext = _prop(d, client, "_GTK_FRAME_EXTENTS")
    left, right, top_s, _bottom = (list(ext) + [0, 0, 0, 0])[:4] if ext is not None else (0, 0, 0, 0)
    vy = y + int(top_s)
    return x + int(left), x + g.width - int(right), vy, vy + TITLE_PX


def pointer_on_title_bar(d) -> bool:
    root = d.screen().root
    p = root.query_pointer()
    if not p.child:
        return False
    band = title_band(d, p.child, root)
    if band is None:
        return False
    x0, x1, y0, y1 = band
    return x0 <= p.root_x < x1 and y0 <= p.root_y < y1


def super_held(d) -> bool:
    """Is a Super (Mod4) key down right now? Reads only the modifier mask."""
    return bool(d.screen().root.query_pointer().mask & X.Mod4Mask)


def wm_buttons() -> tuple[int, int]:
    """(move button, resize button) GNOME uses with the window modifier key."""
    resize = 2
    try:
        from gi.repository import Gio
        if Gio.Settings.new("org.gnome.desktop.wm.preferences").get_boolean(
                "resize-with-right-button"):
            resize = 3
    except Exception:
        pass
    return 1, resize
