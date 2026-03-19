from django.apps import AppConfig


class TjaiAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tjai_app'

    def ready(self):
        import tjai_app.signals  # noqa: F401
