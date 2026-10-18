#!/bin/bash
# PadForge — kullanıcı kurulumu (sudo gerekmez).
#
#   ./install.sh             kur, servisi etkinleştir ve başlat
#   ./install.sh --no-start  kur ama servisi başlatma
#
# Kurulum kaynak klasöre bağlı kalmaz: dosyalar ~/.local/share/padforge altına kopyalanır.
# Ham dokunma verisi için ayrıca bir kez: sudo bash ~/.local/share/padforge/install-udev.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA/padforge"
BIN="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# Sistem Python'u: ~/.profile'daki sanal ortamlar (ör. ArduPilot venv) gi/evdev içermez.
PY=/usr/bin/python3
START=1
[ "${1:-}" = "--no-start" ] && START=0

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

if ! "$PY" - <<'EOF' 2>/dev/null
import gi, evdev, Xlib
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
EOF
then
    echo "Eksik paketler var. Kurmak için:" >&2
    echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-evdev python3-xlib" >&2
    exit 1
fi

say "Dosyalar kopyalanıyor → $PREFIX"
rm -rf "$PREFIX/lib"
mkdir -p "$PREFIX/lib" "$BIN" "$UNIT_DIR" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
cp -r "$SRC/padforge" "$PREFIX/lib/"
find "$PREFIX/lib" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 "$SRC/packaging/install-udev.sh" "$PREFIX/install-udev.sh"
install -m 755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"
install -m 644 "$SRC/packaging/icons/io.github.padforge.PadForge.svg" \
    "$DATA/icons/hicolor/scalable/apps/io.github.padforge.PadForge.svg"

cat > "$BIN/padforge" <<EOF
#!/bin/sh
# PadForge başlatıcısı (install.sh tarafından oluşturuldu)
PYTHONPATH="$PREFIX/lib\${PYTHONPATH:+:\$PYTHONPATH}" exec $PY -m padforge "\$@"
EOF
chmod 755 "$BIN/padforge"

sed -e "s|@BIN@|$BIN|g" "$SRC/packaging/io.github.padforge.PadForge.desktop" \
    > "$DATA/applications/io.github.padforge.PadForge.desktop"
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@PY@|$PY|g" "$SRC/packaging/padforge.service" \
    > "$UNIT_DIR/padforge.service"
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true

say "İlk ayar (mevcut GNOME ayarlarından)"
"$BIN/padforge" init

systemctl --user daemon-reload
if [ "$START" = 1 ]; then
    say "Servis etkinleştiriliyor"
    systemctl --user enable padforge.service
    # start değil restart: zaten çalışan servis eski kodu bellekte tutmaya devam ederdi
    systemctl --user restart padforge.service
    sleep 1
    systemctl --user --no-pager --lines=0 status padforge.service | head -3 || true
else
    systemctl --user enable padforge.service
fi

echo
say "Kuruldu. Uygulama menüsünde: PadForge   (komut satırı: padforge doctor)"
if ! "$PY" -c "import sys; sys.path.insert(0, '$PREFIX/lib'); from padforge import device; \
sys.exit(0 if all(p.readable for p in device.list_touchpads()) else 1)"; then
    echo
    echo "Hareketler ve gecikmesiz dokunma için bir kez yönetici olarak çalıştırın:"
    echo "  sudo bash $PREFIX/install-udev.sh"
fi
