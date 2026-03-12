# linkedin/migrations/0013_rename_is_partner_to_is_freemium.py
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0012_add_legal_accepted_to_linkedinprofile"),
    ]

    operations = [
        migrations.RenameField(
            model_name="campaign",
            old_name="is_partner",
            new_name="is_freemium",
        ),
    ]
