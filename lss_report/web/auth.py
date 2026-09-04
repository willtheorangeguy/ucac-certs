from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import Database
from .settings import LOGIN_CODE_MINUTES, SESSION_DAYS, Settings

SESSION_COOKIE = "lss_session"
PENDING_COOKIE = "lss_pending"
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_MINUTES = 15
CODE_DIGITS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Auth:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.db = database
        self.settings = settings
        self._serializer = URLSafeTimedSerializer(settings.session_secret, salt="lss-session")
        self._pending = URLSafeTimedSerializer(settings.session_secret, salt="lss-pending")

    def code_hash(self, email: str, code: str) -> str:
        """Key the stored digest with the session secret and bind it to one address.

        A six-digit code has a million possible values, so a plain SHA-256 of it is
        reversible by anyone who reads the table. Keying with the secret means a leaked
        database yields nothing without the secret as well, and mixing the address in
        stops a code issued for one manager from being redeemed as another.
        """
        message = f"{email.strip().casefold()}:{code}".encode("utf-8")
        return hmac.new(self.settings.session_secret.encode("utf-8"), message, sha256).hexdigest()

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

    def _rate_limited(self, identifiers: tuple[str, ...]) -> bool:
        for identifier in identifiers:
            if self._too_many(identifier):
                return True
            self._record_attempt(identifier)
        return False

    # --- one-time codes ------------------------------------------------
    def issue_login_code(self, email: str, *, client: str) -> str | None:
        """Return a sign-in code, or None when the request must be silently ignored.

        Callers must respond identically either way: whether an address is on the
        manager allowlist is not something an unauthenticated caller may learn.
        """
        email = email.strip().casefold()
        if self._rate_limited((f"email:{email}", f"issue:{client}")):
            return None

        if not self.settings.is_manager(email):
            return None

        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        expires = _now() + timedelta(minutes=LOGIN_CODE_MINUTES)
        with self.db.write() as connection:
            # One live code per address. Clearing the old rows keeps the guessable space
            # at one code rather than every code the address has ever been sent, and it
            # keeps the UNIQUE digest from colliding when the same code comes up twice.
            connection.execute("DELETE FROM login_token WHERE email = ?", (email,))
            connection.execute(
                "INSERT INTO login_token (token_hash, email, expires_at, created_at)"
                " VALUES (?, ?, ?, ?)",
                (self.code_hash(email, code), email, expires.isoformat(), _now().isoformat()),
            )
        return code

    def redeem(self, email: str, code: str, *, client: str) -> str | None:
        """Consume a sign-in code once, returning the address it belongs to.

        Guessing is the whole threat model for a six-digit secret, so redemption is rate
        limited exactly like issuing is: five tries per address and per client per window.
        The counters are separate from the issuing ones, or four mistyped digits would use
        up the budget for asking for a replacement code.
        """
        email = email.strip().casefold()
        code = code.strip()
        if self._rate_limited((f"verify:{email}", f"guess:{client}")):
            return None

        row = self.db.query_one(
            "SELECT * FROM login_token WHERE token_hash = ?", (self.code_hash(email, code),)
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
        stored = row["email"]
        return stored if self.settings.is_manager(stored) else None

    # --- the address a code was sent to, carried between the two forms ---
    def create_pending(self, email: str) -> str:
        return self._pending.dumps(email.strip().casefold())

    def read_pending(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            return self._pending.loads(cookie, max_age=LOGIN_CODE_MINUTES * 60)
        except BadSignature:
            return None
        except Exception:
            return None

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
