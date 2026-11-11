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


def fake_site(pages, calls):
    def fetch(url, tries=4, timeout=60):
        calls.append(url)
        for k, v in pages.items():
            if url.rstrip("/").endswith(k.rstrip("/")):
                return v
        return None
    return fetch


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
    real = D.fetch
    D.fetch = fake_site(pages, calls)
    try:
        D.davis(delay=0)
        got = sorted(str(p.relative_to(tmp / "davis")) for p in (tmp / "davis").rglob("*.pdf"))
        assert got == ["zArchive/1970s/1976PN/1976PN3bbb1.pdf", "zArchive/1970s/1976PN/1976PN3bbb2.pdf",
                       "zArchive/1970s/index70s.pdf", "zArchive/contents.pdf"], got
        n = len(calls)
        D.davis(delay=0)  # second run: everything is on disk, nothing fetched again
        assert len(calls) == n, "resume should not re-download"
    finally:
        D.fetch = real


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
    real = D.fetch
    D.fetch = fake_site(pages, calls)
    try:
        D.espn(["1998-99"], "test", delay=0)
        saved = sorted(p.name for p in (tmp / "espn" / "ARCHIVE").rglob("*.html"))
        assert saved == ["AUS_IND_WIC_ODI-QF3_28OCT1998.html", "AUS_IND_WIC_ODI-QF3_28OCT1998_BBB-COMMS.html",
                         "AUS_IND_WIC_ODI-QF3_28OCT1998_PLYR-V-PLYR.html"], saved
        assert not any("ARTICLES" in c or "1999-2000" in c or "REPORT" in c for c in calls), calls
        n = len(calls)
        D.espn(["1998-99"], "test", delay=0)
        assert len(calls) == n, "resume should not re-download"
    finally:
        D.fetch = real


if __name__ == "__main__":
    for t in (test_match_names, test_espn_keys, test_pdf_links_and_davis_keys, test_davis_crawl_and_resume,
              test_espn_crawl_saves_only_match_files):
        t()
        print("ok ", t.__name__)
    print("All downloader tests passed.")
