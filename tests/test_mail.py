from email import policy
from email.parser import BytesParser
from pathlib import Path

from lss_report.mail import SmtpConfig, send_report


class FakeSmtp:
    def __init__(self):
        self.logged_in = None
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.message = message


def test_send_report_attaches_the_pdf_and_the_workbook(monkeypatch, tmp_path: Path):
    fake = FakeSmtp()
    monkeypatch.setattr("lss_report.mail._connect", lambda config: fake)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-test")
    excel = tmp_path / "report.xlsx"
    excel.write_bytes(b"PK\x03\x04test")
    config = SmtpConfig("smtp.example.com", 587, "starttls", "user", "secret", "from@example.com", ["manager@example.com"])
    send_report(
        config,
        pdf,
        excel,
        report_date="2026-08-26",
        staff_count=45,
        expired_count=7,
        expiring_count=3,
        error_count=1,
    )
    assert fake.logged_in == ("user", "secret")
    assert fake.message["To"] == "manager@example.com"
    types = [part.get_content_type() for part in fake.message.iter_attachments()]
    assert types == [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]

