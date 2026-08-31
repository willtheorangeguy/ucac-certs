from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import ConfigurationError, load_staff_file
from ..grid import Grid, MemberRow
from ..models import CellStatus, StaffMember
from .db import Database


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
    sms_consent_at: str | None

    @property
    def display_name(self) -> str:
        return self.society_name or self.name

    def as_member(self) -> StaffMember:
        return StaffMember(name=self.name, member_code=self.member_code, away=self.away)


def _staff(row) -> Staff:
    return Staff(
        id=row["id"],
        name=row["name"],
        society_name=row["society_name"],
        member_code=row["member_code"],
        email=row["email"],
        phone=row["phone"],
        away=bool(row["away"]),
        sms_consent_at=row["sms_consent_at"],
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
                "INSERT INTO staff (name, society_name, member_code, email, phone, away, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, society_name, member_code, email or None, phone or None, int(away), _now()),
            )
            staff_id = cursor.lastrowid
            _audit(connection, actor, "staff.add", member_code, name)
        return self.get(staff_id)  # type: ignore[return-value]

    def update(self, staff_id: int, *, actor: str, **fields) -> Staff | None:
        allowed = {"name", "society_name", "email", "phone", "away", "sms_consent_at"}
        changes = {key: value for key, value in fields.items() if key in allowed}
        if not changes:
            return self.get(staff_id)
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

    def due(self, scan_id: int, thresholds: tuple[int, ...], as_of: date) -> list[dict]:
        """Cells whose expiry lands exactly on a reminder step."""
        wanted = {(as_of.toordinal() + days): days for days in thresholds}
        rows = self.db.query(
            "SELECT r.staff_id, r.column_code, r.expiry_date, s.name, s.society_name, s.email,"
            " s.phone, s.sms_consent_at FROM scan_result r JOIN staff s ON s.id = r.staff_id"
            " WHERE r.scan_id = ? AND r.expiry_date IS NOT NULL AND s.removed_at IS NULL",
            (scan_id,),
        )
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


def rows_from_scan(database: Database, scan_id: int) -> list[dict]:
    """Grid rows for rendering, straight out of stored results."""
    staff_rows = database.query(
        "SELECT * FROM staff WHERE removed_at IS NULL ORDER BY away, name COLLATE NOCASE"
    )
    results = database.query(
        "SELECT * FROM scan_result WHERE scan_id = ?", (scan_id,)
    )
    by_staff: dict[int, dict[str, dict]] = {}
    for result in results:
        by_staff.setdefault(result["staff_id"], {})[result["column_code"]] = dict(result)
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
