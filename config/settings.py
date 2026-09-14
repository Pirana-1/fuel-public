import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent.parent

# Test çalıştırıcısı DEBUG'u False'a çeker; üretim sertleştirmesi testleri
# etkilemesin diye test modunu baştan ayırıyoruz.
RUNNING_TESTS = "test" in sys.argv


def env_bool(name, default=False):
    return os.getenv(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


# Boş env değişkeni "eksik" sayılır; geliştirme varsayılanına düşer.
# Üretimde (DEBUG=0) bu varsayılan aşağıdaki kontrolle reddedilir.
SECRET_KEY = os.getenv("SECRET_KEY") or "local-development-only-change-me"
DEBUG = env_bool("DEBUG", False)
if not DEBUG and not RUNNING_TESTS and SECRET_KEY == "local-development-only-change-me":
    raise RuntimeError(
        "Üretimde SECRET_KEY ortam değişkeni zorunludur. "
        ".env dosyasına güçlü bir anahtar yazın."
    )
ALLOWED_HOSTS = [value.strip() for value in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if value.strip()]
# HTTPS dağıtımlarında (ör. Coolify/Traefik arkası) POST'lar için zorunludur:
# CSRF_TRUSTED_ORIGINS=https://ornek.gov.tr,https://www.ornek.gov.tr
CSRF_TRUSTED_ORIGINS = [
    value.strip()
    for value in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if value.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "fuel.apps.FuelConfig",
]
if DEBUG:
    INSTALLED_APPS.append("django_browser_reload")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
]
if DEBUG:
    MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")
MIDDLEWARE += [
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
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
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            # DEBUG'da şablonlar her istekte yeniden okunur (canlı geliştirme),
            # üretimde performans için önbelleğe alınır.
            "loaders": (
                [
                    (
                        "django.template.loaders.cached.Loader",
                        [
                            "django.template.loaders.filesystem.Loader",
                            "django.template.loaders.app_directories.Loader",
                        ],
                    )
                ]
                if not DEBUG
                else [
                    "django.template.loaders.filesystem.Loader",
                    "django.template.loaders.app_directories.Loader",
                ]
            ),
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

# Veritabanı: DATABASE_URL verilirse (ör. Coolify/Heroku tarzı tek değişken)
# ondan okunur; verilmezse POSTGRES_* değişkenleriyle kurulur.
_database_url = os.getenv("DATABASE_URL")
if _database_url:
    _parsed = urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(_parsed.path.lstrip("/")),
            "USER": unquote(_parsed.username or ""),
            "PASSWORD": unquote(_parsed.password or ""),
            "HOST": _parsed.hostname or "",
            "PORT": str(_parsed.port or 5432),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "akaryakit"),
            "USER": os.getenv("POSTGRES_USER", "akaryakit"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "local_only_change_me"),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5433"),
            "CONN_MAX_AGE": 60,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "tr"
TIME_ZONE = "Europe/Istanbul"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Oturum ömrü: kurum içi yönetim paneli için 12 saat.
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# HTTPS sertleştirme. Trafik ters proxy arkasında HTTPS ile taşınır;
# geliştirmede (DEBUG) ve testlerde bu başlıklar devre dışıdır.
if not DEBUG and not RUNNING_TESTS:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

# Hata ve güvenlik olayları yapılandırılmış log'a yazılır; parola, bağlantı
# dizesi veya hassas kullanıcı verisi loglanmaz.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {"level": "INFO"},
        "django.request": {"level": "ERROR"},
        "django.security": {"level": "WARNING", "propagate": True},
        "fuel": {"level": "INFO"},
    },
}
