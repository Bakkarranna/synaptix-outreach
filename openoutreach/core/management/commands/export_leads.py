# openoutreach/core/management/commands/export_leads.py
"""Export every lead to a portable CSV (the ``import_leads`` schema).

The row shape uses the provider's own field names, so an export can be edited in
any spreadsheet and fed straight back in — or shipped to another tool, since a
CSV is the universal bridge out of (and into) any lead database.

    python manage.py export_leads                     # → data/leads-export-YYYYMMDD.csv
    python manage.py export_leads --path out.csv      # → out.csv
"""
from __future__ import annotations

import csv
from datetime import datetime

from django.core.management.base import BaseCommand

from openoutreach.core.db.leads import LEAD_EXPORT_FIELDS, lead_to_row


class Command(BaseCommand):
    help = "Export all leads to a portable CSV (round-trips with import_leads)."

    def add_arguments(self, parser):
        parser.add_argument("--path", default="",
                            help="Output CSV path (default: data/leads-export-YYYYMMDD.csv).")

    def handle(self, *args, **options):
        from openoutreach.crm.models import Lead

        path = options["path"] or f"data/leads-export-{datetime.now():%Y%m%d}.csv"
        qs = Lead.objects.order_by("-creation_date")
        n = 0
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=LEAD_EXPORT_FIELDS)
            w.writeheader()
            for lead in qs.iterator():
                w.writerow(lead_to_row(lead))
                n += 1
        self.stdout.write(self.style.SUCCESS(f"exported {n} lead(s) → {path}"))
