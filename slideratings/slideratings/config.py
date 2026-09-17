"""Typed configuration loaded from ``config.yaml``."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PKG_ROOT.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


@dataclass
class HttpConfig:
    user_agent: str
    timeout_s: int
    max_retries: int
    min_interval_s: float
    cache_max_age_s: int
    render_fallback: bool


@dataclass
class CcrConfig:
    base: str
    max_course_pages: int


@dataclass
class RmpConfig:
    max_workers: int
    request_delay_s: float


@dataclass
class SlidesConfig:
    max_search_results: int
    max_decks: int
    max_index_follow: int
    use_browser_search: bool
    max_prof_courses: int


@dataclass
class VerifyConfig:
    min_pages: int
    slide_min_aspect_ratio: float
    notes_max_chars_per_page: float
    enforce_slide_likeness: bool


@dataclass
class Settings:
    data_dir: Path
    work_dir: Path
    db_path: Path
    http: HttpConfig
    ccr: CcrConfig
    rmp: RmpConfig
    slides: SlidesConfig
    verify: VerifyConfig

    @property
    def cache_dir(self) -> Path:
        return self.work_dir / "http_cache"

    @property
    def search_cache_dir(self) -> Path:
        return self.work_dir / "search_cache"

    @property
    def log_dir(self) -> Path:
        return self.work_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.work_dir, self.cache_dir,
                  self.search_cache_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    root = cfg_path.resolve().parent
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    paths = raw.get("paths", {})
    http = raw.get("http", {})
    ccr = raw.get("ccr", {})
    rmp = raw.get("rmp", {})
    slides = raw.get("slides", {})
    verify = raw.get("verify", {})

    return Settings(
        data_dir=_resolve(root, paths.get("data_dir", "data")),
        work_dir=_resolve(root, paths.get("work_dir", "work")),
        db_path=_resolve(root, paths.get("db_path", "slideratings.db")),
        http=HttpConfig(
            user_agent=str(http.get("user_agent", "Mozilla/5.0")),
            timeout_s=int(http.get("timeout_s", 30)),
            max_retries=int(http.get("max_retries", 3)),
            min_interval_s=float(http.get("min_interval_s", 1.2)),
            cache_max_age_s=int(http.get("cache_max_age_s", 604800)),
            render_fallback=bool(http.get("render_fallback", True)),
        ),
        ccr=CcrConfig(
            base=str(ccr.get("base", "https://collegeclassreviews.com")).rstrip("/"),
            max_course_pages=int(ccr.get("max_course_pages", 200)),
        ),
        rmp=RmpConfig(
            max_workers=int(rmp.get("max_workers", 4)),
            request_delay_s=float(rmp.get("request_delay_s", 0.4)),
        ),
        slides=SlidesConfig(
            max_search_results=int(slides.get("max_search_results", 8)),
            max_decks=int(slides.get("max_decks", 60)),
            max_index_follow=int(slides.get("max_index_follow", 3)),
            use_browser_search=bool(slides.get("use_browser_search", True)),
            max_prof_courses=int(slides.get("max_prof_courses", 6)),
        ),
        verify=VerifyConfig(
            min_pages=int(verify.get("min_pages", 3)),
            slide_min_aspect_ratio=float(verify.get("slide_min_aspect_ratio", 1.4)),
            notes_max_chars_per_page=float(verify.get("notes_max_chars_per_page", 900)),
            enforce_slide_likeness=bool(verify.get("enforce_slide_likeness", False)),
        ),
    )
