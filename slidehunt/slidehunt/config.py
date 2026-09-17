"""Typed configuration loaded from ``config.yaml`` for slidehunt."""
from __future__ import annotations

from dataclasses import dataclass, field
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
class SearchConfig:
    max_results: int
    use_browser: bool


@dataclass
class SlidesConfig:
    max_decks: int
    max_index_follow: int
    max_hits_per_code: int


@dataclass
class RmpConfig:
    request_delay_s: float


@dataclass
class CcrConfig:
    base: str


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
    skip_dirs: list[Path]
    http: HttpConfig
    search: SearchConfig
    slides: SlidesConfig
    rmp: RmpConfig
    ccr: CcrConfig
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
    search = raw.get("search", {})
    slides = raw.get("slides", {})
    rmp = raw.get("rmp", {})
    ccr = raw.get("ccr", {})
    verify = raw.get("verify", {})

    skip_dirs = [_resolve(root, str(p)) for p in raw.get("skip_dirs", [])]

    return Settings(
        data_dir=_resolve(root, paths.get("data_dir", "data")),
        work_dir=_resolve(root, paths.get("work_dir", "work")),
        db_path=_resolve(root, paths.get("db_path", "slidehunt.db")),
        skip_dirs=skip_dirs,
        http=HttpConfig(
            user_agent=str(http.get("user_agent", "Mozilla/5.0")),
            timeout_s=int(http.get("timeout_s", 30)),
            max_retries=int(http.get("max_retries", 3)),
            min_interval_s=float(http.get("min_interval_s", 1.0)),
            cache_max_age_s=int(http.get("cache_max_age_s", 604800)),
            render_fallback=bool(http.get("render_fallback", True)),
        ),
        search=SearchConfig(
            max_results=int(search.get("max_results", 8)),
            use_browser=bool(search.get("use_browser", True)),
        ),
        slides=SlidesConfig(
            max_decks=int(slides.get("max_decks", 40)),
            max_index_follow=int(slides.get("max_index_follow", 3)),
            max_hits_per_code=int(slides.get("max_hits_per_code", 4)),
        ),
        rmp=RmpConfig(
            request_delay_s=float(rmp.get("request_delay_s", 0.4)),
        ),
        ccr=CcrConfig(
            base=str(ccr.get("base", "https://collegeclassreviews.com")).rstrip("/"),
        ),
        verify=VerifyConfig(
            min_pages=int(verify.get("min_pages", 3)),
            slide_min_aspect_ratio=float(verify.get("slide_min_aspect_ratio", 1.4)),
            notes_max_chars_per_page=float(verify.get("notes_max_chars_per_page", 900)),
            enforce_slide_likeness=bool(verify.get("enforce_slide_likeness", False)),
        ),
    )
