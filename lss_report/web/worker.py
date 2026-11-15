"""Authenticated, temporary page requests for a scanner behind a home firewall."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from ..models import MemberRecord
from ..scraper import UpstreamError, parse_member_page

JOB_SECONDS = 150
ONLINE_SECONDS = 30
MAX_HTML_BYTES = 2_000_000
ERRORS = {
    "cloudflare": "The local scanner encountered Cloudflare verification. Check Firefox on your computer.",
    "browser": "The local scanner could not complete the Society lookup. Check its connection and Firefox installation.",
}


class WorkerQueue:
    def __init__(self, database, *, clock=time.time, timeout=JOB_SECONDS):
        self.db = database
        self.clock = clock
        self.timeout = timeout
        self.changed = threading.Event()

    def reset(self):
        with self.db.write() as conn:
            conn.execute("DELETE FROM worker_job")
            conn.execute("DELETE FROM worker_presence")
        self.changed.set()

    def online(self):
        now = self.clock()
        row = self.db.query_one("SELECT seen_at FROM worker_presence WHERE id=1")
        busy = self.db.query_one(
            "SELECT id FROM worker_job WHERE status='claimed' AND created_at > ? LIMIT 1",
            (now - self.timeout,),
        )
        return bool((row and row["seen_at"] > now - ONLINE_SECONDS) or busy)

    def claim(self):
        now = self.clock()
        lease = secrets.token_urlsafe(32)
        with self.db.write() as conn:
            conn.execute("DELETE FROM worker_job WHERE created_at < ?", (now - self.timeout,))
            conn.execute("INSERT INTO worker_presence(id,seen_at) VALUES(1,?) "
                         "ON CONFLICT(id) DO UPDATE SET seen_at=excluded.seen_at", (now,))
            row = conn.execute("SELECT id,member_code FROM worker_job WHERE status='queued' "
                               "ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE worker_job SET status='claimed',lease_hash=? WHERE id=?",
                         (hashlib.sha256(lease.encode()).hexdigest(), row["id"]))
        return {"id": row["id"], "member_code": row["member_code"], "lease": lease}

    def complete(self, job_id, lease, html, error):
        with self.db.write() as conn:
            row = conn.execute("SELECT status,lease_hash,created_at FROM worker_job WHERE id=?", (job_id,)).fetchone()
            if (row is None or row["status"] != "claimed" or row["created_at"] <= self.clock() - self.timeout
                    or not hmac.compare_digest(row["lease_hash"], hashlib.sha256(lease.encode()).hexdigest())):
                return False
            conn.execute("UPDATE worker_job SET status='complete',html=?,error=?,completed_at=? WHERE id=?",
                         (html, error, self.clock(), job_id))
        self.changed.set()
        return True

    def request(self, member_code):
        if not member_code.isalnum() or len(member_code) > 64:
            raise UpstreamError("Member ID must be letters and digits only.")
        if not self.online():
            raise UpstreamError("Local scanner is offline. Start it on your computer and try again.")
        with self.db.write() as conn:
            conn.execute("DELETE FROM worker_job WHERE created_at < ?", (self.clock() - self.timeout,))
            if conn.execute("SELECT COUNT(*) FROM worker_job").fetchone()[0] >= 10:
                raise UpstreamError("The local scanner is busy. Try again shortly.")
            job_id = conn.execute("INSERT INTO worker_job(member_code,status,created_at) VALUES(?,'queued',?)",
                                  (member_code, self.clock())).lastrowid
        deadline = time.monotonic() + self.timeout
        try:
            while time.monotonic() < deadline:
                self.changed.clear()
                row = self.db.query_one("SELECT status,html,error FROM worker_job WHERE id=?", (job_id,))
                if row is None:
                    break
                if row["status"] == "complete":
                    if row["error"]:
                        raise UpstreamError(ERRORS[row["error"]])
                    return row["html"]
                self.changed.wait(min(0.25, max(0, deadline - time.monotonic())))
            raise UpstreamError("Local scanner did not respond in time. Check that your computer is awake and online.")
        finally:
            with self.db.write() as conn:
                conn.execute("DELETE FROM worker_job WHERE id=?", (job_id,))


class RemoteSocietyClient:
    def __init__(self, queue):
        self.queue = queue

    def fetch(self, staff):
        html = self.queue.request(staff.member_code)
        if html is None:
            return MemberRecord(configured_name=staff.name, member_code=staff.member_code,
                                away=staff.away, error="Member ID was not found.")
        # The server validates member identity and calculates dates itself.
        return parse_member_page(html, staff)
