"""Render the pages against stored scan data. Template errors only surface at runtime."""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from lss_report.awards import NATIONAL_LIFEGUARD, OXYGEN, SWIM_INSTRUCTOR, add_years
from lss_report.grid import build_grid
from lss_report.models import Certification, MemberRecord, ReportData
from lss_report.web.app import create_app
from lss_report.web.auth import SESSION_COOKIE, Auth
from lss_report.web.repository import ScanRepository, StaffRepository

# The reminder pages compare stored expiries against the real clock, so the fixture
# scan has to be dated relative to today rather than pinned to a date that goes stale.
TODAY = date.today()


@pytest.fixture
def populated(database, settings):
    staff_repo = StaffRepository(database)
    scan_repo = ScanRepository(database)
    active = staff_repo.add(name="Robin Rivers", member_code="RRV001", email="robin@example.org")
    away = staff_repo.add(name="Sam Summers", member_code="SSM002", away=True)
    missing = staff_repo.add(name="Ghost Person", member_code="ZZZZZZ")

    records = [
        MemberRecord(
            configured_name=active.name,
            member_code=active.member_code,
            certifications=[
                # expired, expiring in 7 days, and no O2 on record
                Certification("National Lifeguard - Pool", date(2022, 1, 1), NATIONAL_LIFEGUARD, date(2024, 1, 1), site_current=False),
                Certification("Swim Instructor", date(2024, 9, 6), SWIM_INSTRUCTOR, TODAY + timedelta(days=7)),
            ],
        ),
        MemberRecord(configured_name=away.name, member_code=away.member_code, away=True),
        MemberRecord(
            configured_name=missing.name,
            member_code=missing.member_code,
            error="Member ID was not found.",
        ),
    ]
    grid = build_grid(
        ReportData(generated_at=datetime.combine(TODAY, time(6, 15)), records=records)
    )
    scan_id = scan_repo.start(triggered_by="test")
    scan_repo.store(
        scan_id, grid, {member.member_code: member.id for member in staff_repo.active()}
    )
    return database


@pytest.fixture
def client(settings, populated):
    app = create_app(settings, populated)
    with TestClient(app, follow_redirects=False) as test_client:
        test_client.cookies.set(
            SESSION_COOKIE, Auth(populated, settings).create_session("manager@example.org")
        )
        yield test_client


def test_dashboard_renders_colours_the_away_split_and_the_error_row(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Robin Rivers" in response.text
    assert ">Away</th>" in response.text  # the section header, not a stray match
    assert "Member ID was not found." in response.text
    assert "#FF6565" in response.text  # expired
    assert "#FFD13F" in response.text  # expiring


def test_staff_page_renders(client):
    response = client.get("/staff")
    assert response.status_code == 200
    assert "RRV001" in response.text


def test_diagnostics_page_renders_stored_notes(client):
    response = client.get("/diagnostics")
    assert response.status_code == 200
    assert "Member ID was not found." in response.text


def test_old_notifications_url_is_gone(client):
    assert client.get("/notifications").status_code == 404


def test_reminders_page_lists_the_forward_schedule(client):
    response = client.get("/reminders")
    assert response.status_code == 200
    # Robin's SI expires in 7 days, so the 7-day reminder sends today.
    assert "Robin Rivers" in response.text
    assert "SI" in response.text


def test_reminders_page_flags_staff_with_no_email(client, populated):
    from lss_report.web.repository import StaffRepository

    StaffRepository(populated).update(1, actor="test", email=None)
    assert "no email on file" in client.get("/reminders").text


def test_reminders_page_shows_sent_history(client, populated):
    with populated.write() as connection:
        connection.execute(
            "INSERT INTO notification_log (staff_id, column_code, expiry_date, threshold,"
            " channel, sent_at) VALUES (1, 'NL', '2026-09-06', 7, 'email', '2026-08-30T07:00:00')"
        )
    text = client.get("/reminders").text
    assert "2026-08-30 07:00" in text
    assert "No reminders have been sent yet" not in text


def test_reminders_page_mentions_no_sms(client):
    text = client.get("/reminders").text.lower()
    assert "sms" not in text
    assert "twilio" not in text


def test_excel_export_downloads(client):
    response = client.get("/export.xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert "attachment" in response.headers["content-disposition"]


def test_pdf_export_downloads(client):
    response = client.get("/export.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


def test_a_manual_date_fills_a_cell_the_scan_left_empty(client, populated):
    # Robin has no first aid award, so the cell is grey until one is entered by hand.
    StaffRepository(populated).set_manual_cert(1, "FA", TODAY, actor="test")

    text = client.get("/").text
    assert add_years(TODAY, 2).isoformat() in text
    assert "Manual entry" in text


def test_a_manual_date_does_not_override_a_better_scanned_award(client, populated):
    # Robin's Swim Instructor award runs to a week from today; an older hand-entered
    # date is an extra source, not an override, so it must not pull the cell back.
    StaffRepository(populated).set_manual_cert(1, "SI", date(2020, 1, 1), actor="test")

    text = client.get("/").text
    assert (TODAY + timedelta(days=7)).isoformat() in text
    assert "2022-01-01" not in text


def test_a_manual_date_moves_the_reminder_schedule_with_it(client, populated):
    # 30 days out lands inside the 60-day horizon on the 30-day step.
    certified = add_years(TODAY + timedelta(days=30), -2)
    StaffRepository(populated).set_manual_cert(1, "FA", certified, actor="test")

    text = client.get("/reminders").text
    assert "FA" in text
    assert (TODAY + timedelta(days=30)).isoformat() in text


def test_the_staff_page_offers_an_edit_panel_and_a_delete_button(client):
    text = client.get("/staff").text
    assert "fa-pen-to-square" in text
    assert "fa-trash-can" in text
    assert '<dialog id="edit-1">' in text
    assert 'name="red_cross_number"' in text
    assert 'name="manual_CPR-C"' in text


def test_the_edit_panel_is_prefilled_with_the_stored_manual_date(client, populated):
    StaffRepository(populated).set_manual_cert(1, "O2", date(2025, 5, 6), actor="test")
    assert 'name="manual_O2" value="2025-05-06"' in client.get("/staff").text


def test_adding_goes_through_the_same_panel_as_editing(client):
    text = client.get("/staff").text
    assert '<dialog id="add-staff">' in text
    # No inline add form on the page any more — the button opens the dialog.
    assert 'showModal()' in text
    # Both dialogs carry the same fields, the add one empty.
    assert 'id="rc-add"' in text
    assert 'name="manual_FA" value=""' in text
    assert 'id="rc-1"' in text
