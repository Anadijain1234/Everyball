"""
Join duplicate player records into the real player ("W W GD McGrath", "GD McGrath." and "McGrath" -> Glenn McGrath).

A stray record = a player with neither an ESPN id (cricinfo_id) nor a Cricsheet id - usually a name read from the
book or an old scorecard. It is joined to a real player ONLY by match context: the real player must be in the same
team in the same matches, with the same surname and matching initials. Anything not certain is listed, never guessed.

  python scripts/merge_players.py            # do it (data/everyball.db)
  python scripts/merge_players.py --dry-run  # only list what would be joined
Report: data/reports/players_merged.csv
"""
import csv
import difflib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "everyball.db"
REPORT = ROOT / "data" / "reports" / "players_merged.csv"

REFS = {"balls": ["batter_id", "bowler_id", "non_striker_id", "out_id"], "batting": ["player_id", "bowler_id"],
        "bowling": ["player_id"], "played": ["player_id"], "pvp": ["batter_id", "bowler_id"],
        "batting_gold": ["player_id", "bowler_id"], "bowling_gold": ["player_id"],
        "balls_fix": ["batter_id", "non_striker_id"], "source_disagreements": ["player_id"]}


def clean(name):
    """'W W GD McGrath' -> ('GD', 'mcgrath'); 'GD McGrath.' -> ('GD', 'mcgrath'); 'McGrath' -> ('', 'mcgrath')."""
    s = re.sub(r"[.†*()]+", " ", name or "").strip()
    parts = s.split()
    while len(parts) > 2 and parts[0] in ("W", "w") and len(parts[0]) == 1:   # wicket marks printed before names
        parts = parts[1:]
    if not parts:
        return "", ""
    sur = parts[-1].lower()
    initials = "".join(p[0].upper() if len(p) > 1 and not p.isupper() else p.upper() for p in parts[:-1])
    return initials, sur


def compatible(a, b):
    """Initials 'GD' fit 'GD', 'G' fits 'GD', '' fits anything."""
    return not a or not b or b.startswith(a) or a.startswith(b)


def tables(con):
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    out = {}
    for t, cols in REFS.items():
        if t in have:
            real = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            out[t] = [c for c in cols if c in real]
    return out


def main():
    dry = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db = Path(args[0]) if args else DB
    con = sqlite3.connect(db)
    T = tables(con)
    players = {pid: (name, team, full, ci, cs) for pid, name, team, full, ci, cs in
               con.execute("SELECT player_id, name, team, full_name, cricinfo_id, cricsheet_id FROM players")}
    real = {pid for pid, v in players.items() if v[3] or v[4]}
    strays = [pid for pid in players if pid not in real]
    # matches each player appears in (any table), and who appears with whom
    where = defaultdict(set)
    for t, cols in T.items():
        if "match_id" not in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}:
            continue
        for c in cols:
            for mid, pid in con.execute(f"SELECT DISTINCT match_id, {c} FROM {t} WHERE {c} IS NOT NULL"):
                where[pid].add(mid)
    in_match = defaultdict(set)
    for pid in real:
        for mid in where.get(pid, ()):
            in_match[mid].add(pid)
    by_sur = defaultdict(list)
    for pid in real:
        ini, sur = clean(players[pid][0])
        by_sur[sur].append((pid, ini))
    plan, unsure, own = [], [], 0
    for s in strays:
        name, team = players[s][0], players[s][1]
        ini, sur = clean(name)
        if not sur:
            continue
        cands = [(p, i) for p, i in by_sur.get(sur, []) if compatible(ini, i)
                 and (team is None or players[p][1] is None or players[p][1] == team)]
        ms = where.get(s, set())
        if ms:
            # each match votes only if exactly one real player of that team there has this surname + initials
            votes, ambiguous = Counter(), 0
            for mid in ms:
                here = [p for p, _ in cands if p in in_match.get(mid, ())]
                if len(here) == 1:
                    votes[here[0]] += 1
                elif len(here) > 1:
                    ambiguous += 1
            if len(votes) == 1:
                tgt, n = next(iter(votes.items()))
                if n >= max(1, (len(ms) + 1) // 2):
                    plan.append((s, tgt, f"the only {players[tgt][0]} of {team} in {n} of its {len(ms)} matches"))
                    continue
            if cands:
                unsure.append((s, name, team, len(ms), [players[p][0] for p in votes or [p for p, _ in cands]][:4]))
            else:
                own += 1
        elif len(cands) == 1:
            plan.append((s, cands[0][0], "empty record, only one real player with this name and team"))
        elif not cands:
            own += 1
        else:
            unsure.append((s, name, team, 0, [players[p][0] for p, _ in cands][:4]))
    # pass 2: spelling slips ('Shewag' for Sehwag). In each match, a stray name on the balls whose team's scorecard
    # has exactly one player not seen on the balls, with a similar name -> one vote. Joined only if every match agrees.
    planned = {s for s, _, _ in plan}
    card_ids = defaultdict(set)
    for t in ("batting", "bowling", "played"):
        if t in T:
            for mid, pid in con.execute(f"SELECT DISTINCT x.match_id, x.player_id FROM {t} x JOIN matches m "
                                        f"ON m.match_id = x.match_id AND x.source = m.card_source"):
                card_ids[mid].add(pid)
    ball_ids = defaultdict(set)
    for mid, a, b, c in con.execute("SELECT DISTINCT x.match_id, x.batter_id, x.bowler_id, x.non_striker_id FROM balls x "
                                    "JOIN matches m ON m.match_id = x.match_id AND x.source = m.ball_source"):
        ball_ids[mid].update(v for v in (a, b, c) if v is not None)
    votes2 = defaultdict(Counter)
    for mid, onballs in ball_ids.items():
        card = card_ids.get(mid)
        if not card:
            continue
        for team in {players[p][1] for p in onballs - card if p in players}:
            extra = [p for p in onballs - card if p in players and players[p][1] == team and p not in real and p not in planned]
            lost = [p for p in card - onballs if p in players and players[p][1] == team]
            if len(extra) == 1 and len(lost) == 1:
                a, b = clean(players[extra[0]][0])[1], clean(players[lost[0]][0])[1]
                if difflib.SequenceMatcher(None, a, b).ratio() >= 0.6:
                    votes2[extra[0]][lost[0]] += 1
    for s_, v in votes2.items():
        if len(v) == 1:
            tgt, n = next(iter(v.items()))
            ms = [m for m in where.get(s_, ()) if card_ids.get(m)]
            if not compatible(clean(players[s_][0])[0], clean(players[tgt][0])[0]) or \
                    any(tgt not in card_ids[m] or tgt in ball_ids.get(m, ()) for m in ms):
                continue    # initials disagree, or in some match he wasn't playing / both names are on the balls
            plan.append((s_, tgt, f"spelling slip: the only {players[tgt][0]} missing from the balls in {n} match(es)"))
    unsure = [u for u in unsure if u[0] not in {x for x, _, _ in plan}]

    if not dry:
        for s, tgt, why in plan:
            for t, cols in T.items():
                for c in cols:
                    con.execute(f"UPDATE OR IGNORE {t} SET {c} = ? WHERE {c} = ?", (tgt, s))
                    con.execute(f"DELETE FROM {t} WHERE {c} = ?", (s,))     # rows that would now be exact repeats
            for t in ("player_stats", "player_stats_gold"):
                if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone():
                    con.execute(f"DELETE FROM {t} WHERE player_id = ?", (s,))
            con.execute("DELETE FROM players WHERE player_id = ?", (s,))
        con.commit()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["result", "stray_id", "stray_name", "team", "joined_to_id", "joined_to_name", "why / matches", "candidates"])
        for s, tgt, why in plan:
            w.writerow(["joined", s, players[s][0], players[s][1], tgt, players[tgt][0], why, ""])
        for s, name, team, n, c in unsure:
            w.writerow(["NOT joined (unsure)", s, name, team, "", "", n, " | ".join(c)])
    print(("DRY RUN - nothing changed. " if dry else "") +
          f"Records without an ESPN/Cricsheet id: {len(strays)}; joined to the real player: {len(plan)}; "
          f"own players (no look-alike): {own}; look-alikes NOT joined because not certain (listed): {len(unsure)}")
    for s, tgt, why in plan[:12]:
        print(f"  {players[s][0]!r} ({players[s][1]}) -> {players[tgt][0]!r}  [{why}]")
    print(f"  report: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
