"""Tests for the search-driven discovery layer (URL filtering/reduction/search)."""
from argparse import Namespace

from slidefetch.cli import _expand_queries
from slidefetch.search import parse_results
from slidefetch.urls import (
    already_downloaded,
    is_university,
    page_slug,
    reduce_url,
)


# --------------------------------------------------------------------------- #
# reduce_url
# --------------------------------------------------------------------------- #

def test_reduce_url_climbs_out_of_per_lecture_folder():
    url = ("https://web.stanford.edu/class/archive/cs/cs106a/cs106a.1178/"
           "lectures/Lecture1/Lecture1.pdf")
    assert reduce_url(url) == (
        "https://web.stanford.edu/class/archive/cs/cs106a/cs106a.1178/lectures/"
    )


def test_reduce_url_strips_slide_file_to_its_dir():
    url = "https://x.edu/cs101/slides/01StableMatching.pdf"
    assert reduce_url(url) == "https://x.edu/cs101/slides/"


def test_reduce_url_strips_index_html():
    url = "https://x.edu/cs101/lectures/index.html"
    assert reduce_url(url) == "https://x.edu/cs101/lectures/"


def test_reduce_url_leaves_homepage_unchanged():
    url = "https://web.stanford.edu/class/cs143/"
    assert reduce_url(url) == url


def test_reduce_url_pptx_in_week_folder():
    url = "https://x.ac.uk/ee201/week-3/slides.pptx"
    assert reduce_url(url) == "https://x.ac.uk/ee201/"


# --------------------------------------------------------------------------- #
# is_university
# --------------------------------------------------------------------------- #

def test_is_university_accepts_edu():
    assert is_university("https://cs.stanford.edu/people/x/")[0]
    assert is_university("https://web.mit.edu/6.006/")[0]


def test_is_university_accepts_academic_cc_tlds():
    assert is_university("https://www.cse.iitb.ac.in/~x/")[0]      # .ac.in
    assert is_university("https://www.cl.cam.ac.uk/teaching/")[0]  # .ac.uk
    assert is_university("https://nus.edu.sg/course/")[0]          # .edu.sg


def test_is_university_accepts_known_non_edu_universities():
    assert is_university("https://ethz.ch/cs/")[0]
    assert is_university("https://student.cs.uwaterloo.ca/cs240/")[0]


def test_is_university_course_host_toggle():
    ok_default, why = is_university("https://cs144.github.io/")
    assert ok_default and why == "course-host"
    ok_strict, _ = is_university("https://cs144.github.io/", allow_course_hosts=False)
    assert not ok_strict


def test_is_university_rejects_aggregators_and_repos():
    assert not is_university("https://www.slideshare.net/x/deck")[0]
    assert not is_university("https://www.studocu.com/x")[0]
    assert not is_university("https://github.com/owner/repo")[0]
    assert not is_university("https://example.com/cs/slides.pdf")[0]


# --------------------------------------------------------------------------- #
# already_downloaded  +  page_slug
# --------------------------------------------------------------------------- #

def test_already_downloaded_detects_existing_nonempty_folder(tmp_path):
    url = "https://web.stanford.edu/class/cs143/"
    folder = tmp_path / page_slug(url)
    folder.mkdir(parents=True)
    (folder / "lecture_01.pdf").write_bytes(b"%PDF-1.4 x")
    assert already_downloaded(url, tmp_path)


def test_already_downloaded_false_for_missing_or_empty(tmp_path):
    url = "https://web.stanford.edu/class/cs143/"
    assert not already_downloaded(url, tmp_path)          # missing
    (tmp_path / page_slug(url)).mkdir(parents=True)
    assert not already_downloaded(url, tmp_path)          # present but empty


def test_already_downloaded_scans_multiple_roots(tmp_path):
    url = "https://web.stanford.edu/class/cs143/"
    corpus, newdir = tmp_path, tmp_path / "new"
    # Present in the existing corpus but not in new/ -> still counts as done.
    folder = corpus / page_slug(url)
    folder.mkdir(parents=True)
    (folder / "lecture_01.pdf").write_bytes(b"%PDF x")
    assert already_downloaded(url, [corpus, newdir])
    assert already_downloaded(url, [newdir, corpus])  # order independent


def test_already_downloaded_false_when_no_root_has_it(tmp_path):
    url = "https://web.stanford.edu/class/cs143/"
    assert not already_downloaded(url, [tmp_path, tmp_path / "new"])


# --------------------------------------------------------------------------- #
# search result parsing (no network)
# --------------------------------------------------------------------------- #

DDG_HTML = """
<div class="results">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fweb.stanford.edu%2Fclass%2Fcs106a%2Flectures%2F&amp;rut=abc">
     CS106A Lecture Slides - Stanford</a>
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.slideshare.net%2Fslideshow%2Fcs106a&amp;rut=def">
     Lecture1 CS106A - SlideShare</a>
  <a class="result__a" href="//duckduckgo.com/y.js?ad=1">sponsored</a>
</div>
"""


def test_parse_results_decodes_redirect_and_skips_ads():
    hits = parse_results(DDG_HTML)
    urls = [h.url for h in hits]
    assert "https://web.stanford.edu/class/cs106a/lectures/" in urls
    assert "https://www.slideshare.net/slideshow/cs106a" in urls
    assert all("duckduckgo.com" not in u for u in urls)  # ad link dropped


# --------------------------------------------------------------------------- #
# query expansion
# --------------------------------------------------------------------------- #

def test_expand_queries_from_search_and_disciplines():
    args = Namespace(
        search=["cs course slides"],
        disciplines="cs,ee",
        query_template="{name} course lecture slides",
    )
    qs = _expand_queries(args)
    assert qs == [
        "cs course slides",
        "computer science course lecture slides",
        "electrical engineering course lecture slides",
    ]


def test_expand_queries_all_keyword():
    args = Namespace(search=None, disciplines="all",
                     query_template="{name} lecture slides")
    qs = _expand_queries(args)
    assert "computer science lecture slides" in qs
    assert len(qs) >= 5
