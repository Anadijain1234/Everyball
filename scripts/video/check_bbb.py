"""Replay a ball-by-ball CSV and check it against cricket's scoring rules.

Rules used (ODI, pre-2022 Laws):
- Runs off the bat: an odd number swaps the striker.
- Byes / leg-byes: runs are run, so an odd number swaps the striker; legal ball; counts as a ball faced.
- Wide: 1 penalty run + any runs run (wN = N total). Runs run = N-1; if that is odd the striker swaps.
  Not a legal ball, not a ball faced.
- No-ball: 1 penalty run (nbN = N total) plus any runs off the bat. Bat runs (or byes run off it) that are odd
  swap the striker. Not a legal ball, but it IS a ball faced.
- Boundary 4 / 6: nobody runs, so no swap (all even anyway).
- Every over has exactly 6 legal balls; after the 6th the striker swaps (also after a wicket on the last ball).
- Wicket: bowled / lbw / stumped / hit wicket -> new batter takes strike.
  Caught -> new batter takes strike unless the batters crossed (pre-2022 Law), then the non-striker does.
  Run out -> depends on who is out and at which end; the CSV must say so.
- A line marked 'not shown' is a ball we did not see: it still counts as a legal ball of its over,
  and the strike after it is taken from the next seen line.
"""
import csv, re, sys

def parse_extra(ex):
    ex = (ex or "").strip().lower()
    if ex in ("", "0"):
        return None, 0
    m = re.match(r"(wd|w|nb|lb|b)(\d+)", ex)
    if not m:
        raise ValueError(f"unknown extra: {ex}")
    kind = {"w": "wd"}.get(m.group(1), m.group(1))
    return kind, int(m.group(2))

def replay(rows, striker, non, score=0, wkts=0, next_batters=(), hidden=None):
    hidden = dict(hidden or {})  # runs we know an unseen stretch added, by over
    nxt = list(next_batters)
    legal, faced, runs, problems, totals = {}, {}, {}, [], {}
    for r in rows:
        o = int(r["over"])
        if r["status"] == "dropped":
            continue
        if r["status"] == "not shown":
            legal[o] = legal.get(o, 0) + 1
            score += hidden.pop(o, 0)
            if legal[o] == 6:
                totals[o] = f"{score}/{wkts}"
            striker = None  # unknown until the next seen line
            continue
        if striker is None:  # resume after unseen balls
            striker = r["striker"]
            non = r["non_striker"] or non
            if striker == non:
                problems.append(f"{o}.{r['ball']}: striker and non-striker are the same")
        elif r["striker"] != striker:
            problems.append(f"{o}.{r['ball']}: striker should be {striker}, file says {r['striker']}")
            striker = r["striker"]
        bat = int(r["runs_bat"] or 0)
        kind, n = parse_extra(r["extras"])
        score += bat + n
        is_legal = kind not in ("wd", "nb")
        if kind != "wd":
            faced[striker] = faced.get(striker, 0) + 1
        runs[striker] = runs.get(striker, 0) + bat
        if is_legal:
            legal[o] = legal.get(o, 0) + 1
        ran = bat + (n if kind in ("b", "lb") else 0) + (n - 1 if kind == "wd" else 0)
        w = (r["wicket"] or "none").lower()
        if w != "none":
            wkts += 1
            new = nxt.pop(0) if nxt else "next batter"
            if "run out" in w:
                problems.append(f"{o}.{r['ball']}: run out - check which end by hand")
                striker = new
            elif "caught" in w and "crossed" in (r.get("note") or "").lower():
                striker, non = non, new
            else:
                striker = new
        elif ran % 2:
            striker, non = non, striker
        if is_legal and legal[o] == 6:
            striker, non = non, striker
            totals[o] = f"{score}/{wkts}"
    for o, c in legal.items():
        if c != 6:
            problems.append(f"over {o}: {c} legal balls, should be 6")
    return totals, faced, runs, problems

if __name__ == "__main__":
    rows = list(csv.DictReader(open(sys.argv[1])))
    totals, faced, runs, problems = replay(
        rows, "Jadeja", "Tendulkar", next_batters=["Kambli", "Azharuddin", "Manjrekar"],
        hidden={10: 10, 11: 4, 17: 1})
    print("score after each over:", totals)
    print("balls faced:", faced)
    print("runs off the bat:", runs)
    print("problems:", problems or "none")
