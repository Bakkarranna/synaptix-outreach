# openoutreach/emails/models.py
"""Mailbox: one SMTP sending inbox, imported from the provider's creds export."""
from __future__ import annotations

from django.db import models


class Mailbox(models.Model):
    """One SMTP inbox. host/port default to IceMail's Google Workspace boxes.

    A box that fails SMTP auth on import is stored ``active=False`` — the
    provider has no health API, so bad credentials only surface as an auth error.
    """

    host = models.CharField(max_length=255, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=320, unique=True)
    password = models.CharField(max_length=255)
    from_address = models.EmailField(max_length=320)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Mailboxes"

    def __str__(self):
        return self.from_address or self.username
