"""Tests for the database builder, using real sample files in tests/fixtures/ (no internet needed).
Run:  python tests/test_build.py      (needs pdftotext: sudo apt-get install -y poppler-utils)"""
import collections
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent / "scripts"))
import common as C  # noqa: E402
import read_davis as RD  # noqa: E402


def text(name):
    return C.pdf_text(FIX / name)


def rosters_from(page):
    r = {}
    for inn in page["innings"]:
        r.setdefault(inn["team"], set()).update(b["name"] for b in inn["batting"])
        r.setdefault(inn["bowling_team"], set()).update(b["name"] for b in inn["bowling"])
    return r


def test_test_page_1970():
    p = RD.parse_test_page(text("1970AE1.pdf"))
    assert p["number"] == 674 and p["team1"] == "Australia" and p["team2"] == "England"
    assert p["start_date"] == "1970-11-27" and p["toss"] == "Australia" and p["result"] == "Match Drawn"
    assert [i["runs"] for i in p["innings"]] == [433, 464, 214, 39]
    for inn in p["innings"]:
        assert sum(b["runs"] for b in inn["batting"]) + inn["extras"] == inn["runs"], "batters + extras = total"
    lawry = p["innings"][0]["batting"][0]
    assert lawry["name"] == "WM Lawry" and lawry["kind"] == "caught" and lawry["captain"] == 1
    assert p["innings"][0]["bowling"][0] == {"name": "JA Snow", "overs": "32.3", "maidens": 6, "runs": 114, "wickets": 6}


def test_ball_by_ball_1970_matches_the_scorecard():
    page = RD.parse_test_page(text("1970AE1.pdf"))
    bb = RD.parse_bbb(text("1970AE1bbb1.pdf"), rosters_from(page))
    assert bb["number"] == 674 and bb["balls_per_over"] == 8
    assert bb["stats"]["sure"] / bb["stats"]["segments"] > 0.99
    for inn, card in zip(bb["innings"], page["innings"]):
        assert sum(b["bat_runs"] + b["extras"] for b in inn["balls"]) == card["runs"]
        assert sum(b["wicket"] for b in inn["balls"]) == card["wickets"]
        got = collections.defaultdict(lambda: [0, 0])
        for b in inn["balls"]:
            got[b["batter"]][0] += b["bat_runs"]
            got[b["batter"]][1] += b["extra_type"] != "wd"
        for bat in card["batting"]:
            assert tuple(got[bat["name"]]) == (bat["runs"], bat["balls"]), (bat["name"], got[bat["name"]])


def test_ball_by_ball_2010_hat_trick_and_wides():
    bb = RD.parse_bbb(text("2010AE1bbb1.pdf"), {})
    assert bb["number"] == 1981 and bb["balls_per_over"] == 6 and bb["stats"]["unsure"] == 0
    assert [sum(b["bat_runs"] + b["extras"] for b in i["balls"]) for i in bb["innings"]] == [260, 481, 517, 107]
    outs = [(b["bowler"], b["out"]) for b in bb["innings"][0]["balls"] if b["wicket"]]
    i = outs.index(("PM Siddle", "AN Cook"))
    assert outs[i:i + 3] == [("PM Siddle", "AN Cook"), ("PM Siddle", "MJ Prior"), ("PM Siddle", "SCJ Broad")], \
        "Siddle's hat-trick, ball by ball"
    notes = " ".join(n for _, n in bb["innings"][0]["notes"])
    assert "DRS" in notes


def test_cover_and_players():
    cov = RD.parse_cover(text("1876AEcov.pdf"))
    assert cov["balls_per_over"] == 4 and cov["reconstructed"]
    pl = RD.parse_players(text("pc_1.pdf"))
    assert {"name": "C Bannerman", "team": "Australia", "full_name": "Charles Bannerman", "born": "Kent",
            "role": "Bat"} in pl


def test_whole_build_and_questions():
    tmp = Path(tempfile.mkdtemp())
    raw = tmp / "raw"
    for rel, fix in (("davis/zArchive/1970s/1970AE/1970AE1.pdf", "1970AE1.pdf"),
                     ("davis/zArchive/1970s/1970AE/1970AE1bbb1.pdf", "1970AE1bbb1.pdf"),
                     ("davis/zArchive/2010s/2010AE/2010AE1bbb1.pdf", "2010AE1bbb1.pdf"),
                     ("davis/zArchive/1800s/1876AE/1876AE1.pdf", "1876AE1.pdf"),
                     ("davis/zArchive/1800s/1876AE/1876AEcov.pdf", "1876AEcov.pdf"),
                     ("davis/zArchive/1800s/pc/1.pdf", "pc_1.pdf")):
        (raw / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / fix, raw / rel)
    (raw / "cricsheet").mkdir(parents=True)
    with zipfile.ZipFile(raw / "cricsheet" / "odis_json.zip", "w") as z:
        z.write(FIX / "cricsheet_odi_1000887.json", "1000887.json")
    C.RAW, C.DB_PATH, C.REPORT = raw, tmp / "test.db", tmp / "report.txt"
    import build_db as B
    import checks
    B.TEXT_CACHE = raw / "_text"
    checks.LISTS = raw / "espn_lists"
    checks.fetch_lists = lambda say: 0          # no internet in tests
    con = C.connect()
    for step in ("davis", "cricsheet", "checks"):
        B.step(step, con)
    m = con.execute("SELECT card_source, ball_source, winner, toss, balls_per_over FROM matches WHERE match_id='T0674'").fetchone()
    assert m == ("davis", "davis", None, "Australia", 8)
    assert con.execute("SELECT confidence FROM matches WHERE match_id='T0001'").fetchone()[0] is None  # no bbb here
    assert con.execute("SELECT format, ball_source FROM matches WHERE match_id='O3817'").fetchone() == ("ODI", "cricsheet")
    assert con.execute("SELECT full_name FROM players WHERE name='C Bannerman'").fetchone()[0] == "Charles Bannerman"
    assert "Batters whose runs from the balls = scorecard runs: 35 of 35" in C.REPORT.read_text()
    import queries as Q
    ro = Q.connect_ro(C.DB_PATH)
    d = Q.duel(ro, "Stackpole", "Snow", "Test")
    assert (d["batter"], d["bowler"], d["runs"], d["balls"], d["outs"]) == ("KR Stackpole", "JA Snow", 59, 120, 1)
    assert d["longest_dot_streak"] >= 10 and d["matches_both_played"] == 1
    p = Q.player(ro, "KR Stackpole")
    assert p["batting"]["runs"] == 215 and p["batting"]["highest"] == 207
    cols, rows = Q.run_sql(ro, "SELECT COUNT(*) FROM ball WHERE format='ODI'")
    assert rows[0][0] > 500
    try:
        Q.run_sql(ro, "DELETE FROM balls")
        raise AssertionError("must refuse")
    except ValueError:
        pass
    _ = json


def test_kaggle_and_espn():
    """Kaggle gives the 11 Apr 1999 ODI its number (O1433); ESPN's files for it join on, and add up. India v England
    was played the same day (O1432): ESPN's files must not land there."""
    tmp = Path(tempfile.mkdtemp())
    raw = tmp / "raw"
    (raw / "kaggle").mkdir(parents=True)
    shutil.copy(FIX / "kaggle_O1433.zip", raw / "kaggle" / "odi.zip")
    odi = raw / "espn" / "ARCHIVE" / "1998-99" / "WI_IN_AUS"
    odi.mkdir(parents=True)
    shutil.copy(FIX / "espn_bbb_comms__AUS_WI_ODI1_11APR1999_BBB-COMMS.html", odi / "AUS_WI_ODI1_11APR1999_BBB-COMMS.html")
    shutil.copy(FIX / "espn_player_v_player__AUS_WI_ODI1_11APR1999_PLYR-V-PLYR.html",
                odi / "AUS_WI_ODI1_11APR1999_PLYR-V-PLYR.html")
    test = raw / "espn" / "ARCHIVE" / "2001" / "AUS_IN_ENG"
    test.mkdir(parents=True)
    shutil.copy(FIX / "espn_one_year_folder__AUS_ENG_T1_05-09JUL2001.html", test / "AUS_ENG_T1_05-09JUL2001.html")
    (raw / "espn" / "ARCHIVE" / "2001" / "OTHER").mkdir()
    shutil.copy(FIX / "espn_one_year_folder__AUS_ENG_T1_05-09JUL2001.html",
                raw / "espn" / "ARCHIVE" / "2001" / "OTHER" / "KENT_AUS_19JUN2001.html")   # a tour game: skipped
    C.RAW, C.DB_PATH, C.REPORT = raw, tmp / "test.db", tmp / "report.txt"
    import build_db as B
    con = C.connect()
    C.upsert_match(con, "T9999", "test", format="Test", number=9999, start_date="2001-07-05",
                   team1="England", team2="Australia")          # stands in for the Test's real number
    con.commit()
    B.step("kaggle", con)
    B.step("espn", con)
    q = lambda s: con.execute(s).fetchall()  # noqa: E731
    assert q("SELECT team1, team2, card_source, ball_source FROM matches WHERE match_id='O1433'") == \
        [("West Indies", "Australia", "kaggle", "espn")]   # no ESPN scorecard file in this test
    # ESPN's ball-by-ball = ESPN's own player-v-player tables, innings by innings
    for inn in (1, 2):
        balls = q(f"SELECT COUNT(*), SUM(bat_runs) FROM balls WHERE match_id='O1433' AND inn={inn} AND "
                  f"(extra_type IS NULL OR extra_type != 'wd')")[0]
        pvp = q(f"SELECT SUM(balls), SUM(runs) FROM pvp WHERE match_id='O1433' AND inn={inn}")[0]
        assert balls == pvp, (inn, balls, pvp)
    # the same people in Kaggle's card and ESPN's balls (no 'Campbell' next to 'SL Campbell')
    names = {r[0] for r in q("SELECT DISTINCT batter FROM ball WHERE match_id='O1433'")}
    kaggle = {r[0] for r in q("SELECT p.name FROM batting b JOIN players p USING(player_id) "
                              "WHERE match_id='O1433' AND source='kaggle'")}
    assert len(names) >= 20 and names <= kaggle, (len(names), names - kaggle)
    assert q("SELECT COUNT(*) FROM ball WHERE match_id='O1433'") == q("SELECT COUNT(*) FROM balls WHERE match_id='O1433'")
    assert q("SELECT COUNT(*) FROM pvp WHERE match_id='O1433' AND (batter_id IS NULL OR bowler_id IS NULL)") == [(0,)]
    assert q("SELECT COUNT(*) FROM commentary WHERE match_id='O1433'")[0][0] > 100
    # the Test scorecard: runs add up, McGrath's figures, the toss
    assert q("SELECT runs, wickets, extras FROM innings WHERE match_id='T9999' AND source='espn' ORDER BY inn") == \
        [(294, 10, 34), (576, 10, 33), (164, 10, 18)]
    assert q("SELECT overs, runs, wickets FROM bowling b JOIN players p USING(player_id) "
             "WHERE match_id='T9999' AND inn=1 AND p.name LIKE '%McGrath'") == [("17.3", 67, 3)]
    assert q("SELECT toss FROM matches WHERE match_id='T9999'") == [("Australia",)]
    assert q("SELECT COUNT(*) FROM matches") == [(3,)]      # O1432 (India v England, same day) gets nothing
    assert q("SELECT COUNT(*) FROM balls WHERE match_id='O1432'") == [(0,)]


if __name__ == "__main__":
    import shutil as _s
    if not _s.which("pdftotext"):
        sys.exit("pdftotext is missing: sudo apt-get update && sudo apt-get install -y poppler-utils")
    for t in (test_test_page_1970, test_ball_by_ball_1970_matches_the_scorecard, test_ball_by_ball_2010_hat_trick_and_wides,
              test_cover_and_players, test_whole_build_and_questions, test_kaggle_and_espn):
        t()
        print("ok ", t.__name__)
    print("All build tests passed.")
