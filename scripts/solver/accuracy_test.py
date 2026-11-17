"""
Accuracy test: hide a real match's ball-by-ball, rebuild it from the facts of one level, and compare
ball by ball with the real thing - strictly.

  python3 scripts/solver/accuracy_test.py O3148                       # 2011 World Cup final, levels 1, 2, 3a, 3b
  python3 scripts/solver/accuracy_test.py O3148 --levels 2,3a,3b      # skip level 1 (the slow one)
  python3 scripts/solver/accuracy_test.py O3148 --time 600 --time-level1 1800

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
import argparse, collections, csv, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cricket_facts as F
import db_load as L
import rebuild as R

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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match")
    ap.add_argument("--levels", default="1,2,3a,3b")
    ap.add_argument("--innings", default="1,2")
    ap.add_argument("--db", default=os.path.join(R.ROOT, "data", "everyball.db"))
    ap.add_argument("--time", type=float, default=600, help="seconds per innings, levels 2/3a/3b")
    ap.add_argument("--time-level1", type=float, default=1800, help="seconds per innings, level 1")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()
    if R.BACKEND != "ortools":
        print("NOTE: OR-Tools is not installed (pip install ortools) - using the slow test stand-in.")
    c = L.connect(a.db)
    names = dict(c.execute("select player_id, name from players"))
    prior = R.load_prior(c)
    info = L.match_info(c, a.match)
    out = os.path.join(R.ROOT, "data", "solver", "accuracy", a.match)
    os.makedirs(out, exist_ok=True)
    report = {"match": a.match, "date": info["day"], "teams": f"{info['team1']} v {info['team2']}", "backend": R.BACKEND,
              "workers": a.workers, "results": []}
    jpath = os.path.join(out, "report.json")
    for lv in a.levels.split(","):
        for inn in [int(x) for x in a.innings.split(",")]:
            truth, why = L.true_deliveries(c, a.match, inn)
            if why:
                print(f"innings {inn}: cannot test ({why})"); continue
            facts = F.facts_from_deliveries(truth, info["day"], info["bpo"], lv)
            probs = F.replay(truth, facts)
            if probs:
                print(f"innings {inn}: the real balls break a modelled rule ({probs[0]}) - skipped"); continue
            limit = a.time_level1 if lv == "1" else a.time
            print(f"{NAMES[lv]}, innings {inn}: solving (up to {int(limit)} s, {a.workers} workers)...", flush=True)
            t0 = time.time()
            rb = R.Rebuilder(facts, prior).build()
            sol = rb.solve(limit, a.workers)
            secs = round(time.time() - t0)
            entry = {"level": lv, "innings": inn, "status": rb.status, "seconds": secs}
            if sol is None:
                entry["note"] = "no answer found in the time given"
                report["results"].append(entry); print(f"  {rb.status} after {secs} s - no answer"); continue
            res, rows = strict(sol, truth, facts, names)
            left = F.replay(sol, facts)
            entry.update(res, facts_broken=[f"{n} (off by {v})" for n, v in rb.broken], rule_problems=len(left))
            report["results"].append(entry)
            sheet = os.path.join(out, f"level{lv}_innings{inn}.csv")
            with open(sheet, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["ball", "result", "bowler (real)", "bowler (rebuilt)", "striker (real)", "striker (rebuilt)",
                            "real", "rebuilt", "given as a fact", "what is wrong"])
                w.writerows(rows)
            al, hd = res["all"], res["hidden"]
            print(f"  {rb.status} in {secs} s | EXACTLY RIGHT {al['exactly_right_pct']}% of {al['deliveries']} deliveries "
                  f"| runs {al['runs_right_pct']}% (every-ball-a-dot guess {al['every_ball_a_dot_guess_runs_pct']}%) "
                  f"| striker {al['striker_right_pct']}% | bowler {al['bowler_right_pct']}% "
                  f"| wickets on the right ball {res['wickets']['on_the_right_ball']}/{res['wickets']['real']}"
                  + (f" | not-given balls exactly right {hd['exactly_right_pct']}%" if hd["deliveries"] != al["deliveries"] else "")
                  + f" | facts broken {len(rb.broken)}", flush=True)
            json.dump(report, open(jpath, "w"), indent=1)
    json.dump(report, open(jpath, "w"), indent=1)
    # the readable report
    md = [f"# Rebuild accuracy: {report['teams']}, {report['date']} ({a.match})", "",
          "Each innings was hidden and rebuilt from the facts of one level, then compared delivery by delivery. "
          "A ball is **exactly right** only if bowler, striker, non-striker, runs off the bat, the extra and the wicket "
          "all match. Wrong-length overs count every extra or missing delivery as wrong.", "",
          "| Level | Inn | Solver | Exactly right | Runs right | Striker | Bowler | Wickets on right ball | Not-given balls exactly right | Every-ball-a-dot guess (runs) | Batter v bowler pairs exact | Facts broken |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in report["results"]:
        if "all" not in e:
            md.append(f"| {e['level']} | {e['innings']} | {e['status']} {e['seconds']} s | no answer | | | | | | | | |"); continue
        al, hd, bv = e["all"], e["hidden"], e["batter_v_bowler"]
        md.append(f"| {e['level']} | {e['innings']} | {e['status']} {e['seconds']} s | **{al['exactly_right_pct']}%** of {al['deliveries']} "
                  f"| {al['runs_right_pct']}% | {al['striker_right_pct']}% | {al['bowler_right_pct']}% "
                  f"| {e['wickets']['on_the_right_ball']} of {e['wickets']['real']} | {hd['exactly_right_pct']}% of {hd['deliveries']} "
                  f"| {al['every_ball_a_dot_guess_runs_pct']}% | {bv['balls_and_runs_exact']} of {bv['pairs']} | {len(e['facts_broken'])} |")
    md += ["", "Levels: " + "; ".join(f"**{k}** = {v.split(' - ')[1]}" for k, v in NAMES.items()) + ".",
           "Side-by-side sheets (every ball, real v rebuilt, wrong ones marked): `level<L>_innings<I>.csv` in this folder."]
    open(os.path.join(out, "report.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"\nReport: {os.path.relpath(os.path.join(out, 'report.md'), R.ROOT)}")


if __name__ == "__main__":
    main()
