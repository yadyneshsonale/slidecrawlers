# ccr_rmp

Get **Rate My Professors** course-level ratings for every populated course in
`dataset_ccr_new.xlsx`.

The single source of truth is `dataset_ccr_new.xlsx` — no other database is read
or joined. The only external access is (a) visiting the `course_slide_links`
URLs and (b) the Rate My Professors GraphQL API.

## Pipeline (each stage is a standalone, resumable script with an `--example` mode)

| Stage | Script | What it does |
|-------|--------|--------------|
| 0 | `stage0_excel_to_db.py` | xlsx → `ccr_rmp.db` `courses` table (skips empty rows, parses the college out of `course_college`) |
| 1 | `stage1_fetch_slide.py` | fetch the slide link → PDF/PPT page image or HTML text |
| 2 | `stage2_extract_instructor.py` | Qwen2.5-VL reads the page → instructor name or NULL |
| 3 | `stage3_rmp_find.py` | find the professor on RMP, scoped to the course's college |
| 4 | `stage4_rmp_ratings.py` | keep only THIS course's ratings (normalized code match), aggregate |
| — | `run.py` | run every course through all stages |

## Setup

```bash
cd ~/ccr_rmp
python3 -m venv --system-site-packages .venv
./.venv/bin/pip install -r requirements.txt
```

The LLM stage needs the Qwen2.5-VL server (started by `RateMySlides/serve_vllm.sh`,
tmux session `rms_vllm`, port 8000).

## Usage

```bash
# Stage 0 — build the DB and preview 5 parsed rows
./.venv/bin/python stage0_excel_to_db.py --example 5

# Each stage supports --example N (preview N courses without touching the run)
./.venv/bin/python stage1_fetch_slide.py --example 3

# Full run (resumable)
./.venv/bin/python run.py --limit 10
```

See `knowledge_base.md` for the design decisions and the reasoning behind them.
