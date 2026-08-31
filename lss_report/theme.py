from __future__ import annotations

from .models import CellStatus

# Sampled from the fill operators in "Cert Form June 2024.pdf".
EXPIRED = "FF6565"
EXPIRING = "FFD13F"
MISSING = "808080"
HEADER = "D9E1F2"
ERROR = "FFC7CE"

STATUS_FILL: dict[CellStatus, str | None] = {
    CellStatus.CURRENT: None,
    CellStatus.EXPIRING: EXPIRING,
    CellStatus.EXPIRED: EXPIRED,
    CellStatus.MISSING: MISSING,
}

STATUS_TEXT: dict[CellStatus, str] = {
    CellStatus.CURRENT: "000000",
    CellStatus.EXPIRING: "000000",
    CellStatus.EXPIRED: "000000",
    CellStatus.MISSING: "FFFFFF",
}
