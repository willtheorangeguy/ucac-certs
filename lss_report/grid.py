from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .awards import COLUMNS, CPR_C, FIRST_AID, CertColumn, columns_for
from .models import CellStatus, Certification, MemberRecord, ReportData

EXPIRY_WARNING_DAYS = 30

# The Society keeps first aid and CPR awards valid longer than the Aquatic Centre
# accepts them, so those two columns are expected to disagree and are not compared.
_CROSS_CHECKED = tuple(column for column in COLUMNS if column not in (FIRST_AID, CPR_C))


def status_for(expiry_date: date | None, as_of: date) -> CellStatus:
    if expiry_date is None:
        return CellStatus.MISSING
    if expiry_date < as_of:
        return CellStatus.EXPIRED
    if (expiry_date - as_of).days <= EXPIRY_WARNING_DAYS:
        return CellStatus.EXPIRING
    return CellStatus.CURRENT


@dataclass(frozen=True)
class GridCell:
    column: CertColumn
    expiry_date: date | None
    status: CellStatus
    source_award: str | None = None
    provisional: bool = False


@dataclass(frozen=True)
class MemberRow:
    name: str
    member_code: str
    cells: tuple[GridCell, ...]
    away: bool = False
    error: str | None = None
    name_warning: str | None = None
    red_cross_warning: str | None = None


@dataclass(frozen=True)
class Grid:
    as_of: date
    generated_at: datetime
    rows: tuple[MemberRow, ...]
    unmapped_awards: tuple[str, ...] = ()
    disagreements: tuple[str, ...] = ()

    def rows_with_status(self, status: CellStatus) -> list[tuple[MemberRow, GridCell]]:
        return [
            (row, cell) for row in self.rows for cell in row.cells if cell.status is status
        ]


def _best(certifications: list[Certification], column: CertColumn) -> Certification | None:
    candidates = [
        cert
        for cert in certifications
        if cert.column is column and cert.expiry_date is not None
    ]
    if not candidates:
        return None
    # A confirmed award beats a provisional one; otherwise the latest expiry wins.
    return max(candidates, key=lambda cert: (not cert.provisional, cert.expiry_date))


def _row(record: MemberRecord, as_of: date) -> MemberRow:
    cells = []
    for column in COLUMNS:
        cert = None if record.error else _best(record.certifications, column)
        cells.append(
            GridCell(
                column=column,
                expiry_date=cert.expiry_date if cert else None,
                status=status_for(cert.expiry_date if cert else None, as_of),
                source_award=cert.name if cert else None,
                provisional=bool(cert and cert.provisional),
            )
        )
    return MemberRow(
        name=record.display_name,
        member_code=record.member_code,
        cells=tuple(cells),
        away=record.away,
        error=record.error,
        name_warning=record.name_warning,
        red_cross_warning=record.red_cross_warning,
    )


def build_grid(report: ReportData) -> Grid:
    as_of = report.as_of
    unmapped: list[str] = []
    disagreements: list[str] = []

    for record in report.records:
        for cert in record.certifications:
            if cert.column is None and columns_for(cert.name) is None:
                if cert.name not in unmapped:
                    unmapped.append(cert.name)
                continue
            if cert.column not in _CROSS_CHECKED or cert.provisional:
                continue
            computed_current = cert.expiry_date is not None and cert.expiry_date >= as_of
            if computed_current != cert.site_current:
                disagreements.append(
                    f"{record.member_code} {cert.name}: Society says "
                    f"{'current' if cert.site_current else 'expired'}, "
                    f"computed expiry {cert.expiry_date}."
                )

    rows = sorted(
        (_row(record, as_of) for record in report.records),
        key=lambda row: (row.away, row.name.casefold()),
    )
    return Grid(
        as_of=as_of,
        generated_at=report.generated_at,
        rows=tuple(rows),
        unmapped_awards=tuple(sorted(unmapped)),
        disagreements=tuple(disagreements),
    )
