from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import (
    ConfigurationError,
    load_env_file,
    load_recipients,
    load_staff_file,
    load_staff_json,
)
from .excel import build_workbook
from .grid import build_grid
from .mail import SmtpConfig, send_failure, send_report
from .models import CellStatus, ReportData
from .pdf import build_pdf
from .scraper import SocietyClient, UpstreamError

TIMEZONE = ZoneInfo("America/Edmonton")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Lifesaving certification PDF report.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--staff-file", type=Path, help="Path to the staff JSON file.")
    source.add_argument("--staff-json", help=argparse.SUPPRESS)
    parser.add_argument("--env-file", type=Path, help="Load SMTP settings from an ignored .env file.")
    parser.add_argument("--output", type=Path, help="Write the PDF locally.")
    parser.add_argument("--excel", type=Path, help="Write the Excel workbook locally.")
    parser.add_argument("--email", action="store_true", help="Email the report using SMTP environment variables.")
    return parser


def _smtp_config() -> SmtpConfig:
    required = [
        "SMTP_HOST",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "REPORT_RECIPIENTS",
    ]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ConfigurationError("Missing SMTP environment variables: " + ", ".join(missing))
    security = os.environ.get("SMTP_SECURITY", "starttls").casefold()
    default_port = "465" if security == "ssl" else "587"
    try:
        port = int(os.environ.get("SMTP_PORT", default_port))
    except ValueError as exc:
        raise ConfigurationError("SMTP_PORT must be an integer.") from exc
    return SmtpConfig(
        host=os.environ["SMTP_HOST"],
        port=port,
        security=security,
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        sender=os.environ["SMTP_FROM"],
        recipients=load_recipients(os.environ["REPORT_RECIPIENTS"]),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.env_file:
            load_env_file(args.env_file)
        raw_secret = args.staff_json or os.environ.get("STAFF_JSON")
        if args.staff_file:
            staff = load_staff_file(args.staff_file)
        elif raw_secret:
            staff = load_staff_json(raw_secret)
        else:
            raise ConfigurationError("Provide --staff-file or the STAFF_JSON environment variable.")
        smtp = _smtp_config() if args.email else None
        if not args.email and args.output is None and args.excel is None:
            raise ConfigurationError(
                "Choose --output and/or --excel for local files, or --email for delivery."
            )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(TIMEZONE)
    try:
        client = SocietyClient()
        records = [client.fetch(person) for person in staff]
        grid = build_grid(ReportData(generated_at=now, records=records))

        if args.output:
            build_pdf(grid, args.output)
        if args.excel:
            build_workbook(grid, args.excel)
        if smtp is not None:
            with tempfile.TemporaryDirectory(prefix="lss-report-") as temp_dir:
                pdf_path = args.output or Path(temp_dir) / "report.pdf"
                excel_path = args.excel or Path(temp_dir) / "report.xlsx"
                if not args.output:
                    build_pdf(grid, pdf_path)
                if not args.excel:
                    build_workbook(grid, excel_path)
                send_report(
                    smtp,
                    pdf_path,
                    excel_path,
                    report_date=now.date().isoformat(),
                    staff_count=len(records),
                    expired_count=len(grid.rows_with_status(CellStatus.EXPIRED)),
                    expiring_count=len(grid.rows_with_status(CellStatus.EXPIRING)),
                    error_count=sum(row.error is not None for row in grid.rows),
                )
        print(f"Report completed for {len(records)} staff record(s).")
        return 0
    except UpstreamError as exc:
        print(f"Upstream error: {exc}", file=sys.stderr)
        if smtp is not None:
            try:
                send_failure(smtp, report_date=now.date().isoformat(), reason=str(exc))
            except Exception as mail_exc:
                print(f"Failure notification could not be sent: {type(mail_exc).__name__}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Report generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if smtp is not None:
            try:
                send_failure(
                    smtp,
                    report_date=now.date().isoformat(),
                    reason="Internal report generation or delivery error.",
                )
            except Exception as mail_exc:
                print(f"Failure notification could not be sent: {type(mail_exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
