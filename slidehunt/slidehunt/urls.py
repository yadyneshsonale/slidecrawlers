"""URL helpers: blocking, link reduction, dedup slugs, host->university.

Pure (network-free) functions. ``reduce_url`` / ``page_slug`` / ``already_downloaded``
replicate slidefetch's behaviour so slidehunt can detect courses already present
in the slidefetch corpus and skip them. ``host_to_university`` infers the school
(and a CCR slug guess) from a download host.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# --------------------------------------------------------------------------- #
# Host blocking
# --------------------------------------------------------------------------- #

MIT_HOSTS = ("mit.edu", "ocw.mit.edu", "mitocw.com", "csail.mit.edu")
JUNK_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "coursehero.com", "studocu.com",
    "scribd.com", "chegg.com", "quizlet.com", "slideshare.net", "academia.edu",
    "researchgate.net", "amazon.com", "docsity.com", "course-notes.org",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "reddit.com",
    "medium.com", "wikipedia.org",
)


def host_of(url: str) -> str:
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _registrable_match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_mit(url: str) -> bool:
    return _registrable_match(host_of(url), MIT_HOSTS)


def is_blocked_host(url: str) -> bool:
    return _registrable_match(host_of(url), MIT_HOSTS + JUNK_HOSTS)


# --------------------------------------------------------------------------- #
# URL reduction:  deep slide link  ->  directory that lists the whole course
# --------------------------------------------------------------------------- #

_SLIDE_FILE_RE = re.compile(r"\.(?:pdf|pptx?|ppt)$", re.IGNORECASE)
_INDEX_FILE_RE = re.compile(r"^(?:index|default|home)\.(?:html?|php|aspx?|jsp)$", re.IGNORECASE)
_SINGLE_ITEM_RE = re.compile(
    r"^(?:lecture|lect|lec|week|wk|class|day|session|topic|chapter|chap|"
    r"module|unit|part)[-_ ]?\d+[a-z]?$",
    re.IGNORECASE,
)


def reduce_url(url: str) -> str:
    """Reduce a deep slide URL to the directory that lists the whole course."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return url

    last = segs[-1]
    if _INDEX_FILE_RE.match(last):
        segs = segs[:-1]
    elif _SLIDE_FILE_RE.search(last):
        segs = segs[:-1]
        if segs and _SINGLE_ITEM_RE.match(segs[-1]):
            segs = segs[:-1]
    else:
        return url

    new_path = "/" + "/".join(segs)
    if segs:
        new_path += "/"
    return urlunsplit((parts.scheme, parts.netloc, new_path, "", ""))


# --------------------------------------------------------------------------- #
# Output-folder slug + "already downloaded?" check
# --------------------------------------------------------------------------- #

_GENERIC_SEGMENTS = {
    "index", "home", "default", "schedule", "lectures", "lecture",
    "slides", "slide", "materials", "calendar", "www", "course",
    "courses", "class", "classes", "pages", "page", "syllabus",
    "content", "main", "afs", "cs", "academic", "people",
}


def sanitize(name: str, maxlen: int = 120) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    name = re.sub(r"_+", "_", name).strip("._-")
    return name[:maxlen] or "file"


def page_slug(url: str) -> str:
    """Derive a stable output-folder name from a URL (matches slidefetch)."""
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

    course_like = [s for s in segs if re.search(r"\d", s)]
    chosen = course_like[:3] if course_like else segs[-1:]

    parts = [host] + chosen
    slug = sanitize("_".join(parts))
    return slug or sanitize(host) or "slides"


def already_downloaded(url: str, out_dirs) -> bool:
    """True if a non-empty output folder already exists for this course URL."""
    if isinstance(out_dirs, (str, Path)):
        out_dirs = [out_dirs]
    slug = page_slug(url)
    for root in out_dirs:
        folder = Path(root) / slug
        if folder.is_dir() and any(folder.iterdir()):
            return True
    return False


# --------------------------------------------------------------------------- #
# Host -> university inference (for RMP / CCR lookups)
# --------------------------------------------------------------------------- #

# Registrable domain -> full display name. RMP find_school + a slugified CCR
# guess both derive from the display name.
HOST_UNIVERSITIES = {
    "stanford.edu": "Stanford University",
    "cornell.edu": "Cornell University",
    "berkeley.edu": "University of California Berkeley",
    "washington.edu": "University of Washington",
    "umich.edu": "University of Michigan",
    "mit.edu": "Massachusetts Institute of Technology",
    "cmu.edu": "Carnegie Mellon University",
    "illinois.edu": "University of Illinois Urbana-Champaign",
    "gatech.edu": "Georgia Institute of Technology",
    "princeton.edu": "Princeton University",
    "harvard.edu": "Harvard University",
    "yale.edu": "Yale University",
    "ucla.edu": "University of California Los Angeles",
    "ucsd.edu": "University of California San Diego",
    "ucsb.edu": "University of California Santa Barbara",
    "utexas.edu": "University of Texas at Austin",
    "wisc.edu": "University of Wisconsin Madison",
    "caltech.edu": "California Institute of Technology",
    "nyu.edu": "New York University",
    "columbia.edu": "Columbia University",
    "duke.edu": "Duke University",
    "northwestern.edu": "Northwestern University",
    "umn.edu": "University of Minnesota",
    "psu.edu": "Penn State University",
    "osu.edu": "Ohio State University",
    "rutgers.edu": "Rutgers University",
    "ufl.edu": "University of Florida",
    "ucf.edu": "University of Central Florida",
    "fiu.edu": "Florida International University",
    "asu.edu": "Arizona State University",
    "tamu.edu": "Texas A&M University",
    "pitt.edu": "University of Pittsburgh",
    "virginia.edu": "University of Virginia",
    "wpi.edu": "Worcester Polytechnic Institute",
    "uci.edu": "University of California Irvine",
    "purdue.edu": "Purdue University",
    "utoronto.ca": "University of Toronto",
    "toronto.edu": "University of Toronto",
    "uwaterloo.ca": "University of Waterloo",
    "ubc.ca": "University of British Columbia",
    "ucalgary.ca": "University of Calgary",
    "mcgill.ca": "McGill University",
    "umontreal.ca": "University of Montreal",
    "ox.ac.uk": "University of Oxford",
    "cam.ac.uk": "University of Cambridge",
    "ethz.ch": "ETH Zurich",
}

_MULTI_TLDS = ("ac.uk", "ac.il", "ac.at", "edu.au", "edu.cn", "edu.sg",
               "edu.hk", "edu.in", "co.uk")


def _registrable_domain(host: str) -> str:
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last2 = ".".join(labels[-2:])
    if last2 in _MULTI_TLDS:
        return ".".join(labels[-3:])
    return last2


def slugify_university(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def host_to_university(url: str) -> dict:
    """Infer ``{name, slug, ccr_slug, host}`` for the school behind *url*."""
    host = host_of(url)
    name = None
    for dom, dom_name in HOST_UNIVERSITIES.items():
        if host == dom or host.endswith("." + dom):
            name = dom_name
            break
    reg = _registrable_domain(host)
    if name is None:
        label = reg.split(".")[0]
        name = label.replace("-", " ").title()
    return {
        "name": name,
        "slug": slugify_university(name),
        "ccr_slug": slugify_university(name),
        "host": reg,
    }
