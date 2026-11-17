"""
Add ESPNcricinfo's live-site ball-by-ball (the 66 `espn_full_commentary_*.json.gz` files + the redo file) to the
database, for every Test and ODI that has NO ball-by-ball yet. Nothing that is already there is changed.

  python scripts/add_espn_live.py data/raw/espn_full            # check + add (writes data/everyball.db)
  python scripts/add_espn_live.py data/raw/espn_full --dry-run  # only report what would be added

For every innings it checks ESPN's balls against ESPN's own scorecard (legal balls and runs):
  complete  -> balls marked sure = 1
  partial   -> balls added but marked sure = 0 (ESPN's feed is missing some balls, mostly 1999-2000), listed in the report
Players are joined by ESPN's player id (players.cricinfo_id). A player we don't have yet is added from ESPN's own
record (name, full name, date of birth, styles). Report: data/reports/espn_live_added.csv + a summary on screen.
Source name in the database: 'espn_live'.
"""
import csv
import glob
import gzip
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "everyball.db"
REPORT = ROOT / "data" / "reports" / "espn_live_added.csv"
SRC = "espn_live"

# ESPN's dismissalType numbers -> our words (checked against Cricsheet on the same balls; see the test notes)
KIND = {1: "caught", 2: "bowled", 3: "lbw", 4: "run out", 5: "stumped", 6: "hit wicket", 7: "handled the ball",
        8: "obstructing the field", 9: "hit the ball twice", 10: "timed out", 11: "retired hurt",
        12: "retired out", 13: "retired not out"}
SHORT = {"caught": "caught", "bowled": "bowled", "lbw": "lbw", "run out": "run out", "stumped": "stumped",
         "hit wicket": "hit wicket", "handled ball": "handled the ball", "obstructing the field": "obstructing the field",
         "hit ball twice": "hit the ball twice", "timed out": "timed out", "retired hurt": "retired hurt",
         "retired out": "retired out", "retired not out": "retired not out", "retired": "retired hurt"}


def records(folder):
    files = sorted(glob.glob(os.path.join(folder, "espn_full_commentary_*.json.gz")))
    if not files:
        sys.exit(f"No espn_full_commentary_*.json.gz files in {folder}")
    seen = set()
    for f in files:
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for r in json.load(fh):
                key = (r.get("format"), r.get("number"))
                if r.get("innings") and key not in seen:     # skips the exact duplicates the "(1)" copies hold
                    seen.add(key)
                    yield r


def kind_of(x):
    dt = x.get("dismissalText") or {}
    if "c & b" in (dt.get("long") or "") or "c & b" in (dt.get("bowlerText") or ""):
        return "caught and bowled"
    t = (dt.get("short") or "").strip().lower()
    if t in SHORT:
        return SHORT[t]
    if t.startswith("c ") or t == "c&b" or "caught" in t:
        return "caught and bowled" if "c&b" in t or "& b" in t else "caught"
    return KIND.get(x.get("dismissalType"), "out")


def balls_of(items):
    """ESPN items of one innings -> our ball rows (in delivery order), plus legal-ball and run counts."""
    items = sorted(items, key=lambda x: (x.get("overNumber") or 0, x.get("ballNumber") or 0, x.get("id") or 0))
    rows, legal_in_over, last_over = [], 0, None
    for x in items:
        ov = x.get("overNumber") or 0
        if ov != last_over:
            legal_in_over, last_over = 0, ov
        wd, nb, b, lb, pen = (x.get(k) or 0 for k in ("wides", "noballs", "byes", "legbyes", "penalties"))
        legal = 0 if (wd or nb) else 1
        et = "wd" if wd else "nb" if nb else "b" if b else "lb" if lb else "pen" if pen else None
        rows.append({"over_no": ov, "ball": legal_in_over + 1, "bowler": x.get("bowlerPlayerId"),
                     "batter": x.get("batsmanPlayerId"), "non": x.get("nonStrikerPlayerId"),
                     "bat_runs": x.get("batsmanRuns") or 0, "extras": wd + nb + b + lb + pen, "extra_type": et,
                     "legal": legal, "wicket": 1 if x.get("isWicket") else 0,
                     "kind": kind_of(x) if x.get("isWicket") else None,
                     "out": (x.get("outPlayerId") or x.get("batsmanPlayerId")) if x.get("isWicket") else None})
        legal_in_over += legal
    return rows


class Players:
    """ESPN player id -> our player_id (through ESPN's objectId = our cricinfo_id); adds missing players."""

    def __init__(self, con):
        self.con = con
        self.by_obj = {str(o).replace(".0", ""): p for p, o in
                       con.execute("SELECT player_id, cricinfo_id FROM players WHERE cricinfo_id IS NOT NULL")}
        self.next_id = con.execute("SELECT COALESCE(MAX(player_id), 0) + 1 FROM players").fetchone()[0]
        self.added, self.linked = [], []

    def get(self, espn_id, plist, team):
        if espn_id is None:
            return None
        p = plist.get(str(espn_id)) or {}
        obj = str(p.get("objectId") or "")
        if obj and obj in self.by_obj:
            return self.by_obj[obj]
        if not obj:
            return None
        name = p.get("name") or p.get("longName")
        same = self.con.execute("SELECT player_id, cricinfo_id FROM players WHERE name = ? AND team IS ?",
                                (name, team)).fetchone()
        if same and not same[1]:                       # we have him, just without ESPN's id: link, don't add
            self.con.execute("UPDATE players SET cricinfo_id = ? WHERE player_id = ?", (obj, same[0]))
            self.by_obj[obj] = same[0]
            self.linked.append((same[0], name, team))
            return same[0]
        if same:                                       # a different player with the same short name and team
            for cand in (p.get("longName"), f"{p.get('longName') or name} ({obj})"):
                if cand and not self.con.execute("SELECT 1 FROM players WHERE name = ? AND team IS ?",
                                                 (cand, team)).fetchone():
                    name = cand
                    break
        pid = self.next_id
        self.next_id += 1
        dob = p.get("dateOfBirth") or {}
        dob_s = f"{dob.get('year'):04d}-{dob.get('month') or 1:02d}-{dob.get('date') or 1:02d}" if dob.get("year") else None
        self.con.execute("INSERT INTO players (player_id, name, team, full_name, dob, batting_style, bowling_style, "
                         "cricinfo_id, sources) VALUES (?,?,?,?,?,?,?,?,?)",
                         (pid, name, team, p.get("longName"), dob_s,
                          ", ".join(p.get("longBattingStyles") or []) or None,
                          ", ".join(p.get("longBowlingStyles") or []) or None, obj, SRC))
        self.by_obj[obj] = pid
        self.added.append((pid, p.get("longName") or p.get("name"), team))
        return pid


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    folder = args[0] if args else str(ROOT / "data" / "raw" / "espn_full")
    db = Path(args[1]) if len(args) > 1 else DB
    if not db.exists():
        sys.exit(f"{db} not found. Make it first: python scripts/publish_db.py unpack")
    con = sqlite3.connect(db)
    match_of = {(f, n): (mid, src) for mid, f, n, src in
                con.execute("SELECT match_id, format, number, ball_source FROM matches")}
    players = Players(con)
    out, tally, years = [], Counter(), Counter()
    for r in records(folder):
        key = (r["format"], r["number"])
        if key not in match_of:
            tally["not in our match list"] += 1
            out.append([r["format"], r["number"], r.get("start_date"), r.get("teams"), "", "not in our match list", "", "", ""])
            continue
        mid, have = match_of[key]
        if have:
            tally[f"already has ball-by-ball ({have})"] += 1
            continue
        plist = r.get("players") or {}
        cards = {s.get("inningNumber"): s for s in (r.get("scorecard_innings") or [])}
        team_of = {s.get("inningNumber"): ((s.get("team") or {}).get("name")) for s in cards.values()}
        rows, status = [], []
        for k in sorted(r["innings"], key=lambda z: int(z)):
            inn = int(k)
            bl = balls_of(r["innings"][k])
            legal = sum(b["legal"] for b in bl)
            runs = sum(b["bat_runs"] + b["extras"] for b in bl)
            card = cards.get(inn) or {}
            complete = bool(card) and legal == card.get("balls") and runs == card.get("runs")
            status.append(f"inn{inn}: {legal}/{card.get('balls')} balls, {runs}/{card.get('runs')} runs"
                          + ("" if complete else " PARTIAL"))
            bat_team = team_of.get(inn)
            bowl_team = next((t for i, t in team_of.items() if t and t != bat_team), None)
            for seq, b in enumerate(bl, 1):
                rows.append((mid, inn, seq, b["over_no"], b["ball"],
                             players.get(b["bowler"], plist, bowl_team), players.get(b["batter"], plist, bat_team),
                             players.get(b["non"], plist, bat_team), b["bat_runs"], b["extras"], b["extra_type"],
                             b["legal"], b["wicket"], b["kind"], players.get(b["out"], plist, bat_team),
                             1 if complete else 0, SRC))
        whole = all("PARTIAL" not in s for s in status)
        tally["added, complete" if whole else "added, partial"] += 1
        years[(r.get("start_date") or "")[:3] + "0s"] += 1
        out.append([r["format"], r["number"], r.get("start_date"), r.get("teams"), mid,
                    "complete" if whole else "partial", len(rows), "; ".join(status), ""])
        if not dry:
            con.executemany("INSERT INTO balls (match_id, inn, seq, over_no, ball, bowler_id, batter_id, non_striker_id, "
                            "bat_runs, extras, extra_type, legal, wicket, kind, out_id, sure, source) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.execute("UPDATE matches SET ball_source = ?, sources = COALESCE(sources || ',', '') || ? "
                        "WHERE match_id = ? AND ball_source IS NULL", (SRC, SRC, mid))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["format", "number", "date", "teams", "match_id", "result", "balls", "innings check", "note"])
        w.writerows(out)
        for pid, name, team in players.added:
            w.writerow(["player", pid, "", team, "", "new player from ESPN", "", name, ""])
        for pid, name, team in players.linked:
            w.writerow(["player", pid, "", team, "", "linked to ESPN id", "", name, ""])
    if dry:
        con.rollback()
    else:
        con.commit()
        if "--no-stats" not in sys.argv:
            refresh_stats(con)
    print(("DRY RUN - nothing written. " if dry else "") + "ESPN live ball-by-ball:")
    for k, v in tally.most_common():
        print(f"  {k}: {v}")
    print("  new matches by decade:", dict(sorted(years.items())))
    print(f"  new players added from ESPN: {len(players.added)}; existing players linked to their ESPN id: {len(players.linked)}")
    for f in ("Test", "ODI"):
        n, b = con.execute("SELECT COUNT(*), SUM(ball_source IS NOT NULL) FROM matches WHERE format = ?", (f,)).fetchone()
        print(f"  {f}s with ball-by-ball now: {b} of {n} ({100 * b / n:.1f}%)")
    print(f"  report: {REPORT.relative_to(ROOT)}")
    con.close()


def refresh_stats(con):
    """player_stats (used by search) must count the new players too."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(player_stats)")]
    if not cols:
        return
    missing = [r[0] for r in con.execute(
        "SELECT p.player_id FROM players p WHERE p.sources = ? AND p.player_id NOT IN (SELECT player_id FROM player_stats)", (SRC,))]
    if not missing or not {"player_id", "format", "matches", "first_year", "last_year"} <= set(cols):
        return
    for pid in missing:
        for f, m, a, z in con.execute("""
                SELECT m.format, COUNT(DISTINCT m.match_id), MIN(substr(m.start_date,1,4)), MAX(substr(m.start_date,1,4))
                FROM balls x JOIN matches m ON m.match_id = x.match_id
                WHERE x.batter_id = ? OR x.bowler_id = ? GROUP BY 1""", (pid, pid)).fetchall():
            con.execute("INSERT INTO player_stats (player_id, format, matches, first_year, last_year) VALUES (?,?,?,?,?)",
                        (pid, f, m, a, z))
    con.commit()


if __name__ == "__main__":
    main()
