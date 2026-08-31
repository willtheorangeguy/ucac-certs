from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigurationError

DEFAULT_DATABASE = Path("data/lss.sqlite3")
LOGIN_TOKEN_MINUTES = 15
SESSION_DAYS = 30


def _split(raw: str) -> list[str]:
    return [item.strip().casefold() for item in raw.replace(",", "\n").split("\n") if item.strip()]


def _hour(env: dict[str, str], key: str, default: str) -> int:
    """Read an hour-of-day setting, rejecting anything the scheduler could never match.

    Both failure modes here used to be silent in their own way. A non-numeric value
    raised a bare ``ValueError``, which is the *parent* of ``ConfigurationError`` and so
    escaped the handler that reports settings problems cleanly. An out-of-range value was
    accepted outright, and since the scheduler fires on ``now.hour == value`` the job then
    never ran at all — the worst outcome for a tool whose purpose is noticing expiries.
    """
    raw = env.get(key, default).strip() or default
    try:
        hour = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a whole number between 0 and 23.") from exc
    if not 0 <= hour <= 23:
        raise ConfigurationError(f"{key} must be between 0 and 23, not {hour}.")
    return hour


@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_secret: str
    base_url: str
    managers: tuple[str, ...]
    resend_api_key: str | None = None
    mail_from: str = "certifications@example.org"
    scan_hour: int = 6
    reminder_hour: int = 7
    reminder_days: tuple[int, ...] = (30, 14, 7)
    warnings: tuple[str, ...] = field(default=())

    def is_manager(self, email: str) -> bool:
        return email.strip().casefold() in self.managers

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key)

    @property
    def is_local(self) -> bool:
        return any(host in self.base_url for host in ("127.0.0.1", "localhost", "testserver"))


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    warnings: list[str] = []

    secret = env.get("SESSION_SECRET", "").strip()
    if not secret:
        raise ConfigurationError("SESSION_SECRET is required.")
    if len(secret) < 32:
        raise ConfigurationError("SESSION_SECRET must be at least 32 characters.")

    managers = tuple(_split(env.get("MANAGER_EMAILS", "")))
    if not managers:
        raise ConfigurationError("MANAGER_EMAILS must list at least one address.")
    if any("@" not in address for address in managers):
        raise ConfigurationError("MANAGER_EMAILS contains an entry that is not an address.")

    if not env.get("RESEND_API_KEY"):
        warnings.append("RESEND_API_KEY is unset; login links will be written to the log instead.")

    return Settings(
        database_path=Path(env.get("DATABASE_PATH", DEFAULT_DATABASE)),
        session_secret=secret,
        base_url=env.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        managers=managers,
        resend_api_key=env.get("RESEND_API_KEY") or None,
        mail_from=env.get("MAIL_FROM", "certifications@example.org"),
        scan_hour=_hour(env, "SCAN_HOUR", "6"),
        reminder_hour=_hour(env, "REMINDER_HOUR", "7"),
        warnings=tuple(warnings),
    )
