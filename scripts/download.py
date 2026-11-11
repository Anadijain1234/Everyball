"""
Everyball downloader: fetches the raw data into data/raw/ (never pushed to GitHub: too big).

  python scripts/download.py cricsheet     # ~2 min   Tests + ODIs ball by ball (2002 on) + player register
  python scripts/download.py kaggle        # seconds  unpacks the ODI scorecards 1971-2024 (zip already included)
  python scripts/download.py davis         # 2-5 h    Charles Davis's Test Match Database (1877 on), with his permission
  python scripts/download.py espn-bbb      # ~1 h     Cricinfo ball-by-ball + player-v-player files, Oct 1998-2002, with ESPN's permission
  python scripts/download.py espn-cards    # 2-4 h    Cricinfo scorecards 1970-71 to 1997-98 (who took each wicket)
  python scripts/download.py all           # everything above, in that order
  python scripts/download.py status        # what is downloaded so far

Safe to stop any time (Ctrl+C) and run again: finished files are skipped, half-finished ones are redone.
Polite: one request every 1.5-2 seconds to Davis's and ESPN's sites, so we never strain them.
Everything is logged to data/raw/download_log.txt.
"""
import argparse
import io
import re
import sys
import time
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
LOG = RAW / "download_log.txt"

S = requests.Session()
S.headers["User-Agent"] = "Everyball cricket-history project (github.com/Anadijain1234; slow, polite downloads, with permission)"


# ---------------------------------------------------------------- basics
def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    RAW.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def save(path, data):
    """Write atomically: a file only exists once it is complete, so a stopped run never leaves half files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch(url, tries=4, timeout=60):
    """GET with retries and back-off. Returns bytes, or None if missing / still failing."""
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code in (403, 404, 410):
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.content
        except Exception as e:  # network hiccup: wait longer each time
            wait = 10 * (2 ** i)
            log(f"   retry {i + 1}/{tries} in {wait}s: {url} ({e})")
            time.sleep(wait)
    log(f"   FAILED after {tries} tries: {url}")
    return None


def fetch_big(url, path):
    """Stream a large file (Cricsheet zips) straight to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with S.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(path)


def mb(n):
    return f"{n / 1e6:.1f} MB"


# ---------------------------------------------------------------- 1. Cricsheet
CRICSHEET = {
    "tests_json.zip": "https://cricsheet.org/downloads/tests_json.zip",
    "odis_json.zip": "https://cricsheet.org/downloads/odis_json.zip",
    "people.csv": "https://cricsheet.org/register/people.csv",
    "names.csv": "https://cricsheet.org/register/names.csv",
}


def cricsheet(force=False):
    log("== Cricsheet: Tests + ODIs ball by ball (about 2002 onwards), and the player register")
    for name, url in CRICSHEET.items():
        p = RAW / "cricsheet" / name
        if p.exists() and not force:
            log(f"   have {name} ({mb(p.stat().st_size)})")
            continue
        try:
            fetch_big(url, p)
            log(f"   got {name} ({mb(p.stat().st_size)})")
        except Exception as e:
            log(f"   FAILED {name}: {e}")
    for z in ("tests_json.zip", "odis_json.zip"):
        p = RAW / "cricsheet" / z
        if p.exists():
            with zipfile.ZipFile(p) as zf:
                n = sum(1 for i in zf.namelist() if i.endswith(".json"))
            log(f"   {z}: {n} matches (men's and women's; we keep men's when building)")


# ---------------------------------------------------------------- 2. Kaggle ODI scorecards (zip shipped with the project)
def kaggle():
    log("== Kaggle ODI scorecards 1971-2024")
    z = RAW / "kaggle" / "odi_scorecards_1971_2024.zip"
    if not z.exists():
        log("   MISSING data/raw/kaggle/odi_scorecards_1971_2024.zip (it comes in the project zip)")
        return
    out = RAW / "kaggle" / "odi"
    with zipfile.ZipFile(z) as zf:
        zf.extractall(out)
        log(f"   unpacked {len(zf.namelist())} files to data/raw/kaggle/odi/")


# ---------------------------------------------------------------- 3. Charles Davis (sportstats.com.au)
DAVIS_START = ["https://www.sportstats.com.au/zArchive/contents.pdf",
               "http://www.sportstats.com.au/zArchive/contents.pdf"]
DAVIS_HOSTS = {"www.sportstats.com.au", "sportstats.com.au"}


def pdf_links(data, base):
    """Every link inside a PDF: web links (/URI), links to other PDF files (/F), and plain URLs in the text."""
    from pypdf import PdfReader
    out = []
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return out
    for page in reader.pages:
        try:
            annots = page.get("/Annots") or []
            annots = annots.get_object() if hasattr(annots, "get_object") else annots
        except Exception:
            annots = []
        for a in annots:
            try:
                o = a.get_object()
                act = o.get("/A")
                act = act.get_object() if act is not None else None
                if not act:
                    continue
                target = act.get("/URI")
                if target is None:
                    f = act.get("/F")
                    f = f.get_object() if hasattr(f, "get_object") else f
                    if isinstance(f, dict):
                        f = f.get("/UF") or f.get("/F")
                    target = f
                if target:
                    out.append(urljoin(base, str(target).replace("\\", "/")))
            except Exception:
                continue
        try:  # some PDFs only print the addresses as text
            for u in re.findall(r"https?://[^\s<>\"')]+", page.extract_text() or ""):
                out.append(u.rstrip(".,;"))
        except Exception:
            pass
    return out


def html_links(data, base):
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(data, "html.parser")
    except Exception:
        return []
    return [urljoin(base, a["href"]) for a in soup.find_all("a", href=True)]


def davis_key(url):
    """Where a Davis file lives on disk, or None if we should not follow it."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or u.netloc.lower() not in DAVIS_HOSTS:
        return None
    path = unquote(u.path)
    if not path.lower().startswith("/zarchive/"):
        return None
    if not (path.lower().endswith((".pdf", ".htm", ".html")) or path.endswith("/")):
        return None
    if path.endswith("/"):
        path += "index.html"
    return path.lstrip("/")


def davis(delay=1.5, max_files=40000):
    log("== Charles Davis: Test Match Database (1877 onwards), crawling from contents.pdf")
    root = RAW / "davis"
    queue, seen, new, have = deque(), set(), 0, 0
    for s in DAVIS_START:
        queue.append(s)
    while queue and len(seen) < max_files:
        url = queue.popleft()
        key = davis_key(url)
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        p = root / key
        if p.exists():
            data = p.read_bytes()
            have += 1
        else:
            data = fetch(url.replace("http://", "https://", 1)) or fetch(url.replace("https://", "http://", 1))
            time.sleep(delay)
            if data is None:
                continue
            save(p, data)
            new += 1
            if new % 50 == 0:
                log(f"   ...{new} new files so far ({have} already had), now at {key}")
        links = pdf_links(data, url) if key.lower().endswith(".pdf") else html_links(data, url)
        for link in links:
            k = davis_key(link)
            if k and k.lower() not in seen:
                queue.append(link)
        if key.lower().endswith("contents.pdf"):
            ok = sum(1 for x in links if davis_key(x))
            log(f"   contents.pdf: {len(links)} links found, {ok} of them inside the Test archive")
            if not ok:
                log("   WARNING: nothing to follow. Send data/raw/davis/zArchive/contents.pdf to Claude.")
    log(f"   Davis done: {new} new files, {have} already had, {len(seen)} looked at")


# ---------------------------------------------------------------- 4. ESPNcricinfo's old archive (i.imgci.com)
ESPN = "https://i.imgci.com"
ESPN_HOSTS = {"i.imgci.com", "static.espncricinfo.com", "static.cricinfo.com", "www.cricinfo.com",
              "www.espncricinfo.com", "content-aus.cricinfo.com", "content-usa.cricinfo.com"}
SKIP_DIRS = {"ARTICLES", "STATS", "PLAYERS", "GROUNDS", "INTERACTIVE", "PHOTOS", "PICTURES", "NEWS", "AUDIO",
             "VIDEO", "FEATURES", "AVERAGES", "SURVEYS", "OPINION", "REPORTS", "PREVIEWS", "IMAGES", "GIFS"}
MATCH_RE = re.compile(r"_(?:ODI|T\d)[\w-]*?_\d{1,2}(?:-\d{1,2})?[A-Z]{3}", re.I)
NOT_MATCH = ("REPORT", "PREVIEW", "SQUAD", "NOTES", "HIGHLIGHT", "CHAT", "PHOTO", "QUOTES", "REVIEW", "MOM")


def season_name(y):
    """1998 -> '1998-99', 1999 -> '1999-2000' (the archive's own naming)."""
    return "1999-2000" if y == 1999 else f"{y}-{str(y + 1)[-2:]}"


def espn_key(url):
    """('dir'|'file', key) for an archive page worth following/saving, else None. /db/ and /link_to_database/
    hold the same pages, so both map to one key."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or u.netloc.lower() not in ESPN_HOSTS:
        return None
    path = unquote(u.path)
    m = re.match(r"^/(?:db|link_to_database)/(ARCHIVE/.*)$", path, re.I)
    if not m:
        return None
    rest = m.group(1)
    parts = [x for x in rest.split("/") if x]
    if any(x.upper() in SKIP_DIRS for x in parts[:-1]) or (rest.endswith("/") and parts and parts[-1].upper() in SKIP_DIRS):
        return None
    if rest.endswith("/"):
        return ("dir", rest)
    name = parts[-1] if parts else ""
    if not name.lower().endswith((".html", ".htm")):
        return None
    if is_match_file(name):
        return ("file", rest)
    if MATCH_RE.search(name):
        return None  # match reports, previews, squads: not needed now (saves thousands of requests)
    return ("page", rest)  # tour home pages, schedules: read for links, not saved


def is_match_file(name):
    up = name.upper()
    if "BBB" in up or "PLYR-V-PLYR" in up:
        return True
    return bool(MATCH_RE.search(name)) and not any(x in up for x in NOT_MATCH)


def espn(seasons, label, delay=2.0, max_pages=60000, follow_pages=True):
    log(f"== ESPNcricinfo archive: {label}")
    root = RAW / "espn"
    prefixes = tuple(f"ARCHIVE/{s}/" for s in seasons)
    queue, seen = deque(), set()
    for s in seasons:
        queue.append(f"{ESPN}/link_to_database/ARCHIVE/{s}/")
    counts = {"dir": 0, "file_new": 0, "file_had": 0, "page": 0}
    while queue and len(seen) < max_pages:
        url = queue.popleft()
        k = espn_key(url)
        if not k:
            continue
        kind, key = k
        if key.lower() in seen or not key.startswith(prefixes):
            continue
        seen.add(key.lower())
        if kind == "file":
            p = root / key
            if p.exists():
                counts["file_had"] += 1
                data = p.read_bytes()
            else:
                data = fetch(f"{ESPN}/link_to_database/{key}")
                time.sleep(delay)
                if data is None:
                    continue
                save(p, data)
                counts["file_new"] += 1
                if counts["file_new"] % 50 == 0:
                    log(f"   ...{counts['file_new']} new match files, now at {key}")
            # match pages can link to their ball-by-ball / player-v-player pages
        else:
            cache = root / "_index" / (key if kind == "page" else key + "index.html")
            if cache.exists():
                data = cache.read_bytes()
            else:
                data = fetch(f"{ESPN}/link_to_database/{key}")
                time.sleep(delay)
                if data is None:
                    continue
                save(cache, data)
            counts["dir" if kind == "dir" else "page"] += 1
        for link in html_links(data, url):
            k2 = espn_key(link)
            if not k2 or k2[1].lower() in seen or not k2[1].startswith(prefixes):
                continue
            if k2[0] == "page" and not follow_pages:
                continue
            queue.append(link)
    log(f"   {label} done: {counts['file_new']} new match files, {counts['file_had']} already had, "
        f"{counts['dir']} folders + {counts['page']} pages read")


def espn_bbb():
    seasons = [season_name(y) for y in range(1997, 2003)] + ["WORLD_CUPS/WC99"]
    espn(seasons, "ball-by-ball and player-v-player, 1997-98 to 2002-03 (+ World Cup 1999)")


def espn_cards():
    seasons = [season_name(y) for y in range(1970, 1997)] + [f"WORLD_CUPS/WC{x}" for x in ("75", "79", "83", "87", "92", "96")]
    espn(seasons, "scorecards 1970-71 to 1996-97 (+ World Cups 1975-1996)")


# ---------------------------------------------------------------- status
def status():
    print("What is downloaded so far (data/raw/):")
    for name, sub, pat in (("Cricsheet", "cricsheet", "*"), ("Kaggle ODI", "kaggle/odi", "*.csv"),
                           ("Davis PDFs", "davis", "*.pdf"), ("ESPN match files", "espn/ARCHIVE", "*.htm*")):
        d = RAW / sub
        files = [p for p in d.rglob(pat) if p.is_file() and not p.name.endswith(".part")] if d.exists() else []
        print(f"  {name:18} {len(files):6} files  {mb(sum(p.stat().st_size for p in files)):>9}")
    e = RAW / "espn" / "ARCHIVE"
    if e.exists():
        names = [p.name.upper() for p in e.rglob("*.htm*")]
        print(f"     ESPN ball-by-ball: {sum('BBB' in n for n in names)}  player-v-player: {sum('PLYR-V-PLYR' in n for n in names)}")
    if LOG.exists():
        lines = LOG.read_text(encoding="utf-8").splitlines()
        fails = sum("FAILED" in x for x in lines)
        print(f"  Log: {len(lines)} lines, {fails} FAILED. Last lines:")
        for x in lines[-5:]:
            print("   ", x)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Everyball downloader")
    ap.add_argument("what", choices=["cricsheet", "kaggle", "davis", "espn-bbb", "espn-cards", "all", "status"])
    ap.add_argument("--force", action="store_true", help="re-download Cricsheet even if present")
    a = ap.parse_args(argv)
    try:
        if a.what in ("cricsheet", "all"):
            cricsheet(a.force)
        if a.what in ("kaggle", "all"):
            kaggle()
        if a.what in ("davis", "all"):
            davis()
        if a.what in ("espn-bbb", "all"):
            espn_bbb()
        if a.what in ("espn-cards", "all"):
            espn_cards()
        if a.what == "status":
            status()
    except KeyboardInterrupt:
        log("Stopped by you. Run the same command again to continue where it left off.")
        sys.exit(1)


if __name__ == "__main__":
    main()
