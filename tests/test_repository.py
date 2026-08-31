import json

import pytest

from lss_report.web.repository import DuplicateMemberCode, StaffRepository


@pytest.fixture
def staff(database) -> StaffRepository:
    return StaffRepository(database)


def test_add_normalises_and_records_an_audit_entry(staff, database):
    member = staff.add(name="  Robin   Rivers ", member_code=" rrv001 ", actor="manager@example.org")
    assert member.name == "Robin Rivers"
    assert member.member_code == "RRV001"
    audit = database.query("SELECT * FROM audit")
    assert audit[0]["action"] == "staff.add"
    assert audit[0]["actor"] == "manager@example.org"


def test_duplicate_active_member_code_is_refused(staff):
    staff.add(name="Robin Rivers", member_code="RRV001")
    with pytest.raises(DuplicateMemberCode):
        staff.add(name="Someone Else", member_code="rrv001")


def test_removed_member_disappears_from_the_roster_but_can_be_re_added(staff):
    member = staff.add(name="Robin Rivers", member_code="RRV001")
    staff.remove(member.id, actor="manager@example.org")
    assert staff.active() == []
    # The unique index is partial, so the same code is free again.
    again = staff.add(name="Robin Rivers", member_code="RRV001")
    assert again.id != member.id


def test_remove_is_a_soft_delete_so_history_survives(staff, database):
    member = staff.add(name="Robin Rivers", member_code="RRV001")
    staff.remove(member.id, actor="manager@example.org")
    row = database.query_one("SELECT removed_at FROM staff WHERE id = ?", (member.id,))
    assert row["removed_at"] is not None


def test_update_only_touches_permitted_fields(staff):
    member = staff.add(name="Robin Rivers", member_code="RRV001")
    staff.update(member.id, actor="manager@example.org", email="robin@example.org", id=999)
    refreshed = staff.get(member.id)
    assert refreshed.email == "robin@example.org"
    assert refreshed.id == member.id


def test_roster_sorts_away_staff_last(staff):
    staff.add(name="Zoe Active", member_code="AAA111")
    staff.add(name="Aaron Away", member_code="BBB222", away=True)
    assert [member.name for member in staff.active()] == ["Zoe Active", "Aaron Away"]


def test_seed_imports_once_then_never_again(staff, tmp_path):
    path = tmp_path / "staff.json"
    path.write_text(
        json.dumps([{"name": "Robin Rivers", "memberCode": "RRV001", "away": True}]), encoding="utf-8"
    )
    assert staff.seed_from_file(path) == 1
    assert staff.seed_from_file(path) == 0
    assert staff.active()[0].away is True


def test_a_scan_left_running_is_closed_out(database):
    from lss_report.web.repository import ScanRepository

    scans = ScanRepository(database)
    scan_id = scans.start(triggered_by="manager@example.org")

    assert scans.abandon_running() == 1

    latest = scans.latest()
    assert latest["status"] == "failed"
    assert latest["finished_at"] is not None
    assert "restart" in latest["detail"]
    assert scans.latest_complete_id() is None
    # Idempotent: a second pass finds nothing left to close.
    assert scans.abandon_running() == 0


def test_closing_out_running_scans_leaves_completed_ones_alone(database):
    from lss_report.web.repository import ScanRepository

    scans = ScanRepository(database)
    finished = scans.start(triggered_by="manager@example.org")
    with database.write() as connection:
        connection.execute(
            "UPDATE scan SET status = 'complete', finished_at = '2026-01-01T00:00:00'"
            " WHERE id = ?",
            (finished,),
        )

    assert scans.abandon_running() == 0
    assert scans.latest_complete_id() == finished
