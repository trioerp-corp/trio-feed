"""Trio settings.

Local trial: SQLite (no setup). Company server: set DATABASE_URL-style env vars for PostgreSQL:
GG_DB_ENGINE=postgres GG_DB_NAME=acc_erp GG_DB_USER=... GG_DB_PASSWORD=... GG_DB_HOST=localhost
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("GG_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("GG_SECRET_KEY", "")
if not SECRET_KEY:
    # the launcher always passes GG_SECRET_KEY; started by hand without it, keep a random key in data/ (never a fixed one)
    _kf = BASE_DIR / "data" / ".secret_key"
    try:
        SECRET_KEY = _kf.read_text(encoding="utf-8").strip()
    except OSError:
        SECRET_KEY = ""
    if not SECRET_KEY:
        import secrets as _secrets

        SECRET_KEY = _secrets.token_urlsafe(50)
        try:
            _kf.parent.mkdir(parents=True, exist_ok=True)
            _kf.write_text(SECRET_KEY, encoding="utf-8")
        except OSError:
            pass
TRAINING = os.environ.get("GG_TRAINING") == "1"
ALLOWED_HOSTS = os.environ.get("GG_ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("GG_CSRF_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.forms",
    "core",
    "ledger",
    "treasury",
    "inventory",
    "accruals",
    "pos",
    "gaming",
    "pos.clinic",
    "pos.staff",
    "pos.credit",
    "ui",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.CurrentUserMiddleware",
    "core.middleware.SetupGuard",
    "core.middleware.SuspendGuard",
    "core.twofa.TwoFactorMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
    "core.middleware.ShopEditionGuard",
    "core.middleware.LicenseGuard",
    "core.middleware.ModuleGuard",
    "core.middleware.FriendlyErrors",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context.company",
            ],
        },
    },
]

FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("GG_DB_ENGINE") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("GG_DB_NAME", "acc_erp"),
            "USER": os.environ.get("GG_DB_USER", "acc_erp"),
            "PASSWORD": os.environ.get("GG_DB_PASSWORD", ""),
            "HOST": os.environ.get("GG_DB_HOST", "localhost"),
            "PORT": os.environ.get("GG_DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("GG_SQLITE") or (BASE_DIR / "data" / "acc-erp.sqlite3"),
            # WAL: safer on a power cut and readers never block the cashier; IMMEDIATE: no «database is locked» mid-transaction
            "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE",
                        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;"},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية"), ("en", "English")]
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True
FORMAT_MODULE_PATH = ["config.formats"]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_AGE = 60 * 60 * 10  # 10 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# HTTPS (set GG_HTTPS=1 when the site is served through https, e.g. behind Cloudflare Tunnel)
if os.environ.get("GG_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

TWO_FACTOR_ENFORCE = os.environ.get("GG_2FA", "1") == "1"
if os.environ.get("GG_PROXY_HTTPS") == "1":  # behind Cloudflare Tunnel: trust its https header, keep local http working
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Errors go to the launcher's log (data\\trio.log reads stderr) and to data/errors.log, so a fault at a client can be read later
(BASE_DIR / "data").mkdir(exist_ok=True)
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"line": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "line", "level": "WARNING"},
                 "file": {"class": "logging.handlers.RotatingFileHandler", "filename": str(BASE_DIR / "data" / "errors.log"), "maxBytes": 1_000_000,
                          "backupCount": 2, "encoding": "utf-8", "delay": True, "formatter": "line", "level": "WARNING"}},
    "root": {"handlers": ["stderr"] if "pytest" in sys.modules else ["stderr", "file"], "level": "WARNING"},
    # refused pages (403/404) are not faults: only real errors from requests are kept
    "loggers": {"django.security.DisallowedHost": {"handlers": [], "propagate": False}, "django.request": {"level": "ERROR"}},
}
