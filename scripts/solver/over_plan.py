"""
Step 1 of a level-1 rebuild (scorecard only): plan the OVERS before the balls.

A small, fast puzzle decides who bowled each over and how many runs came off it, so that everything on the
scorecard adds up at over level:
  - one bowler per over, never two overs in a row by the same bowler
  - each bowler's balls (overs), runs conceded, wickets, maidens = his figures
  - the bowler who took a wicket bowled the over in which it fell (fall of wickets gives the ball: 6.1 -> over 7)
  - the score at each fall of wicket fits the runs of the overs before / up to that ball
  - team total, byes + leg-byes
Between answers that fit equally well it prefers what real matches look like: spells (the same bowler every
other over), and over runs near the innings' own average for that phase (slower start, faster end).
The plan is GUIDANCE for step 2 (the ball-by-ball solve), never locked: if the balls can't fit it, step 2 moves.
"""
import collections

try:
    from ortools.sat.python import cp_model as cp
except ImportError:
    import cpshim as cp

BIG = 1000


def plan_overs(facts, seconds=60, workers=8):
    bpo, lt = facts["bpo"], facts["legal_total"]
    NO = max(1, -(-lt // bpo))
    L = [bpo] * (NO - 1) + [lt - bpo * (NO - 1)]
    bowlers = facts["bowlers"]
    NBw = len(bowlers)
    wix = {b["pid"]: i for i, b in enumerate(bowlers)}
    m = cp.CpModel()
    pen = []                                                    # (weight, var) to minimise

    def soft(expr, value, w=BIG, ub=500):
        if value is None:
            return
        s = m.NewIntVar(0, ub, "s")
        m.Add(expr - value <= s); m.Add(value - expr <= s)
        pen.append((w, s))

    x = [[m.NewBoolVar(f"x{o}_{b}") for b in range(NBw)] for o in range(NO)]
    for o in range(NO):
        m.AddExactlyOne(x[o])
        if o + 1 < NO:
            for b in range(NBw):
                m.AddBoolOr([x[o][b].Not(), x[o + 1][b].Not()])
    ex = facts.get("extras") or {}
    R = [m.NewIntVar(0, 40, f"R{o}") for o in range(NO)]        # team runs in the over
    BL = [m.NewIntVar(0, 12, f"BL{o}") for o in range(NO)]      # byes + leg-byes in the over (not the bowler's)
    for o in range(NO):
        m.Add(BL[o] <= R[o])
    if ex.get("b") is not None and ex.get("lb") is not None:
        soft(sum(BL), ex["b"] + ex["lb"])
    soft(sum(R) + (facts.get("pen") or 0), facts["total"])
    C = [R[o] - BL[o] for o in range(NO)]                       # conceded by the bowler
    Cb = [[m.NewIntVar(0, 40, f"C{o}_{b}") for b in range(NBw)] for o in range(NO)]
    for o in range(NO):
        for b in range(NBw):
            m.Add(Cb[o][b] == C[o]).OnlyEnforceIf(x[o][b])
            m.Add(Cb[o][b] == 0).OnlyEnforceIf(x[o][b].Not())
    zero = []
    for o in range(NO):
        z = m.NewBoolVar(f"z{o}")
        m.Add(C[o] == 0).OnlyEnforceIf(z); m.Add(C[o] >= 1).OnlyEnforceIf(z.Not())
        zero.append(z)
    for b, fb in enumerate(bowlers):
        soft(sum(L[o] * x[o][b] for o in range(NO)), fb.get("balls"))
        soft(sum(Cb[o][b] for o in range(NO)), fb.get("runs"))
        if fb.get("maidens") is not None:
            mz = []
            for o in range(NO):
                if L[o] == bpo:
                    q = m.NewBoolVar(f"mz{o}_{b}")
                    m.AddImplication(q, x[o][b]); m.AddImplication(q, zero[o])
                    m.AddBoolOr([q, x[o][b].Not(), zero[o].Not()])
                    mz.append(q)
            soft(sum(mz) if mz else 0, fb["maidens"])
    # wickets: the over each fell in (from the ball count at the fall), its bowler, and the score at the fall
    for w in facts.get("fow") or []:
        lg = w.get("legal")
        if lg is None:
            continue
        ow = min(NO - 1, max(0, (lg - 1) // bpo))
        if w.get("bowler") in wix:
            ok = m.NewBoolVar("wk")
            m.AddImplication(ok, x[ow][wix[w["bowler"]]])
            pen.append((BIG, 1 - ok))
        if w.get("score") is not None:
            before = sum(R[o] for o in range(ow)) if ow else 0
            upto = before + R[ow]
            s1 = m.NewIntVar(0, 500, "f1"); s2 = m.NewIntVar(0, 500, "f2")
            m.Add(before - w["score"] <= s1)                     # runs before the over <= score at the fall
            if lg % bpo == 0 or ow == NO - 1:
                m.Add(upto - w["score"] <= s2); m.Add(w["score"] - upto <= s2)   # fell on the last ball of the over
            else:
                m.Add(w["score"] - upto <= s2)                   # score at the fall <= runs up to the end of the over
            pen += [(BIG, s1), (BIG, s2)]
    # what real innings look like (tie-breaks only, far below any fact)
    total = facts["total"] or 0
    avg = total / max(1, NO)
    for o in range(NO):
        ph = 0.85 if o < 10 else (1.35 if o >= NO - 10 and NO >= 40 else 0.95)
        e = int(round(avg * ph * L[o] / bpo))
        d = m.NewIntVar(0, 40, f"d{o}")
        m.Add(R[o] - e <= d); m.Add(e - R[o] <= d)
        pen.append((2, d))
        pen.append((3, BL[o]))
    for o in range(NO - 2):
        for b in range(NBw):
            sp = m.NewBoolVar(f"sp{o}_{b}")                      # same bowler two overs later = a spell
            m.AddImplication(sp, x[o][b]); m.AddImplication(sp, x[o + 2][b])
            pen.append((-4, sp))
    m.Minimize(sum(w * v for w, v in pen))
    sol = cp.CpSolver()
    sol.parameters.max_time_in_seconds = float(seconds)
    try:
        sol.parameters.num_workers = workers
    except Exception:
        pass
    st = sol.StatusName(sol.Solve(m))
    if st not in ("OPTIMAL", "FEASIBLE"):
        return None, st
    plan = []
    for o in range(NO):
        b = next(b for b in range(NBw) if sol.BooleanValue(x[o][b]))
        plan.append({"over": o, "bowler": bowlers[b]["pid"], "runs": sol.Value(R[o])})
    return plan, st


def plan_accuracy(plan, truth):
    """How close the plan is to the real overs (for the report)."""
    real = collections.OrderedDict()
    for d in truth:
        r = real.setdefault(d["over"], {"bowler": d["bowler"], "runs": 0})
        r["runs"] += d["bat"] + d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"]
    n = len(real)
    pb = {p["over"]: p for p in plan}
    return {"overs": n, "bowler_right": sum(pb.get(o, {}).get("bowler") == r["bowler"] for o, r in real.items()),
            "runs_right": sum(pb.get(o, {}).get("runs") == r["runs"] for o, r in real.items())}
