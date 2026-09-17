"""Verify a downloaded slide deck with PyMuPDF.

Rejects corrupted/empty PDFs and, optionally, text-dense *notes* PDFs that
slipped through the link filter. PPT/PPTX files are accepted as-is (magic-byte
checked at download time).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from .config import VerifyConfig


@dataclass
class VerifyResult:
    valid: bool
    pages: int
    title: str
    chars_per_page: float
    is_slide_like: bool
    reason: str


def verify_pdf(path, cfg: VerifyConfig) -> VerifyResult:
    path = Path(path)
    if not path.exists():
        return VerifyResult(False, 0, "", 0, False, "missing file")
    try:
        doc = fitz.open(path)
    except Exception as err:  # noqa: BLE001
        return VerifyResult(False, 0, "", 0, False, f"open failed: {err}")
    try:
        if doc.needs_pass and doc.authenticate("") == 0:
            doc.close()
            return VerifyResult(False, 0, "", 0, False, "encrypted")
        pages = doc.page_count
        if pages < cfg.min_pages:
            doc.close()
            return VerifyResult(False, pages, "", 0, False, "too few pages")
        meta = doc.metadata or {}
        title = meta.get("title", "") or ""
        total_chars = 0
        landscape_pages = 0
        sampled = 0
        for page in doc:
            sampled += 1
            total_chars += len(page.get_text("text"))
            rect = page.rect
            if rect.height > 0 and rect.width / rect.height >= cfg.slide_min_aspect_ratio:
                landscape_pages += 1
            if sampled >= 30:
                break
        chars_per_page = total_chars / max(sampled, 1)
        mostly_landscape = landscape_pages >= sampled / 2
        is_slide_like = mostly_landscape and chars_per_page <= cfg.notes_max_chars_per_page
        if cfg.enforce_slide_likeness and not is_slide_like:
            doc.close()
            return VerifyResult(False, pages, title, chars_per_page, False,
                                "looks like notes (portrait + text-dense)")
        doc.close()
        return VerifyResult(True, pages, title, chars_per_page, is_slide_like, "")
    except Exception as err:  # noqa: BLE001
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        return VerifyResult(False, 0, "", 0, False, f"verify error: {err}")


def verify_office(path) -> VerifyResult:
    """Lightweight acceptance for PPT/PPTX (magic bytes already checked)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return VerifyResult(False, 0, "", 0, False, "missing/empty file")
    return VerifyResult(True, 0, "", 0, True, "")
