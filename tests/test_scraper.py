from pathlib import Path

import pytest

from lss_report.awards import CPR_C, FIRST_AID, NATIONAL_LIFEGUARD
from lss_report.models import StaffMember
from lss_report.scraper import ParseError, SocietyClient, parse_member_page

FIXTURE = Path(__file__).parent / "fixtures" / "member.html"


def _by_column(record, column):
    return [cert for cert in record.certifications if cert.column is column]


def test_expiry_is_computed_because_the_society_never_publishes_one():
    record = parse_member_page(
        FIXTURE.read_text(encoding="utf-8"),
        StaffMember("Example Staff Member", "ABC123"),
    )
    assert record.error is None
    recert = _by_column(record, NATIONAL_LIFEGUARD)[0]
    assert recert.name == "National Lifeguard - Pool Recert"
    assert recert.certification_date.isoformat() == "2025-09-14"
    assert recert.expiry_date.isoformat() == "2027-09-14"


def test_cpr_award_fills_both_the_cpr_and_first_aid_columns():
    record = parse_member_page(
        FIXTURE.read_text(encoding="utf-8"),
        StaffMember("Example Staff Member", "ABC123"),
    )
    cpr = _by_column(record, CPR_C)[0]
    first_aid = _by_column(record, FIRST_AID)[0]
    assert cpr.expiry_date.isoformat() == "2026-09-14"
    assert not cpr.provisional
    assert first_aid.expiry_date.isoformat() == "2027-09-14"
    assert first_aid.provisional


def test_instructor_award_is_not_mistaken_for_a_cpr_certification():
    record = parse_member_page(
        FIXTURE.read_text(encoding="utf-8"),
        StaffMember("Example Staff Member", "ABC123"),
    )
    assert len(_by_column(record, CPR_C)) == 1


def test_name_mismatch_is_a_warning_and_keeps_the_awards():
    record = parse_member_page(
        FIXTURE.read_text(encoding="utf-8"),
        StaffMember("Example Staff Membre", "ABC123"),
    )
    assert record.error is None
    assert record.name_warning
    assert record.certifications
    assert record.display_name == "Example Staff Member"


def test_member_id_mismatch_is_still_fatal():
    html = FIXTURE.read_text(encoding="utf-8").replace("ABC123", "XYZ789", 1)
    with pytest.raises(ParseError):
        parse_member_page(html, StaffMember("Example Staff Member", "ABC123"))


def test_malformed_page_is_fatal():
    with pytest.raises(ParseError):
        parse_member_page("<html></html>", StaffMember("Person", "ABC123"))


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}

    def get(self, *args, **kwargs):
        return next(self.responses)


def test_redirect_is_a_member_error():
    client = SocietyClient(
        session=FakeSession(
            [FakeResponse(302, headers={"Location": "https://www.lifesaving.org/member-services/find-a-member"})]
        ),
        sleep=lambda _: None,
    )
    record = client.fetch(StaffMember("Missing Person", "ZZZZZZ"))
    assert record.error == "Member ID was not found."


def test_rate_limit_is_retried():
    session = FakeSession(
        [FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200, FIXTURE.read_text())]
    )
    waits = []
    client = SocietyClient(session=session, sleep=waits.append)
    record = client.fetch(StaffMember("Example Staff Member", "ABC123"))
    assert record.error is None
    assert waits == [1.0]
