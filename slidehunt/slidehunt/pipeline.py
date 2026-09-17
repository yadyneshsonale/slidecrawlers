"""Orchestration: generate codes -> search -> download slides -> ratings.

For each randomly-generated course code the pipeline searches the web, follows
the resulting links to download real lecture slides (skipping MIT and anything
already present in the slidefetch corpus), then enriches the course with RMP
professor ratings and CCR course ratings. Every important step is logged so the
tmux run.log reads as a live trace.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from . import ccr, store
from .codes import code_stream, generate_codes
from .config import Settings
from .download import download
from .extract import discover_index_links, discover_slides
from .http_util import get_html
from .people import extract_instructors
from .rmp import find_school, lookup, normalize_name
from .search import web_search
from .urls import (
    already_downloaded,
    host_to_university,
    is_blocked_host,
    is_mit,
    page_slug,
    reduce_url,
)
from .verify import verify_office, verify_pdf


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str, indent: int = 0) -> None:
    prefix = "  " * indent
    print(f"[{_now()}] {prefix}{msg}", flush=True)


def _title_of(hit) -> str:
    return getattr(hit, "title", "") or ""


def _verify(path: Path, file_type: str, settings: Settings):
    if file_type == "pdf":
        return verify_pdf(path, settings.verify)
    return verify_office(path)


class Pipeline:
    def __init__(self, settings: Settings, conn) -> None:
        self.settings = settings
        self.conn = conn
        self.known = store.known_sha(conn)
        self._school_cache: dict[str, dict | None] = {}
        # Folders to treat as "already downloaded" (skip): slidefetch corpus + our own data.
        self.skip_roots = list(settings.skip_dirs) + [settings.data_dir]

    # ---- university / ratings -------------------------------------------- #
    def _school(self, name: str) -> dict | None:
        if name not in self._school_cache:
            try:
                self._school_cache[name] = find_school(name)
            except Exception as err:  # noqa: BLE001
                log(f"RMP school lookup failed for {name!r}: {err}", 3)
                self._school_cache[name] = None
        return self._school_cache[name]

    def _ratings(self, course_id: str, uni: dict, code: str, html: str, title: str) -> None:
        # --- RMP professors --------------------------------------------- #
        instructors = extract_instructors(html, title)
        log(f"instructors found: {instructors or 'none'}", 3)
        if instructors:
            school = self._school(uni["name"])
            if school:
                store.upsert_university(self.conn, {
                    "slug": uni["slug"], "name": uni["name"], "host": uni["host"],
                    "ccr_slug": uni["ccr_slug"],
                    "rmp_school_id": school.get("id"),
                    "rmp_school_name": school.get("name"),
                })
                for person in instructors:
                    nm = normalize_name(person)
                    if not nm:
                        continue
                    row = lookup(nm, school["id"], delay=self.settings.rmp.request_delay_s)
                    pid = store.upsert_professor(self.conn, {
                        "name": nm, "uni_slug": uni["slug"],
                        "rmp_legacy_id": row.get("rmp_legacy_id"),
                        "matched_name": row.get("matched_name"),
                        "department": row.get("department"),
                        "avg_rating": row.get("avg_rating"),
                        "avg_difficulty": row.get("avg_difficulty"),
                        "num_ratings": row.get("num_ratings"),
                        "would_take_again": row.get("would_take_again"),
                        "rmp_url": row.get("rmp_url"),
                        "match_type": row.get("match_type"),
                    })
                    if pid:
                        store.link_course_professor(self.conn, course_id, pid, "websearch")
                    if row.get("rmp_url"):
                        store.add_link(self.conn, course_id, "rmp", row["rmp_url"])
                    log(f"RMP {nm}: {row.get('match_type')} "
                        f"rating={row.get('avg_rating')}", 4)
            else:
                log(f"no RMP school match for {uni['name']!r}", 3)

        # --- CCR course rating ------------------------------------------ #
        if uni.get("ccr_slug"):
            try:
                row = ccr.fetch_course(self.settings, uni["ccr_slug"], code)
            except Exception as err:  # noqa: BLE001
                log(f"CCR lookup failed: {err}", 3)
                row = None
            if row:
                store.upsert_ccr_ratings(self.conn, {
                    "course_id": course_id,
                    "ccr_url": row.get("ccr_url"),
                    "star_rating": row.get("star_rating"),
                    "num_reviews": row.get("num_reviews"),
                    "difficulty": row.get("difficulty"),
                    "student_satisfaction": row.get("student_satisfaction"),
                    "challenge_level": row.get("challenge_level"),
                    "grade_accessibility": row.get("grade_accessibility"),
                    "time_investment": row.get("time_investment"),
                    "attendance_importance": row.get("attendance_importance"),
                    "recommendation_rate": row.get("recommendation_rate"),
                })
                store.add_link(self.conn, course_id, "ccr", row.get("ccr_url"))
                log(f"CCR {row.get('star_rating')}* "
                    f"({row.get('num_reviews')} reviews)", 3)
            else:
                log("no CCR ratings", 3)

    # ---- slide download -------------------------------------------------- #
    def _candidates(self, reduced: str):
        """Return (html, title, candidates) following up to N index hops."""
        try:
            html = get_html(reduced, self.settings.http, self.settings.cache_dir)
        except Exception as err:  # noqa: BLE001
            log(f"fetch failed: {err}", 3)
            return "", []
        if not html:
            return "", []
        cands = discover_slides(html, reduced)
        if cands:
            return html, cands
        # Follow a few index links looking for a slide listing.
        for idx_url in discover_index_links(html, reduced)[: self.settings.slides.max_index_follow]:
            log(f"following index link: {idx_url}", 3)
            try:
                sub = get_html(idx_url, self.settings.http, self.settings.cache_dir)
            except Exception:  # noqa: BLE001
                continue
            if not sub:
                continue
            sub_cands = discover_slides(sub, idx_url)
            if sub_cands:
                return sub, sub_cands
        return html, []

    def _download_deck(self, course_id: str, page_url: str, reduced: str, cands) -> int:
        dest = self.settings.data_dir / page_slug(reduced)
        number = 1
        downloaded = 0
        encrypted_seen = 0
        for cand in cands[: self.settings.slides.max_decks]:
            res = download(cand.url, dest, number, self.settings.http, self.known)
            if not res.ok or not res.file_path:
                if res.error and res.error not in ("exists", "duplicate sha"):
                    log(f"reject {cand.url}: {res.error}", 4)
                continue
            vr = _verify(Path(res.file_path), res.file_type, self.settings)
            if not vr.valid:
                log(f"reject {cand.url}: {vr.reason}", 4)
                try:
                    Path(res.file_path).unlink()
                except OSError:
                    pass
                if vr.reason == "encrypted":
                    encrypted_seen += 1
                    if encrypted_seen >= 2:
                        log("skipping rest: directory is password-protected", 4)
                        break
                continue
            store.record_deck(self.conn, {
                "course_id": course_id,
                "source_url": cand.url,
                "page_url": page_url,
                "file_path": res.file_path,
                "sha256": res.sha256,
                "file_type": res.file_type,
                "lecture_number": cand.lecture_number,
                "status": "verified",
            })
            store.add_link(self.conn, course_id, "slide_source", cand.url)
            log(f"saved {Path(res.file_path).name} "
                f"({vr.pages}p) <- {cand.url}", 4)
            number += 1
            downloaded += 1
        return downloaded

    # ---- per-hit / per-code --------------------------------------------- #
    def process_code(self, code: str, idx: int, total: int) -> None:
        query = f"{code} course slides"
        log(f"[{idx}/{total}] code={code} :: search {query!r}")
        try:
            hits = web_search(
                query,
                limit=self.settings.search.max_results,
                cache_dir=self.settings.search_cache_dir,
                use_browser=self.settings.search.use_browser,
                cache_max_age_s=self.settings.http.cache_max_age_s,
            )
        except Exception as err:  # noqa: BLE001
            log(f"search failed: {err}", 1)
            store.record_search(self.conn, code, query, 0, _now())
            return
        store.record_search(self.conn, code, query, len(hits), _now())
        log(f"{len(hits)} hits", 1)

        considered = 0
        for hit in hits:
            if considered >= self.settings.slides.max_hits_per_code:
                break
            url = hit.url
            if is_mit(url):
                log(f"skip MIT: {url}", 2)
                continue
            if is_blocked_host(url):
                log(f"skip blocked host: {url}", 2)
                continue
            reduced = reduce_url(url)
            if already_downloaded(reduced, self.skip_roots):
                log(f"skip already-downloaded: {reduced}", 2)
                continue
            considered += 1
            log(f"try {reduced}", 2)

            html, cands = self._candidates(reduced)
            if not cands:
                log("no slide links", 3)
                continue
            log(f"{len(cands)} slide candidates", 3)

            uni = host_to_university(reduced)
            course_id = f"{uni['slug']}-{code}"
            title = _title_of(hit)
            store.upsert_university(self.conn, {
                "slug": uni["slug"], "name": uni["name"], "host": uni["host"],
                "ccr_slug": uni["ccr_slug"], "rmp_school_id": None,
                "rmp_school_name": None,
            })
            store.upsert_course(self.conn, {
                "course_id": course_id, "code": code, "uni_slug": uni["slug"],
                "page_url": url, "reduced_url": reduced,
                "slug": page_slug(reduced), "title": title,
                "status": "pending", "created_at": _now(),
            })

            downloaded = self._download_deck(course_id, url, reduced, cands)
            log(f"downloaded {downloaded} deck(s) for {course_id}", 3)
            if downloaded == 0:
                store.set_course_status(self.conn, course_id, "error")
                continue

            self._ratings(course_id, uni, code, html, title)
            store.set_course_status(self.conn, course_id, "done")
            log(f"done {course_id}", 2)


def run(settings: Settings, count: int | None = None, seed: int | None = None,
        max_hits: int | None = None, resume: bool = True,
        until_downloaded: int | None = None) -> None:
    settings.ensure_dirs()
    if max_hits is not None:
        settings.slides.max_hits_per_code = max_hits
    conn = store.connect(settings.db_path)
    try:
        seen = store.seen_codes(conn) if resume else set()
        pipeline = Pipeline(settings, conn)

        if until_downloaded:
            target = until_downloaded
            start = store.downloaded_course_count(conn)
            log(f"target: {target} downloaded courses (currently {start}); "
                f"skipping {len(seen)} seen codes")
            stopped = False
            for code in code_stream(seed, exclude=seen):
                done = store.downloaded_course_count(conn)
                if done >= target:
                    break
                try:
                    pipeline.process_code(code, done, target)
                except KeyboardInterrupt:
                    log("interrupted by user")
                    stopped = True
                    break
                except Exception as err:  # noqa: BLE001
                    log(f"code {code} failed: {err}")
            final = store.downloaded_course_count(conn)
            log(f"{'stopped' if stopped else 'reached'} {final}/{target} "
                f"downloaded courses")
        else:
            n = count or 50
            codes = [c for c in generate_codes(n * 3, seed) if c not in seen][:n]
            log(f"generated {len(codes)} codes (skipping {len(seen)} already seen)")
            for i, code in enumerate(codes, 1):
                try:
                    pipeline.process_code(code, i, len(codes))
                except KeyboardInterrupt:
                    log("interrupted by user")
                    break
                except Exception as err:  # noqa: BLE001
                    log(f"code {code} failed: {err}")
        log(f"counts: {store.counts(conn)}")
    finally:
        conn.close()


def _main(argv=None) -> int:  # pragma: no cover - thin shim
    from .cli import main
    return main(argv if argv is not None else sys.argv[1:])
