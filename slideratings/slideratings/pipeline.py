"""End-to-end orchestration for the CCR-driven scraper.

Stage 1 ranks universities by CCR "Courses with ratings" (desc). Stage 2 walks
the universities in that order and, for every rated course:

* parses CCR rating metrics + the professors named in its reviews,
* reconciles the course against the official catalogue by *number*,
* looks each professor up on Rate My Professors, and for every RMP course of
  every professor searches ``"<course> <prof> <uni> course slides"`` and
  downloads the decks it finds, and
* also runs a course-based slide search for the CCR course itself.

MIT is skipped entirely (university and any mit.edu download host). The whole
run is resumable: finished universities/courses are marked ``done``.
"""
from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone

from . import catalog, ccr, rmp, slides, store
from .abbrev import derive_abbrev
from .config import Settings

SKIP_UNIS = {"massachusetts-institute-of-technology"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def make_course_id(abbrev: str, code_or_slug: str) -> str:
    return f"{abbrev}-{_norm(code_or_slug)}"


def log(msg: str) -> None:
    print(f"[{_now()}] {msg}", flush=True)


def crawl_universities(settings: Settings, conn: sqlite3.Connection,
                       limit: int | None = None, refresh: bool = False) -> int:
    slugs = ccr.university_slugs(settings, use_cache=not refresh)
    log(f"found {len(slugs)} universities on CCR")
    records = []
    for i, slug in enumerate(slugs, 1):
        if slug in SKIP_UNIS:
            continue
        try:
            info = ccr.fetch_university(settings, slug, use_cache=not refresh)
        except Exception as err:  # noqa: BLE001
            log(f"  ! {slug}: {err}")
            continue
        if not info.get("rated_courses"):
            continue
        info["abbrev"] = derive_abbrev(slug)
        records.append(info)
        if i % 25 == 0:
            log(f"  scanned {i}/{len(slugs)} universities")
        if limit and len(records) >= limit:
            break
    records.sort(key=lambda r: r.get("rated_courses") or 0, reverse=True)
    for rank, info in enumerate(records, 1):
        try:
            school = rmp.find_school(info.get("name") or info["slug"])
        except Exception:  # noqa: BLE001
            school = None
        school = school or {}
        store.upsert_university(conn, {
            "slug": info["slug"],
            "name": info.get("name"),
            "abbrev": info["abbrev"],
            "location": info.get("location"),
            "students": info.get("students"),
            "total_courses": info.get("total_courses"),
            "rated_courses": info.get("rated_courses"),
            "student_reviews": info.get("student_reviews"),
            "rmp_school_id": school.get("id"),
            "rmp_school_name": school.get("name"),
            "rank": rank,
            "ccr_url": info.get("ccr_url"),
            "status": "pending",
            "scraped_at": _now(),
        })
    log(f"ranked {len(records)} universities by rated-course count")
    return len(records)


def _record_professor(settings: Settings, conn: sqlite3.Connection,
                      course_id: str, uni_row: sqlite3.Row,
                      raw_name: str) -> dict | None:
    name = rmp.normalize_name(raw_name)
    if not name:
        return None
    school_id = uni_row["rmp_school_id"]
    result = {"name": name, "match_type": "no_school"}
    if school_id:
        result = rmp.lookup(name, school_id, delay=settings.rmp.request_delay_s)
    prof_id = store.upsert_professor(conn, {
        "name": name,
        "uni_slug": uni_row["slug"],
        "rmp_legacy_id": result.get("rmp_legacy_id"),
        "matched_name": result.get("matched_name"),
        "department": result.get("department"),
        "school": result.get("school"),
        "avg_rating": result.get("avg_rating"),
        "avg_difficulty": result.get("avg_difficulty"),
        "num_ratings": result.get("num_ratings"),
        "would_take_again": result.get("would_take_again"),
        "rmp_url": result.get("rmp_url"),
        "match_type": result.get("match_type"),
        "scraped_at": _now(),
    })
    store.link_course_professor(conn, course_id, prof_id, "ccr_review")
    if result.get("rmp_url"):
        store.add_link(conn, course_id, "rmp", result["rmp_url"])
    result["professor_id"] = prof_id
    return result


def _professor_course_slides(settings: Settings, conn: sqlite3.Connection,
                             uni_row: sqlite3.Row, prof: dict,
                             known_sha: set[str]) -> None:
    """For each RMP course of *prof*, search + download slides."""
    legacy = prof.get("rmp_legacy_id")
    if not legacy:
        return
    uni_name = uni_row["name"] or uni_row["slug"]
    abbrev = uni_row["abbrev"]
    courses = rmp.teacher_courses(legacy)[:settings.slides.max_prof_courses]
    if courses:
        log(f"        {prof['name']}: {len(courses)} RMP courses -> slide search")
    for c in courses:
        rmp_code = c["course"]
        code, number = ccr.split_code(rmp_code)
        course_id = make_course_id(abbrev, rmp_code)
        if not store.course_is_done(conn, course_id):
            store.upsert_course(conn, {
                "course_id": course_id,
                "uni_slug": uni_row["slug"],
                "course_slug": None,
                "course_code": code,
                "course_number": number,
                "course_name": None,
                "department": prof.get("department"),
                "credits": None,
                "ccr_url": None,
                "official_url": None,
                "status": "pending",
                "scraped_at": _now(),
            })
        store.link_course_professor(conn, course_id, prof["professor_id"], "rmp_course")
        query = f"{rmp_code} {prof['name']} {uni_name} course slides"
        stats = slides.discover_and_download(settings, conn, course_id, [query], known_sha)
        log(f"        rmp {course_id}: {stats['found']} candidates, "
            f"{stats['downloaded']} decks")


def process_course(settings: Settings, conn: sqlite3.Connection,
                   uni_row: sqlite3.Row, entry: "ccr.CourseEntry",
                   known_sha: set[str], idx: int = 0, total: int = 0) -> None:
    abbrev = uni_row["abbrev"]
    uni_name = uni_row["name"] or uni_row["slug"]
    course_id = make_course_id(abbrev, entry.slug)
    prefix = f"[{idx}/{total}]" if total else ""
    if store.course_is_done(conn, course_id):
        log(f"  {prefix} {course_id}: already done, skipping")
        return
    log(f"  {prefix} {course_id} ({entry.slug}): fetching CCR course page")
    try:
        data = ccr.fetch_course(settings, uni_row["slug"], entry.slug)
        code = data.get("course_code") or entry.code
        number = data.get("course_number") or re.sub(r"\D", "", entry.slug)
        ccr_name = data.get("course_name") or entry.name
        department = data.get("department") or entry.department
        profs = data.get("professors", [])
        log(f"      CCR: {code} '{ccr_name}' "
            f"({data.get('num_reviews') or 0} reviews, {len(profs)} profs named)")
        recon = catalog.reconcile(settings, uni_name, code, number)
        course_name = ccr_name or recon.get("official_name")
        if recon.get("official_url"):
            log(f"      catalog: matched {recon['official_url']}")
        else:
            log("      catalog: no official match")
        store.upsert_course(conn, {
            "course_id": course_id,
            "uni_slug": uni_row["slug"],
            "course_slug": entry.slug,
            "course_code": code,
            "course_number": number,
            "course_name": course_name,
            "department": department,
            "credits": data.get("credits"),
            "ccr_url": data.get("ccr_url"),
            "official_url": recon.get("official_url"),
            "status": "pending",
            "scraped_at": _now(),
        })
        store.add_link(conn, course_id, "ccr", data.get("ccr_url"))
        if recon.get("official_url"):
            store.add_link(conn, course_id, "official", recon["official_url"])
        store.upsert_ccr_ratings(conn, {
            "course_id": course_id,
            "star_rating": data.get("star_rating"),
            "num_reviews": data.get("num_reviews"),
            "difficulty": data.get("difficulty"),
            "hours_per_week": data.get("hours_per_week"),
            "recommend_pct": data.get("recommend_pct"),
            "student_satisfaction": data.get("student_satisfaction"),
            "challenge_level": data.get("challenge_level"),
            "grade_accessibility": data.get("grade_accessibility"),
            "time_investment": data.get("time_investment"),
            "attendance_importance": data.get("attendance_importance"),
            "recommendation_rate": data.get("recommendation_rate"),
            "scraped_at": _now(),
        })
        course_query = f"{uni_name} {code} {course_name or ''} lecture slides".strip()
        log(f"      course slide search: {course_query!r}")
        cstats = slides.discover_and_download(settings, conn, course_id, [course_query], known_sha)
        log(f"      course search: {cstats['found']} candidates, "
            f"{cstats['downloaded']} decks")
        for raw in profs:
            prof = _record_professor(settings, conn, course_id, uni_row, raw)
            if not prof:
                log(f"      prof {raw!r}: skipped (unusable name)")
                continue
            if not prof.get("rmp_legacy_id"):
                log(f"      prof {prof['name']}: no RMP match ({prof.get('match_type')})")
                continue
            log(f"      prof {prof['name']}: RMP {prof.get('match_type')} "
                f"(rating {prof.get('avg_rating')})")
            _professor_course_slides(settings, conn, uni_row, prof, known_sha)
        store.set_course_status(conn, course_id, "done")
        log(f"  {prefix} {course_id}: done")
    except Exception as err:  # noqa: BLE001
        log(f"    ! course {entry.slug}: {err}")


def process_university(settings: Settings, conn: sqlite3.Connection,
                       uni_row: sqlite3.Row, limit_courses: int | None = None,
                       known_sha: set[str] | None = None) -> None:
    if uni_row["slug"] in SKIP_UNIS:
        store.set_university_status(conn, uni_row["slug"], "skipped")
        return
    if known_sha is None:
        known_sha = store.known_sha(conn)
    log(f"== [{uni_row['rank']}] {uni_row['name']} "
        f"({uni_row['rated_courses']} rated courses) ==")
    try:
        courses = ccr.rated_courses(settings, uni_row["slug"])
        if limit_courses:
            courses = courses[:limit_courses]
        log(f"  {len(courses)} rated courses to process")
        total = len(courses)
        for i, entry in enumerate(courses, 1):
            process_course(settings, conn, uni_row, entry, known_sha, i, total)
        store.set_university_status(conn, uni_row["slug"], "done")
    except Exception as err:  # noqa: BLE001
        log(f"  ! failed to list courses: {err}")
        store.set_university_status(conn, uni_row["slug"], "error")


def run(settings: Settings, conn: sqlite3.Connection,
        limit_unis: int | None = None, limit_courses: int | None = None,
        start_rank: int = 1, only_uni: str | None = None,
        resume: bool = True) -> None:
    unis = store.universities_by_rank(conn, only_pending=resume)
    if only_uni:
        unis = [u for u in unis if u["slug"] == only_uni]
    else:
        unis = [u for u in unis if (u["rank"] or 0) >= start_rank]
        if limit_unis:
            unis = unis[:limit_unis]
    known = store.known_sha(conn)
    for uni_row in unis:
        process_university(settings, conn, uni_row, limit_courses, known)
        time.sleep(settings.http.min_interval_s)
    log("run complete: " + ", ".join(f"{k}={v}" for k, v in store.counts(conn).items()))
