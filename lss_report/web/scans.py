from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..awards import CertColumn
from ..grid import Grid, build_grid
from ..models import MemberRecord, ReportData, StaffMember
from ..redcross import RedCrossClient, certifications_from, last_name_for
from ..scraper import SocietyClient, UpstreamError
from .repository import ScanRepository, Staff, StaffRepository

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


def verify_red_cross_number(
    certificate_number: str,
    name: str,
    *,
    expected_column: CertColumn | None = None,
    client: RedCrossClient | None = None,
) -> Verification:
    """Check a certificate number against the Red Cross validator at entry time.

    The validator keys off the number and the holder's last name together, so a
    number that belongs to someone else fails here rather than silently returning
    nothing at the next scan.
    """
    validator = client or RedCrossClient()
    try:
        certificate = validator.fetch(last_name_for(name), certificate_number)
    except UpstreamError as exc:
        return Verification(ok=False, error=str(exc))
    if certificate is None:
        return Verification(
            ok=False,
            error="No Red Cross certificate matches that number and last name.",
        )
    if expected_column and not any(
        certification.column is expected_column and not certification.provisional
        for certification in certifications_from(certificate)
    ):
        return Verification(
            ok=False,
            error=f"That certificate does not include {expected_column.label}.",
        )
    return Verification(ok=True)


def _add_red_cross(record: MemberRecord, member: Staff, client: RedCrossClient) -> None:
    """Fold a member's Red Cross certificate into their Society record.

    A failed lookup is a warning, not an error: the Society awards are unaffected and
    the first aid cell simply falls back to whatever the Society has.
    """
    numbers = [
        ("Standard First Aid", member.red_cross_fa_number),
        ("CPR-C", member.red_cross_cpr_number),
    ]
    numbers = [(label, number) for label, number in numbers if number]
    if not numbers:
        return
    warnings = []
    seen = set()
    for label, number in numbers:
        if number in seen:
            continue
        seen.add(number)
        try:
            certificate = client.fetch(last_name_for(member.display_name), number)
        except UpstreamError as exc:
            warnings.append(f"{label} certificate {number}: {exc}")
            continue
        if certificate is None:
            warnings.append(f"{label} certificate {number} did not validate against the Red Cross.")
            continue
        record.certifications.extend(certifications_from(certificate))
    record.red_cross_warning = "; ".join(warnings) or None


def _fetch_society_profiles(member: Staff, client: SocietyClient) -> MemberRecord:
    """Read every Society profile for one person and combine their awards."""
    profiles = [
        client.fetch(
            StaffMember(name=member.name, member_code=code, away=member.away)
        )
        for code in member.member_codes
    ]
    usable = [profile for profile in profiles if not profile.error]
    if not usable:
        errors = "; ".join(
            f"{profile.member_code}: {profile.error}" for profile in profiles
        )
        return MemberRecord(
            configured_name=member.name,
            member_code=member.member_code,
            error=errors,
            away=member.away,
        )

    name_warnings = [
        f"{profile.member_code}: {profile.name_warning}"
        for profile in usable
        if profile.name_warning
    ]
    lookup_warnings = [
        f"{profile.member_code}: {profile.error}"
        for profile in profiles
        if profile.error
    ]
    return MemberRecord(
        configured_name=member.name,
        # Keep the primary code as the stable key used to store this staff row.
        member_code=member.member_code,
        source_name=usable[0].source_name,
        certifications=[cert for profile in usable for cert in profile.certifications],
        name_warning="; ".join(name_warnings) or None,
        lookup_warning="; ".join(lookup_warnings) or None,
        away=member.away,
    )


def run_scan(
    staff_repo: StaffRepository,
    scan_repo: ScanRepository,
    *,
    triggered_by: str,
    client: SocietyClient | None = None,
    red_cross: RedCrossClient | None = None,
) -> Grid | None:
    roster = staff_repo.active()
    scan_id = scan_repo.start(triggered_by=triggered_by)
    if not roster:
        scan_repo.fail(scan_id, "The roster is empty. Add staff before running a scan.")
        return None

    society = client or SocietyClient()
    validator = red_cross or RedCrossClient()
    try:
        records = []
        for member in roster:
            record = _fetch_society_profiles(member, society)
            if not record.error:
                _add_red_cross(record, member, validator)
            records.append(record)
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
