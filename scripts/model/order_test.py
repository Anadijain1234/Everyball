"""Honest test of the trained model on the rebuild job (run on CPU after Kaggle training).

For real overs from matches the model NEVER saw (test split), the true balls and who faced them are given; only the
ORDER is hidden. Every valid order is listed; each method picks one; we count balls right:
  old rule (the solver today) | random valid order | model: most believable order | model: most balls right
  python3 scripts/model/order_test.py [--overs 1500] [--ckpt ...] [--data ...]
The model is switched on in the solver only if it beats the old rule here.
"""
import argparse, collections, math, os, random, sys, time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from everyball_model import Everyball, ROOT  # noqa: E402

RANK = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 10: 7, 9: 8, 8: 9}      # the old rule's order of kinds (dots first)


def overs_of(m, split=2):
    z = m.z
    ii, ov = z["inn"], z["over"]
    key = ii * 1000 + ov
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(key)]
    for k in range(len(starts) - 1):
        s, e, nx = starts[k], ends[k], starts[k + 1]
        i = ii[s]
        if m.inn["split"][i] != split or m.inn["flagged"][i] or ii[nx] != i or ov[nx] != ov[s] + 1:
            continue
        yield i, s, e, nx


def all_orders(items, first, ends_legal=True, cap=5000):
    rem = {X: collections.Counter(v) for X, v in items.items()}
    total = sum(len(v) for v in items.values())
    out, res = [], []

    def dfs(X):
        if len(res) >= cap:
            return
        if len(out) == total:
            if not (ends_legal and not out[-1][1][2]):
                res.append(tuple(out))
            return
        for it in list(rem[X]):
            if rem[X][it] == 0:
                continue
            rem[X][it] -= 1; out.append((X, it))
            dfs(("B" if X == "A" else "A") if it[3] else X)
            out.pop(); rem[X][it] += 1
    dfs(first)
    return res


def old_rule(items, first):
    rem = {X: collections.Counter(v) for X, v in items.items()}
    total = sum(len(v) for v in items.values())
    out = []

    def dfs(X):
        if len(out) == total:
            return out[-1][1][2]
        for it in sorted(rem[X], key=lambda t: (t[3], -t[2], RANK.get(t[0], 9))):
            if rem[X][it] == 0:
                continue
            rem[X][it] -= 1; out.append((X, it))
            if dfs(("B" if X == "A" else "A") if it[3] else X):
                return True
            out.pop(); rem[X][it] += 1
        return False
    return tuple(out) if dfs(first) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overs", type=int, default=1500, help="test overs per format")
    ap.add_argument("--ckpt", default=os.path.join(ROOT, "data", "model", "everyball_model.pt"))
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "model_export"))
    a = ap.parse_args()
    t0 = time.time()
    m = Everyball(a.ckpt, a.data)
    z = m.z
    rng = random.Random(5)
    pool = collections.defaultdict(list)
    for i, s, e, nx in overs_of(m):
        if e - s > 9 or z["cat"][s:e].max() >= 7 and (z["cat"][s:e] == 7).any() or (z["cat"][s:e] == 11).any():
            continue                                     # no wicket overs / unknown codes / very long overs
        pool[int(m.inn["fmt"][i])].append((s, e, nx))
    score = collections.Counter()
    for fm in (0, 1):
        todo = rng.sample(pool[fm], min(a.overs, len(pool[fm])))
        for n, (s, e, nx) in enumerate(todo, 1):
            A, B = z["bat"][s], z["ns"][s]
            if B == A or B == 0:
                continue
            seq = []
            for j in range(s, e):
                nxt = z["bat"][j + 1] if j + 1 < e else z["bat"][nx]
                odd = int(nxt != z["bat"][j]) if j + 1 < e else int(nxt == z["bat"][j])
                X = "A" if z["bat"][j] == A else ("B" if z["bat"][j] == B else None)
                seq.append((X, (int(z["cat"][j]), int(z["runs"][j]), int(z["legal"][j]), odd)))
            if any(X is None for X, _ in seq):
                continue
            items = {X: [it for x, it in seq if x == X] for X in "AB"}
            cands = all_orders(items, "A")
            if tuple(seq) not in set(cands):
                continue
            old = old_rule(items, "A")
            bowl = int(z["bowl"][s])
            pid = {"A": int(A), "B": int(B)}
            balls = [[{"bat": pid[X], "ns": pid["B" if X == "A" else "A"], "bowl": bowl, "cat": it[0], "runs": it[1],
                       "wk": 0, "legal": it[2]} for X, it in c] for c in cands]
            lp = np.concatenate([m.score_overs(s, balls[k:k + 64]) for k in range(0, len(balls), 64)])
            w = np.exp(lp - lp.max()); w /= w.sum()
            marg = collections.defaultdict(float)
            for c, wi in zip(cands, w):
                for j, it in enumerate(c):
                    marg[(j, it)] += wi
            picks = {"old rule (solver today)": old, "random valid order": rng.choice(cands),
                     "model: most believable": cands[int(lp.argmax())],
                     "model: most balls right": max(cands, key=lambda c: sum(marg[(j, it)] for j, it in enumerate(c)))}
            for name, c in picks.items():
                score[(fm, name)] += sum(x == y for x, y in zip(c, seq)); score[(fm, name, "over")] += (list(c) == seq)
            score[(fm, "balls")] += len(seq); score[(fm, "overs")] += 1; score[(fm, "versions")] += len(cands)
            if n % 50 == 0:
                el = int(time.time() - t0)
                print(f"\r  ⏱ {el // 60}:{el % 60:02d} | {'ODI' if fm == 0 else 'Test'} | over {n}/{len(todo)}   ", end="", flush=True)
    print()
    for fm, nm in ((0, "ODI"), (1, "Test")):
        n, b = score[(fm, "overs")], score[(fm, "balls")]
        if not n:
            continue
        print(f"{nm}: {n} test overs (matches never seen), avg {score[(fm, 'versions')] / n:.1f} valid orders per over")
        for name in ("old rule (solver today)", "random valid order", "model: most believable", "model: most balls right"):
            print(f"   {name:25s} balls right {100 * score[(fm, name)] / b:5.1f}%   whole over right {100 * score[(fm, name, 'over')] / n:5.1f}%")


if __name__ == "__main__":
    main()
