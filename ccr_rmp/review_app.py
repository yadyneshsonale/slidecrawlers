#!/usr/bin/env python3
"""Interactive review app for the ccr_rmp pipeline.

A local web UI to inspect every course's fetched slide, the extracted instructor
(with the exact pages/text sent to the model and the raw reply), re-run a stage
on demand, and record your feedback / corrections.

    ./.venv/bin/python review_app.py        # then open http://127.0.0.1:5057
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import stage1_fetch_slide as stage1
import stage3_rmp_find as stage3
import stage4_rmp_ratings as stage4
from src import common, instructor as instructor_mod

CFG = common.load_config()
ART_ROOT = Path(CFG["artifacts_dir"]).resolve()

app = Flask(__name__)
app.secret_key = os.urandom(24)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def db():
    return common.open_db(CFG["db_path"])


def _json(raw) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _course(conn, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM courses WHERE course_college=?", (key,)).fetchone()
    return dict(row) if row else None


def _feedback(conn, key: str) -> dict:
    row = conn.execute("SELECT * FROM feedback WHERE course_college=?", (key,)).fetchone()
    return dict(row) if row else {
        "instructor_ok": None, "instructor_correction": None,
        "rmp_ok": None, "notes": None, "needs_review": 0,
    }


def _professors(conn, key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM course_professors WHERE course_college=? ORDER BY id", (key,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["matched_classes"] = json.loads(d["matched_class_values"]) if d.get("matched_class_values") else []
        out.append(d)
    return out


def _ratings(conn, key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM course_ratings_raw WHERE course_college=? "
        "ORDER BY date DESC, rating_id", (key,)
    ).fetchall()
    return [dict(r) for r in rows]


FILTERS = [
    ("all", "All", lambda c: True),
    ("found", "Instructor found", lambda c: c["instructor_status"] == "found"),
    ("null", "Instructor NULL", lambda c: c["instructor_status"] == "no_instructor"),
    ("rmp", "On RMP", lambda c: c["rmp_status"] == "found"),
    ("rmp_miss", "RMP not found", lambda c: c["rmp_status"] in ("not_found", "school_not_found")),
    ("ratings", "Has course ratings", lambda c: (c["course_num_ratings"] or 0) > 0),
    ("fetch_error", "Fetch failed", lambda c: c["slide_status"] == "error"),
    ("review", "Needs review", lambda c: c["fb_needs_review"]),
]


def _all_courses(conn) -> list[dict]:
    sql = """SELECT c.*, f.needs_review AS fb_needs_review,
                    f.instructor_ok AS fb_instructor_ok
             FROM courses c LEFT JOIN feedback f
               ON f.course_college = c.course_college
             ORDER BY c.row_index"""
    return [dict(r) for r in conn.execute(sql).fetchall()]


@app.context_processor
def inject_globals():
    conn = db()
    try:
        n = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        fetched = conn.execute("SELECT COUNT(*) FROM courses WHERE slide_status='ok'").fetchone()[0]
        found = conn.execute("SELECT COUNT(*) FROM courses WHERE instructor_status='found'").fetchone()[0]
        rmp = conn.execute("SELECT COUNT(*) FROM courses WHERE rmp_status='found'").fetchone()[0]
    finally:
        conn.close()
    return {"progress": f"{n} courses · {fetched} fetched · {found} instructors · {rmp} on RMP"}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    active = request.args.get("filter", "all")
    conn = db()
    try:
        rows = _all_courses(conn)
    finally:
        conn.close()
    filters = [
        {"key": k, "label": lbl, "count": sum(1 for c in rows if pred(c))}
        for k, lbl, pred in FILTERS
    ]
    pred = next((p for k, _, p in FILTERS if k == active), FILTERS[0][2])
    courses = [c for c in rows if pred(c)]
    return render_template("index.html", courses=courses, filters=filters, active=active)


@app.route("/course")
def course():
    key = request.args.get("c", "")
    conn = db()
    try:
        c = _course(conn, key)
        if not c:
            abort(404)
        fb = _feedback(conn, key)
        profs = _professors(conn, key)
        ratings = _ratings(conn, key)
    finally:
        conn.close()
    return render_template(
        "course.html", course=c, fb=fb, professors=profs, ratings=ratings,
        sart=_json(c.get("slide_artifact")),
        idbg=_json(c.get("instructor_debug")),
    )


@app.route("/artifact")
def artifact():
    raw = request.args.get("path", "")
    target = Path(raw).resolve()
    if target != ART_ROOT and ART_ROOT not in target.parents:
        abort(403)                       # path traversal guard
    if not target.is_file():
        abort(404)
    mime = "text/plain" if target.suffix == ".txt" else None
    return send_file(str(target), mimetype=mime)


@app.route("/run/<stage>", methods=["POST"])
def run(stage: str):
    key = request.args.get("c", "")
    conn = db()
    try:
        c = _course(conn, key)
        if not c:
            abort(404)
        if stage == "fetch":
            res = stage1.run_one(conn, CFG, c)
            flash(f"Stage 1: {res['slide_kind'] or '-'} · {res['slide_status']}")
        elif stage == "instructor":
            res = instructor_mod.run_one(conn, CFG, c)
            flash(f"Stage 2: {res['instructor_status']} → {res.get('instructor') or 'NULL'}")
        elif stage == "rmp":
            res = stage3.run_one(conn, CFG, c)
            nfound = sum(1 for p in res.get("professors", []) if p.get("rmp_status") == "found")
            flash(f"Stage 3: {res['rmp_status']} · {nfound} professor(s) matched")
        elif stage == "ratings":
            res = stage4.run_one(conn, CFG, c)
            ca = res.get("course_agg", {})
            flash(f"Stage 4: {ca.get('course_num_ratings', 0)} course rating(s) kept "
                  f"· avg quality {ca.get('course_avg_quality')}")
        else:
            flash(f"Stage '{stage}' is not available yet.")
    finally:
        conn.close()
    return redirect(url_for("course", c=key))


@app.route("/set_source", methods=["POST"])
def set_source():
    """Edit the source fields (course code, college, slide link) for a course.

    Changing the slide link clears the fetched-slide fields so Run Stage 1
    re-fetches. college_name feeds the RMP school lookup; course_code feeds
    Stage 4 class matching — both take effect on the next Run Stage 3 / 4.
    """
    key = request.args.get("c", "")
    f = request.form
    conn = db()
    try:
        c = _course(conn, key)
        if not c:
            abort(404)
        code = (f.get("course_code") or "").strip() or None
        college = (f.get("college_name") or "").strip() or None
        link = (f.get("course_slide_links") or "").strip() or None
        fields = dict(course_code=code, college_name=college, course_slide_links=link)
        link_changed = (link or "") != (c.get("course_slide_links") or "")
        if link_changed:
            fields.update(slide_url_used=None, slide_kind=None,
                          slide_artifact=None, slide_status=None)
        common.update_course(conn, key, **fields)
        msg = "Source updated."
        if link_changed:
            msg += " Slide link changed — press Run Stage 1 to re-fetch, then Stage 2."
        flash(msg)
    finally:
        conn.close()
    return redirect(url_for("course", c=key))


@app.route("/set_instructor", methods=["POST"])
def set_instructor():
    """Manually set/edit the instructor(s) for a course, then run Stage 3/4.

    Saving marks the instructor 'found' (source human) and clears any prior
    Stage 3/4 results for the course so a fresh Run Stage 3/4 starts clean.
    Separate co-instructors with & ; / + (they become separate RMP rows).
    """
    key = request.args.get("c", "")
    name = (request.form.get("instructor") or "").strip()
    conn = db()
    try:
        c = _course(conn, key)
        if not c:
            abort(404)
        # wipe downstream results for a clean re-run
        conn.execute("DELETE FROM course_professors WHERE course_college=?", (key,))
        conn.execute("DELETE FROM course_ratings_raw WHERE course_college=?", (key,))
        conn.commit()
        reset = dict(
            rmp_status=None, rmp_school_id=None, rmp_school_name=None,
            rmp_legacy_id=None, rmp_matched_name=None, rmp_profile_url=None,
            rmp_avg_rating_overall=None, rmp_avg_difficulty_overall=None,
            rmp_num_ratings_overall=None, rmp_would_take_again_overall=None,
            course_num_ratings=None, course_avg_quality=None,
            course_avg_difficulty=None, course_would_take_again=None,
            matched_class_values=None,
        )
        if name:
            common.update_course(
                conn, key, instructor=name, instructor_source="human",
                instructor_status="found", instructor_confidence=1.0,
                stage="instructor", **reset,
            )
            flash(f"Instructor set to “{name}”. Now press Run Stage 3, then Run Stage 4.")
        else:
            common.update_course(
                conn, key, instructor=None, instructor_source="human",
                instructor_status="no_instructor", instructor_confidence=None,
                stage="instructor", **reset,
            )
            flash("Instructor cleared (marked no_instructor).")
    finally:
        conn.close()
    return redirect(url_for("course", c=key))


@app.route("/feedback", methods=["POST"])
def feedback():
    key = request.args.get("c", "")
    f = request.form

    def _tri(name):
        v = f.get(name, "")
        return int(v) if v in ("0", "1") else None

    instructor_ok = _tri("instructor_ok")
    rmp_ok = _tri("rmp_ok")
    correction = (f.get("instructor_correction") or "").strip() or None
    notes = (f.get("notes") or "").strip() or None
    needs_review = 1 if f.get("needs_review") else 0
    apply_corr = bool(f.get("apply_correction")) and bool(correction)

    conn = db()
    try:
        conn.execute(
            """INSERT INTO feedback
                   (course_college, instructor_ok, instructor_correction,
                    rmp_ok, notes, needs_review, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(course_college) DO UPDATE SET
                   instructor_ok=excluded.instructor_ok,
                   instructor_correction=excluded.instructor_correction,
                   rmp_ok=excluded.rmp_ok, notes=excluded.notes,
                   needs_review=excluded.needs_review, updated_at=excluded.updated_at""",
            (key, instructor_ok, correction, rmp_ok, notes, needs_review, common.now()),
        )
        conn.commit()
        if apply_corr:
            common.update_course(
                conn, key, instructor=correction, instructor_source="human",
                instructor_status="found", instructor_confidence=1.0,
            )
        flash("Feedback saved." + (" Instructor updated." if apply_corr else ""))
    finally:
        conn.close()
    return redirect(url_for("course", c=key))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5057"))
    app.run(host="127.0.0.1", port=port, debug=False)
