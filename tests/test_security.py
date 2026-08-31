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
    ("/notifications", {}),
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


def test_login_does_not_leak_whether_an_address_is_a_manager(client):
    manager = client.post("/login", data={"email": "manager@example.org"})
    stranger = client.post("/login", data={"email": "nobody@example.org"})
    assert manager.status_code == stranger.status_code
    assert manager.headers["location"] == stranger.headers["location"]
    assert manager.text == stranger.text


def test_auth_endpoint_ignores_a_forged_token(client):
    response = client.get("/auth?token=not-a-real-token")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_scan_status_needs_a_session(client):
    assert client.get("/scan/status").status_code == 303
