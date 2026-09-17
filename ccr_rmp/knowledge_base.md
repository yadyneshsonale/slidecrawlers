# Knowledge Base — ccr_rmp

A living log of the design decisions for this pipeline and **the reasoning behind
them**. Update this whenever a step is approved or changed, so future iterations
build on the same reasoning. Each stage below records: what it does, the decision
made, and *why* (the user's reasoning).

---

## Source data facts (dataset_ccr_new.xlsx)

- 998 rows total; **only 122 rows are populated** — the other 876 are empty
  padding rows and are skipped.
- Columns (4): `course_college`, `course_code`, `num_ratings`, `course_slide_links`.
- There is **no separate college column** and **no instructor column**.
- `course_college` is formatted `"<code> - <College>"` (e.g. `"CSC476 - American University"`);
  the **university is parsed from here** (right of the ` - `). 39 distinct colleges.
- `course_code` is usually clean but occasionally contains the full `"<code> - <College>"`
  string; it is cleaned by taking the part before ` - `.
- `num_ratings` is a float-like string (`"6.0"`) or empty.
- `course_slide_links` is a single URL. Kinds among the 122: 70 university/HTML
  pages, 30 GitHub pages/repos, 12 direct PDFs, 8 PowerPoint, 2 GitHub blob PDFs.
- Some links are **wrong in the source** (e.g. an ASU course points to a CalPoly
  or UW page). Per the rule below, links are used as-is; such rows will mismatch
  on RMP and get flagged `needs_review`.

---

## Locked decisions (2026-07-01)

| # | Decision | Choice | Reasoning (user) |
|---|----------|--------|------------------|
| 1 | Source of truth | `dataset_ccr_new.xlsx` **only** — never read/join `ccr_courses.db` or any other DB | "all the data is from the [xlsx] ONLY and nowhere else" |
| 2 | University source | Parse `college_name` out of `course_college` (not scraped) | University is already known from the data, so we only need to extract the instructor. |
| 3 | Instructor extraction | **Qwen2.5-VL LLM only.** Strict JSON output, returns `null` when no instructor is present; prompt made very robust | User: use the local VLM to read the page / first PDF page, return NULL if absent, make it robust. |
| 4 | Find professor on RMP | **RMP GraphQL API directly**, search scoped to the course's college (school lookup → schoolID → teacher search) | Reliability over scraping search-result HTML. |
| 5 | Pick the course's ratings | **Normalized-digit match** of `course_code` against RMP's free-text `class` field | RMP `class` is free text with spelling variants; digit matching catches them. |
| 6 | Code location | New **top-level** folder `ccr_rmp/` (own venv, copied helper patterns) | Clean separation from the other projects. |

### Refinements derived from the data
- Because the university is always known (decision 2), the Step-2 "no instructor
  **and** uni" NULL rule reduces to: **no instructor found → save row with
  `instructor = NULL`, skip RMP, move to the next course.**
- Handle 1-or-many URLs in the `course_slide_links` cell (try each until an
  instructor is found).

---

## Stage decisions (filled in as each stage is approved)

### Stage 0 — Excel → DB
- Reads the xlsx with a dependency-free stdlib reader; skips the 876 empty rows;
  parses `college_name` from `course_college`; cleans `course_code`; stores
  `num_ratings` as a number.
- STATUS: **built + verified.** `ccr_rmp.db` holds 122 courses, 122 with links,
  39 colleges. Link kinds: 70 html, 29 gh_page, 12 pdf, 10 ppt, 1 gh_blob.
- Your reasoning / changes: _(to be filled in)_

### Stage 1 — Fetch slide link
- Splits the cell into URL(s) and tries each until one yields content.
- Rewrites GitHub `blob`/`raw` links to `raw.githubusercontent.com`.
- Classifies by body/`Content-Type` (not just extension), producing ONE of:
  - **image artifact** — PDF rendered with poppler, PPT/PPTX via LibreOffice →
    poppler; first `max_pdf_pages` (=2) pages, downscaled to 1400px JPEG. For the
    vision model.
  - **text artifact** — HTML visible text via BeautifulSoup (GitHub repo pages
    use `article.markdown-body`); truncated to `max_html_chars` (=6000). For the
    text model.
- Artifacts cached under `work/<course_college>/`; `slide_artifact` = JSON list
  of image paths (image kinds) or the `page.txt` path (html).
- STATUS: **built + verified** on a 6-course spread (html / pdf-from-blob / ppt /
  direct pdf all `ok`; a rendered slide confirmed legible).
- Open note: for lecture-slide PDFs the first pages are often a title slide, so
  the instructor may not appear in the 2-page budget → Stage 2 will return NULL
  for those (matches the agreed "no instructor → NULL" rule). We can raise the
  page budget or also try a syllabus link if you want better recall.
- Your reasoning / changes: _(to be filled in)_

### Stage 2 — Extract instructor (LLM)
- **Approved approach: "smarter pick" (option 3).** For PDF/PPT we read every
  page's text with poppler (`pdftotext`), score pages for instructor cues
  (`instructor|professor|lecturer|taught by|...`), and send only the **title page
  + top cue pages** (≤ `max_vlm_pages`=3) to Qwen2.5-VL. For HTML we send a
  focused text window (page top + a window around the first cue).
- Robust prompt: strict JSON `{"instructor": name|null, "confidence": 0-1}`,
  returns `null` when absent, ignores TAs / paper authors / textbook authors /
  department & university names, drops titles/degrees, never guesses from the URL
  or course code.
- Output validated (`clean_name`): strips honorifics/degrees, needs 2–5 tokens,
  rejects stopwords/digits/the course code, supports co-taught `A & B`.
- Debug (pages sent, cue pages, rendered images, raw reply) is stored in
  `instructor_debug` so the review app can show exactly what the model saw.
- STATUS: **built + run over all fetched courses.** Result after refinements:
  **61 found, 48 NULL, 2 error** (of 111 fetched).
- **Refinement 1 — send text alongside images (feedback CSE220).** The cue
  detection found the right page, but Qwen misread the *rendered image* of small
  text and returned null. Fix: in the image branch we now also pass the exact
  `pdftotext` snippet of each sent page (1200 chars/page) next to the images, so
  the model reads real text, not OCR of a screenshot. Verified: CSE220's page-2
  “Faculty Name” → `Sk. Sabit Bin Mosaddek` (was null). Recovered 3 more courses.
- **Refinement 2 — recognise “Faculty” cues (feedback CSE220).** `CUE_RE` and the
  system prompt now treat *Faculty / Faculty Name / teaching staff / course staff
  / instructed by / presented by* as instructor labels.
- **Refinement 3 — save co-instructors separately (feedback CSE325).**
  `codes.split_instructors` splits on `&`, `;`, `/`, `+`, and standalone `and`
  (never on comma, which can appear inside a single name). Stage 3 then stores
  **one `course_professors` row per co-instructor**.
- Spot checks good (CS310 “Dr. Mudassir Shabbir” → `Mudassir Shabbir`; CSE240 →
  `Javier Gonzalez-Sanchez`; CSE220 → `Sk. Sabit Bin Mosaddek`).
- Acceptable NULLs confirmed by you: CIS9002 (link has no slides), CS303 (deck
  has no instructor name).
- Your reasoning / changes: _(to be filled in — use the review app)_

### Stage 3 — Find professor on RMP
- **RMP GraphQL client** (`src/rmp_api.py`), public site token `Basic test:test`.
- **School lookup:** `newSearch.schools(query:{text})` → pick the school whose
  name best matches the parsed college (exact → token-subset → abbrev), cached in
  `school_cache` so each college is resolved once. **Key gotcha:** the teacher
  search's `schoolID` must be the **base64 GraphQL node id** (e.g.
  `U2Nob29sLTYw` = “School-60”), *not* the numeric `legacyId`.
- **Teacher search:** `newSearch.teachers(query:{text, schoolID}, first)` scoped
  to that school, so a surname is usually unique.
- **Match verification (`pick_match`):** the RMP surname must equal the queried
  surname (accent-folded, so `Müller`≡`Muller`); if several share it, narrow by
  given name / initial, else take the most-rated namesake and flag `ambiguous`.
  A wrong surname → `not_found` (verified: Andreas C. Müller correctly rejected at
  Columbia — he simply isn't rated there).
- **Storage:** one row per (course, instructor) in `course_professors`
  (status, match_type, school, legacy_id, matched_name, profile_url, department,
  overall quality/difficulty/n/would-take-again). The **primary** match is
  mirrored into `courses.rmp_*` for the dashboard.
- STATUS: **built + run.** 61 found-instructor courses → **45 matched on RMP,
  16 not found.** Spot checks: Xiao Qin (Auburn) 4.3/161; Julie Thornton
  (Kansas State) 4.7/53; Michael Whiteman (Baruch) 5.0/6.
- Your reasoning / changes: _(to be filled in — use the review app)_

### Stage 4 — Course-specific ratings
- For each matched professor, paginate **all** their ratings
  (`node(id).ratings`, 20/page) and keep only those whose free-text `class`
  maps to the course code via `codes.class_matches` (exact normalized-alnum, or
  same digits with compatible letters — so `476`/`CSC476` match CSC476 but
  `MAT476` and `CISC3115` vs `CISC1115` do not).
- Kept ratings are stored in `course_ratings_raw`; per-professor aggregates
  (kept n, avg quality, avg difficulty, would-take-again %, matched class list)
  go into `course_professors`, and the course-level roll-up into `courses.course_*`.
- **Per-rating detail (your request):** every individual kept rating stores
  quality, difficulty, clarity, helpful, date, class, grade, **for-credit**,
  **attendance** (mandatory / non mandatory), **online**, **textbook use**,
  would-take-again, thumbs up/down, tags (`--` split into a list) and the full
  comment. Field names confirmed by probing the RMP API
  (`attendanceMandatory`, `isForCredit`, `isForOnlineClass`, `textbookUse`;
  `""`/`-1`/`null` are normalised to NULL). The review app shows each rating as a
  card; the CLI `--example` prints a 3-rating sample.
- STATUS: **built + run.** 45 matched courses → **319 course-specific ratings**
  kept. Many graduate/low-volume courses legitimately keep 0 (the professor is
  rated, but none of their ratings are tagged with that exact course number —
  e.g. Hui Chen has CISC3115/3171 ratings but none for CISC1115).
- Your reasoning / changes: _(to be filled in — use the review app)_

---

## Interactive review app
- `review_app.py` (Flask) — a local UI at `http://127.0.0.1:5057`.
- Dashboard lists all 122 courses with slide/instructor/RMP/ratings status and
  filters (found / NULL / fetch-failed / needs-review).
- Per-course page shows the source link, the fetched slide (preview images or the
  extracted text), the instructor **with the exact pages/text sent to the model
  and the raw reply**, the **Stage 3 professor table** (one row per co-instructor
  with matched name / type / overall stats / profile link) and the **Stage 4
  course-ratings table** (kept n, quality, difficulty, would-take-again %,
  matched classes).
- Buttons **Run Stage 1 / 2 / 3 / 4** re-process that one course live.
- Feedback form saves to a `feedback` table: instructor correct? correct name
  (optionally applied as the instructor, feeding Stage 3), RMP correct?, needs
  review, and free-text notes/reasoning for this knowledge base.
- Security: the `/artifact` route only serves files under `work/` (path-traversal
  attempts return 403).
