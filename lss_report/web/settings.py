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


@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_secret: str
    base_url: str
    managers: tuple[str, ...]
    resend_api_key: str | None = None
    mail_from: str = "certifications@example.org"
    sms_enabled: bool = False
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None
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

    @property
    def sms_configured(self) -> bool:
        return bool(
            self.sms_enabled
            and self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_from
        )


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

    sms_enabled = env.get("SMS_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}
    if sms_enabled and not all(
        env.get(key) for key in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
    ):
        warnings.append("SMS_ENABLED is set but Twilio credentials are missing; SMS stays off.")

    return Settings(
        database_path=Path(env.get("DATABASE_PATH", DEFAULT_DATABASE)),
        session_secret=secret,
        base_url=env.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        managers=managers,
        resend_api_key=env.get("RESEND_API_KEY") or None,
        mail_from=env.get("MAIL_FROM", "certifications@example.org"),
        sms_enabled=sms_enabled,
        twilio_account_sid=env.get("TWILIO_ACCOUNT_SID") or None,
        twilio_auth_token=env.get("TWILIO_AUTH_TOKEN") or None,
        twilio_from=env.get("TWILIO_FROM") or None,
        scan_hour=int(env.get("SCAN_HOUR", "6")),
        reminder_hour=int(env.get("REMINDER_HOUR", "7")),
        warnings=tuple(warnings),
    )
