"""Canonical school + discipline knowledge for the course pipeline.

This is the data that normally lives "in a human's head": which course-page
hostname belongs to which university, what that university is called on
RateMyProfessors (RMP) vs. CollegeClassReviews (CCR), and which department
abbreviations belong to the same academic discipline (cs == cse == eecs == ...).

Everything here is plain data + small pure helpers so it is easy to extend and
unit-test. No network, no LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


# --------------------------------------------------------------------------- #
# Schools
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class School:
    canonical: str          # human-readable, unambiguous name
    rmp_keyword: str        # substring that must appear in an RMP school name
    ccr_slug: str | None    # collegeclassreviews.com university slug (None=unknown)


# Ordered list of (host fragment, School). The first fragment found in a course
# URL's hostname wins, so put more specific fragments before generic ones.
#
# rmp_keyword is matched case-insensitively as a substring of the RMP
# "school.name" field (see rmp_crawler.school_matches). ccr_slug is the path
# segment in https://collegeclassreviews.com/universities/<slug>/...
# A None ccr_slug means "not yet verified" -> the pipeline flags CCR for review
# instead of guessing.
SCHOOLS: list[tuple[str, School]] = [
    ("ocw.mit.edu",      School("Massachusetts Institute of Technology", "massachusetts institute of technology", "massachusetts-institute-of-technology")),
    ("mit.edu",          School("Massachusetts Institute of Technology", "massachusetts institute of technology", "massachusetts-institute-of-technology")),
    ("cseweb.ucsd.edu",  School("University of California, San Diego",    "san diego",              "uc-san-diego")),
    ("ucsd.edu",         School("University of California, San Diego",    "san diego",              "uc-san-diego")),
    ("cs61a.org",        School("University of California, Berkeley",     "berkeley",               "uc-berkeley")),
    ("berkeley.edu",     School("University of California, Berkeley",     "berkeley",               "uc-berkeley")),
    ("stanford.edu",     School("Stanford University",                   "stanford",               "stanford-university")),
    ("cs.cmu.edu",       School("Carnegie Mellon University",            "carnegie mellon",        "carnegie-mellon-university")),
    ("cmu.edu",          School("Carnegie Mellon University",            "carnegie mellon",        "carnegie-mellon-university")),
    ("eecs.umich.edu",   School("University of Michigan",                "michigan",               "university-of-michigan")),
    ("umich.edu",        School("University of Michigan",                "michigan",               "university-of-michigan")),
    ("cs.cornell.edu",   School("Cornell University",                    "cornell",                "cornell-university")),
    ("cornell.edu",      School("Cornell University",                    "cornell",                "cornell-university")),
    ("cs.princeton.edu", School("Princeton University",                  "princeton",              "princeton-university")),
    ("princeton.edu",    School("Princeton University",                  "princeton",              "princeton-university")),
    ("cc.gatech.edu",    School("Georgia Institute of Technology",       "georgia institute",      "georgia-tech")),
    ("gatech.edu",       School("Georgia Institute of Technology",       "georgia institute",      "georgia-tech")),
    ("usc.edu",          School("University of Southern California",      "southern california",    "university-of-southern-california")),
    ("cs.washington.edu", School("University of Washington",             "washington",             "university-of-washington")),
    ("washington.edu",   School("University of Washington",              "washington",             "university-of-washington")),
    ("toronto.edu",      School("University of Toronto",                 "toronto",                "university-of-toronto")),
    ("utoronto.ca",      School("University of Toronto",                 "toronto",                "university-of-toronto")),
    ("ucla.edu",         School("University of California, Los Angeles", "los angeles",            "uc-los-angeles")),
    ("illinois.edu",     School("University of Illinois Urbana-Champaign", "illinois",             "university-of-illinois-urbana-champaign")),
    ("harvard.edu",      School("Harvard University",                    "harvard",                "harvard-university")),
    ("ucalgary.ca",      School("University of Calgary",                 "calgary",                "university-of-calgary")),
    ("pitt.edu",         School("University of Pittsburgh",              "pittsburgh",             "university-of-pittsburgh")),
    ("umn.edu",          School("University of Minnesota",               "minnesota",              "university-of-minnesota-twin-cities")),
]


def host_of(url: str) -> str:
    """Lowercase hostname without a leading www. (best effort)."""
    netloc = urlsplit(url if "//" in url else "//" + url).netloc.lower()
    netloc = netloc.split("@")[-1].split(":")[0]
    return netloc[4:] if netloc.startswith("www.") else netloc


def resolve_school(url: str) -> School | None:
    """Map a course URL to a canonical School, or None if the host is unknown."""
    host = host_of(url)
    for fragment, school in SCHOOLS:
        if fragment in host:
            return school
    return None


# --------------------------------------------------------------------------- #
# Disciplines  (department abbreviation <-> field)
# --------------------------------------------------------------------------- #
# A discipline groups the department prefixes that a human treats as "the same
# field". This is what lets the pipeline know that searching for a CS course
# code should accept cse/eecs/csci/... and reject mus/poli.
DISCIPLINES: dict[str, list[str]] = {
    "cs": ["cs", "cse", "csci", "csc", "cos", "comp", "compsci", "cmsc",
           "csce", "cmpsc", "cmpt", "cpsc", "eecs", "coms"],
    "ee": ["ee", "ece", "eecs", "eel", "elec", "eng", "ene"],
    "math": ["math", "mat", "ma", "amath", "apma", "stat", "stats"],
    "me": ["me", "mech", "mae", "meam", "mecheng"],
    "phys": ["phys", "physics", "phy"],
    "bio": ["bio", "biol", "bild", "bimm", "bicd", "mcb"],
    "chem": ["chem", "chm", "che"],
}

# abbreviation -> set of disciplines it belongs to (eecs spans cs and ee).
_ABBR_TO_DISCIPLINES: dict[str, set[str]] = {}
for _disc, _abbrs in DISCIPLINES.items():
    for _a in _abbrs:
        _ABBR_TO_DISCIPLINES.setdefault(_a, set()).add(_disc)


# code = letters + number(+optional trailing letter), e.g. cse127, cs61a, math20c
_CODE_RE = re.compile(r"([a-z]{2,8})[\s\-_]?(\d{2,4})([a-z]?)", re.I)
# MIT-style numeric department codes, e.g. 6.042J, 18.06
_MIT_CODE_RE = re.compile(r"\b(\d{1,2})[.\-_](\d{2,4})([a-z]?)\b", re.I)


@dataclass
class CourseCode:
    raw: str            # as found, e.g. "cse127"
    prefix: str         # letters, e.g. "cse" ("" for MIT numeric)
    number: str         # digits, e.g. "127"
    suffix: str = ""    # trailing section letter, e.g. "a"
    disciplines: set[str] = field(default_factory=set)

    @property
    def ccr_code(self) -> str:
        """Slug form used by CCR, e.g. 'cse127' (section suffix dropped)."""
        return f"{self.prefix}{self.number}".lower()


def extract_course_code(url: str) -> CourseCode | None:
    """Pull a course code (cse127, cs61a, 6.042) out of a course URL.

    Scans the path segments right-to-left so the most specific course token
    (usually the last meaningful segment) wins over directory noise.
    """
    parts = urlsplit(url if "//" in url else "//" + url)
    segments = [s for s in re.split(r"[/]", parts.path) if s]
    segments.reverse()

    seasons = {"wi", "fa", "sp", "su", "win", "spr", "sum", "fall", "aut",
               "summer", "autumn", "winter", "spring"}
    for seg in segments:
        # MIT-style numeric department codes (6.042, 18.06) are very specific
        # and can appear mid-segment, so try them before the generic pattern.
        mm = _MIT_CODE_RE.search(seg)
        if mm:
            return CourseCode(
                raw=f"{mm.group(1)}.{mm.group(2)}{mm.group(3)}",
                prefix="",
                number=f"{mm.group(1)}.{mm.group(2)}",
                suffix=mm.group(3).lower(),
                disciplines=set(),
            )
        m = _CODE_RE.search(seg)
        if m:
            prefix = m.group(1).lower()
            number = m.group(2)
            suffix = m.group(3).lower()
            # Reject term tokens like "wi21"/"fall2010" (season + year).
            if prefix in seasons:
                continue
            return CourseCode(
                raw=f"{prefix}{number}{suffix}",
                prefix=prefix,
                number=number,
                suffix=suffix,
                disciplines=_ABBR_TO_DISCIPLINES.get(prefix, set()),
            )
    return None


def sibling_abbreviations(code: CourseCode) -> list[str]:
    """Department abbreviations to also try on CCR for the same course number.

    Ordered: the exact prefix first, then other prefixes in the same
    discipline(s). Used so a 'cse127' miss can fall back to 'cs127'/'csci127'.
    """
    ordered: list[str] = []
    if code.prefix:
        ordered.append(code.prefix)
    for disc in sorted(code.disciplines):
        for abbr in DISCIPLINES.get(disc, []):
            if abbr not in ordered:
                ordered.append(abbr)
    return ordered
