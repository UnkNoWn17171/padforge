#!/usr/bin/python3
"""Ham touchpad kaydı: dokunuş kareleri + aynı aygıtın XI2 ham çıktısı (JSON satırları).

    tools/kayit.py SANİYE ÇIKTI.jsonl [BEKLEME_SN [PARMAK]]

BEKLEME verilirse kayıt, en az PARMAK (varsayılan 1) parmak değdiği anda başlar
ve SANİYE sürer; BEKLEME boyunca kimse dokunmazsa boş biter. Gerçek parmak
verisiyle eşik ayarlamak için kullanıldı (2026-10-18).
"""
import json
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from padforge import device, xinput as xi  # noqa: E402

seconds = float(sys.argv[1])
out = open(sys.argv[2], "w")
wait = float(sys.argv[3]) if len(sys.argv) > 3 else None
trigger = int(sys.argv[4]) if len(sys.argv) > 4 else 1

info = next(p for p in device.list_touchpads() if p.readable and p.is_internal)
reader = device.TouchpadReader(info)
x = xi.XInput()
xd = next(d for d in x.touchpads() if d.node == info.node)
x.select(x.d.screen().root, [(xd.id, xi.xinput.RawMotionMask | xi.xinput.RawButtonPressMask
                              | xi.xinput.RawButtonReleaseMask)])
out.write(json.dumps({"meta": {"name": info.name, "res": reader.resolution,
                               "w": reader.axis_x.size_mm, "h": reader.axis_y.size_mm,
                               "clock": reader.clock, "xid": xd.id}}) + "\n")
wait_until = time.monotonic() + wait if wait else None
end = None if wait else time.monotonic() + seconds
nf = nx = 0
while end is None or time.monotonic() < end:
    if end is None and time.monotonic() > wait_until:
        break
    r, _, _ = select.select([reader.fd, x.d.fileno()], [], [], 0.5)
    now = time.monotonic()
    if reader.fd in r:
        for f in reader.frames():
            if end is None:
                if f.count < trigger:
                    continue
                end = time.monotonic() + seconds
            nf += 1
            out.write(json.dumps({"f": [round(f.time, 6), f.hw_time, f.count, int(f.button),
                                        [[t.slot, t.tracking_id, round(t.x, 3), round(t.y, 3),
                                          int(t.palm)] for t in f.touches.values()],
                                        round(now, 6)]}) + "\n")
    while x.d.pending_events():
        ev = x.d.next_event()
        if end is None:
            continue
        et = getattr(ev, "evtype", None)
        if et in (xi.xinput.RawMotion, xi.xinput.RawButtonPress, xi.xinput.RawButtonRelease):
            raw = xi.parse_raw_event(et, ev.data)
            if raw.deviceid == xd.id:
                nx += 1
                out.write(json.dumps({"x": [round(now, 6), et, raw.detail,
                                            {str(k): round(v, 4) for k, v in raw.values.items()}]})
                          + "\n")
out.close()
print(f"kaydedildi: {nf} kare, {nx} X olayı")
