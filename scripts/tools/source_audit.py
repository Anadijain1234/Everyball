"""Cross-check every source against every other source, field by field, and against the ball-by-ball.
Read-only on the database. Prints a report and writes CSVs of anomalies to an output folder.
Usage: python3 source_audit.py <everyball.db> <out_dir>
"""
import collections, csv, os, sqlite3, sys, itertools

DB, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: db.execute(s, a).fetchall()
name = {r[0]: r[1] for r in q("select player_id, name from players")}
fmt_of = {r[0]: r[1] for r in q("select match_id, format from matches")}
bpo = {r[0]: (r[1] or 6) for r in q("select match_id, balls_per_over from matches")}
card_src = {r[0]: r[1] for r in q("select match_id, card_source from matches")}
ball_src = {r[0]: r[1] for r in q("select match_id, ball_source from matches")}
mdate = {r[0]: r[1] for r in q("select match_id, start_date from matches")}

def write(fn, header, rows):
    with open(os.path.join(OUT, fn), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

# ---------------- A. batting: field agreement between sources ----------------
BF = ["runs", "balls", "fours", "sixes", "minutes", "not_out", "kind", "bowler_id"]
bat = collections.defaultdict(dict)   # (mid, inn, pid) -> {src: {field: value}}
for row in q("select match_id, inn, player_id, source, runs, balls, fours, sixes, minutes, not_out, kind, bowler_id from batting"):
    mid, inn, pid, src = row[:4]
    d = dict(zip(BF, row[4:]))
    if d["kind"]: d["kind"] = d["kind"].lower().replace("caught and bowled", "caught").replace("c&b", "caught").strip()
    bat[(mid, inn, pid)][src] = d
print("A. BATTING - agreement between sources on the same batter-innings")
anom_bat = []
for f in ("Test", "ODI"):
    pairs = collections.Counter(); agree = collections.Counter()
    for k, v in bat.items():
        if fmt_of.get(k[0]) != f or len(v) < 2: continue
        for a, b in itertools.combinations(sorted(v), 2):
            for fld in BF:
                x, y = v[a][fld], v[b][fld]
                if x is None or y is None or x == "" or y == "": continue
                pairs[(a, b, fld)] += 1
                if x == y: agree[(a, b, fld)] += 1
                elif fld in ("runs", "balls", "kind", "bowler_id"):
                    anom_bat.append((f, k[0], mdate.get(k[0]), k[1], name.get(k[2], k[2]), fld, a, x, b, y))
    for (a, b, fld), n in sorted(pairs.items()):
        if fld in ("runs", "balls", "fours", "sixes", "kind", "bowler_id", "not_out"):
            print(f"  {f:4} {a:9} v {b:9} {fld:9}: {agree[(a,b,fld)]/n*100:6.2f}% of {n:6}  ({n-agree[(a,b,fld)]} differ)")
write("batting_disagreements.csv", ["format","match","date","inn","player","field","src_a","a","src_b","b"], anom_bat)

# ---------------- B. bowling ----------------
WF = ["balls", "maidens", "runs", "wickets"]
bowl = collections.defaultdict(dict)
for row in q("select match_id, inn, player_id, source, balls, maidens, runs, wickets from bowling"):
    bowl[(row[0], row[1], row[2])][row[3]] = dict(zip(WF, row[4:]))
print("\nB. BOWLING - agreement between sources on the same bowler-innings")
anom_bowl = []
for f in ("Test", "ODI"):
    pairs = collections.Counter(); agree = collections.Counter()
    for k, v in bowl.items():
        if fmt_of.get(k[0]) != f or len(v) < 2: continue
        for a, b in itertools.combinations(sorted(v), 2):
            for fld in WF:
                x, y = v[a][fld], v[b][fld]
                if x is None or y is None: continue
                pairs[(a, b, fld)] += 1
                if x == y: agree[(a, b, fld)] += 1
                else: anom_bowl.append((f, k[0], mdate.get(k[0]), k[1], name.get(k[2], k[2]), fld, a, x, b, y))
    for (a, b, fld), n in sorted(pairs.items()):
        print(f"  {f:4} {a:9} v {b:9} {fld:8}: {agree[(a,b,fld)]/n*100:6.2f}% of {n:6}  ({n-agree[(a,b,fld)]} differ)")
write("bowling_disagreements.csv", ["format","match","date","inn","player","field","src_a","a","src_b","b"], anom_bowl)

# ---------------- C. ball-by-ball recompute v scorecards ----------------
print("\nC. BALL-BY-BALL recomputed v each scorecard source (where the match has ball-by-ball)")
bb_bat = collections.defaultdict(lambda: [0, 0, 0, 0])      # runs, balls, 4s, 6s
bb_bowl = collections.defaultdict(lambda: [0, 0, 0, 0])     # legal balls, runs incl wd/nb, wkts, maidens
over_acc = collections.defaultdict(lambda: [0, 0, set()])   # (mid,inn,over) -> legal balls, runs charged, bowlers
for mid, inn, over_no, bo, bt, r, ex, et, legal, w, kind, out, src in q(
        "select match_id, inn, over_no, bowler_id, batter_id, bat_runs, extras, extra_type, legal, wicket, kind, out_id, source from balls"):
    if src != ball_src.get(mid): continue
    r = r or 0; ex = ex or 0; et = (et or "").lower()
    if et not in ("wd", "wide", "wides"):
        a = bb_bat[(mid, inn, bt)]; a[0] += r; a[1] += 1; a[2] += r == 4; a[3] += r == 6
    charged = r + (ex if et in ("wd", "wide", "wides", "nb", "noball", "no ball") else 0)
    b = bb_bowl[(mid, inn, bo)]; b[0] += 1 if legal else 0; b[1] += charged
    k = (kind or "").lower()
    if w and k and not any(x in k for x in ("run out", "retired", "obstruct", "handled", "timed")): b[2] += 1
    o = over_acc[(mid, inn, over_no)]; o[0] += 1 if legal else 0; o[1] += charged; o[2].add(bo)
for (mid, inn, ov), (lb, ch, bs) in over_acc.items():
    if lb >= bpo.get(mid, 6) and ch == 0 and len(bs) == 1:
        bb_bowl[(mid, inn, next(iter(bs)))][3] += 1
res = collections.Counter(); tot = collections.Counter(); anom_bbb = []
for (mid, inn, pid), v in bat.items():
    if (mid, inn, pid) not in bb_bat: continue
    r, b, f4, f6 = bb_bat[(mid, inn, pid)]
    for src, d in v.items():
        for fld, val in (("runs", r), ("balls", b), ("fours", f4), ("sixes", f6)):
            if d[fld] is None: continue
            tot[(fmt_of[mid], "bat", src, fld)] += 1
            if d[fld] == val: res[(fmt_of[mid], "bat", src, fld)] += 1
            elif fld == "runs": anom_bbb.append((fmt_of[mid], mid, mdate.get(mid), inn, name.get(pid, pid), "bat runs", src, d[fld], "bbb", val))
for (mid, inn, pid), v in bowl.items():
    if (mid, inn, pid) not in bb_bowl: continue
    lb, ch, wk, md = bb_bowl[(mid, inn, pid)]
    for src, d in v.items():
        for fld, val in (("balls", lb), ("runs", ch), ("wickets", wk), ("maidens", md)):
            if d[fld] is None: continue
            tot[(fmt_of[mid], "bowl", src, fld)] += 1
            if d[fld] == val: res[(fmt_of[mid], "bowl", src, fld)] += 1
            elif fld in ("maidens", "wickets"): anom_bbb.append((fmt_of[mid], mid, mdate.get(mid), inn, name.get(pid, pid), f"bowl {fld}", src, d[fld], "bbb", val))
for k in sorted(tot):
    print(f"  {k[0]:4} {k[1]:4} {k[2]:9} {k[3]:8}: {res[k]/tot[k]*100:6.2f}% of {tot[k]:6} agree with ball-by-ball")
write("bbb_v_scorecard.csv", ["format","match","date","inn","player","field","src","card","bbb_src","bbb"], anom_bbb)

# ---------------- D. innings totals add up ----------------
print("\nD. INNINGS TOTALS: batters' runs + extras = total?")
inn_rows = {(r[0], r[1], r[2]): (r[3], r[4]) for r in q("select match_id, inn, source, runs, extras from innings")}
sums = collections.Counter()
for (mid, inn, pid), v in bat.items():
    for src, d in v.items(): sums[(mid, inn, src)] += d["runs"] or 0
ok = collections.Counter(); n = collections.Counter(); anom_tot = []
for (mid, inn, src), (runs, ex) in inn_rows.items():
    if runs is None or ex is None or (mid, inn, src) not in sums: continue
    n[(fmt_of[mid], src)] += 1
    if sums[(mid, inn, src)] + ex == runs: ok[(fmt_of[mid], src)] += 1
    else: anom_tot.append((fmt_of[mid], mid, mdate.get(mid), inn, src, runs, sums[(mid, inn, src)], ex))
for k in sorted(n): print(f"  {k[0]:4} {k[1]:9}: {ok[k]/n[k]*100:6.2f}% of {n[k]} innings add up")
write("innings_totals_not_adding_up.csv", ["format","match","date","inn","src","total","batters_sum","extras"], anom_tot)

# ---------------- E. single-source innings ----------------
print("\nE. HOW MANY SOURCES EACH BATTER-INNINGS HAS")
cnt = collections.Counter()
for k, v in bat.items(): cnt[(fmt_of.get(k[0]), len(v))] += 1
for k in sorted(cnt, key=str): print(f"  {k[0]} with {k[1]} source(s): {cnt[k]}")

# ---------------- F. split people ----------------
print("\nF. POSSIBLE SPLIT PEOPLE (name variants with extra words/dots)")
import re
base = collections.defaultdict(list)
for pid, nm in name.items():
    b = re.sub(r"[^A-Za-z ]", "", nm).strip()
    b = re.sub(r"\b(DNB|injured|R Min|absent|hurt|sub)\b.*$", "", b).strip()
    base[b].append(pid)
splits = [(b, ids) for b, ids in base.items() if len(ids) > 1]
print(f"  {len(splits)} names map to more than one player id (some are genuinely different people, e.g. same initials)")
write("possible_split_people.csv", ["base_name","ids","names"], [(b, ";".join(map(str, ids)), " | ".join(name[i] for i in ids)) for b, ids in splits])
print("\nCSV files written to", OUT)
