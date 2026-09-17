#!/usr/bin/env python3
"""Backfill missing instructor names using per-course web search.

This script uses the repo's SearchClient (Tavily/Serper/Brave). If Serper is
configured, results are Google-based.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from html import unescape

from slidegrab.search import SearchClient, SearchHit

DB_PATH = "/home/b-ysonale/scraper/dataset_courses.db"

UNIVERSITY_MAP = {
    "mit": "MIT",
    "stanford": "Stanford University",
    "cmu": "Carnegie Mellon University",
    "harvard": "Harvard University",
    "caltech": "Caltech",
    "cambridge": "University of Cambridge",
    "university_of_cambridge": "University of Cambridge",
    "oxford": "University of Oxford",
    "princeton": "Princeton University",
    "uc_berkeley": "UC Berkeley",
    "eth_zurich": "ETH Zurich",
    "nus": "National University of Singapore",
    "national_university_of_singapore": "National University of Singapore",
    "iit_bombay": "IIT Bombay",
    "iit_delhi": "IIT Delhi",
    "iit_madras": "IIT Madras",
    "iit_kanpur": "IIT Kanpur",
    "iit_kharagpur": "IIT Kharagpur",
    "iit_guwahati": "IIT Guwahati",
    "iit_hyderabad": "IIT Hyderabad",
    "iisc": "IISc Bangalore",
    "iiit_hyderabad": "IIIT Hyderabad",
    "brown_university": "Brown University",
    "duke_university": "Duke University",
    "northwestern_university": "Northwestern University",
    "northeastern_university": "Northeastern University",
    "rice_university": "Rice University",
    "arizona_state_university": "Arizona State University",
    "texas_a_m_university": "Texas A&M University",
    "ut_austin": "UT Austin",
    "uiuc": "UIUC",
    "uw": "University of Washington",
    "university_of_toronto": "University of Toronto",
    "university_of_waterloo": "University of Waterloo",
    "university_of_maryland": "University of Maryland",
    "university_of_pennsylvania": "University of Pennsylvania",
    "university_of_florida": "University of Florida",
    "university_of_utah": "University of Utah",
    "university_of_edinburgh": "University of Edinburgh",
    "university_of_north_carolina_at_chapel_hill": "UNC Chapel Hill",
    "university_of_central_florida": "University of Central Florida",
    "university_of_wisconsin-madison": "University of Wisconsin-Madison",
}

LABEL_PATTERNS = [
    re.compile(r"(?:course\s+)?(?:instructor|instructors|professor|lecturer|faculty)\s*[:\-]\s*([A-Za-z][A-Za-z\.'\- ]{2,80})", re.I),
    re.compile(r"taught\s+by\s*[:\-]?\s*([A-Za-z][A-Za-z\.'\- ]{2,80})", re.I),
]

NAME_SPAN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b")


@dataclass
class Candidate:
    name: str
    score: int
    source: str


def normalize_text(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_plausible_name(name: str) -> bool:
    if not name:
        return False
    name = name.strip(" .,:;|\t\n\r")
    low = name.lower()
    if any(tok in low for tok in [
        "course", "lecture", "syllabus", "schedule", "assignment", "overview",
        "slides", "notes", "week", "office", "hours", "contact", "homepage",
        "department", "university", "candidate", "talk", "seminar", "project",
        "http", "www", "pdf", "ppt",
    ]):
        return False
    if any(ch in name for ch in ["<", ">", "=", "#", "_", "/", "\\", "\""]):
        return False

    parts = [p for p in name.split() if p]
    if len(parts) < 2 or len(parts) > 5:
        return False
    if sum(ch.isdigit() for ch in name) > 0:
        return False

    valid = 0
    for p in parts:
        if re.fullmatch(r"[A-Za-z][A-Za-z\.'\-]{0,29}", p):
            valid += 1
    return valid >= 2


def parse_labeled_names(text: str, source: str) -> list[Candidate]:
    out: list[Candidate] = []
    cleaned = normalize_text(text)
    for pattern in LABEL_PATTERNS:
        for match in pattern.finditer(cleaned):
            name = normalize_text(match.group(1))
            if not is_plausible_name(name):
                continue
            out.append(Candidate(name=name, score=8, source=source))
    return out


def parse_context_names(text: str, source: str) -> list[Candidate]:
    out: list[Candidate] = []
    cleaned = normalize_text(text)
    low = cleaned.lower()
    if not any(k in low for k in ["instructor", "professor", "lecturer", "taught by", "faculty"]):
        return out

    for match in NAME_SPAN.finditer(cleaned):
        name = match.group(1).strip()
        if not is_plausible_name(name):
            continue
        window_start = max(0, match.start() - 80)
        window_end = min(len(low), match.end() + 80)
        window = low[window_start:window_end]
        if any(k in window for k in ["instructor", "professor", "lecturer", "taught by", "faculty"]):
            out.append(Candidate(name=name, score=4, source=source))
    return out


def build_queries(university_slug: str, course_slug: str) -> list[str]:
    uni = UNIVERSITY_MAP.get(university_slug, university_slug.replace("_", " ").replace("-", " ").title())
    course = course_slug.replace("_", " ").replace("-", " ")
    return [
        f"{uni} {course} instructor",
        f"{uni} {course} professor",
        f"{uni} {course} syllabus instructor",
    ]


def extract_candidates(hits: list[SearchHit]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for idx, hit in enumerate(hits):
        rank_bonus = max(0, 3 - idx)
        src = hit.url
        title_cands = parse_labeled_names(hit.title, src) + parse_context_names(hit.title, src)
        desc_cands = parse_labeled_names(hit.description, src) + parse_context_names(hit.description, src)
        for c in title_cands:
            candidates.append(Candidate(c.name, c.score + rank_bonus + 1, src))
        for c in desc_cands:
            candidates.append(Candidate(c.name, c.score + rank_bonus, src))
    return candidates


def pick_best(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None

    merged: dict[str, Candidate] = {}
    for c in candidates:
        key = c.name.lower()
        if key not in merged:
            merged[key] = Candidate(name=c.name, score=c.score, source=c.source)
        else:
            merged[key].score += c.score

    ranked = sorted(merged.values(), key=lambda x: x.score, reverse=True)
    if not ranked:
        return None

    best = ranked[0]
    second_score = ranked[1].score if len(ranked) > 1 else -1

    if best.score >= 10 and (best.score - second_score >= 2):
        return best
    return None


def backfill(limit: int | None = None, count: int = 8) -> int:
    sc = SearchClient()
    if not sc.has_key:
        raise RuntimeError(
            "No search API key found. Set TAVILY_API_KEY or SERPER_API_KEY or BRAVE_API_KEY."
        )

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT id, course_name
        FROM courses
        WHERE instructor_name IS NULL OR trim(instructor_name) = ''
        ORDER BY course_name
        """
    ).fetchall()
    if limit:
        rows = rows[:limit]

    print(f"Provider: {sc.provider}")
    print(f"Courses to process: {len(rows)}")

    updated = 0
    skipped = 0
    errors = 0

    for idx, (row_id, course_name) in enumerate(rows, 1):
        try:
            if "/" not in course_name:
                print(f"[{idx}/{len(rows)}] - {course_name} (bad course_name format)")
                skipped += 1
                continue

            uni, course = course_name.split("/", 1)
            all_candidates: list[Candidate] = []
            first_hit_url = None

            for query in build_queries(uni, course):
                hits = sc.search(query, count=count)
                if hits and first_hit_url is None:
                    first_hit_url = hits[0].url
                all_candidates.extend(extract_candidates(hits))

            best = pick_best(all_candidates)
            if not best:
                print(f"[{idx}/{len(rows)}] - {course_name}")
                skipped += 1
                continue

            note_url = best.source or first_hit_url or ""
            notes = f"Extracted via web search ({sc.provider}) from: {note_url}" if note_url else f"Extracted via web search ({sc.provider})"
            conn.execute(
                """
                UPDATE courses
                SET instructor_name = ?,
                    confidence = 'extracted',
                    notes = ?,
                    last_updated = datetime('now')
                WHERE id = ?
                """,
                (best.name, notes, row_id),
            )
            updated += 1
            print(f"[{idx}/{len(rows)}] ✓ {course_name} -> {best.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{idx}/{len(rows)}] ERROR {course_name}: {exc}")
            errors += 1

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    with_instr = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE instructor_name IS NOT NULL AND trim(instructor_name) != ''"
    ).fetchone()[0]
    missing = total - with_instr
    conn.close()

    print("\n--- Summary ---")
    print(f"Updated this run: {updated}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")
    print(f"Total courses: {total}")
    print(f"With instructor: {with_instr}")
    print(f"Missing instructor: {missing}")

    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill instructor names by web search")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N missing courses")
    parser.add_argument("--count", type=int, default=8, help="Search results per query")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    backfill(limit=args.limit, count=args.count)


if __name__ == "__main__":
    main()
