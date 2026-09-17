"""RateMyProfessors GraphQL client (school search, teacher search, ratings).

Uses the public site token (Basic test:test). School lookups resolve a college
name to an RMP schoolID; teacher searches are scoped to that school so a surname
is usually unique. Ratings pagination is used by Stage 4.
"""

from __future__ import annotations

import base64
import re
import time
import unicodedata
from typing import Any

import requests

GRAPHQL_URL = "https://www.ratemyprofessors.com/graphql"
HEADERS = {
    "Authorization": "Basic dGVzdDp0ZXN0",  # test:test — the token the site ships
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}
PROFILE_URL = "https://www.ratemyprofessors.com/professor/{}"

SCHOOL_QUERY = """
query SchoolSearch($q: SchoolSearchQuery!) {
  newSearch {
    schools(query: $q) {
      edges { node { id legacyId name city state } }
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
          id legacyId firstName lastName
          avgRating avgDifficulty numRatings wouldTakeAgainPercent
          department school { name city state }
        }
      }
    }
  }
}
"""

RATINGS_QUERY = """
query Ratings($id: ID!, $count: Int!, $cursor: String) {
  node(id: $id) {
    ... on Teacher {
      numRatings
      ratings(first: $count, after: $cursor) {
        edges {
          node {
            id class date grade
            qualityRating clarityRating helpfulRating difficultyRating
            wouldTakeAgain attendanceMandatory isForCredit isForOnlineClass
            textbookUse ratingTags thumbsUpTotal thumbsDownTotal comment
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def gql(query: str, variables: dict[str, Any], retries: int = 3) -> dict:
    last_err = "unknown error"
    for attempt in range(retries):
        try:
            r = requests.post(
                GRAPHQL_URL, headers=HEADERS,
                json={"query": query, "variables": variables}, timeout=25,
            )
            if r.status_code in (429, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - network/JSON errors are retried
            last_err = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_err)


def _fold(s: str | None) -> str:
    """Lowercase and strip diacritics (Müller -> muller)."""
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _fold(s)).strip()


def _tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[.\-']", " ", _fold(name)).split() if t]


# --------------------------------------------------------------------------- #
# School search
# --------------------------------------------------------------------------- #
def search_schools(text: str) -> list[dict]:
    data = gql(SCHOOL_QUERY, {"q": {"text": text}})
    schools = (((data.get("data") or {}).get("newSearch") or {}).get("schools")) or {}
    return [e["node"] for e in schools.get("edges", [])]


def pick_school(college_name: str, nodes: list[dict]) -> dict | None:
    """Choose the RMP school that best matches a college name."""
    if not nodes:
        return None
    want = _norm(college_name)
    wt = set(want.split())
    for n in nodes:                                   # exact name
        if _norm(n.get("name")) == want:
            return n
    for n in nodes:                                   # college tokens ⊆ RMP name
        if wt and wt.issubset(set(_norm(n.get("name")).split())):
            return n
    for n in nodes:                                   # RMP name ⊆ college (campus/abbrev)
        nt = set(_norm(n.get("name")).split())
        if nt and nt.issubset(wt):
            return n
    return nodes[0]                                   # RMP's own best guess


def find_school(college_name: str) -> dict | None:
    return pick_school(college_name, search_schools(college_name))


# --------------------------------------------------------------------------- #
# Teacher search + match
# --------------------------------------------------------------------------- #
def search_teachers(text: str, school_id: str, first: int = 20) -> list[dict]:
    data = gql(TEACHER_QUERY, {"q": {"text": text, "schoolID": school_id}, "first": first})
    teachers = (((data.get("data") or {}).get("newSearch") or {}).get("teachers")) or {}
    return [e["node"] for e in teachers.get("edges", [])]


def pick_match(query: str, nodes: list[dict]) -> tuple[dict | None, str]:
    """Verify RMP's fuzzy hits by surname; return (node|None, match_type)."""
    toks = _tokens(query)
    if not toks:
        return None, "not_found"
    surname = toks[-1]
    given = toks[0] if len(toks) > 1 else None

    exact = [n for n in nodes if _fold(n.get("lastName")) == surname]
    if not exact:
        return None, "not_found"
    if len(exact) == 1:
        return exact[0], "unique"

    pool = exact
    if given:
        if len(given) == 1:
            narrowed = [n for n in exact if _fold(n.get("firstName")).startswith(given)]
        else:
            narrowed = [n for n in exact if _fold(n.get("firstName")) == given]
        if len(narrowed) == 1:
            return narrowed[0], "given"
        if narrowed:
            pool = narrowed
    best = max(pool, key=lambda n: n.get("numRatings") or 0)   # most-rated namesake
    return best, "ambiguous"


def teacher_result(node: dict, match_type: str) -> dict:
    legacy = node.get("legacyId")
    return {
        "rmp_status": "found",
        "match_type": match_type,
        "legacy_id": str(legacy) if legacy is not None else None,
        "matched_name": f"{node.get('firstName', '')} {node.get('lastName', '')}".strip(),
        "department": node.get("department"),
        "school_name": (node.get("school") or {}).get("name"),
        "avg_rating_overall": node.get("avgRating"),
        "avg_difficulty_overall": node.get("avgDifficulty"),
        "num_ratings_overall": node.get("numRatings"),
        "would_take_again_overall": node.get("wouldTakeAgainPercent"),
        "profile_url": PROFILE_URL.format(legacy) if legacy is not None else None,
    }


# --------------------------------------------------------------------------- #
# Ratings (Stage 4)
# --------------------------------------------------------------------------- #
def node_id(legacy_id: str) -> str:
    return base64.b64encode(f"Teacher-{legacy_id}".encode()).decode()


def _wta(value) -> int:
    if value == 1:
        return 1
    if value == 0:
        return 0
    return -1


def _tri_bool(value) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def _attendance(value) -> str | None:
    v = (value or "").strip().lower()
    if v in ("mandatory", "non mandatory"):
        return v
    return None


def parse_rating(node: dict) -> dict:
    tags = node.get("ratingTags") or ""
    if isinstance(tags, list):
        tags = ", ".join(tags)
    else:
        tags = ", ".join(t.strip() for t in tags.split("--") if t.strip())
    return {
        "rating_id": node.get("id"),
        "class": (node.get("class") or "").strip() or None,
        "quality": node.get("qualityRating"),
        "difficulty": node.get("difficultyRating"),
        "clarity": node.get("clarityRating"),
        "helpful": node.get("helpfulRating"),
        "would_take_again": _wta(node.get("wouldTakeAgain")),
        "grade": (node.get("grade") or "").strip() or None,
        "attendance": _attendance(node.get("attendanceMandatory")),
        "for_credit": _tri_bool(node.get("isForCredit")),
        "online_class": _tri_bool(node.get("isForOnlineClass")),
        "textbook_use": node.get("textbookUse"),
        "thumbs_up": node.get("thumbsUpTotal"),
        "thumbs_down": node.get("thumbsDownTotal"),
        "date": (node.get("date") or "")[:10] or None,
        "comment": (node.get("comment") or "").strip() or None,
        "tags": tags or None,
    }


def fetch_all_ratings(legacy_id: str, delay: float = 0.0, page_size: int = 20) -> list[dict]:
    """Paginate through every rating for one professor."""
    nid = node_id(legacy_id)
    out: list[dict] = []
    cursor = None
    while True:
        data = gql(RATINGS_QUERY, {"id": nid, "count": page_size, "cursor": cursor})
        node = (data.get("data") or {}).get("node") or {}
        ratings = node.get("ratings")
        if not ratings:
            break
        for edge in ratings.get("edges", []):
            out.append(parse_rating(edge["node"]))
        page = ratings.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if delay:
            time.sleep(delay)
    return out
