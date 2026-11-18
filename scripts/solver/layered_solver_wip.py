"""
Layered solver: rebuild an innings ball by ball in two layers (Everyball, 18 Nov 2026).

WHY: the one-big-model solvers (rebuild.py, split_solver.Joint) are correct but OR-Tools could not find even one
answer in 10 minutes, because every ball's order and value are decided at once. Here the two are split:

LAYER 1 - COUNTS (one OR-Tools model for the whole innings, small)
  The innings is cut into "segments" = partnership x over (e.g. Gambhir-Kohli in over 7 = balls 7.3-7.6).
  For every segment and each of its two batters it decides only HOW MANY deliveries of each kind he faced:
  dots, 1s, 2s, 3s, 4s, 5s, 6s, byes 1-4, leg-byes 1-4, no-balls (+runs), wides (+runs run). Plus who is on strike
  at the start of the segment and which ball took the wicket. Every scorecard fact is exact (hard):
  partnership runs/balls and each batter's share, batters' 4s/6s, bowlers' runs/dots/4s/6s/wides/no-balls/maidens,
  extras by type, wickets on their exact ball (fall of wickets), level 2+: each over's bowler and runs,
  level 3a: the over of each 4/6 and who hit it, level 3b: the exact ball of each 4/6, level 1: who bowled each over.
  STRIKE is exact with counting alone: inside a segment the striker changes only on odd-run deliveries, so if A
  starts on strike, A's odd deliveries = B's odd deliveries or one more (A, B, A, B ... in turn).
  The "most likely" score (same as split_solver: run rates by phase + each batter's and bowler's real patterns,
  this match left out) adds up ball by ball, so it is decided here, exactly, on the counts.
LAYER 2 - ORDER (one tiny search per segment, at most ~10 deliveries)
  Puts each segment's deliveries in an order that fits: strike changes on odd runs, an over ends on a legal ball,
  the wicket ball is the last of its partnership, level 3b boundaries on their exact ball. If a segment can't be
  ordered, that exact count plan is forbidden and layer 1 runs again (never gives up a fact).
Every answer is replayed by cricket_facts.replay (all facts + Laws) before it is returned.

  python3 scripts/solver/layered_solver.py O3148 --level 2 --innings 1      # quick try on one innings
"""
import collections, math, sys, time

from ortools.sat.python import cp_model as cp

import cricket_facts as F
import split_solver as SS

LEGAL_ONLY, STRIKER_ONLY = SS.LEGAL_ONLY, SS.STRIKER_ONLY
STATUS = {"stage": "starting", "extra": ""}
GAP = 0.005          # "good enough": stop when the answer is within 0.5% of the best possible score


# --------------------------------------------------------------------------------------------- delivery kinds
def kinds(era):
    """Every kind of delivery as (kind, bat, rx) with its scoring, under the Laws of the match's date."""
    P, BB = era["pen_plus"], era["nb_byes_are_byes"]
    out = []
    for r in range(7):
        out.append(("", r, 0))
    for rx in range(1, 5):
        out += [("b", 0, rx), ("lb", 0, rx)]
    for r in range(7):
        out.append(("nb", r, 0))
    for rx in range(1, 5):
        out.append(("nbb", 0, rx))
        if BB:
            out.append(("nblb", 0, rx))
    for rx in range(0, 5):
        out.append(("wd", 0, rx))
    res = []
    for k, r, rx in out:
        d = {"bat": r, "kind": k, "ran": rx, "ex_w": 0, "ex_nb": 0, "ex_b": 0, "ex_lb": 0, "legal": k in ("", "b", "lb")}
        if k in ("b", "lb"):
            d["ex_" + k] = rx
        elif k == "nb":
            d["ex_nb"] = 1 if P else (1 if r == 0 else 0)
        elif k in ("nbb", "nblb"):
            d["kind"] = "nb"
            if P and BB:
                d["ex_nb"] = 1
                d["ex_b" if k == "nbb" else "ex_lb"] = rx
            elif P:
                d["ex_nb"] = 1 + rx
            else:
                d["ex_nb"] = rx
        elif k == "wd":
            d["ex_w"] = (1 + rx) if P else (rx if rx > 0 else 1)
        d["team"] = F.team_runs(d)
        d["conc"] = F.conceded(d, era)
        d["odd"] = F.ran_parity(d)
        d["faced"] = d["kind"] != "wd"
        d["dot"] = d["legal"] and d["conc"] == 0
        d["key"] = (k, r, rx)
        res.append(d)
    return res


def wicket_ok(how, d):
    """Can this kind of delivery carry this dismissal? (same Laws as split_solver.Piece)"""
    if how in LEGAL_ONLY:
        return d["kind"] == "" and d["bat"] == 0
    if how in ("stumped", "hit wicket"):
        return d["kind"] in ("", "wd") and d["bat"] == 0 and d["ran"] == 0
    if how in ("handled the ball", "hit the ball twice"):
        return d["kind"] != "wd" and d["bat"] == 0
    return d["bat"] not in (4, 6) and not (d["bat"] == 0 and d["ran"] == 4)   # run out etc.


# --------------------------------------------------------------------------------------------- likelihood
def costs(f, prior, patterns, A, B, over, bowler):
    """Cost (lower = more likely) of runs x off the bat for striker A / B, as in split_solver.Piece.like."""
    pat = patterns or {}
    ball_all = pat.get("all") or {}

    def delta(table, pid, x):
        t = (table or {}).get(pid)
        if not t or not ball_all:
            return 0.0
        return (math.log(t[0]) - math.log(ball_all[0])) - (math.log(t[x]) - math.log(ball_all[x]))
    ph = 0 if over < 10 else (2 if over >= 40 else 1)
    pr = prior.get(ph) or prior.get(str(ph)) or {}
    p0 = pr.get(0, pr.get("0", 0.5))
    cA, cB = [0] * 7, [0] * 7
    for x in range(1, 7):
        px = pr.get(x, pr.get(str(x), 0.001))
        base = math.log(p0) - math.log(max(px, 1e-6)) + (delta(pat.get("bowl"), bowler, x) if bowler else 0.0)
        dA, dB = delta(pat.get("bat"), A, x), delta(pat.get("bat"), B, x)
        cB[x] = int(round(100 * (base + dB)))
        cA[x] = cB[x] + int(round(100 * (dA - dB)))
    return cA, cB


# --------------------------------------------------------------------------------------------- layer 1
def make_parts(f, pieces):
    """Every segment (partnership x over) cut at the balls given exactly (level 3b boundaries) into parts.
    A part = deliveries between two given balls; the given ball after it is fixed (striker, runs)."""
    bpo = f["bpo"]
    anch = collections.defaultdict(list)
    for a in f.get("anchors") or []:
        anch[a["over"]].append(a)
    parts = {}
    for i, p in enumerate(pieces):
        for j, sg in enumerate(p["segs"]):
            o, n, first = sg["over"], sg["n"], (sg["first"] or 1)
            inside = sorted([a for a in anch[o] if n and first <= a["ball"] <= first + n - 1], key=lambda a: a["ball"])
            cuts, prev = [], first
            for a in inside:
                if a["striker"] not in (p["A"], p["B"]):
                    raise ValueError(f"over {o + 1}: boundary by a batter not at the crease")
                cuts.append((a["ball"] - prev, a))
                prev = a["ball"] + 1
            cuts.append((first + n - prev if n else 0, None))
            for k, (nk, a) in enumerate(cuts):
                last = k == len(cuts) - 1
                parts[(i, j, k)] = {"n": nk, "last": last, "anchor": a,
                                    "aX": None if a is None else ("A" if a["striker"] == p["A"] else "B"),
                                    "ends_over": last and sg["ends_over"], "trailing": sg["trailing"], "over": o}
    return parts


class Counts:
    def __init__(self, f, pieces, prior, patterns, nogoods=()):
        self.f, self.pieces = f, pieces
        era, bpo = f["era"], f["bpo"]
        self.K = K = kinds(era)
        m = self.m = cp.CpModel()
        lt = f["legal_total"]
        given = {ov["over"]: ov for ov in (f.get("overs") or [])}
        self.given = given
        NO = max([sg["over"] for p in pieces for sg in p["segs"]] + [0]) + 1
        self.NO = NO
        Lo = [max(0, min(bpo, lt - o * bpo)) for o in range(NO)]
        bowlers = self.bowlers = [b["pid"] for b in f["bowlers"]]
        wix = {b: i for i, b in enumerate(bowlers)}
        like = []
        has_anchors = bool(f.get("anchors"))
        self.parts = parts = make_parts(f, pieces)
        # ---- who bowled each over: known overs are fixed, the others are decided here
        known = self.known = {o: g_["bowler"] for o, g_ in given.items() if g_.get("bowler") in wix}
        unknown = self.unknown = [o for o in range(NO) if o not in known]
        xv = self.xv = {(o, b): m.NewBoolVar(f"x{o}_{b}") for o in unknown for b in range(len(bowlers))}
        X = self.X = lambda o, b: xv[(o, b)] if o not in known else (1 if known[o] == bowlers[b] else 0)
        for o in unknown:
            m.AddExactlyOne([xv[(o, b)] for b in range(len(bowlers))])
        for o in range(NO - 1):
            for b in range(len(bowlers)):
                a_, b_ = X(o, b), X(o + 1, b)
                if isinstance(a_, int) and isinstance(b_, int):
                    if a_ and b_:
                        raise ValueError(f"overs {o + 1}-{o + 2}: same bowler twice in a row")
                elif isinstance(a_, int):
                    if a_:
                        m.Add(b_ == 0)
                elif isinstance(b_, int):
                    if b_:
                        m.Add(a_ == 0)
                else:
                    m.AddBoolOr([a_.Not(), b_.Not()])
        if unknown:
            for b, fb in enumerate(f["bowlers"]):
                if fb.get("balls") is not None:
                    m.Add(sum(Lo[o] * X(o, b) for o in range(NO)) == fb["balls"])
            for o in range(NO - 2):
                for b in range(len(bowlers)):
                    a_, b_ = X(o, b), X(o + 2, b)
                    if isinstance(a_, int) and isinstance(b_, int):
                        continue
                    if (isinstance(a_, int) and not a_) or (isinstance(b_, int) and not b_):
                        continue
                    sp = m.NewBoolVar("sp")
                    for l_ in (a_, b_):
                        if not isinstance(l_, int):
                            m.AddImplication(sp, l_)
                    like.append(-60 * sp)
        bowler_of = lambda o: known.get(o)
        self.c, self.w, self.s0, self.sE, self.inc, self.free = {}, {}, {}, {}, {}, {}
        self.part_bat = {}                                   # (g, X) -> {field: expr}, wicket + given ball included
        parts_of_over = collections.defaultdict(list)
        self.where_of = {}                                   # g -> "N" / "T" if the wicket ball may be in it
        spare_cap = 6
        prev_inc = None
        for i, p in enumerate(pieces):
            A, B = p["A"], p["B"]
            how, wk = p["how"], p["wicket"]
            nseg = len(p["segs"])
            norm = [j for j, sg in enumerate(p["segs"]) if not sg["trailing"]]
            jlast = norm[-1] if norm else None
            jtr = next((j for j, sg in enumerate(p["segs"]) if sg["trailing"]), None)
            klast = {j: max(k for (i_, j_, k) in parts if i_ == i and j_ == j) for j in range(nseg)}
            gN = (i, jlast, klast[jlast]) if jlast is not None else None
            gT = (i, jtr, 0) if jtr is not None else None
            if wk:
                for where, g in (("N", gN), ("T", gT)):
                    if g is None:
                        continue
                    pt = parts[g]
                    self.where_of[g] = where
                    for X, pid in (("A", A), ("B", B)):
                        if how in STRIKER_ONLY and pid != wk["out"]:
                            continue
                        for t, d in enumerate(K):
                            if not wicket_ok(how, d):
                                continue
                            if where == "T" and d["legal"]:
                                continue
                            if where == "N" and pt["ends_over"] and not d["legal"]:
                                continue
                            if d["legal"] and pt["n"] == 0:
                                continue
                            if how in F.CREDITED and wk.get("bowler") is not None and pt["over"] in known and \
                                    known[pt["over"]] != wk["bowler"]:
                                continue
                            self.w[(g, X, t)] = m.NewBoolVar("w")
                wl = [v for k_, v in self.w.items() if k_[0][0] == i]
                if not wl:
                    raise ValueError(f"wicket {i + 1}: no delivery can carry '{how}'")
                m.AddExactlyOne(wl)
            pgs = sorted(g for g in parts if g[0] == i)
            for g in pgs:
                pt = parts[g]
                j = g[1]
                o, n = pt["over"], pt["n"]
                parts_of_over[o].append(g)
                cA, cB = costs(f, prior, patterns, A, B, o, bowler_of(o))
                for X in "AB":
                    for t, d in enumerate(K):
                        if pt["trailing"]:
                            ub = 0 if (wk or d["legal"]) else 1
                        else:
                            ub = n if d["legal"] else spare_cap
                        if has_anchors and d["bat"] in (4, 6):
                            ub = 0                                   # level 3b: every boundary is a given ball
                        self.c[(g, X, t)] = m.NewIntVar(0, ub, "c")
                s0 = self.s0[g] = m.NewBoolVar("s0")
                Cf = lambda X, fld: sum(d[fld] * self.c[(g, X, t)] for t, d in enumerate(K) if d[fld])
                Cn = lambda X, pred: sum(self.c[(g, X, t)] for t, d in enumerate(K) if pred(d))
                wv = [(X, t, v) for (gg, X, t), v in self.w.items() if gg == g]
                Wn = lambda X, pred: sum(v for XX, t, v in wv if XX == X and pred(K[t]))
                Wf = lambda X, fld: sum(K[t][fld] * v for XX, t, v in wv if XX == X and K[t][fld])
                m.Add(sum(Cn(X, lambda d: d["legal"]) + Wn(X, lambda d: d["legal"]) for X in "AB") == n)
                if pt["trailing"]:
                    m.Add(sum(Cn(X, lambda d: True) for X in "AB") <= (0 if wk else 1))
                else:
                    m.Add(sum(Cn(X, lambda d: not d["legal"]) for X in "AB") <= spare_cap)
                # strike inside the part: odd deliveries alternate A, B, A, ...
                oA, oB = Cf("A", "odd"), Cf("B", "odd")
                z = m.NewIntVar(-1, 1, "z")
                m.Add(z == oA - oB)
                m.Add(z >= 0).OnlyEnforceIf(s0); m.Add(z <= 0).OnlyEnforceIf(s0.Not())
                dz = m.NewBoolVar("dz")
                m.Add(z != 0).OnlyEnforceIf(dz); m.Add(z == 0).OnlyEnforceIf(dz.Not())
                sE = self.sE[g] = m.NewBoolVar("sE")
                m.AddBoolXOr([s0, dz, sE.Not()])                    # sE = s0 xor dz
                nA, nB = Cn("A", lambda d: True), Cn("B", lambda d: True)
                hasB, hasA = m.NewBoolVar("hb"), m.NewBoolVar("ha")  # a batter needs a turn on strike to face
                m.Add(oA >= 1).OnlyEnforceIf([s0, hasB]); m.Add(nB == 0).OnlyEnforceIf([s0, hasB.Not()])
                m.Add(oB >= 1).OnlyEnforceIf([s0.Not(), hasA]); m.Add(nA == 0).OnlyEnforceIf([s0.Not(), hasA.Not()])
                for XX, t, v in wv:                                 # the wicket ball: whoever is on strike at the end
                    m.Add((sE if self.where_of[g] == "N" else s0) == (1 if XX == "A" else 0)).OnlyEnforceIf(v)
                # the over ends on a legal ball
                if pt["ends_over"]:
                    EL = {X: Cn(X, lambda d: d["legal"] and not d["odd"]) for X in "AB"}
                    EA = {X: Cn(X, lambda d: not d["odd"]) for X in "AB"}
                    OL = {X: Cn(X, lambda d: d["legal"] and d["odd"]) for X in "AB"}
                    o1, o2 = m.NewBoolVar("o1"), m.NewBoolVar("o2")
                    m.Add(EL["A"] >= 1).OnlyEnforceIf([o1, sE]); m.Add(EL["B"] >= 1).OnlyEnforceIf([o1, sE.Not()])
                    m.Add(OL["B"] >= 1).OnlyEnforceIf([o2, sE]); m.Add(EA["A"] == 0).OnlyEnforceIf([o2, sE])
                    m.Add(OL["A"] >= 1).OnlyEnforceIf([o2, sE.Not()]); m.Add(EA["B"] == 0).OnlyEnforceIf([o2, sE.Not()])
                    opts = [o1, o2]
                    if g[2] > 0:
                        o3 = m.NewBoolVar("o3")
                        m.Add(nA + nB == 0).OnlyEnforceIf(o3)
                        opts.append(o3)
                    m.AddBoolOr(opts + [v for _, _, v in wv])
                # stats of the part: its deliveries + the wicket ball + the given ball after it
                a = pt["anchor"]
                for X in "AB":
                    isa = 1 if (a is not None and pt["aX"] == X) else 0
                    ar = a["bat"] if isa else 0
                    bt = {fld: Cf(X, fld) + Wf(X, fld) for fld in ("team", "conc", "dot", "faced", "legal", "bat")}
                    bt["team"] += ar; bt["conc"] += ar; bt["faced"] += isa; bt["legal"] += isa; bt["bat"] += ar
                    for r in (4, 6):
                        bt[str(r)] = Cn(X, lambda d, r=r: d["bat"] == r) + Wn(X, lambda d, r=r: d["bat"] == r) + (isa if ar == r else 0)
                    for nm in ("ex_w", "ex_nb", "ex_b", "ex_lb"):
                        bt[nm] = Cf(X, nm) + Wf(X, nm)
                    bt["wd"] = Cn(X, lambda d: d["kind"] == "wd") + Wn(X, lambda d: d["kind"] == "wd")
                    bt["nb"] = Cn(X, lambda d: d["kind"] == "nb") + Wn(X, lambda d: d["kind"] == "nb")
                    bt["rxp"] = Cn(X, lambda d: d["ran"] > 0) + Wn(X, lambda d: d["ran"] > 0)
                    self.part_bat[(g, X)] = bt
                    cc = cA if X == "A" else cB
                    for t, d in enumerate(K):
                        if d["bat"]:
                            like.append(cc[d["bat"]] * self.c[(g, X, t)])
                    for XX, t, v in wv:
                        if XX == X and K[t]["bat"]:
                            like.append(cc[K[t]["bat"]] * v)
                    like.append(150 * bt["rxp"])
                # strike across a given ball: it is faced by its striker, and boundaries don't change ends
                if a is not None:
                    m.Add(sE == (1 if pt["aX"] == "A" else 0))
            # strike chain: part -> next part (across a given ball) -> next segment (ends change after an over)
            for g, g2 in zip(pgs, pgs[1:]):
                if g2[1] == g[1]:
                    m.Add(self.s0[g2] == self.sE[g])
                elif p["segs"][g[1]]["ends_over"]:
                    m.Add(self.s0[g2] + self.sE[g] == 1)
                else:
                    m.Add(self.s0[g2] == self.sE[g])
            if wk:
                if gT is not None:
                    for X in "AB":
                        for t in range(len(K)):
                            m.Add(self.c[(gT, X, t)] == 0)
                free = self.free[i] = m.NewBoolVar("free")
                if how in STRIKER_ONLY and (how not in ("caught", "caught and bowled") or era["caught_new_on_strike"]):
                    m.Add(free == 1)
                eovw = sum(v for (gg, X, t), v in self.w.items() if gg == gN and parts[gN]["ends_over"]) if gN else 0
                inc = self.inc[i] = m.NewBoolVar("inc")
                h = m.NewIntVar(0, 1, "h")
                m.Add(free + eovw - inc == 2 * h)
            if i > 0 and prev_inc is not None and pgs:
                m.Add(self.s0[pgs[0]] == 1 - prev_inc)
            prev_inc = self.inc.get(i)
            # the partnership, exactly
            if p.get("runs") is not None:
                m.Add(sum(self.part_bat[(g, X)]["team"] for g in pgs for X in "AB") == p["runs"])
            if not f.get("no_split"):                         # each batter's share of the stand (when known)
                for X, pid in (("A", A), ("B", B)):
                    m.Add(sum(self.part_bat[(g, X)]["bat"] for g in pgs) == p["runs_by"].get(pid, 0))
                    m.Add(sum(self.part_bat[(g, X)]["faced"] for g in pgs) == p["balls_by"].get(pid, 0))
        self.parts_of_over = parts_of_over
        # ---- overs and bowlers
        PT = lambda g, k_: self.part_bat[(g, "A")][k_] + self.part_bat[(g, "B")][k_]
        agg = {o: {k_: sum(PT(g, k_) for g in gs) for k_ in ("team", "conc", "dot", "4", "6", "wd", "nb")}
               for o, gs in parts_of_over.items()}
        for o in agg:
            if given.get(o, {}).get("runs") is not None:
                m.Add(agg[o]["team"] == given[o]["runs"])
        mzo = {}
        for o in agg:
            if Lo[o] == bpo:
                zz = m.NewBoolVar("mz")
                m.Add(agg[o]["conc"] == 0).OnlyEnforceIf(zz); m.Add(agg[o]["conc"] >= 1).OnlyEnforceIf(zz.Not())
                mzo[o] = zz
        ub = {"conc": 80, "dot": bpo, "4": 12, "6": 12, "wd": 12, "nb": 12}
        for b, fb in enumerate(f["bowlers"]):
            for k_, fk in (("conc", "runs"), ("dot", "dots"), ("4", "4"), ("6", "6"), ("wd", "wd"), ("nb", "nb")):
                if fb.get(fk) is None:
                    continue
                ps = []
                for o in agg:
                    if o in known:
                        if known[o] == bowlers[b]:
                            ps.append(agg[o][k_])
                        continue
                    y = m.NewIntVar(0, ub[k_], "yb")
                    m.Add(y == agg[o][k_]).OnlyEnforceIf(xv[(o, b)]); m.Add(y == 0).OnlyEnforceIf(xv[(o, b)].Not())
                    ps.append(y)
                m.Add(sum(ps) == fb[fk])
            if fb.get("maidens") is not None:
                mz = []
                for o, zz in mzo.items():
                    if o in known:
                        if known[o] == bowlers[b]:
                            mz.append(zz)
                    else:
                        q = m.NewBoolVar("mq")
                        m.AddImplication(q, zz); m.AddImplication(q, xv[(o, b)]); m.AddBoolOr([q, zz.Not(), xv[(o, b)].Not()])
                        mz.append(q)
                m.Add(sum(mz) == fb["maidens"])
        if unknown:
            for (g, X_, t), v in self.w.items():                # "b X": X bowled the over of the wicket ball
                p = pieces[g[0]]
                if p["how"] in F.CREDITED and p["wicket"].get("bowler") in wix and parts[g]["over"] not in known:
                    m.AddImplication(v, xv[(parts[g]["over"], wix[p["wicket"]["bowler"]])])
            pat = patterns or {}
            if pat.get("bowl") and pat.get("all"):              # bowler patterns, by who bowls the over
                al = pat["all"]
                for o, gs in parts_of_over.items():
                    if o in known:
                        continue
                    cnt = {r_: sum(self.c[(g, X_, t)] for g in gs for X_ in "AB"
                                   for t, d in enumerate(K) if d["bat"] == r_) for r_ in range(1, 7)}
                    for b, bp in enumerate(bowlers):
                        tb = pat["bowl"].get(bp)
                        if not tb:
                            continue
                        for r_ in range(1, 7):
                            dlt = int(round(100 * ((math.log(tb[0]) - math.log(al[0])) - (math.log(tb[r_]) - math.log(al[r_])))))
                            if not dlt:
                                continue
                            y = m.NewIntVar(0, 12, "pb")
                            m.Add(y == cnt[r_]).OnlyEnforceIf(xv[(o, b)]); m.Add(y == 0).OnlyEnforceIf(xv[(o, b)].Not())
                            like.append(dlt * y)
        # ---- batters' 4s / 6s, extras by type
        f4, f6 = collections.defaultdict(list), collections.defaultdict(list)
        for (g, X), bt in self.part_bat.items():
            pid = pieces[g[0]][X]
            f4[pid].append(bt["4"]); f6[pid].append(bt["6"])
        tR, tB = collections.defaultdict(list), collections.defaultdict(list)
        for (g, X_), bt in self.part_bat.items():
            pid = pieces[g[0]][X_]
            tR[pid].append(bt["bat"]); tB[pid].append(bt["faced"])
        for fb in f["batters"]:                                 # each batter's runs and balls, exactly
            if fb["pid"] in tR:
                if fb.get("R") is not None:
                    m.Add(sum(tR[fb["pid"]]) == fb["R"])
                if fb.get("B") is not None:
                    m.Add(sum(tB[fb["pid"]]) == fb["B"])
        for fb in f["batters"]:
            if fb["pid"] in f4:
                if fb.get("4") is not None:
                    m.Add(sum(f4[fb["pid"]]) == fb["4"])
                if fb.get("6") is not None:
                    m.Add(sum(f6[fb["pid"]]) == fb["6"])
        ex = f.get("extras") or {}
        for key, nm in (("wd", "ex_w"), ("nb", "ex_nb"), ("b", "ex_b"), ("lb", "ex_lb")):
            if ex.get(key) is not None:
                m.Add(sum(bt[nm] for bt in self.part_bat.values()) == ex[key])
        # ---- level 3a: the over of each four and six, and who hit it
        if f.get("over_events") is not None:
            want = collections.Counter()
            for e in f["over_events"]:
                want[(e["over"], e["striker"], e["bat"])] += e["n"]
            cells = collections.defaultdict(list)
            for (g, X), bt in self.part_bat.items():
                for r in (4, 6):
                    cells[(parts[g]["over"], pieces[g[0]][X], r)].append(bt[str(r)])
            for key, vs in cells.items():
                m.Add(sum(vs) == want.get(key, 0))
        rec = f.get("recorded") or {}
        hard = set(f.get("recorded_hard") or ())
        keyix = {d["key"]: t for t, d in enumerate(K)}
        for o, cnt_ in rec.items():
            gs = parts_of_over.get(o, [])
            for key in set(cnt_) | {d["key"] for d in K}:
                t = keyix.get(key)
                if t is None:
                    continue
                expr = sum(self.c[(g, X_, t)] for g in gs for X_ in "AB") + \
                    sum(v for (gg, X_, tt), v in self.w.items() if gg in gs and tt == t)
                want = cnt_.get(key, 0)
                if o in hard:
                    m.Add(expr == want)
                elif want or True:
                    dev = m.NewIntVar(0, 20, "dev")
                    m.Add(expr - want <= dev); m.Add(want - expr <= dev)
                    like.append(1000 * dev)
        for o, cnt_ in (f.get("recorded_by") or {}).items():   # who faced what (known overs)
            gs = parts_of_over.get(o, [])
            for (pid, key), want in cnt_.items():
                t = keyix.get(key)
                if t is None:
                    continue
                expr = sum(self.c[(g, X_, t)] for g in gs for X_ in "AB" if pieces[g[0]][X_] == pid) + \
                    sum(v for (gg, X_, tt), v in self.w.items() if gg in gs and tt == t and pieces[gg[0]][X_] == pid)
                m.Add(expr == want)
        for g, vals in nogoods:
            m.AddBoolOr(self._differs(g, vals))
        self.like = like
        m.Minimize(sum(like))

    def part_vars(self, g):
        vs = [self.c[(g, X, t)] for X in "AB" for t in range(len(self.K))] + [self.s0[g]]
        vs += [v for (gg, X, t), v in sorted(self.w.items(), key=lambda kv: (kv[0][1], kv[0][2])) if gg == g]
        return vs

    def _differs(self, g, vals):
        lits = []
        for var, val in zip(self.part_vars(g), vals):
            up, dn = self.m.NewBoolVar("up"), self.m.NewBoolVar("dn")
            self.m.Add(var >= val + 1).OnlyEnforceIf(up); self.m.Add(var <= val - 1).OnlyEnforceIf(dn)
            lits += [up, dn]
        return lits

    def solve(self, seconds, workers, hint=None):
        s = cp.CpSolver()
        s.parameters.max_time_in_seconds = float(seconds)
        s.parameters.num_workers = workers
        s.parameters.relative_gap_limit = GAP                 # stop when within GAP of the best possible score
        if hint:
            for g, vals in hint.items():
                if g == "x":
                    for (o, b), val in vals.items():
                        if (o, b) in self.xv:
                            self.m.AddHint(self.xv[(o, b)], val)
                    continue
                for var, val in zip(self.part_vars(g), vals):
                    self.m.AddHint(var, val)
        t0 = time.time()
        found = [0]

        class CB(cp.CpSolverSolutionCallback):
            def on_solution_callback(cb):
                found[0] += 1
                STATUS["extra"] = f"answer #{found[0]} at {time.time() - t0:.0f} s (score {cb.ObjectiveValue():.0f})"
        st = s.StatusName(s.Solve(self.m, CB()))
        self.status, self.sol = st, s
        return st in ("OPTIMAL", "FEASIBLE")

    def read(self):
        """The plan of every part: items per batter, start striker, the wicket ball."""
        s, K = self.sol, self.K
        plan = {}
        for g in self.parts:
            items = {X: [] for X in "AB"}
            for X in "AB":
                for t in range(len(K)):
                    items[X] += [t] * s.Value(self.c[(g, X, t)])
            wball = None
            for (gg, X, t), v in self.w.items():
                if gg == g and s.Value(v):
                    wball = (X, t)
            plan[g] = {"items": items, "s0": s.Value(self.s0[g]), "wicket": wball,
                       "vals": [s.Value(v) for v in self.part_vars(g)]}
        self.bowler_of = dict(self.known)
        for o in self.unknown:
            self.bowler_of[o] = self.bowlers[next(b for b in range(len(self.bowlers)) if s.Value(self.xv[(o, b)]))]
        if self.xv:
            plan["x"] = {k_: s.Value(v) for k_, v in self.xv.items()}
        return plan


# --------------------------------------------------------------------------------------------- layer 2
def order_part(K, pt, pl):
    """Order one part's deliveries: strike changes on odd runs, the wicket ball last, an over ends on a legal
    ball. Returns a list of (X, kind index, is_wicket) or None."""
    rem = {X: collections.Counter(pl["items"][X]) for X in "AB"}
    total = sum(len(v) for v in pl["items"].values())
    out = []

    def dfs(X):
        if len(out) == total:
            if pl["wicket"]:
                wX, wt = pl["wicket"]
                if wX != X:
                    return False
                out.append((wX, wt, True))
                return True
            if pt["ends_over"] and total and not K[out[-1][1]]["legal"]:
                return False
            if pt["anchor"] is not None and X != pt["aX"]:
                return False
            return True
        tried = set()
        for t in sorted(rem[X], key=lambda t_: (K[t_]["odd"], -K[t_]["legal"])):
            if rem[X][t] == 0 or t in tried:
                continue
            tried.add(t)
            rem[X][t] -= 1
            out.append((X, t, False))
            if dfs(("B" if X == "A" else "A") if K[t]["odd"] else X):
                return True
            out.pop()
            rem[X][t] += 1
        return False
    return out if dfs("A" if pl["s0"] else "B") else None


# --------------------------------------------------------------------------------------------- the whole innings
def plan_bowlers(f, pieces, seconds, workers, nogoods=()):
    """Level 1: who bowled each over (a small model): each bowler's overs, never two in a row, the bowler of
    every "b X" wicket bowled the over it fell in, bowlers in spells (prefers o, o+2, o+4 ...)."""
    bpo, lt = f["bpo"], f["legal_total"]
    NO = max([sg["over"] for p in pieces for sg in p["segs"]] + [0]) + 1
    Lo = [max(0, min(bpo, lt - o * bpo)) for o in range(NO)]
    bowlers = [b["pid"] for b in f["bowlers"]]
    wix = {b: i for i, b in enumerate(bowlers)}
    m = cp.CpModel()
    x = [[m.NewBoolVar("x") for _ in bowlers] for _ in range(NO)]
    obj = []
    for o in range(NO):
        m.AddExactlyOne(x[o])
        if o + 1 < NO:
            for b in range(len(bowlers)):
                m.AddBoolOr([x[o][b].Not(), x[o + 1][b].Not()])
    for b, fb in enumerate(f["bowlers"]):
        if fb.get("balls") is not None:
            m.Add(sum(Lo[o] * x[o][b] for o in range(NO)) == fb["balls"])
        if fb.get("maidens"):
            m.Add(sum(x[o][b] for o in range(NO) if Lo[o] == bpo) >= fb["maidens"])
    for p in pieces:
        w = p["wicket"]
        if w and p["how"] in F.CREDITED and w.get("bowler") in wix:
            ovs = {sg["over"] for sg in p["segs"] if sg["trailing"] or sg is [s_ for s_ in p["segs"] if not s_["trailing"]][-1]}
            m.AddBoolOr([x[o][wix[w["bowler"]]] for o in ovs])
    for o in range(NO - 2):
        for b in range(len(bowlers)):
            sp = m.NewBoolVar("sp")
            m.AddImplication(sp, x[o][b]); m.AddImplication(sp, x[o + 2][b])
            obj.append(sp)
    for plan in nogoods:
        m.AddBoolOr([x[o][wix[b]].Not() for o, b in plan.items()])
    m.Maximize(sum(obj))
    s = cp.CpSolver()
    s.parameters.max_time_in_seconds = float(seconds)
    s.parameters.num_workers = workers
    if s.StatusName(s.Solve(m)) not in ("OPTIMAL", "FEASIBLE"):
        return None
    return {o: bowlers[next(b for b in range(len(bowlers)) if s.Value(x[o][b]))] for o in range(NO)}


def solve_innings(f, prior, patterns=None, seconds=300, workers=8, log=print, max_rounds=50, plan_tries=5):
    """Level 2 / 3a / 3b: the counts model directly. Level 1 (no over-by-over): first plan who bowled each over,
    then the same counts model with those bowlers (over runs still free); a plan that can't fit is forbidden."""
    if f.get("overs"):
        return _solve(f, prior, patterns, seconds, workers, log, max_rounds)
    t0 = time.time()
    pieces = SS.make_pieces(f)
    bad_plans, info = [], {}
    for k in range(plan_tries):
        STATUS["stage"] = f"level 1: planning who bowled each over (plan {k + 1})"
        plan = plan_bowlers(f, pieces, 30, workers, bad_plans)
        if plan is None:
            info = {"status": "no bowler plan fits", "rounds": 0}
            break
        f2 = dict(f, overs=[{"over": o, "bowler": b, "runs": None} for o, b in sorted(plan.items())])
        left = seconds - (time.time() - t0)
        per = max(30, left / max(1, plan_tries - k)) if k < plan_tries - 1 else left
        dl, info = _solve(f2, prior, patterns, per, workers, log, max_rounds)
        info["bowler_plans"] = k + 1
        if dl is not None:
            info["seconds"] = round(time.time() - t0)
            return dl, info
        log(f"  bowler plan {k + 1} gave no answer ({info.get('status')}) - forbidding it, next plan")
        bad_plans.append(plan)
        if time.time() - t0 > seconds:
            break
    info["seconds"] = round(time.time() - t0)
    return None, info


def _solve(f, prior, patterns=None, seconds=300, workers=8, log=print, max_rounds=50):
    t0 = time.time()
    pieces = SS.make_pieces(f)
    nogoods, hint = [], None
    info = {"rounds": 0, "nogoods": 0}
    for rnd in range(max_rounds):
        STATUS["stage"] = f"layer 1 (counts), round {rnd + 1}"
        cm = Counts(f, pieces, prior, patterns, nogoods)
        ok = cm.solve(max(5, seconds - (time.time() - t0)), workers, hint=hint)
        info["rounds"], info["status"] = rnd + 1, cm.status
        if not ok:
            info["seconds"] = round(time.time() - t0)
            return None, info
        plan = cm.read()
        STATUS["stage"] = f"layer 2 (order), round {rnd + 1}"
        bad, seqs = [], {}
        for g, pt in cm.parts.items():
            sq = order_part(cm.K, pt, plan[g])
            if sq is None:
                bad.append(g)
            seqs[g] = sq
        if not bad:
            info["score"], info["bound"] = cm.sol.ObjectiveValue(), cm.sol.BestObjectiveBound()
            dl = assemble(cm, pieces, seqs)
            info["seconds"] = round(time.time() - t0)
            return dl, info
        for g in bad:
            nogoods.append((g, plan[g]["vals"]))
        hint = {g: plan[g]["vals"] for g in cm.parts if g not in bad}
        if "x" in plan:
            hint["x"] = plan["x"]
        info["nogoods"] = len(nogoods)
        log(f"  round {rnd + 1}: {len(bad)} part(s) can't be ordered: {bad} - forbidding those plans, again")
    info["seconds"] = round(time.time() - t0)
    info["status"] = "gave up (too many rounds)"
    return None, info


def assemble(cm, pieces, seqs):
    K, dl = cm.K, []
    for g in sorted(cm.parts):
        i = g[0]
        p, pt = pieces[i], cm.parts[g]
        o = pt["over"]

        def emit(X, d0, isw):
            dl.append({"over": o, "bowler": cm.bowler_of[o], "striker": p[X], "non_striker": p["B" if X == "A" else "A"],
                       "bat": d0["bat"], "kind": d0["kind"], "ran": d0["ran"], "ex_w": d0["ex_w"], "ex_nb": d0["ex_nb"],
                       "ex_b": d0["ex_b"], "ex_lb": d0["ex_lb"], "legal": d0["legal"],
                       "out": p["wicket"]["out"] if isw else None, "how": p["how"] if isw else None,
                       "label": "guessed"})
        for X, t, isw in seqs[g]:
            emit(X, K[t], isw)
        a = pt["anchor"]
        if a is not None:
            emit(pt["aX"], {"bat": a["bat"], "kind": "", "ran": 0, "ex_w": 0, "ex_nb": 0, "ex_b": 0, "ex_lb": 0,
                            "legal": True}, False)
            dl[-1]["label"] = "real"
    dl.sort(key=lambda d: d["over"])        # stable: partnership order inside an over is kept
    return dl


if __name__ == "__main__":
    import argparse, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db_load as L
    import accuracy_test as AT
    ap = argparse.ArgumentParser()
    ap.add_argument("match"); ap.add_argument("--level", default="2"); ap.add_argument("--innings", type=int, default=1)
    ap.add_argument("--db", default="data/everyball.db"); ap.add_argument("--time", type=float, default=300)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    c = L.connect(a.db)
    info = L.match_info(c, a.match)
    truth, why = L.true_deliveries(c, a.match, a.innings)
    facts = F.facts_from_deliveries(truth, info["day"], info["bpo"], a.level)
    prior, pat = AT.prior_without(c, a.match), AT.patterns_without(c, a.match)
    dl, inf = solve_innings(facts, prior, pat, a.time, a.workers)
    print(inf)
    if dl:
        print("replay problems:", F.replay(dl, facts)[:5])
