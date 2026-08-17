# openoutreach/core/management/commands/reembed_leads.py
"""Re-embed every lead after a change to ``discovery.TEXT_FIELDS``.

``TEXT_FIELDS`` defines the embedding space — ``profile_text_for`` builds both
the stored ``profile_text`` (the LLM qualifier's input) and the vector that
ranks it. Adding or dropping a field moves the space, so a store that already
holds leads would keep two populations — old vectors in the old space, new
leads in the new one — unless they are rebuilt. This command rebuilds both for
every lead from its stored row (``source_fields`` + the ``phone``/``website``
columns), restoring a single space.

Best-effort on the retrieving query terms: a discovered lead's embedding
carries ``keyword_terms(its first-touch node)``; those are rebuilt from
``Lead.discovered_by`` when the node still exists and omitted when it was
pruned. ``profile_text`` never carried the terms, so the LLM input is the same
either way.

Leads whose ``profile_text`` was an explicit CSV override are rebuilt from
firmographics like everyone else — re-import those rows with their
``profile_text`` column if the override must survive. Leads with nothing to
rebuild from (empty ``source_fields`` and columns) keep what they have.

    python manage.py reembed_leads
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from openoutreach.crm.models import Lead
from openoutreach.discovery import embed_profile, keyword_terms, profile_text_for


class Command(BaseCommand):
    help = ("Rebuild profile_text + embedding for every lead (run after a "
            "discovery.TEXT_FIELDS change).")

    def handle(self, *args, **options):
        rebuilt = 0
        kept = 0
        for lead in Lead.objects.all().iterator():
            row = dict(lead.source_fields or {})
            row["phone"] = lead.phone or ""
            row["website"] = lead.website or ""
            text = profile_text_for(row)
            if not text:
                # Nothing to rebuild from — a pre-source_fields lead keeps the
                # text it was created with rather than becoming unreadable.
                kept += 1
                continue

            query_terms = ""
            if lead.discovered_by is not None:
                query_terms = keyword_terms(lead.discovered_by.pairs)

            lead.profile_text = text
            lead.embedding_array = embed_profile(text, query_terms)
            lead.save(update_fields=["profile_text", "embedding"])
            rebuilt += 1

        self.stdout.write(self.style.SUCCESS(
            f"re-embedded {rebuilt} lead(s), kept {kept} with nothing to rebuild"))
