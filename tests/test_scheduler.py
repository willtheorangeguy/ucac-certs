from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from lss_report.web.notify import Reminders
from lss_report.web.repository import ScanRepository, StaffRepository
from lss_report.web.scheduler import Scheduler

TZ = ZoneInfo("America/Edmonton")
MONDAY_6AM = datetime(2026, 8, 31, 6, 30, tzinfo=TZ)


class FakeRunner:
    def __init__(self):
        self.starts = []
        self.running = False

    def start(self, *, triggered_by):
        self.starts.append(triggered_by)
        return True


@pytest.fixture
def scheduler(database, settings):
    scan_repo = ScanRepository(database)
    runner = FakeRunner()
    reminders = Reminders(database, settings, [])
    return Scheduler(settings, runner, scan_repo, reminders), runner, scan_repo


def test_weekly_scan_fires_on_monday_morning(scheduler):
    sched, runner, _ = scheduler
    sched.tick(MONDAY_6AM)
    assert runner.starts == ["schedule"]


def test_scan_does_not_fire_twice_in_one_day(scheduler):
    sched, runner, _ = scheduler
    sched.tick(MONDAY_6AM)
    sched.tick(MONDAY_6AM.replace(minute=59))
    assert runner.starts == ["schedule"]


def test_scan_skips_other_weekdays_and_other_hours(scheduler):
    sched, runner, _ = scheduler
    sched.tick(MONDAY_6AM.replace(day=1, month=9))  # Tuesday
    sched.tick(MONDAY_6AM.replace(hour=9))
    assert runner.starts == []


def test_scan_is_skipped_when_one_is_already_running(scheduler):
    sched, runner, _ = scheduler
    runner.running = True
    sched.tick(MONDAY_6AM)
    assert runner.starts == []


def test_a_restart_mid_day_does_not_rerun_a_finished_scan(scheduler, database, settings):
    sched, runner, scan_repo = scheduler
    scan_repo.start(triggered_by="manual")
    with database.write() as connection:
        connection.execute("UPDATE scan SET started_at = ?", (MONDAY_6AM.isoformat(),))
    fresh = Scheduler(settings, runner, scan_repo, Reminders(database, settings, []))
    fresh.tick(MONDAY_6AM)
    assert runner.starts == []


def test_reminder_pass_runs_once_a_day(scheduler, monkeypatch):
    sched, _, scan_repo = scheduler
    calls = []
    monkeypatch.setattr(sched, "_send_reminders", lambda today: calls.append(today))
    seven_am = MONDAY_6AM.replace(hour=7)
    sched.tick(seven_am)
    sched.tick(seven_am.replace(minute=45))
    assert len(calls) == 1
