from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_delete_task_drop_action_fraction"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="firecrawl_api_key",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
