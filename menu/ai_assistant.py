"""AI Ofitsiant — QR menyuni ko'rayotgan mijoz uchun aqlli, ko'p tilli yordamchi.

Aktiv menyudagi taomlar va kategoriyalarni Gemini System Prompt'ga yuklaydi
va faqat shu menyu asosida samimiy, oddiy odamdek muloqot qiladi.
"""

import logging
from typing import Any

from ai.client import AiError, get_client
from menu.models import Restaurant

logger = logging.getLogger(__name__)


def build_menu_context(restaurant: Restaurant, categories: list, dishes: list, lang: str = "uz") -> str:
    """Restoran va uning mavjud menyusini Gemini tushunadigan ixcham matnga o'giradi."""
    lines = [
        f"RESTORAN NOMI: {restaurant.name}",
    ]

    if restaurant.address:
        addr_text = (
            restaurant.address.get(lang)
            or restaurant.address.get(restaurant.primary_language)
            or ""
        )
        if addr_text:
            lines.append(f"Manzil: {addr_text}")

    phones = []
    if restaurant.phone:
        phones.append(restaurant.phone)
    if restaurant.extra_phones:
        phones.extend(restaurant.extra_phones)
    if restaurant.delivery_phones:
        phones.extend(restaurant.delivery_phones)
    if phones:
        lines.append(f"Telefon raqamlar: {', '.join(phones)}")

    if restaurant.cuisine:
        cuisine_text = (
            restaurant.cuisine.get(lang)
            or restaurant.cuisine.get(restaurant.primary_language)
            or ""
        )
        if cuisine_text:
            lines.append(f"Oshxona turi: {cuisine_text}")

    if restaurant.service_charge_percent:
        lines.append(f"Xizmat haqi: {restaurant.service_charge_percent}%")

    if restaurant.instagram:
        lines.append(f"Instagram: {restaurant.instagram}")

    lines.append("\n=== MENYU VA TAOMLAR RO'YXATI ===")

    dish_map = {}
    for dish in dishes:
        dish_map.setdefault(dish.category_id, []).append(dish)

    for cat in categories:
        cat_name = (
            cat.name.get(lang)
            or cat.name.get(restaurant.primary_language)
            or str(cat.id)
        )
        cat_dishes = dish_map.get(cat.id, [])
        if not cat_dishes:
            continue

        lines.append(f"\nKategoriya: [{cat_name}]")
        for dish in cat_dishes:
            d_name = (
                dish.name.get(lang)
                or dish.name.get(restaurant.primary_language)
                or ""
            )
            d_desc = (
                dish.description.get(lang)
                or dish.description.get(restaurant.primary_language)
                or ""
            )

            d_ing = ""
            if dish.ingredients and isinstance(dish.ingredients, dict):
                ing_list = (
                    dish.ingredients.get(lang)
                    or dish.ingredients.get(restaurant.primary_language)
                    or []
                )
                if ing_list:
                    d_ing = ", ".join(ing_list)

            price_str = f"{dish.price:,}".replace(",", " ") + " so'm"
            portion_str = f"{dish.weight} {dish.unit}" if dish.weight else ""
            kcal_str = f"≈{dish.kcal} kkal" if dish.kcal else ""
            badges_str = f"[{', '.join(dish.badges)}]" if dish.badges else ""

            details = []
            if portion_str:
                details.append(portion_str)
            if kcal_str:
                details.append(kcal_str)
            if badges_str:
                details.append(badges_str)

            dish_line = f"- ID:{dish.id} | {d_name} — {price_str}"
            if details:
                dish_line += f" ({', '.join(details)})"
            if d_desc:
                dish_line += f"\n  Tavsif: {d_desc}"
            if d_ing:
                dish_line += f"\n  Tarkibi: {d_ing}"

            lines.append(dish_line)

    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """Siz "{restaurant_name}" restoranining samimiy, xushmuomala, chaqqon va juda bilimdon AI ofitsiantisiz.

Sizning vazifangiz:
Mijozga xuddi tajribali va e'tiborli inson kabi samimiy va tabiiy muomala qilish, restoranimiz, manzilimiz va menyudagi taomlar haqida aniq ma'lumot berish.

SOHAT QA'TIY QOIDALARI:
1. MASALLIQ VA MEZONLARGA (GURUCH, BALIQ, PARHEZ, VA H.K.) ANIQ AMAL QILISH:
   - Agar mijoz muayyan masalliq bo'lgan taom so'rasa (masalan: "guruchlik taomlar", "baliqli ovqat", "vegetarian"), FAQAT va FAQAT tarkibida yoki tavsifida O'SHA MASALLIQ bor taomlarni ko'rsating!
   - Agar menyuda bunday taom 1 ta bo'lsa — FAQAT o'sha 1 ta taomni ayting. Boshqa guruchsiz/masalliqsiz taomlarni so'ralmagan bo'lsa majburlab qo'shmang!
   - Agar menyuda bunday taom umuman bo'lmasa — muloyimlik bilan menyuda ushbu masalliqli taom yo'qligini ayting va boshqa narsa tavsiya qilishni taklif qiling.
2. MANZIL, TELEFON RAQAM, ISH VAQTI SO'RALGANDA:
   - Mijoz restoran manzili, telefon raqami, instagram yoki ish vaqti haqida so'rasa, BEVOSITA va ANIQ o'sha ma'lumotni bering! Soxta telefon yoki mavhum manzil o'ylamang — faqat berilgan kontekstdagi ma'lumotdan foydalaning.
   - Manzil yoki telefon so'ralganda "ofitsiantga murojaat qiling" degan gapni UMUMAN AYTMANG!
3. TAOM NOMLARINI GAP ICHIDA TABIIY AYTIB O'TING:
   - Javobingizda tavsiya qilayotgan taomlaringiz nomini gap o'rtasida aynan menyudagidek va tabiiy ravishda ishlatib keting (masalan: "Sizga menyumizdagi Tuya go'shti bilan dolma taomimizni tavsiya qilaman.").
4. SUHBAT TAVSILOTI VA SALOMLASHISH:
   - Agar suhbat allaqachon ketayotgan bo'lsa (tarixda xabarlar bo'lsa), QAYTA "Xush kelibsiz!" YOKI "Assalomu alaykum!" DEB SALOMLASHMANING! Darhol savolga tabiiy javob bering.
5. BUYURTMA ESLATMASI:
   - "Buyurtma uchun stoldagi ofitsiantga murojaat qiling" degan eslatmani har bir oddiy savolda (masalan narx, manzil, telefon so'ralganda) TEKSTGA TIQMANG! Bu eslatmani FAQAT mijoz taom tanlab buyurtma berish istagini bildirgandagina aytishingiz kifoya.
6. TAOM ID-LARINI VA XUNUK RO'YXATLARNI ('*', '-', NUMERATSIYA) UMUMAN YOZMANI!
7. Tili: Mijoz murojaat qilgan tilda ({lang}) javob bering.

---
Mavjud Restoran va Menyu ma'lumotlari:
{menu_context}
"""


def ask_ai_waiter(
    restaurant: Restaurant,
    categories: list,
    dishes: list,
    user_message: str,
    history: list[dict[str, str]] | None = None,
    lang: str = "uz",
) -> str:
    """Gemini API chaqiradi va AI Ofitsiant javobini qaytaradi."""
    client = get_client()
    menu_context = build_menu_context(restaurant, categories, dishes, lang=lang)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        restaurant_name=restaurant.name, lang=lang, menu_context=menu_context
    )

    contents = []
    if history:
        for msg in history:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
            if text:
                contents.append({"role": role, "parts": [{"text": text}]})

    contents.append({"role": "user", "parts": [{"text": user_message}]})

    if not client:
        return f"Assalomu alaykum! Men {restaurant.name} restoranining AI ofitsiantiman. Bugun sizga qanday taom tavsiya qilishim mumkin?"

    try:
        return client.generate_text(system=system_prompt, contents=contents, temperature=0.4)
    except AiError as e:
        logger.error("AI Ofitsiant xatoligi: %s", str(e))
        raise
