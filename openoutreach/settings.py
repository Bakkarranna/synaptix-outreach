# openoutreach/settings.py
"""
Settings for the Synaptix Studio Console — the OpenOutreach engine rebranded
as an internal ops tool (Django ORM + Admin + the operator dashboard).
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

# The agents drive async pydantic-ai from a sync boundary (core/llm.py), so an
# event loop can be live on the thread when the ORM is touched. We only use the
# ORM synchronously, so Django's async-safety guard is safe to relax.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

ROOT_DIR = Path(__file__).resolve().parent.parent

BASE_DIR = ROOT_DIR

_DEV_SECRET_KEY = "openoutreach-local-dev-key-change-in-production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or _DEV_SECRET_KEY

# ``DJANGO_DEBUG=0`` in production (compose.prod.yml); dev stays verbose.
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

if not DEBUG and SECRET_KEY == _DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG=0 "
        "(see deploy/env.example and deploy/README.md)."
    )

# Comma-separated via env in production (VM IP / tunnel hostname); dev stays open.
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()
]

# HTTPS origins (e.g. behind Cloudflare Tunnel on a custom domain).
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# WhiteNoise (production only) serves the collected static files from gunicorn
# so the console has no separate nginx dependency. Harmless when absent in dev.
try:
    import whitenoise  # noqa: F401

    INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
except ImportError:
    pass

INSTALLED_APPS = [
    "django.contrib.sites",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "openoutreach.crm.apps.CrmConfig",
    "openoutreach.chat.apps.ChatConfig",
    "openoutreach.core.apps.CoreConfig",
    "openoutreach.legacy.apps.LegacyConfig",
    "openoutreach.emails.apps.EmailsConfig",
    "openoutreach.dashboard.apps.DashboardConfig",
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
]

ROOT_URLCONF = "openoutreach.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-level templates load before app templates, so the branded
        # ``templates/admin/base_site.html`` shadows contrib.admin's default.
        "DIRS": [ROOT_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# `manage.py --db PATH` sets OPENOUTREACH_DB; otherwise the bundled data dir.
DATABASE_PATH = Path(os.environ.get("OPENOUTREACH_DB") or ROOT_DIR / "data" / "db.sqlite3").expanduser()
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(DATABASE_PATH),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_ID = 1

STATIC_URL = "/static/"
STATIC_ROOT = ROOT_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = ROOT_DIR / "media"

LOGIN_URL = "/admin/login/"

DEFAULT_FROM_EMAIL = "noreply@localhost"
EMAIL_SUBJECT_PREFIX = "Synaptix Studio: "

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

TESTING = sys.argv[1:2] == ["test"]
