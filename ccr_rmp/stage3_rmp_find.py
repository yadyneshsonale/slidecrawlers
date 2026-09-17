#!/usr/bin/env python3
"""Stage 3 — find each instructor on RateMyProfessors.

For every course whose instructor was found in Stage 2:
  1. resolve the college name -> RMP school (cached in school_cache),
  2. split co-instructors and search each one scoped to that school,
  3. verify the hit by surname and store one row per professor in
     course_professors (feedback CSE325: co-instructors saved separately),
  4. mirror the primary match into courses.rmp_* for the dashboard.

Standalone:  ./.venv/bin/python stage3_rmp_find.py [--example N] [--limit N]
             [--refresh] [--config config.yaml]
"""

from __future__ import annotations

import argparse
import time

from src import codes, common, rmp_api

_PROF_COLS = [
    "course_college", "instructor_name", "rmp_status", "match_type",
    "school_id", "school_name", "legacy_id", "matched_name", "profile_url",
    "department", "avg_rating_overall", "avg_difficulty_overall",
    "num_ratings_overall", "would_take_again_overall", "updated_at",
]

_MISS = {
    "rmp_status": "not_found", "match_type": None, "school_id": None,
    "school_name": None, "legacy_id": None, "matched_name": None,
    "profile_url": None, "department": None, "avg_rating_overall": None,
    "avg_difficulty_overall": None, "num_ratings_overall": None,
    "would_take_again_overall": None,
}


# --------------------------------------------------------------------------- #
# School cache
# --------------------------------------------------------------------------- #
def _cache_school(conn, college_name, node, status) -> None:
    conn.execute(
        """INSERT INTO school_cache
             (college_name, school_id, legacy_id, school_name, city, state, status, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(college_name) DO UPDATE SET
             school_id=excluded.school_id, legacy_id=excluded.legacy_id,
             school_name=excluded.school_name, city=excluded.city,
             state=excluded.state, status=excluded.status, updated_at=excluded.updated_at""",
        (
            college_name,
            node.get("id") if node else None,
            str(node.get("legacyId")) if node else None,
            node.get("name") if node else None,
            node.get("city") if node else None,
            node.get("state") if node else None,
            status,
            common.now(),
        ),
    )
    conn.commit()


def get_school(conn, cfg, college_name) -> dict | None:
    """Return the cached/looked-up RMP school row (dict) for a college name."""
    if not college_name:
        return None
    row = conn.execute(
        "SELECT * FROM school_cache WHERE college_name = ?", (college_name,)
    ).fetchone()
    if row and row["status"]:
        return dict(row)
    node = rmp_api.find_school(college_name)
    _cache_school(conn, college_name, node, "found" if node else "not_found")
    row = conn.execute(
        "SELECT * FROM school_cache WHERE college_name = ?", (college_name,)
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Professor rows
# --------------------------------------------------------------------------- #
def upsert_professor(conn, course_college, name, res) -> None:
    vals = dict(res)
    vals["course_college"] = course_college
    vals["instructor_name"] = name
    vals["updated_at"] = common.now()
    placeholders = ", ".join("?" for _ in _PROF_COLS)
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in _PROF_COLS
        if c not in ("course_college", "instructor_name")
    )
    conn.execute(
        f"INSERT INTO course_professors ({', '.join(_PROF_COLS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(course_college, instructor_name) DO UPDATE SET {updates}",
        [vals.get(c) for c in _PROF_COLS],
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# Core resolve
# --------------------------------------------------------------------------- #
def resolve(conn, cfg, course, persist: bool = True) -> dict:
    key = course["course_college"]
    out = {
        "course_college": key,
        "course_code": course.get("course_code"),
        "college_name": course.get("college_name"),
        "instructor": course.get("instructor"),
        "school": None,
        "professors": [],
        "rmp_status": None,
    }

    names = (
        codes.split_instructors(course.get("instructor"))
        if course.get("instructor_status") == "found"
        else []
    )
    if not names:
        out["rmp_status"] = "no_instructor"
        if persist:
            common.update_course(conn, key, rmp_status="no_instructor", stage="rmp")
        return out

    school = get_school(conn, cfg, course.get("college_name"))
    out["school"] = school

    if not school or school.get("status") != "found":
        for nm in names:
            res = dict(_MISS, rmp_status="school_not_found")
            out["professors"].append({"name": nm, **res})
            if persist:
                upsert_professor(conn, key, nm, res)
        out["rmp_status"] = "school_not_found"
        if persist:
            common.update_course(conn, key, rmp_status="school_not_found", stage="rmp")
        return out

    delay = float((cfg.get("rmp") or {}).get("delay") or 0)
    primary = None
    any_found = False
    for nm in names:
        try:
            nodes = rmp_api.search_teachers(nm, school["school_id"])
            node, mt = rmp_api.pick_match(nm, nodes)
        except Exception as e:  # noqa: BLE001 — record and continue
            res = dict(_MISS, rmp_status="error", match_type=str(e)[:60],
                       school_id=school["school_id"], school_name=school["school_name"])
            out["professors"].append({"name": nm, **res})
            if persist:
                upsert_professor(conn, key, nm, res)
            continue

        if node:
            res = rmp_api.teacher_result(node, mt)
            res["school_id"] = school["school_id"]
            res["school_name"] = school["school_name"]
            any_found = True
            if primary is None:
                primary = res
        else:
            res = dict(_MISS, rmp_status="not_found", match_type=mt,
                       school_id=school["school_id"], school_name=school["school_name"])
        out["professors"].append({"name": nm, **res})
        if persist:
            upsert_professor(conn, key, nm, res)
        if delay:
            time.sleep(delay)

    out["rmp_status"] = "found" if any_found else "not_found"
    if persist:
        fields = {
            "rmp_status": out["rmp_status"],
            "rmp_school_id": school["school_id"],
            "rmp_school_name": school["school_name"],
            "stage": "rmp",
        }
        if primary:
            fields.update(
                rmp_legacy_id=primary["legacy_id"],
                rmp_matched_name=primary["matched_name"],
                rmp_profile_url=primary["profile_url"],
                rmp_avg_rating_overall=primary["avg_rating_overall"],
                rmp_avg_difficulty_overall=primary["avg_difficulty_overall"],
                rmp_num_ratings_overall=primary["num_ratings_overall"],
                rmp_would_take_again_overall=primary["would_take_again_overall"],
            )
        common.update_course(conn, key, **fields)
    return out


def run_one(conn, cfg, course) -> dict:
    """Entry point used by the review app."""
    return resolve(conn, cfg, course, persist=True)


# --------------------------------------------------------------------------- #
# Batch selection
# --------------------------------------------------------------------------- #
def _pending(conn, refresh: bool) -> list[dict]:
    if refresh:
        sql = "SELECT * FROM courses WHERE instructor_status='found' ORDER BY course_college"
    else:
        sql = ("SELECT * FROM courses WHERE instructor_status='found' "
               "AND rmp_status IS NULL ORDER BY course_college")
    return [dict(r) for r in conn.execute(sql)]


def _mark_no_instructor(conn) -> int:
    cur = conn.execute(
        "UPDATE courses SET rmp_status='no_instructor', stage='rmp', updated_at=? "
        "WHERE (instructor_status IS NULL OR instructor_status<>'found') "
        "AND rmp_status IS NULL",
        (common.now(),),
    )
    conn.commit()
    return cur.rowcount


# --------------------------------------------------------------------------- #
# Visual output
# --------------------------------------------------------------------------- #
def _show(out: dict) -> None:
    print(common.rule(f"{out['course_code']}  —  {out['college_name']}"))
    print(common.fmt_kv({
        "instructor (Stage 2)": out.get("instructor"),
        "rmp_status": out.get("rmp_status"),
    }))
    school = out.get("school")
    if school:
        print(common.fmt_kv({
            "school matched": f"{school.get('school_name')}  (id {school.get('legacy_id')})",
        }))
    profs = out.get("professors") or []
    if profs:
        print()
        rows = []
        for p in profs:
            rows.append({
                "instructor": p.get("name"),
                "matched": p.get("matched_name") or f"— {p.get('rmp_status')}",
                "type": p.get("match_type"),
                "rating": p.get("avg_rating_overall"),
                "diff": p.get("avg_difficulty_overall"),
                "n": p.get("num_ratings_overall"),
                "dept": p.get("department"),
            })
        common.print_table(rows, [
            ("instructor", 20), ("matched", 20), ("type", 9),
            ("rating", 6), ("diff", 4), ("n", 4), ("dept", 16),
        ])
        for p in profs:
            if p.get("profile_url"):
                print(f"    {p.get('name')}: {p['profile_url']}")
    print()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 3: match instructors on RateMyProfessors.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--example", type=int, metavar="N",
                    help="preview N found-instructor courses (read-only, no DB writes)")
    ap.add_argument("--limit", type=int, default=None, help="cap how many courses to process")
    ap.add_argument("--refresh", action="store_true",
                    help="re-resolve courses that already have an rmp_status")
    args = ap.parse_args()

    cfg = common.load_config(args.config)
    conn = common.open_db(cfg["db_path"])

    if args.example:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM courses WHERE instructor_status='found' "
            "ORDER BY course_college LIMIT ?", (args.example,)
        )]
        print(common.rule("STAGE 3 EXAMPLE — RateMyProfessors matching (read-only)"))
        print(f"previewing {len(rows)} course(s)\n")
        for course in rows:
            _show(resolve(conn, cfg, course, persist=False))
        return

    marked = _mark_no_instructor(conn)
    pending = _pending(conn, args.refresh)
    if args.limit:
        pending = pending[: args.limit]
    print(common.rule("STAGE 3 — RateMyProfessors matching"))
    print(f"courses to process: {len(pending)}   (marked no_instructor: {marked})\n")

    counts: dict[str, int] = {}
    for i, course in enumerate(pending, 1):
        out = resolve(conn, cfg, course, persist=True)
        st = out.get("rmp_status") or "?"
        counts[st] = counts.get(st, 0) + 1
        found = sum(1 for p in out.get("professors", []) if p.get("rmp_status") == "found")
        print(f"[{i:>3}/{len(pending)}] {course['course_code']:12} "
              f"{course['college_name'][:34]:34} -> {st} ({found} prof)")

    print("\n" + common.rule("SUMMARY"))
    print(f"  by status   {counts}")


if __name__ == "__main__":
    main()
