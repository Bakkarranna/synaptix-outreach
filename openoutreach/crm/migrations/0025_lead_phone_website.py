"""Add Lead.phone and Lead.website, backfilling phone from source_fields.

Scraped/imported rows previously carried a phone only in ``source_fields``
(kept there via ``discovery.ENRICHMENT_FIELDS``); this promotes it to a real
column and copies the stored values across. The website has no stored source
(the row carried ``company_domain`` instead), so there is nothing to backfill.
profile_text / embedding re-builds are a separate manual step
(``manage.py reembed_leads``) — the vector space must not change inside a
migration.
"""
from django.db import migrations, models


def _backfill_phone_from_source_fields(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    for lead in Lead.objects.all().iterator():
        phone = (lead.source_fields or {}).get("phone") or ""
        if phone:
            lead.phone = phone
            lead.save(update_fields=["phone"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0024_requalificationlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="lead",
            name="website",
            field=models.URLField(blank=True, default="", max_length=200),
        ),
        migrations.RunPython(_backfill_phone_from_source_fields,
                             migrations.RunPython.noop),
    ]
