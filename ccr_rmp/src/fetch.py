"""Stage 1 helpers: turn a course's slide link into a usable artifact.

A single `course_slide_links` cell may contain one or more URLs. We try each
until one yields content, then produce ONE of two artifact kinds:

  * image artifact  -> rendered page images (PDF / PPT / PPTX)  [for the VLM]
  * text  artifact  -> extracted visible text (HTML / GitHub page) [for the LLM]

`slide_artifact` stores a JSON list of image paths when `slide_kind` is an
image kind, or the path to a `.txt` file when it is `html`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from pdf2image import convert_from_path
from PIL import Image

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

_URL_RE = re.compile(r"https?://[^\s,;\"'<>()\]]+", re.IGNORECASE)
_IMG_MAX_W = 1400            # downscale wide renders to keep VLM tokens sane
_JPEG_Q = 85


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #
def split_links(cell: str | None) -> list[str]:
    """Extract the URL(s) from a raw cell, de-duplicated, order preserved."""
    if not cell:
        return []
    seen: list[str] = []
    for m in _URL_RE.findall(cell):
        u = m.rstrip(".)")
        if u not in seen:
            seen.append(u)
    return seen


def to_raw_github(url: str) -> str:
    """Rewrite a github.com blob/raw URL to raw.githubusercontent.com."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)", url)
    if m:
        owner, repo, rest = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
    return url


def safe_key(course_college: str) -> str:
    """Filesystem-safe folder name for a course."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", course_college or "unknown")
    return s.strip("_")[:80] or "unknown"


# --------------------------------------------------------------------------- #
# Low-level fetch
# --------------------------------------------------------------------------- #
def fetch_one(url: str, timeout: int) -> tuple[str, str, Any]:
    """GET a URL and classify it.

    Returns (kind, url_used, payload) where kind is 'pdf' | 'ppt' | 'html'
    and payload is bytes (pdf/ppt) or str (html). Raises on transport error.
    """
    url_used = to_raw_github(url)
    r = requests.get(url_used, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    body = r.content
    low = url_used.lower()

    if body[:4] == b"%PDF" or "application/pdf" in ctype or low.endswith(".pdf"):
        return "pdf", url_used, body
    if (
        low.endswith((".ppt", ".pptx"))
        or "powerpoint" in ctype
        or "presentationml" in ctype
        or "vnd.ms-powerpoint" in ctype
    ):
        return "ppt", url_used, body
    return "html", url_used, r.text


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def _prep(im: Image.Image) -> Image.Image:
    if im.mode != "RGB":
        im = im.convert("RGB")
    if im.width > _IMG_MAX_W:
        h = round(im.height * _IMG_MAX_W / im.width)
        im = im.resize((_IMG_MAX_W, h), Image.LANCZOS)
    return im


def save_source_pdf(body: bytes, out_dir: Path) -> Path:
    dest = out_dir / "source.pdf"
    dest.write_bytes(body)
    return dest


def ppt_to_pdf(body: bytes, ext: str, out_dir: Path) -> Path:
    """Convert PPT/PPTX bytes to a cached source.pdf via LibreOffice."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"deck{ext}"
        src.write_bytes(body)
        profile = f"file://{tmp_path}/lo_profile_{uuid.uuid4().hex}"
        subprocess.run(
            [
                "soffice", "--headless", f"-env:UserInstallation={profile}",
                "--convert-to", "pdf", "--outdir", str(tmp_path), str(src),
            ],
            check=True, capture_output=True, timeout=120,
        )
        pdf = tmp_path / "deck.pdf"
        if not pdf.exists():
            raise RuntimeError("LibreOffice produced no PDF")
        dest = out_dir / "source.pdf"
        dest.write_bytes(pdf.read_bytes())
        return dest


def render_pages(pdf_path: str | Path, out_dir: Path, pages: list[int]) -> dict[int, str]:
    """Render specific 1-based pages of a PDF to JPEGs -> {page: path}."""
    out: dict[int, str] = {}
    for p in sorted({p for p in pages if p >= 1}):
        try:
            imgs = convert_from_path(str(pdf_path), dpi=110, first_page=p, last_page=p)
        except Exception:  # noqa: BLE001
            continue
        if imgs:
            dest = out_dir / f"page{p}.jpg"
            _prep(imgs[0]).save(dest, "JPEG", quality=_JPEG_Q)
            out[p] = str(dest)
    return out


def page_texts(pdf_path: str | Path, max_pages: int) -> list[str]:
    """Return the text of the first `max_pages` pages (poppler pdftotext)."""
    try:
        r = subprocess.run(
            ["pdftotext", "-l", str(max_pages), str(pdf_path), "-"],
            capture_output=True, timeout=60,
        )
    except Exception:  # noqa: BLE001
        return []
    if r.returncode != 0:
        return []
    pages = r.stdout.decode("utf-8", "ignore").split("\f")
    return [p.strip() for p in pages][:max_pages]


def extract_html_text(html: str, url: str, max_chars: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "head"]):
        tag.decompose()
    root = None
    if "github.com" in url:
        root = soup.select_one("article.markdown-body") or soup.select_one("#readme")
    node = root or soup.body or soup
    lines = [ln.strip() for ln in node.get_text("\n").splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars].strip()


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def fetch_slide(course: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Fetch a course's slide link -> artifact dict (see module docstring)."""
    fetch_cfg = cfg.get("fetch", {})
    timeout = int(fetch_cfg.get("timeout", 25))
    max_pages = int(fetch_cfg.get("max_pdf_pages", 2))
    max_chars = int(fetch_cfg.get("max_html_chars", 6000))

    links = split_links(course.get("course_slide_links"))
    result: dict[str, Any] = {
        "slide_url_used": None,
        "slide_kind": None,
        "slide_artifact": None,
        "slide_status": "no_link",
        "example": {"mode": "none"},
    }
    if not links:
        return result

    out_dir = Path(cfg["artifacts_dir"]) / safe_key(course["course_college"])
    out_dir.mkdir(parents=True, exist_ok=True)

    last_err: str | None = None
    for url in links:
        try:
            kind, url_used, payload = fetch_one(url, timeout)
            if kind in ("pdf", "ppt"):
                if kind == "pdf":
                    pdf_path = save_source_pdf(payload, out_dir)
                else:
                    ext = ".pptx" if url_used.lower().endswith(".pptx") else ".ppt"
                    pdf_path = ppt_to_pdf(payload, ext, out_dir)
                preview = render_pages(pdf_path, out_dir, list(range(1, max_pages + 1)))
                imgs = [preview[p] for p in sorted(preview)]
                if not imgs:
                    last_err = f"{kind} produced no pages"
                    continue
                result.update(
                    slide_url_used=url_used, slide_kind=kind,
                    slide_artifact=json.dumps({"pdf": str(pdf_path), "preview": imgs}),
                    slide_status="ok",
                    example={"mode": "image", "images": imgs},
                )
                return result
            # html
            text = extract_html_text(payload, url_used, max_chars)
            if len(text) < 20:
                last_err = "html had almost no text"
                continue
            txt_path = out_dir / "page.txt"
            txt_path.write_text(text, encoding="utf-8")
            result.update(
                slide_url_used=url_used, slide_kind="html",
                slide_artifact=json.dumps({"text": str(txt_path)}), slide_status="ok",
                example={"mode": "text", "chars": len(text), "snippet": text[:600]},
            )
            return result
        except Exception as exc:  # noqa: BLE001 - record and try the next link
            last_err = f"{type(exc).__name__}: {exc}"
            continue

    result.update(slide_status="error", example={"mode": "error", "error": last_err})
    return result
