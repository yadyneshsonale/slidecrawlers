"""Find slide-deck links (PDF/PPT/PPTX) directly on a page.

Keeps real lecture slides and skips notes/homework/solutions/exams. Scans both
the rendered and raw DOM so JS-injected links are captured.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

SLIDE_EXTS = (".pdf", ".ppt", ".pptx")
# Client-side slideshow pages (remark.js / reveal.js / impress.js). They are not
# downloadable files; cli renders them to PDF. Accepted only with a slide signal
# (slide/lecture/presentation directory or filename) so ordinary HTML pages
# (home, schedule, syllabus) are not picked up.
HTML_EXTS = (".html", ".htm")

INCLUDE_TOKENS = (
    "slide", "slides", "ppt", "pptx", "presentation", "deck", "lecture",
    "lec", "week", "topic", "chapter", "module", "class", "session",
)
EXCLUDE_TOKENS = (
    "note", "notes", "reading", "readings", "handout", "scribe", "transcript",
    "homework", "hw", "pset", "problem-set", "problemset", "assignment",
    "solution", "solutions", "exam", "quiz", "syllabus", "textbook",
)

# Cloud storage services that may host slide decks
CLOUD_HOSTS = ("drive.google.com", "onedrive.live.com", "dropbox.com")

# Words that suggest a sub-page likely listing the actual slide decks. Used to
# follow one hop from a course homepage into its lectures/schedule index.
INDEX_TOKENS = (
    "lecture", "lectures", "slide", "slides", "schedule", "calendar",
    "materials", "resources", "class", "classes", "session",
    "sessions", "week", "weeks", "topics", "agenda", "lessons",
)

NUM_RE = re.compile(r"(?:lec(?:ture)?|week|wk|l|w)\s*[-_#]?\s*(\d{1,3})", re.IGNORECASE)
ANY_NUM_RE = re.compile(r"(\d{1,3})")

# Filenames that themselves name a lecture/slide deck, e.g. ``lect0.pdf``,
# ``lecture-3.pdf``, ``slides_07.pdf``, ``week2.pdf``. Lets such PDFs be
# accepted even when the link text is bare (e.g. just "pdf") and the page
# doesn't announce itself as a slide listing.
SLIDE_FILENAME_RE = re.compile(
    r"^(?:lec(?:t|ture)?|slides?|class|week|wk|topic|chapter|chap|module|"
    r"unit|session|day|part)[\s._-]*\d",
    re.IGNORECASE,
)

# A PDF whose containing directory is literally a lecture/slides folder
# (e.g. ``.../Lec/L01.pdf``, ``.../lectures/...``, ``.../slides/...``) is a
# lecture deck even when the link text is bare ("pdf") and the filename carries
# no word-boundary slide token (e.g. ``452L05F14_kurt.pdf``).
SLIDE_DIR_RE = re.compile(
    r"^(?:lec|lect|lecture|lectures|slide|slides|lecnotes|lecturenotes|"
    r"deck|decks|talk|talks|presentation|presentations)$",
    re.IGNORECASE,
)

# Strongly slide-specific keywords detected even when embedded in a camelCase
# filename (e.g. ``L01SlidesF14.pdf`` -> "Slides"), which the word-boundary
# token check above would otherwise miss.
SLIDE_KEYWORD_RE = re.compile(r"slides?|lecture|presentation", re.IGNORECASE)


@dataclass
class SlideLink:
    url: str
    text: str
    file_type: str          # pdf|ppt|pptx
    number: int | None
    reason: str
    name: str | None = None  # original filename (e.g. ``Lecture01.pdf``)


def _ext(url: str) -> str:
    path = urlparse(url).path.lower()
    for e in SLIDE_EXTS:
        if path.endswith(e):
            return e
    return ""


def _is_html_url(url: str) -> bool:
    """True if the URL points at an .html/.htm page (a candidate slideshow)."""
    return urlparse(url).path.lower().endswith(HTML_EXTS)


def _has_token(haystack: str, tokens) -> str | None:
    for t in tokens:
        if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", haystack):
            return t
    return None


def _in_slide_dir(href: str) -> bool:
    """True if the file sits in a lecture/slides directory (any path segment)."""
    segs = [s for s in urlparse(href).path.split("/")[:-1] if s]
    return any(SLIDE_DIR_RE.match(s) for s in segs)


def _name_from_url(href: str) -> str | None:
    """Return the original filename a URL points at (percent-decoded)."""
    fname = urlparse(href).path.rsplit("/", 1)[-1]
    fname = unquote(fname).strip()
    return fname or None


def _guess_number(text: str, href: str) -> int | None:
    for source in (text, href):
        m = NUM_RE.search(source)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 99:
                return n
    fname = urlparse(href).path.rsplit("/", 1)[-1]
    m = ANY_NUM_RE.search(fname)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 99:
            return n
    return None


def is_slide_link(text: str, href: str, page_is_slides: bool = False,
                  base_url: str | None = None) -> tuple[bool, str]:
    ext = _ext(href)
    fname = urlparse(href).path.rsplit("/", 1)[-1]
    
    # Only check text and filename for exclusions, not the full URL path
    # (to avoid excluding slides just because the URL contains /notes/)
    hay_text = text.lower()
    hay_fname = fname.lower()
    
    bad = _has_token(hay_text, EXCLUDE_TOKENS) or _has_token(hay_fname, EXCLUDE_TOKENS)
    if bad:
        return False, f"excluded:{bad}"

    if ext in (".ppt", ".pptx"):
        return True, f"ext:{ext}"

    # For PDFs, check link text and page context
    hay_include = f"{text} {fname}".lower()  # link text and filename only, not full URL
    good = _has_token(hay_include, INCLUDE_TOKENS)
    
    if ext == ".pdf":
        if good:
            return True, f"pdf+token:{good}"
        # A "Lecture Slides" page vouches only for its own same-site PDFs, so an
        # externally-linked reference/textbook isn't swept in as a slide deck.
        if page_is_slides and (base_url is None or _same_site(href, base_url)):
            return True, "pdf+slide-page"
        if SLIDE_FILENAME_RE.match(fname):
            return True, "pdf+slide-filename"
        if SLIDE_KEYWORD_RE.search(fname):
            return True, "pdf+slide-keyword"
        if _in_slide_dir(href):
            return True, "pdf+slide-dir"
        return False, "pdf-no-slide-token"

    # Client-side HTML slideshows (remark.js / reveal.js / impress.js). Only a
    # strong slide signal qualifies, because the deck is verified by rendering
    # later; this keeps home/schedule/syllabus .html pages out.
    if _is_html_url(href):
        if _in_slide_dir(href):
            return True, "html+slide-dir"
        if SLIDE_KEYWORD_RE.search(fname):
            return True, "html+slide-keyword"
        if SLIDE_FILENAME_RE.match(fname):
            return True, "html+slide-filename"
        if page_is_slides and good:
            return True, f"html+slide-page:{good}"
        return False, "html-no-slide-signal"

    # Check for cloud storage links (Google Drive, OneDrive, Dropbox)
    # if link text indicates it's slides
    netloc = urlparse(href).netloc.lower()
    for cloud_host in CLOUD_HOSTS:
        if cloud_host in netloc:
            good = _has_token(hay_text, INCLUDE_TOKENS)
            if good:
                return True, f"cloud+token:{good}"
            break
    
    return False, "not-a-slide-file"


TITLE_SLIDE_RE = re.compile(r"(?<![a-z])(slides?|lecture slides?|presentations?)(?![a-z])", re.IGNORECASE)


def _page_is_slides(html: str) -> bool:
    """True if the page's <title> or a heading announces it as a slide deck list.

    Lets topic-named PDFs (e.g. ``pdf/01StableMatching.pdf``) on a dedicated
    "Lecture Slides" page be accepted even without a per-link slide keyword.
    """
    soup = BeautifulSoup(html, "lxml")
    parts = []
    if soup.title and soup.title.string:
        parts.append(soup.title.string)
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        parts.append(h.get_text(" ", strip=True))
    blob = " ".join(parts)
    return bool(TITLE_SLIDE_RE.search(blob))


def _iter_links(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if not href.startswith(("http://", "https://")):
            continue
        yield href, a.get_text(" ", strip=True)


# Printable/handout layout suffixes on an otherwise-identical deck, e.g.
# "01StableMatching-2x2.pdf" (4-up handout) vs "01StableMatching.pdf".
HANDOUT_SUFFIX_RE = re.compile(
    r"[-_](?:\d+x\d+|\d+up|\d+perpage|\d+pp|handouts?|printable|print|bw|"
    r"grayscale|gray|notesheet)$",
    re.IGNORECASE,
)


def _base_stem(url: str) -> tuple[str, bool]:
    """Return (base-stem-without-handout-suffix, is_variant) for a file URL.

    ``.../pdf/01StableMatching-2x2.pdf`` -> ("/pdf/01stablematching", True)
    ``.../pdf/01StableMatching.pdf``     -> ("/pdf/01stablematching", False)
    """
    path = urlparse(url).path
    directory, _, fname = path.rpartition("/")
    stem = fname.rsplit(".", 1)[0]
    stripped = HANDOUT_SUFFIX_RE.sub("", stem)
    is_variant = stripped != stem
    return f"{directory}/{stripped}".lower(), is_variant


def _drop_handout_variants(links: list[SlideLink]) -> list[SlideLink]:
    """Drop N-up/handout variants when the full-size deck is also present."""
    full_bases = {
        _base_stem(l.url)[0]
        for l in links
        if not _base_stem(l.url)[1]
    }
    kept: list[SlideLink] = []
    for l in links:
        base, is_variant = _base_stem(l.url)
        if is_variant and base in full_bases:
            continue  # full-size version exists; skip the handout layout
        kept.append(l)
    return kept


def find_slides(html: str, base_url: str, raw_html: str | None = None) -> list[SlideLink]:
    out: list[SlideLink] = []
    seen: set[str] = set()
    sources = [html] + ([raw_html] if raw_html and raw_html != html else [])
    page_is_slides = any(_page_is_slides(s) for s in sources)

    for source in sources:
        for href, text in _iter_links(source, base_url):
            if href in seen:
                continue
            ok, reason = is_slide_link(
                text, href, page_is_slides=page_is_slides, base_url=base_url
            )
            if not ok:
                continue
            seen.add(href)
            ext = _ext(href)
            file_type = ext.lstrip(".") if ext else ("html" if _is_html_url(href) else "")
            out.append(
                SlideLink(
                    url=href,
                    text=text,
                    file_type=file_type,
                    number=_guess_number(text, href),
                    reason=reason,
                    name=_name_from_url(href),
                )
            )
    return _drop_handout_variants(out)


def _same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower(), urlparse(b).netloc.lower()
    ha = ha[4:] if ha.startswith("www.") else ha
    hb = hb[4:] if hb.startswith("www.") else hb
    return ha == hb


def find_index_links(html: str, base_url: str, raw_html: str | None = None) -> list[str]:
    """Same-site sub-pages likely to list slide decks (lectures/schedule/etc.).

    Returned in priority order: links whose text/url mention lecture/slide
    first, then schedule/calendar/materials. Used to follow one hop from a
    course homepage that has no slides directly on it.
    """
    base_path = urlparse(base_url).path.rstrip("/")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    sources = [html] + ([raw_html] if raw_html and raw_html != html else [])

    for source in sources:
        for href, text in _iter_links(source, base_url):
            target = href.split("#", 1)[0]
            key = target.rstrip("/")
            if key in seen:
                continue
            if not _same_site(target, base_url):
                continue
            if urlparse(target).path.rstrip("/") == base_path:
                continue  # same page (e.g. self / fragment link)
            if _ext(target):
                continue  # a file, not an index page
            hay = f"{text} {href}".lower()
            if _has_token(hay, EXCLUDE_TOKENS):
                continue
            tok = _has_token(hay, INDEX_TOKENS)
            if not tok:
                continue
            seen.add(key)
            rank = 0 if tok in ("lecture", "lectures", "slide", "slides") else 1
            scored.append((rank, target))

    scored.sort(key=lambda x: x[0])
    return [url for _, url in scored]

