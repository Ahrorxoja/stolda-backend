"""Gemini API mijozi — tarjima va AI yordamchi shu yerdan so'rov yuboradi.

Bir nechta kalit va zaxira model bo'lishi mumkin: kalit limitga uchrasa (429)
yoki rad etilsa (401/403) keyingisi sinaladi, hammasi tugasa — keyingi model.
Limitga uchragan kalit bir daqiqa chetga qo'yiladi, keyingi so'rovlar uni
behuda urib ko'rmaydi.

**Muhim:** limit Google'da *loyiha* bo'yicha hisoblanadi, kalit bo'yicha emas.
Bitta loyihadagi bir nechta kalit limitni oshirmaydi — kalitlar boshqa-boshqa
loyihalardan (yoki to'lov yoqilgan loyihadan) bo'lishi kerak.
"""

import hashlib
import json
import logging
import threading
import time
from typing import Sequence

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.1-flash-lite"

#: Limitga uchragan kalit shuncha soniya ishlatilmaydi.
COOLDOWN_SECONDS = 60

_cooldown: dict[str, float] = {}
_lock = threading.Lock()


class AiError(RuntimeError):
    """Provayder javob bermadi yoki javobni o'qib bo'lmadi. Matnda kalit bo'lmaydi."""


def configured_keys() -> list[str]:
    """`GEMINI_API_KEYS` (vergul bilan) + `GEMINI_API_KEY`, takrorlarsiz."""
    raw = [getattr(settings, "GEMINI_API_KEY", "")]
    raw += str(getattr(settings, "GEMINI_API_KEYS", "") or "").split(",")
    keys: list[str] = []
    for key in (item.strip() for item in raw):
        if key and key not in keys:
            keys.append(key)
    return keys


def configured_models() -> list[str]:
    """Asosiy model va (bo'lsa) zaxira model."""
    models = [getattr(settings, "GEMINI_MODEL", "") or DEFAULT_MODEL]
    fallback = (getattr(settings, "GEMINI_FALLBACK_MODEL", "") or "").strip()
    if fallback and fallback not in models:
        models.append(fallback)
    return models


def _fingerprint(key: str) -> str:
    # Xotirada ham, loglarda ham kalitning o'zi emas — faqat qisqa izi.
    return hashlib.sha256(key.encode()).hexdigest()[:8]


class GeminiClient:
    def __init__(
        self,
        keys: str | Sequence[str],
        models: str | Sequence[str] = DEFAULT_MODEL,
        timeout: int = 30,
        session: requests.Session | None = None,
    ):
        self.keys = [keys] if isinstance(keys, str) else list(keys)
        self.keys = [key for key in self.keys if key]
        if not self.keys:
            raise ValueError("GEMINI_API_KEY berilmagan.")
        self.models = [models] if isinstance(models, str) else list(models)
        self.timeout = timeout
        self.session = session or requests.Session()

    def generate_json(self, system: str, prompt: str, temperature: float = 0.2) -> object:
        """So'rov yuboradi va javob matnini JSON sifatida qaytaradi."""
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": temperature,
            },
        }
        body = self._post(payload)
        try:
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise AiError("Gemini javobi kutilganidek emas.") from error
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as error:
            logger.warning("Gemini JSON qaytarmadi: %s", str(raw)[:200])
            raise AiError("Gemini JSON qaytarmadi.") from error

    def _ordered_keys(self) -> list[str]:
        """Dam olayotgan kalitlar oxiriga — boshqa iloj qolmasa baribir sinaladi."""
        now = time.monotonic()
        with _lock:
            resting = {key for key in self.keys if _cooldown.get(_fingerprint(key), 0) > now}
        return [key for key in self.keys if key not in resting] + [
            key for key in self.keys if key in resting
        ]

    def _rest(self, key: str) -> None:
        with _lock:
            _cooldown[_fingerprint(key)] = time.monotonic() + COOLDOWN_SECONDS

    def _post(self, payload: dict) -> dict:
        last: requests.Response | None = None
        for model in self.models:
            for key in self._ordered_keys():
                response = self._send(model, key, payload)
                # "Band" (500/503) — o'sha kalit bilan bir marta qaytaramiz.
                if response.status_code in (500, 503):
                    response = self._send(model, key, payload)
                if response.ok:
                    try:
                        return response.json()
                    except ValueError as error:
                        raise AiError("Gemini javobini o'qib bo'lmadi.") from error
                last = response
                if response.status_code == 429:
                    logger.warning("Gemini kaliti %s limitga uchradi (%s)", _fingerprint(key), model)
                    self._rest(key)
                    continue
                if response.status_code in (401, 403):
                    logger.error("Gemini kaliti %s rad etildi (%s)", _fingerprint(key), model)
                    self._rest(key)
                    continue
                if response.status_code in (500, 503):
                    continue
                # 400 kabi xatolar — so'rovning o'zi noto'g'ri, boshqa kalit yordam bermaydi.
                raise AiError(self._failure(response))
        raise AiError(self._failure(last))

    def _send(self, model: str, key: str, payload: dict) -> requests.Response:
        try:
            return self.session.post(
                f"{API_ROOT}/{model}:generateContent",
                # Kalit sarlavhada — `requests` xatolarida URL ko'rinib qoladi.
                headers={"x-goog-api-key": key},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise AiError("Gemini API'ga ulanib bo'lmadi.") from error

    @staticmethod
    def _failure(response: requests.Response | None) -> str:
        """Xato matni — kalit hech qachon tushib qolmasligi kerak."""
        if response is None:
            return "Gemini javob bermadi."
        reason = ""
        try:
            reason = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            pass
        if response.status_code == 429:
            return "AI limiti tugadi (429). Birozdan keyin qayta urinib ko'ring."
        if response.status_code in (401, 403):
            return "Gemini kaliti qabul qilinmadi (403). Kalitni tekshiring."
        if response.status_code in (500, 503):
            return "AI hozir band, birozdan keyin qayta urinib ko'ring."
        return f"Gemini xatosi ({response.status_code}). {reason}".strip()


def get_client() -> GeminiClient | None:
    """Kalit bo'lmasa `None` — chaqiruvchi soxta provayderga o'tadi."""
    keys = configured_keys()
    if not keys:
        return None
    return GeminiClient(keys, configured_models())
