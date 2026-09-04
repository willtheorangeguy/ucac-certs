from datetime import datetime, timedelta, timezone

import pytest

from lss_report.web.auth import CODE_DIGITS, MAX_ATTEMPTS, Auth


@pytest.fixture
def auth(database, settings) -> Auth:
    return Auth(database, settings)


def issue(auth, email="manager@example.org", client="1.2.3.4") -> str:
    return auth.issue_login_code(email, client=client)


def test_code_round_trip_signs_the_manager_in(auth):
    code = issue(auth)
    assert code and len(code) == CODE_DIGITS and code.isdigit()
    assert auth.redeem("manager@example.org", code, client="1.2.3.4") == "manager@example.org"


def test_code_is_single_use(auth):
    code = issue(auth)
    assert auth.redeem("manager@example.org", code, client="1.2.3.4") == "manager@example.org"
    assert auth.redeem("manager@example.org", code, client="1.2.3.4") is None


def test_code_is_stored_hashed_never_in_the_clear(auth, database):
    code = issue(auth)
    rows = database.query("SELECT token_hash FROM login_token")
    assert rows[0]["token_hash"] == auth.code_hash("manager@example.org", code)
    assert code not in rows[0]["token_hash"]


def test_stored_digest_is_keyed_so_a_leaked_table_does_not_reveal_the_code(database, settings):
    # An unkeyed hash of six digits is reversible by anyone who reads the table.
    other = settings.__class__(**{**settings.__dict__, "session_secret": "y" * 40})
    auth = Auth(database, settings)
    assert auth.code_hash("manager@example.org", "123456") != Auth(database, other).code_hash(
        "manager@example.org", "123456"
    )


def test_a_code_cannot_be_redeemed_as_a_different_address(database, settings):
    settings = settings.__class__(
        **{**settings.__dict__, "managers": ("manager@example.org", "other@example.org")}
    )
    auth = Auth(database, settings)
    code = auth.issue_login_code("manager@example.org", client="1.2.3.4")
    assert auth.redeem("other@example.org", code, client="1.2.3.4") is None


def test_issuing_a_new_code_retires_the_previous_one(auth):
    first = issue(auth)
    second = issue(auth)
    assert first != second
    assert auth.redeem("manager@example.org", first, client="1.2.3.4") is None
    assert auth.redeem("manager@example.org", second, client="1.2.3.4") == "manager@example.org"


def test_expired_code_is_refused(auth, database):
    code = issue(auth)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with database.write() as connection:
        connection.execute("UPDATE login_token SET expires_at = ?", (stale,))
    assert auth.redeem("manager@example.org", code, client="1.2.3.4") is None


def test_address_outside_the_allowlist_gets_no_code(auth, database):
    assert auth.issue_login_code("stranger@example.org", client="1.2.3.4") is None
    assert database.query("SELECT id FROM login_token") == []


def test_login_requests_are_rate_limited_per_address(auth):
    for _ in range(MAX_ATTEMPTS):
        issue(auth)
    assert auth.issue_login_code("manager@example.org", client="9.9.9.9") is None


def test_guessing_a_code_is_rate_limited_per_address(auth):
    code = issue(auth)
    for _ in range(MAX_ATTEMPTS):
        assert auth.redeem("manager@example.org", "000000", client="1.2.3.4") is None
    # Six digits is only a million values, so the cap has to stop the right code too.
    assert auth.redeem("manager@example.org", code, client="9.9.9.9") is None


def test_guessing_a_code_is_rate_limited_per_client(auth):
    issue(auth)
    for index in range(MAX_ATTEMPTS):
        auth.redeem(f"probe{index}@example.org", "000000", client="1.2.3.4")
    assert auth.redeem("manager@example.org", "000000", client="1.2.3.4") is None


def test_mistyping_a_code_does_not_use_up_the_budget_for_a_new_one(auth):
    issue(auth)
    for _ in range(MAX_ATTEMPTS):
        auth.redeem("manager@example.org", "000000", client="1.2.3.4")
    replacement = auth.issue_login_code("manager@example.org", client="1.2.3.4")
    assert replacement is not None


def test_pending_address_round_trip(auth):
    assert auth.read_pending(auth.create_pending("Manager@Example.org")) == "manager@example.org"
    assert auth.read_pending(None) is None
    assert auth.read_pending(auth.create_pending("manager@example.org")[:-2] + "xy") is None


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
