# PadForge — devir teslim (2026-10-24)

Etiketler: **ÖLÇÜLDÜ** (bu makinede ölçüldü/denendi) · **ÇIKARIM** (ölçümden çıkarıldı) ·
**BİLİNMİYOR** (doğrulanmadı).

## 1. Nerede ne var

- Repo: `github.com/UnkNoWn17171/padforge` — **özel (private)**, dal `main`.
  Bu makinede yerel kopya: `~/projeler/padforge` (2026-10-19'dan beri; önceki kopya bir
  Claude oturumunun geçici klasöründeydi).
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

## 2. Şu anki durum (ÖLÇÜLDÜ, 2026-10-24 20:18)

- Servis: `active`, hatasız. Kurulu olan = `~/projeler/padforge` çalışma ağacı: `main`
  (`676ba2f`) **+ commit edilmemiş değişiklikler** (bölüm 6d; 13 dosya, +770/−63).
- Fare: **hiçbir tuşa görev atanmış değil**; X eşlemesi `1 2 3 … 20`, gizli/yakalanmış tuş
  yok, Super takılı değil. İleri/Geri tarayıcıda normal çalışıyor.
- Testler (çalışma ağacı): birim 262 geçti; gerçek Xorg 12/12; arayüz 12/12 (ekransız).
- **Fare İleri tuşu ÇALIŞIYOR (2026-10-24 ~21:10, kullanıcı onayı + kayıt, ÖLÇÜLDÜ):**
  İleri basılıyken tekerlek Super Scroll Switch menüsünü açıyor, bırakınca pencereye geçiyor.
  Yöntem Solaar'da (PadForge değil): İleri = "Diverted" + `~/.config/solaar/rules.yaml`
  (basılınca Super_L bas, bırakınca bırak). Kayıtta her basış: Super BASILDI → çentik başına
  bir `Step` → Super BIRAKILDI, gerçek tutuş 0,3–1 sn. Yan etki: İleri'ye tek dokunuş =
  Etkinlikler (tarayıcıda "ileri" yok). Yedek: `~/.config/solaar/config.yaml.yedek-padforge`.
  PadForge'da fare ataması yok; ayrıntı ve geri alma bölüm 6d'de.

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
- `mouse.py` + `device.list_mice()` harici fare tuşları (eşleme hesabı; daemon uygular).
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

## 6b. Hata ayıklama turu (2026-10-19, `main`'e alındı)

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
  değişiklik, arayüzde bir sonraki ayarda (ya da kapatınca) eziliyordu → arayüz dosyayı
  izliyor, kapanışta yalnız bekleyen düzenlemeyi yazıyor.
- Küçükler: ikinci `padforge daemon` artık hiçbir şeye dokunmadan çıkıyor; kapanışta
  Super-sürükleme tuşu da bırakılıyor; touchpad ayrılınca açık pencere menüsü (Super)
  hemen bırakılıyor; SYN_DROPPED sonrası geçerli MT yuvası okunuyor.
- **Yeni**: `super_key` eylemi (Super bas-bırak). Önerilen düzen ve bu makinedeki ayar:
  3 parmak yukarı = Super (canlı: Etkinlikler açıldı/kapandı, ÖLÇÜLDÜ).
- "Logitech USB Receiver" da touchpad olarak açılıyor (Bolt alıcısının boş PTP arayüzü,
  0 kare) — zararsız.

## 6c. Fare tuşları (2026-10-19, `main`'e alındı)

- Fare: **Logitech Signature M650 L** (Bolt alıcısı, X'te "Logitech USB Receiver Mouse"),
  tuşlar: sol, sağ, orta (2), Geri (8), İleri (9). Alıcı 16 tuş bildiriyor (çoğu yok):
  gerçek tuşlar basıldıkça öğrenilip `~/.cache/padforge/devices.json`'a yazılıyor.
- Yöntem (ÖLÇÜLDÜ, entegrasyon testi var): görev atanan tuşun o faredeki X eşlemesi 0 →
  uygulamalar görmez, XI2 ham olay fiziksel numarayla gelir → daemon eylemi çalıştırır.
  Yakalama/izin/sudo yok. Yalnız USB/Bluetooth fiziksel fareler (sanal ve dahili hariç).
- Servis durunca tuşlar geri verilir; çökme artığı sonraki başlangıçta onarılır
  (`zeroed` kaydı). Elle geri: `xinput set-button-map "Logitech USB Receiver Mouse" $(seq 1 20)`.
- Arayüz › Fare: bağlı fare, tuşa bas → satırı belirir, görev seç, ▶ ile dene. Görev hemen
  geçerli; sayfa açıkken de çalışır (ilk sürüm sayfa açıkken bekletiyordu: kullanıcı "ayar
  çalışmıyor" sandı, 2026-10-19). Yeni fare ilk görülünce bir kez bildirim.

## 6d. Fare tuşunu basılı tutma — ÇÖZÜLDÜ (Solaar ile, 2026-10-24 21:10)

**Kök neden (ÖLÇÜLDÜ, USB ham raporu `/dev/hidraw3` + X aynı anda, 5 dk kayıt):** kullanıcı
İleri'yi 3 kez ~1 sn tuttu; **fare her seferinde "basıldı" (`0x10`) raporundan ~10 ms sonra
"bırakıldı" (`0x00`) gönderdi**, X de 30–40 ms'lik tık gördü (tuş 9, aygıt 9). Yani M650'nin
yazılımı İleri'yi (Solaar'da "Mouse Forward Button" eylemi, "Regular") **anlık tık** olarak
bildiriyor; bilgisayar açısından tuş hiç basılı tutulmuyor. Tarayıcıda "ileri" bu yüzden
çalışıyor, PadForge'un hiçbir basılı-tutma yöntemi bu yüzden çalışamadı. **Sorun PadForge'da değil.**

**Çözüm (UYGULANDI, kullanıcı "çalışıyor" dedi, kayıtla doğrulandı):** tuşu Solaar'da "Diverted" yap; fare o zaman basma
ve bırakmayı HID++ bildirimleriyle ayrı ayrı yollar (M650: "divertable, raw XY"). Solaar
1.1.11 kural motoru `Key: [Forward Button, pressed|released]` ve
`KeyPress: [Super_L, depress|release]` destekliyor (`/usr/share/solaar/lib/logitech_receiver/diversion.py`).
```bash
cp ~/.config/solaar/config.yaml ~/.config/solaar/config.yaml.yedek-padforge
solaar config "M650" divert-keys "Forward Button" Diverted
```
`~/.config/solaar/rules.yaml`:
```yaml
%YAML 1.3
---
- Key: [Forward Button, pressed]
- KeyPress: [Super_L, depress]
...
---
- Key: [Forward Button, released]
- KeyPress: [Super_L, release]
...
```
Sonra Solaar'ı yeniden başlat (`pkill -f "solaar --window=hide"; setsid -f solaar --window=hide`).
Sonuç: İleri basılıyken Super tutulur → tekerlek kullanıcının Super+tekerlek menüsüne gider,
bırakınca pencere seçilir; tek dokunuş = Super = Etkinlikler (tarayıcıda "ileri" kalkar).
Geri alma: `solaar config "M650" divert-keys "Forward Button" Regular`, `rules.yaml`'ı sil,
Solaar'ı yeniden başlat. Alternatif (daha çok iş): PadForge'un `/dev/hidraw4`'ten HID++
"diverted buttons" bildirimini okuyup kendi `gesture`/`hold_*` modlarını onunla sürmesi.

### Geçmiş (aşağıdakiler kök neden bulunmadan önceki iz)

**Hedef (kullanıcının sözü):** İleri tuşu basılıyken tekerlek çevirince Super Scroll Switch
pencere menüsü (Super+tekerlek ile yaptığı hızlı pencere değiştirme); ayrıca "yaratıcı"
basılı-tutma kısayolları.

**Yazılan kod (çalışma ağacında, testli):**
- Fare tuşu modları (`mouse.py`, `daemon.py`, arayüz › Fare):
  - `gesture` = hareket tuşu: o tuşa XI2 **pasif grab** (`xinput.grab_button`); basınca fare
    PadForge'a geçer, `mouse.GestureHold` dokunma / basılı + tekerlek (sürekli eylem: begin/
    step/end) / basılı + 4 yön itme ayırır; bırakınca imleç başlangıca geri konur.
  - `hold_super|ctrl|shift|alt`: tuş eşlemesi 0 (gizli), ham basışta XTest ile tuşa bas,
    ham bırakışta bırak. Tekerlek o zaman kullanıcının kendi Super+tekerlek yakalayıcısına gider.
- Sürekli `workspace` eylemi; başlangıçta XTest'te takılı kalmış Super/Ctrl/Shift/Alt bırakılır;
  bekçi 30 sn'den uzun tutuşu bitirir; basılı tuşun eşlemesi asla değiştirilmez.
- Tanı kayıtları (`journalctl --user -u padforge`): `fare hareket tuşu basıldı/bırakıldı: …
  sn · tekerlek N çentik · hareket px · olaylar: M+15ms W5+120ms …`; ≥8 numaralı tuşlarda
  `fare: … fiziksel olarak N ms basılı kaldı` (ve "PadForge'un tutuşu X ms önce bitmişti").
- python-xlib `ButtonState` ayrıştırıcısı yamalandı (aşağıdaki "hayalet tuş" olayı).

**Ölçülenler:**
- Sanal farede (uinput, gerçek Xorg) her iki yöntem çalışıyor; entegrasyon testleri:
  `test_hidden_mouse_button_still_reported_raw`, `test_gesture_button_grab_hands_the_mouse_over_and_back`. ÖLÇÜLDÜ.
- Yalnız tekerlek tuşlarını (4/5) pasif yakalamak yetmiyor: GTK'nın akıcı kaydırması uygulamaya
  yine gidiyor; tüm aygıtı almak gerekiyor. ÖLÇÜLDÜ. (Kullanıcının Super Scroll Switch
  yakalayıcısında da arkadaki pencere kayıyor olabilir — ÇIKARIM.)
- XTest ile Super basılıyken tekerlek → yakalayıcı `Step(+1)` gönderdi (dbus-monitor),
  bırakınca Etkinlikler açılmadı. ÖLÇÜLDÜ. Menü yalnız imlecin olduğu monitördeki
  pencereleri gösterir (HDMI-1'de tek pencere vardı → değişiklik görünmedi).
- **Gerçek farede (M650, X id 9):**
  - 2026-10-24 20:03, `gesture`: iki basış; tutuş 40 ms ve 48 ms'de bitti, 0 tekerlek
    çentiği; birinde ayrılan imleç 320 px sıçradı ve sahte "sola it" çalıştı (düzeltildi:
    yön ilk hareketten ölçülüyor). Kullanıcı tuşu "tuttum" diyor.
  - 20:09:37'den (yeni servis) ve 20:16'dan (`hold_super`) sonraki denemelerde kayıtta
    İleri/Geri'den **hiç olay yok** (ne grab ne ham basış/bırakış satırı).
  - 2026-10-19 18:56:16'da aynı `hold_super` yolu ham basışı görmüştü.
  - Eş zamanlı ölçüm 3 kez denendi; ölçüm penceresinde yan tuşa basılmadı (yalnız sol/sağ/tekerlek).

- **2026-10-24 ~20:20, üç ölçüm (kullanıcı "İleri'yi tuttum" dedi):** X fare aygıtı 60 sn
  (tekerlek + sol tık geldi), X'te tüm Logitech aygıtları 45 sn (yalnız sol tık), USB alıcısının
  ham raporları `/dev/hidraw3` 45 sn / 1259 rapor (yalnız sol tık, hareket) — **İleri/Geri hiçbir
  seviyede yok**. Ya pencerelerde basılmadı ya da fare İleri'yi göndermiyor. Sudo'suz ham
  okuyucu: `/usr/bin/python3 <betik> /dev/hidraw3 45` (rapor `02 BB …`, BB = tuş baytı:
  bit0 sol, bit1 sağ, bit2 orta, bit3 geri, bit4 ileri). PadForge tarafında aranacak bir şey kalmadı.

**Bilinmeyenler (BİLİNMİYOR):** (1) İleri basılı tutulduğunda X'e gerçekten ne geliyor
(basma/bırakma zamanı)? (2) Gerçek aygıtta pasif grab neden 40 ms'de bitti? (3) 20:09 sonrası
servis neden yan tuştan hiç ham olay almadı — kullanıcı farklı tuşa mı bastı, yoksa servis
ham olay almıyor mu (son değişikliklerde gerileme olabilir: `_raw_down`, `held` taşıma)?

**Sıradaki adım (önce ölç, sonra kodla):**
1. Kullanıcı terminalde kendisi çalıştırsın, 15 sn içinde İleri'yi 2 sn tutup tekerleği
   çevirsin (9 = `xinput list --id-only "Logitech USB Receiver Mouse"`; çıplak
   `xinput test-xi2` hareket olaylarıyla taşıyor, 2026-10-24 denendi, işe yaramadı):
   ```bash
   timeout 15 stdbuf -oL xinput test-xi2 --root 9 | awk '/RawButton(Press|Release)/{e=($4~/Press/)?"BAS":"BIRAK"} /time:/{t=$2} /detail:/{if(e){print t" ms  "e"  tuş "$2; e=""}}'
   ```
   Bakılacak: `BAS tuş 9` → `BIRAK tuş 9` arası süre; arada `tuş 4/5` (tekerlek) var mı.
2. Basma/bırakma doğru geliyorsa sorun serviste: `systemctl --user stop padforge.service`,
   sonra `PYTHONPATH=~/.local/share/padforge/lib /usr/bin/python3 -m padforge daemon -v`
   ile önde çalıştır, Fare › İleri = `hold_super` yap, aynı denemeyi canlı izle.
3. Bırakma 40 ms'de geliyorsa sorun donanım/sürücüde: `sudo evtest` ile doğrula (şifreyi kullanıcı girer).
- Hızlı alternatif (çalıştığı ölçülen tek yol): İleri'ye tek basış görevi ver (ör. Geri'de
  `super_key`, 2026-10-19 kullanıcı "çalışıyor" dedi). Basılı tutma yok.

**"Hayalet 0. tuş" olayı (2026-10-19, benim deneyim):** gizli (0) tuş basılıyken eşlemesi
değişince Xorg ana işaretçide "0. tuş basılı" bıraktı → python-xlib her aygıt sorgusunda
`AssertionError` → servisin taraması düştü (journalctl'de `zamanlayıcı rescan`). Temizleme:
sanal farede bir tuşu `xinput set-button-map ID 1 2 3 4 5 6 7 0` ile 0 yapıp bas-bırak.
Artık servis bundan etkilenmiyor (yamalı ayrıştırıcı + basılı tuşu yeniden eşlememe).

## 7. Açık işler

- Fare İleri + tekerlek: Solaar ile çözüldü (6d). PadForge'un `gesture`/`hold_*` modları bu
  farenin "Regular" İleri'siyle çalışamaz (fare anlık tık gönderiyor); gerçek basılı tutma
  bildiren fareler için kodda duruyor. İleride: PadForge `/dev/hidraw4`'ten HID++ "diverted
  buttons" bildirimini okursa dokunma = ileri + basılı = menü birlikte olabilir (BİLİNMİYOR).

- 4 parmak sıkıştırma gerçek parmakla hiç kaydedilmedi (kayıtta yalnız 4 parmak dokunma vardı)
  → **BİLİNMİYOR**; şu an zaten kapalı.
- Kinetik kaydırma uzunluğu tercihi sorulmadı → **BİLİNMİYOR**.
- NVIDIA ekran kartı pilde uyanık (~8 W, Xorg tutuyor) → PadForge dışı, ayrı iş.
- GNOME'un bir süre aradan sonraki ilk bildirimi X'i kilitliyor: 2,2 sn, sonra 0,36 sn,
  sonra 2 ms (ÖLÇÜLDÜ 2026-10-19, X gidiş-dönüş süresi) → PadForge dışı; bu yüzden
  "Fare bulundu" bildirimi başlangıçtan 5 sn sonraya ertelendi.
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
PADFORGE_INTEGRATION=1 /usr/bin/python3 -m pytest tests/integration   # gerçek Xorg, 11 test
gtk4-broadwayd :9 &      # ekransız, pencere açmaz (Xephyr :7 de olur)
GDK_BACKEND=broadway BROADWAY_DISPLAY=:9 PADFORGE_GUI_DISPLAY=:9 \
    /usr/bin/python3 -m pytest tests/integration/test_gui.py      # arayüz, 10 test
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
| Commit edilmemiş fare-tutma kodunu kaldır, `main`'i kur | `cd ~/projeler/padforge && git stash -u && ./install.sh` (geri: `git stash pop && ./install.sh`) |
| Fare tuşlarını elle normale döndür | `xinput set-button-map "Logitech USB Receiver Mouse" $(seq 1 20)` |
| Ayar yedekleri (2026-10-24) | `~/.config/padforge/config.json.yedek-1024`, `-1024b`, `-1024c` |
