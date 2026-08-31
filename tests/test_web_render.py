"""Render the pages against stored scan data. Template errors only surface at runtime."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lss_report.awards import NATIONAL_LIFEGUARD, OXYGEN, SWIM_INSTRUCTOR
from lss_report.grid import build_grid
from lss_report.models import Certification, MemberRecord, ReportData
from lss_report.web.app import create_app
from lss_report.web.auth import SESSION_COOKIE, Auth
from lss_report.web.repository import ScanRepository, StaffRepository

TODAY = date(2026, 8, 30)


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
    grid = build_grid(ReportData(generated_at=datetime(2026, 8, 30, 6, 15), records=records))
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
    assert "Away Spring / Summer" in response.text
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


def test_notifications_page_previews_without_sending(client, populated):
    response = client.get("/notifications")
    assert response.status_code == 200
    assert populated.query("SELECT id FROM notification_log") == []


def test_excel_export_downloads(client):
    response = client.get("/export.xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert "attachment" in response.headers["content-disposition"]


def test_pdf_export_downloads(client):
    response = client.get("/export.pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
