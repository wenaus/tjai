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
- get_memories(context, limit, offset, start_date, end_date): Get memory entries.
  Call unfiltered to see recent activity.
- get_bookmarks(context, limit, offset, start_date, end_date): Get saved bookmarks.
- get_logs(source, level, contains, ref, start_date, end_date, limit): Read
  application log (AppLog) rows — agent/script/server logs (the Agent Log page
  data). For operational/diagnostic questions; AppLog is not an Entry, so the
  entry-query tools can't reach it. Use this instead of raw SQL.
- search_entries(query, kind, context, limit, offset): Search or list entries. Omit
  query for structured listing/filtering by kind, context, or date. Use
  order_by='rank' only with a non-empty query.
- get_named_entries(name, context): Get entries by @name, or list all named.
- get_entry(entry_id): Get a single entry by UUID.
- get_entry_by_entry_id(entry_id): Find entry by human-readable entry_id.
- create_entry(content, kind, context, name, tags, event_date, priority, status,
  create_context): Add new entries. Context must exist unless create_context=True.
- edit_entry_metadata(entry_id, tags, status, priority, ...): Edit metadata
  fields only (no content).
- replace_entry_content(entry_id, content): Replace an entry's content
  (destructive full rewrite).
- append_entry_content(entry_id, content, separator): Append text to an
  entry's existing content (preserves existing).
- copy_calendar_entry(entry_id, event_date, event_time): Copy a journal entry
  to a new date, preserving all fields.
- change_entry_kind(entry_id, kind): Change entry type without modifying
  content or timestamp.
- run_action(entry_id): Execute an action entry immediately.
- delete_entry(entry_id): Soft delete an entry. Requires user approval.
- create_goal(content, context, tags, priority, status, create_context, data):
  Create a goal entry. Goals are the organizing nodes of the knowledge graph.
- get_goal(entry_id): Get a goal entry with all its relations.
- create_relation(entry1_id, entry2_id, relation_type, data): Create a relation
  between any two entries. One relation per pair; use data field for metadata.
- edit_relation(relation_id, relation_type, data): Edit a relation's type/data.
- delete_relation(relation_id): Delete a relation.
- get_relations(entry_id): Get all relations for an entry.
- get_relation_graph(entry_id, depth, kinds): Traverse the relation graph from
  an entry. BFS up to depth hops, optional kind filtering on results.

Entry types: memory (notes), todo (tasks), journal (calendar events), profile
(user facts), ai (AI instructions), bookmark (URLs), list (lists), action,
goal (organizing nodes of the knowledge graph).
Valid statuses: active, done, blocked, archive. Priority: positive integers (1=highest).

Error handling: All tools return {"error": "message"} on validation failures.
Always check for "error" key in response before processing results.

Pagination: For read tools that accept limit and offset, limit is a page size,
not a requirement. The hard maximum page size is 500. If a returned list has
exactly the requested limit, more results may exist; at your discretion, fetch
the next page with offset += limit until a page returns fewer than the requested
limit or the requested window is complete.""",
}


# Telegram Mini App
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_USER_ID = env("TELEGRAM_USER_ID", default="")
