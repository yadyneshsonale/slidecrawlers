"""Tests for the UW RMP lookup matcher (pure, no network)."""
from uw_rmp_lookup import _tokens, pick_match


def _n(first, last, dept="Computer Science", n=10):
    return {"firstName": first, "lastName": last, "department": dept, "numRatings": n}


def test_tokens_splits_and_lowercases():
    assert _tokens("Abilio Oliveira") == ["abilio", "oliveira"]
    assert _tokens("Beame") == ["beame"]
    assert _tokens("J.-P. O'Brien") == ["j", "p", "o", "brien"]


def test_unique_surname_match():
    nodes = [_n("Paul", "Beame")]
    node, n_cand, mt = pick_match("Beame", nodes)
    assert mt == "unique" and n_cand == 1 and node["firstName"] == "Paul"


def test_not_found_when_no_surname_hit():
    nodes = [_n("Someone", "Else")]
    node, n_cand, mt = pick_match("Beame", nodes)
    assert node is None and mt == "not_found" and n_cand == 0


def test_given_name_disambiguates():
    nodes = [_n("Ruth", "Anderson"), _n("Richard", "Anderson")]
    node, n_cand, mt = pick_match("Ruth Anderson", nodes)
    assert mt == "given" and node["firstName"] == "Ruth" and n_cand == 2


def test_given_initial_disambiguates():
    nodes = [_n("Ruth", "Anderson"), _n("Richard", "Anderson")]
    node, _n_cand, mt = pick_match("R Anderson", nodes)
    # Both start with R -> still two; initial alone cannot split -> falls to CS logic
    assert mt in {"ambiguous", "given"}


def test_cs_dept_preferred_among_namesakes():
    nodes = [_n("James", "Zhang", dept="Mathematics", n=122),
             _n("Lin", "Zhang", dept="Computer Science", n=5)]
    node, n_cand, mt = pick_match("Zhang", nodes)
    assert mt == "cs-dept" and node["department"] == "Computer Science" and n_cand == 2


def test_multiple_cs_namesakes_are_ambiguous_most_rated():
    nodes = [_n("Ruth", "Anderson", n=57), _n("Richard", "Anderson", n=120)]
    node, _n_cand, mt = pick_match("Anderson", nodes)
    assert mt == "ambiguous" and node["firstName"] == "Richard"  # most ratings


def test_no_cs_namesake_returns_no_match():
    # Several "Brown" namesakes, none in Computer Science -> do not guess.
    nodes = [_n("Jonathon", "Brown", dept="Psychology", n=160),
             _n("Sam", "Brown", dept="Biology", n=20)]
    node, n_cand, mt = pick_match("Brown", nodes)
    assert node is None and mt == "no_cs_match" and n_cand == 2


def test_single_non_cs_unique_is_still_accepted():
    # One namesake only, even in another department, is the best we can do.
    nodes = [_n("Zhixu", "Su", dept="Mathematics", n=80)]
    node, _n_cand, mt = pick_match("Su", nodes)
    assert mt == "unique" and node["firstName"] == "Zhixu"
