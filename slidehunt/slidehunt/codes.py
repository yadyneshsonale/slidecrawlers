"""Random course-code generation.

Produces codes like ``cs223`` / ``eecs281`` / ``math214`` by combining a
weighted subject prefix with a plausible course number. CS / EECS subjects are
over-represented (per the task), with a broad STEM tail for variety.
"""
from __future__ import annotations

import random
from collections.abc import Iterator

# (prefix, weight) — CS-family dominates, broad STEM fills the tail.
_PREFIXES: list[tuple[str, int]] = [
    ("cs", 22), ("cse", 12), ("eecs", 10), ("ece", 7), ("ee", 5),
    ("csci", 5), ("comp", 4), ("cmsc", 3), ("cmpt", 2), ("cosc", 2),
    ("math", 6), ("stat", 3), ("phys", 3), ("chem", 2), ("bio", 2),
    ("econ", 2), ("me", 2), ("mae", 1), ("aero", 1), ("che", 1),
    ("isye", 1), ("info", 1), ("ds", 1), ("data", 1),
]

_PREFIX_POP = [p for p, _ in _PREFIXES]
_PREFIX_WEIGHTS = [w for _, w in _PREFIXES]


def _number(rng: random.Random) -> str:
    # Mostly 3-digit undergrad/grad numbers; occasionally 4-digit.
    if rng.random() < 0.05:
        return str(rng.randint(1000, 4999))
    return str(rng.randint(100, 599))


def generate_code(rng: random.Random) -> str:
    prefix = rng.choices(_PREFIX_POP, weights=_PREFIX_WEIGHTS, k=1)[0]
    return f"{prefix}{_number(rng)}"


def generate_codes(n: int, seed: int | None = None) -> Iterator[str]:
    """Yield *n* unique randomly-generated course codes."""
    rng = random.Random(seed)
    seen: set[str] = set()
    attempts = 0
    while len(seen) < n and attempts < n * 50:
        attempts += 1
        code = generate_code(rng)
        if code in seen:
            continue
        seen.add(code)
        yield code


def code_stream(seed: int | None = None,
                exclude: set[str] | None = None) -> Iterator[str]:
    """Yield unique randomly-generated codes (effectively) indefinitely.

    Used by the "run until N downloaded" mode where the number of codes needed
    is not known in advance. Stops once the code space is largely exhausted.
    """
    rng = random.Random(seed)
    seen: set[str] = set(exclude or ())
    space = len(_PREFIX_POP) * 500
    attempts = 0
    while attempts < space * 20:
        attempts += 1
        code = generate_code(rng)
        if code in seen:
            continue
        seen.add(code)
        yield code
