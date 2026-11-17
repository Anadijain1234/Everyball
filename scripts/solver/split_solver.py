"""
Rebuild an innings ball by ball, split at the wickets (the Everyball "best version").

STEP 1 - THE PLAN (one small puzzle for the whole innings, "master")
  The innings is cut into partnerships (pieces) at every wicket, and each piece into the overs it touches
  ("segments", e.g. Gambhir-Kohli in over 7 = balls 7.3 to 7.6). For every segment the plan decides only
  COUNTS: each batter's runs, balls, 4s, 6s; wides, no-balls, byes, leg-byes; the bowler's dots; and at level 1
  also who bowled the over. Everything the scorecard says must add up exactly (hard rules):
    each partnership's runs, balls and each batter's share; each batter's 4s and 6s; each bowler's balls, runs,
    wides, no-balls, dots, 4s, 6s, maidens and wickets; the extras by type; the team total; every wicket on its
    exact ball (from the fall of wickets); level 2+: each over's bowler and runs; level 3: each four and six.
  Among plans that fit, it prefers the natural one (a batter scores at his usual rate through the stand, extras
  spread through the innings, bowlers in spells).
STEP 2 - THE PIECES (one small puzzle per partnership)
  With the counts fixed, each partnership is solved on its own: the exact order of its balls, who was on strike,
  where the extras and the wicket fall. Strike passes from one piece to the next (who faced after the wicket).
  If a piece can't be built from its plan, that plan is forbidden and step 1 runs again (it never gives up a fact).
STEP 3 - PROVEN OR GUESSED (every legal ball)
  "Proven" = no other runs off the bat were possible on that ball, given the real facts: the piece's plan was the
  only possible plan AND inside the piece no other value fits. Otherwise "guessed" (the most likely value is used:
  run rates by phase learned from our own real ball-by-ball, this match left out).

Rules of cricket built in: 6 legal balls an over (the last over can be short), wides / no-balls are extra
deliveries, an over never ends on a wide or no-ball (except the very last delivery of a chase), ends change after
every over, batters cross on odd runs that were run (not on boundaries), bowled / lbw / caught only off a legal
ball and only the striker, stumped / hit wicket not off a no-ball, run out off anything at either end, caught
before Oct 2022 the batters may have crossed, wides 1 + runs run, no-balls 1 + runs (byes off a no-ball count as
no-balls before Oct 2017), wides and no-balls go to the bowler (from 1983), a maiden = a full over with nothing
conceded by the bowler.
"""
import collections, math, time

try:
    from ortools.sat.python import cp_model as cp
    BACKEND = "ortools"
except ImportError:
    import cpshim as cp
    BACKEND = "shim"

import cricket_facts as F

LEGAL_ONLY = ("bowled", "caught", "caught and bowled", "lbw")
STRIKER_ONLY = LEGAL_ONLY + ("stumped", "hit wicket", "handled the ball", "hit the ball twice")


def _solver(seconds, workers):
    s = cp.CpSolver()
    if seconds and seconds > 0:
        s.parameters.max_time_in_seconds = float(seconds)
    try:
        s.parameters.num_workers = workers
    except Exception:
        pass
    return s


def _absdev(m, expr, ub, name):
    d = m.NewIntVar(0, ub, name)
    m.Add(expr <= d); m.Add(-expr <= d)
    return d


# ------------------------------------------------------------------------------------------- the pieces
def make_pieces(f):
    """Partnerships with their batters, ball range and wicket; and their segments (piece x over)."""
    bpo, lt, order, fow = f["bpo"], f["legal_total"], f["order"], f["fow"]
    parts = f.get("partnerships") or []
    if len(parts) not in (len(fow), len(fow) + 1):
        raise ValueError("partnerships missing or don't match the fall of wickets")
    if any(w.get("legal") is None for w in fow):
        raise ValueError("fall of wickets without the ball (over) it fell on - can't split at the wickets")
    pieces, at_crease, nxt, start = [], [order[0], order[1]], 2, 0
    for k in range(len(fow) + 1):
        w = fow[k] if k < len(fow) else None
        end = w["legal"] if w else lt
        pk = parts[k] if k < len(parts) else {"runs": 0, "balls": 0, "runs_by": {}, "balls_by": {}}
        if w is None and end == start and not pk.get("runs") and not pk.get("balls"):
            break                                                   # all out: nothing after the last wicket
        A, B = at_crease
        segs = []
        o_first = start // bpo
        o_last = (end - 1) // bpo if end > start else o_first
        for o in range(o_first, o_last + 1):
            lo, hi = max(start + 1, o * bpo + 1), min(end, o * bpo + bpo)
            n = max(0, hi - lo + 1)
            segs.append({"over": o, "n": n, "first": lo - o * bpo if n else None,
                         "ends_over": n > 0 and hi == o * bpo + bpo, "trailing": False})
        how = F.norm_kind(w["how"]) if w else None
        # a run out / stumping off a wide right after the over's last ball belongs to the NEXT over
        if end > 0 and end % bpo == 0 and ((w and how not in LEGAL_ONLY) or (not w and end < 50 * bpo)):
            segs.append({"over": end // bpo, "n": 0, "first": None, "ends_over": False, "trailing": True})
        pieces.append({"k": k, "A": A, "B": B, "start": start, "end": end, "wicket": w, "how": how, "segs": segs,
                       "runs": pk.get("runs"), "balls": pk.get("balls"),
                       "runs_by": pk.get("runs_by") or {}, "balls_by": pk.get("balls_by") or {}})
        if w:
            if w["out"] not in (A, B):
                raise ValueError(f"wicket {k + 1}: {w['out']} is not at the crease")
            survivor = B if w["out"] == A else A
            if nxt >= len(order):
                break
            at_crease = [survivor, order[nxt]]
            nxt += 1
        start = end
    return pieces


# ------------------------------------------------------------------------------------------- step 1: the plan
SK = ("TR", "CON", "DOT", "F4", "X6", "WD", "NB", "EW", "ENB", "EB", "ELB")       # per segment (partnership x over)
PK = ("FA", "FB", "XA", "XB")                                                      # per partnership


def _over_owner(pieces):
    """over -> list of piece indexes that touch it (a wicket inside an over splits it between two pieces)."""
    own = collections.defaultdict(list)
    for i, p in enumerate(pieces):
        for sg in p["segs"]:
            if i not in own[sg["over"]]:
                own[sg["over"]].append(i)
    return own


def _events(f):
    ev = None
    if f.get("over_events") is not None:
        ev = collections.Counter()
        for e in f["over_events"]:
            ev[(e["over"], e["striker"], e["bat"])] += e["n"]
    if f.get("anchors"):
        ev = ev if ev is not None else collections.Counter()
        if f.get("over_events") is None:
            for a in f["anchors"]:
                ev[(a["over"], a["striker"], a["bat"])] += 1
    return ev


class Master:
    """The plan: for every partnership, how its batters' 4s / 6s and the extras are split, and for every
    bowler what he conceded inside that partnership (runs, dots, 4s, 6s, wides, no-balls, byes, maidens).
    At level 1 also who bowled each over. Everything adds up exactly to the scorecard."""

    def __init__(self, f, pieces, nogoods=(), differ=None):
        self.f, self.pieces = f, pieces
        era, bpo = f["era"], f["bpo"]
        P, BB, TB = era["pen_plus"], era["nb_byes_are_byes"], era["to_bowler"]
        m = self.m = cp.CpModel()
        lt = f["legal_total"]
        NO = max([sg["over"] for p in pieces for sg in p["segs"]] + [0]) + 1
        self.NO = NO
        Lo = [max(0, min(bpo, lt - o * bpo)) for o in range(NO)]
        bowlers = self.bowlers = [b["pid"] for b in f["bowlers"]]
        NBw = len(bowlers)
        wix = {b: i for i, b in enumerate(bowlers)}
        given = {ov["over"]: ov for ov in (f.get("overs") or [])} if not f.get("overs_planned") else {}
        own = self.own = _over_owner(pieces)
        split = {o for o, ps_ in own.items() if len(ps_) > 1}
        pen = []
        ex = f.get("extras") or {}
        # ---- who bowled each over
        self.x = None
        if given:
            bo = {}
            for o in range(NO):
                g = given.get(o)
                if g is None or g.get("bowler") not in wix:
                    raise ValueError(f"over {o + 1}: bowler not given")
                bo[o] = wix[g["bowler"]]
            X = lambda o, b: 1 if bo[o] == b else 0
        else:
            x = self.x = [[m.NewBoolVar(f"x{o}_{b}") for b in range(NBw)] for o in range(NO)]
            for o in range(NO):
                m.AddExactlyOne(x[o])
                if o + 1 < NO:
                    for b in range(NBw):
                        m.AddBoolOr([x[o][b].Not(), x[o + 1][b].Not()])
                if Lo[o] == 0:                                       # an over with no legal ball (chase ends on a wide)
                    pass
            X = lambda o, b: x[o][b]
            for b, fb in enumerate(f["bowlers"]):
                if fb.get("balls") is not None:
                    m.Add(sum(Lo[o] * x[o][b] for o in range(NO)) == fb["balls"])
            for o in range(NO - 2):                                  # bowlers bowl in spells
                for b in range(NBw):
                    sp = m.NewBoolVar("sp")
                    m.AddImplication(sp, x[o][b]); m.AddImplication(sp, x[o + 2][b])
                    pen.append((-2, sp))
            plan_hint = {p_["over"]: p_ for p_ in (f.get("overs") or [])} if f.get("overs_planned") else {}
            for o, h in plan_hint.items():
                if o < NO and h.get("bowler") in wix:
                    pen.append((1, 1 - x[o][wix[h["bowler"]]]))
        self.X = X
        # ---- per partnership (batters' 4s / 6s, extras) and per segment = partnership x over (bowler side)
        Pv, SG = self.Pv, self.SG = [], self.SG if hasattr(self, "SG") else {}
        self.Pv, self.SG = Pv, SG
        for i, p in enumerate(pieces):
            nP = sum(sg["n"] for sg in p["segs"])
            NBp = p["balls"] - nP                                    # balls faced = legal balls + no-balls
            if NBp < 0:
                raise ValueError(f"partnership {i + 1}: fewer balls faced than legal balls")
            A, B = p["A"], p["B"]
            RA, RB = p["runs_by"].get(A, 0), p["runs_by"].get(B, 0)
            BA, BBf = p["balls_by"].get(A, 0), p["balls_by"].get(B, 0)
            BR = RA + RB
            pv = {k: m.NewIntVar(0, 60, k) for k in PK}
            for a, R_, B_ in (("A", RA, BA), ("B", RB, BBf)):
                F_, X_ = pv["F" + a], pv["X" + a]
                m.Add(4 * F_ + 6 * X_ <= R_); m.Add(F_ + X_ <= B_)
                m.Add(R_ - 4 * F_ - 6 * X_ <= 5 * (B_ - F_ - X_))
            Pv.append(pv)
            cells = []
            for j, sg in enumerate(p["segs"]):
                n = sg["n"]
                v = {k: m.NewIntVar(0, 6 * (n + 6) + 30, k) for k in SK}
                m.Add(v["WD"] <= 6); m.Add(v["NB"] <= 6); m.Add(v["DOT"] <= n)
                if sg["trailing"]:
                    m.Add(v["WD"] + v["NB"] <= (0 if p["how"] in LEGAL_ONLY else 1))
                    for k in ("F4", "X6", "EB", "ELB"):
                        m.Add(v[k] == 0)
                bat = v["CON"] - ((v["EW"] + v["ENB"]) if TB else 0)
                m.Add(bat >= 0)
                m.Add(v["TR"] == bat + v["EW"] + v["ENB"] + v["EB"] + v["ELB"])
                nd = n - v["DOT"]
                fx = v["F4"] + v["X6"]
                m.Add(fx <= nd + v["NB"])
                m.Add(bat >= 4 * v["F4"] + 6 * v["X6"] + nd - fx)
                m.Add(bat <= 4 * v["F4"] + 6 * v["X6"] + 5 * (nd + v["NB"] - fx))
                m.Add(v["EW"] >= v["WD"]); m.Add(v["EW"] <= 5 * v["WD"])
                if P and BB:
                    m.Add(v["ENB"] == v["NB"])
                else:
                    m.Add(v["ENB"] >= (v["NB"] if P else 0)); m.Add(v["ENB"] <= 5 * v["NB"])
                m.Add(v["EB"] + v["ELB"] <= 4 * (n + (v["NB"] if BB else 0)))
                SG[(i, j)] = v
                cells.append(v)
            # the partnership, exactly
            m.Add(sum(v["TR"] for v in cells) == p["runs"])
            m.Add(sum(v["CON"] - ((v["EW"] + v["ENB"]) if TB else 0) for v in cells) == BR)
            m.Add(sum(v["NB"] for v in cells) == NBp)
            m.Add(sum(v["F4"] for v in cells) == pv["FA"] + pv["FB"])
            m.Add(sum(v["X6"] for v in cells) == pv["XA"] + pv["XB"])
        # ---- overs: runs (level 2+), maidens
        by_over = collections.defaultdict(list)
        for (i, j), v in SG.items():
            by_over[pieces[i]["segs"][j]["over"]].append(v)
        omaid = {}
        for o, cells in by_over.items():
            if given and given[o].get("runs") is not None:
                m.Add(sum(v["TR"] for v in cells) == given[o]["runs"])
            if Lo[o] == bpo:
                z = m.NewBoolVar("mz")
                tot = sum(v["CON"] for v in cells)
                m.Add(tot == 0).OnlyEnforceIf(z); m.Add(tot >= 1).OnlyEnforceIf(z.Not())
                omaid[o] = z
        # ---- the bowlers' figures, exactly
        for b, fb in enumerate(f["bowlers"]):
            for key, fk in (("CON", "runs"), ("DOT", "dots"), ("F4", "4"), ("X6", "6"), ("WD", "wd"), ("NB", "nb")):
                if fb.get(fk) is None:
                    continue
                parts = []
                for o, cells in by_over.items():
                    if given:
                        if X(o, b):
                            parts += [v[key] for v in cells]
                    else:
                        for v in cells:
                            y = m.NewIntVar(0, 200, "yb")
                            m.Add(y == v[key]).OnlyEnforceIf(x[o][b]); m.Add(y == 0).OnlyEnforceIf(x[o][b].Not())
                            parts.append(y)
                m.Add(sum(parts) == fb[fk]) if parts else m.Add(0 == fb[fk])
            if fb.get("maidens") is not None:
                mz = []
                for o, z in omaid.items():
                    if given:
                        if X(o, b):
                            mz.append(z)
                    else:
                        q = m.NewBoolVar("mq")
                        m.AddImplication(q, z); m.AddImplication(q, x[o][b]); m.AddBoolOr([q, z.Not(), x[o][b].Not()])
                        mz.append(q)
                m.Add(sum(mz) == fb["maidens"])
        # the bowler named in "b X" bowled the over the wicket fell in
        for i, p in enumerate(pieces):
            w = p["wicket"]
            if w and p["how"] in F.CREDITED and w.get("bowler") in wix and not given:
                sg = [s_ for s_ in p["segs"] if not s_["trailing"]][-1]
                m.Add(x[sg["over"]][wix[w["bowler"]]] == 1)
        # ---- batters' 4s and 6s, extras by type, exactly
        f4, f6 = collections.defaultdict(list), collections.defaultdict(list)
        for i, p in enumerate(pieces):
            f4[p["A"]].append(Pv[i]["FA"]); f4[p["B"]].append(Pv[i]["FB"])
            f6[p["A"]].append(Pv[i]["XA"]); f6[p["B"]].append(Pv[i]["XB"])
        for fb in f["batters"]:
            if fb["pid"] in f4:
                if fb.get("4") is not None:
                    m.Add(sum(f4[fb["pid"]]) == fb["4"])
                if fb.get("6") is not None:
                    m.Add(sum(f6[fb["pid"]]) == fb["6"])
        for key, nm in (("wd", "EW"), ("nb", "ENB"), ("b", "EB"), ("lb", "ELB")):
            if ex.get(key) is not None:
                m.Add(sum(v[nm] for v in SG.values()) == ex[key])
        # ---- level 3: each four and six in its over, by its batter (split between partnerships if needed)
        ev = _events(f)
        self.E = {}
        if ev is not None:
            for i, p in enumerate(pieces):
                ovs = sorted({sg["over"] for sg in p["segs"]})
                for o in ovs:
                    for a, pid in (("A", p["A"]), ("B", p["B"])):
                        for k in (4, 6):
                            self.E[(i, o, pid, k)] = m.NewIntVar(0, 6, "E")
                for a, pid in (("A", p["A"]), ("B", p["B"])):
                    m.Add(sum(self.E[(i, o, pid, 4)] for o in ovs) == Pv[i]["F" + a])
                    m.Add(sum(self.E[(i, o, pid, 6)] for o in ovs) == Pv[i]["X" + a])
                for j, sg in enumerate(p["segs"]):
                    m.Add(SG[(i, j)]["F4"] == self._ev_over(i, sg["over"], p, 4))
                    m.Add(SG[(i, j)]["X6"] == self._ev_over(i, sg["over"], p, 6))
            cells = collections.defaultdict(list)
            for (i, o, pid, k), var in self.E.items():
                cells[(o, pid, k)].append(var)
            for key, vs in cells.items():
                m.Add(sum(vs) == ev.get(key, 0))
        # ---- the natural plan: rates and extras spread in proportion to balls
        tot = {fb["pid"]: fb for fb in f["batters"]}
        for i, p in enumerate(pieces):
            nP = max(1, sum(sg["n"] for sg in p["segs"]))
            for a, pid in (("A", p["A"]), ("B", p["B"])):
                Bp = p["balls_by"].get(pid, 0)
                Bt = max(1, tot.get(pid, {}).get("B") or 1)
                for k, fk in (("F", "4"), ("X", "6")):
                    pen.append((2, _absdev(m, Pv[i][k + a] * Bt - (tot.get(pid, {}).get(fk) or 0) * Bp, 10 ** 6, "db")))
            for j, sg in enumerate(p["segs"]):                      # extras and dots spread evenly
                v = SG[(i, j)]
                for k, key in (("EW", "wd"), ("ENB", "nb"), ("EB", "b"), ("ELB", "lb")):
                    pen.append((1, _absdev(m, v[k] * max(1, lt) - (ex.get(key) or 0) * sg["n"], 10 ** 6, "de")))
        for (i, vals) in nogoods:
            if i == "SW":                                            # a placement of wides / no-balls that failed
                lits, keys = [], [(pi, j) for pi in range(len(pieces)) for j in range(len(pieces[pi]["segs"]))]
                for (pi, j), val in zip(keys, vals):
                    for k in ("WD", "NB"):
                        up, dn = m.NewBoolVar("up"), m.NewBoolVar("dn")
                        m.Add(SG[(pi, j)][k] >= val[k] + 1).OnlyEnforceIf(up); m.Add(SG[(pi, j)][k] <= val[k] - 1).OnlyEnforceIf(dn)
                        lits += [up, dn]
                m.AddBoolOr(lits)
            else:
                m.AddBoolOr(self._differs(i, vals))
        if differ is not None:
            m.AddBoolOr(self._differs(*differ))
        if pen:
            m.Minimize(sum(w * t for w, t in pen))

    def _ev_over(self, i, o, p, k):
        return self.E[(i, o, p["A"], k)] + self.E[(i, o, p["B"], k)]

    def _vars_of(self, i):
        vs = [self.Pv[i][k] for k in PK]
        for j in range(len(self.pieces[i]["segs"])):
            vs += [self.SG[(i, j)][k] for k in SK]
        for key in sorted(k for k in self.E if k[0] == i):
            vs.append(self.E[key])
        return vs

    def _differs(self, i, vals):
        m, lits = self.m, []
        for var, val in zip(self._vars_of(i), vals["v"]):
            up, dn = m.NewBoolVar("up"), m.NewBoolVar("dn")
            m.Add(var >= val + 1).OnlyEnforceIf(up); m.Add(var <= val - 1).OnlyEnforceIf(dn)
            lits += [up, dn]
        if self.x is not None:
            for o, b in vals["bowlers"].items():
                lits.append(self.x[o][b].Not())
        return lits

    def solve(self, seconds, workers):
        s = _solver(seconds, workers)
        st = s.StatusName(s.Solve(self.m))
        self.status = st
        if st not in ("OPTIMAL", "FEASIBLE"):
            return None
        val = lambda e: e if isinstance(e, int) else s.Value(e)
        if self.x is not None:
            self.bowler_of = {o: self.bowlers[next(b for b in range(len(self.bowlers)) if s.BooleanValue(self.x[o][b]))]
                              for o in range(self.NO)}
        else:
            self.bowler_of = {o: self.bowlers[next(b for b in range(len(self.bowlers)) if self.X(o, b))] for o in range(self.NO)}
        plans = []
        for i, p in enumerate(self.pieces):
            pl = {"piece": {k: val(self.Pv[i][k]) for k in PK},
                  "seg": [{k: val(self.SG[(i, j)][k]) for k in SK} for j in range(len(p["segs"]))],
                  "ev": {(o, pid, k): val(var) for (j, o, pid, k), var in self.E.items() if j == i},
                  "v": [val(e) for e in self._vars_of(i)], "bowlers": {}}
            if self.x is not None:
                for sg in p["segs"]:
                    o = sg["over"]
                    pl["bowlers"][o] = next(b for b in range(len(self.bowlers)) if s.BooleanValue(self.x[o][b]))
            plans.append(pl)
        return plans


# ------------------------------------------------------------------------------------------- step 2: a piece
class Piece:
    """One partnership, ball by ball, built into the joint model. Every over of the partnership has its legal
    balls plus a few spare slots for wides / no-balls (unused spares are switched off), so where the extras fall
    is decided by the solver together with everything else."""

    def __init__(self, f, p, bowler_of, prior, model, spare):
        era, bpo = f["era"], f["bpo"]
        P, BB, TB = era["pen_plus"], era["nb_byes_are_byes"], era["to_bowler"]
        m = self.m = model
        self.p, self.bowler_of = p, bowler_of
        given = {ov["over"]: ov for ov in (f.get("overs") or [])} if not f.get("overs_planned") else {}
        segof = []
        for j, sg in enumerate(p["segs"]):
            o = sg["over"]
            if sg["trailing"]:
                k = 0 if p["how"] in LEGAL_ONLY else 1
            else:
                b = given.get(o, {}).get("bowler")
                k = spare.get(b, spare["*"])                         # never more than his wides + no-balls in all
                r = given.get(o, {}).get("runs")
                if P and r is not None:
                    k = min(k, r)                                    # every wide / no-ball costs at least 1 run
                k = min(k, spare["*"])
            segof += [j] * (sg["n"] + k)
        self.segof = segof
        D = self.D = len(segof)
        overof = self.overof = [p["segs"][j]["over"] for j in segof]
        self.ovs = sorted(set(overof))
        S = self.S = []
        for i in range(D):
            d = {}
            d["L"], d["W"], d["N"] = m.NewBoolVar("L"), m.NewBoolVar("W"), m.NewBoolVar("N")
            d["act"] = m.NewBoolVar("act")                          # an unused spare slot has act = 0
            m.Add(d["L"] + d["W"] + d["N"] == d["act"])
            d["v"] = [m.NewBoolVar(f"v{x}") for x in range(7)]
            m.AddExactlyOne(d["v"])
            d["bat"] = sum(x * d["v"][x] for x in range(7))
            m.AddImplication(d["W"], d["v"][0])
            m.AddImplication(d["act"].Not(), d["v"][0])
            d["rx"] = m.NewIntVar(0, 4, "rx")
            d["rxp"], d["rx4"], d["LB"] = m.NewBoolVar("rxp"), m.NewBoolVar("rx4"), m.NewBoolVar("lb")
            m.Add(d["rx"] >= 1).OnlyEnforceIf(d["rxp"]); m.Add(d["rx"] == 0).OnlyEnforceIf(d["rxp"].Not())
            m.Add(d["rx"] == 4).OnlyEnforceIf(d["rx4"]); m.Add(d["rx"] <= 3).OnlyEnforceIf(d["rx4"].Not())
            m.AddImplication(d["LB"], d["rxp"])
            m.Add(d["rx"] == 0).OnlyEnforceIf(d["v"][0].Not())
            if BB:
                m.AddBoolOr([d["L"], d["N"], d["LB"].Not()])
            else:
                m.AddImplication(d["LB"], d["L"])
            for nm in ("exw", "exnb", "exb", "exlb"):
                d[nm] = m.NewIntVar(0, 7, nm)
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
                carrier = m.NewBoolVar("car")
                m.AddBoolOr([d["L"], d["N"], carrier.Not()]); m.AddImplication(d["L"], carrier); m.AddImplication(d["N"], carrier)
            qb, ql = m.NewBoolVar("qb"), m.NewBoolVar("ql")
            for q, lits in ((qb, [carrier, d["rxp"], d["LB"].Not()]), (ql, [carrier, d["LB"]])):
                for l in lits:
                    m.AddImplication(q, l)
                m.AddBoolOr([q] + [l.Not() for l in lits])
            m.Add(d["exb"] == d["rx"]).OnlyEnforceIf(qb); m.Add(d["exb"] == 0).OnlyEnforceIf(qb.Not())
            m.Add(d["exlb"] == d["rx"]).OnlyEnforceIf(ql); m.Add(d["exlb"] == 0).OnlyEnforceIf(ql.Not())
            d["conc"] = d["bat"] + ((d["exw"] + d["exnb"]) if TB else 0)
            d["tr"] = d["bat"] + d["exw"] + d["exnb"] + d["exb"] + d["exlb"]
            d["odd"] = m.NewBoolVar("odd")
            run = m.NewBoolVar("run")
            for l in (d["v"][4], d["v"][6], d["rx4"]):
                m.AddImplication(run, l.Not())
            m.AddBoolOr([run, d["v"][4], d["v"][6], d["rx4"]])
            h = m.NewIntVar(0, 3, "h")
            m.Add(d["bat"] + d["rx"] == 2 * h + d["odd"]).OnlyEnforceIf(run)
            m.AddImplication(run.Not(), d["odd"].Not())
            cz = m.NewBoolVar("cz")
            m.Add(d["conc"] == 0).OnlyEnforceIf(cz); m.Add(d["conc"] >= 1).OnlyEnforceIf(cz.Not())
            d["dot"] = m.NewBoolVar("dot")
            m.AddImplication(d["dot"], d["L"]); m.AddImplication(d["dot"], cz); m.AddBoolOr([d["dot"], d["L"].Not(), cz.Not()])
            S.append(d)
        idx = collections.defaultdict(list)
        for i, j in enumerate(segof):
            idx[j].append(i)
        self.idx = idx
        eov = [0] * D
        ball_no = {}
        for j, sg in enumerate(p["segs"]):
            ii = idx[j]
            if not ii:
                continue
            m.Add(sum(S[i]["L"] for i in ii) == sg["n"])
            for a_, b_ in zip(ii, ii[1:]):                           # spares (switched off) only at the end
                m.AddImplication(S[b_]["act"], S[a_]["act"])
            if sg["ends_over"]:                                      # the over ends on its last delivery = legal ball
                for k_, i in enumerate(ii):
                    nxt = S[ii[k_ + 1]]["act"] if k_ + 1 < len(ii) else None
                    e = m.NewBoolVar("eov")
                    if nxt is None:
                        m.Add(e == S[i]["act"])
                    else:
                        m.AddImplication(e, S[i]["act"]); m.AddImplication(e, nxt.Not())
                        m.AddBoolOr([e, S[i]["act"].Not(), nxt])
                    m.AddImplication(e, S[i]["L"])
                    eov[i] = e
            cl = 0
            for i in ii:
                cl = cl + S[i]["L"]
                if sg["n"]:
                    ball_no[i] = (sg["first"] - 1) + cl              # legal ball number within the over
        self.eov, self.ball_no = eov, ball_no
        nP = sum(sg["n"] for sg in p["segs"])
        m.Add(sum(d["N"] for d in S) == p["balls"] - nP)            # balls faced = legal balls + no-balls
        in_over = collections.defaultdict(list)
        for i, o in enumerate(overof):
            in_over[o].append(i)
        self.in_over = in_over
        # strike: A on strike before delivery i; passes on odd runs and at the end of an over
        sA = self.sA = [m.NewBoolVar(f"sA{i}") for i in range(D + 1)]
        for i in range(D):
            h2 = m.NewIntVar(0, 1, "h2")
            m.Add(sA[i] + S[i]["odd"] + eov[i] - sA[i + 1] == 2 * h2)
        # batters: runs and balls (scorecard)
        on = {}
        self.f4s, self.f6s = {}, {}
        for a, isA, pid in (("A", 1, p["A"]), ("B", 0, p["B"])):
            rr, bb, f4, f6 = [], [], [], []
            for i in range(D):
                o_ = sA[i] if isA else sA[i].Not()
                on[(i, a)] = o_
                y = m.NewIntVar(0, 6, "br")
                m.Add(y == S[i]["bat"]).OnlyEnforceIf(o_); m.Add(y == 0).OnlyEnforceIf(o_.Not())
                rr.append(y)
                bb.append(self._and(o_, S[i]["L"]) + self._and(o_, S[i]["N"]))
                f4.append(self._and(o_, S[i]["v"][4])); f6.append(self._and(o_, S[i]["v"][6]))
            m.Add(sum(rr) == p["runs_by"].get(pid, 0)); m.Add(sum(bb) == p["balls_by"].get(pid, 0))
            self.f4s[pid], self.f6s[pid] = f4, f6
        m.Add(sum(d["tr"] for d in S) == p["runs"])
        self.on = on
        # level 3b: the exact ball of each four and six
        for an in f.get("anchors") or []:
            ii = [i for i in in_over.get(an["over"], []) if i in ball_no]
            pos = an["over"] * bpo + an["ball"]
            if not ii or not (p["start"] < pos <= p["end"]):
                continue
            hit = []
            for i in ii:
                z = m.NewBoolVar("an")
                m.Add(ball_no[i] == an["ball"]).OnlyEnforceIf(z)
                m.AddImplication(z, S[i]["L"]); m.AddImplication(z, S[i]["v"][an["bat"]])
                if an.get("striker") in (p["A"], p["B"]):
                    m.Add(sA[i] == (1 if an["striker"] == p["A"] else 0)).OnlyEnforceIf(z)
                hit.append(z)
            m.Add(sum(hit) == 1)
        # the last delivery of the partnership (the wicket ball, or the end of the innings)
        suf = [None] * (D + 1)
        suf[D] = 0
        for i in range(D - 1, -1, -1):
            z = m.NewBoolVar("suf")
            if isinstance(suf[i + 1], int):
                m.Add(z == S[i]["act"])
            else:
                m.AddImplication(S[i]["act"], z); m.AddImplication(suf[i + 1], z)
                m.AddBoolOr([z.Not(), S[i]["act"], suf[i + 1]])
            suf[i] = z
        plast = []
        for i in range(D):
            z = m.NewBoolVar("plast")
            if isinstance(suf[i + 1], int):
                m.Add(z == S[i]["act"])
            else:
                m.AddImplication(z, S[i]["act"]); m.AddImplication(z, suf[i + 1].Not())
                m.AddBoolOr([z, S[i]["act"].Not(), suf[i + 1]])
            plast.append(z)
        if D:
            m.Add(sum(plast) == 1)
        self.plast = plast
        w = p["wicket"]
        self.inc_on_strike = None
        if w and D:
            how = p["how"]
            inc = self.inc_on_strike = m.NewBoolVar("inc")
            free = m.NewBoolVar("free")                              # new batter at the striker's end?
            if how in STRIKER_ONLY and (how not in ("caught", "caught and bowled") or era["caught_new_on_strike"]):
                m.Add(free == 1)
            for i in range(D):
                d, z = S[i], plast[i]
                if how in LEGAL_ONLY:
                    m.AddImplication(z, d["L"]); m.AddImplication(z, d["v"][0]); m.Add(d["rx"] == 0).OnlyEnforceIf(z)
                elif how in ("stumped", "hit wicket"):
                    m.AddImplication(z, d["N"].Not()); m.AddImplication(z, d["v"][0]); m.Add(d["rx"] == 0).OnlyEnforceIf(z)
                elif how in ("handled the ball", "hit the ball twice"):
                    m.AddImplication(z, d["W"].Not()); m.AddImplication(z, d["v"][0])
                else:
                    m.AddImplication(z, d["v"][4].Not()); m.AddImplication(z, d["v"][6].Not()); m.AddImplication(z, d["rx4"].Not())
                if how in STRIKER_ONLY:
                    m.Add(sA[i] == (1 if w["out"] == p["A"] else 0)).OnlyEnforceIf(z)
                if how in F.CREDITED and w.get("bowler") is not None and bowler_of is not None \
                        and bowler_of.get(overof[i]) != w["bowler"]:
                    m.Add(z == 0)
                h3 = m.NewIntVar(0, 1, "h3")
                m.Add(free + eov[i] - inc == 2 * h3).OnlyEnforceIf(z)   # ends change if the over ended
        # every legal ball by its number: e[(over, ball)] -> delivery indicators (for the proven check)
        self.ball_ind = collections.defaultdict(list)
        for i, bn in ball_no.items():
            o = overof[i]
            sg = p["segs"][segof[i]]
            for q in range(sg["first"], sg["first"] + sg["n"]):
                z = m.NewBoolVar("bi")
                m.Add(bn == q).OnlyEnforceIf(z); m.Add(bn != q).OnlyEnforceIf(z.Not())
                z2 = self._and(z, S[i]["L"])
                self.ball_ind[(o, q)].append((i, z2))
        # most likely values: run rates by phase (learned from real balls); byes / leg-byes and extras rare
        like = []
        for i in range(D):
            o = overof[i]
            ph = 0 if o < 10 else (2 if o >= 40 else 1)
            pr = prior.get(ph) or prior.get(str(ph)) or {}
            p0 = pr.get(0, pr.get("0", 0.5))
            for x in range(1, 7):
                px = pr.get(x, pr.get(str(x), 0.001))
                like.append(int(round(100 * (math.log(p0) - math.log(max(px, 1e-6))))) * S[i]["v"][x])
            like.append(150 * S[i]["rxp"])
        self.like = like

    def _and(self, a, b):
        z = self.m.NewBoolVar("and")
        self.m.AddImplication(z, a); self.m.AddImplication(z, b); self.m.AddBoolOr([z, a.Not(), b.Not()])
        return z

    def _and_all(self, lits):
        z = self.m.NewBoolVar("all")
        for l in lits:
            self.m.AddImplication(z, l)
        self.m.AddBoolOr([z] + [l.Not() for l in lits])
        return z

    def _read(self, V, Bo):
        p, out = self.p, []
        w = p["wicket"]
        lc = collections.Counter()
        for i in range(self.D):
            d = self.S[i]
            if not Bo(d["act"]):
                continue
            o = self.overof[i]
            kind = "wd" if Bo(d["W"]) else "nb" if Bo(d["N"]) else ""
            if not kind and V(d["rx"]) > 0:
                kind = "lb" if Bo(d["LB"]) else "b"
            st, ns = (p["A"], p["B"]) if Bo(self.sA[i]) else (p["B"], p["A"])
            is_w = bool(w) and Bo(self.plast[i])
            ball = V(self.ball_no[i]) if (i in self.ball_no and Bo(d["L"])) else None
            out.append({"over": o, "bowler": self.bowler_of[o], "striker": st, "non_striker": ns,
                        "bat": sum(x * Bo(d["v"][x]) for x in range(7)), "kind": kind, "ran": V(d["rx"]),
                        "ex_w": V(d["exw"]), "ex_nb": V(d["exnb"]), "ex_b": V(d["exb"]), "ex_lb": V(d["exlb"]),
                        "legal": Bo(d["L"]), "out": w["out"] if is_w else None, "how": p["how"] if is_w else None,
                        "label": "estimate", "slot": i, "ball": ball})
        self.inc_value = Bo(self.inc_on_strike) if self.inc_on_strike is not None else None
        return out


# ------------------------------------------------------------------------------------------- step 2: all pieces together
class Joint:
    """Every partnership's small ball-by-ball model in ONE solver run, joined only by what crosses partnerships:
    strike after each wicket, over runs, bowlers' figures, batters' 4s / 6s, extras. The over of every delivery
    is fixed by the plan, so the model is small and fixed-shape (nothing has to be searched for twice)."""

    def __init__(self, f, pieces, prior, bowler_hint=None):
        era, bpo = f["era"], f["bpo"]
        TB = era["to_bowler"]
        m = self.m = cp.CpModel()
        self.f, self.pieces = f, pieces
        given = {ov["over"]: ov for ov in (f.get("overs") or [])} if not f.get("overs_planned") else {}
        split = {o for o, q in _over_owner(pieces).items() if len(q) > 1}
        lt = f["legal_total"]
        NO = max([sg["over"] for p in pieces for sg in p["segs"]] + [0]) + 1
        Lo = [max(0, min(bpo, lt - o * bpo)) for o in range(NO)]
        bowlers = self.bowlers = [b["pid"] for b in f["bowlers"]]
        wix = {b: i for i, b in enumerate(bowlers)}
        bo_known = {o: given[o]["bowler"] for o in given} if given else None
        self.P = []
        like = []
        known = [b.get("wd") for b in f["bowlers"]] + [b.get("nb") for b in f["bowlers"]]
        ex0 = f.get("extras") or {}
        if all(k is not None for k in known):
            spare = {b["pid"]: b["wd"] + b["nb"] for b in f["bowlers"]}
            spare["*"] = max(spare.values()) if spare else 0         # level 1: the bowler isn't known yet
        else:                                                        # no per-bowler counts: each costs >= 1 run
            tot = (ex0.get("wd") or 0) + (ex0.get("nb") or 0) if era["pen_plus"] and ex0.get("wd") is not None \
                and ex0.get("nb") is not None else 12
            spare = {"*": tot}
        self.spare = spare
        for i, p in enumerate(pieces):
            pc = Piece(f, p, bo_known, prior, m, spare)
            self.P.append(pc)
            like += pc.like
        if all(k is not None for k in known):                        # total wides and no-balls (deliveries)
            m.Add(sum(d["W"] for pc in self.P for d in pc.S) == sum(b["wd"] for b in f["bowlers"]))
            m.Add(sum(d["N"] for pc in self.P for d in pc.S) == sum(b["nb"] for b in f["bowlers"]))
        # strike passes from one partnership to the next (survivor = A of the next piece)
        for i in range(len(pieces) - 1):
            a, b = self.P[i], self.P[i + 1]
            if a.inc_on_strike is not None and b.D:
                m.Add(b.sA[0] == 1 - a.inc_on_strike)
        # per over aggregates
        agg = collections.defaultdict(lambda: collections.defaultdict(list))
        for pc in self.P:
            for o, ii in pc.in_over.items():
                for i in ii:
                    d = pc.S[i]
                    for k, e in (("tr", d["tr"]), ("conc", d["conc"]), ("dot", d["dot"]), ("4", d["v"][4]),
                                 ("6", d["v"][6]), ("wd", d["W"]), ("nb", d["N"])):
                        agg[o][k].append(e)
        A = {o: {k: sum(v) for k, v in agg[o].items()} for o in agg}
        for o in A:
            if given and given.get(o, {}).get("runs") is not None:
                m.Add(A[o]["tr"] == given[o]["runs"])
        mzo = {}
        for o in A:
            if Lo[o] == bpo:
                z = m.NewBoolVar("mz")
                m.Add(A[o]["conc"] == 0).OnlyEnforceIf(z); m.Add(A[o]["conc"] >= 1).OnlyEnforceIf(z.Not())
                mzo[o] = z
        # who bowled each over
        self.x = None
        if given:
            X = lambda o, b: 1 if bo_known[o] == bowlers[b] else 0
        else:
            x = self.x = [[m.NewBoolVar(f"x{o}_{b}") for b in range(len(bowlers))] for o in range(NO)]
            for o in range(NO):
                m.AddExactlyOne(x[o])
                if o + 1 < NO:
                    for b in range(len(bowlers)):
                        m.AddBoolOr([x[o][b].Not(), x[o + 1][b].Not()])
            X = lambda o, b: x[o][b]
            for b, fb in enumerate(f["bowlers"]):
                if fb.get("balls") is not None:
                    m.Add(sum(Lo[o] * x[o][b] for o in range(NO)) == fb["balls"])
            for o in range(NO - 2):
                for b in range(len(bowlers)):
                    sp = m.NewBoolVar("sp")
                    m.AddImplication(sp, x[o][b]); m.AddImplication(sp, x[o + 2][b])
                    like.append(-60 * sp)                            # bowlers bowl in spells
            for i, p in enumerate(pieces):                           # "b X": X bowled the wicket ball
                w = p["wicket"]
                pc = self.P[i]
                if w and p["how"] in F.CREDITED and w.get("bowler") in wix and pc.D:
                    for i_ in range(pc.D):
                        m.AddImplication(pc.plast[i_], x[pc.overof[i_]][wix[w["bowler"]]])
            if bowler_hint:
                for o, b in bowler_hint.items():
                    m.AddHint(x[o][b], 1) if hasattr(m, "AddHint") else None
        self.X = X
        # bowlers' figures
        ub = {"conc": 80, "dot": bpo, "4": 12, "6": 12, "wd": 12, "nb": 12}
        for b, fb in enumerate(f["bowlers"]):
            for k, fk in (("conc", "runs"), ("dot", "dots"), ("4", "4"), ("6", "6"), ("wd", "wd"), ("nb", "nb")):
                if fb.get(fk) is None:
                    continue
                if given:
                    m.Add(sum(A[o][k] for o in A if X(o, b)) == fb[fk])
                else:
                    parts = []
                    for o in A:
                        y = m.NewIntVar(0, ub[k], "yb")
                        m.Add(y == A[o][k]).OnlyEnforceIf(x[o][b]); m.Add(y == 0).OnlyEnforceIf(x[o][b].Not())
                        parts.append(y)
                    m.Add(sum(parts) == fb[fk])
            if fb.get("maidens") is not None:
                mz = []
                for o, z in mzo.items():
                    if given:
                        if X(o, b):
                            mz.append(z)
                    else:
                        q = m.NewBoolVar("mq")
                        m.AddImplication(q, z); m.AddImplication(q, x[o][b]); m.AddBoolOr([q, z.Not(), x[o][b].Not()])
                        mz.append(q)
                m.Add(sum(mz) == fb["maidens"])
        # batters' 4s and 6s over all their partnerships
        f4, f6 = collections.defaultdict(list), collections.defaultdict(list)
        for pc in self.P:
            for pid, lst in pc.f4s.items():
                f4[pid] += lst
            for pid, lst in pc.f6s.items():
                f6[pid] += lst
        for fb in f["batters"]:
            if fb["pid"] in f4:
                if fb.get("4") is not None:
                    m.Add(sum(f4[fb["pid"]]) == fb["4"])
                if fb.get("6") is not None:
                    m.Add(sum(f6[fb["pid"]]) == fb["6"])
        # extras by type
        ex = f.get("extras") or {}
        for key, nm in (("wd", "exw"), ("nb", "exnb"), ("b", "exb"), ("lb", "exlb")):
            if ex.get(key) is not None:
                m.Add(sum(d[nm] for pc in self.P for d in pc.S) == ex[key])
        # level 3a: each four and six in its over, by its batter
        ev = _events(f)
        if ev is not None:
            cells = collections.defaultdict(list)
            for pc in self.P:
                for o, ii in pc.in_over.items():
                    for a, pid in (("A", pc.p["A"]), ("B", pc.p["B"])):
                        for k in (4, 6):
                            cells[(o, pid, k)] += [pc._and(pc.on[(i, a)], pc.S[i]["v"][k]) for i in ii]
            for key, vs in cells.items():
                m.Add(sum(vs) == ev.get(key, 0))
        self.like = like
        m.Minimize(sum(like))

    def solve(self, seconds, workers):
        s = _solver(seconds, workers)
        st = s.StatusName(s.Solve(self.m))
        self.status = st
        if st not in ("OPTIMAL", "FEASIBLE"):
            return None
        self.sol = s
        if self.x is not None:
            bo = {o: self.bowlers[next(b for b in range(len(self.bowlers)) if s.BooleanValue(self.x[o][b]))]
                  for o in range(len(self.x))}
        else:
            bo = {o: self.bowlers[next(b for b in range(len(self.bowlers)) if self.X(o, b))]
                  for o in range(max(pc_o for pc in self.P for pc_o in (pc.ovs or [0])) + 1)}
        self.bowler_of = bo
        out = []
        for pc in self.P:
            pc.bowler_of = bo
            out.append(pc._read(s.Value, s.BooleanValue))
        return out


def prove_all(jt, out, info, prove_seconds, prove_budget, workers, log=print):
    """Label every delivery proven / guessed: proven = no other version of it fits all the facts."""
    tp = time.time()
    try:
        jt.m.ClearObjective()
    except Exception:
        try:
            jt.m.Proto().ClearField("objective")
        except Exception:
            pass
    nproven, m = 0, jt.m
    def same_lit(pc, i, d):
        """delivery slot i looks exactly like d: runs, extras (type and runs), striker, wicket"""
        S_ = pc.S[i]
        lits = [S_["v"][min(d["bat"], 6)]]
        for nm, key in (("exw", "ex_w"), ("exnb", "ex_nb"), ("exb", "ex_b"), ("exlb", "ex_lb")):
            z = m.NewBoolVar("eq")
            m.Add(S_[nm] == d[key]).OnlyEnforceIf(z); m.Add(S_[nm] != d[key]).OnlyEnforceIf(z.Not())
            lits.append(z)
        lits.append(pc.sA[i] if d["striker"] == pc.p["A"] else pc.sA[i].Not())
        if pc.p["wicket"]:
            lits.append(pc.plast[i] if d["out"] else pc.plast[i].Not())
        return pc._and_all(lits)
    for pc, piece in zip(jt.P, out):
        for d in piece:
            if not d["legal"] or d["label"] != "estimate":
                continue
            if time.time() - tp > prove_budget:
                d["label"] = "unchecked"; continue
            alt = m.NewBoolVar("alt")                            # "something about this ball was different"
            for i, z in pc.ball_ind[(d["over"], d["ball"])]:
                m.AddBoolOr([alt.Not(), z.Not(), same_lit(pc, i, d).Not()])
            m.ClearAssumptions(); m.AddAssumptions([alt])
            s = _solver(prove_seconds, workers)
            st = s.StatusName(s.Solve(m))
            d["label"] = "proven" if st == "INFEASIBLE" else ("guessed" if st in ("OPTIMAL", "FEASIBLE") else "unchecked")
            nproven += d["label"] == "proven"
    # wides / no-balls: could the extras of this over have been placed or scored differently?
    overs_ex = collections.defaultdict(dict)                     # over -> {(piece no, slot): delivery}
    for k_, (pc, piece) in enumerate(zip(jt.P, out)):
        for d in piece:
            if not d["legal"]:
                overs_ex[d["over"]][(k_, d["slot"])] = d
    for o, cur in overs_ex.items():
        if time.time() - tp > prove_budget:
            for d in cur.values():
                d["label"] = "unchecked"
            continue
        diffs = []
        for k_, pc in enumerate(jt.P):
            for i in pc.in_over.get(o, []):
                d = cur.get((k_, i))
                if d is not None:                                # an extra here: is it still the same extra?
                    typ = pc.S[i]["W"] if d["kind"] == "wd" else pc.S[i]["N"]
                    diffs.append(pc._and(typ, same_lit(pc, i, d)).Not())
                else:                                            # no extra here: did one appear?
                    diffs += [pc.S[i]["W"], pc.S[i]["N"]]
        alt = m.NewBoolVar("alto")
        m.AddBoolOr([alt.Not()] + diffs)
        m.ClearAssumptions(); m.AddAssumptions([alt])
        s = _solver(prove_seconds, workers)
        st = s.StatusName(s.Solve(m))
        lab = "proven" if st == "INFEASIBLE" else ("guessed" if st in ("OPTIMAL", "FEASIBLE") else "unchecked")
        for d in cur.values():
            d["label"] = lab
            nproven += lab == "proven"
    jt.m.ClearAssumptions()
    info["proven"] = nproven
    info["prove_seconds"] = round(time.time() - tp)
    log(f"  proven/guessed checked ({info['prove_seconds']} s): {nproven} balls proven")


# ------------------------------------------------------------------------------------------- the whole innings
def solve_innings(f, prior, seconds=600, workers=8, prove=True, prove_seconds=2, prove_budget=900, log=print):
    """Returns (deliveries, info). Deliveries carry label 'proven' / 'guessed' ('real' = given as a fact)."""
    t0 = time.time()
    pieces = make_pieces(f)
    jt = Joint(f, pieces, prior)
    log(f"  {len(pieces)} partnerships, {sum(pc.D for pc in jt.P)} delivery slots; solving all together...")
    out = jt.solve(seconds, workers)
    info = {"status": jt.status, "seconds": round(time.time() - t0), "pieces": len(pieces)}
    if out is None:
        return None, info
    dl = [d for piece in out for d in piece]
    log(f"  built ball by ball ({jt.status}, {info['seconds']} s)")
    anch = {(a["over"], a["ball"]) for a in f.get("anchors") or []}
    for d in dl:
        if d["legal"] and (d["over"], d["ball"]) in anch:
            d["label"] = "real"
    if prove and BACKEND == "ortools":
        prove_all(jt, out, info, prove_seconds, prove_budget, workers, log)
    for d in dl:
        if d["label"] == "estimate":
            d["label"] = "guessed"
    info["seconds"] = round(time.time() - t0)
    return dl, info
