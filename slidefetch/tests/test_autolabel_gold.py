"""Regression tests for the gold-honouring auto-labeller and the classify_link
reject-filter (pure, no network).

These lock in the fix for the "autolabel is working wrong" report: the labeller
must (a) reject administrative pages (catalog / bulletin / registrar / library /
bare homepage) instead of emitting them, (b) keep real course material, and
(c) only emit course-specific ``ambiguous`` links, never a stray university page.
"""
from ccr_label_auto import label_course
from ccr_slide_kb import classify_link


def _label(code, college, links):
    return label_course(code, college, links, f"{code} - {college}")


def test_admin_pages_are_dropped_and_course_abstains():
    # Both candidates are administrative -> no label at all.
    assert _label(
        "AS103", "Johns Hopkins University",
        ["https://courses.jhu.edu/",
         "https://e-catalogue.jhu.edu/course-descriptions/"],
    ) is None


def test_bulletin_and_generic_page_do_not_get_emitted():
    # bulletin = hard reject; /police = generic no-signal ambiguous -> dropped.
    assert _label(
        "ACHM121", "University at Albany",
        ["https://www.albany.edu/undergraduate-bulletin/chemistry-courses.php",
         "https://www.albany.edu/police"],
    ) is None


def test_real_slide_index_is_kept():
    row = _label(
        "CS2800", "Cornell University",
        ["https://www.cs.cornell.edu/courses/cs2800/2009fa/notes.htm"],
    )
    assert row is not None
    assert row[0] == "https://www.cs.cornell.edu/courses/cs2800/2009fa/notes.htm"


def test_github_slides_repo_is_kept():
    row = _label(
        "CS7637", "Georgia Institute of Technology",
        ["https://github.com/parthi2929/gt_cs7637/tree/master/Slides"],
    )
    assert row is not None and "gt_cs7637" in row[0]


def test_classify_link_rejects_registrar_and_catalog():
    assert classify_link(
        "https://www.albany.edu/registrar/schedule-classes", "ACHM121",
        "University at Albany").verdict == "reject"
    assert classify_link(
        "https://e-catalogue.jhu.edu/course-descriptions/", "AS103",
        "Johns Hopkins University").verdict == "reject"


def test_classify_link_accepts_owning_university_slide_path():
    v = classify_link(
        "https://www.cs.cornell.edu/courses/cs2800/2009fa/notes.htm", "CS2800",
        "Cornell University")
    assert v.verdict == "accept"
