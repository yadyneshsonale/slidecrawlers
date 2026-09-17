"""Emit one standalone Spearman heatmap per ability persona (memory run).

Splits the faceted ``per_memory_persona_heatmaps.png`` into five individual
figures: ``per_memory_persona_<persona>.png`` (9 metrics x course targets,
insignificant cells left blank).

Run with the RateMySlides venv::

    python3 memory_persona_heatmaps.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze_memory as M
import correlate as C

HERE = Path(__file__).resolve().parent


def main() -> None:
    long = M.persona_type_panel()
    num = C.load_numeric()
    tgt_cols = [t for t in M.TARGETS if t in num.columns]
    personas = [p for p in M.PERSONA_ORDER if p in long["persona_id"].unique()]

    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad(color="#f0f0f0")

    for pid in personas:
        wide = (long[long["persona_id"] == pid]
                .pivot_table(index="course_college", columns="metric",
                             values="score", aggfunc="first"))
        wide.columns = [f"m_{c}" for c in wide.columns]
        merged = wide.join(num[tgt_cols], how="inner")
        rows = [f"m_{m}" for m in M.METRICS if f"m_{m}" in merged.columns]
        r_df, p_df = C.rp_matrices(merged, rows, tgt_cols, method="spearman")
        data = r_df.values.astype(float)
        masked = np.where(p_df.values.astype(float) < C.ALPHA, data, np.nan)
        n = merged[rows + tgt_cols].dropna(how="all").shape[0]

        fig, ax = plt.subplots(figsize=(max(8, len(tgt_cols) * 0.9),
                                        max(5, len(rows) * 0.55)))
        im = ax.imshow(masked, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
        ax.set_xticks(range(len(tgt_cols)))
        ax.set_xticklabels([t.replace("ccr_", "").replace("rmp_", "")
                            for t in tgt_cols], rotation=45, ha="right", fontsize=9)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([r.replace("m_", "") for r in rows], fontsize=10)
        for i in range(len(rows)):
            for j in range(len(tgt_cols)):
                v = masked[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                            fontsize=8, color="white" if abs(v) > 0.5 else "black")
        ax.set_title(f"{pid.replace('_', ' ')} — memory-conditioned metrics "
                     f"vs course targets (Spearman r, p<0.05, n={n})", fontsize=12)
        cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
        cb.set_label("Spearman r")
        ax.set_xticks(np.arange(-0.5, len(tgt_cols), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(rows), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=1.5)
        ax.tick_params(which="minor", length=0)
        plt.tight_layout()
        out = HERE / f"per_memory_persona_{pid}.png"
        plt.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out.name}")


if __name__ == "__main__":
    main()
