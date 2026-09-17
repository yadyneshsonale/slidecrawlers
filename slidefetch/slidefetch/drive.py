"""Enumerate public Google Drive folders into downloadable file links.

A Drive folder (``drive.google.com/drive/folders/<id>``) is a JavaScript app,
so its file list isn't present as ``<a href>`` links in the static HTML. We
render it with Playwright and read each file's ``data-id`` (the Drive file id)
and ``aria-label`` (its name), then build a direct-download URL per file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - import guard
    sync_playwright = None  # type: ignore

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidefetch/1.0"
)

# ``/drive/folders/<id>``, ``/drive/u/0/folders/<id>``, ``/folders/<id>`` ...
_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")

# Document extensions slidefetch cares about, used to keep only file rows
# (skip sub-folders) and to recover the bare filename from its aria-label.
_DOC_EXT = r"pdf|pptx|ppt|docx|doc|key|odp"
_NAME_RE = re.compile(rf"^(.*?\.(?:{_DOC_EXT}))(?:\s|$)", re.IGNORECASE)
_EXT_RE = re.compile(rf"\.(?:{_DOC_EXT})$", re.IGNORECASE)

# Runs in the page: pair every filename-like aria-label with its file id
# (the closest ancestor carrying ``data-id``).
_EXTRACT_JS = r"""
els => {
    const re = /\.(pdf|pptx|ppt|docx|doc|key|odp)(\s|$)/i;
    const out = [];
    for (const e of els) {
        const lbl = e.getAttribute('aria-label') || '';
        if (!re.test(lbl)) continue;
        const c = e.closest('[data-id]');
        if (!c) continue;
        out.push([c.getAttribute('data-id'), lbl]);
    }
    return out;
}
"""


@dataclass
class DriveFile:
    file_id: str
    name: str          # original filename, e.g. ``1-Course Overview.pdf``

    @property
    def download_url(self) -> str:
        return f"https://drive.google.com/uc?export=download&id={self.file_id}"

    @property
    def file_type(self) -> str:
        m = _EXT_RE.search(self.name)
        return m.group(0).lstrip(".").lower() if m else "pdf"


def is_drive_folder(url: str) -> bool:
    """True if *url* points at a Google Drive folder."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host == "drive.google.com" and bool(_FOLDER_RE.search(parts.path))


def folder_id(url: str) -> str | None:
    m = _FOLDER_RE.search(urlsplit(url).path)
    return m.group(1) if m else None


def _clean_name(label: str) -> str | None:
    """Recover the bare filename from a Drive row aria-label.

    ``"1-Course Overview.pdf PDF Shared"`` -> ``"1-Course Overview.pdf"``.
    """
    m = _NAME_RE.match(label.strip())
    return m.group(1).strip() if m else None


def enumerate_folder(url: str, timeout_ms: int = 45000,
                     settle_ms: int = 4500) -> list[DriveFile]:
    """Render a public Drive folder and return its document files in order."""
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed (pip install -r requirements.txt)")

    own_id = folder_id(url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=_USER_AGENT)
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            pairs = page.eval_on_selector_all("[aria-label]", _EXTRACT_JS)
        finally:
            context.close()
            browser.close()

    seen: set[str] = set()
    files: list[DriveFile] = []
    for file_id, label in pairs:
        if not file_id or file_id == own_id or file_id in seen:
            continue
        name = _clean_name(label or "")
        if not name:
            continue
        seen.add(file_id)
        files.append(DriveFile(file_id=file_id, name=name))
    return files
