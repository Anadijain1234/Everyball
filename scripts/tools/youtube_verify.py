"""Let Gemini "watch" each candidate video (straight from its YouTube link, nothing is downloaded) and say
whether it really shows EVERY BALL, like a human checking it would. You don't have to watch them yourself.

How: for each video in data/lists/youtube_candidates.csv (full_match / full_innings, plus --with-extended),
Gemini looks at three 8-minute windows (near the start, the middle and the end) at 1 photo per second, low
resolution (cheap), and reports for each window: the teams, the score and overs on the screen at the start
and end, and whether the balls follow one after another or jump (highlights). From that the script decides:
  every_ball   - overs move ~1 ball every 30-60 s in all windows (a real full innings / match)
  partial      - every ball in some windows, jumps in others (e.g. one full innings + highlights)
  highlights   - overs jump in every window
  wrong_match  - the teams on screen are not the match we want
  unclear      - no score box / Gemini could not tell (check this one yourself)

Key: GEMINI_API_KEY in .env (gitignored). If it is not there the script asks once (hidden) and saves it.
Free tier: about one video a minute; it stops by itself when Google says the daily limit is reached, and the
next run continues (data/lists/youtube_verified.csv keeps what is done).

Usage (repo root):
  python3 scripts/tools/youtube_verify.py
  python3 scripts/tools/youtube_verify.py --with-extended      # also check the 30-90 min videos
  python3 scripts/tools/youtube_verify.py --model gemini-3.8-flash
"""
import argparse, csv, getpass, json, os, re, sys, time, urllib.error, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LISTS = os.path.join(ROOT, "data", "lists")
CAND = os.path.join(LISTS, "youtube_candidates.csv")
OUT = os.path.join(LISTS, "youtube_verified.csv")
API = "https://generativelanguage.googleapis.com/v1beta/"
FIELDS = ["key", "date", "match", "url", "title", "minutes", "ai_verdict", "teams_on_screen", "windows",
          "notes", "checked_at"]
PROMPT = """You are checking an old cricket broadcast on YouTube for a database. This clip is a window of the video.
Expected match: {match} ({date}), a one-day international.
Look at the TV score box and the play. Answer ONLY with JSON:
{{"teams_on_screen": "<teams you can see named, or unknown>",
  "score_box_visible": true/false,
  "start": "<score and overs on the screen at the start of this window, e.g. IND 62/2 12.3 ov, or unknown>",
  "end": "<score and overs at the end of this window, or unknown>",
  "balls_shown_one_after_another": true/false,
  "skips": "<short: replays only / cuts between overs / jumps of several overs / none>",
  "is_this_the_expected_match": "yes/no/unsure",
  "notes": "<max 15 words>"}}"""


class Quota(Exception):
    pass


def load_key():
    k = os.environ.get("GEMINI_API_KEY", "").strip()
    env = os.path.join(ROOT, ".env")
    if not k and os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.strip().startswith("GEMINI_API_KEY="):
                k = line.split("=", 1)[1].strip().strip('"').strip("'")
    if k:
        return k
    print("No Gemini API key found yet (get a free one at aistudio.google.com -> Get API key).")
    k = getpass.getpass("Paste your Gemini API key (it will not show) and press Enter: ").strip()
    if k:
        old = open(env, encoding="utf-8").read() if os.path.exists(env) else ""
        with open(env, "a", encoding="utf-8") as f:
            f.write(("" if not old or old.endswith("\n") else "\n") + "GEMINI_API_KEY=" + k + "\n")
        gi = os.path.join(ROOT, ".gitignore")
        if ".env" not in (open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []):
            open(gi, "a", encoding="utf-8").write("\n.env\n")
    return k


def call(path, key, body=None, timeout=240):
    req = urllib.request.Request(API + path + ("&" if "?" in path else "?") + "key=" + key,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        if e.code == 429 or "RESOURCE_EXHAUSTED" in msg:
            raise Quota(msg[:200])
        raise RuntimeError(f"Gemini error {e.code}: {msg[:300]}")   # never print the URL (has the key)


def pick_model(key, wanted):
    if wanted:
        return wanted
    names = [m["name"].split("/")[-1] for m in call("models?pageSize=200", key).get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n and not re.search(r"lite|image|tts|audio|live|exp|preview", n)]
    flash.sort(key=lambda n: [int(x) for x in re.findall(r"\d+", n)] or [0], reverse=True)
    if not flash:
        sys.exit("No Gemini Flash model found for this key. Pass one with --model.")
    return flash[0]


def window(key, model, url, start_s, end_s, match, date):
    body = {"contents": [{"parts": [
        {"fileData": {"fileUri": url}, "videoMetadata": {"startOffset": f"{start_s}s", "endOffset": f"{end_s}s", "fps": 1}},
        {"text": PROMPT.format(match=match, date=date)}]}],
        "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW", "responseMimeType": "application/json",
                             "temperature": 0}}
    r = call(f"models/{model}:generateContent", key, body)
    text = "".join(p.get("text", "") for c in r.get("candidates", []) for p in c.get("content", {}).get("parts", []))
    try:
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    except Exception:
        return {"notes": "could not read answer: " + text[:80]}


def overs_of(s):
    m = re.search(r"(\d{1,2})\.(\d)\s*ov", s or "") or re.search(r"\b(\d{1,2})\.([0-5])\b", s or "")
    return int(m.group(1)) * 6 + int(m.group(2)) if m else None


def decide(wins, minutes_window):
    shown = [w for w in wins if w.get("score_box_visible")]
    if any(str(w.get("is_this_the_expected_match", "")).lower() == "no" for w in wins) and \
            not any(str(w.get("is_this_the_expected_match", "")).lower() == "yes" for w in wins):
        return "wrong_match"
    if not shown:
        return "unclear"
    good = 0
    for w in shown:
        a, b = overs_of(w.get("start")), overs_of(w.get("end"))
        steady = w.get("balls_shown_one_after_another") is True
        if a is not None and b is not None and b >= a:
            balls = b - a                                  # 8 minutes of real play = about 8-16 balls
            steady = steady and balls <= 2.5 * minutes_window
        good += bool(steady)
    if good == len(shown):
        return "every_ball"
    return "partial" if good else "highlights"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-extended", action="store_true")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    if not os.path.exists(CAND):
        sys.exit("No data/lists/youtube_candidates.csv yet: run youtube_channels.py / youtube_search.py first.")
    rows = list(csv.DictReader(open(CAND, encoding="utf-8")))
    kinds = {"full_match", "full_innings"} | ({"extended"} if a.with_extended else set())
    rows = [r for r in rows if r["verdict"] in kinds]
    done = set()
    if os.path.exists(OUT):
        done = {(r["key"], r["url"]) for r in csv.DictReader(open(OUT, encoding="utf-8"))}
    seen, todo = set(), []
    for r in rows:
        if (r["key"], r["url"]) in done or (r["key"], r["url"]) in seen: continue
        seen.add((r["key"], r["url"])); todo.append(r)
    print(f"{len(rows)} candidate videos, {len(done)} already checked, {len(todo)} to check.")
    key = load_key()
    if not key: sys.exit("No key given.")
    model = pick_model(key, a.model)
    print(f"Using {model}.")
    new = not os.path.exists(OUT)
    fh = open(OUT, "a", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    if new: w.writeheader()
    tally = {}
    try:
        for i, r in enumerate(todo, 1):
            total = int(float(r["minutes"] or 0) * 60)
            win = 8 * 60
            starts = [min(300, max(0, total - win))] + ([total // 2 - win // 2, max(0, total - win - 300)] if total > 3 * win else [])
            wins = []
            print(f"{i:3}/{len(todo)} {r['key']} {r['match'][:32]} ({r['minutes']} min): checking {len(starts)} windows", flush=True)
            for j, s in enumerate(starts, 1):
                t0 = time.time()
                for attempt in range(2):
                    try:
                        wins.append(window(key, model, r["url"], s, s + win, r["match"], r["date"]))
                        x = wins[-1]
                        print(f"      window {j} ({s // 60}-{(s + win) // 60} min): {x.get('start', '?')} -> {x.get('end', '?')}, "
                              f"{'steady' if x.get('balls_shown_one_after_another') else 'jumps'} [{time.time() - t0:.0f} s]", flush=True)
                        break
                    except Quota:
                        raise
                    except Exception as e:
                        print(f"      window {j}: {str(e)[:90]} [{time.time() - t0:.0f} s]", flush=True)
                        if attempt == 1: wins.append({"notes": str(e)[:80]})
                        time.sleep(5)
            verdict = decide(wins, 8)
            tally[verdict] = tally.get(verdict, 0) + 1
            w.writerow({"key": r["key"], "date": r["date"], "match": r["match"], "url": r["url"], "title": r["title"],
                        "minutes": r["minutes"], "ai_verdict": verdict,
                        "teams_on_screen": "; ".join(str(x.get("teams_on_screen", "")) for x in wins),
                        "windows": " | ".join(f'{x.get("start", "?")} -> {x.get("end", "?")} '
                                              f'({"steady" if x.get("balls_shown_one_after_another") else "jumps"})'
                                              for x in wins),
                        "notes": " | ".join(str(x.get("notes", "")) for x in wins), "checked_at": time.strftime("%Y-%m-%d")})
            fh.flush()
            print(f"{i:3}/{len(todo)} {r['key']:9} {r['date']} {r['match'][:32]:32} {r['minutes']:>6} min -> {verdict}", flush=True)
            time.sleep(4)
    except Quota:
        print("Gemini's free limit for now is reached. Run the same command later (or tomorrow); it continues.")
    except KeyboardInterrupt:
        print("\nStopped by you. What is checked is saved.")
    finally:
        fh.close()
    print("This run:", tally, "| full list: data/lists/youtube_verified.csv")


if __name__ == "__main__":
    main()
