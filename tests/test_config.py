import pytest

from lss_report.config import ConfigurationError, load_env_file, load_recipients, load_staff_json


def test_load_staff_normalizes_codes_and_whitespace():
    staff = load_staff_json('[{"name":" Example   Person ","memberCode":" ab12cd "}]')
    assert staff[0].name == "Example Person"
    assert staff[0].member_code == "AB12CD"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        '[{"name":"","memberCode":"ABC123"}]',
        '[{"name":"One","memberCode":"bad code"}]',
        '[{"name":"One","memberCode":"ABC123"},{"name":"Two","memberCode":"abc123"}]',
    ],
)
def test_invalid_staff_is_rejected(raw):
    with pytest.raises(ConfigurationError):
        load_staff_json(raw)


def test_recipients_are_json_and_reject_header_injection():
    assert load_recipients('["manager@example.com"]') == ["manager@example.com"]
    with pytest.raises(ConfigurationError):
        load_recipients('["manager@example.com\\nBcc: bad@example.com"]')


def test_load_env_file_without_overriding_existing_value(monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_HOST", "already-set.example.com")
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# local settings\nSMTP_HOST="file.example.com"\nSMTP_PORT=587\n',
        encoding="utf-8",
    )
    load_env_file(env_file)
    assert __import__("os").environ["SMTP_HOST"] == "already-set.example.com"
    assert __import__("os").environ["SMTP_PORT"] == "587"


def test_load_env_file_rejects_invalid_lines(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("NOT VALID", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_env_file(env_file)
