# openoutreach/dashboard/admin.py
"""Rebrand the Django admin shell for the Synaptix Studio Console.

Django's admin autodiscovers an ``admin`` module in every installed app, so this
module is imported at startup — the right place to set the site-wide header
strings. The visual shell is overridden by ``templates/admin/base_site.html``
in this app's templates directory.
"""
from django.contrib import admin

admin.site.site_header = "Synaptix Studio Console"
admin.site.site_title = "Synaptix Studio Console"
admin.site.index_title = "Studio operations"
