"""
Reader for the Kaggle ODI dataset (every men's ODI from 1971 to early 2024): match details, batting and bowling
cards. The files are in data/raw/kaggle/odi/ (or inside data/raw/kaggle/odi_scorecards_1971_2024.zip).
Match key: ESPN's ODI number ('ODI Match No' 1 -> 'O0001'), the same key Cricsheet uses.

Players are matched to the names used elsewhere (e.g. 'SR Tendulkar') through their ESPNcricinfo id (Cricsheet's
register), then their full name (Davis's player lists), and only then by building initials from the full name.
"""
import ast
import csv
import io
import zipfile
from collections import defaultdict

import common as C

KINDS = {"stumping": "stumped", "hitwicket": "hit wicket", "DNB": None}


def open_csv(name):
    folder = C.RAW / "kaggle" / "odi"
    p = folder / name
    if p.exists():
        return list(csv.DictReader(p.open(encoding="utf-8")))
    for zp in (C.RAW / "kaggle").glob("*.zip"):
        with zipfile.ZipFile(zp) as z:
            hit = next((n for n in z.namelist() if n.endswith(name)), None)
            if hit:
                return list(csv.DictReader(io.TextIOWrapper(z.open(hit), encoding="utf-8")))
    return []


def i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def initials_name(full):
    parts = (full or "").split()
    if len(parts) < 2:
        return full
    return "".join(p[0].upper() for p in parts[:-1]) + " " + parts[-1]


def run(con, say):
    matches = open_csv("odi_Matches_Data.csv")
    if not matches:
        say("Kaggle: odi_Matches_Data.csv not found, skipping")
        return
    C.clear_source(con, "kaggle")
    reg = C.Registry(con)
    info = {r["player_id"].split(".")[0]: r for r in open_csv("players_info.csv")}
    # 'Saleem Malik' and 'Shoaib Malik' would both become 'S Malik': such players keep their full names
    seen = defaultdict(set)
    for r in info.values():
        seen[initials_name(r.get("player_name"))].add(r["player_id"])
    shared = {n for n, ids in seen.items() if len(ids) > 1}
    by_cricinfo = {str(ci): (n, t) for n, t, ci in con.execute(
        "SELECT name, team, cricinfo_id FROM players WHERE cricinfo_id IS NOT NULL")}
    by_full, by_team = {}, defaultdict(list)
    for n, t, f in con.execute("SELECT name, team, full_name FROM players WHERE sources NOT LIKE '%kaggle%'"):
        if f:
            by_full.setdefault((f.lower(), t), n)
        by_team[t].append((n, f))
    cache = {}

    def pid(kid, team_name):
        kid = str(kid or "").split(".")[0]
        if not kid:
            return None
        key = (kid, team_name)
        if key in cache:
            return cache[key]
        p = info.get(kid, {})
        full = p.get("player_name")
        ci = (p.get("player_object_id") or "").split(".")[0]
        if ci and ci in by_cricinfo:
            name = by_cricinfo[ci][0]
        elif full and (full.lower(), team_name) in by_full:
            name = by_full[(full.lower(), team_name)]
        elif full and C.same_person(full, by_team[team_name]):
            name = C.same_person(full, by_team[team_name])     # 'Mark Waugh' -> Davis's 'ME Waugh'
        else:
            name = initials_name(full)
            name = full if name in shared else name or f"player {kid}"
        cache[key] = reg.get(name, team_name, "kaggle", full_name=full, dob=p.get("dob") or None,
                             batting_style=p.get("batting_style") or None, bowling_style=p.get("bowling_style") or None,
                             cricinfo_id=ci or None)
        return cache[key]

    key_of = {}
    for m in matches:
        num = C.espn_number(con, "ODI", i(m["ODI Match No"]), m["Match Date"], C.team(m["Team1 Name"]),
                            C.team(m["Team2 Name"]))
        if not num:
            continue
        mid = f"O{num:04d}"
        key_of[m["Match ID"]] = (mid, m)
        C.upsert_match(con, mid, "kaggle", format="ODI", number=num, start_date=m["Match Date"],
                       team1=C.team(m["Team1 Name"]), team2=C.team(m["Team2 Name"]), ground=m["Match Venue (Stadium)"],
                       result=m["Match Result Text"] or None,
                       winner=C.team(m["Match Winner"]) if m["Match Winner"] and "no result" not in m["Match Winner"].lower()
                       else None, toss=C.team(m["Toss Winner"]),
                       umpires="; ".join(x for x in (m["Umpire 1"], m["Umpire 2"]) if x) or None,
                       series=m["Series Name"], balls_per_over=6)
    batting = open_csv("odi_Batting_Card.csv")
    first_team = {}
    pos = {}
    n_bat = 0
    for r in batting:
        km = key_of.get(r["Match ID"])
        if not km:
            continue
        if r["wicketType"] == "DNB":                                  # in the XI, didn't bat
            tm = C.team(r["team"])
            con.execute("INSERT INTO played VALUES(?,?,?,?)", (km[0], tm, pid(r["batsman"], tm), "kaggle"))
            continue
        mid, m = km
        inn = i(r["innings"])
        tm = C.team(r["team"])
        opp = C.team(m["Team2 Name"]) if tm == C.team(m["Team1 Name"]) else C.team(m["Team1 Name"])
        first_team.setdefault((mid, inn), tm)
        pos[(mid, inn)] = pos.get((mid, inn), 0) + 1
        kind = KINDS.get(r["wicketType"], r["wicketType"] or None)
        fielders = []
        try:
            fielders = [str(x) for x in ast.literal_eval(r["fielders"])] if r["fielders"] else []
        except (ValueError, SyntaxError):
            pass
        fielder_names = ", ".join(info.get(f, {}).get("player_name", f) for f in fielders) or None
        con.execute("INSERT INTO batting VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, inn, pos[(mid, inn)], pid(r["batsman"], tm), tm, kind, kind,
                     pid(r["bowler"], opp) if r["bowler"] else None, fielder_names, i(r["runs"]), i(r["balls"]), None,
                     i(r["fours"]), i(r["sixes"]), int(r["isOut"] != "True"), None, None, "kaggle"))
        n_bat += 1
    for r in open_csv("odi_Bowling_Card.csv"):
        km = key_of.get(r["Match ID"])
        if not km:
            continue
        mid, m = km
        tm = C.team(r["team"])
        con.execute("INSERT INTO bowling VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (mid, i(r["innings"]), pid(r["bowler id"], tm), tm, r["overs"], i(r["balls"]), i(r["maidens"]),
                     i(r["conceded"]), i(r["wickets"]), "kaggle"))
    # innings totals from the match table (team 1 / team 2 -> innings 1 / 2 by who batted first)
    for mid, m in key_of.values():
        bat1 = first_team.get((mid, 1))
        for n_t in ("Team1", "Team2"):
            tm = C.team(m[f"{n_t} Name"])
            inn = 1 if tm == bat1 else 2
            if i(m[f"{n_t} Runs Scored"]) is None:
                continue
            con.execute("INSERT INTO innings VALUES(?,?,?,?,?,?,?,?,?)",
                        (mid, inn, tm, i(m[f"{n_t} Runs Scored"]), i(m[f"{n_t} Wickets Fell"]), None,
                         i(m[f"{n_t} Extras Rec"]), 0, "kaggle"))
    C.log(con, "kaggle", "odi_Matches_Data.csv", "ok", f"{len(key_of)} ODIs, {n_bat} batting rows")
    con.commit()
    C.finish(con)
    say(f"Kaggle: {len(key_of):,} ODIs, {n_bat:,} batting rows")
