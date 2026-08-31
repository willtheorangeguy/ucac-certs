from datetime import datetime, timedelta, timezone

import pytest

from lss_report.web.auth import MAX_ATTEMPTS, Auth, hash_token


@pytest.fixture
def auth(database, settings) -> Auth:
    return Auth(database, settings)


def test_token_round_trip_signs_the_manager_in(auth):
    token = auth.issue_login_token("manager@example.org", client="1.2.3.4")
    assert token
    assert auth.redeem(token) == "manager@example.org"


def test_token_is_single_use(auth):
    token = auth.issue_login_token("manager@example.org", client="1.2.3.4")
    assert auth.redeem(token) == "manager@example.org"
    assert auth.redeem(token) is None


def test_token_is_stored_hashed_never_in_the_clear(auth, database):
    token = auth.issue_login_token("manager@example.org", client="1.2.3.4")
    rows = database.query("SELECT token_hash FROM login_token")
    assert rows[0]["token_hash"] == hash_token(token)
    assert token not in rows[0]["token_hash"]


def test_expired_token_is_refused(auth, database):
    token = auth.issue_login_token("manager@example.org", client="1.2.3.4")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with database.write() as connection:
        connection.execute("UPDATE login_token SET expires_at = ?", (stale,))
    assert auth.redeem(token) is None


def test_address_outside_the_allowlist_gets_no_token(auth, database):
    assert auth.issue_login_token("stranger@example.org", client="1.2.3.4") is None
    assert database.query("SELECT id FROM login_token") == []


def test_login_requests_are_rate_limited_per_address(auth):
    for _ in range(MAX_ATTEMPTS):
        auth.issue_login_token("manager@example.org", client="1.2.3.4")
    assert auth.issue_login_token("manager@example.org", client="9.9.9.9") is None


def test_session_cookie_round_trip(auth):
    cookie = auth.create_session("manager@example.org")
    assert auth.read_session(cookie) == "manager@example.org"


def test_tampered_session_cookie_is_rejected(auth):
    cookie = auth.create_session("manager@example.org")
    assert auth.read_session(cookie[:-2] + "xy") is None
    assert auth.read_session(None) is None


def test_session_stops_working_once_off_the_allowlist(database, settings):
    cookie = Auth(database, settings).create_session("manager@example.org")
    demoted = Auth(database, settings.__class__(**{**settings.__dict__, "managers": ()}))
    assert demoted.read_session(cookie) is None
