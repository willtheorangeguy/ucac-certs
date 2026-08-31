from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import theme
from .awards import COLUMNS
from .grid import EXPIRY_WARNING_DAYS, Grid


def _register_fonts() -> tuple[str, str]:
    import reportlab

    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    regular = font_dir / "Vera.ttf"
    bold = font_dir / "VeraBd.ttf"
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("ReportFont", regular))
        pdfmetrics.registerFont(TTFont("ReportFont-Bold", bold))
        return "ReportFont", "ReportFont-Bold"
    return "Helvetica", "Helvetica-Bold"


def _styles(font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=bold_font,
            fontSize=13,
            textColor=colors.HexColor("#123B5D"),
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "Body": ParagraphStyle("Body", parent=base["BodyText"], fontName=font, fontSize=7, leading=9, spaceAfter=1),
        "Heading": ParagraphStyle(
            "Heading", parent=base["Heading2"], fontName=bold_font, fontSize=11, leading=14
        ),
        "Cell": ParagraphStyle(
            "Cell",
            parent=base["BodyText"],
            fontName=font,
            fontSize=6.5,
            leading=7.5,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "Note": ParagraphStyle(
            "Note",
            parent=base["BodyText"],
            fontName=font,
            fontSize=8,
            textColor=colors.HexColor("#5B6470"),
        ),
    }


def _grid_table(grid: Grid, font: str, bold_font: str, styles) -> Table:
    header = ["Names:", *(column.code for column in COLUMNS), "LS#"]
    rows: list[list] = [header]
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 0), (-1, 0), bold_font),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{theme.HEADER}")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AEAAAA")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]

    seen_away = False
    for row in grid.rows:
        if row.away and not seen_away:
            seen_away = True
            index = len(rows)
            rows.append(["Away Spring / Summer", *([""] * (len(header) - 1))])
            commands.extend(
                [
                    ("SPAN", (0, index), (-1, index)),
                    ("BACKGROUND", (0, index), (-1, index), colors.HexColor(f"#{theme.HEADER}")),
                    ("FONTNAME", (0, index), (-1, index), bold_font),
                ]
            )

        index = len(rows)
        line = [Paragraph(escape(row.name), styles["Cell"])]
        if row.error:
            line.append(Paragraph(escape(row.error), styles["Cell"]))
            line.extend([""] * (len(COLUMNS) - 1))
            commands.extend(
                [
                    ("SPAN", (1, index), (len(COLUMNS), index)),
                    (
                        "BACKGROUND",
                        (1, index),
                        (len(COLUMNS), index),
                        colors.HexColor(f"#{theme.ERROR}"),
                    ),
                ]
            )
        else:
            for offset, cell in enumerate(row.cells, start=1):
                line.append(cell.expiry_date.isoformat() if cell.expiry_date else "")
                fill = theme.STATUS_FILL[cell.status]
                if fill:
                    commands.append(
                        ("BACKGROUND", (offset, index), (offset, index), colors.HexColor(f"#{fill}"))
                    )
                    commands.append(
                        (
                            "TEXTCOLOR",
                            (offset, index),
                            (offset, index),
                            colors.HexColor(f"#{theme.STATUS_TEXT[cell.status]}"),
                        )
                    )
        line.append(row.member_code)
        rows.append(line)

    table = Table(
        rows,
        colWidths=[1.65 * inch, *([0.83 * inch] * len(COLUMNS)), 0.72 * inch],
        repeatRows=1,
    )
    table.setStyle(TableStyle(commands))
    return table


def build_pdf(grid: Grid, output_path: Path) -> None:
    font, bold_font = _register_fonts()
    styles = _styles(font, bold_font)

    legend = (
        f"<font backColor='#{theme.EXPIRED}'>&nbsp; Expired &nbsp;</font> &nbsp; "
        f"<font backColor='#{theme.EXPIRING}'>&nbsp; Expires within {EXPIRY_WARNING_DAYS} days &nbsp;</font> &nbsp; "
        f"<font backColor='#{theme.MISSING}' color='#FFFFFF'>&nbsp; No award on record &nbsp;</font>"
    )
    validity = " &nbsp;·&nbsp; ".join(
        f"{column.code} {column.validity_years}yr" for column in COLUMNS
    )

    story: list = [
        Paragraph("Ucalgary Aquatic Center Staff Certifications", styles["Title"]),
        Paragraph(
            f"Generated {grid.as_of.isoformat()} &nbsp;|&nbsp; "
            f"Valid from certification date: {validity}",
            styles["Body"],
        ),
        Paragraph(legend, styles["Body"]),
        Spacer(1, 0.08 * inch),
        _grid_table(grid, font, bold_font, styles),
    ]

    notes = [
        *(f"Unmapped award: {title}" for title in grid.unmapped_awards),
        *(f"Status disagreement: {note}" for note in grid.disagreements),
    ]
    if notes:
        story.append(Spacer(1, 0.2 * inch))
        story.append(
            KeepTogether(
                [
                    Paragraph("Diagnostics", styles["Heading"]),
                    *(Paragraph(escape(note), styles["Note"]) for note in notes),
                ]
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        title="Lifesaving Society Certification Report",
        author="Automated Certification Report",
    )
    document.build(story)
