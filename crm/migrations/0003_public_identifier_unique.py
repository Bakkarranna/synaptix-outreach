"""Backfill empty public_identifier/linkedin_url, then enforce unique + non-nullable."""
from urllib.parse import quote, urlparse, unquote

from django.db import migrations, models


def _url_to_public_id(url):
    if not url:
        return None
    parts = urlparse(url.strip()).path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "in":
        return None
    return unquote(parts[1])


def _public_id_to_url(public_id):
    if not public_id:
        return ""
    return f"https://www.linkedin.com/in/{quote(public_id.strip('/'), safe='')}/"


def backfill(apps, schema_editor):
    Lead = apps.get_model("crm", "Lead")
    for lead in Lead.objects.filter(public_identifier=""):
        pid = _url_to_public_id(lead.linkedin_url)
        if not pid:
            pid = f"_unknown_{lead.pk}"
        print(f"  Backfill: Lead {lead.pk} '{lead.linkedin_url}' → public_identifier='{pid}'")
        lead.public_identifier = pid
        lead.save(update_fields=["public_identifier"])

    for lead in Lead.objects.filter(linkedin_url=""):
        url = _public_id_to_url(lead.public_identifier)
        print(f"  Backfill: Lead {lead.pk} linkedin_url='' → '{url}'")
        lead.linkedin_url = url
        lead.save(update_fields=["linkedin_url"])


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0002_rename_description_to_profile_data"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lead",
            name="public_identifier",
            field=models.CharField(max_length=200, unique=True),
        ),
        migrations.AlterField(
            model_name="lead",
            name="linkedin_url",
            field=models.URLField(max_length=200, unique=True),
        ),
    ]
