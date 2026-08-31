from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import Database
from .settings import LOGIN_TOKEN_MINUTES, SESSION_DAYS, Settings

SESSION_COOKIE = "lss_session"
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Auth:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.db = database
        self.settings = settings
        self._serializer = URLSafeTimedSerializer(settings.session_secret, salt="lss-session")

    # --- rate limiting -------------------------------------------------
    def _too_many(self, identifier: str) -> bool:
        cutoff = (_now() - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)).isoformat()
        row = self.db.query_one(
            "SELECT COUNT(*) AS hits FROM login_attempt WHERE identifier = ? AND created_at > ?",
            (identifier, cutoff),
        )
        return bool(row and row["hits"] >= MAX_ATTEMPTS)

    def _record_attempt(self, identifier: str) -> None:
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO login_attempt (identifier, created_at) VALUES (?, ?)",
                (identifier, _now().isoformat()),
            )

    # --- magic link ----------------------------------------------------
    def issue_login_token(self, email: str, *, client: str) -> str | None:
        """Return a link token, or None when the request must be silently ignored.

        Callers must respond identically either way: whether an address is on the
        manager allowlist is not something an unauthenticated caller may learn.
        """
        email = email.strip().casefold()
        for identifier in (f"email:{email}", f"client:{client}"):
            if self._too_many(identifier):
                return None
            self._record_attempt(identifier)

        if not self.settings.is_manager(email):
            return None

        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(minutes=LOGIN_TOKEN_MINUTES)
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO login_token (token_hash, email, expires_at, created_at)"
                " VALUES (?, ?, ?, ?)",
                (hash_token(token), email, expires.isoformat(), _now().isoformat()),
            )
        return token

    def redeem(self, token: str) -> str | None:
        """Consume a login token once, returning the email it belongs to."""
        row = self.db.query_one(
            "SELECT * FROM login_token WHERE token_hash = ?", (hash_token(token),)
        )
        if row is None:
            return None
        if row["used_at"] is not None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            return None
        with self.db.write() as connection:
            cursor = connection.execute(
                "UPDATE login_token SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (_now().isoformat(), row["id"]),
            )
            if cursor.rowcount != 1:
                # Another request redeemed it between the read and the write.
                return None
        email = row["email"]
        return email if self.settings.is_manager(email) else None

    # --- sessions ------------------------------------------------------
    def create_session(self, email: str) -> str:
        return self._serializer.dumps(email)

    def read_session(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            email = self._serializer.loads(cookie, max_age=SESSION_DAYS * 86400)
        except BadSignature:
            return None
        except Exception:
            return None
        return email if self.settings.is_manager(email) else None

    def purge_expired(self) -> None:
        cutoff = (_now() - timedelta(days=1)).isoformat()
        with self.db.write() as connection:
            connection.execute("DELETE FROM login_token WHERE expires_at < ?", (cutoff,))
            connection.execute("DELETE FROM login_attempt WHERE created_at < ?", (cutoff,))
