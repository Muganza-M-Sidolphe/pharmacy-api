from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        # Register startup signals (e.g., bootstrap default super admin after migrate).
        from . import signals  # noqa: F401
