"""Render client-side HTML slide decks (remark.js / reveal.js / impress.js) to PDF.

Some course "slides" are not downloadable files but JavaScript slideshows that
build the deck in the browser from Markdown/HTML (e.g. keysan.me's remark.js
decks). There is nothing to download directly, so we open the deck in headless
Chromium (Playwright) and print it to PDF -- the same "open in Chrome, print to
PDF" path the slide authors themselves recommend -- producing one PDF per deck,
one slide per page, with selectable text.

A page is only accepted if it actually looks like one of the supported slideshow
frameworks, so arbitrary HTML pages are never saved as bogus "slide" PDFs.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - import guard
    sync_playwright = None  # type: ignore

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidefetch/1.0"
)

# Substrings that identify a client-side slideshow framework in the page source.
# Used both to recognise candidate decks and to refuse non-slideshow HTML pages.
_FRAMEWORK_MARKERS = {
    "remark": ("remark.create", "remarkjs.com", "remark-latest", "remark.min",
               "remark-slide"),
    "reveal": ("reveal.js", "reveal.min.js", "reveal.initialize", 'class="reveal"',
               "class='reveal'"),
    "impress": ("impress().init", "impress.js", 'id="impress"', "id='impress'"),
}

# Element that exists once per rendered slide, per framework. Waited on so the
# deck is fully built before printing.
_SLIDE_SELECTOR = {
    "remark": ".remark-slide-container",
    "reveal": ".reveal .slides section",
    "impress": "#impress .step",
}

_PDF_MAGIC = b"%PDF"


def detect_framework(html: str) -> str | None:
    """Return the slideshow framework name if *html* is a known deck, else None."""
    low = html.lower()
    for name, markers in _FRAMEWORK_MARKERS.items():
        if any(m.lower() in low for m in markers):
            return name
    return None


def looks_like_slideshow(html: str) -> bool:
    """True if *html* appears to be a supported client-side slide deck."""
    return detect_framework(html) is not None


def render_slides_to_pdf(
    url: str,
    out_path: Path,
    timeout_ms: int = 60000,
    settle_ms: int = 3000,
) -> tuple[bool, str]:
    """Render a client-side HTML slide deck at *url* to a PDF at *out_path*.

    Returns ``(ok, error)``. ``ok`` is False (with a reason) when Playwright is
    unavailable, the page is not a recognised slideshow, or printing produced no
    valid PDF. Never raises for an ordinary load/render failure.
    """
    if sync_playwright is None:
        return False, "playwright is not installed (pip install -r requirements.txt)"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_USER_AGENT)
            page = context.new_page()
            try:
                # domcontentloaded (not "load"): these decks embed slow third-party
                # media (YouTube, remote images) and often never fire the full
                # load event, but remark/reveal build the deck as soon as the DOM
                # is parsed. Media is then given time by the waits below.
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                framework = detect_framework(page.content())
                if framework is None:
                    return False, "not a recognized HTML slideshow"

                # reveal.js renders a print-friendly, one-section-per-page layout
                # only when asked via the ?print-pdf query flag.
                if framework == "reveal" and "print-pdf" not in url:
                    sep = "&" if "?" in url else "?"
                    page.goto(f"{url}{sep}print-pdf", timeout=timeout_ms,
                              wait_until="domcontentloaded")

                # Best-effort settle for late images, web fonts and MathJax; these
                # media-heavy decks rarely reach networkidle, so the wait is
                # capped and failure is non-fatal.
                try:
                    page.wait_for_load_state(
                        "networkidle", timeout=min(timeout_ms, 15000)
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    page.wait_for_selector(
                        _SLIDE_SELECTOR.get(framework, "body"),
                        timeout=min(timeout_ms, 20000),
                    )
                except Exception:  # noqa: BLE001
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)

                # Print to PDF. preferCSSPageSize honors the deck's own @page /
                # slide size (e.g. remark's native 4:3), so each slide fills a
                # page; landscape + no margins + backgrounds match the on-screen
                # look.
                page.pdf(
                    path=str(out_path),
                    landscape=True,
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                )
            finally:
                context.close()
                browser.close()
    except Exception as err:  # noqa: BLE001
        return False, str(err)

    if not out_path.exists() or out_path.stat().st_size == 0:
        return False, "no PDF produced"
    with out_path.open("rb") as fh:
        if not fh.read(8).lstrip().startswith(_PDF_MAGIC):
            try:
                out_path.unlink()
            except OSError:
                pass
            return False, "rendered output is not a valid PDF"
    return True, ""
