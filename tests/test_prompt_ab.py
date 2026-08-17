# tests/test_prompt_ab.py
"""Tests for the ``prompt_ab`` command — the strict-vs-loose prompt A/B.

Locks down the read-only guarantee (no deals/labels written), the oldest-first
sampling of both pools (funnel and rejections), the dry-run (renders, calls no
LLM), and the side-by-side summary + CSV output. The LLM is always mocked —
these tests judge the harness, not the model.
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from openoutreach.crm.models import DealState, Outcome
from tests.factories import DealFactory, LeadFactory


def _run(*args, **kwargs):
    out = StringIO()
    call_command("prompt_ab", *args, stdout=out, **kwargs)
    return out.getvalue()


def _sample_of(n=3, **kw):
    return [_make_lead(f"https://www.linkedin.com/in/lead-{i}/", f"profile {i}", **kw)
            for i in range(n)]


def _make_lead(profile_url, profile_text="engineer at acme", email=None):
    from openoutreach.crm.models import Lead
    import numpy as np

    return Lead.objects.create(
        profile_url=profile_url,
        profile_text=profile_text,
        email=email,
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )


@pytest.mark.django_db
class TestPoolSampling:
    def test_oldest_first_funnel_sample(self, campaign):
        _sample_of()
        out = _run("--campaign", campaign.pk, "--sample", "2", "--dry-run")
        assert "lead-0" in out and "lead-1" in out
        assert "lead-2" not in out

    def test_pool_excludes_leads_already_dealt_in_this_campaign(self, campaign):
        dealt = _make_lead("https://www.linkedin.com/in/dealt/")
        from openoutreach.crm.models import Deal

        Deal.objects.create(lead=dealt, campaign=campaign, state=DealState.QUALIFIED)
        _make_lead("https://www.linkedin.com/in/fresh/")
        out = _run("--campaign", campaign.pk, "--dry-run")
        assert "dealt" not in out
        assert "fresh" in out


@pytest.mark.django_db
class TestRejectsSampling:
    def test_rejects_sample_carries_the_original_reason(self, campaign):
        rej = _make_lead("https://www.linkedin.com/in/rej/", "CEO of a services firm")
        DealFactory(lead=rej, campaign=campaign, state=DealState.FAILED,
                    outcome=Outcome.WRONG_FIT, reason="Consultant, not founder.")
        DealFactory(campaign=campaign, state=DealState.EMAILED)  # not a rejection

        out = _run("--campaign", campaign.pk, "--source", "rejects", "--dry-run")
        assert "originally rejected: Consultant, not founder." in out
        # only the wrong_fit deal is sampled — the emailed deal is not
        assert "rej" in out

    def test_dry_run_renders_both_prompts_without_calling_llm(self, campaign):
        _make_lead("https://www.linkedin.com/in/lead-0/")
        with patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock:
            out = _run("--campaign", campaign.pk, "--dry-run")
        mock.assert_not_called()
        assert "DRY RUN" in out
        assert "Strict template renders as:" in out
        assert "Loose template renders as:" in out


@pytest.mark.django_db
class TestVerdictComparison:
    def test_summary_counts_disagreements_and_rescues(self, campaign):
        _make_lead("https://www.linkedin.com/in/a/", "A")   # both accept
        _make_lead("https://www.linkedin.com/in/b/", "B")   # both reject
        _make_lead("https://www.linkedin.com/in/c/", "C")   # loose rescues (strict reject)
        _make_lead("https://www.linkedin.com/in/d/", "D")   # strict-only

        verdicts = {
            "A": (1, 1), "B": (0, 0), "C": (0, 1), "D": (1, 0),
        }

        def fake_llm(profile_text, product_docs, campaign_target, template):
            strict_label, loose_label = verdicts[profile_text]
            label = strict_label if "strict" in template else loose_label
            return (label, f"reason under {template}")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", side_effect=fake_llm),
            patch("time.sleep"),
        ):
            out = _run("--campaign", campaign.pk, "--sample", "4")

        assert "both accept:     1" in out
        assert "both reject:     1" in out
        assert "strict-only:     1" in out
        assert "loose-only:      1" in out
        assert "disagree:        2" in out
        assert "≠" in out  # disagreeing rows are flagged

    def test_rejects_source_reports_rescue_rate(self, campaign):
        rej = _make_lead("https://www.linkedin.com/in/rej/", "CEO of a services firm")
        DealFactory(lead=rej, campaign=campaign, state=DealState.FAILED,
                    outcome=Outcome.WRONG_FIT, reason="Consultant, not founder.")

        def fake_llm(profile_text, product_docs, campaign_target, template):
            # strict keeps rejecting, loose accepts — the rescue case
            label = 0 if "strict" in template else 1
            return (label, f"reason under {template}")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", side_effect=fake_llm),
            patch("time.sleep"),
        ):
            out = _run("--campaign", campaign.pk, "--source", "rejects")

        assert "1 of 1 previously-rejected lead(s) (100%) now qualify" in out

    def test_writes_csv_report(self, campaign, tmp_path):
        _make_lead("https://www.linkedin.com/in/a/", "A")
        csv_path = tmp_path / "ab.csv"

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "good fit")),
            patch("time.sleep"),
        ):
            out = _run("--campaign", campaign.pk, "--csv", str(csv_path))

        assert str(csv_path) in out
        text = csv_path.read_text(encoding="utf-8")
        assert "lead_id,identity,strict_verdict,strict_reason,loose_verdict,loose_reason" in text
        assert "accept" in text

    def test_read_only_no_deals_or_labels_written(self, campaign):
        _make_lead("https://www.linkedin.com/in/a/", "A")
        from openoutreach.crm.models import Deal

        before_deals = Deal.objects.count()

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "good fit")),
            patch("time.sleep"),
        ):
            _run("--campaign", campaign.pk)

        assert Deal.objects.count() == before_deals


@pytest.mark.django_db
class TestApply:
    """--apply: re-judge the rejection pool with the loose prompt and flip the
    now-accepted deals back to QUALIFIED — the same state change the Reviews
    page makes by hand, en masse."""

    @staticmethod
    def _reject(campaign, profile_url, reason, profile_text):
        lead = _make_lead(profile_url, profile_text)
        return DealFactory(lead=lead, campaign=campaign, state=DealState.FAILED,
                           outcome=Outcome.WRONG_FIT, reason=reason)

    def test_requalifies_accepts_and_leaves_rejects(self, campaign):
        rescued = self._reject(campaign, "https://x/rescued/", "Consultant, not founder.",
                               "CEO of a services firm")
        kept = self._reject(campaign, "https://x/kept/", "Stealth, nothing to reach.",
                            "Stealth startup")

        def fake_llm(profile_text, product_docs, campaign_target, template):
            return (1 if profile_text.startswith("CEO") else 0,
                    f"reason under {template}")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", side_effect=fake_llm),
            patch("time.sleep"),
        ):
            out = _run("--campaign", campaign.pk, "--source", "rejects", "--apply", "--yes")

        rescued.refresh_from_db()
        kept.refresh_from_db()
        assert rescued.state == DealState.QUALIFIED
        assert rescued.reason == "Consultant, not founder."  # audit trail kept
        assert kept.state == DealState.FAILED
        assert "1" in out and "back in the qualified pool" in out

    def test_apply_requires_rejects_source(self, campaign):
        _make_lead("https://x/a/", "A")
        from django.core.management import CommandError

        with pytest.raises(CommandError):
            _run("--campaign", campaign.pk, "--apply", "--yes")

    def test_apply_requires_confirmation_without_yes(self, campaign):
        deal = self._reject(campaign, "https://x/rescued/", "Consultant.", "CEO of a firm")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "good fit now")),
            patch("time.sleep"),
            patch("builtins.input", return_value="no"),
        ):
            out = _run("--campaign", campaign.pk, "--source", "rejects", "--apply")

        deal.refresh_from_db()
        assert deal.state == DealState.FAILED  # nothing was changed
        assert "Aborted" in out

    def test_apply_skips_leads_without_profile_text(self, campaign):
        lead = _make_lead("https://x/blank/", profile_text="")
        deal = DealFactory(lead=lead, campaign=campaign, state=DealState.FAILED,
                           outcome=Outcome.WRONG_FIT, reason="no text")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock,
            patch("time.sleep"),
        ):
            out = _run("--campaign", campaign.pk, "--source", "rejects", "--apply", "--yes")

        deal.refresh_from_db()
        assert deal.state == DealState.FAILED
        mock.assert_not_called()
        assert "skipped (no profile text)" in out

    def test_apply_logs_each_override_with_script_source(self, campaign):
        """prompt_ab flips route through the same audit log as the Reviews page,
        marked source=prompt_ab with no user (script-driven)."""
        from openoutreach.crm.models import RequalificationLog

        rej = _make_lead("https://x/rescued/", "CEO of a services firm")
        deal = DealFactory(lead=rej, campaign=campaign, state=DealState.FAILED,
                           outcome=Outcome.WRONG_FIT, reason="Consultant, not founder.")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "founder of a live company")),
            patch("time.sleep"),
        ):
            _run("--campaign", campaign.pk, "--source", "rejects", "--apply", "--yes")

        log = RequalificationLog.objects.get(deal=deal)
        assert log.source == "prompt_ab"
        assert log.user_id is None
        assert log.original_reason == "Consultant, not founder."

    def test_apply_writes_rescue_csv(self, campaign, tmp_path):
        self._reject(campaign, "https://x/rescued/", "Consultant.", "CEO of a firm")
        self._reject(campaign, "https://x/kept/", "Stealth.", "Stealth startup")
        csv_path = tmp_path / "rescue.csv"

        def fake_llm(profile_text, product_docs, campaign_target, template):
            return (1 if profile_text.startswith("CEO") else 0, f"reason: {profile_text}")

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", side_effect=fake_llm),
            patch("time.sleep"),
        ):
            _run("--campaign", campaign.pk, "--source", "rejects", "--apply", "--yes",
                 "--csv", str(csv_path))

        text = csv_path.read_text(encoding="utf-8")
        assert "requalified" in text and "still_rejected" in text
        assert "lead_id,identity,action,loose_reason,original_reason" in text
