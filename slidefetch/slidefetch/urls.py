"""URL helpers for the search-driven workflow.

Pure (network-free) functions used to:
  * decide whether a URL belongs to a university / course host,
  * reduce a deep slide URL to the directory that lists the whole course,
  * derive a stable output-folder slug for a course page,
  * tell whether a course has already been downloaded.

Keeping these here (instead of in cli.py) lets ``search.py`` reuse them without
importing the command-line layer, and makes them straightforward to unit-test.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from slidefetch.download import sanitize

SLIDE_EXTS = (".pdf", ".ppt", ".pptx")

# --------------------------------------------------------------------------- #
# University / course-host detection
# --------------------------------------------------------------------------- #

# Universities whose course pages do NOT sit on an academic TLD (.edu/.ac.*).
# Registrable domains only; subdomains (cs.<domain>, www.<domain>) also match.
KNOWN_UNIVERSITIES = {
    # Switzerland
    "ethz.ch", "epfl.ch", "uzh.ch", "unige.ch", "unibe.ch", "usi.ch",
    # Canada (universities publish directly on .ca)
    "uwaterloo.ca", "utoronto.ca", "ucalgary.ca", "mcgill.ca", "ubc.ca",
    "sfu.ca", "yorku.ca", "queensu.ca", "uottawa.ca", "ualberta.ca",
    "umontreal.ca", "concordia.ca", "uvic.ca", "dal.ca", "umanitoba.ca",
    # Germany / Austria / Netherlands / Nordics (generic ccTLDs)
    "tum.de", "kit.edu", "rwth-aachen.de", "uni-freiburg.de", "tu-berlin.de",
    "tu-darmstadt.de", "lmu.de", "uni-tuebingen.de", "tuwien.ac.at",
    "tudelft.nl", "uva.nl", "uu.nl", "ru.nl", "vu.nl",
    "kth.se", "chalmers.se", "lu.se", "uu.se", "ntnu.no", "uio.no", "dtu.dk",
    "aalto.fi", "helsinki.fi",
    # Israel / others
    "technion.ac.il", "weizmann.ac.il", "huji.ac.il", "tau.ac.il",
}

# Course-material hosts that are not universities themselves but are widely used
# to publish real university course pages (project sites). Accepted by default;
# disable with ``allow_course_hosts=False``.
COURSE_HOSTS = ("github.io", "gitlab.io", "pages.dev", "netlify.app")

# Obvious non-course aggregators we never want, even if they sneak past a filter.
BLOCKED_HOSTS = {
    "slideshare.net", "studocu.com", "scribd.com", "coursehero.com",
    "chegg.com", "quizlet.com", "youtube.com", "youtu.be", "facebook.com",
    "twitter.com", "x.com", "linkedin.com", "reddit.com", "medium.com",
    "wikipedia.org", "amazon.com", "researchgate.net", "academia.edu",
    "github.com",  # repos, not course pages (use a direct URL for those)
}

_ACADEMIC_TLD_RE = re.compile(r"(?:^|\.)edu$|\.(?:edu|ac)\.[a-z]{2,3}$", re.IGNORECASE)


def host_of(url: str) -> str:
    """Return the lowercased hostname without a leading ``www.`` or port."""
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _registrable_match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_university(url: str, allow_course_hosts: bool = True) -> tuple[bool, str]:
    """Whether *url* is a university / course-host page.

    Returns ``(accepted, reason)`` so the caller can explain its decision.
    """
    host = host_of(url)
    if not host:
        return False, "no-host"
    if _registrable_match(host, BLOCKED_HOSTS):
        return False, f"blocked:{host}"
    if _ACADEMIC_TLD_RE.search(host):
        return True, "academic-tld"
    if _registrable_match(host, KNOWN_UNIVERSITIES):
        return True, "known-university"
    if allow_course_hosts and _registrable_match(host, COURSE_HOSTS):
        return True, "course-host"
    return False, f"not-university:{host}"


# --------------------------------------------------------------------------- #
# URL reduction:  deep slide link  ->  directory that lists the whole course
# --------------------------------------------------------------------------- #

_SLIDE_FILE_RE = re.compile(r"\.(?:pdf|pptx?|ppt)$", re.IGNORECASE)
_INDEX_FILE_RE = re.compile(r"^(?:index|default|home)\.(?:html?|php|aspx?|jsp)$", re.IGNORECASE)

# A path segment that names ONE lecture (so its parent lists them all), e.g.
# ``Lecture1``, ``lec03``, ``week-2``, ``class10``, ``Topic4``.
_SINGLE_ITEM_RE = re.compile(
    r"^(?:lecture|lect|lec|week|wk|class|day|session|topic|chapter|chap|"
    r"module|unit|part)[-_ ]?\d+[a-z]?$",
    re.IGNORECASE,
)


def reduce_url(url: str) -> str:
    """Reduce a deep slide URL to the directory that lists the whole course.

    Examples
    --------
    ``.../lectures/Lecture1/Lecture1.pdf`` -> ``.../lectures/``
    ``.../slides/01StableMatching.pdf``    -> ``.../slides/``
    ``.../lectures/index.html``            -> ``.../lectures/``
    A normal course homepage is returned unchanged.
    """
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return url

    last = segs[-1]
    if _INDEX_FILE_RE.match(last):
        # ``.../lectures/index.html`` -> ``.../lectures/``
        segs = segs[:-1]
    elif _SLIDE_FILE_RE.search(last):
        # Drop the file itself.
        segs = segs[:-1]
        # If it lived in a per-lecture folder, climb to the collection root.
        if segs and _SINGLE_ITEM_RE.match(segs[-1]):
            segs = segs[:-1]
    else:
        # Not a file we recognise -> leave the page as-is (treat as a homepage).
        return url

    new_path = "/" + "/".join(segs)
    if segs:
        new_path += "/"
    return urlunsplit((parts.scheme, parts.netloc, new_path, "", ""))


# --------------------------------------------------------------------------- #
# Output-folder slug + "already downloaded?" check
# --------------------------------------------------------------------------- #

# Path segments too generic to identify a course on their own.
_GENERIC_SEGMENTS = {
    "index", "home", "default", "schedule", "lectures", "lecture",
    "slides", "slide", "materials", "calendar", "www", "course",
    "courses", "class", "classes", "pages", "page", "syllabus",
    "content", "main", "afs", "cs", "academic", "people",
}


def page_slug(url: str) -> str:
    """Derive a stable, collision-resistant output folder name from a URL.

    Combines the host with the course-identifier path segments so that, e.g.,
    several pages all ending in ``schedule.html`` / ``lectures`` / ``index.html``
    don't dump into one shared folder. Examples:
      web.stanford.edu/class/cs143/                 -> stanford.edu_cs143
      www.cs.cmu.edu/.../class/15213-f15/.../sched  -> cs.cmu.edu_15213-f15
      github.com/oxford-cs-deepnlp-2017/lectures    -> github.com_oxford-cs-deepnlp-2017
      cs.cornell.edu/courses/cs5150/2026sp/sched    -> cs.cornell.edu_cs5150_2026sp
    """
    parsed = urlsplit(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]

    segs: list[str] = []
    for raw in parsed.path.split("/"):
        s = raw.strip().lstrip("~")
        s = re.sub(r"\.(html?|php|aspx?|jsp)$", "", s, flags=re.IGNORECASE)
        if not s or s.lower() in _GENERIC_SEGMENTS:
            continue
        segs.append(s)

    # Prefer segments that look like a course code / term (contain a digit).
    course_like = [s for s in segs if re.search(r"\d", s)]
    chosen = course_like[:3] if course_like else segs[-1:]

    parts = [host] + chosen
    slug = sanitize("_".join(parts))
    return slug or sanitize(host) or "slides"


# Backwards-compatible alias (older imports / tests use the underscore name).
_page_slug = page_slug


def already_downloaded(url: str, out_dirs) -> bool:
    """True if a non-empty output folder already exists for this course URL.

    *out_dirs* may be a single path or an iterable of paths; the course counts
    as present if its slug folder exists and is non-empty under any of them
    (e.g. both the existing ``downloads/`` corpus and ``downloads/new/``). This
    lets callers skip courses already downloaded without ever overwriting them.
    """
    if isinstance(out_dirs, (str, Path)):
        out_dirs = [out_dirs]
    slug = page_slug(url)
    for root in out_dirs:
        folder = Path(root) / slug
        if folder.is_dir() and any(folder.iterdir()):
            return True
    return False
