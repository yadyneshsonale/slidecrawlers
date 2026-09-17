#!/usr/bin/env bash
# Launch the slideratings pipeline in a detached tmux session, logging to work/run.log.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python}"
SESSION="${SESSION:-slideratings}"
LOG="work/run.log"

mkdir -p work

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' already running; attach with: tmux attach -t $SESSION"
    exit 1
fi

tmux new-session -d -s "$SESSION" \
    "$PYTHON -m slideratings.cli crawl-unis 2>&1 | tee $LOG; \
     $PYTHON -m slideratings.cli run 2>&1 | tee -a $LOG"

echo "started tmux session '$SESSION' (log: $LOG)"
echo "attach with: tmux attach -t $SESSION"
