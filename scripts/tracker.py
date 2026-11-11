"""
Live download tracker: what is done, what is the target so far, speed, and time left.

  python scripts/tracker.py          # live board, refreshes every 30 s (Ctrl+C closes the board, not the downloads)
  python scripts/tracker.py --once   # print it once

It also writes data/raw/status.html (auto-refreshing) so you can watch from your phone:
  python -m http.server 8080 --directory data/raw   ->  Ports tab  ->  open port 8080  ->  status.html

"Known" grows while the downloaders read index pages, so time left is "at least" until the source says final.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
IST = timezone(timedelta(hours=5, minutes=30))  # India time (the Codespace clock is UTC)

SOURCES = [  # name, label, folder to measure speed (only for old progress files), file pattern
    ("davis", "Davis: Tests 1877-2020, ball by ball", "davis", "*.pdf"),
    ("espn_bbb", "ESPN: ball-by-ball 1998-2002", "espn", "*.htm*"),
    ("espn_cards", "ESPN: scorecards 1970-1997", "espn", "*.htm*"),
    ("espn_years", "ESPN: England etc. 1971-2002", "espn", "*.htm*"),
]


def ist(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), IST)


def count(sub, pat):
    d = RAW / sub
    return len([p for p in d.rglob(pat) if p.is_file()]) if d.exists() else 0


def speed(sub, pat, minutes=10):
    """Files written in the last N minutes, per minute (index pages count too: they are requests)."""
    d = RAW / sub
    if not d.exists():
        return 0.0
    cutoff = time.time() - minutes * 60
    n = sum(1 for p in d.rglob(pat) if p.is_file() and p.stat().st_mtime >= cutoff)
    return n / minutes


def bar(frac, width=20):
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full)


def hm(minutes):
    if minutes is None:
        return "—"
    m = int(round(minutes))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def rows():
    out = []
    cs = count("cricsheet", "*")
    out.append({"label": "Cricsheet: Tests + ODIs 2002 on", "done": cs, "known": 4, "state": "done" if cs >= 4 else "not started",
                "speed": None, "left": None, "failed": 0, "final": True})
    kg = count("kaggle/odi", "*.csv")
    out.append({"label": "Kaggle: ODI scorecards 1971-2024", "done": kg, "known": 6, "state": "done" if kg >= 6 else "not started",
                "speed": None, "left": None, "failed": 0, "final": True})
    for name, label, sub, pat in SOURCES:
        f = RAW / f"progress_{name}.json"
        if not f.exists():
            out.append({"label": label, "done": 0, "known": None, "state": "not started", "speed": None, "left": None,
                        "failed": 0, "final": False})
            continue
        try:
            p = json.loads(f.read_text())
        except Exception:
            continue
        stale = time.time() - p.get("updated", 0) > 600
        # each downloader measures its own requests per minute (dead links included, they are in the queue too)
        sp = p["rate"] if "rate" in p else speed(sub, pat)
        left = (p.get("queue", 0) / sp) if sp > 0 and not p.get("finished") else None
        state = "done" if p.get("finished") else ("stopped? run  bash scripts/start.sh" if stale else "running")
        note = None
        if p.get("slowed"):
            note = f"the site asked us to slow down: pause now {p.get('pause', 0):.0f} s (it comes back by itself)"
        done, dead, failed = p.get("done", 0), p.get("dead", 0), p.get("failed", 0)
        out.append({"label": label, "done": done, "checked": done + dead + failed, "known": p.get("known", 0),
                    "state": state, "speed": sp, "left": left, "failed": failed, "final": p.get("finished", False),
                    "pages": p.get("pages_read"), "dead": dead, "note": note})
    out.append(backup_row())
    return out


BACKUP_FOLDERS = ["cricsheet", "kaggle", "davis", "espn"]


def backup_row():
    """How many of the downloaded files already have a safe copy on GitHub (Releases -> raw-data)."""
    total = 0
    for sub in BACKUP_FOLDERS:
        d = RAW / sub
        if d.exists():
            total += sum(1 for p in d.rglob("*") if p.is_file() and not p.name.endswith((".part", ".tmp")))
    row = {"label": "Backup to GitHub (safe copy)", "done": 0, "known": total, "state": "", "speed": None,
           "left": None, "failed": 0, "final": True, "note": None}
    f = RAW / "progress_backup.json"
    try:
        b = json.loads(f.read_text())
    except Exception:
        row["state"] = "NOT RUNNING: run  bash scripts/start.sh"
        return row
    row["done"] = min(b.get("saved", 0), total) if total else b.get("saved", 0)
    last = ist(b["last"]).strftime("%H:%M") if b.get("last") else "not yet"
    if time.time() - b.get("updated", 0) > 25 * 60:
        row["state"] = "STOPPED? run  bash scripts/start.sh"
    elif b.get("working"):
        row["state"] = "saving to GitHub now"
    else:
        row["state"] = f"last saved {last}"
    if b.get("error"):
        row["note"] = "PROBLEM: " + b["error"]
    else:
        row["note"] = f"{b.get('parts', 0)} part(s) on GitHub · new files are saved every 20 min"
    return row


def details(r):
    """The small facts under each bar."""
    extra = []
    if "checked" in r:
        extra.append(f"{r['done']:,} files saved")
    if r.get("dead"):
        extra.append(f"{r['dead']:,} dead links (not on the site)")
    if r.get("failed"):
        extra.append(f"{r['failed']} failed (retried later)")
    if r.get("speed"):
        extra.append(f"{r['speed']:.0f}/min")
    if r.get("left") is not None:
        extra.append(("~" if r["final"] else "at least ") + hm(r["left"]) + " left")
    if r.get("pages"):
        extra.append(f"{r['pages']:,} index pages read")
    if r.get("note"):
        extra.append(r["note"])
    return extra


def wrap(items, width=60, indent="  "):
    lines, cur = [], ""
    for it in items:
        joined = it if not cur else cur + " · " + it
        if cur and len(indent + joined) > width:
            lines.append(indent + cur)
            cur = it
        else:
            cur = joined
    if cur:
        lines.append(indent + cur)
    return lines


def figures(r):
    known, checked = r["known"], r.get("checked", r["done"])
    frac = (checked / known) if known else 0
    target = (f"{checked:,} / {known:,}" + ("" if r["final"] else "+")) if known else f"{checked:,} / ?"
    return frac, target


def text(rs):
    """Narrow layout (fits a half-width terminal): name line, bar line, details."""
    lines = [f"EVERYBALL DOWNLOADS   {ist():%H:%M:%S} India time",
             "(refreshes every 30 s · Ctrl+C closes the board only)", ""]
    for r in rs:
        frac, target = figures(r)
        lines.append(f"{r['label']}   [{r['state']}]")
        lines.append(f"  {bar(frac)} {frac * 100:5.1f}%  {target}")
        lines += wrap(details(r))
        lines.append("")
    lines += ["'+' = still finding links, so the total can grow.",
              "Safe copy: GitHub -> your Everyball repo -> Releases -> raw-data.",
              "Stopped? Run: bash scripts/start.sh"]
    return "\n".join(lines)


def html(rs):
    cards = []
    for r in rs:
        frac, target = figures(r)
        pct = frac * 100
        info = details(r)
        cards.append(f"""<div class="c"><b>{r['label']}</b><div class="b"><i style="width:{pct:.1f}%"></i></div>
<div>{target} · {pct:.1f}% · {r['state']}</div>
<small>{' · '.join(info)}</small></div>""")
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Everyball downloads</title>
<style>body{{font:15px system-ui;margin:16px;background:#0f172a;color:#e2e8f0}}.c{{background:#1e293b;padding:12px;border-radius:10px;margin:10px 0}}
.b{{height:10px;background:#334155;border-radius:5px;margin:8px 0}}.b i{{display:block;height:100%;background:#22c55e;border-radius:5px}}small{{color:#94a3b8}}</style>
</head><body><h2>Everyball downloads</h2><small>Updated {ist():%H:%M:%S} India time · refreshes every 30 s ·
"+" means still finding files</small>{''.join(cards)}</body></html>"""


def main():
    once = "--once" in sys.argv
    while True:
        rs = rows()
        board = text(rs)
        try:
            (RAW / "status.html").write_text(html(rs), encoding="utf-8")
        except Exception:
            pass
        if once:
            print(board)
            return
        print("\033[2J\033[H" + board, flush=True)
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
