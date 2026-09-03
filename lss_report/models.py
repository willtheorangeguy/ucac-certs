from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from .awards import CertColumn


@dataclass(frozen=True)
class StaffMember:
    name: str
    member_code: str
    away: bool = False
    # Red Cross certificates are validated by number rather than by member, so the
    # number is held per staff member alongside the Society Member ID.
    red_cross_number: str | None = None


class CellStatus(str, Enum):
    CURRENT = "current"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    MISSING = "missing"


@dataclass(frozen=True)
class Certification:
    name: str
    certification_date: date
    column: CertColumn | None = None
    expiry_date: date | None = None
    site_current: bool = True
    # True when the award counts towards this column only as a side effect, e.g. an
    # FA row derived from "Lifesaving CPR C & AED". A Red Cross record should win.
    provisional: bool = False


@dataclass
class MemberRecord:
    configured_name: str
    member_code: str
    source_name: str | None = None
    certifications: list[Certification] = field(default_factory=list)
    error: str | None = None
    name_warning: str | None = None
    away: bool = False
    # A Red Cross lookup that failed. It is a warning rather than an error: the
    # Society awards still stand, only the Red Cross first aid record is missing.
    red_cross_warning: str | None = None

    @property
    def display_name(self) -> str:
        return self.source_name or self.configured_name

    def tracked_certifications(self) -> list[Certification]:
        return [cert for cert in self.certifications if cert.column is not None]


@dataclass(frozen=True)
class ReportData:
    generated_at: datetime
    records: list[MemberRecord]
    unmapped_awards: tuple[str, ...] = ()

    @property
    def as_of(self) -> date:
        return self.generated_at.date()

    @property
    def error_count(self) -> int:
        return sum(record.error is not None for record in self.records)
