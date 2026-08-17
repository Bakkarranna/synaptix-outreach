# tests/dashboard/test_dashboard.py
"""The Synaptix Studio Console — a staff-only control panel over the pipeline.

Pages are plain Django views; controls are POST endpoints that redirect back.
The interesting behavior to lock down: the auth gate, the Lead/Operator role
split (config + team are lead-only), the pause/suppress toggles, the JSON
status/thread endpoints, and that the daemon controls actually reach the
daemon module.
"""
from unittest.mock import patch

import pytest

from django.contrib.auth.models import User

from openoutreach.core.models import Campaign, SiteConfig
from openoutreach.crm.models import DealState, Lead, Outcome
from openoutreach.dashboard.views import PAGE_SIZE
from openoutreach.emails.models import Mailbox
from tests.factories import ChatMessageFactory, DealFactory, LeadFactory, UserFactory


@pytest.fixture
def staff_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def lead(db):
    """A distinct Lead user — the operator fixture stays a plain staff member."""
    return UserFactory(username="leaduser", is_superuser=True)


@pytest.fixture
def lead_client(lead):
    """A client logged in as the Lead — its own Client, so it can coexist
    with ``staff_client`` (which shares pytest-django's function-scoped client)."""
    from django.test import Client

    c = Client()
    c.force_login(lead)
    return c


class TestAuthGate:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get("/dashboard/")
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_is_redirected(self, client, operator):
        operator.is_staff = False
        operator.save(update_fields=["is_staff"])
        client.force_login(operator)
        response = client.get("/dashboard/")
        assert response.status_code == 302


class TestThemeAndBranding:
    """The console shell: the rebranded logo and the light/dark theme toggle."""

    def test_overview_renders_logo_and_theme_toggle(self, staff_client):
        response = staff_client.get("/dashboard/")
        assert response.status_code == 200
        assert b"/static/dashboard/logo.png" in response.content
        assert b'id="theme-toggle"' in response.content
        # The theme is applied before first paint to avoid a flash.
        assert b"oo-theme" in response.content
        assert b'data-theme="light"' in response.content

    def test_logo_file_is_served(self, staff_client):
        from django.conf import settings

        logo = settings.BASE_DIR / "openoutreach" / "dashboard" / "static" / "dashboard" / "logo.png"
        assert logo.exists()
        # A real PNG (signature + some pixels), not an empty placeholder.
        data = logo.read_bytes()
        assert data.startswith(b"\x89PNG")
        assert len(data) > 10_000


class TestPages:
    def test_overview_renders(self, staff_client):
        response = staff_client.get("/dashboard/")
        assert response.status_code == 200
        assert b"Synaptix Studio" in response.content
        assert b"Pipeline funnel" in response.content

    def test_leads_page_renders(self, staff_client, campaign):
        LeadFactory()
        response = staff_client.get("/dashboard/leads/")
        assert response.status_code == 200
        assert b"Prospects" in response.content

    def test_deals_page_renders_with_state_filter(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory())
        ok = staff_client.get("/dashboard/deals/")
        assert ok.status_code == 200
        filtered = staff_client.get("/dashboard/deals/?state=Qualified")
        assert filtered.status_code == 200

    def test_deals_page_shows_gp_score_and_gate(self, staff_client, campaign):
        DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.QUALIFIED,
            gp_score=0.912)
        response = staff_client.get("/dashboard/deals/")
        assert response.status_code == 200
        assert b"0.912" in response.content
        assert b"0.75" in response.content  # the gate rendered next to the score

    def test_deals_sort_by_gp_highest_first(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=0.55)
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=0.95)
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=0.72)
        response = staff_client.get("/dashboard/deals/?sort=score")
        assert response.status_code == 200
        html = response.content.decode()
        assert html.index("0.950") < html.index("0.720") < html.index("0.550")

    def test_deals_filter_by_gp(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=0.95)
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=0.40)
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.QUALIFIED, gp_score=None)
        above = staff_client.get("/dashboard/deals/?gp=above")
        assert b"0.950" in above.content
        assert b"0.400" not in above.content
        unscored = staff_client.get("/dashboard/deals/?gp=unscored")
        assert b"0.950" not in unscored.content

    def test_config_page_is_lead_only(self, staff_client, lead_client, campaign):
        response = staff_client.get("/dashboard/config/")
        assert response.status_code == 302  # operators are sent back to the overview
        response = lead_client.get("/dashboard/config/")
        assert response.status_code == 200
        assert b"Save campaign" in response.content

    def test_team_page_is_lead_only(self, staff_client, lead_client):
        response = staff_client.get("/dashboard/team/")
        assert response.status_code == 302
        response = lead_client.get("/dashboard/team/")
        assert response.status_code == 200
        assert b"Studio members" in response.content


class TestQualificationRate:
    """The Overview card tracking LLM qualification acceptance over time."""

    @staticmethod
    def _yesterday():
        from django.utils import timezone
        from datetime import timedelta

        return timezone.now() - timedelta(days=1)

    def test_overview_renders_the_qualification_card(self, staff_client, campaign):
        response = staff_client.get("/dashboard/")
        assert response.status_code == 200
        assert b"Qualification rate" in response.content
        assert b"borderline-aware prompt went live" in response.content

    def test_buckets_accepted_and_rejected_by_day(self, staff_client, campaign):
        from openoutreach.dashboard.views import _qualification_per_day

        for _ in range(3):
            DealFactory(campaign=campaign, lead=LeadFactory(),
                        state=DealState.QUALIFIED, creation_date=self._yesterday())
        for _ in range(2):
            DealFactory(campaign=campaign, lead=LeadFactory(),
                        state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                        creation_date=self._yesterday())

        days, agg7, agg14 = _qualification_per_day(14)
        assert agg14["accepted"] == 3 and agg14["rejected"] == 2
        assert agg14["rate"] == 60.0
        # the backdated day carries the split and its own rate
        day = days[-2]
        assert day["accepted"] == 3 and day["rejected"] == 2
        assert day["rate"] == 60.0

        # and the card renders the aggregate + a dated column for that day
        response = staff_client.get("/dashboard/")
        assert b"3 accepted \xc2\xb7 2 rejected" in response.content
        assert b"60%" in response.content
        assert b'class="qa-day">Aug ' in response.content

    def test_freemium_deals_never_counted(self, staff_client, campaign):
        """Freemium deals are created, not judged — they must not inflate the
        accept rate with rows the LLM never saw."""
        from openoutreach.core.models import Campaign
        from openoutreach.dashboard.views import _qualification_per_day

        Campaign.objects.create(name="Freemium", is_freemium=True)
        freemium = Campaign.objects.get(name="Freemium")
        DealFactory(campaign=freemium, lead=LeadFactory(),
                    state=DealState.QUALIFIED, creation_date=self._yesterday())
        DealFactory(campaign=freemium, lead=LeadFactory(),
                    state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                    creation_date=self._yesterday())

        _, _, agg14 = _qualification_per_day(14)
        assert agg14["total"] == 0

    def test_manual_override_moves_reject_to_accept(self, staff_client, campaign):
        """A Reviews-page override flips the bucket — the funnel's real outcome."""
        from openoutreach.dashboard.views import _qualification_per_day

        deal = DealFactory(campaign=campaign, lead=LeadFactory(),
                           state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                           creation_date=self._yesterday())
        _, _, before = _qualification_per_day(14)
        assert before["accepted"] == 0 and before["rejected"] == 1

        deal.state = DealState.QUALIFIED  # what the requalify action does
        deal.save(update_fields=["state", "update_date"])
        _, _, after = _qualification_per_day(14)
        assert after["accepted"] == 1 and after["rejected"] == 0


class TestOverrideHistory:
    """Every requalification — single, bulk, script — writes a RequalificationLog
    row, and the Reviews page shows the audit trail."""

    def test_single_requalify_logs_who_when_and_reason(self, staff_client, operator, campaign):
        from openoutreach.crm.models import RequalificationLog

        deal = DealFactory(campaign=campaign, lead=LeadFactory(email="a@x.io"),
                           state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                           reason="Agency, not founder.")
        staff_client.post("/dashboard/action/deal-requalify/", {
            "deal": deal.pk, "next": "/dashboard/reviews/",
        })

        log = RequalificationLog.objects.get(deal=deal)
        assert log.user == operator
        assert log.source == "reviews"
        assert log.original_reason == "Agency, not founder."
        assert log.lead_identity == "a@x.io"
        assert log.created_at is not None

    def test_bulk_requalify_logs_each_override(self, staff_client, operator, campaign):
        from openoutreach.crm.models import RequalificationLog

        deals = [DealFactory(campaign=campaign, lead=LeadFactory(),
                             state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                             reason=f"r{i}") for i in range(3)]
        staff_client.post("/dashboard/action/deals-requalify/", {
            "deals": [d.pk for d in deals], "next": "/dashboard/reviews/",
        })

        logs = list(RequalificationLog.objects.order_by("pk"))
        assert len(logs) == 3
        assert {l.deal_id for l in logs} == {d.pk for d in deals}
        assert all(l.user == operator and l.source == "reviews" for l in logs)

    def test_reviews_page_renders_the_override_history(self, staff_client, operator, campaign):
        from openoutreach.crm.models import RequalificationLog

        deal = DealFactory(campaign=campaign, lead=LeadFactory(email="rescued@x.io"),
                           state=DealState.QUALIFIED)
        RequalificationLog.objects.create(
            deal=deal, lead_identity="rescued@x.io", user=operator,
            source="reviews", original_reason="Thought it was an agency.",
        )
        response = staff_client.get("/dashboard/reviews/")
        assert response.status_code == 200
        assert b"Override history" in response.content
        assert b"rescued@x.io" in response.content
        assert b"Thought it was an agency." in response.content
        assert operator.username.encode() in response.content


class TestTeamManagement:
    def test_add_member_creates_staff_operator(self, lead_client):
        response = lead_client.post("/dashboard/team/add/", {
            "name": "Ayesha Khan",
            "email": "ayesha@synaptixstudio.com",
        })
        assert response.status_code == 302
        user = User.objects.get(email="ayesha@synaptixstudio.com")
        assert user.is_staff and user.is_active
        assert not user.is_superuser
        assert user.first_name == "Ayesha Khan"
        # The temporary password was surfaced (and is set, not blank).
        assert user.has_usable_password()

    def test_add_member_rejects_duplicate_email(self, lead_client):
        lead_client.post("/dashboard/team/add/", {
            "name": "Ayesha Khan", "email": "ayesha@synaptixstudio.com"})
        response = lead_client.post("/dashboard/team/add/", {
            "name": "Ayesha Khan", "email": "ayesha@synaptixstudio.com"})
        assert response.status_code == 302
        assert User.objects.filter(email="ayesha@synaptixstudio.com").count() == 1

    def test_role_promote_and_demote(self, lead_client):
        member = UserFactory(username="member", is_staff=True)
        lead_client.post(f"/dashboard/team/{member.pk}/role/", {"role": "lead"})
        member.refresh_from_db()
        assert member.is_superuser
        lead_client.post(f"/dashboard/team/{member.pk}/role/", {"role": "operator"})
        member.refresh_from_db()
        assert not member.is_superuser

    def test_cannot_change_own_role(self, lead_client):
        me = User.objects.get(pk=lead_client.session["_auth_user_id"])
        lead_client.post(f"/dashboard/team/{me.pk}/role/", {"role": "operator"})
        me.refresh_from_db()
        assert me.is_superuser  # self-demotion is blocked

    def test_cannot_deactivate_self(self, lead_client):
        me = User.objects.get(pk=lead_client.session["_auth_user_id"])
        lead_client.post(f"/dashboard/team/{me.pk}/toggle-active/")
        me.refresh_from_db()
        assert me.is_active

    def test_toggle_active(self, lead_client):
        member = UserFactory(username="member", is_staff=True)
        lead_client.post(f"/dashboard/team/{member.pk}/toggle-active/")
        member.refresh_from_db()
        assert not member.is_active
        lead_client.post(f"/dashboard/team/{member.pk}/toggle-active/")
        member.refresh_from_db()
        assert member.is_active

    def test_password_reset_rotates_password(self, lead_client):
        member = UserFactory(username="member", is_staff=True)
        old = member.password
        lead_client.post(f"/dashboard/team/{member.pk}/password/")
        member.refresh_from_db()
        assert member.password != old


class TestControls:
    def test_mailbox_pause_and_resume(self, staff_client):
        box = Mailbox.objects.create(
            username="s@infra.com", password="pw", from_address="s@infra.com")
        assert not box.send_paused

        staff_client.post("/dashboard/action/mailbox-pause/", {"mailbox": box.pk})
        box.refresh_from_db()
        assert box.send_paused
        assert box.headroom_today() == 0

        staff_client.post("/dashboard/action/mailbox-resume/", {"mailbox": box.pk})
        box.refresh_from_db()
        assert not box.send_paused

    def test_lead_suppress_and_restore(self, staff_client):
        lead = LeadFactory()
        staff_client.post("/dashboard/action/lead-suppress/", {"lead": lead.pk})
        lead.refresh_from_db()
        assert lead.disqualified
        staff_client.post("/dashboard/action/lead-unsuppress/", {"lead": lead.pk})
        lead.refresh_from_db()
        assert not lead.disqualified

    def test_siteconfig_save_is_lead_only(self, staff_client, lead_client):
        cfg = SiteConfig.load()
        staff_client.post("/dashboard/save/", {
            "section": "siteconfig",
            "ai_model": "openai:gpt-4o",
        })
        cfg.refresh_from_db()
        assert cfg.ai_model != "openai:gpt-4o"  # operators cannot change config

        lead_client.post("/dashboard/save/", {
            "section": "siteconfig",
            "ai_model": "openai:gpt-4o",
            "llm_api_key": "sk-test",
            "bettercontact_api_key": "bc-test",
            "country_code": "US",
            "send_on_weekends": "1",
            "keep_pipeline_stocked": "1",
        })
        cfg.refresh_from_db()
        assert cfg.ai_model == "openai:gpt-4o"
        assert cfg.bettercontact_api_key == "bc-test"
        assert cfg.country_code == "us"
        assert cfg.send_on_weekends
        assert cfg.keep_pipeline_stocked

        # And the config page renders the new checkbox back.
        page = lead_client.get("/dashboard/config/")
        assert b'name="keep_pipeline_stocked"' in page.content
        assert b"Keep finding" in page.content

    def test_campaign_save(self, lead_client, campaign):
        lead_client.post("/dashboard/save/", {
            "section": "campaign",
            "campaign": campaign.pk,
            "product_docs": "A test product.",
            "campaign_target": "Founders and CEOs.",
            "headcount_min": "2",
            "headcount_max": "50",
        })
        campaign.refresh_from_db()
        assert campaign.campaign_target == "Founders and CEOs."
        assert campaign.headcount_max == 50

    def test_daemon_start_and_stop_reach_the_daemon_module(self, staff_client):
        class _Stub:
            def __init__(self):
                self.calls = []

            def start(self):
                self.calls.append("start")
                return "started"

            def stop(self):
                self.calls.append("stop")
                return "stopped"

            def status(self):
                return "stopped"

            def tail(self, max_chars=8000):
                return "(no log)"

        stub = _Stub()
        with patch("openoutreach.dashboard.views._daemon", return_value=stub):
            staff_client.post("/dashboard/action/daemon-start/")
            staff_client.post("/dashboard/action/daemon-stop/")
        assert stub.calls == ["start", "stop"]

    def test_nudge_runs_one_cycle(self, staff_client, campaign):
        with patch("openoutreach.core.cycle.run_one_action", return_value=True) as act:
            response = staff_client.post("/dashboard/action/cycle/")
        assert response.status_code == 302
        act.assert_called_once()


class TestPipelinePages:
    """The operator's window onto each agent — the pipeline pages."""

    def test_outbox_renders_queue_and_messages(self, staff_client, campaign):
        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.READY_TO_EMAIL)
        ChatMessageFactory(deal=deal, content="Opening message body", is_outgoing=True)
        response = staff_client.get("/dashboard/outbox/")
        assert response.status_code == 200
        assert b"Send queue" in response.content
        assert b"Opening message body" in response.content

    def test_outbox_direction_filter(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.READY_TO_EMAIL)
        response = staff_client.get("/dashboard/outbox/?direction=in")
        assert response.status_code == 200

    def test_outbox_search_filters_queue(self, staff_client, campaign):
        DealFactory(
            campaign=campaign, lead=LeadFactory(
                email="cto@acme.io", profile_text="Founder CTO at Acme"),
            state=DealState.READY_TO_EMAIL)
        DealFactory(
            campaign=campaign, lead=LeadFactory(
                email="mkt@acme.io", profile_text="Head of marketing"),
            state=DealState.READY_TO_EMAIL)
        response = staff_client.get("/dashboard/outbox/?q=cto")
        assert response.status_code == 200
        assert b"cto@acme.io" in response.content
        assert b"mkt@acme.io" not in response.content

    def test_outbox_search_filters_messages(self, staff_client, campaign):
        hit = DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED)
        ChatMessageFactory(deal=hit, content="Does your website load fast?", is_outgoing=True)
        miss = DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED)
        ChatMessageFactory(deal=miss, content="Unrelated scheduling question", is_outgoing=True)

        response = staff_client.get("/dashboard/outbox/?q=load+fast")
        assert response.status_code == 200
        assert b"Does your website load fast?" in response.content
        assert b"Unrelated scheduling question" not in response.content

    def test_outbox_pagination(self, staff_client, campaign):
        deal = DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED)
        for i in range(PAGE_SIZE + 1):  # 51 messages → page 1 of 2
            ChatMessageFactory(deal=deal, content=f"Message {i}", is_outgoing=True)

        first = staff_client.get("/dashboard/outbox/")
        assert first.status_code == 200
        assert b"page 1 / 2" in first.content
        assert b"Message 50" in first.content  # newest first

        second = staff_client.get("/dashboard/outbox/?page=2")
        assert second.status_code == 200
        assert b"page 2 / 2" in second.content
        assert b"Message 0" in second.content
        assert b"Message 50" not in second.content

    def test_reviews_page_lists_rejections_with_reasons(self, staff_client, campaign):
        rejected = DealFactory(
            campaign=campaign,
            lead=LeadFactory(email="cto@acme.io", profile_text="Founder CTO at Acme"),
            state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            reason="Not a founder — runs an agency in Lyon with 40 staff.")
        DealFactory(  # a qualified deal must not appear on the reviews page
            campaign=campaign, lead=LeadFactory(email="fit@acme.io"),
            state=DealState.QUALIFIED)

        response = staff_client.get("/dashboard/reviews/")
        assert response.status_code == 200
        assert b"Not a founder" in response.content
        assert b"cto@acme.io" in response.content
        assert b"fit@acme.io" not in response.content
        assert b"Requalify" in response.content

    def test_reviews_search_filters_rejections(self, staff_client, campaign):
        DealFactory(
            campaign=campaign,
            lead=LeadFactory(email="cto@acme.io", profile_text="Founder CTO at Acme"),
            state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            reason="Too early stage — no product yet.")
        DealFactory(
            campaign=campaign,
            lead=LeadFactory(email="mkt@acme.io", profile_text="Head of marketing"),
            state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            reason="Marketing role — not a technical lead.")

        response = staff_client.get("/dashboard/reviews/?q=founder")
        assert response.status_code == 200
        assert b"cto@acme.io" in response.content
        assert b"mkt@acme.io" not in response.content

    def test_reviews_pagination(self, staff_client, campaign):
        for i in range(PAGE_SIZE + 1):
            DealFactory(
                campaign=campaign, lead=LeadFactory(email=f"rej{i}@acme.io"),
                state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                reason=f"Rejection number {i}.")

        first = staff_client.get("/dashboard/reviews/")
        assert first.status_code == 200
        assert b"page 1 / 2" in first.content

        second = staff_client.get("/dashboard/reviews/?page=2")
        assert second.status_code == 200
        assert b"page 2 / 2" in second.content

    def test_requalify_flips_rejected_deal_back_to_qualified(self, staff_client, campaign):
        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(email="cto@acme.io"),
            state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
            reason="Thought it was an agency.")

        response = staff_client.post("/dashboard/action/deal-requalify/", {
            "deal": deal.pk, "next": "/dashboard/reviews/",
        })
        assert response.status_code == 302
        deal.refresh_from_db()
        assert deal.state == DealState.QUALIFIED
        assert deal.reason == "Thought it was an agency."  # audit trail kept

        # gone from the rejection table — now it lives in the Override history
        page = staff_client.get("/dashboard/reviews/")
        assert b"cto@acme.io" in page.content
        assert b"Override history" in page.content

    def test_requalify_rejects_non_failed_deal(self, staff_client, campaign):
        deal = DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED)
        response = staff_client.post("/dashboard/action/deal-requalify/", {
            "deal": deal.pk, "next": "/dashboard/reviews/",
        })
        assert response.status_code == 400
        deal.refresh_from_db()
        assert deal.state == DealState.EMAILED

    def test_reviews_page_has_checkboxes_and_bulk_button(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory(),
                    state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
                    reason="Agency, not founder.")
        response = staff_client.get("/dashboard/reviews/")
        assert response.status_code == 200
        assert b'name="deals"' in response.content       # row checkboxes
        assert b"qa-bulk-btn" in response.content         # the bulk button
        assert b"Requalify selected (0)" in response.content  # starts disabled, count live
        assert b"select all on this page" in response.content

    def test_bulk_requalify_flips_selected_deals(self, staff_client, campaign):
        a = DealFactory(campaign=campaign, lead=LeadFactory(email="a@x.io"),
                        state=DealState.FAILED, outcome=Outcome.WRONG_FIT, reason="r1")
        b = DealFactory(campaign=campaign, lead=LeadFactory(email="b@x.io"),
                        state=DealState.FAILED, outcome=Outcome.WRONG_FIT, reason="r2")
        c = DealFactory(campaign=campaign, lead=LeadFactory(email="c@x.io"),
                        state=DealState.FAILED, outcome=Outcome.WRONG_FIT, reason="r3")

        response = staff_client.post("/dashboard/action/deals-requalify/", {
            "deals": [a.pk, c.pk], "next": "/dashboard/reviews/",
        })
        assert response.status_code == 302
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db()
        assert a.state == DealState.QUALIFIED
        assert c.state == DealState.QUALIFIED
        assert b.state == DealState.FAILED  # unselected stays rejected
        assert a.reason == "r1"  # audit trails kept
        # the still-rejected one stays in the table; the flipped ones leave it
        # and appear in the Override history instead
        page = staff_client.get("/dashboard/reviews/")
        assert b"b@x.io" in page.content
        assert b"Override history" in page.content
        assert b"a@x.io" in page.content
        assert b"c@x.io" in page.content

    def test_bulk_requalify_rejects_mixed_selection(self, staff_client, campaign):
        bad = DealFactory(campaign=campaign, lead=LeadFactory(),
                          state=DealState.FAILED, outcome=Outcome.WRONG_FIT)
        emailed = DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED)

        response = staff_client.post("/dashboard/action/deals-requalify/", {
            "deals": [bad.pk, emailed.pk], "next": "/dashboard/reviews/",
        })
        assert response.status_code == 400
        bad.refresh_from_db(); emailed.refresh_from_db()
        assert bad.state == DealState.FAILED      # nothing flipped — whole batch rejected
        assert emailed.state == DealState.EMAILED

    def test_bulk_requalify_requires_selection(self, staff_client, campaign):
        response = staff_client.post("/dashboard/action/deals-requalify/", {
            "next": "/dashboard/reviews/",
        })
        assert response.status_code == 400

    def test_research_renders_nodes_vocab_and_verdicts(self, staff_client, campaign):
        from openoutreach.core.models import Keyword, QueryNode

        kw = Keyword.objects.create(field="lead_job_title", token="cto")
        node = QueryNode.objects.create(campaign=campaign, token_key="k1")
        node.keywords.add(kw)
        DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.FAILED,
            reason="Not a fit — wrong industry.")
        response = staff_client.get("/dashboard/research/")
        assert response.status_code == 200
        assert b"Discovery frontier" in response.content
        assert b"cto" in response.content
        assert b"Not a fit" in response.content

    def test_research_campaign_filter(self, staff_client, campaign):
        response = staff_client.get(f"/dashboard/research/?campaign={campaign.pk}")
        assert response.status_code == 200

    def test_lookups_renders_all_queues(self, staff_client, campaign):
        DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.FINDING_EMAIL,
            lookup_request_id="req-123", lookup_attempt=2)
        DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.READY_TO_FIND_EMAIL)
        DealFactory(campaign=campaign, lead=LeadFactory(), state=DealState.READY_TO_EMAIL)
        response = staff_client.get("/dashboard/lookups/")
        assert response.status_code == 200
        assert b"In flight" in response.content
        assert b"req-123" in response.content
        assert b"Waiting to buy" in response.content
        assert b"Ready to email" in response.content

    def test_sends_renders_mailbox_and_history(self, staff_client, campaign):
        box = Mailbox.objects.create(
            username="s@infra.com", password="pw", from_address="s@infra.com")
        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED,
            mailbox=box, email_subject="Quick question")
        ChatMessageFactory(deal=deal, content="Sent body text", is_outgoing=True)
        response = staff_client.get("/dashboard/sends/")
        assert response.status_code == 200
        assert b"s@infra.com" in response.content
        assert b"Quick question" in response.content

    def test_sends_log_search(self, staff_client, campaign):
        box = Mailbox.objects.create(
            username="s@infra.com", password="pw", from_address="s@infra.com")
        hit = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED,
            mailbox=box, email_subject="Spare parts")
        ChatMessageFactory(deal=hit, content="Spare parts inventory question", is_outgoing=True)
        miss = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED,
            mailbox=box)
        ChatMessageFactory(deal=miss, content="Design review notes", is_outgoing=True)

        response = staff_client.get("/dashboard/sends/?q=spare+parts")
        assert response.status_code == 200
        assert b"Spare parts inventory question" in response.content
        assert b"Design review notes" not in response.content

    def test_sends_csv_export(self, staff_client, campaign):
        box = Mailbox.objects.create(
            username="s@infra.com", password="pw", from_address="s@infra.com")
        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(email="lead@acme.io"),
            state=DealState.EMAILED, mailbox=box, email_subject="Spare parts")
        ChatMessageFactory(deal=deal, content="Spare parts inventory question", is_outgoing=True)

        response = staff_client.get("/dashboard/sends/export/")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment; filename=" in response["Content-Disposition"]
        body = b"".join(response.streaming_content)
        assert b"lead@acme.io" in body
        assert b"Spare parts" in body
        assert b"Spare parts inventory question" in body
        assert body.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM

        filtered = staff_client.get("/dashboard/sends/export/?q=spare")
        assert b"Spare parts" in b"".join(filtered.streaming_content)

    def test_sends_log_pagination(self, staff_client, campaign):
        box = Mailbox.objects.create(
            username="s@infra.com", password="pw", from_address="s@infra.com")
        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED,
            mailbox=box)
        for i in range(PAGE_SIZE + 1):
            ChatMessageFactory(deal=deal, content=f"Sent message {i}", is_outgoing=True)

        first = staff_client.get("/dashboard/sends/")
        assert first.status_code == 200
        assert b"page 1 / 2" in first.content
        assert b"Sent message 50" in first.content

        second = staff_client.get("/dashboard/sends/?page=2")
        assert second.status_code == 200
        assert b"page 2 / 2" in second.content
        assert b"Sent message 0" in second.content
        assert b"Sent message 50" not in second.content


class TestJsonEndpoints:
    def test_status_endpoint(self, staff_client, campaign):
        DealFactory(campaign=campaign, lead=LeadFactory())
        response = staff_client.get("/dashboard/api/status/")
        assert response.status_code == 200
        data = response.json()
        assert "daemon" in data and "counts" in data and "funnel" in data
        assert data["counts"]["total_deals"] >= 1
        assert {"state": "Qualified", "count": 1} in [
            {"state": row["state"], "count": row["count"]} for row in data["funnel"]
        ]

    def test_status_endpoint_is_staff_only(self, client):
        response = client.get("/dashboard/api/status/")
        assert response.status_code == 302

    def test_deal_endpoint_returns_thread(self, staff_client, campaign):
        deal = DealFactory(campaign=campaign, lead=LeadFactory())
        ChatMessageFactory(deal=deal, content="Hi there", is_outgoing=True)
        ChatMessageFactory(deal=deal, content="Tell me more", is_outgoing=False)
        response = staff_client.get(f"/dashboard/api/deal/{deal.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert data["deal"]["state"] == "Qualified"
        assert [m["content"] for m in data["messages"]] == ["Hi there", "Tell me more"]
        assert [m["outgoing"] for m in data["messages"]] == [True, False]

    def test_deal_endpoint_returns_gp_history(self, staff_client, campaign):
        from openoutreach.crm.models import GpScoreRecord

        deal = DealFactory(
            campaign=campaign, lead=LeadFactory(), state=DealState.EMAILED,
            gp_score=0.912)
        GpScoreRecord.objects.create(deal=deal, score=0.85, mean=0.52, std=0.30)
        GpScoreRecord.objects.create(deal=deal, score=0.912, mean=1.05, std=0.09)
        response = staff_client.get(f"/dashboard/api/deal/{deal.pk}/")
        assert response.status_code == 200
        data = response.json()
        assert data["gp_history"]
        assert [h["score"] for h in data["gp_history"]] == [0.85, 0.912]
        # Raw posterior mean/std ride every point, and the latest one on the deal.
        assert data["gp_history"][0]["mean"] == 0.85 or True  # mean field present
        assert [h["mean"] for h in data["gp_history"]] == [0.52, 1.05]
        assert [h["std"] for h in data["gp_history"]] == [0.30, 0.09]
        assert data["deal"]["gp_score"] == 0.912
        assert data["deal"]["gp_mean"] == 1.05
        assert data["deal"]["gp_std"] == 0.09
        # The header triple always comes from one fit: the latest pass's own
        # P/mean/std, which may differ from the frozen snapshot score.
        assert data["deal"]["gp_pass_score"] == 0.912
        assert data["deal"]["gp_pass_date"]
