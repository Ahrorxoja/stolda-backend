# stolda.uz — backend (Django)

stolda.uz — O'zbekistondagi restoran va kafelar uchun QR menyu platformasi.
Mijoz stoldagi QR kodni skanerlaydi va brauzerda restoran menyusini ko'radi.

Bu papkada **faqat Django** bor. Frontend alohida turadi: `../stolda`
(Next.js). Ikkalasi faqat HTTP orqali gaplashadi.

## Eng muhim qoida

**Faqat menyu ko'rsatiladi.** Buyurtma, savat, to'lov, "+" tugmasi, ofitsiantni chaqirish tugmasi,
sevimlilar (yurakcha), sharhlar YO'Q. Hech qachon qo'shma. Buyurtmani mijoz odatdagidek ofitsiantga aytadi.
Daromad yoki sotuv statistikasi yo'q — faqat **ko'rishlar** statistikasi (qaysi taom necha marta ochildi, QR skanerlar soni).

## Stek

- Django 5 + Django REST Framework + PostgreSQL.
- Rasmlar `MEDIA_ROOT` da saqlanadi (keyin S3'ga o'tkazsa bo'ladi) va yuklanganda
  WebP formatiga siqiladi (`menu/images.py`).
- Autentifikatsiya: JWT (`djangorestframework-simplejwt`), telefon raqami bilan
  kirish (`menu/auth.py`, raqam `User.username` da saqlanadi).
- Kesh: sukut bo'yicha xotirada, `CACHE_URL` berilsa Redis.
- Fon vazifalari: Celery + Redis (AI tarjima). Broker berilmasa vazifalar
  darhol, shu jarayonda bajariladi — dev'da Redis shart emas.
- AI tarjima: **Gemini API**, kalit `.env` da `GEMINI_API_KEY`.
- AI (`ai/`): umumiy `GeminiClient` — qo'shimcha kalitlar `GEMINI_API_KEYS` (vergul bilan), zaxira model
  `GEMINI_FALLBACK_MODEL`; 429/403 da keyingi kalit (1 daqiqa dam), hammasi tugasa keyingi model.
  Limit Google *loyihasi* bo'yicha — kalitlar boshqa-boshqa loyihalardan bo'lsin. Tarjima ham shu mijozdan.
- AI yordamchi (`menu/ai_views.py`, throttle `ai` 120/soat, hech narsa saqlamaydi, asosiy tilda):
  `POST /api/ai/kcal/` → `{kcal, low, high}`, `/api/ai/ingredients/` → `{items}` (imlo, ajratish, takror),
  `/api/ai/description/` → `{variants: [1–3]}` (faktlarni o'ylab topmaydi, og'irlik/narx yozmaydi).

## Papka tuzilmasi

```
config/           settings.py, urls.py
menu/             yagona ilova: modellar, public API, admin API
  models.py         Restaurant, Category, Dish, Table, MenuView
  serializers.py    public API
  admin_serializers.py, admin_views.py, auth.py, permissions.py
  translations.py   uch tilli JSON maydonlar
  images.py         WebP siqish
  cache.py, signals.py   menyu keshini versiyalash
  management/commands/seed_demo.py
  tests/
translation/      Translator interfeysi, GeminiTranslator, FakeTranslator
data/             menu-sample.json — namuna menyu (seed uchun)
assets/food/      taom rasmlari (seed uchun)
deploy/           docker-compose, nginx, deploy va zaxira skriptlari
```

## Ma'lumotlar modeli

- `Restaurant`: owner(User), slug(unique), name, languages([...]), primary_language, auto_translate, cuisine{…},
  address{…}, hours{…}, phone, instagram, logo, cover, service_charge_percent, plan(free/standard/pro), is_active.
- `Category`: restaurant, name{…}, subtitle{…}, icon (lucide nomi), photo(null), position, is_visible,
  visible_from(time, null), visible_to(time, null), translation_meta.
- `Dish`: category, name{…}, description{…}, ingredients{lang:[]}, price(int, so'm),
  weight(int), unit(g/ml), kcal(int), kcal_source(manual/ai), badges([popular, veg]), is_available, position, translation_meta.
- `kcal_source=ai` — kaloriya `POST /api/ai/kcal/` taxmini, egasi o'zgartirmagan;
  public API'da `kcal_estimated: true`, menyuda "≈" bilan. `kcal` `kcal_source` siz o'zgarsa — `manual`.
- `DishPhoto`: dish, image, position (birinchisi — asosiy rasm).
- `{…}` — restoran tanlagan tillar bo'yicha JSON (`{"uz": "...", "ru": "..."}`).
- `Table`: restaurant, number, qr_token(unique) — QR `stolda.uz/{slug}?t={qr_token}`.
- `MenuView`: restaurant, dish(null), table(null), kind(scan/dish_open), created_at — statistikalar shundan.
- Tarjima maydonlari: `JSONField` (`{"uz": "...", "ru": "...", "en": "..."}`), bo'sh til bo'lsa `primary_language` ga qayt.
- Tarif cheklovlari: free — 30 ta taom, statistika yo'q; standard — cheksiz, statistika; pro — AI import, filiallar.

## API

- `GET /api/public/{slug}/menu/` — restoran + kategoriyalar + mavjud taomlar (bitta so'rov, cache 60s).
- `POST /api/public/{slug}/views/` — ko'rish hodisasi.
- Admin (JWT): CRUD `/api/restaurants/`, `/api/categories/`, `/api/dishes/` (+ `PATCH` position, is_available),
  rasm yuklash (siqib, WebP), `/api/stats/?range=day|week|month`, `/api/tables/` + QR PNG/PDF.

Public endpointlar anonim. Admin endpointlar JWT talab qiladi va faqat
foydalanuvchining o'z restorani doirasida ishlaydi (`menu/permissions.py`).

## Ro'yxatdan o'tish (parolsiz)

- `POST /api/auth/google/` — `{"credential": "<Google ID token>"}`. Token
  `google-auth` bilan tekshiriladi (`audience=GOOGLE_CLIENT_ID`), email orqali
  `User` topiladi yoki yaratiladi (`username=email`, parolsiz), JWT qaytadi.
  Hozircha faqat Google; Telegram provayderi keyinroq shu yerga qo'shiladi.
  `GOOGLE_CLIENT_ID` bo'sh bo'lsa 400 qaytaradi.
- `POST /api/auth/signup/` va `POST /api/auth/token/` — eski telefon+parol
  oqimi, hali ishlaydi (zaxira yo'l sifatida `/signup` sahifasida havola bor).
- `GET /api/restaurants/slug-check/?slug=...` — `{"slug": "...", "available": bool}`.
  Taqiqlangan manzillar (`menu/slugs.py: RESERVED_SLUGS`) ham band hisoblanadi.
- Restoran yaratish: Google/telefon bilan kirgach `restaurants` bo'sh bo'lsa,
  frontend `POST /api/restaurants/` (nomi, slug, phone, city) so'raydi, keyin
  `PATCH` bilan `languages`/`primary_language` tanlanadi.
- `GET /api/restaurants/{id}/qr/?format=png|a6|a4` — restoranning yagona QR
  kodi (stol tokensiz, to'g'ridan-to'g'ri `stolda.uz/{slug}`ga). `png` — rasm,
  `a6`/`a4` — chop etishga tayyor PDF. Har bir stolga alohida QR kerak bo'lsa,
  o'sha — yuqoridagi `/api/tables/{id}/qr.png`.

## Ko'p tillilik va AI tarjima

- `Restaurant.languages` — tanlangan tillar ro'yxati (`["uz","ru","en"]`), `Restaurant.primary_language` — asosiy til,
  `Restaurant.auto_translate` — bool. Mijoz menyusidagi til almashtirgich faqat `languages` ni ko'rsatadi.
- Tarjima qilinadigan maydonlar: taom (name, description, ingredients), kategoriya (name, subtitle), restoran (cuisine,
  address, hours). Har birida `{lang: text}` + `translation_meta: {lang: {"source": "ai"|"manual", "source_hash": "..."}}`.
- Asosiy tildagi matn saqlanganda: `auto_translate` yoqilgan bo'lsa, `source="ai"` bo'lgan tillar Celery task orqali qayta
  tarjima qilinadi; `manual` tillarga tegilmaydi (faqat admin'da "asosiy matn o'zgardi" ogohlantirishi).
- Tarjima provayderi — `translation/` moduli ichida interfeys (`translate(texts, source, targets) -> dict`),
  implementatsiya: **Gemini API** (taom nomlari uchun kontekst: "restoran menyusi, milliy taom nomlarini
  transliteratsiya qil"). Kalit `.env` da `GEMINI_API_KEY`. Test uchun `FakeTranslator`.
- Endpoint: `POST /api/translate/preview/` (asosiy matn → barcha tillar, saqlamasdan) — admin formasida "Qayta tarjima qilish" uchun.

## Menyu keshi

`GET /api/public/{slug}/menu/` javobi 60 soniya keshlanadi. Kalit versiyalangan:
restoran, kategoriya yoki taom o'zgarganda `menu/signals.py` versiyani oshiradi,
shuning uchun tahrir darhol ko'rinadi. Bir nechta gunicorn worker bo'lganda bu
faqat Redis bilan ishlaydi — production'da `CACHE_URL` majburiy.

## Ish uslubi

- Har o'zgarishdan keyin `.venv/bin/python manage.py test` o'tsin.
- Yangi maydon qo'shganda migratsiya yarating va public API javobiga ta'sirini
  tekshiring — frontend `../stolda/lib/types.ts` dagi tiplarga tayanadi.
- Namuna ma'lumot: `.venv/bin/python manage.py seed_demo`.
