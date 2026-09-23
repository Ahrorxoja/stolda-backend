"""stolda.uz — Django sozlamalari."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-uchun-xavfsiz-emas-kalit-productionda-albatta-almashtiring",
)
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "menu",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "stolda"),
        "USER": os.getenv("POSTGRES_USER", "stolda"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "stolda"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.getenv("STATIC_ROOT", BASE_DIR / "staticfiles"))

MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    # Hammasi JSON — CSV/XML renderer yo'q. O'chirilmasa DRF `?format=` so'rov
    # parametrini har doim javob formati sifatida talqin qiladi va bizning
    # `qr/?format=png` kabi o'z biznes parametrlarimiz bilan to'qnashib,
    # tanish renderer topilmasa 404 qaytaradi.
    "URL_FORMAT_OVERRIDE": None,
}

from datetime import timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=2),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
}

# Menyu keshi. Bir nechta gunicorn worker bo'lsa Redis kerak, aks holda
# tahrirdan keyingi kesh tozalash faqat bitta worker'da ishlaydi.
CACHE_URL = os.getenv("CACHE_URL", "")
CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": CACHE_URL}
        if CACHE_URL
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    )
}

# Fon vazifalari (AI tarjima, obuna). Broker bo'lmasa vazifa darhol, shu
# jarayonda bajariladi — dev muhitida va testlarda Redis kerak emas.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
CELERY_TASK_ALWAYS_EAGER = not CELERY_BROKER_URL
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

from celery.schedules import crontab  # noqa: E402

# Obunalarni har kuni tekshiradi (eslatma, avtomatik yechish, muddat tugashi).
# Production'da alohida `beat` jarayoni kerak (`deploy/docker-compose.prod.yml`),
# aks holda bu jadval hech qachon ishga tushmaydi.
CELERY_BEAT_SCHEDULE = {
    "process-subscriptions": {
        "task": "menu.tasks.process_subscriptions",
        "schedule": crontab(hour=3, minute=0),
    },
}

# Testlar haqiqiy Gemini API'ga chiqmasligi uchun.
TEST_RUNNER = "config.test_runner.StoldaTestRunner"

# AI tarjima provayderi (Gemini). Kalit bo'lmasa FakeTranslator ishlatiladi.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Google bilan kirish (parolsiz). Google Cloud Console'da yaratilgan
# "OAuth 2.0 Client ID" (Web application) — frontend shu bilan ID token oladi,
# bu yerda audience sifatida tekshiriladi.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

# To'lov provayderlari (Payme, Click). Kalitlar bo'lmasa `FakeProvider`
# ishlatiladi — dev muhitida va testlarda tashqi so'rov yuborilmaydi.
PAYME_MERCHANT_ID = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_KEY = os.getenv("PAYME_KEY", "")
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")

# Yuklanadigan rasm hajmi chegarasi (8 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024

# Nginx orqasida: sxema va xostni proxy sarlavhalaridan olamiz, aks holda
# rasm havolalari `http://` bo'lib qoladi.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    # HTTP → HTTPS yo'naltirishni nginx bajaradi (nginx/nginx.conf), shuning
    # uchun bu yerda sukut bo'yicha o'chiq: aks holda nginx'siz muhitlarda va
    # testlarda har bir so'rov 301 bo'lib qoladi.
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Mijoz menyusi Next.js'dan server-side chaqiriladi, admin panel brauzerdan.
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)
