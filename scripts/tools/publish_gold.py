"""
Make the website show the gold scorecards (Everyball, 18 Nov 2026).

The site reads `batting`, `bowling` and `innings` rows whose source is the match's `card_source`, and `player_stats` for
search and the Ask box. This writes the gold lines (every field chosen by vote of all sources, see build_gold.py) into
those tables as source 'gold', points every match's card_source at them, and rebuilds player_stats from the gold
careers (whole XI counted for matches). The original rows of every source stay untouched. Re-runnable.

  python3 scripts/tools/publish_gold.py data/everyball.db      (after build_gold.py)
"""
import sqlite3, sys, time

DB = sys.argv[1] if len(sys.argv) > 1 else "data/everyball.db"
t0 = time.time()
con = sqlite3.connect(DB)
q = lambda s, *a: con.execute(s, a).fetchall()
PRI = "CASE source WHEN 'cricsheet' THEN 0 WHEN 'espn_live' THEN 1 WHEN 'espn' THEN 2 WHEN 'davis' THEN 3 WHEN 'kaggle' THEN 4 ELSE 9 END"

for t in ("batting", "bowling", "innings"):
    con.execute(f"DELETE FROM {t} WHERE source = 'gold'")

# batting: the gold line + the dismissal wording of the best source for that same line
con.execute(f"""
INSERT INTO batting (match_id, inn, pos, player_id, team, how_out, kind, bowler_id, fielder, runs, balls, minutes, fours,
                     sixes, not_out, captain, keeper, source)
SELECT g.match_id, g.inn, g.pos, g.player_id, g.team,
       (SELECT b.how_out FROM batting b WHERE b.match_id = g.match_id AND b.inn = g.inn AND b.player_id = g.player_id
          AND b.source NOT IN ('gold', 'book') AND b.how_out IS NOT NULL ORDER BY {PRI} LIMIT 1),
       CASE WHEN g.caught_and_bowled = 1 THEN 'caught and bowled' ELSE g.kind END,
       g.bowler_id, g.fielder, g.runs, g.balls, g.minutes, g.fours, g.sixes, g.not_out, g.captain, g.keeper, 'gold'
FROM batting_gold g""")
nb = con.execute("SELECT changes()").fetchone()[0]

# bowling: overs written back from balls (with each match's balls per over)
bpo = dict(q("SELECT match_id, COALESCE(balls_per_over, 6) FROM matches"))
for mid, new in q("SELECT match_id, new FROM match_fix WHERE field = 'balls_per_over'"):
    bpo[mid] = int(new)
rows = []
for mid, inn, pid, team, balls, md, runs, wk in q("SELECT match_id, inn, player_id, team, balls, maidens, runs, wickets FROM bowling_gold"):
    b = bpo.get(mid, 6)
    ov = None if balls is None else (f"{balls // b}" if balls % b == 0 else f"{balls // b}.{balls % b}")
    rows.append((mid, inn, pid, team, ov, balls, md, runs, wk, "gold"))
con.executemany("INSERT INTO bowling (match_id, inn, player_id, team, overs, balls, maidens, runs, wickets, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

# innings: gold totals; overs and declared from the best source
con.execute(f"""
INSERT INTO innings (match_id, inn, team, runs, wickets, overs, extras, declared, source)
SELECT g.match_id, g.inn, g.team, g.runs, g.wickets,
       (SELECT i.overs FROM innings i WHERE i.match_id = g.match_id AND i.inn = g.inn AND i.source NOT IN ('gold', 'book')
          AND i.overs IS NOT NULL ORDER BY {PRI} LIMIT 1),
       g.extras,
       (SELECT MAX(i.declared) FROM innings i WHERE i.match_id = g.match_id AND i.inn = g.inn AND i.source NOT IN ('gold', 'book')),
       'gold'
FROM innings_gold g""")
ni = con.execute("SELECT changes()").fetchone()[0]

# every match with gold lines reads them
con.execute("UPDATE matches SET card_source = 'gold' WHERE match_id IN (SELECT DISTINCT match_id FROM batting_gold)")
nm = con.execute("SELECT changes()").fetchone()[0]

# player_stats (search + Ask box) from the gold careers
year = {m: (d or "")[:4] for m, d in q("SELECT match_id, start_date FROM matches")}
fmt = dict(q("SELECT match_id, format FROM matches"))
span = {}
for mid, pid in q("SELECT match_id, player_id FROM batting_gold UNION SELECT match_id, player_id FROM bowling_gold "
                  "UNION SELECT match_id, player_id FROM played WHERE source NOT IN ('book')"):
    k = (pid, fmt.get(mid))
    y = year.get(mid) or None
    if y:
        a, z = span.get(k, (y, y))
        span[k] = (min(a, y), max(z, y))
con.execute("DELETE FROM player_stats")
ps = [(pid, f, m, *(span.get((pid, f)) or (None, None)), r, w) for pid, f, m, r, w in
      q("SELECT player_id, format, matches, runs, wickets FROM player_stats_gold")]
con.executemany("INSERT INTO player_stats (player_id, format, matches, first_year, last_year, runs, wickets) VALUES (?,?,?,?,?,?,?)", ps)
con.commit()
print(f"gold published in {time.time() - t0:.0f} s: batting {nb}, bowling {len(rows)}, innings {ni} rows; "
      f"{nm} matches now read the gold scorecard; player_stats {len(ps)} rows")
for name in ("SR Tendulkar", "JH Kallis", "DG Bradman"):
    print("  ", name, q("SELECT s.format, s.matches, s.runs, s.wickets FROM player_stats s JOIN players p USING (player_id) WHERE p.name = ?", name))
