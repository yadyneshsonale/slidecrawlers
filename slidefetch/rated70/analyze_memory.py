"""Memory-trajectory analysis for the ability-persona learning simulation.

The memory run scored every lecture deck through each of the five ability
personas *while carrying a running notebook forward* (persona_mem_* tables in
``rated70.db``).  Unlike the memoryless persona runs (``pasum_`` / ``psum_``),
each deck's score is conditioned on everything the persona has "learned" so far,
and every step also emits a learning record (learned / applied / struggled /
mastery / memory).

This script turns those trajectories into course-level features and tests three
things:

  1.  Does the memory-conditioned panel score (``pmsum_``) correlate with the
      CCR / RMP / would-take-again targets any better than the memoryless panel
      scores (``sum_`` / ``pasum_``)?  (Expectation from prior runs: averaging
      washes out, so no.)

  2.  Temporal dynamics.  Per (course, persona) we fit the trajectory of
      mastery and of the perceived clarity / cognitive-load over lecture index
      and compute a learning gain.  The compounding hypothesis: high-ability
      personas *consolidate* (positive mastery slope) while low-ability personas
      *accumulate confusion* (flat/negative slope), and this gap should widen
      the harder / less self-contained the course is.

  3.  Per-persona disaggregation (the 5x9 faceted heatmap), the memory analogue
      of ``per_ability_persona_heatmaps.png``, to see whether conditioning on
      learning history sharpens or blurs the ability gradient.

Course-level columns written into ``rated70_aspects.db`` (aspects table):
  pmsum_<metric>       memory panel mean (Gaussian-weighted), 9 metrics
  pm_gain              ability-weighted learning gain (mastery_last - mastery_1)
  pm_mastery_final     ability-weighted final mastery
  pm_mastery_slope     ability-weighted OLS slope of mastery over lecture index
  pm_clarity_slope     ability-weighted slope of conceptual_clarity
  pm_cogload_slope     ability-weighted slope of cognitive_load_management
  pm_confusion         ability-weighted slope of "struggled" text length
  pm_gain_low/high     learning gain for low_performer / high_achiever
  pm_mslope_low/high   mastery slope for low_performer / high_achiever

Outputs (written next to the db):
  memory_temporal_by_persona.csv     per (course, persona) temporal metrics
  memory_panel_vs_targets.csv        pmsum_ + pm_* vs CCR/RMP/WTA (long, sig)
  memory_vs_memoryless_panel.csv     pmsum_ vs sum_/pasum_ head-to-head r
  per_memory_persona_heatmaps.png    5x9 faceted per-persona heatmap vs targets
  memory_temporal_heatmap.png        temporal features vs targets
  memory_mastery_slope_by_persona.png  compounding-hypothesis bar chart

Run with the RateMySlides venv::

    source /home/b-ysonale/RateMySlides/.venv/bin/activate
    python3 analyze_memory.py
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

import correlate as C  # reuse pairwise_long / heatmap / rp_matrices / ALPHA

HERE = Path(__file__).resolve().parent
ASPECTS_DB = HERE / "rated70_aspects.db"
RATED70_DB = HERE / "rated70.db"

# Ability-mixture weights (Gaussian over the 5-point ladder), matching the
# pipeline's ABILITY_WEIGHTS used for the pasum_ panel.
ABILITY_WEIGHTS = {
    "low_performer": 0.055,
    "below_average": 0.244,
    "average": 0.403,
    "above_average": 0.244,
    "high_achiever": 0.055,
}
PERSONA_ORDER = ["low_performer", "below_average", "average",
                 "above_average", "high_achiever"]

# The nine deck metrics (two are difficulty controls, not "quality").
METRICS = ["conceptual_clarity", "visual_grounding", "example_support",
           "organization_flow", "concept_reinforcement", "motivation_relevance",
           "instructional_visual_richness", "cognitive_load_management",
           "self_containment"]

TARGETS = ["ccr_star_rating", "ccr_student_satisfaction", "ccr_challenge_level",
           "ccr_time_investment", "ccr_recommendation_rate",
           "ccr_grade_accessibility", "rmp_avg_rating", "rmp_avg_difficulty",
           "rmp_would_take_again"]


# --------------------------------------------------------------------------- #
# 1. Temporal features per (course, persona)                                  #
# --------------------------------------------------------------------------- #
def _slope(x: np.ndarray, y: np.ndarray) -> float:
    """OLS slope of y on x; NaN if <2 distinct x."""
    if len(x) < 2 or np.unique(x).size < 2:
        return np.nan
    return float(np.polyfit(x, y, 1)[0])


def temporal_features() -> pd.DataFrame:
    """One row per (course_college, persona_id) with trajectory-derived stats."""
    with sqlite3.connect(RATED70_DB) as conn:
        traj = pd.read_sql(
            "SELECT course_college, persona_id, lecture_idx, metric, score "
            "FROM persona_mem_trajectory", conn)
        learn = pd.read_sql(
            "SELECT course_college, persona_id, lecture_idx, mastery, struggled "
            "FROM persona_mem_learning", conn)

    learn["struggle_len"] = learn["struggled"].fillna("").str.split().apply(len)

    recs = []
    for (course, pid), g in learn.groupby(["course_college", "persona_id"]):
        g = g.sort_values("lecture_idx")
        idx = g["lecture_idx"].to_numpy(float)
        mastery = g["mastery"].to_numpy(float)
        rec = {
            "course_college": course,
            "persona_id": pid,
            "n_lectures": len(g),
            "mastery_first": mastery[0],
            "mastery_final": mastery[-1],
            "learning_gain": mastery[-1] - mastery[0],
            "mastery_slope": _slope(idx, mastery),
            "confusion_slope": _slope(idx, g["struggle_len"].to_numpy(float)),
        }
        recs.append(rec)
    feat = pd.DataFrame(recs).set_index(["course_college", "persona_id"])

    # Per-metric perceived-score slopes (how the deck *feels* as the persona
    # accumulates knowledge) for the two headline metrics.
    for metric, out in [("conceptual_clarity", "clarity_slope"),
                        ("cognitive_load_management", "cogload_slope")]:
        sub = traj[traj["metric"] == metric]
        slopes = {}
        for (course, pid), g in sub.groupby(["course_college", "persona_id"]):
            g = g.sort_values("lecture_idx")
            slopes[(course, pid)] = _slope(g["lecture_idx"].to_numpy(float),
                                           g["score"].to_numpy(float))
        feat[out] = pd.Series(slopes)
    return feat.reset_index()


def weighted_course(feat: pd.DataFrame, col: str) -> pd.Series:
    """Ability-weighted mean of a per-persona feature -> per-course series."""
    w = feat["persona_id"].map(ABILITY_WEIGHTS)
    tmp = feat[["course_college"]].copy()
    tmp["wv"] = feat[col] * w
    tmp["w"] = w.where(feat[col].notna())
    grp = tmp.groupby("course_college")
    return (grp["wv"].sum() / grp["w"].sum())


# --------------------------------------------------------------------------- #
# 2. Memory panel scores (pmsum_) pivot                                       #
# --------------------------------------------------------------------------- #
def memory_panel() -> pd.DataFrame:
    with sqlite3.connect(RATED70_DB) as conn:
        df = pd.read_sql(
            "SELECT course_college, metric, score FROM persona_mem_summary_scores",
            conn)
    wide = df.pivot_table(index="course_college", columns="metric",
                          values="score", aggfunc="first")
    wide.columns = [f"pmsum_{c}" for c in wide.columns]
    return wide


def persona_type_panel() -> pd.DataFrame:
    """Long per-persona course metric scores (for the faceted heatmap)."""
    with sqlite3.connect(RATED70_DB) as conn:
        return pd.read_sql(
            "SELECT course_college, persona_id, metric, score "
            "FROM persona_mem_type_scores", conn)


# --------------------------------------------------------------------------- #
# 3. Write columns into the aspects DB                                        #
# --------------------------------------------------------------------------- #
def write_columns(new_cols: pd.DataFrame) -> None:
    with sqlite3.connect(ASPECTS_DB) as conn:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(aspects)")}
        for col in new_cols.columns:
            if col not in existing:
                conn.execute(f'ALTER TABLE aspects ADD COLUMN "{col}" REAL')
        for cc, row in new_cols.iterrows():
            sets = ", ".join(f'"{c}"=?' for c in new_cols.columns)
            vals = [None if pd.isna(v) else float(v) for v in row]
            conn.execute(f"UPDATE aspects SET {sets} WHERE college_course=?",
                         vals + [cc])
        conn.commit()


# --------------------------------------------------------------------------- #
# 4. Faceted per-persona heatmap (memory analogue)                            #
# --------------------------------------------------------------------------- #
def faceted_heatmap(long: pd.DataFrame, targets_df: pd.DataFrame,
                    path: Path) -> None:
    """5 personas x (9 metrics -> targets) Spearman heatmaps, insignificant blank."""
    personas = [p for p in PERSONA_ORDER if p in long["persona_id"].unique()]
    fig, axes = plt.subplots(1, len(personas),
                             figsize=(4.2 * len(personas), 6.4), squeeze=False)
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad(color="#f0f0f0")
    tgt_cols = [t for t in TARGETS if t in targets_df.columns]
    for ax, pid in zip(axes[0], personas):
        wide = (long[long["persona_id"] == pid]
                .pivot_table(index="course_college", columns="metric",
                             values="score", aggfunc="first"))
        wide.columns = [f"m_{c}" for c in wide.columns]
        merged = wide.join(targets_df[tgt_cols], how="inner")
        rows = [f"m_{m}" for m in METRICS if f"m_{m}" in merged.columns]
        r_df, p_df = C.rp_matrices(merged, rows, tgt_cols, method="spearman")
        data = r_df.values.astype(float)
        masked = np.where(p_df.values.astype(float) < C.ALPHA, data, np.nan)
        im = ax.imshow(masked, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(tgt_cols)))
        ax.set_xticklabels([t.replace("ccr_", "").replace("rmp_", "")
                            for t in tgt_cols], rotation=45, ha="right", fontsize=8)
        if ax is axes[0][0]:
            ax.set_yticks(range(len(rows)))
            ax.set_yticklabels([r.replace("m_", "") for r in rows], fontsize=9)
        else:
            ax.set_yticks([])
        for i in range(len(rows)):
            for j in range(len(tgt_cols)):
                v = masked[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=7, color="white" if abs(v) > 0.5 else "black")
        ax.set_title(pid.replace("_", " "), fontsize=11)
    fig.suptitle("Memory-conditioned per-persona metrics vs course targets "
                 "(Spearman r, p<0.05)", fontsize=13)
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def mastery_slope_bar(feat: pd.DataFrame, path: Path) -> None:
    """Compounding hypothesis: mean mastery slope + learning gain per persona."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    for ax, col, title in [(a1, "mastery_slope", "Mastery slope over lectures"),
                           (a2, "learning_gain", "Learning gain (final - first)")]:
        means = [feat[feat.persona_id == p][col].mean() for p in PERSONA_ORDER]
        sems = [feat[feat.persona_id == p][col].sem() for p in PERSONA_ORDER]
        colors = plt.get_cmap("RdBu_r")(np.linspace(0.15, 0.85, len(PERSONA_ORDER)))
        ax.bar(range(len(PERSONA_ORDER)), means, yerr=sems, capsize=4, color=colors)
        ax.axhline(0, color="black", lw=0.8)
        ax.set_xticks(range(len(PERSONA_ORDER)))
        ax.set_xticklabels([p.replace("_", "\n") for p in PERSONA_ORDER], fontsize=9)
        ax.set_title(title)
    fig.suptitle("Does the difficulty confound compound over time?", fontsize=13)
    plt.tight_layout(rect=(0, 0, 1, 0.95))
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# --------------------------------------------------------------------------- #
# main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    feat = temporal_features()
    feat.round(4).to_csv(HERE / "memory_temporal_by_persona.csv", index=False)
    print(f"temporal features: {len(feat)} (course x persona) rows, "
          f"{feat['course_college'].nunique()} courses")

    # --- ability-weighted course-level temporal columns ------------------- #
    course_cols = {
        "pm_gain": weighted_course(feat, "learning_gain"),
        "pm_mastery_final": weighted_course(feat, "mastery_final"),
        "pm_mastery_slope": weighted_course(feat, "mastery_slope"),
        "pm_clarity_slope": weighted_course(feat, "clarity_slope"),
        "pm_cogload_slope": weighted_course(feat, "cogload_slope"),
        "pm_confusion": weighted_course(feat, "confusion_slope"),
    }
    course_df = pd.DataFrame(course_cols)
    # per-persona extremes for the compounding test
    for pid, tag in [("low_performer", "low"), ("high_achiever", "high")]:
        sub = feat[feat.persona_id == pid].set_index("course_college")
        course_df[f"pm_gain_{tag}"] = sub["learning_gain"]
        course_df[f"pm_mslope_{tag}"] = sub["mastery_slope"]

    pmsum = memory_panel()
    all_new = pmsum.join(course_df, how="outer")
    write_columns(all_new)
    print(f"wrote {all_new.shape[1]} memory columns into aspects DB "
          f"({pmsum.shape[1]} pmsum_ + {course_df.shape[1]} temporal)")

    # --- correlation analysis against targets ----------------------------- #
    num = C.load_numeric()
    pm_panel = [c for c in num.columns if c.startswith("pmsum_")]
    pm_temp = list(course_df.columns)
    tgt = [t for t in TARGETS if t in num.columns]

    panel_long = C.pairwise_long(num, pm_panel, tgt, method="spearman")
    temp_long = C.pairwise_long(num, pm_temp, tgt, method="spearman")
    combined = pd.concat([panel_long, temp_long], ignore_index=True)
    combined = combined.rename(columns={"row": "memory_feature", "col": "target"})
    combined[["memory_feature", "target", "r", "p_raw", "n", "q_bh",
              "sig_bh"]].round(4).to_csv(
        HERE / "memory_panel_vs_targets.csv", index=False)

    # head-to-head: memory panel vs memoryless panels for the same metric
    rows = []
    for m in METRICS:
        for fam in ["pmsum", "pasum", "sum"]:
            col = f"{fam}_{m}"
            if col not in num.columns:
                continue
            best = panel_long if fam == "pmsum" else C.pairwise_long(
                num, [col], tgt, method="spearman").rename(
                columns={"row": "memory_feature", "col": "target"})
            sub = (combined if fam == "pmsum" else best)
            sub = sub[sub["memory_feature"] == col]
            sig = sub[sub["p_raw"] < C.ALPHA]
            rows.append({
                "metric": m, "family": fam,
                "n_sig_targets": len(sig),
                "max_abs_r": sub["r"].abs().max(),
                "best_target": (sig.loc[sig["r"].abs().idxmax(), "target"]
                                if len(sig) else ""),
            })
    h2h = pd.DataFrame(rows)
    h2h.round(4).to_csv(HERE / "memory_vs_memoryless_panel.csv", index=False)

    # --- figures ---------------------------------------------------------- #
    long = persona_type_panel()
    faceted_heatmap(long, num, HERE / "per_memory_persona_heatmaps.png")
    C.heatmap(num, pm_temp, tgt,
              "Memory temporal features vs course targets (Spearman r, p<0.05)",
              HERE / "memory_temporal_heatmap.png", method="spearman")
    mastery_slope_bar(feat, HERE / "memory_mastery_slope_by_persona.png")

    # --- console summary -------------------------------------------------- #
    print("\n=== compounding hypothesis: mean mastery slope by persona ===")
    for p in PERSONA_ORDER:
        s = feat[feat.persona_id == p]
        print(f"  {p:15s} mastery_slope={s['mastery_slope'].mean():+.4f}  "
              f"learning_gain={s['learning_gain'].mean():+.3f}  "
              f"final_mastery={s['mastery_final'].mean():.3f}")

    print("\n=== significant memory-feature x target correlations (Spearman, p<0.05) ===")
    sig = combined[combined["p_raw"] < C.ALPHA].sort_values(
        "r", key=lambda s: s.abs(), ascending=False)
    if sig.empty:
        print("  (none)")
    else:
        for _, r in sig.iterrows():
            print(f"  {r['memory_feature']:22s} x {r['target']:24s} "
                  f"r={r['r']:+.3f}  p={r['p_raw']:.3f}  n={int(r['n'])}")

    print("\n=== memory vs memoryless panel (significant targets per metric) ===")
    piv = h2h.pivot(index="metric", columns="family", values="n_sig_targets")
    print(piv.reindex(columns=["sum", "pasum", "pmsum"]).fillna(0).astype(int))


if __name__ == "__main__":
    main()
