#!/bin/bash
# PadForge — kaldırma. Ayarlar (~/.config/padforge) korunur; silmek için --purge.
set -uo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/padforge.service"

# Servis durunca libinput dokunmasını geri açar (daemon'un temizliği).
systemctl --user disable --now padforge.service 2>/dev/null
rm -f "$UNIT"
systemctl --user daemon-reload 2>/dev/null

rm -rf "$DATA/padforge"
rm -f "$HOME/.local/bin/padforge" \
      "$DATA/applications/io.github.padforge.PadForge.desktop" \
      "$DATA/icons/hicolor/scalable/apps/io.github.padforge.PadForge.svg"
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true

if [ "${1:-}" = "--purge" ]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/padforge" "${XDG_CACHE_HOME:-$HOME/.cache}/padforge"
    echo "Ayarlar da silindi."
fi
echo "PadForge kaldırıldı."
if [ -e /etc/udev/rules.d/70-padforge.rules ] || [ -e /etc/udev/rules.d/71-padforge-power.rules ]; then
    echo "Sistem kurallarını da kaldırmak için:"
    echo "  sudo rm -f /etc/udev/rules.d/70-padforge.rules /etc/udev/rules.d/71-padforge-power.rules"
    echo "  sudo udevadm control --reload-rules"
fi
