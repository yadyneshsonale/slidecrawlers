#!/usr/bin/env python3
"""Flag rated4ccr courses that contain a "document" deck.

For every course folder under ``rated4ccr/`` we RENDER the first page of every
deck to an image and classify it by the image's pixel dimensions::

    image width  > image height  (landscape)      -> SLIDE
    image width <= image height  (portrait/square) -> DOCUMENT

A course is flagged when at least one of its decks has a DOCUMENT first page.

  * ``.pdf``            -> PyMuPDF (fitz) renders page 0 to an image directly
  * ``.pptx`` / ``.ppt`` -> LibreOffice ``--convert-to png`` (first slide only,
    run in parallel), then the PNG is measured with fitz

Run from the slidefetch root with the venv::

    PYTHONPATH=. .venv/bin/python work/_doc_vs_slide.py
    PYTHONPATH=. .venv/bin/python work/_doc_vs_slide.py --render 8   # save sample PNGs
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent          # slidefetch/
RATED4CCR = ROOT / "rated4ccr"
DECK_EXTS = (".pdf", ".ppt", ".pptx")
PPT_EXTS = (".ppt", ".pptx")

# A page counts as "near-square" (worth eyeballing) when the two sides are within
# this relative tolerance of each other.
SQUARE_TOL = 0.02

# Render resolution for PDFs. The verdict only depends on the width/height ratio,
# so a low dpi is enough and keeps rendering fast.
MEASURE_DPI = 72


# --------------------------------------------------------------------------- #
# Render / measure first page -> (width_px, height_px)
# --------------------------------------------------------------------------- #
def render_pdf_first_wh(pdf_path: Path, dpi: int = MEASURE_DPI) -> tuple[int, int]:
    """Render page 0 of a PDF to a pixmap and return (width_px, height_px).

    fitz honours the page's /Rotate, so the image is oriented as a viewer shows it.
    """
    with fitz.open(str(pdf_path)) as doc:
        if doc.page_count == 0:
            raise ValueError("pdf has no pages")
        pix = doc.load_page(0).get_pixmap(dpi=dpi)
        return pix.width, pix.height


def image_wh(png_path: Path) -> tuple[int, int]:
    """Read a rendered PNG's pixel (width, height) with fitz."""
    pix = fitz.Pixmap(str(png_path))
    return pix.width, pix.height


# --------------------------------------------------------------------------- #
# PowerPoint (.ppt/.pptx) -> first-slide PNG via LibreOffice, in parallel.
# Each task converts a chunk in one soffice invocation (unique profile), then
# measures every PNG. Runs at module level so it is picklable by ProcessPool.
# --------------------------------------------------------------------------- #
def _png_convert_task(chunk: list[Path], timeout: int = 600) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not chunk:
        return out
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    try:
        with tempfile.TemporaryDirectory(prefix="lo_in_") as _in, \
             tempfile.TemporaryDirectory(prefix="lo_out_") as _out:
            in_dir, out_dir = Path(_in), Path(_out)
            index: dict[str, Path] = {}
            for i, p in enumerate(chunk):
                stem = f"{i:05d}"
                shutil.copy2(p, in_dir / f"{stem}{p.suffix.lower()}")
                index[stem] = p
            cmd = [
                "soffice", "--headless", "--norestore", "--nolockcheck",
                "--convert-to", "png", "--outdir", str(out_dir),
                f"-env:UserInstallation=file://{profile}",
                *[str(in_dir / f"{s}{index[s].suffix.lower()}") for s in index],
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                pass
            for stem, orig in index.items():
                png = out_dir / f"{stem}.png"
                if png.exists():
                    try:
                        out[str(orig)] = image_wh(png)
                    except Exception:
                        pass
        return out
    finally:
        shutil.rmtree(profile, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Classification / helpers
# --------------------------------------------------------------------------- #
def verdict(w: int, h: int) -> tuple[str, str]:
    """Return (verdict, note). verdict in {'slide','document'}."""
    note = ""
    if abs(w - h) / max(w, h) < SQUARE_TOL:
        note = "near-square"
    return ("slide" if w > h else "document"), note


def iter_courses(base: Path):
    for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir():
            yield d


def collect_decks(course: Path) -> list[Path]:
    return sorted((p for p in course.iterdir()
                   if p.is_file() and p.suffix.lower() in DECK_EXTS),
                  key=lambda p: p.name.lower())


def save_sample_png(deck: Path, dest_dir: Path) -> Path | None:
    """Render a deck's first page to a PNG for eyeballing."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = deck.suffix.lower()
    dest = dest_dir / (deck.parent.name + "__" + deck.stem + ".png")
    try:
        if ext == ".pdf":
            with fitz.open(str(deck)) as doc:
                if doc.page_count == 0:
                    return None
                doc.load_page(0).get_pixmap(dpi=110).save(str(dest))
            return dest
        # ppt / pptx -> png via LibreOffice
        profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
        try:
            with tempfile.TemporaryDirectory(prefix="lo_render_") as tmp:
                subprocess.run(
                    ["soffice", "--headless", "--norestore", "--nolockcheck",
                     "--convert-to", "png", "--outdir", tmp,
                     f"-env:UserInstallation=file://{profile}", str(deck)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=180, check=False)
                cand = Path(tmp) / (deck.stem + ".png")
                if not cand.exists():
                    return None
                shutil.copy2(cand, dest)
                return dest
        finally:
            shutil.rmtree(profile, ignore_errors=True)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=str(RATED4CCR),
                    help="rated4ccr directory (default: %(default)s)")
    ap.add_argument("--csv", default=str(ROOT / "work" / "doc_vs_slide.csv"),
                    help="per-deck CSV output path")
    ap.add_argument("--workers", type=int, default=8,
                    help="parallel LibreOffice workers (default: %(default)s)")
    ap.add_argument("--batch", type=int, default=40,
                    help="how many .ppt/.pptx per LibreOffice invocation")
    ap.add_argument("--render", type=int, default=0, metavar="N",
                    help="also save first-page PNG of N flagged decks")
    args = ap.parse_args()

    base = Path(args.base)
    if not base.is_dir():
        print(f"error: {base} is not a directory", file=sys.stderr)
        return 2

    # The rated4ccr tree may be mutated by the concurrent download pipeline
    # (it rmtree's partial course folders). Silence non-fatal MuPDF warnings
    # (e.g. "No default Layer config") so the log stays readable.
    fitz.TOOLS.mupdf_display_errors(False)

    courses = list(iter_courses(base))
    course_decks: dict[Path, list[Path]] = {c: collect_decks(c) for c in courses}
    n_all = sum(len(v) for v in course_decks.values())
    print(f"scanning {len(courses)} course folders / {n_all} decks under {base}\n",
          flush=True)

    # Pass 1: convert every .ppt/.pptx first slide to PNG (parallel) and measure.
    non_pdf = [p for decks in course_decks.values() for p in decks
               if p.suffix.lower() in PPT_EXTS]
    conv_dims: dict[str, tuple[int, int]] = {}
    if non_pdf:
        workers = max(1, min(args.workers, os.cpu_count() or 4))
        chunks = [non_pdf[i:i + args.batch]
                  for i in range(0, len(non_pdf), args.batch)]
        print(f"converting+rendering {len(non_pdf)} PowerPoint decks to PNG via "
              f"LibreOffice ({workers} workers, {len(chunks)} chunks) ...", flush=True)
        done_files = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_png_convert_task, ch): len(ch) for ch in chunks}
            for fut in as_completed(futs):
                conv_dims.update(fut.result())
                done_files += futs[fut]
                print(f"  ppt {done_files}/{len(non_pdf)} rendered={len(conv_dims)}",
                      flush=True)
        print(flush=True)

    # Pass 2: walk every deck, render pdfs directly, look up converted ones.
    rows: list[dict] = []
    flagged: dict[str, list[dict]] = {}
    unknown: list[dict] = []
    totals = {"pdf": 0, "pptx": 0, "ppt": 0}
    done = 0
    vanished = 0                     # files removed by the concurrent pipeline

    for course in courses:
        for deck in course_decks[course]:
            ext = deck.suffix.lower()
            totals[ext.lstrip(".")] += 1
            done += 1
            if done % 500 == 0:
                print(f"  measured {done}/{n_all}", flush=True)
            # Skip files the download pipeline deleted mid-scan.
            if not deck.exists():
                vanished += 1
                continue
            w = h = None
            err = ""
            try:
                if ext == ".pdf":
                    w, h = render_pdf_first_wh(deck)
                else:  # .ppt / .pptx
                    dims = conv_dims.get(str(deck))
                    if dims is not None:
                        w, h = dims
                    else:
                        err = "libreoffice conversion failed"
            except FileNotFoundError:
                vanished += 1
                continue
            except Exception as e:                       # noqa: BLE001
                err = f"{type(e).__name__}: {e}"

            if w is None or h is None:
                rec = {"course": course.name, "file": deck.name, "ext": ext,
                       "w": "", "h": "", "ratio": "", "verdict": "unknown",
                       "note": err}
                rows.append(rec)
                unknown.append(rec)
                continue

            v, note = verdict(w, h)
            rec = {"course": course.name, "file": deck.name, "ext": ext,
                   "w": w, "h": h, "ratio": round(w / h, 4),
                   "verdict": v, "note": note}
            rows.append(rec)
            if v == "document":
                flagged.setdefault(course.name, []).append(rec)

    # CSV
    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["course", "file", "ext", "w", "h",
                                           "ratio", "verdict", "note"])
        wr.writeheader()
        wr.writerows(rows)

    # ---- report -------------------------------------------------------------
    # Reconcile against the live tree: drop courses whose folder was removed by
    # the download pipeline, and drop individual doc files that no longer exist.
    reconciled: dict[str, list[dict]] = {}
    for cname, docs in flagged.items():
        if not (base / cname).is_dir():
            continue
        live = [d for d in docs if (base / cname / d["file"]).exists()]
        if live:
            reconciled[cname] = live
    flagged = reconciled

    n_docs = sum(len(v) for v in flagged.values())
    print("\n" + "=" * 72)
    print(f"decks scanned: {len(rows)}  "
          f"(pdf={totals['pdf']} pptx={totals['pptx']} ppt={totals['ppt']})")
    print(f"documents found: {n_docs} across {len(flagged)} course(s)")
    print(f"unknown/unreadable: {len(unknown)}  vanished(removed mid-scan): {vanished}")
    print(f"per-deck CSV -> {csv_path}")
    print("=" * 72)

    print(f"\nCOURSES CONTAINING >=1 DOCUMENT ({len(flagged)}):\n")
    for cname in sorted(flagged):
        print(f"  {cname}")
        for d in flagged[cname]:
            extra = f" [{d['note']}]" if d["note"] else ""
            print(f"        - {d['file']}  ({d['w']}x{d['h']}px, r={d['ratio']}){extra}")
    if not flagged:
        print("  (none)")

    if unknown:
        print(f"\nUNKNOWN / UNREADABLE ({len(unknown)}):")
        for d in unknown:
            print(f"  {d['course']}/{d['file']}  {d['note']}")

    print("\n--- flagged course names only ---")
    for cname in sorted(flagged):
        print(cname)

    # ---- optional sample PNGs ----------------------------------------------
    if args.render and flagged:
        sample_dir = ROOT / "work" / "doc_vs_slide_samples"
        picks = [d for cname in sorted(flagged) for d in flagged[cname]][:args.render]
        print(f"\nsaving {len(picks)} sample first-page PNGs -> {sample_dir}",
              flush=True)
        for d in picks:
            deck = base / d["course"] / d["file"]
            png = save_sample_png(deck, sample_dir)
            print(f"  {'OK ' if png else 'FAIL'} {png or deck}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
