"""Tests for the UW catalog crawler's pure parsing layer.

These cover the two HTML-parsing entry points (catalog course list, course
landing-page offerings) plus the small text helpers, using HTML snippets that
mirror the real ``courses.cs.washington.edu`` / ``cs.washington.edu`` markup.
No network access.
"""
from uw_catalog_crawler import (
    Course,
    _extract_prerequisites,
    _split_instructors,
    parse_catalog,
    parse_offerings,
)

# A faithful slice of the catalog widget: two category sections, bold course
# anchors with <br>-wrapped "CODE Title" text + <span> descriptions, the
# back-to-top divs, and a CSE197-style nested "Section Offerings" list whose
# inner (non-bold) links must NOT be parsed as separate courses.
CATALOG_HTML = """
<div class="elementor-widget-container">
<p></p><h3 id="Introductory/Pre-Major" class="mt-5">Introductory/Pre-Major</h3>
<p>Courses available for students to learn the fundamentals of Computer Science.</p>
<div class="mt-4"><a href="#top">↑ Back to Top</a></div>
<div>
  <a href="https://courses.cs.washington.edu/courses/cse120" style="display:inline-block; font-weight: bold"><br>
    CSE120 Computer Science Principles<br>
  </a>&nbsp;<span>Introduces fundamental concepts of computer science.</span>
</div>
<div>
  <a href="https://courses.cs.washington.edu/courses/cse143x" style="display:inline-block; font-weight: bold"><br>
    CSE143X Accelerated Computer Programming I/II<br>
  </a>&nbsp;<span>Accelerated introductory offering. Prerequisite: CSE 142.</span>
</div>
<div class="mt-4"><a href="#top">↑ Back to Top</a></div>
<h3 id="Undergraduate Major" class="mt-5">Undergraduate Major</h3>
<p>Courses for students accepted to the major.</p>
<div>
  <a href="https://courses.cs.washington.edu/courses/cse197" style="display:inline-block; font-weight: bold"><br>
    CSE197 Problem Solving for Computer Science and Engineering<br>
  </a>&nbsp;<span>Collaborative problem-solving sessions.
    <p><strong>Section Offerings:</strong></p>
    <ul>
      <li><a href="https://courses.cs.washington.edu/courses/cse197w">CSE197W ...</a></li>
      <li><a href="https://courses.cs.washington.edu/courses/cse197x">CSE197X ...</a></li>
    </ul>
  </span>
</div>
<div>
  <a href="https://courses.cs.washington.edu/courses/cse197w" style="display:inline-block; font-weight: bold"><br>
    CSE197W Problem Solving for Computer Science and Engineering (ASSP 121)<br>
  </a>&nbsp;<span>Must be taken with CSE 121. Credit/no-credit only</span>
</div>
<div>
  <a href="https://courses.cs.washington.edu/courses/cse446" style="display:inline-block; font-weight: bold"><br>
    CSE446 Machine Learning<br>
  </a>&nbsp;<span>Design of efficient algorithms that learn from data.
    Prerequisite: CSE 332; MATH 208 or MATH 136; and either STAT 390, STAT 391, or CSE 312.</span>
</div>
</div>
"""


def test_parse_catalog_extracts_codes_titles_urls():
    courses = parse_catalog(CATALOG_HTML)
    codes = [c.code for c in courses]
    assert codes == ["CSE120", "CSE143X", "CSE197", "CSE197W", "CSE446"]
    by_code = {c.code: c for c in courses}
    assert by_code["CSE446"].title == "Machine Learning"
    assert by_code["CSE446"].url == (
        "https://courses.cs.washington.edu/courses/cse446"
    )


def test_parse_catalog_tracks_category_sections():
    by_code = {c.code: c for c in parse_catalog(CATALOG_HTML)}
    assert by_code["CSE120"].category == "Introductory/Pre-Major"
    assert by_code["CSE446"].category == "Undergraduate Major"


def test_parse_catalog_skips_in_description_section_offering_links():
    # CSE197X only appears inside CSE197's <span> (non-bold) -> not its own row.
    codes = [c.code for c in parse_catalog(CATALOG_HTML)]
    assert "CSE197X" not in codes
    assert "CSE197W" in codes  # but CSE197W has its own bold entry


def test_parse_catalog_extracts_prerequisites():
    by_code = {c.code: c for c in parse_catalog(CATALOG_HTML)}
    assert by_code["CSE446"].prerequisites.startswith("Prerequisite: CSE 332")
    assert by_code["CSE143X"].prerequisites == "Prerequisite: CSE 142."
    assert by_code["CSE120"].prerequisites == ""


# A slice of a course landing page: quarter links with instructor parentheses,
# plus non-offering links (admin, a prerequisite course) that must be ignored.
OFFERINGS_HTML = """
<html><body>
  <a href="/courses/cse446/26sp/">Spring,&nbsp;2026 (Jaques)</a>
  <a href="/courses/cse446/25au/">Autumn, 2025 (Koh, Oh)</a>
  <a href="/courses/cse446/admin">Administrative info</a>
  <a href="https://courses.cs.washington.edu/courses/cse332">CSE 332</a>
  <a href="/courses/cse446/26sp/">Spring, 2026 (Jaques)</a>
</body></html>
"""


def test_parse_offerings_extracts_terms_and_instructors():
    course = Course(code="CSE446", title="Machine Learning",
                    url="https://courses.cs.washington.edu/courses/cse446")
    offerings = parse_offerings(OFFERINGS_HTML, course)
    assert [o.term for o in offerings] == ["26sp", "25au"]  # deduped, admin gone
    assert offerings[0].url == (
        "https://courses.cs.washington.edu/courses/cse446/26sp/"
    )
    assert offerings[0].instructors == ["Jaques"]
    assert offerings[0].term_label == "Spring, 2026"  # nbsp normalised
    assert "\xa0" not in offerings[0].term_label
    assert offerings[1].instructors == ["Koh", "Oh"]


def test_parse_offerings_ignores_other_courses_and_admin_pages():
    course = Course(code="CSE446", title="ML",
                    url="https://courses.cs.washington.edu/courses/cse446")
    urls = [o.url for o in parse_offerings(OFFERINGS_HTML, course)]
    assert all("/cse446/" in u for u in urls)
    assert not any("admin" in u for u in urls)
    assert not any("cse332" in u for u in urls)


def test_split_instructors_handles_separators_and_noise():
    assert _split_instructors("Jaques") == ["Jaques"]
    assert _split_instructors("Koh, Oh") == ["Koh", "Oh"]
    assert _split_instructors("Du & Jamieson") == ["Du", "Jamieson"]
    assert _split_instructors("Staff") == []
    assert _split_instructors("") == []


def test_extract_prerequisites_finds_recommended_clause():
    desc = "An intro course. Recommended: CSE 121 or self-placement."
    assert _extract_prerequisites(desc) == (
        "Recommended: CSE 121 or self-placement."
    )
