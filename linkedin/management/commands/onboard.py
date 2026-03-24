import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run onboarding (interactive or non-interactive with CLI flags)."

    def add_arguments(self, parser):
        parser.add_argument("--linkedin-email", default="")
        parser.add_argument("--linkedin-password", default="")
        parser.add_argument("--campaign-name", default="")
        parser.add_argument("--llm-api-key", default="")
        parser.add_argument("--ai-model", default="")
        parser.add_argument("--llm-api-base", default="")
        parser.add_argument("--non-interactive", action="store_true")

    def handle(self, *args, **options):
        from linkedin.onboarding import OnboardConfig, ensure_onboarding

        if not options["non_interactive"]:
            ensure_onboarding()
            return

        if not options["linkedin_email"]:
            self.stderr.write("--linkedin-email is required in non-interactive mode")
            sys.exit(1)
        if not options["linkedin_password"]:
            self.stderr.write("--linkedin-password is required in non-interactive mode")
            sys.exit(1)

        ensure_onboarding(OnboardConfig(
            linkedin_email=options["linkedin_email"],
            linkedin_password=options["linkedin_password"],
            campaign_name=options["campaign_name"],
            llm_api_key=options["llm_api_key"],
            ai_model=options["ai_model"],
            llm_api_base=options["llm_api_base"],
        ))
