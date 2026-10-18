#!/usr/bin/python3
"""GNOME geçişlerinde kaçan kareleri ölçer (X Present CompleteNotify, her tazeleme).

    tools/kare_olcum.py [TUR]

Çalışan PadForge servisine soket üzerinden eylem yollar (çalışma alanı ileri/geri,
genel bakış aç/kapa) ve her geçişte atlanan ekran tazelemelerini sayar; hızlandırma
(gestures.boost) kapalı ve açık olarak TUR kez dönüşümlü ölçer, sonra açık bırakır.
EKRANDA GEÇİŞLER OLUR. 2026-10-18 pil ölçümü: %89.1 → %97.7.
"""
import json
import select
import socket
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Xlib import display  # noqa: E402,F401
from Xlib.protocol import rq  # noqa: E402
from padforge import config  # noqa: E402

d = display.Display()
root = d.screen().root
pres = d.query_extension("Present")
if not pres.present:
    sys.exit("Present uzantısı yok")


class PresentSelectInput(rq.Request):
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(3), rq.RequestLength(),
        rq.Card32("eid"), rq.Window("window"), rq.Card32("mask"))


# Clutter'ın sahne penceresi bindirme penceresinin (COW) altında: hepsini dinle.
cow = root.composite_get_overlay_window().overlay_window
for w in [cow] + list(cow.query_tree().children):
    PresentSelectInput(display=d.display, opcode=pres.major_opcode,
                       eid=d.display.allocate_resource_id(), window=w, mask=1 << 1)
d.sync()


def drain(seconds):
    out = []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        select.select([d.fileno()], [], [], 0.01)
        while d.pending_events():
            ev = d.next_event()
            if getattr(ev, "extension", None) == pres.major_opcode and getattr(ev, "evtype", 0) == 1:
                ust, msc = struct.unpack_from("=QQ", ev.data, 14)
                out.append((ust, msc))
    return out


def skipped(frames):
    mscs = sorted({msc for _, msc in frames})
    gaps = [b - a for a, b in zip(mscs, mscs[1:])]
    anim = [g for g in gaps if g < 30]            # 30+ = ekran zaten boştaydı
    return sum(g - 1 for g in anim if g > 1), len(anim)


def run(action):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(config.socket_path()))
    s.sendall((json.dumps({"cmd": "run", "action": action}) + "\n").encode())
    s.recv(4096)
    s.close()


def set_boost(on):
    c = config.load()
    c["gestures"]["boost"] = on
    config.save(c)
    time.sleep(0.6)


ACTIONS = (("workspace_next", 0.9), ("workspace_prev", 0.9), ("overview", 1.0), ("overview", 1.0))
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
res = {"kapalı": {}, "açık": {}}
for _ in range(rounds):
    for cond, on in (("kapalı", False), ("açık", True)):
        set_boost(on)
        if not on:
            time.sleep(8.5)                      # önceki tutmanın bitmesini bekle
        for action, wait in ACTIONS:
            drain(0.05)                          # beklerken biriken kareleri at
            run(action)
            res[cond].setdefault(action, []).append(skipped(drain(wait)))
set_boost(True)
power = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True).stdout.strip()
print(f"şarj: {Path('/sys/class/power_supply/ADP1/online').read_text().strip()} · profil: {power}")
for cond, per in res.items():
    s = sum(a for v in per.values() for a, n in v)
    n = sum(n for v in per.values() for a, n in v)
    print(f"Hızlandırma {cond}: akıcılık %{100 * n / max(1, s + n):.1f} ({s} atlanan / {s + n})")
    for action, v in per.items():
        print(f"   {action:15s} atlanan: {[a for a, _ in v]}  medyan {statistics.median(a for a, _ in v)}")
d.close()
