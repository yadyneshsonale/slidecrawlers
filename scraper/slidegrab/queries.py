"""Build keyword search queries per university.

No URLs are hardcoded: queries combine a university's name/aliases with CS
topics, known course codes, and slide-oriented phrases. The pipeline feeds
these to the Brave client to discover course pages.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import University, load_topics


@dataclass(frozen=True)
class Query:
    text: str
    university_slug: str
    kind: str  # "code" | "topic"
    label: str  # the course code or topic that produced it


def build_queries(uni: University, topics_cfg: dict | None = None,
                  codes: list[str] | None = None,
                  max_per_uni: int | None = None,
                  max_code_queries: int | None = None) -> list[Query]:
    """Build search queries for ``uni``.

    ``codes`` overrides the seed course codes from ``topics.yaml`` (e.g. the
    list discovered by ``coursecodes.discover_course_codes``). When omitted, the
    seed codes are used so existing behaviour is preserved.
    """
    topics_cfg = topics_cfg or load_topics()
    primary = uni.aliases[0] if uni.aliases else uni.name
    slide_phrases: list[str] = topics_cfg.get("slide_phrases", ["lecture slides"])
    main_phrase = slide_phrases[0]

    queries: list[Query] = []
    seen: set[str] = set()

    def add(text: str, kind: str, label: str) -> None:
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(Query(text=text, university_slug=uni.slug, kind=kind, label=label))

    # 1) High-precision: university + course code + slide phrase.
    code_list = codes if codes is not None else \
        topics_cfg.get("course_codes", {}).get(uni.slug, [])
    if max_code_queries is not None:
        code_list = list(code_list)[:max_code_queries]
    for code in code_list:
        add(f"{primary} {code} {main_phrase}", "code", str(code))

    # 2) Topic-based: university + topic + slide phrase (finds unknown courses).
    for topic in topics_cfg.get("topics", []):
        add(f"{primary} {topic} {main_phrase}", "topic", topic)

    if max_per_uni is not None:
        queries = queries[:max_per_uni]
    return queries
