"""Society lookups in a bounded, short-lived Firefox session.

Create and use each client in the same worker thread. Browser profiles, screenshots,
and traces are never persisted: they could contain staff identities.
"""
from __future__ import annotations

import threading
import time
from contextlib import ExitStack
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as BrowserError, sync_playwright

from .models import MemberRecord, StaffMember
from .scraper import BASE_URL, UpstreamError, parse_member_page

_BROWSER_SLOT = threading.BoundedSemaphore(1)
_LOOKUP_PATH = "/member-services/find-a-member"
_MEMBER_PATH = _LOOKUP_PATH + "/member-info"
_TRANSIENT = {429, 500, 502, 503, 504}


class FirefoxSocietyClient:
    def __init__(self, *, delay_seconds=1.1, timeout_seconds=30,
                 challenge_seconds=15, retries=2, sleep=time.sleep):
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.challenge_seconds = challenge_seconds
        self.retries = retries
        self.sleep = sleep
        self._stack = None
        self._page = None
        self._response = None
        self._has_requested = False

    def __enter__(self):
        if self._stack is not None:
            raise RuntimeError("Firefox client is already open.")
        if not _BROWSER_SLOT.acquire(timeout=2):
            raise UpstreamError("A Society browser lookup is already running. Try again when it finishes.")
        stack = ExitStack()
        stack.callback(_BROWSER_SLOT.release)
        try:
            playwright = stack.enter_context(sync_playwright())
            browser = playwright.firefox.launch(headless=True, timeout=self.timeout_seconds * 1000)
            stack.callback(browser.close)
            context = browser.new_context()
            stack.callback(context.close)
            context.route("**/*", self._route)
            self._page = context.new_page()
            self._page.on("response", self._navigation_response)
            self._stack = stack
            return self
        except BrowserError:
            stack.close()
            raise UpstreamError(
                "Unable to start Firefox for Society lookups. "
                "Install the matching browser with python -m playwright install --with-deps firefox."
            ) from None
        except BaseException:
            stack.close()
            raise

    def __exit__(self, *exc):
        try:
            return self._stack.__exit__(*exc)
        finally:
            self._stack = None
            self._page = None

    @staticmethod
    def _route(route):
        host = urlsplit(route.request.url).hostname
        if route.request.resource_type in {"image", "font", "media"} or host in {
            "www.googletagmanager.com", "beacon-v2.helpscout.net",
        }:
            route.abort()
        else:
            route.continue_()

    def _navigation_response(self, response):
        if response.request.is_navigation_request() and response.frame == self._page.main_frame:
            self._response = response

    def _challenged(self):
        return (self._response is not None and
                self._response.headers.get("cf-mitigated", "").lower() == "challenge") or (
                    self._page.title() == "Just a moment...")

    def fetch(self, staff: StaffMember) -> MemberRecord:
        html = self.fetch_html(staff.member_code)
        if html is None:
            return MemberRecord(configured_name=staff.name, member_code=staff.member_code,
                                away=staff.away, error="Member ID was not found.")
        return parse_member_page(html, staff)

    def fetch_html(self, member_code: str) -> str | None:
        """Return an identity page, or None for a confirmed missing member."""
        if self._page is None:
            raise RuntimeError("Use FirefoxSocietyClient as a context manager.")
        if self._has_requested:
            self.sleep(self.delay_seconds)
        self._has_requested = True
        url = BASE_URL + quote(member_code, safe="")
        for attempt in range(self.retries + 1):
            try:
                self._response = None
                self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_seconds * 1000)
                deadline = time.monotonic() + self.challenge_seconds
                while self._challenged() and time.monotonic() < deadline:
                    self._page.wait_for_timeout(250)
                if self._challenged():
                    ray = self._response.headers.get("cf-ray") if self._response else None
                    reference = f" Cloudflare Ray ID: {ray}." if ray else ""
                    raise UpstreamError(
                        "The Society website still requires Cloudflare browser verification in Firefox. "
                        "Contact the Society to arrange approved automated access." + reference
                    )
                response = self._response
                if response is None:
                    raise UpstreamError("Firefox Society lookup returned no response.")
                if response.status in _TRANSIENT:
                    if attempt == self.retries:
                        raise UpstreamError(f"Society lookup failed after retries (HTTP {response.status}).")
                    from .redcross import _retry_wait

                    self.sleep(_retry_wait(response.headers.get("retry-after"), attempt))
                    continue
                if response.status != 200:
                    raise UpstreamError(f"Society lookup returned HTTP {response.status}.")
                target = urlsplit(self._page.url)
                if target.scheme != "https" or target.netloc != "www.lifesaving.org":
                    raise UpstreamError("The Society lookup redirected unexpectedly.")
                if target.path.rstrip("/") == _LOOKUP_PATH:
                    if "Invalid Member Id." not in self._page.locator("body").inner_text():
                        raise UpstreamError("The Society lookup redirected without a member result.")
                    return None
                if target.path.rstrip("/") != _MEMBER_PATH:
                    raise UpstreamError("The Society lookup redirected unexpectedly.")
                return self._page.content()
            except BrowserError:
                if attempt == self.retries:
                    # Browser exception messages can include the member ID in a URL.
                    raise UpstreamError("Unable to complete the Society lookup in Firefox.") from None
                self.sleep(2**attempt)
        raise AssertionError("Unreachable retry state")
