"""Minimal stubs for helper functions used by vendored DjangoCRM models."""

import secrets
from datetime import date


def get_today():
    return date.today()


def token_default():
    return secrets.token_hex(5)[:11]
