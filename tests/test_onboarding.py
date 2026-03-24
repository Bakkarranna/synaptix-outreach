# tests/test_onboarding.py
"""Tests for the DB-backed onboarding module."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from linkedin import onboarding
from linkedin.onboarding import ensure_onboarding, OnboardConfig


@pytest.mark.django_db
class TestEnsureOnboardingAlreadyExist:
    def test_noop_when_campaign_and_profile_exist(self, fake_session):
        """If Campaign and active LinkedInProfile exist → does nothing."""
        with (
            patch.object(onboarding, "_prompt_campaign") as mock_campaign,
            patch.object(onboarding, "_prompt_account") as mock_account,
            patch.object(onboarding, "_prompt_llm_config"),
            patch.object(onboarding, "_require_legal_acceptance"),
        ):
            ensure_onboarding()
            mock_campaign.assert_not_called()
            mock_account.assert_not_called()

    def test_runs_campaign_onboarding_when_no_campaign(self, db):
        """If no Campaign exists → runs _prompt_campaign."""
        from linkedin.models import Campaign
        Campaign.objects.all().delete()

        mock_campaign_obj = MagicMock()
        with (
            patch.object(onboarding, "_prompt_campaign", return_value=mock_campaign_obj) as mock_campaign,
            patch.object(onboarding, "_prompt_seed_urls"),
            patch.object(onboarding, "_prompt_account") as mock_account,
            patch.object(onboarding, "_prompt_llm_config"),
            patch.object(onboarding, "_require_legal_acceptance"),
        ):
            ensure_onboarding()
            mock_campaign.assert_called_once()
            mock_account.assert_called_once_with(mock_campaign_obj)

    def test_runs_account_onboarding_when_no_profile(self, db):
        """If Campaign exists but no active profile → runs _prompt_account."""
        from linkedin.models import Campaign, LinkedInProfile

        LinkedInProfile.objects.all().delete()
        campaign, _ = Campaign.objects.get_or_create(name="LinkedIn Outreach")

        with (
            patch.object(onboarding, "_prompt_campaign") as mock_campaign,
            patch.object(onboarding, "_prompt_account") as mock_account,
            patch.object(onboarding, "_prompt_llm_config"),
            patch.object(onboarding, "_require_legal_acceptance"),
        ):
            ensure_onboarding()
            mock_campaign.assert_not_called()
            mock_account.assert_called_once_with(campaign)


@pytest.mark.django_db
class TestEnsureLlmConfig:
    def test_noop_when_all_set(self):
        """If all LLM vars are already set → does nothing."""
        with (
            patch("linkedin.conf.LLM_API_KEY", "sk-test"),
            patch("linkedin.conf.AI_MODEL", "gpt-4o"),
            patch("linkedin.conf.LLM_API_BASE", "https://api.example.com"),
        ):
            onboarding._prompt_llm_config()
            # Should not prompt for input

    def test_prompts_and_writes_when_missing(self, tmp_path):
        """If LLM vars are missing → prompts and writes to .env."""
        env_file = tmp_path / ".env"
        inputs = iter(["sk-new-key", "gpt-4o", ""])

        with (
            patch("linkedin.conf.LLM_API_KEY", None),
            patch("linkedin.conf.AI_MODEL", None),
            patch("linkedin.conf.LLM_API_BASE", None),
            patch("linkedin.onboarding.ENV_FILE", env_file),
            patch("builtins.input", lambda _: next(inputs)),
        ):
            onboarding._prompt_llm_config()

        content = env_file.read_text()
        assert "LLM_API_KEY=sk-new-key" in content
        assert "AI_MODEL=gpt-4o" in content
        assert "LLM_API_BASE" not in content


@pytest.mark.django_db
class TestNonInteractiveOnboarding:
    def test_creates_campaign_and_profile(self, db, tmp_path):
        """Non-interactive mode creates records without prompting."""
        from linkedin.models import Campaign, LinkedInProfile

        Campaign.objects.all().delete()
        LinkedInProfile.objects.all().delete()

        env_file = tmp_path / ".env"
        config = OnboardConfig(
            linkedin_email="test@example.com",
            linkedin_password="secret123",
            campaign_name="Test Campaign",
            llm_api_key="sk-test",
            ai_model="gpt-4o",
        )

        with (
            patch("linkedin.onboarding.ENV_FILE", env_file),
            patch("builtins.input", side_effect=AssertionError("should not prompt")),
        ):
            ensure_onboarding(config)

        assert Campaign.objects.filter(name="Test Campaign").exists()
        assert LinkedInProfile.objects.filter(linkedin_username="test@example.com").exists()

        content = env_file.read_text()
        assert "LLM_API_KEY=sk-test" in content
        assert "AI_MODEL=gpt-4o" in content

    def test_uses_default_campaign_name(self, db, tmp_path):
        """Non-interactive mode falls back to DEFAULT_CAMPAIGN_NAME."""
        from linkedin.models import Campaign, LinkedInProfile

        Campaign.objects.all().delete()
        LinkedInProfile.objects.all().delete()

        config = OnboardConfig(
            linkedin_email="test@example.com",
            linkedin_password="secret123",
        )

        with (
            patch("linkedin.onboarding.ENV_FILE", tmp_path / ".env"),
            patch("builtins.input", side_effect=AssertionError("should not prompt")),
        ):
            ensure_onboarding(config)

        assert Campaign.objects.filter(name="LinkedIn Outreach").exists()

    def test_auto_accepts_legal(self, fake_session, db):
        """Non-interactive mode auto-accepts the legal notice."""
        from linkedin.models import LinkedInProfile

        profile = LinkedInProfile.objects.first()
        profile.legal_accepted = False
        profile.save(update_fields=["legal_accepted"])

        config = OnboardConfig(
            linkedin_email="test@example.com",
            linkedin_password="secret123",
        )

        with patch("builtins.input", side_effect=AssertionError("should not prompt")):
            ensure_onboarding(config)

        profile.refresh_from_db()
        assert profile.legal_accepted is True
