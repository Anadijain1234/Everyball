"""Rebuild ball-by-ball for an ODI innings from what is known (scorecard, bowler figures, extras, fall of
wickets, partnerships, + optional balls seen on video and over-by-over), with Google OR-Tools CP-SAT.

Every rebuilt innings fits every known fact it can; facts that contradict each other (scorecard errors) are
reported, never hidden. Balls seen on video are labelled "real", everything else "estimate" (or "forced"
with --forced: no other value is possible).

THE LAWS BUILT IN (hard rules, never broken)
  over = exactly balls-per-over legal balls (6, or 8 in 8-ball eras; last over = what the scorecard says);
  wides and no-balls are extra deliveries in the over and never the last delivery of an over;
  one bowler per over, never two overs in a row; batters cross on odd runs that were run (not on boundaries);
  ends change after every over; openers = first two in the batting order; the next batter in the order
  comes in at each wicket; bowled / lbw / caught / c&b only off a legal ball and only the striker;
  stumped / hit wicket also off a wide, never off a no-ball; run out off anything, striker or non-striker,
  with runs completed and the new batter at either end; caught: before 1 Oct 2022 the batters may have
  crossed (survivor faces), from then the new batter always faces; no runs on bowled / lbw / caught /
  stumped / hit wicket balls; the bowler named in "b X" bowled that ball; the last wicket of an all-out
  innings is the last ball; wides: 1 + runs run (before 30 Sep 1998: runs run, or 1 if none); no-balls:
  1 + runs (before 30 Sep 1998: 1 only if nothing else scored); byes off a no-ball count as no-balls until
  1 Oct 2017, then as byes / leg-byes; wides and no-balls go in the bowler's figures (from 1983); a maiden
  = a complete over with nothing conceded by the bowler; a batter's balls faced include no-balls, not wides.
THE FACTS (soft: kept whenever they agree with each other; any that cannot all be true are reported)
  team total, extras (byes, leg-byes, wides, no-balls); each batter's runs, balls, 4s, 6s, how out;
  each bowler's balls, runs, maidens, wides, no-balls, dots, 4s and 6s conceded; fall of wickets (score
  and over); partnerships (runs, balls, each batter's runs and balls); anchors (balls seen on video:
  over, ball, runs, striker, bowler); over-by-over (bowler and runs of an over).
NOT MODELLED (rare; such innings are refused with a message): retired hurt / retired out / timed out,
  a bowler changed in the middle of an over, short runs, penalty runs given on a ball (team-level penalty
  runs are fine), runs off a dead ball.

Usage (repo root, after: pip install ortools):
  python3 scripts/solver/rebuild.py test O3148 2 --level highlights     # hide a real innings, rebuild, score it
  python3 scripts/solver/rebuild.py test20 --levels card,highlights,overs  # 20 random real innings, resumable
  python3 scripts/solver/rebuild.py fill O1065 1 --kaggle data/raw/kaggle   # real gap-fill from the scorecard
Options: --db data/everyball.db  --time 180 (seconds per innings)  --workers (default: all cores)  --forced
"""
import argparse, collections, csv, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cricket_facts as F
import db_load as L

try:
    from ortools.sat.python import cp_model as cp
    BACKEND = "ortools"
except ImportError:  # local testing only
    import cpshim as cp
    BACKEND = "shim"

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUTDIR = os.path.join(ROOT, "data", "solver")
W = 1000          # cost of one unit of a broken fact (runs, balls, ...) - much more than any likelihood


class Rebuilder:
    def __init__(self, facts, prior, extra_slots=None):
        self.f, self.prior = facts, prior
        self.era, self.bpo = facts["era"], facts["bpo"]
        self.m = cp.CpModel()
        self.slack = []           # (name, var)
        self.order = facts["order"]
        self.NB = len(self.order)
        self.bix = {p: i for i, p in enumerate(self.order)}
        self.bowlers = [b["pid"] for b in facts["bowlers"]]
        self.wix = {p: i for i, p in enumerate(self.bowlers)}
        lt = facts["legal_total"]
        self.NO = max(1, -(-lt // self.bpo))
        self.L = [self.bpo] * (self.NO - 1) + [lt - self.bpo * (self.NO - 1)]
        known = [b.get("wd") for b in facts["bowlers"]] + [b.get("nb") for b in facts["bowlers"]]
        if extra_slots is None:
            if all(k is not None for k in known):
                extra_slots = min(4, sum(known))
            else:
                ex = facts.get("extras") or {}
                guess = (ex.get("wd") or 0) + (ex.get("nb") or 0)
                extra_slots = min(4, guess if (ex.get("wd") is not None and ex.get("nb") is not None) else 3)
        self.X = max(0, extra_slots)

    # ------------------------------------------------------------------ small helpers
    def b(self, n):
        return self.m.NewBoolVar(n)

    def i(self, lo, hi, n):
        return self.m.NewIntVar(lo, hi, n)

    def AND(self, lits, n):
        z = self.b(n)
        for l in lits:
            self.m.AddImplication(z, l)
        self.m.AddBoolOr([z] + [l.Not() for l in lits])
        return z

    def eq_bool(self, expr, k, n):
        z = self.b(n)
        self.m.Add(expr == k).OnlyEnforceIf(z)
        self.m.Add(expr != k).OnlyEnforceIf(z.Not())
        return z

    def soft_eq(self, expr, value, name, ub=1000):
        if value is None:
            return
        s = self.i(0, ub, "sl_" + name)
        self.m.Add(expr - value <= s)
        self.m.Add(value - expr <= s)
        self.slack.append((name, s))

    def cond(self, expr, lit, ub, n):
        """y = expr if lit else 0 (expr >= 0)"""
        y = self.i(0, ub, n)
        self.m.Add(y == expr).OnlyEnforceIf(lit)
        self.m.Add(y == 0).OnlyEnforceIf(lit.Not())
        return y

    # ------------------------------------------------------------------ the model
    def build(self):
        m, f, era, bpo = self.m, self.f, self.era, self.bpo
        P, BB, TB = era["pen_plus"], era["nb_byes_are_byes"], era["to_bowler"]
        self.slots = []                # (over, k)
        S = []                          # per slot dicts
        for o in range(self.NO):
            for k in range(self.L[o] + self.X):
                self.slots.append((o, k))
        T = len(self.slots)
        # ---------- per-slot delivery variables
        for g, (o, k) in enumerate(self.slots):
            n = f"{o}_{k}"
            d = {"o": o, "k": k}
            d["act"], d["L"], d["W"], d["N"] = self.b("a" + n), self.b("l" + n), self.b("w" + n), self.b("n" + n)
            m.Add(d["act"] == d["L"] + d["W"] + d["N"])
            d["v"] = [self.b(f"v{n}_{x}") for x in range(7)]
            m.AddExactlyOne(d["v"])
            d["bat"] = self.i(0, 6, "bat" + n)
            m.Add(d["bat"] == sum(x * d["v"][x] for x in range(7)))
            d["rx"] = self.i(0, 4, "rx" + n)
            d["rxp"] = self.b("rxp" + n)
            m.Add(d["rx"] >= 1).OnlyEnforceIf(d["rxp"]); m.Add(d["rx"] == 0).OnlyEnforceIf(d["rxp"].Not())
            d["rx4"] = self.b("rx4" + n)
            m.Add(d["rx"] == 4).OnlyEnforceIf(d["rx4"]); m.Add(d["rx"] <= 3).OnlyEnforceIf(d["rx4"].Not())
            d["LB"] = self.b("lb" + n)
            m.AddImplication(d["LB"], d["rxp"])
            m.AddImplication(d["act"].Not(), d["v"][0]); m.Add(d["rx"] == 0).OnlyEnforceIf(d["act"].Not())
            m.AddImplication(d["W"], d["v"][0])                                  # nothing off the bat on a wide
            m.Add(d["rx"] == 0).OnlyEnforceIf(d["v"][0].Not())                   # runs off the bat XOR byes etc.
            if BB:
                m.AddBoolOr([d["L"], d["N"]]).OnlyEnforceIf(d["LB"])
            else:
                m.AddImplication(d["LB"], d["L"])
            # extras on this delivery (scorecard counting)
            for nm in ("exw", "exnb", "exb", "exlb"):
                d[nm] = self.i(0, 7, nm + n)
            m.Add(d["exw"] == 0).OnlyEnforceIf(d["W"].Not())
            if P:
                m.Add(d["exw"] == d["rx"] + 1).OnlyEnforceIf(d["W"])
            else:
                m.Add(d["exw"] == d["rx"]).OnlyEnforceIf([d["W"], d["rxp"]])
                m.Add(d["exw"] == 1).OnlyEnforceIf([d["W"], d["rxp"].Not()])
            m.Add(d["exnb"] == 0).OnlyEnforceIf(d["N"].Not())
            if P and BB:
                m.Add(d["exnb"] == 1).OnlyEnforceIf(d["N"])
            elif P:
                m.Add(d["exnb"] == d["rx"] + 1).OnlyEnforceIf(d["N"])
            else:
                m.Add(d["exnb"] == d["rx"]).OnlyEnforceIf([d["N"], d["rxp"]])
                m.Add(d["exnb"] == 1).OnlyEnforceIf([d["N"], d["rxp"].Not(), d["v"][0]])
                m.Add(d["exnb"] == 0).OnlyEnforceIf([d["N"], d["v"][0].Not()])
            carrier = d["L"]
            if BB:
                carrier = self.b("car" + n)
                m.AddBoolOr([d["L"], d["N"], carrier.Not()]); m.AddImplication(d["L"], carrier); m.AddImplication(d["N"], carrier)
            qb = self.AND([carrier, d["rxp"], d["LB"].Not()], "qb" + n)
            ql = self.AND([carrier, d["LB"]], "ql" + n)
            m.Add(d["exb"] == d["rx"]).OnlyEnforceIf(qb); m.Add(d["exb"] == 0).OnlyEnforceIf(qb.Not())
            m.Add(d["exlb"] == d["rx"]).OnlyEnforceIf(ql); m.Add(d["exlb"] == 0).OnlyEnforceIf(ql.Not())
            d["conc"] = self.i(0, 14, "c" + n)
            m.Add(d["conc"] == d["bat"] + ((d["exw"] + d["exnb"]) if TB else 0))
            d["tr"] = self.i(0, 14, "t" + n)
            m.Add(d["tr"] == d["bat"] + d["exw"] + d["exnb"] + d["exb"] + d["exlb"])
            # did the batters cross? (odd runs actually run; boundaries are not run)
            d["odd"] = self.b("odd" + n)
            run = self.AND([d["v"][4].Not(), d["v"][6].Not(), d["rx4"].Not()], "run" + n)
            h = self.i(0, 3, "h" + n)
            m.Add(d["bat"] + d["rx"] == 2 * h + d["odd"]).OnlyEnforceIf(run)
            m.AddImplication(run.Not(), d["odd"].Not())
            cz = self.b("cz" + n)
            m.Add(d["conc"] == 0).OnlyEnforceIf(cz); m.Add(d["conc"] >= 1).OnlyEnforceIf(cz.Not())
            d["dot"] = self.AND([d["L"], cz], "dot" + n)
            S.append(d)
        self.S = S
        # ---------- over structure
        first = {}
        for g, (o, k) in enumerate(self.slots):
            first.setdefault(o, g)
        self.first = first
        for o in range(self.NO):
            gs = [g for g, (oo, _) in enumerate(self.slots) if oo == o]
            Lo = self.L[o]
            m.Add(sum(S[g]["L"] for g in gs) == Lo)
            run = []
            for j, g in enumerate(gs):
                run.append(S[g]["L"])
                S[g]["lid"] = self.i(0, Lo, f"lid{o}_{j}")
                m.Add(S[g]["lid"] == sum(run))
                if j + 1 < len(gs):
                    nx = S[gs[j + 1]]
                    m.AddImplication(nx["act"], S[g]["act"])                    # active slots first
                    m.Add(S[g]["lid"] <= Lo - 1).OnlyEnforceIf(nx["act"])      # nothing after the last legal ball
                    m.AddBoolOr([nx["act"], S[g]["L"], S[g]["act"].Not()])     # last delivery is legal
                    S[g]["eo"] = self.AND([S[g]["act"], nx["act"].Not()], f"eo{o}_{j}")
                else:
                    m.AddImplication(S[g]["act"], S[g]["L"])
                    S[g]["eo"] = S[g]["act"]
                S[g]["eqL"] = {}
                for jj in range(1, Lo + 1):
                    z = self.b(f"eqL{o}_{j}_{jj}")
                    m.Add(S[g]["lid"] == jj).OnlyEnforceIf(z)
                    m.AddImplication(z, S[g]["L"])
                    S[g]["eqL"][jj] = z
                m.Add(sum(S[g]["eqL"].values()) == S[g]["L"])
        base, acc = [], 0
        for o in range(self.NO):
            base.append(acc); acc += self.L[o]
        self.base = base
        # cumulative team runs
        cum = []
        for g in range(T):
            c = self.i(0, 2000, f"cum{g}")
            m.Add(c == (cum[-1] if cum else 0) + S[g]["tr"])
            cum.append(c)
        # ---------- bowlers
        NBW = len(self.bowlers)
        if NBW == 0:
            raise ValueError("no bowlers in the facts")
        self.eqB = [[self.b(f"B{o}_{b}") for b in range(NBW)] for o in range(self.NO)]
        for o in range(self.NO):
            m.AddExactlyOne(self.eqB[o])
            if o + 1 < self.NO:
                for b in range(NBW):
                    m.Add(self.eqB[o][b] + self.eqB[o + 1][b] <= 1)
        agg = {}
        for o in range(self.NO):
            gs = [g for g, (oo, _) in enumerate(self.slots) if oo == o]
            agg[o] = {"conc": sum(S[g]["conc"] for g in gs), "tr": sum(S[g]["tr"] for g in gs),
                      "wd": sum(S[g]["W"] for g in gs), "nb": sum(S[g]["N"] for g in gs),
                      "dots": sum(S[g]["dot"] for g in gs), "4": sum(S[g]["v"][4] for g in gs),
                      "6": sum(S[g]["v"][6] for g in gs)}
            if self.L[o] == bpo:
                mz = self.b(f"mz{o}")
                m.Add(agg[o]["conc"] == 0).OnlyEnforceIf(mz); m.Add(agg[o]["conc"] >= 1).OnlyEnforceIf(mz.Not())
                agg[o]["maiden"] = mz
        self.agg = agg
        ub = {"conc": 14 * (bpo + self.X), "wd": bpo + self.X, "nb": bpo + self.X, "dots": bpo, "4": bpo + self.X, "6": bpo + self.X}
        for b, fb in enumerate(f["bowlers"]):
            self.soft_eq(sum(self.L[o] * self.eqB[o][b] for o in range(self.NO)), fb.get("balls"), f"bowler{fb['pid']}_balls")
            for key, fk in (("conc", "runs"), ("wd", "wd"), ("nb", "nb"), ("dots", "dots"), ("4", "4"), ("6", "6")):
                if fb.get(fk) is None:
                    continue
                parts = [self.cond(agg[o][key], self.eqB[o][b], ub[key], f"bw{b}_{key}_{o}") for o in range(self.NO)]
                self.soft_eq(sum(parts), fb[fk], f"bowler{fb['pid']}_{fk}")
            if fb.get("maidens") is not None:
                ms = [self.AND([agg[o]["maiden"], self.eqB[o][b]], f"mb{b}_{o}") for o in range(self.NO) if "maiden" in agg[o]]
                self.soft_eq(sum(ms) if ms else 0, fb["maidens"], f"bowler{fb['pid']}_maidens")
        # ---------- team totals and extras
        self.soft_eq(sum(S[g]["tr"] for g in range(T)) + (f.get("pen") or 0), f["total"], "team_total")
        ex = f.get("extras") or {}
        for key, nm in (("b", "exb"), ("lb", "exlb"), ("wd", "exw"), ("nb", "exnb")):
            self.soft_eq(sum(S[g][nm] for g in range(T)), ex.get(key), "extras_" + key)
        if all(ex.get(k) is None for k in ("b", "lb", "wd", "nb")) and ex.get("all") is not None:
            self.soft_eq(sum(S[g]["exb"] + S[g]["exlb"] + S[g]["exw"] + S[g]["exnb"] for g in range(T)), ex["all"], "extras_all")
        # ---------- dismissals
        fow = f["fow"]
        ND = len(fow)
        self.isD = [[None] * ND for _ in range(T)]
        self.pos = []
        for d in range(ND):
            for g in range(T):
                self.isD[g][d] = self.b(f"D{g}_{d}")
                m.AddImplication(self.isD[g][d], S[g]["act"])
            m.AddExactlyOne([self.isD[g][d] for g in range(T)])
            p = self.i(0, T - 1, f"pos{d}")
            m.Add(p == sum(g * self.isD[g][d] for g in range(T)))
            self.pos.append(p)
            if d:
                m.Add(self.pos[d - 1] + 1 <= p)
        for g in range(T):
            if ND:
                m.Add(sum(self.isD[g][d] for d in range(ND)) <= 1)
        # ---------- who is batting: striker / non-striker before every slot
        NB = self.NB
        if NB < 2:
            raise ValueError("fewer than two batters")
        Sx = [self.i(0, NB - 1, f"S{g}") for g in range(T + 1)]
        Nx = [self.i(0, NB - 1, f"N{g}") for g in range(T + 1)]
        self.Sx, self.Nx = Sx, Nx
        for g in range(T + 1):
            m.Add(Sx[g] != Nx[g])
        first_on = self.b("first_on_strike_is_1")
        m.Add(Sx[0] == 0).OnlyEnforceIf(first_on.Not()); m.Add(Nx[0] == 1).OnlyEnforceIf(first_on.Not())
        m.Add(Sx[0] == 1).OnlyEnforceIf(first_on); m.Add(Nx[0] == 0).OnlyEnforceIf(first_on)
        # Each out batter is dismissed on exactly one delivery. If the fall-of-wicket order is unknown (no
        # scores), the solver also decides WHO fell at each wicket.
        outs = []
        for w in fow:
            how = F.norm_kind(w["how"])
            if how in F.NO_DELIVERY:
                raise ValueError(f"dismissal '{how}' is not a delivery - not supported")
            if w["out"] not in self.bix:
                raise ValueError(f"out batter {w['out']} is not in the batting order")
            outs.append((self.bix[w["out"]], how, w.get("bowler")))
        self.order_known = all(w.get("score") is not None or w.get("legal") is not None for w in fow)
        all_out = ND >= NB - 1
        NO_ = len(outs)
        self.eqO = [[None] * NO_ for _ in range(T)]
        wk = []
        for g in range(T):
            w_ = sum(self.isD[g][d] for d in range(ND)) if ND else 0
            wk.append(w_)
            for j in range(NO_):
                self.eqO[g][j] = self.b(f"O{g}_{j}")
            if NO_:
                m.Add(sum(self.eqO[g]) == w_)
        for j in range(NO_):
            m.AddExactlyOne([self.eqO[g][j] for g in range(T)])
        if self.order_known:
            for d in range(ND):
                for g in range(T):
                    m.AddImplication(self.isD[g][d], self.eqO[g][d])
        # number of wickets before each slot -> who comes in next (batting order is known)
        if ND:
            bef = [[None] * ND for _ in range(T)]
            for g in range(T):
                for d in range(ND):
                    z = self.b(f"bef{g}_{d}")
                    m.Add(self.pos[d] <= g - 1).OnlyEnforceIf(z); m.Add(self.pos[d] >= g).OnlyEnforceIf(z.Not())
                    bef[g][d] = z
            self.bef = bef
        self.fs, self.fl = {}, {}
        for d, w in enumerate(fow):
            if w.get("score") is not None:
                self.fs[d] = self.i(0, 2000, f"sl_fow{d}_score"); self.slack.append((f"fow{d + 1}_score", self.fs[d]))
            if w.get("legal") is not None:
                self.fl[d] = self.i(0, 2000, f"sl_fow{d}_balls"); self.slack.append((f"fow{d + 1}_balls", self.fl[d]))
        for g in range(T):
            d_ = S[g]
            A, Bv = self.i(0, NB - 1, f"A{g}"), self.i(0, NB - 1, f"Bv{g}")
            nw = self.b(f"nw{g}")
            if ND:
                m.Add(wk[g] == 0).OnlyEnforceIf(nw); m.Add(wk[g] == 1).OnlyEnforceIf(nw.Not())
            else:
                m.Add(nw == 1)
            m.Add(A == Sx[g]).OnlyEnforceIf([nw, d_["odd"].Not()]); m.Add(Bv == Nx[g]).OnlyEnforceIf([nw, d_["odd"].Not()])
            m.Add(A == Nx[g]).OnlyEnforceIf([nw, d_["odd"]]); m.Add(Bv == Sx[g]).OnlyEnforceIf([nw, d_["odd"]])
            if ND:
                # fall of wickets: score and balls at the fall (time order)
                for d, w in enumerate(fow):
                    z = self.isD[g][d]
                    if w.get("score") is not None:
                        s = self.fs[d]
                        m.Add(cum[g] - w["score"] <= s).OnlyEnforceIf(z); m.Add(w["score"] - cum[g] <= s).OnlyEnforceIf(z)
                    if w.get("legal") is not None:
                        s = self.fl[d]
                        clg = base[d_["o"]] + d_["lid"]
                        m.Add(clg - w["legal"] <= s).OnlyEnforceIf(z); m.Add(w["legal"] - clg <= s).OnlyEnforceIf(z)
                # the last wicket of an all-out innings ends it
                last_here = self.isD[g][ND - 1] if all_out else None
                if last_here is not None:
                    m.AddImplication(last_here, d_["eo"])
                    m.Add(base[d_["o"]] + d_["lid"] == f["legal_total"]).OnlyEnforceIf(last_here)
                    m.Add(A == Sx[g]).OnlyEnforceIf(last_here); m.Add(Bv == Nx[g]).OnlyEnforceIf(last_here)
                qv = self.i(0, ND + 2, f"q{g}")
                m.Add(qv == 2 + sum(bef[g]))                                  # the next batter in the order
                cr, ro, ns = self.b(f"cross{g}"), self.b(f"ros{g}"), self.b(f"newstr{g}")
                for j, (o_idx, how, bowler) in enumerate(outs):
                    z = self.eqO[g][j]
                    if how in F.ON_LEGAL_ONLY:
                        m.AddImplication(z, d_["L"]); m.AddImplication(z, d_["v"][0]); m.Add(d_["rx"] == 0).OnlyEnforceIf(z)
                    elif how in ("stumped", "hit wicket"):
                        m.AddImplication(z, d_["N"].Not()); m.AddImplication(z, d_["v"][0]); m.Add(d_["rx"] == 0).OnlyEnforceIf(z)
                    elif how in ("handled the ball", "hit the ball twice"):
                        m.AddImplication(z, d_["W"].Not()); m.AddImplication(z, d_["v"][0])
                    else:   # run out, obstructing, unknown: runs completed allowed, no boundary
                        m.AddImplication(z, d_["v"][4].Not()); m.AddImplication(z, d_["v"][6].Not()); m.AddImplication(z, d_["rx4"].Not())
                    if how in F.CREDITED and bowler in self.wix:                 # "b X": X bowled this ball
                        m.AddImplication(z, self.eqB[d_["o"]][self.wix[bowler]])
                    striker_only = how in F.CREDITED + ("handled the ball", "hit the ball twice")
                    if striker_only:
                        m.Add(Sx[g] == o_idx).OnlyEnforceIf(z)
                    else:
                        m.Add(Sx[g] == o_idx).OnlyEnforceIf([z, ro]); m.Add(Nx[g] == o_idx).OnlyEnforceIf([z, ro.Not()])
                    go = [z] if last_here is None else [z, last_here.Not()]
                    if striker_only and (how not in ("caught", "caught and bowled") or era["caught_new_on_strike"]):
                        m.Add(A == qv).OnlyEnforceIf(go); m.Add(Bv == Nx[g]).OnlyEnforceIf(go)
                    elif striker_only:                                              # caught, old Laws: maybe crossed
                        m.Add(A == qv).OnlyEnforceIf(go + [cr.Not()]); m.Add(Bv == Nx[g]).OnlyEnforceIf(go + [cr.Not()])
                        m.Add(A == Nx[g]).OnlyEnforceIf(go + [cr]); m.Add(Bv == qv).OnlyEnforceIf(go + [cr])
                    else:                                                           # run out: new batter at either end
                        m.Add(A == qv).OnlyEnforceIf(go + [ro, ns]); m.Add(Bv == Nx[g]).OnlyEnforceIf(go + [ro, ns])
                        m.Add(A == Nx[g]).OnlyEnforceIf(go + [ro, ns.Not()]); m.Add(Bv == qv).OnlyEnforceIf(go + [ro, ns.Not()])
                        m.Add(A == qv).OnlyEnforceIf(go + [ro.Not(), ns]); m.Add(Bv == Sx[g]).OnlyEnforceIf(go + [ro.Not(), ns])
                        m.Add(A == Sx[g]).OnlyEnforceIf(go + [ro.Not(), ns.Not()]); m.Add(Bv == qv).OnlyEnforceIf(go + [ro.Not(), ns.Not()])
            eo = d_["eo"]
            m.Add(Sx[g + 1] == A).OnlyEnforceIf(eo.Not()); m.Add(Nx[g + 1] == Bv).OnlyEnforceIf(eo.Not())
            m.Add(Sx[g + 1] == Bv).OnlyEnforceIf(eo); m.Add(Nx[g + 1] == A).OnlyEnforceIf(eo)
        # ---------- batters
        self.eqS = [[None] * NB for _ in range(T)]
        bR = collections.defaultdict(list); bB = collections.defaultdict(list)
        b4 = collections.defaultdict(list); b6 = collections.defaultdict(list)
        self.brun, self.fac = {}, {}
        for g in range(T):
            d_ = S[g]
            for p in range(NB):
                e = self.b(f"eqS{g}_{p}")
                m.Add(Sx[g] == p).OnlyEnforceIf(e)
                self.eqS[g][p] = e
            m.AddExactlyOne(self.eqS[g])
            for p in range(NB):
                e = self.eqS[g][p]
                fac = self.AND([e, d_["act"], d_["W"].Not()], f"fac{g}_{p}")
                br = self.cond(d_["bat"], e, 6, f"br{g}_{p}")
                self.brun[(g, p)], self.fac[(g, p)] = br, fac
                bR[p].append(br); bB[p].append(fac)
                b4[p].append(self.AND([e, d_["v"][4]], f"f4{g}_{p}")); b6[p].append(self.AND([e, d_["v"][6]], f"f6{g}_{p}"))
        for fb in f["batters"]:
            p = self.bix[fb["pid"]]
            self.soft_eq(sum(bR[p]), fb.get("R"), f"batter{fb['pid']}_runs")
            self.soft_eq(sum(bB[p]), fb.get("B"), f"batter{fb['pid']}_balls")
            self.soft_eq(sum(b4[p]), fb.get("4"), f"batter{fb['pid']}_4s")
            self.soft_eq(sum(b6[p]), fb.get("6"), f"batter{fb['pid']}_6s")
        # ---------- partnerships
        ps = f.get("partnerships")
        if ps and len(ps) in (ND, ND + 1) and ND:
            bef = self.bef
            for k, pk in enumerate(ps):
                inP = []
                for g in range(T):
                    z = self.b(f"inP{g}_{k}")
                    m.Add(sum(bef[g]) == k).OnlyEnforceIf(z); m.Add(sum(bef[g]) != k).OnlyEnforceIf(z.Not())
                    inP.append(z)
                if pk.get("balls") is not None:
                    self.soft_eq(sum(self.AND([inP[g], S[g]["act"], S[g]["W"].Not()], f"pb{g}_{k}") for g in range(T)), pk["balls"], f"stand{k + 1}_balls")
                for pid, v in (pk.get("runs_by") or {}).items():
                    if pid in self.bix:
                        p = self.bix[pid]
                        self.soft_eq(sum(self.cond(self.brun[(g, p)], inP[g], 6, f"pr{g}_{k}_{p}") for g in range(T)), v, f"stand{k + 1}_{pid}_runs")
                for pid, v in (pk.get("balls_by") or {}).items():
                    if pid in self.bix:
                        p = self.bix[pid]
                        self.soft_eq(sum(self.AND([inP[g], self.fac[(g, p)]], f"pq{g}_{k}_{p}") for g in range(T)), v, f"stand{k + 1}_{pid}_balls")
        # ---------- anchors (balls seen on video) and over-by-over facts
        self.anchor_ok = []
        for a, an in enumerate(f.get("anchors") or []):
            o, j = an["over"], an["ball"]
            if o >= self.NO or j > self.L[o]:
                continue
            ok = self.b(f"anchor{a}")
            self.anchor_ok.append((an, ok))
            for g, (oo, _) in enumerate(self.slots):
                if oo != o:
                    continue
                z = S[g]["eqL"][j]
                m.AddBoolOr([z.Not(), ok.Not(), S[g]["v"][an["bat"]]])
                if an.get("striker") in self.bix:
                    m.AddBoolOr([z.Not(), ok.Not(), self.eqS[g][self.bix[an["striker"]]]])
            if an.get("bowler") in self.wix:
                m.AddImplication(ok, self.eqB[o][self.wix[an["bowler"]]])
        for ov in f.get("overs") or []:
            o = ov["over"]
            if o >= self.NO:
                continue
            if ov.get("bowler") in self.wix:
                ok = self.b(f"overbowler{o}")
                m.AddImplication(ok, self.eqB[o][self.wix[ov["bowler"]]])
                self.anchor_ok.append(({"over": o, "what": "bowler"}, ok))
            self.soft_eq(agg[o]["tr"], ov.get("runs"), f"over{o + 1}_runs")
        # ---------- objective: break no fact, then the most likely runs
        like = []
        for g, (o, k) in enumerate(self.slots):
            ph = 0 if o < 10 else (2 if o >= 40 else 1)
            pr = self.prior.get(ph) or self.prior.get(str(ph)) or {}
            for x in range(1, 7):
                p0 = pr.get(0, pr.get("0", 0.5)); px = pr.get(x, pr.get(str(x), 0.01))
                like.append(int(round(100 * (math.log(p0) - math.log(px)))) * S[g]["v"][x])   # cost vs a dot
            like.append(150 * S[g]["rxp"])                                                      # byes etc. are rare
        m.Minimize(W * sum(s for _, s in self.slack) + W * sum(1 - ok for _, ok in self.anchor_ok) + sum(like))
        return self

    # ------------------------------------------------------------------ solve and read the answer
    def solve(self, seconds=120, workers=8):
        sol = cp.CpSolver()
        sol.parameters.max_time_in_seconds = float(seconds)
        try:
            sol.parameters.num_workers = workers
        except Exception:
            sol.parameters.num_search_workers = workers
        st = sol.Solve(self.m)
        self.sol, self.status = sol, sol.StatusName(st)
        if self.status not in ("OPTIMAL", "FEASIBLE"):
            return None
        V, Bo = sol.Value, sol.BooleanValue
        out = []
        ND = len(self.f["fow"])
        for g, (o, k) in enumerate(self.slots):
            d = self.S[g]
            if not Bo(d["act"]):
                continue
            bw = next(b for b in range(len(self.bowlers)) if Bo(self.eqB[o][b]))
            kind = "wd" if Bo(d["W"]) else "nb" if Bo(d["N"]) else ""
            if not kind and V(d["rx"]) > 0:
                kind = "lb" if Bo(d["LB"]) else "b"
            jj = next((x for x in range(len(self.f["fow"])) if Bo(self.eqO[g][x])), None)
            w = self.f["fow"][jj] if jj is not None else None
            lid = V(d["lid"]) if Bo(d["L"]) else None
            label = "estimate"
            for an, ok in self.anchor_ok:
                if an.get("ball") and an["over"] == o and lid == an["ball"] and Bo(ok):
                    label = "real"
            out.append({"over": o, "lball": lid, "bowler": self.bowlers[bw], "striker": self.order[V(self.Sx[g])],
                        "non_striker": self.order[V(self.Nx[g])], "bat": V(d["bat"]), "kind": kind, "ran": V(d["rx"]),
                        "ex_w": V(d["exw"]), "ex_nb": V(d["exnb"]), "ex_b": V(d["exb"]), "ex_lb": V(d["exlb"]),
                        "legal": Bo(d["L"]), "out": w["out"] if w else None, "how": w["how"] if w else None,
                        "label": label, "g": g})
        self.broken = [(n, V(s)) for n, s in self.slack if V(s) > 0] + \
                      [(f"anchor over {an['over'] + 1}" + (f" ball {an['ball']}" if an.get("ball") else " bowler"), 1)
                       for an, ok in self.anchor_ok if not Bo(ok)]
        return out

    def mark_forced(self, dl, seconds=5):
        """For each estimated legal ball: is any other run value possible with no more broken facts?"""
        if BACKEND != "ortools":
            return dl
        best = sum(v for _, v in self.broken)
        self.m.Add(sum(s for _, s in self.slack) + sum(1 - ok for _, ok in self.anchor_ok) <= best)
        for x in dl:
            if x["label"] != "estimate" or not x["legal"]:
                continue
            d = self.S[x["g"]]
            self.m.ClearAssumptions()
            self.m.AddAssumptions([d["v"][x["bat"]].Not(), d["act"]])
            sol = cp.CpSolver(); sol.parameters.max_time_in_seconds = seconds
            if sol.StatusName(sol.Solve(self.m)) == "INFEASIBLE":
                x["label"] = "forced"
        self.m.ClearAssumptions()
        return dl


# ---------------------------------------------------------------------------------------------- scoring
def score(sol, truth, facts):
    """Compare a rebuilt innings with the real one."""
    def keyed(dl):
        out, lc = {}, collections.Counter()
        for d in dl:
            if d["legal"]:
                lc[d["over"]] += 1
                out[(d["over"], lc[d["over"]])] = d
        return out
    a, t = keyed(sol), keyed(truth)
    anch = {(x["over"], x["ball"]) for x in facts.get("anchors") or []}
    hid = [k for k in t if k not in anch and k in a]
    exact = sum(a[k]["bat"] == t[k]["bat"] for k in hid) / max(1, len(hid))
    dot_base = sum(t[k]["bat"] == 0 for k in hid) / max(1, len(hid))
    strike = sum(a[k]["striker"] == t[k]["striker"] for k in t if k in a) / max(1, len(t))
    ob_t = {d["over"]: d["bowler"] for d in truth}; ob_a = {d["over"]: d["bowler"] for d in sol}
    bowl = sum(ob_a.get(o) == b for o, b in ob_t.items()) / max(1, len(ob_t))

    def duels(dl):
        r, b = collections.Counter(), collections.Counter()
        for d in dl:
            r[(d["striker"], d["bowler"])] += d["bat"]
            if d["kind"] != "wd":
                b[(d["striker"], d["bowler"])] += 1
        return r, b
    (ra, ba), (rt, bt) = duels(sol), duels(truth)
    ks = set(ra) | set(rt)
    dr = sum(abs(ra[k] - rt[k]) for k in ks) / 2 / max(1, sum(rt.values()))
    db = sum(abs(ba[k] - bt[k]) for k in ks) / 2 / max(1, sum(bt.values()))
    # naive baseline: each batter's runs (minus boundaries we know) and balls spread over bowlers by share
    share = collections.Counter(d["bowler"] for d in truth if d["kind"] != "wd")
    tot = sum(share.values())
    nr, nb = collections.Counter(), collections.Counter()
    for fb in facts["batters"]:
        known = [x for x in facts.get("anchors") or [] if x["striker"] == fb["pid"]]
        for x in known:
            nr[(fb["pid"], x["bowler"])] += x["bat"]
        rest = (fb.get("R") or 0) - sum(x["bat"] for x in known)
        for bw, s in share.items():
            nr[(fb["pid"], bw)] += rest * s / tot; nb[(fb["pid"], bw)] += (fb.get("B") or 0) * s / tot
    ks2 = set(nr) | set(rt)
    nvr = sum(abs(nr[k] - rt[k]) for k in ks2) / 2 / max(1, sum(rt.values()))
    nvb = sum(abs(nb[k] - bt[k]) for k in set(nb) | set(bt)) / 2 / max(1, sum(bt.values()))
    orr, otr = collections.Counter(), collections.Counter()
    for d in sol:
        orr[d["over"]] += F.team_runs(d)
    for d in truth:
        otr[d["over"]] += F.team_runs(d)
    over_mae = sum(abs(orr[o] - otr[o]) for o in otr) / max(1, len(otr))
    return {"hidden_balls": len(hid), "exact_runs": exact, "always_dot": dot_base, "striker": strike,
            "bowler_of_over": bowl, "duel_runs_misplaced": dr, "naive_duel_runs": nvr,
            "duel_balls_misplaced": db, "naive_duel_balls": nvb, "over_runs_mae": over_mae}


def load_prior(c):
    path = os.path.join(OUTDIR, "prior.json")
    if os.path.exists(path):
        return {int(k): {int(a): b for a, b in v.items()} for k, v in json.load(open(path)).items()}
    pr = L.prior_table(c)
    os.makedirs(OUTDIR, exist_ok=True)
    json.dump(pr, open(path, "w"))
    return pr


def run_one(c, mid, inn, level, a, prior):
    truth, why = L.true_deliveries(c, mid, inn)
    if why:
        return None, why
    info = L.match_info(c, mid)
    if a.overs_limit:
        truth = [d for d in truth if d["over"] < a.overs_limit]
    facts = F.facts_from_deliveries(truth, info["day"], info["bpo"], level)
    probs = F.replay(truth, facts)
    if probs:
        return None, "the real ball-by-ball itself breaks a rule we model (" + probs[0] + ")"
    t0 = time.time()
    rb = Rebuilder(facts, prior).build()
    sol = rb.solve(a.time, a.workers)
    if sol is None:
        return None, f"solver: {rb.status}"
    left = F.replay(sol, facts)
    res = score(sol, truth, facts)
    res.update({"match": mid, "inn": inn, "level": level, "status": rb.status, "seconds": round(time.time() - t0),
                "facts_broken": len(rb.broken), "replay_problems": len(left)})
    return (res, sol, rb, left), None


def show(res, rb, left):
    print(f"  solver {res['status']} in {res['seconds']} s | facts it could not keep: {len(rb.broken)}"
          + (f" -> {rb.broken[:6]}" if rb.broken else "") + f" | rule check of the rebuilt innings: "
          + ("clean" if not left else f"{len(left)} problems, e.g. {left[:3]}"))
    print(f"  exact runs on hidden balls {res['exact_runs']:.0%} (always-dot guess {res['always_dot']:.0%}) | "
          f"striker {res['striker']:.0%} | bowler of each over {res['bowler_of_over']:.0%}")
    print(f"  duels: runs on the wrong bowler {res['duel_runs_misplaced']:.0%} (naive split {res['naive_duel_runs']:.0%}), "
          f"balls {res['duel_balls_misplaced']:.0%} (naive {res['naive_duel_balls']:.0%}) | runs per over off by {res['over_runs_mae']:.2f}")


def write_csv(path, sol, names):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["over", "ball", "bowler", "striker", "non_striker", "bat_runs", "extra_type", "extras", "wicket", "how", "label"])
        lc = collections.Counter()
        for d in sol:
            if d["legal"]:
                lc[d["over"]] += 1
            ex = d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"]
            w.writerow([d["over"] + 1, lc[d["over"]] + (0 if d["legal"] else 1), names.get(d["bowler"], d["bowler"]),
                        names.get(d["striker"], d["striker"]), names.get(d["non_striker"], d["non_striker"]), d["bat"],
                        d["kind"], ex, names.get(d["out"], "") if d["out"] else "", d["how"] or "", d["label"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["test", "test20", "fill"])
    ap.add_argument("match", nargs="?"); ap.add_argument("inn", nargs="?", type=int)
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "everyball.db"))
    ap.add_argument("--level", default="highlights", choices=["card", "highlights", "overs"])
    ap.add_argument("--levels", default="card,highlights,overs")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--time", type=float, default=180, help="seconds per innings")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--kaggle", default="")
    ap.add_argument("--forced", action="store_true")
    ap.add_argument("--overs-limit", type=int, default=0, help="only the first N overs (quick tests)")
    a = ap.parse_args()
    if BACKEND != "ortools":
        print("NOTE: OR-Tools is not installed (pip install ortools). Using the slow test stand-in.")
    c = L.connect(a.db)
    names = dict(c.execute("select player_id, name from players"))
    prior = load_prior(c)
    if a.cmd == "test":
        if not a.match or not a.inn:
            sys.exit("usage: rebuild.py test MATCH_ID INNINGS [--level card|highlights|overs]")
        r, why = run_one(c, a.match, a.inn, a.level, a, prior)
        if why:
            sys.exit(f"Cannot test {a.match} inn {a.inn}: {why}")
        res, sol, rb, left = r
        print(f"{a.match} innings {a.inn}, level '{a.level}':"); show(res, rb, left)
        if a.forced:
            sol = rb.mark_forced(sol)
        out = os.path.join(OUTDIR, "tests", f"{a.match}_{a.inn}_{a.level}.csv")
        write_csv(out, sol, names); print(f"  rebuilt balls: {os.path.relpath(out, ROOT)}")
    elif a.cmd == "test20":
        out = os.path.join(OUTDIR, "test_results.csv")
        done = set()
        if os.path.exists(out):
            done = {(r["match"], r["inn"], r["level"]) for r in csv.DictReader(open(out, encoding="utf-8"))}
        cols = ["match", "inn", "level", "status", "seconds", "facts_broken", "replay_problems", "hidden_balls", "exact_runs",
                "always_dot", "striker", "bowler_of_over", "duel_runs_misplaced", "naive_duel_runs", "duel_balls_misplaced",
                "naive_duel_balls", "over_runs_mae"]
        new = not os.path.exists(out)
        fh = open(out, "a", newline="", encoding="utf-8"); w = csv.DictWriter(fh, fieldnames=cols)
        if new:
            w.writeheader()
        tested = 0
        for mid, inn in L.odi_innings_with_bbb(c):
            if tested >= a.n:
                break
            truth, why = L.true_deliveries(c, mid, inn)
            if why:
                continue
            info = L.match_info(c, mid)
            if F.replay(truth, F.facts_from_deliveries(truth, info["day"], info["bpo"], "card")):
                continue
            tested += 1
            for level in a.levels.split(","):
                if (mid, str(inn), level) in done:
                    continue
                r, why = run_one(c, mid, inn, level, a, prior)
                if why:
                    print(f"{mid} inn {inn} {level}: skipped ({why})"); continue
                res, sol, rb, left = r
                print(f"[{tested}/{a.n}] {mid} innings {inn}, level '{level}':"); show(res, rb, left)
                w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in res.items() if k in cols}); fh.flush()
        fh.close()
        summary(out)
    else:
        if not a.match or not a.inn:
            sys.exit("usage: rebuild.py fill MATCH_ID INNINGS [--kaggle DIR]")
        facts = L.facts_from_db(c, a.match, a.inn, a.kaggle or None)
        print(f"{a.match} innings {a.inn}: {len(facts['batters'])} batters, {len(facts['bowlers'])} bowlers, "
              f"{len(facts['fow'])} wickets, fall-of-wicket scores {'yes' if all(x.get('score') is not None for x in facts['fow']) else 'NO'}, "
              f"partnerships {'yes' if facts.get('partnerships') else 'no'}")
        rb = Rebuilder(facts, prior).build()
        sol = rb.solve(a.time, a.workers)
        if sol is None:
            sys.exit(f"Solver: {rb.status}")
        if a.forced:
            sol = rb.mark_forced(sol)
        left = F.replay(sol, facts)
        print(f"  solver {rb.status} | facts that contradict each other: {rb.broken or 'none'} | rule check: "
              + ("clean" if not left else f"{len(left)} problems: {left[:5]}"))
        out = os.path.join(OUTDIR, "rebuilt", f"{a.match}_{a.inn}.csv")
        write_csv(out, sol, names); print(f"  rebuilt balls (all 'estimate' unless seen on video): {os.path.relpath(out, ROOT)}")


def summary(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        return
    print("\nAVERAGES over all tested innings:")
    for level in sorted({r["level"] for r in rows}):
        rs = [r for r in rows if r["level"] == level]
        avg = lambda k: sum(float(r[k]) for r in rs) / len(rs)
        print(f"  {level:10} ({len(rs)} innings): exact runs {avg('exact_runs'):.0%} (always-dot {avg('always_dot'):.0%}), "
              f"striker {avg('striker'):.0%}, bowler of over {avg('bowler_of_over'):.0%}, duel runs misplaced "
              f"{avg('duel_runs_misplaced'):.0%} (naive {avg('naive_duel_runs'):.0%}), runs/over off by {avg('over_runs_mae'):.2f}, "
              f"facts broken {avg('facts_broken'):.1f}")


if __name__ == "__main__":
    main()
