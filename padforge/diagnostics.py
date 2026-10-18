"""Health checks shown by ``padforge doctor`` and the GUI overview page."""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from . import config, device

OK, INFO, WARN, ERROR = "ok", "info", "warn", "error"

WAKE_OK_MS = 3.0            # first-report delay below this is not noticeable
WAKE_SAMPLES_NEEDED = 5

# /proc/<pid>/comm prefix -> (label, fix command)
CONFLICTING = {
    "touchegg": ("Touchégg", "padforge disable-touchegg"),
    "libinput-gestu": ("libinput-gestures", "libinput-gestures-setup stop autostart"),
    "fusuma": ("Fusuma", "pkill -x fusuma"),
    "gnome-x11-gestu": ("gnome-x11-gesture-daemon", "systemctl --user disable --now gnome-x11-gesture-daemon"),
    "comfortable-swi": ("comfortable-swipe", "comfortable-swipe stop"),
}


@dataclass
class Check:
    level: str
    title: str
    detail: str = ""
    fix: str = ""            # shell command that fixes it (shown, never run automatically)

    def as_dict(self) -> dict:
        return dict(vars(self))


def helper_script() -> str:
    """Path of install-udev.sh: installed copy first, then the source tree."""
    for cand in (Path.home() / ".local/share/padforge/install-udev.sh",
                 Path(__file__).resolve().parent.parent / "packaging" / "install-udev.sh"):
        if cand.exists():
            return str(cand)
    return "packaging/install-udev.sh"


def daemon_request(msg: dict, timeout: float = 0.6) -> dict | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(str(config.socket_path()))
        s.sendall((json.dumps(msg) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf) if buf else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def i2c_sleep_check(stats: dict, script: str) -> Check:
    """Decide from measurements whether the I2C controller's runtime sleep matters."""
    wake_max, n = stats.get("wake_delay_max_ms"), stats.get("wake_samples", 0)
    measured = isinstance(wake_max, (int, float))
    if measured and n >= WAKE_SAMPLES_NEEDED and wake_max < WAKE_OK_MS:
        return Check(OK, "I2C uykusu gecikme yaratmıyor",
                     f"{n} dokunuşta ilk rapor en çok {wake_max:.1f} ms gecikti; "
                     "uykuyu kapatmaya gerek yok.")
    detail = ("Parmak dokunduğunda denetleyicinin uyanması ilk hareketi geciktirebilir."
              + (f" Ölçülen en kötü ilk rapor gecikmesi: {wake_max:.1f} ms ({n} dokunuş)."
                 if measured else " Henüz ölçülmedi: touchpad'i biraz kullanınca kesinleşir."))
    level = WARN if measured and wake_max >= WAKE_OK_MS else INFO
    return Check(level, "I2C denetleyicisi boşta uykuya geçiyor", detail,
                 fix=f"sudo bash {script} --power-fix")


def running_conflicts(proc: Path = Path("/proc"), uid: int | None = None) -> list[tuple[str, str]]:
    """Gesture tools running in *this user's* session.

    Their root daemons (e.g. ``touchegg --daemon``) do nothing without the session
    client, so they are not a conflict.
    """
    uid = os.getuid() if uid is None else uid
    found: list[tuple[str, str]] = []
    try:
        for pid in os.listdir(proc):
            if not pid.isdigit():
                continue
            try:
                if (proc / pid).stat().st_uid != uid:
                    continue
                comm = (proc / pid / "comm").read_text().strip()
            except OSError:
                continue
            for key, entry in CONFLICTING.items():
                if comm.startswith(key) and entry not in found:
                    found.append(entry)
    except OSError:
        pass
    return found


def run_checks(status: dict | None = None, cfg: dict | None = None,
               xprops: dict | None = None) -> list[Check]:
    """Collect checks. ``status`` is the daemon status (None = not running)."""
    cfg = cfg or config.load()
    checks: list[Check] = []
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session == "wayland":
        checks.append(Check(ERROR, "Wayland oturumu",
                            "PadForge şu an yalnızca X11 oturumunda çalışıyor."))
    elif os.environ.get("DISPLAY"):
        checks.append(Check(OK, "X11 oturumu", f"DISPLAY={os.environ['DISPLAY']}"))

    pads = device.list_touchpads()
    if not pads:
        checks.append(Check(ERROR, "Touchpad bulunamadı",
                            "Çekirdek touchpad olarak işaretlenmiş bir giriş aygıtı yok."))
        return checks
    script = helper_script()
    for pad in pads:
        drivers = ", ".join(pad.driver_chain()) or "?"
        caps = [f"{pad.max_fingers} parmak", f"{pad.width_mm:.0f}×{pad.height_mm:.0f} mm"]
        caps.append("basınç var" if pad.has_pressure else "basınç yok")
        if pad.has_hw_timestamp:
            caps.append("donanım zaman damgası")
        if pad.is_clickpad:
            caps.append("tıklanabilir yüzey")
        checks.append(Check(OK, f"{pad.name}",
                            f"{pad.bus_name} · {pad.node} · sürücü: {drivers} · " + ", ".join(caps)))
        if not pad.readable:
            checks.append(Check(ERROR, "Ham dokunma verisine erişim yok",
                                f"{pad.node} okunamıyor. Hareketler, gecikmesiz dokunma, kinetik "
                                "kaydırma ve özel ivme eğrisi bu izin olmadan çalışmaz.",
                                fix=f"sudo bash {script}"))
        if pad.bustype == 0x18:
            name = pad.sysfs.split("/i2c-")[-1].split("/")[0] if "/i2c-" in pad.sysfs else ""
            irq, count = device.irq_count(name) if name else (None, 0)
            if irq is None:
                checks.append(Check(WARN, "Kesme (IRQ) bulunamadı",
                                    "Touchpad yoklama (polling) modunda olabilir; bu gecikme ve "
                                    "kaçan hareket demektir. dmesg'de i2c_hid uyarılarına bakın."))
            else:
                checks.append(Check(OK, "Kesme modunda çalışıyor",
                                    f"IRQ {irq} ({device.irq_line(name)}), {count} kesme"))
            sleepy = [(p, st) for p, st, ctl in pad.power_parents() if ctl == "auto"]
            if sleepy:
                st = {}
                if status:
                    for sp in status.get("pads", []):
                        if sp.get("node") == pad.node:
                            st = sp.get("stats", {})
                checks.append(i2c_sleep_check(st, script))
            elif pad.power_parents():
                checks.append(Check(OK, "I2C denetleyici uykusu kapalı", ""))

    if not os.access("/dev/uinput", os.R_OK | os.W_OK):
        checks.append(Check(INFO, "/dev/uinput erişimi yok",
                            "Kinetik kaydırma XTest ile (tık tık) çalışır; pürüzsüz kaydırma için "
                            "uinput erişimi gerekir."))

    for name, fix in running_conflicts():
        detail = "İki hareket programı aynı anda çalışırsa hareketler iki kez tetiklenir."
        if name == "Touchégg":
            detail = ("Touchégg'in 2/3 parmak dokunma eylemleri libinput'un kendi dokunmasıyla "
                      "birleşince her dokunuş çift tık olur; 2 parmak sıkıştırmayı da Xorg'dan ayrı "
                      "yorumlar. Hareketlerini PadForge'a aktarıp Touchégg'i kapatın "
                      "(geri almak: padforge enable-touchegg).")
        checks.append(Check(WARN, f"{name} çalışıyor", detail, fix=fix))

    tap = cfg["tap"]
    if tap["enabled"] and tap["engine"] == "libinput" and tap["drag"]:
        checks.append(Check(INFO, "Dokunma tıklamaları ~180 ms gecikiyor",
                            "libinput dokun-sürükle açıkken her tıklamanın bırakılmasını 180 ms "
                            "bekletir. PadForge'un gecikmesiz dokunma motorunu açın."))
    if cfg["typing"]["disable_while_typing"]:
        checks.append(Check(INFO, "Yazarken touchpad kilitleniyor",
                            "libinput her tuştan sonra touchpad'i 0,2–0,5 sn kapatır. Yazdıktan hemen "
                            "sonra imleç tepkisizse bunu kapatın; PadForge dokunmalar için ayrıca "
                            f"{tap['typing_guard_ms']} ms koruma uygular."))

    if status is None:
        checks.append(Check(WARN, "PadForge servisi çalışmıyor",
                            "Ayarlar kalıcı uygulanmıyor ve hareketler çalışmıyor.",
                            fix="systemctl --user enable --now padforge.service"))
    else:
        checks.append(Check(OK, "PadForge servisi çalışıyor",
                            f"sürüm {status.get('version')}, pid {status.get('pid')}"))
        for sp in status.get("pads", []):
            st = sp.get("stats", {})
            rate = st.get("rate_hz")
            if rate:
                level = OK if rate >= 100 else WARN
                jitter, gaps = st.get("interval_jitter_ms"), st.get("gap_percent")
                extra = f", sapma {jitter:.2f} ms" if jitter is not None else ""
                if gaps is not None:
                    extra += f", kaçan/geciken rapor %{gaps:.1f}"
                    if gaps > 5:
                        level = WARN
                checks.append(Check(level, f"Rapor hızı {rate:.0f} Hz",
                                    f"{sp.get('name')}: ölçülen{extra}"))
            noise = st.get("noise_mm")
            if noise is not None:
                level = OK if noise < 0.08 else WARN
                checks.append(Check(level, f"Duran parmak titreşimi {noise:.3f} mm",
                                    "Yüksekse imleç kendi kendine kıpırdar."))
        for err in status.get("errors", [])[-3:]:
            checks.append(Check(WARN, "Servis uyarısı", err))
    if xprops is not None and not xprops:
        checks.append(Check(WARN, "libinput X sürücüsü görünmüyor",
                            "xserver-xorg-input-libinput kurulu olmalı (synaptics değil)."))
    return checks


def format_checks(checks: list[Check]) -> str:
    icon = {OK: "✔", INFO: "ℹ", WARN: "⚠", ERROR: "✖"}
    lines = []
    for c in checks:
        lines.append(f"{icon[c.level]} {c.title}")
        if c.detail:
            lines.append(f"    {c.detail}")
        if c.fix:
            lines.append(f"    düzeltme: {c.fix}")
    return "\n".join(lines)
