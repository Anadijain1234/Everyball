"""Use the trained Everyball model on a normal CPU.

  python3 scripts/model/everyball_model.py duel "Gavaskar" "Bumrah" --fmt Test --year 1985
      -> what the model expects if that batter faced that bowler (never happened = an estimate, with a range)

Files: data/model/everyball_model.pt (from Kaggle Output) + data/model_export/ (from export_balls.py).
In Python: m = Everyball(); m.duel(...); m.score_overs(...) (used by the rebuild tests).
"""
import argparse, csv, math, os, sys

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from everyball_net import Net, CATS, NCAT, START, OTHER  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BAT_RUNS = np.array([0, 1, 2, 3, 4, 5, 6, 0, 0, 0, 0], dtype=float)


class Everyball:
    def __init__(self, ckpt=os.path.join(ROOT, "data", "model", "everyball_model.pt"),
                 data=os.path.join(ROOT, "data", "model_export"), threads=None):
        if threads:
            torch.set_num_threads(threads)
        ck = torch.load(ckpt, map_location="cpu", weights_only=False)
        self.net = Net(**ck["cfg"]); self.net.load_state_dict(ck["state"]); self.net.eval()
        self.ctx = ck["cfg"]["ctx"]
        self.info = {k: ck.get(k) for k in ("epoch", "val", "train_cfg")}
        z = np.load(os.path.join(data, "everyball_balls.npz"))
        self.z = {k: z[k].astype(np.int64) for k in z.files}
        rows = list(csv.DictReader(open(os.path.join(data, "innings.csv"), encoding="utf-8")))
        self.inn = {k: np.array([int(r[k]) for r in rows]) for k in ("fmt", "year", "ground", "bat_team", "bowl_team", "inn", "split", "flagged")}
        self.inn_id = [(r["match_id"], int(r["inn"])) for r in rows]
        self.players = {int(r["idx"]): (r["player_id"], r["name"]) for r in csv.DictReader(open(os.path.join(data, "players.csv"), encoding="utf-8"))}
        self.idx_of_pid = {int(p): i for i, (p, _) in self.players.items() if p}
        ii = self.z["inn"]
        self.first = np.r_[True, ii[1:] != ii[:-1]]
        self.start_of = np.maximum.accumulate(np.where(self.first, np.arange(len(ii)), 0))
        c = self.z["cat"]
        self.prev = np.r_[START, c[:-1]]; self.prev[self.first] = START
        self.nballs = np.bincount(self.z["bat"], minlength=len(self.players) + 1) + np.bincount(self.z["bowl"], minlength=len(self.players) + 1)

    # ---------------------------------------------------------------- players
    def find(self, name):
        """Player number by (part of) a name or a player_id; the one with most balls if several match."""
        if str(name).isdigit() and int(name) in self.idx_of_pid:
            return self.idx_of_pid[int(name)]
        n = str(name).lower()
        hits = [i for i, (_, nm) in self.players.items() if n in nm.lower()]
        if not hits:
            raise SystemExit(f"no player matching '{name}'")
        return max(hits, key=lambda i: self.nballs[i])

    # ---------------------------------------------------------------- core
    def _feats(self, idx, over=None):
        """idx: [B, L] ball numbers (real balls) -> feature dict of tensors; `over` = dict of overrides [B, L]."""
        z, I, ii = self.z, self.inn, self.z["inn"][idx]
        f = {"bat": z["bat"][idx], "bowl": z["bowl"][idx], "ns": z["ns"][idx], "over": z["over"][idx], "bio": z["bio"][idx],
             "score": z["score"][idx], "wkts": z["wkts"][idx], "prev": self.prev[idx], "fmt": I["fmt"][ii],
             "year": I["year"][ii], "ground": I["ground"][ii], "bt": I["bat_team"][ii], "wt": I["bowl_team"][ii],
             "innn": I["inn"][ii]}
        for k, v in (over or {}).items():
            f[k] = v
        return {k: torch.as_tensor(np.asarray(v, dtype=np.int64)) for k, v in f.items()}

    @torch.no_grad()
    def probs(self, feats):
        return torch.softmax(self.net(feats).float(), -1).numpy()

    # ---------------------------------------------------------------- dream duel
    def duel(self, batter, bowler, fmt="Test", year=None, n=1500, seed=1, bs=128):
        """Put `batter` on strike against `bowler` in n real match situations of that format (each with its real
        history before it) and average the model's chances. year = the era to play it in (default: both careers'
        middle). Returns a dict; 'range' = the middle 80% of situations (how much it depends on the situation)."""
        X, Y = self.find(batter), self.find(bowler)
        fm = 0 if fmt.upper() == "ODI" else 1
        if year is None:
            yrs = [int(np.median(self.inn["year"][self.z["inn"][self.z[k] == p]])) for k, p in (("bat", X), ("bowl", Y))]
            year = int(round(sum(yrs) / 2))
        rng = np.random.default_rng(seed)
        ok = np.flatnonzero((self.inn["fmt"][self.z["inn"]] == fm) & (self.z["cat"] != OTHER) &
                            (np.arange(len(self.z["inn"])) - self.start_of >= 16))
        pick = rng.choice(ok, size=min(n, len(ok)), replace=False)
        L = self.ctx
        P = []
        for s in range(0, len(pick), bs):
            ends = pick[s:s + bs]
            st = np.maximum(self.start_of[ends], ends - L + 1)
            lens = ends - st + 1
            idx = np.minimum(st[:, None] + np.arange(L)[None, :], ends[:, None])   # pad by repeating the last ball
            last = np.zeros_like(idx, dtype=bool); last[np.arange(len(ends)), lens - 1] = True
            bat = np.where(last, X, self.z["bat"][idx]); bowl = np.where(last, Y, self.z["bowl"][idx])
            pr = self.probs(self._feats(idx, {"bat": bat, "bowl": bowl, "year": np.full(idx.shape, year)}))
            P.append(pr[np.arange(len(ends)), lens - 1])
        P = np.concatenate(P)
        q = P / (1 - P[:, CATS.index("wd")])[:, None]           # per ball faced (wides are not faced)
        q[:, CATS.index("wd")] = 0
        rpb, opb = (q * BAT_RUNS).sum(1), q[:, CATS.index("W")]
        R, O = rpb.mean(), opb.mean()
        lo, hi = np.percentile(rpb / np.maximum(opb, 1e-6), [10, 90])
        return {"batter": self.players[X][1], "bowler": self.players[Y][1], "format": "ODI" if fm == 0 else "Test",
                "era": year, "situations": len(P), "average": round(float(R / O), 1), "strike_rate": round(float(100 * R), 1),
                "balls_per_dismissal": round(1 / O, 0), "dot_pct": round(float(100 * q[:, 0].mean()), 1),
                "boundary_pct": round(float(100 * (q[:, 4] + q[:, 6]).mean()), 1), "range_average_10_90": (round(float(lo), 1), round(float(hi), 1))}

    # ---------------------------------------------------------------- rebuild scoring
    @torch.no_grad()
    def score_overs(self, upto, cands):
        """upto = index of the over's first real ball (history = the real innings before it). cands = list of
        candidate overs, each a list of balls dict(bat, ns, bowl, cat, runs, wk, legal). Returns log-chance of
        each candidate (higher = more believable)."""
        L, k = self.ctx, len(cands[0])
        h = max(self.start_of[upto], upto - (L - k))
        hist = np.arange(h, upto)
        B = len(cands)
        idx = np.tile(np.r_[hist, np.full(k, upto)], (B, 1))
        f = self._feats(idx)
        nh = len(hist)
        s0 = int(self.z["score"][upto]); w0 = int(self.z["wkts"][upto])
        pv0 = int(self.prev[upto])
        for b, cd in enumerate(cands):
            sc, wk, pv = s0, w0, pv0
            for j, d in enumerate(cd):
                p = nh + j
                for key in ("bat", "ns", "bowl"):
                    f[key][b, p] = d[key]
                f["bio"][b, p] = j + 1; f["score"][b, p] = sc; f["wkts"][b, p] = wk; f["prev"][b, p] = pv
                sc += d["runs"]; wk += d["wk"]; pv = d["cat"]
        lp = torch.log_softmax(self.net(f).float(), -1).numpy()
        cat = np.array([[d["cat"] for d in cd] for cd in cands])
        return lp[np.arange(B)[:, None], nh + np.arange(k)[None, :], cat].sum(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["duel", "info"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--fmt", default="Test")
    ap.add_argument("--year", type=int)
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--ckpt", default=os.path.join(ROOT, "data", "model", "everyball_model.pt"))
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "model_export"))
    a = ap.parse_args()
    m = Everyball(a.ckpt, a.data)
    if a.job == "info":
        print(m.info)
    else:
        r = m.duel(a.names[0], a.names[1], a.fmt, a.year, a.n)
        print(f"{r['batter']} v {r['bowler']} ({r['format']}, played as if in {r['era']}), over {r['situations']} real situations:")
        print(f"  average {r['average']} (middle 80% of situations: {r['range_average_10_90'][0]}-{r['range_average_10_90'][1]}), "
              f"strike rate {r['strike_rate']}, out every {int(r['balls_per_dismissal'])} balls, dots {r['dot_pct']}%, "
              f"4s+6s {r['boundary_pct']}% of balls")
        print("  (they never met: this is the model's estimate, not a record)")


if __name__ == "__main__":
    main()
