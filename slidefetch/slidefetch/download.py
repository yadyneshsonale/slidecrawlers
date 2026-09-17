"""Download slide files with magic-byte verification.

Refuses to save HTML error/login pages disguised as documents, computes a
SHA-256, and skips files that already exist (resumable, no clobber).
"""
from __future__ import annotations

import hashlib
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit

import certifi

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"           # pptx (Office Open XML)
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"     # legacy ppt (OLE compound file)
_HTML_SNIFF = (b"<!doctype html", b"<html", b"<head", b"<body", b"<!--")

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidefetch/1.0"
)


@dataclass
class DownloadResult:
    url: str
    path: str | None
    sha256: str | None
    file_type: str
    ok: bool
    skipped: bool = False
    error: str = ""


def sanitize(name: str, maxlen: int = 120) -> str:
    name = unquote(name).strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("._-")
    return name[:maxlen] or "file"


# Document extensions stripped from an original filename before re-adding the
# canonical one, so ``Lecture01.pdf`` -> stem ``Lecture01``. Includes .html/.htm
# so a rendered slideshow ``ee361_intro.html`` becomes ``ee361_intro.pdf``.
_NAME_EXT_RE = re.compile(r"\.(?:pdf|pptx?|ppt|docx?|key|odp|html?)$", re.IGNORECASE)


def _filename(sequence_no: int, name: str | None, ext: str) -> str:
    """Build ``<N>_<original-name>.<ext>``, falling back to ``<N>.<ext>``.

    Keeps files in download order (1..N) while preserving the source filename,
    e.g. ``1_EECS230_Lecture01.pdf``.
    """
    if name:
        base = unquote(name).rsplit("/", 1)[-1]
        base = _NAME_EXT_RE.sub("", base).strip()
        stem = sanitize(base)
        if base and stem and stem != "file":
            return f"{sequence_no}_{stem}.{ext}"
    return f"{sequence_no}.{ext}"


def _is_html(body: bytes, content_type: str) -> bool:
    head = body[:1024].lstrip().lower()
    return "text/html" in content_type.lower() or head.startswith(_HTML_SNIFF)


def _content_mismatch(file_type: str, body: bytes, content_type: str) -> str:
    if _is_html(body, content_type):
        return "html response, not a file"
    if file_type == "pdf" and not body[:8].lstrip().startswith(_PDF_MAGIC):
        return "not a pdf (bad magic bytes)"
    if file_type == "pptx" and not body.startswith(_ZIP_MAGIC):
        return "not a pptx (bad magic bytes)"
    if file_type == "ppt" and not body.startswith(_OLE_MAGIC):
        return "not a ppt (bad magic bytes)"
    return ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# TLS: verify against Mozilla's CA bundle (certifi) and recover an intermediate
# CA cert that a misconfigured server forgot to send (AIA chasing).
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _ca_file() -> str:
    return certifi.where()


def _verified_context(extra_pem: str | None = None) -> ssl.SSLContext:
    """A verifying TLS context using certifi roots (+ any extra PEM chain)."""
    ctx = ssl.create_default_context(cafile=_ca_file())
    if extra_pem:
        ctx.load_verify_locations(cadata=extra_pem)
    return ctx


# DER value bytes of OID 1.3.6.1.5.5.7.48.2 (id-ad-caIssuers); marks the
# "CA Issuers" access description inside a certificate's AIA extension.
_AIA_CA_ISSUERS_OID = bytes.fromhex("2B06010505073002")


def _aia_ca_issuer_urls(der: bytes) -> list[str]:
    """Extract CA-Issuers URIs from a DER certificate's AIA extension.

    A dependency-free byte scan: locate the caIssuers OID, then read the
    following ``[6] IA5String`` GeneralName (a URI, context tag 0x86).
    """
    urls: list[str] = []
    i = der.find(_AIA_CA_ISSUERS_OID)
    while i != -1:
        j = i + len(_AIA_CA_ISSUERS_OID)
        if j + 1 < len(der) and der[j] == 0x86:  # [6] uniformResourceIdentifier
            length = der[j + 1]
            uri = der[j + 2:j + 2 + length].decode("ascii", "ignore")
            if uri.startswith(("http://", "https://")):
                urls.append(uri)
        i = der.find(_AIA_CA_ISSUERS_OID, j)
    return urls


def _recover_intermediates(host: str, port: int = 443, timeout: int = 30) -> str:
    """Return PEM of intermediate CA cert(s) a server omitted from its chain.

    Opens an *unverified* TLS connection ONLY to read the server's public
    certificate metadata (its AIA pointer), then downloads the named
    intermediate over HTTP. The eventual file download is still verified against
    trusted roots, so supplying a missing-but-public intermediate cannot weaken
    authentication: a forged certificate would still fail that final check.
    """
    ctx = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        return ""
    pems: list[str] = []
    for url in _aia_ca_issuer_urls(der):
        try:
            data = urllib.request.urlopen(url, timeout=timeout).read()
        except Exception:  # noqa: BLE001
            continue
        if data.lstrip().startswith(b"-----BEGIN"):
            pems.append(data.decode("ascii", "ignore"))
        else:
            try:
                pems.append(ssl.DER_cert_to_PEM_cert(data))
            except Exception:  # noqa: BLE001
                continue
    return "\n".join(pems)


def _is_local_issuer_error(err: BaseException) -> bool:
    """True if *err* is a TLS failure caused by a missing intermediate CA."""
    reason = getattr(err, "reason", err)
    if not isinstance(reason, ssl.SSLCertVerificationError):
        return False
    if getattr(reason, "verify_code", None) in (2, 20):  # unable to get issuer
        return True
    return "local issuer" in str(reason).lower()


def _http_get(fetch_url: str, timeout: int, context: ssl.SSLContext) -> tuple[str, bytes]:
    """HTTP GET returning ``(content_type, body)``; retries once on HTTP 429."""
    req = urllib.request.Request(fetch_url, headers={"User-Agent": _USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 2:
                retry_after = err.headers.get("Retry-After") if err.headers else None
                delay = int(retry_after) if (retry_after or "").isdigit() else 2 * (attempt + 1)
                time.sleep(min(delay, 10))
                continue
            raise
    raise urllib.error.URLError("too many retries")


def _get_with_aia(fetch_url: str, timeout: int) -> tuple[str, bytes]:
    """GET with full TLS verification, recovering a missing intermediate once."""
    try:
        return _http_get(fetch_url, timeout, _verified_context())
    except urllib.error.URLError as err:
        if not _is_local_issuer_error(err):
            raise
        host = urlsplit(fetch_url).hostname or ""
        pem = _recover_intermediates(host) if host else ""
        if not pem:
            raise
        return _http_get(fetch_url, timeout, _verified_context(pem))


_GH_BLOB_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$", re.IGNORECASE
)
_GDRIVE_FILE_RE = re.compile(r"^/file/d/([A-Za-z0-9_-]+)(?:/|$)")


def _google_drive_direct_url(url: str) -> str | None:
    """Return a direct-download URL for common Google Drive share links.

    Converts:
    - drive.google.com/file/d/<id>/view?... -> /uc?export=download&id=<id>
    - drive.google.com/open?id=<id>         -> /uc?export=download&id=<id>
    - drive.google.com/uc?id=<id>           -> /uc?export=download&id=<id>
    """
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "drive.google.com":
        return None

    file_id = None
    m = _GDRIVE_FILE_RE.match(parts.path)
    if m:
        file_id = m.group(1)
    if not file_id:
        q = parse_qs(parts.query)
        file_id = (q.get("id") or [None])[0]
    if not file_id:
        return None
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _google_drive_file_id(url: str) -> str | None:
    """Extract a Google Drive file id from common share URL formats."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "drive.google.com":
        return None

    m = _GDRIVE_FILE_RE.match(parts.path)
    if m:
        return m.group(1)

    q = parse_qs(parts.query)
    return (q.get("id") or [None])[0]


_DRIVE_CONFIRM_RE = re.compile(r"confirm=([0-9A-Za-z_\-]+)")


def _drive_confirmed_url(url: str, body: bytes) -> str | None:
    """Resolve Google Drive's "can't scan for viruses" interstitial.

    Large public files answer ``uc?export=download`` with an HTML page instead
    of the bytes; following its confirm token (via the usercontent host) returns
    the real file.
    """
    file_id = _google_drive_file_id(url) or (
        parse_qs(urlsplit(url).query).get("id") or [None]
    )[0]
    if not file_id:
        return None
    m = _DRIVE_CONFIRM_RE.search(body.decode("utf-8", "ignore"))
    token = m.group(1) if m else "t"
    return (
        "https://drive.usercontent.google.com/download?"
        f"id={file_id}&export=download&confirm={token}"
    )


def normalize_url(url: str) -> str:
    """Rewrite host URLs that serve an HTML viewer into the raw file URL.

    GitHub ``/blob/`` links return an HTML page, not the document; convert to
    ``raw.githubusercontent.com``. Also percent-encodes spaces and other unsafe
    characters in the path so URLs like ``.../lec15 2.pdf`` are requestable.
    """
    m = _GH_BLOB_RE.match(url)
    if m:
        owner, repo, rest = m.groups()
        rest = rest.split("?", 1)[0].split("#", 1)[0]
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"

    gdrive = _google_drive_direct_url(url)
    if gdrive:
        url = gdrive

    parts = urlsplit(url)
    # safe set keeps already-encoded sequences intact (%) and normal path chars,
    # while encoding raw spaces and other illegal characters.
    safe = "/%:@-._~!$&'()*+,;="
    path = quote(parts.path, safe=safe)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def download(url: str, dest_dir: Path, file_type: str, _number: int | None,
             sequence_no: int, name: str | None = None,
             timeout: int = 60) -> DownloadResult:
    dest_dir.mkdir(parents=True, exist_ok=True)
    fetch_url = normalize_url(url)
    ext = file_type or (Path(urlparse(url).path).suffix.lstrip(".").lower() or "pdf")
    if ext not in ("pdf", "ppt", "pptx"):
        ext = "pdf"
    out_path = dest_dir / _filename(sequence_no, name, ext)

    if out_path.exists() and out_path.stat().st_size > 0:
        return DownloadResult(url, str(out_path), None, ext, True, skipped=True, error="exists")

    try:
        content_type, body = _get_with_aia(fetch_url, timeout)
    except urllib.error.HTTPError as err:
        return DownloadResult(url, None, None, ext, False, error=f"http {err.code}")
    except Exception as err:  # noqa: BLE001
        return DownloadResult(url, None, None, ext, False, error=str(err))

    # Google Drive can answer with an HTML virus-scan interstitial instead of
    # the file; follow its confirm token to fetch the real bytes.
    if _is_html(body, content_type) and "drive.google" in fetch_url:
        confirmed = _drive_confirmed_url(fetch_url, body)
        if confirmed:
            try:
                content_type, body = _get_with_aia(confirmed, timeout)
            except Exception as err:  # noqa: BLE001
                return DownloadResult(url, None, None, ext, False, error=str(err))

    if not body:
        return DownloadResult(url, None, None, ext, False, error="empty body")
    mismatch = _content_mismatch(ext, body, content_type)
    if mismatch:
        return DownloadResult(url, None, None, ext, False, error=mismatch)

    out_path.write_bytes(body)
    return DownloadResult(url, str(out_path), _sha256(body), ext, True)


def download_html_slides(
    url: str,
    dest_dir: Path,
    sequence_no: int,
    name: str | None = None,
    timeout_ms: int = 60000,
) -> DownloadResult:
    """Render a client-side HTML slide deck (remark/reveal/impress) to a PDF.

    Mirrors :func:`download`: skips an already-present file, returns a
    :class:`DownloadResult` with the file type ``pdf``. Playwright is imported
    lazily so the urllib download path stays dependency-light.
    """
    from slidefetch.render import render_slides_to_pdf

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / _filename(sequence_no, name, "pdf")
    if out_path.exists() and out_path.stat().st_size > 0:
        return DownloadResult(url, str(out_path), None, "pdf", True,
                              skipped=True, error="exists")

    ok, err = render_slides_to_pdf(url, out_path, timeout_ms=timeout_ms)
    if not ok:
        return DownloadResult(url, None, None, "pdf", False, error=err)
    body = out_path.read_bytes()
    return DownloadResult(url, str(out_path), _sha256(body), "pdf", True)
