"""Stage 2 core — extract the course instructor with Qwen2.5-VL (smarter pick).

For PDF/PPT decks we read the page text with poppler, score each page for
instructor cues (Instructor / Professor / Lecturer / "taught by" ...), and send
only the title page plus the top cue pages to the vision model. For HTML we send
a focused text window around the first cue. The model must return strict JSON and
`null` when no instructor is present; we never guess from the URL or course code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import codes, common, fetch, llm

CUE_RE = re.compile(
    r"\b(instructors?|professors?|lecturers?|faculty|teacher|"
    r"taught\s+by|instructed\s+by|presented\s+by|prepared\s+by|"
    r"teaching\s+staff|course\s+staff|prof\.?)\b",
    re.IGNORECASE,
)

_HONORIFIC_RE = re.compile(r"^(dr|prof|professor|mr|mrs|ms|miss)\.?\s+", re.IGNORECASE)
_DEGREE_RE = re.compile(r"[,\s]+(ph\.?d|m\.?d|m\.?sc|m\.?s|b\.?s)\.?$", re.IGNORECASE)
_STOPWORDS = {
    "university", "college", "department", "dept", "school", "course", "lecture",
    "lectures", "syllabus", "instructor", "professor", "lecturer", "spring",
    "fall", "autumn", "winter", "summer", "semester", "teaching", "assistant",
    "staff", "home", "welcome", "introduction",
}

SYSTEM = (
    "You identify the INSTRUCTOR — the professor or lecturer who teaches the "
    "course — from course materials. You are precise and you never guess.\n"
    "Rules:\n"
    "- Return the primary instructor's full name (first and last). If the course "
    "is explicitly co-taught, join the names with ' & '.\n"
    "- The instructor is the person labelled Instructor, Professor, Lecturer, "
    "Faculty, 'Faculty Name', or 'Taught by'. On a title slide it may be the name "
    "shown under the course title.\n"
    "- IGNORE teaching assistants (TAs), authors of cited papers, textbook "
    "authors, guest speakers, department names, and the university name.\n"
    "- Drop titles and degrees (Dr., Prof., PhD).\n"
    "- If no instructor is clearly present, return null. Never invent a name and "
    "never derive one from the URL or the course code.\n"
    'Respond with STRICT JSON only, no prose: '
    '{"instructor": "First Last" | null, "confidence": 0.0-1.0}'
)


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #
def pick_pdf_pages(pdf_path: str, cfg: dict[str, Any]) -> tuple[list[int], list[int], list[str]]:
    """Return (pages_to_send, cue_pages, per_page_texts) using the deck's text."""
    icfg = cfg.get("instructor", {})
    scan = int(icfg.get("scan_pages", 12))
    cap = int(icfg.get("max_vlm_pages", 3))
    texts = fetch.page_texts(pdf_path, scan)
    cue_pages = [i + 1 for i, t in enumerate(texts) if CUE_RE.search(t)]
    ordered: list[int] = [1] + [p for p in cue_pages if p != 1]  # title slide first
    seen: list[int] = []
    for p in ordered:
        if p not in seen:
            seen.append(p)
    return seen[:cap], cue_pages, texts


def focus_html_text(text: str, cfg: dict[str, Any]) -> str:
    """Keep the top of the page plus a window around the first instructor cue."""
    budget = int(cfg.get("fetch", {}).get("max_html_chars", 6000))
    top = text[:1500]
    m = CUE_RE.search(text)
    if not m or m.start() < 1500:
        return text[:budget]
    lo = max(0, m.start() - 300)
    window = text[lo: lo + 2000]
    return (top + "\n...\n" + window)[:budget]


# --------------------------------------------------------------------------- #
# Name validation
# --------------------------------------------------------------------------- #
def clean_name(raw: Any, course_code: str | None = None) -> str | None:
    if not isinstance(raw, str):
        return None
    name = raw.strip().strip(".,;")
    if not name or name.lower() in {"null", "none", "n/a", "na", "unknown"}:
        return None
    parts = [p.strip() for p in name.split("&")]  # co-taught -> validate each
    kept: list[str] = []
    for part in parts:
        cleaned = _clean_single(part, course_code)
        if cleaned:
            kept.append(cleaned)
    if not kept:
        return None
    return " & ".join(kept)


def _clean_single(part: str, course_code: str | None) -> str | None:
    prev = None
    while prev != part:  # strip stacked honorifics (e.g. "Prof. Dr. ...")
        prev = part
        part = _HONORIFIC_RE.sub("", part).strip()
    part = _DEGREE_RE.sub("", part).strip()
    if not part or any(ch.isdigit() for ch in part):
        return None
    tokens = part.split()
    if len(tokens) < 2 or len(tokens) > 5:
        return None
    if any(t.lower().strip(".,") in _STOPWORDS for t in tokens):
        return None
    if course_code and codes.normalize_alnum(part) == codes.normalize_alnum(course_code):
        return None
    return part


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _artifact(course: dict[str, Any]) -> dict[str, Any]:
    raw = course.get("slide_artifact")
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def extract(course: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Run the smarter-pick extraction; return instructor_* fields + debug."""
    out: dict[str, Any] = {
        "instructor": None,
        "instructor_confidence": None,
        "instructor_source": "llm",
        "instructor_status": None,
        "debug": {},
    }
    if course.get("slide_status") != "ok":
        out["instructor_status"] = "fetch_failed"
        out["debug"] = {"reason": f"slide_status={course.get('slide_status')}"}
        return out

    code = course.get("course_code")
    college = course.get("college_name")
    art = _artifact(course)
    try:
        if course.get("slide_kind") in ("pdf", "ppt"):
            pdf_path = art.get("pdf")
            out_dir = Path(pdf_path).parent
            pages, cue_pages, texts = pick_pdf_pages(pdf_path, cfg)
            rendered = fetch.render_pages(pdf_path, out_dir, pages)
            images = [rendered[p] for p in pages if p in rendered]
            snippets = [
                f"[Page {p}]\n{texts[p - 1][:1200]}"
                for p in pages if 1 <= p <= len(texts) and texts[p - 1]
            ]
            text_block = "\n\n".join(snippets)
            user = (
                f"Course: {code} at {college}. The images are the title slide and "
                f"the slides most likely to name the instructor; the exact text of "
                f"those slides is included below. Extract the instructor per the "
                f"rules. JSON only."
                + (f"\n\n---\n{text_block}" if text_block else "")
            )
            reply = llm.chat(cfg, SYSTEM, user, image_paths=images)
            out["debug"] = {
                "mode": "image", "pages_sent": pages, "cue_pages": cue_pages,
                "images": images, "raw": reply,
            }
        else:  # html
            text = Path(art.get("text")).read_text(encoding="utf-8")
            focus = focus_html_text(text, cfg)
            user = (
                f"Course: {code} at {college}. Below is text from the course page. "
                f"Extract the instructor per the rules. JSON only.\n\n---\n{focus}"
            )
            reply = llm.chat(cfg, SYSTEM, user)
            out["debug"] = {"mode": "text", "focus_chars": len(focus), "raw": reply}
    except Exception as exc:  # noqa: BLE001
        out["instructor_status"] = "error"
        out["debug"] = {"error": f"{type(exc).__name__}: {exc}"}
        return out

    parsed = llm.extract_json(reply) or {}
    name = clean_name(parsed.get("instructor"), code)
    if name:
        conf = parsed.get("confidence")
        out["instructor"] = name
        out["instructor_confidence"] = float(conf) if isinstance(conf, (int, float)) else None
        out["instructor_status"] = "found"
    else:
        out["instructor_status"] = "no_instructor"
        out["instructor_confidence"] = 0.0
    return out


def run_one(conn, cfg: dict[str, Any], course: dict[str, Any]) -> dict[str, Any]:
    """Extract + persist for a single course row."""
    res = extract(course, cfg)
    common.update_course(
        conn, course["course_college"],
        instructor=res["instructor"],
        instructor_confidence=res["instructor_confidence"],
        instructor_source=res["instructor_source"],
        instructor_status=res["instructor_status"],
        instructor_debug=json.dumps(res["debug"]),
        stage="instructor",
    )
    return res
