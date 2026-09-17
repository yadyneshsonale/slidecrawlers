"""Unit tests for the pure parsing/identity helpers (no network)."""
from slideratings import ccr, extract, rmp, slides
from slideratings.abbrev import derive_abbrev
from slideratings.pipeline import make_course_id


def test_derive_abbrev_known():
    assert derive_abbrev("cornell-university") == "cornell"
    assert derive_abbrev("university-of-michigan") == "umich"
    assert derive_abbrev("massachusetts-institute-of-technology") == "mit"


def test_derive_abbrev_fallback():
    assert derive_abbrev("liberty-university") == "liberty"
    assert derive_abbrev("some-new-school", "XYZ") == "xyz"


def test_split_code():
    assert ccr.split_code("cs-1110") == ("CS 1110", "1110")
    assert ccr.split_code("aem2210") == ("AEM 2210", "2210")
    assert ccr.split_code("stat0200") == ("STAT 0200", "0200")


def test_make_course_id_matches_dataset_convention():
    assert make_course_id("cornell", "cs-1110") == "cornell-cs1110"
    assert make_course_id("umich", "EECS442") == "umich-eecs442"
    assert make_course_id("cornell", "CS1110") == make_course_id("cornell", "cs-1110")


def test_is_slide_link():
    ok, _ = extract.is_slide_link("Lecture 3 slides", "http://x.edu/lec03.pdf")
    assert ok
    bad, _ = extract.is_slide_link("Homework 3", "http://x.edu/hw03.pdf")
    assert not bad
    ppt, _ = extract.is_slide_link("Week 2", "http://x.edu/w2.pptx")
    assert ppt


def test_guess_number():
    assert extract.guess_number("Lecture 7", "x/lec07.pdf") == 7
    assert extract.guess_number("Week 12 slides", "x/w12.pdf") == 12


def test_mit_and_junk_hosts_blocked():
    assert slides.is_mit("https://ocw.mit.edu/courses/6-006/lec1.pdf")
    assert slides.is_blocked_host("https://web.mit.edu/6.006/slides.pdf")
    assert slides.is_blocked_host("https://www.coursehero.com/file/1/x.pdf")
    assert not slides.is_blocked_host("https://web.stanford.edu/class/cs221/l1.pdf")


def test_rmp_normalize_name():
    assert rmp.normalize_name("Prof. White") == "White"
    assert rmp.normalize_name("Dr. Jure Leskovec") == "Jure Leskovec"
    assert rmp.normalize_name("Teaching Staff") is None
    assert rmp.normalize_name("CS 221 Team") is None


def test_rmp_pick_match():
    nodes = [
        {"firstName": "Walker", "lastName": "White",
         "numRatings": 50, "department": "Computer Science"},
        {"firstName": "Charles", "lastName": "Whitehead",
         "numRatings": 5, "department": "Law"},
    ]
    node, n, kind = rmp.pick_match("White", nodes)
    assert node["firstName"] == "Walker"
    assert kind == "unique"
