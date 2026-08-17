from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("emails", "0007_mailbox_imap_credentials"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailbox",
            name="send_paused",
            field=models.BooleanField(default=False),
        ),
    ]
