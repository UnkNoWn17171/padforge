#!/bin/bash
# PadForge — sistem tarafı kurulum (root gerekir).
#
#   sudo ./install-udev.sh               touchpad okuma izni (zorunlu)
#   sudo ./install-udev.sh --power-fix   + I2C denetleyici uykusunu kapat (ilk dokunuş gecikmesi)
#   sudo ./install-udev.sh --remove      hepsini geri al
#
# Ne yapar:
#   70-padforge.rules       aktif masaüstü oturumundaki kullanıcıya touchpad'in ham
#                           dokunma olaylarını okuma izni verir (uaccess ACL). Klavye
#                           olaylarına izin VERMEZ; yalnız ID_INPUT_TOUCHPAD=1 cihazlar.
#   71-padforge-power.rules touchpad'in bağlı olduğu I2C denetleyicisinin runtime
#                           uykusunu kapatır (power/control=on).
set -euo pipefail

RULE=/etc/udev/rules.d/70-padforge.rules
POWER_RULE=/etc/udev/rules.d/71-padforge-power.rules

if [ "$(id -u)" -ne 0 ]; then
    echo "Root gerekli: sudo $0 $*" >&2
    exit 1
fi

retrigger() {
    udevadm control --reload-rules
    udevadm trigger --action=change --subsystem-match=input --property-match=ID_INPUT_TOUCHPAD=1
    udevadm settle --timeout=5 || true
}

# Touchpad'in I2C/PCI ebeveynlerini bul (ör. i2c_designware.0 ve 0000:00:15.0).
find_power_parents() {
    local node sysdev
    for node in /sys/class/input/event*; do
        if udevadm info -q property -p "$node" 2>/dev/null | grep -qx 'ID_INPUT_TOUCHPAD=1'; then
            sysdev=$(readlink -f "$node/device")
            case "$sysdev" in
                */i2c-*) ;;
                *) continue ;;   # yalnız I2C touchpad'ler (USB/Bluetooth değil)
            esac
            local p=$sysdev
            while [ "$p" != "/sys/devices" ] && [ "$p" != "/" ]; do
                p=$(dirname "$p")
                local name sub
                name=$(basename "$p")
                [ -e "$p/subsystem" ] || continue
                sub=$(basename "$(readlink -f "$p/subsystem")")
                if [ "$sub" = "platform" ] && [[ "$name" == i2c_designware* ]]; then
                    echo "platform $name $p"
                elif [ "$sub" = "pci" ]; then
                    echo "pci $name $p"
                    break
                fi
            done
        fi
    done | sort -u
}

if [ "${1:-}" = "--remove" ]; then
    rm -f "$RULE" "$POWER_RULE"
    retrigger
    for line in $(find_power_parents | awk '{print $3}'); do
        echo auto > "$line/power/control" 2>/dev/null || true
    done
    echo "PadForge udev kuralları kaldırıldı."
    exit 0
fi

cat > "$RULE" <<'EOF'
# PadForge: aktif oturum kullanıcısına touchpad ham olaylarını okuma izni (ACL).
# Geri almak için: sudo rm /etc/udev/rules.d/70-padforge.rules && sudo udevadm control --reload-rules
ACTION!="remove", SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHPAD}=="1", TAG+="uaccess"
EOF
echo "Yazıldı: $RULE"

if [ "${1:-}" = "--power-fix" ]; then
    {
        echo "# PadForge: touchpad I2C denetleyicisinin runtime uykusunu kapat (ilk dokunuş gecikmesi)."
        echo "# Geri almak için: sudo rm $POWER_RULE  (ve yeniden başlat)"
        find_power_parents | while read -r sub name path; do
            echo "ACTION==\"add|bind\", SUBSYSTEM==\"$sub\", KERNEL==\"$name\", ATTR{power/control}=\"on\""
            echo on > "$path/power/control"
            echo "  $sub $name -> power/control=on" >&2
        done
    } > "$POWER_RULE"
    echo "Yazıldı: $POWER_RULE"
fi

retrigger

user=${SUDO_USER:-}
if [ -n "$user" ]; then
    for node in /dev/input/event*; do
        if udevadm info -q property -n "$node" 2>/dev/null | grep -qx 'ID_INPUT_TOUCHPAD=1'; then
            if getfacl -p "$node" 2>/dev/null | grep -q "^user:$user:r"; then
                echo "OK  $node okunabilir ($user)"
            else
                echo "UYARI  $node için ACL görünmüyor; oturumu kapatıp açmayı dene." >&2
            fi
        fi
    done
fi
