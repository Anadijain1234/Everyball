import json
from collections import Counter, defaultdict
R = json.load(open("data/espn_live_survey.json"))
B = ["<1990", "90s", "00-04", "05-09", "10-14", "15-19", "20+"]
def pct(path, sec, buckets=True):
    v = R["S"][sec].get(path)
    if not v: return "absent"
    pres, fil = v[0], v[1]
    if not buckets: return f"{100*sum(fil)/max(1,sum(pres)):.0f}%"
    return " ".join(f"{(100*f/p):>3.0f}" if p else "  -" for p, f in zip(pres, fil))
recs = R["recs"]
print("records", len(recs), "| raw GB", round(R["raw_bytes"]/1e9, 2))
by = defaultdict(list)
for r in recs: by[(r[0], r[1])].append(r)
print("unique matches", len(by), "| duplicated keys", sum(1 for v in by.values() if len(v) > 1))
diff = [(k, [(x[4], x[5], x[10][-12:]) for x in v]) for k, v in by.items() if len(v) > 1 and len({x[5] for x in v}) > 1]
print("duplicates with different item counts:", len(diff), diff[:4])
best = {k: max(v, key=lambda x: (x[5], x[6])) for k, v in by.items()}
st = Counter((r[0], r[4]) for r in best.values()); print("status (unique):", dict(st))
odd = [r for r in best.values() if r[4] not in ("ok", "no commentary")]
print("odd-status matches:", len(odd)); [print("  ", r[9], "|", r[4], "| items", r[5], "| sc inns", r[6]) for r in odd[:15]]
for fmt in ("Test", "ODI"):
    nums = sorted(k[1] for k in best if k[0] == fmt)
    miss = sorted(set(range(1, max(nums) + 1)) - set(nums))
    print(f"{fmt}: {len(nums)} matches, numbers 1..{max(nums)}, missing {len(miss)}: {miss[:20]}")
def bk(d):
    y = int((d or '0')[:4]); return 0 if y < 1990 else 1 if y < 2000 else 2 if y < 2005 else 3 if y < 2010 else 4 if y < 2015 else 5 if y < 2020 else 6
cov = defaultdict(lambda: [0]*7); tot = defaultdict(lambda: [0]*7); nosc = defaultdict(lambda: [0]*7)
for r in best.values():
    b = bk(r[3]); tot[r[0]][b] += 1
    if r[5] > 0: cov[r[0]][b] += 1
    if r[6] == 0: nosc[r[0]][b] += 1
print("\nbuckets:            ", "  ".join(f"{x:>6}" for x in B))
for f in ("Test", "ODI"):
    print(f"{f:5} matches         ", "  ".join(f"{x:>6}" for x in tot[f]))
    print(f"{f:5} with commentary ", "  ".join(f"{x:>6}" for x in cov[f]))
    print(f"{f:5} NO scorecard    ", "  ".join(f"{x:>6}" for x in nosc[f]))
print("dup ball ids inside a match:", sum(r[8] for r in best.values()), "in", sum(1 for r in best.values() if r[8]), "matches")
print("\nFILL % by bucket (", " ".join(B), ")")
rows = [("player", p) for p in ("pl.dateOfBirth", "pl.battingStyles", "pl.bowlingStyles", "pl.playingRoles", "pl.countryTeamId", "pl.dateOfDeath")]
rows += [("scorecard", p) for p in ("sc.runs", "sc.balls", "sc.minutes", "sc.inningBatsmen[].balls", "sc.inningBatsmen[].minutes", "sc.inningBatsmen[].fours",
        "sc.inningBatsmen[].dismissalBowler", "sc.inningBatsmen[].fowRuns", "sc.inningBatsmen[].fowOvers", "sc.inningBowlers[].dots",
        "sc.inningPartnerships", "sc.inningPartnerships[].balls", "sc.inningOvers", "sc.inningFallOfWickets", "sc.catchesDropped")]
rows += [("item", p) for p in ("it.batsmanPlayerId", "it.bowlerPlayerId", "it.nonStrikerPlayerId", "it.totalRuns", "it.timestamp", "it.pitchLine", "it.pitchLength",
        "it.shotType", "it.shotControl", "it.wagonX", "it.wagonZone", "it.dismissalText", "it.commentTextItems", "it.commentPreTextItems",
        "it.commentPostTextItems", "it.commentImages", "it.commentVideos", "it.events", "it.smartStats", "it.over", "it.predictions",
        "it.batsmanStatText", "it.bowlerStatText")]
for sec, p in rows: print(f"  {p:38} {pct(p, sec)}")
print("\nALL item fields (overall fill):", ", ".join(f"{p[3:]}={pct(p,'item',False)}" for p in sorted(R["S"]["item"]) if p.count('.') == 1))
print("item sub-fields:", ", ".join(f"{p[3:]}={pct(p,'item',False)}" for p in sorted(R["S"]["item"]) if p.count('.') > 1)[:1500])
print("\nrecord fields:", ", ".join(f"{p[4:]}={pct(p,'record',False)}" for p in sorted(R["S"]["record"])))
print("scorecard extra fields:", ", ".join(p[3:] for p in sorted(R["S"]["scorecard"]) if p.count('.') == 1 and p not in [x[1] for x in rows])[:900])
print("\nVALUES:")
for k in ("pitchLine", "pitchLength", "shotType", "shotControl", "dismissalType", "commentTextItems.type", "commentPreTextItems.type", "commentPostTextItems.type", "innings_value_type"):
    v = R["VC"].get(k, []); print(f"  {k}: {len(v)} kinds:", ", ".join(f"{a}:{b}" for a, b in v[:14]))
print("\nTEXT by bucket:"); [print(f"  {k:32}", " ".join(f"{x:>9}" for x in v)) for k, v in R["T"].items()]
print("CONSISTENCY (innings) by bucket:"); [print(f"  {k:32}", " ".join(f"{x:>6}" for x in v)) for k, v in sorted(R["CONS"].items())]
