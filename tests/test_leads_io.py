# tests/test_leads_io.py
"""Tests for the leads CSV import/export — the portable bridge in and out of
the store. Locks down the round-trip shape (export feeds straight back into
import), dedupe on profile_url, embedding on import, the no-text warning, and
the dashboard export/import endpoints. ``embed_text`` is mocked by conftest, so
the ONNX model is never needed.
"""
from __future__ import annotations

from io import BytesIO, StringIO

import pytest
from django.core.management import call_command

from openoutreach.core.db.leads import LEAD_EXPORT_FIELDS, import_leads_from_rows, lead_to_row
from tests.factories import LeadFactory, UserFactory


@pytest.fixture
def staff_client(client, operator):
    client.force_login(operator)
    return client


def _csv_file(rows: list[dict]) -> str:
    import csv

    out = StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def _rows(n=2, prefix="https://www.linkedin.com/in/io-"):
    return [
        {
            "profile_url": f"{prefix}{i}/",
            "email": f"io{i}@x.io",
            "country_code": "fr",
            "contact_job_title": "Founder" if i % 2 == 0 else "CTO",
            "company_name": f"Acme {i}",
            "contact_headline": f"Building {i}",
        }
        for i in range(n)
    ]


@pytest.mark.django_db
class TestImportRows:
    def test_creates_embedded_leads_with_country_email_and_fields(self, db):
        report = import_leads_from_rows(_rows(2))
        assert report.created == 2 and report.total == 2

        from openoutreach.crm.models import Lead

        lead = Lead.objects.get(profile_url="https://www.linkedin.com/in/io-0/")
        assert lead.email == "io0@x.io"
        assert lead.country_code == "fr"
        assert lead.embedding is not None          # embedded like a discovered lead
        assert "founder" in lead.profile_text      # firmographic text built for the LLM
        assert lead.source_fields["contact_job_title"] == "Founder"

    def test_dedupes_on_profile_url(self, db):
        import_leads_from_rows(_rows(1))
        again = import_leads_from_rows(_rows(1))
        assert again.created == 0
        assert again.skipped_existing == 1

    def test_counts_rows_without_url(self, db):
        report = import_leads_from_rows([{"email": "no@url.io"}])
        assert report.no_url == 1 and report.created == 0

    def test_warns_about_leads_without_profile_text(self, db):
        report = import_leads_from_rows([{"profile_url": "https://x/bare/"}])
        assert report.created == 1 and report.no_text == 1

    def test_default_country_applies_when_column_missing(self, db):
        rows = _rows(1)
        del rows[0]["country_code"]
        report = import_leads_from_rows(rows, default_country="us")
        from openoutreach.crm.models import Lead

        assert report.created == 1
        assert Lead.objects.first().country_code == "us"

    def test_raw_profile_text_column_wins(self, db):
        rows = _rows(1)
        rows[0]["profile_text"] = "the raw judge input"
        import_leads_from_rows(rows)
        from openoutreach.crm.models import Lead

        assert Lead.objects.first().profile_text == "the raw judge input"


@pytest.mark.django_db
class TestExportRoundTrip:
    def test_export_writes_the_portable_shape(self, db, tmp_path):
        lead = LeadFactory(email="round@x.io", country_code="fr")
        path = tmp_path / "leads.csv"
        out = StringIO()
        call_command("export_leads", "--path", str(path), stdout=out)
        assert "exported 1 lead(s)" in out.getvalue()

        text = path.read_text(encoding="utf-8-sig")
        assert text.startswith("contact_linkedin_profile_url,email,country_code")
        assert lead.profile_url in text
        assert "round@x.io" in text

    def test_export_feeds_straight_back_into_import(self, db, tmp_path):
        import csv

        from openoutreach.crm.models import Lead

        LeadFactory(email="a@x.io", country_code="fr", phone="+15125550142",
                    website="https://acme.example/",
                    profile_url="https://www.linkedin.com/in/roundtrip/")
        path = tmp_path / "leads.csv"
        call_command("export_leads", "--path", str(path))

        # wipe and re-import the export — the round trip must rebuild the lead
        Lead.objects.all().delete()
        out = StringIO()
        call_command("import_leads", str(path), stdout=out)
        assert "imported 1 of 1 row(s)" in out.getvalue()
        lead = Lead.objects.get()
        assert lead.profile_url == "https://www.linkedin.com/in/roundtrip/"
        assert lead.email == "a@x.io"
        assert lead.country_code == "fr"
        assert lead.embedding is not None
        assert lead.phone == "+15125550142"
        assert lead.website == "https://acme.example/"

    def test_reembed_rebuilds_text_and_embedding(self, db):
        """reembed_leads folds the new columns into profile_text + embedding."""
        import numpy as np

        from openoutreach.crm.models import Lead

        LeadFactory(
            profile_url="https://www.linkedin.com/in/reembed/",
            profile_text="stale text",
            embedding=np.zeros(384, dtype=np.float32).tobytes(),
            source_fields={"contact_job_title": "Founder",
                           "contact_first_name": "Ada"},
            phone="5125550142",
            website="https://ada.example/",
        )
        call_command("reembed_leads")

        lead = Lead.objects.get()
        assert "founder" in lead.profile_text
        assert "5125550142" in lead.profile_text        # phone folded in
        assert "ada.example" in lead.profile_text       # website folded in
        assert (lead.embedding_array == np.ones(384)).all()  # rebuilt (mock embed)

    def test_lead_to_row_covers_export_fields(self, db):
        lead = LeadFactory(email="e@x.io")
        row = lead_to_row(lead)
        assert set(LEAD_EXPORT_FIELDS) == set(row.keys())
        assert row["contact_linkedin_profile_url"] == lead.profile_url


@pytest.mark.django_db
class TestDashboardIo:
    def test_leads_export_returns_csv(self, staff_client):
        from openoutreach.crm.models import Lead

        LeadFactory(email="exp@x.io")
        response = staff_client.get("/dashboard/leads/export/")
        assert response.status_code == 200
        assert "text/csv" in response["Content-Type"]
        body = b"".join(response.streaming_content)
        assert b"contact_linkedin_profile_url" in body
        assert b"exp@x.io" in body
        assert Lead.objects.count() == 1  # read-only

    def test_leads_import_upload_creates_and_reports(self, staff_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        csv_text = _csv_file(_rows(1)).encode("utf-8")
        upload = SimpleUploadedFile("leads.csv", csv_text, content_type="text/csv")
        response = staff_client.post(
            "/dashboard/leads/import/", {"file": upload}, follow=True)
        assert response.status_code == 200

        from openoutreach.crm.models import Lead

        lead = Lead.objects.get()
        assert lead.profile_url == "https://www.linkedin.com/in/io-0/"
        assert lead.email == "io0@x.io"
        assert lead.embedding is not None
        assert b"imported 1 of 1 row(s)" in response.content  # success message

    def test_leads_import_rejects_undecodable_file(self, staff_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # bytes that are not valid UTF-8 — the CSV decode fails and is reported
        upload = SimpleUploadedFile("leads.csv", b"\xff\xfe\xfa\x00", content_type="text/csv")
        response = staff_client.post(
            "/dashboard/leads/import/", {"file": upload}, follow=True)
        assert response.status_code == 200
        assert b"Could not read the file as CSV" in response.content

    def test_leads_import_requires_a_file(self, staff_client):
        response = staff_client.post(
            "/dashboard/leads/import/", {}, follow=True)
        assert response.status_code == 200
        assert b"Choose a CSV file" in response.content
