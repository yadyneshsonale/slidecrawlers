#!/usr/bin/env python3
"""Phase D - correlation analysis of CCR vs RMP (+ slide quality) on merged.csv.

For each variable pair it reports the pairwise-complete n, Pearson r (+ p) and
Spearman rho (+ p). Writes:
  - correlation_report.md        : grouped tables + a plain-English reading
  - correlation_pairs.csv        : every reported pair, machine-readable
  - correlation_matrix_pearson.csv / _spearman.csv : full numeric matrices
  - plots/*.png                  : scatter plots for the headline pairs
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

import common

MERGED = common.ROOT / "merged.csv"
REPORT = common.ROOT / "correlation_report.md"
PAIRS_CSV = common.ROOT / "correlation_pairs.csv"
PLOTS = common.ROOT / "plots"

LABELS = {
    "ccr_star_rating": "CCR course rating (/5)",
    "ccr_challenge_level": "CCR challenge level",
    "ccr_time_investment": "CCR time investment",
    "ccr_grade_accessibility": "CCR grade accessibility",
    "ccr_student_satisfaction": "CCR student satisfaction",
    "ccr_attendance_importance": "CCR attendance importance",
    "ccr_recommendation_rate": "CCR recommendation rate",
    "rmp_course_avg_quality": "RMP course quality (/5)",
    "rmp_course_avg_difficulty": "RMP course difficulty (/5)",
    "rmp_course_would_take_again_pct": "RMP would-take-again %",
    "rmp_prof_avg_rating_overall": "RMP professor overall quality (/5)",
    "slide_final": "Slide quality (final)",
    "slide_layer1_course": "Slide layer-1 score",
    "slide_layer2": "Slide layer-2 score",
}

# grouped list of (x, y) pairs to report
GROUPS = {
    "Primary: CCR course rating vs RMP professor quality": [
        ("ccr_star_rating", "rmp_course_avg_quality"),
        ("ccr_star_rating", "rmp_prof_avg_rating_overall"),
        ("ccr_star_rating", "rmp_course_would_take_again_pct"),
        ("ccr_recommendation_rate", "rmp_course_avg_quality"),
    ],
    "Difficulty / workload alignment": [
        ("ccr_challenge_level", "rmp_course_avg_difficulty"),
        ("ccr_time_investment", "rmp_course_avg_difficulty"),
        ("ccr_challenge_level", "rmp_course_avg_quality"),
    ],
    "CCR sub-metrics vs RMP course quality": [
        ("ccr_student_satisfaction", "rmp_course_avg_quality"),
        ("ccr_grade_accessibility", "rmp_course_avg_quality"),
        ("ccr_time_investment", "rmp_course_avg_quality"),
        ("ccr_attendance_importance", "rmp_course_avg_quality"),
    ],
    "Slide quality vs CCR and RMP": [
        ("slide_final", "ccr_star_rating"),
        ("slide_final", "rmp_course_avg_quality"),
        ("slide_final", "ccr_challenge_level"),
        ("slide_final", "rmp_course_avg_difficulty"),
        ("slide_layer1_course", "rmp_course_avg_quality"),
        ("slide_layer2", "rmp_course_avg_quality"),
    ],
}

MATRIX_COLS = [
    "ccr_star_rating", "ccr_student_satisfaction", "ccr_challenge_level",
    "ccr_grade_accessibility", "ccr_time_investment", "ccr_attendance_importance",
    "ccr_recommendation_rate", "rmp_course_avg_quality", "rmp_course_avg_difficulty",
    "rmp_course_would_take_again_pct", "rmp_prof_avg_rating_overall",
    "slide_final", "slide_layer1_course", "slide_layer2",
]


def stars(p: float) -> str:
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""


def corr(df: pd.DataFrame, x: str, y: str) -> dict:
    sub = df[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
    n = len(sub)
    out = {"x": x, "y": y, "n": n, "pearson_r": np.nan, "pearson_p": np.nan,
           "spearman_rho": np.nan, "spearman_p": np.nan}
    if n >= 3 and sub[x].nunique() > 1 and sub[y].nunique() > 1:
        pr = stats.pearsonr(sub[x], sub[y])
        sp = stats.spearmanr(sub[x], sub[y])
        out.update(pearson_r=pr.statistic, pearson_p=pr.pvalue,
                   spearman_rho=sp.statistic, spearman_p=sp.pvalue)
    return out


def fmt(v: float, d: int = 3) -> str:
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{d}f}"


def make_plot(df: pd.DataFrame, x: str, y: str) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sub = df[[x, y, "folder"]].copy()
    sub[x] = pd.to_numeric(sub[x], errors="coerce")
    sub[y] = pd.to_numeric(sub[y], errors="coerce")
    sub = sub.dropna(subset=[x, y])
    if len(sub) < 3:
        return None
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    ax.scatter(sub[x], sub[y], s=28, alpha=0.75, edgecolor="white", linewidth=0.5)
    m, b = np.polyfit(sub[x], sub[y], 1)
    xs = np.linspace(sub[x].min(), sub[x].max(), 100)
    ax.plot(xs, m * xs + b, color="crimson", linewidth=1.3)
    r = stats.pearsonr(sub[x], sub[y])
    ax.set_xlabel(LABELS.get(x, x))
    ax.set_ylabel(LABELS.get(y, y))
    ax.set_title(f"n={len(sub)}  r={r.statistic:.2f} (p={r.pvalue:.3f})", fontsize=10)
    fig.tight_layout()
    PLOTS.mkdir(exist_ok=True)
    path = PLOTS / f"{x}__vs__{y}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path.name


def main() -> None:
    df = pd.read_csv(MERGED, dtype={"folder": str})

    all_pairs = []
    lines = ["# CCR vs RMP correlation analysis", ""]
    lines.append(f"Courses: **{len(df)}** rated70 courses. "
                 f"CCR ratings present: {df.ccr_star_rating.notna().sum()}/73; "
                 f"RMP quality present: {df.rmp_course_avg_quality.notna().sum()}/73; "
                 f"slide score present: {df.slide_final.notna().sum()}/73.")
    lines += ["", "Each cell reports pairwise-complete `n`, Pearson `r` (linear) and "
              "Spearman `rho` (rank/monotonic) with p-values. "
              "Significance: `*` p<0.05, `**` p<0.01, `***` p<0.001.", ""]

    headline = [
        ("ccr_star_rating", "rmp_course_avg_quality"),
        ("ccr_challenge_level", "rmp_course_avg_difficulty"),
        ("slide_final", "ccr_star_rating"),
        ("slide_final", "rmp_course_avg_quality"),
    ]

    for title, pairs in GROUPS.items():
        lines += [f"## {title}", "",
                  "| x | y | n | Pearson r | p | Spearman rho | p |",
                  "|---|---|---|---|---|---|---|"]
        for x, y in pairs:
            c = corr(df, x, y)
            all_pairs.append({**c, "group": title})
            lines.append(
                f"| {LABELS.get(x, x)} | {LABELS.get(y, y)} | {c['n']} | "
                f"{fmt(c['pearson_r'])}{stars(c['pearson_p']) if not np.isnan(c['pearson_p']) else ''} | "
                f"{fmt(c['pearson_p'])} | "
                f"{fmt(c['spearman_rho'])}{stars(c['spearman_p']) if not np.isnan(c['spearman_p']) else ''} | "
                f"{fmt(c['spearman_p'])} |")
        lines.append("")

    # plain-English reading of the primary result
    p = corr(df, "ccr_star_rating", "rmp_course_avg_quality")
    direction = "positive" if (p["pearson_r"] or 0) > 0 else "negative"
    strength = ("negligible" if abs(p["pearson_r"]) < 0.1 else "weak" if abs(p["pearson_r"]) < 0.3
                else "moderate" if abs(p["pearson_r"]) < 0.5 else "strong")
    sig = "statistically significant" if p["pearson_p"] < 0.05 else "not statistically significant"
    lines += ["## Reading", "",
              f"- **Primary result:** CCR course rating vs RMP course quality shows a {strength} "
              f"{direction} correlation (Pearson r={fmt(p['pearson_r'])}, p={fmt(p['pearson_p'])}, "
              f"Spearman rho={fmt(p['spearman_rho'])}, n={p['n']}) — {sig} at alpha=0.05.",
              "- CCR rates the *course*; RMP rates the *professor*. A positive link means courses "
              "students rate highly also tend to have highly-rated instructors.",
              "- Difficulty measures (CCR challenge level / time investment vs RMP difficulty) are "
              "reported separately above.",
              "- Slide-quality (RateMySlides) correlations use the "
              f"{df.slide_final.notna().sum()} courses that were scored.",
              "",
              "### Caveats",
              "- n is modest (73 courses; fewer for slide pairs), so only moderate+ effects reach significance.",
              "- CCR and RMP aggregate different student populations and time windows.",
              "- Correlation is not causation.", ""]

    # headline plots
    made = []
    for x, y in headline:
        name = make_plot(df, x, y)
        if name:
            made.append(name)
    if made:
        lines += ["## Scatter plots", ""] + [f"![{n}](plots/{n})" for n in made] + [""]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame(all_pairs).to_csv(PAIRS_CSV, index=False)

    num = df[MATRIX_COLS].apply(pd.to_numeric, errors="coerce")
    num.corr(method="pearson").round(3).to_csv(common.ROOT / "correlation_matrix_pearson.csv")
    num.corr(method="spearman").round(3).to_csv(common.ROOT / "correlation_matrix_spearman.csv")

    print(f"wrote {REPORT}")
    print(f"wrote {PAIRS_CSV}")
    print("wrote correlation_matrix_pearson.csv / correlation_matrix_spearman.csv")
    print(f"wrote {len(made)} plots to {PLOTS}")
    print("\n=== headline ===")
    for x, y in headline:
        c = corr(df, x, y)
        print(f"{LABELS.get(x, x)}  vs  {LABELS.get(y, y)}: "
              f"n={c['n']} r={fmt(c['pearson_r'])} p={fmt(c['pearson_p'])} "
              f"rho={fmt(c['spearman_rho'])} p={fmt(c['spearman_p'])}")


if __name__ == "__main__":
    main()
