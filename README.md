# stolda.uz — backend

QR menyu platformasining Django qismi: ma'lumotlar modeli, mijoz uchun public
API va restoran egasi uchun JWT bilan himoyalangan admin API.

Frontend alohida papkada: [`../stolda`](../stolda) (Next.js).

## Tuzilma

```
config/        settings.py, urls.py
menu/          yagona ilova — modellar, public API, admin API, testlar
data/          menu-sample.json (namuna menyu, 3 tilda)
assets/food/   namuna taom rasmlari
deploy/        docker-compose, nginx, deploy va zaxira skriptlari
```

Loyiha qoidalari, ma'lumotlar modeli va API — [CLAUDE.md](CLAUDE.md).
Serverga qo'yish — [deploy/DEPLOY.md](deploy/DEPLOY.md).

## Ishga tushirish

PostgreSQL ishlab turishi kerak.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # bazaga ulanish ma'lumotlari
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver   # http://localhost:8000
```

## Namuna ma'lumot

```bash
.venv/bin/python manage.py seed_demo
```

`data/menu-sample.json` va `assets/food/` dan "Zamin" restoranini yaratadi:
6 kategoriya, 12 taom, 8 stol va 30 kunlik ko'rish statistikasi. Buyruq
idempotent. Restoran egasi: `+998 90 123 45 67` / `demo12345`.

## Public API

| Endpoint | Tavsif |
|---|---|
| `GET /api/public/{slug}/menu/` | restoran, kategoriyalar va mavjud taomlar; uchala til bitta javobda, 60 soniya kesh |
| `POST /api/public/{slug}/views/` | ko'rish hodisasi: `{"kind": "scan" \| "dish_open", "dish": id?, "table": qr_token?}` |

Ikkalasi ham anonim.

## Admin API (JWT)

| Endpoint | Tavsif |
|---|---|
| `POST /api/auth/token/` | telefon + parol → `access`, `refresh` |
| `POST /api/auth/refresh/` | tokenni yangilash |
| `GET /api/me/` | foydalanuvchi va uning restoranlari |
| `GET /api/stats/?range=day\|week\|month` | ko'rishlar statistikasi |
| `/api/restaurants/`, `/api/categories/`, `/api/dishes/`, `/api/tables/` | CRUD |
| `POST /api/categories/reorder/`, `/api/dishes/reorder/` | drag-and-drop tartibi |
| `GET /api/tables/{id}/qr.png/` | bitta stol QR kodi |
| `GET /api/tables/qr.pdf/` | hamma stollar uchun chop etiladigan PDF |

Django admin: `/django-admin/`.

## Testlar

```bash
.venv/bin/python manage.py test
```
# stolda-backend
