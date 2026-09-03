from pathlib import Path

import pytest

from lss_report.awards import CPR_C, FIRST_AID
from lss_report.redcross import (
    RedCrossClient,
    certifications_from,
    last_name_for,
    parse_validation,
)
from lss_report.scraper import ParseError, UpstreamError

FIXTURES = Path(__file__).parent / "fixtures"
VALID = (FIXTURES / "redcross_valid.html").read_text(encoding="utf-8")
NOT_FOUND = (FIXTURES / "redcross_not_found.html").read_text(encoding="utf-8")


def test_a_valid_certificate_is_read_off_the_result_paragraph():
    certificate = parse_validation(VALID, "103575156")
    assert certificate.certificate_number == "103575156"
    assert certificate.award_name == "Standard First Aid CPR/AED Level C (Blended)"
    assert certificate.expiry_date.isoformat() == "2025-10-14"
    assert certificate.valid


def test_an_unknown_certificate_is_not_an_error():
    assert parse_validation(NOT_FOUND, "103575156") is None


def test_a_certificate_number_the_validator_did_not_echo_back_is_fatal():
    with pytest.raises(ParseError):
        parse_validation(VALID, "999999999")


def test_a_page_without_a_result_paragraph_is_fatal():
    with pytest.raises(ParseError):
        parse_validation("<html><body></body></html>", "103575156")


def test_the_published_expiry_is_worked_back_to_a_certification_date():
    # The Red Cross publishes only an expiry, three years out from the course.
    certificate = parse_validation(VALID, "103575156")
    assert certificate.certification_date.isoformat() == "2022-10-14"


def test_each_column_expires_on_the_aquatic_centre_schedule_not_the_red_cross_one():
    certifications = certifications_from(parse_validation(VALID, "103575156"))
    by_column = {cert.column: cert for cert in certifications}

    # Two years for first aid, not the three the card itself runs.
    assert by_column[FIRST_AID].expiry_date.isoformat() == "2024-10-14"
    assert by_column[CPR_C].expiry_date.isoformat() == "2023-10-14"


def test_a_red_cross_first_aid_record_is_not_provisional():
    certifications = certifications_from(parse_validation(VALID, "103575156"))
    first_aid = next(cert for cert in certifications if cert.column is FIRST_AID)
    assert not first_aid.provisional


@pytest.mark.parametrize(
    "name, expected",
    [("Amrit Tiu", "Tiu"), ("Robin de la Cruz", "Cruz"), ("Madonna", "Madonna")],
)
def test_the_validator_keys_off_the_last_word_of_the_name(name, expected):
    assert last_name_for(name) == expected


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return next(self.responses)


def test_the_lookup_sends_the_last_name_and_the_number():
    session = FakeSession([FakeResponse(200, VALID)])
    client = RedCrossClient(session=session, sleep=lambda _: None)

    certificate = client.fetch("Tiu", "103575156")

    assert certificate.certificate_number == "103575156"
    assert "ln=Tiu" in session.urls[0]
    assert "cn=103575156" in session.urls[0]


def test_rate_limit_is_retried():
    session = FakeSession(
        [FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200, VALID)]
    )
    waits = []
    client = RedCrossClient(session=session, sleep=waits.append)

    assert client.fetch("Tiu", "103575156") is not None
    assert waits == [1.0]


def test_a_non_numeric_number_never_reaches_the_validator():
    session = FakeSession([])
    client = RedCrossClient(session=session, sleep=lambda _: None)

    with pytest.raises(UpstreamError):
        client.fetch("Tiu", "ABC123")
    assert session.urls == []
