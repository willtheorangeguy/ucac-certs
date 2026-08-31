from datetime import date, datetime
from pathlib import Path

from lss_report.awards import NATIONAL_LIFEGUARD, OXYGEN
from lss_report.grid import build_grid
from lss_report.models import Certification, MemberRecord, ReportData
from lss_report.pdf import build_pdf


def _grid(*records: MemberRecord):
    return build_grid(ReportData(generated_at=datetime(2026, 8, 30, 6, 15), records=list(records)))


def test_grid_pdf_is_generated(tmp_path: Path):
    record = MemberRecord(
        configured_name="Example Staff Member",
        member_code="ABC123",
        certifications=[
            Certification("National Lifeguard - Pool Recert", date(2025, 9, 14), NATIONAL_LIFEGUARD, date(2027, 9, 14)),
            Certification("O2 Administration", date(2021, 12, 24), OXYGEN, date(2023, 12, 24), site_current=False),
        ],
    )
    output = tmp_path / "report.pdf"
    build_pdf(_grid(record), output)
    raw = output.read_bytes()
    assert raw.startswith(b"%PDF-")
    assert len(raw) > 1_000


def test_pdf_escapes_reportlab_markup(tmp_path: Path):
    record = MemberRecord(
        configured_name="A & B <Staff>",
        member_code="ABC123",
        certifications=[Certification("CPR & AED <Current>", date(2025, 1, 1))],
    )
    output = tmp_path / "escaped.pdf"
    build_pdf(_grid(record), output)
    assert output.read_bytes().startswith(b"%PDF-")


def test_pdf_renders_the_away_section(tmp_path: Path):
    grid = _grid(
        MemberRecord(configured_name="Zoe Active", member_code="AAA111"),
        MemberRecord(configured_name="Aaron Away", member_code="BBB222", away=True),
    )
    output = tmp_path / "away.pdf"
    build_pdf(grid, output)
    assert output.read_bytes().startswith(b"%PDF-")
