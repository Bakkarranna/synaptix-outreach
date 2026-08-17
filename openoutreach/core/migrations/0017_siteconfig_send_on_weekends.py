from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_siteconfig_firecrawl_api_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="send_on_weekends",
            field=models.BooleanField(default=False),
        ),
    ]
