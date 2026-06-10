# tests/test_reconcile.py
import pytest
from unittest.mock import patch
from django.utils import timezone

from openoutreach.core.db.deals import set_profile_state
from openoutreach.linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from openoutreach.core.models import Task
from linkedin_cli.enums import ProfileState
from openoutreach.core.scheduler import reconcile


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_pending(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)
    set_profile_state(session, public_id, ProfileState.PENDING.value)


def _make_connected(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)
    set_profile_state(session, public_id, ProfileState.CONNECTED.value)


@pytest.mark.django_db
@patch("openoutreach.core.scheduler.ENABLE_ACTIVE_HOURS", False)
class TestReconcile:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_recovers_stale_running_tasks(self, fake_session):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.RUNNING,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        reconcile(fake_session)
        assert Task.objects.filter(status=Task.Status.RUNNING).count() == 0
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()

    def test_plans_connect_slots_per_campaign(self, fake_session):
        reconcile(fake_session)
        # connect_daily_limit defaults to 20 on LinkedInProfile.
        n = Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).count()
        assert n == fake_session.linkedin_profile.connect_daily_limit

    def test_plans_check_pending_slots_for_due_deals(self, fake_session):
        _make_pending(fake_session, "alice")
        # set_profile_state(PENDING) stamps next_check_pending_at = now + 24h.
        # Pull it back to now so plan_check_pending_window picks it up.
        from openoutreach.crm.models import Deal
        Deal.objects.filter(lead__public_identifier="alice").update(
            next_check_pending_at=timezone.now(),
        )
        Task.objects.all().delete()

        reconcile(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CHECK_PENDING,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).count() == 1

    def test_plans_follow_up_slots(self, fake_session):
        _make_connected(fake_session, "alice")
        reconcile(fake_session)
        # follow_up_daily_limit defaults to 25.
        assert Task.objects.filter(
            task_type=Task.TaskType.FOLLOW_UP,
            status=Task.Status.PENDING,
            payload__campaign_id=fake_session.campaign.pk,
        ).count() == fake_session.linkedin_profile.follow_up_daily_limit

    def test_does_not_replan_when_pending_exists(self, fake_session):
        reconcile(fake_session)
        count_before = Task.objects.filter(status=Task.Status.PENDING).count()
        reconcile(fake_session)
        count_after = Task.objects.filter(status=Task.Status.PENDING).count()
        assert count_before == count_after

    def test_does_not_create_for_completed_tasks(self, fake_session):
        """Already-completed tasks should not block reconcile from planning new ones."""
        # Pre-create a completed connect (no pending). Planner should still plan a fresh window.
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.COMPLETED,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        reconcile(fake_session)
        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()

    def test_recreates_window_after_handler_crash(self, fake_session):
        """A FAILED task with no pending successor → next idle cycle re-plans."""
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.FAILED,
            scheduled_at=timezone.now(),
            payload={"campaign_id": fake_session.campaign.pk},
        )
        assert not Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()

        reconcile(fake_session)

        assert Task.objects.filter(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.PENDING,
        ).exists()
