from django.contrib import admin
from django.urls import path, include

from tjai_app import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("mcp/", include("mcp_server.urls")),
    path("mcp", include("mcp_server.urls")),  # Also handle without trailing slash
    # OAuth 2.0 well-known endpoints
    path(".well-known/oauth-protected-resource", views.oauth_protected_resource, name="oauth_protected_resource"),
    path("api/health", views.api_health, name="api_health"),
    path("api/sync/push", views.sync_push, name="sync_push"),
    path("api/sync/pull", views.sync_pull, name="sync_pull"),
    path("api/command", views.api_command, name="api_command"),
    path("api/entry/<str:entry_id>", views.api_delete_entry, name="api_delete_entry"),
    path("api/add-journal", views.api_add_journal, name="api_add_journal"),
    path("api/add-bookmark", views.api_add_bookmark, name="api_add_bookmark"),
    path("api/dialog", views.api_dialog, name="api_dialog"),
    path("api/bulk-import", views.api_bulk_import, name="api_bulk_import"),
    # Public landing
    path("", views.public_home, name="public_home"),
    # Login/Logout
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Dashboard (protected by login)
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/dashboard/calendar", views.dashboard_calendar, name="dashboard_calendar"),
    path("api/dashboard/status", views.dashboard_status, name="dashboard_status"),
    path("api/dashboard/search", views.dashboard_search, name="dashboard_search"),
    path("api/dashboard/named", views.dashboard_named, name="dashboard_named"),
    path("briefing/", views.daily_briefing, name="daily_briefing"),
    path("api/briefing/dates", views.daily_briefing_data, name="daily_briefing_data"),
    path("api/briefing/content", views.daily_briefing_content, name="daily_briefing_content"),
    path("agent-log/", views.agent_log, name="agent_log"),
    path("api/agent-log", views.agent_log_data, name="agent_log_data"),
    path("api/entry/<uuid:entry_id>/save", views.api_entry_save, name="api_entry_save"),
    path("entry/", views.entry_detail, name="entry_detail_query"),
    path("entry/<path:entry_id>/", views.entry_detail, name="entry_detail"),
    path("context/<str:context_name>/", views.context_entries, name="context_entries"),
    path("poetry/author/<path:author_name>/", views.poetry_author_entries, name="poetry_author_entries"),
    path("tag/<str:tag_name>/", views.tag_entries, name="tag_entries"),
    path("kind/<str:kind_name>/", views.kind_entries, name="kind_entries"),
    # Telegram Mini App
    path("m/", views.miniapp, name="miniapp"),
    path("api/tg-auth", views.tg_auth, name="tg_auth"),
    path("api/contexts", views.api_contexts_list, name="api_contexts_list"),
    path("api/context/<str:context_name>/entries", views.api_context_entries, name="api_context_entries"),
    path("api/entry/<uuid:entry_id>/content", views.api_entry_content, name="api_entry_content"),
]
