import dataclasses
from datetime import date, timedelta

import pytest

from lss_report.web.notify import EmailChannel, Message, Reminders
from lss_report.web.repository import ScanRepository, StaffRepository


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeSession:
    def __init__(self, status_code=200):
        self.calls = []
        self.status_code = status_code

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.status_code)


class RecordingChannel:
    def __init__(self, name):
        self.name = name
        self.sent = []

    def available(self):
        return True

    def send(self, message):
        self.sent.append(message)


def _entry(staff_id=1, **overrides):
    base = {
        "staff_id": staff_id,
        "column_code": "CPR-C",
        "expiry_date": "2026-09-14",
        "threshold": 7,
        "name": "Robin Rivers",
        "society_name": None,
        "email": "robin@example.org",
    }
    return {**base, **overrides}


@pytest.fixture
def staffed(database):
    StaffRepository(database).add(name="Robin Rivers", member_code="RRV001")
    return database


def test_email_channel_sends_one_recipient_per_call(settings):
    session = FakeSession()
    live = dataclasses.replace(settings, resend_api_key="re_test")
    EmailChannel(live, session).send(Message("robin@example.org", "Subject", "Body"))
    _, kwargs = session.calls[0]
    assert kwargs["json"]["to"] == ["robin@example.org"]





def test_a_reminder_is_never_sent_twice(staffed, settings):
    channel = RecordingChannel("email")
    reminders = Reminders(staffed, settings, [channel])
    assert len(reminders.send_due([_entry()])) == 1
    assert reminders.send_due([_entry()]) == []
    assert len(channel.sent) == 1



def test_dry_run_previews_without_sending_or_recording(staffed, settings):
    channel = RecordingChannel("email")
    reminders = Reminders(staffed, settings, [channel])
    preview = reminders.send_due([_entry()], dry_run=True)
    assert len(preview) == 1
    assert channel.sent == []
    # Nothing was recorded, so the real send still goes out afterwards.
    assert len(reminders.send_due([_entry()])) == 1


def test_staff_without_an_email_address_are_skipped(staffed, settings):
    channel = RecordingChannel("email")
    reminders = Reminders(staffed, settings, [channel])
    assert reminders.send_due([_entry(email=None)]) == []


def test_due_finds_only_exact_ladder_steps(database):
    staff_repo = StaffRepository(database)
    scan_repo = ScanRepository(database)
    member = staff_repo.add(name="Robin Rivers", member_code="RRV001")
    today = date(2026, 8, 30)
    scan_id = scan_repo.start(triggered_by="test")
    with database.write() as connection:
        for offset in (7, 8, 14):
            connection.execute(
                "INSERT INTO scan_result (scan_id, staff_id, column_code, expiry_date, status)"
                " VALUES (?, ?, ?, ?, 'expiring')",
                (scan_id, member.id, f"C{offset}", (today + timedelta(days=offset)).isoformat()),
            )
    due = scan_repo.due(scan_id, (30, 14, 7), today)
    assert sorted(item["column_code"] for item in due) == ["C14", "C7"]


@pytest.mark.parametrize("threshold", [30, 14, 7])
def test_locker_reminders_share_cadence_and_dedupe_without_a_scan(database, settings, threshold):
    repo = StaffRepository(database)
    member = repo.add(name="Robin Rivers", member_code="RRV001", supervisor=True,
                      email="robin@example.org", fit_test_date="2024-10-06",
                      cartridge_date="2021-10-06")
    scans = ScanRepository(database)
    today = date(2026, 10, 6) - timedelta(days=threshold)
    due = scans.due(None, settings.reminder_days, today)
    assert {entry["column_code"] for entry in due} == {"Fit test", "Cartridge"}
    assert {entry["threshold"] for entry in due} == {threshold}
    assert scans.due(None, settings.reminder_days, date(2026, 10, 5)) == []
    upcoming = scans.upcoming(None, settings.reminder_days, today)
    assert len([entry for entry in upcoming if entry["threshold"] == threshold]) == 2
    channel = RecordingChannel("email")
    reminders = Reminders(database, settings, [channel])
    assert len(reminders.send_due(due)) == 2
    assert reminders.send_due(due) == []
    assert "book a new fit test" in channel.sent[0].body
    assert "replacement cartridge" in channel.sent[1].body
    assert all("certification" not in message.subject for message in channel.sent)
    assert all(message.to == member.email for message in channel.sent)
    assert len(scans.history()) == 2
    assert all(entry["already_sent"] for entry in scans.upcoming(None, (threshold,), today))


def test_locker_reminders_skip_non_supervisors_removed_and_empty_dates(database, settings):
    repo = StaffRepository(database)
    repo.add(name="Regular Person", member_code="REG001", fit_test_date="2024-10-06")
    removed = repo.add(name="Removed Person", member_code="REM001", supervisor=True,
                       fit_test_date="2024-10-06", cartridge_date="2021-10-06")
    repo.remove(removed.id, actor="manager")
    repo.add(name="Empty Supervisor", member_code="SUP001", supervisor=True)
    assert ScanRepository(database).due(None, settings.reminder_days, date(2026, 9, 6)) == []


def test_locker_renewal_changes_reminder_schedule(database, settings):
    repo = StaffRepository(database)
    member = repo.add(name="Robin Rivers", member_code="RRV001", supervisor=True,
                      fit_test_date="2024-10-06")
    scans = ScanRepository(database)
    today = date(2026, 9, 6)
    assert len(scans.due(None, settings.reminder_days, today)) == 1
    repo.update(member.id, actor="manager", fit_test_date="2026-10-06")
    assert scans.due(None, settings.reminder_days, today) == []
