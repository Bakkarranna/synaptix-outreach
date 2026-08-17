# tests/emails/test_finders.py
"""The free site-scrape finders: extraction, plausibility, and the three scrapers."""
import builtins
from unittest.mock import patch

import pytest

from openoutreach.emails import finders


class TestExtractEmails:
    def test_plain_addresses_and_mailto(self):
        html = '<a href="mailto:hello@acme.com">email</a> team@acme.com CONTACT@acme.com'
        assert finders.extract_emails(html) == [
            "hello@acme.com", "team@acme.com", "contact@acme.com"]

    def test_dedupes_case_insensitively(self):
        assert finders.extract_emails("A@b.co a@b.co") == ["a@b.co"]

    def test_empty(self):
        assert finders.extract_emails("no emails here") == []


class TestPlausible:
    def test_accepts_company_domain_match(self):
        assert finders.plausible("hello@acme.com", "acme.com")

    def test_rejects_off_domain_when_company_known(self):
        assert not finders.plausible("hello@gmail.com", "acme.com")

    def test_rejects_junk(self):
        assert not finders.plausible("sentry@acme.com", "acme.com")
        assert not finders.plausible("noreply@acme.com", "acme.com")
        assert not finders.plausible("you@example.com", "example.com")

    def test_rejects_malformed(self):
        assert not finders.plausible("not-an-email", "acme.com")

    def test_without_company_domain_falls_back_to_dns(self, monkeypatch):
        monkeypatch.setattr(finders, "_domain_resolves", lambda d: d == "acme.com")
        assert finders.plausible("a@acme.com")
        assert not finders.plausible("a@nowhere.invalid")


class TestSiteEmails:
    def test_returns_plausible_address_and_stops_after_first_hit(self):
        with patch("openoutreach.emails.finders._fetch",
                   return_value="<html>Contact: hello@acme.com</html>") as fetch:
            assert finders.site_emails("acme.com") == ["hello@acme.com"]
        fetch.assert_called_once()

    def test_tries_the_contact_page_when_homepage_is_empty(self):
        def fake(url, timeout):
            if url.endswith("/contact"):
                return "mailto:team@acme.com"
            return "<html></html>"

        with patch("openoutreach.emails.finders._fetch", side_effect=fake) as fetch:
            assert finders.site_emails("acme.com") == ["team@acme.com"]
        assert any("/contact" in call.args[0] for call in fetch.call_args_list)

    def test_unreachable_site_is_a_silent_miss(self):
        with patch("openoutreach.emails.finders._fetch", side_effect=TimeoutError):
            assert finders.site_emails("acme.com") == []

    def test_strips_www_and_filters_off_domain_addresses(self):
        with patch("openoutreach.emails.finders._fetch",
                   return_value="a@acme.com b@other.com"):
            assert finders.site_emails("www.acme.com") == ["a@acme.com"]


class TestFirecrawlEmails:
    def test_blank_key_returns_empty(self):
        assert finders.firecrawl_emails("acme.com", "") == []

    def test_extracts_from_scraped_markdown(self):
        with patch("openoutreach.emails.finders.requests.post") as post:
            post.return_value.json.return_value = {
                "data": {"markdown": "Reach us at sales@acme.com"}}
            post.return_value.raise_for_status.return_value = None
            assert finders.firecrawl_emails("acme.com", "key") == ["sales@acme.com"]
        assert post.call_args.args[0] == "https://api.firecrawl.dev/v2/scrape"

    def test_api_failure_is_a_silent_miss(self):
        with patch("openoutreach.emails.finders.requests.post", side_effect=TimeoutError):
            assert finders.firecrawl_emails("acme.com", "key") == []


class _FakePage:
    """Stand-in for a Scrapling response: full-text ``css("::text")`` + get_all_text."""

    def __init__(self, text: str):
        self._text = text
        self._parts = [t for t in text.splitlines() if t.strip()]

    def css(self, selector: str):
        assert selector == "::text"
        return _FakeTextList(self._parts)

    def get_all_text(self):
        return self._text


class _FakeTextList:
    def __init__(self, parts):
        self._parts = parts

    def getall(self):
        return list(self._parts)


def _page_with(text: str) -> _FakePage:
    return _FakePage(text)


class _FakeFetcher:
    """Records calls; ``get``/``fetch`` return a page or raise the given error."""

    def __init__(self, get_result=None, get_error=None, fetch_result=None, fetch_error=None):
        self._get_result, self._get_error = get_result, get_error
        self._fetch_result, self._fetch_error = fetch_result, fetch_error
        self.get_calls = 0
        self.fetch_calls = 0

    def get(self, *a, **k):
        self.get_calls += 1
        if self._get_error is not None:
            raise self._get_error
        return self._get_result

    def fetch(self, *a, **k):
        self.fetch_calls += 1
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._fetch_result


def _install_fake_scrapling(fetcher, stealth):
    """Install fake ``scrapling`` / ``scrapling.fetchers`` modules in sys.modules."""
    import sys
    import types

    pkg = types.ModuleType("scrapling")
    pkg.Fetcher = fetcher
    fets = types.ModuleType("scrapling.fetchers")
    fets.Fetcher = fetcher
    fets.StealthyFetcher = stealth
    pkg.fetchers = fets
    sys.modules["scrapling"] = pkg
    sys.modules["scrapling.fetchers"] = fets


class _fake_scrapling:
    """Context manager: install the fake scrapling modules, restore after."""

    def __init__(self, fetcher, stealth):
        self._fetcher, self._stealth = fetcher, stealth

    def __enter__(self):
        import sys

        self._saved = (sys.modules.get("scrapling"),
                       sys.modules.get("scrapling.fetchers"))
        _install_fake_scrapling(self._fetcher, self._stealth)
        return self._fetcher, self._stealth

    def __exit__(self, *exc):
        import sys

        saved_pkg, saved_fets = self._saved
        if saved_pkg is None:
            sys.modules.pop("scrapling", None)
        else:
            sys.modules["scrapling"] = saved_pkg
        if saved_fets is None:
            sys.modules.pop("scrapling.fetchers", None)
        else:
            sys.modules["scrapling.fetchers"] = saved_fets


class TestScraplingEmails:
    def test_not_installed_returns_empty(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "scrapling":
                raise ImportError
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            assert finders.scrapling_emails("acme.com") == []

    def test_plain_fetch_success_skips_the_browser(self):
        fetcher = _FakeFetcher(get_result=_page_with("Contact: hello@acme.com"))
        stealth = _FakeFetcher(fetch_result=AssertionError("browser must not launch"))
        with _fake_scrapling(fetcher, stealth):
            assert finders.scrapling_emails("acme.com") == ["hello@acme.com"]
        assert stealth.fetch_calls == 0

    def test_reads_descendant_text_nodes(self):
        """The email lives in a nested element — direct root text is empty."""
        html = ("<html><body><ul><li><a href='/x'>Contact</a> info@acme.com"
                "</li></ul></body></html>")
        fetcher = _FakeFetcher(get_result=_page_with(html))
        stealth = _FakeFetcher(fetch_result=AssertionError("browser must not launch"))
        with _fake_scrapling(fetcher, stealth):
            assert finders.scrapling_emails("acme.com") == ["info@acme.com"]
        assert stealth.fetch_calls == 0

    def test_stealth_rescues_when_plain_fetch_is_blocked(self):
        # A Cloudflare 403 makes the plain HTTP leg raise; the browser reads the page.
        fetcher = _FakeFetcher(get_error=TimeoutError("blocked"))
        stealth = _FakeFetcher(fetch_result=_page_with("Reach us at sales@acme.com"))
        with _fake_scrapling(fetcher, stealth):
            assert finders.scrapling_emails("acme.com") == ["sales@acme.com"]
        assert stealth.fetch_calls == 1

    def test_stealth_rescues_when_plain_page_has_no_emails(self):
        # A JS-rendered page returns HTML with no emails; the browser re-renders it.
        fetcher = _FakeFetcher(get_result=_page_with("<html><p>no mail</p></html>"))
        stealth = _FakeFetcher(fetch_result=_page_with("jane@acme.com"))
        with _fake_scrapling(fetcher, stealth):
            assert finders.scrapling_emails("acme.com") == ["jane@acme.com"]

    def test_both_legs_failing_is_a_silent_miss(self):
        fetcher = _FakeFetcher(get_error=TimeoutError("blocked"))
        stealth = _FakeFetcher(fetch_error=TimeoutError("no browser"))
        with _fake_scrapling(fetcher, stealth):
            assert finders.scrapling_emails("acme.com") == []


@pytest.mark.django_db
class TestFindEmails:
    def test_no_company_domain_returns_none(self):
        from tests.factories import LeadFactory

        assert finders.find_emails(LeadFactory()) is None

    def test_cheapest_source_wins_and_personal_match_preferred(self):
        from tests.factories import LeadFactory

        lead = LeadFactory(source_fields={
            "company_domain": "acme.com", "contact_first_name": "Jane"})
        with patch("openoutreach.emails.finders.site_emails",
                   return_value=["info@acme.com", "jane@acme.com"]), \
                patch("openoutreach.emails.finders.firecrawl_emails") as fc, \
                patch("openoutreach.emails.finders.scrapling_emails") as sc:
            assert finders.find_emails(lead) == "jane@acme.com"
        fc.assert_not_called()
        sc.assert_not_called()

    def test_falls_through_to_firecrawl_when_the_site_has_nothing(self):
        from tests.factories import LeadFactory

        lead = LeadFactory(source_fields={"company_domain": "acme.com"})
        with patch("openoutreach.emails.finders.site_emails", return_value=[]), \
                patch("openoutreach.emails.finders.firecrawl_emails",
                      return_value=["sales@acme.com"]), \
                patch("openoutreach.emails.finders.scrapling_emails") as sc:
            assert finders.find_emails(lead) == "sales@acme.com"
        sc.assert_not_called()

    def test_all_scrapers_empty_returns_none(self):
        from tests.factories import LeadFactory

        lead = LeadFactory(source_fields={"company_domain": "acme.com"})
        with patch("openoutreach.emails.finders.site_emails", return_value=[]), \
                patch("openoutreach.emails.finders.firecrawl_emails", return_value=[]), \
                patch("openoutreach.emails.finders.scrapling_emails", return_value=[]):
            assert finders.find_emails(lead) is None
