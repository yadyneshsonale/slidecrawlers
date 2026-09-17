"""Central configuration for the slidegrab keyword-search downloader.

Loads `universities.yaml` and `topics.yaml`, exposes runtime tunables, and
reads the Brave Search API key from the `BRAVE_API_KEY` environment variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass(frozen=True)
class University:
    name: str
    slug: str
    region: str
    priority: int
    aliases: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass
class SearchConfig:
    """Provider-agnostic search config.

    The active provider is auto-selected from whichever API key env var is set,
    in this order: Tavily, Serper, Brave. Override with SEARCH_PROVIDER=<name>.
    """
    results_per_query: int = 10
    min_seconds_between_calls: float = 1.1  # respect free-tier rate limits
    max_retries: int = 3
    timeout: int = 20

    # Per-provider keys (set the one for the service you signed up with).
    tavily_key: str = field(default_factory=lambda: os.environ.get("TAVILY_API_KEY", ""))
    serper_key: str = field(default_factory=lambda: os.environ.get("SERPER_API_KEY", ""))
    brave_key: str = field(default_factory=lambda: os.environ.get("BRAVE_API_KEY", ""))
    provider_override: str = field(
        default_factory=lambda: os.environ.get("SEARCH_PROVIDER", "").lower()
    )

    def active_provider(self) -> str | None:
        """Return the provider name to use, or None if no key is configured."""
        keys = {"tavily": self.tavily_key, "serper": self.serper_key, "brave": self.brave_key}
        if self.provider_override:
            return self.provider_override if keys.get(self.provider_override) else None
        for name in ("tavily", "serper", "brave"):  # preference order
            if keys[name]:
                return name
        return None

    def key_for(self, provider: str) -> str:
        return {"tavily": self.tavily_key, "serper": self.serper_key,
                "brave": self.brave_key}.get(provider, "")

    @property
    def has_key(self) -> bool:
        return self.active_provider() is not None


@dataclass
class FetchConfig:
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 slidegrab/1.0"
    )
    nav_timeout_ms: int = 15000          # page navigation (dead pages fail fast)
    download_timeout_ms: int = 60000     # binary download (keep large PDFs intact)
    per_domain_delay: float = 1.5
    max_concurrency: int = 8
    max_retries: int = 1
    use_cache: bool = True
    # Hosts that ship a JS shell and must be re-rendered (networkidle).
    render_hosts: tuple[str, ...] = ("ocw.mit.edu", "introtodeeplearning.com")


@dataclass
class SlidesConfig:
    include_tokens: tuple[str, ...] = (
        "slide", "slides", "ppt", "pptx", "presentation", "deck",
        "lecture-slides", "lec-slides", "lecture", "lec", "week",
        "topic", "chapter", "module",
    )
    exclude_tokens: tuple[str, ...] = (
        "note", "notes", "reading", "readings", "handout", "scribe",
        "transcript", "homework", "hw", "pset", "problem-set", "problemset",
        "assignment", "solution", "solutions", "exam", "quiz", "syllabus",
        "textbook",
    )
    # Tokens that mark a link as "follow one hop to find slides".
    index_tokens: tuple[str, ...] = (
        "lecture", "lectures", "slides", "schedule", "syllabus", "calendar",
        "course", "teaching", "materials",
    )


@dataclass
class VerifyConfig:
    min_pages: int = 1
    slide_min_aspect_ratio: float = 1.1
    notes_max_chars_per_page: int = 1800
    enforce_slide_likeness: bool = True


@dataclass
class CourseFilterConfig:
    # A result URL scores as a course page when it contains these path hints.
    course_hints: tuple[str, ...] = (
        "course", "courses", "teaching", "class", "lecture", "lectures",
        "~", "cs", "ee", "col", "ell", "fall", "spring", "autumn", "winter",
    )


@dataclass
class Settings:
    root: Path = ROOT
    data_dir: Path = ROOT / "dataset"
    cache_dir: Path = ROOT / "work" / "cache"
    search_cache_dir: Path = ROOT / "work" / "search_cache"
    index_db: Path = ROOT / "index.sqlite"
    search: SearchConfig = field(default_factory=SearchConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    slides: SlidesConfig = field(default_factory=SlidesConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    course_filter: CourseFilterConfig = field(default_factory=CourseFilterConfig)


settings = Settings()


def load_universities() -> list[University]:
    data = yaml.safe_load((CONFIG_DIR / "universities.yaml").read_text())
    unis = [
        University(
            name=u["name"],
            slug=u["slug"],
            region=u.get("region", "global"),
            priority=int(u.get("priority", 999)),
            aliases=tuple(u.get("aliases", [u["name"]])),
            domains=tuple(u.get("domains", [])),
        )
        for u in data["universities"]
    ]
    unis.sort(key=lambda u: u.priority)
    return unis


def load_topics() -> dict:
    return yaml.safe_load((CONFIG_DIR / "topics.yaml").read_text())
