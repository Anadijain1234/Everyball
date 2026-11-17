"""Channel harvesting: list EVERY upload of the YouTube channels that post old full matches, then match the
titles to our missing ODIs on our side. Much cheaper than searching:
  search (what youtube_search.py does) = 100 quota units for 25 results
  listing a channel's uploads          =   1 unit per 50 videos   (a 20,000-video channel = 400 units)

Where the channels come from (all three are used):
  1. data/lists/youtube_channels.txt  - your own list, one per line: a channel link, @handle or UC... id
  2. channels behind the good videos youtube_search.py already found (full match / full innings / extended)
  3. a few channel searches ("classic cricket full match", ...) - once, about 800 units

Runs until YouTube says the daily quota is used up, then stops; run the same command the next day and it
continues from where it stopped (even in the middle of a big channel).
Every video that fits a missing match goes into data/lists/youtube_found.csv (same file as the search tool),
then the clean list data/lists/youtube_candidates.csv is rebuilt.

Usage (repo root):
  python3 scripts/tools/youtube_channels.py
  python3 scripts/tools/youtube_channels.py --no-discover      # only your list + channels already known
"""
import argparse, csv, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import youtube_search as Y

LISTS = os.path.join(Y.ROOT, "data", "lists")
MY_CHANNELS = os.path.join(LISTS, "youtube_channels.txt")
STATE = os.path.join(LISTS, "youtube_channels_progress.json")
DISCOVER = ["classic cricket full match", "old odi full match cricket", "vintage cricket full match",
            "cricket 1990s full match highlights", "world cup 1996 full match cricket",
            "sharjah cup full match", "cricket archive full match", "doordarshan cricket full match"]
YEARS = range(1971, 2006)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"channels": {}, "discovered": False}


def save_state(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1)


def parse_channel(line):
    """link / @handle / UC id -> ('id', 'UC..') or ('handle', '@x') or ('name', 'text')"""
    t = line.strip()
    if not t or t.startswith("#"): return None
    m = re.search(r"(UC[0-9A-Za-z_-]{22})", t)
    if m: return ("id", m.group(1))
    m = re.search(r"@([0-9A-Za-z._-]+)", t)
    if m: return ("handle", "@" + m.group(1))
    m = re.search(r"youtube\.com/(?:c|user)/([^/?#]+)", t)
    return ("name", m.group(1) if m else t)


def resolve(kind, val, key):
    """-> (channel id, title, uploads playlist id) ; costs 1 unit (100 for a plain name)"""
    if kind == "name":
        r = Y.api_get("search", {"part": "snippet", "type": "channel", "q": val, "maxResults": 1}, key)
        items = r.get("items", [])
        if not items: return None
        kind, val = "id", items[0]["snippet"]["channelId"]
    params = {"part": "snippet,contentDetails,statistics"}
    params["forHandle" if kind == "handle" else "id"] = val
    r = Y.api_get("channels", params, key)
    items = r.get("items", [])
    if not items: return None
    c = items[0]
    return c["id"], c["snippet"]["title"], c["contentDetails"]["relatedPlaylists"]["uploads"], \
        int(c.get("statistics", {}).get("videoCount", 0) or 0)


def known_good_channels(key):
    """channels behind videos youtube_search.py judged full match / full innings / extended (1 unit per 50 videos)"""
    if not os.path.exists(Y.FOUND): return {}
    vids = [r["video_id"] for r in csv.DictReader(open(Y.FOUND, encoding="utf-8"))
            if r["verdict"] in ("full_match", "full_innings", "extended")]
    vids = list(dict.fromkeys(vids))
    out = {}
    for i in range(0, len(vids), 50):
        r = Y.api_get("videos", {"part": "snippet", "id": ",".join(vids[i:i + 50])}, key)
        for v in r.get("items", []):
            sn = v.get("snippet") or {}
            if sn.get("channelId"): out[sn["channelId"]] = sn.get("channelTitle", "")
    return out


def candidate_index(matches):
    by_year = {}
    for m in matches:
        by_year.setdefault(m["date"][:4], []).append(m)
    return by_year


def years_in(title):
    ys = set(re.findall(r"(?<!\d)(19[7-9]\d|200[0-5])(?!\d)", title))
    for a, b in re.findall(r"(?<!\d)(19[7-9]\d|200[0-4])\s*[-/]\s*(\d\d)(?!\d)", title):
        ys.add(a); ys.add(a[:2] + b)
    return ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-discover", action="store_true")
    ap.add_argument("--with-tests", action="store_true")
    a = ap.parse_args()
    matches = list(csv.DictReader(open(Y.MISSING, encoding="utf-8")))
    by_key = {m["key"]: m for m in matches}
    if not a.with_tests:
        matches = [m for m in matches if m["format"] != "Test"]
    by_year = candidate_index(matches)
    series_cities = {}
    for m in matches:
        k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
        series_cities.setdefault(k, set()).add((m["city"] or "").lower())
    key = Y.load_key() or Y.ask_key()
    if not key: sys.exit("No key given.")
    st = load_state()
    if not os.path.exists(MY_CHANNELS):
        open(MY_CHANNELS, "w", encoding="utf-8").write(
            "# One YouTube channel per line: a link, an @handle or a UC... id. Lines starting with # are ignored.\n")
    new_file = not os.path.exists(Y.FOUND)
    out = open(Y.FOUND, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(out, fieldnames=Y.FOUND_FIELDS)
    if new_file: w.writeheader()
    units = 0
    added = {}
    try:
        # ---- 1. collect channels
        queue = {}
        for qk, c in st["channels"].items():          # channels started on an earlier day
            if not c.get("done") and c.get("kind"):
                queue[qk] = (c["kind"], c["val"])
        for line in open(MY_CHANNELS, encoding="utf-8"):
            p = parse_channel(line)
            if p: queue[f"{p[0]}:{p[1]}"] = p
        for cid, title in known_good_channels(key).items():
            queue.setdefault(f"id:{cid}", ("id", cid))
        units += 2
        if not a.no_discover and not st.get("discovered"):
            for q in DISCOVER:
                r = Y.api_get("search", {"part": "snippet", "type": "channel", "q": q, "maxResults": 25}, key)
                units += 100
                for it in r.get("items", []):
                    cid = it["snippet"]["channelId"]
                    queue.setdefault(f"id:{cid}", ("id", cid))
            st["discovered"] = True
            save_state(st)
        print(f"{len(queue)} channels to look at.")
        # ---- 2. harvest each channel (resumes in the middle of a channel)
        for qk, (kind, val) in queue.items():
            ch = st["channels"].get(qk) or {}
            if ch.get("done"): continue
            if "uploads" not in ch:
                res = resolve(kind, val, key); units += 1
                if not res:
                    st["channels"][qk] = {"done": True, "note": "not found"}; save_state(st); continue
                ch = {"kind": kind, "val": val, "id": res[0], "title": res[1], "uploads": res[2], "videos": res[3],
                      "seen": 0, "hits": 0, "page": None}
            print(f"  {ch['title']}  ({ch['videos']} videos)", flush=True)
            while True:
                params = {"part": "snippet", "playlistId": ch["uploads"], "maxResults": 50}
                if ch.get("page"): params["pageToken"] = ch["page"]
                r = Y.api_get("playlistItems", params, key); units += 1
                items = r.get("items", [])
                ch["seen"] += len(items)
                # keep titles with a year we need and two team names of a missing match that year
                keep = []
                for it in items:
                    t = it["snippet"]["title"]
                    vid = it["snippet"].get("resourceId", {}).get("videoId")
                    if not vid: continue
                    for y in years_in(t):
                        for m in by_year.get(y, []):
                            if Y.has_team(t, m["team1"]) and Y.has_team(t, m["team2"]):
                                keep.append((vid, t, m))
                if keep:
                    ids = list(dict.fromkeys(v for v, _, _ in keep))
                    mins = {}
                    for i in range(0, len(ids), 50):
                        d = Y.api_get("videos", {"part": "contentDetails", "id": ",".join(ids[i:i + 50])}, key); units += 1
                        for v in d.get("items", []):
                            mins[v["id"]] = Y.iso_minutes(v["contentDetails"].get("duration"))
                    for vid, t, m in keep:
                        k = (frozenset([m["team1"], m["team2"]]), m["date"][:4], m["format"])
                        others = series_cities.get(k, set()) - {(m["city"] or "").lower()}
                        verdict, sure, reason, _ = Y.judge(m, t, mins.get(vid, 0), others)
                        if verdict in ("wrong_match",): continue
                        w.writerow({"key": m["key"], "format": m["format"], "number": m["number"], "date": m["date"],
                                    "match": f'{m["team1"]} v {m["team2"]}, {m["city"] or m["ground"]}',
                                    "query": "channel: " + ch["title"], "video_id": vid,
                                    "url": "https://www.youtube.com/watch?v=" + vid, "title": t,
                                    "channel": ch["title"], "minutes": round(mins.get(vid, 0), 1), "verdict": verdict,
                                    "how_sure": sure, "reason": reason, "searched_at": time.strftime("%Y-%m-%d")})
                        ch["hits"] += 1
                        added[verdict] = added.get(verdict, 0) + 1
                    out.flush()
                ch["page"] = r.get("nextPageToken")
                st["channels"][qk] = ch
                save_state(st)
                if not ch["page"]:
                    ch["done"] = True; st["channels"][qk] = ch; save_state(st)
                    print(f"    done: {ch['seen']} videos read, {ch['hits']} fit a missing ODI", flush=True)
                    break
    except Y.QuotaDone:
        print("Daily quota used up. Run the same command tomorrow; it continues from here (even mid-channel).")
    except Y.StopRun as e:
        print("Stopped:", e)
    except KeyboardInterrupt:
        print("\nStopped by you. Progress is saved.")
    finally:
        out.close(); save_state(st)
    done_ch = sum(1 for c in st["channels"].values() if c.get("done"))
    print(f"About {units} quota units used this run. Channels finished: {done_ch}/{len(st['channels'])}. "
          f"New videos kept: {added}")
    n = Y.review(by_key)
    print(f"Clean list: data/lists/youtube_candidates.csv ({n} videos, each given to the one match it fits).")
    best = sorted(((c.get("hits", 0), c.get("title", k)) for k, c in st["channels"].items()), reverse=True)[:10]
    if best:
        print("Channels with the most fits so far:", "; ".join(f"{t} ({h})" for h, t in best if h))


if __name__ == "__main__":
    main()
