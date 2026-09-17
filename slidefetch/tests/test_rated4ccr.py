"""Tests for the rated4ccr completeness assessment + link fixes (work/ modules)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "work"))

import _completeness as comp  # noqa: E402
import _linkfix as lf  # noqa: E402


def _decks(names):
    return [comp.Deck(name=n) for n in names]


# --------------------------------------------------------------------------- #
# sequence_of
# --------------------------------------------------------------------------- #
def test_sequence_of_full_word_labels():
    assert comp.sequence_of("Lecture01.pdf") == ("lec", 1)
    assert comp.sequence_of("lec-3.pdf") == ("lec", 3)
    assert comp.sequence_of("slides_07.pptx") == ("slide", 7)
    assert comp.sequence_of("week2.pdf") == ("week", 2)
    assert comp.sequence_of("Chapter 5 intro.pdf") == ("chapter", 5)
    assert comp.sequence_of("module4.pdf") == ("module", 4)
    assert comp.sequence_of("topic-10.pdf") == ("topic", 10)


def test_sequence_of_single_letter_and_leading_number():
    assert comp.sequence_of("L05.pdf") == ("lec", 5)
    assert comp.sequence_of("w3.pdf") == ("week", 3)
    assert comp.sequence_of("01_intro.pdf") == ("num", 1)
    assert comp.sequence_of("3-hashing.pdf") == ("num", 3)


def test_sequence_of_ignores_download_order_prefix():
    # "<seq>_<original>" should still read the real label, not the order prefix.
    assert comp.sequence_of("2_Lecture08.pdf") == ("lec", 8)


def test_sequence_of_none_when_unnumbered():
    assert comp.sequence_of("introduction.pdf") is None
    assert comp.sequence_of("syllabus.pdf") is None


def test_sequence_of_parent_directory_number():
    # lecture number lives in the directory, not the filename
    assert comp.sequence_of(name="Intro.pptx",
                            url="https://x.io/lectures/010/Intro.pptx") == ("num", 10)
    assert comp.sequence_of(name="deck.pdf",
                            url="https://x.edu/cs1/week3/deck.pdf") == ("week", 3)


def test_sequence_of_ignores_year_directory():
    assert comp.sequence_of(name="notes.pdf",
                            url="https://x.edu/cs1/2023/notes.pdf") is None


# --------------------------------------------------------------------------- #
# assess
# --------------------------------------------------------------------------- #
def test_assess_complete_lecture_run():
    r = comp.assess(_decks([f"Lecture{i:02d}.pdf" for i in range(1, 6)]))
    assert r.complete is True
    assert r.kind == "lec" and r.expected_n == 5


def test_assess_complete_slide_and_week_series():
    assert comp.assess(_decks(["slide1.pdf", "slide2.pdf", "slide3.pdf",
                               "slide4.pdf"])).complete is True
    assert comp.assess(_decks(["week1.pdf", "week2.pdf", "week3.pdf"])).complete is True


def test_assess_gap_is_incomplete():
    r = comp.assess(_decks(["lec1.pdf", "lec2.pdf", "lec4.pdf"]))
    assert r.complete is False
    assert "missing" in r.reason and r.expected_n == 4


def test_assess_too_short_is_incomplete():
    r = comp.assess(_decks(["lec1.pdf", "lec2.pdf"]), min_decks=3)
    assert r.complete is False
    assert "too short" in r.reason


def test_assess_allows_lecture_zero_intro():
    r = comp.assess(_decks(["lec0_intro.pdf", "lec1.pdf", "lec2.pdf", "lec3.pdf"]))
    assert r.complete is True and r.expected_n == 3


def test_assess_unnumbered_is_ambiguous():
    r = comp.assess(_decks(["intro.pdf", "hashing.pdf", "trees.pdf"]))
    assert r.complete is None
    assert "subagent" in r.reason


def test_assess_competing_series_is_ambiguous():
    r = comp.assess(_decks(["lec1.pdf", "lec2.pdf", "week1.pdf", "week2.pdf"]))
    assert r.complete is None


def test_assess_sparse_numbering_is_ambiguous():
    names = ["lec1.pdf", "lec2.pdf"] + [f"reading_{w}.pdf" for w in
                                        ("a", "b", "c", "d", "e", "f", "g", "h")]
    r = comp.assess(_decks(names))
    assert r.complete is None


def test_assess_duplicate_number_variants_collapse():
    # a full deck + its 2x2 handout share lecture number 1; no false gap.
    r = comp.assess(_decks(["lec1.pdf", "lec1-2x2.pdf", "lec2.pdf", "lec3.pdf"]))
    assert r.complete is True and r.expected_n == 3


def test_assess_evenly_spaced_directory_sequence():
    urls = [f"https://csci.github.io/lectures/{n:03d}/topic.pptx"
            for n in (10, 20, 30, 40)]
    r = comp.assess([comp.Deck(name="topic.pptx", url=u) for u in urls])
    assert r.complete is True and r.expected_n == 4


def test_assess_pdf_pptx_pairs_not_diluted():
    decks = []
    for n in (10, 20, 30):
        decks.append(comp.Deck(name="t.pptx", url=f"https://x/lectures/{n:03d}/t.pptx"))
        decks.append(comp.Deck(name="t.pdf", url=f"https://x/lectures/{n:03d}/t.pdf"))
    r = comp.assess(decks)
    assert r.complete is True and r.expected_n == 3


# --------------------------------------------------------------------------- #
# link fixes
# --------------------------------------------------------------------------- #
def test_raw_github_rewrites_blob():
    assert lf.raw_github("https://github.com/u/r/blob/main/slides/L1.pdf") == (
        "https://raw.githubusercontent.com/u/r/main/slides/L1.pdf")


def test_raw_github_leaves_non_blob():
    assert lf.raw_github("https://x.edu/l/1.pdf") == "https://x.edu/l/1.pdf"


def test_github_decks_single_blob_file_no_network():
    out = lf.github_decks("https://github.com/u/r/blob/main/lectures/Lecture03.pdf")
    assert len(out) == 1
    assert out[0].url == (
        "https://raw.githubusercontent.com/u/r/main/lectures/Lecture03.pdf")
    assert out[0].name == "Lecture03.pdf" and out[0].file_type == "pdf"


def test_gslides_pdf_export():
    fx = lf.gslides_pdf("https://docs.google.com/presentation/d/ABC_123/edit#slide=id.p")
    assert fx is not None
    assert fx.url == "https://docs.google.com/presentation/d/ABC_123/export/pdf"
    assert fx.file_type == "pdf"


def test_gslides_pdf_none_for_other_urls():
    assert lf.gslides_pdf("https://x.edu/cs101/") is None


def test_ingest_tsv_parse():
    tsv = ("course_college\tcomplete\tN\treason\n"
           "CS61A - UC Berkeley\tyes\t12\tclean lec run\n"
           "CS100 - Foo\tno\t\tgaps everywhere\n")
    out = comp.parse_ingest_tsv(tsv)
    assert out["CS61A - UC Berkeley"] == {"complete": True, "n": 12,
                                          "reason": "clean lec run"}
    assert out["CS100 - Foo"]["complete"] is False
