"""
Accuracy test: hide a real match's ball-by-ball, rebuild it from the facts of one level, and compare
ball by ball with the real thing - strictly. Solver: split_solver.py (split at the wickets, all partnerships
solved together, every scorecard fact exact; --engine old = the earlier whole-innings solver).

  python3 scripts/solver/accuracy_test.py O3148                       # 2011 World Cup final: levels 2, 3a, 3b, then 1
  python3 scripts/solver/accuracy_test.py O3148 --levels 2,3a,3b      # skip level 1 (the slow one)
  python3 scripts/solver/accuracy_test.py O3148 --time 600 --time-level1 1800   # with time limits

Time: up to 600 s per innings for the solve (--time / --time-level1, 0 = no limit), then up to 900 s for the
proven / guessed check (--prove-budget). It stops early as soon as the best answer is proven.

LEVELS (all facts come from the real match):
  1   scorecard: every batter's runs, balls, 4s, 6s and how out; every bowler's overs, maidens, runs, wickets,
      wides, no-balls; extras; fall of wickets with the over (e.g. 31-1 at 6.1); partnerships
  2   level 1 + over by over: who bowled each over and the runs in it
  3a  level 2 + in which over each four and six came, and who hit it (not which ball - what highlights give)
  3b  level 2 + the exact ball of every four and six (what a full video / readable score box gives)

HOW IT IS SCORED (nothing skipped, nothing rounded up)
  Every delivery, wides and no-balls included, is lined up by over and by its place in the over:
  the real 11.1 against the rebuilt 11.1, and so on. A ball is EXACTLY RIGHT only if all of these match:
  bowler, batter on strike, non-striker, runs off the bat, the extra (type and runs) and the wicket (who, how).
  If an over has more or fewer deliveries in the rebuild, the extra / missing ones count as wrong.
  Also shown: the same numbers for only the balls we did NOT give it (3b gives the boundary balls), a plain
  "every ball is a dot" guess for comparison, and order-free checks (each batter v bowler: balls and runs).
Output: data/solver/accuracy/<match>/report.md, report.json and one side-by-side CSV per level and innings
(real v rebuilt, every wrong ball marked).
"""
import argparse, collections, csv, json, os, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cricket_facts as F
import db_load as L
import rebuild as R
import over_plan as OP
import split_solver as SS

NAMES = {"1": "Level 1 - scorecard only", "2": "Level 2 - + who bowled each over and its runs",
         "3a": "Level 3a - + the over of every four and six", "3b": "Level 3b - + the exact ball of every four and six"}


def by_over(dl):
    out = collections.defaultdict(list)
    for d in dl:
        out[d["over"]].append(d)
    return out


def extra(d):
    return (d["kind"] or "", d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"])


def label(d, legal_before, over):
    if d is None:
        return ""
    if d["legal"]:
        return f"{over}.{legal_before + 1}"
    return f"{over}.{legal_before + 1} ({d['kind']})"


def strict(sol, truth, facts, names):
    """Ball by ball, every field. Returns (numbers, rows for the side-by-side sheet)."""
    R_, A_ = by_over(truth), by_over(sol)
    given = set()                                   # (over, legal ball) given as facts (level 3b)
    for an in facts.get("anchors") or []:
        given.add((an["over"], an["ball"]))
    c = collections.Counter()
    rows = []
    for o in sorted(set(R_) | set(A_)):
        rl, al = R_.get(o, []), A_.get(o, [])
        lr = la = 0
        for i in range(max(len(rl), len(al))):
            r = rl[i] if i < len(rl) else None
            a = al[i] if i < len(al) else None
            ok = {}
            ok["bowler"] = bool(r and a and r["bowler"] == a["bowler"])
            ok["striker"] = bool(r and a and r["striker"] == a["striker"])
            ok["non_striker"] = bool(r and a and r["non_striker"] == a["non_striker"])
            ok["runs"] = bool(r and a and r["bat"] == a["bat"] and extra(r) == extra(a))
            ok["wicket"] = bool(r and a and (r["out"], r["how"]) == (a["out"], a["how"]))
            exact = all(ok.values())
            is_given = bool(r and r["legal"] and (o, lr + 1) in given)
            for part in ("all", "hidden") if not is_given else ("all",):
                c[part + "_n"] += 1
                c[part + "_exact"] += exact
                for k, v in ok.items():
                    c[part + "_" + k] += v
                if r:
                    c[part + "_dotguess"] += (r["bat"] == 0 and extra(r) == ("", 0) and not r["out"])
            if r and a:
                lab = a.get("label") or "estimate"
                c["lab_" + lab] += 1
                c["labok_" + lab] += ok["runs"]
                c["labex_" + lab] += exact
            if r and r["out"]:
                c["wk_real"] += 1
                c["wk_right"] += ok["wicket"]
            if a and a["out"] and not (r and r["out"] and ok["wicket"]):
                c["wk_misplaced"] += 1
            nm = lambda p: names.get(p, str(p)) if p is not None else ""
            desc = lambda d: "" if d is None else (
                f"{d['bat']}" + (f" +{extra(d)[1]}{extra(d)[0]}" if extra(d)[0] else "") + (f" OUT {nm(d['out'])} ({d['how']})" if d["out"] else ""))
            rows.append([label(r, lr, o + 1) or label(a, la, o + 1), "RIGHT" if exact else "wrong",
                         nm(r["bowler"]) if r else "", nm(a["bowler"]) if a else "",
                         nm(r["striker"]) if r else "", nm(a["striker"]) if a else "",
                         desc(r), desc(a), "given" if is_given else "",
                         ", ".join(k for k, v in ok.items() if not v)])
            lr += bool(r and r["legal"]); la += bool(a and a["legal"])
    pc = lambda part, k: round(100 * c[part + "_" + k] / c[part + "_n"], 2) if c[part + "_n"] else None
    res = {}
    for part in ("all", "hidden"):
        res[part] = {"deliveries": c[part + "_n"], "exactly_right_pct": pc(part, "exact"), "runs_right_pct": pc(part, "runs"),
                     "striker_right_pct": pc(part, "striker"), "non_striker_right_pct": pc(part, "non_striker"),
                     "bowler_right_pct": pc(part, "bowler"), "wicket_field_right_pct": pc(part, "wicket"),
                     "every_ball_a_dot_guess_runs_pct": pc(part, "dotguess")}
    res["labels"] = {k[4:]: {"balls": v, "runs_right": c["labok_" + k[4:]], "exactly_right": c["labex_" + k[4:]]}
                     for k, v in c.items() if k.startswith("lab_")}
    res["wickets"] = {"real": c["wk_real"], "on_the_right_ball": c["wk_right"], "rebuilt_on_a_wrong_ball": c["wk_misplaced"]}
    # order-free: each batter v bowler, balls and runs
    def duels(dl):
        out = collections.defaultdict(lambda: [0, 0])
        for d in dl:
            if d["kind"] != "wd":
                out[(d["striker"], d["bowler"])][0] += 1
            out[(d["striker"], d["bowler"])][1] += d["bat"]
        return out
    dr, da = duels(truth), duels(sol)
    keys = set(dr) | set(da)
    res["batter_v_bowler"] = {"pairs": len(keys), "balls_and_runs_exact": sum(dr.get(k) == da.get(k) for k in keys),
                              "balls_exact": sum((dr.get(k) or [0, 0])[0] == (da.get(k) or [0, 0])[0] for k in keys),
                              "runs_exact": sum((dr.get(k) or [0, 0])[1] == (da.get(k) or [0, 0])[1] for k in keys)}
    ov_r = {o: (v[0]["bowler"], sum(F.team_runs(d) for d in v)) for o, v in R_.items()}
    ov_a = {o: (v[0]["bowler"], sum(F.team_runs(d) for d in v)) for o, v in A_.items()}
    res["overs"] = {"overs": len(ov_r), "bowler_right": sum(ov_a.get(o, (None,))[0] == b for o, (b, _) in ov_r.items()),
                    "runs_right": sum(ov_a.get(o, (None, None))[1] == x for o, (_, x) in ov_r.items())}
    return res, rows


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def write_reports(report, out, jpath):
    json.dump(report, open(jpath, "w"), indent=1)
    # the readable report
    md = [f"# Rebuild accuracy: {report['teams']}, {report['date']} ({report['match']})", "",
          "Each innings was hidden and rebuilt from the facts of one level, then compared delivery by delivery. "
          "A ball is **exactly right** only if bowler, striker, non-striker, runs off the bat, the extra and the wicket "
          "all match. Wrong-length overs count every extra or missing delivery as wrong.", "",
          "| Level | Inn | Solver | Exactly right | Runs right | Striker | Bowler | Wickets on right ball | Not-given balls exactly right | Every-ball-a-dot guess (runs) | Batter v bowler pairs exact | Proven deliveries (exactly right) | Guessed deliveries (exactly right) | Over plan (bowler / runs right) | Facts broken |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in report["results"]:
        if "all" not in e:
            md.append(f"| {e['level']} | {e['innings']} | {e['status']} {e['seconds']} s | no answer | | | | | | | | | | | |"); continue
        al, hd, bv = e["all"], e["hidden"], e["batter_v_bowler"]
        lb = e.get("labels") or {}
        lab = lambda k: (f"{lb[k]['balls']} ({100 * lb[k].get('exactly_right', lb[k]['runs_right']) / lb[k]['balls']:.1f}%)"
                         if lb.get(k, {}).get("balls") else "-")
        op = e.get("over_plan")
        plan = f"{op['bowler_right']}/{op['overs']} · {op['runs_right']}/{op['overs']}" if op else "given" if e["level"] != "1" else "-"
        md.append(f"| {e['level']} | {e['innings']} | {e['status']} {e['seconds']} s | **{al['exactly_right_pct']}%** of {al['deliveries']} "
                  f"| {al['runs_right_pct']}% | {al['striker_right_pct']}% | {al['bowler_right_pct']}% "
                  f"| {e['wickets']['on_the_right_ball']} of {e['wickets']['real']} | {hd['exactly_right_pct']}% of {hd['deliveries']} "
                  f"| {al['every_ball_a_dot_guess_runs_pct']}% | {bv['balls_and_runs_exact']} of {bv['pairs']} | {lab('proven')} | {lab('guessed')} | {plan} | {len(e['facts_broken'])} |")
    md += ["", "Levels: " + "; ".join(f"**{k}** = {v.split(' - ')[1]}" for k, v in NAMES.items()) + ".",
           "**Proven** = nothing about that delivery (runs, extras, striker, wicket; for wides / no-balls also where they "
           "fell in the over) could be different and still fit every real fact; **guessed** = another version also "
           "fitted (the solver picked the most likely). Proven deliveries should be 100% exactly right.",
           "Side-by-side sheets (every ball, real v rebuilt, wrong ones marked): `level<L>_innings<I>.csv` in this folder."]
    open(os.path.join(out, "report.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")


def prior_without(c, mid):
    """How often each run value happens off the bat, by phase (ODIs, every ball we have) - this match left out."""
    t = collections.defaultdict(collections.Counter)
    for ov, br, n in c.execute("select over_no, bat_runs, count(*) from balls b join matches m using(match_id) "
                               "where m.format='ODI' and b.source = m.ball_source and b.match_id != ? "
                               "and coalesce(extra_type,'') != 'wd' group by 1, 2", (mid,)):
        ph = 0 if ov <= 10 else (2 if ov > 40 else 1)
        t[ph][min(int(br or 0), 6)] += n
    return {ph: {v: (cnt[v] + 1) / (sum(cnt.values()) + 7) for v in range(7)} for ph, cnt in t.items()}


def patterns_without(c, mid, k=200):
    """Each batter's and each bowler's run pattern (how often 0, 1, 2, 3, 4, 5, 6 off the bat), from every ODI ball
    we have - this match left out. Small samples lean on the average player (k pretend balls of average)."""
    allc, bat, bowl = collections.Counter(), collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for bt, bw, br, n in c.execute("select b.batter_id, b.bowler_id, b.bat_runs, count(*) from balls b join matches m "
                                   "using(match_id) where m.format='ODI' and b.source = m.ball_source and b.match_id != ? "
                                   "and coalesce(b.extra_type,'') != 'wd' group by 1, 2, 3", (mid,)):
        x = min(int(br or 0), 6)
        allc[x] += n; bat[bt][x] += n; bowl[bw][x] += n
    tot = sum(allc.values()) or 1
    pall = {x: (allc[x] + 1) / (tot + 7) for x in range(7)}
    shrink = lambda cnt: {x: (cnt[x] + k * pall[x]) / (sum(cnt.values()) + k) for x in range(7)}
    return {"all": pall, "bat": {p: shrink(cn) for p, cn in bat.items()}, "bowl": {p: shrink(cn) for p, cn in bowl.items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match")
    ap.add_argument("--levels", default="2,3a,3b,1")
    ap.add_argument("--innings", default="1,2")
    ap.add_argument("--db", default=os.path.join(R.ROOT, "data", "everyball.db"))
    ap.add_argument("--time", type=float, default=600, help="seconds per innings, levels 2/3a/3b (0 = no limit)")
    ap.add_argument("--time-level1", type=float, default=600, help="seconds per innings, level 1 (0 = no limit)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--engine", default="split", choices=["split", "old"],
                    help="split = the split-at-wickets solver (default); old = the whole-innings solver")
    ap.add_argument("--no-lock", action="store_true", help="old engine: facts soft instead of locked")
    ap.add_argument("--no-plan", action="store_true", help="level 1 without planning the overs first")
    ap.add_argument("--plan-time", type=float, default=120, help="seconds for the over plan (level 1)")
    ap.add_argument("--no-prove", action="store_true", help="skip the proven / guessed check")
    ap.add_argument("--prove-seconds", type=float, default=2, help="seconds per ball for the proven / guessed check")
    ap.add_argument("--prove-budget", type=float, default=900, help="seconds per innings for the proven / guessed check")
    a = ap.parse_args()
    if R.BACKEND != "ortools":
        print("NOTE: OR-Tools is not installed (pip install ortools) - using the slow test stand-in.")
    c = L.connect(a.db)
    names = dict(c.execute("select player_id, name from players"))
    prior = prior_without(c, a.match)
    print("Learning run patterns of every batter and bowler from our ODI balls (this match left out)...", flush=True)
    patterns = patterns_without(c, a.match)
    info = L.match_info(c, a.match)
    out = os.path.join(R.ROOT, "data", "solver", "accuracy", a.match)
    os.makedirs(out, exist_ok=True)
    report = {"match": a.match, "date": info["day"], "teams": f"{info['team1']} v {info['team2']}", "backend": R.BACKEND,
              "workers": a.workers, "results": []}
    jpath = os.path.join(out, "report.json")
    n_runs, run_no = len(a.levels.split(",")) * len(a.innings.split(",")), [0]
    t_start = time.time()

    def heartbeat():                          # a status line every 30 s: where it is, how long, how far
        while True:
            time.sleep(30)
            st = SS.STATUS
            el = int(time.time() - t_start)
            prog = f" | {st['done']}/{st['total']} deliveries checked" if st.get("total") else ""
            print(f"  ⏱ {el // 60}:{el % 60:02d} | {st.get('run', '')} | {st.get('stage', '')}{prog}"
                  + (f" | {st['extra']}" if st.get("extra") else ""), flush=True)
    if a.engine == "split":
        threading.Thread(target=heartbeat, daemon=True).start()
    for lv in a.levels.split(","):
        for inn in [int(x) for x in a.innings.split(",")]:
            truth, why = L.true_deliveries(c, a.match, inn)
            if why:
                print(f"innings {inn}: cannot test ({why})"); continue
            facts = F.facts_from_deliveries(truth, info["day"], info["bpo"], lv)
            probs = F.replay(truth, facts)
            if probs:
                print(f"innings {inn}: the real balls break a modelled rule ({probs[0]}) - skipped"); continue
            facts0 = facts                       # the real facts (the replay check uses these, never the plan)
            if a.engine == "split":
                run_no[0] += 1
                SS.STATUS.update(run=f"run {run_no[0]} of {n_runs}: {NAMES[lv]}, innings {inn}", stage="starting")
                limit = a.time_level1 if lv == "1" else a.time
                print(f"{NAMES[lv]}, innings {inn}: split-at-wickets solver "
                      f"({'no time limit' if limit <= 0 else f'up to {int(limit)} s'}, {a.workers} workers)", flush=True)
                entry = {"level": lv, "innings": inn, "status": "running", "seconds": 0, "over_plan": None}
                report["results"].append(entry)
                t0 = time.time()
                try:
                    sol, sinfo = SS.solve_innings(facts, prior, seconds=limit, workers=a.workers, prove=not a.no_prove,
                                                  prove_seconds=a.prove_seconds, prove_budget=a.prove_budget,
                                                  log=lambda t_: print(t_, flush=True), patterns=patterns)
                except ValueError as e:
                    sol, sinfo = None, {"status": f"can't split: {e}"}
                secs = round(time.time() - t0)
                if sol is None:
                    entry.update(status=sinfo["status"], seconds=secs, note="no answer")
                    write_reports(report, out, jpath); print(f"  {sinfo['status']} after {secs} s - no answer", flush=True)
                    continue
                res, rows = strict(sol, truth, facts, names)
                left_ = F.replay(sol, facts0)
                entry.update(res, status=sinfo["status"], seconds=secs, facts_broken=[], rule_problems=len(left_),
                             solver=sinfo)
                with open(os.path.join(out, f"level{lv}_innings{inn}.csv"), "w", newline="", encoding="utf-8") as fh:
                    w_ = csv.writer(fh)
                    w_.writerow(["ball", "result", "bowler (real)", "bowler (rebuilt)", "striker (real)", "striker (rebuilt)",
                                 "real", "rebuilt", "given as a fact", "what is wrong"])
                    w_.writerows(rows)
                write_reports(report, out, jpath)
                al = res["all"]
                print(f"  DONE in {secs} s | EXACTLY RIGHT {al['exactly_right_pct']}% of {al['deliveries']} deliveries "
                      f"| runs {al['runs_right_pct']}% (every-ball-a-dot guess {al['every_ball_a_dot_guess_runs_pct']}%) "
                      f"| striker {al['striker_right_pct']}% | bowler {al['bowler_right_pct']}% "
                      f"| wickets on the right ball {res['wickets']['on_the_right_ball']}/{res['wickets']['real']}"
                      + "".join(f" | {k} {v['balls']} deliveries, exactly right {100 * v['exactly_right'] / v['balls']:.1f}%"
                                for k, v in res["labels"].items() if k in ("proven", "guessed") and v["balls"])
                      + f" | rule problems {len(left_)}", flush=True)
                continue
            plan_acc = None
            if lv == "1" and not a.no_plan:
                print(f"{NAMES[lv]}, innings {inn}: step 1 - planning the overs (up to {int(a.plan_time)} s)...", flush=True)
                plan, pst = OP.plan_overs(facts, a.plan_time, a.workers)
                if plan:
                    facts = dict(facts, overs=plan, overs_planned=True)
                    plan_acc = OP.plan_accuracy(plan, truth)
                    print(f"  over plan {pst}: bowler right in {plan_acc['bowler_right']} of {plan_acc['overs']} overs, "
                          f"runs exactly right in {plan_acc['runs_right']}", flush=True)
                else:
                    print(f"  no over plan ({pst}) - solving without it", flush=True)
            limit = a.time_level1 if lv == "1" else a.time
            print(f"{NAMES[lv]}, innings {inn}{' (wickets locked)' if not a.no_lock else ''}: solving ({'no time limit' if limit <= 0 else f'up to {int(limit)} s'}, "
                  f"{a.workers} workers) - to move on: touch STOP", flush=True)
            t0 = time.time()
            rb = R.Rebuilder(facts, prior, lock=not a.no_lock).build()
            entry = {"level": lv, "innings": inn, "status": "running", "seconds": 0, "over_plan": plan_acc}
            report["results"].append(entry)
            sheet = os.path.join(out, f"level{lv}_innings{inn}.csv")

            def save(sol, secs, status):
                res, rows = strict(sol, truth, facts, names)
                left = F.replay(sol, facts0)
                entry.update(res, status=status, seconds=secs, facts_broken=[f"{n} (off by {v})" for n, v in rb.broken],
                             rule_problems=len(left))
                with open(sheet, "w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(["ball", "result", "bowler (real)", "bowler (rebuilt)", "striker (real)", "striker (rebuilt)",
                                "real", "rebuilt", "given as a fact", "what is wrong"])
                    w.writerows(rows)
                write_reports(report, out, jpath)
                return res

            def better(sol, secs):
                res = save(sol, secs, "searching (best so far)")
                print(f"  better answer at {secs} s: exactly right {res['all']['exactly_right_pct']}%, "
                      f"runs right {res['all']['runs_right_pct']}%, facts broken {len(rb.broken)}", flush=True)

            stop_file = os.path.join(R.ROOT, "STOP")
            done = threading.Event()

            def watch():
                while not done.wait(2):
                    if os.path.exists(stop_file):
                        os.remove(stop_file)
                        print("  STOP found: keeping the best answer so far, moving on", flush=True)
                        try:
                            rb.live.StopSearch()
                        except Exception:
                            pass
            threading.Thread(target=watch, daemon=True).start()
            sol = rb.solve(limit, a.workers, on_better=better)
            if sol is None and rb.status == "INFEASIBLE" and rb.lock:
                print("  locked facts can't all be true together - solving again with soft facts", flush=True)
                rb = R.Rebuilder(facts, prior, lock=False).build()
                sol = rb.solve(limit, a.workers, on_better=better)
            done.set()
            secs = round(time.time() - t0)
            if sol is None:
                entry.update(status=rb.status, seconds=secs, note="no answer found")
                write_reports(report, out, jpath)
                print(f"  {rb.status} after {secs} s - no answer"); continue
            if not a.no_prove and R.BACKEND == "ortools":
                print(f"  step 3 - proven or guessed: checking every ball (up to {int(a.prove_budget)} s)...", flush=True)
                sol = rb.mark_forced(sol, a.prove_seconds, a.prove_budget, a.workers, log=lambda t: print(t, flush=True))
                secs = round(time.time() - t0)
            res = save(sol, secs, rb.status)
            al, hd = res["all"], res["hidden"]
            print(f"  {rb.status} in {secs} s | EXACTLY RIGHT {al['exactly_right_pct']}% of {al['deliveries']} deliveries "
                  f"| runs {al['runs_right_pct']}% (every-ball-a-dot guess {al['every_ball_a_dot_guess_runs_pct']}%) "
                  f"| striker {al['striker_right_pct']}% | bowler {al['bowler_right_pct']}% "
                  f"| wickets on the right ball {res['wickets']['on_the_right_ball']}/{res['wickets']['real']}"
                  + "".join(f" | {k} {v['balls']} balls, runs right {100 * v['runs_right'] / v['balls']:.1f}%"
                            for k, v in res["labels"].items() if k in ("proven", "guessed") and v["balls"])
                  + (f" | not-given balls exactly right {hd['exactly_right_pct']}%" if hd["deliveries"] != al["deliveries"] else "")
                  + f" | facts broken {len(rb.broken)}", flush=True)
            json.dump(report, open(jpath, "w"), indent=1)
    write_reports(report, out, jpath)
    print(f"\nReport: {os.path.relpath(os.path.join(out, 'report.md'), R.ROOT)}")


if __name__ == "__main__":
    main()
