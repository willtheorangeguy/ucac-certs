from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..grid import Grid, build_grid
from ..models import ReportData
from ..scraper import SocietyClient, UpstreamError
from .repository import ScanRepository, StaffRepository

logger = logging.getLogger(__name__)
TIMEZONE = ZoneInfo("America/Edmonton")


@dataclass(frozen=True)
class Verification:
    ok: bool
    society_name: str | None = None
    error: str | None = None


def verify_member_code(member_code: str, name: str, *, client: SocietyClient | None = None) -> Verification:
    """Check an LS# against the Society at entry time, so a typo is caught immediately."""
    from ..models import StaffMember

    society = client or SocietyClient()
    try:
        record = society.fetch(StaffMember(name=name or member_code, member_code=member_code.upper()))
    except UpstreamError as exc:
        return Verification(ok=False, error=str(exc))
    if record.error:
        return Verification(ok=False, error=record.error)
    return Verification(ok=True, society_name=record.source_name)


def run_scan(
    staff_repo: StaffRepository,
    scan_repo: ScanRepository,
    *,
    triggered_by: str,
    client: SocietyClient | None = None,
) -> Grid | None:
    roster = staff_repo.active()
    scan_id = scan_repo.start(triggered_by=triggered_by)
    if not roster:
        scan_repo.fail(scan_id, "The roster is empty. Add staff before running a scan.")
        return None

    society = client or SocietyClient()
    try:
        records = [society.fetch(member.as_member()) for member in roster]
    except UpstreamError as exc:
        scan_repo.fail(scan_id, str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - the scan must not take the app down
        logger.exception("Scan failed")
        scan_repo.fail(scan_id, f"{type(exc).__name__}: {exc}")
        return None

    grid = build_grid(ReportData(generated_at=datetime.now(TIMEZONE), records=records))
    scan_repo.store(scan_id, grid, {member.member_code: member.id for member in roster})

    # Record the Society's spelling so the roster screen can offer to adopt it.
    for member, record in zip(roster, records):
        if record.source_name and record.source_name != member.society_name:
            staff_repo.update(member.id, actor="scan", society_name=record.source_name)
    return grid


class ScanRunner:
    """Runs one scan at a time on a worker thread; the request returns immediately."""

    def __init__(self, staff_repo: StaffRepository, scan_repo: ScanRepository) -> None:
        self.staff_repo = staff_repo
        self.scan_repo = scan_repo
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, triggered_by: str) -> bool:
        with self._lock:
            if self.running:
                return False
            self._thread = threading.Thread(
                target=run_scan,
                args=(self.staff_repo, self.scan_repo),
                kwargs={"triggered_by": triggered_by},
                daemon=True,
                name="lss-scan",
            )
            self._thread.start()
            return True
