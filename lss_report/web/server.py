from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import uvicorn

from ..config import ConfigurationError, load_env_file
from .app import create_app
from .db import Database
from .repository import StaffRepository
from .settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the certification web application.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--env-file", type=Path, help="Load settings from a dotenv file.")
    parser.add_argument(
        "--seed",
        type=Path,
        help="One-time roster import from a staff.json. Ignored once the database has staff.",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Import the roster and exit without starting the server.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        if args.env_file:
            load_env_file(args.env_file)
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    database = Database(settings.database_path)
    if args.seed:
        imported = StaffRepository(database).seed_from_file(args.seed, actor="seed")
        if imported:
            logging.info("Seeded %d staff from %s; the database is the roster now.", imported, args.seed)
        else:
            logging.info("Roster already populated; ignored %s.", args.seed)

    if args.seed_only:
        return 0

    uvicorn.run(create_app(settings, database), host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
