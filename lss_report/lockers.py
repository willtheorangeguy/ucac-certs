from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .grid import status_for
from .models import CellStatus

LOCKER_HEADINGS = ("Name", "Locker Number", "Fit Test Expiry", "Cartridge Expiry", "Boot Size")


@dataclass(frozen=True)
class LockerExpiry:
    expiry_date: date | None
    status: CellStatus

    @classmethod
    def for_date(cls, expiry: date | None, as_of: date) -> LockerExpiry:
        return cls(expiry, status_for(expiry, as_of))


@dataclass(frozen=True)
class LockerRow:
    name: str
    locker_number: str | None
    fit_test: LockerExpiry
    cartridge: LockerExpiry
    boot_size: str | None
    away: bool = False


@dataclass(frozen=True)
class LockerReport:
    generated_at: datetime
    rows: tuple[LockerRow, ...]

    @property
    def as_of(self) -> date:
        return self.generated_at.date()
