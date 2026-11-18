"""Ball model: what usually happens on each ball of an over, learned from every ball we have.

The solver's layer 1 decides WHAT happened in a part of an over (how many dots, singles, fours ... and who faced
them). Many orders of those balls obey every Law and every number (see count_versions.py). Before this model the
solver took the FIRST valid order it found (dots first), which is worse than a random pick. Now it takes the MOST
LIKELY valid order:

    P(ball outcome | format, phase of the innings, ball number in the over, previous ball's outcome)

learned by counting 6.5 million real balls, with smoothing (a rare situation leans on the wider average:
prev+position -> position -> format). Outcomes: 0 1 2 3 4 5 6, W (wicket), wd, nb, bye (byes / leg byes).

Train:  python3 scripts/solver/ball_model.py [--db data/everyball.db]   -> data/model/ball_order.json (small)
The solver loads that file by itself (layered_solver.order_part_ml); no file = old behaviour.
"""
import argparse, collections, json, math, os, sqlite3, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ROOT, "data", "model", "ball_order.json")
CATS = ["0", "1", "2", "3", "4", "5", "6", "W", "wd", "nb", "bye"]
MAXPOS = 7                                    # ball 7, 8, ... of an over share one slot

CAT_SQL = """CASE WHEN b.wicket = 1 THEN 'W'
                  WHEN b.extra_type = 'wd' THEN 'wd'
                  WHEN b.extra_type = 'nb' THEN 'nb'
                  WHEN b.extra_type IN ('b', 'lb') THEN 'bye'
                  WHEN b.extra_type IS NULL OR b.extra_type = '' THEN CAST(MIN(MAX(COALESCE(b.bat_runs, 0), 0), 6) AS TEXT)
                  ELSE NULL END"""


def phase(fmt, over0):
    """over0 = 0-based over of the innings."""
    if fmt == "ODI":
        return 0 if over0 < 10 else (1 if over0 < 40 else 2)
    return 0 if over0 < 20 else (1 if over0 < 80 else 2)


def count(c, where="", args=()):
    q = f"""
      WITH x AS (
        SELECT m.format AS fmt, b.match_id, b.inn, b.over_no, b.seq, {CAT_SQL} AS cat
        FROM balls b JOIN matches m USING (match_id)
        WHERE b.source = m.ball_source {where})
      SELECT fmt, over_no, pos, prev, cat, COUNT(*) FROM (
        SELECT fmt, over_no, cat,
               ROW_NUMBER() OVER w AS pos,
               LAG(cat, 1, 'S') OVER w AS prev
        FROM x WINDOW w AS (PARTITION BY match_id, inn, over_no ORDER BY seq))
      WHERE cat IS NOT NULL AND prev IS NOT NULL
      GROUP BY 1, 2, 3, 4, 5"""
    t = collections.Counter()
    for fmt, ov, pos, prev, cat, n in c.execute(q, args):
        t[(fmt, phase(fmt, (ov or 1) - 1), min(pos, MAXPOS), prev, cat)] += n
    return t


def train(db, out=PATH):
    t0 = time.time()
    c = sqlite3.connect(db)
    print("  counting every ball (one pass, ~1-3 min) ...", flush=True)
    t = count(c)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"cats": CATS, "maxpos": MAXPOS, "counts": [[*k, v] for k, v in t.items()]}, open(out, "w"))
    tot = sum(t.values())
    print(f"  model saved: {out} ({len(t)} cells from {tot:,} balls) in {int(time.time() - t0)} s", flush=True)
    return t


class OrderModel:
    """log P(cat | fmt, phase, pos, prev), smoothed: prev+pos+phase -> pos+phase -> phase -> format."""
    A1, A2, A3 = 50.0, 200.0, 500.0

    def __init__(self, counts):
        self.n = collections.defaultdict(collections.Counter)           # context -> Counter(cat)
        for (fmt, ph, pos, prev, cat), v in counts.items():
            for ctx in ((fmt, ph, pos, prev), (fmt, ph, pos), (fmt, ph), (fmt,)):
                self.n[ctx][cat] += v
        self.cache = {}

    @classmethod
    def load(cls, path=PATH, minus=None):
        if not os.path.exists(path):
            return None
        d = json.load(open(path))
        t = collections.Counter({tuple(r[:5]): r[5] for r in d["counts"]})
        if minus:                                                       # leave one match out (honest tests)
            t.subtract(minus)
            t = +t
        return cls(t)

    def _p(self, ctx, cat):
        c = self.n.get(ctx)
        if len(ctx) == 1:
            tot = sum(c.values()) if c else 0
            return ((c[cat] if c else 0) + 1) / (tot + len(CATS))
        back = self._p(ctx[:-1], cat)
        a = {4: self.A1, 3: self.A2, 2: self.A3}[len(ctx)]
        if not c:
            return back
        return (c[cat] + a * back) / (sum(c.values()) + a)

    def logp(self, fmt, ph, pos, prev, cat):
        k = (fmt, ph, min(pos, MAXPOS), prev, cat)
        v = self.cache.get(k)
        if v is None:
            v = self.cache[k] = math.log(self._p(k[:4], cat))
        return v


def cat_of(d, wicket=False):
    """A solver kind dict (from layered_solver.kinds) -> outcome category."""
    if wicket:
        return "W"
    k = d["key"][0]
    if k == "wd":
        return "wd"
    if k in ("nb", "nbb", "nblb"):
        return "nb"
    if k in ("b", "lb"):
        return "bye"
    return str(min(int(d["key"][1] or 0), 6))


def match_counts(c, mid):
    return count(c, "AND b.match_id = ?", (mid,))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "everyball.db"))
    ap.add_argument("--out", default=PATH)
    a = ap.parse_args()
    train(a.db, a.out)
