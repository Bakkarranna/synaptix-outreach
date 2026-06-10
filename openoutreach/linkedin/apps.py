# openoutreach/linkedin/apps.py
from django.apps import AppConfig


class LinkedInConfig(AppConfig):
    name = "openoutreach.linkedin"
    label = "linkedin"
    default_auto_field = "django.db.models.BigAutoField"
