# Slide-Metric Findings — rated70

How the automatically-computed **GPT slide metrics** relate to the other metric
families (CCR course metrics, RMP quality/difficulty, LLM-extracted review
aspects). All numbers computed from `rated70_aspects.db` (+ `rated70.db` for
course code / enrollment), Pearson unless noted.

**Sample:** 73 courses total; **n = 58** for anything involving a slide metric
(15 courses have no slide deck). Course–instructor–term linkage between deck and
reviews is established. Statistics are **raw p** (two-sided); none survive
Benjamini–Hochberg / Holm multiple-comparison correction, so every result below
is **directional evidence**, strongest where n = 73 and |r| is large.

---

## 1. The slide metrics and how they are computed

| metric | formula (src/metrics.py) | measures | mean ± sd (n=58) |
|---|---|---|---|
| `visual_documentation` | `1 − U/Total` | fraction of visual elements captioned/referenced | 0.68 ± 0.12 |
| `prerequisite` | `1 − violations/|Used|` | share of used keywords defined-in-deck / prereq / seen earlier | 0.50 ± 0.11 |
| `progression` | LLM 0–100 → [0,1] | logical concept sequencing | 0.79 ± 0.05 |
| `long_term_recall` | reuse score: redefined 1.0 / referenced 0.5 / used 0.0 | cross-lecture reinforcement | 0.37 ± 0.16 |
| `visual_appeal` | weighted per-slide clutter/hierarchy/focus/consistency | slide design quality | 0.75 ± 0.03 |
| `Total` (byproduct) | count of visual elements | deck size | 661 ± 643 |
| `U/Total` | = 1 − visual_documentation | fraction of **uncaptioned** visuals | 0.34 ± 0.12 |
| `violations/used` | = 1 − prerequisite | prerequisite **violation** rate | 0.51 ± 0.11 |

Dropped from analysis: `symbol`, `abbreviation` (ceiling-inflated penalty scores,
most decks ≈ 1.0), and `final` (near-constant composite).

**Variance note:** `visual_appeal` (sd 0.03) and `progression` (7 distinct
values) are near-constant → little to correlate. `U/Total` and `violations/used`
are exact complements of `visual_documentation` and `prerequisite`.

---

## 2. Raw correlations (|r| ≥ 0.25, p < 0.05)

| slide metric | significant associations |
|---|---|
| **visual_documentation** | star **−0.40**, satisfaction **−0.40**, grade_accessibility −0.26, attendance_importance −0.27 |
| **prerequisite** | challenge **+0.33**, time **+0.36**, num_reviews +0.33, course_level −0.35; star/satisfaction −0.30 |
| **progression** | challenge **+0.39**; star/satisfaction −0.26, grade_accessibility −0.27, num_reviews −0.27 |
| **long_term_recall** | time **+0.33**, challenge +0.27; `asp_learning_materials_quality` −0.28 |
| **visual_appeal** | *nothing* — no \|r\| ≥ 0.25 with anything |
| **Total (deck size)** | time **+0.38**, challenge +0.28, rmp_difficulty +0.29; star −0.33, satisfaction −0.32 |
| **U/Total** | star **+0.40**, satisfaction **+0.40**, time −0.29, cognitive_load +0.26 |
| **violations/used** | challenge −0.34, time **−0.37**, num_reviews −0.32, course_level +0.34; star/satisfaction +0.33 |

Every slide metric's strongest links are to **course difficulty / workload**
(challenge, time) and, negatively, to **satisfaction** — not to the pedagogical
review aspects, which are almost absent from this table.

---

## 3. The central result: confound decomposition (slide → satisfaction)

Partial correlations, adding controls left→right (course level, then difficulty,
then deck size):

| metric | raw r | \| level | \| level+difficulty | \| +deck size |
|---|---|---|---|---|
| **visual_documentation** | −0.40 | −0.36 | −0.29 | **−0.28 (SURVIVES)** |
| prerequisite | −0.30 | −0.28 | **+0.01** | +0.03 |
| progression | −0.26 | −0.29 | −0.09 | −0.05 |
| long_term_recall | −0.26 | −0.27 | −0.10 | −0.10 |
| Total (deck size) | −0.32 | −0.31 | −0.15 | — |
| visual_appeal | +0.04 | +0.01 | −0.13 | −0.12 |
| U/Total | +0.40 | +0.36 | +0.29 | **+0.27 (SURVIVES)** |

**Conclusion:** four of five metrics (`prerequisite`, `progression`,
`long_term_recall`, `Total`) are **pure rigor proxies** — their tie to ratings
collapses to ≈ 0 once difficulty is held constant (`prerequisite` goes to exactly
+0.01). **Only `visual_documentation` (equivalently `U/Total`) retains a
slide-specific association with satisfaction** after controlling for level,
difficulty, and deck size. It is the single defensible slide→outcome effect.

---

## 4. Why `visual_documentation` is negative (mechanism)

Higher documentation = **fewer uncaptioned visuals**, yet predicts **lower**
ratings. High-documentation decks are systematically:

| trait | high-doc | low-doc |
|---|---|---|
| star rating | 2.93 | 3.55 |
| satisfaction | 0.58 | 0.71 |
| deck size (Total) | 773 | 549 |
| time investment | 0.80 | 0.74 |
| challenge | 0.74 | 0.69 |

Interpretation: an "unexplained" visual is often a **decorative/illustrative
image** (photo, icon) that needs no caption, while a captioned visual is usually
a **technical figure**. So low documentation ≈ image-rich, approachable decks
(better-liked), high documentation ≈ dense technical decks (less-liked). The
metric conflates *documentation quality* with *technical density*. Because the
effect survives difficulty control (§3), part of it is genuine: **visual
engagement beats exhaustive annotation for perceived satisfaction, independent of
difficulty** — the one publishable-grade slide finding, pending replication at
larger n.

---

## 5. User ratio hypotheses — both validated

- **H1: `U/Total` ↑ (more uncaptioned visuals) → CCR star ↑.** Confirmed:
  r = **+0.40**, p = 0.002 (satisfaction +0.40). Partly survives difficulty
  control (+0.27) — semi-independent, not only a rigor artifact.
- **H2: `violations/used` ↑ → CCR challenge ↓.** Confirmed: r = **−0.34**,
  p = 0.009 (time −0.37, p = 0.004). But this one is a **pure difficulty
  artifact** — `prerequisite`'s satisfaction link vanishes under control, so H2
  reflects course rigor, not a slide property.

---

## 6. Cross-family / behavioral findings

- **Difficulty proxy:** deck size, prerequisite, progression, long_term_recall
  all load positively on challenge/time and negatively on satisfaction — slide
  metrics primarily re-measure course rigor.
- **"Worth-it" effect:** the recommend-minus-satisfaction gap is driven by
  challenge (+0.40) and time (+0.44), **not** easy grading (+0.18, ns). Students
  recommend hard courses *beyond* their satisfaction.
- **Easy grading buys recommendations, not respect:** grade_accessibility →
  recommendation +0.42, but → rmp quality +0.09 (ns).
- **Halo-residualized aspects:** after removing general sentiment, the aspect ↔
  slide correlations essentially vanish (only `materials_quality` ↔ deck size
  +0.27 remains) — de-haloed pedagogical judgments are near-orthogonal to the
  slide metrics.
- **Course level ≠ rating:** level ↔ star ≈ 0; level mainly tracks
  `prerequisite` (−0.35) and enrollment (−0.26).
- **Enrollment is not the confound:** controlling for it leaves
  `visual_documentation` → star unchanged (−0.40 → −0.40).

---

## 7. Bottom line

1. **Slide metrics are mostly difficulty detectors.** `prerequisite`,
   `progression`, `long_term_recall`, and deck size measure course rigor, not
   slide quality — their rating correlations are confounds that vanish under
   control.
2. **`visual_documentation` is the exception** — the only slide feature with a
   satisfaction link independent of level, difficulty, and size (partial ≈ −0.28
   / +0.28 for `U/Total`), plausibly "visual engagement > exhaustive annotation."
3. **`visual_appeal` carries no signal** (near-constant); `progression` is
   low-resolution.
4. **Students reward difficulty with recommendations and punish it with
   satisfaction; easy grading does the reverse.**
5. All results are **raw-p, n = 58/73, uncorrected** — directional, not
   confirmatory. The path to publishable: scale n (~150), pre-register H1/H2 +
   the `visual_documentation` effect, extract slide-relevant review sentences, and
   add human slide-quality ground truth.
