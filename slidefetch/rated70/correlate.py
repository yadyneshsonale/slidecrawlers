"""Recompute the rated70 correlation analysis from ``rated70_aspects.db``.

This regenerates every correlation artifact from the current ``aspects`` table so
that newly-added columns (the pedagogically-aligned ``asp_*`` aspects, the review
signal ``rev_*`` columns, and any extra ``gpt_*`` metrics) are included, and adds
the headline validation table/figure: the GPT slide metrics against the six
pedagogical aspects.

Column families are detected by name:
  * ``gpt_*``                      -> GPT slide metrics (predictors)
  * ``asp_*``                      -> new pedagogical review aspects
  * Lecturer/Course/Materials/Support/Grading -> original sentiment aspects
  * ``rev_*``                      -> review-derived signal columns
  * ``ccr_*`` / ``rmp_*``          -> external CCR / RMP course metrics

Outputs (written next to the db):
  full_correlation_matrix.csv          Pearson r for every numeric column pair
  gpt_vs_other_correlation.csv         GPT x (all non-GPT) long form + p-values
  gpt_correlation_significance.csv     same, with Holm/BH significance flags
  aspects_vs_other_significance.csv    all aspect cols (old + asp_) x CCR/RMP/GPT
  asp_vs_gpt_correlation.csv           NEW: 6 pedagogical aspects x GPT metrics
  gpt_correlation_heatmap.png          GPT x non-GPT heatmap
  aspects_correlation_heatmap.png      aspects x CCR/RMP/GPT heatmap
  asp_vs_gpt_heatmap.png               NEW: pedagogical aspects x GPT heatmap

Run with the RateMySlides venv (pandas/scipy/matplotlib)::

    source /home/b-ysonale/RateMySlides/.venv/bin/activate
    python3 correlate.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

DB_PATH = Path(__file__).resolve().parent / "rated70_aspects.db"
RATED70_DB = DB_PATH.parent / "rated70.db"
OLD_ASPECTS = ["Lecturer", "Course", "Materials", "Support", "Grading"]
# Correlations with a raw two-sided p-value at or above this are treated as
# statistically insignificant and left blank in the value CSVs and heatmaps.
ALPHA = 0.05
# Robust variant: aspect ratings the LLM gave with confidence below this are
# treated as "not evidenced" and dropped (set to NaN) before correlating.
CONF_MIN = 0.6
# Aspect display-name -> asp_ column (for joining confidence from rated70.db).
ASPECT_COL = {
    "Conceptual Clarity": "asp_conceptual_clarity",
    "Organization and Flow": "asp_organization_flow",
    "Learning Materials Quality": "asp_learning_materials_quality",
    "Cognitive Load": "asp_cognitive_load",
    "Concept Reinforcement": "asp_concept_reinforcement",
    "Assessment Alignment": "asp_assessment_alignment",
}


def load_numeric() -> pd.DataFrame:
    """Numeric columns of the aspects table, indexed by college_course."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM aspects", conn)
    df = df.set_index("college_course")
    return df.select_dtypes("number")


def load_confidence() -> pd.DataFrame:
    """Per-course LLM confidence for each pedagogical aspect (index college_course)."""
    with sqlite3.connect(RATED70_DB) as conn:
        rows = pd.read_sql(
            "SELECT c.course_college AS college_course, a.aspect, a.confidence "
            "FROM aspect_ratings a JOIN courses c ON a.folder=c.folder "
            "WHERE a.aspect IN (%s)" % ",".join("?" * len(ASPECT_COL)),
            conn, params=list(ASPECT_COL),
        )
    wide = rows.pivot_table(index="college_course", columns="aspect",
                            values="confidence", aggfunc="first")
    return wide.rename(columns=ASPECT_COL)



def _holm(pvals: np.ndarray) -> np.ndarray:
    """Holm-Bonferroni adjusted p-values."""
    order = np.argsort(pvals)
    m = len(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * pvals[idx]
        running = max(running, val)
        adj[idx] = min(running, 1.0)
    return adj


def _bh(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg (FDR) adjusted q-values."""
    order = np.argsort(pvals)
    m = len(pvals)
    adj = np.empty(m)
    running = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        val = pvals[idx] * m / (rank + 1)
        running = min(running, val)
        adj[idx] = min(running, 1.0)
    return adj


def pairwise_long(num: pd.DataFrame, rows: list[str], cols: list[str],
                  method: str = "pearson") -> pd.DataFrame:
    """Long-form r + p for each (row metric, col metric), pairwise-complete.

    ``method`` is ``"pearson"`` or ``"spearman"``.
    """
    corr_fn = stats.spearmanr if method == "spearman" else stats.pearsonr
    recs = []
    for r_name in rows:
        for c_name in cols:
            if r_name == c_name:
                continue
            pair = num[[r_name, c_name]].dropna()
            n = len(pair)
            if n < 3 or pair[r_name].nunique() < 2 or pair[c_name].nunique() < 2:
                r_val, p_val = np.nan, np.nan
            else:
                r_val, p_val = corr_fn(pair[r_name], pair[c_name])
            recs.append({"row": r_name, "col": c_name, "r": r_val, "p_raw": p_val, "n": n})
    out = pd.DataFrame(recs)
    valid = out["p_raw"].notna()
    out["p_holm"] = np.nan
    out["q_bh"] = np.nan
    if valid.any():
        out.loc[valid, "p_holm"] = _holm(out.loc[valid, "p_raw"].to_numpy())
        out.loc[valid, "q_bh"] = _bh(out.loc[valid, "p_raw"].to_numpy())
    out["sig_holm"] = out["p_holm"] < 0.05
    out["sig_bh"] = out["q_bh"] < 0.05
    return out


def rp_matrices(num: pd.DataFrame, rows: list[str], cols: list[str], method: str = "pearson"):
    """Return (r_df, p_df) pivots of r and raw p for rows x cols."""
    long = pairwise_long(num, rows, cols, method=method)
    r_df = long.pivot(index="row", columns="col", values="r").reindex(index=rows, columns=cols)
    p_df = long.pivot(index="row", columns="col", values="p_raw").reindex(index=rows, columns=cols)
    return r_df, p_df


def heatmap(num: pd.DataFrame, rows: list[str], cols: list[str], title: str, path: Path,
            row_strip: str = "", alpha: float = ALPHA, method: str = "pearson") -> None:
    """Heatmap of r; cells with raw p >= alpha are left blank (grey)."""
    r_df, p_df = rp_matrices(num, rows, cols, method=method)
    data = r_df.values.astype(float)
    pvals = p_df.values.astype(float)
    masked = np.where((pvals < alpha) & ~np.isnan(data), data, np.nan)
    # Drop x-axis columns that are completely blank (no significant cell).
    keep = ~np.isnan(masked).all(axis=0)
    masked = masked[:, keep]
    cols = [c for c, k in zip(cols, keep) if k]
    if not cols:
        print(f"  [skip] {path.name}: no significant columns to plot")
        return
    r_labels = [r.replace(row_strip, "") if row_strip else r for r in rows]
    fig, ax = plt.subplots(figsize=(max(8, len(cols) * 0.85), max(4, len(rows) * 0.6)))
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad(color="#f0f0f0")  # insignificant / missing cells shown blank grey
    im = ax.imshow(masked, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(r_labels, fontsize=10)
    for i in range(len(rows)):
        for j in range(len(cols)):
            v = masked[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(v) > 0.5 else "black")
    ax.set_title(title, fontsize=12, pad=12)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cb.set_label(f"{method.title()} r")
    ax.set_xticks(np.arange(-0.5, len(cols), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(rows), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.5)
    ax.tick_params(which="minor", length=0)
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    num = load_numeric()
    # Columns excluded from the correlation analysis entirely:
    #  * gpt_final              - near-constant composite
    #  * gpt_symbol, gpt_abbreviation - sparse/zero-inflated penalty scores
    #    (most decks score ~1; little variance to correlate)
    #  * asp_concept_reinforcement - reviews rarely evidence it (most ratings
    #    low-confidence), so it is removed rather than correlated on thin data.
    excluded = {"gpt_final", "gpt_symbol", "gpt_abbreviation",
                "asp_concept_reinforcement"}
    num = num.drop(columns=[c for c in num.columns if c in excluded])
    cols_all = list(num.columns)
    gpt = [c for c in cols_all if c.startswith("gpt_")]
    asp = [c for c in cols_all if c.startswith("asp_")]
    rev = [c for c in cols_all if c.startswith("rev_")]
    ccr = [c for c in cols_all if c.startswith("ccr_")]
    rmp = [c for c in cols_all if c.startswith("rmp_")]
    old_asp = [c for c in OLD_ASPECTS if c in cols_all]
    aspects = old_asp + asp
    non_gpt = [c for c in cols_all if c not in gpt]
    ext = ccr + rmp  # external course metrics

    n_gpt = num.dropna(subset=gpt, how="all").shape[0]
    print(f"rows: {len(num)} total, {n_gpt} with GPT data")
    print(f"gpt={len(gpt)} asp={len(asp)} old_aspects={len(old_asp)} "
          f"rev={len(rev)} ccr={len(ccr)} rmp={len(rmp)}")

    here = DB_PATH.parent

    def blank_insignificant(frame: pd.DataFrame) -> pd.DataFrame:
        """Return a copy with r left blank (NaN) wherever raw p >= ALPHA."""
        out = frame.copy()
        out["r"] = out["r"].where(out["p_raw"] < ALPHA)
        return out

    # 1. Full correlation matrix (all numeric columns), insignificant cells blank.
    r_all, p_all = rp_matrices(num, cols_all, cols_all)
    for c in cols_all:  # self-correlation is 1.0 and always kept
        r_all.loc[c, c] = 1.0
        p_all.loc[c, c] = 0.0
    r_all.where(p_all < ALPHA).round(4).to_csv(here / "full_correlation_matrix.csv")

    # 2. GPT vs every non-GPT column (long form + significance).
    gpt_other = pairwise_long(num, gpt, non_gpt)
    gpt_other_r = gpt_other.rename(columns={"row": "gpt_metric", "col": "other"})
    gpt_other_r["gpt_metric"] = gpt_other_r["gpt_metric"].str.replace("gpt_", "", regex=False)
    blank_insignificant(gpt_other_r)[["gpt_metric", "other", "r", "p_raw", "q_bh",
                                       "p_holm"]].round(4).to_csv(
        here / "gpt_vs_other_correlation.csv", index=False)
    # The *_significance.csv keeps every row (incl. insignificant) for auditing.
    gpt_other_r[["gpt_metric", "other", "r", "p_raw", "n", "p_holm", "q_bh",
                 "sig_holm", "sig_bh"]].round(4).to_csv(
        here / "gpt_correlation_significance.csv", index=False)

    # 3. All aspect columns (old sentiment + new pedagogical) vs CCR/RMP/GPT.
    asp_other = pairwise_long(num, aspects, ext + gpt)
    asp_other = asp_other.rename(columns={"row": "aspect", "col": "other"})
    asp_other[["aspect", "other", "r", "p_raw", "q_bh", "p_holm", "n",
               "sig_holm", "sig_bh"]].round(4).to_csv(
        here / "aspects_vs_other_significance.csv", index=False)

    # 4. NEW headline: the six pedagogical aspects vs the GPT slide metrics.
    asp_gpt = pairwise_long(num, asp, gpt)
    asp_gpt = asp_gpt.rename(columns={"row": "aspect", "col": "gpt_metric"})
    asp_gpt["aspect"] = asp_gpt["aspect"].str.replace("asp_", "", regex=False)
    asp_gpt["gpt_metric"] = asp_gpt["gpt_metric"].str.replace("gpt_", "", regex=False)
    blank_insignificant(asp_gpt)[["aspect", "gpt_metric", "r", "p_raw", "n", "q_bh",
             "p_holm", "sig_holm", "sig_bh"]].round(4).to_csv(
        here / "asp_vs_gpt_correlation.csv", index=False)

    # 5. Heatmaps.
    heatmap(num, gpt, non_gpt,
            f"GPT slide metrics vs course/aspect/RMP/CCR metrics (Pearson r, n={n_gpt})",
            here / "gpt_correlation_heatmap.png", row_strip="gpt_")
    heatmap(num, aspects, ext + gpt,
            "Review aspects (sentiment + pedagogical) vs CCR/RMP/GPT (Pearson r)",
            here / "aspects_correlation_heatmap.png")
    heatmap(num, asp, gpt,
            f"Pedagogical review aspects vs GPT slide metrics (Pearson r, n={n_gpt})",
            here / "asp_vs_gpt_heatmap.png", row_strip="asp_")

    # Console summary of the headline table (insignificant cells blanked).
    pd.set_option("display.width", 200)
    masked_gpt = blank_insignificant(asp_gpt)
    pivot = masked_gpt.pivot(index="aspect", columns="gpt_metric", values="r")
    print(f"\n=== Pedagogical aspects vs GPT slide metrics (Pearson r, p<{ALPHA}) ===")
    print(pivot.round(3).fillna("").to_string())
    sig = asp_gpt[asp_gpt["p_raw"] < ALPHA].sort_values("p_raw")
    print(f"\nRaw-significant aspect-GPT pairs (p<{ALPHA}): {len(sig)}  "
          f"(BH q<0.05: {int(asp_gpt['sig_bh'].sum())})")
    for _, row in sig.iterrows():
        print(f"  {row['aspect']:24s} x {row['gpt_metric']:20s} "
              f"r={row['r']:+.3f}  p={row['p_raw']:.4f}  q={row['q_bh']:.4f}")

    # ------------------------------------------------------------------ #
    # ROBUST VARIANT: confidence-filtered ratings + normalised GPT counts
    # + near-constant predictors dropped, Spearman rank correlation.
    # ------------------------------------------------------------------ #
    robust_headline(num, asp, gpt, here)

    print("\nsaved: full_correlation_matrix.csv, gpt_vs_other_correlation.csv, "
          "gpt_correlation_significance.csv, aspects_vs_other_significance.csv, "
          "asp_vs_gpt_correlation.csv, gpt_correlation_heatmap.png, "
          "aspects_correlation_heatmap.png, asp_vs_gpt_heatmap.png, "
          "asp_vs_gpt_correlation_robust.csv, asp_vs_gpt_heatmap_robust.png, "
          "asp_vs_gpt_n_heatmap.png")


def build_robust_gpt(num: pd.DataFrame, gpt: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Return (frame, gpt_predictors) with counts normalised and constants dropped.

    - adds ``gpt_violation_rate = violations / used`` and drops the raw count
      columns (U, Total, violations, used) that mostly encode deck size,
    - drops near-constant predictors (< 8 distinct values or CV < 0.05).
    """
    out = num.copy()
    counts = {"gpt_U", "gpt_Total", "gpt_violations", "gpt_used"}
    if {"gpt_violations", "gpt_used"} <= set(out.columns):
        out["gpt_violation_rate"] = out["gpt_violations"] / out["gpt_used"].replace(0, np.nan)
    candidates = [c for c in out.columns if c.startswith("gpt_") and c not in counts]
    predictors = []
    dropped = []
    for c in candidates:
        s = out[c].dropna()
        cv = s.std() / abs(s.mean()) if s.mean() else 0.0
        if s.nunique() < 8 or cv < 0.05:
            dropped.append(f"{c.replace('gpt_', '')}(nuniq={s.nunique()},cv={cv:.2f})")
        else:
            predictors.append(c)
    if dropped:
        print(f"  dropped near-constant GPT predictors: {', '.join(dropped)}")
    return out, predictors


def n_heatmap(num: pd.DataFrame, rows: list[str], cols: list[str], title: str, path: Path,
              row_strip: str = "") -> None:
    """Heatmap whose cells are the pairwise-complete sample size n (courses)."""
    N = np.array([[len(num[[r, c]].dropna()) for c in cols] for r in rows])
    r_labels = [r.replace(row_strip, "") if row_strip else r for r in rows]
    c_labels = [c.replace("gpt_", "") for c in cols]
    fig, ax = plt.subplots(figsize=(max(8, len(cols) * 0.9), max(4, len(rows) * 0.6)))
    im = ax.imshow(N, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(c_labels, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(r_labels, fontsize=10)
    hi = N.max() if N.size else 1
    for i in range(len(rows)):
        for j in range(len(cols)):
            ax.text(j, i, str(N[i, j]), ha="center", va="center", fontsize=10,
                    color="white" if N[i, j] < hi * 0.55 else "black")
    ax.set_title(title, fontsize=12, pad=12)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    cb.set_label("n courses")
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def robust_headline(num: pd.DataFrame, asp: list[str], gpt: list[str], here: Path) -> None:
    """Confidence-filtered, Spearman, normalised-count headline (asp x GPT)."""
    conf = load_confidence().reindex(num.index)
    robust = num.copy()
    n_before = int(robust[asp].notna().sum().sum())
    for col in asp:  # blank ratings the LLM was not confident about
        if col in conf.columns:
            robust.loc[conf[col] < CONF_MIN, col] = np.nan
    n_after = int(robust[asp].notna().sum().sum())
    print(f"\n=== ROBUST variant (conf>={CONF_MIN}, Spearman, normalised counts) ===")
    print(f"aspect ratings kept after confidence filter: {n_after}/{n_before}")

    robust, gpt_pred = build_robust_gpt(robust, gpt)

    long = pairwise_long(robust, asp, gpt_pred, method="spearman")
    long = long.rename(columns={"row": "aspect", "col": "gpt_metric"})
    long["aspect"] = long["aspect"].str.replace("asp_", "", regex=False)
    long["gpt_metric"] = long["gpt_metric"].str.replace("gpt_", "", regex=False)
    masked = long.copy()
    masked["r"] = masked["r"].where(masked["p_raw"] < ALPHA)
    masked[["aspect", "gpt_metric", "r", "p_raw", "n", "q_bh", "p_holm",
            "sig_holm", "sig_bh"]].round(4).to_csv(
        here / "asp_vs_gpt_correlation_robust.csv", index=False)

    heatmap(robust, asp, gpt_pred,
            f"Pedagogical aspects vs GPT slide metrics (Spearman, conf>={CONF_MIN})",
            here / "asp_vs_gpt_heatmap_robust.png", row_strip="asp_", method="spearman")

    n_heatmap(robust, asp, gpt_pred,
              f"Sample size n per aspect x GPT pair (after confidence>={CONF_MIN} filter)",
              here / "asp_vs_gpt_n_heatmap.png", row_strip="asp_")

    pivot = masked.pivot(index="aspect", columns="gpt_metric", values="r")
    print(pivot.round(3).fillna("").to_string())
    sig = long[long["p_raw"] < ALPHA].sort_values("p_raw")
    print(f"Raw-significant pairs (p<{ALPHA}): {len(sig)}  "
          f"(BH q<0.05: {int(long['sig_bh'].sum())})")
    for _, row in sig.iterrows():
        print(f"  {row['aspect']:24s} x {row['gpt_metric']:20s} "
              f"rho={row['r']:+.3f}  p={row['p_raw']:.4f}  q={row['q_bh']:.4f}  n={int(row['n'])}")



if __name__ == "__main__":
    main()
