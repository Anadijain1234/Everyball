"""Cricket scoring rules, by era, for checking ball-by-ball data.
Every rule here is written in plain words next to the code. Sources: docs/CRICKET_RULES.md.

Used by check_bbb.py. One function per question the checker asks:
  balls_per_over(date, country)      how many legal balls make an over
  era(date, fmt, country)            which version of each rule applied on that date
  parse_extras(text)                 "wd1", "nb1+lb2", "lb4", "pen5", "nb0" ... -> parts
  outcome(ball, era)                 what one delivery does: runs, legal?, faced?, runs physically run
  dismissal_ok(how, kind, era)       can a batter be out this way on a wide / no-ball / free hit?
  bowler_gets_wicket(how)            is the wicket credited to the bowler?
"""
import re
from datetime import date as D


def d(s):
    y, m, dd = (int(x) for x in str(s)[:10].split("-"))
    return D(y, m, dd)


# ---------------------------------------------------------------- 1. balls per over (Law 17)
# By the country the match is played in. Season "1918/19" = 1 Jul 1918 to 30 Jun 1919.
# Australia's 6-ball Tests 1928/29-1932/33 (and SA 1931/32) are exceptions inside the 8-ball years.
BPO = {
    "Australia":    [("1876-07-01", 4), ("1888-07-01", 6), ("1921-07-01", 8), ("1928-07-01", 6),
                     ("1933-07-01", 8), ("1979-07-01", 6)],
    "England":      [("1880-01-01", 4), ("1889-01-01", 5), ("1900-01-01", 6), ("1939-01-01", 8),
                     ("1946-01-01", 6)],
    "South Africa": [("1888-07-01", 4), ("1889-07-01", 5), ("1899-07-01", 6), ("1938-07-01", 8),
                     ("1961-07-01", 6)],
    "New Zealand":  [("1929-07-01", 6), ("1968-07-01", 8), ("1979-07-01", 6)],
    "Pakistan":     [("1954-07-01", 6), ("1973-07-01", 8), ("1978-07-01", 6)],
}


def balls_per_over(day, country):
    """Normal over length. An over the umpire miscounts (5 or 7 balls) still stands (Law 17.5):
    the checker warns, it does not reject."""
    day = d(day)
    if day >= D(1979, 7, 1):
        return 6
    bpo = 6
    for start, n in BPO.get(country, []):
        if day >= d(start):
            bpo = n
    return bpo


# ---------------------------------------------------------------- 2. which rule applied when
DEBIT_FROM = {  # first match in that country where wides and no-balls were charged to the bowler
    "England": "1983-06-13", "India": "1983-09-14", "Australia": "1983-11-11", "West Indies": "1984-03-02",
    "Pakistan": "1984-10-17", "New Zealand": "1985-01-18", "Sri Lanka": "1985-08-30",
    "South Africa": "1992-11-13"}


def era(day, fmt="ODI", country=""):
    day = d(day)
    e = {"date": day, "format": fmt, "country": country, "bpo": balls_per_over(day, country)}
    # Penalty run for a no-ball / wide.
    #   Before 30 Sep 1998 (1980 Code, Law 24/25): ONE run is scored only "if no runs are made otherwise".
    #     A no-ball hit for 4 = 4 to the batter and NO extra.  Byes run off a wide = all wides, no extra +1.
    #   From 30 Sep 1998 (ICC Tests/ODIs; 2000 Code for everyone): 1 run penalty PLUS any runs.
    e["penalty_plus_runs"] = day >= D(1998, 9, 30)
    # Byes / leg-byes run off a no-ball: scored as no-balls until the 2017 Code (1 Oct 2017);
    # from then only the 1-run penalty is a no-ball, the rest are byes / leg-byes.
    e["byes_off_nb_are_byes"] = day >= D(2017, 10, 1)
    # Wides and no-balls in the bowler's figures (by country, 1983-1992). Before that: only runs off the bat.
    e["extras_to_bowler"] = day >= d(DEBIT_FROM.get(country, "1983-06-13"))
    # Caught: from 1 Oct 2022 the new batter always takes strike. Before: if the batters crossed
    # before the catch, the not-out batter is at the striker's end and faces the next ball.
    e["caught_new_on_strike"] = day >= D(2022, 10, 1)
    # Free hit after a no-ball (limited-overs only): foot-fault no-balls from 29 Sep 2007, all no-balls
    # from 5 Jul 2015. On a free hit the striker can only be out the ways allowed off a no-ball.
    e["free_hit"] = fmt != "Test" and day >= D(2007, 9, 29)
    # "Handled the ball" was its own dismissal until the 2017 Code; after that it is "obstructing the field".
    e["handled_exists"] = day < D(2017, 10, 1)
    # Non-striker run out by the bowler before delivery ("Mankad"): a run out under Law 38 from 1 Oct 2022
    # (before: Law 41 unfair play, still recorded as run out). Either way the ball is NOT one of the over.
    e["runners_allowed"] = day < D(2011, 10, 1)        # runners for injured batters banned 1 Oct 2011
    e["concussion_subs"] = day >= D(2019, 8, 1)         # a substitute may bat/bowl from 1 Aug 2019
    e["five_penalty_runs"] = True   # existed in old Laws (ball stopped illegally); many more cases from 2000
    return e


def bowler_max_overs(match_overs):
    """ODIs: a bowler may bowl a fifth of the innings, rounded up (60 -> 12, 55 -> 11, 50 -> 10, 40 -> 8)."""
    return -(-match_overs // 5)


# ---------------------------------------------------------------- 3. reading the extras column
EX = re.compile(r"(wd|w|nb|lb|b|pen|p)\s*(\d*)")


def parse_extras(text):
    """'0' / '' -> {}.  'w1'/'wd1' wide.  'nb1' no-ball.  'nb0' no-ball, no penalty run (1980 Code, runs
    made otherwise).  'lb2', 'b4' byes.  'pen5' penalty runs.  Several parts: 'nb1+lb2'.
    Also accepts 'lb4 or b4' (a bye of some sort) and words like 'wide 1', 'leg bye 2'."""
    t = (text or "").lower().strip()
    t = (t.replace("leg bye", "lb").replace("legbye", "lb").replace("no ball", "nb").replace("no-ball", "nb")
          .replace("noball", "nb").replace("wides", "wd").replace("wide", "wd").replace("byes", "b")
          .replace("bye", "b").replace("penalty", "pen"))
    if t in ("", "0", "none", "-"):
        return {}
    if " or " in t:                       # "lb4 or b4": same runs, kind unsure -> keep the first
        t = t.split(" or ")[0]
    out = {}
    for k, n in EX.findall(t):
        k = {"w": "wd", "p": "pen"}.get(k, k)
        out[k] = out.get(k, 0) + (int(n) if n != "" else (1 if k in ("wd", "nb") else 0))
    if not out:
        raise ValueError(f"unknown extras: {text!r}")
    return out


# ---------------------------------------------------------------- 4. one delivery
def outcome(bat, ex, e, note=""):
    """bat = runs off the bat, ex = parse_extras(...), e = era(...), note = free text ('short', 'mankad',
    'dead ball', 'crossed', 'not crossed').
    Returns dict:
      runs        team runs from this delivery
      legal       counts as one of the over (not a wide, not a no-ball, not a Mankad / dead ball)
      faced       counts as a ball faced by the striker (legal balls AND no-balls; never wides)
      ran         possible numbers of runs the batters ran. Only odd/even matters: odd = they swapped ends.
                  Boundaries (4, 6) are even, so "runs scored" and "runs run" have the same odd/even except:
                  a short run (ran 2, scored 1) and the 1980-Code penalty run (see era()).
      to_bowler   runs charged to the bowler (era rule)
      warnings    rule problems"""
    note = (note or "").lower()
    has_wd, has_nb = "wd" in ex, "nb" in ex
    wd, nb, b, lb, pen = (ex.get(k, 0) for k in ("wd", "nb", "b", "lb", "pen"))
    w = []
    if has_wd and has_nb:
        w.append("wide AND no-ball on one ball: a no-ball overrides a wide (Law 21/22), write only nb")
    if has_wd and bat:
        w.append("runs off the bat on a wide: impossible (if the bat touches it, it is not a wide)")
    if (b or lb) and bat:
        w.append("byes/leg-byes AND bat runs on one ball: impossible")
    if has_wd and (b or lb):
        w.append("byes on a wide: all runs off a wide are wides (write wdN)")
    if pen and pen % 5:
        w.append("penalty runs come in fives (Law 41/28)")
    plus = e["penalty_plus_runs"]
    if has_wd:
        # 1998+: wdN = 1 penalty + (N-1) run.  1980 Code: wdN = N run (penalty only when nothing was run).
        ran = {wd - 1} if plus else ({0, 1} if wd == 1 else {wd, wd - 1})
        if wd == 0:
            w.append("wd0 is impossible: a wide always adds at least 1")
            ran = {0}
    elif has_nb:
        if plus:
            if nb == 0:
                w.append("nb0: from 30 Sep 1998 a no-ball always adds 1 run")
            ran = {bat + b + lb + max(nb - 1, 0)}
            if (b or lb) and not e["byes_off_nb_are_byes"]:
                w.append("before Oct 2017 byes off a no-ball were scored as no-balls: put them in nb")
        else:
            # 1980 Code: the penalty run is scored only "if no runs are made otherwise".
            if bat and nb:
                w.append("1980 Code: a no-ball hit for runs gave NO extra run (write nb0 + bat runs) "
                         "- unless this series used an extra-run playing condition; check the scorecard")
                ran = {bat}
            elif bat:
                ran = {bat}
            elif nb == 1:
                ran = {0, 1}         # penalty only, or 1 run scored as a no-ball
            else:
                ran = {nb, nb - 1}
    else:
        ran = {bat + b + lb}
    if "short" in note:
        ran = {x + 1 for x in ran}   # one short: they ran one more than was scored
    to_bowler = bat + ((wd + nb) if e["extras_to_bowler"] else 0)   # byes / leg-byes / penalties never
    return {"runs": bat + wd + nb + b + lb + pen,
            "legal": not has_wd and not has_nb and "mankad" not in note and "dead ball" not in note,
            "faced": not has_wd and "mankad" not in note and "dead ball" not in note,
            "ran": ran, "to_bowler": to_bowler, "warnings": w}


# ---------------------------------------------------------------- 5. dismissals (Laws 30-40)
CREDITED = ("bowled", "caught", "lbw", "stumped", "hit wicket")
NOT_CREDITED = ("run out", "obstructing", "handled", "hit the ball twice", "timed out", "retired out")


def kind_of(how):
    h = (how or "none").lower()
    if h in ("", "none", "0", "-", "not out"):
        return None
    for k in ("run out", "retired hurt", "retired not out", "retired out", "retired", "hit wicket",
              "stumped", "lbw", "bowled", "caught", "obstruct", "handled", "twice", "timed out"):
        if k in h:
            return {"obstruct": "obstructing", "twice": "hit the ball twice", "retired": "retired hurt",
                    "retired not out": "retired hurt"}.get(k, k)
    if h.startswith("c ") or " c " in h or h.startswith("c&b") or "c and b" in h:
        return "caught"
    if h.startswith("b ") or h == "b":
        return "bowled"
    if h.startswith("st "):
        return "stumped"
    return "unknown"


def dismissal_ok(kind, ex, e, free_hit=False):
    """Off a no-ball (and a free hit): only run out, hit the ball twice, obstructing (+ handled before 2017).
    Off a wide: stumped, hit wicket, run out, obstructing (+ handled before 2017). Never bowled/lbw/caught."""
    if kind is None or kind in ("retired hurt", "retired out", "timed out"):
        return True
    on_nb = {"run out", "hit the ball twice", "obstructing", "handled"}
    on_wd = {"stumped", "hit wicket", "run out", "obstructing", "handled"}
    if kind == "handled" and not e["handled_exists"]:
        return False
    if ex.get("nb") or free_hit:
        if free_hit and ex.get("wd"):
            return kind in on_nb
        return kind in on_nb
    if ex.get("wd"):
        return kind in on_wd
    return True


def bowler_gets_wicket(kind):
    return kind in CREDITED


def is_wicket(kind):
    """Retired hurt / retired not out is NOT a wicket (the batter may come back). Retired out IS."""
    return kind not in (None, "retired hurt")
