#!/usr/bin/env python3.11
"""Codified slide-link knowledge base (see ``slide_link_knowledge_base.md``).

``classify_link(url, course_code, college)`` returns a :class:`LinkVerdict` with
``verdict`` in ``{"accept", "reject", "ambiguous"}``. The rules are distilled from the
human annotations in ``correct.txt`` / ``wrong.txt``:

  * ACCEPT   -- the URL is (or directly lists) real lecture slides/notes for THIS course.
  * REJECT   -- an administrative page (catalog / registrar / bulletin / course-info /
                syllabus PDF / library guide / schedule search), a bare homepage, a
                README/homework file, or an aggregator.
  * AMBIGUOUS-- can only be decided by looking at the page content (course homepage,
                GitHub repo root, a cross-university code coincidence, an unnamed PDF).
                Resolved downstream by fetching (``find_slides``) and, failing that, a
                subagent.

Pure and network-free so it can be unit-tested and reused by both the deterministic
classifier and the validation harness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ccr_slide_resolve import _norm, _univ_tokens, code_variants
from slidefetch.urls import host_of, is_university

try:  # curated host -> canonical school map (best-effort; not every school is listed)
    from school_map import resolve_school
except Exception:  # pragma: no cover - keep working without it
    resolve_school = None  # type: ignore


@dataclass
class LinkVerdict:
    verdict: str          # "accept" | "reject" | "ambiguous"
    confidence: float     # 0..1
    reason: str

    @property
    def accepted(self) -> bool:
        return self.verdict == "accept"


# --------------------------------------------------------------------------- #
# Signal regexes (matched against the unquoted, lowercased path unless noted)
# --------------------------------------------------------------------------- #
_PPT_RE = re.compile(r"\.pptx?(?:$|[?#])", re.I)
_PDF_RE = re.compile(r"\.pdf(?:$|[?#])", re.I)

# A PDF/GitHub filename that looks like a lecture deck.
_DECK_NAME_RE = re.compile(
    r"lecture|(?:^|[^a-z])lec(?:\d|ture|_|-)|slide|handout|lesson|chapter|(?:^|[^a-z])ch\d"
    r"|(?:^|[^a-z])l\d{1,2}(?:$|[^a-z])|intro|introduction|welcome|(?:^|[^a-z])week\d"
    r"|topic\d|class\d|(?:^|[^a-z])notes?(?:$|[^a-z])|^\d{1,2}[-_ ]|/\d{1,2}[-_ ]",
    re.I,
)
# A PDF/GitHub filename that is clearly an administrative document, not a deck.
_DOC_NAME_RE = re.compile(
    r"syllab|readme|course[-_]?manual|fact[-_ ]?sheet|requirem|handbook|roadmap|advising"
    r"|advice|prerequisite|curriculum|catalog|factsheet|course[-_]?outline|registration"
    r"|knowledge[-_]?slides|course[-_]?catalog",
    re.I,
)

# Administrative pages -- near-exclusive to the WRONG set.
_HARD_NEG_RE = re.compile(
    r"catalog|bulletin|course[-_]?descriptions?|course[-_]?info|courseinfo|course[-_]?listings?"
    r"|course[-_]?offerings?|viewcatalog|view_catalog|academic[-_]?calendar|academiccalendar"
    r"|preview_course|courses_list|showcourse|p_showform|szkschd|programs/bpid|coursicle"
    r"|viewsyllabus|syllabi|model[-_]?course|registrar|class[-_]?schedule"
    r"|(?:^|\.)classes\.|schedule/search|/schedule/search|libguides|libraryguides|reserves\."
    r"|coursebook|course[-_]?map|coursemap|syllabus\.website|course[-_]?preview"
    r"|class[-_]?reviews|ratemycourses|wolfware",
    re.I,
)
# Hosts that are library / registrar services.
_LIBRARY_HOST_RE = re.compile(r"(?:^|\.)librar(?:y|ies)\.|(?:^|\.)libguides\.", re.I)

# A slide/lecture/notes location in the path.
_POSITIVE_PATH_RE = re.compile(
    r"/lectures?(?:/|\.|$)|/slides?(?:/|\.|$)|/lecture[_-]?slides?|/lecture[_-]?notes?"
    r"|/notes?(?:/|\.|$)|/handouts?|lectures?\.(?:html?|php|shtml)"
    r"|handouts?\.(?:html?|shtml)|notes?\.html?|lect\.html?",
    re.I,
)

# GitHub file/folder that is definitely a deck location.
_GH_DECK_DIR_RE = re.compile(r"/(?:slides?|lecture[_-]?slides?|lectures?|decks?)(?:/|%20)", re.I)
# GitHub path that is clearly not a deck (homework / readme / project / markdown).
_GH_BAD_RE = re.compile(r"readme|syllab|(?:^|[^a-z])hw\b|[-_]hw\b|homework|hw\d|[-_]project|\.md(?:$|[?#])", re.I)

# Note-selling / study-aggregator / course-review / slide-dump / tutoring sites.
# They match course codes freely but never hold the real lecture deck, so any
# host ending in one of these is rejected outright.
_AGGREGATOR_HOSTS = (
    "coursehero.com", "studocu.com", "scribd.com", "slideshare.net", "slidetodoc.com",
    "quizlet.com", "chegg.com", "coursicle.com", "ratemyprofessors.com", "youtube.com",
    "youtu.be", "reddit.com", "academia.edu", "researchgate.net",
    "collegeclassreviews.com", "coursesidekick.com", "oneclass.com", "coursemapper.co",
    "stuvia.com", "studysoup.com", "wizeprep.com", "slideserve.com", "gradebuddy.com",
    "docsity.com", "studylib.net", "collegecoursepreview.com", "ratemycourses.io",
    "studypool.com", "carletoncoursemap.ca", "coursaty.com", "knowunity.com",
    "brainscape.com", "cramberry.net", "cram.com", "coursef.com", "vaia.com",
    "planetterp.com", "edubirdie.com", "sweetstudy.com", "piazza.com", "bartleby.com",
    "studymoose.com", "homeworkmarket.com", "coursebook.com", "nexpel.com",
    "gradesfixer.com", "studocu.net", "brainly.com", "brainly.co", "numerade.com",
    "transtutors.com", "toppr.com", "vedantu.com",
    # Social media / general platforms -- surfaced by API search, never the deck.
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "tiktok.com", "pinterest.com", "quora.com", "medium.com", "amazon.com",
    "apple.com", "spotify.com", "tumblr.com", "wordpress.com", "blogspot.com",
    "yelp.com", "glassdoor.com", "indeed.com", "coursera.org", "udemy.com",
    "edx.org", "khanacademy.org", "wikipedia.org", "fandom.com", "pdfcoffee.com",
    "vdocument.in", "fdocuments.net", "dokumen.pub", "yumpu.com", "issuu.com",
)



# --------------------------------------------------------------------------- #
# Institution matching (catches cross-university code coincidences)
# --------------------------------------------------------------------------- #
_GENERIC_SCHOOL_TOKENS = {
    "university", "college", "institute", "state", "technology", "school",
    "polytechnic", "system", "the", "of", "and", "at",
}


def _college_matches_school(college: str, school) -> bool:
    """True if a curated ``School`` clearly refers to ``college``."""
    col = (college or "").lower()
    if not col:
        return False
    kw = (getattr(school, "rmp_keyword", "") or "").lower()
    if kw and kw in col:
        return True
    canon = (getattr(school, "canonical", "") or "").lower()
    col_toks = {t for t in re.split(r"[^a-z]+", col)
                if len(t) >= 4 and t not in _GENERIC_SCHOOL_TOKENS}
    canon_toks = {t for t in re.split(r"[^a-z]+", canon)
                  if len(t) >= 4 and t not in _GENERIC_SCHOOL_TOKENS}
    return bool(col_toks & canon_toks)


def institution_conflict(url: str, college: str) -> bool:
    """True if the host resolves to a *different* known university than ``college``.

    This is where the cross-university false positives came from (e.g. a Binghamton
    ``CS110`` link pointing at ``web.stanford.edu/class/cs110``). Hosts not in the
    curated map return False (treated as "could be this school").
    """
    if resolve_school is None:
        return False
    try:
        sch = resolve_school(url)
    except Exception:  # noqa: BLE001
        return False
    if not sch or not college:
        return False
    return not _college_matches_school(college, sch)


def _acronym(college: str) -> str:
    """Initialism of a college name, e.g. 'Arizona State University' -> 'asu'."""
    words = [w for w in re.split(r"[^A-Za-z]+", college or "")
             if w and w.lower() not in {"of", "the", "and", "at"}]
    return "".join(w[0] for w in words).lower() if len(words) >= 2 else ""


def same_institution(url: str, college: str) -> bool:
    """True if the host is confidently the course's own university."""
    if resolve_school is not None:
        try:
            sch = resolve_school(url)
        except Exception:  # noqa: BLE001
            sch = None
        if sch and _college_matches_school(college, sch):
            return True
    host = host_of(url)
    host_n = _norm(host)
    if any(t in host_n for t in _univ_tokens(college)):
        return True
    # Acronym as a full dot-delimited host label (asu.edu, bu.edu, cmu.edu, ucsd.edu).
    # Ignore public-suffix labels so a short acronym like "ac" (Amherst College)
    # does not match the ".ac" in a ".ac.uk" host (economicsnetwork.ac.uk).
    ac = _acronym(college)
    if len(ac) >= 2:
        _SUFFIX_LABELS = {"ac", "co", "edu", "gov", "com", "org", "net", "int",
                          "mil", "uk", "us", "ca", "au", "nz", "in", "eu"}
        labels = host.split(".")
        cand = {l for l in labels[:-1] if l not in _SUFFIX_LABELS}
        if ac in cand:
            return True
    return False


def confirmation_trust(url: str, course_code: str, college: str) -> tuple[bool, str]:
    """Whether finding decks at *url* is enough to trust it as THIS course's slides.

    A university publishes on a domain that carries its own name/acronym, so:
      * same institution (name/acronym/school-map match) -> trust;
      * a *different* academic host (.edu / known university) -> do NOT trust -- this is
        where cross-university code coincidences live (a Binghamton ``CS110`` link that
        points at ``web.stanford.edu`` or an ASU ``ECE352`` link at ``ece.wisc.edu``);
      * a non-academic host (GitHub / Pages / vanity domain) carries no university name,
        so a course-code match with no known conflict is the best available signal -> trust.
    Everything else is judged by a subagent on topic.
    """
    if same_institution(url, college):
        return True, "same-institution"
    ok_univ, _why = is_university(url, allow_course_hosts=False)  # academic-tld / known univ
    if ok_univ:
        return False, "other-university"
    variants, _num = code_variants(course_code)
    blob = _norm(host_of(url) + urlsplit(url).path)
    code_hit = any(v and _norm(v) in blob for v in variants)
    if code_hit and not institution_conflict(url, college):
        return True, "code-match,non-academic"
    return False, "offsite"


# --------------------------------------------------------------------------- #
# Main classifier
# --------------------------------------------------------------------------- #
def classify_link(url: str, course_code: str, college: str) -> LinkVerdict:
    if not url or not url.strip():
        return LinkVerdict("reject", 0.99, "empty")
    host = host_of(url)
    if not host:
        return LinkVerdict("reject", 0.9, "no-host")
    if any(host == h or host.endswith("." + h) for h in _AGGREGATOR_HOSTS):
        return LinkVerdict("reject", 0.95, f"aggregator:{host}")

    parts = urlsplit(url)
    path = unquote(parts.path)
    pl = path.lower()
    fname = pl.rsplit("/", 1)[-1]
    blob = (host + " " + pl).lower()
    is_github = host == "github.com" or host.endswith(".github.com")
    is_pages = bool(re.search(r"(?:^|\.)(github\.io|gitlab\.io|pages\.dev|netlify\.app)$", host))

    # 1. Deck extension wins (a .pptx named "syllabus" is still a lecture deck).
    if _PPT_RE.search(pl):
        if institution_conflict(url, college):
            return LinkVerdict("ambiguous", 0.4, "deck:pptx,cross-university")
        return LinkVerdict("accept", 0.92, "deck:pptx")
    if _PDF_RE.search(pl):
        if _DOC_NAME_RE.search(fname):
            return LinkVerdict("reject", 0.9, f"pdf:admin-doc:{fname[:40]}")
        looks_deck = bool(_DECK_NAME_RE.search(fname)) or bool(_POSITIVE_PATH_RE.search(pl))
        if looks_deck:
            if institution_conflict(url, college):
                return LinkVerdict("ambiguous", 0.4, "deck:pdf,cross-university")
            return LinkVerdict("accept", 0.85, "deck:pdf")
        # PDF with an unrecognised name -> could be a deck or a doc; needs a look.
        return LinkVerdict("ambiguous", 0.3, "pdf:unknown-name")

    # 2. Hard-negative administrative pages.
    if _HARD_NEG_RE.search(blob) or _LIBRARY_HOST_RE.search(host):
        m = _HARD_NEG_RE.search(blob)
        return LinkVerdict("reject", 0.9, f"admin:{(m.group(0) if m else 'library')}")

    # 3. Bare homepage. Reject only a big *institutional* root (e.g. www.cpp.edu/);
    #    a course-specific subdomain (cs106b.stanford.edu/) or a vanity/Pages course
    #    site (csci1410-2023.vercel.app/) is a real course site -> confirm by content.
    if pl in ("", "/") and not is_github:
        variants, _num = code_variants(course_code)
        host_has_code = any(v and _norm(v) in _norm(host) for v in variants)
        inst_ok, _ = is_university(url, allow_course_hosts=False)
        if inst_ok and not host_has_code:
            return LinkVerdict("reject", 0.8, "bare-institutional-homepage")
        return LinkVerdict("ambiguous", 0.4, "bare-root:course-site?")

    # 4. GitHub repos (github.com is blocked by is_university; judge by path shape).
    if is_github:
        if _GH_BAD_RE.search(pl):
            return LinkVerdict("reject", 0.8, "github:readme/hw/project")
        if _GH_DECK_DIR_RE.search(pl):
            return LinkVerdict("ambiguous", 0.55, "github:slides-dir")  # confirm decks exist
        return LinkVerdict("ambiguous", 0.35, "github:repo")

    ok, why = is_university(url, allow_course_hosts=True)

    # 5. Positive slide/lecture path. Auto-accept ONLY when the host is confidently the
    #    course's own university; otherwise (Pages host, cross-university, or an academic
    #    host we cannot tie to this college) defer to a content/topic check -- this is
    #    where cross-university code coincidences hide.
    if _POSITIVE_PATH_RE.search(pl):
        if institution_conflict(url, college):
            return LinkVerdict("ambiguous", 0.45, "slides-path,cross-university")
        if (ok or is_pages) and same_institution(url, college):
            return LinkVerdict("accept", 0.85, f"slides-path,{why}")
        return LinkVerdict("ambiguous", 0.5, f"slides-path,unconfirmed-institution:{host}")

    # 6. Course/Pages host carrying the course code but no explicit slide path.
    variants, num = code_variants(course_code)
    code_hit = any(v and _norm(v) in _norm(host + pl) for v in variants)
    if (ok or is_pages) and code_hit:
        return LinkVerdict("ambiguous", 0.5, "course-page,has-code")

    # 7. Everything else needs a look.
    if not ok and not is_pages:
        return LinkVerdict("ambiguous", 0.25, f"non-university:{host}")
    return LinkVerdict("ambiguous", 0.3, "no-strong-signal")


if __name__ == "__main__":  # tiny smoke test
    tests = [
        ("https://courses.cs.washington.edu/courses/cse512/25sp/index.html", "CSE512", "University of Washington"),
        ("https://www.baruch.cuny.edu/courseinfo/detail/CIS2200", "CIS2200", "Baruch College"),
        ("https://web.stanford.edu/class/cs110/lectures/cs110-win2122-lecture-1.pdf", "CS110", "Binghamton University"),
        ("https://scai.engineering.asu.edu/wp-content/uploads/sites/31/2025/03/CSE-205-Syllabus-SP25.pdf", "CSE205", "Arizona State University"),
        ("https://github.com/timnaimov/CIS-2300-HW", "CIS2300", "Baruch College"),
        ("https://calpoly-iandunn.github.io/csc476/lectures/", "CSC476", "California Polytechnic State University"),
    ]
    for u, c, col in tests:
        v = classify_link(u, c, col)
        print(f"{v.verdict:>9} {v.confidence:.2f} {v.reason:<32} {u}")
