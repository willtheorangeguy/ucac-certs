from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..awards import COLUMNS_BY_CODE, expiry_for
from ..config import ConfigurationError, load_staff_file
from ..grid import Grid, MemberRow, status_for
from ..models import CellStatus, StaffMember
from .db import Database

MANUAL_SOURCE = "Manual entry"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Staff:
    id: int
    name: str
    society_name: str | None
    member_code: str
    email: str | None
    phone: str | None
    away: bool
    red_cross_number: str | None = None

    @property
    def display_name(self) -> str:
        return self.society_name or self.name

    def as_member(self) -> StaffMember:
        return StaffMember(
            name=self.name,
            member_code=self.member_code,
            away=self.away,
            red_cross_number=self.red_cross_number,
        )


def _staff(row) -> Staff:
    return Staff(
        id=row["id"],
        name=row["name"],
        society_name=row["society_name"],
        member_code=row["member_code"],
        email=row["email"],
        phone=row["phone"],
        away=bool(row["away"]),
        red_cross_number=row["red_cross_number"],
    )


class DuplicateMemberCode(ValueError):
    pass


class StaffRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def active(self) -> list[Staff]:
        rows = self.db.query(
            "SELECT * FROM staff WHERE removed_at IS NULL ORDER BY away, name COLLATE NOCASE"
        )
        return [_staff(row) for row in rows]

    def get(self, staff_id: int) -> Staff | None:
        row = self.db.query_one("SELECT * FROM staff WHERE id = ?", (staff_id,))
        return _staff(row) if row else None

    def add(
        self,
        *,
        name: str,
        member_code: str,
        society_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        away: bool = False,
        red_cross_number: str | None = None,
        actor: str = "system",
    ) -> Staff:
        member_code = member_code.strip().upper()
        name = " ".join(name.split())
        existing = self.db.query_one(
            "SELECT id FROM staff WHERE member_code = ? AND removed_at IS NULL", (member_code,)
        )
        if existing:
            raise DuplicateMemberCode(f"{member_code} is already on the roster.")
        with self.db.write() as connection:
            cursor = connection.execute(
                "INSERT INTO staff (name, society_name, member_code, email, phone,"
                " red_cross_number, away, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    society_name,
                    member_code,
                    email or None,
                    phone or None,
                    red_cross_number or None,
                    int(away),
                    _now(),
                ),
            )
            staff_id = cursor.lastrowid
            _audit(connection, actor, "staff.add", member_code, name)
        return self.get(staff_id)  # type: ignore[return-value]

    def update(self, staff_id: int, *, actor: str, **fields) -> Staff | None:
        allowed = {
            "name",
            "society_name",
            "member_code",
            "email",
            "phone",
            "away",
            "red_cross_number",
        }
        changes = {key: value for key, value in fields.items() if key in allowed}
        if not changes:
            return self.get(staff_id)
        if "member_code" in changes:
            changes["member_code"] = changes["member_code"].strip().upper()
            clash = self.db.query_one(
                "SELECT id FROM staff WHERE member_code = ? AND removed_at IS NULL AND id != ?",
                (changes["member_code"], staff_id),
            )
            if clash:
                raise DuplicateMemberCode(f"{changes['member_code']} is already on the roster.")
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE staff SET {assignments} WHERE id = ? AND removed_at IS NULL",
                (*changes.values(), staff_id),
            )
            _audit(connection, actor, "staff.update", str(staff_id), json.dumps(changes))
        return self.get(staff_id)

    def remove(self, staff_id: int, *, actor: str) -> None:
        """Soft delete. Historical scan results stay so past reports remain reproducible."""
        with self.db.write() as connection:
            connection.execute(
                "UPDATE staff SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
                (_now(), staff_id),
            )
            _audit(connection, actor, "staff.remove", str(staff_id), None)

    def manual_certs(self, staff_id: int) -> dict[str, str]:
        """Manual certification dates for one staff member, keyed by column code."""
        return {
            row["column_code"]: row["certification_date"]
            for row in self.db.query(
                "SELECT column_code, certification_date FROM manual_cert WHERE staff_id = ?",
                (staff_id,),
            )
        }

    def set_manual_cert(
        self,
        staff_id: int,
        column_code: str,
        certification_date: date | None,
        *,
        actor: str,
    ) -> None:
        """Record, replace, or clear one hand-entered certification date."""
        if column_code not in COLUMNS_BY_CODE:
            raise ValueError(f"{column_code} is not a tracked column.")
        with self.db.write() as connection:
            if certification_date is None:
                cursor = connection.execute(
                    "DELETE FROM manual_cert WHERE staff_id = ? AND column_code = ?",
                    (staff_id, column_code),
                )
                if cursor.rowcount:
                    _audit(connection, actor, "manual_cert.clear", str(staff_id), column_code)
                return
            connection.execute(
                "INSERT INTO manual_cert (staff_id, column_code, certification_date,"
                " created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (staff_id, column_code) DO UPDATE SET"
                " certification_date = excluded.certification_date",
                (staff_id, column_code, certification_date.isoformat(), _now()),
            )
            _audit(
                connection,
                actor,
                "manual_cert.set",
                str(staff_id),
                f"{column_code} {certification_date.isoformat()}",
            )

    def seed_from_file(self, path: Path, *, actor: str = "seed") -> int:
        """One-time import of the legacy staff.json. The database is the roster after this."""
        if self.db.query_one("SELECT id FROM staff LIMIT 1"):
            return 0
        try:
            members = load_staff_file(path)
        except ConfigurationError:
            return 0
        for member in members:
            self.add(
                name=member.name, member_code=member.member_code, away=member.away, actor=actor
            )
        return len(members)


def _outranks(candidate: dict, incumbent: dict) -> bool:
    """The ranking the grid uses within a scan, applied across sources.

    A purpose-issued award beats a provisional one; otherwise the later expiry wins.
    """
    return (
        not candidate["provisional"],
        candidate["expiry_date"] or "",
    ) > (
        not incumbent["provisional"],
        incumbent["expiry_date"] or "",
    )


def effective_cells(database: Database, scan_id: int, as_of: date) -> dict[int, dict[str, dict]]:
    """Stored scan cells with hand-entered certification dates folded in.

    A manual entry is a third source alongside the Society and the Red Cross, not an
    override, so it competes with the scanned award on the same terms and only wins
    where it is better. It applies the moment it is saved, without waiting for the
    next scan, because the fold happens on read.
    """
    by_staff: dict[int, dict[str, dict]] = {}
    for result in database.query("SELECT * FROM scan_result WHERE scan_id = ?", (scan_id,)):
        by_staff.setdefault(result["staff_id"], {})[result["column_code"]] = dict(result)

    for row in database.query("SELECT * FROM manual_cert"):
        column = COLUMNS_BY_CODE.get(row["column_code"])
        if column is None:
            # A column retired since the entry was made. Nothing to show it in.
            continue
        expiry = expiry_for(column, date.fromisoformat(row["certification_date"]))
        candidate = {
            "staff_id": row["staff_id"],
            "column_code": row["column_code"],
            "expiry_date": expiry.isoformat(),
            "status": status_for(expiry, as_of).value,
            "source_award": MANUAL_SOURCE,
            "provisional": 0,
        }
        cells = by_staff.setdefault(row["staff_id"], {})
        incumbent = cells.get(row["column_code"])
        if incumbent is None or _outranks(candidate, incumbent):
            cells[row["column_code"]] = candidate
    return by_staff


def _audit(connection, actor: str, action: str, target: str | None, detail: str | None) -> None:
    connection.execute(
        "INSERT INTO audit (actor, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target, detail, _now()),
    )


class ScanRepository:
    def __init__(self, database: Database) -> None:
        self.db = database

    def start(self, *, triggered_by: str) -> int:
        with self.db.write() as connection:
            cursor = connection.execute(
                "INSERT INTO scan (started_at, status, triggered_by) VALUES (?, 'running', ?)",
                (_now(), triggered_by),
            )
            return cursor.lastrowid

    def abandon_running(self, detail: str = "Interrupted by a restart.") -> int:
        """Close out scans left `running` by a process that died mid-scan.

        A scan lives on a daemon thread, so a deploy or a restart takes it with it and
        the row keeps `status = 'running'` forever. Nothing else reconciles that, and the
        dashboard reads the latest scan for its status line, so it would report a scan
        permanently in progress. No scan can survive a process boundary, so every such
        row at startup is by definition abandoned and needs no timeout heuristic.
        """
        with self.db.write() as connection:
            cursor = connection.execute(
                "UPDATE scan SET finished_at = ?, status = 'failed', detail = ?"
                " WHERE status = 'running'",
                (_now(), detail),
            )
            return cursor.rowcount

    def fail(self, scan_id: int, detail: str) -> None:
        with self.db.write() as connection:
            connection.execute(
                "UPDATE scan SET finished_at = ?, status = 'failed', detail = ? WHERE id = ?",
                (_now(), detail, scan_id),
            )

    def store(self, scan_id: int, grid: Grid, codes_to_ids: dict[str, int]) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM scan_result WHERE scan_id = ?", (scan_id,))
            connection.execute("DELETE FROM scan_note WHERE scan_id = ?", (scan_id,))
            for row in grid.rows:
                staff_id = codes_to_ids.get(row.member_code)
                if staff_id is None:
                    continue
                if row.error:
                    connection.execute(
                        "INSERT INTO scan_note (scan_id, kind, detail) VALUES (?, 'error', ?)",
                        (scan_id, f"{row.name} ({row.member_code}): {row.error}"),
                    )
                if row.name_warning:
                    connection.execute(
                        "INSERT INTO scan_note (scan_id, kind, detail) VALUES (?, 'name', ?)",
                        (scan_id, f"{row.member_code}: {row.name_warning}"),
                    )
                if row.red_cross_warning:
                    connection.execute(
                        "INSERT INTO scan_note (scan_id, kind, detail) VALUES (?, 'redcross', ?)",
                        (scan_id, f"{row.name} ({row.member_code}): {row.red_cross_warning}"),
                    )
                for cell in row.cells:
                    connection.execute(
                        "INSERT INTO scan_result (scan_id, staff_id, column_code, expiry_date,"
                        " status, source_award, provisional) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            scan_id,
                            staff_id,
                            cell.column.code,
                            cell.expiry_date.isoformat() if cell.expiry_date else None,
                            cell.status.value,
                            cell.source_award,
                            int(cell.provisional),
                        ),
                    )
            for title in grid.unmapped_awards:
                connection.execute(
                    "INSERT INTO scan_note (scan_id, kind, detail) VALUES (?, 'unmapped', ?)",
                    (scan_id, title),
                )
            for note in grid.disagreements:
                connection.execute(
                    "INSERT INTO scan_note (scan_id, kind, detail) VALUES (?, 'disagreement', ?)",
                    (scan_id, note),
                )
            connection.execute(
                "UPDATE scan SET finished_at = ?, status = 'complete' WHERE id = ?",
                (_now(), scan_id),
            )

    def latest(self) -> dict | None:
        row = self.db.query_one("SELECT * FROM scan ORDER BY id DESC LIMIT 1")
        return dict(row) if row else None

    def latest_complete_id(self) -> int | None:
        row = self.db.query_one(
            "SELECT id FROM scan WHERE status = 'complete' ORDER BY id DESC LIMIT 1"
        )
        return row["id"] if row else None

    def notes(self, scan_id: int) -> list[dict]:
        return [dict(row) for row in self.db.query(
            "SELECT kind, detail FROM scan_note WHERE scan_id = ? ORDER BY kind, detail", (scan_id,)
        )]

    def _reminder_rows(self, scan_id: int, as_of: date) -> list[dict]:
        """Every dated cell of a scan, manual entries folded in, with its staff member.

        Reminders read the same effective value the dashboard and the exports show,
        so a hand-entered date moves the reminder schedule with it.
        """
        staff_by_id = {
            row["id"]: row
            for row in self.db.query("SELECT * FROM staff WHERE removed_at IS NULL")
        }
        rows = []
        for staff_id, cells in effective_cells(self.db, scan_id, as_of).items():
            staff = staff_by_id.get(staff_id)
            if staff is None:
                continue
            for cell in cells.values():
                if not cell["expiry_date"]:
                    continue
                rows.append(
                    {
                        "staff_id": staff_id,
                        "column_code": cell["column_code"],
                        "expiry_date": cell["expiry_date"],
                        "status": cell["status"],
                        "name": staff["name"],
                        "society_name": staff["society_name"],
                        "email": staff["email"],
                    }
                )
        return rows

    def upcoming(
        self, scan_id: int, thresholds: tuple[int, ...], as_of: date, horizon_days: int = 60
    ) -> list[dict]:
        """Every reminder scheduled to fire between today and the horizon.

        A reminder fires on expiry minus each threshold, so one certification can
        appear up to three times on different dates.
        """
        rows = self._reminder_rows(scan_id, as_of)
        already = {
            (row["staff_id"], row["column_code"], row["expiry_date"], row["threshold"])
            for row in self.db.query(
                "SELECT staff_id, column_code, expiry_date, threshold FROM notification_log"
            )
        }
        upcoming = []
        for row in rows:
            expiry = date.fromisoformat(row["expiry_date"])
            for threshold in thresholds:
                when = expiry - timedelta(days=threshold)
                if not (as_of <= when <= as_of + timedelta(days=horizon_days)):
                    continue
                entry = dict(row)
                entry["threshold"] = threshold
                entry["send_on"] = when.isoformat()
                entry["days_until_send"] = (when - as_of).days
                entry["already_sent"] = (
                    row["staff_id"], row["column_code"], row["expiry_date"], threshold
                ) in already
                upcoming.append(entry)
        upcoming.sort(key=lambda item: (item["send_on"], item["name"].casefold()))
        return upcoming

    def history(self, limit: int = 200) -> list[dict]:
        return [
            dict(row)
            for row in self.db.query(
                "SELECT n.sent_at, n.column_code, n.expiry_date, n.threshold, n.channel,"
                " s.name, s.society_name, s.email FROM notification_log n"
                " JOIN staff s ON s.id = n.staff_id ORDER BY n.sent_at DESC LIMIT ?",
                (limit,),
            )
        ]

    def due(self, scan_id: int, thresholds: tuple[int, ...], as_of: date) -> list[dict]:
        """Cells whose expiry lands exactly on a reminder step."""
        wanted = {(as_of.toordinal() + days): days for days in thresholds}
        rows = self._reminder_rows(scan_id, as_of)
        due = []
        for row in rows:
            expiry = date.fromisoformat(row["expiry_date"])
            threshold = wanted.get(expiry.toordinal())
            if threshold is None:
                continue
            entry = dict(row)
            entry["threshold"] = threshold
            due.append(entry)
        return due


def rows_from_scan(database: Database, scan_id: int, as_of: date | None = None) -> list[dict]:
    """Grid rows for rendering, out of stored results with manual entries folded in."""
    staff_rows = database.query(
        "SELECT * FROM staff WHERE removed_at IS NULL ORDER BY away, name COLLATE NOCASE"
    )
    by_staff = effective_cells(database, scan_id, as_of or date.today())
    errors = {
        note["detail"].split(" (")[0]: note["detail"]
        for note in database.query(
            "SELECT detail FROM scan_note WHERE scan_id = ? AND kind = 'error'", (scan_id,)
        )
    }
    return [
        {
            "staff": _staff(row),
            "cells": by_staff.get(row["id"], {}),
            "error": errors.get(row["society_name"] or row["name"]),
        }
        for row in staff_rows
    ]


def grid_rows_to_member_rows(rows: list[dict], columns) -> list[MemberRow]:
    from ..grid import GridCell

    member_rows = []
    for entry in rows:
        staff = entry["staff"]
        cells = []
        for column in columns:
            stored = entry["cells"].get(column.code)
            cells.append(
                GridCell(
                    column=column,
                    expiry_date=date.fromisoformat(stored["expiry_date"])
                    if stored and stored["expiry_date"]
                    else None,
                    status=CellStatus(stored["status"]) if stored else CellStatus.MISSING,
                    source_award=stored["source_award"] if stored else None,
                    provisional=bool(stored["provisional"]) if stored else False,
                )
            )
        member_rows.append(
            MemberRow(
                name=staff.display_name,
                member_code=staff.member_code,
                cells=tuple(cells),
                away=staff.away,
                error=entry.get("error"),
            )
        )
    return member_rows
