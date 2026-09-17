#!/usr/bin/env python3
"""Fetch + parse collegeclassreviews.com (CCR) course pages (stdlib only).

The current CCR layout (Next.js) exposes:
  - an aggregate star rating (/5) via a JSON-LD ``aggregateRating`` block,
  - a student review count,
  - six 0-1 crowd metrics rendered as width-percentage bars:
      Student Satisfaction, Challenge Level, Grade Accessibility,
      Time Investment, Attendance Importance, Recommendation Rate.

Pages are server-rendered so a plain GET is enough (no JS). Responses are cached
on disk for 7 days so re-runs don't re-hit the site.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
CACHE_MAX_AGE_S = 7 * 24 * 3600
_MIN_INTERVAL_S = 1.0
_last_fetch = 0.0

METRICS = (
    ("student_satisfaction", "Student Satisfaction"),
    ("challenge_level", "Challenge Level"),
    ("grade_accessibility", "Grade Accessibility"),
    ("time_investment", "Time Investment"),
    ("attendance_importance", "Attendance Importance"),
    ("recommendation_rate", "Recommendation Rate"),
)


def fetch(url: str, cache_dir: Path, use_cache: bool = True) -> str:
    """GET *url* as text, caching the body on disk for CACHE_MAX_AGE_S seconds."""
    global _last_fetch
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()
    path = cache_dir / f"{key}.html"
    if use_cache and path.exists() and (time.time() - path.stat().st_mtime) < CACHE_MAX_AGE_S:
        return path.read_text(encoding="utf-8", errors="replace")
    wait = _MIN_INTERVAL_S - (time.time() - _last_fetch)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    _last_fetch = time.time()
    path.write_text(body, encoding="utf-8")
    return body


def _jsonld_blocks(html: str) -> list:
    out: list = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _find_aggregate(obj):
    """Recursively locate an aggregateRating dict inside a JSON-LD object."""
    if isinstance(obj, dict):
        agg = obj.get("aggregateRating")
        if isinstance(agg, dict) and agg.get("ratingValue") is not None:
            return agg
        for v in obj.values():
            found = _find_aggregate(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_aggregate(v)
            if found:
                return found
    return None


def _metric_value(text: str, label: str) -> float | None:
    """Read a 0-1 metric for *label* from the nearest width percentage."""
    for m in re.finditer(re.escape(label), text):
        window = text[m.start():m.start() + 400]
        wm = re.search(r'width["\s:]+(\d+)%', window)
        if wm:
            return int(wm.group(1)) / 100
    return None


def parse_course(html: str) -> dict:
    """Parse a CCR course page into star_rating, num_reviews, and the 6 metrics."""
    text = html.replace('\\"', '"')
    star_rating = None
    num_reviews = None

    for block in _jsonld_blocks(html):
        agg = _find_aggregate(block)
        if agg:
            try:
                star_rating = float(agg.get("ratingValue"))
            except (TypeError, ValueError):
                star_rating = None
            rc = agg.get("reviewCount") or agg.get("ratingCount")
            try:
                num_reviews = int(rc) if rc is not None else None
            except (TypeError, ValueError):
                num_reviews = None
            break

    if star_rating is None:
        m = re.search(r'"ratingValue"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)"?', text)
        if m:
            star_rating = float(m.group(1))
    if num_reviews is None:
        m = re.search(r"Aggregated from\s+([0-9,]+)\s+student rating", text)
        if m:
            num_reviews = int(m.group(1).replace(",", ""))

    row = {"star_rating": star_rating, "num_reviews": num_reviews}
    for col, label in METRICS:
        row[col] = _metric_value(text, label)
    return row
