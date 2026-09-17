#!/usr/bin/env python3
"""Link manipulations for turning a resolved course link into downloadable decks.

Some confirmed/searched links don't point straight at a page slidefetch can
scrape:

  * ``github.com/<o>/<r>/blob/<ref>/x.pdf``  -> raw.githubusercontent.com file
  * ``github.com/<o>/<r>[/tree/<ref>/<dir>]`` -> enumerate every deck in the repo
    subtree via the Git trees API (one call, optional ``GITHUB_TOKEN``)
  * ``docs.google.com/presentation/d/<ID>``   -> ``/export/pdf`` direct download

Each helper is defensive: it returns ``[]`` / the input URL on any failure so the
caller can fall back to the normal slidefetch gather path.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

_DECK_EXTS = (".pdf", ".ppt", ".pptx")
_UA = "slidefetch-rated4ccr/1.0 (+https://github.com)"


@dataclass
class FixedLink:
    url: str
    name: str
    file_type: str


def _ext_of(path: str) -> str:
    p = path.lower()
    for e in ("pptx", "ppt", "pdf"):
        if p.endswith("." + e):
            return e
    return "pdf"


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def raw_github(url: str) -> str:
    """Rewrite a ``github.com/.../blob/...`` file URL to its raw form."""
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com", 1).replace(
            "/blob/", "/", 1)
    return url


def _gh_api(path: str, timeout: int = 30):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


_GH_RE = re.compile(
    r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)/(.*))?$",
    re.IGNORECASE,
)


def is_github(url: str) -> bool:
    return "github.com" in (urlparse(url).netloc.lower())


def github_decks(url: str, *, max_files: int = 400) -> list[FixedLink]:
    """Enumerate deck files in a GitHub repo (or a subtree) as raw URLs.

    ``.../blob/<ref>/file.pdf`` returns just that file. A repo root or
    ``.../tree/<ref>/<dir>`` lists every ``.pdf/.ppt/.pptx`` under that path via
    one recursive trees API call. Returns ``[]`` on any error (rate-limit, etc.).
    """
    m = _GH_RE.search(url.split("#", 1)[0].split("?", 1)[0])
    if not m:
        return []
    owner, repo, ref, subpath = m.group(1), m.group(2), m.group(3), (m.group(4) or "")

    # Direct single-file blob link.
    if "/blob/" in url and subpath and subpath.lower().endswith(_DECK_EXTS):
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{subpath}"
        return [FixedLink(raw, subpath.rsplit("/", 1)[-1], _ext_of(subpath))]

    # Resolve the ref (branch) if the URL didn't name one.
    if not ref:
        try:
            info = _gh_api(f"/repos/{owner}/{repo}")
            ref = info.get("default_branch") or "main"
        except Exception:  # noqa: BLE001
            ref = "main"
    try:
        tree = _gh_api(f"/repos/{owner}/{repo}/git/trees/{ref}?recursive=1")
    except Exception:  # noqa: BLE001
        return []
    prefix = subpath.rstrip("/")
    out: list[FixedLink] = []
    for node in tree.get("tree", []):
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        if prefix and not (path == prefix or path.startswith(prefix + "/")):
            continue
        if not path.lower().endswith(_DECK_EXTS):
            continue
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        out.append(FixedLink(raw, path.rsplit("/", 1)[-1], _ext_of(path)))
        if len(out) >= max_files:
            break
    return out


# --------------------------------------------------------------------------- #
# Google Slides
# --------------------------------------------------------------------------- #
_GSLIDES_RE = re.compile(r"docs\.google\.com/presentation/d/(?:e/)?([A-Za-z0-9_-]+)", re.I)


def gslides_pdf(url: str) -> FixedLink | None:
    """Turn a Google Slides presentation URL into a direct PDF-export link."""
    m = _GSLIDES_RE.search(url)
    if not m:
        return None
    slide_id = m.group(1)
    export = f"https://docs.google.com/presentation/d/{slide_id}/export/pdf"
    return FixedLink(export, f"gslides_{slide_id[:12]}", "pdf")
