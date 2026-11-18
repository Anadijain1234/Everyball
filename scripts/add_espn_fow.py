"""
Fall of wickets and partnerships for every innings, from ESPNcricinfo's own scorecards (the 67
espn_full_commentary_*.json.gz files), into two new tables (source 'espn_live'). Run AFTER add_espn_cards.py (players
are joined by their ESPN id, which that script fills in).

  python scripts/add_espn_fow.py data/raw/espn_full [data/everyball.db]

fall_of_wickets  match_id, inn, wicket_no, runs, overs (as printed, e.g. '49.1'), over_no (completed overs),
                 ball (ball of the over), legal (legal balls bowled when it fell), batter_out_id, source
partnerships     match_id, inn, for_wicket, batter1_id, batter2_id, runs, balls, b1_runs, b1_balls, b2_runs, b2_balls,
                 unbroken, source
Count check printed at the end: every ESPN item read = a row written, or listed with a reason.
"""
import gzip, json, os, sqlite3, sys, glob, gc, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = "espn_live"


def main():
    t0 = time.time()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folder = args[0] if args else str(ROOT / "data" / "raw" / "espn_full")
    con = sqlite3.connect(args[1] if len(args) > 1 else ROOT / "data" / "everyball.db")
    con.execute("""CREATE TABLE IF NOT EXISTS fall_of_wickets (match_id TEXT, inn INT, wicket_no INT, runs INT, overs TEXT,
                   over_no INT, ball INT, legal INT, batter_out_id INT, source TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS partnerships (match_id TEXT, inn INT, for_wicket INT, batter1_id INT,
                   batter2_id INT, runs INT, balls INT, b1_runs INT, b1_balls INT, b2_runs INT, b2_balls INT, unbroken INT,
                   source TEXT)""")
    con.execute("DELETE FROM fall_of_wickets WHERE source = ?", (SRC,))
    con.execute("DELETE FROM partnerships WHERE source = ?", (SRC,))
    match_of = {(f, n): (mid, bpo or 6) for mid, f, n, bpo in con.execute("SELECT match_id, format, number, balls_per_over FROM matches")}
    by_obj = {str(o).replace(".0", ""): p for p, o in con.execute("SELECT player_id, cricinfo_id FROM players WHERE cricinfo_id IS NOT NULL")}
    pid = lambda d: by_obj.get(str((d or {}).get("objectId") or "").replace(".0", "")) if d else None
    tally, seen, fw, pr = Counter(), set(), [], []
    files = sorted(glob.glob(os.path.join(folder, "espn_full_commentary_*.json.gz")))
    for k, f in enumerate(files, 1):
        el = int(time.time() - t0)
        print(f"\r  ⏱ {el // 60}:{el % 60:02d} | file {k}/{len(files)} | fall of wickets {len(fw)} | partnerships {len(pr)}", end="", flush=True)
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for r in data:
            key = (r.get("format"), r.get("number"))
            if key in seen:
                continue
            seen.add(key)
            if key not in match_of:
                tally["match not in our list"] += 1
                continue
            mid, bpo = match_of[key]
            for s in r.get("scorecard_innings") or []:
                inn = s.get("inningNumber")
                bpo_i = s.get("ballsPerOver") or bpo
                for w in s.get("inningFallOfWickets") or []:
                    ov = w.get("fowOvers")
                    o_no = b_no = legal = None
                    if ov is not None:
                        txt = f"{ov}"
                        a, _, b = txt.partition(".")
                        o_no, b_no = int(a), int(b or 0)
                        legal = o_no * bpo_i + b_no
                    out = pid(w.get("dismissalBatsman"))
                    if out is None:
                        tally["fall of wicket: batter not joined (kept, id empty)"] += 1
                    fw.append((mid, inn, w.get("fowWicketNum"), w.get("fowRuns"), None if ov is None else f"{ov}", o_no, b_no,
                               legal, out, SRC))
                parts = s.get("inningPartnerships") or []
                for i, p in enumerate(parts, 1):
                    a, b = pid(p.get("player1")), pid(p.get("player2"))
                    if a is None or b is None:
                        tally["partnership: a batter not joined (kept, id empty)"] += 1
                    pr.append((mid, inn, i, a, b, p.get("runs"), p.get("balls"), p.get("player1Runs"), p.get("player1Balls"),
                               p.get("player2Runs"), p.get("player2Balls"), 0 if p.get("outPlayerId") else 1, SRC))
                tally["innings read"] += 1
                if not s.get("inningFallOfWickets") and (s.get("wickets") or 0) > 0:
                    tally["innings with wickets but no fall of wickets from ESPN"] += 1
                if not parts and (s.get("runs") or 0) > 0:
                    tally["innings with runs but no partnerships from ESPN"] += 1
        del data
        gc.collect()
    print()
    con.executemany("INSERT INTO fall_of_wickets VALUES (?,?,?,?,?,?,?,?,?,?)", fw)
    con.executemany("INSERT INTO partnerships VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", pr)
    con.execute("CREATE INDEX IF NOT EXISTS ix_fow ON fall_of_wickets(match_id, inn)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_part ON partnerships(match_id, inn)")
    con.commit()
    # checks: partnerships add up to the innings total less the extras not in any stand? ESPN stands include extras
    bad = con.execute("""SELECT COUNT(*) FROM (SELECT p.match_id, p.inn, SUM(p.runs) s FROM partnerships p WHERE p.source = ?
                         GROUP BY 1, 2) x JOIN innings_gold g ON g.match_id = x.match_id AND g.inn = x.inn
                         WHERE g.runs IS NOT NULL AND x.s != g.runs""", (SRC,)).fetchone()[0]
    print(f"ESPN fall of wickets + partnerships ({time.time() - t0:.0f} s): {len(fw)} fall-of-wicket rows, {len(pr)} partnerships")
    for k_, v in tally.most_common():
        print(f"  {k_}: {v}")
    print(f"  check: innings whose partnerships don't add up to the total: {bad}")
    con.close()


if __name__ == "__main__":
    main()
