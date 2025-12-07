from django.contrib import admin
from django.urls import path

from tjai_app import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", views.api_health, name="api_health"),
    path("api/sync/push", views.sync_push, name="sync_push"),
    path("api/sync/pull", views.sync_pull, name="sync_pull"),
    path("api/command", views.api_command, name="api_command"),
    # Dashboard
    path("", views.dashboard, name="dashboard"),
    path("api/dashboard/calendar", views.dashboard_calendar, name="dashboard_calendar"),
    path("api/dashboard/status", views.dashboard_status, name="dashboard_status"),
]
