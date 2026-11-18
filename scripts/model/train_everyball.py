# %% [markdown]
# # Everyball model - training (Kaggle, GPU)
# 1. Add Data -> your private dataset "everyball-balls" (everyball_balls.npz, innings.csv, players.csv).
# 2. Settings -> Accelerator -> GPU T4 x2 (or P100). Internet can stay off.
# 3. Save Version -> "Save & Run All (Commit)". It runs on its own, even with the browser closed.
# 4. When done: open that version -> Output -> download everyball_model.pt and metrics.json.
# The live progress line is in the version's Logs.

# %%
import glob, json, math, os, time, csv
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as Fn

QUICK = os.environ.get("EVERYBALL_QUICK") == "1"          # tiny CPU check (Claude's test), never on Kaggle
found = glob.glob("/kaggle/input/**/everyball_balls.npz", recursive=True)
DATA = os.path.dirname(found[0]) if found else os.environ.get("EVERYBALL_DATA", "data/model_export")
OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else os.environ.get("EVERYBALL_OUT", "data/model_out")
os.makedirs(OUT, exist_ok=True)
DEV = "cuda" if torch.cuda.is_available() else "cpu"
CFG = dict(d=384, layers=8, heads=8, ctx=256, drop=0.1, batch=128, epochs=16, lr=3e-4, warmup=500, seed=7)
if QUICK:
    CFG.update(d=64, layers=2, heads=4, ctx=64, batch=32, epochs=1, warmup=50)
torch.manual_seed(CFG["seed"]); np.random.seed(CFG["seed"])
print("data:", DATA, "| out:", OUT, "| device:", DEV, torch.cuda.get_device_name(0) if DEV == "cuda" else "", "| config:", CFG)

# %%
# (the network: same code as scripts/model/everyball_net.py)
# NET_CODE_HERE

# %%
t0 = time.time()
z = np.load(os.path.join(DATA, "everyball_balls.npz"))
inn_rows = list(csv.DictReader(open(os.path.join(DATA, "innings.csv"), encoding="utf-8")))
n_players = sum(1 for _ in open(os.path.join(DATA, "players.csv"), encoding="utf-8")) - 1
I = {k: np.array([int(r[k]) for r in inn_rows]) for k in ("fmt", "year", "ground", "bat_team", "bowl_team", "inn", "flagged", "split")}
n_grounds, n_teams = int(I["ground"].max()), int(max(I["bat_team"].max(), I["bowl_team"].max()))
inn = z["inn"].astype(np.int64)
cat = z["cat"].astype(np.int64)
N = len(cat)
first = np.r_[True, inn[1:] != inn[:-1]]
prev = np.r_[START, cat[:-1]]
prev[first] = START
feats = {
    "bat": z["bat"], "bowl": z["bowl"], "ns": z["ns"], "over": z["over"], "bio": z["bio"], "score": z["score"],
    "wkts": z["wkts"], "prev": prev, "fmt": I["fmt"][inn], "year": I["year"][inn], "ground": I["ground"][inn],
    "bt": I["bat_team"][inn], "wt": I["bowl_team"][inn], "innn": I["inn"][inn],
}
G = {k: torch.as_tensor(np.asarray(v, dtype=np.int32)).to(DEV) for k, v in feats.items()}
Y = torch.as_tensor(np.where(cat == OTHER, -100, cat)).to(DEV)
print(f"{N:,} balls, {len(inn_rows):,} innings, {n_players:,} players loaded in {time.time() - t0:.0f} s")

# windows: CTX balls, half-overlapping; each ball is learned exactly once, always with >= CTX/2 balls of context
CTX = CFG["ctx"]; H = CTX // 2
starts = np.flatnonzero(first); ends = np.r_[starts[1:], N]
def windows(keep):
    out = []
    for i, (s, e) in enumerate(zip(starts, ends)):
        if not keep[inn[s]]:
            continue
        w = s
        out.append((w, s, e, 0))
        while w + CTX < e:
            w += H
            out.append((w, s, e, H))
    return np.array(out, dtype=np.int64)
ok = I["flagged"] == 0
W = {name: windows(ok & (I["split"] == sp)) for name, sp in (("train", 0), ("val", 1), ("test", 2))}
if QUICK:                                                  # small random sample of both formats (CPU check)
    W = {k: v[np.random.default_rng(1).choice(len(v), min(len(v), int(os.environ.get("EVERYBALL_WIN", 400))), replace=False)] for k, v in W.items()}
print({k: len(v) for k, v in W.items()}, "windows")
ar = torch.arange(CTX, device=DEV)

def batch(rows):
    rows = torch.as_tensor(rows, device=DEV)
    idx = rows[:, :1] + ar                                     # [B, CTX]
    valid = idx < rows[:, 2:3]
    lossok = valid & (ar >= rows[:, 3:4])                      # skip the first half of a continuing window
    gi = idx.clamp(max=N - 1)
    b = {k: torch.where(valid, v[gi], torch.zeros_like(v[gi])).long() for k, v in G.items()}
    y = torch.where(lossok, Y[gi], torch.full_like(Y[gi], -100))
    return b, y

# %%
model = Net(n_players, n_grounds, n_teams, CFG["d"], CFG["layers"], CFG["heads"], CTX, CFG["drop"]).to(DEV)
print(f"model: {n_params(model) / 1e6:.1f} M numbers to learn")
opt = torch.optim.AdamW(model.parameters(), lr=CFG["lr"], weight_decay=0.01)
steps_ep = math.ceil(len(W["train"]) / CFG["batch"])
total = steps_ep * CFG["epochs"] if not QUICK else int(os.environ.get("EVERYBALL_STEPS", 30))
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1, (s + 1) / CFG["warmup"]) * 0.5 * (1 + math.cos(math.pi * min(1, s / total))))
scaler = torch.amp.GradScaler("cuda", enabled=DEV == "cuda")

# a simple yardstick: how often each outcome happens (by Test/ODI) - the model must beat this
tr = W["train"]
base = np.zeros((2, NCAT))
for f_ in (0, 1):
    m_ = (I["fmt"][inn] == f_) & (cat < NCAT) & np.isin(inn, inn[tr[:, 1]])
    base[f_] = np.bincount(cat[m_], minlength=NCAT)[:NCAT] + 1
base = np.log(base / base.sum(1, keepdims=True))
BASE = torch.as_tensor(base, dtype=torch.float32, device=DEV)

@torch.no_grad()
def evaluate(rows, bs=256):
    model.eval()
    s = {"loss": 0.0, "base": 0.0, "n": 0, "hit": 0, "by_fmt": {0: [0.0, 0.0, 0], 1: [0.0, 0.0, 0]}}
    for i in range(0, len(rows), bs):
        b, y = batch(rows[i:i + bs])
        with torch.autocast(DEV, enabled=DEV == "cuda"):
            lg = model(b)
        lp = torch.log_softmax(lg.float(), -1)
        m = y >= 0
        yy = y.clamp(min=0)
        nll = -lp.gather(-1, yy.unsqueeze(-1)).squeeze(-1)
        bnll = -BASE[b["fmt"], yy]
        s["loss"] += nll[m].sum().item(); s["base"] += bnll[m].sum().item(); s["n"] += m.sum().item()
        s["hit"] += (lp.argmax(-1) == y)[m].sum().item()
        for f_ in (0, 1):
            mf = m & (b["fmt"] == f_)
            s["by_fmt"][f_][0] += nll[mf].sum().item(); s["by_fmt"][f_][1] += bnll[mf].sum().item(); s["by_fmt"][f_][2] += mf.sum().item()
    model.train()
    n = max(1, s["n"])
    return {"loss": s["loss"] / n, "base": s["base"] / n, "acc": s["hit"] / n, "balls": s["n"],
            "ODI": {"loss": s["by_fmt"][0][0] / max(1, s["by_fmt"][0][2]), "base": s["by_fmt"][0][1] / max(1, s["by_fmt"][0][2])},
            "Test": {"loss": s["by_fmt"][1][0] / max(1, s["by_fmt"][1][2]), "base": s["by_fmt"][1][1] / max(1, s["by_fmt"][1][2])}}

# %%
t0, step, best, log = time.time(), 0, float("inf"), []
model.train()
for ep in range(CFG["epochs"]):
    perm = np.random.permutation(len(W["train"]))
    run = 0.0
    for k in range(steps_ep):
        if step >= total:
            break
        b, y = batch(W["train"][perm[k * CFG["batch"]:(k + 1) * CFG["batch"]]])
        with torch.autocast(DEV, enabled=DEV == "cuda"):
            lg = model(b)
            loss = Fn.cross_entropy(lg.float().reshape(-1, NCAT), y.reshape(-1), ignore_index=-100)
        opt.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(opt); nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(opt); scaler.update(); sched.step()
        step += 1; run = 0.98 * run + 0.02 * loss.item() if run else loss.item()
        if step % 50 == 0 or step == total:
            el = time.time() - t0
            eta = el / step * (total - step)
            print(f"\r⏱ {int(el // 60)}:{int(el % 60):02d} | epoch {ep + 1}/{CFG['epochs']} | step {step}/{total} | "
                  f"loss {run:.4f} | about {int(eta // 60)} min left   ", end="", flush=True)
    v = evaluate(W["val"])
    log.append({"epoch": ep + 1, "step": step, "train_loss": run, **{f"val_{k}": v[k] for k in ("loss", "base", "acc")}})
    print(f"\n  epoch {ep + 1}: val loss {v['loss']:.4f} (simple yardstick {v['base']:.4f}) | ODI {v['ODI']['loss']:.4f} "
          f"v {v['ODI']['base']:.4f} | Test {v['Test']['loss']:.4f} v {v['Test']['base']:.4f}", flush=True)
    if v["loss"] < best:
        best = v["loss"]
        torch.save({"state": model.state_dict(), "cfg": model.cfg, "train_cfg": CFG, "epoch": ep + 1, "val": v},
                   os.path.join(OUT, "everyball_model.pt"))
        print("  saved (best so far)", flush=True)
    if step >= total:
        break

# %%
ck = torch.load(os.path.join(OUT, "everyball_model.pt"), map_location=DEV)
model.load_state_dict(ck["state"])
t = evaluate(W["test"])
gain = lambda r: 100 * (1 - math.exp(-(r["base"] - r["loss"])))
res = {"test": t, "best_epoch": ck["epoch"], "minutes": round((time.time() - t0) / 60, 1), "log": log, "cfg": CFG,
       "note": "loss = average surprise per ball (lower is better); base = the simple how-often yardstick"}
json.dump(res, open(os.path.join(OUT, "metrics.json"), "w"), indent=1)
print(f"TEST (matches never seen): loss {t['loss']:.4f} v yardstick {t['base']:.4f} | ODI {t['ODI']['loss']:.4f} v "
      f"{t['ODI']['base']:.4f} | Test {t['Test']['loss']:.4f} v {t['Test']['base']:.4f} | top guess right {100 * t['acc']:.1f}%")
print("DONE. Download everyball_model.pt and metrics.json from Output.")
