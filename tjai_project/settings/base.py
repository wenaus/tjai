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
    AUTH0_DOMAIN=(str, ""),
    AUTH0_CLIENT_ID=(str, ""),
    AUTH0_CLIENT_SECRET=(str, ""),
    AUTH0_API_IDENTIFIER=(str, ""),
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
    "mcp_server",
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
DATABASES = {"default": _db_cfg}

# Static files
STATIC_URL = env("DJANGO_STATIC_URL")
STATIC_ROOT = BASE_DIR / "staticfiles"

# Cookie paths: default to subpath when FORCE_SCRIPT_NAME is set
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
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}

# MCP (Model Context Protocol) Configuration
DJANGO_MCP_GLOBAL_SERVER_CONFIG = {
    "name": "tjai",
    "instructions": """tjai is a personal AI memory and task management system.

Tools:
- get_ai_guidance(context): Get behavioral instructions for AI assistants.
  Returns general guidance plus context-specific guidance if context provided.
  CALL THIS when starting work on any project.
- get_profile(): Get personal facts and preferences about the user.
- get_todos(context, status, include_done): Get task list with filtering.
  Valid statuses: active, done, blocked, archive.
- get_calendar(start_date, end_date, context, days): Get calendar entries
  for a date range. Dates in ISO or YYYYMMDD format.
- list_contexts(): List all projects/topics to discover what contexts exist.
- search_entries(query, kind, context, limit): Full-text search across entries.
- create_entry(content, kind, context, name, tags, event_date, priority, status,
  create_context): Add new entries. Context must exist unless create_context=True.
- delete_entry(entry_id): Soft delete an entry. Requires user approval.

Entry types: memory (notes), todo (tasks), journal (calendar events), profile
(user facts), ai (AI instructions), bookmark (URLs), list (lists).
Valid statuses: active, done, blocked, archive. Priority: positive integers (1=highest).

Error handling: All tools return {"error": "message"} on validation failures.
Always check for "error" key in response before processing results.""",
}

# MCP endpoint path (empty string since we mount at /mcp/ in urls.py)
DJANGO_MCP_ENDPOINT = ""

# Auth0 OAuth 2.1 Configuration (for Claude.ai MCP integration)
AUTH0_DOMAIN = env("AUTH0_DOMAIN")
AUTH0_CLIENT_ID = env("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = env("AUTH0_CLIENT_SECRET")
AUTH0_API_IDENTIFIER = env("AUTH0_API_IDENTIFIER")
AUTH0_ALGORITHMS = ["RS256"]
