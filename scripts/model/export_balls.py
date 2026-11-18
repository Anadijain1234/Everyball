"""Export every ball for the Everyball GPU model (Kaggle).

Run in the Codespace:  python3 scripts/model/export_balls.py
Makes data/model_export/ (upload the 3 files to Kaggle as ONE private dataset, name it "everyball-balls"):
  everyball_balls.npz  every ball in order: who batted / bowled / at the other end, over, ball of the over, outcome,
                       score and wickets before it (numbers only, compressed)
  innings.csv          one row per innings: match, Test/ODI, year, ground, teams, source, flagged, split
  players.csv          player number -> player_id, name (the model learns one profile per player)
Split by match (never by ball): 90% train, 5% val (to watch training), 5% test (never seen, for honest checks).
No names of keys, no secrets; the data is the same as the public site.
"""
import argparse, array, csv, os, sqlite3, sys, time, zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATS = ["0", "1", "2", "3", "4", "5", "6", "W", "wd", "nb", "bye"]      # 11 = other (penalty, unknown codes): not learned
SRC = {"cricsheet": 0, "espn": 1, "espn_live": 2, "davis": 3, "kaggle": 4}


def split_of(mid):
    h = zlib.crc32(mid.encode()) % 20
    return 2 if h == 0 else (1 if h == 1 else 0)          # 2 = test, 1 = val, 0 = train


def cat_of(wk, et, br):
    if wk:
        return 7
    if et == "wd":
        return 8
    if et == "nb":
        return 9
    if et in ("b", "lb"):
        return 10
    if et in (None, ""):
        return min(max(int(br or 0), 0), 6)
    return 11


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "everyball.db"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "model_export"))
    a = ap.parse_args()
    t0 = time.time()
    c = sqlite3.connect(a.db)
    os.makedirs(a.out, exist_ok=True)
    flagged = {(m, i) for m, i in c.execute("SELECT DISTINCT match_id, inn FROM balls_fix WHERE fix = 'attribution_unsure'")}
    team_of = {}
    for m, i, t in c.execute("SELECT match_id, inn, team FROM innings WHERE team IS NOT NULL"):
        team_of.setdefault((m, i), t)
    minfo = {r[0]: r[1:] for r in c.execute("SELECT match_id, format, start_date, ground, team1, team2, "
                                            "COALESCE(balls_per_over, 6), ball_source FROM matches")}
    pidx, gidx, tidx = {}, {}, {}
    P = lambda p: 0 if p is None else pidx.setdefault(p, len(pidx) + 1)
    G = lambda g: gidx.setdefault(g or "?", len(gidx) + 1)
    T = lambda t: tidx.setdefault(t or "?", len(tidx) + 1)
    cols = {k: array.array("i") for k in ("inn", "bat", "bowl", "ns", "over", "bio", "legal", "cat", "runs", "score", "wkts")}
    inns = []
    q = ("SELECT b.match_id, b.inn, b.over_no, b.bowler_id, b.batter_id, b.non_striker_id, b.bat_runs, b.extras, "
         "b.extra_type, b.legal, b.wicket FROM balls b JOIN matches m USING (match_id) "
         "WHERE b.source = m.ball_source ORDER BY b.match_id, b.inn, b.seq")
    cur, first_over, last_over, bio, score, wkts, n = None, 0, None, 0, 0, 0, 0
    for mid, inn, ov, bw, bt, ns, br, ex, et, legal, wk in c.execute(q):
        if (mid, inn) != cur:
            cur = (mid, inn)
            fmt, day, ground, t1, t2, bpo, src = minfo[mid]
            bat_t = team_of.get(cur)
            bowl_t = t2 if bat_t == t1 else (t1 if bat_t == t2 else None)
            inns.append([mid, inn, 0 if fmt == "ODI" else 1, int(str(day)[:4] or 0), G(ground), T(bat_t), T(bowl_t),
                         SRC.get(src, 5), int(cur in flagged), split_of(mid), bpo, ground or "", bat_t or "", bowl_t or ""])
            first_over, last_over, score, wkts = ov or 1, None, 0, 0
        if ov != last_over:
            last_over, bio = ov, 0
        bio += 1
        cols["inn"].append(len(inns) - 1); cols["bat"].append(P(bt)); cols["bowl"].append(P(bw)); cols["ns"].append(P(ns))
        cols["over"].append(max(0, (ov or 1) - first_over)); cols["bio"].append(bio); cols["legal"].append(int(legal or 0))
        cols["cat"].append(cat_of(wk, et, br)); cols["runs"].append(int(br or 0) + int(ex or 0))
        cols["score"].append(score); cols["wkts"].append(wkts)
        score += int(br or 0) + int(ex or 0); wkts += int(wk or 0)
        n += 1
        if n % 500000 == 0:
            el = int(time.time() - t0)
            print(f"\r  ⏱ {el // 60}:{el % 60:02d} | exporting balls | {n:,} done (~6.5M)   ", end="", flush=True)
    dt = {"inn": np.int32, "bat": np.int32, "bowl": np.int32, "ns": np.int32, "over": np.int16, "bio": np.int8,
          "legal": np.int8, "cat": np.int8, "runs": np.int16, "score": np.int16, "wkts": np.int8}
    np.savez_compressed(os.path.join(a.out, "everyball_balls.npz"),
                        **{k: np.frombuffer(v, dtype=np.int32).astype(dt[k]) for k, v in cols.items()})
    with open(os.path.join(a.out, "innings.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "match_id", "inn", "fmt", "year", "ground", "bat_team", "bowl_team", "source", "flagged",
                    "split", "bpo", "ground_name", "bat_team_name", "bowl_team_name"])
        for i, r in enumerate(inns):
            w.writerow([i] + r)
    names = dict(c.execute("SELECT player_id, name FROM players"))
    with open(os.path.join(a.out, "players.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["idx", "player_id", "name"])
        for p, i in sorted(pidx.items(), key=lambda kv: kv[1]):
            w.writerow([i, p, names.get(p, "")])
    mb = sum(os.path.getsize(os.path.join(a.out, f)) for f in os.listdir(a.out)) / 1e6
    print(f"\nDONE: {n:,} balls, {len(inns):,} innings, {len(pidx):,} players, {len(gidx)} grounds -> {a.out} "
          f"({mb:.0f} MB) in {int(time.time() - t0)} s")


if __name__ == "__main__":
    main()
