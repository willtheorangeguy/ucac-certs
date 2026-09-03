"""Security properties that must hold before this is exposed to the internet."""

import pytest
from fastapi.testclient import TestClient

from lss_report.web.app import create_app
from lss_report.web.auth import SESSION_COOKIE, Auth
from lss_report.web.repository import StaffRepository
from lss_report.web.scans import Verification

WRITE_ENDPOINTS = [
    ("/scan", {}),
    ("/staff", {"name": "Intruder", "member_code": "AAA111"}),
    ("/staff/1/remove", {}),
    ("/staff/1/adopt-name", {}),
    ("/staff/1/edit", {"name": "Intruder", "member_code": "AAA111"}),
]


@pytest.fixture
def client(settings, database):
    with TestClient(create_app(settings, database), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client, database, settings):
    client.cookies.set(
        SESSION_COOKIE, Auth(database, settings).create_session("manager@example.org")
    )
    return client


@pytest.mark.parametrize("path, payload", WRITE_ENDPOINTS)
def test_write_endpoints_reject_anonymous_callers(client, database, path, payload):
    response = client.post(path, data=payload)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert StaffRepository(database).active() == []


def test_session_cookie_is_httponly_and_samesite(client, database, settings):
    token = Auth(database, settings).issue_login_token("manager@example.org", client="1.2.3.4")
    response = client.get(f"/auth?token={token}")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_session_cookie_is_not_marked_secure_over_plain_http(client, database, settings):
    # base_url is http in tests; a Secure cookie would never be sent back and would
    # silently break local development. On https deployments it must be set.
    token = Auth(database, settings).issue_login_token("manager@example.org", client="1.2.3.4")
    response = client.get(f"/auth?token={token}")
    assert "secure" not in response.headers["set-cookie"].lower()


def test_reflected_error_message_is_escaped(signed_in, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=False, error="nope"),
    )
    payload = "<script>alert(1)</script>"
    response = signed_in.get("/staff", params={"error": payload})
    assert response.status_code == 200
    assert payload not in response.text
    assert "&lt;script&gt;" in response.text


def test_staff_name_is_escaped_on_the_roster_page(signed_in, database, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="<img src=x onerror=alert(1)>"),
    )
    signed_in.post("/staff", data={"name": "<b>Bold</b>", "member_code": "AAA111"})
    response = signed_in.get("/staff")
    assert "<b>Bold</b>" not in response.text
    assert "<img src=x onerror=alert(1)>" not in response.text


def test_unapproved_address_is_told_it_has_no_access(client):
    # Deliberate trade: the rejection notice is clearer for staff, but it does let
    # someone probe which addresses are managers. The rate limits below are what
    # keep that probing slow.
    response = client.post("/login", data={"email": "nobody@example.org"})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?denied=1"
    assert "does not have access" in client.get("/login", params={"denied": "1"}).text


def test_an_approved_address_is_told_the_link_was_sent(client):
    response = client.post("/login", data={"email": "manager@example.org"})
    assert response.headers["location"] == "/login?sent=1"
    assert "on its way" in client.get("/login", params={"sent": "1"}).text


def test_a_rejected_address_still_never_receives_a_token(client, database):
    client.post("/login", data={"email": "nobody@example.org"})
    assert database.query("SELECT id FROM login_token") == []


def test_probing_for_managers_is_rate_limited(client, database):
    # The oracle exists, so it must at least be slow: repeated probes stop being
    # answered once the per-IP limit trips.
    for index in range(12):
        client.post("/login", data={"email": f"probe{index}@example.org"})
    assert database.query("SELECT id FROM login_token") == []


def test_auth_endpoint_ignores_a_forged_token(client):
    response = client.get("/auth?token=not-a-real-token")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_scan_status_needs_a_session(client):
    assert client.get("/scan/status").status_code == 303


def test_a_name_with_an_apostrophe_cannot_break_out_of_the_confirm_dialog(signed_in, monkeypatch):
    monkeypatch.setattr(
        "lss_report.web.app.verify_member_code",
        lambda code, name, **kwargs: Verification(ok=True, society_name="Robin O'Brien"),
    )
    signed_in.post("/staff", data={"name": "Robin O'Brien", "member_code": "AAA111"})
    text = signed_in.get("/staff").text
    # The apostrophe is escaped inside the JavaScript literal, and the literal's own
    # double quotes sit inside a single-quoted HTML attribute.
    assert 'confirm("Remove " + "Robin O\\u0027Brien"' in text
