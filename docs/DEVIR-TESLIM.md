# PadForge — devir teslim (2026-10-19)

Etiketler: **ÖLÇÜLDÜ** (bu makinede ölçüldü/denendi) · **ÇIKARIM** (ölçümden çıkarıldı) ·
**BİLİNMİYOR** (doğrulanmadı).

## 1. Nerede ne var

- Repo: `github.com/UnkNoWn17171/padforge` — **özel (private)**, dal `main`.
  Yeni makinede: `gh repo clone UnkNoWn17171/padforge ~/projeler/padforge`
- Kurulu kopya (repo değil, `install.sh` kopyalar):
  - `~/.local/share/padforge/lib` — çalışan kod
  - `~/.local/bin/padforge` — komut / arayüz
  - `~/.config/systemd/user/padforge.service` — servis (açılışta başlar)
  - `~/.config/padforge/config.json` — ayarlar · yedek: `config.json.yedek-2134`
    (önerilen düzen uygulanmadan önceki hâl)
- Sistem dosyaları (sudo ile kuruldu):
  - `/etc/udev/rules.d/70-padforge.rules` — touchpad ham verisine okuma izni (yalnızca touchpad)
  - `/etc/udev/rules.d/71-padforge-power.rules` — I2C denetleyici uykusu kapalı (`--power-fix`)
- Touchégg: kullanıcı istemcisi kapalı (`~/.config/autostart/touchegg.desktop`, `Hidden=true`).
  Kök daemon paket olarak duruyor, zararsız.

## 2. Şu anki durum (ÖLÇÜLDÜ, 2026-10-19)

- Servis: `active`, `enabled`; touchpad okunuyor (`/dev/input/event5`, X id 14), 143 Hz.
- Sanal işaretçide basılı kalan tuş yok (`button[1..3]=up`).
- Birim testleri: 232 geçti (17 atlandı = X/arayüz isteyen testler); gerçek Xorg 10/10,
  arayüz 7/7 (ekransız: `gtk4-broadwayd`), `padforge selftest` BAŞARILI (2026-10-19).
- Son iş: `fix/hata-ayiklama-super` dalı (bölüm 6b), kurulu olan bu dal; `main` = `4a8d6e0`.

## 3. Aktif ayarlar

| Bölüm | Değer |
|---|---|
| İmleç | özel eğri "Windows gibi", hız 1.0 |
| Dokunma | PadForge motoru, dokun-sürükle açık, tık ≤180 ms ve ≤1.5 mm, yazarken koruma 250 ms |
| Tıklama | alt bölge düğmeleri (buttonareas) |
| Kaydırma | doğal, hız 1.0, ivme 0.3, kinetik açık (sürtünme 2.6) |
| 3 parmak yukarı | Super tuşu bas-bırak (`super_key`; GNOME'da Etkinlikler) — 2026-10-19 eklendi |
| 3 parmak aşağı/sol/sağ | kendi Super+tekerlek menün (Super Scroll Switch) — her 4.5 mm bir pencere |
| 4 parmak | sol/sağ = çalışma alanı, yukarı = genel bakış, aşağı = masaüstü |
| 2 parmak sıkıştırma | yakınlaştırma |
| Pencere | başlığa dokun + hemen dokunup kaydır = taşı · Super + 1 parmak = taşı · Super + 3 parmak = boyutlandır |
| Pil | 3+ parmak değince 8 sn "performans" profili (boost) |
| Kapalı | 3/4 parmak sıkıştırma, 4 parmak dokunma, 3 parmak sürükleme |

Kullanıcı onayı (ÖLÇÜLDÜ, gerçek kullanım): 3 parmak menü hassasiyeti, başlıktan taşıma,
Super ile taşıma/boyutlandırma, pilde akıcılık — hepsi "iyi çalışıyor".

## 4. Ölçüm özeti

- Donanım: ELAN0303 04F3:31FF, I2C-HID, 118×72 mm, IRQ modu, basınç yok, MSC_TIMESTAMP var.
- Rapor hızı 143 Hz sabit, boşluk ~%0 · çekirdek→servis 0.14 ms · ilk rapor gecikmesi ≤0.8 ms
  (power-fix açıkken; fix gerekli miydi → **BİLİNMİYOR**) · duran parmak titreşimi 0.12–0.14 mm.
- Eğri: libinput çıktısı tasarımın ±%3'ünde (`padforge selftest`) · gerçek kullanımda oran medyanı 1.03.
- Hareketler: 25/25 gerçek kaydırma doğru, 0 yanlış sıkıştırma; sekme (bounce) dokunuşları bastırıldı.
- Pilde akıcılık: boost kapalı %89.1 → açık %97.7 (genel bakışta atlanan kare medyanı 14 → 0).
- Canlı GNOME: başlıktan taşıma, Super+sürükleme, Super+orta tuş boyutlandırma doğru; genel bakış yanlışlıkla açılmadı.

## 5. Mimari (kısa)

- `mt.py` evdev çok-dokunuş → mm/zaman damgalı kareler; kenarda dinlenen başparmak "park" edilir.
- `gestures.py` kaydırma/sıkıştırma tanıma · `tap.py` gecikmesiz dokunma + başlık çubuğu taşıma ·
  `wmdrag.py` Super + parmakla taşıma/boyutlandırma · `kinetic.py` kinetik kaydırma.
- `actions.py` eylemler (XTest, EWMH, Super Scroll Switch D-Bus) · `boost.py` güç profili tutma.
- `xinput.py` libinput özellikleri (eğri, kaydırma) · `gnome.py` GNOME ayarlarıyla çift yönlü senkron.
- `daemon.py` hepsini bağlar; durum soketi `$XDG_RUNTIME_DIR/padforge/daemon.sock`.
- `gui/` GTK4 arayüz (mor-siyah tema `gui/theme.css`).

## 6. Olaylar ve dersler

- **"Bilgisayar dondu" (2026-10-18)**: 3 parmak sürüklerken ayar yenilenince sanal işaretçinin sol
  tuşu 2 dk basılı kaldı. Düzeltildi: yenilemede/parmak kalkınca bırak + 1 sn bekçi. Tekrar olursa:
  `xinput query-state "PadForge Virtual Pointer"` → `button[1]=down` ise
  `systemctl --user restart padforge.service`.
- Testlerde `XDG_CONFIG_HOME` değiştirme: gerçek dconf gizleniyor, imleç hızı bir kez 0'a düştü
  (geri yüklendi).
- ppd tutma hemen düşüyordu: sistem D-Bus bağlantısı referanssız kalınca kapanıyor → modül düzeyinde tutuluyor.
- python-evdev tek okumada ≤64 olay döndürüyor → `frames()` tampon boşalana kadar okuyor.
- Her zaman `/usr/bin/python3` kullan (PATH'teki python `~/venv-ardupilot`).

## 6b. Hata ayıklama turu (2026-10-19, dal `fix/hata-ayiklama-super`)

- **Medya tuşları hiç çalışmıyordu** (ÖLÇÜLDÜ, kayıtta `bilinmeyen tuş: XF86AudioPlay`):
  python-xlib `XF86_AudioPlay` yazımını istiyor. Ses, parlaklık, oynat/duraklat, parça →
  `actions.keysym_for` iki yazımı da çözüyor; canlı ekranda tuş kodları doğrulandı.
- **`install.sh` yeni kodu yüklemiyordu**: `enable --now` çalışan servisi yeniden başlatmaz;
  artık `restart`.
- **X bağlantısı koparsa** (çıkış, Xorg çökmesi) ölü soket YÜKSEK öncelikli izleyiciyi
  sonsuz döngüye sokup SIGTERM'i aç bırakabiliyordu (çıkışı ~90 sn geciktirme riski)
  → servis temiz kapanıyor (ÇIKARIM + birim testi; gerçek X öldürme denenmedi).
- **Yarım yazılmış ayar dosyası**: yeniden yüklemede dosya `.broken` diye kenara
  atılıp varsayılana dönülüyordu → artık son iyi ayarla devam (`config.read`).
- **Açık arayüz eski kopyayı yazıyordu**: GNOME'dan benimsenen ya da dosyadan yapılan
  değişiklik, arayüzde bir sonraki ayarda eziliyordu → arayüz dosyayı izliyor.
- Küçükler: ikinci `padforge daemon` artık hiçbir şeye dokunmadan çıkıyor; kapanışta
  Super-sürükleme tuşu da bırakılıyor; touchpad ayrılınca açık pencere menüsü (Super)
  hemen bırakılıyor; SYN_DROPPED sonrası geçerli MT yuvası okunuyor.
- **Yeni**: `super_key` eylemi (Super bas-bırak). Önerilen düzen ve bu makinedeki ayar:
  3 parmak yukarı = Super (canlı: Etkinlikler açıldı/kapandı, ÖLÇÜLDÜ).
- "Logitech USB Receiver" da touchpad olarak açılıyor (Bolt alıcısının boş PTP arayüzü,
  0 kare) — zararsız.

## 7. Açık işler

- 4 parmak sıkıştırma gerçek parmakla hiç kaydedilmedi (kayıtta yalnız 4 parmak dokunma vardı)
  → **BİLİNMİYOR**; şu an zaten kapalı.
- Kinetik kaydırma uzunluğu tercihi sorulmadı → **BİLİNMİYOR**.
- NVIDIA ekran kartı pilde uyanık (~8 W, Xorg tutuyor) → PadForge dışı, ayrı iş.
- Wayland desteklenmiyor (yalnızca X11).
- Repo özel; açmak istersen:
  `gh repo edit UnkNoWn17171/padforge --visibility public --accept-visibility-change-consequences`

## 8. Komutlar

```bash
padforge                                  # arayüz
padforge doctor                           # teşhis
padforge selftest                         # sanal touchpad ile uçtan uca test
padforge status                           # servis durumu (JSON)
systemctl --user restart padforge.service # servisi yeniden başlat
journalctl --user -u padforge -n 50       # servis kaydı
```

Kod değişikliğinden sonra kurulu kopyayı güncelle: repo kökünde `./install.sh`
(ayarlar korunur).

Testler:

```bash
/usr/bin/python3 -m pytest tests
PADFORGE_INTEGRATION=1 /usr/bin/python3 -m pytest tests/integration   # gerçek Xorg, 10 test
Xephyr :7 -screen 1120x820 -nolisten tcp -ac -br &
PADFORGE_GUI_DISPLAY=:7 /usr/bin/python3 -m pytest tests/integration  # arayüz, 6 test
```

Ölçüm araçları (`tools/`):

- `tools/kayit.py SANİYE çıktı.jsonl [BEKLEME [PARMAK]]` — ham dokunuş + X kaydı; eşik ayarı için.
- `tools/kare_olcum.py [TUR]` — GNOME geçişlerinde atlanan kareler, boost kapalı/açık.
  Ekranda geçiş yapar, ayardaki boost'u değiştirip sonunda açık bırakır. Repoya yeniden
  yazıldı, bu sürüm henüz çalıştırılmadı (**BİLİNMİYOR**).

## 9. Geri alma

| Ne | Komut |
|---|---|
| Ayarları önerilen düzenden önceki hâle döndür | `cp ~/.config/padforge/config.json.yedek-2134 ~/.config/padforge/config.json` |
| Servisi geçici durdur (libinput kendi tıklamasına döner) | `systemctl --user stop padforge.service` |
| Tamamen kaldır (ayarlar kalır / `--purge` siler) | `~/.local/share/padforge/uninstall.sh` |
| udev kuralları + power-fix'i kaldır | `sudo bash ~/.local/share/padforge/install-udev.sh --remove` |
| Touchégg'i geri aç | `padforge enable-touchegg` |
