"""Consensus figures: every source votes, ball-by-ball votes only where that innings' ball-by-ball is complete.
Then answers hard questions and compares with the official record."""
import collections, sqlite3, sys
DB = sys.argv[1]
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: db.execute(s, a).fetchall()
name = {r[0]: r[1] for r in q("select player_id, name from players")}
meta = {r[0]: r[1:] for r in q("select match_id, format, ball_source, card_source, balls_per_over, start_date, number from matches")}
PRI = {"cricsheet": 0, "espn": 1, "davis": 2, "kaggle": 3, "book": 4, "bbb": -1}

# ball-by-ball recompute per innings, and completeness
inn_card = {}
for mid, inn, src, runs in q("select match_id, inn, source, runs from innings"):
    inn_card.setdefault((mid, inn), {})[src] = runs
bb_tot = collections.Counter(); bat_bb = collections.defaultdict(lambda: [0, 0]); bowl_bb = collections.defaultdict(lambda: [0, 0, 0, 0])
overs = collections.defaultdict(lambda: [0, 0, set()])
for mid, inn, ov, bo, bt, r, ex, et, legal, w, kind, out, src in q(
        "select match_id, inn, over_no, bowler_id, batter_id, bat_runs, extras, extra_type, legal, wicket, kind, out_id, source from balls"):
    if src != meta[mid][1]: continue
    r = r or 0; ex = ex or 0; et = (et or "").lower(); k = (kind or "").lower()
    bb_tot[(mid, inn)] += r + ex
    if et not in ("wd", "wide", "wides"):
        bat_bb[(mid, inn, bt)][0] += r; bat_bb[(mid, inn, bt)][1] += 1
    ch = r + (ex if et in ("wd", "wide", "wides", "nb", "noball", "no ball") else 0)
    b = bowl_bb[(mid, inn, bo)]; b[0] += 1 if legal else 0; b[1] += ch
    if w and not any(x in k for x in ("run out", "retired", "obstruct", "handled", "timed")): b[2] += 1
    o = overs[(mid, inn, ov)]; o[0] += 1 if legal else 0; o[1] += ch; o[2].add(bo)
for (mid, inn, ov), (lb, ch, bs) in overs.items():
    if lb >= (meta[mid][3] or 6) and ch == 0 and len(bs) == 1:
        bowl_bb[(mid, inn, next(iter(bs)))][3] += 1
complete = {k for k, t in bb_tot.items() if meta[k[0]][2] in inn_card.get(k, {}) and inn_card[k][meta[k[0]][2]] == t}

def vote(vals, bbb_counts=False):
    """bbb_counts=True for pure counts (balls, maidens) where a complete innings' ball-by-ball is exact."""
    if bbb_counts:
        return _vote(vals)
    cards = [(src, v) for src, v in vals if src != "bbb" and v is not None]
    if len({v for _, v in cards}) <= 1 and cards:
        vals = cards          # scorecards agree (or only one): ball-by-ball does not get a vote
    return _vote(vals)


def _vote(vals):
    """vals: list of (source, value). Majority; tie -> ball-by-ball if voting, else source priority."""
    vals = [(s, v) for s, v in vals if v is not None]
    if not vals: return None, "none"
    c = collections.Counter(v for _, v in vals)
    top = c.most_common()
    best = [v for v, n in top if n == top[0][1]]
    if len(best) == 1:
        return best[0], ("agree" if len(c) == 1 else "majority") if len(vals) > 1 else "single"
    for s, v in sorted(vals, key=lambda x: PRI.get(x[0], 9)):
        if v in best: return v, f"tie->{s}"

raw = q("select match_id, inn, player_id, source, runs, not_out from batting")
dv = {(m, i, p): r for m, i, p, s_, r, n in raw if s_ == "davis"}
agree, seen = collections.Counter(), collections.Counter()
for m, i, p, s_, r, n in raw:
    if s_ == "book" and (m, i, p) in dv and r is not None and dv[(m, i, p)] is not None:
        seen[m] += 1; agree[m] += r == dv[(m, i, p)]
bad_book = {m for m in seen if agree[m] / seen[m] < 0.8}
print(f"Wisden-book scorecards linked to the wrong match (agree <80% with Davis): {len(bad_book)} matches -> book rows ignored there")
bat = collections.defaultdict(list)
for mid, inn, pid, src, runs, nout in raw:
    if src == "book" and mid in bad_book: continue
    bat[(mid, inn, pid)].append((src, runs, nout))
bowl = collections.defaultdict(list)
for mid, inn, pid, src, balls, md, runs, wk in q("select match_id, inn, player_id, source, balls, maidens, runs, wickets from bowling"):
    if src == "book" and mid in bad_book: continue
    bowl[(mid, inn, pid)].append((src, balls, md, runs, wk))

how = collections.Counter()
def bat_consensus(ids, fmt):
    out = []
    for (mid, inn, pid), lst in bat.items():
        if pid not in ids or meta[mid][0] != fmt: continue
        votes = [(s, r) for s, r, _ in lst]
        if (mid, inn) in complete and (mid, inn, pid) in bat_bb: votes.append(("bbb", bat_bb[(mid, inn, pid)][0]))
        r, h = vote(votes); how[("bat", h.split("->")[0])] += 1
        no, _ = vote([(s, n) for s, _, n in lst])
        if r is None: continue
        out.append((mid, inn, r, bool(no)))
    return out

def bowl_consensus(ids, fmt):
    out = []
    for (mid, inn, pid), lst in bowl.items():
        if pid not in ids or meta[mid][0] != fmt: continue
        cb = [(s, b) for s, b, _, _, _ in lst]; cm = [(s, m) for s, _, m, _, _ in lst]
        cr = [(s, r) for s, _, _, r, _ in lst]; cw = [(s, w) for s, _, _, _, w in lst]
        if (mid, inn) in complete and (mid, inn, pid) in bowl_bb:
            lb, ch, wk, md = bowl_bb[(mid, inn, pid)]
            cb.append(("bbb", lb)); cm.append(("bbb", md)); cw.append(("bbb", wk))
        # balls: complete ball-by-ball votes (exact count); maidens: scorecards vote, ball-by-ball is only a check
        b, _ = vote(cb, True); m, h = vote(cm); r, _ = vote(cr); w, _ = vote(cw)
        how[("maidens", h.split("->")[0])] += 1
        out.append((mid, inn, b or 0, m or 0, r or 0, w or 0))
    return out

def ids(exact):
    return {p for p, n in name.items() if n == exact}

print("HARD QUESTIONS - consensus of all sources v the official record\n")
def batq(label, nm, fmt, off_runs, off_100s=None, off_avg=None):
    rows = bat_consensus(ids(nm), fmt)
    runs = sum(r for *_, r, _ in rows); inns = len(rows); no = sum(1 for *_, n in rows if n)
    h = sum(1 for *_, r, _ in rows if r >= 100); avg = runs / max(inns - no, 1)
    tests = len({m for m, *_ in rows})
    ok = "OK" if runs == off_runs and (off_100s is None or h == off_100s) else "DIFF"
    print(f"{ok:4} {label}: {runs} runs (official {off_runs}), {h} hundreds" + (f" (official {off_100s})" if off_100s else "")
          + f", avg {avg:.2f}" + (f" (official {off_avg})" if off_avg else "") + f", {tests} matches, {inns} innings")
def bowlq(label, nm, fmt, off_md=None, off_balls=None, off_wk=None):
    rows = bowl_consensus(ids(nm), fmt)
    md = sum(r[3] for r in rows); b = sum(r[2] for r in rows); w = sum(r[5] for r in rows)
    parts = [f"maidens {md}" + (f" (official {off_md})" if off_md else ""), f"balls {b}" + (f" (official {off_balls})" if off_balls else ""),
             f"wkts {w}" + (f" (official {off_wk})" if off_wk else "")]
    ok = all(x is None or x == y for x, y in ((off_md, md), (off_balls, b), (off_wk, w)))
    print(f"{'OK' if ok else 'DIFF':4} {label}: " + ", ".join(parts))

batq("1. Tendulkar Tests", "SR Tendulkar", "Test", 15921, 51)
batq("2. Bradman Tests", "DG Bradman", "Test", 6996, 29, 99.94)
bowlq("3a. Muralitharan Tests", "M Muralitharan", "Test", 1794, 44039, 800)
bowlq("3b. Warne Tests", "SK Warne", "Test", 1761, None, 708)
bowlq("3c. Kumble Tests", "A Kumble", "Test", 1576, None, 619)
bowlq("3d. McGrath Tests", "GD McGrath", "Test", 1470, 29248, 563)
batq("4. Gavaskar Tests", "SM Gavaskar", "Test", 10122, 34)
batq("5. Kallis Tests", "JH Kallis", "Test", 13289, 45)
bowlq("5b. Kallis Test wickets", "JH Kallis", "Test", None, None, 292)
rows = bat_consensus(ids("CA Walsh"), "Test")
print(f"{'OK' if sum(1 for *_, r, n in rows if r == 0 and not n) == 43 else 'DIFF':4} 6. Walsh Test ducks: {sum(1 for *_, r, n in rows if r == 0 and not n)} (official 43)")
batq("7. Tendulkar ODIs", "SR Tendulkar", "ODI", 18426, 49)
# best match figures
def match_figs(nm, fmt):
    agg = collections.defaultdict(lambda: [0, 0])
    for mid, inn, b, m, r, w in bowl_consensus(ids(nm), fmt): agg[mid][0] += w; agg[mid][1] += r
    return max(agg.items(), key=lambda x: (x[1][0], -x[1][1]))
m, (w, r) = match_figs("JC Laker", "Test"); print(f"{'OK' if (w, r) == (19, 90) else 'DIFF':4} 8. Laker best match: {w}/{r} in {meta[m][4]} (official 19/90, Old Trafford 1956)")
best = max(((mid, inn, r) for mid, inn, r, n in bat_consensus(ids("BC Lara"), "Test")), key=lambda x: x[2])
print(f"{'OK' if best[2] == 400 else 'DIFF':4} 9. Lara highest: {best[2]} on {meta[best[0]][4]} (official 400*)")
inns = [x for x in bowl_consensus(ids("A Kumble"), "Test") if x[5] == 10]
print(f"{'OK' if inns and inns[0][4] == 74 else 'DIFF':4} 10. Kumble 10 in an innings: " + ", ".join(f"10/{x[4]} on {meta[x[0]][4]}" for x in inns) + " (official 10/74)")
print("\nHow the votes were decided:", dict(how))
