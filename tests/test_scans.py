"""The scan itself: two sources merged into one grid."""

from datetime import date

import pytest

from lss_report.awards import CPR_C, FIRST_AID
from lss_report.models import Certification, MemberRecord
from lss_report.redcross import RedCrossCertificate
from lss_report.scraper import UpstreamError
from lss_report.web.repository import ScanRepository, StaffRepository
from lss_report.web.scans import run_scan

RED_CROSS = RedCrossCertificate(
    certificate_number="103575156",
    award_name="Standard First Aid CPR/AED Level C (Blended)",
    expiry_date=date(2027, 10, 14),
)


class FakeSociety:
    """Returns one CPR award, which credits first aid only provisionally."""

    def fetch(self, member):
        return MemberRecord(
            configured_name=member.name,
            member_code=member.member_code,
            source_name=member.name,
            certifications=[
                Certification("Lifesaving CPR C & AED", date(2025, 1, 1), CPR_C, date(2026, 1, 1)),
                Certification(
                    "Lifesaving CPR C & AED",
                    date(2025, 1, 1),
                    FIRST_AID,
                    date(2027, 1, 1),
                    provisional=True,
                ),
            ],
        )


class FakeRedCross:
    def __init__(self, result=RED_CROSS):
        self.result = result
        self.calls = []

    def fetch(self, last_name, certificate_number):
        self.calls.append((last_name, certificate_number))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def repos(database):
    return StaffRepository(database), ScanRepository(database)


def _cells(database, scan_id):
    return {
        row["column_code"]: row
        for row in database.query("SELECT * FROM scan_result WHERE scan_id = ?", (scan_id,))
    }


def test_a_red_cross_first_aid_record_replaces_the_provisional_society_one(database, repos):
    staff_repo, scan_repo = repos
    member = staff_repo.add(name="Amrit Tiu", member_code="RRV001")
    staff_repo.update(member.id, actor="test", red_cross_number="103575156")

    validator = FakeRedCross()
    run_scan(staff_repo, scan_repo, triggered_by="test", client=FakeSociety(), red_cross=validator)

    assert validator.calls == [("Tiu", "103575156")]
    first_aid = _cells(database, scan_repo.latest_complete_id())["FA"]
    assert first_aid["source_award"] == "Standard First Aid CPR/AED Level C (Blended)"
    assert first_aid["provisional"] == 0
    # Three years on the card, two here: certified 2024-10-14, so expires 2026-10-14.
    assert first_aid["expiry_date"] == "2026-10-14"


def test_a_member_with_no_red_cross_number_is_never_looked_up(repos):
    staff_repo, scan_repo = repos
    staff_repo.add(name="Robin Rivers", member_code="RRV001")

    validator = FakeRedCross()
    run_scan(staff_repo, scan_repo, triggered_by="test", client=FakeSociety(), red_cross=validator)

    assert validator.calls == []


def test_a_red_cross_outage_is_a_note_rather_than_a_failed_scan(database, repos):
    staff_repo, scan_repo = repos
    member = staff_repo.add(name="Amrit Tiu", member_code="RRV001")
    staff_repo.update(member.id, actor="test", red_cross_number="103575156")

    run_scan(
        staff_repo,
        scan_repo,
        triggered_by="test",
        client=FakeSociety(),
        red_cross=FakeRedCross(UpstreamError("Unable to contact the Red Cross validator.")),
    )

    scan_id = scan_repo.latest_complete_id()
    assert scan_id is not None
    notes = scan_repo.notes(scan_id)
    assert any(note["kind"] == "redcross" for note in notes)
    # The Society awards still stand; first aid falls back to the provisional credit.
    assert _cells(database, scan_id)["FA"]["provisional"] == 1


def test_a_certificate_that_does_not_validate_is_reported(repos):
    staff_repo, scan_repo = repos
    member = staff_repo.add(name="Amrit Tiu", member_code="RRV001")
    staff_repo.update(member.id, actor="test", red_cross_number="999999999")

    run_scan(
        staff_repo,
        scan_repo,
        triggered_by="test",
        client=FakeSociety(),
        red_cross=FakeRedCross(None),
    )

    notes = scan_repo.notes(scan_repo.latest_complete_id())
    assert any("999999999" in note["detail"] for note in notes if note["kind"] == "redcross")
