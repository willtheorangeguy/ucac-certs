"""Worker authentication, lease boundaries, expiry and end-to-end scan publication."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from lss_report.models import StaffMember
from lss_report.scraper import UpstreamError
from lss_report.web.app import create_app
from lss_report.web.auth import Auth, SESSION_COOKIE
from lss_report.web.repository import ScanRepository, StaffRepository
from lss_report.web.worker import WorkerQueue, RemoteSocietyClient
from lss_report.worker import perform_job, validate_base_url

TOKEN = "worker-test-credential-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}
HTML = (Path(__file__).parent / "fixtures/member.html").read_text(encoding="utf-8")


@pytest.fixture
def service(settings, database, monkeypatch):
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    config = replace(settings, society_transport="worker", worker_token=TOKEN)
    app = create_app(config, database)
    with TestClient(app, follow_redirects=False) as client:
        yield app, client, config


def wait_for_claim(client):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        response = client.post("/worker/claim", headers=HEADERS)
        if response.status_code == 200:
            return response.json()
        assert response.status_code == 204
        time.sleep(0.01)
    raise AssertionError("No queued job")


def test_worker_endpoints_require_scoped_token_not_manager_cookie(service, database):
    _, client, settings = service
    assert client.post("/worker/claim").status_code == 401
    client.cookies.set(SESSION_COOKIE, Auth(database, settings).create_session(settings.managers[0]))
    assert client.post("/worker/claim").status_code == 401
    assert client.post("/worker/claim", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/worker/claim", headers=HEADERS).status_code == 204
    client.cookies.clear()
    assert client.get("/staff", headers=HEADERS).status_code == 303
    assert client.post("/scan", headers=HEADERS).status_code == 303
    assert client.get("/export.xlsx", headers=HEADERS).status_code == 303


def test_page_is_identity_checked_on_server_and_removed_after_consumption(service, database):
    app, client, _ = service
    client.post("/worker/claim", headers=HEADERS)
    remote = RemoteSocietyClient(app.state.worker_queue)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(remote.fetch, StaffMember("Example Staff Member", "ABC123"))
        job = wait_for_claim(client)
        assert set(job) == {"id", "member_code", "lease"}
        assert job["member_code"] == "ABC123"
        assert client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                           json={"lease": "wrong" * 10, "html": HTML, "error": None}).status_code == 409
        response = client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                               json={"lease": job["lease"], "html": HTML, "error": None})
        assert response.status_code == 204
        record = future.result(timeout=3)
        assert record.source_name == "Example Staff Member"
        assert record.certifications
        assert database.query("SELECT * FROM worker_job") == []
        assert client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                           json={"lease": job["lease"], "html": HTML, "error": None}).status_code == 409


def test_wrong_member_page_cannot_be_published(service):
    app, client, _ = service
    client.post("/worker/claim", headers=HEADERS)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(RemoteSocietyClient(app.state.worker_queue).fetch,
                             StaffMember("Example Staff Member", "ABC123"))
        job = wait_for_claim(client)
        client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                    json={"lease": job["lease"], "html": HTML.replace("ABC123", "XYZ789"), "error": None})
        with pytest.raises(UpstreamError, match="different Member ID"):
            future.result(timeout=3)


def test_offline_and_disconnected_scanner_fail_without_retaining_pages(database):
    queue = WorkerQueue(database, timeout=0.05)
    with pytest.raises(UpstreamError, match="offline"):
        queue.request("ABC123")
    queue.claim()
    with pytest.raises(UpstreamError, match="did not respond"):
        queue.request("ABC123")
    assert database.query("SELECT * FROM worker_job") == []


def test_claim_is_exclusive_and_expired_or_restarted_jobs_are_rejected(database):
    clock = [100.0]
    queue = WorkerQueue(database, clock=lambda: clock[0])
    with database.write() as conn:
        conn.execute("INSERT INTO worker_job(member_code,status,created_at) VALUES('ABC123','queued',100)")
    job = queue.claim()
    assert queue.claim() is None
    clock[0] += 151
    assert not queue.complete(job["id"], job["lease"], HTML, None)
    queue.reset()
    assert not queue.complete(job["id"], job["lease"], HTML, None)


def test_actual_button_flow_stores_worker_result(service, database):
    app, client, settings = service
    StaffRepository(database).add(name="Example Staff Member", member_code="ABC123")
    client.cookies.set(SESSION_COOKIE, Auth(database, settings).create_session(settings.managers[0]))
    client.post("/worker/claim", headers=HEADERS)
    assert client.get("/scan/status").json()["worker_online"]
    assert client.post("/scan").status_code == 303
    job = wait_for_claim(client)
    assert client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                       json={"lease": job["lease"], "html": HTML, "error": None}).status_code == 204
    app.state.runner._thread.join(timeout=3)
    repo = ScanRepository(database)
    assert repo.latest()["status"] == "complete"
    assert len(database.query("SELECT * FROM scan_result")) == 6
    assert database.query("SELECT * FROM worker_job") == []


def test_worker_failure_keeps_previous_completed_scan(service, database):
    app, client, settings = service
    StaffRepository(database).add(name="Example Staff Member", member_code="ABC123")
    from lss_report.web.scans import run_scan
    from lss_report.scraper import parse_member_page

    class FixtureClient:
        def fetch(self, person):
            return parse_member_page(HTML, person)

    repo = ScanRepository(database)
    run_scan(StaffRepository(database), repo, triggered_by="test", client=FixtureClient())
    previous = repo.latest_complete_id()
    client.cookies.set(SESSION_COOKIE, Auth(database, settings).create_session(settings.managers[0]))
    client.post("/worker/claim", headers=HEADERS)
    client.post("/scan")
    job = wait_for_claim(client)
    client.post(f"/worker/jobs/{job['id']}", headers=HEADERS,
                json={"lease": job["lease"], "html": None, "error": "cloudflare"})
    app.state.runner._thread.join(timeout=3)
    assert repo.latest()["status"] == "failed"
    assert repo.latest_complete_id() == previous


def test_worker_rejects_unsolicited_oversized_and_invalid_results(service):
    _, client, _ = service
    assert client.post("/worker/jobs/999", headers=HEADERS,
                       json={"lease": "x" * 43, "html": HTML, "error": None}).status_code == 409
    assert client.post("/worker/jobs/999", headers=HEADERS,
                       json={"lease": "x" * 43, "html": HTML, "error": "arbitrary"}).status_code == 400
    assert client.post("/worker/jobs/999", headers=HEADERS, content=b"x" * 2_002_049).status_code == 413


@pytest.mark.parametrize("url", ["http://example.com", "https://user:password@example.com", "https://example.com/?secret=x"])
def test_worker_credentials_cannot_be_sent_to_insecure_or_ambiguous_urls(url):
    from lss_report.config import ConfigurationError
    with pytest.raises(ConfigurationError):
        validate_base_url(url)


def test_worker_jobs_cannot_request_arbitrary_urls():
    from lss_report.config import ConfigurationError
    class Client:
        def fetch_html(self, code):
            raise AssertionError("Must not perform a request")
    with pytest.raises(ConfigurationError):
        perform_job(Client(), {"id": 1, "member_code": "https://attacker.example", "lease": "x" * 43})


def test_roster_edit_during_scan_discards_stale_results(database):
    from lss_report.scraper import parse_member_page
    from lss_report.web.scans import run_scan
    staff = StaffRepository(database)
    member = staff.add(name="Example Staff Member", member_code="ABC123")
    scans = ScanRepository(database)

    class EditingClient:
        def fetch(self, person):
            record = parse_member_page(HTML, person)
            staff.update(member.id, actor="test", member_code="XYZ789")
            return record

    assert run_scan(staff, scans, triggered_by="test", client=EditingClient()) is None
    assert scans.latest()["status"] == "failed"
    assert scans.latest_complete_id() is None
    assert database.query("SELECT * FROM scan_result") == []
