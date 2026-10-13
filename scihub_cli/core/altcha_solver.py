"""Sci-Hub human-verification (ALTCHA) handling.

Sci-Hub guards article pages with an ALTCHA proof-of-work widget. ALTCHA is not
a human CAPTCHA: the widget computes an HMAC-SHA256 proof itself, so the same
computation can be done server-side with a cookie-bearing session.

Flow on a mirror:
  1. GET /<doi>                     -> challenge page carrying the widget
  2. GET /captcha/challenge/<id>    -> {"algorithm","challenge","maxNumber","salt","signature"}
  3. Solve: find n <= maxNumber with sha256(salt + str(n)) == challenge
  4. POST /captcha/solution/<id>    -> {"captcha": "<base64 of {challenge..., number, took}>"}
  5. GET /<doi> again               -> article page with a /storage/ PDF link

Article pages embed the same widget markup, so a page counts as a challenge
only when it carries the widget *and* exposes no PDF link.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from urllib.parse import urljoin

import requests

from ..utils.logging import get_logger

logger = get_logger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CHALLENGE_URL_RE = re.compile(r"challengeurl\s*=\s*[\"']([^\"']+)[\"']")
_CHALLENGE_ID_RE = re.compile(r"/captcha/challenge/(\d+)")
_PDF_LINK_RE = re.compile(r"/(?:storage|downloads?)/[^\"'\s<>]+\.pdf", re.I)
_WIDGET_MARKERS = ("altcha-widget", "captcha/challenge")
# ALTCHA's own default; mirrors are interchangeable, this is a last-resort base.
_FALLBACK_BASE = "https://sci-hub.ru"
_POW_TIME_SLICE = 4096  # iterations between clock checks


def is_altcha_challenge_html(html: str) -> bool:
    """True for a verification page: the ALTCHA widget with no PDF link on it."""
    lowered = (html or "").lower()
    if not any(marker in lowered for marker in _WIDGET_MARKERS):
        return False
    return _PDF_LINK_RE.search(html or "") is None


def extract_challenge_url(html: str, page_url: str) -> tuple[str | None, str]:
    """Return (challenge_id, absolute challenge URL) from a verification page."""
    match = _CHALLENGE_URL_RE.search(html or "")
    if not match:
        return None, ""
    path = match.group(1)
    url = path if path.startswith("http") else urljoin(page_url or _FALLBACK_BASE, path)
    id_match = _CHALLENGE_ID_RE.search(url)
    return (id_match.group(1) if id_match else None), url


def solve_pow(challenge: dict, max_time: float = 5.0) -> int | None:
    """Solve the ALTCHA SHA-256 proof-of-work; None when unsolved within budget."""
    if not challenge or challenge.get("algorithm") != "SHA-256":
        return None
    salt = challenge.get("salt", "")
    target = challenge.get("challenge", "")
    start = time.monotonic()
    for number in range(int(challenge.get("maxNumber", 0)) + 1):
        if number % _POW_TIME_SLICE == 0 and time.monotonic() - start > max_time:
            return None
        if hashlib.sha256(f"{salt}{number}".encode()).hexdigest() == target:
            return number
    return None


class AltchaSolver:
    """Solves a mirror's ALTCHA challenge with a requests.Session cookie jar."""

    def __init__(self, session: requests.Session, timeout: float = 30.0):
        self.session = session
        self.timeout = timeout

    def _get(self, url: str) -> requests.Response:
        return self.session.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": _BROWSER_UA, "Referer": urljoin(url, "/")},
            proxies={"http": None, "https": None},
        )

    def verify(self, article_url: str, html: str | None = None) -> bool:
        """Clear the verification for ``article_url``; True when it is answered.

        ``html`` lets a caller that already fetched the page skip a request.
        Returns True when the page is not a challenge (nothing to solve) or when
        the challenge was solved and accepted.
        """
        page = html if html is not None else (self._get(article_url).text or "")
        if not is_altcha_challenge_html(page):
            return True

        challenge_id, challenge_url = extract_challenge_url(page, article_url)
        if not challenge_id:
            logger.debug("[ALTCHA] verification page without a challenge URL")
            return False
        try:
            challenge = json.loads(self._get(challenge_url).text)
        except (ValueError, requests.RequestException) as exc:
            logger.warning(f"[ALTCHA] challenge {challenge_id} not readable: {exc}")
            return False

        number = solve_pow(challenge)
        if number is None:
            logger.warning(f"[ALTCHA] proof-of-work unsolved for challenge {challenge_id}")
            return False

        payload = {
            "algorithm": challenge["algorithm"],
            "challenge": challenge["challenge"],
            "number": number,
            "salt": challenge["salt"],
            "signature": challenge["signature"],
            "took": 100,
        }
        # The widget sends btoa(JSON.stringify(payload)); a JSON object is rejected
        # by the endpoint with HTTP 500.
        captcha = base64.b64encode(json.dumps(payload).encode()).decode()
        origin = urljoin(article_url, "/")
        try:
            response = self.session.post(
                f"{origin}captcha/solution/{challenge_id}",
                json={"captcha": captcha},
                timeout=self.timeout,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Content-Type": "application/json",
                    "Origin": origin.rstrip("/"),
                    "Referer": article_url,
                },
                proxies={"http": None, "https": None},
            )
        except requests.RequestException as exc:
            logger.warning(f"[ALTCHA] solution POST failed for {challenge_id}: {exc}")
            return False
        if response.status_code != 200:
            logger.warning(f"[ALTCHA] solution POST {challenge_id} -> HTTP {response.status_code}")
            return False
        return True
