# openoutreach/core/management/commands/import_leads.py
"""Import leads from a CSV (the ``export_leads`` schema) into the store.

Each row becomes an embedded Lead awaiting qualification, keyed on its profile
URL — the same path discovery uses, so imported leads flow through the funnel
(LLM qualify → GP rank → find email) exactly like discovered ones. Rows whose
URL already exists are skipped (idempotent re-import). ``profile_text`` is the
LLM judge's input: take a column if the file carries one, else build it from the
firmographic columns; leads with neither will sit unqualified (reported).

    python manage.py import_leads leads.csv
    python manage.py import_leads leads.csv --country fr
"""
from __future__ import annotations

import csv
import sys

from django.core.management.base import BaseCommand

from openoutreach.core.db.leads import import_leads_from_rows


class Command(BaseCommand):
    help = "Import leads from a CSV into the store (deduped on profile URL)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV file to import.")
        parser.add_argument("--country", default="",
                            help="ISO country code to stamp on rows without one "
                                 "(drives the campaign geo-gate).")

    def handle(self, *args, **options):
        path = options["path"]
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except OSError as e:
            self.stderr.write(self.style.ERROR(f"cannot read {path}: {e}"))
            sys.exit(1)
        if not rows:
            self.stderr.write(self.style.ERROR(f"{path} has no rows."))
            sys.exit(1)

        self.stdout.write(f"importing {len(rows)} row(s) from {path} …")
        report = import_leads_from_rows(rows, default_country=options["country"])
        self.stdout.write(self.style.SUCCESS(str(report)))
        if report.no_text:
            self.stdout.write(self.style.WARNING(
                f"{report.no_text} new lead(s) have no profile text and will sit "
                "unqualified until text is added."))
