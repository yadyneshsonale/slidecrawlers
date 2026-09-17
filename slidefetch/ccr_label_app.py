#!/usr/bin/env python3.11
"""Interactive labelling window (local web app) for slide-link patterns.

This is a headless-friendly "interactive window": a tiny stdlib web server you
open in your own browser (VS Code forwards the port automatically). For each CS
example (>= 4 ratings) it gives you:

  * an **Open in Google** button -> the real Google results for
    ``"<course_college> course slides"`` in a new browser tab (human in the
    loop -- no scraping, no captchas),
  * auto-suggested candidate links (from the pipeline's DuckDuckGo searcher),
    each scored by the resolver's pattern rules and click-to-fill,
  * a form to record the slide link you chose and to TYPE YOUR REASON.

Everything is saved to ``ccr_labels.db`` and exported to
``ccr_labels_review.md`` after every save, so your reasoning is captured even if
you stop early.

    python3.11 ccr_label_app.py            # serves http://127.0.0.1:8765
    python3.11 ccr_label_app.py --port 9000
    python3.11 ccr_label_app.py --all      # walk all CS>=4 courses, not just the 12

Then open the printed URL (VS Code: the forwarded port shows a toast / the Ports
panel).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from ccr_pattern_examples import EXAMPLES, search20, verify_cs_4ratings
from ccr_slide_resolve import _univ_tokens, code_variants, score_link

# --------------------------------------------------------------------------- #
# State (populated in main)
# --------------------------------------------------------------------------- #
COURSES_DB = "ccr_courses.db"
LABELS_DB = "ccr_labels.db"
REVIEW_MD = "ccr_labels_review.md"
# items: list of {cc, ratings, chosen, my_read}
ITEMS: list[dict] = []
_SESSION = requests.Session()


def init_labels_db() -> sqlite3.Connection:
    conn = sqlite3.connect(LABELS_DB, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS labels (
            course_college  TEXT PRIMARY KEY,
            chosen_url      TEXT,
            reason          TEXT,
            candidates_json TEXT,
            saved_at        TEXT
        )
        """
    )
    conn.commit()
    return conn


_DB = None  # set in main


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def google_url(cc: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote(
        f"{cc} course slides")


def candidates_for(cc: str, refresh: bool = False) -> list[dict]:
    """Return scored auto-suggested links, cached in the labels DB."""
    if not refresh:
        row = _DB.execute(
            "SELECT candidates_json FROM labels WHERE course_college=?",
            (cc,)).fetchone()
        if row and row[0]:
            try:
                cached = json.loads(row[0])
                if cached:
                    return cached
            except Exception:  # noqa: BLE001
                pass
    code = cc.split(" - ", 1)[0]
    college = cc.split(" - ", 1)[1] if " - " in cc else ""
    variants, num = code_variants(code)
    univ_toks = _univ_tokens(college)
    urls = search20(_SESSION, f"{cc} course slides", want=20)
    out: list[dict] = []
    for rank, url in enumerate(urls, 1):
        sc, reason = score_link(url, variants, num, univ_toks)
        out.append({"rank": rank, "url": url,
                    "score": (None if sc < 0 else sc),
                    "reject": sc < 0, "reason": reason})
    # cache without clobbering an existing label
    _DB.execute(
        "INSERT INTO labels (course_college, candidates_json, saved_at) "
        "VALUES (?,?,?) ON CONFLICT(course_college) DO UPDATE SET "
        "candidates_json=excluded.candidates_json",
        (cc, json.dumps(out), _now()))
    _DB.commit()
    return out


def export_markdown() -> None:
    rows = _DB.execute(
        "SELECT course_college, chosen_url, reason FROM labels "
        "WHERE chosen_url IS NOT NULL AND chosen_url<>'' ORDER BY course_college"
    ).fetchall()
    lines = ["# Your slide-link labels", ""]
    for cc, url, reason in rows:
        lines += [f"## {cc}", f"- chosen: {url}", f"- reason: {reason or ''}", ""]
    with open(REVIEW_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# HTML shell (static; all data loaded via JSON endpoints to avoid templating)
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slide-link labelling</title>
<style>
  :root{--bg:#0f1115;--card:#181b22;--ink:#e6e6e6;--mut:#9aa4b2;--ok:#3fb950;--bad:#f85149;--acc:#58a6ff}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
  header{position:sticky;top:0;background:#10131a;border-bottom:1px solid #232831;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  header b{font-size:16px}
  .wrap{max-width:1000px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid #232831;border-radius:10px;padding:16px;margin-bottom:16px}
  .mut{color:var(--mut)}
  a{color:var(--acc)}
  button{background:#21262d;color:var(--ink);border:1px solid #30363d;border-radius:7px;padding:8px 12px;cursor:pointer;font-size:14px}
  button:hover{border-color:#58a6ff}
  .g{background:#1a73e8;border-color:#1a73e8;color:#fff;font-weight:600}
  .g:hover{background:#1666cf}
  .nav{margin-left:auto;display:flex;gap:8px;align-items:center}
  .pill{font-size:12px;padding:2px 8px;border-radius:20px;background:#21262d;border:1px solid #30363d}
  ul.cands{list-style:none;padding:0;margin:0}
  ul.cands li{display:flex;gap:10px;align-items:flex-start;padding:8px;border-bottom:1px solid #20242c}
  .score{min-width:62px;text-align:center;border-radius:6px;padding:2px 6px;font-weight:700}
  .s-ok{background:#13351c;color:var(--ok)}
  .s-bad{background:#3a1416;color:var(--bad)}
  .curl{word-break:break-all}
  .rsn{color:var(--mut);font-size:12.5px}
  input[type=text],textarea{width:100%;background:#0d1117;color:var(--ink);border:1px solid #30363d;border-radius:7px;padding:9px;font:14px/1.4 inherit}
  textarea{min-height:72px;resize:vertical}
  label{display:block;margin:10px 0 4px;color:var(--mut)}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .saved{color:var(--ok)}
  .choose{white-space:nowrap}
  .myread{border-left:3px solid #30363d;padding-left:10px}
</style></head>
<body>
<header>
  <b>Slide-link labelling</b>
  <span class="pill" id="progress">…</span>
  <div class="nav">
    <button onclick="go(-1)">&larr; Prev</button>
    <span id="counter" class="mut"></span>
    <button onclick="go(1)">Next &rarr;</button>
  </div>
</header>
<div class="wrap">
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <div><b id="cc">…</b> <span class="pill" id="ratings"></span></div>
      <a id="glink" target="_blank" rel="noopener"><button class="g">Open in Google ↗</button></a>
    </div>
    <p class="mut" id="myread" style="margin:8px 0 0"></p>
  </div>

  <div class="card">
    <div class="row" style="justify-content:space-between">
      <b>Auto-suggested links (DuckDuckGo, scored by pattern rules)</b>
      <button onclick="loadCands(true)">↻ Refresh</button>
    </div>
    <p class="mut" id="candnote">loading…</p>
    <ul class="cands" id="cands"></ul>
  </div>

  <div class="card">
    <label for="chosen">Chosen slide link</label>
    <input type="text" id="chosen" placeholder="paste the slide page / deck URL you picked">
    <label for="reason">YOUR REASON — why this link? (type it out)</label>
    <textarea id="reason" placeholder="e.g. faculty ~user page on the .edu; course code + term + 'lectures' in the path; ignored studocu/coursehero; had to append /lectures/ to the homepage"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="g" onclick="save()">Save</button>
      <span id="status" class="saved"></span>
    </div>
  </div>
</div>
<script>
let EX=[], i=0;
async function boot(){
  EX = await (await fetch('/api/examples')).json();
  const q = new URLSearchParams(location.search); i = Math.max(0, Math.min(EX.length-1, (parseInt(q.get('i'))||1)-1));
  render();
}
function go(d){ i=Math.max(0,Math.min(EX.length-1,i+d)); history.replaceState({},'', '?i='+(i+1)); render(); }
async function render(){
  const e = EX[i];
  document.getElementById('progress').textContent = 'CS \u2265 4 ratings';
  document.getElementById('counter').textContent = (i+1)+' / '+EX.length;
  document.getElementById('cc').textContent = e.cc;
  document.getElementById('ratings').textContent = 'ratings: '+e.ratings;
  document.getElementById('glink').href = e.google_url;
  document.getElementById('myread').innerHTML = e.my_read ? ('<span class="myread"><b>my read:</b> '+escape_(e.my_read)+(e.chosen?'<br><b>method.txt pick:</b> '+escape_(e.chosen):'')+'</span>') : '';
  const lab = await (await fetch('/api/label?cc='+encodeURIComponent(e.cc))).json();
  document.getElementById('chosen').value = lab.chosen_url || e.chosen || '';
  document.getElementById('reason').value = lab.reason || '';
  document.getElementById('status').textContent = lab.saved_at ? ('saved '+lab.saved_at) : '';
  loadCands(false);
}
function escape_(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
async function loadCands(refresh){
  const e = EX[i];
  const ul = document.getElementById('cands'); ul.innerHTML='';
  document.getElementById('candnote').textContent = refresh?'re-searching…':'loading…';
  const data = await (await fetch('/api/candidates?cc='+encodeURIComponent(e.cc)+(refresh?'&refresh=1':''))).json();
  document.getElementById('candnote').textContent = data.candidates.length ? '' : 'no results (use the Google button)';
  for(const c of data.candidates){
    const li=document.createElement('li');
    const sc=document.createElement('div');
    sc.className='score '+(c.reject?'s-bad':'s-ok');
    sc.textContent=c.reject?'REJECT':('+'+c.score);
    const mid=document.createElement('div'); mid.style.flex='1';
    mid.innerHTML='<div class="curl"><a href="'+c.url+'" target="_blank" rel="noopener">'+escape_(c.url)+'</a></div>'+(c.reject?'':'<div class="rsn">'+escape_(c.reason)+'</div>');
    const btn=document.createElement('button'); btn.className='choose'; btn.textContent='use this';
    btn.onclick=()=>{document.getElementById('chosen').value=c.url;};
    li.append(sc,mid,btn); ul.append(li);
  }
}
async function save(){
  const e=EX[i];
  const body={cc:e.cc, chosen:document.getElementById('chosen').value.trim(), reason:document.getElementById('reason').value.trim()};
  const r=await (await fetch('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('status').textContent='saved '+r.saved_at;
}
boot();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif u.path == "/api/examples":
            self._json([
                {"cc": it["cc"], "ratings": it["ratings"], "chosen": it["chosen"],
                 "my_read": it["my_read"], "google_url": google_url(it["cc"])}
                for it in ITEMS
            ])
        elif u.path == "/api/candidates":
            cc = q.get("cc", [""])[0]
            refresh = q.get("refresh", ["0"])[0] == "1"
            self._json({"cc": cc, "google_url": google_url(cc),
                        "candidates": candidates_for(cc, refresh=refresh)})
        elif u.path == "/api/label":
            cc = q.get("cc", [""])[0]
            row = _DB.execute(
                "SELECT chosen_url, reason, saved_at FROM labels "
                "WHERE course_college=?", (cc,)).fetchone()
            self._json({"chosen_url": row[0] if row else "",
                        "reason": row[1] if row else "",
                        "saved_at": row[2] if row else ""})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/api/save":
            self._json({"error": "not found"}, 404)
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            self._json({"error": "bad json"}, 400)
            return
        cc = data.get("cc", "")
        ts = _now()
        _DB.execute(
            "INSERT INTO labels (course_college, chosen_url, reason, saved_at) "
            "VALUES (?,?,?,?) ON CONFLICT(course_college) DO UPDATE SET "
            "chosen_url=excluded.chosen_url, reason=excluded.reason, "
            "saved_at=excluded.saved_at",
            (cc, data.get("chosen", ""), data.get("reason", ""), ts))
        _DB.commit()
        export_markdown()
        self._json({"ok": True, "saved_at": ts})


def build_items(use_all: bool) -> list[dict]:
    eligible = verify_cs_4ratings(COURSES_DB)
    # Reasons/picks the user has already stated, keyed by course.
    stated = {cc: (chosen, why) for cc, chosen, why in EXAMPLES}
    items: list[dict] = []
    if use_all:
        # Golden examples first (with their stated reason prefilled), then the
        # rest of the CS>=4 courses alphabetically.
        for cc, chosen, why in EXAMPLES:
            if cc in eligible:
                items.append({"cc": cc, "ratings": eligible[cc],
                              "chosen": chosen, "my_read": why})
        seen = {it["cc"] for it in items}
        for cc in sorted(eligible):
            if cc in seen:
                continue
            chosen, why = stated.get(cc, ("", ""))
            items.append({"cc": cc, "ratings": eligible[cc],
                          "chosen": chosen, "my_read": why})
    else:
        for cc, chosen, why in EXAMPLES:
            items.append({"cc": cc, "ratings": eligible.get(cc),
                          "chosen": chosen, "my_read": why})
    return items


def main() -> None:
    global COURSES_DB, ITEMS, _DB
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--courses-db", default="ccr_courses.db")
    ap.add_argument("--all", action="store_true",
                    help="walk every CS>=4 course, not just the 12 examples")
    args = ap.parse_args()

    COURSES_DB = args.courses_db
    _DB = init_labels_db()
    ITEMS = build_items(args.all)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"labelling window -> {url}")
    print(f"  {len(ITEMS)} course(s); saves to {LABELS_DB} + {REVIEW_MD}")
    print("  (VS Code forwards the port; open the URL in your browser. Ctrl-C to stop.)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
