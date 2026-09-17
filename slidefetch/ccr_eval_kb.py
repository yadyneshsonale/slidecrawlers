#!/usr/bin/env python3
"""Evaluate ``ccr_slide_kb.classify_link`` against the human gold files.

correct.txt = links that ARE slides (verdict should be accept, or at worst
ambiguous -- never reject). wrong.txt = links that are NOT usable slides (verdict
should be reject, or at worst ambiguous -- never accept). Reports precision-style
error counts and every mismatch so the KB rules can be tuned.

    python3 ccr_eval_kb.py            # summary + mismatches
    python3 ccr_eval_kb.py --quiet    # summary only
"""
from __future__ import annotations

import argparse

from ccr_slide_kb import classify_link

CORRECT = "correct.txt"
WRONG = "wrong.txt"


def _college(cc: str) -> str:
    return cc.split(" - ", 1)[1] if " - " in cc else ""


def load(path: str, skip_header: bool):
    rows = []
    with open(path, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    if skip_header and lines:
        lines = lines[1:]
    for ln in lines:
        p = ln.split("\t")
        cc, code = p[0].strip(), (p[1].strip() if len(p) > 1 else "")
        links = [x.strip() for x in p[3:] if x.strip()]
        if links:
            rows.append((cc, code, links))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    good = load(CORRECT, True)
    bad = load(WRONG, False)

    # correct.txt: the first link is the known-good one.
    good_reject = []  # false negatives (rejected a good link)
    good_ok = 0
    for cc, code, links in good:
        v = classify_link(links[0], code, _college(cc))
        if v.verdict == "reject":
            good_reject.append((cc, links[0], v.reason))
        else:
            good_ok += 1

    # wrong.txt: NONE of the links should be accepted.
    bad_accept = []  # false positives (accepted a wrong link)
    bad_ok = 0
    for cc, code, links in bad:
        accepted = [(l, classify_link(l, code, _college(cc))) for l in links]
        fp = [(l, v) for l, v in accepted if v.verdict == "accept"]
        if fp:
            bad_accept.append((cc, fp))
        else:
            bad_ok += 1

    print("=== KB eval vs gold ===")
    print(f"correct.txt: {good_ok}/{len(good)} not-rejected "
          f"({len(good_reject)} wrongly REJECTED)")
    print(f"wrong.txt:   {bad_ok}/{len(bad)} not-accepted "
          f"({len(bad_accept)} wrongly ACCEPTED)")

    if not args.quiet:
        if good_reject:
            print("\n-- correct.txt links wrongly REJECTED --")
            for cc, url, why in good_reject:
                print(f"  [{why}] {cc}\n      {url}")
        if bad_accept:
            print("\n-- wrong.txt links wrongly ACCEPTED --")
            for cc, fps in bad_accept:
                for url, v in fps:
                    print(f"  [{v.reason}] {cc}\n      {url}")


if __name__ == "__main__":
    main()
