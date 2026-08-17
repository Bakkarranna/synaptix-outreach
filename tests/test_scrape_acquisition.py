# tests/test_scrape_acquisition.py
"""Tests for the scraper-backed acquisition mode (core/pipeline/scrape_acquisition.py).

Harvesting is tested against fixture HTML parsed with Scrapling's parser (no
network, no fetchers); the fetch call itself is patched. The suite skips
entirely when Scrapling is not installed — the same guarded-dependency stance
the acquisition step itself has. ``embed_text`` is mocked by conftest.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

try:
    from scrapling.parser import Selector

    HAS_SCRAPLING = True
except ImportError:  # pragma: no cover - exercised on installs without scrapling
    HAS_SCRAPLING = False


class FakeResponse:
    """Minimal Scrapling Response stand-in: status + css()."""

    def __init__(self, html: str, status: int = 200):
        self.status = status
        self._sel = Selector(html)

    def css(self, selector: str):
        return self._sel.css(selector)


_FETCH = "openoutreach.core.pipeline.scrape_acquisition._fetch"

DEMO_CFG = {
    "start_url": "https://directory.example/index.html",
    "fetch": "http",
    "card_selector": ".listing-card",
    "next_selector": "a.next",
    "website_selector": "a.website",
    "profile_url_selector": "a.linkedin",
    "delay_s": 0,
    "fields": {
        "contact_name": {"css": ".name"},
        "contact_job_title": {"css": ".title"},
    },
}

DEMO_PAGE = """
<html><body>
  <div class="listing-card">
    <h2 class="name"><a href="/detail/jane.html">Jane Founder</a></h2>
    <p class="title">CEO at Acme Widgets</p>
    <p class="phone">(512) 555-0142</p>
    <a class="website" href="https://acmewidgets.example/">acmewidgets.example</a>
    <a class="linkedin" href="https://www.linkedin.com/in/jane-founder">LinkedIn</a>
    <span class="email">hello@acmewidgets.example</span>
  </div>
  <div class="listing-card">
    <h2 class="name">Carlos Rivera</h2>
    <p class="title">Founder at Rivera Robotics</p>
    <p class="phone">206-555-0177</p>
    <a class="website" href="https://riverarobotics.example/">riverarobotics.example</a>
  </div>
  <div class="listing-card">
    <h2 class="name">Priya Sharma</h2>
    <p class="title">CTO at Sharda Data</p>
    <p class="phone">+91 80 5555 0188</p>
    <a class="website" href="https://shardadata.example/">shardadata.example</a>
    <span class="email">contact@shardadata.example</span>
  </div>
  <nav><a class="next" href="/page2.html">Next</a></nav>
</body></html>
"""

# The same three cards, but with no next link — the end of the directory.
LAST_PAGE = DEMO_PAGE.replace('<nav><a class="next" href="/page2.html">Next</a></nav>', "")

EMPTY_PAGE = "<html><body><h1>No listings here</h1></body></html>"


def _configured(campaign):
    from openoutreach.core.models import Campaign

    campaign = Campaign.objects.get(pk=campaign.pk)
    campaign.scraper_config = DEMO_CFG
    campaign.save(update_fields=["scraper_config"])
    return campaign


@pytest.mark.skipif(not HAS_SCRAPLING, reason="scrapling not installed")
class TestScrapeOnePage:
    def test_harvests_leads_and_advances_cursor(self, db, campaign):
        from openoutreach.core.pipeline.scrape_acquisition import scrape_one_page
        from openoutreach.crm.models import Lead

        campaign = _configured(campaign)
        with patch(_FETCH, return_value=FakeResponse(DEMO_PAGE)):
            created = scrape_one_page(campaign)

        assert created == 3
        assert Lead.objects.count() == 3

        jane = Lead.objects.get(profile_url="https://www.linkedin.com/in/jane-founder")
        assert jane.email == "hello@acmewidgets.example"
        assert jane.country_code == campaign.country_code  # stamped from the campaign
        assert jane.embedding is not None
        assert jane.source_fields["contact_first_name"] == "Jane"
        assert jane.source_fields["contact_last_name"] == "Founder"
        assert jane.source_fields["contact_job_title"] == "CEO at Acme Widgets"
        assert jane.source_fields["company_domain"] == "acmewidgets.example"
        assert jane.phone == "5125550142"
        assert jane.website == "https://acmewidgets.example/"
        # phone/website are folded into profile_text, so the LLM qualifier sees them.
        assert "5125550142" in jane.profile_text
        assert "acmewidgets.example" in jane.profile_text

        # A card without a LinkedIn link gets a synthetic per-person key.
        carlos = Lead.objects.get(profile_url="https://riverarobotics.example/#scraped-carlos-rivera")
        assert carlos.email is None  # no email on the card — the funnel resolves it later
        assert carlos.phone == "2065550177"

        # Cursor advanced for the next cycle.
        campaign.refresh_from_db()
        state = campaign.scrape_state
        assert state["pages"] == 1
        assert state["next_url"] == "https://directory.example/page2.html"
        assert not state.get("exhausted")

    def test_dedupes_on_second_pass(self, db, campaign):
        from openoutreach.core.pipeline.scrape_acquisition import scrape_one_page
        from openoutreach.crm.models import Lead

        campaign = _configured(campaign)
        with patch(_FETCH, return_value=FakeResponse(DEMO_PAGE)):
            assert scrape_one_page(campaign) == 3
            assert scrape_one_page(campaign) == 0
        assert Lead.objects.count() == 3

    def test_no_next_link_marks_exhausted(self, db, campaign):
        from openoutreach.core.pipeline.scrape_acquisition import scrape_one_page

        campaign = _configured(campaign)
        with patch(_FETCH, return_value=FakeResponse(LAST_PAGE)):
            assert scrape_one_page(campaign) == 3
        campaign.refresh_from_db()
        assert campaign.scrape_state["exhausted"] is True

        # Exhausted → the step never fetches again.
        with patch(_FETCH, side_effect=AssertionError("must not fetch")):
            assert scrape_one_page(campaign) == 0

    def test_fetch_failure_keeps_cursor(self, db, campaign):
        from openoutreach.core.pipeline.scrape_acquisition import scrape_one_page

        campaign = _configured(campaign)
        with patch(_FETCH, return_value=None):
            assert scrape_one_page(campaign) == 0
        campaign.refresh_from_db()
        assert not campaign.scrape_state.get("exhausted")
        assert campaign.scrape_state.get("next_url") is None

    def test_no_cards_on_first_page_disables(self, db, campaign):
        from openoutreach.core.pipeline.scrape_acquisition import scrape_one_page

        campaign = _configured(campaign)
        with patch(_FETCH, return_value=FakeResponse(EMPTY_PAGE)):
            assert scrape_one_page(campaign) == 0
        campaign.refresh_from_db()
        assert campaign.scrape_state["exhausted"] is True


@pytest.mark.skipif(not HAS_SCRAPLING, reason="scrapling not installed")
class TestTopUpScraper:
    def test_top_up_pages_and_labels(self, db, campaign):
        from openoutreach.core.pipeline import top_up

        campaign = _configured(campaign)
        with patch("openoutreach.core.pipeline.scrape_acquisition.scrape_one_page",
                   return_value=2) as page, \
             patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
                   return_value=[]):
            assert top_up.top_up(campaign) is True
        page.assert_called_once_with(campaign)

    def test_top_up_exhausted_idles(self, db, campaign):
        from openoutreach.core.pipeline import top_up

        campaign = _configured(campaign)
        campaign.scrape_state = {"exhausted": True}
        campaign.save(update_fields=["scrape_state"])
        with patch("openoutreach.core.pipeline.scrape_acquisition.scrape_one_page",
                   side_effect=AssertionError("must not page")) as page, \
             patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
                   return_value=[]):
            assert top_up.top_up(campaign) is False
        page.assert_not_called()

    def test_top_up_qualifies_the_pool_after_drain(self, db, campaign):
        from openoutreach.core.pipeline import top_up

        campaign = _configured(campaign)
        with patch("openoutreach.core.pipeline.scrape_acquisition.scrape_one_page",
                   return_value=0), \
             patch("openoutreach.core.pipeline.top_up.fetch_qualification_candidates",
                   return_value=[object()]), \
             patch("openoutreach.core.pipeline.top_up.run_qualification",
                   return_value="https://lead.example/1"):
            assert top_up.top_up(campaign) is True
