"""Real Firefox tests with intercepted fixture responses; no third-party traffic."""
from pathlib import Path

import pytest
from playwright.sync_api import Error as BrowserError

from lss_report.browser import FirefoxSocietyClient, _BROWSER_SLOT
from lss_report.models import StaffMember
from lss_report.scraper import UpstreamError, society_client
from lss_report.web.repository import ScanRepository, StaffRepository
from lss_report.web.scans import run_scan, verify_member_code

HTML = (Path(__file__).parent / "fixtures/member.html").read_text(encoding="utf-8")
PERSON = StaffMember("Example Staff Member", "ABC123")


@pytest.fixture
def browser_client():
    with FirefoxSocietyClient(delay_seconds=0, timeout_seconds=2,
                              challenge_seconds=0, retries=0) as client:
        # Every URL is intercepted, including unexpected redirects and scripts.
        client._page.route("**/*", lambda route: route.fulfill(status=200, body=HTML,
                                                              content_type="text/html"))
        yield client


def test_firefox_reuses_one_page_and_session_for_multiple_staff(browser_client):
    page = browser_client._page
    first = browser_client.fetch(PERSON)
    second = browser_client.fetch(PERSON)
    assert first.source_name == "Example Staff Member"
    assert first.certifications == second.certifications
    assert first.certifications
    assert browser_client._page is page
    assert len(page.context.pages) == 1


def test_firefox_recognizes_invalid_id_at_directory(browser_client, monkeypatch):
    # Route interception is not re-run for an HTTP redirect chain. Simulate its
    # final navigation directly so this fixture cannot contact the real directory.
    goto = browser_client._page.goto
    monkeypatch.setattr(browser_client._page, "goto", lambda url, **kw: goto(
        "https://www.lifesaving.org/member-services/find-a-member", **kw))
    browser_client._page.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<body>Invalid Member Id.</body>"))
    record = browser_client.fetch(PERSON)
    assert record.error == "Member ID was not found."
    assert not record.certifications


def test_firefox_waits_for_browser_challenge_navigation(browser_client):
    calls = []

    def respond(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(status=403, headers={"cf-mitigated": "challenge"}, content_type="text/html",
                          body="<title>Just a moment...</title><script>setTimeout(()=>location.reload(),100)</script>")
        else:
            route.fulfill(status=200, content_type="text/html", body=HTML)

    browser_client._page.route("**/*", respond)
    browser_client.challenge_seconds = 2
    assert browser_client.fetch(PERSON).certifications
    assert len(calls) == 2


def test_firefox_rejects_an_unexpected_redirect(browser_client, monkeypatch):
    goto = browser_client._page.goto
    monkeypatch.setattr(browser_client._page, "goto", lambda url, **kw: goto(
        "https://unexpected.invalid/", **kw))
    with pytest.raises(UpstreamError, match="redirected unexpectedly"):
        browser_client.fetch(PERSON)


@pytest.mark.parametrize("status", [200, 403, 503])
def test_firefox_reports_challenge_with_ray_without_retry(browser_client, status):
    calls = []

    def respond(route):
        calls.append(route.request.url)
        route.fulfill(status=status, headers={"cf-mitigated": "challenge", "cf-ray": "fixture-YYZ"},
                      content_type="text/html", body="<title>Just a moment...</title>")

    browser_client._page.route("**/*", respond)
    browser_client.retries = 2
    with pytest.raises(UpstreamError, match="Cloudflare.*Firefox.*fixture-YYZ"):
        browser_client.fetch(PERSON)
    assert len(calls) == 1


def test_firefox_retries_rate_limit_then_parses_success(browser_client):
    calls = []
    waits = []

    def respond(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(status=429, headers={"Retry-After": "2"}, body="Try later")
        else:
            route.fulfill(status=200, content_type="text/html", body=HTML)

    browser_client._page.route("**/*", respond)
    browser_client.retries = 1
    browser_client.sleep = waits.append
    assert browser_client.fetch(PERSON).certifications
    assert len(calls) == 2
    assert waits == [2.0]


def test_browser_timeout_never_exposes_member_url(browser_client, monkeypatch):
    def fail(*args, **kwargs):
        raise BrowserError("timeout loading https://example.org?member=PRIVATE123")

    monkeypatch.setattr(browser_client._page, "goto", fail)
    with pytest.raises(UpstreamError, match="Unable to complete") as error:
        browser_client.fetch(PERSON)
    assert "PRIVATE123" not in str(error.value)
    assert error.value.__suppress_context__


def test_browser_closes_and_releases_capacity_after_parse_failure():
    from lss_report.scraper import ParseError

    client = FirefoxSocietyClient(delay_seconds=0)
    with pytest.raises(ParseError):
        with client:
            page = client._page
            page.route("**/*", lambda route: route.fulfill(status=200, body="<html></html>"))
            client.fetch(PERSON)
    assert page.is_closed()
    assert _BROWSER_SLOT.acquire(blocking=False)
    _BROWSER_SLOT.release()


def test_browser_start_failure_is_clean_and_releases_capacity(monkeypatch):
    def fail():
        raise BrowserError("Executable does not exist")

    monkeypatch.setattr("lss_report.browser.sync_playwright", fail)
    with pytest.raises(UpstreamError, match="Unable to start Firefox"):
        with FirefoxSocietyClient():
            pytest.fail("startup should fail")
    assert _BROWSER_SLOT.acquire(blocking=False)
    _BROWSER_SLOT.release()


def test_browser_capacity_is_bounded():
    _BROWSER_SLOT.acquire()
    try:
        with pytest.raises(UpstreamError, match="already running"):
            with FirefoxSocietyClient():
                pytest.fail("second browser must not launch")
    finally:
        _BROWSER_SLOT.release()


def test_default_factory_owns_firefox_and_closes_it(monkeypatch):
    monkeypatch.delenv("SOCIETY_TRANSPORT", raising=False)
    with society_client() as client:
        assert isinstance(client, FirefoxSocietyClient)
        page = client._page
    assert page.is_closed()


def test_injected_client_is_not_closed_by_factory():
    client = object()
    with society_client(client) as owned:
        assert owned is client


def test_http_transport_is_explicit(monkeypatch):
    from lss_report.scraper import SocietyClient

    monkeypatch.setenv("SOCIETY_TRANSPORT", "http")
    with society_client() as client:
        assert isinstance(client, SocietyClient)


def test_browser_failure_marks_scan_failed_without_replacing_previous_results(database, monkeypatch):
    staff = StaffRepository(database)
    scans = ScanRepository(database)
    staff.add(name=PERSON.name, member_code=PERSON.member_code)

    class FixtureClient:
        def fetch(self, person):
            from lss_report.scraper import parse_member_page
            return parse_member_page(HTML, person)

    run_scan(staff, scans, triggered_by="test", client=FixtureClient())
    previous = scans.latest_complete_id()

    def fail():
        raise BrowserError("missing Firefox")

    monkeypatch.setenv("SOCIETY_TRANSPORT", "firefox")
    monkeypatch.setattr("lss_report.browser.sync_playwright", fail)
    assert run_scan(staff, scans, triggered_by="test") is None
    assert scans.latest()["status"] == "failed"
    assert scans.latest_complete_id() == previous
    assert "Unable to start Firefox" in scans.latest()["detail"]
    assert not verify_member_code(PERSON.member_code, PERSON.name).ok
