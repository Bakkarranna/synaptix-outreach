# openoutreach/core/apps.py
from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "openoutreach.core"
    label = "core"
    default_auto_field = "django.db.models.BigAutoField"
