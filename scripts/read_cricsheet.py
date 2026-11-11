"""
Reader for Cricsheet (data/raw/cricsheet/tests_json.zip and odis_json.zip): every ball, as recorded.
Men's internationals only. Each match is keyed by ESPN's own number (Test no. 2230 -> 'T2230', ODI no. 3817 -> 'O3817'),
so it lines up with Davis and with ESPN's lists. Scorecards are worked out from the balls.
"""
import csv
import json
import zipfile
from collections import defaultdict

import common as C

BOWLER_WICKETS = {"bowled", "caught", "lbw", "stumped", "caught and bowled", "hit wicket"}


def cricinfo_ids():
    """Cricsheet's people.csv links its player ids to ESPNcricinfo ids (used to join Kaggle later)."""
    p = C.RAW / "cricsheet" / "people.csv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return {r["identifier"]: r.get("key_cricinfo") or None for r in csv.DictReader(f)}


def result_text(info):
    o = info.get("outcome", {})
    if "winner" in o:
        by = o.get("by", {})
        margin = ", ".join(f"{v} {k}" for k, v in by.items())
        return f"{o['winner']} won by {margin}" if margin else f"{o['winner']} won", o["winner"]
    return (o.get("result") or "").capitalize() or None, None


def load_match(con, reg, d, ids, fmt):
    info = d["info"]
    if info.get("gender") != "male" or info.get("team_type", "international") != "international":
        return 0
    num = info.get("match_type_number")
    if not num:
        return 0
    mid = f"{'T' if fmt == 'Test' else 'O'}{int(num):04d}"
    teams = [C.team(t) for t in info.get("teams", [])]
    result, winner = result_text(info)
    bpo = info.get("balls_per_over", 6)
    C.upsert_match(con, mid, "cricsheet", format=fmt, number=int(num), start_date=(info.get("dates") or [None])[0],
                   season=str(info.get("season", "")), team1=teams[0] if teams else None,
                   team2=teams[1] if len(teams) > 1 else None, ground=info.get("venue"), result=result,
                   winner=C.team(winner) if winner else None, toss=C.team(info.get("toss", {}).get("winner")),
                   umpires="; ".join(info.get("officials", {}).get("umpires", [])) or None, balls_per_over=bpo,
                   series=(info.get("event") or {}).get("name"))
    people = info.get("registry", {}).get("people", {})
    squads = {C.team(t): set(v) for t, v in info.get("players", {}).items()}

    def pid(name, tm):
        cs = people.get(name)
        extra = {"cricsheet_id": cs}
        if cs and ids.get(cs):
            extra["cricinfo_id"] = ids[cs]
        return reg.get(name, tm, "cricsheet", **extra)

    balls = []
    inn_no = 0
    for inn in d.get("innings", []):
        if inn.get("super_over"):
            continue
        inn_no += 1
        bat_team = C.team(inn["team"])
        bowl_team = next((t for t in teams if t != bat_team), None)
        seq, legal_count = 0, 0
        runs = wkts = extras_total = 0
        order, card = [], defaultdict(lambda: {"runs": 0, "balls": 0, "fours": 0, "sixes": 0})
        bowl = defaultdict(lambda: {"balls": 0, "runs": 0, "wickets": 0, "overs": defaultdict(int)})
        how = {}
        for over in inn.get("overs", []):
            for dl in over.get("deliveries", []):
                seq += 1
                ex = dl.get("extras", {})
                legal = int("wides" not in ex and "noballs" not in ex)
                bat_runs = dl["runs"]["batter"]
                extras = dl["runs"]["extras"]
                etype = next(iter(ex), None)
                etype = {"wides": "wd", "noballs": "nb", "byes": "b", "legbyes": "lb", "penalty": "pen"}.get(etype, etype)
                batter, bowler = dl["batter"], dl["bowler"]
                for n in (batter, dl.get("non_striker")):
                    if n and n not in order:
                        order.append(n)
                c = card[batter]
                c["runs"] += bat_runs
                c["balls"] += int("wides" not in ex)
                c["fours"] += int(bat_runs == 4 and not dl["runs"].get("non_boundary"))
                c["sixes"] += int(bat_runs == 6)
                w = bowl[bowler]
                w["balls"] += legal
                conceded = bat_runs + ex.get("wides", 0) + ex.get("noballs", 0)
                w["runs"] += conceded
                w["overs"][over["over"]] += conceded
                wk = dl.get("wickets", [])
                out_name, kind = None, None
                for x in wk:
                    out_name, kind = x["player_out"], x["kind"]
                    if kind not in ("retired hurt", "retired not out"):
                        wkts += 1
                    if kind in BOWLER_WICKETS:
                        w["wickets"] += 1
                    fielders = ", ".join(f.get("name", "") for f in x.get("fielders", []) if f.get("name"))
                    how[out_name] = (kind, bowler if kind in BOWLER_WICKETS else None, fielders or None)
                runs += bat_runs + extras
                extras_total += extras
                over_no = legal_count // bpo + 1
                ball_no = legal_count % bpo + 1
                legal_count += legal
                balls.append((mid, inn_no, seq, over_no, ball_no, pid(bowler, bowl_team), pid(batter, bat_team),
                              pid(dl["non_striker"], bat_team) if dl.get("non_striker") else None,
                              bat_runs, extras, etype, legal, int(bool(wk)), kind,
                              pid(out_name, bat_team) if out_name else None, 1, "cricsheet"))
        overs = f"{legal_count // bpo}" + (f".{legal_count % bpo}" if legal_count % bpo else "")
        declared = int(bool(inn.get("declared")))
        con.execute("INSERT INTO innings VALUES(?,?,?,?,?,?,?,?,?)",
                    (mid, inn_no, bat_team, runs, wkts, overs, extras_total, declared, "cricsheet"))
        for pos, name in enumerate(order, 1):
            c = card[name]
            kind, bowler, fielders = how.get(name, ("not out", None, None))
            con.execute("INSERT INTO batting VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, inn_no, pos, pid(name, bat_team), bat_team, kind, kind,
                         pid(bowler, bowl_team) if bowler else None, fielders, c["runs"], c["balls"], None,
                         c["fours"], c["sixes"], int(name not in how or kind in ("retired hurt", "retired not out")),
                         None, None, "cricsheet"))
        for name, w in bowl.items():
            maidens = sum(1 for o, r in w["overs"].items() if r == 0)
            overs_txt = f"{w['balls'] // bpo}" + (f".{w['balls'] % bpo}" if w["balls"] % bpo else "")
            con.execute("INSERT INTO bowling VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (mid, inn_no, pid(name, bowl_team), bowl_team, overs_txt, w["balls"], maidens, w["runs"],
                         w["wickets"], "cricsheet"))
    con.executemany("INSERT INTO balls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", balls)
    _ = squads
    return len(balls)


def run(con, say):
    C.clear_source(con, "cricsheet")
    reg = C.Registry(con)
    ids = cricinfo_ids()
    for zname, fmt in (("tests_json.zip", "Test"), ("odis_json.zip", "ODI")):
        zp = C.RAW / "cricsheet" / zname
        if not zp.exists():
            say(f"Cricsheet: {zname} not found, skipping")
            continue
        n_matches = n_balls = 0
        with zipfile.ZipFile(zp) as z:
            names = [n for n in z.namelist() if n.endswith(".json")]
            for i, n in enumerate(names, 1):
                try:
                    got = load_match(con, reg, json.loads(z.read(n)), ids, fmt)
                    if got:
                        n_matches += 1
                        n_balls += got
                        C.log(con, "cricsheet", n, "ok", f"{got} balls")
                except Exception as e:  # noqa: BLE001
                    C.log(con, "cricsheet", n, "error", e)
                if i % 500 == 0:
                    con.commit()
                    say(f"   ...{i}/{len(names)} {fmt} files")
        con.commit()
        say(f"Cricsheet {fmt}s: {n_matches} men's matches, {n_balls:,} balls")
    C.finish(con)
