"""
All the checks, and the report (data/build_report.txt). Run after the readers:  python scripts/build_db.py checks

  1. ESPN's own list of every Test and ODI, year by year (downloaded once, saved in data/raw/espn_lists/):
     which matches we have, which we don't, and which have ball-by-ball.
  2. Test numbers 1..N with no gaps (works even without the ESPN list).
  3. Cricket maths: batters' runs + extras = innings total; balls add up to the total.
  4. Davis's ball-by-ball vs Davis's own scorecard, batter by batter.
  5. Davis vs Cricsheet (same Tests, about 2002-2020) and Davis vs your book (1877-1970).
  6. Famous facts the database must get exactly right.
"""
import re
import time
from datetime import datetime

import common as C

LISTS = C.RAW / "espn_lists"
LIST_URL = "https://stats.espncricinfo.com/ci/engine/records/team/match_results.html?class={cls};id={year};type=year"
FIRST_YEAR = {"Test": 1877, "ODI": 1971}


# ---------------------------------------------------------------- ESPN's lists
def fetch_lists(say):
    try:
        import requests
    except ImportError:
        return 0
    LISTS.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "Everyball cricket-history project (github.com/Anadijain1234; with permission)"
    got = failed = 0
    this_year = datetime.now().year
    for fmt, cls in (("Test", 1), ("ODI", 2)):
        for year in range(FIRST_YEAR[fmt], this_year + 1):
            f = LISTS / f"{fmt}_{year}_results.html"
            if f.exists() and (year < this_year - 1 or time.time() - f.stat().st_mtime < 86400):
                continue
            try:
                r = s.get(LIST_URL.format(cls=cls, year=year), timeout=60)
                if r.status_code == 200:
                    f.write_bytes(r.content)
                    got += 1
                else:
                    failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
            if failed >= 5 and got == 0:
                say("ESPN lists: site not reachable from here, skipping the download")
                return failed
            time.sleep(1)
    if got or failed:
        say(f"ESPN lists: {got} year pages downloaded, {failed} failed")
    return failed


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(text):
    """'Mar 15-19, 1877' / 'Dec 30, 2004-Jan 3, 2005' / '15 Mar 1877' -> '1877-03-15'."""
    t = text.strip()
    m = re.search(r"([A-Z][a-z]{2})\w*\s+(\d{1,2})\D.*?(\d{4})", t) or re.search(r"([A-Z][a-z]{2})\w*\s+(\d{1,2}),?\s+(\d{4})", t)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]{2})\w*\s+(\d{4})", t)
    if m and m.group(2).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def parse_list(html, fmt):
    out = []
    label = "Test" if fmt == "Test" else "ODI"
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        m = re.search(label + r"\s*#\s*(\d+)", re.sub(r"<[^>]+>", " ", tr))
        if not m:
            continue
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
        cells = [c.replace("&amp;", "&") for c in cells]
        if len(cells) < 6:
            continue
        out.append((fmt, int(m.group(1)), parse_date(cells[5]), C.team(cells[0]), C.team(cells[1]),
                    C.team(cells[2]) if cells[2] and cells[2].lower() not in ("drawn", "tied", "no result", "abandoned")
                    else cells[2], cells[3], cells[4]))
    return out


def load_lists(con, say):
    failed = fetch_lists(say)
    con.execute("DELETE FROM ref_matches")
    n = 0
    for f in sorted(LISTS.glob("*_results.html")):
        fmt = f.name.split("_")[0]
        rows = parse_list(f.read_text(encoding="utf-8", errors="replace"), fmt)
        con.executemany("INSERT OR REPLACE INTO ref_matches VALUES(?,?,?,?,?,?,?,?)", rows)
        n += len(rows)
    con.commit()
    return n, failed


# ---------------------------------------------------------------- the checks
def q(con, sql, *args):
    return con.execute(sql, args).fetchall()


def one(con, sql, *args):
    r = con.execute(sql, args).fetchone()
    return r[0] if r else None


def add(con, area, item, status, detail=""):
    con.execute("INSERT INTO checks VALUES(?,?,?,?)", (area, item, status, str(detail)))


FACTS = [
    ("Bradman: 6,996 Test runs at 99.94",
     "SELECT SUM(runs), ROUND(1.0*SUM(runs)/SUM(CASE WHEN not_out=0 THEN 1 ELSE 0 END),2) FROM bat "
     "WHERE format='Test' AND player='DG Bradman'", lambda r: r[0] == 6996 and abs((r[1] or 0) - 99.94) < 0.01),
    ("Lara: 400 not out, the highest Test score",
     "SELECT runs, not_out FROM bat WHERE format='Test' ORDER BY runs DESC LIMIT 1",
     lambda r: r[0] == 400 and r[1] == 1),
    ("Laker: 19 wickets in one Test (1956)",
     "SELECT SUM(wickets) FROM bowl WHERE player='JC Laker' GROUP BY match_id ORDER BY 1 DESC LIMIT 1",
     lambda r: r[0] == 19),
    ("Tendulkar: 15,921 Test runs", "SELECT SUM(runs) FROM bat WHERE format='Test' AND player='SR Tendulkar'",
     lambda r: r[0] == 15921),
    ("Muralitharan: 800 Test wickets",
     "SELECT SUM(wickets) FROM bowl WHERE format='Test' AND (player LIKE '%Muralidaran' OR player LIKE '%Muralitharan')",
     lambda r: r[0] == 800),
    ("First Test (1877): Australia won by 45 runs", "SELECT winner, result FROM matches WHERE match_id='T0001'",
     lambda r: r[0] == "Australia" and "45" in (r[1] or "")),
    ("Tendulkar: 18,426 ODI runs", "SELECT SUM(runs) FROM bat WHERE format='ODI' AND player='SR Tendulkar'",
     lambda r: r[0] == 18426),
    ("First ODI (1971): Australia won", "SELECT winner FROM matches WHERE match_id='O0001'",
     lambda r: r[0] == "Australia"),
]


def run(con, say):
    con.execute("DELETE FROM checks")
    lines = [f"EVERYBALL BUILD REPORT   {datetime.now():%d %b %Y %H:%M}", ""]

    def section(title):
        lines.extend(["", title, "-" * len(title)])

    # what's in the database
    section("1. What is in the database")
    for fmt in ("Test", "ODI"):
        total = one(con, "SELECT COUNT(*) FROM matches WHERE format=?", fmt) or 0
        bbb = one(con, "SELECT COUNT(*) FROM matches WHERE format=? AND ball_source IS NOT NULL", fmt) or 0
        lines.append(f"{fmt}s: {total:,} matches, {bbb:,} with ball-by-ball")
        for src, cnt in q(con, "SELECT ball_source, COUNT(*) FROM matches WHERE format=? AND ball_source IS NOT NULL "
                               "GROUP BY 1 ORDER BY 2 DESC", fmt):
            lines.append(f"   ball-by-ball from {src}: {cnt:,}")
    lines.append(f"Balls stored: {one(con, 'SELECT COUNT(*) FROM balls'):,}   Players: "
                 f"{one(con, 'SELECT COUNT(*) FROM players'):,}")
    for src, st, cnt in q(con, "SELECT source, status, COUNT(*) FROM build_log GROUP BY 1, 2 ORDER BY 1, 2"):
        lines.append(f"   {src:10} {st:8} {cnt:,} files")
    errs = q(con, "SELECT source, file, detail FROM build_log WHERE status='error' LIMIT 25")
    if errs:
        lines.append("   First problem files (send 2-3 of these to Claude):")
        lines += [f"     {s}: {f.split('data/raw/')[-1]} -> {d}" for s, f, d in errs]

    # Davis reading quality
    section("2. How well Davis's ball-by-ball was read")
    sure = one(con, "SELECT SUM(sure), COUNT(*) FROM balls WHERE source='davis'")
    tot = q(con, "SELECT SUM(sure), COUNT(*) FROM balls WHERE source='davis'")[0]
    if tot[1]:
        lines.append(f"Balls whose batter is proven by the over-by-over totals: {tot[0]:,} of {tot[1]:,} "
                     f"({100 * tot[0] / tot[1]:.1f}%)")
        add(con, "davis", "balls proven", "ok" if tot[0] / tot[1] > 0.95 else "check", f"{tot[0]}/{tot[1]}")
    rows = q(con, """
        WITH b AS (SELECT match_id, inn, batter_id, SUM(bat_runs) r, SUM(CASE WHEN extra_type='wd' THEN 0 ELSE 1 END) n
                   FROM balls WHERE source='davis' GROUP BY 1, 2, 3)
        SELECT COUNT(*), SUM(CASE WHEN c.runs = b.r THEN 1 ELSE 0 END), SUM(CASE WHEN c.runs = b.r AND c.balls = b.n THEN 1 ELSE 0 END)
        FROM batting c JOIN b ON b.match_id = c.match_id AND b.inn = c.inn AND b.batter_id = c.player_id
        WHERE c.source = 'davis'""")
    if rows and rows[0][0]:
        n, r_ok, rb_ok = rows[0]
        lines.append(f"Batters whose runs from the balls = scorecard runs: {r_ok:,} of {n:,} ({100 * r_ok / n:.1f}%); "
                     f"runs AND balls: {rb_ok:,} ({100 * rb_ok / n:.1f}%)")
        add(con, "davis", "balls vs scorecard", "ok" if r_ok / n > 0.97 else "check", f"{r_ok}/{n}")
    rows = q(con, """
        WITH b AS (SELECT match_id, inn, SUM(bat_runs + extras) r FROM balls WHERE source='davis' GROUP BY 1, 2)
        SELECT COUNT(*), SUM(CASE WHEN i.runs = b.r THEN 1 ELSE 0 END) FROM innings i
        JOIN b ON b.match_id = i.match_id AND b.inn = i.inn WHERE i.source = 'davis'
        AND NOT EXISTS (SELECT 1 FROM notes n WHERE n.match_id = i.match_id AND n.inn = i.inn
                        AND n.note LIKE 'PARTIAL RECORD%')""")
    partial = one(con, "SELECT COUNT(DISTINCT match_id || '-' || inn) FROM notes WHERE note LIKE 'PARTIAL RECORD%'")
    if partial:
        lines.append(f"Innings where Davis's own record is partial (gaps he marks, or missing overs), left out below: "
                     f"{partial:,}")
    if rows and rows[0][0]:
        lines.append(f"Innings whose balls add up to the scorecard total: {rows[0][1]:,} of {rows[0][0]:,}")
        bad = q(con, """WITH b AS (SELECT match_id, inn, SUM(bat_runs + extras) r FROM balls WHERE source='davis' GROUP BY 1, 2)
                        SELECT i.match_id, i.inn, i.runs, b.r FROM innings i JOIN b ON b.match_id = i.match_id AND b.inn = i.inn
                        WHERE i.source = 'davis' AND i.runs <> b.r AND NOT EXISTS (SELECT 1 FROM notes n
                        WHERE n.match_id = i.match_id AND n.inn = i.inn AND n.note LIKE 'PARTIAL RECORD%') LIMIT 15""")
        if bad:
            lines.append("   Examples that don't add up (match, innings, scorecard, balls): " +
                         ", ".join(f"{m} inn{i} {a}/{b}" for m, i, a, b in bad))

    # cricket maths on every scorecard
    section("3. Cricket maths on every scorecard (batters' runs + extras = total)")
    for src, n, ok in q(con, """
        WITH s AS (SELECT match_id, inn, source, SUM(runs) r FROM batting GROUP BY 1, 2, 3)
        SELECT i.source, COUNT(*), SUM(CASE WHEN s.r + COALESCE(i.extras, 0) = i.runs THEN 1 ELSE 0 END)
        FROM innings i JOIN s ON s.match_id = i.match_id AND s.inn = i.inn AND s.source = i.source
        WHERE i.runs IS NOT NULL GROUP BY 1"""):
        lines.append(f"   {src:10} {ok:,} of {n:,} innings add up ({100 * ok / max(n, 1):.1f}%)")
        add(con, "maths", src, "ok" if ok / max(n, 1) > 0.97 else "check", f"{ok}/{n}")

    # Davis vs Cricsheet, Davis vs book
    section("4. Two sources, same match")
    for a, b in (("davis", "cricsheet"), ("davis", "book"), ("cricsheet", "kaggle"), ("espn", "kaggle")):
        r = q(con, f"""SELECT COUNT(*), SUM(CASE WHEN x.runs = y.runs THEN 1 ELSE 0 END)
                       FROM innings x JOIN innings y ON y.match_id = x.match_id AND y.inn = x.inn AND y.source = '{b}'
                       WHERE x.source = '{a}'""")[0]
        if r[0]:
            pr = q(con, f"""SELECT COUNT(*), SUM(CASE WHEN x.runs = y.runs THEN 1 ELSE 0 END)
                            FROM batting x JOIN batting y ON y.match_id = x.match_id AND y.inn = x.inn
                            AND y.player_id = x.player_id AND y.source = '{b}' WHERE x.source = '{a}'""")[0]
            lines.append(f"{a} vs {b}: innings totals agree {r[1]:,}/{r[0]:,}; batters' runs agree {pr[1] or 0:,}/{pr[0]:,}")
            add(con, "cross", f"{a} vs {b}", "ok" if r[1] / r[0] > 0.97 else "check", f"{r[1]}/{r[0]}")

    # ESPN ball-by-ball commentary vs ESPN's own player-v-player tables
    r = q(con, """WITH b AS (SELECT match_id, inn, COUNT(*) n, SUM(bat_runs) r FROM balls
                             WHERE source='espn' AND (extra_type IS NULL OR extra_type <> 'wd') GROUP BY 1, 2),
                       p AS (SELECT match_id, inn, SUM(balls) n, SUM(runs) r FROM pvp GROUP BY 1, 2)
                  SELECT COUNT(*), SUM(CASE WHEN b.n = p.n AND b.r = p.r THEN 1 ELSE 0 END)
                  FROM b JOIN p ON p.match_id = b.match_id AND p.inn = b.inn""")[0]
    if r[0]:
        lines.append(f"ESPN commentary vs ESPN player-v-player: innings with the same balls and runs {r[1]:,}/{r[0]:,}")
        add(con, "cross", "espn balls vs pvp", "ok" if r[1] / r[0] > 0.95 else "check", f"{r[1]}/{r[0]}")
    for k, v in q(con, "SELECT 'Matches with ESPN player-v-player tables', COUNT(DISTINCT match_id) FROM pvp UNION ALL "
                       "SELECT 'ODIs with ESPN ball-by-ball', COUNT(*) FROM matches WHERE ball_source='espn'"):
        lines.append(f"{k}: {v:,}")

    # coverage vs ESPN's lists
    section("5. Every Test and ODI ever played (ESPN's lists) vs what we have")
    n_ref, failed = load_lists(con, say)
    if n_ref:
        lines.append(f"ESPN lists: {n_ref:,} matches" + (f" ({failed} year pages could not be fetched)" if failed else ""))
        for fmt in ("Test", "ODI"):
            ref = one(con, "SELECT COUNT(*) FROM ref_matches WHERE format=?", fmt)
            have = one(con, "SELECT COUNT(*) FROM ref_matches r JOIN matches m ON m.match_id = "
                            "(CASE r.format WHEN 'Test' THEN 'T' ELSE 'O' END) || printf('%04d', r.number) WHERE r.format=?", fmt)
            bbb = one(con, "SELECT COUNT(*) FROM ref_matches r JOIN matches m ON m.match_id = "
                           "(CASE r.format WHEN 'Test' THEN 'T' ELSE 'O' END) || printf('%04d', r.number) "
                           "WHERE r.format=? AND m.ball_source IS NOT NULL", fmt)
            lines.append(f"{fmt}s: ESPN lists {ref:,}; we have {have:,} ({100 * have / max(ref, 1):.1f}%); "
                         f"with ball-by-ball {bbb:,}")
            add(con, "coverage", fmt, "ok" if have == ref else "check", f"{have}/{ref}")
            lines.append(f"   Year by year ({fmt}s), only years that don't match:")
            for yr, r_n, h_n in q(con, """
                    SELECT substr(r.start_date, 1, 4) y, COUNT(*), SUM(CASE WHEN m.match_id IS NULL THEN 0 ELSE 1 END)
                    FROM ref_matches r LEFT JOIN matches m ON m.match_id =
                      (CASE r.format WHEN 'Test' THEN 'T' ELSE 'O' END) || printf('%04d', r.number)
                    WHERE r.format = ? GROUP BY 1 HAVING COUNT(*) <> SUM(CASE WHEN m.match_id IS NULL THEN 0 ELSE 1 END)
                    ORDER BY 1""", fmt):
                lines.append(f"     {yr}: ESPN {r_n}, we have {h_n}")
            missing = q(con, """SELECT r.number, r.start_date, r.team1, r.team2, r.ground FROM ref_matches r
                                LEFT JOIN matches m ON m.match_id = (CASE r.format WHEN 'Test' THEN 'T' ELSE 'O' END)
                                  || printf('%04d', r.number)
                                WHERE r.format = ? AND m.match_id IS NULL ORDER BY r.number LIMIT 40""", fmt)
            if missing:
                lines.append(f"   Missing {fmt}s (first 40):")
                lines += [f"     {fmt} #{n}: {d} {t1} v {t2}, {g}" for n, d, t1, t2, g in missing]
            # same number, different match? (a source numbering its matches differently from ESPN)
            for src in ("davis", "cricsheet", "kaggle", "espn", "book"):
                wrong = q(con, f"""SELECT r.number, r.start_date, r.team1, r.team2, m.start_date, m.team1, m.team2
                    FROM ref_matches r JOIN matches m ON m.match_id = (CASE r.format WHEN 'Test' THEN 'T' ELSE 'O' END)
                      || printf('%04d', r.number)
                    WHERE r.format = ? AND m.sources LIKE '%{src}%'
                      AND (abs(julianday(r.start_date) - julianday(m.start_date)) > 3
                           OR NOT ((r.team1 = m.team1 AND r.team2 = m.team2) OR (r.team1 = m.team2 AND r.team2 = m.team1)))
                    ORDER BY r.number""", fmt)
                if wrong:
                    lines.append(f"   {src}: {len(wrong)} {fmt}s whose number points to a different match than ESPN's "
                                 f"(first: " + "; ".join(f"#{n} ESPN {d} {a} v {b} / ours {d2} {c} v {e}"
                                                         for n, d, a, b, d2, c, e in wrong[:4]) + ")")
                    add(con, "numbers", f"{src} {fmt}", "check", str(len(wrong)))
    else:
        lines.append("ESPN's lists could not be downloaded right now; using the Test-number check below instead.")
    gaps = []
    top = one(con, "SELECT MAX(number) FROM matches WHERE format='Test' AND sources LIKE '%davis%'") or 0
    have = {r[0] for r in q(con, "SELECT number FROM matches WHERE format='Test' AND sources LIKE '%davis%'")}
    gaps = [i for i in range(1, top + 1) if i not in have]
    lines.append(f"Davis: Test numbers 1 to {top}: {len(have):,} present, {len(gaps)} missing" +
                 (f": {', '.join(map(str, gaps[:60]))}" if gaps else ""))
    add(con, "coverage", "Davis Test numbers", "ok" if not gaps else "check", f"{len(gaps)} missing")

    # famous facts
    section("6. Famous facts (must be exactly right)")
    for label, sql, ok in FACTS:
        try:
            r = con.execute(sql).fetchone()
            good = bool(r) and r[0] is not None and ok(r)
            lines.append(f"   {'PASS' if good else ('n/a ' if not r or r[0] is None else 'FAIL')}  {label}"
                         + ("" if good else f"   (database says: {tuple(r) if r else None})"))
            add(con, "facts", label, "ok" if good else "check", tuple(r) if r else None)
        except Exception as e:  # noqa: BLE001
            lines.append(f"   ERR   {label}: {e}")

    con.commit()
    C.REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    _ = sure
