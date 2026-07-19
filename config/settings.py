"""
Django settings for the DT Beach Supply Control project.

Every value that differs between development and production is read from
the environment (see .env.example / .env.production.example). Nothing here
should require a code change to move from dev -> staging -> production.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    val = os.environ.get(name, default)
    return [item.strip() for item in val.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core / security
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")

DEBUG = env_bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "rest_framework",
    "django_htmx",
    # Domain apps (modular monolith - one app per bounded context)
    "apps.core",
    "apps.accounts",
    "apps.projects",
    "apps.documents",
    "apps.procurement",
    "apps.items",
    "apps.shipments",
    "apps.matching",
    "apps.receiving",
    "apps.inventory",
    "apps.requests",
    "apps.cost",
    "apps.workflow",
    "apps.audit",
    "apps.reports",
    "apps.tools",
    "apps.customs",
    "apps.claims",
    "apps.labels",
    "apps.drawings",
    "apps.fieldissues",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.audit.middleware.CurrentUserMiddleware",
    "apps.core.middleware.EnsureProfileMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.org_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "dtbeach"),
        "USER": os.environ.get("POSTGRES_USER", "dtbeach"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "dtbeach"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

# Allow a pure-sqlite override for lightweight local checks / CI without
# docker. Never used in docker-compose (which always sets POSTGRES_HOST).
# Guarded by `not DEBUG` as defense in depth: a stray dev .env baked into a
# production image (see .dockerignore) must never silently downgrade a
# production deployment to SQLite.
if DEBUG and env_bool("USE_SQLITE_FOR_TESTS", default=False):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard-home"
LOGOUT_REDIRECT_URL = "login"

# ---------------------------------------------------------------------------
# Internationalization — Spanish business UI by default (spec section 30)
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "es"
LANGUAGES = [
    ("es", "Español"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "America/Santo_Domingo")

USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Defensive: a vendored third-party asset with a dangling sourceMappingURL
# comment should never hard-fail collectstatic in production.
WHITENOISE_MANIFEST_STRICT = False

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Documents are never served directly from MEDIA as public files; downloads
# go through an authenticated view (apps.documents.views.download). This
# root is only where the storage abstraction persists bytes on disk.
DOCUMENT_STORAGE_ROOT = os.environ.get("DOCUMENT_STORAGE_ROOT", str(BASE_DIR / "protected_documents"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "50"))
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024

ALLOWED_UPLOAD_EXTENSIONS = [
    ".xlsx", ".xls", ".csv", ".pdf", ".png", ".jpg", ".jpeg", ".docx",
]

# ---------------------------------------------------------------------------
# Django REST Framework — versioned API per spec section 31
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [],
}

# ---------------------------------------------------------------------------
# Security hardening (spec section 32)
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # must be readable by htmx to attach the token
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}

# ---------------------------------------------------------------------------
# Application-level configuration (organization defaults, spec-derived)
# ---------------------------------------------------------------------------

DTBEACH = {
    # Priority 0 gate defaults — configurable per spec section 9.4, 18.
    "DEFAULT_PROVISIONAL_RECEIPT_DEADLINE_DAYS": int(os.environ.get("PROVISIONAL_RECEIPT_DEADLINE_DAYS", "7")),
    "DEFAULT_STORAGE_DURATION_WARNING_DAYS": int(os.environ.get("STORAGE_DURATION_WARNING_DAYS", "30")),
    "DEFAULT_CLAIM_WINDOW_DAYS": int(os.environ.get("CLAIM_WINDOW_DAYS", "20")),
    "BASE_CURRENCY": os.environ.get("BASE_CURRENCY", "USD"),
}
