"""
Website tests: pages, search, duel, player page, coverage, the Ask box (with a stand-in AI, no internet or keys),
and packing the database for the website.

  python tests/test_web.py
"""
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "app"))
import common as C  # noqa: E402


def build_sample():
    tmp = Path(tempfile.mkdtemp())
    raw = tmp / "raw"
    for rel, fix in (("davis/zArchive/1970s/1970AE/1970AE1.pdf", "1970AE1.pdf"),
                     ("davis/zArchive/1970s/1970AE/1970AE1bbb1.pdf", "1970AE1bbb1.pdf"),
                     ("davis/zArchive/1800s/1876AE/1876AE1.pdf", "1876AE1.pdf"),
                     ("davis/zArchive/1800s/1876AE/1876AEcov.pdf", "1876AEcov.pdf")):
        (raw / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / fix, raw / rel)
    (raw / "cricsheet").mkdir(parents=True)
    with zipfile.ZipFile(raw / "cricsheet" / "odis_json.zip", "w") as z:
        z.write(FIX / "cricsheet_odi_1000887.json", "1000887.json")
    (raw / "kaggle").mkdir()
    shutil.copy(FIX / "kaggle_O1433.zip", raw / "kaggle" / "odi.zip")
    odi = raw / "espn" / "ARCHIVE" / "1998-99" / "WI_IN_AUS"
    odi.mkdir(parents=True)
    shutil.copy(FIX / "espn_bbb_comms__AUS_WI_ODI1_11APR1999_BBB-COMMS.html", odi / "AUS_WI_ODI1_11APR1999_BBB-COMMS.html")
    C.RAW, C.DB_PATH, C.REPORT = raw, tmp / "everyball.db", tmp / "report.txt"
    import build_db as B
    B.TEXT_CACHE = raw / "_text"
    con = C.connect()
    for step in ("davis", "cricsheet", "kaggle", "espn"):
        B.step(step, con)
    con.close()
    return tmp


def test_site():
    tmp = build_sample()
    import os
    os.environ["EVERYBALL_DB"] = str(C.DB_PATH)
    os.environ.pop("GROQ_API_KEY", None)
    import server
    server.DB = C.DB_PATH
    c = server.app.test_client()
    for url in ("/", "/duel", "/ask", "/credits", "/healthz"):
        assert c.get(url).status_code == 200, url
    assert c.get("/player/999999").status_code == 404
    hits = c.get("/api/search?q=stackpole").get_json()
    assert hits[0]["name"] == "KR Stackpole", hits
    bat = hits[0]["player_id"]
    bowl = c.get("/api/search?q=JA Snow").get_json()[0]["player_id"]
    d = c.get(f"/api/duel?batter={bat}&bowler={bowl}&format=Test").get_json()
    assert (d["balls"], d["runs"], d["outs"]) == (120, 59, 1), d
    assert sum(len(m["codes"]) for m in d["matches"]) == d["all"]
    assert d["outcomes"]["W"] == 1 and sum(d["outcomes"].values()) == d["all"]
    assert c.get(f"/player/{bat}").status_code == 200
    p = c.get(f"/api/player/{bat}").get_json()
    assert p["formats"][0]["batting"]["runs"] == 215 and "JA Snow" in {x["name"] for x in p["dismissed_by"]}
    cov = c.get("/api/coverage").get_json()
    assert {f["format"] for f in cov["formats"]} == {"Test", "ODI"}
    # Ask, with no key: a clear message, not a crash
    r = c.post("/api/ask", json={"question": "Who scored the most ODI runs?"})
    assert r.status_code == 400 and "GROQ_API_KEY" in r.get_json()["error"]
    # Ask, with a stand-in AI: the numbers come from the database, not from the AI
    import ask_ai
    calls = []

    def fake_groq(messages, max_tokens=900):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"sql": "SELECT player, SUM(runs) AS runs FROM bat WHERE player LIKE '%Stackpole%' "
                                      "AND format = 'Test' GROUP BY player_id", "how": "Added up his Test runs."})
        return "KR Stackpole scored 215 Test runs."
    ask_ai.groq = fake_groq
    r = c.post("/api/ask", json={"question": "How many Test runs did Stackpole score?"}).get_json()
    assert r["rows"] == [["KR Stackpole", 215]] and "SELECT" in r["sql"], r
    assert '"rows": [["KR Stackpole", 215]]' in calls[1][-1]["content"]      # the answer is written from the rows
    # the AI can't change anything: writing queries are refused, even if it tries
    calls.clear()
    ask_ai.groq = lambda m, max_tokens=900: json.dumps({"sql": "DELETE FROM balls", "how": "x"})
    r = c.post("/api/ask", json={"question": "delete everything please"})
    assert r.status_code == 400
    import sqlite3
    assert sqlite3.connect(C.DB_PATH).execute("SELECT COUNT(*) FROM balls").fetchone()[0] > 0
    shutil.rmtree(tmp, ignore_errors=True)


def test_pack_and_unpack():
    tmp = build_sample()
    import publish_db as P
    P.WEB, P.MANIFEST = tmp / "web", tmp / "web" / "manifest.json"
    P.PART = 20_000                        # small parts, to test the splitting
    P.pack()
    info = json.loads(P.MANIFEST.read_text())
    assert len(info["parts"]) >= 2 and info["counts"]["balls"] > 1000
    out = tmp / "site" / "everyball.db"
    assert P.unpack(out, quiet=True) and P.unpack(out, quiet=True)
    import sqlite3
    con = sqlite3.connect(out)
    assert con.execute("SELECT COUNT(*) FROM balls").fetchone()[0] == info["counts"]["balls"]
    assert con.execute("SELECT COUNT(*) FROM commentary").fetchone()[0] == 0
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if not shutil.which("pdftotext"):
        sys.exit("pdftotext is missing: sudo apt-get update && sudo apt-get install -y poppler-utils")
    for t in (test_site, test_pack_and_unpack):
        t()
        print("ok ", t.__name__)
    print("All website tests passed.")
