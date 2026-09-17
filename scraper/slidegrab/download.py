"""Download slide-deck candidates with magic-byte verification.

Uses Playwright's request API (shared User-Agent / TLS), writes files to the
dataset layout `dataset/<university>/<course>/lecture_NN.<ext>`, and refuses to
persist HTML error/login pages that masquerade as documents.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from config.settings import settings

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"           # pptx/docx (Office Open XML)
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"     # legacy ppt/doc (OLE compound file)
_HTML_SNIFF = (b"<!doctype html", b"<html", b"<head", b"<body", b"<!--")


def slug(value: str, maxlen: int = 80) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._-")
    return value[:maxlen].strip("._-") or "unknown"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DownloadResult:
    url: str
    file_path: str | None
    sha256: str | None
    file_type: str
    ok: bool
    skipped: bool = False
    error: str = ""


def _content_matches(file_type: str, body: bytes, content_type: str) -> str:
    head = body[:1024].lstrip()
    lower = head.lower()
    if "text/html" in content_type.lower() or lower.startswith(_HTML_SNIFF):
        return "html response, not a file"
    if file_type == "pdf":
        if not body[:8].lstrip().startswith(_PDF_MAGIC):
            return "not a pdf (bad magic bytes)"
    elif file_type in ("pptx", "docx", "xlsx"):
        if not body.startswith(_ZIP_MAGIC):
            return f"not a {file_type} (bad magic bytes)"
    elif file_type in ("ppt", "doc", "xls"):
        if not body.startswith(_OLE_MAGIC):
            return f"not a {file_type} (bad magic bytes)"
    return ""


def target_path(university: str, course: str, lecture_number: int | None,
                url: str, index: int) -> Path:
    ext = Path(urlparse(url).path).suffix.lower() or ".pdf"
    course_dir = settings.data_dir / slug(university) / slug(course)
    if lecture_number is not None:
        name = f"lecture_{lecture_number:02d}{ext}"
    else:
        name = f"deck_{index:03d}{ext}"
    return course_dir / name


class Downloader:
    """Downloads files through a Playwright request context."""

    def __init__(self, cfg=settings.fetch) -> None:
        self.cfg = cfg
        self._pw = None
        self._request = None

    async def __aenter__(self) -> "Downloader":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._request = await self._pw.request.new_context(user_agent=self.cfg.user_agent)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._request:
            await self._request.dispose()
        if self._pw:
            await self._pw.stop()

    async def download(
        self,
        url: str,
        university: str,
        course: str,
        lecture_number: int | None = None,
        index: int = 0,
    ) -> DownloadResult:
        ext = Path(urlparse(url).path).suffix.lower().lstrip(".") or "pdf"
        path = target_path(university, course, lecture_number, url, index)

        # Dedup against already-downloaded files (retain existing dataset).
        if path.exists() and path.stat().st_size > 0:
            return DownloadResult(
                url, str(path), None, ext, True, skipped=True, error="exists"
            )
        try:
            resp = await self._request.get(url, timeout=self.cfg.download_timeout_ms)
            if not resp.ok:
                return DownloadResult(url, None, None, ext, False, error=f"http {resp.status}")
            body = await resp.body()
            if not body:
                return DownloadResult(url, None, None, ext, False, error="empty body")
            content_type = resp.headers.get("content-type", "")
            mismatch = _content_matches(ext, body, content_type)
            if mismatch:
                return DownloadResult(url, None, None, ext, False, error=mismatch)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            digest = sha256_file(path)
            return DownloadResult(url, str(path), digest, ext, True)
        except Exception as err:  # noqa: BLE001
            return DownloadResult(url, None, None, ext, False, error=str(err))
