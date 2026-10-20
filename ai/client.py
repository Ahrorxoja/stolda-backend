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
import random
import threading
import time
from typing import Sequence

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash"

#: Limitga uchragan kalit shuncha soniya ishlatilmaydi.
COOLDOWN_SECONDS = 10

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
        timeout: int = 5,
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

    def generate_text(self, system: str, contents: list[dict], temperature: float = 0.2) -> str:
        """Suhbat tarixi (contents) va system prompt bilan matnli javob oladi."""
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        body = self._post(payload)
        try:
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as error:
            raise AiError("Gemini javobi kutilganidek emas.") from error

    def _ordered_keys(self) -> list[str]:
        """Kalitlarni navbatma-navbat (Round-Robin) va dam olayotganlarini oxiriga qo'yib beradi."""
        now = time.monotonic()
        with _lock:
            resting = {key for key in self.keys if _cooldown.get(_fingerprint(key), 0) > now}

        active = [key for key in self.keys if key not in resting]
        inactive = [key for key in self.keys if key in resting]

        if len(active) > 1:
            offset = random.randint(0, len(active) - 1)
            active = active[offset:] + active[:offset]

        return active + inactive

    def _rest(self, key: str) -> None:
        with _lock:
            _cooldown[_fingerprint(key)] = time.monotonic() + COOLDOWN_SECONDS

    def _post(self, payload: dict) -> dict:
        last: requests.Response | None = None
        start_time = time.monotonic()
        for attempt in range(2):
            for model in self.models:
                for key in self._ordered_keys():
                    if time.monotonic() - start_time > 10:
                        break
                    response = self._send(model, key, payload)
                    if response is None:
                        self._rest(key)
                        continue
                    if response.ok:
                        try:
                            return response.json()
                        except ValueError as error:
                            raise AiError("Gemini javobini o'qib bo'lmadi.") from error
                    last = response
                    if response.status_code in (429, 500, 503):
                        logger.warning("Gemini kaliti %s vaqtincha ishlamadi (%s - %s)", _fingerprint(key), model, response.status_code)
                        self._rest(key)
                        continue
                    if response.status_code in (401, 403):
                        logger.error("Gemini kaliti %s rad etildi (%s)", _fingerprint(key), model)
                        self._rest(key)
                        continue
                    self._rest(key)
                    continue
            if attempt == 0 and time.monotonic() - start_time < 4:
                time.sleep(0.3)
        raise AiError(self._failure(last))

    def _send(self, model: str, key: str, payload: dict) -> requests.Response | None:
        try:
            return self.session.post(
                f"{API_ROOT}/{model}:generateContent",
                # Kalit sarlavhada — `requests` xatolarida URL ko'rinib qoladi.
                headers={"x-goog-api-key": key},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            logger.warning("Gemini kaliti %s tarmoq xatosi: %s", _fingerprint(key), str(error))
            return None

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


class GrokClient:
    """xAI Grok API mijozi — Gemini ishlamay qolganda zaxira (fallback) provayder."""

    def __init__(self, key: str, model: str = "grok-2-mini", timeout: int = 10):
        self.key = key
        self.model = model
        self.timeout = timeout
        self.session = requests.Session()

    def generate_text(self, system: str, contents: list[dict], temperature: float = 0.4) -> str:
        messages = [{"role": "system", "content": system}]
        for item in contents:
            role = "assistant" if item.get("role") in ("model", "assistant") else "user"
            text = ""
            if "parts" in item and item["parts"]:
                text = item["parts"][0].get("text", "")
            elif "content" in item:
                text = str(item["content"])
            if text:
                messages.append({"role": role, "content": text})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        try:
            res = self.session.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.key}",
                },
                json=payload,
                timeout=self.timeout,
            )
            if res.ok:
                data = res.json()
                return data["choices"][0]["message"]["content"]
            logger.warning("Grok API xatosi (%s): %s", res.status_code, res.text[:200])
            raise AiError("Grok API javob bermadi.")
        except requests.RequestException as e:
            logger.warning("Grok API tarmoq xatosi: %s", str(e))
            raise AiError("Grok API'ga ulanib bo'lmadi.") from e


def get_grok_client() -> GrokClient | None:
    key = (getattr(settings, "XAI_GROK_API_KEY", "") or "").strip()
    if not key:
        return None
    model = getattr(settings, "XAI_GROK_MODEL", "grok-2-mini") or "grok-2-mini"
    return GrokClient(key, model)
