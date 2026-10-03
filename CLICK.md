# Click'ni ulash

Click ulangach restoran egasi «Click orqali to'lash» tugmasini bosib to'laydi
va obuna **o'zi uzayadi** — chekni qo'lda tasdiqlash shart emas.
Chek orqali to'lov ham zaxira sifatida qoladi.

Kod tayyor, hozir faqat `dev` da. Quyidagi 6 qadamni **shu tartibda** bajaring.

---

## 1-qadam. Click kabinetidan 4 ta raqamni oling

merchant.click.uz ga kiring va ko'chirib oling:

- **Service ID**
- **Merchant ID**
- **Secret key** — bu parol. Hech kimga, chatga, skrinshotga bermang.
- **Merchant user ID**

## 2-qadam. Ularni serverga yozing

Terminalda:

```bash
ssh root@173.212.226.178
nano /srv/stolda/stolda-backend/deploy/.env
```

Faylning eng oxiriga qo'shing (o'z raqamlaringiz bilan):

```
CLICK_SERVICE_ID=bu_yerga_service_id
CLICK_MERCHANT_ID=bu_yerga_merchant_id
CLICK_SECRET_KEY=bu_yerga_secret_key
CLICK_MERCHANT_USER_ID=bu_yerga_merchant_user_id
```

Saqlash: `Ctrl+O` → `Enter` → chiqish: `Ctrl+X`. Keyin `exit`.

## 3-qadam. Kodni saytga chiqaring

O'z kompyuteringizda. **Avval backend:**

```bash
cd ~/Development/stolda-backend
git checkout main && git pull && git merge dev && git push && git checkout dev
```

GitHub → **stolda-backend** → **Actions** — yashil ✅ chiqishini kuting
(6–8 daqiqa). **Keyin frontend:**

```bash
cd ~/Development/stolda
git checkout main && git pull && git merge dev && git push && git checkout dev
```

GitHub → **stolda-front** → **Actions** — yana yashil ✅ ni kuting.

Qizil ❌ chiqsa — qo'rqmang, sayt eski holatda ishlayveradi.

## 4-qadam. Click kabinetiga 2 ta manzilni kiriting

Faqat 3-qadamdan **keyin**:

| Maydon | Manzil |
|---|---|
| Prepare URL | `https://stolda.uz/api/billing/click/prepare/` |
| Complete URL | `https://stolda.uz/api/billing/click/complete/` |

Oxiridagi `/` belgisini tushirib qoldirmang.

## 5-qadam. Tugmani yoqing

stolda.uz/django-admin → **Platforma sozlamalari** → «Click» bo'limi:

- **«Click tugmasini ko'rsatish»** ga belgi qo'ying → **Save**.
- Pastdagi «Serverdagi Click kalitlari» qatorida **✅ Qo'yilgan** turishi kerak.
  ❌ bo'lsa — 2-qadamni tekshiring.

## 6-qadam. Tekshiring

1. **stolda.uz/admin/billing** ni oching → «To'lov» kartasining pastida
   **«[click] orqali to'lash»** tugmasi bo'lishi kerak.
2. O'zingizdan **oylik (99 000 so'm)** to'lab ko'ring.
3. To'lovdan keyin:
   - «Muddat tugashiga» kunlari ko'payadi;
   - «To'lovlar tarixi» da yangi qator chiqadi;
   - Telegram'ga **«💳 Click»** xabari keladi.

Hammasi shunday bo'lsa — **tayyor!** 🎉

---

## Muammo chiqsa

| Nima bo'ldi | Nima qilish kerak |
|---|---|
| Tugma chiqmayapti | Django admin → Platforma sozlamalari → «Click tugmasini ko'rsatish» yoqilganmi? |
| Tugma bor, bosilsa «ulanmagan» deydi | Serverda kalitlar yo'q: 2-qadamni tekshiring, keyin pastdagi `./deploy.sh` |
| Click sahifasida xato | Kalitlarni kabinetdagi bilan solishtiring (ortiqcha bo'sh joy bo'lmasin). 4-qadamdagi manzillarni tekshiring |
| To'ladim, muddat uzaymadi | Django admin → **Click payments** → holati qanday? «To'landi» bo'lmasa — Click qo'llab-quvvatlashiga yozing |

**Click'ni tezda yashirish:** Django admin → Platforma sozlamalari →
«Click tugmasini ko'rsatish» belgisini olib tashlang → Save. Tugma darhol
yo'qoladi, server va kalitlarga tegish shart emas.

**Click'ni butunlay o'chirish:** serverdagi `.env` da 3 ta kalitni bo'shating:

```
CLICK_SERVICE_ID=
CLICK_MERCHANT_ID=
CLICK_SECRET_KEY=
```

va:

```bash
cd /srv/stolda/stolda-backend/deploy && ./deploy.sh
```

Tugma yo'qoladi, sayt chek orqali to'lov bilan ishlayveradi.

**Kalitni o'zgartirgandan keyin** har doim `./deploy.sh` kerak — oddiy
restart yangi kalitni o'qimaydi.

---

## Qo'shimcha

**To'lovlarni ko'rish:** stolda.uz/django-admin → **Click payments**.
«Yaratildi» — egasi tugmani bosgan, lekin to'lamagan (bu normal).
«To'landi» — pul tushgan, obuna uzaygan. «Bekor qilindi» — karta rad etgan.

**Pulni qaytarish** — avtomatik emas: Click kabinetida qaytarasiz, keyin
Django admin → Subscriptions'da restoranning muddatini qo'lda to'g'rilaysiz.

**Soliq cheki (OFD)** — hozir o'chiq. Click menejeridan so'rang:
*«O'zini o'zi band qilgan shaxs sifatida fiskal chek yuborishim shartmi?»*
Shart bo'lsa, ular beradigan kodlarni `.env` ga qo'shing va `./deploy.sh`:

```
CLICK_FISCAL_SPIC=          (MXIK kodi)
CLICK_FISCAL_PACKAGE_CODE=  (qadoq kodi)
CLICK_FISCAL_VAT_PERCENT=0
CLICK_FISCAL_TIN=           (JShShIR — 14 xonali shaxsiy raqamingiz)
```

Bu qism haqiqiy Click bilan sinalmagan — birinchi to'lovdan keyin Django
admin → Click payments → `Fiscal error` bo'sh ekanini tekshiring.

---

## Dasturchi uchun

- Kod: `menu/click.py` (Prepare/Complete, imzo, soliq cheki),
  `menu/billing_views.py` (endpointlar), `menu/tasks.py`, testlar —
  `menu/tests/test_click.py`. Frontend: `components/billing/PaymentCard.tsx`.
- Oqim: `POST /api/billing/click/ {period}` → `ClickPayment` + Click havolasi →
  Click `prepare/` (action=0) va `complete/` (action=1) ni chaqiradi →
  `record_payment()` obunani uzaytiradi (`Invoice.provider_payment_id = click_<id>`).
- Summa serverda tarifdan olinadi. Imzo: `md5(click_trans_id + service_id +
  SECRET_KEY + merchant_trans_id [+ merchant_prepare_id] + amount + action + sign_time)`.
- Javob kodlari: `0` ok, `-1` imzo, `-2` summa, `-3` action, `-4` allaqachon
  to'langan, `-5` buyurtma yo'q, `-6` tranzaksiya yo'q, `-7` bazaga yozilmadi,
  `-8` maydon yetishmaydi, `-9` bekor qilingan.
- Loglar: `docker compose -f docker-compose.prod.yml logs --tail=100 api`.
