"""Unit tests for the Sci-Hub ALTCHA verification and placeholder detection."""

import base64
import hashlib
import json

from scihub_cli.core.altcha_solver import (
    AltchaSolver,
    extract_challenge_url,
    is_altcha_challenge_html,
    solve_pow,
)
from scihub_cli.core.challenge_detection import is_placeholder_pdf

ARTICLE_PAGE = (
    '<html><body><altcha-widget challengeurl="/captcha/challenge/42"></altcha-widget>'
    '<a href="https://sci-hub.ru/storage/2024/1/abc/paper.pdf">pdf</a></body></html>'
)
CHALLENGE_PAGE = (
    "<html><head><title>Sci-Hub: rob&#244;?</title></head><body>"
    '<altcha-widget challengeurl="/captcha/challenge/42"></altcha-widget></body></html>'
)


def test_article_page_is_not_a_challenge_even_though_widget_is_embedded():
    assert is_altcha_challenge_html(ARTICLE_PAGE) is False


def test_challenge_page_detected_and_id_extracted():
    assert is_altcha_challenge_html(CHALLENGE_PAGE) is True
    challenge_id, url = extract_challenge_url(CHALLENGE_PAGE, "https://sci-hub.ru/10.1/x")
    assert challenge_id == "42"
    assert url == "https://sci-hub.ru/captcha/challenge/42"


def test_solve_pow_finds_the_number_and_rejects_other_algorithms():
    salt, number = "abc?expires=1&", 1234
    challenge = {
        "algorithm": "SHA-256",
        "salt": salt,
        "challenge": hashlib.sha256(f"{salt}{number}".encode()).hexdigest(),
        "maxNumber": 5000,
    }
    assert solve_pow(challenge) == number
    assert solve_pow({"algorithm": "SHA-512", "maxNumber": 10}) is None
    assert (
        solve_pow({"algorithm": "SHA-256", "salt": "x", "challenge": "dead", "maxNumber": 5})
        is None
    )


class _Response:
    def __init__(self, *, status_code=200, text="", url="https://sci-hub.ru/"):
        self.status_code = status_code
        self.text = text
        self.url = url


class _Session:
    """Minimal requests.Session double: records requests, replays canned bodies."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.posts = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.pages[url]

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        return _Response(text='{"success":true}')


def test_verify_posts_base64_payload_and_clears_challenge():
    salt, number = "s?x&", 7
    challenge_body = json.dumps(
        {
            "algorithm": "SHA-256",
            "salt": salt,
            "challenge": hashlib.sha256(f"{salt}{number}".encode()).hexdigest(),
            "signature": "sig",
            "maxNumber": 100,
        }
    )
    pages = {
        "https://sci-hub.ru/captcha/challenge/42": _Response(text=challenge_body),
    }
    session = _Session(pages)
    solver = AltchaSolver(session, timeout=1)

    assert solver.verify("https://sci-hub.ru/10.1/x", html=CHALLENGE_PAGE) is True

    url, body = session.posts[0]
    assert url == "https://sci-hub.ru/captcha/solution/42"
    assert set(body) == {"captcha"}
    decoded = json.loads(base64.b64decode(body["captcha"]))
    assert decoded["number"] == number
    assert decoded["challenge"] == hashlib.sha256(f"{salt}{number}".encode()).hexdigest()


def test_verify_returns_true_without_solving_for_a_plain_article_page():
    session = _Session({})
    assert AltchaSolver(session, timeout=1).verify("https://sci-hub.ru/10.1/x", html=ARTICLE_PAGE)
    assert session.posts == []


def test_placeholder_pdf_detected_by_digest(tmp_path):
    """The brochure is identified by size+SHA-256, not by a PDF header alone."""
    brochure = tmp_path / "brochure.pdf"
    body = b"%PDF-1.4" + b"\x00" * (608728 - 8)
    brochure.write_bytes(body)
    import scihub_cli.core.challenge_detection as detection

    monkeypatched = hashlib.sha256(body).hexdigest()
    original = detection._PLACEHOLDER_PDF_SHA256
    detection._PLACEHOLDER_PDF_SHA256 = monkeypatched
    try:
        assert is_placeholder_pdf(str(brochure)) is True
    finally:
        detection._PLACEHOLDER_PDF_SHA256 = original

    article = tmp_path / "article.pdf"
    article.write_bytes(b"%PDF-1.4" + b"\x01" * 1000)
    assert is_placeholder_pdf(str(article)) is False
    assert is_placeholder_pdf(str(tmp_path / "missing.pdf")) is False
