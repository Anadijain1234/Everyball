"""
Reader for the old ESPNcricinfo archive (data/raw/espn/ARCHIVE/...), about 1990s to 2002:
  <match>.html               scorecard (batting with minutes/balls/4s/6s, bowling, extras, totals, toss, umpires)
  <match>_BBB-COMMS.html     ball-by-ball commentary from October 1998 ("0.1 Fleming to Campbell, no run, ...")
  <match>_PLYR-V-PLYR.html   batter-vs-bowler tables per innings (0s, 1s, 2s, 3s, 4s, runs, balls)

Each file is matched to a numbered Test/ODI already in the database (from Davis, Kaggle or Cricsheet) by its date
and teams. Files that match nothing (domestic, tour, Under-19 and women's games) are skipped, which keeps only men's
internationals. Ball-by-ball is only stored for matches that Cricsheet doesn't already cover ball by ball.
"""
import html as H
import re
from collections import defaultdict
from datetime import date, timedelta

import common as C

MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
DISMISSAL_START = re.compile(r"\s(?=(?:c\s|c&b\s|c & b\s|b\s|lbw\s|st\s|run out|not out|retired|hit wicket|handled|"
                             r"obstructing|absent|timed out)\b)")
WORDS = {"no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def html_lines(path):
    s = path.read_text(encoding="latin-1")
    s = re.sub(r"<(script|style).*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</tr>|</h\d>|<pre>|</pre>|</li>|</td>\s*</tr>", "\n", s, flags=re.I)
    t = H.unescape(re.sub(r"<[^>]+>", " ", s))
    return [x for x in (re.sub(r"[ \t\xa0]+", " ", y).strip() for y in t.splitlines()) if x]


def file_date(stem):
    """Start date from names like AUS_WI_ODI1_11APR1999, ENG_AUS_T1_05-09JUL2001, PAK_IND_T1_28JAN-01FEB1999."""
    m = re.search(r"_(\d{2})([A-Z]{3})(\d{4})-\d{2}[A-Z]{3}\d{4}$", stem)
    if m:
        return date(int(m[3]), MON[m[2]], int(m[1]))
    m = re.search(r"_(\d{2})([A-Z]{3})-\d{2}([A-Z]{3})(\d{4})$", stem)
    if m:
        y = int(m[4]) - (1 if MON[m[2]] > MON[m[3]] else 0)
        return date(y, MON[m[2]], int(m[1]))
    m = re.search(r"_(\d{2})(?:-\d{2})?([A-Z]{3})(\d{4})$", stem)
    if m and m[2] in MON:
        try:
            return date(int(m[3]), MON[m[2]], int(m[1]))
        except ValueError:
            return None
    return None


def clean_name(n):
    return C.person(re.sub(r"^[\*\+]+|[\*\+†]+$", "", n.strip()))


# ---------------------------------------------------------------- scorecard
TITLE = re.compile(r"^(?P<label>[^:]{2,60}):\s*(?P<t1>.+?) v (?P<t2>.+?) at (?P<ground>.+?),\s*(?P<when>.+)$")
INN = re.compile(r"^(?P<team>[A-Z][A-Za-z\. ]+?)\s+(?:(?P<n>\d)(?:st|nd|rd|th)\s+)?innings\b(?P<rest>.*)$", re.I)


def parse_scorecard(lines):
    out = {"innings": []}
    for l in lines[:40]:
        m = TITLE.match(l)
        if m:
            out.update(label=m["label"], team1=C.team(m["t1"]), team2=C.team(m["t2"]), ground=m["ground"])
            break
    for l in lines:
        for key, rx in (("result", r"^Result:\s*(.+)$"), ("toss", r"^Toss:\s*(.+)$"), ("umpires", r"^Umpires:\s*(.+)$")):
            m = re.match(rx, l)
            if m and key not in out:
                out[key] = m.group(1).strip()
    inn, mode = None, None
    for l in lines:
        h = INN.match(l)
        if h and re.search(r"\bR\b", h["rest"]):
            cols = [c for c in h["rest"].split() if c in ("R", "M", "B", "4", "6")]
            inn = {"team": C.team(h["team"]), "cols": cols, "batting": [], "bowling": [], "dnb": []}
            out["innings"].append(inn)
            mode = "bat"
            continue
        if inn is None:
            continue
        if l.startswith("Bowling"):
            mode = "bowl"
            continue
        if l.startswith(("FoW", "Fall of wickets")):
            mode = "fow"
            continue
        if mode == "bat":
            if l.startswith("Extras"):
                m = re.search(r"(\d+)\s*$", l)
                inn["extras"] = int(m.group(1)) if m else None
                continue
            if l.startswith("Total"):
                m = re.match(r"^Total\s*\((?P<d>.*?)\)\s*(?P<r>\d+)", l)
                if m:
                    d = m["d"].lower()
                    wk = re.search(r"(\d+)\s*wkts?|(\d+)\s*wickets?", d)
                    ov = re.search(r"([\d.]+)\s*overs", d)
                    inn.update(runs=int(m["r"]), declared=int("dec" in d), overs=ov.group(1) if ov else None,
                               wickets=10 if "all out" in d else (int(wk.group(1) or wk.group(2)) if wk else None))
                continue
            if l.lower().startswith(("did not bat", "yet to bat", "dnb")):
                names = l.split(":", 1)[-1]
                inn["dnb"] += [clean_name(x) for x in names.split(",") if x.strip()]
                continue
            nums = re.findall(r"\s(\d+|-)(?=\s|$)", " " + l)
            k = len(inn["cols"])
            if len(nums) < k or k == 0:
                continue
            head = re.sub(r"(\s+(\d+|-)){%d}\s*$" % k, "", l)
            parts = DISMISSAL_START.split(head, maxsplit=1)
            if len(parts) != 2:
                continue
            vals = dict(zip(inn["cols"], [None if v == "-" else int(v) for v in nums[-k:]]))
            how = parts[1].strip()
            low = how.lower()
            kind = ("not out" if low.startswith("not out") else "run out" if low.startswith("run out")
                    else "lbw" if low.startswith("lbw") else "stumped" if low.startswith("st ")
                    else "caught" if low.startswith(("c ", "c&b", "c & b")) else "bowled" if low.startswith("b ")
                    else "retired" if low.startswith("retired") else how.split()[0])
            bm = re.search(r"(?:^|\s)b\s+(.+)$", how)
            fm = re.match(r"^(?:c|st)\s+(.+?)\s+b\s", how)
            inn["batting"].append({"name": clean_name(parts[0]), "how": how, "kind": kind,
                                   "bowler": bm.group(1).strip() if bm and kind != "run out" else None,
                                   "fielder": fm.group(1) if fm else None, "runs": vals.get("R"), "minutes": vals.get("M"),
                                   "balls": vals.get("B"), "fours": vals.get("4"), "sixes": vals.get("6"),
                                   "not_out": int(kind in ("not out", "retired"))})
        elif mode == "bowl":
            m = re.match(r"^(?P<n>[A-Za-z][A-Za-z'\-\. ]*?)\s+(?P<o>\d+(?:\.\d)?)\s+(?P<m>\d+)\s+(?P<r>\d+)\s+(?P<w>\d+)\b", l)
            if m:
                inn["bowling"].append({"name": clean_name(m["n"]), "overs": m["o"], "maidens": int(m["m"]),
                                       "runs": int(m["r"]), "wickets": int(m["w"])})
    return out


# ---------------------------------------------------------------- ball-by-ball commentary
BALL = re.compile(r"^(\d+)\.(\d+)\s+(.+?) to (.+?),\s*(.*)$")


def outcome(text):
    """'two runs, drives...' -> (bat_runs, extras, extra_type, legal, wicket, description)."""
    t = text.strip()
    et, legal = None, 1
    low = t.lower()
    for pre, code in (("(noball)", "nb"), ("no ball:", "nb"), ("noball:", "nb"), ("wides:", "wd"), ("wide:", "wd"),
                      ("legbyes:", "lb"), ("legbye:", "lb"), ("leg byes:", "lb"), ("leg bye:", "lb"), ("byes:", "b"),
                      ("bye:", "b")):
        if low.startswith(pre):
            et, t, low = code, t[len(pre):].strip(), low[len(pre):].strip()
            break
    if et in ("nb", "wd"):
        legal = 0
    wicket = int(low.startswith("out"))
    runs = 0
    m = re.match(r"^(no|one|two|three|four|five|six|seven|\d+)\s+runs?\b", low)
    if m:
        runs = WORDS.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else 0)
    elif low.startswith("four"):
        runs = 4
    elif low.startswith("six"):
        runs = 6
    desc = re.sub(r"^(out\b:?|four\b,?|six\b,?|(no|one|two|three|four|five|six|seven|\d+)\s+runs?\b,?)\s*", "", t,
                  flags=re.I).strip()
    if et == "wd":
        return 0, 1 + runs, "wd", 0, wicket, desc
    if et == "nb":
        return runs, 1, "nb", 0, wicket, desc
    if et in ("lb", "b"):
        return 0, runs, et, 1, wicket, desc
    return runs, 0, None, legal, wicket, desc


def parse_bbb(lines):
    innings, cur, last_over = [], None, -1
    for l in lines:
        m = BALL.match(l)
        if not m:
            e = re.match(r"^End of over \d+.*?\)?\s*([A-Z][A-Za-z\. ]+?)\s+\d+/\d+", l)
            if e and cur is not None and not cur.get("team"):
                cur["team"] = C.team(e.group(1))
            continue
        over = int(m.group(1))
        if cur is None or over < last_over - 1:
            cur = {"team": None, "balls": []}
            innings.append(cur)
        last_over = over
        bat, ext, et, legal, wk, desc = outcome(m.group(5))
        cur["balls"].append({"over": over + 1, "ball": int(m.group(2)), "bowler": m.group(3).strip(),
                             "batter": m.group(4).strip(), "bat_runs": bat, "extras": ext, "extra_type": et,
                             "legal": legal, "wicket": wk, "text": desc})
    return innings


# ---------------------------------------------------------------- player v player
def parse_pvp(lines):
    out, inn, batter, cols, in_batsmen = [], 0, None, None, False
    for l in lines:
        if re.match(r"^\d(?:st|nd|rd|th) innings$", l, re.I):
            inn += 1
            in_batsmen = False
            continue
        if l.lower() == "batsmen":
            in_batsmen = True
            continue
        if l.lower() in ("bowlers", "bowler"):
            in_batsmen = False
            continue
        if not in_batsmen:
            continue
        h = re.match(r"^(?P<n>.+?)\s+(?P<c>0s\s.*)$", l)
        if h:
            batter, cols = clean_name(h["n"]), h["c"].split()
            continue
        v = re.match(r"^v\s+(?P<n>.+?)\s+(?P<nums>[\d.\s]+)$", l)
        if v and batter and cols:
            vals = dict(zip(cols, v["nums"].split()))
            num = lambda k: int(float(vals[k])) if k in vals else 0  # noqa: E731
            out.append({"inn": inn, "batter": batter, "bowler": clean_name(v["n"]), "dots": num("0s"), "ones": num("1s"),
                        "twos": num("2s"), "threes": num("3s"), "fours": num("4s"), "sixes": num("6s"),
                        "runs": num("Runs"), "balls": num("Balls")})
    return out


# ---------------------------------------------------------------- loading into the database
def run(con, say):
    root = C.RAW / "espn" / "ARCHIVE"
    if not root.exists():
        say("ESPN: data/raw/espn/ARCHIVE not found, skipping")
        return
    C.clear_source(con, "espn")
    reg = C.Registry(con)
    index = defaultdict(list)
    for mid, fmt, d, t1, t2 in con.execute("SELECT match_id, format, start_date, team1, team2 FROM matches"):
        if d:
            index[(fmt, d)].append(({t1, t2}, mid))
    has_cricsheet_balls = {r[0] for r in con.execute("SELECT DISTINCT match_id FROM balls WHERE source='cricsheet'")}
    groups = defaultdict(dict)
    for p in root.rglob("*.htm*"):
        stem = re.sub(r"\.html?$", "", p.name)
        kind = "bbb" if stem.endswith("_BBB-COMMS") else "pvp" if stem.endswith("_PLYR-V-PLYR") else "card"
        base = re.sub(r"_(BBB-COMMS|PLYR-V-PLYR)$", "", stem)
        groups[base][kind] = p
    stats = defaultdict(int)
    for base, files in sorted(groups.items()):
        fmt = "ODI" if re.search(r"_ODI", base) else "Test" if re.search(r"_T\d", base) else None
        d = file_date(base)
        card = parse_scorecard(html_lines(files["card"])) if "card" in files else {"innings": []}
        teams = {card.get("team1"), card.get("team2")} - {None}
        if len(teams) < 2:                       # no scorecard: the teams are in the file name (AUS_WI_ODI1_...)
            teams = {C.TEAMS.get(c.lower()) for c in base.split("_")[:2]} - {None}
        mid = None
        if fmt and d and len(teams) == 2:
            for delta in (0, 1, -1, 2, -2):
                hits = [cand for tset, cand in index.get((fmt, (d + timedelta(days=delta)).isoformat()), [])
                        if tset == teams]
                if len(hits) == 1:
                    mid = hits[0]
                    break
        if not mid:
            stats["skipped (not a numbered men's international)"] += 1
            continue
        stats["matched"] += 1
        load_match(con, reg, mid, card, files, has_cricsheet_balls, stats)
        if stats["matched"] % 300 == 0:
            con.commit()
            say(f"   ...{stats['matched']} ESPN matches loaded")
    con.commit()
    C.finish(con)
    say("ESPN: " + ", ".join(f"{k} {v:,}" for k, v in stats.items()))
    C.log(con, "espn", "ARCHIVE", "ok", dict(stats))


def load_match(con, reg, mid, card, files, has_cricsheet_balls, stats):
    teams = list(con.execute("SELECT team1, team2 FROM matches WHERE match_id = ?", (mid,)).fetchone())
    # which team batted in each innings: ESPN's scorecard, else the card already in the database
    inn_team = {i: t for i, t in con.execute("SELECT inn, team FROM innings WHERE match_id = ? ORDER BY source", (mid,))}
    inn_team.update({i: inn["team"] for i, inn in enumerate(card["innings"], 1)})
    C.upsert_match(con, mid, "espn", toss=C.team(card.get("toss")), umpires=card.get("umpires"))
    # who is who in this match: names other sources already use (so 'SL Campbell' joins up with the same person)
    known = defaultdict(list)
    for name, full, tm in con.execute("""SELECT p.name, p.full_name, p.team FROM players p WHERE p.player_id IN
                                   (SELECT player_id FROM batting WHERE match_id = ? UNION
                                    SELECT player_id FROM bowling WHERE match_id = ?)""", (mid, mid)):
        known[tm].append((name, full))

    def pick(name, tm):
        if not name or not tm:
            return None
        n = clean_name(name)
        return reg.get(C.same_person(n, known[tm]) or n, tm, "espn")

    rosters = defaultdict(set)
    for inn in card["innings"]:
        inn["bowl_team"] = next((t for t in teams if t and t != inn["team"]), None)
        rosters[inn["team"]].update([b["name"] for b in inn["batting"]] + inn["dnb"])
    # a scorecard is only used if every innings adds up (batters' runs + extras = total) and has its bowling
    card_ok = bool(card["innings"]) and all(
        inn.get("runs") is not None and inn["bowling"] and inn.get("extras") is not None
        and sum(b["runs"] or 0 for b in inn["batting"]) + inn["extras"] == inn["runs"] for inn in card["innings"])
    if card["innings"] and not card_ok:
        stats["scorecards not adding up (other source's card kept)"] += 1
    for i, inn in enumerate(card["innings"] if card_ok else [], 1):
        con.execute("INSERT INTO innings VALUES(?,?,?,?,?,?,?,?,?)", (mid, i, inn["team"], inn.get("runs"), inn.get("wickets"),
                                                                     inn.get("overs"), inn.get("extras"),
                                                                     inn.get("declared"), "espn"))
    for i, inn in enumerate(card["innings"] if card_ok else [], 1):
        bat_t, bowl_t = inn["team"], inn["bowl_team"]
        for pos, b in enumerate(inn["batting"], 1):
            bowler = resolve_short(b["bowler"], rosters[bowl_t]) if b["bowler"] else None
            con.execute("INSERT INTO batting VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, i, pos, pick(b["name"], bat_t), bat_t, b["how"], b["kind"], pick(bowler, bowl_t),
                         b["fielder"], b["runs"], b["balls"], b["minutes"], b["fours"], b["sixes"], b["not_out"], None,
                         None, "espn"))
        for w in inn["bowling"]:
            full = resolve_short(w["name"], rosters[bowl_t])
            con.execute("INSERT INTO bowling VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (mid, i, pick(full, bowl_t), bowl_t, w["overs"], C.overs_to_balls(w["overs"]), w["maidens"],
                         w["runs"], w["wickets"], "espn"))
    stats["scorecards"] += card_ok
    if "pvp" in files:
        rows = parse_pvp(html_lines(files["pvp"]))
        for r in rows:
            bat_t = inn_team.get(r["inn"])
            bowl_t = next((t for t in teams if t and t != bat_t), None)
            con.execute("INSERT INTO pvp VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, r["inn"], pick(r["batter"], bat_t), pick(resolve_short(r["bowler"], rosters[bowl_t]), bowl_t),
                         r["dots"], r["ones"], r["twos"], r["threes"], r["fours"], r["sixes"], r["runs"], r["balls"], "espn"))
        stats["player-v-player tables"] += bool(rows)
    if "bbb" in files and mid not in has_cricsheet_balls:
        inns = parse_bbb(html_lines(files["bbb"]))
        rows, comm = [], []
        for i, inn in enumerate(inns, 1):
            bat_t = inn_team.get(i) or inn["team"]
            bowl_t = next((t for t in teams if t and t != bat_t), None)
            for seq, b in enumerate(inn["balls"], 1):
                batter = pick(resolve_short(b["batter"], rosters[bat_t]), bat_t)
                rows.append((mid, i, seq, b["over"], b["ball"], pick(resolve_short(b["bowler"], rosters[bowl_t]), bowl_t),
                             batter, None, b["bat_runs"], b["extras"], b["extra_type"], b["legal"], b["wicket"], None,
                             batter if b["wicket"] else None, 1, "espn"))
                if b["text"]:
                    comm.append((mid, i, seq, b["text"], "espn"))
        con.executemany("INSERT INTO balls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.executemany("INSERT INTO commentary VALUES(?,?,?,?,?)", comm)
        stats["ball-by-ball matches"] += bool(rows)
        stats["balls"] += len(rows)


def resolve_short(name, roster):
    """'McGrath' -> 'GD McGrath', using the names on the same scorecard."""
    if not name:
        return None
    n = clean_name(name)
    if n in roster:
        return n
    hits = [r for r in roster if C.surname(r) == n or r.split()[-1] == n or r.endswith(" " + n)]
    return hits[0] if len(hits) == 1 else n
