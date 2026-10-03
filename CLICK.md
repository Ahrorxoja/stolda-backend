# Click orqali to'lov

Restoran egasi `/admin/billing` (yoki Telegram ilovadagi «To'lov» bo'limi)
da davrni tanlaydi → **«Click orqali to'lash»** → Click sahifasida to'laydi →
obuna **avtomatik** uzayadi. Chek yuklash va Telegram'dan tasdiqlash shart emas.

Kalitlar qo'yilmaguncha Click o'chiq: tugma ko'rinmaydi, faqat eski usul
(kartaga o'tkazib chek yuborish) ishlaydi. Kalitlar qo'yilgandan keyin ham chek
usuli zaxira sifatida qoladi.

---

## Ulash — qadam-baqadam (shu tartibda)

Click kodi hozircha faqat `dev` branch'da. Server `main` dagi kodni ishlatadi,
shuning uchun faqat kalit qo'yish yetmaydi — kodni ham chiqarish kerak.

1. **Kalitlarni serverga yozing** — 2-bo'lim. `./deploy.sh` ni hozir
   ishlatmasangiz ham bo'ladi, keyingi qadamdagi deploy uni o'zi bajaradi.

2. **Kodni chiqaring** — o'z kompyuteringizda, avval backend:
   ```bash
   cd ~/Development/stolda-backend
   git checkout main && git pull && git merge dev && git push && git checkout dev
   ```
   GitHub → `stolda-backend` → **Actions** — yashil ✅ chiqishini kuting
   (6–8 daqiqa). Qizil ❌ bo'lsa sayt eski versiyada ishlayveradi, hech narsa
   buzilmaydi. Keyin frontend:
   ```bash
   cd ~/Development/stolda
   git checkout main && git pull && git merge dev && git push && git checkout dev
   ```
   Yana yashil ✅ ni kuting.

3. **Click kabinetiga Prepare/Complete URL'larini kiriting** — 1-bo'lim.
   Deploydan *keyin* — aks holda Click tekshirganda manzil hali yo'q bo'ladi.

4. **Tekshiring** — 3-bo'lim: tugma chiqdimi, keyin o'zingizdan oylik
   (99 000 so'm) to'lab ko'ring. Muddat uzayishi va Telegram'ga «💳 Click»
   xabari kelishi kerak.

**Muammo chiqsa — Click'ni o'chirish:** serverdagi `.env` da uchta kalitni
bo'shating (`CLICK_SERVICE_ID=`, `CLICK_MERCHANT_ID=`, `CLICK_SECRET_KEY=`) va

```bash
cd /srv/stolda/stolda-backend/deploy && ./deploy.sh
```

Tugma yo'qoladi, sayt avvalgidek chek orqali to'lov bilan ishlaydi. Kodni
qaytarish shart emas.

---

## 1. Click kabinetida (merchant.click.uz)

Xizmat (servis) sozlamalarida shu ikki manzilni kiriting:

| Maydon | Qiymat |
|---|---|
| Prepare URL | `https://stolda.uz/api/billing/click/prepare/` |
| Complete URL | `https://stolda.uz/api/billing/click/complete/` |

Oxiridagi `/` muhim — usiz so'rov ishlamaydi.

Kabinetdan quyidagilarni ko'chirib oling:

| Click'da | `.env` dagi kalit |
|---|---|
| Service ID | `CLICK_SERVICE_ID` |
| Merchant ID | `CLICK_MERCHANT_ID` |
| Secret key | `CLICK_SECRET_KEY` |
| Merchant user ID | `CLICK_MERCHANT_USER_ID` (soliq cheki uchun) |

`CLICK_SECRET_KEY` — parol bilan teng. Uni chatga, git'ga, skrinshotga
qo'ymang. Faqat serverdagi `deploy/.env` da turadi.

## 2. Serverda kalitlarni qo'yish

```bash
ssh root@173.212.226.178
nano /srv/stolda/stolda-backend/deploy/.env
```

Fayl oxiriga qo'shing (qiymatlar — o'zingiznikini):

```
CLICK_SERVICE_ID=12345
CLICK_MERCHANT_ID=67890
CLICK_SECRET_KEY=xxxxxxxxxxxxxxxx
CLICK_MERCHANT_USER_ID=11111
```

Keyin:

```bash
cd /srv/stolda/stolda-backend/deploy && ./deploy.sh
```

`./deploy.sh` shart — oddiy `restart` yangi `.env` ni o'qimaydi.

## 3. Ishlayotganini tekshirish

**a) Endpoint ochiqmi** — kalitsiz so'rov `-8` (yoki kalit bo'lmasa `-1`)
qaytarishi kerak, 404/500 emas:

```bash
curl -s -X POST https://stolda.uz/api/billing/click/prepare/ -d "click_trans_id=1"
# {"click_trans_id":1,"merchant_trans_id":null,"error":-8,"error_note":"Error in request from click"}
```

**b) Tugma chiqdimi** — `/admin/billing` → «To'lov» kartasida oltinrang
«Click orqali to'lash · 99 000 so'm» tugmasi bo'lishi kerak.

**c) Click test vositasi** — kabinetda «Тестирование / Test» bo'limi bo'lsa,
avval `/admin/billing` da tugmani bosing (Click sahifasi ochiladi, URL dagi
`transaction_param=N` — buyurtma raqami). Test vositasiga shu `N` ni
`merchant_trans_id` qilib, summani aynan tanlangan narx bilan kiriting.
Prepare va Complete ikkalasi `error: 0` qaytarishi kerak.

**d) Haqiqiy to'lov** — o'zingizning restoraningizdan oylik (99 000 so'm)
to'lab ko'ring. Natija:
- `/admin/billing` ga qaytasiz, «To'lov o'tgan bo'lsa…» xabari chiqadi;
- «Muddat tugashiga» kunlari ko'payadi, «To'lovlar tarixi» da yangi qator;
- Telegram'ga «💳 Click: …» xabari keladi (cheklar boti chatiga);
- restoran egasiga «✅ to'lov tasdiqlandi» xabari boradi.

Sinov pulini qaytarish kerak bo'lsa — Click kabinetidan (pastga qarang).

## 4. To'lovlarni ko'rish

**Django admin → Click payments** (`/django-admin/menu/clickpayment/`):

| Holat | Ma'nosi |
|---|---|
| Yaratildi | egasi tugmani bosdi, Click'da hali to'lamadi (yoki tashlab ketdi) |
| Click tekshirdi | Prepare o'tdi, pul yechilishi kutilmoqda |
| To'landi | obuna uzaytirildi, `Invoice` ga bog'langan |
| Bekor qilindi | Click xato yubordi (mablag' yetmadi, karta rad etdi…) — `error_note` da sababi |

«Yaratildi» holatida qolganlar — normal holat, ularni o'chirish shart emas.

## 5. Soliq cheki (OFD) — ixtiyoriy

Kalitlar bo'sh bo'lsa chek yuborilmaydi, to'lov baribir ishlaydi.

```
CLICK_FISCAL_SPIC=            # MXIK (IKPU) kodi — tasnif.soliq.uz
CLICK_FISCAL_PACKAGE_CODE=    # o'lchov birligi (qadoq) kodi — o'sha yerda
CLICK_FISCAL_VAT_PERCENT=0    # QQS to'lovchi bo'lsangiz 12, aks holda 0
CLICK_FISCAL_TIN=             # STIR (MChJ) yoki JShShIR (YaTT)
```

To'lov o'tgach chek fon vazifasida `https://api.click.uz/v2/merchant/payment/ofd_data/submit_items`
ga yuboriladi. Xato bo'lsa 6 marta qayta urinadi (2, 4, 8… daqiqa). Natija —
Django admin → Click payments → `Fiscalized at` / `Fiscal error`.

> ⚠️ Soliq cheki so'rovining maydonlari (`payment_id`, `items`, summalar
> tiyinda) Click hujjati bo'yicha yozilgan, lekin haqiqiy kalitlar bilan
> sinalmagan. Ulash kuni Click menejeridan aniq so'rov namunasini so'rang va
> `menu/click.py` → `fiscal_payload()` bilan solishtiring. Birinchi
> to'lovdan keyin `Fiscal error` bo'sh ekanini tekshiring.

## 6. Pulni qaytarish

Avtomatik qaytarish yo'q. Kerak bo'lsa:
1. Click kabinetida to'lovni bekor qiling (qaytaring).
2. Django admin → Subscriptions → restoranning `current_period_end` ini
   to'g'rilang; Invoices'da shu to'lov holatini «Qaytarildi» qiling.

## 7. Qanday ishlaydi (dasturchi uchun)

```
Egasi ── POST /api/billing/click/ {period, return_to} ──▶ ClickPayment(created)
      ◀── {url: my.click.uz/services/pay?...&transaction_param=<id>}
Egasi ── Click'da to'laydi
Click ── POST /api/billing/click/prepare/  (action=0) ──▶ prepared
Click ── POST /api/billing/click/complete/ (action=1) ──▶ paid → record_payment()
Egasi ◀── return_url: /admin/billing?click=done  (yoki /app)
```

- Kod: `menu/click.py` (protokol), `menu/billing_views.py` (endpointlar),
  `menu/tasks.py` (`notify_click_payment`, `submit_click_fiscal`),
  testlar — `menu/tests/test_click.py`.
- Summa serverda tarifdan olinadi va `ClickPayment.amount` da qotiriladi;
  Click boshqa summa yuborsa `-2`.
- Imzo: `md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
  [+ merchant_prepare_id] + amount + action + sign_time)`.
- Takroriy Complete ikkinchi marta pul yozmaydi (`-4`), `Invoice.provider_payment_id
  = click_<click_trans_id>` unique.
- Agent ulushi, menyuni tiklash, egaga xabar — chekdagidek (`record_payment`).

| Kod | Ma'nosi |
|---|---|
| 0 | muvaffaqiyat |
| -1 | imzo noto'g'ri (yoki serverda kalitlar yo'q / service_id boshqa) |
| -2 | summa mos emas |
| -3 | action noto'g'ri |
| -4 | allaqachon to'langan |
| -5 | buyurtma topilmadi |
| -6 | tranzaksiya topilmadi (Prepare'siz Complete yoki boshqa `click_trans_id`) |
| -7 | bazaga yozib bo'lmadi — Click qayta urinadi |
| -8 | so'rovda maydon yetishmaydi |
| -9 | bekor qilingan |

## 8. Muammolar

| Belgi | Sabab | Yechim |
|---|---|---|
| Tugma chiqmayapti | `.env` da uch kalitdan biri yo'q yoki `./deploy.sh` qilinmagan | `.env` ni tekshiring, `./deploy.sh` |
| Click to'lov sahifasida xato chiqadi | Prepare `error ≠ 0` qaytaryapti | `docker compose -f docker-compose.prod.yml logs --tail=100 api` |
| Hamma so'rov `-1` | `CLICK_SECRET_KEY` yoki `CLICK_SERVICE_ID` noto'g'ri, bo'sh joy bilan ko'chirilgan | kabinetdagi bilan solishtiring |
| To'landi, lekin muddat uzaymadi | Complete kelmagan yoki `-7` | Django admin → Click payments holati; `api` loglari |
