"""
Catch what's still missing. Two jobs, each safe to stop (Ctrl+C) and run again: it carries on where it stopped.

  python scripts/catch_missing.py espn     # every Test/ODI we have no ball-by-ball for: does ESPNcricinfo's site
                                           # have commentary for it now (some old matches were recreated later)?
  python scripts/catch_missing.py davis    # ask Davis's site once more for the dead links that could matter
                                           # (Test pages and ball-by-ball files of Tests we lack), at
                                           # under 300 a minute (Davis's own limit)
  python scripts/catch_missing.py report   # what was found

ESPN: one request per match (the old address /ci/engine/match/<id>.html leads to the match's page, whose built-in
data says hasCommentary and where the ball-by-ball came from). About 2,300 matches, at most ~900 a minute (within
the 1,000 a minute ESPNcricinfo allowed us): about 3-4 minutes. Pages of matches that have commentary are saved in
data/raw/espn_live/, ready for the next step (downloading the full commentary).
"""
import csv
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import download as D  # noqa: E402  (same polite session, folders and helpers as the downloader)

RAW = D.RAW
DB = RAW.parent / "everyball.db"
OUT = RAW / "espn_live"
RESULTS = OUT / "check.csv"
FIELDS = ["format", "number", "espn_id", "start_date", "teams", "status", "has_commentary", "ball_by_ball_source",
          "commentary_source", "series_id", "url"]
PER_MINUTE = 900          # ESPNcricinfo allowed us up to 1,000 a minute
DAVIS_PER_MINUTE = 280    # Charles Davis allowed up to 300 a minute
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36 Everyball (with ESPNcricinfo's permission)",
           "Accept-Language": "en-US,en;q=0.9"}


# ---------------------------------------------------------------- ESPN
def espn_ids():
    """(format, number) -> ESPN match id, from ESPN's own year lists already saved by the build."""
    ids = {}
    for f in (RAW / "espn_lists").glob("*_results.html"):
        fmt = f.name.split("_")[0]
        html = f.read_text(encoding="utf-8", errors="replace")
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
            n = re.search((r"Test" if fmt == "Test" else r"ODI") + r"\s*#\s*(\d+)", re.sub(r"<[^>]+>", " ", tr))
            m = re.search(r"/ci/engine/match/(\d+)\.html", tr)
            if n and m:
                ids[(fmt, int(n.group(1)))] = m.group(1)
    return ids


def targets():
    """Every Test and ODI with no ball-by-ball in our database (and ESPN's matches we don't have at all)."""
    con = sqlite3.connect(DB)
    have = {(f, n): bb for f, n, bb in con.execute("SELECT format, number, ball_source FROM matches")}
    ref = con.execute("SELECT format, number, start_date, team1 || ' v ' || team2 FROM ref_matches").fetchall()
    con.close()
    return [(f, n, d, t) for f, n, d, t in ref if not have.get((f, n))]


def page_info(html):
    """hasCommentary etc. from the page's built-in data (__NEXT_DATA__)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    d = json.loads(m.group(1))
    props = d.get("props", {})
    data = (props.get("appPageProps") or props.get("pageProps") or {}).get("data") or {}
    data = data.get("data", data)
    match = data.get("match") or {}
    if not match:
        return None
    comments = ((data.get("content") or {}).get("comments")) or []
    return {"has_commentary": bool(match.get("hasCommentary")) or bool(comments),
            "ball_by_ball_source": match.get("ballByBallSource"), "commentary_source": match.get("commentarySource"),
            "series_id": (match.get("series") or {}).get("objectId")}


class Pace:
    def __init__(self, per_minute):
        self.gap, self.next, self.lock = 60.0 / per_minute, time.time(), threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


def check_one(row, pace, session):
    fmt, number, espn_id, date, teams = row
    url = f"https://www.espncricinfo.com/ci/engine/match/{espn_id}.html"
    for attempt in range(4):
        pace.wait()
        try:
            r = session.get(url, timeout=40, headers=BROWSER)
        except Exception:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code in (403, 429):
            time.sleep(20 * (attempt + 1))        # refused: slow down, ask again
            continue
        if r.status_code in (404, 410):
            return {"status": "not found", "url": url}
        if r.status_code != 200:
            time.sleep(5 * (attempt + 1))
            continue
        info = page_info(r.text)
        if info is None:
            return {"status": "page without match data", "url": r.url}
        if info["has_commentary"]:
            D.save(OUT / f"{fmt}_{number:04d}_{espn_id}.html", r.content)
        return {"status": "ok", "url": r.url, **info}
    return {"status": "refused or failed", "url": url}


def espn():
    OUT.mkdir(parents=True, exist_ok=True)
    ids = espn_ids()
    done = set()
    if RESULTS.exists():
        done = {(r["format"], int(r["number"])) for r in csv.DictReader(RESULTS.open(encoding="utf-8"))
                if r["status"] in ("ok", "not found")}
    todo = [(f, n, ids[(f, n)], d, t) for f, n, d, t in targets() if (f, n) in ids and (f, n) not in done]
    D.log(f"== ESPN check: {len(todo):,} matches without ball-by-ball to ask about ({len(done):,} already checked)")
    if not todo:
        return report()
    pace, lock, found, n_done = Pace(PER_MINUTE), threading.Lock(), 0, 0
    new_file = not RESULTS.exists()
    f = RESULTS.open("a", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new_file:
        w.writeheader()

    def job(row):
        nonlocal found, n_done
        res = check_one(row, pace, D.S)
        with lock:
            w.writerow({"format": row[0], "number": row[1], "espn_id": row[2], "start_date": row[3], "teams": row[4],
                        **{k: res.get(k) for k in FIELDS[5:]}})
            f.flush()
            n_done += 1
            found += bool(res.get("has_commentary"))
            if n_done % 100 == 0:
                D.log(f"   ...{n_done:,}/{len(todo):,} checked, {found} with commentary so far")
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(job, todo))
    finally:
        f.close()
    report()


# ---------------------------------------------------------------- Davis
def davis():
    """Dead links that could hide data we lack: Test pages and ball-by-ball files of Tests without them.
    Each is asked once more, slowly. Found ones are saved where the downloader would have put them."""
    dead_file = RAW / "dead_davis.txt"
    if not dead_file.exists():
        D.log("No data/raw/dead_davis.txt: nothing to re-check")
        return
    dead = [x.strip() for x in dead_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    root = RAW / "davis"
    have = {p.name.lower() for p in root.rglob("*.pdf")}
    keep = []
    for key in dead:
        name = key.rsplit("/", 1)[-1]
        m = re.match(r"^(\d{4}[A-Z]+)(\d+)(bbb\d*)?\.pdf$", name)
        if not m or int(m.group(2)) == 50:
            continue                             # covers, averages, "Scores of 50+": nothing to gain
        page = f"{m.group(1)}{m.group(2)}.pdf".lower()
        bbb = f"{m.group(1)}{m.group(2)}bbb1.pdf".lower()
        if name.lower() in have:
            continue
        if m.group(3) and bbb in have:
            continue
        if not m.group(3) and page in have:
            continue
        keep.append(key)
    D.log(f"== Davis: {len(dead):,} dead links in all; {len(keep):,} could hide a Test page or ball-by-ball we lack. "
          f"Asking each once more, at most {DAVIS_PER_MINUTE} a minute (about {len(keep) / DAVIS_PER_MINUTE:.0f} min)")
    found, lock, n_done = [], threading.Lock(), 0
    pace = Pace(DAVIS_PER_MINUTE)

    def job(key):
        nonlocal n_done
        pace.wait()
        data, st = D.fetch2("https://www.sportstats.com.au/" + key, tries=2)
        with lock:
            n_done += 1
            if st == "ok" and data and data[:4] == b"%PDF":
                D.save(root / key, data)
                found.append(key)
                D.log(f"   FOUND: {key}")
            if n_done % 100 == 0:
                D.log(f"   ...{n_done}/{len(keep)} asked, {len(found)} found")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(job, keep))
    (RAW / "davis_recheck.txt").write_text("\n".join(found), encoding="utf-8")
    D.log(f"   Davis re-check done: {len(found)} of {len(keep)} now found"
          + (" (run python scripts/build_db.py all to add them)" if found else " (they really are missing on his site)"))


# ---------------------------------------------------------------- report
def report():
    if not RESULTS.exists():
        print("Nothing checked yet: python scripts/catch_missing.py espn")
        return
    rows = list(csv.DictReader(RESULTS.open(encoding="utf-8")))
    last = {}
    for r in rows:
        last[(r["format"], r["number"])] = r
    rows = list(last.values())
    yes = [r for r in rows if r["has_commentary"] == "True"]
    print(f"\nESPN check: {len(rows):,} matches without our ball-by-ball looked at")
    for st in sorted({r["status"] for r in rows}):
        print(f"   {st}: {sum(1 for r in rows if r['status'] == st):,}")
    print(f"   WITH COMMENTARY on ESPN's site: {len(yes):,}")
    for fmt in ("Test", "ODI"):
        ys = sorted((r for r in yes if r["format"] == fmt), key=lambda r: r["start_date"] or "")
        if ys:
            print(f"   {fmt}s ({len(ys)}): " + "; ".join(f"#{r['number']} {r['start_date']} {r['teams']}" for r in ys[:60])
                  + (" ..." if len(ys) > 60 else ""))
    print("Their pages are saved in data/raw/espn_live/. Next: download their full commentary.")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"espn": espn, "davis": davis, "report": report}.get(what, report)()
