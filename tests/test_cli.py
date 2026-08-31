def test_cli_imports_with_edmonton_timezone():
    from lss_report.cli import TIMEZONE

    assert TIMEZONE.key == "America/Edmonton"


def test_the_roster_cannot_be_passed_as_a_json_string(capsys, monkeypatch):
    """argv and the environment are both readable by other processes on the machine."""
    from lss_report.cli import main

    roster = '[{"name":"Robin Rivers","memberCode":"RRV001"}]'
    monkeypatch.setenv("STAFF_JSON", roster)

    assert main(["--output", "report.pdf"]) == 2
    assert "--staff-file" in capsys.readouterr().err


def test_the_hidden_staff_json_flag_is_gone():
    import pytest

    from lss_report.cli import main

    with pytest.raises(SystemExit):
        main(["--staff-json", '[{"name":"Robin Rivers","memberCode":"RRV001"}]'])
