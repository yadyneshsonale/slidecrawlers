"""Download slide-deck candidates with magic-byte verification.

Plain ``urllib`` download with a desktop User-Agent. Refuses to persist HTML
error/login pages masquerading as documents, computes a SHA-256 for global
dedup, and writes files into ``data/{course_id}/{N}.{ext}`` (sequential ints so
alphabetical order equals lecture order — the RateMySlides convention).
"""
from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from .config import HttpConfig

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"
_HTML_SNIFF = (b"<!doctype html", b"<html", b"<head", b"<body", b"<!--")


@dataclass
class DownloadResult:
    url: str
    file_path: str | None
    sha256: str | None
    file_type: str
    ok: bool
    skipped: bool = False
    error: str = ""


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _content_problem(file_type: str, body: bytes, content_type: str) -> str:
    head = body[:1024].lstrip()
    lower = head.lower()
    if "text/html" in content_type.lower() or lower.startswith(_HTML_SNIFF):
        return "html response, not a file"
    if file_type == "pdf":
        if not body[:8].lstrip().startswith(_PDF_MAGIC):
            return "not a pdf (bad magic bytes)"
        return ""
    if file_type == "pptx":
        if not body.startswith(_ZIP_MAGIC):
            return "not a pptx (bad magic bytes)"
        return ""
    if file_type == "ppt" and not body.startswith(_OLE_MAGIC):
        return "not a ppt (bad magic bytes)"
    return ""


def _ext_for(url: str, default: str = "pdf") -> str:
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    return suffix or default


def _encode_url(url: str) -> str:
    """Percent-encode spaces/unsafe chars without double-encoding existing %."""
    parts = urlsplit(url.strip())
    path = quote(parts.path, safe="/%:@!$&'()*+,;=~")
    query = quote(parts.query, safe="/%:@!$&'()*+,;=~?")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def download(url: str, dest_dir: Path, number: int, cfg: HttpConfig,
             known_sha: set[str] | None = None) -> DownloadResult:
    """Download *url* to ``dest_dir/{number}.{ext}`` with verification."""
    ext = _ext_for(url)
    if ext not in ("pdf", "ppt", "pptx"):
        ext = "pdf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{number}.{ext}"
    if path.exists() and path.stat().st_size > 0:
        return DownloadResult(url, str(path), None, ext, True, skipped=True, error="exists")

    headers = {"User-Agent": cfg.user_agent, "Accept": "*/*"}
    req = urllib.request.Request(_encode_url(url), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
            content_type = resp.headers.get("content-type", "")
            body = resp.read()
    except urllib.error.HTTPError as err:
        return DownloadResult(url, None, None, ext, False, error=f"http {err.code}")
    except (urllib.error.URLError, OSError) as err:
        return DownloadResult(url, None, None, ext, False, error=str(err))

    if not body:
        return DownloadResult(url, None, None, ext, False, error="empty body")
    problem = _content_problem(ext, body, content_type)
    if problem:
        return DownloadResult(url, None, None, ext, False, error=problem)
    digest = sha256_bytes(body)
    if known_sha is not None and digest in known_sha:
        return DownloadResult(url, None, digest, ext, False, error="duplicate sha")
    path.write_bytes(body)
    if known_sha is not None:
        known_sha.add(digest)
    return DownloadResult(url, str(path), digest, ext, True)
