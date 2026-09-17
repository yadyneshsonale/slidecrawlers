#!/usr/bin/env python3.11
"""Held-out validation of ccr_slide_kb.classify_link against the human annotations.

POSITIVE = every link in correct.txt (user says it is real slides).
NEGATIVE = every link in wrong.txt  (user says it is NOT real slides).

We want, on URL rules alone:
  * negatives -> mostly 'reject' (an 'accept' here is a FALSE POSITIVE = worst error)
  * positives -> mostly 'accept' (a 'reject' here is a FALSE NEGATIVE)
  * 'ambiguous' is fine on both -> resolved later by fetch/subagent.
"""
from __future__ import annotations

import sqlite3
from collections import Counter

from ccr_slide_kb import classify_link, confirmation_trust


def parse(path, skip_header):
    out = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            out.append(line.rstrip("\n").split("\t"))
    return out[1:] if skip_header else out


def main():
    # course_college -> (code, college) ; college derived from "CODE - College".
    def college_of(cc):
        return cc.split(" - ", 1)[1] if " - " in cc else ""

    pos = []  # (url, code, college)
    for r in parse("correct.txt", True):
        cc, code, link = r[0], r[1], r[3]
        if link.strip():
            pos.append((link.strip(), code, college_of(cc)))

    neg = []
    for r in parse("wrong.txt", False):
        cc, code = r[0], r[1]
        for link in r[3:5]:
            if link.strip():
                neg.append((link.strip(), code, college_of(cc)))

    def run(items):
        c = Counter()
        detail = []
        for url, code, col in items:
            v = classify_link(url, code, col)
            c[v.verdict] += 1
            detail.append((v, url, code, col))
        return c, detail

    pc, pdet = run(pos)
    nc, ndet = run(neg)

    print(f"POSITIVES (correct.txt, n={len(pos)}):  {dict(pc)}")
    print(f"NEGATIVES (wrong.txt,   n={len(neg)}):  {dict(nc)}")
    print()
    fn_rate = pc["reject"] / max(1, len(pos))
    fp_rate = nc["accept"] / max(1, len(neg))
    print(f"FALSE NEGATIVE rate (correct->reject): {pc['reject']}/{len(pos)} = {fn_rate:.1%}")
    print(f"FALSE POSITIVE rate (wrong->accept):   {nc['accept']}/{len(neg)} = {fp_rate:.1%}")
    print(f"positives accepted: {pc['accept']}/{len(pos)} = {pc['accept']/len(pos):.1%}"
          f"  | negatives rejected: {nc['reject']}/{len(neg)} = {nc['reject']/len(neg):.1%}")

    print("\n--- FALSE POSITIVES (wrong links classified ACCEPT) ---")
    for v, url, code, col in ndet:
        if v.verdict == "accept":
            print(f"  [{v.reason}] {url}")

    print("\n--- FALSE NEGATIVES (correct links classified REJECT) ---")
    for v, url, code, col in pdet:
        if v.verdict == "reject":
            print(f"  [{v.reason}] {url}")

    # Trust-gate coverage: of links NOT rejected, how many would be trusted (deterministic
    # confirm if decks are found) vs handed to the subagent. On negatives, "trusted" is a
    # POTENTIAL false positive (only realised if decks are actually found on fetch).
    pos_trust = sum(1 for v, u, c, col in pdet
                    if v.verdict != "reject" and confirmation_trust(u, c, col)[0])
    neg_trust = sum(1 for v, u, c, col in ndet
                    if v.verdict != "reject" and confirmation_trust(u, c, col)[0])
    print("\n--- TRUST GATE (among non-rejected links) ---")
    print(f"positives trusted: {pos_trust}/{len(pos)}  (rest -> subagent)")
    print(f"negatives trusted: {neg_trust}/{len(neg)}  (potential FP if decks are found)")


if __name__ == "__main__":
    main()
