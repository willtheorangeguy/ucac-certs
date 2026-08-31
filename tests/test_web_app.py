import pytest
from fastapi.testclient import TestClient

from lss_report.web.app import create_app
from lss_report.web.auth import SESSION_COOKIE, Auth
from lss_report.web.repository import StaffRepository
from lss_report.web.scans import Verification

PROTECTED = ["/", "/staff", "/diagnostics", "/export.xlsx", "/export.pdf"]


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
    assert "Certification overview" in response.text


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
