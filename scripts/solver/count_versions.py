"""
How many versions of each over fit ALL the facts? (Everyball idea (g), 18 Nov 2026)

For one real innings, take each over on its own and assume EVERY OTHER BALL of the innings is known exactly
(the best case any method could ever hope for). Then list every version of that over that still fits every fact:
each batter's runs / balls in each partnership, batters' 4s / 6s, the bowler's runs / dots / 4s / 6s / wides /
no-balls, extras by type, the over's runs, wickets on their exact ball (how out, who), strike rotation, and who is
on strike at the start of the next over. Level 3b adds the exact ball of every four and six.
(With the rest of the innings fixed, levels 1, 2 and 3a give the SAME facts for one over, so one number covers
them; 3b is counted separately.)

What comes out per over: number of versions; how many deliveries are the same in every version ("forced": any
method gets them right); and the exact-ball score you'd expect from picking a version at random. This is a
CEILING: a real rebuild has much less known than "every other ball", so it can only do worse on average,
except where a good "most likely" model beats a random pick.

  python3 scripts/solver/count_versions.py O3148 --db data/everyball.db
"""
import argparse, collections, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cricket_facts as F
import db_load as L
import layered_solver as LS

CAP = 200000


def vis(d):
    """What the strict score compares on one delivery."""
    return (d["striker"], d["non_striker"], d["bat"], d["kind"] or "",
            d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"], d["out"], F.norm_kind(d["how"]) if d["out"] else None)


def over_versions(truth, o, facts, order, anchors):
    era, bpo = facts["era"], facts["bpo"]
    K = LS.kinds(era)
    idx = [k for k, d in enumerate(truth) if d["over"] == o]
    td = [truth[k] for k in idx]
    nxt = truth[idx[-1] + 1] if idx[-1] + 1 < len(truth) else None
    # segments (split at wickets inside the over)
    segs, cur = [], []
    for d in td:
        cur.append(d)
        if d["out"]:
            segs.append(cur); cur = []
    if cur:
        segs.append(cur)
    S = []
    for sd in segs:
        w = sd[-1] if sd[-1]["out"] else None
        tg = {"n": sum(d["legal"] for d in sd), "runs": collections.Counter(), "faced": collections.Counter(),
              "team": sum(F.team_runs(d) for d in sd), "w": w, "how": F.norm_kind(w["how"]) if w else None}
        for d in sd:
            tg["runs"][d["striker"]] += d["bat"]
            if d["kind"] != "wd":
                tg["faced"][d["striker"]] += 1
        S.append(tg)
    tot = collections.Counter()
    for d in td:
        tot["conc"] += F.conceded(d, era); tot["dot"] += d["legal"] and F.conceded(d, era) == 0
        tot["wd"] += d["kind"] == "wd"; tot["nb"] += d["kind"] == "nb"
        for nm in ("ex_w", "ex_nb", "ex_b", "ex_lb"):
            tot[nm] += d[nm]
        if d["bat"] in (4, 6):
            tot[(d["striker"], d["bat"])] += 1
    over_complete = sum(d["legal"] for d in td) == bpo
    anc = {(a["ball"]): (a["striker"], a["bat"]) for a in anchors if a["over"] == o}
    # order of incoming batters
    pos_in_order = {p: k for k, p in enumerate(order)}
    versions = []
    acc = collections.Counter()

    def dfs(si, st, ns, legal_seg, legal_over, seq):
        if len(versions) >= CAP:
            return
        if si == len(S):
            for k_ in list(acc):
                if acc[k_] != tot[k_]:
                    return
            for k_ in tot:
                if acc[k_] != tot[k_]:
                    return
            if nxt is not None:
                a, b = (st, ns)
                if nxt["over"] != o:
                    a, b = b, a
                if (nxt["striker"], nxt["non_striker"]) != (a, b):
                    return
            versions.append(list(seq))
            return
        tg = S[si]
        n = tg["n"]
        w = tg["w"]
        segacc = seq_acc[si]
        # wicket ball now?
        cands = []
        for t, d in enumerate(K):
            if d["legal"] and legal_seg >= n - (1 if (w and d["legal"]) else 0) and not (w is None and legal_seg < n):
                pass
            cands.append((t, d))
        for t, d in cands:
            for is_w in ((False, True) if w else (False,)):
                if is_w:
                    if not LS.wicket_ok(tg["how"], d):
                        continue
                    if tg["how"] in LS.STRIKER_ONLY and w["out"] != st:
                        continue
                    if w["out"] not in (st, ns):
                        continue
                    if legal_seg + d["legal"] != n:
                        continue
                    if over_complete and si == len(S) - 1 and not d["legal"]:
                        continue
                else:
                    if d["legal"] and legal_seg + 1 > n - (1 if (w and True) else 0) and w is not None:
                        # leave room for a legal wicket ball only if the wicket ball must be legal; else allow
                        if legal_seg + 1 > n:
                            continue
                    elif d["legal"] and legal_seg + 1 > n:
                        continue
                    if w is None and si == len(S) - 1 and legal_seg == n:
                        continue                      # nothing after the over's last legal ball
                # anchors (3b)
                if d["legal"]:
                    a = anc.get(legal_over + 1)
                    if a is not None and (a[0] != st or a[1] != d["bat"]):
                        continue
                    if anchors and a is None and d["bat"] in (4, 6):
                        continue
                # accumulate and prune
                ch = []
                def add(k_, v):
                    acc[k_] += v; ch.append((k_, v))
                add("conc", d["conc"]); add("dot", int(d["dot"])); add("wd", int(d["kind"] == "wd")); add("nb", int(d["kind"] == "nb"))
                for nm in ("ex_w", "ex_nb", "ex_b", "ex_lb"):
                    add(nm, d[nm])
                if d["bat"] in (4, 6):
                    add((st, d["bat"]), 1)
                segacc["runs"][st] += d["bat"]; segacc["faced"][st] += d["faced"]; segacc["team"] += d["team"]
                ok = all(acc[k_] <= tot[k_] for k_, _ in ch) and segacc["runs"][st] <= tg["runs"][st] \
                    and segacc["faced"][st] <= tg["faced"][st] and segacc["team"] <= tg["team"]
                if ok:
                    dd = {"striker": st, "non_striker": ns, "bat": d["bat"], "kind": d["kind"], "ex_w": d["ex_w"],
                          "ex_nb": d["ex_nb"], "ex_b": d["ex_b"], "ex_lb": d["ex_lb"],
                          "out": w["out"] if is_w else None, "how": w["how"] if is_w else None}
                    seq.append(dd)
                    a_, b_ = (ns, st) if d["odd"] else (st, ns)
                    if is_w:
                        fin = segacc["team"] == tg["team"] and all(segacc["runs"][p] == tg["runs"][p] for p in set(tg["runs"]) | set(segacc["runs"])) \
                            and all(segacc["faced"][p] == tg["faced"][p] for p in set(tg["faced"]) | set(segacc["faced"]))
                        if fin:
                            surv = ns if w["out"] == st else st
                            nb_ = [p for p in order if p not in out_set and p not in (st, ns)]
                            newb = nb_[0] if nb_ else None
                            out_set.add(w["out"])
                            how = tg["how"]
                            if how in ("caught", "caught and bowled"):
                                opts = [(newb, surv)] if facts["era"]["caught_new_on_strike"] else [(newb, surv), (surv, newb)]
                            elif how in LS.STRIKER_ONLY:
                                opts = [(newb, surv)]
                            else:
                                opts = [(newb, surv), (surv, newb)]
                            for x, y in set(opts):
                                seq_acc.append({"runs": collections.Counter(), "faced": collections.Counter(), "team": 0})
                                dfs(si + 1, x, y, 0, legal_over + d["legal"], seq)
                                seq_acc.pop()
                            out_set.discard(w["out"])
                    else:
                        done_seg = (w is None and legal_seg + d["legal"] == n)
                        if done_seg and si == len(S) - 1:
                            fin = segacc["team"] == tg["team"] and all(segacc["runs"][p] == tg["runs"][p] for p in set(tg["runs"]) | set(segacc["runs"])) \
                                and all(segacc["faced"][p] == tg["faced"][p] for p in set(tg["faced"]) | set(segacc["faced"]))
                            if fin:
                                dfs(si + 1, a_, b_, legal_seg + 1, legal_over + 1, seq)
                        elif not done_seg:
                            dfs(si, a_, b_, legal_seg + d["legal"], legal_over + d["legal"], seq)
                    seq.pop()
                segacc["runs"][st] -= d["bat"]; segacc["faced"][st] -= d["faced"]; segacc["team"] -= d["team"]
                for k_, v in ch:
                    acc[k_] -= v
    out_set = {d["out"] for d in truth[:idx[0]] if d["out"]}
    seq_acc = [{"runs": collections.Counter(), "faced": collections.Counter(), "team": 0}]
    dfs(0, td[0]["striker"], td[0]["non_striker"], 0, 0, [])
    real = [vis(d) for d in td]
    return versions, real


def summarise(versions, real):
    vs = {tuple(vis(dict(d, how=d["how"])) for d in v) for v in versions}
    vs = [list(v) for v in vs]
    n = len(real)
    forced = sum(1 for k in range(n) if all(k < len(v) and v[k] == real[k] for v in vs))
    exp = sum(sum(1 for k in range(max(n, len(v))) if k < n and k < len(v) and v[k] == real[k]) / max(n, len(v)) for v in vs) / len(vs) if vs else 0
    return {"versions": len(vs), "deliveries": n, "forced": forced, "random_pick_exact": exp,
            "real_found": tuple(real) in {tuple(v) for v in vs}, "capped": len(versions) >= CAP}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match"); ap.add_argument("--db", default="data/everyball.db"); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    c = L.connect(a.db)
    info = L.match_info(c, a.match)
    report = {}
    t0 = time.time()
    for inn in (1, 2):
        truth, why = L.true_deliveries(c, a.match, inn)
        for lv in ("2", "3b"):
            facts = F.facts_from_deliveries(truth, info["day"], info["bpo"], lv)
            rows = []
            overs = sorted({d["over"] for d in truth})
            for o in overs:
                vers, real = over_versions(truth, o, facts, facts["order"], facts.get("anchors") or [])
                s = summarise(vers, real)
                s["over"] = o + 1
                rows.append(s)
                el = int(time.time() - t0)
                print(f"\r  ⏱ {el // 60}:{el % 60:02d} | innings {inn} level {lv} | over {o + 1}/{len(overs)} | "
                      f"{s['versions']} versions", end="", flush=True)
            print()
            report[f"inn{inn}_L{lv}"] = rows
    if a.out:
        json.dump(report, open(a.out, "w"), indent=1)
    for key, rows in report.items():
        nd = sum(r["deliveries"] for r in rows)
        print(f"{key}: overs {len(rows)} | real version found in {sum(r['real_found'] for r in rows)} | "
              f"versions per over: median {sorted(r['versions'] for r in rows)[len(rows) // 2]}, max {max(r['versions'] for r in rows)}"
              f"{' (capped)' if any(r['capped'] for r in rows) else ''} | overs with only 1 version {sum(r['versions'] == 1 for r in rows)} | "
              f"forced deliveries {sum(r['forced'] for r in rows)}/{nd} ({100 * sum(r['forced'] for r in rows) / nd:.1f}%) | "
              f"random-pick exact {100 * sum(r['random_pick_exact'] * r['deliveries'] for r in rows) / nd:.1f}%")


if __name__ == "__main__":
    main()
