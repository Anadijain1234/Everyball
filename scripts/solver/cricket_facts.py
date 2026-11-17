"""Facts about one innings + a replay checker. Pure Python (no solver needed).

A "delivery" (what the solver produces and what the checker replays) is a dict:
  over (0-based), bowler, striker, non_striker (player ids), bat (runs off the bat 0-6),
  kind: "" (normal legal ball) | "b" | "lb" (legal, byes / leg-byes) | "wd" | "nb",
  ran (runs physically run that were NOT off the bat: byes / leg-byes / wides run / no-ball byes),
  ex_w, ex_nb, ex_b, ex_lb (extras runs of each kind on this delivery, as a scorecard counts them),
  legal (bool), out (player id or None), how (dismissal kind or None)

"Facts" (dict) = everything known without ball-by-ball: scorecard, bowler figures, extras, fall of wickets,
partnerships, optional anchors (balls seen on video) and optional over-by-over (bowler + runs per over).
Any fact may be None = unknown.
"""
import collections, os, sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "video"))
try:
    import rules as R        # scripts/video/rules.py (era-aware Laws)
except Exception:            # pragma: no cover
    R = None

CREDITED = ("bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket")
ON_LEGAL_ONLY = ("bowled", "caught", "caught and bowled", "lbw")          # cannot happen off a wide / no-ball
ON_WIDE_OK = ("stumped", "hit wicket", "run out", "obstructing the field")
ON_NB_OK = ("run out", "obstructing the field", "handled the ball", "hit the ball twice")
NO_DELIVERY = ("retired hurt", "retired not out", "retired out", "timed out", "retired")


def era_flags(day, bpo):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    if R is not None:
        e = R.era(d, "ODI", "")
        return {"bpo": bpo, "pen_plus": e["penalty_plus_runs"], "nb_byes_are_byes": e["byes_off_nb_are_byes"],
                "to_bowler": e["extras_to_bowler"], "caught_new_on_strike": e["caught_new_on_strike"]}
    return {"bpo": bpo, "pen_plus": d >= date(1998, 9, 30), "nb_byes_are_byes": d >= date(2017, 10, 1),
            "to_bowler": d >= date(1983, 6, 13), "caught_new_on_strike": d >= date(2022, 10, 1)}


def norm_kind(k):
    k = (k or "").strip().lower()
    return {"c&b": "caught and bowled", "st": "stumped", "hw": "hit wicket", "ro": "run out",
            "obstructing": "obstructing the field", "handled": "handled the ball"}.get(k, k)


def conceded(d, era):
    return d["bat"] + ((d["ex_w"] + d["ex_nb"]) if era["to_bowler"] else 0)


def team_runs(d):
    return d["bat"] + d["ex_w"] + d["ex_nb"] + d["ex_b"] + d["ex_lb"]


def ran_parity(d):
    """Runs actually run (decides whether the batters crossed): boundaries are not run."""
    if d["bat"] in (4, 6):
        return 0
    if d["bat"] == 0 and d["ran"] == 4:
        return 0
    return (d["bat"] + d["ran"]) % 2


# ----------------------------------------------------------------------------------------------- facts
def facts_from_deliveries(dl, day, bpo, level="card", order=None):
    """Make the facts a scorecard (+ highlights / + over-by-over) would give, from a true delivery list.
    level: card | highlights (card + every boundary and wicket ball seen) | overs (highlights + bowler and
    runs of every over)."""
    era = era_flags(day, bpo)
    if order is None:
        order = []
        for d in dl:
            for p in (d["striker"], d["non_striker"]):
                if p not in order:
                    order.append(p)
    bat = {p: {"pid": p, "R": 0, "B": 0, "4": 0, "6": 0, "how": None, "bowler": None} for p in order}
    bowl = collections.OrderedDict()
    ex = {"b": 0, "lb": 0, "wd": 0, "nb": 0}
    fow, parts = [], []
    overs = collections.OrderedDict()
    lidx = collections.Counter()
    cum, legal_total = 0, 0
    part = {"runs": 0, "balls": 0, "pr": collections.Counter(), "pb": collections.Counter()}
    anchors = []
    for d in dl:
        o = d["over"]
        b = bowl.setdefault(d["bowler"], {"pid": d["bowler"], "balls": 0, "runs": 0, "maidens": 0, "wkts": 0,
                                           "wd": 0, "nb": 0, "dots": 0, "4": 0, "6": 0})
        ov = overs.setdefault(o, {"over": o, "bowler": d["bowler"], "runs": 0, "conc": 0, "legal": 0})
        s = bat[d["striker"]]
        s["R"] += d["bat"]
        if d["kind"] != "wd":
            s["B"] += 1; part["balls"] += 1; part["pb"][d["striker"]] += 1
        s["4"] += d["bat"] == 4; s["6"] += d["bat"] == 6
        part["pr"][d["striker"]] += d["bat"]
        c = conceded(d, era)
        b["runs"] += c; b["4"] += d["bat"] == 4; b["6"] += d["bat"] == 6
        b["wd"] += d["kind"] == "wd"; b["nb"] += d["kind"] == "nb"
        if d["legal"]:
            b["balls"] += 1; legal_total += 1; lidx[o] += 1; ov["legal"] += 1
            b["dots"] += c == 0
            if level in ("highlights", "overs") and d["bat"] in (4, 6):
                anchors.append({"over": o, "ball": lidx[o], "bat": d["bat"], "striker": d["striker"], "bowler": d["bowler"]})
        for k in ("b", "lb"):
            ex[k] += d["ex_" + k]
        ex["wd"] += d["ex_w"]; ex["nb"] += d["ex_nb"]
        r = team_runs(d); cum += r; part["runs"] += r
        ov["runs"] += r; ov["conc"] += c
        if d["out"]:
            how = norm_kind(d["how"])
            bat[d["out"]]["how"] = how
            if how in CREDITED:
                bat[d["out"]]["bowler"] = d["bowler"]; b["wkts"] += 1
            fow.append({"out": d["out"], "how": how, "bowler": d["bowler"] if how in CREDITED else None,
                        "score": cum, "legal": legal_total})
            parts.append(part)
            part = {"runs": 0, "balls": 0, "pr": collections.Counter(), "pb": collections.Counter()}
    parts.append(part)
    for o, ov in overs.items():
        if ov["legal"] == bpo and ov["conc"] == 0:
            bowl[ov["bowler"]]["maidens"] += 1
    pships = [{"runs": p["runs"], "balls": p["balls"], "runs_by": dict(p["pr"]), "balls_by": dict(p["pb"])} for p in parts]
    f = {"day": str(day), "bpo": bpo, "era": era, "order": order, "legal_total": legal_total,
         "total": cum, "pen": 0, "batters": [bat[p] for p in order], "bowlers": list(bowl.values()),
         "extras": ex, "fow": fow, "partnerships": pships, "anchors": anchors, "overs": None}
    if level == "overs":
        f["overs"] = [{"over": o, "bowler": ov["bowler"], "runs": ov["runs"]} for o, ov in overs.items()]
    return f


# ----------------------------------------------------------------------------------------------- checker
def replay(dl, facts, check_strike=True):
    """Return a list of problems: every fact that the deliveries do not match, and every broken Law."""
    era, bpo, probs = facts["era"], facts["bpo"], []
    got = facts_from_deliveries(dl, facts["day"], bpo, "overs", order=facts["order"])

    def cmp(name, want, have):
        if want is not None and have is not None and want != have:
            probs.append(f"{name}: scorecard {want}, rebuilt {have}")
    gb = {b["pid"]: b for b in got["batters"]}
    for b in facts["batters"]:
        h = gb.get(b["pid"], {"R": 0, "B": 0, "4": 0, "6": 0, "how": None, "bowler": None})
        for k in ("R", "B", "4", "6"):
            cmp(f"batter {b['pid']} {k}", b.get(k), h[k])
        if b.get("how"):
            cmp(f"batter {b['pid']} how out", b["how"], h["how"])
        if b.get("bowler"):
            cmp(f"batter {b['pid']} bowler", b["bowler"], h["bowler"])
    gw = {b["pid"]: b for b in got["bowlers"]}
    for b in facts["bowlers"]:
        h = gw.get(b["pid"], {k: 0 for k in ("balls", "runs", "maidens", "wkts", "wd", "nb", "dots", "4", "6")})
        for k in ("balls", "runs", "maidens", "wkts", "wd", "nb", "dots", "4", "6"):
            cmp(f"bowler {b['pid']} {k}", b.get(k), h[k])
    for k in ("b", "lb", "wd", "nb"):
        cmp(f"extras {k}", (facts["extras"] or {}).get(k), got["extras"][k])
    cmp("total", facts["total"], got["total"] + (facts.get("pen") or 0))
    cmp("legal balls", facts["legal_total"], got["legal_total"])
    if len(facts["fow"]) != len(got["fow"]):
        probs.append(f"wickets: scorecard {len(facts['fow'])}, rebuilt {len(got['fow'])}")
    ordered = any(a.get("score") is not None or a.get("legal") is not None for a in facts["fow"])
    if ordered:
        for i, (a, b) in enumerate(zip(facts["fow"], got["fow"])):
            cmp(f"fow {i + 1} batter", a["out"], b["out"]); cmp(f"fow {i + 1} score", a.get("score"), b["score"])
            cmp(f"fow {i + 1} legal balls", a.get("legal"), b["legal"])
    elif sorted(a["out"] for a in facts["fow"]) != sorted(b["out"] for b in got["fow"]):
        probs.append("out batters differ from the scorecard")
    for i, (a, b) in enumerate(zip(facts.get("partnerships") or [], got["partnerships"])):
        cmp(f"stand {i + 1} runs", a.get("runs"), b["runs"]); cmp(f"stand {i + 1} balls", a.get("balls"), b["balls"])
        for p, v in (a.get("runs_by") or {}).items():
            cmp(f"stand {i + 1} {p} runs", v, b["runs_by"].get(p, 0))
        for p, v in (a.get("balls_by") or {}).items():
            cmp(f"stand {i + 1} {p} balls", v, b["balls_by"].get(p, 0))
    if facts.get("overs"):
        go = {o["over"]: o for o in got["overs"]}
        for o in facts["overs"]:
            h = go.get(o["over"], {})
            cmp(f"over {o['over'] + 1} bowler", o.get("bowler"), h.get("bowler"))
            cmp(f"over {o['over'] + 1} runs", o.get("runs"), h.get("runs"))
    # anchors
    seen = {}
    lc = collections.Counter()
    for d in dl:
        if d["legal"]:
            lc[d["over"]] += 1
            seen[(d["over"], lc[d["over"]])] = d
    for a in facts.get("anchors") or []:
        d = seen.get((a["over"], a["ball"]))
        if not d or d["bat"] != a["bat"] or d["striker"] != a["striker"] or d["bowler"] != a["bowler"]:
            probs.append(f"anchor over {a['over'] + 1} ball {a['ball']} not matched")
    # Laws
    obw = {}
    for d in dl:
        obw.setdefault(d["over"], set()).add(d["bowler"])
    for o, s in obw.items():
        if len(s) > 1:
            probs.append(f"over {o + 1}: more than one bowler")
        if o + 1 in obw and s & obw[o + 1]:
            probs.append(f"overs {o + 1}-{o + 2}: same bowler twice in a row")
    nover = max(obw) + 1 if obw else 0
    for o in range(nover):
        want = bpo if o < nover - 1 else facts["legal_total"] - bpo * (nover - 1)
        if lc[o] != want:
            probs.append(f"over {o + 1}: {lc[o]} legal balls, should be {want}")
    for d in dl:
        h = norm_kind(d["how"]) if d["out"] else None
        if h in ON_LEGAL_ONLY and d["kind"] in ("wd", "nb"):
            probs.append(f"over {d['over'] + 1}: {h} off a {d['kind']}")
        if h == "stumped" and d["kind"] == "nb":
            probs.append(f"over {d['over'] + 1}: stumped off a no-ball")
        if h in CREDITED and d["out"] != d["striker"]:
            probs.append(f"over {d['over'] + 1}: {h} but the out batter was not on strike")
        if d["out"] and d["out"] not in (d["striker"], d["non_striker"]):
            probs.append(f"over {d['over'] + 1}: out batter not at the crease")
        if d["striker"] == d["non_striker"]:
            probs.append(f"over {d['over'] + 1}: same batter at both ends")
    if check_strike:
        probs += strike_problems(dl, facts)
    return probs


def strike_problems(dl, facts):
    """Who should face each ball, from the runs run, wickets and over changes."""
    era, order, probs = facts["era"], facts["order"], []
    if not dl:
        return probs
    s, n = dl[0]["striker"], dl[0]["non_striker"]
    if {s, n} != set(order[:2]):
        probs.append("openers are not the first two in the batting order")
    nxt = 2
    for i, d in enumerate(dl):
        if (d["striker"], d["non_striker"]) != (s, n):
            probs.append(f"ball {i + 1} (over {d['over'] + 1}): striker should be {s}, rebuilt has {d['striker']}")
            s, n = d["striker"], d["non_striker"]
        a, b = (n, s) if ran_parity(d) else (s, n)
        if d["out"]:
            how = norm_kind(d["how"])
            q = order[nxt] if nxt < len(order) else None
            nxt += 1
            if how in ("caught", "caught and bowled"):
                # batters do not run on a catch: if they had crossed (old Laws), the survivor faces next
                surv = n if d["out"] == s else s
                cand = [(q, surv)] if era["caught_new_on_strike"] else [(q, surv), (surv, q)]
            elif how in ("run out", "obstructing the field"):
                surv = n if d["out"] == s else s
                cand = [(q, surv), (surv, q)]
            else:
                cand = [(q, n)]
            nxt_d = dl[i + 1] if i + 1 < len(dl) else None
            if nxt_d is not None:
                eo = nxt_d["over"] != d["over"]
                want = {(x, y) if not eo else (y, x) for x, y in cand}
                if (nxt_d["striker"], nxt_d["non_striker"]) in want:
                    s, n = nxt_d["striker"], nxt_d["non_striker"]
                    continue
                a, b = cand[0]
            else:
                continue
        nxt_d = dl[i + 1] if i + 1 < len(dl) else None
        if nxt_d is not None and nxt_d["over"] != d["over"]:
            a, b = b, a
        s, n = a, b
    return probs
