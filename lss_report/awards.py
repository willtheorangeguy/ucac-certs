from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CertColumn:
    code: str
    label: str
    validity_years: int


NATIONAL_LIFEGUARD = CertColumn("NL", "National Lifeguard", 2)
SWIM_INSTRUCTOR = CertColumn("SI", "Swim Instructor", 2)
LIFESAVING_INSTRUCTOR = CertColumn("LSI", "Lifesaving Instructor", 2)
# The certificate itself runs three years; the Aquatic Centre only honours two.
FIRST_AID = CertColumn("FA", "First Aid", 2)
CPR_C = CertColumn("CPR-C", "CPR Level C", 1)
OXYGEN = CertColumn("O2", "O2 Administration", 2)

COLUMNS: tuple[CertColumn, ...] = (
    NATIONAL_LIFEGUARD,
    SWIM_INSTRUCTOR,
    LIFESAVING_INSTRUCTOR,
    FIRST_AID,
    CPR_C,
    OXYGEN,
)

# Ordered; the first pattern that matches wins. Qualifications that merely mention a
# tracked award are listed first and map to nothing, so that "Lifesaving CPR
# Instructor/Examiner" is not read as a CPR-C award nor "2023 National Lifeguard
# Update" as a National Lifeguard certification.
#
# The bool beside each column marks a provisional mapping: the award counts towards
# that column only as a side effect, so a purpose-issued award should outrank it.
_RULES: tuple[tuple[str, tuple[tuple[CertColumn, bool], ...]], ...] = (
    (r"\b(update|clinic|conference|proficiency|activation|auditor|official)\b", ()),
    (r"^national lifeguard instructor", ()),
    (r"^lifesaving cpr (instructor|examiner)", ()),
    (r"^swim instructor\b", ((SWIM_INSTRUCTOR, False),)),
    (r"^lifesaving instructor\b", ((LIFESAVING_INSTRUCTOR, False),)),
    (r"\b(instructor|examiner|trainer|coach|advocate|attendant)\b", ()),
    (r"^(bronze|rebronze|junior lifeguard|registered)\b", ()),
    (r"\bnational lifeguard\b", ((NATIONAL_LIFEGUARD, False),)),
    # Employer-run recerts are titled with the abbreviation, e.g.
    # "City of Calgary Staff NL Recert".
    (r"\bnl recert(ification)?\b", ((NATIONAL_LIFEGUARD, False),)),
    (r"\bo2 administration\b", ((OXYGEN, False),)),
    # A combined first aid and CPR award certifies both outright.
    (r"\bfirst aid\b.*\bcpr\s*-?\s*c\b", ((FIRST_AID, False), (CPR_C, False))),
    # A CPR-only award is accepted for first aid by Aquatic Centre policy, but a Red
    # Cross first aid record should replace it once that source exists.
    (r"\bcpr\s*-?\s*c\b", ((CPR_C, False), (FIRST_AID, True))),
    (r"\bfirst aid\b", ((FIRST_AID, False),)),
)

_COMPILED = tuple((re.compile(pattern), columns) for pattern, columns in _RULES)


def normalize_award(title: str) -> str:
    return " ".join(title.casefold().split())


def columns_for(title: str) -> tuple[tuple[CertColumn, bool], ...] | None:
    """(column, provisional) pairs an award counts towards.

    An empty tuple means the award was recognised but is not tracked on the grid.
    ``None`` means the title matched no rule at all and belongs in diagnostics.
    """
    text = normalize_award(title)
    for pattern, columns in _COMPILED:
        if pattern.search(text):
            return columns
    return None


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Only 29 February can fail to exist in the target year.
        return value.replace(year=value.year + years, month=2, day=28)


def expiry_for(column: CertColumn, certification_date: date) -> date:
    return add_years(certification_date, column.validity_years)


def certification_date_from_expiry(column: CertColumn, expiry_date: date) -> date:
    """Recover a certification date from a published "Expired On" value.

    Some expired cards carry only the expiry. Working back to the certification
    date lets every column be derived the same way, which matters when one award
    feeds two columns with different validity periods.
    """
    return add_years(expiry_date, -column.validity_years)
