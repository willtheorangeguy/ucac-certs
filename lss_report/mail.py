from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    recipients: list[str]


def _connect(config: SmtpConfig):
    context = ssl.create_default_context()
    if config.security == "ssl":
        return smtplib.SMTP_SSL(config.host, config.port, timeout=30, context=context)
    if config.security != "starttls":
        raise ValueError("SMTP_SECURITY must be 'starttls' or 'ssl'.")
    client = smtplib.SMTP(config.host, config.port, timeout=30)
    client.ehlo()
    client.starttls(context=context)
    client.ehlo()
    return client


_EXCEL_SUBTYPE = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def send_report(
    config: SmtpConfig,
    pdf_path: Path,
    excel_path: Path,
    *,
    report_date: str,
    staff_count: int,
    expired_count: int,
    expiring_count: int,
    error_count: int,
) -> None:
    message = EmailMessage()
    message["Subject"] = f"Lifesaving certification report — {report_date}"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(
        "The weekly Lifesaving Society certification report is attached.\n\n"
        f"Staff records: {staff_count}\n"
        f"Expired certifications: {expired_count}\n"
        f"Expiring within 30 days: {expiring_count}\n"
        f"Record errors: {error_count}\n"
    )
    message.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=f"lifesaving-certifications-{report_date}.pdf",
    )
    message.add_attachment(
        excel_path.read_bytes(),
        maintype="application",
        subtype=_EXCEL_SUBTYPE,
        filename=f"lifesaving-certifications-{report_date}.xlsx",
    )
    with _connect(config) as client:
        client.login(config.username, config.password)
        client.send_message(message)


def send_failure(config: SmtpConfig, *, report_date: str, reason: str) -> None:
    message = EmailMessage()
    message["Subject"] = f"Lifesaving certification report failed — {report_date}"
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    message.set_content(
        "The weekly certification report could not be generated. No report was sent.\n\n"
        f"Reason: {reason}\n\n"
        "Review the GitHub Actions run for technical diagnostics."
    )
    with _connect(config) as client:
        client.login(config.username, config.password)
        client.send_message(message)

