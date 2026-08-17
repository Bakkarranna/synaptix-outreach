# tests/test_ready_pool.py
"""Find-email pool: the GP rank gate promoting QUALIFIED → READY_TO_FIND_EMAIL."""
import pytest
from unittest.mock import patch

import numpy as np

from openoutreach.core.db.deals import set_profile_state
from openoutreach.core.db.leads import promote_lead_to_deal
from openoutreach.core.ml.qualifier import BayesianQualifier, KitQualifier
from openoutreach.core.pipeline.ready_pool import promote_to_ready, find_ready_candidate
from openoutreach.crm.models import DealState


def _fitted_kit_model():
    """A Pipeline(StandardScaler, GPR) fitted so an all-ones embedding scores ~1."""
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    X = np.array([np.ones(384), np.zeros(384)], dtype=np.float64)
    model = Pipeline([("scaler", StandardScaler()),
                      ("gpr", GaussianProcessRegressor(alpha=0.01, random_state=42))])
    model.fit(X, np.array([1.0, 0.0]))
    return model


def _make_qualified(session, slug="alice"):
    """Create an embedded Lead and a QUALIFIED Deal for it. Returns the profile_url."""
    from openoutreach.crm.models import Lead

    url = f"https://www.linkedin.com/in/{slug}/"
    Lead.objects.create(
        profile_url=url,
        profile_text="engineer at acme",
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )
    promote_lead_to_deal(session, url)
    return url


def _drift_pk_sequences():
    """Consume a Lead pk that never becomes a Deal, so the two tables' pks diverge.

    Both tables start at 1 in a fresh DB, so a bug that wrote the *lead* pk into
    ``GpScoreRecord.deal`` (a Deal FK) passed every test because the numbers
    coincided — it only surfaced as ``FOREIGN KEY constraint failed`` on a live
    install, where the sequences always drift. Calling this before creating
    qualified deals makes lead pk ≠ deal pk, so the FK is actually exercised.
    """
    from openoutreach.crm.models import Lead

    Lead.objects.create(
        profile_url="https://www.linkedin.com/in/decoy/",
        profile_text="decoy that never becomes a deal",
        embedding=np.ones(384, dtype=np.float32).tobytes(),
    )


@pytest.mark.django_db
class TestPromoteToReady:
    def test_promotes_above_threshold(self, campaign):
        _drift_pk_sequences()
        alice_url = _make_qualified(campaign, "alice")
        bob_url = _make_qualified(campaign, "bob")

        scorer = BayesianQualifier(seed=42)

        with patch.object(scorer, "predict_stats", return_value=(
            np.array([0.95, 0.60]),
            np.array([1.20, 0.55]),
            np.array([0.08, 0.40]),
        )):
            count = promote_to_ready(campaign, scorer)

        assert count == 1

        from openoutreach.crm.models import Deal
        alice_deal = Deal.objects.get(lead__profile_url=alice_url)
        bob_deal = Deal.objects.get(lead__profile_url=bob_url)
        assert alice_deal.state == DealState.READY_TO_FIND_EMAIL
        assert bob_deal.state == DealState.QUALIFIED
        # The gate's verdict is stored on every scored deal — the operator's
        # window onto why a lead passed or failed the rank gate — and each pass
        # leaves a history row for the evolution across refits.
        assert alice_deal.gp_score == pytest.approx(0.95)
        assert bob_deal.gp_score == pytest.approx(0.60)

        from openoutreach.crm.models import GpScoreRecord
        scores = sorted(
            GpScoreRecord.objects.filter(deal__campaign=campaign)
            .values_list("score", flat=True))
        assert scores == pytest.approx([0.60, 0.95])
        # The raw GP posterior behind each P is recorded too.
        rec = GpScoreRecord.objects.get(deal=bob_deal)
        assert rec.mean == pytest.approx(0.55)
        assert rec.std == pytest.approx(0.40)

    def test_history_grows_across_refits(self, campaign):
        _drift_pk_sequences()
        url = _make_qualified(campaign, "alice")
        scorer = BayesianQualifier(seed=42)

        # First pass: below the gate, deal stays QUALIFIED. Second pass: above it.
        with patch.object(scorer, "predict_stats", return_value=(
            np.array([0.70]), np.array([0.52]), np.array([0.30]))):
            assert promote_to_ready(campaign, scorer) == 0
        with patch.object(scorer, "predict_stats", return_value=(
            np.array([0.88]), np.array([1.05]), np.array([0.09]))):
            assert promote_to_ready(campaign, scorer) == 1

        from openoutreach.crm.models import Deal, GpScoreRecord
        deal = Deal.objects.get(lead__profile_url=url)
        assert deal.state == DealState.READY_TO_FIND_EMAIL
        assert deal.gp_score == pytest.approx(0.88)  # latest snapshot
        history = list(
            GpScoreRecord.objects.filter(deal=deal).values_list("score", flat=True))
        assert history == pytest.approx([0.70, 0.88])  # both passes recorded
        # Each point keeps its raw mean/std, so a borderline P is visibly borderline.
        rows = list(GpScoreRecord.objects.filter(deal=deal).order_by("created_at"))
        assert (rows[0].mean, rows[0].std) == pytest.approx((0.52, 0.30))
        assert (rows[1].mean, rows[1].std) == pytest.approx((1.05, 0.09))

    def test_returns_zero_on_cold_start(self, campaign):
        _make_qualified(campaign)

        scorer = BayesianQualifier(seed=42)

        with patch.object(scorer, "predict_stats", return_value=None):
            assert promote_to_ready(campaign, scorer) == 0

    def test_returns_zero_on_empty_pool(self, campaign):
        scorer = BayesianQualifier(seed=42)
        assert promote_to_ready(campaign, scorer) == 0

    def test_promotes_with_a_kit_qualifier(self, campaign):
        """The gate runs for freemium campaigns too, whose qualifier is a KitQualifier.

        Unmocked on purpose: the freemium campaign reached this gate with a qualifier
        that had no ``predict_probs`` at all, and every existing test patched the
        method it was missing.
        """
        _drift_pk_sequences()
        url = _make_qualified(campaign, "alice")

        scorer = KitQualifier(_fitted_kit_model())
        assert promote_to_ready(campaign, scorer) == 1

        from openoutreach.crm.models import Deal
        assert Deal.objects.get(lead__profile_url=url).state == DealState.READY_TO_FIND_EMAIL


@pytest.mark.django_db
class TestFindReadyCandidate:
    def test_returns_none_when_empty(self, campaign):
        scorer = BayesianQualifier(seed=42)
        assert find_ready_candidate(campaign, scorer) is None

    def test_returns_top_ranked(self, campaign):
        url = _make_qualified(campaign, "alice")
        set_profile_state(campaign, url, DealState.READY_TO_FIND_EMAIL.value)

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles: profiles

        result = find_ready_candidate(campaign, scorer)
        assert result is not None
        assert result["profile_url"] == url
