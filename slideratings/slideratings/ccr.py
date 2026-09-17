"""CollegeClassReviews crawling and parsing.

CCR is a Next.js site whose data is server-rendered into the HTML (both as
visible markup and as React Server Component JSON). This module:

* lists every university slug,
* reads a university's headline stats (notably "Courses with ratings", the
  ranking key requested by the user),
* enumerates a university's *rated* courses via its department pages (complete
  and clean, unlike the client-paginated ``/courses`` list), and
* parses a single course page into CCR rating metrics + professor names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .config import Settings
from .http_util import get_html

METRICS = (
    ("student_satisfaction", "Student Satisfaction"),
    ("challenge_level", "Challenge Level"),
    ("grade_accessibility", "Grade Accessibility"),
    ("time_investment", "Time Investment"),
    ("attendance_importance", "Attendance Importance"),
    ("recommendation_rate", "Recommendation Rate"),
)
_SLUG_SPLIT = re.compile(r"^([a-z]+)[\s\-]?([0-9][0-9a-z]*)$", re.IGNORECASE)


@dataclass
class CourseEntry:
    slug: str
    code: str
    name: str
    rated: bool
    department: str = ""


def _deescape(html: str) -> str:
    r"""Turn RSC-escaped JSON (``\"``) back into plain quotes for regexing."""
    return html.replace('\\"', '"')


def _num_before(text: str, label: str, window: int = 400) -> int | None:
    """Find the numeric ``children`` value that precedes *label* in the RSC."""
    idx = text.find(f'"children":"{label}"')
    if idx < 0:
        idx = text.find(label)
    if idx < 0:
        return None
    seg = text[max(0, idx - window):idx]
    nums = re.findall(r'"children":"([0-9][0-9,]*)"', seg)
    if not nums:
        return None
    return int(nums[-1].replace(",", ""))


def split_code(slug: str) -> tuple[str, str]:
    """Split a course slug into (course_code, course_number).

    ``cs-1110`` -> ("CS 1110", "1110"); ``aem2210`` -> ("AEM 2210", "2210");
    ``stat0200`` -> ("STAT 0200", "0200").
    """
    m = _SLUG_SPLIT.match(slug.strip())
    if not m:
        return (slug.upper(), re.sub(r"\D", "", slug))
    digits = m.group(2)
    letters = m.group(1).upper()
    number = re.sub(r"\D", "", slug)
    return (f"{letters} {digits}", number)


def university_slugs(settings: Settings, use_cache: bool = True) -> list[str]:
    """Return every university slug on CCR (in page order)."""
    url = f"{settings.ccr.base}/universities"
    html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
    slugs: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="/universities/([a-z0-9\-]+)"', html):
        slug = m.group(1)
        if slug in {"request"} or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    return slugs


def parse_university_page(html: str) -> dict:
    """Extract a university's name, location, and headline course/review stats."""
    text = _deescape(html)
    soup = BeautifulSoup(html, "lxml")
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(" ", strip=True)
    location = ""
    students = None
    mloc = re.search(r"([A-Za-z .\-]+,\s*[A-Z]{2})\s*([\d,]+)\s*students", text)
    if mloc:
        location = mloc.group(1).strip()
        students = int(mloc.group(2).replace(",", ""))
    return {
        "name": name,
        "location": location,
        "students": students,
        "total_courses": _num_before(text, "Courses"),
        "rated_courses": _num_before(text, "Courses with ratings"),
        "student_reviews": _num_before(text, "Student reviews"),
    }


def fetch_university(settings: Settings, slug: str, use_cache: bool = True) -> dict:
    url = f"{settings.ccr.base}/universities/{slug}"
    html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
    info = parse_university_page(html)
    info["slug"] = slug
    info["ccr_url"] = url
    return info


def department_slugs(settings: Settings, uni_slug: str, use_cache: bool = True) -> list[str]:
    url = f"{settings.ccr.base}/universities/{uni_slug}/departments"
    html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
    out: list[str] = []
    seen: set[str] = set()
    pattern = rf'href="/universities/{re.escape(uni_slug)}/departments/([a-z0-9\-]+)"'
    for m in re.finditer(pattern, html):
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def parse_department_courses(html: str, uni_slug: str, department: str = "") -> list[CourseEntry]:
    """Parse a department page into course entries with a rated/unrated flag."""
    soup = BeautifulSoup(html, "lxml")
    prefix = f"/universities/{uni_slug}/courses/"
    out: list[CourseEntry] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if prefix not in href:
            continue
        slug = href.split(prefix, 1)[1].split("/", 1)[0].split("#", 1)[0]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        card_text = a.get_text(" ", strip=True)
        rated = "Be the first to review" not in card_text
        code, _ = split_code(slug)
        name = ""
        p = a.find("p")
        if p:
            name = p.get_text(" ", strip=True)
        out.append(CourseEntry(slug=slug, code=code, name=name,
                               rated=rated, department=department))
    return out


def _dept_name(dept_slug: str) -> str:
    return " ".join(w.capitalize() for w in dept_slug.split("-"))


def rated_courses(settings: Settings, uni_slug: str, use_cache: bool = True) -> list[CourseEntry]:
    """Enumerate every rated course for a university (deduped by course code)."""
    entries: dict[str, CourseEntry] = {}
    for dept in department_slugs(settings, uni_slug, use_cache=use_cache):
        url = f"{settings.ccr.base}/universities/{uni_slug}/departments/{dept}"
        try:
            html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
            for entry in parse_department_courses(html, uni_slug, _dept_name(dept)):
                if not entry.rated:
                    continue
                key = re.sub(r"[^a-z0-9]", "", entry.slug.lower())
                entries.setdefault(key, entry)
        except Exception:  # noqa: BLE001
            continue
    return list(entries.values())


def _jsonld_blocks(html: str) -> list:
    out: list = []
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except Exception:  # noqa: BLE001
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _metric_value(text: str, label: str) -> float | None:
    """Read a 0-1 metric for *label* from the nearest width percentage."""
    for m in re.finditer(re.escape(label), text):
        window = text[m.start():m.start() + 400]
        wm = re.search(r'width["\s:]+(\d+)%', window)
        if not wm:
            continue
        return int(wm.group(1)) / 100
    return None


def parse_course_page(html: str, slug: str) -> dict:
    text = _deescape(html)
    soup = BeautifulSoup(html, "lxml")
    code, number = split_code(slug)
    name = ""
    department = ""
    credits = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
        parts = re.split(r"\s*[—:–-]\s*", title, maxsplit=1)
        if len(parts) == 2 and re.search(r"\d", parts[0]):
            name = parts[1].strip()
        else:
            name = title
    mdep = re.search(r'"children":"([^"]+)"\}\]," Department"', text)
    if mdep:
        department = mdep.group(1).strip()
    mcred = re.search(r'"children":"([0-9.]+)"\}\],"Credits"', text)
    if mcred:
        credits = mcred.group(1).strip()

    star_rating = None
    num_reviews = None
    try:
        for block in _jsonld_blocks(html):
            agg = block.get("aggregateRating") if isinstance(block, dict) else None
            if not isinstance(agg, dict):
                continue
            star_rating = float(agg.get("ratingValue"))
            num_reviews = int(agg.get("reviewCount") or agg.get("ratingCount"))
            break
    except (TypeError, ValueError):
        pass
    if num_reviews is None:
        m = re.search(r"Aggregated from\s+([0-9,]+)\s+student rating", text)
        if m:
            num_reviews = int(m.group(1).replace(",", ""))

    difficulty = None
    m = re.search(r"Difficulty:[^0-9]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*/\s*5", text)
    if m:
        difficulty = float(m.group(1))
    hours_per_week = None
    m = re.search(r'"children":"?([0-9]+(?:\.[0-9]+)?)"?\}\],"\s*hrs/week"', text)
    if m:
        hours_per_week = float(m.group(1))
    recommend_pct = None
    m = re.search(r'"children":\[\s*([0-9]+)\s*,\s*"%"\]\}\],"\s*recommend"', text)
    if m:
        recommend_pct = float(m.group(1))

    professors: list[str] = []
    for pm in re.finditer(r"Professor:</span><span[^>]*>([^<]+)</span>", text):
        nm = pm.group(1).strip()
        if not nm or nm in professors:
            continue
        professors.append(nm)

    row = {
        "course_code": code,
        "course_number": number,
        "course_name": name,
        "department": department,
        "credits": credits,
        "star_rating": star_rating,
        "num_reviews": num_reviews,
        "difficulty": difficulty,
        "hours_per_week": hours_per_week,
        "recommend_pct": recommend_pct,
        "professors": professors,
    }
    for col, label in METRICS:
        row[col] = _metric_value(text, label)
    return row


def variant_slugs(html: str, number: str) -> list[str]:
    """Numbered course-variant slugs (e.g. ``cs-1110``) sharing ``number``."""
    text = _deescape(html)
    if not number:
        return []
    want = re.sub(r"\D", "", str(number))
    out: list[str] = []
    for m in re.finditer(r'\["\$","li","([a-z]+-?[0-9][0-9a-z]*)"', text):
        slug = m.group(1)
        if not want:
            continue
        if re.sub(r"\D", "", slug) != want:
            continue
        if slug in out:
            continue
        out.append(slug)
    return out


def fetch_course(settings: Settings, uni_slug: str, course_slug: str,
                 use_cache: bool = True) -> dict:
    url = f"{settings.ccr.base}/universities/{uni_slug}/courses/{course_slug}"
    html = get_html(url, settings.http, settings.cache_dir, use_cache=use_cache)
    row = parse_course_page(html, course_slug)
    row["ccr_url"] = url
    name_is_code = (
        re.sub(r"[^a-z0-9]", "", row["course_name"].lower())
        == re.sub(r"[^a-z0-9]", "", row["course_code"].lower())
    )
    if not row["professors"] or name_is_code:
        for variant in variant_slugs(html, row["course_number"])[:3]:
            if variant == course_slug:
                continue
            vurl = f"{settings.ccr.base}/universities/{uni_slug}/courses/{variant}"
            try:
                vhtml = get_html(vurl, settings.http, settings.cache_dir, use_cache=use_cache)
                vrow = parse_course_page(vhtml, variant)
            except Exception:  # noqa: BLE001
                continue
            for prof in vrow["professors"]:
                if prof in row["professors"]:
                    continue
                row["professors"].append(prof)
            if name_is_code and vrow["course_name"]:
                row["course_name"] = vrow["course_name"]
                name_is_code = False
    return row
