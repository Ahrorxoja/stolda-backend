# stolda.uz — Contabo VPS'ga qo'yish

Ubuntu 22.04/24.04 va Docker o'rnatilgan VPS uchun. Barcha servislar bitta
`docker-compose.prod.yml` orqali ishlaydi: nginx, Next.js, Django (gunicorn),
PostgreSQL, Redis va certbot.

**Muhim:** frontend va backend alohida papkalarda turadi va ular
**yonma-yon** bo'lishi kerak — compose web image'ini `../../stolda` dan quradi:

```
/srv/
  stolda/           ← Next.js (frontend)
  stolda-backend/   ← Django (backend) + deploy/ (shu qo'llanma)
```

---

## 1. Serverni tayyorlash

```bash
# Docker va compose plagini
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # keyin qayta kiring

# Ikkala loyihani yonma-yon joylashtirish
sudo mkdir -p /srv && sudo chown "$USER" /srv
git clone <frontend-repo> /srv/stolda
git clone <backend-repo>  /srv/stolda-backend
cd /srv/stolda-backend
```

DNS'da `stolda.uz` va `www.stolda.uz` uchun **A yozuvlari** VPS IP manziliga
qaratilgan bo'lsin — sertifikat shusiz olinmaydi.

## 2. Muhit o'zgaruvchilari

```bash
cd /srv/stolda-backend/deploy
cp .env.prod.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # DJANGO_SECRET_KEY
nano .env
```

`.env` da to'ldiriladigan qatorlar: `DOMAIN`, `LETSENCRYPT_EMAIL`,
`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`.

## 3. HTTPS sertifikati (bir marta)

```bash
./init-ssl.sh
```

Skript vaqtincha HTTP-only nginx ko'taradi, Let's Encrypt'dan sertifikat oladi
va to'liq konfiguratsiyani qaytaradi. Keyinchalik `certbot` konteyneri har 12
soatda yangilab turadi.

## 4. Ishga tushirish

```bash
./deploy.sh
```

Skript image'larni quradi, migratsiyalarni bajaradi, static fayllarni yig'adi
va hamma servisni ko'taradi. Keyingi yangilanishlarda ham shu bitta buyruq:

```bash
(cd ../../stolda && git pull) && (cd .. && git pull) && ./deploy.sh
```

## 5. Birinchi restoran

```bash
C="docker compose -f docker-compose.prod.yml"

# Django admin uchun superuser (username — telefon raqami)
$C exec api python manage.py createsuperuser --username +998901234567

# Yoki namuna "Zamin" restorani bilan tekshirib ko'rish
$C exec api python manage.py seed_demo
```

Restoran egasi `https://stolda.uz/admin` orqali telefon raqami va parol bilan
kiradi. Kategoriya va taomlar Django admin (`/django-admin/`) yoki admin
panelning menyu bo'limidan qo'shiladi.

---

## Zaxira nusxalar

`deploy/backup.sh` bazani `pg_dump` bilan va yuklangan rasmlarni `tar` bilan
zaxiralaydi, eski nusxalarni (`BACKUP_KEEP_DAYS`, sukut bo'yicha 14 kun)
o'chiradi.

Kunlik cron:

```bash
crontab -e
# har kuni 03:00 da
0 3 * * * /srv/stolda-backend/deploy/backup.sh >> /var/log/stolda-backup.log 2>&1
```

Tiklash:

```bash
C="docker compose -f docker-compose.prod.yml"

# Baza
gunzip -c /srv/backups/stolda/db-2026-09-22_0300.sql.gz \
  | $C exec -T db psql -U stolda -d stolda

# Rasmlar
$C run --rm --no-deps --user root -v /srv/backups/stolda:/backup \
  --entrypoint tar api -xzf /backup/media-2026-09-22_0300.tar.gz -C /srv/media
```

---

## Foydali buyruqlar

```bash
C="docker compose -f docker-compose.prod.yml"

$C ps                    # servislar holati
$C logs -f api           # Django loglari
$C logs -f web           # Next.js loglari
$C logs -f nginx
$C exec api python manage.py migrate
$C exec db psql -U stolda -d stolda
$C restart web
```

## Tuzilish

| Servis | Vazifasi |
|---|---|
| `nginx` | HTTPS, statik fayllar, `/api/` va `/django-admin/` ni Django'ga, qolganini Next.js'ga uzatadi |
| `web` | Next.js (standalone), 3000-port |
| `api` | Django + gunicorn (3 worker), 8000-port |
| `db` | PostgreSQL 17, `pgdata` volume |
| `redis` | Menyu keshi — bir nechta gunicorn worker'da kesh tozalash uchun zarur |
| `certbot` | Sertifikatni har 12 soatda yangilaydi |

Volume'lar: `pgdata` (baza), `media` (yuklangan rasmlar), `static` (Django
static), `certbot-conf` va `certbot-www` (sertifikatlar).

## Diqqat qilinadigan joylar

- `NEXT_PUBLIC_API_URL` **qurish paytida** kodga yoziladi. Production'da u bo'sh
  qiymat — brauzer API'ni shu domendan chaqiradi (`/api/...`). Domen o'zgarsa,
  `web` image'ini qayta qurish kerak.
- Barcha `docker compose` buyruqlari `stolda-backend/deploy/` papkasidan
  bajariladi.
- Menyu javobi 60 soniya keshlanadi, lekin tahrirlanganda kesh versiyasi oshadi
  (`menu/signals.py`) — shuning uchun o'zgarish darhol ko'rinadi. Aynan shu
  sababdan Redis kerak: har bir gunicorn worker alohida xotiraga ega.
- Yuklangan rasmlar serverda WebP formatiga siqiladi va eng katta tomoni
  1600px gacha kichraytiriladi (`menu/images.py`).
