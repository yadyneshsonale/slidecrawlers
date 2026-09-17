# slidefetch

Give it a course-page URL **or just a search term**; it downloads **all the
slide decks (PDF/PPT/PPTX)** for the matching university courses into
`downloads/new/<page-name>/`.

- **Search mode:** type `cs course slides` (or `--disciplines cs,ee,me`) and it
  finds course pages for you -- no URL needed.
- Keeps only **university / course-host** pages; rejects aggregators
  (slideshare, studocu, scribd, youtube, ...) and courses already downloaded.
- **Smart URL reduction:** a deep link like `.../lectures/Lecture1/Lecture1.pdf`
  is reduced to `.../lectures/` so the whole course is pulled, not one file.
- Renders JavaScript when needed (Playwright) so JS-injected links are found.
- Renders client-side **HTML slide decks** (remark.js / reveal.js / impress.js,
  e.g. `keysan.me/.../ee361_intro.html`) to PDF via headless print-to-PDF.
- Keeps real lecture slides; skips notes, homework, solutions, exams, syllabi.
- Verifies each file by magic bytes (won't save HTML error pages as `.pdf`).
- Skips files that already exist (safe to re-run).

## Setup

```bash
cd ~/slidefetch
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium      # first run only
```

## Usage

```bash
# Download every slide deck linked on the page
python -m slidefetch.cli "https://www.cse.iitm.ac.in/~miteshk/CS7015.html"

# Preview first (list links, download nothing)
python -m slidefetch.cli "<url>" --dry-run

# Force JS rendering for a client-side-rendered page
python -m slidefetch.cli "<url>" --render

# Custom output directory
python -m slidefetch.cli "<url>" --out my_slides
```

Files land in `downloads/new/<page-name>/`, named `lecture_NN_<original>.pdf`
when a lecture number can be inferred, otherwise the original filename. Courses
already present in `downloads/` (or `downloads/new/`) are skipped, never
overwritten.

## Search mode (no URL needed)

Instead of pasting a link, search the web and let slidefetch pick the university
courses to download:

```bash
# Search a phrase and download every matching university course's slides
python -m slidefetch.cli --search "cs course slides"

# Search several disciplines at once (cs=computer science, ee=electrical, ...)
python -m slidefetch.cli --disciplines cs,ee,me

# Every built-in discipline; cap how many courses to pull this run
python -m slidefetch.cli --disciplines all --max-courses 30

# Preview the accept/reject decisions and slide links without downloading
python -m slidefetch.cli --search "operating systems lecture slides" --dry-run

# Interactive: just type a URL or a search query at the prompt
python -m slidefetch.cli
```

What happens for each search result:

1. **Reduce** the URL to the course's slide directory --
   `.../lectures/Lecture1/Lecture1.pdf` becomes `.../lectures/`, then the whole
   listing is crawled.
2. **Reject** anything that isn't a university (`.edu`, `.ac.*`, `.edu.*`, a
   known university, or a course host like `github.io`). Pass
   `--strict-university` to drop course hosts too.
3. **Skip** courses already present in `downloads/` or `downloads/new/` --
   existing files are never overwritten.
4. **Download** every slide deck on the remaining pages into
   `downloads/new/<page-name>/`.

Useful flags: `--limit N` (results considered per query, default 20),
`--max-courses N` (courses downloaded per run, default 15),
`--query-template '{name} course lecture slides'`. Search results are cached
under `downloads/.search_cache/` so re-runs stay fast and polite.

### Search backend (Tavily)

Search uses [Tavily](https://tavily.com) first when an API key is available --
it returns clean, relevant course pages and avoids the rate-limiting and
geo-localised junk that scraping DuckDuckGo/Bing suffers from. Provide the key
via the environment (preferred):

```bash
export TAVILY_API_KEY='tvly-...'
python -m slidefetch.cli --disciplines ee,me,ce
```

or per-run with `--tavily-key 'tvly-...'`. Without a key it falls back to
DuckDuckGo over HTTP, then a headless browser.


## Layout

```
slidefetch/
  fetch.py      # Playwright page loader (static + JS render)
  extract.py    # find PDF/PPT/PPTX slide links, filter out notes/hw
  render.py     # render remark/reveal/impress HTML decks to PDF (print-to-PDF)
  download.py   # download + magic-byte verify + dedup
  search.py     # web search (DuckDuckGo HTTP + headless-browser fallback)
  urls.py       # university filter, URL reduction, slug, already-downloaded
  cli.py        # command-line entry point (URL mode + search mode)
tests/
downloads/      # output (created on first run)
```

## Tests

```bash
pytest -q
```
