"""
Reader for your Wisden book data (The Wisden Book of Test Cricket, Vol 1, bought copy): Tests 1877-1970.
Put the CSV files from cricket-project/data/ into data/raw/book/ (matches.csv, innings.csv, batting.csv, bowling.csv).
Only numbers are used (scores, results, toss, captains, umpires), never the book's text.
Rows the OCR marked as not adding up keep a 'mismatch' note; Davis's numbers are preferred for those.
"""
import csv

import common as C


def rows(name):
    p = C.RAW / "book" / name
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def n(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def run(con, say):
    if not (C.RAW / "book" / "matches.csv").exists():
        say("Book: data/raw/book/matches.csv not found, skipping (copy cricket-project/data/*.csv there)")
        return
    C.clear_source(con, "book")
    reg = C.Registry(con)
    ids, skipped = {}, 0
    for m in rows("matches.csv"):
        mid = f"T{n(m['test_no']):04d}"
        # the book's Test numbers were read by OCR: trust a number only if that Test (from Davis) has the same teams,
        # otherwise find the Test with the same teams within 3 days of the same date
        teams = {C.team(m.get("home_team")), C.team(m.get("away_team"))}
        have = con.execute("SELECT team1, team2 FROM matches WHERE match_id = ?", (mid,)).fetchone()
        if have and set(have) != teams:
            hit = con.execute("""SELECT match_id FROM matches WHERE format = 'Test'
                                 AND abs(julianday(start_date) - julianday(?)) <= 3
                                 AND ((team1 = ? AND team2 = ?) OR (team1 = ? AND team2 = ?))""",
                              (m.get("start_date"), *sorted(teams), *sorted(teams, reverse=True))).fetchall()
            if len(hit) != 1:
                ids[m["test_no"]] = None
                skipped += 1
                C.log(con, "book", "matches.csv", "skipped", f"book Test {m['test_no']} ({' v '.join(sorted(teams))}) "
                      f"doesn't match {mid}")
                continue
            mid = hit[0][0]
        ids[m["test_no"]] = mid
        caps = "; ".join(x for x in (m.get("captain_home"), m.get("captain_away")) if x)
        C.upsert_match(con, mid, "book", format="Test", number=int(mid[1:]), start_date=m.get("start_date"),
                       season=m.get("season"), team1=C.team(m.get("home_team")), team2=C.team(m.get("away_team")),
                       ground=m.get("venue"), result=m.get("result"), winner=C.team(m.get("winner")),
                       toss=C.team(m.get("toss_won_by")), umpires=m.get("umpires"),
                       balls_per_over=n(m.get("balls_per_over")))
        _ = caps
    for r in rows("innings.csv"):
        if not ids.get(r["test_no"]):
            continue
        con.execute("INSERT INTO innings VALUES(?,?,?,?,?,?,?,?,?)",
                    (ids[r["test_no"]], n(r["innings_no"]), C.team(r["team"]), n(r["runs"]), n(r["wickets"]),
                     None, n(r["extras"]), n(r["declared"]), "book"))
    batting = rows("batting.csv")
    for r in batting:
        if not ids.get(r["test_no"]):
            continue
        if r.get("did_not_bat") == "1":
            tm = C.team(r["team"])
            con.execute("INSERT INTO played VALUES(?,?,?,?)", (ids[r["test_no"]], tm, reg.get(r["player"], tm, "book"), "book"))
            continue
        tm, opp = C.team(r["team"]), C.team(r["opponent"])
        con.execute("INSERT INTO batting VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ids[r["test_no"]], n(r["innings_no"]), n(r["batting_pos"]), reg.get(r["player"], tm, "book"),
                     tm, r.get("dismissal") or r.get("how_out"), r.get("how_out"),
                     reg.get(r["bowler"], opp, "book") if r.get("bowler") else None, r.get("fielder") or None,
                     n(r["runs"]), None, None, None, None, n(r["not_out"]), n(r.get("captain")), n(r.get("keeper")),
                     "book"))
    for r in rows("bowling.csv"):
        if not ids.get(r["test_no"]):
            continue
        tm = C.team(r["team"])
        con.execute("INSERT INTO bowling VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ids[r["test_no"]], n(r["innings_no"]), reg.get(r["player"], tm, "book"), tm, r.get("overs"),
                     n(r.get("balls")), n(r.get("maidens")), n(r.get("runs")), n(r.get("wickets")), "book"))
    bad = sum(1 for r in batting if r.get("runs_check") not in ("ok", "", None))
    C.log(con, "book", "batting.csv", "ok", f"{len(batting)} batting rows, {bad} marked as not adding up")
    con.commit()
    C.finish(con)
    say(f"Book: {len(rows('matches.csv')) - skipped} Tests ({skipped} left out: number and teams don't match), {len(batting)} batting rows ({bad} flagged by the OCR checks)")
