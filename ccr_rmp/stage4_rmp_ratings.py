#!/usr/bin/env python3
"""Stage 4 — pull each professor's ratings and keep only THIS course's.

For every matched professor (course_professors.rmp_status='found'):
  1. paginate all of their RMP ratings,
  2. keep only ratings whose free-text `class` maps to the course code
     (codes.class_matches — normalized-digit matching),
  3. store the kept ratings in course_ratings_raw and aggregate per professor,
  4. roll the course-level aggregate up into courses.course_*.

Standalone:  ./.venv/bin/python stage4_rmp_ratings.py [--example N] [--limit N]
             [--refresh] [--config config.yaml]
"""

from __future__ import annotations

import argparse
import json

from src import codes, common, rmp_api


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _agg(ratings: list[dict]) -> dict:
    q = [r["quality"] for r in ratings if r.get("quality") is not None]
    d = [r["difficulty"] for r in ratings if r.get("difficulty") is not None]
    wta = [r["would_take_again"] for r in ratings if r.get("would_take_again") in (0, 1)]
    return {
        "course_num_ratings": len(ratings),
        "course_avg_quality": round(sum(q) / len(q), 2) if q else None,
        "course_avg_difficulty": round(sum(d) / len(d), 2) if d else None,
        "course_would_take_again": round(100 * sum(wta) / len(wta), 1) if wta else None,
    }


def _classes(ratings: list[dict]) -> list[str]:
    return sorted({r["class"] for r in ratings if r.get("class")})


# --------------------------------------------------------------------------- #
# Per-professor
# --------------------------------------------------------------------------- #
def process_professor(conn, cfg, prof, course_code, persist: bool = True) -> dict:
    delay = float((cfg.get("rmp") or {}).get("delay") or 0)
    legacy = prof.get("legacy_id")
    result = {
        "instructor_name": prof.get("instructor_name"),
        "matched_name": prof.get("matched_name"),
        "legacy_id": legacy,
        "total": 0,
        "matched": [],
        "classes": [],
        "agg": _agg([]),
    }
    if not legacy:
        return result

    allr = rmp_api.fetch_all_ratings(legacy, delay=delay)
    matched = [r for r in allr if r.get("class") and codes.class_matches(course_code, r["class"])]
    result["total"] = len(allr)
    result["matched"] = matched
    result["classes"] = _classes(matched)
    result["agg"] = _agg(matched)

    if persist:
        key = prof["course_college"]
        conn.execute(
            "DELETE FROM course_ratings_raw WHERE course_college=? AND legacy_id=?",
            (key, legacy),
        )
        for r in matched:
            conn.execute(
                "INSERT OR REPLACE INTO course_ratings_raw "
                "(rating_id, course_college, legacy_id, class, quality, difficulty, "
                " clarity, helpful, would_take_again, grade, attendance, for_credit, "
                " online_class, textbook_use, thumbs_up, thumbs_down, date, comment, tags) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["rating_id"], key, legacy, r["class"], r["quality"], r["difficulty"],
                 r["clarity"], r["helpful"], r["would_take_again"], r["grade"],
                 r["attendance"], r["for_credit"], r["online_class"], r["textbook_use"],
                 r["thumbs_up"], r["thumbs_down"], r["date"], r["comment"], r["tags"]),
            )
        agg = result["agg"]
        conn.execute(
            "UPDATE course_professors SET course_num_ratings=?, course_avg_quality=?, "
            "course_avg_difficulty=?, course_would_take_again=?, matched_class_values=?, "
            "updated_at=? WHERE course_college=? AND instructor_name=?",
            (agg["course_num_ratings"], agg["course_avg_quality"], agg["course_avg_difficulty"],
             agg["course_would_take_again"], json.dumps(result["classes"]), common.now(),
             key, prof["instructor_name"]),
        )
        conn.commit()
    return result


def _course_aggregate(conn, key) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT quality, difficulty, would_take_again, class "
        "FROM course_ratings_raw WHERE course_college=?", (key,)
    )]
    agg = _agg(rows)
    classes = _classes(rows)
    common.update_course(
        conn, key,
        course_num_ratings=agg["course_num_ratings"],
        course_avg_quality=agg["course_avg_quality"],
        course_avg_difficulty=agg["course_avg_difficulty"],
        course_would_take_again=agg["course_would_take_again"],
        matched_class_values=json.dumps(classes),
        stage="ratings",
    )
    return agg


# --------------------------------------------------------------------------- #
# Per-course
# --------------------------------------------------------------------------- #
def resolve(conn, cfg, course, persist: bool = True) -> dict:
    key = course["course_college"]
    profs = [dict(r) for r in conn.execute(
        "SELECT * FROM course_professors WHERE course_college=? AND rmp_status='found'", (key,)
    )]
    results = [process_professor(conn, cfg, p, course["course_code"], persist) for p in profs]
    course_agg = _course_aggregate(conn, key) if persist else _agg(
        [r for res in results for r in res["matched"]]
    )
    return {
        "course_college": key,
        "course_code": course["course_code"],
        "college_name": course.get("college_name"),
        "professors": results,
        "course_agg": course_agg,
    }


def run_one(conn, cfg, course) -> dict:
    """Entry point used by the review app."""
    return resolve(conn, cfg, course, persist=True)


# --------------------------------------------------------------------------- #
# Batch selection
# --------------------------------------------------------------------------- #
def _pending(conn, refresh: bool) -> list[dict]:
    if refresh:
        sql = "SELECT * FROM courses WHERE rmp_status='found' ORDER BY course_college"
    else:
        sql = ("SELECT * FROM courses WHERE rmp_status='found' "
               "AND course_num_ratings IS NULL ORDER BY course_college")
    return [dict(r) for r in conn.execute(sql)]


# --------------------------------------------------------------------------- #
# Visual output
# --------------------------------------------------------------------------- #
def _show(out: dict) -> None:
    print(common.rule(f"{out['course_code']}  —  {out.get('college_name')}"))
    rows = []
    for p in out["professors"]:
        agg = p["agg"]
        rows.append({
            "professor": p.get("matched_name") or p.get("instructor_name"),
            "all": p.get("total"),
            "kept": agg["course_num_ratings"],
            "qual": agg["course_avg_quality"],
            "diff": agg["course_avg_difficulty"],
            "wta%": agg["course_would_take_again"],
            "classes": ", ".join(p.get("classes") or []),
        })
    common.print_table(rows, [
        ("professor", 22), ("all", 4), ("kept", 5),
        ("qual", 5), ("diff", 5), ("wta%", 6), ("classes", 26),
    ])
    ca = out["course_agg"]
    print()
    print(common.fmt_kv({
        "COURSE total kept": ca["course_num_ratings"],
        "COURSE avg quality": ca["course_avg_quality"],
        "COURSE avg difficulty": ca["course_avg_difficulty"],
        "COURSE would-take-again %": ca["course_would_take_again"],
    }))
    sample = [r for p in out["professors"] for r in p.get("matched", [])][:3]
    if sample:
        print("\n  sample individual ratings:")
        for r in sample:
            bits = [
                f"Q{r['quality']}", f"D{r['difficulty']}",
                f"clarity {r['clarity']}", f"helpful {r['helpful']}",
                f"grade {r['grade'] or '·'}", f"credit {'Y' if r['for_credit'] else 'N'}",
                f"attend {r['attendance'] or '·'}", f"txt {r['textbook_use']}",
                f"online {'Y' if r['online_class'] else 'N'}",
                f"wta {r['would_take_again']}",
            ]
            print(f"    [{r['date']} · {r['class']}] " + "  ".join(bits))
            if r.get("tags"):
                print(f"        tags: {r['tags']}")
    print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 4: keep each course's RMP ratings.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--example", type=int, metavar="N",
                    help="preview N matched courses (read-only, no DB writes)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull ratings for courses already aggregated")
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    conn = common.open_db(cfg["db_path"])

    if args.example:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM courses WHERE rmp_status='found' "
            "ORDER BY course_college LIMIT ?", (args.example,)
        )]
        print(common.rule("STAGE 4 EXAMPLE — course-specific ratings (read-only)"))
        print(f"previewing {len(rows)} course(s)\n")
        for course in rows:
            _show(resolve(conn, cfg, course, persist=False))
        return

    pending = _pending(conn, args.refresh)
    if args.limit:
        pending = pending[: args.limit]
    print(common.rule("STAGE 4 — course-specific RMP ratings"))
    print(f"courses to process: {len(pending)}\n")

    total_kept = 0
    for i, course in enumerate(pending, 1):
        out = resolve(conn, cfg, course, persist=True)
        kept = out["course_agg"]["course_num_ratings"]
        total_kept += kept
        q = out["course_agg"]["course_avg_quality"]
        print(f"[{i:>3}/{len(pending)}] {course['course_code']:12} "
              f"{(course['college_name'] or '')[:30]:30} -> {kept:>3} ratings kept, "
              f"avg quality {q}")

    print("\n" + common.rule("SUMMARY"))
    print(f"  total course-specific ratings kept: {total_kept}")


if __name__ == "__main__":
    main()
