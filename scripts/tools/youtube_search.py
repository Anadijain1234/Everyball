"""Find full-match videos on YouTube for matches that have no ball-by-ball.

Uses the official YouTube Data API v3 (free quota: 10,000 units a day).
One search costs 100 units, so about 95 searches a day. The script remembers
where it stopped (data/lists/youtube_progress.json), so run the same command
once a day until the list is done.

Your key: put  YOUTUBE_API_KEY=...  in the .env file at the repo root
(.env is gitignored) or set it as a Codespaces secret. Never paste it in chat.
The script never prints the key.

Usage (from the repo root):
  python3 scripts/tools/youtube_search.py                  # until today's quota is used up
  python3 scripts/tools/youtube_search.py --max-searches 20
  python3 scripts/tools/youtube_search.py --dry-run        # show the next queries, no API calls
  python3 scripts/tools/youtube_search.py --only-priority 1   # India ODIs only

Input : data/lists/missing_matches.csv  (key, format, number, date, team1, team2, ground, city, series, priority)
Output: data/lists/youtube_found.csv    (one row per candidate video, with a verdict)
        data/lists/youtube_progress.json (which matches are already searched)
"""
import argparse, csv, getpass, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MISSING = os.path.join(ROOT, "data", "lists", "missing_matches.csv")
FOUND = os.path.join(ROOT, "data", "lists", "youtube_found.csv")
PROGRESS = os.path.join(ROOT, "data", "lists", "youtube_progress.json")
API = "https://www.googleapis.com/youtube/v3/"
FOUND_FIELDS = ["key", "format", "number", "date", "match", "query", "video_id", "url", "title",
                "channel", "minutes", "verdict", "how_sure", "reason", "searched_at"]

ALIASES = {
    "India": ["india", "ind"], "Pakistan": ["pakistan", "pak"], "Australia": ["australia", "aus"],
    "England": ["england", "eng"], "West Indies": ["west indies", "windies", "wi"],
    "New Zealand": ["new zealand", "nz", "kiwis"], "Sri Lanka": ["sri lanka", "srilanka", "sl"],
    "South Africa": ["south africa", "sa", "rsa", "proteas"], "Zimbabwe": ["zimbabwe", "zim"],
    "Bangladesh": ["bangladesh", "ban", "bd"], "Kenya": ["kenya", "ken"],
    "United Arab Emirates": ["uae", "united arab emirates"], "Netherlands": ["netherlands", "holland", "ned"],
    "Canada": ["canada", "can"], "Scotland": ["scotland", "sco"], "Ireland": ["ireland", "ire"],
    "Bermuda": ["bermuda"], "East Africa": ["east africa"], "Namibia": ["namibia", "nam"],
    "Afghanistan": ["afghanistan", "afg"], "Hong Kong": ["hong kong"], "USA": ["usa", "united states"],
    "World XI": ["world xi"], "ICC World XI": ["world xi"], "Asia XI": ["asia xi"], "Africa XI": ["africa xi"],
}


def ask_key():
    """Ask for the key once in the terminal (hidden), keep it in .env (gitignored), then go."""
    print("No YouTube API key found yet.")
    key = getpass.getpass("Paste your YouTube API key (it will not show) and press Enter: ").strip()
    if not key:
        return ""
    env = os.path.join(ROOT, ".env")
    old = open(env, encoding="utf-8").read() if os.path.exists(env) else ""
    with open(env, "a", encoding="utf-8") as f:
        f.write(("" if old == "" or old.endswith("\n") else "\n") + "YOUTUBE_API_KEY=" + key + "\n")
    gi = os.path.join(ROOT, ".gitignore")
    lines = open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []
    if ".env" not in lines:
        with open(gi, "a", encoding="utf-8") as f:
            f.write("\n.env\n")
    print("Key saved in .env (kept out of GitHub). Starting the search...")
    return key


def load_key():
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    env = os.path.join(ROOT, ".env")
    if not key and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("YOUTUBE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


def api_get(endpoint, params, key):
    url = API + endpoint + "?" + urllib.parse.urlencode({**params, "key": key})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        if isinstance(e, urllib.error.HTTPError):
            return handle_http(e)
        raise StopRun(f"No internet connection to YouTube ({e.reason}). Progress is saved; run again later.")
    except TimeoutError:
        raise StopRun("YouTube did not answer in time. Progress is saved; run again later.")


def handle_http(e):
    body = e.read().decode("utf-8", "replace")
    if e.code == 403 and ("quotaExceeded" in body or "dailyLimitExceeded" in body):
        raise QuotaDone()
    if "accessNotConfigured" in body or "SERVICE_DISABLED" in body:
        raise StopRun("This key's Google Cloud project does not have 'YouTube Data API v3' switched on. "
                      "Google Cloud console -> APIs & Services -> Library -> YouTube Data API v3 -> Enable, then run again.")
    if e.code == 400 and ("API_KEY_INVALID" in body or "keyInvalid" in body or "API key not valid" in body):
        raise StopRun("The API key is not valid. Remove the YOUTUBE_API_KEY line from .env and run again to paste it.")
    # never echo the URL (it contains the key)
    raise StopRun(f"YouTube API error {e.code}: {body[:300]}")


class QuotaDone(Exception):
    pass


class StopRun(Exception):
    pass


def iso_minutes(d):
    """PT1H23M45S -> 83.75"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0.0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 1440 + h * 60 + mi + s / 60


def has_team(title, team):
    t = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
    return any(f" {a} " in t for a in ALIASES.get(team, [team.lower()]))


TOURNAMENTS = ["world cup", "sahara", "pepsi", "coca cola", "independence cup", "asia cup", "titan cup",
               "singer", "wills", "hero cup", "champions trophy", "nehru cup", "rothmans", "benson", "texaco",
               "natwest", "standard bank", "silver jubilee", "austral asia", "akai", "carlton", "sharjah cup",
               "emirates", "nidahas", "friendship cup", "mandela", "red stripe", "cable wireless", "aiwa",
               "ld trophy", "lg cup", "bank alfalah", "paktel", "prudential", "reliance", "commonwealth bank"]
STAGE_WORDS = [("quarter", r"quarter[ -]?final|\bq ?final|\bqf\b"), ("semi", r"semi[ -]?final|\bsf\b"),
               ("final", r"\bfinal\b")]
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}


def flat(x):
    return " " + re.sub(r"[^a-z0-9]+", " ", (x or "").lower()) + " "


def stage_of(text):
    t = (text or "").lower()
    for name, rx in STAGE_WORDS:
        if re.search(rx, t):
            m = re.search(r"\b(1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth)\s+(?:semi[ -]?)?final", t)
            k = m.group(1) if m else ""
            return name, (ORD.get(k) or int(k[0]) if k else 0)
    return "", 0


def ordinal_of(text):
    """'3rd ODI', 'Match 6', '6th match', 'Game 1' -> number"""
    t = (text or "").lower()
    m = (re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\s+(?:odi|match|one ?day|game)\b", t)
         or re.search(r"\b(?:odi|match|game)\s*(?:no\.?\s*)?#?(\d{1,2})\b", t))
    return int(m.group(1)) if m else 0


def judge(match, title, minutes, other_cities=()):
    """Return (verdict, how_sure, reason, score). verdict 'wrong_match' = not this game.
    Hard rejects: names a team not in this match; Test video for an ODI (or the reverse); no year;
    another match's city of the same series; a different tournament (when we know ours);
    a different stage (final / semi / quarter) or a different match number."""
    low = title.lower()
    ft = flat(title)
    mine = {match["team1"], match["team2"]}
    named = {k for k, al in ALIASES.items() if any(f" {a} " in ft for a in al)}
    named = {k.replace("ICC ", "") for k in named}
    if named - mine - {"World XI"}:
        return "wrong_match", "", "names another team", 0
    is_test_title = re.search(r"\b(\d+(st|nd|rd|th)\s+test|test match|test series|only test|\d(st|nd|rd|th) test|day [1-5])\b", low)
    if match["format"] == "ODI" and is_test_title:
        return "wrong_match", "", "a Test video", 0
    if match["format"] == "Test" and re.search(r"\bodi\b|one ?day", low):
        return "wrong_match", "", "an ODI video", 0
    year = match["date"][:4]
    if not (year in title or (year[2:] + "/") in title or ("/" + year[2:]) in title):
        return "wrong_match", "", "no year", 0
    place = (match.get("city") or "").lower()
    at_place = bool(place) and place in low
    others = [c for c in other_cities if c and c != place and c in low]
    if others and not at_place:
        return "wrong_match", "", f"another match's city ({others[0]})", 0
    series = flat(match.get("series_full") or match.get("series"))
    ours = [k for k in TOURNAMENTS if f" {k} " in series]
    theirs = [k for k in TOURNAMENTS if f" {k} " in ft]
    if theirs and ours and not set(theirs) & set(ours):
        return "wrong_match", "", f"another tournament ({theirs[0]})", 0
    st_t, n_t = stage_of(title)
    st_m, n_m = stage_of(match.get("match_name"))
    if st_t and st_t != st_m:
        return "wrong_match", "", f"title is a {st_t}, this match is not", 0
    if st_t and n_t and n_m and n_t != n_m:
        return "wrong_match", "", f"title is final {n_t}, this is final {n_m}", 0
    o_t, o_m = ordinal_of(title), ordinal_of(match.get("match_name"))
    if o_t and o_m and o_t != o_m and not st_t:
        return "wrong_match", "", f"title is match {o_t}, this is match {o_m}", 0
    score = 2 * len(named & mine) + (3 if at_place else 0) + (2 if set(theirs) & set(ours) else 0)
    # a score or total from our scorecard in the title (e.g. "143(131)", "Chase 316", "Anwar 194")
    ours_n = set((match.get("numbers") or "").split())
    hits = [n for n in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", title) if n in ours_n and n != year[2:]]
    score += 3 * min(len(set(hits)), 2)
    score += 3 if (st_t and st_t == st_m) else 0
    score += 3 if (o_t and o_t == o_m) else 0
    if score < 4:
        return "wrong_match", "", "too little in the title to be sure", score
    sure = "high" if score >= 7 else "medium"
    cut = "ball by ball" in low or "ball-by-ball" in low or "full innings" in low
    if match["format"] == "Test":
        if minutes >= 300:
            return "full_match", sure, "Test, 5h+", score
        if minutes >= 120:
            return "extended", sure, "Test, 2-5h", score
        if cut and minutes >= 15:
            return "full_innings", sure, "one innings / one batter, every ball", score
        return "short", sure, "Test, under 2h", score
    highlights = "highlight" in low and not ("full match" in low or "every ball" in low or cut)
    if highlights:
        return ("extended" if minutes >= 30 else "short"), sure, "title says highlights", score
    if minutes >= 200:
        return "full_match", sure, "ODI, 3h20m+", score
    if minutes >= 90:
        return "full_innings", sure, "ODI, 90m+ (check: one innings or long highlights)", score
    if cut and minutes >= 15:
        return "full_innings", sure, "title says ball-by-ball / full innings", score
    if minutes >= 30:
        return "extended", sure, "ODI, 30-90m", score
    return "short", sure, "under 30m", score


def review(matches_by_key):
    """Re-judge everything in youtube_found.csv with the current rules and give each video to the one
    match it fits best. Writes data/lists/youtube_candidates.csv (only extended / full_innings / full_match)."""
    if not os.path.exists(FOUND):
        return 0
    rows = list(csv.DictReader(open(FOUND, encoding="utf-8")))
    series_cities = {}
    for m in matches_by_key.values():
        k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
        series_cities.setdefault(k, set()).add((m.get("city") or "").lower())
    fits = {}
    for r in rows:
        m = matches_by_key.get(r["key"])
        if not m:
            continue
        k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
        others = series_cities.get(k, set()) - {(m.get("city") or "").lower()}
        v, sure, reason, score = judge(m, r["title"], float(r["minutes"] or 0), others)
        if v in ("wrong_match", "short"):
            continue
        fits.setdefault(r["video_id"], []).append((score, r["key"], v, sure, reason, r))
    out = []
    for vid, lst in fits.items():
        top = max(x[0] for x in lst)
        win = [x for x in lst if x[0] == top]
        keys = [x[1] for x in win]
        for score, key, v, sure, reason, r in win:
            out.append({"key": key, "date": r["date"], "match": r["match"], "verdict": v,
                        "how_sure": sure if len(win) == 1 else "ambiguous",
                        "also_fits": ";".join(k for k in keys if k != key), "minutes": r["minutes"],
                        "title": r["title"], "channel": r["channel"], "url": r["url"], "reason": reason,
                        "score": score})
    rank = {"full_match": 0, "full_innings": 1, "extended": 2}
    pr = {k: int(m["priority"]) for k, m in matches_by_key.items()}
    out.sort(key=lambda x: (pr.get(x["key"], 9), x["date"], rank[x["verdict"]], -float(x["minutes"])))
    cand = os.path.join(ROOT, "data", "lists", "youtube_candidates.csv")
    with open(cand, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["key", "date", "match", "verdict", "how_sure", "also_fits", "minutes",
                                          "title", "channel", "url", "reason", "score"])
        w.writeheader()
        w.writerows(out)
    return len(out)


def build_query(m):
    place = m["city"] or m["ground"]
    kind = "Test" if m["format"] == "Test" else "ODI"
    return f'{m["team1"]} vs {m["team2"]} {m["date"][:4]} {place} {kind} full match'


def search_one(m, key, other_cities=()):
    q = build_query(m)
    res = api_get("search", {"part": "snippet", "type": "video", "maxResults": 25, "q": q,
                             "relevanceLanguage": "en", "safeSearch": "none"}, key)
    ids = [it["id"]["videoId"] for it in res.get("items", []) if it.get("id", {}).get("videoId")]
    rows = []
    if not ids:
        return q, rows
    det = api_get("videos", {"part": "contentDetails,snippet", "id": ",".join(ids[:50])}, key)
    for v in det.get("items", []):
        title = v["snippet"]["title"]
        mins = iso_minutes(v["contentDetails"].get("duration"))
        verdict, sure, reason, _ = judge(m, title, mins, other_cities)
        if verdict == "wrong_match":
            continue
        rows.append({"key": m["key"], "format": m["format"], "number": m["number"], "date": m["date"],
                     "match": f'{m["team1"]} v {m["team2"]}, {m["city"] or m["ground"]}', "query": q,
                     "video_id": v["id"], "url": "https://www.youtube.com/watch?v=" + v["id"],
                     "title": title, "channel": v["snippet"].get("channelTitle", ""),
                     "minutes": round(mins, 1), "verdict": verdict, "how_sure": sure, "reason": reason,
                     "searched_at": time.strftime("%Y-%m-%d")})
    return q, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-searches", type=int, default=0, help="0 = no limit: keep going until YouTube says the daily quota is used up")
    ap.add_argument("--only-priority", type=int, default=0)
    ap.add_argument("--with-tests", action="store_true", help="also search Tests (default: ODIs only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true", help="only rebuild youtube_candidates.csv from what is already found")
    a = ap.parse_args()

    matches = list(csv.DictReader(open(MISSING, encoding="utf-8")))
    by_key = {m["key"]: m for m in matches}
    if a.review:
        n = review(by_key)
        print(f"youtube_candidates.csv rebuilt: {n} videos that fit one match (extended or better).")
        return
    if not a.with_tests:
        matches = [m for m in matches if m["format"] != "Test"]     # ODIs only (1,486 missing)
    if a.only_priority:
        matches = [m for m in matches if int(m["priority"]) == a.only_priority]
    done = json.load(open(PROGRESS)) if os.path.exists(PROGRESS) else {}
    todo = [m for m in matches if m["key"] not in done]
    series_cities = {}
    for m in csv.DictReader(open(MISSING, encoding="utf-8")):
        k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
        series_cities.setdefault(k, set()).add((m["city"] or "").lower())
    print(f"{len(matches)} matches in the list, {len(matches) - len(todo)} already searched, {len(todo)} left.")
    if a.dry_run:
        for m in todo[:a.max_searches or 20]:
            print("  ", m["key"], "|", build_query(m))
        return
    key = load_key() or ask_key()
    if not key:
        sys.exit("No key given, nothing searched.")

    new_file = not os.path.exists(FOUND)
    out = open(FOUND, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(out, fieldnames=FOUND_FIELDS)
    if new_file:
        w.writeheader()
    counts = {}
    n = 0
    try:
        for m in (todo[:a.max_searches] if a.max_searches else todo):
            k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
            others = series_cities.get(k, set()) - {(m["city"] or "").lower()}
            q, rows = search_one(m, key, others)
            for r in rows:
                w.writerow(r)
                counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            best = max((r["verdict"] for r in rows), default="nothing",
                       key=lambda v: ["nothing", "short", "extended", "full_innings", "full_match"].index(v))
            done[m["key"]] = {"best": best, "videos": len(rows), "date": time.strftime("%Y-%m-%d")}
            n += 1
            print(f"{n:3} {m['key']:9} {m['date']} {m['team1']} v {m['team2']}: best = {best}")
            out.flush()
            json.dump(done, open(PROGRESS, "w"), indent=0)
            time.sleep(1)
    except QuotaDone:
        print("Daily quota used up. Run the same command again tomorrow; it continues from here.")
    except StopRun as e:
        print("Stopped:", e)
    except KeyboardInterrupt:
        print("\nStopped by you (Ctrl+C). Progress is saved; the same command continues from here.")
    finally:
        out.close()
        json.dump(done, open(PROGRESS, "w"), indent=0)
    left = len([m for m in matches if m["key"] not in done])
    best = {}
    for v in done.values():
        best[v["best"]] = best.get(v["best"], 0) + 1
    print(f"Searched today: {n}. Left: {left}. Videos kept today by verdict: {counts}")
    print(f"All searched so far, best video per match: {best}")
    n = review(by_key)
    print(f"Clean list for review: data/lists/youtube_candidates.csv ({n} videos, each given to the one match it fits).")


if __name__ == "__main__":
    main()
