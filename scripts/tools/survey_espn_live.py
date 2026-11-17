import sys, os, gzip, json, glob, re
from collections import Counter, defaultdict
from multiprocessing import Pool

D = sys.argv[1]
BUCKETS = ["<1990", "1990s", "2000-04", "2005-09", "2010-14", "2015-19", "2020+"]
def bucket(y):
    y = int(y) if y and str(y).isdigit() else 0
    if y < 1990: return 0
    if y < 2000: return 1
    if y < 2005: return 2
    if y < 2010: return 3
    if y < 2015: return 4
    if y < 2020: return 5
    return 6
def filled(v): return v not in (None, "", [], {})
def tname(v): return type(v).__name__

def new_stats(): return defaultdict(lambda: [[0]*7, [0]*7, set(), None])
def note(st, path, v, b):
    s = st[path]; s[0][b] += 1
    if filled(v):
        s[1][b] += 1; s[2].add(tname(v))
        if s[3] is None and not isinstance(v, (dict, list)): s[3] = str(v)[:40]

def walk(st, prefix, d, b, depth):
    for k, v in d.items():
        key = "{n}" if k.isdigit() else k
        p = prefix + "." + key
        note(st, p, v, b)
        if depth > 1:
            if isinstance(v, dict) and v:
                walk(st, p, v, b, depth - 1)
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                for x in v: walk(st, p + "[]", x, b, depth - 1)

def one(path):
    S = {k: new_stats() for k in ("record", "match", "player", "scorecard", "item")}
    VC = defaultdict(Counter); T = defaultdict(lambda: [0]*7); CONS = defaultdict(lambda: [0]*7)
    recs = []
    raw = gzip.open(path, "rb").read()
    data = json.loads(raw)
    for r in data:
        y = (r.get("start_date") or "0000")[:4]; b = bucket(y)
        walk(S["record"], "rec", r, b, 1)
        if isinstance(r.get("match"), dict): walk(S["match"], "match", r["match"], b, 1)
        for p in (r.get("players") or {}).values(): walk(S["player"], "pl", p, b, 2)
        for si in (r.get("scorecard_innings") or []): walk(S["scorecard"], "sc", si, b, 2)
        VC["status"][(r.get("format"), r.get("status"))] += 1
        sc = {si.get("inningNumber"): si for si in (r.get("scorecard_innings") or [])}
        n_items = 0; ids = Counter()
        for inn_no, items in (r.get("innings") or {}).items():
            if not isinstance(items, list):
                VC["innings_value_type"][tname(items)] += 1; continue
            legal = runs = 0; last_total = None
            for it in items:
                n_items += 1; ids[it.get("id")] += 1
                walk(S["item"], "it", it, b, 2)
                for f in ("pitchLine", "pitchLength", "shotType", "shotControl", "dismissalType", "wagonZone"):
                    if filled(it.get(f)): VC[f][it.get(f)] += 1
                for f in ("commentTextItems", "commentPreTextItems", "commentPostTextItems"):
                    xs = it.get(f) or []
                    if xs: T[f + " items"][b] += 1
                    for x in xs:
                        VC[f + ".type"][x.get("type")] += 1
                        T[f + " chars"][b] += len(x.get("html") or x.get("text") or "")
                if it.get("over"): T["items with over summary"][b] += 1
                T["items"][b] += 1
                if not (it.get("wides") or it.get("noballs")): legal += 1
                runs += it.get("totalRuns") or 0
                if it.get("totalInningRuns") is not None: last_total = it.get("totalInningRuns")
            s = sc.get(int(inn_no)) if str(inn_no).isdigit() else None
            if not items: CONS["innings empty"][b] += 1
            elif s is None: CONS["no scorecard innings"][b] += 1
            else:
                dr = abs(runs - (s.get("runs") or 0)); dl = abs(legal - (s.get("balls") or 0))
                CONS["runs exact" if dr == 0 else "runs off 1-5" if dr <= 5 else "runs off >5"][b] += 1
                CONS["balls exact" if dl == 0 else "balls off 1-6" if dl <= 6 else "balls off >6"][b] += 1
                if last_total is not None:
                    CONS["last running total = scorecard" if last_total == s.get("runs") else "last running total differs"][b] += 1
        dup = sum(c - 1 for c in ids.values() if c > 1)
        recs.append((r.get("format"), r.get("number"), r.get("espn_id"), r.get("start_date"), r.get("status"), n_items,
                     len(r.get("scorecard_innings") or []), len(r.get("players") or {}), dup, r.get("label"), os.path.basename(path)))
    S2 = {k: {p: [v[0], v[1], sorted(v[2]), v[3]] for p, v in st.items()} for k, st in S.items()}
    return S2, {k: Counter(c) for k, c in VC.items()}, {k: list(v) for k, v in T.items()}, {k: list(v) for k, v in CONS.items()}, recs, len(raw)

def merge(A, B):
    for p, s in B.items():
        a = A[p]
        for i in range(7): a[0][i] += s[0][i]; a[1][i] += s[1][i]
        a[2] |= set(s[2])
        if a[3] is None: a[3] = s[3]

if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(D, "*.json.gz")))
    S = {k: new_stats() for k in ("record", "match", "player", "scorecard", "item")}
    VC = defaultdict(Counter); T = defaultdict(lambda: [0]*7); CONS = defaultdict(lambda: [0]*7); recs = []; raw = 0
    with Pool(2) as pool:
        for s, vc, t, cons, rr, n in pool.imap_unordered(one, files):
            for k in S: merge(S[k], s[k])
            for k, c in vc.items(): VC[k].update(c)
            for k, v in t.items(): T[k] = [a + b for a, b in zip(T[k], v)]
            for k, v in cons.items(): CONS[k] = [a + b for a, b in zip(CONS[k], v)]
            recs += rr; raw += n
            print("done", len(recs), flush=True)
    out = {"S": {k: {p: [v[0], v[1], sorted(v[2]), v[3]] for p, v in s.items()} for k, s in S.items()},
           "VC": {k: [[str(a), b] for a, b in c.most_common()] for k, c in VC.items()},
           "T": dict(T), "CONS": dict(CONS), "recs": recs, "raw_bytes": raw, "files": len(files)}
    json.dump(out, open("data/espn_live_survey.json", "w"))
    print("SURVEY DONE", raw)
