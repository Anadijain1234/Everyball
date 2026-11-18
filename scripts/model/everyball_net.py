"""The Everyball network (PyTorch). Same file is pasted into the Kaggle notebook and used on CPU by everyball_model.py.

One ball = the sum of learned vectors for: the batter (his batting profile), the bowler (bowling profile), the batter
at the other end, Test/ODI, era (5-year step), ground, batting team, bowling team, innings 1-4, over number, ball of
the over, score, wickets down, and the previous ball's outcome. A causal transformer reads the innings so far (up to
CTX balls) and gives, for every ball, the chance of each outcome: 0 1 2 3 4 5 6 W wd nb bye.
"""
import math

import torch
import torch.nn as nn

CATS = ["0", "1", "2", "3", "4", "5", "6", "W", "wd", "nb", "bye"]
NCAT, OTHER, START = 11, 11, 12          # target 11 = not learned; prev-outcome 12 = first ball of the window/innings
YEAR0 = 1875


def clamp_feats(b):
    """b: dict of int tensors [B, L] -> safe indices for the embedding tables."""
    return {
        "bat": b["bat"], "bowl": b["bowl"], "ns": b["ns"],
        "fmt": b["fmt"].clamp(0, 1), "era": ((b["year"] - YEAR0) // 5).clamp(0, 39),
        "ground": b["ground"], "bt": b["bt"], "wt": b["wt"], "innn": b["innn"].clamp(0, 4),
        "over": b["over"].clamp(0, 199), "bio": b["bio"].clamp(0, 12),
        "score": (b["score"] // 10).clamp(0, 99), "wkts": b["wkts"].clamp(0, 10), "prev": b["prev"].clamp(0, 12),
    }


class Net(nn.Module):
    def __init__(self, n_players, n_grounds, n_teams, d=256, layers=6, heads=8, ctx=256, drop=0.1):
        super().__init__()
        self.cfg = dict(n_players=n_players, n_grounds=n_grounds, n_teams=n_teams, d=d, layers=layers, heads=heads,
                        ctx=ctx, drop=drop)
        E = lambda n: nn.Embedding(n, d)
        self.bat, self.bowl = E(n_players + 1), E(n_players + 1)      # batting and bowling profile of every player
        self.ns = nn.Linear(d, d, bias=False)                         # the other batter, from his batting profile
        self.fmt, self.era, self.ground, self.team = E(2), E(40), E(n_grounds + 1), E(n_teams + 1)
        self.bt, self.wt = nn.Linear(d, d, bias=False), nn.Linear(d, d, bias=False)
        self.innn, self.over, self.bio, self.score, self.wkts, self.prev = E(5), E(200), E(13), E(100), E(11), E(13)
        self.pos = E(ctx)
        self.norm_in = nn.LayerNorm(d)
        layer = nn.TransformerEncoderLayer(d, heads, 4 * d, drop, batch_first=True, norm_first=True, activation="gelu")
        self.enc = nn.TransformerEncoder(layer, layers)
        self.norm_out = nn.LayerNorm(d)
        self.head = nn.Linear(d, NCAT)
        self.register_buffer("mask", torch.triu(torch.full((ctx, ctx), float("-inf")), 1), persistent=False)

    def embed(self, f):
        x = (self.bat(f["bat"]) + self.ns(self.bat(f["ns"])) + self.bowl(f["bowl"]) + self.fmt(f["fmt"]) +
             self.era(f["era"]) + self.ground(f["ground"]) + self.bt(self.team(f["bt"])) + self.wt(self.team(f["wt"])) +
             self.innn(f["innn"]) + self.over(f["over"]) + self.bio(f["bio"]) + self.score(f["score"]) +
             self.wkts(f["wkts"]) + self.prev(f["prev"]))
        L = x.shape[1]
        return self.norm_in(x + self.pos.weight[:L].unsqueeze(0))

    def forward(self, b):
        f = clamp_feats(b)
        x = self.embed(f)
        L = x.shape[1]
        h = self.enc(x, mask=self.mask[:L, :L], is_causal=True)
        return self.head(self.norm_out(h))                            # [B, L, 11] logits


def n_params(m):
    return sum(p.numel() for p in m.parameters())
