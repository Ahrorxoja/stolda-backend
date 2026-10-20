"""AI Ofitsiant — QR menyuni ko'rayotgan mijoz uchun aqlli, ko'p tilli yordamchi.

Aktiv menyudagi taomlar va kategoriyalarni Gemini System Prompt'ga yuklaydi
va faqat shu menyu asosida samimiy, oddiy odamdek muloqot qiladi.
"""

import logging
from typing import Any

from django.core.cache import cache

from ai.client import AiError, get_client, get_grok_client, get_groq_client
from menu.models import Restaurant

logger = logging.getLogger(__name__)


def build_menu_context(restaurant: Restaurant, categories: list, dishes: list, lang: str = "uz") -> str:
    """Restoran va uning mavjud menyusini Gemini tushunadigan o'ta ixcham matnga o'giradi."""
    lines = [f"RESTORAN: {restaurant.name}"]

    info_parts = []
    if restaurant.address:
        addr = restaurant.address.get(lang) or restaurant.address.get(restaurant.primary_language) or ""
        if addr:
            info_parts.append(f"Manzil: {addr}")
    if restaurant.phone:
        info_parts.append(f"Tel: {restaurant.phone}")
    if restaurant.service_charge_percent:
        info_parts.append(f"Xizmat: {restaurant.service_charge_percent}%")
    if info_parts:
        lines.append(" | ".join(info_parts))

    lines.append("\n=== MENYU ===")

    dish_map = {}
    for dish in dishes:
        dish_map.setdefault(dish.category_id, []).append(dish)

    for cat in categories:
        cat_name = cat.name.get(lang) or cat.name.get(restaurant.primary_language) or str(cat.id)
        cat_dishes = dish_map.get(cat.id, [])
        if not cat_dishes:
            continue

        lines.append(f"\n[{cat_name}]:")
        for dish in cat_dishes:
            d_name = dish.name.get(lang) or dish.name.get(restaurant.primary_language) or ""
            if not d_name:
                continue

            price_str = f"{dish.price:,}".replace(",", " ") + " so'm"
            meta = []
            if dish.weight:
                meta.append(f"{dish.weight}{dish.unit or ''}")
            if dish.badges:
                meta.append(", ".join(dish.badges))

            d_ing = ""
            if dish.ingredients and isinstance(dish.ingredients, dict):
                ing_list = dish.ingredients.get(lang) or dish.ingredients.get(restaurant.primary_language) or []
                if ing_list:
                    d_ing = ", ".join(ing_list[:4])

            d_desc = dish.description.get(lang) or dish.description.get(restaurant.primary_language) or ""
            desc_snippet = (d_ing or d_desc[:60]).replace("\n", " ")

            meta_str = f" ({'; '.join(meta)})" if meta else ""
            desc_str = f" | {desc_snippet}" if desc_snippet else ""

            lines.append(f"- {d_name} — {price_str}{meta_str}{desc_str}")

    return "\n".join(lines)


def get_cached_menu_context(restaurant: Restaurant, categories: list, dishes: list, lang: str = "uz") -> str:
    """Restoran menyusi va ma'lumotlarini keshda saqlaydi (1 soat TTL)."""
    cache_key = f"ai_menu_ctx_v2_{restaurant.id}_{lang}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    context = build_menu_context(restaurant, categories, dishes, lang=lang)
    cache.set(cache_key, context, timeout=3600)
    return context


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
3. TAVSIYA QILINADIGAN TAOMLARNI HAR DOIM ALOHIDA RO'YXAT KO'RINISHIDA YOZING:
   - Taomlarni tavsiya qilayotganda ularni alohida tartiblangan ro'yxat ko'rinishida yozing (masalan: 1. **Bardak Choy** - 5 000 so'm).
   - Taom nomlarini qavs ichida yoki bitta uzun matn matnida sanab o'tmang! Tizim ularni avtomatik interaktiv taom kartochkasi ko'rinishida ko'rsatadi.
4. TAOM ID-LARINI UMUMAN YOZMANI:
   - Kontekstdagi ID:123 kabi texnik ID raqamlarini mijozga aslo ko'rsatmang.
5. SUHBAT TAVSILOTI VA SALOMLASHISH:
   - Agar suhbat allaqachon ketayotgan bo'lsa (tarixda xabarlar bo'lsa), QAYTA "Xush kelibsiz!" YOKI "Assalomu alaykum!" DEB SALOMLASHMANING! Darhol savolga tabiiy javob bering.
6. BUYURTMA ESLATMASI:
   - "Buyurtma uchun stoldagi ofitsiantga murojaat qiling" degan eslatmani har bir oddiy savolda (masalan narx, manzil, telefon so'ralganda) TEKSTGA TIQMANG! Bu eslatmani FAQAT mijoz taom tanlab buyurtma berish istagini bildirgandagina aytishingiz kifoya.
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
    """Groq API (va zaxirada Gemini/xAI Grok API) chaqiradi va AI Ofitsiant javobini qaytaradi."""
    groq_client = get_groq_client()
    client = get_client()
    grok_client = get_grok_client()
    menu_context = get_cached_menu_context(restaurant, categories, dishes, lang=lang)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        restaurant_name=restaurant.name, lang=lang, menu_context=menu_context
    )

    contents = []
    if history:
        # Tarixning faqat eng so'nggi 4 ta xabarini olamiz (tezlik va token tejamkorligi uchun)
        recent_history = history[-4:]
        for msg in recent_history:
            role = "user" if msg.get("role") == "user" else "model"
            text = msg.get("text", "")
            if text:
                contents.append({"role": role, "parts": [{"text": text}]})

    contents.append({"role": "user", "parts": [{"text": user_message}]})

    if groq_client:
        try:
            return groq_client.generate_text(system=system_prompt, contents=contents, temperature=0.3)
        except AiError as e:
            logger.warning("Groq API xatosi, Gemini'ga o'tilmoqda: %s", str(e))

    if client:
        try:
            return client.generate_text(system=system_prompt, contents=contents, temperature=0.4)
        except AiError as e:
            logger.warning("Gemini API xatosi, Grok'ga o'tilmoqda: %s", str(e))

    if grok_client:
        try:
            return grok_client.generate_text(system=system_prompt, contents=contents, temperature=0.4)
        except AiError as e:
            logger.error("Grok API ham xato berdi: %s", str(e))

    if not groq_client and not client and not grok_client:
        return f"Assalomu alaykum! Men {restaurant.name} restoranining AI ofitsiantiman. Bugun sizga qanday taom tavsiya qilishim mumkin?"

    raise AiError("AI hozir band, birozdan keyin qayta urinib ko'ring.")
