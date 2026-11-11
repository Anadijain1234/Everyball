"""
Build the Everyball database (data/everyball.db) from the downloaded files in data/raw/.

  python scripts/build_db.py all         # everything below, in order (about 30-60 minutes the first time)
  python scripts/build_db.py davis       # Charles Davis: Test pages, ball-by-ball, covers, player lists
  python scripts/build_db.py cricsheet   # Cricsheet: Tests + ODIs ball by ball (2000s to now)
  python scripts/build_db.py book        # your Wisden book data (data/raw/book/*.csv), Tests 1877-1970
  python scripts/build_db.py kaggle      # Kaggle ODI scorecards 1971-2024 (gives every ODI its number)
  python scripts/build_db.py espn        # ESPN old archive: scorecards, ball-by-ball commentary, player v player
  python scripts/build_db.py checks      # all the checks + the report (data/build_report.txt)

Each step can be re-run on its own: it first removes its own old rows. Nothing is downloaded except, in
'checks', ESPN's list of every Test and ODI per year (about 200 small pages, saved in data/raw/espn_lists/).
"""
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import read_davis as RD  # noqa: E402

TEXT_CACHE = C.RAW / "_text"


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _worker_paths(raw, text_cache):
    """Worker processes start fresh on newer Pythons (3.14+): give them the same folders as the main process."""
    global TEXT_CACHE
    C.RAW, TEXT_CACHE = Path(raw), Path(text_cache)


def cached_text(path):
    """PDF text, cached in data/raw/_text so re-runs after a fix take minutes, not an hour."""
    rel = Path(path).relative_to(C.RAW)
    cache = TEXT_CACHE / rel.with_suffix(".txt")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    text = C.pdf_text(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------- Davis
TEST_FILE = re.compile(r"^(\d{4}[A-Z]+)(\d+)\.pdf$")
BBB_FILE = re.compile(r"^(\d{4}[A-Z]+)(\d+)bbb(\d*)\.pdf$")


def davis_jobs(root):
    """Group the files: one job per Test (its page + ball-by-ball parts + the series cover)."""
    tests = {}
    covers = {}
    pcs = []
    for p in root.rglob("*.pdf"):
        name = p.name
        if p.parent.name == "pc":
            pcs.append(p)
            continue
        if name.endswith("cov.pdf"):
            covers[name[:-7]] = p
            continue
        m = BBB_FILE.match(name)
        if m:
            tests.setdefault((m[1], int(m[2])), {"page": None, "bbb": []})["bbb"].append((int(m[3] or 1), p))
            continue
        m = TEST_FILE.match(name)
        if m and int(m[2]) == 50:             # '...50.pdf' is the series' "Scores of 50+" page, not a Test
            continue
        if m:
            tests.setdefault((m[1], int(m[2])), {"page": None, "bbb": []})["page"] = p
    jobs = []
    for (series, n), t in sorted(tests.items()):
        jobs.append({"series": series, "n": n, "page": t["page"], "bbb": [p for _, p in sorted(t["bbb"])],
                     "cover": covers.get(series)})
    return jobs, pcs


def davis_work(job):
    """Runs in a worker process: read the PDFs of one Test and return plain data."""
    out = {"job": {k: str(v) if isinstance(v, Path) else v for k, v in job.items() if k != "bbb"},
           "errors": []}
    try:
        cover = RD.parse_cover(cached_text(job["cover"])) if job["cover"] else {}
    except Exception as e:  # noqa: BLE001
        cover = {}
        out["errors"].append(f"cover: {e}")
    out["cover"] = cover
    page = None
    if job["page"]:
        try:
            page = RD.parse_test_page(cached_text(job["page"]))
            if not page.get("number"):
                out["errors"].append("Test page: header not recognised")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Test page: {e}")
    out["page"] = page
    bbb = None
    if job["bbb"]:
        try:
            text = "\n".join(cached_text(p) for p in job["bbb"])
            rosters = {}
            for inn in (page or {}).get("innings", []):
                rosters.setdefault(inn["team"], set()).update(b["name"] for b in inn["batting"])
                rosters.setdefault(inn["bowling_team"], set()).update(b["name"] for b in inn["bowling"])
            bbb = RD.parse_bbb(text, rosters, bpo=cover.get("balls_per_over"))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"ball-by-ball: {e}")
    out["bbb"] = bbb
    return out


def winner_of(result, teams):
    m = re.match(r"^\s*(.+?) won\b", result or "")
    return C.team(m.group(1)) if m else None


def step_davis(con):
    root = C.RAW / "davis" / "zArchive"
    if not root.exists():
        say("Davis: folder data/raw/davis/zArchive not found, skipping")
        return
    import shutil
    if not shutil.which("pdftotext") and not os.environ.get("EVERYBALL_ALLOW_PYPDF"):
        say("Davis: the PDF tool 'pdftotext' is missing. Install it once with:\n"
            "   sudo apt-get update && sudo apt-get install -y poppler-utils\n"
            "and run this step again. (Without it the over lines come out jumbled and many balls can't be checked.)")
        return
    C.clear_source(con, "davis")
    jobs, pcs = davis_jobs(root)
    say(f"Davis: {len(jobs)} Tests to read ({sum(len(j['bbb']) > 0 for j in jobs)} with ball-by-ball), "
        f"{len(pcs)} player lists. First run reads every PDF (cached afterwards).")
    reg = C.Registry(con)
    stats = {"tests": 0, "with_bbb": 0, "segments": 0, "sure": 0, "balls": 0, "errors": 0}
    workers = max(1, (os.cpu_count() or 2))
    done = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_paths,
                             initargs=(str(C.RAW), str(TEXT_CACHE))) as pool:
        for res in pool.map(davis_work, jobs, chunksize=4):
            done += 1
            if done % 200 == 0:
                say(f"   ...{done}/{len(jobs)} Tests read")
                con.commit()
            save_davis_test(con, reg, res, stats)
    for p in pcs:
        try:
            for pl in RD.parse_players(cached_text(p)):
                reg.get(pl["name"], pl["team"], "davis", full_name=pl["full_name"], born=pl["born"], role=pl["role"])
        except Exception as e:  # noqa: BLE001
            C.log(con, "davis", p, "error", f"player list: {e}")
    con.commit()
    C.finish(con)
    pct = 100 * stats["sure"] / max(1, stats["segments"])
    say(f"Davis done: {stats['tests']} Tests, {stats['with_bbb']} with ball-by-ball, {stats['balls']:,} balls, "
        f"{pct:.1f}% of over-lines fully checked, {stats['errors']} files with problems")


def save_davis_test(con, reg, res, stats):
    job, page, bbb, cover = res["job"], res["page"], res["bbb"], res["cover"]
    src_file = job.get("page") or job.get("series")
    for e in res["errors"]:
        stats["errors"] += 1
        C.log(con, "davis", src_file, "error", e)
    number = (page or {}).get("number") or (bbb or {}).get("number")
    if not number:
        C.log(con, "davis", src_file, "skipped", "no Test number found")
        return
    mid = f"T{number:04d}"
    teams = ((page or {}).get("team1"), (page or {}).get("team2"))
    if not all(teams) and bbb and bbb.get("teams"):
        teams = bbb["teams"]
    bpo = (bbb or {}).get("balls_per_over") or cover.get("balls_per_over")
    C.upsert_match(con, mid, "davis", format="Test", number=number,
                   start_date=(page or {}).get("start_date") or (bbb or {}).get("start_date"),
                   season=(page or {}).get("season"), team1=teams[0], team2=teams[1],
                   ground=(page or {}).get("ground"), result=(page or {}).get("result"),
                   winner=winner_of((page or {}).get("result"), teams), toss=(page or {}).get("toss"),
                   umpires=(page or {}).get("umpires"), balls_per_over=bpo, series=cover.get("series"),
                   confidence="rebuilt from newspaper reports" if cover.get("reconstructed") and bbb else None)
    stats["tests"] += 1
    rosters = {}
    if page:
        for i, inn in enumerate(page["innings"], 1):
            bat_team, bowl_team = inn["team"], inn["bowling_team"]
            rosters.setdefault(bat_team, set()).update(b["name"] for b in inn["batting"])
            rosters.setdefault(bowl_team, set()).update(b["name"] for b in inn["bowling"])
            con.execute("INSERT INTO innings VALUES(?,?,?,?,?,?,?,?,?)",
                        (mid, i, bat_team, inn.get("runs"), inn.get("wickets"), inn.get("overs"), inn.get("extras"),
                         inn.get("declared"), "davis"))
            for pos, b in enumerate(inn["batting"], 1):
                bowler_full = RD.resolve(b["bowler"], set(rosters.get(bowl_team, set()))) if b["bowler"] else None
                con.execute("INSERT INTO batting VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (mid, i, pos, reg.get(b["name"], bat_team, "davis"), bat_team, b["how"], b["kind"],
                             reg.get(bowler_full, bowl_team, "davis") if bowler_full else None, b["fielder"],
                             b["runs"], b["balls"], b["minutes"], b["fours"], b["sixes"], b["not_out"],
                             b["captain"], b["keeper"], "davis"))
            for w in inn["bowling"]:
                con.execute("INSERT INTO bowling VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (mid, i, reg.get(w["name"], bowl_team, "davis"), bowl_team, w["overs"],
                             C.overs_to_balls(w["overs"], bpo or 6), w["maidens"], w["runs"], w["wickets"], "davis"))
    if bbb and bbb["innings"]:
        stats["with_bbb"] += 1
        s = bbb["stats"]
        stats["segments"] += s["segments"]
        stats["sure"] += s["sure"]
        rows, notes = [], []
        order = {(x["team"], x["team_inn"]): j for j, x in enumerate((page or {}).get("innings", []), 1)}
        for k, inn in enumerate(bbb["innings"], 1):
            i = order.get((inn["team"], int(inn.get("n") or 0)), k)     # e.g. only England's 1st innings survives
            bat_team, bowl_team = inn["team"], inn["bowling_team"]
            for seq, b in enumerate(inn["balls"], 1):
                rows.append((mid, i, seq, b["over_no"], b["ball"], reg.get(b["bowler"], bowl_team, "davis"),
                             reg.get(b["batter"], bat_team, "davis"),
                             reg.get(b["non_striker"], bat_team, "davis") if b["non_striker"] else None,
                             b["bat_runs"], b["extras"], b["extra_type"], b["legal"], b["wicket"], b["kind"],
                             reg.get(b["out"], bat_team, "davis") if b["out"] else None, b["sure"], "davis"))
            notes += [(mid, i, o, n, "davis") for o, n in inn["notes"]]
            if inn.get("partial"):
                notes.append((mid, i, 0, "PARTIAL RECORD: " + inn["partial"], "davis"))
        con.executemany("INSERT INTO balls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT INTO notes VALUES(?,?,?,?,?)", notes)
        stats["balls"] += len(rows)
        C.log(con, "davis", src_file, "ok", f"{mid}: {len(rows)} balls, {s['sure']}/{s['segments']} over-lines checked")
    else:
        C.log(con, "davis", src_file, "ok", f"{mid}: scorecard only")


# ---------------------------------------------------------------- the other sources (loaded only when used)
def step(name, con):
    try:
        return _step(name, con)
    except ModuleNotFoundError as e:
        say(f"{name}: not built yet ({e.name}.py is missing), skipping")


def _step(name, con):
    if name == "davis":
        return step_davis(con)
    if name == "cricsheet":
        import read_cricsheet
        return read_cricsheet.run(con, say)
    if name == "book":
        import read_book
        return read_book.run(con, say)
    if name == "espn":
        import read_espn
        return read_espn.run(con, say)
    if name == "kaggle":
        import read_kaggle
        return read_kaggle.run(con, say)
    if name == "checks":
        import checks
        return checks.run(con, say)
    raise SystemExit(f"unknown step: {name}")


ORDER = ["davis", "cricsheet", "book", "kaggle", "espn", "checks"]


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = ORDER if what == "all" else [what]
    C.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = C.connect()
    t0 = time.time()
    for s in steps:
        say(f"== {s}")
        step(s, con)
    C.finish(con)
    say(f"All done in {(time.time() - t0) / 60:.1f} min. Database: {C.DB_PATH} "
        f"({C.DB_PATH.stat().st_size / 1e6:,.0f} MB). Report: {C.REPORT}")


if __name__ == "__main__":
    main()
