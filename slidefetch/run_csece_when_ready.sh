#!/usr/bin/env bash
# Wait until the web-search backend recovers from rate-limiting, then live-search
# every CS/ECE course fresh (ignoring the cache) and label the single most
# probable slide link via the correct/wrong classifier.
#
# Meant to run inside tmux:  tmux new -d -s csece 'bash ~/slidefetch/run_csece_when_ready.sh'
set -u
cd "$HOME/slidefetch" || exit 1
PY=.venv/bin/python
LOG=csece_run.log

# Health probe: SearXNG (default engines) must return several genuinely
# academic results (>=1 .edu / github). Exits 0 when the engines have recovered.
read -r -d '' PROBE <<'PYEOF'
import sys, requests
try:
    r = requests.get("http://localhost:8080/search",
                     params={"q": "cornell cs2110 lecture slides", "format": "json"},
                     timeout=20).json()
except Exception as e:
    print("probe-error:", e); sys.exit(1)
res = r.get("results", [])
good = [x for x in res if any(t in (x.get("url") or "").lower()
                              for t in (".edu", "github", "github.io"))]
print(f"results={len(res)} academic={len(good)} unresponsive={r.get('unresponsive_engines')}")
sys.exit(0 if (len(res) >= 3 and len(good) >= 1) else 1)
PYEOF

echo "[$(date)] waiting for search engines to recover..." | tee -a "$LOG"
if [ -n "${TAVILY_API_KEY:-}" ] || [ -n "${SERPAPI_API_KEY:-}" ] || [ -n "${SERPAPI_KEY:-}" ]; then
    echo "[$(date)] search API key set (Tavily/SerpAPI) -> using API, no wait needed" | tee -a "$LOG"
else
    until $PY -c "$PROBE" >> "$LOG" 2>&1; do
        echo "[$(date)] still rate-limited; sleeping 15m" >> "$LOG"
        sleep 900
    done
fi
echo "[$(date)] engines healthy -> refreshing CS/ECE rows" | tee -a "$LOG"

# Back up, then clear non-gold CS/ECE rows so every one is re-searched fresh
# (gold correct.txt/wrong.txt courses are kept / abstained by the labeller).
cp -f ccr_labels.db "ccr_labels.db.bak_before_csece_run" 2>/dev/null
$PY - >> "$LOG" 2>&1 <<'PYEOF'
import sqlite3
c = sqlite3.connect("ccr_labels.db")
n = c.execute("SELECT COUNT(*) FROM auto_labels "
              "WHERE source!='gold' AND discipline IN ('cs','ece')").fetchone()[0]
c.execute("DELETE FROM auto_labels WHERE source!='gold' AND discipline IN ('cs','ece')")
c.commit()
print(f"cleared {n} non-gold cs/ece rows for fresh live search")
PYEOF

echo "[$(date)] starting live CS/ECE labeller (--no-cache)" | tee -a "$LOG"
$PY -u ccr_label_auto.py --disciplines cs,ece --no-cache >> "$LOG" 2>&1
echo "[$(date)] DONE" | tee -a "$LOG"
