"""Agent qoidalari — botda ariza oldidan ko'rsatiladi, agent rozi bo'ladi.

Matnni o'zgartirsangiz `RULES_VERSION` ni oshiring: rozi bo'lgan versiyasi
eskirgan agentlarga bot yangi qoidalarni ko'rsatib, qayta rozilik so'raydi.
Rozilik sanasi va versiyasi `Agent.rules_accepted_at` / `rules_version` da.

Foizlar model sukutidan olinadi — shunda matn va hisob-kitob ajralib ketmaydi.
"""

from .models import Agent

RULES_VERSION = "2"

#: Telegram xabari 4096 belgidan oshmasin — shuning uchun ikki qismga bo'lingan.
_PART_ONE = """📜 <b>stolda.uz agent qoidalari</b> (v{version})

<b>1. Agent kim</b>
Agent stolda.uz QR menyusini restoran va kafelarga tanishtiradi va ularni ulaydi. Agent — mustaqil hamkor, stolda.uz xodimi emas; ish vaqti va joyini o'zi tanlaydi.

<b>2. Daromad</b>
• Restoranning <b>birinchi to'lovidan {first}%</b> — oylik yoki yillik bo'lishidan qat'i nazar.
• <b>Keyingi to'lovlaridan {percent}%</b> — agent faol ekan, restoran to'lashda davom etgancha.
• Daromad faqat stolda.uz tasdiqlagan haqiqiy to'lovdan yoziladi. Bepul sinov uchun daromad yo'q.
• Restoran agentning havolasi yoki kodi bilan ro'yxatdan o'tgan bo'lishi kerak. Kod keyin faqat sinov muddati ichida qo'shilishi mumkin.
• Alohida kelishilgan shartlar bo'lsa — o'shalar amal qiladi.

<b>3. Pul yechish</b>
• Balans {minimum} dan oshganda botdagi «💸 Pul yechish» orqali so'raladi.
• Pul agent ko'rsatgan kartaga odatda 1–3 ish kunida o'tkaziladi.
• Karta raqami va egasini to'g'ri kiritish agentning mas'uliyati.

<b>4. Agentning vazifalari</b>
• stolda.uz haqida to'g'ri va halol ma'lumot berish.
• Restoranga menyuni kiritishda va QR kodlarni chop etib qo'yishda yordam berish.
• Restoran bilan aloqada bo'lish: to'lov yaqinlashganda yoki kechiksa eslatish (bot o'zi xabar beradi).
• Borgan har bir joyni «➕ Borgan joyim» orqali halol yozish: nomi va manzili lotin harflarida, natija va izoh bilan. Jurnalni hamma agentlar ko'radi.
• Borishdan oldin «🔍 Qidirish» bilan tekshirish."""

_PART_TWO = """<b>5. Taqiqlanadi</b>
• stolda.uz mijozi (🟢) yoki boshqa agent bilan ishlanayotgan (🟡, «Qiziqdi» dan keyin 14 kun) joyga borish. Band paytida restoran boshqa agentning kodi bilan ulansa ham, daromad kod egasiga yoziladi — qoidabuzarlikni stolda.uz alohida ko'rib chiqadi.
• Jurnalga yolg'on yoki haqoratli yozuv qoldirish.
• Yolg'on va'da berish (masalan: «buyurtma qabul qiladi», «abadiy bepul», «chegirma beraman»).
• Soxta yoki restoran egasining roziligisiz ro'yxatdan o'tkazish.
• Spam, bosim o'tkazish, stolda.uz nomidan boshqa xizmat yoki to'lov so'rash.
• Restoranlarning ma'lumotlarini boshqalarga berish.

<b>6. Faollikni to'xtatish</b>
• stolda.uz agentni qoidabuzarlik, mas'uliyatsizlik (restoranlar bilan aloqa yo'qligi) yoki agentning o'z xohishi bilan faolsizlantirishi mumkin.
• Shundan keyin yangi daromad yozilmaydi. Oldin topilgan va tasdiqlangan balans yechib olinishi mumkin.
• Agentning restoranlari boshqa agentga o'tkazilishi mumkin — keyingi to'lovlar yangi agentga yoziladi.
• Soxta ro'yxat yoki aldov aniqlansa, unga tegishli daromad bekor qilinadi.

<b>7. Soliq</b>
Agent o'z daromadi bo'yicha soliq va to'lovlarni O'zbekiston qonunchiligiga muvofiq o'zi to'laydi (masalan, o'zini o'zi band qilgan shaxs sifatida).

<b>8. Qoidalarning o'zgarishi</b>
stolda.uz qoidalarni o'zgartirishi mumkin. O'zgarish haqida bot orqali xabar beriladi va yangi qoidalarga rozilik so'raladi. Allaqachon yozilgan daromad o'zgarmaydi.

<b>9. Aloqa</b>
Savollar va kelishmovchiliklar: @aha_daragoy

«✅ Qoidalarga roziman» tugmasini bosib, siz ushbu qoidalarni o'qiganingiz va ularga rozi ekaningizni tasdiqlaysiz."""


def rules_parts() -> list[str]:
    from .services import MIN_WITHDRAWAL, money

    field = Agent._meta.get_field
    return [
        _PART_ONE.format(
            version=RULES_VERSION,
            first=field("first_percent").default,
            percent=field("percent").default,
            minimum=money(MIN_WITHDRAWAL),
        ),
        _PART_TWO,
    ]


def accepted_current(agent: Agent) -> bool:
    return bool(agent.rules_accepted_at) and agent.rules_version == RULES_VERSION
