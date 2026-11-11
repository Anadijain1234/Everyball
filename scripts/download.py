"""
Everyball downloader: fetches the raw data into data/raw/ (never pushed to GitHub: too big).

  python scripts/download.py cricsheet     # ~2 min   Tests + ODIs ball by ball (2002 on) + player register
  python scripts/download.py kaggle        # seconds  unpacks the ODI scorecards 1971-2024 (zip already included)
  python scripts/download.py davis         # 6-7 h    Charles Davis's Test Match Database (1877-2020), with his permission
  python scripts/download.py espn-bbb      # ~1 h     Cricinfo ball-by-ball + player-v-player files, Oct 1998-2002, with ESPN's permission
  python scripts/download.py espn-cards    # 1-2 h    Cricinfo scorecards, two-year seasons 1970-71 to 1996-97 (who took each wicket)
  python scripts/download.py espn-years    # 1-2 h    Cricinfo one-year folders 1971-2002 (matches played in England etc.)
  python scripts/download.py retry         # try once more every file that failed earlier (never the dead links)
  python scripts/download.py all           # everything above, in that order (then retry)
  python scripts/download.py status        # what is downloaded so far

Safe to stop any time (Ctrl+C) and run again: finished files are skipped, half-finished ones are redone.
Polite: one request at a time, a pause after each (Davis 0.75 s, ESPN 1 s). If a site ever refuses us
("blocked" / "too many requests"), we slow down by ourselves and try that file again later: a refusal is
never treated as "not there". Only a plain "not found" counts as a dead link (listed in dead_*.txt).
Everything is logged to data/raw/download_log.txt (India time).
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
LOG = RAW / "download_log.txt"

S = requests.Session()
S.headers["User-Agent"] = "Everyball cricket-history project (github.com/Anadijain1234; slow, polite downloads, with permission)"


# ---------------------------------------------------------------- basics
IST = timezone(timedelta(hours=5, minutes=30))  # India time for logs and the tracker (the Codespace clock is UTC)
BACKOFF = 10  # seconds before the first retry after a network problem (doubles each time)


def log(msg):
    line = f"{datetime.now(IST):%Y-%m-%d %H:%M:%S}  {msg}"
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


MISSING = frozenset({401, 404, 410})  # the site says "not here" (Davis's site answers 401 for a folder that doesn't exist)
BLOCKED = frozenset({403, 429})       # the site refuses us: slow down and try again later, never "not here"


def fetch2(url, tries=4, timeout=60, missing=MISSING, blocked=BLOCKED):
    """GET with retries. Returns (bytes or None, status):
    'ok'; 'missing' (the site says the file isn't there); 'blocked' (the site refused: slow down, try later);
    'failed' (network trouble or server errors even after retries: goes on the retry list)."""
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code in missing:
                return None, "missing"
            if r.status_code in blocked:
                return None, "blocked"
            if r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.content, "ok"
        except Exception as e:  # network hiccup: wait longer each time
            if i == tries - 1:
                break
            wait = BACKOFF * (2 ** i)
            log(f"   retry {i + 1}/{tries} in {wait}s: {url} ({e})")
            time.sleep(wait)
    log(f"   FAILED after {tries} tries: {url}")
    return None, "failed"


def fetch(url, tries=4, timeout=60):
    """Bytes, or None if the file isn't there or still failing (used by retry)."""
    return fetch2(url, tries, timeout, missing=MISSING | BLOCKED, blocked=frozenset())[0]


class Pacer:
    """The pause after each request. A refusal doubles it (at least 5 s, at most 2 min); good answers bring it
    back down slowly. Also measures requests per minute for the tracker."""

    def __init__(self, base):
        self.base = self.pause = base
        self.times = deque()

    def done(self, status):
        self.times.append(time.time())
        if status == "blocked":
            self.pause = min(120.0, max(self.pause * 2, 5.0)) if self.base > 0 else 0
            log(f"   the site refused a request: slowing down, pause now {self.pause:.0f} s")
        elif status == "ok" and self.pause > self.base:
            self.pause = max(self.base, self.pause * 0.9)
        quick = status == "missing" and self.pause == self.base
        time.sleep(min(0.3, self.base) if quick else self.pause)

    def rate(self, minutes=10):
        now = time.time()
        while self.times and self.times[0] < now - minutes * 60:
            self.times.popleft()
        if len(self.times) < 2:
            return 0.0
        return len(self.times) / max(0.5, (now - self.times[0]) / 60)

    @property
    def slowed(self):
        return self.pause > self.base


def read_list(path):
    try:
        return {x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()}
    except FileNotFoundError:
        return set()


def note(path, line):
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def progress(name, **info):
    """A small JSON file the live tracker (scripts/tracker.py) reads: how much is found, done, failed."""
    info["updated"] = time.time()
    try:
        save(RAW / f"progress_{name}.json", json.dumps(info).encode())
    except Exception:
        pass


def note_failed(name, url):
    with (RAW / f"failed_{name}.txt").open("a", encoding="utf-8") as f:
        f.write(url + "\n")


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


def folder_fix(key):
    """A link whose folder doesn't match its file name, e.g. zArchive/1980s/1988/1988ELcov.pdf:
    the same file in the folder named after it (zArchive/1980s/1988EL/1988ELcov.pdf). None if it matches."""
    parts = key.split("/")
    m = re.match(r"^(\d{4}[A-Z]+)", parts[-1]) if len(parts) >= 3 else None
    if not m or parts[-2] == m.group(1):
        return None
    return "/".join(parts[:-2] + [m.group(1), parts[-1]])


def davis(delay=0.75, max_files=60000):
    """Crawl Davis's archive from contents.pdf. Every link is asked for at least once; nothing is skipped by guessing.
    'Not found' = dead link (asked once, listed in dead_davis.txt, never retried). A refusal = slow down and ask again
    later. Network trouble = retried, then listed in failed_davis.txt for `retry`."""
    log("== Charles Davis: Test Match Database (1877-2020), crawling from contents.pdf")
    root = RAW / "davis"
    dead_file = RAW / "dead_davis.txt"
    known_dead = read_list(dead_file)
    old = RAW / "failed_davis.txt"
    if old.exists():  # failures from an earlier run: they are simply asked for again below, and sorted properly
        note(RAW / "old_failed_davis.txt", old.read_text(encoding="utf-8").rstrip("\n"))
        old.unlink()
    queue, seen, queued, got = deque(), set(), set(), set()
    blocked_tries = {}
    c = {"new": 0, "had": 0, "failed": 0, "dead": 0}
    pacer = Pacer(delay)
    order = ["https", "http"]  # switches to http first if the secure address keeps failing
    for s in DAVIS_START:
        queue.append(s)
    started = time.time()

    def report(finished=False):
        progress("davis", label="Davis (Tests, 1877-2020)", done=c["new"] + c["had"], new=c["new"], had=c["had"],
                 failed=c["failed"], dead=c["dead"], known=len(seen) + (0 if finished else len(queue)),
                 queue=0 if finished else len(queue), rate=pacer.rate(), pause=pacer.pause, slowed=pacer.slowed,
                 started=started, finished=finished)

    last_report = 0.0
    while queue and len(seen) < max_files:
        if time.time() - last_report > 5:
            report()
            last_report = time.time()
        url = queue.popleft()
        key = davis_key(url)
        if not key or key in seen:
            continue
        queued.discard(key)
        if key.lower() in got:  # the same file linked with other capitals: we already have it
            seen.add(key)
            continue
        p = root / key
        if p.exists():
            seen.add(key)
            got.add(key.lower())
            c["had"] += 1
            data = p.read_bytes()
        elif key in known_dead:  # found dead in an earlier run
            seen.add(key)
            c["dead"] += 1
            continue
        else:
            plain = url.split("://", 1)[1]
            data, st = fetch2(f"{order[0]}://{plain}", tries=2)
            if st == "failed":  # network trouble: try the other address (http/https)
                data, st = fetch2(f"{order[1]}://{plain}", tries=3)
                if st == "ok":
                    order.reverse()
                    log(f"   switching to {order[0]}:// first (works better for this site)")
            pacer.done(st)
            if st == "blocked":
                blocked_tries[key] = blocked_tries.get(key, 0) + 1
                if blocked_tries[key] < 4:  # ask again later, more slowly
                    queue.append(url)
                    queued.add(key)
                    continue
                st = "failed"
            seen.add(key)
            if st == "missing":
                c["dead"] += 1
                note(dead_file, key)
                alt = folder_fix(key)
                if alt and alt not in seen and alt not in queued and alt.lower() not in got:
                    queued.add(alt)
                    queue.append(f"{order[0]}://www.sportstats.com.au/{alt}")
                continue
            if st == "failed":
                c["failed"] += 1
                note_failed("davis", url)
                continue
            save(p, data)
            got.add(key.lower())
            c["new"] += 1
            if c["new"] % 100 == 0:
                log(f"   ...{c['new']} new files so far ({c['had']} already had, {c['dead']} dead links), now at {key}")
        links = pdf_links(data, url) if key.lower().endswith(".pdf") else html_links(data, url)
        for link in links:
            k = davis_key(link)
            if k and k not in seen and k not in queued:
                queued.add(k)
                queue.append(link)
        if key.lower().endswith("contents.pdf"):
            ok = sum(1 for x in links if davis_key(x))
            log(f"   contents.pdf: {len(links)} links found, {ok} of them inside the Test archive")
            if not ok:
                log("   WARNING: nothing to follow. Send data/raw/davis/zArchive/contents.pdf to Claude.")
    report(finished=True)
    log(f"   Davis done: {c['new']} new files, {c['had']} already had, {c['dead']} dead links, "
        f"{c['failed']} failed, {len(seen)} looked at")


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


def espn(seasons, label, delay=1.0, max_pages=60000, follow_pages=True, name="espn"):
    """Crawl the old Cricinfo archive for the given season folders and save every match file.
    'Not found' = dead link (asked once, listed in dead_<name>.txt). ESPN's file store also answers 403 for a file
    that isn't there, so a 403 is asked again once later: if the site kept answering other requests in between, it is
    a dead link; if the whole site was refusing us, we slow down and it goes on the retry list instead."""
    log(f"== ESPNcricinfo archive: {label}")
    root = RAW / "espn"
    prefixes = tuple(f"ARCHIVE/{s}/" for s in seasons)
    dead_file = RAW / f"dead_{name}.txt"
    known_dead = {x.lower() for x in read_list(dead_file)}
    queue, seen, queued, files_known = deque(), set(), set(), set()
    for s in seasons:
        queue.append(f"{ESPN}/link_to_database/ARCHIVE/{s}/")
    counts = {"dir": 0, "file_new": 0, "file_had": 0, "page": 0, "failed": 0, "dead": 0}
    blocked_tries, ok_at_first_block, oks = {}, {}, [0]
    pacer = Pacer(delay)
    started = time.time()

    def report(finished=False):
        progress(name, label=label, done=counts["file_new"] + counts["file_had"], new=counts["file_new"],
                 had=counts["file_had"], failed=counts["failed"], dead=counts["dead"], known=len(files_known),
                 pages_read=counts["dir"] + counts["page"], queue=0 if finished else len(queue),
                 rate=pacer.rate(), pause=pacer.pause, slowed=pacer.slowed, started=started, finished=finished)

    def get(url, key):
        """('ok', data) / ('dead', None) / ('again', None) / ('failed', None)."""
        data, st = fetch2(f"{ESPN}/link_to_database/{key}")
        pacer.done(st)
        if st == "ok":
            oks[0] += 1
            return "ok", data
        if st == "missing":
            return "dead", None
        if st == "blocked":
            n = blocked_tries[key.lower()] = blocked_tries.get(key.lower(), 0) + 1
            ok_at_first_block.setdefault(key.lower(), oks[0])
            if n < 2:
                return "again", None
            return ("dead" if oks[0] > ok_at_first_block[key.lower()] else "failed"), None
        return "failed", None

    last_report = 0.0
    while queue and len(seen) < max_pages:
        if time.time() - last_report > 5:
            report()
            last_report = time.time()
        url = queue.popleft()
        k = espn_key(url)
        if not k:
            continue
        kind, key = k
        if key.lower() in seen or not key.startswith(prefixes):
            continue
        queued.discard(key.lower())
        if kind == "file":
            files_known.add(key.lower())
            p = root / key
            if p.exists():
                seen.add(key.lower())
                counts["file_had"] += 1
                data = p.read_bytes()
            elif key.lower() in known_dead:
                seen.add(key.lower())
                counts["dead"] += 1
                continue
            else:
                st, data = get(url, key)
                if st == "again":
                    queue.append(url)
                    queued.add(key.lower())
                    continue
                seen.add(key.lower())
                if st == "dead":
                    counts["dead"] += 1
                    note(dead_file, key)
                    continue
                if st == "failed":
                    counts["failed"] += 1
                    note_failed(name, url)
                    continue
                save(p, data)
                counts["file_new"] += 1
                if counts["file_new"] % 50 == 0:
                    log(f"   ...{counts['file_new']} new match files, now at {key}")
            # match pages can link to their ball-by-ball / player-v-player pages
        else:
            cache = root / "_index" / (key if kind == "page" else key + "index.html")
            if cache.exists():
                seen.add(key.lower())
                data = cache.read_bytes()
            else:
                st, data = get(url, key)
                if st == "again":
                    queue.append(url)
                    queued.add(key.lower())
                    continue
                seen.add(key.lower())
                if st != "ok":
                    continue
                save(cache, data)
            counts["dir" if kind == "dir" else "page"] += 1
        for link in html_links(data, url):
            k2 = espn_key(link)
            if not k2 or k2[1].lower() in seen or k2[1].lower() in queued or not k2[1].startswith(prefixes):
                continue
            if k2[0] == "page" and not follow_pages:
                continue
            queued.add(k2[1].lower())
            if k2[0] == "file":
                files_known.add(k2[1].lower())
            queue.append(link)
    report(finished=True)
    log(f"   {label} done: {counts['file_new']} new match files, {counts['file_had']} already had, "
        f"{counts['dead']} dead links, {counts['failed']} failed, "
        f"{counts['dir']} folders + {counts['page']} pages read")


def espn_bbb():
    seasons = [season_name(y) for y in range(1997, 2003)] + ["WORLD_CUPS/WC99"]
    espn(seasons, "ESPN ball-by-ball 1998-2002", name="espn_bbb")


def espn_cards():
    seasons = [season_name(y) for y in range(1970, 1997)] + [f"WORLD_CUPS/WC{x}" for x in ("75", "79", "83", "87", "92", "96")]
    espn(seasons, "ESPN scorecards 1970-1997", name="espn_cards")


def espn_years():
    """The archive keeps matches played in England (and other northern summers) in one-year folders like ARCHIVE/2001/,
    not in two-year ones like ARCHIVE/2001-02/. Folders that don't exist simply answer 'not found'."""
    espn([str(y) for y in range(1971, 2003)], "ESPN one-year folders 1971-2002", name="espn_years")


# ---------------------------------------------------------------- retry what failed
def retry():
    """Try every file listed in data/raw/failed_*.txt once more (slowly)."""
    log("== Retrying files that failed earlier")
    for f in sorted(RAW.glob("failed_*.txt")):
        urls = list(dict.fromkeys(x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip()))
        still = []
        for url in urls:
            if davis_key(url):
                p = RAW / "davis" / davis_key(url)
                tries = [url.replace("https://", "http://", 1), url.replace("http://", "https://", 1)]
            elif espn_key(url) and espn_key(url)[0] == "file":
                p = RAW / "espn" / espn_key(url)[1]
                tries = [f"{ESPN}/link_to_database/{espn_key(url)[1]}"]
            else:
                continue
            if p.exists():
                continue
            data = None
            for u in tries:
                data = fetch(u, tries=3)
                if data:
                    break
            time.sleep(2)
            if data:
                save(p, data)
                log(f"   got it this time: {p.name}")
            else:
                still.append(url)
        f.write_text("".join(u + "\n" for u in still), encoding="utf-8")
        log(f"   {f.name}: {len(urls) - len(still)} recovered, {len(still)} still missing")


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
    ap.add_argument("what", choices=["cricsheet", "kaggle", "davis", "espn-bbb", "espn-cards", "espn-years", "all",
                                     "retry", "status"])
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
        if a.what in ("espn-years", "all"):
            espn_years()
        if a.what in ("retry", "all"):
            retry()
        if a.what == "status":
            status()
    except KeyboardInterrupt:
        log("Stopped by you. Run the same command again to continue where it left off.")
        sys.exit(1)


if __name__ == "__main__":
    main()
