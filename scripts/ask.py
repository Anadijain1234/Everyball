"""
Ask the database from the terminal.

  python scripts/ask.py duel Tendulkar McGrath          # every ball between them (Tests and ODIs)
  python scripts/ask.py duel Tendulkar McGrath Test     # Tests only
  python scripts/ask.py player Dravid                   # career summary + how he got out
  python scripts/ask.py player Dravid ODI
  python scripts/ask.py find Gregory                    # which players match a name
  python scripts/ask.py sql "SELECT player, SUM(runs) FROM bat WHERE format='Test' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"

Easy tables for `sql`: bat (batting cards), bowl (bowling cards), ball (every ball, with names), matches, players.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import queries as Q  # noqa: E402


def show_duel(d):
    if "error" in d:
        return print(d["error"])
    fmt = f" ({d['format']}s)" if d["format"] else ""
    print(f"\n{d['batter']} ({d['batter_team']}) vs {d['bowler']} ({d['bowler_team']}){fmt}")
    print(f"  Balls {d['balls']:,}   Runs {d['runs']:,}   Dismissals {d['outs']}   Average {d['average']}   "
          f"Strike rate {d['strike_rate']}   Dot balls {d['dot_pct']}%   4s {d['fours']}   6s {d['sixes']}")
    print(f"  Longest run of dot balls: {d['longest_dot_streak']} ({d['longest_dot_streak_match']})")
    print(f"  Coverage: ball-by-ball for {d['matches_with_balls']} of {d['matches_both_played']} matches where both "
          f"played; batter-faced balls proven over by over: {d['sure_pct']}%")
    print(f"  From the scorecards (all matches): {d['bowler']} dismissed {d['batter']} {d['dismissed_in_scorecards']} times")
    if d["espn_player_v_player"]:
        p = d["espn_player_v_player"]
        print(f"  ESPN player-v-player tables: {p['runs']} runs off {p['balls']} balls in {p['matches']} matches")
    if d["matches"]:
        print("  Match by match:")
        for m in d["matches"]:
            print(f"    {m['date']}  {m['format']:4} {m['ground'][:28]:28} {m['runs']:>4} off {m['balls']:>4}"
                  f"{'  OUT' if m['out'] else ''}")


def show_player(p):
    if "error" in p:
        return print(p["error"])
    print(f"\n{p['name']} ({p['team']}){' - ' + p['full_name'] if p.get('full_name') else ''}"
          f"{' - ' + p['format'] + 's' if p['format'] else ''}")
    b, w = p["batting"], p["bowling"]
    print(f"  Batting: {b['matches']} matches, {b['innings']} innings, {b['runs']} runs, average {b['average']}, "
          f"highest {b['highest']}, {b['hundreds']} hundreds, {b['fifties']} fifties, strike rate {b['strike_rate']}")
    if w["wickets"]:
        print(f"  Bowling: {w['wickets']} wickets at {w['average']}, economy {w['economy']}")
    if p["dismissals"]:
        print("  How he got out: " + ", ".join(f"{d['kind']} {d['pct']}%" for d in p["dismissals"]))


def main(argv):
    if len(argv) < 2:
        return print(__doc__)
    con = Q.connect_ro()
    cmd, args = argv[0], argv[1:]
    if cmd == "duel" and len(args) >= 2:
        show_duel(Q.duel(con, args[0], args[1], args[2] if len(args) > 2 else None))
    elif cmd == "player":
        show_player(Q.player(con, args[0], args[1] if len(args) > 1 else None))
    elif cmd == "find":
        for p in Q.find_players(con, args[0]):
            print(f"  {p['name']:28} {p['team'] or '':15} {p['full_name'] or '':28} {p['games']} matches")
    elif cmd == "sql":
        cols, rows = Q.run_sql(con, args[0])
        print(" | ".join(cols))
        for r in rows:
            print(" | ".join("" if v is None else str(v) for v in r))
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BrokenPipeError:          # output cut short by '| head': not an error
        pass
