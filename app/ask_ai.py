"""
The Ask box. The AI never counts anything: it only turns the question into one read-only SQL query.
The database runs the query; the answer sentence is written from those rows only, and the rows and the query are
shown under it ("how it was counted").

Keys come from the environment (Render's Environment tab, or a local .env that is never committed):
  GROQ_API_KEY     the model that writes the query (openai/gpt-oss-120b on Groq)
  SARVAM_API_KEY   Hindi <-> English (Sarvam's translate API); without it, Hindi questions still work, less well
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import queries as Q  # noqa: E402

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
SARVAM_URL = "https://api.sarvam.ai/translate"

SCHEMA = """
SQLite database of men's international cricket: every Test since 1877 and every ODI since 1971.

VIEWS (use these; they already pick the best source for each match):
bat(match_id, format, start_date, ground, team1, team2, winner, inn, pos, player_id, player, team, how_out, kind,
    bowler, fielder, runs, balls, minutes, fours, sixes, not_out, captain, keeper, opponent)
    -- one row per batter per innings, from the scorecards. EVERY match. kind: 'bowled','caught','lbw','stumped',
       'run out','hit wicket','not out', ... bowler = who got him out (NULL for run outs / not out). balls/minutes/
       fours/sixes can be NULL in old matches.
bowl(match_id, format, start_date, ground, team1, team2, winner, inn, player_id, player, team, overs, balls, maidens,
     runs, wickets, opponent)   -- one row per bowler per innings. EVERY match.
inns(match_id, format, start_date, ground, inn, team, runs, wickets, overs, extras, declared, opponent, winner)
    -- team totals per innings.
ball(match_id, format, start_date, ground, inn, seq, over_no, ball, bowler, bowling_team, batter, batting_team,
     non_striker, bat_runs, extras, extra_type, legal, wicket, kind, player_out, sure, bowler_id, batter_id)
    -- ONE ROW PER DELIVERY, but ONLY for matches with ball-by-ball (most Tests; ODIs mainly from Oct 1998).
       extra_type: NULL (none), 'wd' wide, 'nb' no-ball, 'b' bye, 'lb' leg-bye.
       Balls faced by a batter = rows WHERE extra_type IS NOT 'wd'. Runs off the bat = SUM(bat_runs).
       A dot ball faced = bat_runs = 0 AND extra_type IS NOT 'wd'.
       Out to that bowler = wicket = 1 AND player_out = batter AND (kind IS NULL OR kind NOT IN ('run out',
       'retired hurt','retired out','retired not out','obstructing the field','timed out','handled the ball')).
matches(match_id, format, number, start_date, season, team1, team2, ground, result, winner, toss, umpires, series,
        card_source, ball_source)  -- match_id 'T0001' = Test no. 1, 'O0001' = ODI no. 1. ball_source NULL = no ball-by-ball.
players(player_id, name, team, full_name, born, dob, batting_style, bowling_style)
player_stats(player_id, format, matches, first_year, last_year, runs, wickets)

RULES
- format is exactly 'Test' or 'ODI'. Dates are 'YYYY-MM-DD' text; year = substr(start_date, 1, 4).
- Player names are short scorecard names: 'SR Tendulkar', 'GD McGrath', 'BC Lara', 'Wasim Akram', 'Inzamam-ul-Haq'
  (in old ODIs sometimes 'S Tendulkar'). ALWAYS match names with LIKE on the surname: player LIKE '%Tendulkar%'.
  If a surname is shared (Waugh, Khan, Singh, Malik, Ali, Smith...) add initials or a first name and/or team.
  Team names: 'Australia','England','India','Pakistan','South Africa','West Indies','New Zealand','Sri Lanka',
  'Zimbabwe','Bangladesh','Ireland','Afghanistan', ...
- Group per person with GROUP BY player_id (show player, team).
- Totals over careers: use bat/bowl (complete). Questions about balls, dots, matchups, overs, streaks: use ball, and
  say in "how" that only matches with ball-by-ball are counted.
- Batting average = SUM(runs) / NULLIF(SUM(1 - not_out), 0). Strike rate = 100.0 * runs / balls.
  Bowling average = SUM(runs) / NULLIF(SUM(wickets), 0).
- One SELECT (WITH allowed). Lists: ORDER BY and LIMIT 25 or fewer. Round decimals with ROUND(x, 2).
- Never invent numbers. If the database can't answer (T20s, women's cricket, domestic cricket, Hawk-Eye, fielding
  positions...), return "sql": null and say why in "how".

Reply with JSON only: {"sql": "...", "how": "one plain sentence for a cricket fan on what was counted"}
"""

EXAMPLES = [
    ("How many Test runs did Sachin Tendulkar score?",
     {"sql": "SELECT player, team, COUNT(*) AS innings, SUM(runs) AS runs, MAX(runs) AS highest FROM bat "
             "WHERE format = 'Test' AND player LIKE '%Tendulkar%' GROUP BY player_id",
      "how": "Added up his runs from every Test scorecard."}),
    ("McGrath vs Tendulkar in Tests",
     {"sql": "SELECT batter, bowler, COUNT(DISTINCT match_id) AS matches, SUM(extra_type IS NOT 'wd') AS balls, "
             "SUM(bat_runs) AS runs, SUM(bat_runs = 0 AND extra_type IS NOT 'wd') AS dots, "
             "SUM(wicket = 1 AND player_out = batter AND (kind IS NULL OR kind NOT IN ('run out'))) AS dismissals "
             "FROM ball WHERE format = 'Test' AND batter LIKE '%Tendulkar%' AND bowler LIKE '%McGrath%' "
             "GROUP BY batter_id, bowler_id",
      "how": "Counted every ball McGrath bowled to Tendulkar in Tests that have ball-by-ball records."}),
    ("Most ODI wickets in 1999",
     {"sql": "SELECT player, team, SUM(wickets) AS wickets, COUNT(DISTINCT match_id) AS matches FROM bowl "
             "WHERE format = 'ODI' AND substr(start_date, 1, 4) = '1999' GROUP BY player_id "
             "ORDER BY wickets DESC LIMIT 10",
      "how": "Added up wickets from every ODI scorecard of 1999."}),
]

ANSWER = """You write the answer line for a cricket statistics site.
You get the question, what was counted, and the rows the database returned. Write 1-2 short sentences that answer the
question using ONLY numbers that appear in the rows (copy them exactly; never compute, round or estimate anything new).
If the rows are empty, say the database found nothing for that. No preamble, no markdown."""


class AskError(Exception):
    pass


def _post(url, payload, headers, timeout=40):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Everyball/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise AskError(f"{url.split('/')[2]} said {e.code}: {body}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise AskError(f"Couldn't reach {url.split('/')[2]}: {e}") from None


def groq(messages, max_tokens=900):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise AskError("The Ask box isn't switched on yet (GROQ_API_KEY is not set on the server).")
    payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0, "max_completion_tokens": max_tokens,
               "reasoning_effort": "low"}
    try:
        out = _post(GROQ_URL, payload, {"Authorization": f"Bearer {key}"})
    except AskError as e:
        if "reasoning_effort" not in str(e):
            raise
        payload.pop("reasoning_effort")
        out = _post(GROQ_URL, payload, {"Authorization": f"Bearer {key}"})
    return (out["choices"][0]["message"].get("content") or "").strip()


def is_hindi(text):
    return bool(re.search(r"[ऀ-ॿ]", text or ""))


def sarvam(text, src, dst):
    key = os.environ.get("SARVAM_API_KEY")
    if not key or not text:
        return None
    out = _post(SARVAM_URL, {"input": text[:990], "source_language_code": src, "target_language_code": dst,
                             "model": "mayura:v1", "mode": "formal"}, {"api-subscription-key": key}, timeout=20)
    return out.get("translated_text")


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise AskError("The model didn't return a query. Try asking a little differently.")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        raise AskError("The model's reply couldn't be read. Try again.") from None


def run_limited(con, sql, seconds=6.0):
    """Run the query with a time limit, so one heavy question can't hold up the site."""
    deadline = time.monotonic() + seconds
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    try:
        return Q.run_sql(con, sql, limit=200)
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise AskError("That question took too long for the database. Try narrowing it (a format, a year, a team).")
        raise
    finally:
        con.set_progress_handler(None, 0)


def ask(con, question):
    question = (question or "").strip()[:500]
    if len(question) < 3:
        raise AskError("Ask a question about Test or ODI cricket.")
    hindi = is_hindi(question)
    q_en = question
    if hindi:
        try:
            q_en = sarvam(question, "hi-IN", "en-IN") or question
        except AskError:
            q_en = question
    msgs = [{"role": "system", "content": SCHEMA}]
    for q, a in EXAMPLES:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": json.dumps(a)}]
    msgs.append({"role": "user", "content": q_en})
    plan = parse_json(groq(msgs))
    sql, how = plan.get("sql"), plan.get("how") or ""
    if not sql:
        raise AskError(how or "The database can't answer that one.")
    try:
        cols, rows = run_limited(con, sql)
    except (sqlite3.Error, ValueError) as e:            # one retry: show the model its error
        msgs += [{"role": "assistant", "content": json.dumps(plan)},
                 {"role": "user", "content": f"That query failed: {e}. Reply with corrected JSON only."}]
        plan = parse_json(groq(msgs))
        sql, how = plan.get("sql"), plan.get("how") or how
        if not sql:
            raise AskError(how or "The database can't answer that one.")
        try:
            cols, rows = run_limited(con, sql)
        except (sqlite3.Error, ValueError) as e2:
            raise AskError(f"The query didn't work ({e2}). Try asking a little differently.") from None
    shown = rows[:25]
    answer = groq([{"role": "system", "content": ANSWER},
                   {"role": "user", "content": json.dumps({"question": q_en, "what_was_counted": how,
                                                           "columns": cols, "rows": shown}, default=str)}],
                  max_tokens=400)
    out = {"question": question, "question_en": q_en if hindi else None, "sql": sql, "how": how,
           "columns": cols, "rows": rows, "answer": answer, "answer_hi": None}
    if hindi:
        try:
            out["answer_hi"] = sarvam(answer, "en-IN", "hi-IN")
        except AskError:
            pass
    return out
