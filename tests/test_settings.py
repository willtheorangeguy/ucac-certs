"""Settings validation. A bad value must stop the process, not disable a job quietly."""

import pytest

from lss_report.config import ConfigurationError
from lss_report.web.settings import load_settings

BASE = {"SESSION_SECRET": "x" * 40, "MANAGER_EMAILS": "manager@example.org"}


def _env(**overrides) -> dict:
    return {**BASE, **overrides}


def test_the_hours_default_when_unset():
    settings = load_settings(_env())
    assert (settings.scan_hour, settings.reminder_hour) == (6, 7)


@pytest.mark.parametrize("key", ["SCAN_HOUR", "REMINDER_HOUR"])
@pytest.mark.parametrize("value", ["24", "99", "-1"])
def test_an_hour_outside_the_clock_is_refused(key, value):
    # The scheduler fires on `now.hour == value`, so an out-of-range hour never matches
    # and the job silently never runs. That has to be a startup failure, not a default.
    with pytest.raises(ConfigurationError, match="between 0 and 23"):
        load_settings(_env(**{key: value}))


@pytest.mark.parametrize("key", ["SCAN_HOUR", "REMINDER_HOUR"])
def test_a_non_numeric_hour_is_a_configuration_error_not_a_traceback(key):
    # ConfigurationError subclasses ValueError, so a bare ValueError from int() escapes
    # the handler that reports settings problems cleanly.
    with pytest.raises(ConfigurationError):
        load_settings(_env(**{key: "noon"}))


@pytest.mark.parametrize("value", ["0", "23"])
def test_the_ends_of_the_clock_are_allowed(value):
    assert load_settings(_env(SCAN_HOUR=value)).scan_hour == int(value)


def test_an_empty_hour_falls_back_to_the_default():
    assert load_settings(_env(SCAN_HOUR="")).scan_hour == 6
