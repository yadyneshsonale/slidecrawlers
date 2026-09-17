# slidegrab

Keyword-search course slide-deck downloader. Instead of crawling university
domains, it **searches** for course pages by keyword (e.g.
*"IIT Madras CS7015 deep learning lecture slides"*), opens the results, extracts
lecture-slide links (PDF/PPT/PPTX) from both static HTML and JS-rendered pages,
and downloads them into `dataset/<university>/<course>/lecture_NN.pdf`.

No course URLs are hardcoded — every page is discovered via search.
Indian universities (IITs, IISc, IIIT, NPTEL) are processed first, then global.

## Why keyword search

Keyless web search (Bing/DuckDuckGo/etc.) is blocked from this host's IP, so
discovery uses a keyed web-search API. The search layer is provider-agnostic and
auto-selects whichever key you set:

| Provider | Env var | Free tier |
|----------|---------|-----------|
| **Tavily** | `TAVILY_API_KEY` | 1,000 queries/month, recurring (recommended) |
| **Serper** | `SERPER_API_KEY` | 2,500 queries, one-time (Google results) |
| **Brave**  | `BRAVE_API_KEY`  | only if you still have a key |

Force a specific one with `SEARCH_PROVIDER=tavily|serper|brave`.

## Setup

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium      # first run only

# Set ONE of these (free signup, no card):
export TAVILY_API_KEY=your_key_here        # https://app.tavily.com  (1k/mo)
# export SERPER_API_KEY=your_key_here      # https://serper.dev      (2.5k once)
# export BRAVE_API_KEY=your_key_here
```

## Usage

```bash
# Dry run: search only, print discovered course-page URLs (no download)
python -m slidegrab.cli run --region india --dry-run

# Download decks for Indian universities, then global
python -m slidegrab.cli run --region india
python -m slidegrab.cli run --region global

# Rebuild the index from the existing dataset/ (no search/network)
python -m slidegrab.cli index
```

Useful flags: `--limit-unis N`, `--max-queries N`, `--max-pages N`,
`--per-course-cap N`.

## How it works

1. `coursecodes.py` discovers each university's course codes by searching its
   catalog pages and extracting codes (CS229, EE2703, 6.036, 11-785, COL774…);
   `topics.yaml` seeds are merged in. Disable with `--no-discover-codes`.
2. `queries.py` builds keyword queries per (university × discovered code / topic).
3. `search.py` calls a web-search API (Tavily/Serper/Brave; on-disk cached, rate-limited).
4. `pipeline.py` keeps results that look like course pages.
5. `fetch.py` (Playwright) loads each page — static HTML **and** networkidle
   JS render — and follows one hop to a lectures/schedule page if needed.
6. `extract.py` selects slide links (include/exclude token filter).
7. `download.py` downloads with magic-byte verification, writing to
   `dataset/<uni>/<course>/`, skipping files that already exist.
8. `verify.py` (PyMuPDF) rejects corrupt / text-dense notes PDFs.
9. `store.py` indexes everything in `index.sqlite` and, on startup, scans the
   existing `dataset/` so previously downloaded files are never re-fetched.

## Layout

```
config/
  universities.yaml   # India-first list with aliases + domains
  topics.yaml         # topics, slide phrases, seed course codes
  settings.py
slidegrab/
  search.py queries.py fetch.py extract.py download.py verify.py
  store.py pipeline.py cli.py coursecodes.py
tests/
dataset/              # downloaded decks (preserved)
index.sqlite          # slidegrab index
dataset.old.sqlite    # backup of the previous project's DB
```

## Tests

```bash
pytest -q
```
