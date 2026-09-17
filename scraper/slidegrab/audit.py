"""Audit the dataset: judge each course and its decks for validity.

For every ``dataset/<university>/<course>/`` folder this reports three things:

1. **Name validity** — is ``<course>`` a real course (code/name) or junk that
   leaked through discovery (a stray filename like ``lecture12.pdf``, an
   extension-suffixed slug, or a generic word like ``slides``/``lectures``)?
2. **File validity** — does each deck open and look like slides? PDFs are
   checked with :func:`slidegrab.verify.verify_pdf` (pages + slide-likeness);
   PPT/PPTX are checked for non-empty Office magic bytes.
3. **Flow / numbering** — for ``lecture_NN`` decks: how many, what range, are
   there gaps or duplicate numbers, and what fraction of the run is present.

Each course gets a verdict: ``valid`` / ``weak`` / ``invalid`` with reasons.

Usage:
    python -m slidegrab.audit                 # full report
    python -m slidegrab.audit --only-bad      # only weak/invalid courses
    python -m slidegrab.audit --json out.json # machine-readable dump
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.settings import settings
from slidegrab.verify import verify_pdf

_SLIDE_EXTS = (".pdf", ".ppt", ".pptx")
_ZIP_MAGIC = b"PK\x03\x04"            # pptx
_OLE_MAGIC = b"\xd0\xcf\x11\xe0"      # legacy ppt
_LECTURE_RE = re.compile(r"lecture_(\d{1,3})", re.IGNORECASE)

# Course-name shapes that indicate a real course code.
_CODE_LIKE = re.compile(
    r"^(?:[a-z]{2,4}[-_]?\d{2,5}[a-z]?"      # cs229, ee2703, col774, cs60050
    r"|\d{1,2}[._-]s?\d{2,3}[a-z]?"          # 6.036, 6_s191, 6-390
    r"|\d{2}-\d{3})$",                       # 11-785
    re.IGNORECASE,
)
# Obvious junk course names (generic words, or a filename used as a folder).
_JUNK_NAMES = frozenset({
    "slides", "lectures", "lecture", "course", "courses", "teaching",
    "materials", "notes", "schedule", "index", "home", "unknown",
})


@dataclass
class FileReport:
    name: str
    ext: str
    ok: bool
    pages: int = 0
    reason: str = ""


@dataclass
class CourseReport:
    university: str
    course: str
    verdict: str = "valid"            # valid | weak | invalid
    reasons: list[str] = field(default_factory=list)
    name_ok: bool = True
    n_files: int = 0
    n_valid_files: int = 0
    numbered: int = 0
    number_range: str = ""
    missing: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)
    coverage: float = 0.0
    files: list[FileReport] = field(default_factory=list)


def _name_is_valid(course: str) -> tuple[bool, str]:
    low = course.lower()
    if low in _JUNK_NAMES:
        return False, "generic/non-course name"
    if low.endswith((".pdf", ".ppt", ".pptx", ".html", ".htm")):
        return False, "filename used as course folder"
    if _CODE_LIKE.match(low):
        return True, ""
    # Multi-word descriptive names (e.g. "introduction_to_computational_genomics")
    # are acceptable courses; very short non-code tokens are suspect.
    if len(low) <= 3:
        return False, "too short / not a course code"
    return True, ""


def _check_file(path: Path) -> FileReport:
    ext = path.suffix.lower().lstrip(".")
    if path.stat().st_size == 0:
        return FileReport(path.name, ext, False, 0, "empty file")
    if ext == "pdf":
        vr = verify_pdf(path)
        return FileReport(path.name, ext, vr.valid, vr.pages, vr.reason)
    # PPT / PPTX: validate Office magic bytes (PyMuPDF can't render these).
    with open(path, "rb") as fh:
        head = fh.read(8)
    if ext == "pptx" and head.startswith(_ZIP_MAGIC):
        return FileReport(path.name, ext, True, 0, "")
    if ext == "ppt" and head.startswith(_OLE_MAGIC):
        return FileReport(path.name, ext, True, 0, "")
    return FileReport(path.name, ext, False, 0, "bad office magic bytes")


def audit_course(uni: str, course: str, course_dir: Path) -> CourseReport:
    rep = CourseReport(university=uni, course=course)

    name_ok, name_reason = _name_is_valid(course)
    rep.name_ok = name_ok
    if not name_ok:
        rep.reasons.append(name_reason)

    files = sorted(
        f for f in course_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _SLIDE_EXTS
    )
    rep.n_files = len(files)
    if not files:
        rep.verdict = "invalid"
        rep.reasons.append("no slide files")
        return rep

    nums: list[int] = []
    for f in files:
        fr = _check_file(f)
        rep.files.append(fr)
        if fr.ok:
            rep.n_valid_files += 1
        m = _LECTURE_RE.search(f.name)
        if m:
            nums.append(int(m.group(1)))

    # ---- numbering / flow analysis ----
    rep.numbered = len(nums)
    if nums:
        seen = sorted(nums)
        uniq = sorted(set(nums))
        lo, hi = uniq[0], uniq[-1]
        rep.number_range = f"{lo:02d}-{hi:02d}"
        full = set(range(lo, hi + 1))
        rep.missing = sorted(full - set(uniq))
        rep.duplicates = sorted({n for n in uniq if seen.count(n) > 1})
        span = hi - lo + 1
        rep.coverage = round(len(uniq) / span, 2) if span else 1.0

    # ---- verdict ----
    valid_ratio = rep.n_valid_files / rep.n_files
    if rep.n_valid_files == 0:
        rep.verdict = "invalid"
        rep.reasons.append("no file passed content verification")
    elif not name_ok:
        rep.verdict = "invalid"
    else:
        weak = False
        if valid_ratio < 0.6:
            rep.reasons.append(f"only {rep.n_valid_files}/{rep.n_files} files valid")
            weak = True
        if rep.n_valid_files == 1:
            rep.reasons.append("single deck (no lecture sequence)")
            weak = True
        if nums and rep.coverage < 0.7 and (rep.missing):
            rep.reasons.append(
                f"sequence gaps: missing {rep.missing} (coverage {rep.coverage:.0%})"
            )
            weak = True
        if rep.duplicates:
            rep.reasons.append(f"duplicate lecture numbers {rep.duplicates}")
            weak = True
        rep.verdict = "weak" if weak else "valid"
    return rep


def audit_dataset(data_dir: Path) -> list[CourseReport]:
    reports: list[CourseReport] = []
    for uni_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        for course_dir in sorted(p for p in uni_dir.iterdir() if p.is_dir()):
            reports.append(audit_course(uni_dir.name, course_dir.name, course_dir))
    return reports


def _print_report(reports: list[CourseReport], only_bad: bool) -> None:
    icon = {"valid": "OK  ", "weak": "WEAK", "invalid": "BAD "}
    shown = 0
    for r in reports:
        if only_bad and r.verdict == "valid":
            continue
        shown += 1
        head = (
            f"[{icon[r.verdict]}] {r.university}/{r.course}  "
            f"files={r.n_valid_files}/{r.n_files}"
        )
        if r.number_range:
            head += f"  lec {r.number_range} cov {r.coverage:.0%}"
        print(head)
        for reason in r.reasons:
            print(f"        - {reason}")
    print(f"\nshown: {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slidegrab.audit")
    parser.add_argument("--data-dir", default=str(settings.data_dir))
    parser.add_argument("--only-bad", action="store_true",
                        help="show only weak/invalid courses")
    parser.add_argument("--json", default=None, help="write full report to JSON file")
    args = parser.parse_args(argv)

    reports = audit_dataset(Path(args.data_dir))

    total = len(reports)
    valid = sum(1 for r in reports if r.verdict == "valid")
    weak = sum(1 for r in reports if r.verdict == "weak")
    invalid = sum(1 for r in reports if r.verdict == "invalid")
    bad_name = sum(1 for r in reports if not r.name_ok)
    decks = sum(r.n_files for r in reports)
    valid_decks = sum(r.n_valid_files for r in reports)

    _print_report(reports, args.only_bad)

    print("\n==== dataset audit summary ====")
    print(f"course folders   : {total}")
    print(f"  valid          : {valid}")
    print(f"  weak           : {weak}")
    print(f"  invalid        : {invalid}")
    print(f"bad/junk names   : {bad_name}")
    print(f"deck files       : {valid_decks}/{decks} pass content check")

    if args.json:
        Path(args.json).write_text(json.dumps([asdict(r) for r in reports], indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
