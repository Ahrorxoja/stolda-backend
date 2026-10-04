"""Admin formasidagi AI yordamchi: kaloriya, tarkibni tuzatish, tavsif yozish.

Hammasi faqat taklif — natija formaga tushadi, egasi ko'rib "Qo'llash" yoki
"Saqlash" bosmaguncha hech narsa saqlanmaydi.
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from .client import AiError, GeminiClient, get_client

logger = logging.getLogger(__name__)

#: 100 g/ml dagi kaloriya shundan oshsa — javob xato (sof yog' ~900).
MAX_KCAL_PER_100 = 900
MAX_ITEMS = 60
MAX_ITEM_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 220

#: Promptda tilni aniq aytish uchun — yozuv ham muhim (lotin/kirill).
PROMPT_LANGUAGES = {
    "uz": "o'zbek tili, lotin yozuvi",
    "uz-Cyrl": "o'zbek tili, kirill yozuvi",
    "ru": "rus tili",
    "en": "ingliz tili",
    "tr": "turk tili",
    "zh": "xitoy tili (soddalashtirilgan ieroglif)",
    "ko": "koreys tili",
    "ar": "arab tili",
    "de": "nemis tili",
}

#: O'zbek lotinidagi o'/g' uchun turli belgilar — menyuda bittasi bo'lsin.
APOSTROPHES = str.maketrans({"ʻ": "'", "‘": "'", "’": "'", "`": "'", "ʼ": "'", "´": "'"})


@dataclass(frozen=True)
class DishFacts:
    """AI'ga beriladigan ma'lumot — hammasi restoranning asosiy tilida."""

    name: str
    lang: str = "uz"
    weight: int | None = None
    unit: str = "g"
    description: str = ""
    ingredients: list[str] = field(default_factory=list)
    category: str = ""


@dataclass(frozen=True)
class KcalEstimate:
    """Bir porsiya uchun: `kcal` — maydonga tushadigan qiymat, `low`–`high` — oraliq."""

    kcal: int
    low: int
    high: int


class Assistant(Protocol):
    def estimate_kcal(self, facts: DishFacts) -> KcalEstimate: ...

    def fix_ingredients(self, items: list[str], lang: str) -> list[str]: ...

    def describe(self, facts: DishFacts) -> list[str]: ...


def tidy(text: str, lang: str) -> str:
    """AI'dan keyin ham, AI'siz ham: bo'shliqlar, apostrof, chetdagi qo'shtirnoq."""
    text = " ".join(str(text).split()).strip().strip("\"«»“”")
    if lang == "uz":
        text = text.translate(APOSTROPHES)
    return text


def tidy_items(items: list[str], lang: str) -> list[str]:
    """Bo'sh va takror yozuvlarni olib tashlaydi, birinchi harfni katta qiladi."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = tidy(item, lang)[:MAX_ITEM_LENGTH]
        if not text:
            continue
        text = text[0].upper() + text[1:]
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        result.append(text)
    return result[:MAX_ITEMS]


KCAL_PROMPT = """Sen oziq-ovqat texnologisan. O'zbekistondagi restoran taomining
bir porsiyasidagi kaloriyani taxminiy hisoblaysan.

Qoidalar:
- Porsiya og'irligi berilgan — hisobni aynan shu og'irlik uchun qil.
- Tarkibdagi har bir masalliqning porsiyadagi ulushini taomning odatiy
  retsepti bo'yicha taxmin qil, keyin kaloriyalarni qo'sh.
- Pishirish usulini hisobga ol: qovurilgan, yog'da pishirilgan taomlarga
  yutilgan yog'ni qo'sh. O'zbek milliy taomlari (palov, manti, somsa,
  lag'mon, chuchvara, qozon kabob va h.k.) odatda yog'li tayyorlanadi —
  yog'ni kam hisoblama.
- Ichimliklar uchun (ml) shakar va sutni hisobga ol; suv, shakarsiz choy,
  qora qahva deyarli 0.
- `low` va `high` — real oraliq (taxminan ±15–25%), `kcal` ular orasida.
- Faqat butun sonlar. Javob faqat JSON: {"kcal": 0, "low": 0, "high": 0}"""

INGREDIENTS_PROMPT = """Sen restoran menyusidagi taom tarkibi ro'yxatini tartibga keltirasan.
Ro'yxat tili: {language}.

Qoidalar:
- Faqat aniq imlo xatolarini tuzat (masalan "gosht" → "go'sht", "pamidor" →
  "pomidor"). Ishonching komil bo'lmasa — o'zgartirma.
- Milliy taom va masalliq nomlarini (qazi, norin, nahot, suzma, qurt, kashnich),
  brend va mahalliy nomlarni "tuzatma".
- Bitta yozuvga yopishib qolgan bir nechta masalliqni ajrat ("guruch go'sht
  sabzi" → "Guruch", "Go'sht", "Sabzi"). Ko'p so'zli bitta masalliqni
  ("mol go'shti", "zaytun moyi", "qaymoqli sous") bo'lma.
- Takrorlarni olib tashla. Har birining faqat birinchi harfi katta.
- Yangi masalliq qo'shma va mavjudini olib tashlama. Tartibni saqla.
- Tarjima qilma — xuddi shu tilda qoldir.
- Javob faqat JSON: {{"items": ["...", "..."]}}"""

DESCRIPTION_PROMPT = """Sen restoran menyusi uchun taom tavsifini yozasan.
Til: {language} — faqat shu tilda va shu yozuvda yoz.

Qoidalar:
- 3 xil variant, har biri boshqacha boshlansin. Har biri 1–2 qisqa gap,
  60–130 belgi.
- Masalliqlarni ro'yxat qilib sanama: 2–3 ta asosiysini tanla va taomni
  jonli tasvirla — mijoz nimani tatib ko'rishini his qilsin.
- Faqat berilgan ma'lumotdan foydalan: nom, kategoriya, tarkib, mavjud tavsif.
  Berilmagan masalliq, pishirish usuli, kelib chiqishi va "uy usulida",
  "yangi uzilgan", "qo'lda tayyorlangan", "saralangan", "tanlangan" kabi
  da'volarni o'ylab topma.
- Og'irlik, porsiya, narx va kaloriyani yozma — ular menyuda alohida ko'rinadi.
- Masalliq nomlarini egasi qanday yozgan bo'lsa shunday qoldir (masalan
  "nahot" — "no'xat"ga almashtirma). Gap o'rtasida umumiy otlarni kichik
  harf bilan yoz.
- Reklama so'zlari ("eng mazali", "betakror", "ajoyib", "mukammal", "100%"),
  undov belgisi, emoji va qo'shtirnoq ishlatma.
- Taom nomini tavsif boshida takrorlama.
- Mavjud tavsif berilgan bo'lsa — ma'nosini saqla, qisqartir, silliqla va
  imlo xatolarini tuzat.
- Ohang: sodda, iliq, ishtaha ochadigan; mijoz bir qarashda tushunsin.
- Javob faqat JSON: {{"variants": ["...", "...", "..."]}}"""


def _facts_prompt(facts: DishFacts, weight: bool = True) -> str:
    lines = [f"Taom: {facts.name}"]
    if weight and facts.weight:
        lines.append(f"Porsiya: {facts.weight} {facts.unit}")
    if facts.category:
        lines.append(f"Kategoriya: {facts.category}")
    if facts.ingredients:
        lines.append(f"Tarkibi: {', '.join(facts.ingredients)}")
    if facts.description:
        lines.append(f"Mavjud tavsif: {facts.description}")
    return "\n".join(lines)


class GeminiAssistant(Assistant):
    def __init__(self, client: GeminiClient):
        self.client = client

    def estimate_kcal(self, facts: DishFacts) -> KcalEstimate:
        if not facts.weight:
            raise AiError("Og'irlik berilmagan.")
        parsed = self.client.generate_json(KCAL_PROMPT, _facts_prompt(facts), temperature=0.1)
        if not isinstance(parsed, dict):
            raise AiError("Gemini kutilgan JSON'ni qaytarmadi.")
        try:
            kcal = round(float(parsed["kcal"]))
            low = round(float(parsed.get("low", kcal)))
            high = round(float(parsed.get("high", kcal)))
        except (KeyError, TypeError, ValueError) as error:
            raise AiError("Gemini kaloriya raqamini bermadi.") from error

        # Model ba'zan noto'g'ri tartib yoki absurd qiymat beradi.
        low, high = min(low, high, kcal), max(low, high, kcal)
        if low < 0 or high > facts.weight * MAX_KCAL_PER_100 / 100:
            logger.warning("Gemini absurd kaloriya berdi: %s (%s)", parsed, facts.name)
            raise AiError("Hisob ishonchsiz chiqdi, qayta urinib ko'ring.")
        return KcalEstimate(kcal=kcal, low=low, high=high)

    def fix_ingredients(self, items: list[str], lang: str) -> list[str]:
        system = INGREDIENTS_PROMPT.format(language=PROMPT_LANGUAGES.get(lang, lang))
        parsed = self.client.generate_json(system, "\n".join(f"- {item}" for item in items), 0.1)
        fixed = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(fixed, list) or not all(isinstance(item, str) for item in fixed):
            raise AiError("Gemini ro'yxat qaytarmadi.")
        result = tidy_items(fixed, lang)
        if items and not result:
            raise AiError("Gemini bo'sh ro'yxat qaytardi.")
        return result

    def describe(self, facts: DishFacts) -> list[str]:
        system = DESCRIPTION_PROMPT.format(language=PROMPT_LANGUAGES.get(facts.lang, facts.lang))
        # Og'irlik tavsifga kerak emas — bersak, model uni matnga tiqib qo'yadi.
        parsed = self.client.generate_json(system, _facts_prompt(facts, weight=False), temperature=0.8)
        variants = parsed.get("variants") if isinstance(parsed, dict) else None
        if not isinstance(variants, list):
            raise AiError("Gemini variantlarni qaytarmadi.")
        result: list[str] = []
        for variant in variants:
            if not isinstance(variant, str):
                continue
            text = tidy(variant, facts.lang)
            if text and len(text) <= MAX_DESCRIPTION_LENGTH and text not in result:
                result.append(text)
        if not result:
            raise AiError("Gemini yaroqli tavsif bermadi, qayta urinib ko'ring.")
        return result[:3]


class FakeAssistant(Assistant):
    """Testlar va kalitsiz dev muhiti uchun — aniq, takrorlanadigan natija."""

    def estimate_kcal(self, facts: DishFacts) -> KcalEstimate:
        if not facts.weight:
            raise AiError("Og'irlik berilmagan.")
        kcal = round(facts.weight * 1.5)
        return KcalEstimate(kcal=kcal, low=round(kcal * 0.85), high=round(kcal * 1.15))

    def fix_ingredients(self, items: list[str], lang: str) -> list[str]:
        return tidy_items(items, lang)

    def describe(self, facts: DishFacts) -> list[str]:
        base = ", ".join(facts.ingredients) or facts.name
        return [f"[{facts.lang}] {base}.", f"[{facts.lang}] {facts.name}: {base}."]


def get_assistant() -> Assistant:
    client = get_client()
    return GeminiAssistant(client) if client else FakeAssistant()
