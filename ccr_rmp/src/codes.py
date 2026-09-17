"""Course-code and college parsing + RMP `class` matching.

`course_college` is formatted "<code> - <College>" (e.g. "CSC476 - American
University"). We split on the FIRST " - " so college names that themselves
contain a hyphen (rare) stay intact on the right side.

RMP stores the course a rating was for in a free-text `class` field: the same
course shows up as "CSC476", "csc 476", "476", etc. We match a course by
collapsing to a normalized alnum key and a digits-only key.
"""

from __future__ import annotations

import re

_SEP = " - "


def split_course_college(course_college: str | None) -> tuple[str | None, str | None]:
    """'CSC476 - American University' -> ('CSC476', 'American University')."""
    if not course_college:
        return None, None
    text = course_college.strip()
    if _SEP in text:
        left, right = text.split(_SEP, 1)
        return left.strip() or None, right.strip() or None
    return text or None, None


def clean_code(course_code: str | None, course_college: str | None) -> str | None:
    """Return the course code, repairing cells that hold the whole '<code> - <College>'.

    Falls back to the code parsed from `course_college` when `course_code` is empty.
    """
    code = (course_code or "").strip()
    if _SEP in code:                       # dirty cell: "CS310 - Arizona State University"
        code = code.split(_SEP, 1)[0].strip()
    if not code:
        code, _ = split_course_college(course_college)
    return code or None


def parse_num_ratings(value: str | None) -> float | None:
    """'6.0' -> 6.0 ; '' / None -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_alnum(code: str | None) -> str:
    """'CSC 476' / 'csc-476' -> 'CSC476' (uppercase, alphanumerics only)."""
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def digits(code: str | None) -> str:
    """Digit-only key: 'COMP2710' -> '2710'."""
    return re.sub(r"\D", "", code or "")


def letters(code: str | None) -> str:
    """Letter-only prefix: 'COMP2710' -> 'COMP' (uppercase)."""
    return re.sub(r"[^A-Za-z]", "", code or "").upper()


def class_matches(course_code: str | None, rmp_class: str | None) -> bool:
    """True when an RMP free-text `class` refers to `course_code`.

    Rules (in order):
      1. Exact normalized-alnum match ("CSC476" == "csc 476").
      2. Same digits AND (the RMP class carries no letters, or its letters share a
         prefix with the course code's letters) — so "476"/"CSC476" match CSC476
         but "MAT476" does not.
    """
    want_alnum = normalize_alnum(course_code)
    have_alnum = normalize_alnum(rmp_class)
    if not want_alnum or not have_alnum:
        return False
    if want_alnum == have_alnum:
        return True

    want_d, have_d = digits(course_code), digits(rmp_class)
    if not want_d or want_d != have_d:
        return False
    want_l, have_l = letters(course_code), letters(rmp_class)
    if not have_l:                         # bare number, e.g. "476"
        return True
    return want_l.startswith(have_l) or have_l.startswith(want_l)


_INSTR_SPLIT_RE = re.compile(r"\s*(?:&|\band\b|;|/|\+)\s*", re.IGNORECASE)


def split_instructors(instructor: str | None) -> list[str]:
    """Split a co-taught instructor string into individual names.

    Splits on '&', ';', '/', '+', and the standalone word 'and' (never on comma,
    which is used for 'Last, First'). Order preserved, duplicates dropped.
    """
    if not instructor:
        return []
    out: list[str] = []
    for part in _INSTR_SPLIT_RE.split(instructor):
        name = part.strip().strip(",")
        if name and name not in out:
            out.append(name)
    return out
