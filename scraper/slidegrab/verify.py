"""Verify a downloaded slide deck with PyMuPDF.

Rejects corrupted/empty PDFs and, optionally, text-dense *notes* PDFs that
slipped through the link filter (we want slides, not lecture notes).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from config.settings import settings


@dataclass
class VerifyResult:
    valid: bool
    pages: int
    title: str
    chars_per_page: float
    is_slide_like: bool
    reason: str = ""


def verify_pdf(path: str | Path, cfg=settings.verify) -> VerifyResult:
    path = Path(path)
    if not path.exists():
        return VerifyResult(False, 0, "", 0.0, False, "missing file")

    try:
        doc = fitz.open(path)
    except Exception as err:  # noqa: BLE001
        return VerifyResult(False, 0, "", 0.0, False, f"corrupt: {err}")

    try:
        pages = doc.page_count
        if pages < cfg.min_pages:
            return VerifyResult(False, pages, "", 0.0, False, "zero pages")

        title = (doc.metadata or {}).get("title", "") or ""
        total_chars = 0
        landscape_pages = 0
        sampled = 0
        for page in doc:
            sampled += 1
            total_chars += len(page.get_text("text"))
            rect = page.rect
            if rect.height > 0 and (rect.width / rect.height) >= cfg.slide_min_aspect_ratio:
                landscape_pages += 1
            if sampled >= 30:
                break

        chars_per_page = total_chars / max(sampled, 1)
        mostly_landscape = landscape_pages >= (sampled / 2)
        is_slide_like = mostly_landscape or chars_per_page <= cfg.notes_max_chars_per_page

        if cfg.enforce_slide_likeness and not is_slide_like:
            return VerifyResult(
                False, pages, title, chars_per_page, False,
                "looks like notes (portrait + text-dense)",
            )
        return VerifyResult(True, pages, title, chars_per_page, is_slide_like, "")
    finally:
        doc.close()
