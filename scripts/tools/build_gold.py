"""Gold layer: fix the 12 anomalies of BUILD_SPEC 22.10 in one go, on top of the existing database.

Nothing is deleted or overwritten. New tables are added to the database:
  book_link            which Wisden-book scorecards are linked to the right match (verified / ignored / unchecked)
  batting_gold         one row per batter-innings, every field chosen by vote, with how it was chosen
  bowling_gold         one row per bowler-innings (balls from complete ball-by-ball, the rest by vote)
  innings_gold         per innings: total, ball-by-ball source, does the ball-by-ball add up (complete?)
  balls_fix            corrections layered on `balls` (dismissal kind filled from the scorecard; strike rebalanced
                       where replaying the strike rules makes every batter's runs match the scorecard)
  source_disagreements every field where sources differ: all values, the chosen one, why
  player_stats_gold    career figures per player and format from the gold tables
  official_check       hard questions v the official record
and writes data/build_report_gold.txt.

Usage (repo root):  python3 scripts/tools/build_gold.py data/everyball.db
"""
import collections, json, os, re, sqlite3, sys, time

DB = sys.argv[1] if len(sys.argv) > 1 else "data/everyball.db"
REPORT = os.path.join(os.path.dirname(os.path.abspath(DB)), "build_report_gold.txt")
t0 = time.time()
con = sqlite3.connect(DB)
q = lambda s, *a: con.execute(s, a).fetchall()
log = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); log.append(s)

PRI = {"cricsheet": 0, "espn": 1, "davis": 2, "kaggle": 3, "book": 4}
name = {r[0]: r[1] for r in q("select player_id, name from players")}
meta = {r[0]: {"fmt": r[1], "ball": r[2], "card": r[3], "bpo": r[4] or 6, "date": r[5], "no": r[6]}
        for r in q("select match_id, format, ball_source, card_source, balls_per_over, start_date, number from matches")}

# ---------------------------------------------------------------- balls per over (anomaly 13)
# 8-ball overs ended after the 1979-80 season. Before that, trust what the scorecards themselves imply.
match_fix = []
_imp = collections.defaultdict(collections.Counter)
for mid, ov, b in q("select match_id, overs, balls from bowling where overs is not null and balls is not null"):
    m_ = re.match(r"^(\d+)(?:\.(\d))?$", str(ov))
    if not m_ or int(m_.group(1)) < 3: continue
    for bpo in (4, 5, 6, 8):
        if int(m_.group(1)) * bpo + int(m_.group(2) or 0) == b: _imp[mid][bpo] += 1
for mid, m in meta.items():
    old = m["bpo"]; new = old
    if (m["date"] or "") >= "1980-06-01": new = 6
    elif _imp[mid]:
        top = _imp[mid].most_common()
        if len(top) == 1 or top[0][1] > top[1][1]: new = top[0][0]
    if new != old:
        match_fix.append((mid, "balls_per_over", str(old), str(new), "8-ball overs ended in 1979-80" if new == 6 else "the scorecards' overs and balls imply it"))
        m["bpo"] = new
con.execute("drop table if exists match_fix")
con.execute("create table match_fix (match_id text, field text, old text, new text, reason text)")
con.executemany("insert into match_fix values (?,?,?,?,?)", match_fix)
say(f"0/8 balls-per-over corrected in {len(match_fix)} matches")

# ---------------------------------------------------------------- dismissal wording (anomalies 5, 6)
BOWLED_HOW = {"edg": "inside edge onto the stumps", "pad": "off the pad onto the stumps", "ns": "no stroke offered",
              "edgns": "inside edge, no stroke", "padns": "off the pad, no stroke", "edg/ns": "inside edge, no stroke",
              "pad/ns": "off the pad, no stroke"}
def norm_kind(k):
    """-> (kind, caught_and_bowled, bowled_how_raw)"""
    k = (k or "").strip().lower()
    if not k: return None, 0, None
    m = re.match(r"^b-([a-z0-9/]*)", k)
    if m: return "bowled", 0, (m.group(1) or None)
    if k in ("caught and bowled", "c&b", "c and b"): return "caught", 1, None
    if k == "hit": return "hit wicket", 0, None
    if k == "retired": return "retired hurt", 0, None
    if k in ("handled", "the ball"): return "handled the ball", 0, None
    return k, 0, None

def vote(vals):
    """vals: [(source, value)]. Majority; tie -> source priority. Returns (value, how)."""
    vals = [(s, v) for s, v in vals if v is not None and v != ""]
    if not vals: return None, "none"
    c = collections.Counter(v for _, v in vals)
    top = c.most_common(); best = [v for v, n in top if n == top[0][1]]
    if len(vals) == 1: return vals[0][1], "single"
    if len(c) == 1: return best[0], "agree"
    if len(best) == 1: return best[0], "majority"
    for s, v in sorted(vals, key=lambda x: PRI.get(x[0], 9)):
        if v in best: return v, f"tie->{s}"

# ---------------------------------------------------------------- 1. book links (anomaly 1)
say("1/8 Checking which Wisden-book scorecards sit on the right match ...")
braw = q("select match_id, inn, pos, player_id, team, how_out, kind, bowler_id, fielder, runs, balls, minutes, fours, sixes, not_out, captain, keeper, source from batting")
dv = {(r[0], r[1], r[3]): r[9] for r in braw if r[17] == "davis"}
seen, agree = collections.Counter(), collections.Counter()
book_matches = set()
for r in braw:
    if r[17] != "book": continue
    book_matches.add(r[0])
    k = (r[0], r[1], r[3])
    if k in dv and r[9] is not None and dv[k] is not None:
        seen[r[0]] += 1; agree[r[0]] += r[9] == dv[k]
book_status = {}
for m in book_matches:
    if seen[m] == 0: book_status[m] = ("unchecked", None)
    else:
        rate = agree[m] / seen[m]
        book_status[m] = ("verified" if rate >= 0.8 else "ignored", round(rate, 3))
con.execute("drop table if exists book_link")
con.execute("create table book_link (match_id text primary key, status text, agree_rate real)")
con.executemany("insert into book_link values (?,?,?)", [(m, s, r) for m, (s, r) in book_status.items()])
say(f"    book scorecards: {sum(1 for s,_ in book_status.values() if s=='verified')} verified, "
    f"{sum(1 for s,_ in book_status.values() if s=='ignored')} ignored (wrong match), {sum(1 for s,_ in book_status.values() if s=='unchecked')} unchecked (no Davis to compare)")

# ---------------------------------------------------------------- 2. batting_gold (anomalies 5, 6, 9)
say("2/8 Choosing every batting field by vote ...")
bat = collections.defaultdict(list)
for r in braw:
    if r[17] == "book" and book_status.get(r[0], ("",))[0] == "ignored": continue
    bat[(r[0], r[1], r[3])].append(r)
FIELDS = {"runs": 9, "balls": 10, "minutes": 11, "fours": 12, "sixes": 13, "not_out": 14, "bowler_id": 7}
gold_bat, disagree = [], []
how_count = collections.Counter()
for (mid, inn, pid), rows in bat.items():
    out = {}
    for f, i in FIELDS.items():
        vals = [(r[17], r[i]) for r in rows]
        v, how = vote(vals); out[f] = v; how_count[(f, how.split("->")[0])] += 1
        distinct = {x for _, x in vals if x is not None and x != ""}
        if len(distinct) > 1:
            disagree.append(("batting", mid, inn, pid, f, json.dumps({s: x for s, x in vals}), str(v), how))
    kinds = [(r[17], norm_kind(r[6])) for r in rows]
    k, how = vote([(s, kk[0]) for s, kk in kinds]); out["kind"] = k
    distinct = {kk[0] for _, kk in kinds if kk[0]}
    if len(distinct) > 1: disagree.append(("batting", mid, inn, pid, "kind", json.dumps({s: kk[0] for s, kk in kinds}), str(k), how))
    cnb = max(kk[1] for _, kk in kinds)
    raw_how = next((kk[2] for s, kk in kinds if kk[2]), None)
    bowled_how = BOWLED_HOW.get(raw_how, f"Davis code '{raw_how}'" if raw_how else None) if k == "bowled" else None
    best = sorted(rows, key=lambda r: PRI.get(r[17], 9))[0]
    srcs = ",".join(sorted({r[17] for r in rows}, key=lambda s: PRI.get(s, 9)))
    gold_bat.append((mid, inn, best[2], pid, best[4], out["runs"], out["balls"], out["minutes"], out["fours"], out["sixes"],
                     out["not_out"], k, cnb, bowled_how, out["bowler_id"], best[8], max(r[15] or 0 for r in rows),
                     max(r[16] or 0 for r in rows), srcs, "single" if len(rows) == 1 else "vote"))
con.execute("drop table if exists batting_gold")
con.execute("""create table batting_gold (match_id text, inn int, pos int, player_id int, team text, runs int, balls int,
  minutes int, fours int, sixes int, not_out int, kind text, caught_and_bowled int, bowled_how text, bowler_id int,
  fielder text, captain int, keeper int, sources text, chosen text)""")
con.executemany("insert into batting_gold values (" + ",".join("?" * 20) + ")", gold_bat)
con.execute("create index if not exists ix_bg on batting_gold(match_id, inn, player_id)")
con.execute("create index if not exists ix_bg_p on batting_gold(player_id)")
say(f"    {len(gold_bat)} batter-innings; single source {sum(1 for g in gold_bat if g[19]=='single')}; "
    f"bowled with detail: {sum(1 for g in gold_bat if g[13])}")
gb = {(g[0], g[1], g[3]): g for g in gold_bat}

# ---------------------------------------------------------------- 3. innings_gold + ball-by-ball completeness (anomaly 3)
say("3/8 Checking whether each innings' ball-by-ball adds up ...")
icard = collections.defaultdict(list)
for mid, inn, team, runs, wk, ov, ex, dec, src in q("select match_id, inn, team, runs, wickets, overs, extras, declared, source from innings"):
    if src == "book" and book_status.get(mid, ("",))[0] == "ignored": continue
    icard[(mid, inn)].append((src, team, runs, wk, ov, ex, dec))
bbtot = {}
for mid, inn, src, runs, legal, unsure, n in q("""select match_id, inn, source, sum(coalesce(bat_runs,0)+coalesce(extras,0)),
        sum(legal), sum(sure=0), count(*) from balls group by match_id, inn, source"""):
    if meta.get(mid, {}).get("ball") == src: bbtot[(mid, inn)] = (src, runs, legal, unsure, n)
gold_inn = []
complete = set()
for (mid, inn), rows in icard.items():
    runs, how = vote([(r[0], r[2]) for r in rows]); wk, _ = vote([(r[0], r[3]) for r in rows])
    ov, _ = vote([(r[0], str(r[4]) if r[4] is not None else None) for r in rows])
    card_balls = None
    if ov:
        m_ = re.match(r"^(\d+)(?:\.(\d))?$", ov.strip())
        if m_: card_balls = int(m_.group(1)) * meta[mid]["bpo"] + int(m_.group(2) or 0)
    ex, _ = vote([(r[0], r[5]) for r in rows]); team = sorted(rows, key=lambda r: PRI.get(r[0], 9))[0][1]
    b = bbtot.get((mid, inn))
    comp = 1 if (b and runs is not None and b[1] == runs and (card_balls is None or b[2] == card_balls)) else 0 if b else None
    if comp: complete.add((mid, inn))
    gold_inn.append((mid, inn, team, runs, wk, ex, how, b[0] if b else None, b[1] if b else None, comp,
                     b[2] if b else None, b[3] if b else None))
con.execute("drop table if exists innings_gold")
con.execute("""create table innings_gold (match_id text, inn int, team text, runs int, wickets int, extras int, chosen text,
  bbb_source text, bbb_runs int, bbb_complete int, bbb_legal_balls int, bbb_unsure_balls int)""")
con.executemany("insert into innings_gold values (" + ",".join("?" * 12) + ")", gold_inn)
part = collections.Counter(g[7] for g in gold_inn if g[9] == 0)
say(f"    innings with ball-by-ball: {sum(1 for g in gold_inn if g[7])}; complete (runs AND balls add up) {len(complete)}; partial by source: {dict(part)}")

# ---------------------------------------------------------------- 4. balls_fix: kinds + strike rebalance (anomalies 2, 4)
say("4/8 Filling dismissal kinds and rebalancing strike where the rules fix the batter totals ...")
con.execute("drop table if exists balls_fix")
con.execute("""create table balls_fix (match_id text, inn int, seq int, batter_id int, non_striker_id int, kind text,
  fix text)""")
fixes = []
# 4a. dismissal kinds from the scorecard
for mid, inn, seq, out, kind, src in q("select match_id, inn, seq, out_id, kind, source from balls where wicket=1 and (kind is null or kind='')"):
    if meta.get(mid, {}).get("ball") != src: continue
    g = gb.get((mid, inn, out))
    if g and g[11]: fixes.append((mid, inn, seq, None, None, g[11], "kind_from_scorecard"))
n_kind = len(fixes)
# 4b. which complete innings have batter runs that differ from the gold scorecard?
bb_runs = collections.defaultdict(int)
for mid, inn, bt, r, src in q("select match_id, inn, batter_id, bat_runs, source from balls"):
    if meta.get(mid, {}).get("ball") == src and (mid, inn) in complete: bb_runs[(mid, inn, bt)] += r or 0
need = set()
for (mid, inn, bt), r in bb_runs.items():
    g = gb.get((mid, inn, bt))
    if g and g[5] is not None and g[5] != r: need.add((mid, inn))
say(f"    dismissal kinds filled: {n_kind}; complete innings with wrong batter attribution: {len(need)}")
RAN_EXTRA = {"b", "lb", "e", "pen"}
def replay(balls, order, first, second):
    """Replay strike by the rules. balls: [(seq, over_no, legal, bat_runs, extras, extra_type, wicket, out_id)]"""
    s, ns = first, second; queue = [p for p in order if p not in (first, second)]
    runs = collections.Counter(); assign = []
    for i, (seq, ov, legal, r, ex, et, w, out) in enumerate(balls):
        assign.append((seq, s, ns)); runs[s] += r or 0
        et = (et or "").lower(); ex = ex or 0
        ran = (r or 0) + (ex if et in RAN_EXTRA else 0) + (max(ex - 1, 0) if et == "wd" else 0)
        if w:
            gone = out if out in (s, ns) else s
            new = queue.pop(0) if queue else None
            if gone == s: s = new
            else: ns = new
        elif ran % 2: s, ns = ns, s
        if i + 1 < len(balls) and balls[i + 1][1] != ov: s, ns = ns, s
    return runs, assign
fixed = unsure = 0
for (mid, inn) in sorted(need):
    src = meta[mid]["ball"]
    rows = q("""select seq, over_no, legal, bat_runs, extras, extra_type, wicket, out_id, batter_id, non_striker_id
                from balls where match_id=? and inn=? and source=? order by seq""", mid, inn, src)
    if not rows: continue
    order = [g[3] for g in sorted((g for k, g in gb.items() if k[0] == mid and k[1] == inn), key=lambda g: (g[2] or 99))]
    first = rows[0][8]; second = rows[0][9] or next((p for p in order if p != first), None)
    card = {g[3]: g[5] for k, g in gb.items() if k[0] == mid and k[1] == inn}
    runs, assign = replay([r[:8] for r in rows], order, first, second)
    batted = [p for p in card if card[p] is not None and (runs.get(p, 0) or bb_runs.get((mid, inn, p), 0) or card[p])]
    if all(runs.get(p, 0) == card[p] for p in batted):
        for (seq, s, ns), r in zip(assign, rows):
            if s != r[8] or (ns and ns != r[9]): fixes.append((mid, inn, seq, s, ns, None, "strike_rebalanced"))
        fixed += 1
    else:
        unsure += 1
        fixes.append((mid, inn, None, None, None, None, "attribution_unsure"))
con.executemany("insert into balls_fix values (?,?,?,?,?,?,?)", fixes)
con.execute("create index if not exists ix_bf on balls_fix(match_id, inn, seq)")
say(f"    strike rebalanced (every batter now matches the scorecard): {fixed} innings; still unsure (flagged): {unsure}")

# ---------------------------------------------------------------- 5. bowling_gold (anomalies 7, 8, 10)
say("5/8 Choosing bowling fields (scorecards vote; complete ball-by-ball breaks ties) ...")
bb_bowl = collections.defaultdict(int)
ovr = collections.defaultdict(lambda: [0, 0, set()])
for mid, inn, ov, bo, legal, r, ex, et, src in q("select match_id, inn, over_no, bowler_id, legal, bat_runs, extras, extra_type, source from balls"):
    if meta.get(mid, {}).get("ball") == src and (mid, inn) in complete:
        bb_bowl[(mid, inn, bo)] += legal or 0
        o = ovr[(mid, inn, ov)]; o[0] += legal or 0
        o[1] += (r or 0) + ((ex or 0) if (et or "") in ("wd", "nb") else 0); o[2].add(bo)
bb_md = collections.Counter()
for (mid, inn, ov), (lb, ch, bs) in ovr.items():
    if lb >= meta[mid]["bpo"] and ch == 0 and len(bs) == 1: bb_md[(mid, inn, next(iter(bs)))] += 1
MPRI = {"espn": 0, "davis": 1, "kaggle": 2, "book": 3, "cricsheet": 4}
def vote_maidens(vals, key):
    vals = [(s_, v) for s_, v in vals if v is not None]
    if len({v for _, v in vals}) <= 1 or key not in bb_bowl:
        global PRI
        old = PRI; PRI = MPRI
        try: return vote(vals)
        finally: PRI = old
    v, how = vote(vals + [("bbb", bb_md.get(key, 0))])
    return v, how + "+bbb"
bw = collections.defaultdict(list)
unit_fix = 0
def balls_from_overs(ov, bpo):
    m_ = re.match(r"^(\d+)(?:\.(\d))?$", str(ov).strip()) if ov is not None else None
    return int(m_.group(1)) * bpo + int(m_.group(2) or 0) if m_ else None
for r in q("select match_id, inn, player_id, team, overs, balls, maidens, runs, wickets, source from bowling"):
    if r[9] == "book" and book_status.get(r[0], ("",))[0] == "ignored": continue
    r = list(r)
    fo = balls_from_overs(r[4], meta.get(r[0], {}).get("bpo", 6))
    if fo is not None and r[5] != fo:
        # the overs figure is the scorecard's own number; a balls value that differs (often the overs figure typed
        # into the balls column) is replaced, and the change is logged
        disagree.append(("bowling", r[0], r[1], r[2], "balls_unit", json.dumps({r[9]: r[5], "from_overs": fo}), str(fo), "balls taken from the overs figure"))
        r[5] = fo; unit_fix += 1
    bw[(r[0], r[1], r[2])].append(tuple(r))
say(f"    balls column fixed from the overs figure in {unit_fix} rows")
gold_bowl = []
for (mid, inn, pid), rows in bw.items():
    vals = {f: [(r[9], r[i]) for r in rows] for f, i in (("balls", 5), ("maidens", 6), ("runs", 7), ("wickets", 8))}
    # balls: scorecards vote; complete ball-by-ball only breaks a tie (it can credit an over to the wrong bowler)
    cardb = [(s_, v) for s_, v in vals["balls"] if v is not None]
    if len({v for _, v in cardb}) > 1 and (mid, inn, pid) in bb_bowl:
        balls, bhow = vote(cardb + [("bbb", bb_bowl[(mid, inn, pid)])]); bhow += "+bbb"
    elif not cardb and (mid, inn, pid) in bb_bowl:
        balls, bhow = bb_bowl[(mid, inn, pid)], "ball-by-ball only"
    else:
        balls, bhow = vote(cardb)
    if (mid, inn, pid) in bb_bowl and balls is not None and bb_bowl[(mid, inn, pid)] != balls:
        disagree.append(("bowling", mid, inn, pid, "balls_bbb_v_card", json.dumps({"bbb": bb_bowl[(mid, inn, pid)]}), str(balls), "ball-by-ball credits a different number of balls to this bowler"))
    md, mhow = vote_maidens(vals["maidens"], (mid, inn, pid)); rn, rhow = vote(vals["runs"]); wk, whow = vote(vals["wickets"])
    for f, v, how in (("balls", balls, bhow), ("maidens", md, mhow), ("runs", rn, rhow), ("wickets", wk, whow)):
        distinct = {x for _, x in vals[f] if x is not None}
        if f == "balls" and bhow == "ball-by-ball": distinct.add(balls)
        if len(distinct) > 1: disagree.append(("bowling", mid, inn, pid, f, json.dumps({s: x for s, x in vals[f]}), str(v), how))
    best = sorted(rows, key=lambda r: PRI.get(r[9], 9))[0]
    gold_bowl.append((mid, inn, pid, best[3], balls, md, rn, wk, bhow, mhow,
                      ",".join(sorted({r[9] for r in rows}, key=lambda s: PRI.get(s, 9)))))
con.execute("drop table if exists bowling_gold")
con.execute("""create table bowling_gold (match_id text, inn int, player_id int, team text, balls int, maidens int, runs int,
  wickets int, balls_from text, maidens_from text, sources text)""")
con.executemany("insert into bowling_gold values (" + ",".join("?" * 11) + ")", gold_bowl)
con.execute("create index if not exists ix_bwg on bowling_gold(player_id)")
say(f"    {len(gold_bowl)} bowler-innings; ball-by-ball broke a tie on balls {sum(1 for g in gold_bowl if 'bbb' in (g[8] or ''))}, "
    f"only source {sum(1 for g in gold_bowl if g[8]=='ball-by-ball only')}")

# ---------------------------------------------------------------- 6. disagreements
say("6/8 Writing the disagreement list ...")
con.execute("drop table if exists source_disagreements")
con.execute("""create table source_disagreements (tbl text, match_id text, inn int, player_id int, field text,
  values_json text, chosen text, how text)""")
con.executemany("insert into source_disagreements values (?,?,?,?,?,?,?,?)", disagree)
dc = collections.Counter((d[0], d[4]) for d in disagree)
say("    " + ", ".join(f"{t}.{f}: {n}" for (t, f), n in sorted(dc.items())))

# ---------------------------------------------------------------- 7. player_stats_gold
say("7/8 Career figures from the gold tables ...")
ps = collections.defaultdict(lambda: collections.Counter())
mts = collections.defaultdict(set)
for g in gold_bat:
    mid, pid = g[0], g[3]; f = meta[mid]["fmt"]; c = ps[(pid, f)]; mts[(pid, f)].add(mid)
    if g[5] is None: continue
    c["innings"] += 1; c["runs"] += g[5]; c["not_outs"] += 1 if g[10] else 0
    c["hundreds"] += g[5] >= 100; c["fifties"] += 50 <= g[5] < 100
    c["ducks"] += 1 if (g[5] == 0 and not g[10]) else 0; c["balls_faced"] += g[6] or 0
    c["high"] = max(c["high"], g[5])
for g in gold_bowl:
    mid, pid = g[0], g[2]; f = meta[mid]["fmt"]; c = ps[(pid, f)]; mts[(pid, f)].add(mid)
    c["balls_bowled"] += g[4] or 0; c["maidens"] += g[5] or 0; c["runs_conceded"] += g[6] or 0; c["wickets"] += g[7] or 0
con.execute("drop table if exists player_stats_gold")
con.execute("""create table player_stats_gold (player_id int, format text, matches int, innings int, runs int, not_outs int,
  average real, hundreds int, fifties int, ducks int, high int, balls_faced int, balls_bowled int, maidens int,
  runs_conceded int, wickets int)""")
rows = []
for (pid, f), c in ps.items():
    outs = c["innings"] - c["not_outs"]
    rows.append((pid, f, len(mts[(pid, f)]), c["innings"], c["runs"], c["not_outs"], round(c["runs"] / outs, 2) if outs else None,
                 c["hundreds"], c["fifties"], c["ducks"], c["high"], c["balls_faced"], c["balls_bowled"], c["maidens"],
                 c["runs_conceded"], c["wickets"]))
con.executemany("insert into player_stats_gold values (" + ",".join("?" * 16) + ")", rows)
say(f"    {len(rows)} player-format rows")

# ---------------------------------------------------------------- 8. official checks
say("8/8 Hard questions v the official record ...")
byname = collections.defaultdict(set)
for p, n in name.items(): byname[n].add(p)
def stat(nm, f):
    t = collections.Counter()
    for pid in byname[nm]:
        for r in q("select innings, runs, not_outs, hundreds, ducks, balls_bowled, maidens, wickets from player_stats_gold where player_id=? and format=?", pid, f):
            for k, v in zip(("innings", "runs", "not_outs", "hundreds", "ducks", "balls_bowled", "maidens", "wickets"), r): t[k] += v or 0
    return t
checks = []
def chk(label, ours, official):
    checks.append((label, str(ours), str(official), "OK" if ours == official else "DIFF"))
s = stat("SR Tendulkar", "Test"); chk("Tendulkar Test runs", s["runs"], 15921); chk("Tendulkar Test hundreds", s["hundreds"], 51)
s = stat("DG Bradman", "Test"); chk("Bradman Test runs", s["runs"], 6996); chk("Bradman Test average", round(s["runs"] / (s["innings"] - s["not_outs"]), 2), 99.94)
s = stat("SM Gavaskar", "Test"); chk("Gavaskar Test runs", s["runs"], 10122); chk("Gavaskar Test hundreds", s["hundreds"], 34)
s = stat("CA Walsh", "Test"); chk("Walsh Test ducks", s["ducks"], 43)
s = stat("SR Tendulkar", "ODI"); chk("Tendulkar ODI runs", s["runs"], 18426); chk("Tendulkar ODI hundreds", s["hundreds"], 49)
s = stat("M Muralitharan", "Test"); chk("Muralitharan Test wickets", s["wickets"], 800); chk("Muralitharan Test balls", s["balls_bowled"], 44039); chk("Muralitharan Test maidens", s["maidens"], 1794)
s = stat("SK Warne", "Test"); chk("Warne Test wickets", s["wickets"], 708); chk("Warne Test maidens", s["maidens"], 1761)
s = stat("A Kumble", "Test"); chk("Kumble Test wickets", s["wickets"], 619); chk("Kumble Test maidens", s["maidens"], 1576)
s = stat("GD McGrath", "Test"); chk("McGrath Test wickets", s["wickets"], 563); chk("McGrath Test balls", s["balls_bowled"], 29248); chk("McGrath Test maidens", s["maidens"], 1470)
s = stat("JH Kallis", "Test"); chk("Kallis Test runs", s["runs"], 13289); chk("Kallis Test wickets", s["wickets"], 292)
hi = q("select max(b.runs) from batting_gold b where b.player_id in (%s)" % ",".join(map(str, byname["BC Lara"]))); chk("Lara highest Test score", hi[0][0], 400)
con.execute("drop table if exists official_check")
con.execute("create table official_check (question text, ours text, official text, status text)")
con.executemany("insert into official_check values (?,?,?,?)", checks)
for c in checks: say(f"    {c[3]:4} {c[0]}: {c[1]} (official {c[2]})")
con.commit()
say(f"Done in {time.time()-t0:.0f} s. Gold tables added; nothing deleted.")
open(REPORT, "w", encoding="utf-8").write("\n".join(log) + "\n")
print("Report:", REPORT)
