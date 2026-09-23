from typing import Sequence

from .base import Translator


class FakeTranslator(Translator):
    """Testlar va kalitsiz dev muhiti uchun.

    Hech qayerga so'rov yubormaydi, matn oldiga til belgisini qo'yadi:
    `Do'lma` → `[ru] Do'lma`. Natija aniq va takrorlanadigan.
    """

    def translate(
        self, texts: dict[str, str], source: str, targets: Sequence[str]
    ) -> dict[str, dict[str, str]]:
        return {
            lang: {key: f"[{lang}] {value}" if value else "" for key, value in texts.items()}
            for lang in targets
        }
