from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ConfigurationError, load_env_file, load_staff_file, load_staff_json
from .excel import build_workbook
from .grid import build_grid
from .models import ReportData
from .pdf import build_pdf
from .scraper import SocietyClient, UpstreamError

TIMEZONE = ZoneInfo("America/Edmonton")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Lifesaving certification grid. Maintenance and debugging tool; "
            "the web application is the primary entrypoint and owns the roster."
        )
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--staff-file", type=Path, help="Path to the staff JSON file.")
    source.add_argument("--staff-json", help=argparse.SUPPRESS)
    parser.add_argument("--env-file", type=Path, help="Load settings from an ignored .env file.")
    parser.add_argument("--output", type=Path, help="Write the PDF locally.")
    parser.add_argument("--excel", type=Path, help="Write the Excel workbook locally.")
    return parser


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
        if args.output is None and args.excel is None:
            raise ConfigurationError("Choose --output and/or --excel.")
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        client = SocietyClient()
        records = [client.fetch(person) for person in staff]
        grid = build_grid(ReportData(generated_at=datetime.now(TIMEZONE), records=records))
        if args.output:
            build_pdf(grid, args.output)
        if args.excel:
            build_workbook(grid, args.excel)
        print(f"Report completed for {len(records)} staff record(s).")
        return 0
    except UpstreamError as exc:
        print(f"Upstream error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Report generation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
