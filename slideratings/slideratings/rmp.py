"""Rate My Professors lookup.

Discovers a university's RMP school id by name, then searches that school for an
instructor and verifies the match by surname (RMP fuzz-matches first names). The
matching/disambiguation mirrors slidefetch's ``rmp_crawler`` and
``uw_rmp_lookup`` logic. Standard library only.
"""
from __future__ import annotations

import base64
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",
    "Content-Type": "application/json",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}
PROFILE_URL = "https://www.ratemyprofessors.com/professor/{}"

SCHOOL_QUERY = """
query SchoolSearch($q: SchoolSearchQuery!, $first: Int!) {
  newSearch {
    schools(query: $q, first: $first) {
      edges { node { id name city state numRatings } }
    }
  }
}
"""
TEACHER_QUERY = """
query TeacherSearch($q: TeacherSearchQuery!, $first: Int!) {
  newSearch {
    teachers(query: $q, first: $first) {
      edges {
        node {
          legacyId firstName lastName avgRating avgDifficulty numRatings
          wouldTakeAgainPercent department school { name city state }
        }
      }
    }
  }
}
"""
COURSES_QUERY = """
query TeacherCourses($id: ID!) {
  node(id: $id) {
    ... on Teacher { firstName lastName courseCodes { courseName courseCount } }
  }
}
"""

TITLE_RE = re.compile(r"^\s*(prof|professor|dr|mr|mrs|ms|miss|sir|mx|rev)\.?\s+", re.I)
PAREN_RE = re.compile(r"\([^)]*\)")
SUFFIX_TOKENS = {"do", "ii", "iv", "jr", "md", "sr", "esq", "iii", "msc",
                 "phd", "dphil", "emerita", "emeritus"}
STOPWORDS = {"al", "et", "no", "and", "tba", "tbd", "team", "staff", "others",
             "faculty", "various", "department", "instructor", "instructors"}
_TOKEN_RE = re.compile(r"[A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F.'\-]*")


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def clean_person(piece: str) -> str | None:
    """Clean a single name fragment into 'First [Middle] Last' or return None."""
    if not piece:
        return None
    s = PAREN_RE.sub(" ", piece)
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip(" ,.-\t")
    while True:
        new = TITLE_RE.sub("", s)
        if new == s:
            break
        s = new
    s = s.strip(" ,.-")
    if not s:
        return None
    tokens = s.split()
    while tokens and tokens[-1].strip(".,").lower() in SUFFIX_TOKENS:
        tokens.pop()
    if not (2 <= len(tokens) <= 5):
        return None
    if any(t.strip(".,").lower() in STOPWORDS for t in tokens):
        return None
    if any(ch.isdigit() for ch in s):
        return None
    if not all(_TOKEN_RE.fullmatch(t) for t in tokens):
        return None
    return " ".join(tokens)


def normalize_name(raw: str) -> str | None:
    """Lighter cleaner for a single instructor cell (allows a lone surname).

    CCR reviews often expose only a surname ("Prof. White"); RMP's per-school
    search resolves a surname fine, so unlike ``clean_person`` this keeps
    one-token names. Returns ``None`` for non-names.
    """
    s = PAREN_RE.sub(" ", raw or "")
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip(" ,.-\t")
    while True:
        new = TITLE_RE.sub("", s)
        if new == s:
            break
        s = new
    s = s.strip(" ,.-")
    if not s:
        return None
    tokens = s.split()
    while tokens and tokens[-1].strip(".,").lower() in SUFFIX_TOKENS:
        tokens.pop()
    if not tokens or len(tokens) > 4:
        return None
    if any(t.strip(".,").lower() in STOPWORDS for t in tokens):
        return None
    if any(ch.isdigit() for ch in s):
        return None
    if not all(_TOKEN_RE.fullmatch(t) for t in tokens):
        return None
    return " ".join(tokens)


def _post(query: str, variables: dict, retries: int = 3) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    last_err = "unknown error"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(GRAPHQL_URL, data=payload,
                                         headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(last_err)


def find_school(name: str) -> dict | None:
    """Return the best-matching RMP school node ``{id,name,...}`` for *name*."""
    if not name:
        return None
    data = _post(SCHOOL_QUERY, {"q": {"text": name}, "first": 10})
    schools = (((data.get("data") or {}).get("newSearch") or {})
               .get("schools") or {}).get("edges") or []
    nodes = [e["node"] for e in schools]
    if not nodes:
        return None
    want = set(_tokens(name))

    def score(node):
        cand = set(_tokens(node.get("name") or ""))
        overlap = len(want & cand)
        return (overlap, node.get("numRatings") or 0)

    return max(nodes, key=score)


def _tokens(name: str) -> list[str]:
    name = strip_accents(name or "").lower()
    return [t for t in re.sub(r"[.\-']", " ", name).split() if t]


def teacher_search(text: str, school_id: str, first: int = 20) -> list[dict]:
    data = _post(TEACHER_QUERY,
                 {"q": {"text": text, "schoolID": school_id}, "first": first})
    teachers = ((data.get("data") or {}).get("newSearch") or {}).get("teachers")
    if not teachers:
        return []
    return [edge["node"] for edge in (teachers.get("edges") or [])]


def pick_match(query: str, nodes: list[dict]) -> tuple[dict | None, int, str]:
    """Choose the best professor for *query* within one school.

    Returns ``(node | None, num_surname_candidates, match_type)`` with
    ``match_type`` in unique/given/dept/ambiguous/no_match/not_found.
    """
    toks = _tokens(query)
    if not toks:
        return (None, 0, "not_found")
    surname = toks[-1]
    given = toks[0] if len(toks) > 1 else None
    exact = [n for n in nodes if (n.get("lastName") or "").lower() == surname]
    if not exact:
        return (None, 0, "not_found")
    if len(exact) == 1:
        return (exact[0], 1, "unique")
    pool = exact
    if given:
        if len(given) == 1:
            narrowed = [n for n in exact
                        if (n.get("firstName") or "").lower().startswith(given)]
        else:
            narrowed = [n for n in exact
                        if (n.get("firstName") or "").lower() == given]
        if narrowed:
            pool = narrowed
            if len(pool) == 1:
                return (pool[0], len(exact), "given")
    best = max(pool, key=lambda n: n.get("numRatings") or 0)
    return (best, len(exact), "ambiguous")


def lookup(name: str, school_id: str, delay: float = 0) -> dict:
    """Search + disambiguate one instructor at *school_id*; return a row dict."""
    if delay:
        time.sleep(delay)
    row = {"name": name, "match_type": "not_found"}
    try:
        nodes = teacher_search(name, school_id)
        node, n_cand, match_type = pick_match(name, nodes)
        row["match_type"] = match_type
        row["num_candidates"] = n_cand
        if node is None:
            return row
        legacy = node.get("legacyId")
        row.update(
            rmp_legacy_id=legacy,
            matched_name=f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
            department=node.get("department"),
            school=(node.get("school") or {}).get("name"),
            avg_rating=node.get("avgRating"),
            avg_difficulty=node.get("avgDifficulty"),
            num_ratings=node.get("numRatings"),
            would_take_again=node.get("wouldTakeAgainPercent"),
            rmp_url=PROFILE_URL.format(legacy) if legacy else None,
        )
    except Exception as err:  # noqa: BLE001
        row["match_type"] = "error"
        row["error"] = str(err)
    return row


def teacher_courses(legacy_id: int) -> list[dict]:
    """Return the course codes a professor has been rated for on RMP.

    Each item is ``{"course": <code>, "count": <num_ratings_for_course>}``,
    sorted by descending rating count (most-taught course first).
    """
    if not legacy_id:
        return []
    try:
        node_id = base64.b64encode(f"Teacher-{legacy_id}".encode()).decode()
        data = _post(COURSES_QUERY, {"id": node_id})
        node = (data.get("data") or {}).get("node") or {}
        codes = node.get("courseCodes") or []
        out = [
            {"course": (c.get("courseName") or "").strip(),
             "count": int(c.get("courseCount") or 0)}
            for c in codes
            if (c.get("courseName") or "").strip()
        ]
        out.sort(key=lambda c: c["count"], reverse=True)
        return out
    except Exception:  # noqa: BLE001
        return []
