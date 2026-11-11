"""
Ready-made questions, shared by the command line (scripts/ask.py) and the website (app/).
Every function returns plain data (dicts and lists), so the same numbers appear everywhere.
"""
import sqlite3

import common as C

BOWLER_KINDS = {None, "out", "bowled", "caught", "lbw", "stumped", "caught and bowled", "hit wicket"}


def connect_ro(path=None):
    """Read-only connection: nothing asked here can change the database."""
    con = sqlite3.connect(f"file:{path or C.DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def find_players(con, text, limit=12):
    """'Tendulkar', 'sachin', 'SR Tendulkar' -> matching players, most-capped first."""
    t = (text or "").strip()
    if not t:
        return []
    like = f"%{t}%"
    rows = con.execute("""
        SELECT p.player_id, p.name, p.team, p.full_name,
               (SELECT COUNT(DISTINCT match_id) FROM batting b WHERE b.player_id = p.player_id) +
               (SELECT COUNT(DISTINCT match_id) FROM bowling w WHERE w.player_id = p.player_id) AS games
        FROM players p
        WHERE p.name = ? COLLATE NOCASE OR p.name LIKE ? OR p.full_name LIKE ?
        ORDER BY (p.name = ? COLLATE NOCASE) DESC, games DESC LIMIT ?""", (t, like, like, t, limit)).fetchall()
    return [dict(r) for r in rows]


def ids_for(con, text):
    """All player_ids of the best-matching person (same name, possibly several teams)."""
    hits = find_players(con, text, 30)
    if not hits:
        return [], None
    name = hits[0]["name"]
    return [h["player_id"] for h in hits if h["name"] == name], hits[0]


def duel(con, batter, bowler, fmt=None):
    """Everything about batter vs bowler, looked up by name (command line)."""
    bat_ids, bat = ids_for(con, batter)
    bowl_ids, bowl = ids_for(con, bowler)
    if not bat_ids or not bowl_ids:
        return {"error": f"Couldn't find {'batter ' + repr(batter) if not bat_ids else 'bowler ' + repr(bowler)}"}
    return duel_ids(con, bat_ids, bowl_ids, fmt, bat, bowl)


def person_row(con, pid):
    r = con.execute("SELECT player_id, name, team, full_name, born, dob, batting_style, bowling_style, role "
                    "FROM players WHERE player_id = ?", (pid,)).fetchone()
    return dict(r) if r else None


def duel_ids(con, bat_ids, bowl_ids, fmt=None, bat=None, bowl=None):
    """Everything about batter vs bowler: balls, runs, dismissals, dots, longest dot streak, match by match,
    and every ball as a short code ('0', '1', '4', 'W', 'wd', 'nb1', ...) for the ball strip."""
    bat = bat or person_row(con, bat_ids[0])
    bowl = bowl or person_row(con, bowl_ids[0])
    qb, qw = ",".join("?" * len(bat_ids)), ",".join("?" * len(bowl_ids))
    fmt_sql = "AND m.format = ?" if fmt else ""
    args = [*bat_ids, *bowl_ids] + ([fmt] if fmt else [])
    rows = con.execute(f"""
        SELECT x.match_id, m.format, m.start_date, m.ground, m.team1, m.team2, x.inn, x.seq, x.over_no, x.bat_runs,
               x.extra_type, x.extras, x.legal, x.wicket, x.kind, x.out_id, x.sure, x.source
        FROM balls x JOIN matches m ON m.match_id = x.match_id AND x.source = m.ball_source
        WHERE x.batter_id IN ({qb}) AND x.bowler_id IN ({qw}) {fmt_sql}
        ORDER BY m.start_date, x.match_id, x.inn, x.seq""", args).fetchall()
    per_match, streak, best = {}, 0, (0, None)
    sources, outs_list = {}, []
    outcome = {k: 0 for k in ("0", "1", "2", "3", "4", "5+", "6", "W", "extras")}
    s = {"balls": 0, "runs": 0, "outs": 0, "dots": 0, "fours": 0, "sixes": 0, "sure": 0, "all": 0}
    last_match = None
    for r in rows:
        if r["match_id"] != last_match:
            streak, last_match = 0, r["match_id"]
        pm = per_match.setdefault(r["match_id"], {"match_id": r["match_id"], "format": r["format"],
                                                  "date": r["start_date"], "ground": r["ground"],
                                                  "teams": f"{r['team1']} v {r['team2']}",
                                                  "balls": 0, "runs": 0, "out": 0, "dots": 0, "codes": []})
        s["all"] += 1
        s["sure"] += r["sure"] or 0
        sources[r["source"]] = sources.get(r["source"], 0) + 1
        out_here = bool(r["wicket"] and r["out_id"] in bat_ids and r["kind"] in BOWLER_KINDS)
        code = "W" if out_here else ("wd" if r["extra_type"] == "wd" else
                                     f"nb{r['bat_runs']}" if r["extra_type"] == "nb" else
                                     f"{r['extra_type']}{r['extras']}" if r["extra_type"] in ("b", "lb") else
                                     str(r["bat_runs"]))
        pm["codes"].append(code)
        outcome[code if code in ("0", "1", "2", "3", "4", "6", "W") else ("5+" if code.isdigit() else "extras")] += 1
        if r["extra_type"] == "wd":
            continue
        s["balls"] += 1
        pm["balls"] += 1
        s["runs"] += r["bat_runs"]
        pm["runs"] += r["bat_runs"]
        s["fours"] += r["bat_runs"] == 4
        s["sixes"] += r["bat_runs"] == 6
        if r["bat_runs"] == 0:
            s["dots"] += 1
            pm["dots"] += 1
            streak += 1
            if streak > best[0]:
                best = (streak, r["match_id"])
        else:
            streak = 0
        if out_here:
            s["outs"] += 1
            pm["out"] = 1
            outs_list.append({"match_id": r["match_id"], "date": r["start_date"], "ground": r["ground"],
                              "kind": r["kind"] or "out", "inn": r["inn"], "over": r["over_no"]})
    # matches where both played (from the scorecards) and how often he got him out there
    both = con.execute(f"""
        SELECT COUNT(DISTINCT b.match_id), SUM(CASE WHEN b.bowler_id IN ({qw}) THEN 1 ELSE 0 END)
        FROM batting b JOIN matches m ON m.match_id = b.match_id AND b.source = m.card_source
        WHERE b.player_id IN ({qb}) {fmt_sql}
          AND EXISTS (SELECT 1 FROM bowling w WHERE w.match_id = b.match_id AND w.inn = b.inn AND w.source = b.source
                      AND w.player_id IN ({qw}))""", [*bowl_ids, *bat_ids] + ([fmt] if fmt else []) + bowl_ids).fetchone()
    pvp = con.execute(f"""SELECT SUM(balls), SUM(runs), COUNT(DISTINCT match_id) FROM pvp
                          WHERE batter_id IN ({qb}) AND bowler_id IN ({qw})""", [*bat_ids, *bowl_ids]).fetchone()
    s.update(
        batter=bat["name"], batter_team=bat["team"], bowler=bowl["name"], bowler_team=bowl["team"], format=fmt,
        average=round(s["runs"] / s["outs"], 2) if s["outs"] else None,
        strike_rate=round(100 * s["runs"] / s["balls"], 1) if s["balls"] else None,
        dot_pct=round(100 * s["dots"] / s["balls"], 1) if s["balls"] else None,
        longest_dot_streak=best[0], longest_dot_streak_match=best[1],
        matches_with_balls=len(per_match), matches_both_played=both[0] or 0,
        dismissed_in_scorecards=both[1] or 0, sure_pct=round(100 * s["sure"] / s["all"], 1) if s["all"] else None,
        espn_player_v_player={"balls": pvp[0], "runs": pvp[1], "matches": pvp[2]} if pvp and pvp[0] else None,
        matches=list(per_match.values()), ball_sources=sources, outcomes=outcome, dismissals=outs_list,
        batter_id=bat["player_id"], bowler_id=bowl["player_id"],
        scorecard_dismissals=[dict(r) for r in con.execute(f"""
            SELECT b.match_id, m.start_date AS date, m.ground, b.kind, b.runs, m.ball_source IS NOT NULL AS has_balls
            FROM batting b JOIN matches m ON m.match_id = b.match_id AND b.source = m.card_source
            WHERE b.player_id IN ({qb}) AND b.bowler_id IN ({qw}) {fmt_sql} ORDER BY m.start_date""",
            [*bat_ids, *bowl_ids] + ([fmt] if fmt else []))])
    return s


def player(con, name, fmt=None):
    ids, p = ids_for(con, name)
    if not ids:
        return {"error": f"Couldn't find {name!r}"}
    q = ",".join("?" * len(ids))
    f_sql = "AND format = ?" if fmt else ""
    args = ids + ([fmt] if fmt else [])
    b = con.execute(f"""
        SELECT COUNT(DISTINCT x.match_id) m, COUNT(*) i, SUM(x.runs) r, SUM(x.not_out) no, MAX(x.runs) hs,
               SUM(CASE WHEN x.runs >= 100 THEN 1 ELSE 0 END) hundreds,
               SUM(CASE WHEN x.runs BETWEEN 50 AND 99 THEN 1 ELSE 0 END) fifties,
               SUM(x.balls) bf, SUM(CASE WHEN x.balls IS NOT NULL THEN x.runs END) rb
        FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        WHERE x.player_id IN ({q}) {f_sql.replace('format', 'm.format')}""", args).fetchone()
    w = con.execute(f"""
        SELECT COUNT(DISTINCT x.match_id) m, SUM(x.balls) balls, SUM(x.runs) r, SUM(x.wickets) wk
        FROM bowling x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        WHERE x.player_id IN ({q}) {f_sql.replace('format', 'm.format')}""", args).fetchone()
    kinds = con.execute(f"""
        SELECT x.kind, COUNT(*) n FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        WHERE x.player_id IN ({q}) AND x.not_out = 0 {f_sql.replace('format', 'm.format')}
        GROUP BY 1 ORDER BY 2 DESC""", args).fetchall()
    outs = (b["i"] or 0) - (b["no"] or 0)
    total_outs = sum(k["n"] for k in kinds) or 1
    return {
        "name": p["name"], "team": p["team"], "full_name": p.get("full_name"), "format": fmt,
        "batting": {"matches": b["m"], "innings": b["i"], "runs": b["r"], "not_outs": b["no"], "highest": b["hs"],
                    "average": round(b["r"] / outs, 2) if outs > 0 and b["r"] is not None else None,
                    "hundreds": b["hundreds"], "fifties": b["fifties"],
                    "strike_rate": round(100 * b["rb"] / b["bf"], 1) if b["bf"] else None},
        "bowling": {"matches": w["m"], "wickets": w["wk"], "runs": w["r"],
                    "average": round(w["r"] / w["wk"], 2) if w["wk"] else None,
                    "economy": round(6 * w["r"] / w["balls"], 2) if w["balls"] else None},
        "dismissals": [{"kind": k["kind"], "count": k["n"], "pct": round(100 * k["n"] / total_outs, 1)} for k in kinds],
    }


def search(con, text, limit=8):
    """Autocomplete: every word must appear in the short or full name ('sachin tend', 'mcgrath')."""
    words = [w for w in (text or "").replace("%", "").split() if w][:4]
    if not words:
        return []
    where = " AND ".join(["(p.name || ' ' || COALESCE(p.full_name, '')) LIKE ?"] * len(words))
    rows = con.execute(f"""
        SELECT p.player_id, p.name, p.team, p.full_name, COALESCE(SUM(s.matches), 0) AS games,
               MIN(s.first_year) AS first_year, MAX(s.last_year) AS last_year,
               GROUP_CONCAT(s.format || ' ' || s.matches, ' · ') AS formats
        FROM players p LEFT JOIN player_stats s ON s.player_id = p.player_id
        WHERE {where} GROUP BY p.player_id
        ORDER BY (p.name = ? COLLATE NOCASE) DESC, games DESC LIMIT ?""",
                       [f"%{w}%" for w in words] + [" ".join(words), limit]).fetchall()
    rows = [dict(r) for r in rows]
    return [r for r in rows if r["games"]] or rows


def player_page(con, pid, fmt=None):
    """A player's page: totals per format, year by year, how he got out, his rivals."""
    p = person_row(con, pid)
    if not p:
        return None
    f_sql, fa = ("AND m.format = ?", [fmt]) if fmt else ("", [])
    formats = []
    for f in ("Test", "ODI"):
        b = con.execute("""
            SELECT COUNT(DISTINCT x.match_id) m, COUNT(*) i, SUM(x.runs) r, SUM(x.not_out) no, MAX(x.runs) hs,
                   SUM(x.runs >= 100) h, SUM(x.runs BETWEEN 50 AND 99) f, SUM(x.balls) bf,
                   SUM(CASE WHEN x.balls IS NOT NULL THEN x.runs END) rb, SUM(x.fours) fours, SUM(x.sixes) sixes
            FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
            WHERE x.player_id = ? AND m.format = ?""", (pid, f)).fetchone()
        w = con.execute("""
            SELECT COUNT(DISTINCT x.match_id) m, SUM(x.balls) balls, SUM(x.runs) r, SUM(x.wickets) wk,
                   SUM(x.wickets >= 5) five, MAX(x.wickets * 1000 - x.runs) best
            FROM bowling x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
            WHERE x.player_id = ? AND m.format = ?""", (pid, f)).fetchone()
        if not (b["i"] or w["m"]):
            continue
        outs = (b["i"] or 0) - (b["no"] or 0)
        best = w["best"]
        formats.append({
            "format": f,
            "batting": {"matches": b["m"], "innings": b["i"], "runs": b["r"], "not_outs": b["no"], "highest": b["hs"],
                        "average": round(b["r"] / outs, 2) if outs > 0 and b["r"] is not None else None,
                        "hundreds": b["h"], "fifties": b["f"], "fours": b["fours"], "sixes": b["sixes"],
                        "strike_rate": round(100 * b["rb"] / b["bf"], 1) if b["bf"] else None},
            "bowling": {"matches": w["m"], "wickets": w["wk"] or 0, "runs": w["r"], "five_fors": w["five"],
                        "best": f"{(best + 999) // 1000}/{(-best) % 1000}" if best is not None and w["wk"] else None,
                        "average": round(w["r"] / w["wk"], 2) if w["wk"] else None,
                        "economy": round(6 * w["r"] / w["balls"], 2) if w["balls"] else None}})
    years = [dict(r) for r in con.execute(f"""
        SELECT substr(m.start_date, 1, 4) AS year, SUM(r) AS runs, SUM(w) AS wickets, COUNT(DISTINCT m.match_id) AS matches
        FROM (SELECT match_id, source, runs AS r, 0 AS w FROM batting WHERE player_id = ?
              UNION ALL SELECT match_id, source, 0, wickets FROM bowling WHERE player_id = ?) x
        JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        WHERE 1 {f_sql} GROUP BY 1 ORDER BY 1""", [pid, pid] + fa)]
    kinds = [dict(r) for r in con.execute(f"""
        SELECT COALESCE(x.kind, 'out') AS kind, COUNT(*) AS n
        FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        WHERE x.player_id = ? AND x.not_out = 0 {f_sql} GROUP BY 1 ORDER BY 2 DESC""", [pid] + fa)]
    got_him = [dict(r) for r in con.execute(f"""
        SELECT x.bowler_id AS player_id, q.name, q.team, COUNT(*) AS n
        FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        JOIN players q ON q.player_id = x.bowler_id
        WHERE x.player_id = ? {f_sql} GROUP BY 1 ORDER BY 4 DESC LIMIT 8""", [pid] + fa)]
    he_got = [dict(r) for r in con.execute(f"""
        SELECT x.player_id, q.name, q.team, COUNT(*) AS n
        FROM batting x JOIN matches m ON m.match_id = x.match_id AND x.source = m.card_source
        JOIN players q ON q.player_id = x.player_id
        WHERE x.bowler_id = ? {f_sql} GROUP BY 1 ORDER BY 4 DESC LIMIT 8""", [pid] + fa)]
    out_kinds = "(x.kind IS NULL OR x.kind IN ('" + "','".join(k for k in BOWLER_KINDS if k) + "'))"
    faced = [dict(r) for r in con.execute(f"""
        SELECT x.bowler_id AS player_id, q.name, q.team, SUM(x.extra_type IS NOT 'wd') AS balls, SUM(x.bat_runs) AS runs,
               SUM(x.wicket = 1 AND x.out_id = ? AND {out_kinds}) AS outs
        FROM balls x JOIN matches m ON m.match_id = x.match_id AND x.source = m.ball_source
        JOIN players q ON q.player_id = x.bowler_id
        WHERE x.batter_id = ? {f_sql} GROUP BY 1 ORDER BY 4 DESC LIMIT 10""", [pid, pid] + fa)]
    bowled_to = [dict(r) for r in con.execute(f"""
        SELECT x.batter_id AS player_id, q.name, q.team, SUM(x.extra_type IS NOT 'wd') AS balls, SUM(x.bat_runs) AS runs,
               SUM(x.wicket = 1 AND x.out_id = x.batter_id AND {out_kinds}) AS outs
        FROM balls x JOIN matches m ON m.match_id = x.match_id AND x.source = m.ball_source
        JOIN players q ON q.player_id = x.batter_id
        WHERE x.bowler_id = ? {f_sql} GROUP BY 1 ORDER BY 4 DESC LIMIT 10""", [pid] + fa)]
    return {"player": p, "format": fmt, "formats": formats, "years": years, "dismissals": kinds,
            "dismissed_by": got_him, "dismissed": he_got, "faced_most": faced, "bowled_to_most": bowled_to}


def coverage(con):
    """How much we have: matches per format and decade, how many ball by ball, and ESPN's own count if known."""
    out = {"formats": [], "decades": []}
    for f in ("Test", "ODI"):
        n, b = con.execute("SELECT COUNT(*), SUM(ball_source IS NOT NULL) FROM matches WHERE format = ?", (f,)).fetchone()
        ref = con.execute("SELECT COUNT(*) FROM ref_matches WHERE format = ?", (f,)).fetchone()[0]
        balls = con.execute("SELECT COUNT(*) FROM balls x JOIN matches m ON m.match_id = x.match_id AND "
                            "x.source = m.ball_source WHERE m.format = ?", (f,)).fetchone()[0]
        first, last = con.execute("SELECT MIN(start_date), MAX(start_date) FROM matches WHERE format = ?", (f,)).fetchone()
        srcs = dict(con.execute("SELECT ball_source, COUNT(*) FROM matches WHERE format = ? AND ball_source IS NOT NULL "
                                "GROUP BY 1", (f,)).fetchall())
        out["formats"].append({"format": f, "matches": n or 0, "with_balls": b or 0, "espn_count": ref or None,
                               "balls": balls, "first": first, "last": last, "ball_sources": srcs})
    out["decades"] = [dict(r) for r in con.execute("""
        SELECT format, substr(start_date, 1, 3) || '0s' AS decade, COUNT(*) AS matches,
               SUM(ball_source IS NOT NULL) AS with_balls
        FROM matches WHERE start_date IS NOT NULL GROUP BY 1, 2 ORDER BY 1 DESC, 2""")]
    out["players"] = con.execute("SELECT COUNT(*) FROM player_stats").fetchone()[0]
    return out


def run_sql(con, sql, limit=200):
    """Run one read-only SELECT (used by the Ask box and `ask.py sql`)."""
    s = sql.strip().rstrip(";")
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")) or ";" in s:
        raise ValueError("Only a single SELECT question is allowed.")
    for bad in ("attach", "pragma", "insert", "update", "delete", "drop", "create", "alter", "replace", "vacuum"):
        if f" {bad} " in f" {low} ":
            raise ValueError(f"'{bad}' is not allowed.")
    cur = con.execute(s)
    cols = [d[0] for d in cur.description]
    return cols, [list(r) for r in cur.fetchmany(limit)]
