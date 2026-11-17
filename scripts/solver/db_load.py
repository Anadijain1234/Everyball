"""Read innings from data/everyball.db: true deliveries (for tests) and scorecard facts (for real gap-filling)."""
import collections, csv, glob, os, sqlite3

import cricket_facts as F


def connect(path):
    if not os.path.exists(path):
        raise SystemExit(f"Database not found: {path} (build it first: python3 scripts/tools/build_gold.py)")
    return sqlite3.connect(path)


def match_info(c, mid):
    r = c.execute("select start_date, coalesce(balls_per_over, 6), team1, team2, number from matches where match_id=?", (mid,)).fetchone()
    if not r:
        raise SystemExit(f"No match {mid}")
    return {"day": r[0], "bpo": int(r[1] or 6), "team1": r[2], "team2": r[3], "number": r[4]}


def true_deliveries(c, mid, inn):
    """Cricsheet / ESPN balls of one innings as deliveries. Returns (list, reason-if-unusable)."""
    info = match_info(c, mid)
    era = F.era_flags(info["day"], info["bpo"])
    rows = c.execute("select over_no, ball, bowler_id, batter_id, non_striker_id, bat_runs, extras, extra_type, legal,"
                     " wicket, kind, out_id from balls where match_id=? and inn=? order by seq", (mid, inn)).fetchall()
    if not rows:
        return None, "no ball-by-ball"
    dl, why = [], None
    first_over = min(r[0] for r in rows)
    for (ov, ball, bw, st, ns, bat, ex, et, legal, wk, kind, out) in rows:
        et = (et or "").lower()
        ex = ex or 0; bat = bat or 0
        if et in ("e", "?", "pen"):
            why = f"mixed / unknown extras type '{et}'"
        k = F.norm_kind(kind) if wk else None
        if k in F.NO_DELIVERY or (kind and F.norm_kind(kind) in F.NO_DELIVERY):
            why = f"'{kind}' (not a delivery) - not supported"
        d = {"over": ov - first_over, "bowler": bw, "striker": st, "non_striker": ns, "bat": bat, "kind": "",
             "ran": 0, "ex_w": 0, "ex_nb": 0, "ex_b": 0, "ex_lb": 0, "legal": bool(legal),
             "out": out if wk else None, "how": k}
        if et == "wd":
            d["kind"], d["ex_w"] = "wd", ex
            d["ran"] = (ex - 1) if era["pen_plus"] else (ex if ex > 1 else 0)
            d["legal"] = False
        elif et == "nb":
            d["kind"], d["ex_nb"] = "nb", ex
            d["ran"] = (ex - 1) if era["pen_plus"] else (ex if (bat == 0 and ex > 1) else 0)
            d["legal"] = False
        elif et in ("b", "lb"):
            d["kind"], d["ex_" + et], d["ran"] = et, ex, ex
        dl.append(d)
    return dl, why


def facts_from_db(c, mid, inn, kaggle_dir=None):
    """Scorecard facts for a match WITHOUT ball-by-ball (the real use). Uses the gold scorecard; fall of
    wickets, partnerships and bowler wides / no-balls / dots / 4s / 6s come from the Kaggle files if given."""
    info = match_info(c, mid)
    era = F.era_flags(info["day"], info["bpo"])
    bpo = info["bpo"]
    bats = c.execute("select pos, player_id, runs, balls, fours, sixes, not_out, kind, bowler_id from batting_gold "
                     "where match_id=? and inn=? order by pos", (mid, inn)).fetchall()
    batters = []
    for pos, pid, r, b, f4, f6, no, kind, bw in bats:
        k = F.norm_kind(kind)
        if (b in (None, 0)) and not r and (no or not k or k in ("did not bat", "dnb", "absent")):
            continue                                            # did not bat
        batters.append({"pid": pid, "R": r, "B": b or None, "4": f4, "6": f6,
                        "how": None if no else (k or None), "bowler": bw if k in F.CREDITED else None})
    bowl = c.execute("select player_id, balls, maidens, runs, wickets from bowling_gold where match_id=? and inn=?",
                     (mid, inn)).fetchall()
    bowlers = [{"pid": p, "balls": b, "runs": r, "maidens": m, "wkts": w, "wd": None, "nb": None, "dots": None,
                "4": None, "6": None} for p, b, m, r, w in bowl]
    inn_row = c.execute("select runs, wickets, overs, extras from innings where match_id=? and inn=? "
                        "order by case source when 'cricsheet' then 0 when 'espn' then 1 else 2 end", (mid, inn)).fetchone()
    total, wk, overs, extras = inn_row
    legal = sum(b["balls"] or 0 for b in bowlers)
    if overs:
        o, _, p = str(overs).partition(".")
        legal = int(o) * bpo + int(p or 0)
    f = {"day": info["day"], "bpo": bpo, "era": era, "order": [b["pid"] for b in batters], "legal_total": legal,
         "total": total, "pen": 0, "batters": batters, "bowlers": bowlers,
         "extras": {"b": None, "lb": None, "wd": None, "nb": None, "all": extras}, "fow": [], "partnerships": None,
         "anchors": [], "overs": None}
    outs = [b for b in batters if b["how"]]
    f["fow"] = [{"out": b["pid"], "how": b["how"], "bowler": b["bowler"], "score": None, "legal": None} for b in outs]
    if kaggle_dir:
        add_kaggle(c, f, info, inn, kaggle_dir)
    return f


def add_kaggle(c, f, info, inn, kdir):
    """Fall of wickets (score + over), partnerships (runs, balls, each batter's share) and bowler extras
    from the Kaggle ODI files (ESPN ids -> our player ids through players.cricinfo_id)."""
    def path(name):
        g = glob.glob(os.path.join(kdir, "**", name), recursive=True)
        return g[0] if g else None
    mfile = path("odi_Matches_Data.csv")
    if not mfile:
        return
    kid = None
    for r in csv.DictReader(open(mfile, encoding="utf-8")):
        if r["ODI Match No"].isdigit() and int(r["ODI Match No"]) == int(info["number"] or -1):
            kid = r["Match ID"]; break
    if not kid:
        return
    ours = {str(x).replace(".0", ""): p for p, x in c.execute("select player_id, cricinfo_id from players where cricinfo_id is not null")}
    ids = {}
    pinfo = path("players_info.csv")          # Kaggle's own player id -> ESPN object id (= our cricinfo_id)
    if pinfo:
        for r in csv.DictReader(open(pinfo, encoding="utf-8")):
            p = ours.get(str(r.get("player_object_id", "")).replace(".0", ""))
            if p is not None and r.get("player_id"):
                ids[str(r["player_id"]).replace(".0", "")] = p
    num = lambda x: None if x in (None, "", "nan") else float(x)
    bpo = f["bpo"]
    fw = path("odi_Fow_Card.csv")
    if fw:
        rows = [r for r in csv.DictReader(open(fw, encoding="utf-8")) if r["Match ID"] == kid and r["innings"] == str(inn)]
        rows.sort(key=lambda r: num(r["wicket"]) or 0)
        byp = {}
        for r in rows:
            p = ids.get(str(int(num(r["player"]) or 0)))
            ov = num(r["over"])
            legal = None
            if ov is not None:
                o, b = divmod(round(ov * 10), 10)
                legal = int(o) * bpo + int(b)
            byp[p] = {"score": int(num(r["runs"])) if num(r["runs"]) is not None else None, "legal": legal}
        for x in f["fow"]:
            if x["out"] in byp:
                x.update(byp[x["out"]])
        # FoW order is by the time of the fall
        if all(x.get("score") is not None for x in f["fow"]):
            f["fow"].sort(key=lambda x: (x["score"], x.get("legal") or 0))
    pt = path("odi_Partnership_Card.csv")
    if pt:
        rows = [r for r in csv.DictReader(open(pt, encoding="utf-8")) if r["Match ID"] == kid and r["innings"] == str(inn)]
        rows.sort(key=lambda r: num(r["for wicket"]) or 0)
        ps = []
        for r in rows:
            p1, p2 = ids.get(str(int(num(r["player1"]) or 0))), ids.get(str(int(num(r["player2"]) or 0)))
            item = {"runs": int(num(r["partnership runs"])) if num(r["partnership runs"]) is not None else None,
                    "balls": int(num(r["partnership balls"])) if num(r["partnership balls"]) is not None else None,
                    "runs_by": {}, "balls_by": {}}
            for p, kr, kb in ((p1, "player1 runs", "player1 balls"), (p2, "player2 runs", "player2 balls")):
                if p and num(r[kr]) is not None:
                    item["runs_by"][p] = int(num(r[kr]))
                if p and num(r[kb]) is not None:
                    item["balls_by"][p] = int(num(r[kb]))
            ps.append(item)
        if ps and len(ps) in (len(f["fow"]), len(f["fow"]) + 1):
            f["partnerships"] = ps
    bc = path("odi_Bowling_Card.csv")
    if bc:
        byb = {b["pid"]: b for b in f["bowlers"]}
        for r in csv.DictReader(open(bc, encoding="utf-8")):
            if r["Match ID"] != kid or r["innings"] != str(inn):
                continue
            p = ids.get(str(int(num(r["bowler id"]) or 0)))
            if p in byb:
                for k, col in (("wd", "wides"), ("nb", "noballs"), ("dots", "dots"), ("4", "fours"), ("6", "sixes")):
                    v = num(r.get(col))
                    if v is not None:
                        byb[p][k] = int(v)


def odi_innings_with_bbb(c, limit=None, seed=7):
    import random
    rows = c.execute("select b.match_id, b.inn from balls b join matches m using(match_id) where m.format='ODI' "
                     "and b.source='cricsheet' group by b.match_id, b.inn having sum(case when coalesce(b.extra_type,'') in ('e','?','pen') then 1 else 0 end)=0 "
                     "and count(*) > 250").fetchall()
    random.Random(seed).shuffle(rows)
    return rows[:limit] if limit else rows


def prior_table(c):
    """How often each run value happens off the bat, by phase of the innings (from every ODI ball we have)."""
    t = collections.defaultdict(collections.Counter)
    for ov, br, n in c.execute("select over_no, bat_runs, count(*) from balls b join matches m using(match_id) "
                               "where m.format='ODI' and coalesce(extra_type,'') not in ('wd') group by 1, 2"):
        ph = 0 if ov <= 10 else (2 if ov > 40 else 1)
        t[ph][min(int(br or 0), 6)] += n
    out = {}
    for ph, cnt in t.items():
        tot = sum(cnt.values())
        out[ph] = {v: (cnt[v] + 1) / (tot + 7) for v in range(7)}
    return out
