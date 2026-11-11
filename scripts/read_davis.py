"""
Reader for Charles Davis's Test Match Database (the PDFs downloaded into data/raw/davis/zArchive/).

What it reads:
  <series>cov.pdf      series cover: balls per over, captains, whether ball-by-ball is rebuilt from newspapers
  <series><n>.pdf      Test page: match details + full scorecard (batting, bowling, extras, totals)
  <series><n>bbb*.pdf  the whole Test ball by ball, one line per over (or part of an over, when a wicket falls)
  pc/<n>.pdf           players in a series: full name, birthplace, role

How one over line is read (example, 1970-71 Brisbane):
      Snow(4) 1-12   3.....12      6.8  19-1   Chappell 2(15) .....1 32  Stackpole 9(18)
  bowler Snow, his 4th over, 1 wicket for 12 so far; the 8 balls were 3 . . . . . 1 2;
  after 6.8 overs the score is 19 for 1; Chappell has 2 off 15 balls, Stackpole 9 off 18.
  Davis doesn't say which batter faced each ball, so we try both batters as the first striker, run the over
  (odd runs = they swap ends) and keep the order that gives exactly the batters' new totals. If it fits, the
  balls are marked sure=1; if no order fits, they're kept as a best guess with sure=0 and counted in the report.
"""
import itertools
import re
from datetime import datetime

from common import person, surname, team

# ---------------------------------------------------------------- small helpers
NAME = r"(?:[A-Za-z][A-Za-z'\-\.]*\s)*?[A-Za-z][A-Za-z'\-\.]*"
DATE = re.compile(r"(\d{1,2})-([A-Z][a-z]{2})-(\d{4})")


def iso(text):
    m = DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)}-{m.group(2)}-{m.group(3)}", "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def kind_of(how):
    """'cwk Knott b Snow' -> ('caught', 'Knott', 'Snow'); 'lbw b Garrett' -> ('lbw', None, 'Garrett')."""
    h = (how or "").strip()
    low = h.lower()
    bowler = None
    m = re.search(r"(?:^|\s)b\s+(.+)$", h)
    if m and not low.startswith("run out"):
        bowler = m.group(1).strip()
    if low.startswith("not out"):
        return "not out", None, None
    if low.startswith("retired") or low.startswith("ret."):
        return "retired", None, None
    if low.startswith("absent"):
        return "absent", None, None
    if low.startswith("run out"):
        f = re.search(r"\(([^)]*)\)", h)
        return "run out", f.group(1) if f else None, None
    if low.startswith("lbw"):
        return "lbw", None, bowler
    if low.startswith("st "):
        f = re.match(r"st\s+(.+?)\s+b\s", h)
        return "stumped", f.group(1) if f else None, bowler
    if low.startswith("c & b") or low.startswith("c&b"):
        return "caught", bowler, bowler
    if low.startswith("hit wkt") or low.startswith("hit wicket") or low.startswith("hw "):
        return "hit wicket", None, bowler
    if low.startswith("handled"):
        return "handled the ball", None, None
    if low.startswith("obstruct"):
        return "obstructing the field", None, None
    if low.startswith("timed"):
        return "timed out", None, None
    if low.startswith("c"):                       # c Hill b Shaw, cwk Knott b Snow, c-sl Hill b Shaw
        f = re.match(r"c\S*\s+(.+?)\s+b\s", h)
        return "caught", f.group(1) if f else None, bowler
    if low.startswith("b "):
        return "bowled", None, bowler
    return h or None, None, bowler


# ---------------------------------------------------------------- series cover
def parse_cover(text):
    first = next((l for l in text.splitlines() if l.strip()), "")
    bpo = re.search(r"Balls per over:\s*(\d)", text)
    caps = re.search(r"Captains?:\s*(.+)", text)
    return {
        "series": re.split(r"\s{2,}", first.strip())[0] if first else None,
        "balls_per_over": int(bpo.group(1)) if bpo else None,
        "captains": caps.group(1).strip() if caps else None,
        "reconstructed": bool(re.search(r"newspaper|uncertaint", text, re.I)),
    }


# ---------------------------------------------------------------- Test page (scorecard)
HEAD = re.compile(r"^\s*(?P<t1>.+?) v (?P<t2>.+?)\s+(?P<season>\d{4}(?:/\d{2,4})?)\s+(?:(?:(?P<sno>\d+)(?:st|nd|rd|th)|Only)\s+)?Test"
                  r"\s+(?P<venue>.+?)\s+Test:\s*(?P<num>\d+)")
INN_HEAD = re.compile(r"^\s*(?:Test #\s+)?(?P<team>[A-Z][A-Z\.' ]*?[A-Z])\s{2,}(?P<n>\d)(?:st|nd|rd|th) Innings")
BAT = re.compile(r"^\s*(?:(?P<tno>\d[0-9a-z,]*)\s+)?(?P<name>[A-Za-z][A-Za-z'\-\. ]*?[A-Za-z'\.])\s{2,}"
                 r"(?P<how>\S.*?)\s{2,}(?P<runs>\d+)\*?(?:\s{1,8}(?P<mins>\d+|-)(?![\d,]))?(?P<rest>.*)$")
FOURS_SIXES = re.compile(r"^\s+(?P<f>\d+|-)\s*,\s*(?P<s>\d+|-)")
BALLS_FACED = re.compile(r"^\s+(?P<bf>\d+|-)(?![\d\-])")
BOWL = re.compile(r"(?<!\S)(?P<name>" + NAME + r")\s+(?P<ov>\d+(?:\.\d)?)\s+(?P<m>\d+)\s+(?P<r>\d+)\s+(?P<w>\d+)"
                  r"(?:\s+[\d\-]+(?:\s*,\s*[\d\-]+)?)?(?:\s+[\d\-]+)?\s*$")
TOTAL = re.compile(r"^\s*Total\s+(?P<desc>.*?)\s{2,}(?P<runs>\d+)(?:\s|$)")
EXTRAS = re.compile(r"^\s*Extras\s+(?P<desc>.*?)\s{2,}(?P<runs>\d+)(?:\s|$)")


def parse_test_page(text):
    lines = text.splitlines()
    out = {"innings": []}
    for i, line in enumerate(lines[:6]):
        m = HEAD.match(line)
        if m:
            out.update(team1=team(m["t1"]), team2=team(m["t2"]), season=m["season"], series_test_no=int(m["sno"] or 1),
                       ground=m["venue"].strip().rstrip("."), number=int(m["num"]))
            nxt = next((l for l in lines[i + 1:i + 4] if l.strip()), "")
            out["result"] = re.split(r"\s{2,}", nxt.strip())[0].rstrip(".") if nxt else None
            break
    toss = re.search(r"Toss:\s*([A-Za-z][A-Za-z ]*?)(?:\s{2,}|$)", text, re.M)
    ump = re.search(r"Umpires:\s*(.+?)\s*$", text, re.M)
    out["toss"] = team(toss.group(1)) if toss else None
    out["umpires"] = ump.group(1).strip() if ump else None
    out["start_date"] = iso(text)
    inn = None
    for line in lines:
        h = INN_HEAD.match(line)
        if h:
            inn = {"team": team(h["team"]), "team_inn": int(h["n"]), "batting": [], "bowling": [],
                   "bf_end": (line.index(" BF") + 3) if re.search(r"\sBF(\s|$)", line) else None}
            out["innings"].append(inn)
            continue
        if inn is None:
            continue
        b = BOWL.search(line)
        if b and not line.strip().startswith(("Total", "Extras")):
            inn["bowling"].append({"name": person(b["name"]), "overs": b["ov"], "maidens": int(b["m"]),
                                   "runs": int(b["r"]), "wickets": int(b["w"])})
        t = TOTAL.match(line)
        if t:
            d = t["desc"]
            wk = re.search(r"(\d+)\s*(?:wkts?|wickets?)", d)
            ov = re.search(r"([\d.]+)\s*overs", d)
            inn.update(runs=int(t["runs"]), declared=int("dec" in d.lower()), overs=ov.group(1) if ov else None,
                       wickets=10 if "all out" in d.lower() else (int(wk.group(1)) if wk else None))
            continue
        e = EXTRAS.match(line)
        if e:
            inn["extras"] = int(e["runs"])
            continue
        m = BAT.match(line) if len(line) - len(line.lstrip()) < 40 else None
        if m and re.fullmatch(r"[\d.\s\-,]+", m["how"]):
            m = None                            # a bowling-only line, not a batter
        if m and m["name"].strip() not in ("Extras", "Total"):
            kind, fielder, bowler = kind_of(m["how"])
            tno = m["tno"] or ""
            rest, f6, bf = m["rest"], None, None          # 4s,6s and balls faced are blank in some old pages
            fs = FOURS_SIXES.match(rest)
            if fs:
                f6, rest = fs, rest[fs.end():]
            if inn["bf_end"]:                     # balls faced: the number that sits under the "BF" heading
                bf = BALLS_FACED.match(rest)
                if bf and bf.start("bf") > 3 and abs(len(line) - len(rest) + bf.end() - inn["bf_end"]) > 3:
                    bf = None                         # not under the heading: it's the fall-of-wickets column
            inn["batting"].append({
                "name": person(m["name"]), "how": m["how"].strip(), "kind": kind, "fielder": fielder,
                "bowler": bowler, "runs": int(m["runs"]), "minutes": num(m["mins"]),
                "fours": num(f6["f"]) if f6 else None, "sixes": num(f6["s"]) if f6 else None,
                "balls": num(bf["bf"]) if bf else None, "not_out": int(kind in ("not out", "retired", "absent")),
                "captain": int("c" in tno), "keeper": int("w" in tno)})
    # the opponent of each innings
    teams = [out.get("team1"), out.get("team2")]
    for x in out["innings"]:
        x["bowling_team"] = next((t for t in teams if t and t != x["team"]), None)
    return out


# ---------------------------------------------------------------- ball-by-ball
OVS = re.compile(r"(?<=\s)(\d{1,3})\.(\d)\s+(\d{1,4})-(\d{1,2})(?=\s|$)")
BOWLER_TOK = re.compile(r"(?<!\S)(" + NAME + r")\((\d+(?:\.\d)?)\)\s+(\d+)-(\d+)")
BATTER_TOK = re.compile(r"(?<!\S)(" + NAME + r")\s(\d+)\((\d+)\)")
FOW_TOK = re.compile(r"(?<!\S)(\d+)-(\d+)\s+(" + NAME + r")\s\d+\(\d+\)")
FOW_TOK2 = re.compile(r"(?<!\S)(\d+)-(\d+)\s+(" + NAME + r")\s(\d+)\((\d+)\)")
INN_LINE = re.compile(r"^\s*(?P<team>[A-Z][A-Za-z\.' ]*?)\s+(?P<n>\d)(?:st|nd|rd|th) innings(?P<cont>\s*\(cont\.?\))?(?P<rest>.*)$",
                      re.I)
TOKEN = re.compile(r"\[[^\]]*\]|\S")


def batter_tokens(text):
    """'Lawry 1(11)' tokens in the batting columns, leaving out fall-of-wicket notes like '12-1 WM Lawry 4(23)'."""
    return [f for f in BATTER_TOK.finditer(text) if not re.search(r"\d+-\d+\s+$", text[:f.start()])]


def ball_options(tok):
    """All the ways a symbol can be read: (bat_runs, extras, extra_type, legal, faced, wicket_kind)."""
    if tok == ".":
        return [(0, 0, None, 1, 1, None)]
    if tok.isdigit():
        return [(int(tok), 0, None, 1, 1, None)]
    if tok == "W":
        return [(0, 0, None, 1, 1, "out")]
    if tok == "R":
        return [(0, 0, None, 1, 1, "run out")]
    if tok == "n":
        return [(0, 1, "nb", 0, 1, None)]
    if tok == "y":
        return [(0, 1, "wd", 0, 0, None)]
    m = re.fullmatch(r"\[(\d*)\s*,?\s*([A-Za-z]+)(\d*)\]", tok)
    if not m:
        return [(0, 0, "?", 1, 1, None)]
    k = int(m.group(1)) if m.group(1) else 0
    code, tail = m.group(2), m.group(3)
    if code == "B":
        return [(0, k, "b", 1, 1, None)]
    if code == "L":
        return [(0, k, "lb", 1, 1, None)]
    if code == "y":                       # k wides, or a wide that ran/went for k more (k + 1 wides)
        return [(0, k or 1, "wd", 0, 0, None), (0, k + 1, "wd", 0, 0, None)]
    if code == "n":                       # no-ball: k off the bat (before 1980s no extra was added when the
        return [(k, 0, "nb", 0, 1, None),  # batter scored), k off the bat + 1 extra, or k no-ball extras
                (k, 1, "nb", 0, 1, None), (0, k, "nb", 0, 1, None), (max(k - 1, 0), 1, "nb", 0, 1, None)]
    if code == "e":                       # unspecified extras: legal (byes-like) or not (no-ball-like)
        return [(0, k, "e", 1, 1, None), (0, k, "e", 0, 1, None)]
    if code == "RO":
        return [(k, 0, None, 1, 1, "run out"), (0, k, "b", 1, 1, "run out")]
    if code.lower() == "pen":
        return [(k, int(tail or 5), "pen", 1, 1, None)]
    return [(0, k, "?", 1, 1, None)]


def simulate(events, pair, incoming, fow_order, bpo, legal_before):
    """Run a segment with pair[0] facing first. On each wicket the named batter (from Davis's fall-of-wicket note)
    leaves and the next incoming batter takes his place. Returns runs/balls gained per batter, who faced each
    ball, and who is on strike at the end."""
    on, off = pair
    gain = {}
    faced_by, outs = [], []
    incoming, fows = list(incoming), list(fow_order)
    legal = legal_before
    for bat, ext, et, lg, faced, wk in events:
        faced_by.append((on, off))
        g = gain.setdefault(on, [0, 0])
        g[0] += bat
        g[1] += faced
        ran = ext if et in ("b", "lb", "e") else (max(ext - 1, 0) if et == "wd" else bat)
        if wk:
            out = fows.pop(0) if fows else on
            if out not in (on, off):
                out = on
            outs.append(out)
            new = incoming.pop(0) if incoming else None
            if out == on:
                on = new
            else:
                off = new
        elif ran % 2 == 1:
            on, off = off, on
        legal += lg
        if lg and legal % bpo == 0:
            on, off = off, on                 # end of over: ends change
    return gain, faced_by, outs, (on, off)


def resolve(name, roster):
    """Davis writes 'WM Lawry' once, then just 'Lawry'. Map short forms back to the full name for this match."""
    if not name:
        return None
    n = person(name)
    if n in roster:
        return n
    hits = [r for r in roster if surname(r) == n or r.split()[-1] == n]
    if len(hits) == 1:
        return hits[0]
    parts = n.split()
    if len(parts) > 1 and parts[0] in ("W", "R", "n", "y"):     # a wicket/run-out mark caught in front of a name
        rest = " ".join(parts[1:])
        hits = [r for r in roster if r == rest or surname(r) == rest or r.split()[-1] == rest]
        if len(hits) == 1:
            return hits[0]
    roster.add(n)
    return n


def parse_bbb(text, rosters=None, bpo=None):
    """rosters: {team: set(full names)} from the Test page. Returns match info, innings, balls, notes, stats."""
    rosters = {k: set(v) for k, v in (rosters or {}).items()}
    lines = text.splitlines()
    anchors = [(m.start(), int(m.group(2))) for l in lines for m in [OVS.search(l)] if m]
    if bpo is None:
        bpo = max((d for _, d in anchors), default=6)
        bpo = bpo if bpo in (4, 5, 6, 8) else 6
    cols = sorted(c for c, _ in anchors)
    ovs_col = cols[len(cols) // 2] if cols else 64       # where the 'Ovs' column usually starts
    out = {"balls_per_over": bpo, "innings": [], "number": None, "teams": None, "start_date": None}
    head = next((l for l in lines if re.search(r"\sv\s.*Test", l)), "")
    mt = re.match(r"^\s*(.+?) v (.+?),", head)
    if mt:
        out["teams"] = (team(mt.group(1)), team(mt.group(2)))
    stats = {"segments": 0, "sure": 0, "unsure": 0, "balls": 0, "skipped_lines": 0}
    inn, cum, state, pending = None, {}, None, None
    consumed = set()
    for idx, line in enumerate(lines):
        if idx in consumed:
            continue
        h = INN_LINE.match(line)
        if h and "Ovs" not in line[:20]:
            same = inn is not None and inn.get("team") == team(h["team"]) and inn.get("n") == h["n"]
            if same and re.search(r"\bonly\b|partial", line, re.I):
                inn["partial"] = line.strip()[:120]
            if not h["cont"] and not same:              # a repeated heading on a new page continues the innings
                bat_team = team(h["team"])
                bowl_team = next((t for t in (out["teams"] or ()) if t != bat_team), None)
                inn = {"team": bat_team, "bowling_team": bowl_team, "balls": [], "notes": [], "legal": 0,
                       "n": h["n"]}
                if re.search(r"\bonly\b|partial", line, re.I):
                    inn["partial"] = line.strip()[:120]
                out["innings"].append(inn)
                cum, state, pending = {}, None, None
                tail = re.findall(r"(\d{1,4})\s*$", h["rest"] or "")
                if tail and out["number"] is None:
                    out["number"] = int(tail[0])
                d = iso(h["rest"])
                if d and not out["start_date"]:
                    out["start_date"] = d
            continue
        if inn is None or not line.strip():
            continue
        m = OVS.search(line)
        if not m:
            s = line.strip()
            if s.startswith(("Page", "Bowling", "All out")) or "ump." in s or re.search(r"\sv\s.*Test", s):
                continue
            bt = BOWLER_TOK.search(line[:ovs_col])
            if bt:                                # bowler + balls on this line, the score comes on a later line
                left = line[:ovs_col]
                pending = {"bt": bt, "balls": (left[:bt.start()] + left[bt.end():]).replace(" ", ""),
                           "notes": [line[ovs_col:].strip()]}
            elif pending is not None:
                pending["notes"].append(line[ovs_col:].strip())
            elif inn["balls"] and len(line) - len(line.lstrip()) > 60:
                inn["notes"].append((inn["balls"][-1]["over_no"], s))
            continue
        left, right = line[:m.start()], line[m.end():]
        bt = BOWLER_TOK.search(left)
        extra_notes = []
        if bt:
            ball_str = (left[:bt.start()] + left[bt.end():]).replace(" ", "")
        elif pending is not None:
            bt, ball_str, extra_notes = pending["bt"], pending["balls"], pending["notes"]
        else:
            stats["skipped_lines"] += 1
            continue
        pending = None
        bowl_roster = rosters.setdefault(inn["bowling_team"], set())
        bat_roster = rosters.setdefault(inn["team"], set())
        bowler = resolve(bt.group(1), bowl_roster)
        found = batter_tokens(right)
        if len(found) < 2:                     # the batters' columns can spill onto the next line
            for j in (idx + 1, idx + 2):
                if j < len(lines) and not OVS.search(lines[j]) and not BOWLER_TOK.search(lines[j][:ovs_col]) \
                        and batter_tokens(lines[j]):
                    right = right + "   " + lines[j][m.end():] if len(lines[j]) > m.end() else right
                    consumed.add(j)
                    found = batter_tokens(right)
                    if len(found) >= 2:
                        break
        bats = [(resolve(f.group(1), bat_roster), int(f.group(2)), int(f.group(3))) for f in found[:2]]
        notes = right[found[1].end():] if len(found) > 1 else right
        all_notes = " ".join(x for x in extra_notes + [notes.strip()] if x)
        fow_text = " ".join(x for x in extra_notes + [right] if x)
        fows = [resolve(f.group(3), bat_roster) for f in FOW_TOK.finditer(fow_text)]
        legal_end = int(m.group(1)) * bpo + int(m.group(2))
        runs_end, wkts_end = int(m.group(3)), int(m.group(4))
        prev_runs, prev_wkts = inn.get("score", 0), inn.get("wkts", 0)
        options = [ball_options(t) for t in TOKEN.findall(ball_str)]
        targets = {n: (r - cum.get(n, (0, 0))[0], b - cum.get(n, (0, 0))[1]) for n, r, b in bats}
        fow_full = [(resolve(f.group(3), bat_roster), int(f.group(4)), int(f.group(5)))
                    for f in FOW_TOK2.finditer(fow_text)]
        for n, r, b in fow_full:
            targets[n] = (r - cum.get(n, (0, 0))[0], b - cum.get(n, (0, 0))[1])
        names = []
        for n in [x for x, _, _ in fow_full] + [x for x, _, _ in bats]:
            if n and n not in names:
                names.append(n)
        if state:                              # the two batters at the crease when this segment began
            names = [n for n in state if n and n in names] + [n for n in names if n not in state]
        pairs = list(itertools.permutations(names, 2)) if len(names) > 1 else [(names[0] if names else None, None)]
        if state and len(names) > 1:
            pairs.sort(key=lambda pr: (pr[0] != state[0]) + (pr[1] != state[1]))
        pairs = pairs[:12]
        best = None
        for combo in (itertools.islice(itertools.product(*options), 256) if options else [()]):
            legal = sum(e[3] for e in combo)
            runs = sum(e[0] + e[1] for e in combo)
            wk = sum(1 for e in combo if e[5])
            score = (legal == legal_end - inn["legal"]) * 4 + (runs == runs_end - prev_runs) * 4 \
                + (wk == wkts_end - prev_wkts) * 2
            for pr in pairs:
                incoming = [n for n in names if n not in pr]
                gain, faced_by, outs, end_state = simulate(combo, pr, incoming, fows, bpo, inn["legal"])
                fit = bool(targets) and all(tuple(gain.get(n, [0, 0])) == tuple(v) for n, v in targets.items())
                total = score + (3 if fit else 0)
                if best is None or total > best[0]:
                    best = (total, combo, faced_by, outs, end_state)
                if total == 13:
                    break
            if best[0] == 13:
                break
        total, combo, faced_by, outs, end_state = best
        sure = int(total == 13)
        stats["segments"] += 1
        stats["sure" if sure else "unsure"] += 1
        legal = inn["legal"]
        out_iter = iter(outs)
        for (bat, ext, et, lg, faced, wk), (striker, non_striker) in zip(combo, faced_by):
            who_out = next(out_iter, None) if wk else None
            inn["balls"].append({"over_no": legal // bpo + 1, "ball": legal % bpo + 1, "bowler": bowler,
                                 "batter": striker, "non_striker": non_striker, "bat_runs": bat, "extras": ext,
                                 "extra_type": et, "legal": lg, "wicket": int(bool(wk)),
                                 "kind": None if wk == "out" else wk, "out": who_out, "sure": sure})
            legal += lg
            stats["balls"] += 1
        inn["legal"], inn["score"], inn["wkts"] = legal_end, runs_end, wkts_end
        for n, r, b in bats + fow_full:
            cum[n] = (r, b)
        state = end_state
        clean = re.sub(r"\s{2,}", " ", all_notes).strip()
        if clean and inn["balls"]:
            inn["notes"].append((inn["balls"][-1]["over_no"], clean))
    for x in out["innings"]:                      # Davis's record has holes in some old Tests ("Large gap")
        overs = sorted({b["over_no"] for b in x["balls"]})
        holes = [f"{a + 1}-{b - 1}" if b - a > 2 else str(a + 1) for a, b in zip(overs, overs[1:]) if b - a > 1]
        if holes and not x.get("partial"):
            x["partial"] = "no record of overs " + ", ".join(holes[:5])
    out["stats"] = stats
    return out


# ---------------------------------------------------------------- players in a series (pc pages)
PC_LINE = re.compile(r"^(?P<sur>[A-Z][^,]{1,40}),\s(?P<ini>[A-Z][A-Za-z]*)\s+(?P<tm>[A-Z][A-Za-z]{1,3})\s+"
                     r"(?P<known>.+?)\s{2,}(?P<born>\S.*?)\s+(?P<age>\d{1,2})\s+(?P<career>\d{4}(?:-\d{2,4})?)\s+(?P<role>[A-Za-z][A-Za-z ]*?)\s{2,}")


def parse_players(text):
    out = []
    for line in text.splitlines():
        m = PC_LINE.match(line.strip())
        if m:
            out.append({"name": person(f"{m['ini']} {m['sur'].strip()}"), "team": team(m["tm"]),
                        "full_name": m["known"].strip(), "born": m["born"].strip(), "role": m["role"].strip()})
    return out
