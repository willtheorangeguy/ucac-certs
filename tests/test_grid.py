from datetime import date, datetime

from lss_report.awards import CPR_C, FIRST_AID, NATIONAL_LIFEGUARD, OXYGEN
from lss_report.grid import build_grid, status_for
from lss_report.models import CellStatus, Certification, MemberRecord, ReportData

AS_OF = date(2026, 8, 30)


def _report(*records: MemberRecord) -> ReportData:
    return ReportData(generated_at=datetime(2026, 8, 30, 6, 15), records=list(records))


def _cell(grid, column):
    return next(cell for cell in grid.rows[0].cells if cell.column is column)


def test_status_boundaries_are_inclusive_at_zero_and_thirty_days():
    assert status_for(date(2026, 8, 29), AS_OF) is CellStatus.EXPIRED
    assert status_for(date(2026, 8, 30), AS_OF) is CellStatus.EXPIRING
    assert status_for(date(2026, 9, 29), AS_OF) is CellStatus.EXPIRING
    assert status_for(date(2026, 9, 30), AS_OF) is CellStatus.CURRENT
    assert status_for(None, AS_OF) is CellStatus.MISSING


def test_latest_expiry_wins_within_a_column():
    record = MemberRecord(
        configured_name="Example",
        member_code="ABC123",
        certifications=[
            Certification("National Lifeguard - Pool", date(2021, 12, 24), NATIONAL_LIFEGUARD, date(2023, 12, 24), False),
            Certification("National Lifeguard - Pool Recert", date(2025, 9, 14), NATIONAL_LIFEGUARD, date(2027, 9, 14)),
        ],
    )
    cell = _cell(build_grid(_report(record)), NATIONAL_LIFEGUARD)
    assert cell.expiry_date == date(2027, 9, 14)
    assert cell.status is CellStatus.CURRENT


def test_confirmed_award_beats_a_later_provisional_one():
    record = MemberRecord(
        configured_name="Example",
        member_code="ABC123",
        certifications=[
            Certification("Standard First Aid", date(2025, 1, 1), FIRST_AID, date(2027, 1, 1)),
            Certification("Lifesaving CPR C & AED", date(2026, 1, 1), FIRST_AID, date(2028, 1, 1), provisional=True),
        ],
    )
    cell = _cell(build_grid(_report(record)), FIRST_AID)
    assert cell.source_award == "Standard First Aid"
    assert not cell.provisional


def test_missing_award_renders_as_a_missing_cell():
    record = MemberRecord(configured_name="Example", member_code="ABC123")
    assert _cell(build_grid(_report(record)), OXYGEN).status is CellStatus.MISSING


def test_lookup_error_suppresses_all_cells():
    record = MemberRecord(
        configured_name="Example",
        member_code="ZZZZZZ",
        error="Member ID was not found.",
        certifications=[Certification("O2 Administration", date(2026, 1, 1), OXYGEN, date(2028, 1, 1))],
    )
    row = build_grid(_report(record)).rows[0]
    assert row.error
    assert all(cell.status is CellStatus.MISSING for cell in row.cells)


def test_away_staff_sort_after_active_staff():
    active = MemberRecord(configured_name="Zoe Active", member_code="AAA111")
    away = MemberRecord(configured_name="Aaron Away", member_code="BBB222", away=True)
    grid = build_grid(_report(away, active))
    assert [row.name for row in grid.rows] == ["Zoe Active", "Aaron Away"]


def test_unmapped_awards_are_collected_once():
    record = MemberRecord(
        configured_name="Example",
        member_code="ABC123",
        certifications=[
            Certification("Wilderness Guide Level 4", date(2025, 1, 1)),
            Certification("Wilderness Guide Level 4", date(2024, 1, 1)),
            Certification("Bronze Cross", date(2019, 11, 29)),
        ],
    )
    assert build_grid(_report(record)).unmapped_awards == ("Wilderness Guide Level 4",)


def test_disagreement_with_the_society_is_reported():
    record = MemberRecord(
        configured_name="Example",
        member_code="ABC123",
        certifications=[
            Certification("2023 National Lifeguard Update", date(2023, 4, 19), NATIONAL_LIFEGUARD, date(2025, 4, 19), site_current=True)
        ],
    )
    grid = build_grid(_report(record))
    assert len(grid.disagreements) == 1
    assert "ABC123" in grid.disagreements[0]


def test_cpr_and_first_aid_are_not_cross_checked_against_the_society():
    # House policy is deliberately shorter than the Society's, so these always differ.
    record = MemberRecord(
        configured_name="Example",
        member_code="ABC123",
        certifications=[
            Certification("Lifesaving CPR C & AED", date(2020, 1, 1), CPR_C, date(2021, 1, 1), site_current=True)
        ],
    )
    assert build_grid(_report(record)).disagreements == ()
