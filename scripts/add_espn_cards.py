"""
Add ESPNcricinfo's own SCORECARDS (not the commentary) from the 67 espn_full_commentary_*.json.gz files to the
database as a fourth scorecard source, 'espn_live', for every Test and ODI ESPN lists (all 7,692, 1877 on).

  python scripts/add_espn_cards.py data/raw/espn_full                 # add (writes data/everyball.db)
  python scripts/add_espn_cards.py data/raw/espn_full --dry-run       # only report

What goes in (source = 'espn_live'):
  batting   every batter who batted: position, runs, balls, minutes, 4s, 6s, out or not, how out, bowler, fielders,
            captain / keeper
  bowling   every bowler: overs, balls, maidens, runs, wickets
  innings   every innings: runs, wickets, overs, extras, declared
  played    the whole XI of both teams (also those who did not bat or bowl, and teams that never batted)

PLAYERS (one player = one record). Each ESPN player is joined to our record in this order:
  1. ESPN id (our players.cricinfo_id = ESPN objectId);
  2. MATCH CONTEXT: a player already in THIS match for THIS team (from Cricsheet / Davis / ESPN old / Kaggle / book)
     with the same surname and fitting initials - then our record gets ESPN's id;
  3. otherwise a new record from ESPN's own data (name, full name, date of birth, styles).
  If two of our records turn out to be the same ESPN player (a split person, e.g. 'McGrath' and 'GD McGrath'), they
  are merged into one (every table updated; the empty record removed) and listed.
Nothing already in the database is changed except player ids of merged split records and missing ESPN ids.
Count check: every ESPN scorecard line read = a row written or listed with a reason.
Report: data/reports/espn_cards_added.csv (matches) + data/reports/espn_players_joined.csv (players).
"""
import csv
import glob
import gzip
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_players as MP          # noqa: E402  (clean(), compatible(), tables())
from add_espn_live import kind_of   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "everyball.db"
SRC = "espn_live"


def records(folder):
    """Every match record once, one file at a time (memory stays small). The three '(1)' files are exact copies
    made by a resumed download: a match already seen is skipped."""
    import gc
    files = sorted(glob.glob(os.path.join(folder, "espn_full_commentary_*.json.gz")))
    if not files:
        sys.exit(f"No espn_full_commentary_*.json.gz files in {folder}")
    seen = set()
    for k, f in enumerate(files, 1):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        for r in data:
            r.pop("innings", None)                  # the commentary is not needed here
        for i in range(len(data)):
            r = data[i]
            data[i] = None
            key = (r.get("format"), r.get("number"))
            if key in seen:
                continue
            seen.add(key)
            yield k, len(files), r
        del data
        gc.collect()


def norm_team(t):
    return (t or "").strip().lower().replace("u.s.a.", "united states of america").replace("usa", "united states of america")


class People:
    def __init__(self, con):
        self.con = con
        self.by_obj = {}
        for p, o in con.execute("SELECT player_id, cricinfo_id FROM players WHERE cricinfo_id IS NOT NULL AND cricinfo_id != ''"):
            self.by_obj.setdefault(str(o).replace(".0", ""), p)
        self.info = {p: (n, fn, str(o or "").replace(".0", "")) for p, n, fn, o in
                     con.execute("SELECT player_id, name, full_name, cricinfo_id FROM players")}
        self.next_id = con.execute("SELECT COALESCE(MAX(player_id), 0) + 1 FROM players").fetchone()[0]
        # who is in each match for each team, from every source already in the database
        self.in_match = defaultdict(set)
        for t in ("batting", "bowling", "played"):
            for mid, team, pid in con.execute(f"SELECT match_id, team, player_id FROM {t} WHERE source NOT IN (?, 'gold')", (SRC,)):
                if pid is not None:
                    self.in_match[mid].add((norm_team(team), pid))
        for mid, pid in con.execute("SELECT DISTINCT match_id, batter_id FROM balls UNION SELECT DISTINCT match_id, bowler_id FROM balls"):
            if pid is not None:
                self.in_match[mid].add(("", pid))
        self.roster = {}         # (match, team) -> names of ESPN's players in that team (set per match)
        self.roster_ids = {}
        self.log = []            # (how, espn objectId, our player_id, name, match)
        self.merges = {}         # stray player_id -> real player_id
        self.moves = {}          # (match, our player_id) -> the right player_id: our record holds the wrong person here

    @staticmethod
    def _fits(ci, cs, fi, fs, ini, sur, lini, lsur):
        return bool((cs and cs in (sur, lsur) and (MP.compatible(ci, ini) or MP.compatible(ci, lini))) or
                    (fs and fs in (sur, lsur) and (MP.compatible(fi, lini) or MP.compatible(fi, ini))))

    def set_roster(self, mid, team, plist):
        self.roster[(mid, norm_team(team))] = [(*MP.clean(p.get("name") or ""), *MP.clean(p.get("longName") or ""))
                                              for p in plist if p]
        self.roster_ids[(mid, norm_team(team))] = [p.get("objectId") for p in plist if p]

    def _new(self, p, team):
        pid = self.next_id
        self.next_id += 1
        dob = p.get("dateOfBirth") or {}
        dob_s = f"{dob.get('year'):04d}-{dob.get('month') or 1:02d}-{dob.get('date') or 1:02d}" if dob.get("year") else None
        name = p.get("name") or p.get("longName")
        if self.con.execute("SELECT 1 FROM players WHERE name = ? AND team IS ?", (name, team)).fetchone():
            name = f"{p.get('longName') or name} ({p.get('objectId')})"
        self.con.execute("INSERT INTO players (player_id, name, team, full_name, dob, batting_style, bowling_style, "
                         "cricinfo_id, sources) VALUES (?,?,?,?,?,?,?,?,?)",
                         (pid, name, team, p.get("longName"), dob_s, ", ".join(p.get("longBattingStyles") or []) or None,
                          ", ".join(p.get("longBowlingStyles") or []) or None, str(p.get("objectId")), SRC))
        self.info[pid] = (name, p.get("longName"), str(p.get("objectId")))
        return pid

    def get(self, p, team, mid):
        """ESPN player dict -> our player_id."""
        if not p:
            return None
        obj = str(p.get("objectId") or "").replace(".0", "")
        if not obj:
            return None
        ini, sur = MP.clean(p.get("name") or "")
        lini, lsur = MP.clean(p.get("longName") or "")
        cands, wrong = [], []
        tn = norm_team(team)
        roster_ids = {str(x) for x in self.roster_ids.get((mid, tn), ())}
        for t, pid in self.in_match.get(mid, ()):
            if t and tn and t != tn:
                continue
            n, fn, o = self.info.get(pid, ("", "", ""))
            if o and o != obj:
                # our record has another ESPN id. If ESPN says that id did NOT play for this team in this match,
                # our id is wrong here (e.g. Alan Brown 1961 filed under Ali Brown's id): move this match's rows.
                ci, cs = MP.clean(n)
                fi, fs = MP.clean(fn or "")
                if o not in roster_ids and self._fits(ci, cs, fi, fs, ini, sur, lini, lsur):
                    fit = sum(1 for (a, b_, c, d) in self.roster.get((mid, tn), ()) if self._fits(ci, cs, fi, fs, a, b_, c, d))
                    if fit == 1:
                        wrong.append(pid)
                continue
            ci, cs = MP.clean(n)
            fi, fs = MP.clean(fn or "")
            if self._fits(ci, cs, fi, fs, ini, sur, lini, lsur):
                # it must fit exactly ONE of ESPN's players in this team (Hanif / Mushtaq Mohammad stay apart)
                fit = sum(1 for (a, b_, c, d) in self.roster.get((mid, tn), ()) if self._fits(ci, cs, fi, fs, a, b_, c, d))
                if fit == 1:
                    cands.append(pid)
        cands = sorted(set(cands))
        if wrong:
            real = self.by_obj.get(obj)
            if real is None:
                real = self._new(p, team)
                self.by_obj[obj] = real
                self.log.append(("new record: our record had another player's ESPN id", obj, real, p.get("name"), mid))
            for w_ in wrong:
                if (mid, w_) not in self.moves:
                    self.moves[(mid, w_)] = real
                    self.log.append((f"rows of this match moved from record {w_} (wrong ESPN id {self.info[w_][2]})", obj, real, self.info[w_][0], mid))
        if obj in self.by_obj:
            real = self.by_obj[obj]
            for c in cands:                                  # a second record of the same person in this match
                if c != real and not self.info[c][2] and c not in self.merges:
                    self.merges[c] = real
                    self.log.append(("merged (same ESPN player, same match)", obj, c, self.info[c][0], mid))
            return real
        if len(cands) > 1:
            # every candidate fits ONLY this ESPN player in this team: they are one person stored twice
            # (e.g. Davis 'PJP Burge' + book 'P JjJ.P.Burge') -> keep one, merge the others into it
            exact = [c for c in cands if (self.info[c][0] or "").strip() == (p.get("name") or "").strip()]
            keep = exact[0] if exact else cands[0]
            for c in cands:
                if c != keep and c not in self.merges:
                    self.merges[c] = keep
                    self.log.append(("merged (same ESPN player, same match)", obj, c, self.info[c][0], mid))
            cands = [keep]
        if len(cands) == 1:
            pid = cands[0]
            self.con.execute("UPDATE players SET cricinfo_id = ? WHERE player_id = ?", (obj, pid))
            self.by_obj[obj] = pid
            n, fn, _ = self.info[pid]
            self.info[pid] = (n, fn, obj)
            self.log.append(("linked by match context", obj, pid, n, mid))
            return pid
        pid = self._new(p, team)
        self.by_obj[obj] = pid
        self.log.append(("new player from ESPN", obj, pid, p.get("name"), mid))
        return pid

    def apply_moves(self):
        """Rows of one match that sit on the wrong person's record go to the right record (that match only)."""
        tabs = MP.tables(self.con)
        n = 0
        for (mid, wrong), right in self.moves.items():
            for t, cols in tabs.items():
                if "match_id" not in {r[1] for r in self.con.execute(f"PRAGMA table_info({t})")}:
                    continue
                for c in cols:
                    n += self.con.execute(f"UPDATE {t} SET {c} = ? WHERE match_id = ? AND {c} = ? AND "
                                          + ("source != 'espn_live'" if t in ("batting", "bowling", "played") else "1"),
                                          (right, mid, wrong)).rowcount
        # records left with no rows at all are removed
        empty = [p for (p,) in self.con.execute(
            "SELECT player_id FROM players WHERE player_id NOT IN (SELECT player_id FROM batting) AND player_id NOT IN "
            "(SELECT player_id FROM bowling) AND player_id NOT IN (SELECT player_id FROM played) AND player_id NOT IN "
            "(SELECT batter_id FROM balls WHERE batter_id IS NOT NULL) AND player_id NOT IN (SELECT bowler_id FROM balls WHERE bowler_id IS NOT NULL)")]
        return n, empty

    def pair_orphans(self):
        """Inside one innings: a line of ours (another source) whose player ESPN does not have, and an ESPN line whose
        player has no line of ours, with IDENTICAL figures, are the same person filed under the wrong record.
        Only exact, unique pairs are moved; the rest is listed."""
        con = self.con
        pairs = unpaired = 0
        xi = defaultdict(set)                       # ESPN's XI of each match: those players are never moved
        for mid, pid in con.execute("SELECT match_id, player_id FROM played WHERE source = ?", (SRC,)):
            xi[mid].add(pid)
        for tbl, figs in (("batting", "runs, balls"), ("bowling", "balls, runs, wickets")):
            E = defaultdict(dict); O = defaultdict(lambda: defaultdict(set))
            for row in con.execute(f"SELECT match_id, inn, player_id, team, {figs}, source FROM {tbl}"):
                mid, inn, pid, team, *fg, src = row
                if pid is None:
                    continue
                if src == SRC:
                    E[(mid, inn)][pid] = (norm_team(team), tuple(fg))
                elif src not in ("book", "gold"):
                    O[(mid, inn)][pid].add((norm_team(team), tuple(fg)))
            for k, epl in E.items():
                ours = O.get(k)
                if not ours:
                    continue
                orphans = {p: v for p, v in ours.items() if p not in epl}
                lonely = {p: v for p, v in epl.items() if p not in ours}
                for x, xv in orphans.items():
                    # first an ESPN player with no line of ours; failing that, any ESPN player in the innings (our line
                    # is then a duplicate of his, filed under the wrong record) - identical figures, exactly one match
                    hits = [y for y, (yt, yf) in lonely.items() if any(
                        (not t or not yt or t == yt) and all(a is None or b is None or a == b for a, b in zip(f, yf))
                        and any(a is not None for a in f) for t, f in xv)] or [y for y, (yt, yf) in epl.items() if any(
                        (not t or not yt or t == yt) and all(a is None or b is None or a == b for a, b in zip(f, yf))
                        and any(a is not None for a in f) for t, f in xv)]
                    if len(hits) == 1 and (k[0], x) not in self.moves and x not in xi[k[0]]:
                        self.moves[(k[0], x)] = hits[0]
                        self.log.append((f"{tbl} line moved: same figures as ESPN's line for this player", "", hits[0],
                                         self.info.get(x, ("",))[0], k[0]))
                        lonely.pop(hits[0], None)
                        pairs += 1
                    else:
                        unpaired += 1
                        self.log.append((f"{tbl} line ESPN does not have (left as is, listed)", "", x,
                                         self.info.get(x, ("",))[0], k[0]))
        return pairs, unpaired

    def apply_merges(self):
        """Point every reference of a stray record at the real player, then remove the empty record."""
        tabs = MP.tables(self.con)
        for stray, real in self.merges.items():
            while real in self.merges:
                real = self.merges[real]
            for t, cols in tabs.items():
                for c in cols:
                    self.con.execute(f"UPDATE {t} SET {c} = ? WHERE {c} = ?", (real, stray))
            self.con.execute("DELETE FROM players WHERE player_id = ?", (stray,))
        return len(self.merges)


def main():
    t0 = time.time()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    folder = args[0] if args else str(ROOT / "data" / "raw" / "espn_full")
    db = Path(args[1]) if len(args) > 1 else DB
    con = sqlite3.connect(db)
    con.execute("DELETE FROM batting WHERE source = ?", (SRC,))       # re-runnable
    con.execute("DELETE FROM bowling WHERE source = ?", (SRC,))
    con.execute("DELETE FROM innings WHERE source = ?", (SRC,))
    con.execute("DELETE FROM played WHERE source IN (?, 'kaggle_xi')", (SRC,))
    match_of = {(f, n): mid for mid, f, n in con.execute("SELECT match_id, format, number FROM matches")}
    people = People(con)
    tally, out = Counter(), []
    bat_rows, bowl_rows, inn_rows, played_rows = [], [], [], []
    k = 0
    for fno, nfiles, r in records(folder):
        k += 1
        if k % 250 == 0:
            el = int(time.time() - t0)
            print(f"\r  ⏱ {el // 60}:{el % 60:02d} | file {fno}/{nfiles} | matches {k} | players linked "
                  f"{sum(1 for x in people.log if x[0].startswith('linked'))}, new {sum(1 for x in people.log if x[0].startswith('new'))}",
                  end="", flush=True)
        key = (r.get("format"), r.get("number"))
        mid = match_of.get(key)
        if not mid:
            # a Test / ODI ESPN lists that our match list lacks (e.g. Test #1541, Afghanistan's Tests, 2024-26):
            # added from ESPN's own match details
            if r.get("format") not in ("Test", "ODI") or not r.get("number"):
                tally["not a Test/ODI record (skipped)"] += 1
                continue
            mid = ("T" if r["format"] == "Test" else "O") + f"{int(r['number']):04d}"
            if con.execute("SELECT 1 FROM matches WHERE match_id = ?", (mid,)).fetchone():
                tally["match id taken by another match (listed)"] += 1
                out.append([*key, r.get("start_date"), r.get("teams"), mid, "id clash - not added", 0, 0, 0])
                continue
            m_ = r.get("match") or {}
            tn = {(t.get("team") or {}).get("id"): (t.get("team") or {}).get("name") for t in (m_.get("teams") or [])}
            t1, t2 = ((r.get("teams") or "").split(" v ") + ["", ""])[:2]
            bpo = next((s_.get("ballsPerOver") for s_ in (r.get("scorecard_innings") or []) if s_.get("ballsPerOver")), 6)
            con.execute("INSERT INTO matches (match_id, format, number, start_date, season, team1, team2, ground, result, "
                        "winner, toss, balls_per_over, series, sources, card_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (mid, r["format"], int(r["number"]), (r.get("start_date") or "")[:10], m_.get("season"),
                         t1.strip() or None, t2.strip() or None, (m_.get("ground") or {}).get("name"), m_.get("statusText"),
                         tn.get(m_.get("winnerTeamId")), tn.get(m_.get("tossWinnerTeamId")), bpo,
                         (m_.get("series") or {}).get("name"), SRC, SRC))
            match_of[key] = mid
            tally["match added to our list (from ESPN)"] += 1
            out.append([*key, r.get("start_date"), r.get("teams"), mid, "MATCH ADDED to our list", 0, 0, 0])
        plist = r.get("players") or {}
        teams = {}
        for t in ((r.get("match") or {}).get("teams") or []):
            tm = t.get("team") or {}
            teams[tm.get("id")] = tm.get("name")
        nb = nw = ni = 0
        seen_xi = set()
        # ESPN's roster of each team in this match (for the one-to-one name check)
        ros = defaultdict(dict)
        for s in r.get("scorecard_innings") or []:
            bt_ = (s.get("team") or {}).get("name")
            ot_ = next((n for i, n in teams.items() if n and n != bt_), None)
            for b in s.get("inningBatsmen") or []:
                if b.get("player"): ros[bt_][b["player"].get("objectId")] = b["player"]
            for w in s.get("inningBowlers") or []:
                if w.get("player"): ros[ot_][w["player"].get("objectId")] = w["player"]
        for p in plist.values():
            tname = teams.get(p.get("countryTeamId"))
            if tname:
                ros[tname].setdefault(p.get("objectId"), p)
        for tname, d in ros.items():
            people.set_roster(mid, tname, list(d.values()))
        for s in r.get("scorecard_innings") or []:
            inn = s.get("inningNumber")
            bat_team = (s.get("team") or {}).get("name")
            bowl_team = next((n for i, n in teams.items() if n and n != bat_team), None)
            ni += 1
            dec = 1 if "declared" in str(s.get("event") or "").lower() or s.get("isDeclared") else 0
            inn_rows.append((mid, inn, bat_team, s.get("runs"), s.get("wickets"),
                             str(s.get("overs")) if s.get("overs") is not None else None,
                             s.get("extras"), dec, SRC))
            for pos, b in enumerate(s.get("inningBatsmen") or [], 1):
                pid = people.get(b.get("player"), bat_team, mid)
                if pid is None:
                    tally["batter without ESPN id"] += 1
                    continue
                if (bat_team, pid) not in seen_xi:
                    seen_xi.add((bat_team, pid))
                    played_rows.append((mid, bat_team, pid, SRC))
                if b.get("battedType") not in ("yes", "sub") or b.get("runs") is None:
                    tally[f"in XI, {b.get('battedType')}"] += 1
                    continue
                dt = b.get("dismissalText") or {}
                fielders = ", ".join(((f.get("player") or {}).get("name") or "") for f in (b.get("dismissalFielders") or []) if f) or None
                role = str(b.get("playerRoleType") or "")
                bwl = people.get(b.get("dismissalBowler"), bowl_team, mid) if b.get("isOut") and b.get("dismissalBowler") else None
                bat_rows.append((mid, inn, pos, pid, bat_team, dt.get("long") or dt.get("short"),
                                 kind_of(b) if b.get("isOut") else None, bwl, fielders, b.get("runs"), b.get("balls"),
                                 b.get("minutes"), b.get("fours"), b.get("sixes"), 0 if b.get("isOut") else 1,
                                 1 if "C" in role else 0, 1 if "WK" in role else 0, SRC))
                nb += 1
            for w in s.get("inningBowlers") or []:
                pid = people.get(w.get("player"), bowl_team, mid)
                if pid is None:
                    tally["bowler without ESPN id"] += 1
                    continue
                if (bowl_team, pid) not in seen_xi:
                    seen_xi.add((bowl_team, pid))
                    played_rows.append((mid, bowl_team, pid, SRC))
                bowl_rows.append((mid, inn, pid, bowl_team, str(w.get("overs")) if w.get("overs") is not None else None,
                                  w.get("balls"), w.get("maidens"), w.get("conceded"), w.get("wickets"), SRC))
                nw += 1
        # the rest of each XI (did not bat or bowl, or the team never batted): ESPN's player list for the match
        have = {pid for _, pid in seen_xi}
        for p in plist.values():
            team = teams.get(p.get("countryTeamId"))
            if not team:
                if str(p.get("objectId")) in people.by_obj and people.by_obj[str(p.get("objectId"))] in have:
                    continue
                tally["XI player with no team (listed)"] += 1
                people.log.append(("XI player without a team in this match", p.get("objectId"), None, p.get("name"), mid))
                continue
            pid = people.get(p, team, mid)
            if pid and (team, pid) not in seen_xi and pid not in have:
                seen_xi.add((team, pid))
                played_rows.append((mid, team, pid, SRC))
        tally["matches read"] += 1
        tally["matches with a scorecard" if ni else "matches with no scorecard (XI only)"] += 1
        out.append([*key, r.get("start_date"), r.get("teams"), mid, "added", nb, nw, len(seen_xi)])
    print()
    con.executemany("INSERT INTO batting (match_id, inn, pos, player_id, team, how_out, kind, bowler_id, fielder, runs, balls, "
                    "minutes, fours, sixes, not_out, captain, keeper, source) VALUES (" + ",".join("?" * 18) + ")", bat_rows)
    con.executemany("INSERT INTO bowling (match_id, inn, player_id, team, overs, balls, maidens, runs, wickets, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", bowl_rows)
    con.executemany("INSERT INTO innings (match_id, inn, team, runs, wickets, overs, extras, declared, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", inn_rows)
    con.executemany("INSERT INTO played (match_id, team, player_id, source) VALUES (?,?,?,?)", played_rows)
    # ODI playing XIs from Kaggle ("Team1/Team2 Playing 11", ESPN player numbers): fills rain-hit matches where
    # ESPN's files list only the players who batted (e.g. India v Pakistan, Colombo, 20 Jul 1997)
    kdir = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--kaggle=")), str(ROOT / "data" / "raw" / "kaggle" / "odi"))
    kx = 0
    if os.path.exists(os.path.join(kdir, "odi_Matches_Data.csv")):
        import ast
        id2obj = {}
        with open(os.path.join(kdir, "players_info.csv"), encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                id2obj[str(row["player_id"]).strip()] = str(row["player_object_id"]).strip().replace(".0", "")
        have = defaultdict(set)
        for mid_, pid_ in con.execute("SELECT match_id, player_id FROM played"):
            have[mid_].add(pid_)
        for mid_, _, pid_, _ in played_rows:
            have[mid_].add(pid_)
        kmiss = Counter()
        with open(os.path.join(kdir, "odi_Matches_Data.csv"), encoding="utf-8", errors="replace") as fh:
            for row in csv.DictReader(fh):
                no = (row.get("ODI Match No") or "").strip()
                if not no.isdigit():
                    continue
                mid_ = f"O{int(no):04d}"
                if ("ODI", int(no)) not in match_of:
                    kmiss["Kaggle ODI not in our list"] += 1
                    continue
                for col, tcol in (("Team1 Playing 11", "Team1 Name"), ("Team2 Playing 11", "Team2 Name")):
                    try:
                        ids = ast.literal_eval(row.get(col) or "[]")
                    except Exception:
                        kmiss["unreadable XI"] += 1
                        continue
                    for i_ in ids:
                        pid_ = people.by_obj.get(id2obj.get(str(i_), ""))
                        if pid_ is None:
                            kmiss["Kaggle player we cannot place"] += 1
                            continue
                        if pid_ not in have[mid_]:
                            have[mid_].add(pid_)
                            played_rows.append((mid_, row.get(tcol), pid_, "kaggle_xi"))
                            con.execute("INSERT INTO played (match_id, team, player_id, source) VALUES (?,?,?,?)",
                                        (mid_, row.get(tcol), pid_, "kaggle_xi"))
                            kx += 1
        for k_, v in kmiss.items():
            tally[k_] += v
    else:
        print(f"\n  !!! WARNING: Kaggle files not found in {kdir} - ODI playing XIs NOT filled (Sachin would show 460 ODIs). "
              f"Give the folder with --kaggle=PATH (the folder holding odi_Matches_Data.csv).")
    tally["XI players added from Kaggle's Playing 11"] = kx
    merged = people.apply_merges()
    paired, unpaired = people.pair_orphans()
    tally["lines moved to the right person (identical figures)"] = paired
    tally["lines ESPN does not have (listed)"] = unpaired
    moved, empty = people.apply_moves()
    if empty:
        con.executemany("DELETE FROM players WHERE player_id = ?", [(p,) for p in empty])
        people.log += [("empty record removed", "", p, people.info.get(p, ("",))[0], "") for p in empty]
    # new records were named "Long Name (ESPN id)" only while another record held the short name: tidy them up
    for pid, nm_, team in con.execute("SELECT player_id, name, team FROM players WHERE sources = ? AND name LIKE '%)'", (SRC,)).fetchall():
        obj = people.info.get(pid, ("", "", ""))[2]
        short = next((x[3] for x in people.log if str(x[1]) == str(obj) and x[2] == pid and x[3]), None)
        for cand in (short, nm_.rsplit(" (", 1)[0]):
            if cand and not con.execute("SELECT 1 FROM players WHERE name = ? AND team IS ? AND player_id != ?", (cand, team, pid)).fetchone():
                con.execute("UPDATE players SET name = ? WHERE player_id = ?", (cand, pid))
                break
    rep = ROOT / "data" / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    with open(rep / "espn_cards_added.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["format", "number", "date", "teams", "match_id", "result", "batting rows", "bowling rows", "XI players"])
        w.writerows(out)
    with open(rep / "espn_players_joined.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["how", "espn_object_id", "player_id", "name", "match_id"])
        w.writerows(people.log)
    if dry:
        con.rollback()
    else:
        con.commit()
    how = Counter(x[0] for x in people.log)
    print(("DRY RUN - nothing written. " if dry else "") + f"ESPN scorecards ({time.time() - t0:.0f} s):")
    for k_, v in tally.most_common():
        print(f"  {k_}: {v}")
    print(f"  rows: batting {len(bat_rows)}, bowling {len(bowl_rows)}, innings {len(inn_rows)}, XI {len(played_rows)}")
    print(f"  players: " + ", ".join(f"{k_} {v}" for k_, v in how.most_common()) + f"; split records merged {merged}; rows moved to the right person {moved} ({len(people.moves)} player-matches); empty records removed {len(empty)}")
    print("  reports: data/reports/espn_cards_added.csv, data/reports/espn_players_joined.csv")
    con.close()


if __name__ == "__main__":
    main()
