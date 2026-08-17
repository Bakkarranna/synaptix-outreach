"""WSGI entrypoint for the Synaptix Studio Console.

Used by gunicorn in the production compose stack; dev uses ``runserver``.
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openoutreach.settings")

application = get_wsgi_application()
