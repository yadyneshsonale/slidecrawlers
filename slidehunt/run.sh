#!/usr/bin/env bash
# Launch the slidehunt pipeline in a detached tmux session, logging to work/run.log.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python}"
SESSION="${SESSION:-slidehunt}"
TARGET="${TARGET:-500}"
LOG="work/run.log"

mkdir -p work

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session '$SESSION' already running; attach with: tmux attach -t $SESSION"
    exit 1
fi

tmux new-session -d -s "$SESSION" \
    "$PYTHON -m slidehunt.cli run --until-downloaded $TARGET 2>&1 | tee $LOG"

echo "started tmux session '$SESSION' (log: $LOG)"
echo "attach with: tmux attach -t $SESSION"
echo "tail with:   tail -f $(pwd)/$LOG"
