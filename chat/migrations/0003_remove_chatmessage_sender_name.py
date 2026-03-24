from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_add_linkedin_sync_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="chatmessage",
            name="sender_name",
        ),
    ]
