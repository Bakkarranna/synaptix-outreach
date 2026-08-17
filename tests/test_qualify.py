# tests/test_qualify.py
"""Tests for run_qualification — the qualify leg of the lazy chain.

Post-pivot, qualify only promotes (label=1) or disqualifies (label=0/promote
failure). Enrichment / email-resolution moved to the find-email leg. Leads carry
their own ``profile_text`` + embedding from discovery — no live scrape."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from openoutreach.core.ml.qualifier import BayesianQualifier
from openoutreach.core.pipeline.qualify import run_qualification


def _make_lead(profile_url="https://www.linkedin.com/in/alice/", profile_text="engineer at acme",
               embedding=None, creation_date=None):
    from openoutreach.crm.models import Lead

    if embedding is None:
        embedding = np.ones(384, dtype=np.float32)
    fields = {"creation_date": creation_date} if creation_date else {}
    return Lead.objects.create(
        profile_url=profile_url,
        profile_text=profile_text,
        embedding=np.asarray(embedding, dtype=np.float32).tobytes(),
        **fields,
    )


def _axis(index: int, scale: float = 1.0) -> np.ndarray:
    """A 384-dim vector on a single axis — orthogonal, so distances are exact."""
    vec = np.zeros(384, dtype=np.float32)
    vec[index] = scale
    return vec


@pytest.mark.django_db
class TestQualifyPrompt:
    """The LLM prompt must not reject borderline strong founders by default.

    Pins the decision framework: hard rejections are an explicit short list
    (banned category, stealth/unlaunched, no decision authority, pure freelancer),
    everything else is a soft signal, and a founder/CEO who misses soft criteria is
    qualified with the caveat in the reason.
    """

    def test_prompt_gives_borderline_profiles_benefit_of_the_doubt(self):
        from openoutreach.core.ml.qualifier import QualificationDecision, qualify_with_llm

        captured = {}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, prompt):
                captured["prompt"] = prompt
                from types import SimpleNamespace
                return SimpleNamespace(output=QualificationDecision(
                    qualified=True,
                    reason="Founder of a services firm — fits the decision-maker test, "
                           "company type is a soft miss; worth a first email.",
                ))

        with (
            patch("pydantic_ai.Agent", FakeAgent),
            patch("openoutreach.core.llm.get_llm_model", return_value=object()),
        ):
            label, reason = qualify_with_llm(
                "CEO of a small IT services firm with 12 staff, live website",
                "AI dev tools",
                "Founders/CEOs of early-stage startups (2-50 employees) who vibe-coded "
                "their product and have an outdated website",
            )

        prompt = captured["prompt"]
        assert label == 1
        assert "Hard reject" in prompt and "Soft criteria" in prompt
        assert "when in doubt, qualify" in prompt
        assert "NOT a freelancer" in prompt  # founder of a services firm ≠ freelancer

    def test_prompt_still_pins_the_explicit_hard_exclusions(self):
        """Loosening soft criteria must not soften real hard exclusions — the
        template must still name them as the only reject reasons."""
        import jinja2

        from openoutreach.core.conf import PROMPTS_DIR

        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PROMPTS_DIR)))
        prompt = env.get_template("qualify_lead.j2").render(
            product_docs="x", campaign_target="y", profile_text="z")
        assert "must NOT" in prompt or "hard exclusion" in prompt
        assert "stealth or unlaunched" in prompt
        assert "no decision-making authority" in prompt


@pytest.mark.django_db
class TestRunQualification:
    def test_calls_llm_on_candidate_with_profile_text(self, campaign):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(1, "Good fit")) as mock_llm,
            patch("openoutreach.core.db.leads.promote_lead_to_deal"),
        ):
            result = run_qualification(campaign, qualifier)

        mock_llm.assert_called_once()
        assert result == "https://www.linkedin.com/in/alice/"

    def test_skips_when_profile_text_empty(self, campaign):
        _make_lead(profile_text="")
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock_llm,
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
        ):
            result = run_qualification(campaign, qualifier)

        assert result is None
        mock_llm.assert_not_called()
        mock_promote.assert_not_called()

    def test_promotes_on_label_1(self, campaign):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(campaign, qualifier)

        mock_promote.assert_called_once()
        mock_disq.assert_not_called()

    def test_disqualifies_on_label_0(self, campaign):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(0, "Bad fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal") as mock_promote,
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(campaign, qualifier)

        mock_promote.assert_not_called()
        mock_disq.assert_called_once()

    def test_disqualifies_when_promote_raises_value_error(self, campaign):
        _make_lead()
        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm", return_value=(1, "Good fit")),
            patch("openoutreach.core.db.leads.promote_lead_to_deal",
                  side_effect=ValueError("no company_name")),
            patch("openoutreach.core.db.deals.create_disqualified_deal") as mock_disq,
        ):
            run_qualification(campaign, qualifier)

        mock_disq.assert_called_once()

    def test_returns_none_when_no_candidates(self, campaign):
        qualifier = BayesianQualifier(seed=42)

        with patch("openoutreach.core.ml.qualifier.qualify_with_llm") as mock_llm:
            assert run_qualification(campaign, qualifier) is None

        mock_llm.assert_not_called()


@pytest.mark.django_db
class TestUnanchoredSelection:
    """The degraded path: anchoring failed, so the label set is still single-class and
    no posterior exists to rank with. Oldest first — nothing here can rank."""

    def test_falls_back_to_oldest(self, campaign):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        _make_lead("https://www.linkedin.com/in/first/", "first", _axis(0),
                   creation_date=now - timedelta(hours=1))
        _make_lead("https://www.linkedin.com/in/second/", "second", _axis(1),
                   creation_date=now)

        qualifier = BayesianQualifier(seed=42)

        with (
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(0, "Bad fit")),
            patch("openoutreach.core.db.deals.create_disqualified_deal"),
        ):
            result = run_qualification(campaign, qualifier)

        assert result == "https://www.linkedin.com/in/first/"


@pytest.mark.django_db
class TestDegeneratePosteriorSelection:
    """A negative-dominated GP collapses (length-scale hits its bound) and every
    candidate scores the same value, so argmax would degrade to oldest-first. The
    fallback ranks by similarity to the anchors, with an email-first tie-break —
    the LLM still owns the verdict, we only change which candidate gets the call."""

    def test_degenerate_scores_fall_back_to_anchor_similarity(self, campaign):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        # Closest to the anchor axis, but NOT oldest.
        _make_lead("https://www.linkedin.com/in/close/", "close to anchor", _axis(0),
                   creation_date=now - timedelta(minutes=30))
        _make_lead("https://www.linkedin.com/in/old/", "oldest lead", _axis(5),
                   creation_date=now - timedelta(hours=2))

        qualifier = BayesianQualifier(seed=42)
        qualifier.set_anchors(np.array([_axis(0), _axis(1)], dtype=np.float32))

        # Simulate the degenerate GP: fitted, but flat probabilities across all
        # candidates (a negative-dominated fit blowing its length-scale to the bound).
        with (
            patch.object(qualifier, "acquisition_scores",
                         return_value=("exploit (p)", np.full(2, 0.0001))),
            patch.object(qualifier, "predict", return_value=None),
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(0, "Bad fit")),
            patch("openoutreach.core.db.deals.create_disqualified_deal"),
        ):
            result = run_qualification(campaign, qualifier)

        # The anchor-similar candidate wins, not the oldest one.
        assert result == "https://www.linkedin.com/in/close/"

    def test_email_break_tie_among_equal_anchor_similarities(self, campaign):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        _make_lead("https://www.linkedin.com/in/with-email/", "same region", _axis(0),
                   creation_date=now - timedelta(hours=1))
        _make_lead("https://www.linkedin.com/in/without-email/", "same region", _axis(0),
                   creation_date=now)

        # Give the first lead a resolved email.
        from openoutreach.crm.models import Lead

        Lead.objects.filter(profile_url="https://www.linkedin.com/in/with-email/").update(
            email="founder@acme.com")

        qualifier = BayesianQualifier(seed=42)
        qualifier.set_anchors(np.array([_axis(0)], dtype=np.float32))

        with (
            patch.object(qualifier, "acquisition_scores",
                         return_value=("exploit (p)", np.full(2, 0.0001))),
            patch.object(qualifier, "predict", return_value=None),
            patch("openoutreach.core.ml.qualifier.qualify_with_llm",
                  return_value=(0, "Bad fit")),
            patch("openoutreach.core.db.deals.create_disqualified_deal"),
        ):
            result = run_qualification(campaign, qualifier)

        assert result == "https://www.linkedin.com/in/with-email/"
