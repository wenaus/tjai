from django.contrib import admin
from django.urls import path

from tjai_app import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", views.api_health, name="api_health"),
    path("api/sync/push", views.sync_push, name="sync_push"),
    path("api/sync/pull", views.sync_pull, name="sync_pull"),
    path("api/command", views.api_command, name="api_command"),
    # Public landing
    path("", views.public_home, name="public_home"),
    # Login/Logout
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Dashboard (protected by login)
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/dashboard/calendar", views.dashboard_calendar, name="dashboard_calendar"),
    path("api/dashboard/status", views.dashboard_status, name="dashboard_status"),
    path("api/dashboard/named", views.dashboard_named, name="dashboard_named"),
    path("entry/<str:entry_id>/", views.entry_detail, name="entry_detail"),
    path("context/<str:context_name>/", views.context_entries, name="context_entries"),
    path("tag/<str:tag_name>/", views.tag_entries, name="tag_entries"),
    path("kind/<str:kind_name>/", views.kind_entries, name="kind_entries"),
]
