"""
Career check: is every player's career right, match by match? (Everyball, 18 Nov 2026)

Compares the gold scorecards (what the site shows) with ESPNcricinfo's own scorecards ('espn_live', every match since
1877), line by line, and every player's list of matches with ESPN's playing XIs. Read-only.

  python3 scripts/tools/career_check.py data/everyball.db

Writes data/reports/career_check.txt (summary) and data/reports/career_differences.csv (every difference).
What is checked:
  1. every batting line: runs, balls, 4s, 6s, out / not out
  2. every bowling line: balls, maidens, runs, wickets
  3. every player's matches: in our XI but not ESPN's, or the other way round
  4. career totals per player and format: matches, runs, wickets (ours v ESPN's)
  5. 20 random careers (20+ matches) checked line by line, in full
"""
import collections, csv, os, random, sqlite3, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "data/everyball.db"
OUT = os.path.join(os.path.dirname(os.path.abspath(DB)), "reports")
os.makedirs(OUT, exist_ok=True)
con = sqlite3.connect(DB)
q = lambda s, *a: con.execute(s, a).fetchall()
name = dict(q("select player_id, name from players"))
fmt = dict(q("select match_id, format from matches"))
lines, diffs = [], []
say = lambda s: (print(s, flush=True), lines.append(s))

# 1. batting lines
E = {(m, i, p): (r, b, f4, f6, no) for m, i, p, r, b, f4, f6, no in
     q("select match_id, inn, player_id, runs, balls, fours, sixes, not_out from batting where source='espn_live'")}
G = {(m, i, p): (r, b, f4, f6, no) for m, i, p, r, b, f4, f6, no in
     q("select match_id, inn, player_id, runs, balls, fours, sixes, not_out from batting_gold")}
F = ("runs", "balls", "4s", "6s", "not out")
cmp_ = collections.Counter(); bad = collections.Counter()
for k, e in E.items():
    g = G.get(k)
    if g is None:
        bad["line missing in ours"] += 1
        diffs.append(("batting", k[0], k[1], k[2], name.get(k[2]), "line", "missing in ours", str(e)))
        continue
    for f, a, b in zip(F, g, e):
        if a is None or b is None:
            continue
        cmp_[f] += 1
        if a != b:
            bad[f] += 1
            diffs.append(("batting", k[0], k[1], k[2], name.get(k[2]), f, a, b))
for k in G.keys() - E.keys():
    if k[0] in {m for m, _, _ in E}:              # ESPN has the match but not this line
        bad["line only in ours"] += 1
        diffs.append(("batting", k[0], k[1], k[2], name.get(k[2]), "line", "only in ours", str(G[k])))
say(f"1. BATTING lines compared with ESPN: {len(E)}")
for f in F:
    say(f"   {f:8} {cmp_[f] - bad[f]:7} agree, {bad[f]:5} differ ({100 * (cmp_[f] - bad[f]) / max(1, cmp_[f]):.3f}%)")
say(f"   lines ESPN has that we lack: {bad['line missing in ours']}; lines we have in ESPN matches that ESPN lacks: {bad['line only in ours']}")

# 2. bowling lines
E2 = {(m, i, p): (b, md, r, w) for m, i, p, b, md, r, w in
      q("select match_id, inn, player_id, balls, maidens, runs, wickets from bowling where source='espn_live'")}
G2 = {(m, i, p): (b, md, r, w) for m, i, p, b, md, r, w in
      q("select match_id, inn, player_id, balls, maidens, runs, wickets from bowling_gold")}
F2 = ("balls", "maidens", "runs conceded", "wickets")
cmp2 = collections.Counter(); bad2 = collections.Counter()
for k, e in E2.items():
    g = G2.get(k)
    if g is None:
        bad2["line missing in ours"] += 1
        diffs.append(("bowling", k[0], k[1], k[2], name.get(k[2]), "line", "missing in ours", str(e)))
        continue
    for f, a, b in zip(F2, g, e):
        if a is None or b is None:
            continue
        cmp2[f] += 1
        if a != b:
            bad2[f] += 1
            diffs.append(("bowling", k[0], k[1], k[2], name.get(k[2]), f, a, b))
say(f"2. BOWLING lines compared with ESPN: {len(E2)}")
for f in F2:
    say(f"   {f:13} {cmp2[f] - bad2[f]:7} agree, {bad2[f]:5} differ ({100 * (cmp2[f] - bad2[f]) / max(1, cmp2[f]):.3f}%)")
say(f"   lines ESPN has that we lack: {bad2['line missing in ours']}")

# 3. matches per player: ours (gold lines + every XI) v ESPN's XI
ours = collections.defaultdict(set)
for m, p in q("select match_id, player_id from batting_gold union select match_id, player_id from bowling_gold "
              "union select match_id, player_id from played where source != 'book'"):
    ours[p].add(m)
espn_m = {m for (m,) in q("select distinct match_id from played where source='espn_live'")}
espn = collections.defaultdict(set)
for m, p in q("select match_id, player_id from played where source='espn_live'"):
    espn[p].add(m)
only_ours = only_espn = 0
for p in set(ours) | set(espn):
    a = {m for m in ours.get(p, ()) if m in espn_m}
    b = espn.get(p, set())
    for m in sorted(a - b):
        only_ours += 1; diffs.append(("match", m, None, p, name.get(p), "played", "ours only", ""))
    for m in sorted(b - a):
        only_espn += 1; diffs.append(("match", m, None, p, name.get(p), "played", "ESPN only", ""))
say(f"3. PLAYER-MATCHES: in our XI but not ESPN's {only_ours}; in ESPN's XI but not ours {only_espn}")
nm_all = len({m for (m,) in q("select match_id from matches")}); say(f"   matches ESPN has a scorecard/XI for: {len(espn_m)} of our {nm_all}")

# 4. career totals
tot_e = collections.defaultdict(collections.Counter)
for m, p, r in q("select match_id, player_id, runs from batting where source='espn_live'"):
    tot_e[(p, fmt.get(m))]["runs"] += r or 0
for m, p, w in q("select match_id, player_id, wickets from bowling where source='espn_live'"):
    tot_e[(p, fmt.get(m))]["wickets"] += w or 0
for p, ms in espn.items():
    for m in ms:
        tot_e[(p, fmt.get(m))]["matches"] += 1
car_bad = collections.Counter(); worst = []
for p, f, mt, r, w in q("select player_id, format, matches, runs, wickets from player_stats_gold"):
    e = tot_e.get((p, f))
    if not e:
        continue
    for k, ours_v in (("matches", mt), ("runs", r), ("wickets", w)):
        if (ours_v or 0) != e[k]:
            car_bad[k] += 1
            worst.append((abs((ours_v or 0) - e[k]), name.get(p), f, k, ours_v, e[k]))
n_car = sum(1 for _ in tot_e)
say(f"4. CAREERS (player x format) compared: {n_car}; differ on matches {car_bad['matches']}, runs {car_bad['runs']}, wickets {car_bad['wickets']}")
for d, nm, f, k, a, b in sorted(worst, reverse=True)[:15]:
    say(f"   {nm} {f} {k}: ours {a}, ESPN {b}")

# 5. 20 random careers, in full
random.seed(18)
big = [p for (p,) in q("select player_id from player_stats_gold group by player_id having sum(matches) >= 20")]
say("5. TWENTY RANDOM CAREERS, every line:")
for p in random.sample(big, min(20, len(big))):
    keys = [k for k in E if k[2] == p] + [k for k in E2 if k[2] == p]
    okb = sum(1 for k in keys if (k in G and G[k] == E[k]) or (k in G2 and G2[k] == E2.get(k)))
    mm = len(ours.get(p, set()) ^ espn.get(p, set()) & espn_m) if p in espn else None
    say(f"   {name.get(p, p)}: {okb}/{len(keys)} lines exactly equal to ESPN; matches differing {len((ours.get(p, set()) & espn_m) ^ espn.get(p, set()))}")

with open(os.path.join(OUT, "career_differences.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["table", "match_id", "inn", "player_id", "player", "field", "ours", "espn"]); w.writerows(diffs)
open(os.path.join(OUT, "career_check.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("reports: data/reports/career_check.txt, data/reports/career_differences.csv")
