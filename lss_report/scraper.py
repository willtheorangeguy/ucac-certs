from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Tag

from .awards import certification_date_from_expiry, columns_for, expiry_for
from .models import Certification, MemberRecord, StaffMember

BASE_URL = "https://www.lifesaving.org/member-services/find-a-member/member-info?member="
DATE_FORMAT = "%b %d, %Y"


class UpstreamError(RuntimeError):
    pass


class ParseError(UpstreamError):
    pass


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[^\w\s'-]", "", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())


def _parse_date(raw: str) -> datetime.date:
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except ValueError as exc:
        raise ParseError("The Society returned an unrecognized date.") from exc


def _value_for_label(container: Tag, label: str) -> str | None:
    for title in container.select("span.title"):
        if title.get_text(" ", strip=True).casefold() == label.casefold():
            sibling = title.find_next_sibling(class_="value")
            return sibling.get_text(" ", strip=True) if sibling else None
    return None


def parse_member_page(html: str, staff: StaffMember) -> MemberRecord:
    soup = BeautifulSoup(html, "html.parser")
    details = soup.select_one(".member-info__details")
    if details is None:
        raise ParseError("The Society member page structure was not recognized.")

    source_name_node = details.select_one(".member-name .value")
    source_code_node = details.select_one(".member-id .value")
    if source_name_node is None or source_code_node is None:
        raise ParseError("The Society member identity fields were not found.")
    source_name = source_name_node.get_text(" ", strip=True)
    source_code = source_code_node.get_text(" ", strip=True).upper()
    if source_code != staff.member_code:
        raise ParseError("The Society returned a different Member ID.")

    record = MemberRecord(
        configured_name=staff.name,
        member_code=staff.member_code,
        source_name=source_name,
        away=staff.away,
    )
    if normalize_name(source_name) != normalize_name(staff.name):
        # The Member ID already matched, so this is a spelling difference in the
        # roster rather than the wrong person. Flag it, but keep the awards.
        record.name_warning = (
            f"Roster name {staff.name!r} differs from the Society record {source_name!r}."
        )

    cards = soup.select(".member-info__cards > .member-info__card[data-target]")
    for card in cards:
        title = card.select_one(".card--title")
        if title is None:
            raise ParseError("A certification card is missing its title.")
        award_name = title.get_text(" ", strip=True)
        site_current = "card--expired" not in card.get("class", [])
        columns = columns_for(award_name)

        # Current cards carry a certification date; some expired cards carry only the
        # expiry, in which case work backwards so every column derives alike.
        date_text = _value_for_label(card, "Certification Date")
        expired_text = _value_for_label(card, "Expired On")
        expired_on: datetime.date | None = None
        if date_text is not None:
            certification_date = _parse_date(date_text)
        elif expired_text is not None:
            expired_on = _parse_date(expired_text)
            # Only a placeholder, for the untracked-award branch below and for display.
            # Each tracked column recovers its own date from its own validity period.
            certification_date = expired_on
        else:
            raise ParseError("A certification card is missing its dates.")
        if not columns:
            # None means unrecognised (surfaced in diagnostics); () means recognised
            # but untracked. Keep both so the detail pages can still show them.
            record.certifications.append(
                Certification(
                    name=award_name,
                    certification_date=certification_date,
                    site_current=site_current,
                )
            )
            continue
        for column, provisional in columns:
            if expired_on is None:
                column_date = certification_date
                column_expiry = expiry_for(column, column_date)
            else:
                # The card published the expiry, so take it as given for this column
                # rather than round-tripping it. One award can feed two columns with
                # different validity periods — a combined first aid and CPR-C award
                # runs 2 years and 1 year — and deriving a single certification date
                # from the first column would put the other column's expiry out by the
                # difference between them.
                column_expiry = expired_on
                column_date = certification_date_from_expiry(column, expired_on)
            record.certifications.append(
                Certification(
                    name=award_name,
                    certification_date=column_date,
                    column=column,
                    expiry_date=column_expiry,
                    site_current=site_current,
                    provisional=provisional,
                )
            )
    return record


class SocietyClient:
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

    def fetch(self, staff: StaffMember) -> MemberRecord:
        if self._has_requested:
            self.sleep(self.delay_seconds)
        self._has_requested = True
        url = BASE_URL + quote(staff.member_code, safe="")

        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise UpstreamError("Unable to contact the Society website.") from exc
                self.sleep(2**attempt)
                continue

            if response.status_code == 302:
                location = response.headers.get("Location", "")
                if not location.rstrip("/").endswith("/member-services/find-a-member"):
                    raise UpstreamError("The Society lookup redirected unexpectedly.")
                return MemberRecord(
                    configured_name=staff.name,
                    member_code=staff.member_code,
                    error="Member ID was not found.",
                )
            if response.status_code in {429, 500, 502, 503, 504}:
                if attempt == self.retries:
                    raise UpstreamError(
                        f"Society lookup failed after retries (HTTP {response.status_code})."
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = max(float(retry_after), 2**attempt) if retry_after else 2**attempt
                except ValueError:
                    try:
                        retry_date = parsedate_to_datetime(retry_after)
                        wait = max((retry_date - datetime.now(retry_date.tzinfo)).total_seconds(), 1)
                    except (TypeError, ValueError):
                        wait = 2**attempt
                self.sleep(wait)
                continue
            if response.status_code != 200:
                raise UpstreamError(f"Society lookup returned HTTP {response.status_code}.")
            return parse_member_page(response.text, staff)

        raise AssertionError("Unreachable retry state")
