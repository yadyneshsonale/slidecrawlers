#!/usr/bin/env python3
"""Match every course directory in slidehunt/data to its CollegeClassReviews
course page, using the CCR sitemap as the authoritative index. Saves only the
CCR link per matched course.

Inputs:
  - data/<host>_<code>[_<extra>]/        course dirs
  - work/ccr_all_courses.txt             every CCR course URL (from sitemap)

Output:
  - work/ccr_links.csv                   dir,ccr_url   (matches only)
  - prints a coverage summary + unresolved domains
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CCR_URLS = os.path.join(ROOT, "work", "ccr_all_courses.txt")
OUT_CSV = os.path.join(ROOT, "work", "ccr_links.csv")

MULTI_TLDS = {"ac.uk", "ac.il", "ac.at", "edu.au", "edu.cn", "edu.sg",
              "edu.hk", "edu.in", "edu.sa", "ac.in", "ac.jp", "ac.kr",
              "co.uk", "ac.nz", "edu.tr", "edu.pk", "net.nz"}

# registrable domain -> CCR university slug (validated against sitemap at runtime)
DOMAIN_TO_CCR = {
    "usc.edu": "university-of-southern-california",
    "stanford.edu": "stanford-university",
    "illinois.edu": "university-of-illinois-urbana-champaign",
    "uiuc.edu": "university-of-illinois-urbana-champaign",
    "washington.edu": "university-of-washington",
    "uw.edu": "university-of-washington",
    "uwaterloo.ca": "university-of-waterloo",
    "purdue.edu": "purdue-university",
    "berkeley.edu": "uc-berkeley",
    "ucsd.edu": "uc-san-diego",
    "stonybrook.edu": "stony-brook-university",
    "uic.edu": "university-of-illinois-chicago",
    "umd.edu": "university-of-maryland",
    "princeton.edu": "princeton-university",
    "utexas.edu": "university-of-texas-at-austin",
    "buffalo.edu": "university-at-buffalo",
    "tamu.edu": "texas-a-and-m-university",
    "mcgill.ca": "mcgill-university",
    "ucsb.edu": "uc-santa-barbara",
    "bu.edu": "boston-university",
    "cornell.edu": "cornell-university",
    "toronto.edu": "university-of-toronto",
    "utoronto.ca": "university-of-toronto",
    "sc.edu": "university-of-south-carolina",
    "cmu.edu": "carnegie-mellon-university",
    "duke.edu": "duke-university",
    "brown.edu": "brown-university",
    "sjsu.edu": "san-jose-state-university",
    "wpi.edu": "worcester-polytechnic-institute",
    "cooper.edu": "cooper-union",
    "iastate.edu": "iowa-state-university",
    "northwestern.edu": "northwestern-university",
    "ualberta.ca": "university-of-alberta",
    "uci.edu": "uc-irvine",
    "umich.edu": "university-of-michigan",
    "byu.edu": "brigham-young-university",
    "ucdavis.edu": "uc-davis",
    "wisc.edu": "university-of-wisconsin-madison",
    "umass.edu": "umass-amherst",
    "harvard.edu": "harvard-university",
    "ucla.edu": "ucla",
    "utk.edu": "university-of-tennessee",
    "oregonstate.edu": "oregon-state-university",
    "upenn.edu": "university-of-pennsylvania",
    "arizona.edu": "university-of-arizona",
    "wvu.edu": "west-virginia-university",
    "ubc.ca": "university-of-british-columbia",
    "jhu.edu": "johns-hopkins-university",
    "pomona.edu": "pomona-college",
    "rice.edu": "rice-university",
    "tufts.edu": "tufts-university",
    "unc.edu": "university-of-north-carolina-at-chapel-hill",
    "wellesley.edu": "wellesley-college",
    "williams.edu": "williams-college",
    "msu.edu": "michigan-state-university",
    "uvic.ca": "university-of-victoria",
    "colostate.edu": "colorado-state-university",
    "uc.edu": "university-of-cincinnati",
    "ku.edu": "university-of-kansas",
    "pitt.edu": "university-of-pittsburgh",
    "rose-hulman.edu": "rose-hulman-institute-of-technology",
    "sfu.ca": "simon-fraser-university",
    "vt.edu": "virginia-tech",
    "brynmawr.edu": "bryn-mawr-college",
    "columbia.edu": "columbia-university",
    "rhodes.edu": "rhodes-college",
    "rochester.edu": "university-of-rochester",
    "rutgers.edu": "rutgers-university",
    "virginia.edu": "university-of-virginia",
    "vanderbilt.edu": "vanderbilt-university",
    "lsu.edu": "louisiana-state-university",
    "wsu.edu": "washington-state-university",
    "auburn.edu": "auburn-university",
    "uchicago.edu": "university-of-chicago",
    "emory.edu": "emory-university",
    "nyu.edu": "nyu",
    "gatech.edu": "georgia-tech",
    "udel.edu": "university-of-delaware",
    "sdsu.edu": "san-diego-state-university",
    "unm.edu": "university-of-new-mexico",
    "uky.edu": "university-of-kentucky",
    "csus.edu": "cal-state-sacramento",
    "uh.edu": "university-of-houston",
    "gwu.edu": "george-washington-university",
    "concordia.ca": "concordia-university",
    "psu.edu": "penn-state-university",
}

# prefixes treated as interchangeable when the course number matches
ALIAS_GROUPS = [
    {"cs", "csci", "csc", "comp", "cmpsc", "cmpt", "cse", "cosc", "cmsc", "cis"},
    {"ee", "ece", "eecs", "el", "elec", "ene"},
    {"math", "maths", "mat", "ma", "mth"},
    {"phys", "physics", "phy"},
    {"stat", "stats", "sta"},
    {"chem", "chm", "che"},
    {"econ", "econ"},
    {"bio", "biol"},
]


def registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in MULTI_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_CODE_RE = re.compile(r"([a-z]{1,6})[-_ ]?(\d{2,4}[a-z]?)")


def split_code(code: str):
    """(prefix, number) from a raw code chunk, or None."""
    m = _CODE_RE.search(code.lower())
    if not m:
        return None
    return m.group(1), m.group(2)


def prefixes_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    for g in ALIAS_GROUPS:
        if a in g and b in g:
            return True
    return False


def load_ccr():
    uni_courses: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    unis: set[str] = set()
    with open(CCR_URLS, encoding="utf-8") as fh:
        for line in fh:
            url = line.strip()
            m = re.search(r"/universities/([^/]+)/courses/([^/]+)", url)
            if not m:
                continue
            uni, slug = m.group(1), m.group(2)
            unis.add(uni)
            sc = split_code(slug)
            if sc:
                uni_courses[uni].append((sc[0], sc[1], url))
    return uni_courses, unis


def candidate_codes(dirname: str):
    """Yield raw code chunks to try, most-specific first."""
    parts = dirname.split("_")
    host = parts[0]
    rest = parts[1:]
    seen = []
    for chunk in rest:
        seen.append(chunk)
    # subdomain may itself carry the code (e.g. cme295.stanford.edu)
    sub = host.split(".")[0]
    seen.append(sub)
    out = []
    for c in seen:
        if c and c not in out:
            out.append(c)
    return out


def match_course(uni_courses, uni, prefix, number):
    courses = uni_courses.get(uni)
    if not courses:
        return None
    exact = [u for (p, n, u) in courses if p == prefix and n == number]
    if exact:
        return min(exact, key=len)
    compat = [u for (p, n, u) in courses if n == number and prefixes_compatible(p, prefix)]
    if compat:
        return min(compat, key=len)
    return None


def main():
    uni_courses, ccr_unis = load_ccr()

    # validate the domain map against the real CCR universities
    invalid = {d: s for d, s in DOMAIN_TO_CCR.items() if s not in ccr_unis}
    domain_map = {d: s for d, s in DOMAIN_TO_CCR.items() if s in ccr_unis}

    dirs = sorted(d for d in os.listdir(DATA)
                  if os.path.isdir(os.path.join(DATA, d)))

    rows = []
    no_uni = []          # domain not mapped / not on CCR
    uni_no_course = []    # uni mapped but course not found
    unresolved_domains = defaultdict(int)

    for d in dirs:
        host = d.split("_", 1)[0]
        reg = registrable(host)
        uni = domain_map.get(reg)
        if not uni:
            no_uni.append(d)
            unresolved_domains[reg] += 1
            continue
        url = None
        for chunk in candidate_codes(d):
            sc = split_code(chunk)
            if not sc:
                continue
            url = match_course(uni_courses, uni, sc[0], sc[1])
            if url:
                break
        if url:
            rows.append((d, url))
        else:
            uni_no_course.append(d)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["dir", "ccr_url"])
        w.writerows(rows)

    print(f"data dirs            : {len(dirs)}")
    print(f"matched (saved link) : {len(rows)}")
    print(f"uni mapped, no course: {len(uni_no_course)}")
    print(f"no uni/CCR coverage  : {len(no_uni)}")
    print(f"-> wrote {OUT_CSV}")
    if invalid:
        print("\nINVALID domain->slug guesses (not on CCR, fix these):")
        for d, s in sorted(invalid.items()):
            print(f"   {d:24} {s}")
    top_unres = sorted(unresolved_domains.items(), key=lambda x: -x[1])[:30]
    print("\nTop unresolved domains (no CCR mapping):")
    for dom, n in top_unres:
        print(f"   {n:3}  {dom}")

    if "--show-missing" in sys.argv:
        print("\nUni mapped but course not found:")
        for d in uni_no_course:
            print("   ", d)


if __name__ == "__main__":
    main()
