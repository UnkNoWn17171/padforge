"""Command line: ``padforge [gui|daemon|doctor|measure|status|apply|profile|selftest]``."""
from __future__ import annotations

import argparse
import json
import sys
import time


def cmd_doctor(args) -> int:
    from . import diagnostics, xinput
    status = diagnostics.daemon_request({"cmd": "status"})
    xprops = None
    try:
        x = xinput.XInput()
        xprops = {d.name: d.id for d in x.touchpads()}
    except Exception:
        pass
    checks = diagnostics.run_checks(status.get("status") if status else None, xprops=xprops)
    if args.json:
        print(json.dumps([c.as_dict() for c in checks], ensure_ascii=False, indent=2))
    else:
        print(diagnostics.format_checks(checks))
    return 1 if any(c.level == "error" for c in checks) else 0


def cmd_measure(args) -> int:
    from . import device
    from .stats import TouchStats
    pads = [p for p in device.list_touchpads() if p.readable]
    if not pads:
        print("Okunabilir touchpad yok. Önce: sudo bash packaging/install-udev.sh", file=sys.stderr)
        return 1
    import select
    reader = device.TouchpadReader(pads[0])
    stats = TouchStats()
    print(f"{pads[0].name}: {reader.axis_x.size_mm:.1f}×{reader.axis_y.size_mm:.1f} mm, "
          f"{reader.resolution:g} birim/mm, {reader.nslots} yuva, saat={reader.clock}")
    print(f"{args.seconds:g} sn boyunca parmağını gezdir, bırak, tekrar dokun, bir süre sabit tut…")
    end = time.monotonic() + args.seconds
    last_print = 0.0
    while time.monotonic() < end:
        r, _, _ = select.select([reader.fd], [], [], 0.2)
        if r:
            t = time.monotonic()
            for f in reader.frames():
                stats.feed(f, t)
        if time.monotonic() - last_print > 1.0:
            last_print = time.monotonic()
            s = stats.summary(reader.parser.dropped_count)
            print("  " + _fmt_summary(s), flush=True)
    s = stats.summary(reader.parser.dropped_count)
    if args.json:
        print(json.dumps(s.as_dict(), indent=2))
    else:
        print("Sonuç: " + _fmt_summary(s))
    return 0


def _fmt_summary(s) -> str:
    def f(v, unit, nd=1):
        return f"{v:.{nd}f} {unit}" if isinstance(v, (int, float)) else "—"
    return (f"rapor {f(s.rate_hz, 'Hz', 0)} · sapma {f(s.interval_jitter_ms, 'ms', 2)} · "
            f"ilk rapor gecikmesi {f(s.wake_delay_ms, 'ms')} (en çok {f(s.wake_delay_max_ms, 'ms')}) · "
            f"okuma {f(s.read_latency_ms, 'ms', 2)} · titreşim {f(s.noise_mm, 'mm', 3)} · "
            f"{s.frames} kare, {s.sessions} dokunuş, en çok {s.max_fingers} parmak")


def cmd_status(args) -> int:
    from . import diagnostics
    r = diagnostics.daemon_request({"cmd": "status"})
    if not r:
        print("PadForge servisi çalışmıyor.")
        return 1
    print(json.dumps(r.get("status"), ensure_ascii=False, indent=2))
    return 0


def cmd_apply(args) -> int:
    """Apply the config once (no daemon): libinput properties + GNOME keys."""
    from . import config, device, gnome, xinput
    cfg = config.load()
    x = xinput.XInput()
    infos = {i.node: i for i in device.list_touchpads()}
    rc = 0
    for xd in x.touchpads():
        res = 0.0
        info = infos.get(xd.node or "")
        if info and info.readable:
            try:
                res = device.TouchpadReader(info).resolution
            except Exception:
                pass
        plan = xinput.plan_properties(cfg, x.caps(xd.id, res))
        if cfg["tap"]["engine"] == "padforge":
            # Without the daemon nobody taps for us: keep libinput tapping.
            plan = [(n, [int(cfg["tap"]["enabled"])] if n == xinput.P_TAP else v) for n, v in plan]
        errs = x.apply(xd.id, plan)
        print(f"{xd.name}: {'tamam' if not errs else ', '.join(errs)}")
        rc |= bool(errs)
    g = gnome.GnomeSync()
    if g.available:
        g.write(gnome.to_gsettings(cfg))
    return rc


def cmd_init(args) -> int:
    """Create the first config from the current GNOME settings (install.sh calls this)."""
    from . import config, gnome
    path = config.config_path()
    if path.exists() and not args.force:
        print(f"Ayar zaten var: {path}")
        return 0
    cfg = config.defaults()
    g = gnome.GnomeSync()
    if g.available:
        cfg = gnome.seed(cfg, g.read())
    cfg = config.validate(cfg)
    config.save(cfg, path)
    print(f"İlk ayar oluşturuldu (mevcut GNOME ayarlarından): {path}")
    return 0


def cmd_profile(args) -> int:
    from . import config
    cfg = config.apply_profile(config.load(), args.name)
    path = config.save(cfg)
    print(f"'{args.name}' profili uygulandı → {path}")
    return 0


def cmd_import_touchegg(args) -> int:
    from . import config, touchegg
    path = touchegg.USER_CONF
    if not path.exists():
        print(f"Touchégg ayarı yok: {path}")
        return 1
    imported = touchegg.parse(path)
    for key, b in sorted(imported["bindings"].items()):
        print(f"  {key:14s} → {b['action']}{' ' + b['arg'] if b.get('arg') else ''}")
    for note in imported["notes"]:
        print(f"  not: {note}")
    for skip in imported["skipped"]:
        print(f"  atlandı: {skip}")
    if args.dry_run:
        return 0
    cfg = touchegg.merge_into(config.load(), imported)
    print(f"Kaydedildi: {config.save(cfg)}")
    if args.disable:
        for line in touchegg.disable_client():
            print(line)
    return 0


def cmd_disable_touchegg(args) -> int:
    from . import touchegg
    for line in touchegg.disable_client():
        print(line)
    if touchegg.daemon_running():
        print("Not: Touchégg'in root servisi hâlâ çalışıyor (istemci olmadan bir şey yapmaz). "
              "Tamamen kapatmak için: sudo systemctl disable --now touchegg.service")
    print("Geri almak için: padforge enable-touchegg")
    return 0


def cmd_enable_touchegg(args) -> int:
    from . import touchegg
    touchegg.enable_client()
    print("Touchégg istemcisi yeniden etkin.")
    return 0


def cmd_selftest(args) -> int:
    from . import selftest
    return selftest.main(verbose=args.verbose)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="padforge", description="PadForge touchpad aracı")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="arayüzü aç (varsayılan)")
    d = sub.add_parser("daemon", help="arka plan servisini çalıştır")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("-v", "--verbose", action="store_true")
    doc = sub.add_parser("doctor", help="donanım ve ayar teşhisi")
    doc.add_argument("--json", action="store_true")
    m = sub.add_parser("measure", help="canlı ölçüm (rapor hızı, gecikme, titreşim)")
    m.add_argument("--seconds", type=float, default=15)
    m.add_argument("--json", action="store_true")
    sub.add_parser("status", help="servis durumu")
    sub.add_parser("apply", help="ayarları bir kez uygula")
    ini = sub.add_parser("init", help="ilk ayarı mevcut GNOME ayarlarından oluştur")
    ini.add_argument("--force", action="store_true")
    p = sub.add_parser("profile", help="hazır profil uygula")
    p.add_argument("name", choices=["windows", "macos", "linux"])
    it = sub.add_parser("import-touchegg", help="Touchégg hareketlerini PadForge'a aktar")
    it.add_argument("--dry-run", action="store_true", help="yalnızca göster")
    it.add_argument("--disable", action="store_true", help="ardından Touchégg istemcisini kapat")
    sub.add_parser("disable-touchegg", help="Touchégg istemcisini durdur (geri alınabilir)")
    sub.add_parser("enable-touchegg", help="Touchégg istemcisini yeniden aç")
    st = sub.add_parser("selftest", help="sanal touchpad ile uçtan uca test")
    st.add_argument("-v", "--verbose", action="store_true")
    args, rest = ap.parse_known_args(argv)

    if args.cmd in (None, "gui"):
        from .gui.app import main as gui_main
        return gui_main(rest)
    if args.cmd == "daemon":
        from .daemon import main as daemon_main
        return daemon_main((["--dry-run"] if args.dry_run else [])
                           + (["-v"] if args.verbose else []) + rest)
    return {"doctor": cmd_doctor, "measure": cmd_measure, "status": cmd_status,
            "apply": cmd_apply, "profile": cmd_profile, "selftest": cmd_selftest, "init": cmd_init,
            "import-touchegg": cmd_import_touchegg, "disable-touchegg": cmd_disable_touchegg,
            "enable-touchegg": cmd_enable_touchegg}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
