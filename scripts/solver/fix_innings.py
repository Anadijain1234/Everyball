"""
Fix ball-by-ball with the layered solver (Everyball, 18 Nov 2026). Two jobs:

  who_faced  innings whose balls add up but give runs to the wrong batter (balls_fix 'attribution_unsure', 727 at the
             13 Nov build): keep every recorded ball as close as possible (each over's deliveries and their order are a
             strong preference), move strike so every batter's runs / balls / 4s / 6s match the scorecard (and each
             partnership's split where ESPN's adds up).
  gaps       innings with missing overs (partial ball-by-ball): every COMPLETE recorded over is kept exactly (who faced
             what, in order); only the missing overs are solved, from the scorecard, ESPN's fall of wickets (exact
             ball) and partnerships. Rebuilt balls are labelled 'rebuilt'.

  python3 scripts/solver/fix_innings.py test   --db data/everyball.db          # known-answer tests (2011 WC final)
  python3 scripts/solver/fix_innings.py who_faced --db data/everyball.db [--limit N] [--time 120]
  python3 scripts/solver/fix_innings.py gaps      --db data/everyball.db [--limit N] [--time 300]

Output: table balls_rebuilt (match_id, inn, seq, over_no, ball, bowler_id, batter_id, non_striker_id, bat_runs, extras,
extra_type, legal, wicket, kind, out_id, label, job) + data/reports/fix_<job>.csv (one line per innings: status,
seconds, balls whose striker changed, balls whose runs changed, rule problems). Every answer is replayed against all
facts and the Laws (cricket_facts.replay) and only kept with 0 problems.
"""
import re
import argparse, collections, csv, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cricket_facts as F, db_load as L, layered_solver as LS   # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


# ------------------------------------------------------------------------------------------- facts
def card(c, mid, inn):
    bat = {p: (r, b, f4, f6, pos) for p, r, b, f4, f6, pos in c.execute(
        "SELECT player_id, runs, balls, fours, sixes, pos FROM batting_gold WHERE match_id = ? AND inn = ?", (mid, inn))}
    parts = c.execute("SELECT for_wicket, batter1_id, batter2_id, runs, balls, b1_runs, b1_balls, b2_runs, b2_balls FROM "
                      "partnerships WHERE match_id = ? AND inn = ? AND source = 'espn_live' ORDER BY for_wicket", (mid, inn)).fetchall()
    fow = c.execute("SELECT wicket_no, runs, legal, batter_out_id FROM fall_of_wickets WHERE match_id = ? AND inn = ? AND "
                    "source = 'espn_live' ORDER BY wicket_no", (mid, inn)).fetchall()
    return bat, parts, fow


def apply_card(f, bat, parts):
    """Scorecard truths over the recorded ones: each batter's runs, balls, 4s, 6s; partnership splits if ESPN's add up."""
    for b in f["batters"]:
        g = bat.get(b["pid"])
        if g:
            b["R"], b["B"], b["4"], b["6"] = g[0], g[1], g[2], g[3]
    ok = bool(parts) and len(parts) == len(f["partnerships"]) and all(
        p[3] == q.get("runs") for p, q in zip(parts, f["partnerships"]))
    if ok:
        for p, q in zip(parts, f["partnerships"]):
            q["runs_by"] = {p[1]: p[5] or 0, p[2]: p[7] or 0}
            q["balls_by"] = {p[1]: p[6] or 0, p[2]: p[8] or 0}
        f["no_split"] = False
    else:
        f["no_split"] = True
    return ok


def recorded(f, dl, hard_overs=None, by=False):
    rec, seq, rby = collections.defaultdict(collections.Counter), collections.defaultdict(list), collections.defaultdict(collections.Counter)
    for d in dl:
        k = LS.kind_key(d)
        rec[d["over"]][k] += 1
        seq[d["over"]].append(k)
        rby[d["over"]][(d["striker"], k)] += 1
    f["recorded"], f["recorded_seq"] = dict(rec), dict(seq)
    f["recorded_hard"] = set(hard_overs or ())
    if by:
        f["recorded_by"] = {o: rby[o] for o in (hard_overs or ())}
        first = {}
        for d in dl:
            first.setdefault(d["over"], None if d["out"] else d["striker"])   # a wicket first ball: leave free
        f["recorded_first"] = {o: first[o] for o in (hard_overs or ()) if first.get(o)}   # who faced the over's 1st ball
    return f


def order_from(bat, dl):
    """Batting order: the scorecard's batting positions (ESPN's, in the gold scorecard) first - the recorded balls are
    the ones with wrong names; then anyone seen in the balls but missing from the card."""
    order = [p for p, _ in sorted(bat.items(), key=lambda kv: (kv[1][4] if kv[1][4] is not None else 99))]
    for d in dl:
        for p in (d["striker"], d["non_striker"]):
            if p is not None and p not in order:
                order.append(p)
    return order


# ------------------------------------------------------------------------------------------- jobs
def is_card_number(p):
    """A replay problem that is a scorecard NUMBER (reported as bent), not a broken Law of cricket."""
    return (p.startswith(("batter ", "stand ", "bowler ", "total:", "extras ", "fow "))
            or (p.startswith("over ") and (" runs: scorecard" in p)))


def classify(c, mid, inn, rec):
    """What is wrong with a flagged innings? Only 'strike/runs' can be fixed by moving strike; the rest need a rebuild."""
    bat, prt, fw = card(c, mid, inn)
    ro = [d["out"] for d in rec if d["out"]]
    xi = {p for (p,) in c.execute("SELECT player_id FROM played WHERE match_id = ? AND source = 'espn_live'", (mid,))}
    if not fw and ro:
        return "no ESPN fall of wickets"
    if len(ro) != len(fw):
        return "wickets missing/extra in the recorded balls (needs a rebuild)"
    if sorted(ro, key=str) != sorted([w[3] for w in fw], key=str):
        return "wickets given to the wrong player (needs a rebuild)"
    if xi and ({p for d in rec for p in (d["striker"], d["non_striker"]) if p is not None} - xi):
        return "a batter who wasn't in ESPN's XI (needs a rebuild)"
    return None


def who_faced(c, mid, inn, prior, pat, seconds, workers, info):
    rec, why = L.true_deliveries(c, mid, inn)
    if why or not rec:
        return None, {"status": f"skipped: {why or 'no balls'}"}
    cl = classify(c, mid, inn, rec)
    if cl:
        return None, {"status": f"skipped: {cl}"}
    bat, parts, _ = card(c, mid, inn)
    order = order_from(bat, rec)
    f = F.facts_from_deliveries(rec, info["day"], info["bpo"], "2", order=order)
    split = apply_card(f, bat, parts)
    recorded(f, rec)
    tries = [("with partnership splits" if split else "batter totals only", dict(f))]
    if split:
        g = dict(f); g["no_split"] = True; tries.append(("batter totals only", g))
    _b, prt, fw = card(c, mid, inn)
    fow_out = [r[3] for r in fw]
    pairs = [(p[1], p[2]) for p in prt]
    for label, ff in tries[:1]:
        dl, inf = who_faced_seq(ff, rec, seconds, workers, fow_out, pairs)
        if dl:
            # the Laws must hold exactly; scorecard numbers that had to bend are reported, never hidden
            ff2 = dict(ff, order=inf.get("order") or ff["order"])
            # same batters, new order (the incoming batters ESPN's partnerships name)
            have = {b["pid"]: b for b in ff["batters"]}
            ff2["batters"] = [have.get(p, {"pid": p, "R": None, "B": None, "4": None, "6": None, "how": None, "bowler": None}) for p in ff2["order"]]
            src_err = {p for p in F.replay(rec, ff2, check_strike=False)}      # errors already in the recorded balls
            laws = [p for p in F.replay(dl, ff2, check_strike=True) if not is_card_number(p) and p not in src_err]
            if not laws:
                inf["facts"] = label
                return dl, inf
            inf["status"] = f"answer broke a Law: {laws[0]}"
            inf["dl"], inf["ff2"] = dl, ff2
    return None, inf


def gaps(c, mid, inn, prior, pat, seconds, workers, info, rec=None, scorecard=None):
    """rec = the recorded (partial) deliveries; scorecard = full facts (level 1) of the innings."""
    if rec is None:
        rec, why = L.true_deliveries(c, mid, inn)
        if why or not rec:
            return None, {"status": f"skipped: {why or 'no balls'}"}
    bpo = info["bpo"]
    leg = collections.Counter(d["over"] for d in rec if d["legal"])
    f = dict(scorecard)
    f["fmt"] = "ODI" if str(mid).startswith("O") else "Test"     # the ball model differs by format
    total_overs = (f["legal_total"] + bpo - 1) // bpo
    known = {o for o, n in leg.items() if n == (bpo if o < total_overs - 1 else f["legal_total"] - bpo * (total_overs - 1))}
    kdl = [d for d in rec if d["over"] in known]
    over_info = {}
    for d in kdl:
        oi = over_info.setdefault(d["over"], {"over": d["over"], "bowler": d["bowler"], "runs": 0})
        oi["runs"] += F.team_runs(d)
    f["overs"] = [over_info[o] for o in sorted(over_info)]
    recorded(f, kdl, hard_overs=known, by=True)
    dl, inf = LS.solve_innings(f, prior, pat, seconds, workers, log=lambda *a: None)
    inf["known_overs"], inf["missing_overs"] = len(known), total_overs - len(known)
    if dl:
        # kept overs are real: put their recorded balls back in the recorded order (same batters, same start)
        by_o = collections.defaultdict(list)
        for d in kdl:
            by_o[d["over"]].append(d)
        out, put_back = [], 0
        for o in sorted({d["over"] for d in dl}):
            mine = [d for d in dl if d["over"] == o]
            r = by_o.get(o)
            if r and (mine[0]["striker"], mine[0]["non_striker"]) == (r[0]["striker"], r[0]["non_striker"]) and \
                    [LS.kind_key(d) for d in mine] != [LS.kind_key(d) for d in r]:
                mine = [dict(d) for d in r]; put_back += 1
            out += mine
        if put_back and not F.replay(out, f):
            dl = out
        inf["kept_overs_put_back"] = put_back
        probs = F.replay(dl, f)
        if probs:
            return None, dict(inf, status=f"answer broke {len(probs)} facts: {probs[0]}")
        for d in dl:
            d["label"] = "recorded" if d["over"] in known else "rebuilt"
    return dl, inf


# ------------------------------------------------------------------------------------------- who_faced, exact version
def who_faced_exact(f, rec, seconds, workers, prior=None, pat=None):
    """Keep every recorded delivery in its place; change as FEW of them as possible (runs, extra type) so that the
    strike, replayed by the Laws, gives every batter his scorecard runs / balls / 4s / 6s. Uses split_solver.Joint
    (one slot per delivery) with the recorded innings as the starting point."""
    import split_solver as SS
    from ortools.sat.python import cp_model as cp
    pieces = SS.make_pieces(f)
    jt = SS.Joint(f, pieces, prior or {}, patterns=None)
    m = jt.m
    m.ClearObjective()
    # recorded delivery -> its slot (pieces split at the wickets, then by over, in order)
    per_piece, k = collections.defaultdict(list), 0
    for d in rec:
        per_piece[k].append(d)
        if d["out"]:
            k += 1
    cost = []
    for k, pc in enumerate(jt.P):
        by_over = collections.defaultdict(list)
        for d in per_piece.get(k, []):
            by_over[d["over"]].append(d)
        used = collections.Counter()
        for j, sg in enumerate(pc.p["segs"]):
            o, slots = sg["over"], pc.idx[j]
            ds = by_over[o][used[o]:]
            take, nl = [], 0
            for d in ds:
                if not sg["trailing"] and nl == sg["n"] and (d["legal"] or sg["ends_over"]):
                    break
                take.append(d); nl += d["legal"]
                if sg["trailing"]:
                    break
            used[o] += len(take)
            for i, slot in enumerate(slots):
                S = pc.S[slot]
                d = take[i] if i < len(take) else None
                if d is None:
                    cost.append(10 * S["act"]); m.AddHint(S["act"], 0)
                    continue
                m.AddHint(S["act"], 1)
                same = [S["v"][min(d["bat"], 6)], S["W"] if d["kind"] == "wd" else S["W"].Not(),
                        S["N"] if d["kind"] == "nb" else S["N"].Not()]
                for x in range(7):
                    m.AddHint(S["v"][x], d["bat"] == x)
                m.AddHint(S["W"], d["kind"] == "wd"); m.AddHint(S["N"], d["kind"] == "nb"); m.AddHint(S["L"], bool(d["legal"]))
                z = m.NewBoolVar("same")
                for l_ in same:
                    m.AddImplication(z, l_)
                cost.append(10 * z.Not())
                rz = m.NewBoolVar("rx")                       # extra runs (byes / leg-byes / wides run) kept too
                m.Add(S["rx"] == d["ran"]).OnlyEnforceIf(rz)
                cost.append(3 * rz.Not())
    m.Minimize(sum(cost))
    s = cp.CpSolver()
    s.parameters.max_time_in_seconds = float(seconds)
    s.parameters.num_workers = workers
    st = s.StatusName(s.Solve(m))
    if st not in ("OPTIMAL", "FEASIBLE"):
        return None, {"status": st}
    jt.sol = s
    out = []
    bo = {o: (f["overs"][i]["bowler"]) for i, o in enumerate([x["over"] for x in f["overs"]])}
    for pc in jt.P:
        pc.bowler_of = bo
        out += pc._read(s.Value, s.BooleanValue)
    return out, {"status": st, "changed_cost": s.ObjectiveValue()}


# ------------------------------------------------------------------------------------------- who_faced: the sequence model
def who_faced_seq(f, rec, seconds, workers, fow_out=None, pairs=None):
    """Every recorded delivery stays where it is (over, order, extras, wickets). The striker follows the Laws: odd runs
    run swap ends, the ends swap after every over, a new batter comes in where the Laws allow (free only for run outs and
    pre-Oct-2022 catches). The only things that may change are runs off the bat (as few balls as possible, each over
    keeping its recorded total) - so every batter's runs, balls, 4s and 6s match the scorecard."""
    from ortools.sat.python import cp_model as cp
    era = f["era"]
    order = list(f["order"])
    card = {b["pid"]: b for b in f["batters"]}
    m = cp.CpModel()
    n = len(rec)
    outs = [i for i, d in enumerate(rec) if d["out"]]
    who_out = list(fow_out) if fow_out and len(fow_out) == len(outs) else [rec[i]["out"] for i in outs]
    # the pair at the crease for each stretch between wickets: A = survivor / opener 1, B = the other
    v = [[m.NewBoolVar("v") for _ in range(7)] for _ in range(n)]
    s_ = [m.NewBoolVar("s") for _ in range(n + 1)]       # A on strike before delivery i
    cost = []
    for i, d in enumerate(rec):
        m.AddExactlyOne(v[i])
        if d["kind"] == "wd":
            m.Add(v[i][0] == 1)                           # no runs off the bat on a wide
        m.AddHint(v[i][min(d["bat"], 6)], 1)
        cost.append(v[i][min(d["bat"], 6)].Not())
    bat = [sum(x * v[i][x] for x in range(7)) for i in range(n)]
    # over totals kept
    by_over = collections.defaultdict(list)
    for i, d in enumerate(rec):
        by_over[d["over"]].append(i)
    bent = []                                             # (what, slack var): facts that may bend, at a heavy price
    for o, ii in by_over.items():
        sl = m.NewIntVar(0, 60, "slo")
        m.Add(sum(bat[i] for i in ii) - sum(rec[i]["bat"] for i in ii) <= sl)
        m.Add(sum(rec[i]["bat"] for i in ii) - sum(bat[i] for i in ii) <= sl)
        bent.append((f"over {o + 1} total", sl))
    # odd runs actually run
    odd = []
    for i, d in enumerate(rec):
        ran = d["ran"]
        z = m.NewBoolVar("odd")
        if d["bat"] == 0 and ran == 4 and d["kind"] in ("b", "lb"):
            m.Add(z == 0)                                 # four byes = boundary, nobody ran
        else:
            # boundaries off the bat are not run: 4 and 6 count as even
            run_bat = sum(x * v[i][x] for x in (1, 2, 3, 5))
            h = m.NewIntVar(0, 10, "h")
            m.Add(run_bat + ran == 2 * h + z)
        odd.append(z)
    eov = [1 if (i + 1 < n and rec[i + 1]["over"] != d["over"]) else 0 for i, d in enumerate(rec)]
    # stretches between wickets
    pairs, cur, nxt = [], [order[0], order[1]], 2
    stretch = []
    k = 0
    for i in range(n):
        stretch.append(k)
        if rec[i]["out"]:
            k += 1
    pair_of = [None] * (len(outs) + 1)
    pair_of[0] = (order[0], order[1])
    inc = []
    # ESPN's partnerships name the exact pair at the crease for every stretch: use them when they fit the wickets
    exact = None
    if pairs and len(pairs) in (len(outs), len(outs) + 1) and all(a and b for a, b in pairs):
        exact = [tuple(p) for p in pairs]
        pair_of[0] = exact[0]
    for w, i in enumerate(outs):
        A, B = pair_of[w]
        out = who_out[w]
        if exact and w + 1 < len(exact):
            nxt_pair = set(exact[w + 1])
            gone = [p for p in (A, B) if p not in nxt_pair]
            if len(gone) == 1:
                out = gone[0]                              # the one not in the next stand is the one who got out
        if out not in (A, B):
            out = rec[i]["out"]                            # the scorecard order didn't fit: the recorded batter
        if out not in (A, B):
            return None, {"status": f"wicket {w + 1}: neither the scorecard's nor the recorded out batter is at the crease"}
        how = F.norm_kind(rec[i]["how"])
        survivor = B if out == A else A
        if exact and w + 1 < len(exact) and survivor in exact[w + 1]:
            newb = next(p for p in exact[w + 1] if p != survivor)
        else:
            newb = order[nxt] if nxt < len(order) else None
        nxt += 1
        pair_of[w + 1] = (survivor, newb)
        free = m.NewBoolVar("free")
        if how in LS.STRIKER_ONLY and (how not in ("caught", "caught and bowled") or era["caught_new_on_strike"]):
            m.Add(free == 1)
            m.Add(s_[i] == (1 if out == A else 0))        # bowled / lbw / stumped ...: the striker is out
        elif how in LS.STRIKER_ONLY:
            m.Add(s_[i] == (1 if out == A else 0))
        inc_ = m.NewBoolVar("inc")                          # new batter on strike next ball?
        h = m.NewIntVar(0, 1, "h2")
        m.Add(free + eov[i] - inc_ == 2 * h)
        inc.append(inc_)
    # strike transitions
    for i in range(n):
        if rec[i]["out"]:
            w = outs.index(i)
            m.Add(s_[i + 1] == 1 - inc[w])                 # next stretch: A = survivor
        else:
            h = m.NewIntVar(0, 1, "h3")
            m.Add(s_[i] + odd[i] + eov[i] - s_[i + 1] == 2 * h)
    # each batter's runs, balls, 4s, 6s
    contrib = collections.defaultdict(lambda: {"R": [], "B": [], "4": [], "6": []})
    for i, d in enumerate(rec):
        A, B = pair_of[stretch[i]]
        for pid, on in ((A, s_[i]), (B, s_[i].Not())):
            if pid is None:
                continue
            y = m.NewIntVar(0, 6, "y")
            m.Add(y == bat[i]).OnlyEnforceIf(on); m.Add(y == 0).OnlyEnforceIf(on.Not())
            contrib[pid]["R"].append(y)
            if d["kind"] != "wd":
                contrib[pid]["B"].append(on)
            for x in (4, 6):
                q = m.NewBoolVar("q")
                m.AddImplication(q, on); m.AddImplication(q, v[i][x]); m.AddBoolOr([q, on.Not(), v[i][x].Not()])
                contrib[pid][str(x)].append(q)
    soft = []
    for pid, c in contrib.items():
        b = card.get(pid) or {}
        if b.get("R") is not None:                         # runs: as exact as at all possible (heavy price)
            sl = m.NewIntVar(0, 1000, "slr")
            m.Add(sum(c["R"]) - b["R"] <= sl); m.Add(b["R"] - sum(c["R"]) <= sl)
            bent.append((f"batter {pid} runs", sl))
        for key in ("B", "4", "6"):                        # balls faced, 4s, 6s: often estimates in old cards -> as close as possible
            if b.get(key) is not None:
                d_ = m.NewIntVar(0, 1000, "dev")
                m.Add(sum(c[key]) - b[key] <= d_); m.Add(b[key] - sum(c[key]) <= d_)
                soft.append(d_)
    # a complete starting point (OR-Tools docs: hand CP-SAT a first solution): the recorded runs, strike replayed by the Laws
    sA = 1
    for i, d in enumerate(rec):
        m.AddHint(s_[i], sA)
        A, B = pair_of[stretch[i]]
        if d["out"]:
            sA = 0                                        # new batter (B of the next pair) on strike by default
            if eov[i]:
                sA = 1
        else:
            o_ = (d["bat"] not in (4, 6) and not (d["bat"] == 0 and d["ran"] == 4)) and (d["bat"] + d["ran"]) % 2 == 1
            sA = sA ^ int(o_) ^ eov[i]
    m.Minimize(10 * sum(cost) + 3 * sum(soft) + 1000 * sum(x for _, x in bent))
    sol = cp.CpSolver()
    sol.parameters.max_time_in_seconds = float(seconds)
    sol.parameters.num_workers = workers
    st = sol.StatusName(sol.Solve(m))
    if st not in ("OPTIMAL", "FEASIBLE"):
        return None, {"status": st}
    out = []
    for i, d in enumerate(rec):
        A, B = pair_of[stretch[i]]
        stk = A if sol.BooleanValue(s_[i]) else B
        e = dict(d)
        e["striker"], e["non_striker"] = stk, (B if stk == A else A)
        e["bat"] = next(x for x in range(7) if sol.BooleanValue(v[i][x]))
        e["label"] = "rebuilt"
        out.append(e)
    dev = sum(sol.Value(x) for x in soft)
    broke = [f"{w} off by {sol.Value(x)}" for w, x in bent if sol.Value(x)]
    # the batting order this answer used (openers, then each incoming batter) - the checker must use the same one
    used = [pair_of[0][0], pair_of[0][1]] + [pair_of[w + 1][1] for w in range(len(outs)) if pair_of[w + 1][1] is not None]
    used += [p for p in order if p not in used]
    return out, {"order": used, "status": st if not broke else "fixed except: " + "; ".join(broke[:4]),
                 "runs_changed": sum(sol.Value(c_) for c_ in cost), "card_misses": dev, "broken": broke}


# ------------------------------------------------------------------------------------------- tests
def strict_pct(sol, truth, overs=None):
    by = lambda dl: collections.defaultdict(list, {o: [d for d in dl if d["over"] == o] for o in {d["over"] for d in dl}})
    A, R = by(sol), by(truth)
    n = ex = st = 0
    for o in sorted(set(A) | set(R)):
        if overs is not None and o not in overs:
            continue
        for i in range(max(len(A[o]), len(R[o]))):
            a = A[o][i] if i < len(A[o]) else None
            r = R[o][i] if i < len(R[o]) else None
            n += 1
            if a and r:
                st += a["striker"] == r["striker"]
                ex += (a["striker"], a["bat"], a["kind"], a["out"], a["bowler"]) == (r["striker"], r["bat"], r["kind"], r["out"], r["bowler"])
    return round(100 * ex / max(1, n), 1), round(100 * st / max(1, n), 1), n


def test(c, prior_of, workers):
    mid = "O3148"
    info = L.match_info(c, mid)
    prior, pat = prior_of(mid)
    random.seed(7)
    for inn in (1, 2):
        truth, _ = L.true_deliveries(c, mid, inn)
        # who_faced: scramble the recorded strikers (20% of balls get the other batter) + 2 runs errors
        rec = [dict(d) for d in truth]
        for d in rec:
            if random.random() < 0.2 and d["non_striker"]:
                d["striker"], d["non_striker"] = d["non_striker"], d["striker"]
        before = strict_pct(rec, truth)
        scard = F.facts_from_deliveries(truth, info["day"], info["bpo"], "2")
        f = F.facts_from_deliveries(rec, info["day"], info["bpo"], "2", order=scard["order"])
        for b, t in zip(f["batters"], scard["batters"]):
            b.update({k: t[k] for k in ("R", "B", "4", "6")})
        f["partnerships"] = scard["partnerships"]; f["no_split"] = False
        f["fow"] = scard["fow"]
        recorded(f, rec)
        t0 = time.time()
        dl, inf = who_faced_seq(f, rec, 120, workers)
        after = strict_pct(dl, truth) if dl else None
        print(f"who_faced test, innings {inn}: {len(rec)} balls, 20% strikers scrambled -> exactly right before {before[0]}% "
              f"(striker {before[1]}%), after fix {after[0] if after else '-'}% (striker {after[1] if after else '-'}%) "
              f"| {inf.get('status')} {time.time() - t0:.0f} s | rule problems {len(F.replay(dl, f)) if dl else '-'}", flush=True)
        if "--b-only" in sys.argv:
            continue
        # gaps: hide overs 11-20 and 31-35, rebuild them
        hide = set(range(10, 20)) | set(range(30, 35))
        part = [d for d in truth if d["over"] not in hide]
        full = F.facts_from_deliveries(truth, info["day"], info["bpo"], "1")
        t0 = time.time()
        dl, inf = gaps(c, mid, inn, prior, pat, 300, workers, info, rec=part, scorecard=full)
        if dl:
            h = strict_pct(dl, truth, hide); k = strict_pct(dl, truth, set(range(60)) - hide)
            print(f"gaps test, innings {inn}: 15 overs hidden -> hidden overs exactly right {h[0]}% (striker {h[1]}%) of {h[2]} "
                  f"| kept overs {k[0]}% of {k[2]} | {inf.get('status')} {time.time() - t0:.0f} s", flush=True)
        else:
            print(f"gaps test, innings {inn}: NO ANSWER {inf}", flush=True)


# ------------------------------------------------------------------------------------------- batch
def batch(job, c, prior_of, args):
    c.execute("""CREATE TABLE IF NOT EXISTS balls_rebuilt (match_id TEXT, inn INT, seq INT, over_no INT, ball INT,
                 bowler_id INT, batter_id INT, non_striker_id INT, bat_runs INT, extras INT, extra_type TEXT, legal INT,
                 wicket INT, kind TEXT, out_id INT, label TEXT, job TEXT)""")
    if job == "who_faced":
        todo = c.execute("SELECT DISTINCT match_id, inn FROM balls_fix WHERE fix = 'attribution_unsure' ORDER BY 1, 2").fetchall()
        if "--trial" in sys.argv:
            todo = c.execute("SELECT match_id, inn FROM trial30").fetchall()
    else:
        todo = c.execute("SELECT match_id, inn FROM innings_gold WHERE bbb_complete = 0 ORDER BY 1, 2").fetchall()
    if args.sample:                                     # a random trial of N innings (same every time)
        todo = sorted(random.Random(3).sample(todo, min(args.sample, len(todo))))
    if args.limit:
        todo = todo[:args.limit]
    tag = ""
    if args.shard:                                      # e.g. 2/4 = the 2nd of 4 copies running side by side
        k, n = map(int, args.shard.split("/"))
        todo, tag = todo[k - 1::n], f"_s{k}"
        c.execute("PRAGMA busy_timeout = 120000")       # the copies share one database file
    os.makedirs(os.path.join(ROOT, "data", "reports"), exist_ok=True)
    rpath = os.path.join(ROOT, "data", "reports", f"fix_{job}{tag}.csv")
    done = set()
    if args.resume and os.path.exists(rpath):          # carry on after a stop: skip innings already in the report
        done = {(r["match_id"], int(r["inn"])) for r in csv.DictReader(open(rpath, encoding="utf-8"))}
        todo = [t for t in todo if (t[0], int(t[1])) not in done]
        print(f"  resuming: {len(done)} innings already done, {len(todo)} to go", flush=True)
    rep = open(rpath, "a" if done else "w", newline="", encoding="utf-8")
    w = csv.writer(rep)
    if not done:
        w.writerow(["match_id", "inn", "status", "seconds", "facts", "balls", "striker_changed", "runs_changed", "note"])
    t0, ok, cnt = time.time(), 0, collections.Counter()
    for k, (mid, inn) in enumerate(todo, 1):
        el = int(time.time() - t0)
        eta = int(el / max(1, k - 1) * (len(todo) - k + 1)) if k > 1 else 0
        print(f"\r  ⏱ {el // 60}:{el % 60:02d} | {job} | innings {k}/{len(todo)} ({mid} inn {inn}) | fixed {ok} | "
              f"about {eta // 60} min left   ", end="", flush=True)
        info = L.match_info(c, mid)
        prior, pat = prior_of(mid)
        nb = len(L.true_deliveries(c, mid, inn)[0] or [])
        secs = min(args.max_time, max(args.time, nb * args.per_ball))   # long Test innings get more time
        t1 = time.time()
        try:
            if job == "who_faced":
                dl, inf = who_faced(c, mid, inn, prior, pat, secs, args.workers, info)
            else:
                rec, why = L.true_deliveries(c, mid, inn)
                if why or not rec:
                    dl, inf = None, {"status": f"skipped: {why or 'no balls'}"}
                else:
                    sc = scorecard_facts(c, mid, inn, info, rec)
                    dl, inf = (None, {"status": "skipped: scorecard facts incomplete"}) if sc is None else \
                        gaps(c, mid, inn, prior, pat, secs, args.workers, info, rec=rec, scorecard=sc)
        except Exception as e:                          # never stop the batch for one innings
            dl, inf = None, {"status": f"error: {type(e).__name__}: {e}"[:200]}
        sec = round(time.time() - t1)
        bent = [int(v) for v in re.findall(r"off by (\d+)", str(inf.get("status", "")))]
        if dl and bent and max(bent) >= args.max_bend:     # a poor answer is never saved as fixed
            dl, inf = None, dict(inf, status=f"not fixed: a number bent by {max(bent)} (limit {args.max_bend - 1}); "
                                              f"try more --time. {inf.get('status')}"[:300])
        if dl:
            ok += 1
            rec, _ = L.true_deliveries(c, mid, inn)
            rmap = collections.defaultdict(list)
            for d in rec:
                rmap[d["over"]].append(d)
            sch = rch = 0
            for o in {d["over"] for d in dl}:
                for a, r in zip([d for d in dl if d["over"] == o], rmap[o]):
                    sch += a["striker"] != r["striker"]; rch += a["bat"] != r["bat"]
            c.execute("DELETE FROM balls_rebuilt WHERE match_id = ? AND inn = ? AND job = ?", (mid, inn, job))
            lc = collections.Counter()
            rows = []
            for s, d in enumerate(dl, 1):
                lc[d["over"]] += d["legal"]
                ex = d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"]
                rows.append((mid, inn, s, d["over"] + min(r["over"] for r in rec) if False else d["over"], lc[d["over"]],
                             d["bowler"], d["striker"], d["non_striker"], d["bat"], ex, d["kind"] or None, int(d["legal"]),
                             1 if d["out"] else 0, d["how"], d["out"], d.get("label") or "rebuilt", job))
            c.executemany("INSERT INTO balls_rebuilt VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            c.commit()
            w.writerow([mid, inn, inf.get("status"), sec, inf.get("facts", ""), len(dl), sch, rch, ""])
            cnt["fixed"] += 1
        else:
            c.execute("DELETE FROM balls_rebuilt WHERE match_id = ? AND inn = ? AND job = ?", (mid, inn, job)); c.commit()
            w.writerow([mid, inn, inf.get("status"), sec, "", 0, "", "", ""])
            cnt[str(inf.get("status"))[:40]] += 1
        rep.flush()
    rep.close()
    print(f"\n{job}: {ok} of {len(todo)} innings fixed in {int(time.time() - t0)} s; " + ", ".join(f"{k}: {v}" for k, v in cnt.most_common()))
    print(f"report: data/reports/fix_{job}{tag}.csv; rows in table balls_rebuilt")


def scorecard_facts(c, mid, inn, info, rec):
    """Level-1 facts of a real innings from the gold scorecard + ESPN's fall of wickets and partnerships."""
    bat, parts, fow = card(c, mid, inn)
    if not bat or not fow and any(d["out"] for d in rec):
        return None
    g = c.execute("SELECT runs, wickets, extras FROM innings_gold WHERE match_id = ? AND inn = ?", (mid, inn)).fetchone()
    ov = c.execute("SELECT overs FROM innings WHERE match_id = ? AND inn = ? AND source IN ('espn_live','gold') AND overs IS NOT NULL "
                   "ORDER BY source = 'espn_live' DESC LIMIT 1", (mid, inn)).fetchone()
    if not g or not ov:
        return None
    a, _, b = str(ov[0]).partition(".")
    lt = int(a) * info["bpo"] + int(b or 0)
    order = order_from(bat, rec)
    kinds = {p: (k, bw) for p, k, bw in c.execute("SELECT player_id, kind, bowler_id FROM batting_gold WHERE match_id = ? AND inn = ?", (mid, inn))}
    bowl = [{"pid": p, "balls": bl, "runs": r, "maidens": md, "wkts": wk, "wd": None, "nb": None, "dots": None, "4": None, "6": None}
            for p, bl, r, md, wk in c.execute("SELECT player_id, balls, runs, maidens, wickets FROM bowling_gold WHERE match_id = ? AND inn = ?", (mid, inn))]
    f = {"day": info["day"], "bpo": info["bpo"], "era": F.era_flags(info["day"], info["bpo"]), "order": order, "legal_total": lt,
         "total": g[0], "pen": 0,
         "batters": [{"pid": p, "R": bat[p][0] if p in bat else None, "B": bat[p][1] if p in bat else None,
                      "4": bat[p][2] if p in bat else None, "6": bat[p][3] if p in bat else None,
                      "how": F.norm_kind((kinds.get(p) or (None, None))[0]), "bowler": (kinds.get(p) or (None, None))[1]} for p in order],
         "bowlers": bowl, "extras": {"b": None, "lb": None, "wd": None, "nb": None},
         "fow": [], "partnerships": [], "anchors": [], "overs": None}
    for wn, runs, legal, out in fow:
        k = F.norm_kind((kinds.get(out) or (None, None))[0])
        f["fow"].append({"out": out, "how": k, "bowler": (kinds.get(out) or (None, None))[1] if k in F.CREDITED else None,
                         "score": runs, "legal": legal})
    for p in parts:
        f["partnerships"].append({"runs": p[3], "balls": p[4], "runs_by": {p[1]: p[5] or 0, p[2]: p[7] or 0},
                                  "balls_by": {p[1]: p[6] or 0, p[2]: p[8] or 0}})
    if sum(p[3] or 0 for p in parts) != g[0]:
        f["no_split"] = True
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["test", "who_faced", "gaps"])
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "everyball.db"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--time", type=float, default=120, help="seconds per innings")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--per_ball", type=float, default=0.12, help="extra seconds per ball for long innings")
    ap.add_argument("--max_time", type=float, default=240, help="most seconds for one innings")
    ap.add_argument("--resume", action="store_true", help="skip innings already in this job's report")
    ap.add_argument("--sample", type=int, default=0, help="run a random sample of N innings (a trial)")
    ap.add_argument("--max_bend", type=int, default=5, help="refuse answers that bend a number by this much")
    ap.add_argument("--shard", default="", help="k/n: run the k-th of n copies side by side")
    args, _unknown = ap.parse_known_args()
    import accuracy_test as AT
    c = L.connect(args.db)
    cache = {}
    pat_all = AT.patterns_without(c, "-")                 # one pattern table for the batch (fast); tests leave the match out
    def prior_of(mid, leave_out=False):
        if mid not in cache:
            cache[mid] = (AT.prior_without(c, mid), AT.patterns_without(c, mid) if args.job == "test" else pat_all)
        return cache[mid]
    if args.job == "test":
        test(c, prior_of, args.workers)
    else:
        batch(args.job, c, lambda mid: (prior_of("-")[0], pat_all), args)


if __name__ == "__main__":
    main()
