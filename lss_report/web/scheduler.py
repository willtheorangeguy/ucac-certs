from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from .notify import Reminders
from .repository import ScanRepository
from .scans import TIMEZONE, ScanRunner
from .settings import Settings

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
SCAN_WEEKDAY = 0  # Monday


class Scheduler:
    """Weekly scan and daily reminder pass, driven by a plain daemon thread.

    Deliberately not a cron library: two jobs on a single always-on machine do not
    justify the dependency. Both jobs are idempotent — a repeat scan on the same day
    is skipped by date, and reminders are deduped by `notification_log` — so a restart
    mid-day cannot double-fire.
    """

    def __init__(
        self,
        settings: Settings,
        runner: ScanRunner,
        scan_repo: ScanRepository,
        reminders: Reminders,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.scan_repo = scan_repo
        self.reminders = reminders
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_reminder_date: date | None = None
        self._last_scan_date: date | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lss-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick(datetime.now(TIMEZONE))
            except Exception:  # noqa: BLE001 - a bad tick must not kill the scheduler
                logger.exception("Scheduled job failed")

    def tick(self, now: datetime) -> None:
        if (
            now.weekday() == SCAN_WEEKDAY
            and now.hour == self.settings.scan_hour
            and self._last_scan_date != now.date()
            and not self._scanned_today(now.date())
            and not self.runner.running
        ):
            # Claim the day before starting: the worker thread writes its scan row
            # asynchronously, so the database guard alone can miss a fast second tick.
            self._last_scan_date = now.date()
            logger.info("Starting the weekly scan")
            self.runner.start(triggered_by="schedule")

        if now.hour == self.settings.reminder_hour and self._last_reminder_date != now.date():
            self._last_reminder_date = now.date()
            self._send_reminders(now.date())

    def _scanned_today(self, today: date) -> bool:
        latest = self.scan_repo.latest()
        if not latest:
            return False
        started = datetime.fromisoformat(latest["started_at"])
        return started.date() == today

    def _send_reminders(self, today: date) -> None:
        scan_id = self.scan_repo.latest_complete_id()
        if scan_id is None:
            return
        due = self.scan_repo.due(scan_id, self.settings.reminder_days, today)
        sent = self.reminders.send_due(due)
        if sent:
            logger.info("Sent %d reminder(s)", len(sent))
