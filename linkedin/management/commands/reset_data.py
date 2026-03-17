from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Delete all Leads, Companies, Deals, ProfileEmbeddings, "
        "TheFiles, ActionLogs, reset SearchKeywords, and remove GP model files. "
        "Keeps Campaigns, Departments, LinkedInProfiles."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        from common.models import TheFile
        from crm.models import Company, Deal, Lead

        from linkedin.conf import MODELS_DIR
        from linkedin.models import ActionLog, ProfileEmbedding, SearchKeyword

        counts = {
            "Leads": Lead.objects.count(),
            "Companies": Company.objects.count(),
            "Deals": Deal.objects.count(),
            "ProfileEmbeddings": ProfileEmbedding.objects.count(),
            "TheFiles": TheFile.objects.count(),
            "ActionLogs": ActionLog.objects.count(),
            "SearchKeywords (to reset)": SearchKeyword.objects.count(),
        }

        model_files = list(MODELS_DIR.glob("*.joblib"))

        self.stdout.write("Will delete:")
        for name, count in counts.items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(f"  Model files: {len(model_files)}")
        for f in model_files:
            self.stdout.write(f"    {f}")

        if not options["yes"]:
            confirm = input("\nProceed? [y/N] ")
            if confirm.lower() != "y":
                self.stdout.write("Aborted.")
                return

        # Order matters: delete dependents first
        Deal.objects.all().delete()
        TheFile.objects.all().delete()
        ProfileEmbedding.objects.all().delete()
        ActionLog.objects.all().delete()
        Company.objects.all().delete()
        Lead.objects.all().delete()

        # Reset search keywords to unused
        SearchKeyword.objects.update(used=False, used_at=None)

        # Remove GP model files
        for f in model_files:
            f.unlink()
            self.stdout.write(f"  Removed {f.name}")

        self.stdout.write(self.style.SUCCESS("Reset complete."))
