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
NOISE_WARN_MM = 0.25        # measured resting noise here: 0.12-0.14 mm (<1 px of cursor)

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
        return Check(OK, "Touchpad yongasının uykusu sorun değil",
                     f"{n} dokunuşta ilk bilgi en çok {wake_max:.1f} ms gecikti; sorun yok.")
    detail = ("Uyanması ilk dokunuşu geciktirebilir."
              + (f" Ölçülen en kötü gecikme {wake_max:.1f} ms ({n} dokunuş)."
                 if measured else " Touchpad'i biraz kullanınca ölçülür."))
    level = WARN if measured and wake_max >= WAKE_OK_MS else INFO
    return Check(level, "Touchpad yongası boşta uyuyor", detail,
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
        checks.append(Check(ERROR, "Wayland oturumu desteklenmiyor",
                            "PadForge şimdilik yalnızca X11 (Xorg) oturumunda çalışır."))
    elif os.environ.get("DISPLAY"):
        checks.append(Check(OK, "Masaüstü oturumu uygun", "X11 oturumu"))

    pads = device.list_touchpads()
    if not pads:
        checks.append(Check(ERROR, "Touchpad bulunamadı",
                            "Sistem hiçbir aygıtı touchpad olarak tanımıyor."))
        return checks
    script = helper_script()
    for pad in pads:
        caps = [("dahili" if pad.is_internal else "harici"),
                f"{pad.width_mm:.0f}×{pad.height_mm:.0f} mm", f"{pad.max_fingers} parmağa kadar"]
        if not pad.has_pressure:
            caps.append("basınç algılamaz")
        checks.append(Check(OK, f"{pad.name}", " · ".join(caps)))
        if not pad.readable:
            checks.append(Check(ERROR, "Touchpad erişimi kurulmamış",
                                "Hareketler, anında dokunma, süzülme ve akıllı hız eğrisi için bir "
                                "kez yönetici izni gerekir.",
                                fix=f"sudo bash {script}"))
        if pad.bustype == 0x18:
            name = pad.sysfs.split("/i2c-")[-1].split("/")[0] if "/i2c-" in pad.sysfs else ""
            irq, count = device.irq_count(name) if name else (None, 0)
            if irq is None:
                checks.append(Check(WARN, "Touchpad yavaş modda olabilir",
                                    "Kesme bulunamadı; touchpad sorgulanarak okunuyor olabilir. "
                                    "Bu gecikme ve kaçan hareket demektir."))
            else:
                checks.append(Check(OK, "Touchpad hızlı modda",
                                    f"Her dokunuşu anında bildiriyor (kesme {irq})."))
            sleepy = [(p, st) for p, st, ctl in pad.power_parents() if ctl == "auto"]
            if sleepy:
                st = {}
                if status:
                    for sp in status.get("pads", []):
                        if sp.get("node") == pad.node:
                            st = sp.get("stats", {})
                checks.append(i2c_sleep_check(st, script))
            elif pad.power_parents():
                checks.append(Check(OK, "Touchpad yongası uyumuyor",
                                    "İlk dokunuşta bekleme olmaz."))

    if not os.access("/dev/uinput", os.R_OK | os.W_OK):
        checks.append(Check(INFO, "Pürüzsüz süzülme için izin yok",
                            "Süzülme yine çalışır ama kademeli kayar."))

    for name, fix in running_conflicts():
        detail = "İki hareket programı birlikte çalışırsa her hareket iki kez olur."
        if name == "Touchégg":
            detail = ("Touchégg ile PadForge aynı işi yapıyor; dokunuşlar çift tık olabilir. "
                      "Hareketlerini PadForge'a aktarıp Touchégg'i kapat (geri alınabilir).")
        checks.append(Check(WARN, f"{name} çalışıyor", detail, fix=fix))

    tap = cfg["tap"]
    if tap["enabled"] and tap["engine"] == "libinput" and tap["drag"]:
        checks.append(Check(INFO, "Dokunarak tıklama yaklaşık 0,2 sn gecikiyor",
                            "Dokunma › Tepki ayarında 'Anında'yı seç."))
    if cfg["typing"]["disable_while_typing"]:
        checks.append(Check(INFO, "Yazarken touchpad kısa süre kilitlenir",
                            "Her tuştan sonra 0,2–0,5 sn. Yazdıktan hemen sonra imleç tepkisizse "
                            "Dokunma › Yazarken ayarını kapat."))

    if status is None:
        checks.append(Check(WARN, "PadForge servisi kapalı",
                            "Hareketler, anında dokunma ve süzülme çalışmıyor.",
                            fix="systemctl --user enable --now padforge.service"))
    else:
        checks.append(Check(OK, "PadForge servisi çalışıyor", f"sürüm {status.get('version')}"))
        for sp in status.get("pads", []):
            st = sp.get("stats", {})
            rate = st.get("rate_hz")
            if rate:
                level = OK if rate >= 100 else WARN
                gaps = st.get("gap_percent")
                extra = f" Kaçan bildirim %{gaps:.1f}." if gaps is not None else ""
                if gaps is not None and gaps > 5:
                    level = WARN
                checks.append(Check(level, f"Okuma hızı {rate:.0f} Hz",
                                    f"Touchpad saniyede {rate:.0f} kez konum bildiriyor.{extra}"))
            noise = st.get("noise_mm")
            if noise is not None:
                level = OK if noise < NOISE_WARN_MM else WARN
                checks.append(Check(level, f"Titreme {noise:.2f} mm",
                                    "Parmak dururken çok az oynuyor, sorun yok." if level == OK
                                    else "Parmak dururken imleç kendi kendine kıpırdayabilir."))
        for err in status.get("errors", [])[-3:]:
            checks.append(Check(WARN, "Servis uyarısı", err))
    if xprops is not None and not xprops:
        checks.append(Check(WARN, "Touchpad sürücüsü eksik",
                            "xserver-xorg-input-libinput paketi gerekli."))
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
