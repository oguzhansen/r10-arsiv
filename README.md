# R10 Konu Takip

R10.net arşivinde seçtiğiniz kategorileri ve anahtar kelimeleri izleyen, yeni konularda **Telegram** bildirimi gönderen self-hosted web paneli.

## Özellikler

- R10 arşivden kategori listesi (Cloudflare uyumlu `curl_cffi`)
- Aktif kategorilerin **1. sayfası** paralel tarama (~60 sn aralık)
- Yeni konu + kelime eşleşmesinde **anında Telegram**
- Aynı konu birden fazla kelimeye uyarsa **tek bildirim / tek log satırı**
- Kategori grubunda **Tümünü seç / Tümünü kaldır**
- Virgülle toplu anahtar kelime ekleme
- İlk kurulumda yönetici hesabı, sonrasında giriş koruması

## Gereksinimler

- Python 3.10+
- İnternet (R10 + `api.telegram.org`)

## Hızlı başlangıç (geliştirme)

```bash
git clone https://github.com/KULLANICI/r10-arsiv.git
cd r10-arsiv
python -m venv venv
# Windows: venv\Scripts\activate
# Linux: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env içinde SECRET_KEY değiştirin
python app.py
```

Tarayıcı: `http://127.0.0.1:5000` → **İlk kurulum** (kullanıcı adı + şifre) → Giriş.

### Panel sırası

1. **Ayarlar** — Telegram bot token + chat ID (grup için negatif ID)
2. **Dashboard** — R10'dan Güncelle → kategorileri seç → kelimeleri ekle
3. **Taramayı Başlat** — baseline (sessiz), sonra yeni konular için bildirim

## Telegram

| Hedef | Chat ID |
|--------|---------|
| Kişisel | Pozitif sayı (`@userinfobot`) |
| Grup | Negatif sayı (`getUpdates` → `"type":"group"`) |
| Kanal | `@kanaladi` veya `-100...` (bot yönetici olmalı) |

Test: Ayarlar → **Test Mesajı Gönder**

## Ubuntu VPS kurulumu

```bash
apt update && apt install -y python3 python3-venv git ufw
mkdir -p /opt/r10-arsiv
# Dosyaları /opt/r10-arsiv/ altına kopyalayın (git clone veya scp)
cd /opt/r10-arsiv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # SECRET_KEY güçlü rastgele değer
```

Firewall:

```bash
ufw allow OpenSSH
ufw allow 5000/tcp
ufw enable
```

Systemd (tek worker — scheduler için önemli):

```bash
cp deploy/r10-takip.service.example /etc/systemd/system/r10-takip.service
# WorkingDirectory ve yolları düzenleyin
systemctl daemon-reload
systemctl enable --now r10-takip
journalctl -u r10-takip -f
```

Kategori testi:

```bash
source venv/bin/activate
python -c "from scraper import fetch_all_categories; print(len(fetch_all_categories()))"
```

~239 dönmeli.

## Windows Server

```powershell
cd C:\r10-arsiv
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m waitress --listen=0.0.0.0:5000 app:app
```

Kalıcı çalışma: NSSM ile Windows servisi (`scripts/start-waitress.bat` referans). Firewall'da TCP 5000 açın.

## Ortam değişkenleri

| Değişken | Açıklama |
|----------|----------|
| `SECRET_KEY` | Flask oturum imzası (üretimde zorunlu) |

## Veritabanı

SQLite: `r10_tracker.db` (git'e eklenmez). Yedek:

```bash
cp r10_tracker.db r10_tracker.db.bak
```

## İsteğe bağlı: toplu sosyal medya takip anahtar kelime listesi

```bash
python3 scripts/seed_social_keywords.py /opt/r10-arsiv/r10_tracker.db
```

## Güncelleme

```bash
systemctl stop r10-takip   # Linux
cd /opt/r10-arsiv && git pull
source venv/bin/activate && pip install -r requirements.txt
systemctl start r10-takip
```

Yeni sürümde ilk açılışta `/setup` görürseniz (admin yoksa) bir kez yönetici oluşturun; Telegram ayarları DB'de kalır.

## Güvenlik

- Varsayılan `SECRET_KEY` kullanmayın
- Paneli internete açıyorsanız güçlü şifre + mümkünse VPN / IP kısıtı
- Bot token ve chat ID'yi repoya commit etmeyin

## Lisans

MIT — bkz. [LICENSE](LICENSE)

## LinkedIn

Paylaşım metni örneği: [LINKEDIN.md](LINKEDIN.md)
