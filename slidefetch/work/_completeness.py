#!/usr/bin/env python3
"""Decide whether a set of gathered slide decks forms a complete 1..N sequence.

A course qualifies for download only when its decks cover a gap-free run
``1, 2, ..., N`` under some consistent lecture label -- ``lec1..lecN``,
``slide1..slideN``, ``week1..weekN``, ``chapter1..chapterN`` and many spelling
variants. The code pass (:func:`assess`) handles the clean cases; genuinely
ambiguous ones (mixed labels, mostly-unnumbered files, competing kinds) are
routed to a subagent via :func:`emit_batch` / :func:`ingest`, mirroring the
established ``work/_subagent_batch.py`` emit->judge->ingest loop. An optional
LLM judge (:func:`llm_judge`) auto-resolves the ambiguous cases inline when an
OpenAI-compatible endpoint is configured.

Assessment always runs on the *original* deck names/links (never the saved
``<seq>_name.pdf`` files, whose numeric prefix is just download order and would
mask the real sequence).
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

# --------------------------------------------------------------------------- #
# Label vocabulary. Each surface spelling maps to a canonical "kind" so that
# ``Lecture07`` / ``lec7`` / ``L07`` all count as the same series.
# --------------------------------------------------------------------------- #
_LABEL_MAP = {
    "lecture": "lec", "lectures": "lec", "lect": "lec", "lec": "lec",
    "slides": "slide", "slide": "slide", "sld": "slide",
    "weeks": "week", "week": "week", "wk": "week",
    "chapters": "chapter", "chapter": "chapter", "chap": "chapter", "ch": "chapter",
    "topics": "topic", "topic": "topic",
    "modules": "module", "module": "module", "mod": "module",
    "units": "unit", "unit": "unit",
    "sessions": "session", "session": "session", "sess": "session",
    "classes": "class", "class": "class",
    "lessons": "lesson", "lesson": "lesson",
    "parts": "part", "part": "part",
    "days": "day", "day": "day",
}
# Preference order when several kinds tie for "most numbered decks".
_KIND_PRIORITY = [
    "lec", "slide", "week", "chapter", "topic", "module", "unit", "lesson",
    "session", "class", "day", "part", "num",
]

# Full-word label immediately followed by a number, e.g. ``lecture-03``,
# ``slides_7``, ``week2``, ``chapter 5``. Optional leading zeros are dropped.
_SEQ_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(sorted(_LABEL_MAP, key=len, reverse=True)) +
    r")[\s._#:\-]*0*(\d{1,3})(?![0-9])",
    re.IGNORECASE,
)
# Single-letter label ``l05`` (lecture) / ``w3`` (week). Narrower to limit noise.
_SINGLE_RE = re.compile(r"(?<![a-z0-9])([lw])[\s._#\-]*0*(\d{1,2})(?![0-9])", re.IGNORECASE)
_SINGLE_MAP = {"l": "lec", "w": "week"}
# A bare leading number, e.g. ``01_intro.pdf`` / ``3-hashing.pdf`` -> kind "num".
_LEAD_RE = re.compile(r"^\s*0*(\d{1,3})(?![0-9])")
# Strip the downloader's ``<seq>_`` order prefix if one slipped in.
_ORDER_PREFIX_RE = re.compile(r"^\d{1,3}[_-](?=[A-Za-z])")
_DOC_EXT_RE = re.compile(r"\.(?:pdf|pptx?|ppt|key|odp|html?)$", re.IGNORECASE)


_MAX_BARE = 199  # cap a bare number so year dirs (2016, 2023) aren't read as slots


def _basename(url_or_name: str) -> str:
    """Percent-decoded final path segment, without a document extension."""
    s = unquote(url_or_name or "").strip()
    if "://" in s or "/" in s:
        s = urlparse(s).path if "://" in s else s
        s = s.rstrip("/").rsplit("/", 1)[-1]
    s = _DOC_EXT_RE.sub("", s)
    return s


def _parent_dir(url_or_name: str) -> str:
    """Immediate parent directory segment of a path/URL (``.../010/x.pdf`` -> ``010``)."""
    s = unquote(url_or_name or "")
    path = urlparse(s).path if "://" in s else s
    segs = [x for x in path.split("/") if x]
    return segs[-2] if len(segs) >= 2 else ""


def sequence_of(name: str = "", text: str = "", url: str = "") -> tuple[str, int] | None:
    """Return ``(kind, number)`` naming a lecture slot for a deck.

    Looks at (in order) the filename, link text, and the immediate parent
    directory -- so ``Lecture07.pdf``, ``"Week 3 slides"`` and
    ``.../lectures/010/intro.pptx`` all resolve. A full-word label wins over a
    single-letter one, which wins over a bare number.
    """
    fname = _basename(name or url)
    ftext = _basename(text)
    parent = _parent_dir(url or name)
    for raw in (fname, ftext, parent):
        if not raw:
            continue
        s = _ORDER_PREFIX_RE.sub("", raw)
        m = _SEQ_RE.search(s)
        if m:
            return _LABEL_MAP[m.group(1).lower()], int(m.group(2))
        m = _SINGLE_RE.search(s)
        if m:
            return _SINGLE_MAP[m.group(1).lower()], int(m.group(2))
    # Bare leading number from the filename, else the parent directory.
    for raw in (fname, parent):
        m = _LEAD_RE.match(raw)
        if m:
            n = int(m.group(1))
            if 1 <= n <= _MAX_BARE:
                return "num", n
    return None


@dataclass
class Deck:
    """A candidate deck to be assessed (built from a gathered slide link)."""
    name: str            # original filename (may be empty)
    text: str = ""       # anchor / link text
    url: str = ""

    def slot(self) -> tuple[str, int] | None:
        return sequence_of(self.name, self.text, self.url)


@dataclass
class CompletenessResult:
    complete: bool | None          # True / False / None (ambiguous -> subagent)
    kind: str | None
    numbers: list[int]
    expected_n: int | None
    method: str                    # "code" | "subagent" | "llm"
    reason: str
    per_deck: list[dict] = field(default_factory=list)


def _slots(decks: list[Deck]) -> tuple[dict[str, dict[int, int]], int, list[dict]]:
    """Map kind -> {number: count}, count of decks that carry a slot, and records."""
    by_kind: dict[str, dict[int, int]] = {}
    numbered = 0
    per_deck: list[dict] = []
    for d in decks:
        slot = d.slot()
        rec = {"name": d.name or _basename(d.url), "url": d.url}
        if slot:
            kind, num = slot
            rec["kind"], rec["number"] = kind, num
            by_kind.setdefault(kind, {})[num] = by_kind.get(kind, {}).get(num, 0) + 1
            numbered += 1
        else:
            rec["kind"], rec["number"] = None, None
        per_deck.append(rec)
    return by_kind, numbered, per_deck


def _sequence_verdict(positives: list[int], min_decks: int) -> tuple[bool, int, str]:
    """Judge a set of positive lecture numbers as a gap-free sequence.

    Accepts a plain ``1..N`` run and step schemes (``10,20,30`` or ``5,10,15``),
    which are normalised by the greatest common divisor of the numbers before the
    gap check -- so directory numbering like ``/lectures/010,020,030/`` reads as
    ``1,2,3``. Returns ``(complete, N, reason)``.
    """
    P = sorted(set(n for n in positives if n >= 1))
    if not P:
        return False, 0, "no positive lecture numbers"
    g = 0
    for p in P:
        g = math.gcd(g, p)
    if g > 1:
        P = [p // g for p in P]
    n = P[-1]
    missing = sorted(set(range(1, n + 1)) - set(P))
    if missing:
        shown = ", ".join(map(str, missing[:8])) + ("..." if len(missing) > 8 else "")
        return False, n, f"gap: missing {shown}"
    if n < min_decks:
        return False, n, f"sequence too short: N={n} < min {min_decks}"
    return True, n, f"contiguous 1..{n}"


def assess(decks: list[Deck], *, min_decks: int = 3,
           dominant_frac: float = 0.6) -> CompletenessResult:
    """Judge whether *decks* form a gap-free 1..N sequence.

    Returns ``complete=True`` for a clean contiguous run, ``False`` for a clear
    gap, and ``None`` (ambiguous) when the numbering is too mixed/sparse to
    decide by rule -- those go to the subagent.
    """
    total = len(decks)
    by_kind, numbered, per_deck = _slots(decks)
    if total == 0:
        return CompletenessResult(False, None, [], None, "code",
                                  "no decks gathered", per_deck)
    if not by_kind:
        return CompletenessResult(None, None, [], None, "code",
                                  "no numbered decks; needs subagent", per_deck)

    # Dominant kind = most distinct numbered decks (ties broken by priority).
    def _rank(k: str) -> tuple[int, int]:
        pr = _KIND_PRIORITY.index(k) if k in _KIND_PRIORITY else len(_KIND_PRIORITY)
        return (len(by_kind[k]), -pr)

    kind = max(by_kind, key=_rank)
    nums = sorted(by_kind[kind])
    covered = len(nums)                       # distinct numbers in the series
    dominant_decks = sum(by_kind[kind].values())  # decks carrying that series
    positives = [n for n in nums if n >= 1]

    # A second series with comparable coverage -> ambiguous (which one is it?).
    contenders = [k for k in by_kind if len(by_kind[k]) >= 2 and k != kind]
    if any(len(by_kind[k]) >= covered for k in contenders):
        return CompletenessResult(None, kind, positives, None, "code",
                                  "competing label series; needs subagent", per_deck)

    # Most decks should belong to the dominant series (pdf+pptx pairs share a
    # number, so gate on deck count, not distinct numbers).
    if dominant_decks < max(2, int(dominant_frac * total)):
        return CompletenessResult(None, kind, positives, None, "code",
                                  f"only {dominant_decks}/{total} decks numbered "
                                  f"({kind}); needs subagent", per_deck)

    if not positives:
        return CompletenessResult(None, kind, [], None, "code",
                                  "only a zero/intro deck numbered; needs subagent",
                                  per_deck)

    complete, n, reason = _sequence_verdict(positives, min_decks)
    return CompletenessResult(complete, kind, positives, n, "code",
                              f"{kind}: {reason}", per_deck)


# --------------------------------------------------------------------------- #
# Optional LLM judge for the ambiguous middle (auto-resolves emit/ingest inline)
# --------------------------------------------------------------------------- #
_LLM_SYS = (
    "You judge whether a set of university lecture-slide files is a COMPLETE course "
    "or only a partial fragment. Decks may be NUMBERED (lec/lecture/slide/week/chapter/"
    "topic/module 1..N) or TOPIC-NAMED with no numbers (e.g. Intro, Variables, Recursion, "
    "Final-Review). Rules: (a) if numbered, complete means the numbers run 1..N with NO gap "
    "(an extra intro/lecture-0 is fine); (b) if topic-named with no numbers, complete means "
    "the files look like a whole course from beginning (an intro/overview) through to an end "
    "(final/review/last topic) with no obviously missing middle -- do NOT invent lecture "
    "numbers or require them. When unsure, answer false. Reply ONLY with compact JSON: "
    '{"complete": true|false, "n": <deck count as int>, "kind": "<numbered|topic>", '
    '"reason": "<short>"}.'
)


def llm_judge(course: str, per_deck: list[dict], *,
              base_url: str | None = None, api_key: str | None = None,
              model: str | None = None, timeout: int = 60) -> CompletenessResult | None:
    """Ask an OpenAI-compatible endpoint to judge an ambiguous deck set.

    Returns ``None`` (caller keeps it for manual review) when no endpoint is
    configured or the call/parse fails. Never raises.
    """
    base_url = base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("SLIDEFETCH_LLM_BASE")
    if not base_url:
        return None
    api_key = api_key or os.environ.get("OPENAI_API_KEY") or "not-needed"
    model = model or os.environ.get("SLIDEFETCH_LLM_MODEL")
    try:
        import requests
    except Exception:  # noqa: BLE001
        return None
    if not model:  # auto-detect the served model (e.g. a local vLLM)
        try:
            mr = requests.get(base_url.rstrip("/") + "/models",
                              headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            model = mr.json()["data"][0]["id"]
        except Exception:  # noqa: BLE001
            model = "local-model"
    files = [f"{r.get('name') or r.get('url')}" for r in per_deck]
    user = (f"Course: {course}\nFiles ({len(files)}):\n" +
            "\n".join(f"- {f}" for f in files))
    try:
        resp = requests.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "temperature": 0,
                  "messages": [{"role": "system", "content": _LLM_SYS},
                               {"role": "user", "content": user}]},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    complete = bool(data.get("complete"))
    n = data.get("n")
    return CompletenessResult(
        complete, data.get("kind"),
        list(range(1, int(n) + 1)) if isinstance(n, int) else [],
        int(n) if isinstance(n, int) else None,
        "llm", str(data.get("reason", ""))[:200], per_deck,
    )


# --------------------------------------------------------------------------- #
# Manual subagent loop: emit a JSON batch, ingest a TSV of verdicts.
# --------------------------------------------------------------------------- #
def emit_batch(rows: list[dict]) -> str:
    """Serialize ambiguous courses (course_college, code, files) for a subagent.

    Each row: ``{"course_college","course_code","resolved_link","decks":[...]}``
    where decks are the parsed per-deck records. The subagent replies with a TSV
    ``course_college<TAB>complete(yes/no)<TAB>N<TAB>reason`` fed back to
    :func:`ingest`.
    """
    return json.dumps(rows, indent=1, ensure_ascii=False)


def parse_ingest_tsv(text: str) -> dict[str, dict]:
    """Parse a subagent verdict TSV into ``{course_college: {complete,n,reason}}``."""
    out: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.lower().startswith("course_college\t"):
            continue
        parts = line.split("\t")
        cc = parts[0].strip()
        if not cc:
            continue
        verdict = (parts[1].strip().lower() if len(parts) > 1 else "")
        complete = verdict in ("yes", "y", "true", "complete", "1")
        n = None
        if len(parts) > 2 and parts[2].strip().isdigit():
            n = int(parts[2].strip())
        reason = parts[3].strip() if len(parts) > 3 else ""
        out[cc] = {"complete": complete, "n": n, "reason": reason}
    return out
