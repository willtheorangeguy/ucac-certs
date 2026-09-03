import pytest
from fastapi.testclient import TestClient

from lss_report.web.app import create_app
from lss_report.web.auth import SESSION_COOKIE, Auth
from lss_report.web.repository import StaffRepository
from lss_report.web.scans import Verification

PROTECTED = ["/", "/staff", "/reminders", "/diagnostics", "/export.xlsx", "/export.pdf"]


@pytest.fixture
def client(settings, database):
    app = create_app(settings, database)
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client, database, settings):
    client.cookies.set(SESSION_COOKIE, Auth(database, settings).create_session("manager@example.org"))
    return client


@pytest.mark.parametrize("path", PROTECTED)
def test_every_page_requires_a_session(client, path):
    response = client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_distinguishes_managers_from_strangers(client):
    manager = client.post("/login", data={"email": "manager@example.org"})
    stranger = client.post("/login", data={"email": "stranger@example.org"})
    assert manager.headers["location"] == "/login?sent=1"
    assert stranger.headers["location"] == "/login?denied=1"


def test_stranger_never_gets_a_token(client, database):
    client.post("/login", data={"email": "stranger@example.org"})
    assert database.query("SELECT id FROM login_token") == []


def test_signed_in_manager_sees_the_dashboard(signed_in):
    response = signed_in.get("/")
    assert response.status_code == 200
    assert "Certification Overview" in response.text


def test_adding_staff_verifies_the_member_id_first(signed_in, database, monkeypatch):
    calls = []

    def fake_verify(code, name, **kwargs):
        calls.append(code)
        return Verification(ok=True, society_name="Robin A Rivers")

    monkeypatch.setattr("lss_report.web.app.verify_member_code", fake_verify)
    response = signed_in.post("/staff", data={"name": "Robin Rivers", "member_code": "rrv001"})
    assert response.status_code == 303
    assert calls == ["RRV001"]
    member = StaffRepository(database).active()[0]
    assert member.member_code == "RRV001"
    assert member.society_name == "Robin A Rivers"


def test_a_bad_member_id_is_refused_and_not_stored(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=False, error="Member ID was not found."),
    )
    response = signed_in.post("/staff", data={"name": "Ghost", "member_code": "ZZZZZZ"})
    assert response.status_code == 303
    assert "Member+ID+was+not+found" in response.headers["location"].replace("%20", "+")
    assert StaffRepository(database).active() == []


def test_non_alphanumeric_member_id_is_rejected_without_a_lookup(signed_in, database, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("should not reach the Society")

    monkeypatch.setattr("lss_report.web.app.verify_member_code", explode)
    signed_in.post("/staff", data={"name": "Ghost", "member_code": "AB-123"})
    assert StaffRepository(database).active() == []


def test_removing_staff_takes_them_off_the_roster(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="Robin Rivers"),
    )
    signed_in.post("/staff", data={"name": "Robin Rivers", "member_code": "RRV001"})
    member = StaffRepository(database).active()[0]
    signed_in.post(f"/staff/{member.id}/remove")
    assert StaffRepository(database).active() == []


def test_export_without_a_scan_is_a_clean_404(signed_in):
    assert signed_in.get("/export.xlsx").status_code == 404


def test_healthz_needs_no_session(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_startup_closes_out_a_scan_that_a_restart_interrupted(settings, database):
    from lss_report.web.repository import ScanRepository

    scans = ScanRepository(database)
    scans.start(triggered_by="manager@example.org")

    # Standing the app up is what reconciles the row, so the dashboard cannot report a
    # scan permanently in progress after a deploy took its thread with it.
    with TestClient(create_app(settings, database), follow_redirects=False):
        pass

    assert scans.latest()["status"] == "failed"


@pytest.fixture
def member(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="Robin Rivers"),
    )
    signed_in.post("/staff", data={"name": "Robin Rivers", "member_code": "RRV001"})
    return StaffRepository(database).active()[0]


def test_editing_a_member_saves_their_details(signed_in, database, member):
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={
            "name": "Robin Rivers",
            "member_code": "RRV001",
            "email": "robin@example.org",
            "phone": "403-555-0100",
            "away": "true",
        },
    )
    refreshed = StaffRepository(database).get(member.id)
    assert refreshed.email == "robin@example.org"
    assert refreshed.phone == "403-555-0100"
    assert refreshed.away is True


def test_an_unchanged_member_id_is_not_looked_up_again(signed_in, database, member, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("should not reach the Society")

    monkeypatch.setattr("lss_report.web.app.verify_member_code", explode)
    response = signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "email": "robin@example.org"},
    )
    assert response.status_code == 303
    assert StaffRepository(database).get(member.id).email == "robin@example.org"


def test_a_red_cross_number_is_validated_before_it_is_saved(signed_in, database, member, monkeypatch):
    calls = []

    def fake_verify(number, name, **kwargs):
        calls.append((number, name))
        return Verification(ok=True)

    monkeypatch.setattr("lss_report.web.app.verify_red_cross_number", fake_verify)
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "red_cross_number": "103575156"},
    )
    assert calls == [("103575156", "Robin Rivers")]
    assert StaffRepository(database).get(member.id).red_cross_number == "103575156"


def test_a_red_cross_number_that_does_not_validate_is_refused(signed_in, database, member, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_red_cross_number",
        lambda number, name, **kwargs: Verification(ok=False, error="No Red Cross certificate."),
    )
    response = signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "red_cross_number": "999999999"},
    )
    assert "No+Red+Cross" in response.headers["location"].replace("%20", "+")
    assert StaffRepository(database).get(member.id).red_cross_number is None


def test_a_non_numeric_red_cross_number_is_rejected_without_a_lookup(signed_in, database, member, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("should not reach the Red Cross")

    monkeypatch.setattr("lss_report.web.app.verify_red_cross_number", explode)
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "red_cross_number": "ABC-1"},
    )
    assert StaffRepository(database).get(member.id).red_cross_number is None


def test_manual_dates_are_saved_and_cleared_from_the_edit_panel(signed_in, database, member):
    repo = StaffRepository(database)
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "manual_FA": "2025-03-01"},
    )
    assert repo.manual_certs(member.id) == {"FA": "2025-03-01"}

    # A column left blank clears the entry rather than leaving the old date behind.
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "manual_FA": ""},
    )
    assert repo.manual_certs(member.id) == {}


def test_a_manual_date_that_is_not_a_date_is_refused(signed_in, database, member):
    response = signed_in.post(
        f"/staff/{member.id}/edit",
        data={"name": "Robin Rivers", "member_code": "RRV001", "manual_FA": "not-a-date"},
    )
    assert response.status_code == 303
    assert "must+be+a+real+date" in response.headers["location"].replace("%20", "+")
    assert StaffRepository(database).manual_certs(member.id) == {}


def test_adding_a_member_accepts_the_whole_panel(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="Amrit K Tiu"),
    )
    monkeypatch.setattr(
        "lss_report.web.app.verify_red_cross_number",
        lambda number, name, **kwargs: Verification(ok=True),
    )
    repo = StaffRepository(database)
    signed_in.post(
        "/staff",
        data={
            "name": "Amrit Tiu",
            "member_code": "RRV001",
            "red_cross_number": "103575156",
            "email": "amrit@example.org",
            "away": "true",
            "manual_O2": "2025-05-06",
        },
    )
    member = repo.active()[0]
    assert member.red_cross_number == "103575156"
    assert member.away is True
    assert repo.manual_certs(member.id) == {"O2": "2025-05-06"}


def test_a_bad_red_cross_number_stops_the_add_before_anything_is_written(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="Amrit Tiu"),
    )
    monkeypatch.setattr(
        "lss_report.web.app.verify_red_cross_number",
        lambda number, name, **kwargs: Verification(ok=False, error="No Red Cross certificate."),
    )
    response = signed_in.post(
        "/staff",
        data={"name": "Amrit Tiu", "member_code": "RRV001", "red_cross_number": "999999999"},
    )
    assert "No+Red+Cross" in response.headers["location"].replace("%20", "+")
    assert StaffRepository(database).active() == []


def test_a_bad_manual_date_stops_the_add_without_a_lookup(signed_in, database, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("should not reach the Society")

    monkeypatch.setattr("lss_report.web.app.verify_member_code", explode)
    signed_in.post(
        "/staff",
        data={"name": "Amrit Tiu", "member_code": "RRV001", "manual_FA": "not-a-date"},
    )
    assert StaffRepository(database).active() == []


def test_a_bad_manual_date_leaves_an_edit_entirely_unsaved(signed_in, database, member):
    repo = StaffRepository(database)
    signed_in.post(
        f"/staff/{member.id}/edit",
        data={
            "name": "Robin Rivers",
            "member_code": "RRV001",
            "email": "robin@example.org",
            "manual_FA": "not-a-date",
        },
    )
    # The email sits above the date fields in the panel; a bad date must not save it.
    assert repo.get(member.id).email is None
