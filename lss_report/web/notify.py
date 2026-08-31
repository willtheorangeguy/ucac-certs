from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

import requests

from .db import Database
from .settings import Settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
TWILIO_ENDPOINT = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class DeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


class Channel(Protocol):
    name: str

    def available(self) -> bool: ...

    def send(self, message: Message) -> None: ...


class EmailChannel:
    name = "email"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def available(self) -> bool:
        return self.settings.email_enabled

    def send(self, message: Message) -> None:
        if not self.available():
            if self.settings.is_local:
                # Local development: printing the body is how you get a sign-in link.
                logger.warning("Email not configured; would send to %s: %s", message.to, message.body)
            else:
                # Never write sign-in links or staff details to production logs.
                logger.error(
                    "Email not configured; dropped a message to %s. Set RESEND_API_KEY.",
                    message.to,
                )
            return
        response = self.session.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
            json={
                "from": self.settings.mail_from,
                # One recipient per call: Resend counts each To/CC/BCC separately.
                "to": [message.to],
                "subject": message.subject,
                "text": message.body,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            raise DeliveryError(f"Resend rejected the message (HTTP {response.status_code}).")


class SmsChannel:
    """Twilio adapter. Written and tested, but off unless SMS_ENABLED and credentials are set.

    Enabling it later is configuration only: create the Twilio account, buy a Canadian
    number, complete A2P registration, set three secrets, flip SMS_ENABLED.
    """

    name = "sms"

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def available(self) -> bool:
        return self.settings.sms_configured

    def send(self, message: Message) -> None:
        if not self.available():
            raise DeliveryError("SMS is not enabled.")
        response = self.session.post(
            TWILIO_ENDPOINT.format(sid=self.settings.twilio_account_sid),
            auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
            data={"From": self.settings.twilio_from, "To": message.to, "Body": message.body},
            timeout=20,
        )
        if response.status_code >= 400:
            raise DeliveryError(f"Twilio rejected the message (HTTP {response.status_code}).")


def reminder_text(name: str, column_code: str, expiry: date, days: int) -> str:
    when = "expires today" if days == 0 else f"expires in {days} days"
    return (
        f"Hi {name.split()[0]}, your {column_code} certification {when} "
        f"(on {expiry.isoformat()}). Please book a recertification with the Aquatic Centre."
    )


class Reminders:
    def __init__(self, database: Database, settings: Settings, channels: list[Channel]) -> None:
        self.db = database
        self.settings = settings
        self.channels = channels

    def _already_sent(self, entry: dict, channel: str) -> bool:
        row = self.db.query_one(
            "SELECT id FROM notification_log WHERE staff_id = ? AND column_code = ?"
            " AND expiry_date = ? AND threshold = ? AND channel = ?",
            (entry["staff_id"], entry["column_code"], entry["expiry_date"], entry["threshold"], channel),
        )
        return row is not None

    def _record(self, entry: dict, channel: str) -> None:
        try:
            with self.db.write() as connection:
                connection.execute(
                    "INSERT INTO notification_log (staff_id, column_code, expiry_date, threshold,"
                    " channel, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entry["staff_id"],
                        entry["column_code"],
                        entry["expiry_date"],
                        entry["threshold"],
                        channel,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
        except sqlite3.IntegrityError:
            pass

    def send_due(self, due: list[dict], *, dry_run: bool = False) -> list[dict]:
        sent = []
        for entry in due:
            name = entry["society_name"] or entry["name"]
            expiry = date.fromisoformat(entry["expiry_date"])
            body = reminder_text(name, entry["column_code"], expiry, entry["threshold"])
            for channel in self.channels:
                target = self._target(entry, channel)
                if target is None or not channel.available():
                    continue
                if self._already_sent(entry, channel.name):
                    continue
                record = {
                    "staff": name,
                    "column": entry["column_code"],
                    "expiry": entry["expiry_date"],
                    "days": entry["threshold"],
                    "channel": channel.name,
                    "to": target,
                    "body": body,
                }
                if dry_run:
                    sent.append(record)
                    continue
                try:
                    channel.send(
                        Message(
                            to=target,
                            subject=f"{entry['column_code']} certification expires {entry['expiry_date']}",
                            body=body,
                        )
                    )
                except DeliveryError as exc:
                    logger.warning("Reminder to %s failed: %s", target, exc)
                    continue
                self._record(entry, channel.name)
                sent.append(record)
        return sent

    @staticmethod
    def _target(entry: dict, channel: Channel) -> str | None:
        if channel.name == "email":
            return entry.get("email")
        if channel.name == "sms":
            # Never text a number without a recorded consent timestamp.
            if not entry.get("sms_consent_at"):
                return None
            return entry.get("phone")
        return None
