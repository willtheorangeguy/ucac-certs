import dataclasses
from datetime import date, timedelta

import pytest

from lss_report.web.notify import EmailChannel, Message, Reminders, SmsChannel
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
        "phone": "+15875550100",
        "sms_consent_at": None,
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


def test_sms_is_unavailable_until_enabled_and_credentialled(settings):
    assert SmsChannel(settings).available() is False
    half = dataclasses.replace(settings, sms_enabled=True)
    assert SmsChannel(half).available() is False
    full = dataclasses.replace(
        settings,
        sms_enabled=True,
        twilio_account_sid="AC1",
        twilio_auth_token="secret",
        twilio_from="+15875550000",
    )
    assert SmsChannel(full).available() is True


def test_sms_never_goes_to_a_number_without_recorded_consent(staffed, settings):
    channel = RecordingChannel("sms")
    reminders = Reminders(staffed, settings, [channel])
    assert reminders.send_due([_entry(sms_consent_at=None)]) == []
    assert channel.sent == []


def test_sms_goes_out_once_consent_is_recorded(staffed, settings):
    channel = RecordingChannel("sms")
    reminders = Reminders(staffed, settings, [channel])
    sent = reminders.send_due([_entry(sms_consent_at="2026-08-01T00:00:00")])
    assert len(sent) == 1
    assert channel.sent[0].to == "+15875550100"


def test_a_reminder_is_never_sent_twice(staffed, settings):
    channel = RecordingChannel("email")
    reminders = Reminders(staffed, settings, [channel])
    assert len(reminders.send_due([_entry()])) == 1
    assert reminders.send_due([_entry()]) == []
    assert len(channel.sent) == 1


def test_enabling_sms_later_does_not_replay_past_email_reminders(staffed, settings):
    email = RecordingChannel("email")
    Reminders(staffed, settings, [email]).send_due([_entry()])
    sms = RecordingChannel("sms")
    # A newly enabled channel sends its own first message, but email stays deduped.
    both = Reminders(staffed, settings, [email, sms])
    sent = both.send_due([_entry(sms_consent_at="2026-08-01T00:00:00")])
    assert [item["channel"] for item in sent] == ["sms"]
    assert len(email.sent) == 1


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
