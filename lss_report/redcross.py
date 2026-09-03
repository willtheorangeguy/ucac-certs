"""Canadian Red Cross certificate validation.

The Red Cross publishes one certificate at a time, keyed by certificate number and
the holder's last name, and reports only an expiry date. The Society publishes a
certification date instead, so the two sources are reconciled here: the published
expiry is worked back to a certification date using the Red Cross validity period,
and each grid column then applies its own — which is what keeps the Aquatic Centre's
shorter first aid policy in force against a Red Cross card.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .awards import add_years, columns_for
from .models import Certification
from .scraper import ParseError, UpstreamError

BASE_URL = "https://myrc.redcross.ca/en/ValidateCertificate/"

# Red Cross first aid and CPR certificates run three years from the course date.
# Only the expiry is published, so this is what recovers the certification date;
# the grid columns then expire it on the Aquatic Centre's own schedule.
ISSUER_VALIDITY_YEARS = 3

# The validator renders its answer into a single paragraph, e.g. "The Certificate
# with Certificate ID 103575156 is Valid for Standard First Aid CPR/AED Level C
# (Blended) and the Expiry Date is 2025-10-14". Non-breaking spaces separate the
# fields, so the text is normalised before matching.
_RESULT = re.compile(
    r"certificate id\s+(?P<number>\S+)\s+is\s+(?P<state>.+?)"
    r"\s+for\s+(?P<award>.+?)"
    r"\s+and the expiry date is\s+(?P<expiry>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedCrossCertificate:
    certificate_number: str
    award_name: str
    expiry_date: date
    valid: bool = True

    @property
    def certification_date(self) -> date:
        return add_years(self.expiry_date, -ISSUER_VALIDITY_YEARS)


def last_name_for(name: str) -> str:
    """The surname the validator keys off.

    Certificate holders with a single name have it stored in the last name field, so
    taking the final word covers both cases.
    """
    parts = name.split()
    return parts[-1] if parts else name.strip()


def parse_validation(html: str, certificate_number: str) -> RedCrossCertificate | None:
    """``None`` when the pair is not a certificate the Red Cross knows about."""
    soup = BeautifulSoup(html, "html.parser")
    paragraph = soup.select_one("p.certificate-error")
    if paragraph is None:
        raise ParseError("The Red Cross validator page structure was not recognized.")
    text = " ".join(paragraph.get_text(" ", strip=True).replace("\xa0", " ").split())
    if "no certificate found" in text.casefold():
        return None
    match = _RESULT.search(text)
    if match is None:
        raise ParseError("The Red Cross validator returned an unrecognized result.")
    if match.group("number") != certificate_number:
        raise ParseError("The Red Cross validator returned a different certificate number.")
    return RedCrossCertificate(
        certificate_number=match.group("number"),
        award_name=match.group("award").strip(),
        expiry_date=date.fromisoformat(match.group("expiry")),
        valid=match.group("state").strip().casefold() == "valid",
    )


def certifications_from(certificate: RedCrossCertificate) -> list[Certification]:
    """Grid certifications for one validated Red Cross certificate.

    The published expiry belongs to the Red Cross, not to the grid: a Standard First
    Aid card runs three years there and two here. Each column therefore derives its
    own expiry from the recovered certification date, the same way an award that
    feeds two columns with different validity periods is handled for the Society.
    """
    certification_date = certificate.certification_date
    columns = columns_for(certificate.award_name)
    if not columns:
        # None means the title matched no rule and belongs in diagnostics; () means
        # it is recognised but not tracked. Keep both, untracked, as the Society
        # awards are kept.
        return [
            Certification(
                name=certificate.award_name,
                certification_date=certification_date,
                site_current=certificate.valid,
            )
        ]
    return [
        Certification(
            name=certificate.award_name,
            certification_date=certification_date,
            column=column,
            expiry_date=add_years(certification_date, column.validity_years),
            site_current=certificate.valid,
            provisional=provisional,
        )
        for column, provisional in columns
    ]


class RedCrossClient:
    """One certificate lookup at a time, paced and retried like the Society client."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        delay_seconds: float = 1.1,
        retries: int = 2,
        timeout_seconds: float = 20,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session or requests.Session()
        self.delay_seconds = delay_seconds
        self.retries = retries
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self._has_requested = False
        self.session.headers.update(
            {"User-Agent": "PoolCertificationReport/0.1 (authorized low-volume verification)"}
        )

    def fetch(self, last_name: str, certificate_number: str) -> RedCrossCertificate | None:
        certificate_number = certificate_number.strip()
        if not certificate_number.isdigit():
            # The validator's own form refuses anything else, so this never reaches it.
            raise UpstreamError("A Red Cross certificate number must be digits only.")
        if self._has_requested:
            self.sleep(self.delay_seconds)
        self._has_requested = True
        url = BASE_URL + "?" + urlencode({"ln": last_name.strip(), "cn": certificate_number})

        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise UpstreamError("Unable to contact the Red Cross validator.") from exc
                self.sleep(2**attempt)
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt == self.retries:
                    raise UpstreamError(
                        "Red Cross lookup failed after retries "
                        f"(HTTP {response.status_code})."
                    )
                self.sleep(_retry_wait(response.headers.get("Retry-After"), attempt))
                continue
            if response.status_code != 200:
                raise UpstreamError(
                    f"Red Cross lookup returned HTTP {response.status_code}."
                )
            return parse_validation(response.text, certificate_number)

        raise AssertionError("Unreachable retry state")


def _retry_wait(retry_after: str | None, attempt: int) -> float:
    backoff = float(2**attempt)
    if not retry_after:
        return backoff
    try:
        return max(float(retry_after), backoff)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        retry_date = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return backoff
    return max((retry_date - datetime.now(retry_date.tzinfo)).total_seconds(), 1.0)
