"""Rename the 'New' deal stage to 'Qualified'."""

from django.db import migrations


def forwards(apps, schema_editor):
    Stage = apps.get_model("crm", "Stage")
    Stage.objects.filter(name="New").update(name="Qualified")


def backwards(apps, schema_editor):
    Stage = apps.get_model("crm", "Stage")
    Stage.objects.filter(name="Qualified").update(name="New")


class Migration(migrations.Migration):
    dependencies = [
        ("linkedin", "0007_profileembedding"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
