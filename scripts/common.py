"""
Shared pieces for building the Everyball database: paths, the database layout, name and team clean-up,
reading text out of PDFs, and the build log.

The database is one SQLite file: data/everyball.db (it never goes into git; it can always be rebuilt).
Every table row says which source it came from (davis, cricsheet, book, espn, kaggle).
"""
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "everyball.db"
REPORT = ROOT / "data" / "build_report.txt"

# Which source's scorecard wins when several have the same match (lower = preferred).
CARD_PRIORITY = {"davis": 1, "cricsheet": 2, "espn": 3, "kaggle": 4, "book": 5}
# Which source's ball-by-ball wins (Cricsheet is direct data; Davis is reconstructed from his over lines).
BALL_PRIORITY = {"cricsheet": 1, "davis": 2, "espn": 3}

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches(
  match_id TEXT PRIMARY KEY,          -- T0674 = Test no. 674, O3817 = ODI no. 3817 (ESPN's own numbering)
  format TEXT, number INTEGER, start_date TEXT, season TEXT,
  team1 TEXT, team2 TEXT, ground TEXT, result TEXT, winner TEXT, toss TEXT, umpires TEXT,
  balls_per_over INTEGER, series TEXT,
  sources TEXT DEFAULT '',            -- every source that has this match
  card_source TEXT,                   -- whose scorecard the views use
  ball_source TEXT,                   -- whose ball-by-ball the views use (NULL = none)
  confidence TEXT);                   -- e.g. 'reconstructed' for 1800s ball-by-ball from newspapers
CREATE TABLE IF NOT EXISTS players(
  player_id INTEGER PRIMARY KEY, name TEXT, team TEXT,
  full_name TEXT, born TEXT, role TEXT, dob TEXT, batting_style TEXT, bowling_style TEXT,
  cricsheet_id TEXT, cricinfo_id TEXT, sources TEXT DEFAULT '',
  UNIQUE(name, team));
CREATE TABLE IF NOT EXISTS innings(
  match_id TEXT, inn INTEGER, team TEXT, runs INTEGER, wickets INTEGER, overs TEXT, extras INTEGER,
  declared INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS batting(
  match_id TEXT, inn INTEGER, pos INTEGER, player_id INTEGER, team TEXT, how_out TEXT, kind TEXT,
  bowler_id INTEGER, fielder TEXT, runs INTEGER, balls INTEGER, minutes INTEGER, fours INTEGER, sixes INTEGER,
  not_out INTEGER, captain INTEGER, keeper INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS bowling(
  match_id TEXT, inn INTEGER, player_id INTEGER, team TEXT, overs TEXT, balls INTEGER, maidens INTEGER,
  runs INTEGER, wickets INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS balls(
  match_id TEXT, inn INTEGER, seq INTEGER, over_no INTEGER, ball INTEGER,
  bowler_id INTEGER, batter_id INTEGER, non_striker_id INTEGER,
  bat_runs INTEGER, extras INTEGER, extra_type TEXT, legal INTEGER,
  wicket INTEGER, kind TEXT, out_id INTEGER,
  sure INTEGER,                       -- 1 = checked against the over-by-over totals, 0 = best guess
  source TEXT);
CREATE TABLE IF NOT EXISTS notes(match_id TEXT, inn INTEGER, over_no INTEGER, note TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS commentary(match_id TEXT, inn INTEGER, seq INTEGER, text TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS pvp(
  match_id TEXT, inn INTEGER, batter_id INTEGER, bowler_id INTEGER, dots INTEGER, ones INTEGER, twos INTEGER,
  threes INTEGER, fours INTEGER, sixes INTEGER, runs INTEGER, balls INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS ref_matches(
  format TEXT, number INTEGER, start_date TEXT, team1 TEXT, team2 TEXT, winner TEXT, margin TEXT, ground TEXT,
  PRIMARY KEY(format, number));
CREATE TABLE IF NOT EXISTS checks(area TEXT, item TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS build_log(source TEXT, file TEXT, status TEXT, detail TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_balls_duel ON balls(batter_id, bowler_id);
CREATE INDEX IF NOT EXISTS ix_balls_match ON balls(match_id, inn, seq);
CREATE INDEX IF NOT EXISTS ix_balls_bowler ON balls(bowler_id);
CREATE INDEX IF NOT EXISTS ix_bat_player ON batting(player_id);
CREATE INDEX IF NOT EXISTS ix_bat_match ON batting(match_id, source);
CREATE INDEX IF NOT EXISTS ix_bowl_player ON bowling(player_id);
CREATE INDEX IF NOT EXISTS ix_bowl_match ON bowling(match_id, source);
CREATE INDEX IF NOT EXISTS ix_inn_match ON innings(match_id, source);
CREATE INDEX IF NOT EXISTS ix_players_name ON players(name);
"""

# Easy-to-query views: names instead of numbers, and only the preferred source per match.
VIEWS = """
DROP VIEW IF EXISTS bat;
CREATE VIEW bat AS
  SELECT m.match_id, m.format, m.start_date, m.ground, m.team1, m.team2, m.winner, b.inn, b.pos,
         b.player_id, p.name AS player, b.team, b.how_out, b.kind, q.name AS bowler, b.fielder,
         b.runs, b.balls, b.minutes, b.fours, b.sixes, b.not_out, b.captain, b.keeper,
         CASE WHEN b.team = m.team1 THEN m.team2 ELSE m.team1 END AS opponent
  FROM batting b JOIN matches m ON m.match_id = b.match_id AND b.source = m.card_source
  JOIN players p ON p.player_id = b.player_id LEFT JOIN players q ON q.player_id = b.bowler_id;
DROP VIEW IF EXISTS bowl;
CREATE VIEW bowl AS
  SELECT m.match_id, m.format, m.start_date, m.ground, m.team1, m.team2, m.winner, w.inn,
         w.player_id, p.name AS player, w.team, w.overs, w.balls, w.maidens, w.runs, w.wickets,
         CASE WHEN w.team = m.team1 THEN m.team2 ELSE m.team1 END AS opponent
  FROM bowling w JOIN matches m ON m.match_id = w.match_id AND w.source = m.card_source
  JOIN players p ON p.player_id = w.player_id;
DROP VIEW IF EXISTS ball;
CREATE VIEW ball AS
  SELECT m.match_id, m.format, m.start_date, m.ground, x.inn, x.seq, x.over_no, x.ball,
         bo.name AS bowler, bo.team AS bowling_team, ba.name AS batter, ba.team AS batting_team,
         ns.name AS non_striker, x.bat_runs, x.extras, x.extra_type, x.legal, x.wicket, x.kind,
         ou.name AS player_out, x.sure, x.bowler_id, x.batter_id
  FROM balls x JOIN matches m ON m.match_id = x.match_id AND x.source = m.ball_source
  JOIN players bo ON bo.player_id = x.bowler_id JOIN players ba ON ba.player_id = x.batter_id
  LEFT JOIN players ns ON ns.player_id = x.non_striker_id LEFT JOIN players ou ON ou.player_id = x.out_id;
DROP VIEW IF EXISTS inns;
CREATE VIEW inns AS
  SELECT m.match_id, m.format, m.start_date, m.ground, i.inn, i.team, i.runs, i.wickets, i.overs, i.extras, i.declared,
         CASE WHEN i.team = m.team1 THEN m.team2 ELSE m.team1 END AS opponent, m.winner
  FROM innings i JOIN matches m ON m.match_id = i.match_id AND i.source = m.card_source;
"""

# One row per player and format (matches, years, runs, wickets): for search ranking and player pages.
STATS = """
DROP TABLE IF EXISTS player_stats;
CREATE TABLE player_stats AS
  SELECT player_id, format, COUNT(DISTINCT match_id) AS matches, MIN(substr(start_date, 1, 4)) AS first_year,
         MAX(substr(start_date, 1, 4)) AS last_year, SUM(r) AS runs, SUM(w) AS wickets
  FROM (SELECT b.player_id, m.format, m.match_id, m.start_date, b.runs AS r, 0 AS w
          FROM batting b JOIN matches m ON m.match_id = b.match_id AND b.source = m.card_source
        UNION ALL
        SELECT w.player_id, m.format, m.match_id, m.start_date, 0, w.wickets
          FROM bowling w JOIN matches m ON m.match_id = w.match_id AND w.source = m.card_source)
  GROUP BY player_id, format;
CREATE INDEX ix_stats_player ON player_stats(player_id);
"""


def connect(path=None):
    con = sqlite3.connect(str(path or DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def finish(con):
    """Indexes, views and the per-match choice of preferred source. Run at the end of every build step."""
    con.executescript(INDEXES)
    for table, col, prio in (("batting", "card_source", CARD_PRIORITY), ("balls", "ball_source", BALL_PRIORITY)):
        order = " ".join(f"WHEN '{s}' THEN {n}" for s, n in prio.items())
        con.execute(f"""UPDATE matches SET {col} = (SELECT source FROM {table} t WHERE t.match_id = matches.match_id
                        GROUP BY source ORDER BY CASE source {order} ELSE 9 END LIMIT 1)""")
    con.executescript(VIEWS)
    con.executescript(STATS)
    con.commit()


# ---------------------------------------------------------------- names and teams
TEAMS = {
    "au": "Australia", "aus": "Australia", "australia": "Australia",
    "en": "England", "eng": "England", "england": "England",
    "sa": "South Africa", "saf": "South Africa", "rsa": "South Africa", "south africa": "South Africa",
    "wi": "West Indies", "west indies": "West Indies",
    "nz": "New Zealand", "new zealand": "New Zealand",
    "in": "India", "ind": "India", "india": "India",
    "pk": "Pakistan", "pak": "Pakistan", "pakistan": "Pakistan",
    "sl": "Sri Lanka", "sri lanka": "Sri Lanka", "sri": "Sri Lanka",
    "zi": "Zimbabwe", "zim": "Zimbabwe", "zimbabwe": "Zimbabwe",
    "ba": "Bangladesh", "ban": "Bangladesh", "bdesh": "Bangladesh", "bangladesh": "Bangladesh",
    "ir": "Ireland", "ire": "Ireland", "ireland": "Ireland",
    "af": "Afghanistan", "afg": "Afghanistan", "afghanistan": "Afghanistan",
    "icc": "ICC World XI", "wld": "ICC World XI", "icc world xi": "ICC World XI", "world xi": "ICC World XI",
    "kenya": "Kenya", "ken": "Kenya", "canada": "Canada", "can": "Canada", "scotland": "Scotland", "scot": "Scotland",
    "netherlands": "Netherlands", "neth": "Netherlands", "holl": "Netherlands", "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates", "namibia": "Namibia", "nam": "Namibia", "usa": "United States of America",
    "united states of america": "United States of America", "bermuda": "Bermuda", "east africa": "East Africa",
    "hong kong": "Hong Kong", "hk": "Hong Kong", "eaf": "East Africa", "ea": "East Africa", "nl": "Netherlands", "oman": "Oman", "nepal": "Nepal", "png": "Papua New Guinea",
    "papua new guinea": "Papua New Guinea", "asia xi": "Asia XI", "africa xi": "Africa XI",
}


def team(name):
    """'AUSTRALIA', 'Au', 'aus' -> 'Australia'. Unknown names are returned tidied, never dropped."""
    if not name:
        return None
    key = re.sub(r"[^a-z ]", "", name.strip().lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return TEAMS.get(key) or " ".join(w.capitalize() for w in key.split()) or name.strip()


def person(name):
    """One spelling for each person: 'N.F.D.Thomson' / 'NFD Thomson' / 'nfd  thomson' -> 'NFD Thomson'."""
    if not name:
        return None
    n = re.sub(r"[\*††]", "", str(name)).strip()
    n = re.sub(r"\s+", " ", n)
    m = re.match(r"^((?:[A-Z]\.\s?)+)\s*(.+)$", n)            # C.Bannerman, N.F.D.Thomson, W. G. Grace
    if m:
        n = m.group(1).replace(".", "").replace(" ", "") + " " + m.group(2).strip()
    return n


def surname(name):
    parts = (name or "").split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]{1,4}", parts[0]):
        return " ".join(parts[1:])
    return name


# ---------------------------------------------------------------- reading PDFs
def pdf_text(path):
    """Text of a PDF with its layout kept (columns line up). Uses pdftotext if installed, else pypdf."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace")
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    out = []
    for page in reader.pages:
        try:
            out.append(page.extract_text(extraction_mode="layout"))
        except Exception:
            out.append(page.extract_text() or "")
    return "\n".join(out)


# ---------------------------------------------------------------- players and matches
def same_person(name, candidates):
    """Which of `candidates` [(name, full_name), ...] is `name`?  'ME Waugh' or 'Mark Waugh' -> 'ME Waugh', never
    'SR Waugh'. Same surname, then same first name (from full names) or same first initial, and only if exactly one
    candidate is left. Returns that candidate's name, or None."""
    name = person(name)
    if not name:
        return None
    if any(c[0] == name for c in candidates):
        return name
    words = name.split()
    pool = [c for c in candidates if c[0].split()[-1].lower() == words[-1].lower()]
    if len(words) == 1 or not pool:
        return pool[0][0] if len(pool) == 1 and len(words) == 1 else None
    first = words[0]
    if not re.fullmatch(r"[A-Z]{1,4}", first):
        hit = [c for c in pool if c[1] and c[1].split()[0].lower() == first.lower()]
        if len(hit) == 1:
            return hit[0][0]
    hit = [c for c in pool if len(c[0].split()) > 1 and c[0].split()[0][0] == first[0]]
    return hit[0][0] if len(hit) == 1 else None


class Registry:
    """Hands out one player_id per (name, team) and remembers it for the whole build."""

    def __init__(self, con):
        self.con = con
        self.cache = {(n, t): i for i, n, t in con.execute("SELECT player_id, name, team FROM players")}

    def get(self, name, team_name, source=None, **info):
        name, team_name = person(name), team(team_name) if team_name else None
        if not name:
            return None
        key = (name, team_name)
        pid = self.cache.get(key)
        if pid is None:
            cur = self.con.execute("INSERT INTO players(name, team, sources) VALUES(?,?,?)",
                                   (name, team_name, source or ""))
            pid = self.cache[key] = cur.lastrowid
        if info:
            sets = ", ".join(f"{k} = COALESCE({k}, ?)" for k in info)
            self.con.execute(f"UPDATE players SET {sets} WHERE player_id = ?", (*info.values(), pid))
        return pid


def upsert_match(con, match_id, source, **fields):
    """Create the match or fill in fields it doesn't have yet; remember which sources have it."""
    row = con.execute("SELECT sources FROM matches WHERE match_id = ?", (match_id,)).fetchone()
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    if row is None:
        cols = ["match_id", "sources", *fields]
        con.execute(f"INSERT INTO matches({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    (match_id, source, *fields.values()))
        return
    sources = set(filter(None, (row[0] or "").split(",")))
    sources.add(source)
    if fields:
        sets = ", ".join(f"{k} = COALESCE({k}, ?)" for k in fields)
        con.execute(f"UPDATE matches SET {sets} WHERE match_id = ?", (*fields.values(), match_id))
    con.execute("UPDATE matches SET sources = ? WHERE match_id = ?", (",".join(sorted(sources)), match_id))


def log(con, source, file, status, detail=""):
    con.execute("INSERT INTO build_log VALUES(?,?,?,?)", (source, str(file), status, str(detail)[:500]))


def clear_source(con, source, formats=None):
    """Remove one source's rows so a step can be re-run cleanly."""
    for t in ("innings", "batting", "bowling", "balls", "notes", "pvp", "commentary"):
        con.execute(f"DELETE FROM {t} WHERE source = ?", (source,))
    con.execute("DELETE FROM build_log WHERE source = ?", (source,))
    con.commit()


def overs_to_balls(overs, bpo=6):
    """'32.3' with 6-ball overs -> 195 balls."""
    if overs in (None, ""):
        return None
    m = re.match(r"^(\d+)(?:\.(\d))?$", str(overs).strip())
    if not m:
        return None
    return int(m.group(1)) * bpo + int(m.group(2) or 0)
