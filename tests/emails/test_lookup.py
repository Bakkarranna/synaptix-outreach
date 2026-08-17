# tests/emails/test_lookup.py
"""The paid lookup, in two steps: buy the address, then check on it.

The backoff is the part worth pinning down. It lives on the deal now
(``not_before`` + ``lookup_attempt``), so a job that never terminates delays that
one lead and nothing else — the same backoff in a shared queue row once put a live
install to sleep for 34 hours.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from openoutreach.crm.models import DealState
from openoutreach.emails.bettercontact import BetterContactUnavailable, PollOutcome
from openoutreach.emails.steps.lookup import buy_address, check_lookup, reclaim_lookup
from tests.factories import DealFactory, LeadFactory


def _ready_to_find(campaign, email=None, source_fields=None):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(email=email, source_fields=source_fields or {}),
        state=DealState.READY_TO_FIND_EMAIL,
    )


def _in_flight(campaign, attempt=0, request_id="req1"):
    return DealFactory(
        campaign=campaign,
        lead=LeadFactory(),
        state=DealState.FINDING_EMAIL,
        lookup_request_id=request_id,
        lookup_attempt=attempt,
    )


# ── _slug_first_name ──────────────────────────────────────────────


class TestSlugFirstName:
    def test_takes_the_first_token_of_a_name_slug(self):
        from openoutreach.emails.steps.lookup import _slug_first_name

        assert _slug_first_name("https://www.linkedin.com/in/campbell-phillips-481710b0") == "campbell"

    def test_ignores_the_truncated_last_name(self):
        """Common last names are cut to one letter; only the first name is safe."""
        from openoutreach.emails.steps.lookup import _slug_first_name

        assert _slug_first_name("https://www.linkedin.com/in/brendan-d-6074a287") == "brendan"

    def test_empty_for_a_url_without_a_slug(self):
        from openoutreach.emails.steps.lookup import _slug_first_name

        assert _slug_first_name("") == ""

    def test_drops_a_numeric_lead(self):
        from openoutreach.emails.steps.lookup import _slug_first_name

        assert _slug_first_name("https://www.linkedin.com/in/481710b0") == ""


# ── buy_address ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestBuyAddress:
    def test_a_known_address_skips_the_hub_and_the_provider(self, campaign):
        deal = _ready_to_find(campaign, email="known@corp.com")

        with patch("openoutreach.contacts.service.resolve") as resolve, \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.READY_TO_EMAIL

        resolve.assert_not_called()
        submit.assert_not_called()

    def test_a_hub_hit_skips_the_paid_job(self, campaign):
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value="hub@corp.com"), \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.READY_TO_EMAIL

        submit.assert_not_called()
        deal.lead.refresh_from_db()
        assert deal.lead.email == "hub@corp.com"

    def test_a_hub_miss_submits_and_parks_on_the_handle(self, campaign):
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.emails.bettercontact.submit", return_value="req-42"):
            assert buy_address(deal) == DealState.FINDING_EMAIL

        assert deal.lookup_request_id == "req-42"
        assert deal.lookup_attempt == 0
        assert deal.not_before > timezone.now()

    def test_a_site_finder_hit_skips_the_paid_job(self, campaign):
        """A free scrape of the company's own site resolves the address before any
        credit is spent — the site finder sits between the hub and the paid job."""
        deal = _ready_to_find(campaign, source_fields={"company_domain": "acme.com"})

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.finders.find_emails",
                      return_value="hello@acme.com") as find, \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            assert buy_address(deal) == DealState.READY_TO_EMAIL

        submit.assert_not_called()
        find.assert_called_once_with(deal.lead)
        deal.lead.refresh_from_db()
        assert deal.lead.email == "hello@acme.com"

    def test_the_submit_carries_the_name_and_company_that_lift_hit_rate(self, campaign):
        """URL-only lookups ran 3-for-3 terminal misses; the row's name + company
        are what the paid finder wants and they were being thrown away."""
        deal = _ready_to_find(campaign, source_fields={
            "contact_first_name": "Campbell", "contact_last_name": "Phillips",
            "company_name": "Tumblr", "company_domain": "tumblr.com",
        })

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.finders.find_emails", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            buy_address(deal)

        query = submit.call_args.args[0]
        assert query.linkedin_url == deal.lead.profile_url
        assert query.first_name == "Campbell"
        assert query.last_name == "Phillips"
        assert query.company == "Tumblr"
        assert query.company_domain == "tumblr.com"

    def test_an_old_lead_without_enrichment_fields_falls_back_to_the_slug(self, campaign):
        """Leads discovered before the fields existed carry no name/company; the
        slug's first token is a safe first-name hint, and nothing else is guessed.
        """
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.finders.find_emails", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            buy_address(deal)

        query = submit.call_args.args[0]
        assert query.linkedin_url == deal.lead.profile_url
        assert query.first_name == "lead"  # slug ``/in/lead-<n>/``
        assert query.last_name == ""
        assert query.company == "" and query.company_domain == ""

    def test_stored_fields_win_over_the_slug_hint(self, campaign):
        deal = _ready_to_find(campaign, source_fields={
            "contact_first_name": "Campbell", "contact_last_name": "Phillips",
        })

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            buy_address(deal)

        query = submit.call_args.args[0]
        assert query.first_name == "Campbell"
        assert query.last_name == "Phillips"

    def test_an_unconfigured_finder_leaves_the_deal_queued(self, campaign):
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=False), \
                patch("openoutreach.emails.bettercontact.submit") as submit:
            assert buy_address(deal) is None

        submit.assert_not_called()
        assert deal.lookup_request_id == ""

    def test_an_outage_spends_no_credit_and_leaves_it_queued(self, campaign):
        """No handle exists to poll, so the next cycle simply tries again."""
        deal = _ready_to_find(campaign)

        with patch("openoutreach.contacts.service.resolve", return_value=None), \
                patch("openoutreach.emails.bettercontact.is_configured", return_value=True), \
                patch("openoutreach.emails.bettercontact.submit",
                      side_effect=BetterContactUnavailable("503")):
            assert buy_address(deal) is None

        assert deal.lookup_request_id == ""


# ── check_lookup ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestCheckLookup:
    def test_a_hit_stores_the_address_and_gives_it_back(self, campaign):
        deal = _in_flight(campaign)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=False, email="found@corp.com")), \
                patch("openoutreach.contacts.service.contribute") as contribute:
            assert check_lookup(deal) == DealState.READY_TO_EMAIL

        deal.lead.refresh_from_db()
        assert deal.lead.email == "found@corp.com"
        contribute.assert_called_once()
        assert deal.not_before is None
        assert deal.lookup_request_id == ""

    def test_a_miss_is_its_own_terminal(self, campaign):
        """Reachability failed, not fit — the ML labeler keeps the lead positive."""
        deal = _in_flight(campaign)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=False, email="")):
            assert check_lookup(deal) == DealState.NO_EMAIL_BETTERCONTACT

    def test_a_running_job_backs_off_on_its_own_row(self, campaign):
        deal = _in_flight(campaign, attempt=0)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.lookup_attempt == 1
        assert deal.not_before > timezone.now()

    def test_the_backoff_doubles_into_days(self, campaign):
        deal = _in_flight(campaign, attempt=15)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            check_lookup(deal)

        assert deal.not_before - timezone.now() > timedelta(days=1)

    def test_an_extreme_attempt_count_stays_representable(self, campaign):
        """The rail exists so ``datetime`` can still express the schedule."""
        deal = _in_flight(campaign, attempt=200)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.not_before is not None

    def test_a_provider_outage_retries_at_the_same_interval(self, campaign):
        """Nothing was learned about the job, so the backoff must not advance."""
        deal = _in_flight(campaign, attempt=3)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   side_effect=BetterContactUnavailable("503")):
            assert check_lookup(deal) is None

        assert deal.lookup_attempt == 3
        assert deal.not_before > timezone.now()

    def test_a_stalled_job_is_never_abandoned(self, campaign):
        """Abandoning reverted the deal and bought a *second* job for the same lead."""
        deal = _in_flight(campaign, attempt=40)

        with patch("openoutreach.emails.bettercontact.poll_once",
                   return_value=PollOutcome(running=True)):
            assert check_lookup(deal) is None

        assert deal.lookup_request_id == "req1"


# ── reclaim_lookup ────────────────────────────────────────────────


@pytest.mark.django_db
class TestReclaimLookup:
    def test_a_handleless_deal_goes_back_to_be_bought(self, campaign):
        """No request_id means no job and no credit spent — the buy step owns it."""
        deal = _in_flight(campaign, attempt=2, request_id="")
        deal.not_before = timezone.now() - timedelta(hours=1)

        assert reclaim_lookup(deal) == DealState.READY_TO_FIND_EMAIL
        assert deal.not_before is None
        assert deal.lookup_attempt == 0

    def test_it_never_touches_the_provider(self, campaign):
        """There is nothing to poll, and polling an empty handle would spend a call
        to be told so."""
        deal = _in_flight(campaign, request_id="")

        with patch("openoutreach.emails.bettercontact.poll_once") as poll:
            reclaim_lookup(deal)

        poll.assert_not_called()
