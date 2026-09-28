from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, ""),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_DATABASE_URL=(str, ""),
    DJANGO_FORCE_SCRIPT_NAME=(str, ""),
    DJANGO_STATIC_URL=(str, "/static/"),
    DJANGO_SECURE_SSL_REDIRECT=(bool, False),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_LOG_LEVEL=(str, "INFO"),
    DJANGO_CSRF_COOKIE_PATH=(str, ""),
    DJANGO_SESSION_COOKIE_PATH=(str, ""),
    DJANGO_CSRF_COOKIE_NAME=(str, "tjai_csrftoken"),
    DJANGO_SESSION_COOKIE_NAME=(str, "tjai_sessionid"),
)

BASE_DIR = Path(__file__).resolve().parents[2]

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY") or "insecure-dev-key-change-me"
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# If mounted at subpath /tjai
FORCE_SCRIPT_NAME = env("DJANGO_FORCE_SCRIPT_NAME") or None
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "tjai_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "tjai_app.middleware.MCPAuthMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "tjai_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.static",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tjai_app.context_processors.health_status",
            ],
        },
    },
]

WSGI_APPLICATION = "tjai_project.wsgi.application"
ASGI_APPLICATION = "tjai_project.asgi.application"

# Database (Postgres only)
_db_cfg = env.db("DJANGO_DATABASE_URL") if env("DJANGO_DATABASE_URL") else None
if not _db_cfg:
    raise ImproperlyConfigured(
        "DJANGO_DATABASE_URL is required and must point to a Postgres database."
    )
engine = _db_cfg.get("ENGINE", "")
if engine.endswith("sqlite3") or engine == "django.db.backends.sqlite3":
    raise ImproperlyConfigured(
        "SQLite is not supported. Configure Postgres in DJANGO_DATABASE_URL."
    )
_db_cfg["CONN_HEALTH_CHECKS"] = True
DATABASES = {"default": _db_cfg}

# Static files
STATIC_URL = env("DJANGO_STATIC_URL")
STATIC_ROOT = BASE_DIR / "staticfiles"

# Cookie paths: default to subpath when FORCE_SCRIPT_NAME is set
CSRF_COOKIE_NAME = env("DJANGO_CSRF_COOKIE_NAME")
SESSION_COOKIE_NAME = env("DJANGO_SESSION_COOKIE_NAME")
_subpath = FORCE_SCRIPT_NAME or ""
if not env("DJANGO_CSRF_COOKIE_PATH") and _subpath:
    CSRF_COOKIE_PATH = _subpath
else:
    CSRF_COOKIE_PATH = env("DJANGO_CSRF_COOKIE_PATH") or "/"

if not env("DJANGO_SESSION_COOKIE_PATH") and _subpath:
    SESSION_COOKIE_PATH = _subpath
else:
    SESSION_COOKIE_PATH = env("DJANGO_SESSION_COOKIE_PATH") or "/"

# Security
SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'

# Logging
LOG_LEVEL = env("DJANGO_LOG_LEVEL")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "db": {
            "class": "tjai_app.db_log_handler.DbLogHandler",
            "level": "WARNING",
            "source": "django",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "tjai_app": {"handlers": ["console", "db"], "level": "WARNING", "propagate": False},
    },
}

# MCP (Model Context Protocol) Configuration. Served by tjai_project.mcp_asgi.
TJAI_MCP_SERVER_CONFIG = {
    "name": "tjai",
    "stateless": True,
    "instructions": """TJAI provides personal memory, guidance, tasks, dialog and peer messaging.
At startup load get_profile, then get_ai_guidance for the project, location_name
and provider audience. Follow next_offset until complete=true for both tools.
Check every result for an error. List reads return JSON text; parse it once.
For boot use get_todos(status="inflight", summary_only=True) and read only the
assigned activity in full. Daily health: get_entry_by_entry_id("daily-YYYY-MM-DD",
heading="Health Assessment", level=2). Fetch dialog only when relevant to the task.
Nontrivial entries require data.entry_id with a readable kebab-case slug; use it
in links. Read logs through get_logs. Prefer surgical edits; deletion requires
operator approval. List reads use limit/offset pagination where supported;
a full page can mean more results. Consult each tool's own parameters and limits.""",
}


# Telegram Mini App
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_USER_ID = env("TELEGRAM_USER_ID", default="")
