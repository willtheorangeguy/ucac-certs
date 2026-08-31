def test_cli_imports_with_edmonton_timezone():
    from lss_report.cli import TIMEZONE

    assert TIMEZONE.key == "America/Edmonton"
