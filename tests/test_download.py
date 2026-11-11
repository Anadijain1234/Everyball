"""Offline tests for the downloader (no internet needed): link finding, file naming, polite resume.
Run:  python tests/test_download.py"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import download as D  # noqa: E402


def make_pdf(links):
    """A one-page PDF with web links (http...) and file links (anything else, like Word-made PDFs use)."""
    from pypdf import PdfWriter
    from pypdf.annotations import Link
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
    w = PdfWriter()
    w.add_blank_page(300, 300)
    for i, target in enumerate(links):
        if target.startswith("http"):
            w.add_annotation(0, Link(rect=(10, 10 + i * 20, 200, 25 + i * 20), url=target))
        else:
            w.add_annotation(0, DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([FloatObject(x) for x in (10, 10 + i * 20, 200, 25 + i * 20)]),
                NameObject("/A"): DictionaryObject({NameObject("/S"): NameObject("/GoToR"),
                                                    NameObject("/F"): TextStringObject(target)})}))
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def test_match_names():
    yes = ["IND_AUS_PTC_ODI1_01APR1998.html", "AUS_IND_WIC_ODI-QF3_28OCT1998_BBB-COMMS.html",
           "AUS_IND_WIC_ODI-QF3_28OCT1998_PLYR-V-PLYR.html", "RSA_IND_ODI3_15MAR2000_BBB-COMMS.html",
           "PAK_IND_T1_28JAN-01FEB1999_BBB-COMMS.html", "WI_IND_T1_18-23FEB1971.html", "ENG_IND_CCC_ODI3_09APR1999.html"]
    no = ["PTC_APR1998_SCHEDULE.html", "PTC_APR1998_AUS-SQUAD.html", "PTC_APR1998_RESULTS-SUMMARY.html",
          "IND_AUS_PTC_ODI1_01APR1998_REPORT.html", "index.html"]
    for n in yes:
        assert D.is_match_file(n), n
    for n in no:
        assert not D.is_match_file(n), n


def test_espn_keys():
    k = D.espn_key("http://static.espncricinfo.com/db/ARCHIVE/1998-99/OD_TOURNEYS/CCC/SCORECARDS/ENG_IND_CCC_ODI3_09APR1999_BBB-COMMS.html")
    assert k == ("file", "ARCHIVE/1998-99/OD_TOURNEYS/CCC/SCORECARDS/ENG_IND_CCC_ODI3_09APR1999_BBB-COMMS.html")
    assert D.espn_key("https://i.imgci.com/link_to_database/ARCHIVE/1998-99/OD_TOURNEYS/CCC/SCORECARDS/ENG_IND_CCC_ODI3_09APR1999_BBB-COMMS.html") == k
    assert D.espn_key("https://i.imgci.com/link_to_database/ARCHIVE/1997-98/OD_TOURNEYS/PTC/") == ("dir", "ARCHIVE/1997-98/OD_TOURNEYS/PTC/")
    assert D.espn_key("https://i.imgci.com/link_to_database/ARCHIVE/1997-98/OD_TOURNEYS/PTC/ARTICLES/") is None
    assert D.espn_key("https://i.imgci.com/link_to_database/ARCHIVE/1997-98/OD_TOURNEYS/PTC/STATS/x.html") is None
    assert D.espn_key("https://i.imgci.com/ci/engine/series/60981.html") is None
    assert D.espn_key("https://evil.example.com/db/ARCHIVE/1998-99/x_ODI1_01APR1998.html") is None
    assert D.espn_key("https://i.imgci.com/link_to_database/ARCHIVE/1997-98/OD_TOURNEYS/PTC/PTC_APR1998_SCHEDULE.html")[0] == "page"
    assert [D.season_name(y) for y in (1970, 1998, 1999, 2000)] == ["1970-71", "1998-99", "1999-2000", "2000-01"]


def test_pdf_links_and_davis_keys():
    base = "https://www.sportstats.com.au/zArchive/contents.pdf"
    pdf = make_pdf(["https://www.sportstats.com.au/zArchive/1970s/1976PN/1976PN3bbb1.pdf",
                    "1970s/1976PN/1976PN3bbb2.pdf", "https://www.example.com/elsewhere.pdf"])
    links = D.pdf_links(pdf, base)
    assert "https://www.sportstats.com.au/zArchive/1970s/1976PN/1976PN3bbb1.pdf" in links
    assert "https://www.sportstats.com.au/zArchive/1970s/1976PN/1976PN3bbb2.pdf" in links  # relative file link
    keys = [D.davis_key(x) for x in links]
    assert "zArchive/1970s/1976PN/1976PN3bbb1.pdf" in keys and "zArchive/1970s/1976PN/1976PN3bbb2.pdf" in keys
    assert D.davis_key("https://www.example.com/elsewhere.pdf") is None
    assert D.davis_key("https://www.sportstats.com.au/bloghome.html") is None


def fake_site(pages, calls, refusals=None):
    """A pretend website. pages: path ending -> bytes. refusals: path ending -> how many times it says 'blocked'
    first ('always' = every time). Anything else answers 'not found'. Matching is case-sensitive, like a real server."""
    refusals = dict(refusals or {})

    def fetch2(url, tries=4, timeout=60, missing=None, blocked=None):
        calls.append(url)
        for k, n in list(refusals.items()):
            if url.endswith(k) and (n == "always" or n > 0):
                if n != "always":
                    refusals[k] = n - 1
                return None, "blocked"
        for k, v in pages.items():
            if url.rstrip("/").endswith(k.rstrip("/")):
                return v, "ok"
        return None, "missing"
    return fetch2


def test_davis_crawl_and_resume():
    tmp = Path(tempfile.mkdtemp())
    D.RAW, D.LOG = tmp, tmp / "log.txt"
    pages = {
        "zArchive/contents.pdf": make_pdf(["1970s/index70s.pdf"]),
        "zArchive/1970s/index70s.pdf": make_pdf(["1976PN/1976PN3bbb1.pdf", "1976PN/1976PN3bbb2.pdf"]),
        "zArchive/1970s/1976PN/1976PN3bbb1.pdf": make_pdf([]),
        "zArchive/1970s/1976PN/1976PN3bbb2.pdf": make_pdf([]),
    }
    calls = []
    real = D.fetch2
    D.fetch2 = fake_site(pages, calls)
    try:
        D.davis(delay=0)
        got = sorted(str(p.relative_to(tmp / "davis")) for p in (tmp / "davis").rglob("*.pdf"))
        assert got == ["zArchive/1970s/1976PN/1976PN3bbb1.pdf", "zArchive/1970s/1976PN/1976PN3bbb2.pdf",
                       "zArchive/1970s/index70s.pdf", "zArchive/contents.pdf"], got
        n = len(calls)
        D.davis(delay=0)  # second run: everything is on disk, nothing fetched again
        assert len(calls) == n, "resume should not re-download"
        import json
        prog = json.loads((tmp / "progress_davis.json").read_text())
        assert prog["finished"] and prog["done"] == 4 and prog["known"] == 4, prog
        import tracker as T
        T.RAW = tmp
        board = T.text(T.rows())
        assert "Davis" in board and "100.0%" in board
    finally:
        D.fetch2 = real


def test_espn_crawl_saves_only_match_files():
    tmp = Path(tempfile.mkdtemp())
    D.RAW, D.LOG = tmp, tmp / "log.txt"
    season = '<a href="OD_TOURNEYS/">One-day tournaments</a><a href="../1999-2000/">next season</a>'
    tourneys = '<a href="WIC/">Wills Cup</a>'
    wic = ('<a href="SCORECARDS/">Scorecards</a><a href="ARTICLES/">Articles</a>'
           '<a href="WIC_OCT1998_SCHEDULE.html">Schedule</a>')
    cards = ('<a href="AUS_IND_WIC_ODI-QF3_28OCT1998.html">card</a>'
             '<a href="AUS_IND_WIC_ODI-QF3_28OCT1998_BBB-COMMS.html">bbb</a>'
             '<a href="AUS_IND_WIC_ODI-QF3_28OCT1998_PLYR-V-PLYR.html">pvp</a>'
             '<a href="AUS_IND_WIC_ODI-QF3_28OCT1998_REPORT.html">report</a>')
    pages = {"ARCHIVE/1998-99/": season.encode(), "ARCHIVE/1998-99/OD_TOURNEYS/": tourneys.encode(),
             "ARCHIVE/1998-99/OD_TOURNEYS/WIC/": wic.encode(), "WIC_OCT1998_SCHEDULE.html": b"<p>schedule</p>",
             "ARCHIVE/1998-99/OD_TOURNEYS/WIC/SCORECARDS/": cards.encode(),
             "AUS_IND_WIC_ODI-QF3_28OCT1998.html": b"<p>card</p>",
             "AUS_IND_WIC_ODI-QF3_28OCT1998_BBB-COMMS.html": b"<p>0.4 Pollock to Tendulkar, no run</p>",
             "AUS_IND_WIC_ODI-QF3_28OCT1998_PLYR-V-PLYR.html": b"<p>pvp</p>"}
    calls = []
    real = D.fetch2
    D.fetch2 = fake_site(pages, calls)
    try:
        D.espn(["1998-99"], "test", delay=0)
        saved = sorted(p.name for p in (tmp / "espn" / "ARCHIVE").rglob("*.html"))
        assert saved == ["AUS_IND_WIC_ODI-QF3_28OCT1998.html", "AUS_IND_WIC_ODI-QF3_28OCT1998_BBB-COMMS.html",
                         "AUS_IND_WIC_ODI-QF3_28OCT1998_PLYR-V-PLYR.html"], saved
        assert not any("ARTICLES" in c or "1999-2000" in c or "REPORT" in c for c in calls), calls
        n = len(calls)
        D.espn(["1998-99"], "test", delay=0)
        assert len(calls) == n, "resume should not re-download"
        import json
        prog = json.loads((tmp / "progress_espn.json").read_text())
        assert prog["finished"] and prog["done"] == 3 and prog["known"] == 3, prog
    finally:
        D.fetch2 = real


def new_raw():
    tmp = Path(tempfile.mkdtemp())
    D.RAW, D.LOG = tmp, tmp / "log.txt"
    return tmp


def run_davis(pages, refusals=None):
    calls = []
    real = D.fetch2
    D.fetch2 = fake_site(pages, calls, refusals)
    try:
        D.davis(delay=0)
    finally:
        D.fetch2 = real
    return calls


def test_davis_dead_links_are_all_asked_once_and_listed():
    tmp = new_raw()
    a = "zArchive/1800s/1876AE/"
    pages = {"zArchive/contents.pdf": make_pdf(["1800s/1876AE/1876AEcov.pdf"]),
             a + "1876AEcov.pdf": make_pdf(["1876AE1.pdf", "1876AE1bbb1.pdf", "1876AE2.pdf", "1876AE3.pdf",
                                           "1876AE3bbb1.pdf"]),
             a + "1876AE1.pdf": make_pdf([]), a + "1876AE1bbb1.pdf": make_pdf([]), a + "1876AE2.pdf": make_pdf([])}
    calls = run_davis(pages)
    assert any(c.endswith("1876AE3.pdf") for c in calls) and any(c.endswith("1876AE3bbb1.pdf") for c in calls), \
        "every link is asked for, nothing skipped by guessing"
    assert sum(c.endswith("1876AE3.pdf") for c in calls) == 1, "a dead link is asked once, not twice"
    import json
    prog = json.loads((tmp / "progress_davis.json").read_text())
    assert prog["dead"] == 2 and prog["failed"] == 0 and prog["done"] == 5, prog
    assert sorted((tmp / "dead_davis.txt").read_text().split()) == [a + "1876AE3.pdf", a + "1876AE3bbb1.pdf"]
    assert not (tmp / "failed_davis.txt").exists(), "dead links are not on the retry list"
    assert run_davis(pages) == [], "next run: files on disk and known dead links are not asked again"


def test_davis_folder_typos_and_capitals():
    tmp = new_raw()
    pages = {"zArchive/contents.pdf": make_pdf(["1980s/1988/1988ELcov.pdf", "1970s/1976PN/1976pn3BBB1.pdf",
                                                "1970s/1976PN/1976PN3bbb1.pdf", "1960s/1961AB/1961AB1.pdf",
                                                "1960s/1961AB/1961ab1.pdf"]),
             "zArchive/1980s/1988EL/1988ELcov.pdf": make_pdf([]),
             "zArchive/1970s/1976PN/1976PN3bbb1.pdf": make_pdf([]),
             "zArchive/1960s/1961AB/1961AB1.pdf": make_pdf([]),
             "zArchive/1960s/1961AB/1961ab1.pdf": make_pdf([])}  # a server that ignores capitals
    calls = run_davis(pages)
    got = sorted(str(p.relative_to(tmp / "davis")) for p in (tmp / "davis").rglob("*.pdf"))
    assert "zArchive/1980s/1988EL/1988ELcov.pdf" in got, "a link into the wrong folder also tries the right one"
    assert "zArchive/1970s/1976PN/1976PN3bbb1.pdf" in got, "a wrongly-capitalised dead link doesn't hide the real one"
    assert sum("1961" in c.lower() for c in calls) == 1, "the same file under two spellings is fetched once"
    assert D.folder_fix("zArchive/1980s/1988/1988ELcov.pdf") == "zArchive/1980s/1988EL/1988ELcov.pdf"
    assert D.folder_fix("zArchive/1800s/1876AE/1876AE3.pdf") is None
    assert D.folder_fix("zArchive/1990s/199598series.pdf") is None


def test_refusals_slow_down_and_never_count_as_dead():
    tmp = new_raw()
    pages = {"zArchive/contents.pdf": make_pdf(["1900s/1900AB/1900AB1.pdf", "1900s/1900AB/1900AB2.pdf"]),
             "zArchive/1900s/1900AB/1900AB1.pdf": make_pdf([]), "zArchive/1900s/1900AB/1900AB2.pdf": make_pdf([])}
    run_davis(pages, refusals={"1900AB1.pdf": 2, "1900AB2.pdf": "always"})
    assert (tmp / "davis/zArchive/1900s/1900AB/1900AB1.pdf").exists(), "refused twice, then fetched"
    assert not (tmp / "dead_davis.txt").exists(), "a refusal is never a dead link"
    assert (tmp / "failed_davis.txt").read_text().strip().endswith("1900AB2.pdf"), "always refused: on the retry list"


def test_espn_one_year_folders_and_403s():
    tmp = new_raw()
    season = ('<a href="ENG_PAK_ODI2_09JUN2001.html">gone</a><a href="ENG_PAK_T1_17-20MAY2001.html">t1</a>'
              '<a href="ENG_AUS_ODI1_07JUN2001.html">odi</a>')
    pages = {"ARCHIVE/2001/": season.encode(), "ENG_PAK_T1_17-20MAY2001.html": b"<p>t1</p>",
             "ENG_AUS_ODI1_07JUN2001.html": b"<p>odi</p>"}
    calls = []
    real = D.fetch2
    D.fetch2 = fake_site(pages, calls, refusals={"ENG_PAK_ODI2_09JUN2001.html": "always"})
    try:
        D.espn(["2001"], "test", delay=0, name="espn_t")
        saved = sorted(p.name for p in (tmp / "espn" / "ARCHIVE").rglob("*.html"))
        assert saved == ["ENG_AUS_ODI1_07JUN2001.html", "ENG_PAK_T1_17-20MAY2001.html"], saved
        assert "ENG_PAK_ODI2_09JUN2001.html" in (tmp / "dead_espn_t.txt").read_text(), \
            "403 while the rest of the site answers = ESPN's way of saying 'not here'"
        import json
        prog = json.loads((tmp / "progress_espn_t.json").read_text())
        assert prog["dead"] == 1 and prog["failed"] == 0 and "rate" in prog, prog
        pages2 = {"ARCHIVE/2002/": b'<a href="ENG_IND_ODI1_29JUN2002.html">x</a>'}
        D.fetch2 = fake_site(pages2, [], refusals={"ENG_IND_ODI1_29JUN2002.html": "always"})
        D.espn(["2002"], "test2", delay=0, name="espn_t2")
        assert (tmp / "failed_espn_t2.txt").exists() and not (tmp / "dead_espn_t2.txt").exists(), \
            "when the whole site is refusing us, nothing is called dead"
    finally:
        D.fetch2 = real
    assert [D.season_name(1990), "1990"] and D.espn_key(
        "https://i.imgci.com/link_to_database/ARCHIVE/2001/ENG_PAK_T1_17-20MAY2001.html")[0] == "file"


def test_fetch2_reads_the_answers_right():
    class R:
        def __init__(self, code):
            self.status_code, self.content = code, b"x"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)
    real_get, real_backoff = D.S.get, D.BACKOFF
    D.BACKOFF = 0
    new_raw()
    try:
        for code, want in [(200, "ok"), (404, "missing"), (401, "missing"), (410, "missing"), (403, "blocked"),
                           (429, "blocked"), (503, "failed")]:
            D.S.get = lambda url, timeout=60, c=code: R(c)
            assert D.fetch2("https://x/y.pdf", tries=2)[1] == want, code
        D.S.get = lambda url, timeout=60: R(403)
        assert D.fetch("https://x/y.pdf") is None, "retry() keeps treating a refusal as 'not now'"
    finally:
        D.S.get, D.BACKOFF = real_get, real_backoff


def test_tracker_shows_dead_links_and_india_time():
    import json
    import time as _t
    import tracker as T
    tmp = new_raw()
    T.RAW = tmp
    (tmp / "progress_davis.json").write_text(json.dumps({
        "done": 1500, "dead": 300, "failed": 2, "known": 16600, "queue": 14798, "rate": 40.0, "pause": 0.75,
        "slowed": False, "finished": False, "updated": _t.time()}))
    board = T.text(T.rows())
    assert "India time" in board and "1,500 files saved" in board and "300 dead links" in board, board
    assert "1,802 / 16,600+" in board and "40/min" in board and "at least 6h 10m left" in board, board


if __name__ == "__main__":
    for t in (test_match_names, test_espn_keys, test_pdf_links_and_davis_keys, test_davis_crawl_and_resume,
              test_espn_crawl_saves_only_match_files, test_davis_dead_links_are_all_asked_once_and_listed,
              test_davis_folder_typos_and_capitals, test_refusals_slow_down_and_never_count_as_dead,
              test_espn_one_year_folders_and_403s, test_fetch2_reads_the_answers_right,
              test_tracker_shows_dead_links_and_india_time):
        t()
        print("ok ", t.__name__)
    print("All downloader tests passed.")
