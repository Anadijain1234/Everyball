"""
Over-by-over rebuild (plain Python, no solver library).

It walks through the innings like a commentator: over 1, ball 1, ball 2 ... For each delivery it tries the most
likely outcomes first (run patterns of the batter on strike, the bowler, and the phase of the innings, learned
from our real balls), and keeps an outcome only if the REST of the innings can still add up exactly:
  each partnership's runs, balls and each batter's share; each batter's 4s and 6s; each bowler's balls, runs,
  dots, 4s, 6s, wides, no-balls and maidens; the extras by type; each wicket on its exact ball;
  level 2+: each over's bowler and runs; level 3a: each four and six in its over by its batter; 3b: on its ball.
If an over leads to a dead end it steps back and tries the next most likely way (and remembers dead ends so it
never tries the same one twice). Level 1: it also chooses who bowled each over (never two in a row, each bowler's
overs = his figures, the bowler named in "b X" bowls the wicket ball; spells preferred).
Cricket rules: as in split_solver.py (wides 1 + runs run, no-balls 1 + runs, crossing on odd runs, ends change
after every over, bowled / lbw / caught only off a legal ball, run out at either end, caught: new batter faces
from Oct 2022, before that the batters may have crossed).
"""
import collections, math, sys, time

import cricket_facts as F
import split_solver as SS

LEGAL_ONLY = SS.LEGAL_ONLY
STRIKER_ONLY = SS.STRIKER_ONLY
STATUS = SS.STATUS
sys.setrecursionlimit(20000)


class Timeout(Exception):
    pass


class OverSolver:
    def __init__(self, f, prior, patterns=None, forbid=None):
        self.f = f
        era = self.era = f["era"]
        self.P, self.BB, self.TB = era["pen_plus"], era["nb_byes_are_byes"], era["to_bowler"]
        bpo = self.bpo = f["bpo"]
        lt = self.lt = f["legal_total"]
        self.NO = max(1, -(-lt // bpo))
        self.Lo = [min(bpo, lt - o * bpo) for o in range(self.NO)]
        self.pieces = SS.make_pieces(f)
        self.given = {ov["over"]: ov for ov in (f.get("overs") or [])} if not f.get("overs_planned") else {}
        self.bowlers = [b["pid"] for b in f["bowlers"]]
        self.bf = {b["pid"]: b for b in f["bowlers"]}
        self.bat = {b["pid"]: b for b in f["batters"]}
        ex = f.get("extras") or {}
        self.ex = {k: ex.get(k) for k in ("b", "lb", "wd", "nb")}
        self.ev = SS._events(f)
        self.anch = {(a["over"], a["ball"]): a for a in (f.get("anchors") or [])}
        self.forbid = forbid or {}            # (over, ball) -> a delivery dict this ball must NOT equal (proven check)
        # costs: phase, batter, bowler patterns
        pat = patterns or {}
        self.pall = pat.get("all") or {}
        self.pbat, self.pbowl = pat.get("bat") or {}, pat.get("bowl") or {}
        self.prior = prior
        # remaining balls / runs of each batter after each partnership (for look-ahead)
        n = len(self.pieces)
        self.after = [collections.Counter() for _ in range(n + 1)]
        self.after_r = [collections.Counter() for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            p = self.pieces[i]
            self.after[i] = self.after[i + 1].copy(); self.after_r[i] = self.after_r[i + 1].copy()
            for pid, v in p["balls_by"].items():
                self.after[i][pid] += v
            for pid, v in p["runs_by"].items():
                self.after_r[i][pid] += v
        # level 2+: bowler's legal balls and over runs still to come, per over boundary
        self.rem_legal_b = None
        if self.given:
            for o in range(self.NO):
                if self.given.get(o, {}).get("bowler") is None:
                    raise ValueError(f"over {o + 1}: bowler not given")
        self.nodes, self.backtracks = 0, 0
        self._oc, self._can_memo = {}, {}
        self.failed = set()
        self.deadline = None

    # ------------------------------------------------------------------ likelihood of an outcome
    def cost(self, o, striker, bowler, x):
        ph = 0 if o < 10 else (2 if o >= 40 else 1)
        pr = self.prior.get(ph) or self.prior.get(str(ph)) or {}
        g = lambda d, k: d.get(k, d.get(str(k), 1e-4))
        c = -math.log(max(g(pr, x), 1e-6))
        if self.pall:
            for tab, pid in ((self.pbat, striker), (self.pbowl, bowler)):
                t = tab.get(pid)
                if t:
                    c -= math.log(t[x]) - math.log(self.pall[x])
        return c

    # ------------------------------------------------------------------ the search
    def solve(self, seconds=600):
        self.deadline = time.time() + seconds if seconds and seconds > 0 else None
        f = self.f
        st = self.st = {
            "pos": 0, "k": 0, "o": 0, "orun": 0, "oconc": 0, "pi": 0,
            "A": self.pieces[0]["A"], "B": self.pieces[0]["B"], "sA": True,
            "pr": collections.Counter(), "pb": collections.Counter(), "pbl": collections.Counter(), "prun": 0, "pnb": 0,
            "f4": collections.Counter(), "f6": collections.Counter(),
            "b": {b: collections.Counter() for b in self.bowlers},
            "x": collections.Counter(), "evc": collections.Counter(), "bowler_of": {}, "legal_b": collections.Counter()}
        self.out = []
        STATUS.update(stage="searching over by over", done=0, total=self.lt, found=0, extra="")
        firsts = [True, False]
        for s0 in firsts:                                            # who faced the first ball
            st["sA"] = s0
            try:
                if self._start_over():
                    return self.out
            except Timeout:
                return None
        return None

    def _bowler_choices(self):
        st, o = self.st, self.st["o"]
        if self.given:
            return [self.given[o]["bowler"]]
        prev = st["bowler_of"].get(o - 1)
        out = []
        for b in self.bowlers:
            fb = self.bf[b]
            if b == prev:
                continue
            if fb.get("balls") is not None and st["legal_b"][b] + self.Lo[o] > fb["balls"]:
                continue
            out.append(b)
        # spells first: the bowler of two overs ago, then whoever has most balls left
        two = st["bowler_of"].get(o - 2)
        out.sort(key=lambda b: (b != two, -((self.bf[b].get("balls") or 0) - st["legal_b"][b])))
        return out

    def _start_over(self):
        st = self.st
        if st["pos"] >= self.lt and self._innings_done():
            return self._final_check()
        o = st["o"]
        key = self._key()
        if key in self.failed:
            return False
        if self.given and not self._partnership_can_finish():
            self.failed.add(key)
            return False
        for b in self._bowler_choices():
            st["bowler_of"][o] = b
            if not self._bowler_ok(b, o):
                continue
            if self._ball():
                return True
        st["bowler_of"].pop(o, None)
        self.failed.add(key)
        return False

    def _innings_done(self):
        st = self.st
        p = self.pieces[st["pi"]]
        return st["pi"] == len(self.pieces) - 1 and p["wicket"] is None and st["prun"] == p["runs"]

    def _key(self):
        st = self.st
        return (st["o"], st["pi"], st["sA"], st["prun"], st["pnb"], tuple(sorted(st["pr"].items())),
                tuple(sorted(st["pb"].items())), tuple(sorted(st["f4"].items())), tuple(sorted(st["f6"].items())),
                tuple((b, tuple(sorted(c.items()))) for b, c in sorted(st["b"].items())),
                tuple(sorted(st["x"].items())), st["bowler_of"].get(st["o"] - 1), st["bowler_of"].get(st["o"] - 2),
                tuple(sorted(st["legal_b"].items())))

    def _bowler_ok(self, b, o):
        """level 1: the bowler of this over must still fit his figures (balls)"""
        fb = self.bf[b]
        return fb.get("balls") is None or self.st["legal_b"][b] + self.Lo[o] <= fb["balls"]

    # one delivery
    def _ball(self):
        st = self.st
        self.nodes += 1
        if self.deadline and self.nodes % 2000 == 0 and time.time() > self.deadline:
            raise Timeout()
        o = st["o"]
        p = self.pieces[st["pi"]]
        striker = st["A"] if st["sA"] else st["B"]
        bowler = st["bowler_of"][o]
        q = st["k"] + 1                                               # legal ball number if this one is legal
        if self.nodes % 200 == 0:
            STATUS["extra"] = f"now at {o}.{q} | {self.nodes:,} deliveries tried, {self.backtracks:,} step-backs"
            STATUS["done"] = st["pos"]
        w = p["wicket"]
        wicket_now = w is not None and st["pos"] + 1 == p["end"]     # the next legal ball is the wicket ball
        for d in self._options(o, q, striker, bowler, p, wicket_now):
            if self._apply_and_go(d):
                return True
        self.backtracks += 1
        return False

    def _options(self, o, q, striker, bowler, p, wicket_now):
        """possible deliveries here, most likely first"""
        st, P, BB = self.st, self.P, self.BB
        opts = []
        an = self.anch.get((o, q))
        fb = self.bf[bowler]
        bstat = st["b"][bowler]
        w = p["wicket"]
        # legal balls
        for x in range(7):
            if an is not None and (an["bat"] != x or (an.get("striker") and an["striker"] != striker)):
                continue
            if an is None and self.anch and x in (4, 6) and self.f.get("anchors") is not None:
                continue                                              # 3b: every four / six is a given ball
            opts.append((self.cost(o, striker, bowler, x), {"kind": "", "bat": x, "rx": 0, "legal": True}))
        if an is None:
            for r in (1, 2, 3, 4):                                    # byes / leg-byes
                for k in ("b", "lb"):
                    if (self.ex.get(k) or 0) - st["x"][k] >= r:
                        opts.append((6.0 + r, {"kind": k, "bat": 0, "rx": r, "legal": True}))
        # wides / no-balls (only if the bowler still has some)
        if fb.get("wd") is None or bstat["wd"] < fb["wd"]:
            for r in range(0, 5):
                opts.append((7.0 + r, {"kind": "wd", "bat": 0, "rx": r, "legal": False}))
        if fb.get("nb") is None or bstat["nb"] < fb["nb"]:
            for x in range(7):
                opts.append((7.5 + x * 0.1, {"kind": "nb", "bat": x, "rx": 0, "legal": False}))
        opts.sort(key=lambda t: t[0])
        res = []
        for _, d in opts:
            d = dict(d)
            if wicket_now and d["legal"]:
                # the wicket ball (legal): legal-only dismissals happen here; run outs too
                how = p["how"]
                if how in LEGAL_ONLY or how in ("stumped", "hit wicket", "handled the ball", "hit the ball twice"):
                    if d["bat"] != 0 or d["rx"] != 0:
                        continue
                if how not in LEGAL_ONLY and how not in ("stumped", "hit wicket", "handled the ball", "hit the ball twice"):
                    if d["bat"] in (4, 6) or d["rx"] == 4:
                        continue
                if how in STRIKER_ONLY and striker != w["out"]:
                    continue
                if how in F.CREDITED and w.get("bowler") is not None and bowler != w["bowler"]:
                    continue
                d["wicket"] = True
                res.append(d)
                # a run out may also happen on a later extra (then this legal ball is not the wicket)
                if how not in STRIKER_ONLY:
                    d2 = dict(d); d2["wicket"] = False; d2["wk_after"] = True
                    res.append(d2)
            else:
                d["wicket"] = False
                res.append(d)
        # a run out / stumping off a wide or no-ball after the last legal ball of the partnership
        if w is not None and st["pos"] == p["end"] and p["how"] not in LEGAL_ONLY:
            res = [dict(d, wicket=True) for d in res if not d["legal"] and not (d["kind"] == "nb" and p["how"] in ("stumped", "hit wicket"))
                   and not (p["how"] in ("stumped", "hit wicket") and d["rx"])]
            res = [d for d in res if not (p["how"] in STRIKER_ONLY and striker != w["out"])]
        return res

    def _apply_and_go(self, d):
        st, P, BB, TB = self.st, self.P, self.BB, self.TB
        o = st["o"]
        p = self.pieces[st["pi"]]
        striker = st["A"] if st["sA"] else st["B"]
        non = st["B"] if st["sA"] else st["A"]
        bowler = st["bowler_of"][o]
        kind, x, r = d["kind"], d["bat"], d["rx"]
        exw = (1 + r if P else (r if r else 1)) if kind == "wd" else 0
        exnb = (1 + (0 if BB else r) if P else (r if r else (1 if x == 0 else 0))) if kind == "nb" else 0
        exb = r if kind == "b" else 0
        exlb = r if kind == "lb" else 0
        tr = x + exw + exnb + exb + exlb
        conc = x + ((exw + exnb) if TB else 0)
        legal = d["legal"]
        q = st["k"] + 1
        if legal and (o, q) in self.forbid:
            fd = self.forbid[(o, q)]
            if fd["bat"] == x and fd["kind"] == kind and fd["ran"] == r and fd["striker"] == striker \
                    and bool(fd["out"]) == bool(d.get("wicket")):
                return False
        snap = self._snap()
        # apply
        st["pr"][striker] += x
        if kind != "wd":
            st["pb"][striker] += 1
        if legal:
            st["pbl"][striker] += 1
        if kind == "nb":
            st["pnb"] += 1
        st["prun"] += tr
        st["f4"][striker] += x == 4; st["f6"][striker] += x == 6
        bs = st["b"][bowler]
        bs["conc"] += conc; bs["dots"] += legal and conc == 0; bs["4"] += x == 4; bs["6"] += x == 6
        bs["wd"] += kind == "wd"; bs["nb"] += kind == "nb"
        st["x"]["wd"] += exw; st["x"]["nb"] += exnb; st["x"]["b"] += exb; st["x"]["lb"] += exlb
        st["orun"] += tr; st["oconc"] += conc
        if x in (4, 6):
            st["evc"][(o, striker, x)] += 1
        ball_label = None
        if legal:
            st["pos"] += 1; st["k"] += 1; st["legal_b"][bowler] += 1
            ball_label = st["k"]
        rec = {"over": o, "bowler": bowler, "striker": striker, "non_striker": non, "bat": x, "kind": kind,
               "ran": r, "ex_w": exw, "ex_nb": exnb, "ex_b": exb, "ex_lb": exlb, "legal": legal, "out": None,
               "how": None, "label": "guessed", "ball": ball_label}
        self.out.append(rec)
        ok = self._feasible()
        if ok:
            ran = x + r if kind != "wd" else r
            boundary = x in (4, 6) or r == 4
            crossed = (ran % 2 == 1) and not boundary
            over_ends = legal and st["k"] == self.Lo[o]
            if d.get("wicket"):
                rec["out"], rec["how"] = p["wicket"]["out"], p["how"]
                ok = self._after_wicket(striker, non, crossed, over_ends)
            else:
                if crossed:
                    st["sA"] = not st["sA"]
                if over_ends:
                    ok = self._end_over()
                else:
                    ok = self._ball() if not (st["pos"] >= self.lt and self._innings_done()) else self._final_check()
        if ok:
            return True
        self.out.pop()
        self._restore(snap)
        return False

    def _after_wicket(self, striker, non, crossed, over_ends):
        st = self.st
        p = self.pieces[st["pi"]]
        w = p["wicket"]
        # the partnership must be exactly complete
        if st["prun"] != p["runs"] or st["pnb"] != p["balls"] - (p["end"] - p["start"]):
            return False
        for pid in (p["A"], p["B"]):
            if st["pr"][pid] != p["runs_by"].get(pid, 0) or st["pb"][pid] != p["balls_by"].get(pid, 0):
                return False
        if st["pi"] + 1 >= len(self.pieces):
            return self._final_check() if st["pos"] >= self.lt or True else False
        nxt = self.pieces[st["pi"] + 1]
        survivor = nxt["A"]
        # where does the new batter go? positions after the runs: (striker end, non-striker end)
        at_str, at_non = (non, striker) if crossed else (striker, non)
        how = p["how"]
        choices = []
        if how in STRIKER_ONLY and (how not in ("caught", "caught and bowled") or self.era["caught_new_on_strike"]):
            choices = [True]                                          # new batter at the striker's end
        else:
            choices = [True, False]
        snap = self._snap()
        for inc_at_striker in choices:
            self._restore(snap)
            st["pi"] += 1
            st["A"], st["B"] = nxt["A"], nxt["B"]
            st["pr"], st["pb"], st["prun"], st["pnb"] = collections.Counter(), collections.Counter(), 0, 0
            st["pbl"] = collections.Counter()
            inc_on = inc_at_striker
            if over_ends:
                inc_on = not inc_on
            st["sA"] = not inc_on                                     # A of the next piece = survivor
            if not self._feasible():
                continue
            if over_ends:
                ok = self._end_over()
            else:
                ok = self._ball()
            if ok:
                return True
        self._restore(snap)
        return False

    def _end_over(self):
        st = self.st
        o = st["o"]
        if self.given and self.given[o].get("runs") is not None and st["orun"] != self.given[o]["runs"]:
            return False
        if self.ev is not None:                                       # 3a: this over's fours and sixes, exactly
            for key, n in self.ev.items():
                if key[0] == o and st["evc"][key] != n:
                    return False
            for key, n in st["evc"].items():
                if key[0] == o and self.ev.get(key, 0) != n:
                    return False
        snap = self._snap()
        b = st["bowler_of"][o]
        if self.Lo[o] == self.bpo and st["oconc"] == 0:
            st["b"][b]["maid"] += 1
            fb = self.bf[b]
            if fb.get("maidens") is not None and st["b"][b]["maid"] > fb["maidens"]:
                self._restore(snap)
                return False
        st["sA"] = not st["sA"]                                       # ends change
        st["o"] += 1; st["k"] = 0; st["orun"] = 0; st["oconc"] = 0
        STATUS["done"] = st["pos"]
        if st["pos"] >= self.lt:
            ok = self._final_check() if self._innings_done() or self.pieces[st["pi"]]["wicket"] is None else False
            if not ok:
                ok = self._innings_done() and self._final_check()
        else:
            ok = self._start_over()
        if not ok:
            self._restore(snap)
        return ok

    def _final_check(self):
        st = self.st
        p = self.pieces[st["pi"]]
        if p["wicket"] is None:
            if st["prun"] != p["runs"]:
                return False
            for pid in (p["A"], p["B"]):
                if st["pr"][pid] != p["runs_by"].get(pid, 0) or st["pb"][pid] != p["balls_by"].get(pid, 0):
                    return False
        if st["pos"] != self.lt:
            return False
        for b in self.bowlers:
            fb, bs = self.bf[b], st["b"][b]
            for k, fk in (("conc", "runs"), ("dots", "dots"), ("4", "4"), ("6", "6"), ("wd", "wd"), ("nb", "nb"), ("maid", "maidens")):
                if fb.get(fk) is not None and bs[k] != fb[fk]:
                    return False
            if fb.get("balls") is not None and st["legal_b"][b] != fb["balls"]:
                return False
        for pid, fb in self.bat.items():
            if fb.get("4") is not None and st["f4"][pid] != fb["4"]:
                return False
            if fb.get("6") is not None and st["f6"][pid] != fb["6"]:
                return False
        for k in ("b", "lb", "wd", "nb"):
            if self.ex.get(k) is not None and st["x"][k] != self.ex[k]:
                return False
        if self.ev is not None:
            for key, n in self.ev.items():
                if st["evc"][key] != n:
                    return False
        STATUS["found"] += 1
        return True

    # ------------------------------------------------------------------ exact look-ahead for the partnership
    def _outcomes(self, n, sA):
        """every way n legal balls can go (runs 0-6 each, strike passing on odd runs):
        T (bat runs in all) -> set of (A on strike at the end, A's runs, A's balls)"""
        key = (n, sA)
        if key in self._oc:
            return self._oc[key]
        cur = {(sA, 0, 0, 0)}
        for _ in range(n):
            nxt = set()
            for (s_, ra, ba, t) in cur:
                for v in (0, 1, 2, 3, 4, 5, 6):
                    nxt.add(((not s_) if v % 2 == 1 else s_, ra + (v if s_ else 0), ba + (1 if s_ else 0), t + v))
            cur = nxt
        res = collections.defaultdict(set)
        for (s_, ra, ba, t) in cur:
            res[t].add((s_, ra, ba))
        self._oc[key] = res
        return res

    def _partnership_can_finish(self):
        """At the start of an over: can this partnership still end with EXACTLY each batter's runs and balls,
        given the runs of its remaining overs (level 2+)? Wides / no-balls / byes are allowed anywhere within the
        partnership's own extras (a relaxation: if this says no, it really is impossible)."""
        st = self.st
        pi = st["pi"]
        p = self.pieces[pi]
        if st["pos"] >= p["end"]:
            return True
        A, B = p["A"], p["B"]
        TRA, TRB = p["runs_by"].get(A, 0), p["runs_by"].get(B, 0)
        TBA = p["balls_by"].get(A, 0)
        EXT = p["runs"] - TRA - TRB
        NB = p["balls"] - (p["end"] - p["start"])
        ext = st["prun"] - st["pr"][A] - st["pr"][B]
        return self._can(pi, st["o"], st["sA"], st["pr"][A], st["pbl"][A], ext, st["pr"][A] + st["pr"][B],
                         TRA, TRB, TBA, EXT, NB)

    def _can(self, pi, o, sA, rA, bA, ext, bat, TRA, TRB, TBA, EXT, NB):
        key = (pi, o, sA, rA, bA, ext, bat)
        if key in self._can_memo:
            return self._can_memo[key]
        p = self.pieces[pi]
        bpo = self.bpo
        if rA > TRA or bat > TRA + TRB or ext > EXT or bA > TBA:
            self._can_memo[key] = False
            return False
        lo = o * bpo
        if lo >= p["end"]:
            if NB == 0:
                ok = rA == TRA and bA == TBA and bat == TRA + TRB and ext == EXT
            else:                                                    # no-balls: a little slack (relaxation)
                ok = (TRA - 6 * NB <= rA <= TRA and TBA - NB <= bA <= TBA and TRA + TRB - 6 * NB <= bat <= TRA + TRB
                      and ext <= EXT)
            self._can_memo[key] = ok
            return ok
        n = min(p["end"], lo + bpo) - max(p["start"], lo)
        partial = lo + bpo > p["end"]
        R = self.given[o]["runs"]
        oc = self._outcomes(n, sA)
        ok = False
        for T, outs in oc.items():
            if partial:
                if T > R:
                    continue
                e_choices = range(0, min(EXT - ext, R - T) + 1)
            else:
                e = R - T
                if e < 0 or e > EXT - ext:
                    continue
                e_choices = (e,)
            for e in e_choices:
                for (s_end, dra, dba) in outs:
                    nsA = s_end if partial else (not s_end)
                    if self._can(pi, o + 1, nsA, rA + dra, bA + dba, ext + e, bat + T, TRA, TRB, TBA, EXT, NB):
                        ok = True
                        break
                if ok:
                    break
            if ok:
                break
        self._can_memo[key] = ok
        return ok

    # ------------------------------------------------------------------ look-ahead: can the rest still add up?
    def _feasible(self):
        st = self.st
        p = self.pieces[st["pi"]]
        # partnership
        rem_legal = p["end"] - st["pos"]
        rem_nb = (p["balls"] - (p["end"] - p["start"])) - st["pnb"]
        if rem_legal < 0 or rem_nb < 0:
            return False
        tot_b = 0
        for pid in (p["A"], p["B"]):
            rr = p["runs_by"].get(pid, 0) - st["pr"][pid]
            rb = p["balls_by"].get(pid, 0) - st["pb"][pid]
            if rr < 0 or rb < 0 or rr > 6 * rb:
                return False
            tot_b += rb
        if tot_b != rem_legal + rem_nb:
            return False
        rem_ext = (p["runs"] - st["prun"]) - sum(p["runs_by"].get(pid, 0) - st["pr"][pid] for pid in (p["A"], p["B"]))
        if rem_ext < 0:
            return False
        if rem_ext > sum(max(0, (self.ex.get(k) or 0) - st["x"][k]) for k in ("wd", "nb", "b", "lb")):
            return False
        # batters' 4s and 6s for the rest of the innings
        nxt = st["pi"] + 1
        for pid, fb in self.bat.items():
            r4 = (fb.get("4") or 0) - st["f4"][pid] if fb.get("4") is not None else 0
            r6 = (fb.get("6") or 0) - st["f6"][pid] if fb.get("6") is not None else 0
            if r4 < 0 or r6 < 0:
                return False
            if r4 or r6:
                rb = self.after[nxt][pid] + (p["balls_by"].get(pid, 0) - st["pb"][pid] if pid in (p["A"], p["B"]) else 0)
                rr = self.after_r[nxt][pid] + (p["runs_by"].get(pid, 0) - st["pr"][pid] if pid in (p["A"], p["B"]) else 0)
                if r4 + r6 > rb or 4 * r4 + 6 * r6 > rr:
                    return False
        # extras
        for k in ("b", "lb", "wd", "nb"):
            if self.ex.get(k) is not None and st["x"][k] > self.ex[k]:
                return False
        # bowlers
        for b in self.bowlers:
            fb, bs = self.bf[b], st["b"][b]
            for k, fk in (("conc", "runs"), ("dots", "dots"), ("4", "4"), ("6", "6"), ("wd", "wd"), ("nb", "nb")):
                if fb.get(fk) is not None and bs[k] > fb[fk]:
                    return False
            if self.given and fb.get("balls") is not None:
                left = fb["balls"] - st["legal_b"][b]
                rd = (fb.get("dots") or 0) - bs["dots"] if fb.get("dots") is not None else None
                if rd is not None and rd > left:
                    return False
                if fb.get("runs") is not None and rd is not None:
                    rcon = fb["runs"] - bs["conc"]
                    rwd = (fb.get("wd") or 0) - bs["wd"]; rnb = (fb.get("nb") or 0) - bs["nb"]
                    rbat = rcon - ((rwd + rnb) if (self.TB and self.P) else 0)
                    if left - rd > rbat:                              # every non-dot legal ball costs >= 1 run
                        return False
                    r4 = (fb.get("4") or 0) - bs["4"]; r6 = (fb.get("6") or 0) - bs["6"]
                    if 4 * r4 + 6 * r6 > rbat:
                        return False
        # level 2+: the partnership's remaining runs must fit the remaining runs of its overs
        o = st["o"]
        if self.given:
            last_o = (p["end"] - 1) // self.bpo if p["end"] > st["pos"] else o
            rem_o = self.given[o]["runs"] - st["orun"]
            room = rem_o + sum(self.given[oo]["runs"] for oo in range(o + 1, min(last_o, self.NO - 1) + 1))
            rem_pr = p["runs"] - st["prun"]
            if rem_pr > room:
                return False
            ends_on_over = p["end"] % self.bpo == 0 or p["end"] == self.lt
            if ends_on_over and p["wicket"] is None and rem_pr != room:
                return False
            # each bowler: dots / runs still possible in his remaining overs
            for b in self.bowlers:
                fb, bs = self.bf[b], st["b"][b]
                if fb.get("dots") is None:
                    continue
                need_d = fb["dots"] - bs["dots"]
                min_d, max_r = 0, 0
                for oo in range(o, self.NO):
                    if self.given[oo]["bowler"] != b:
                        continue
                    n_o = self.Lo[oo] - (st["k"] if oo == o else 0)
                    r_o = self.given[oo]["runs"] - (st["orun"] if oo == o else 0)
                    min_d += max(0, n_o - r_o)
                    max_r += r_o
                if need_d < min_d:
                    return False
                if fb.get("runs") is not None and fb["runs"] - bs["conc"] > max_r:
                    return False
        # this over's runs (level 2+)
        if self.given and self.given[o].get("runs") is not None:
            rem = self.given[o]["runs"] - st["orun"]
            if rem < 0:
                return False
            n_left = self.Lo[o] - st["k"]
            b = st["bowler_of"][o]
            fb, bs = self.bf[b], st["b"][b]
            ex_left = sum(max(0, (self.ex.get(k) or 0) - st["x"][k]) for k in ("wd", "nb", "b", "lb"))
            wn_left = ((fb.get("wd") or 0) - bs["wd"]) + ((fb.get("nb") or 0) - bs["nb"])
            extra_cap = min(ex_left, 5 * wn_left + 4 * n_left)
            if rem > 6 * n_left + extra_cap:
                return False
            if n_left == 0 and rem > 0 and wn_left == 0:
                return False
        # level 3a: no more fours / sixes in this over than given
        if self.ev is not None:
            for key, n in st["evc"].items():
                if self.ev.get(key, 0) < n:
                    return False
        return True

    # snapshot / restore of the whole state (small: counters copied)
    def _snap(self):
        st = self.st
        return {k: (v.copy() if isinstance(v, (collections.Counter, dict)) and k != "b" else
                    ({b: c.copy() for b, c in v.items()} if k == "b" else v)) for k, v in st.items()}

    def _restore(self, s):
        for k, v in s.items():
            self.st[k] = (v.copy() if isinstance(v, (collections.Counter, dict)) and k != "b" else
                          ({b: c.copy() for b, c in v.items()} if k == "b" else v))


def solve_innings(f, prior, seconds=600, patterns=None, prove=True, prove_budget=900, log=print):
    t0 = time.time()
    s = OverSolver(f, prior, patterns)
    log(f"  over-by-over search: {len(s.pieces)} partnerships, {s.lt} legal balls")
    dl = s.solve(seconds)
    info = {"status": "FOUND" if dl else ("TIME UP" if s.deadline and time.time() > s.deadline else "NO ANSWER"),
            "seconds": round(time.time() - t0), "deliveries_tried": s.nodes, "step_backs": s.backtracks}
    if not dl:
        return None, info
    dl = [dict(d) for d in dl]
    log(f"  answer found in {info['seconds']} s ({s.nodes:,} deliveries tried, {s.backtracks:,} step-backs)")
    for d in dl:
        if d["legal"] and (d["over"], d["ball"]) in s.anch:
            d["label"] = "real"
    if prove:
        # proven = searching again with this ball forced to be different finds NO complete innings
        tp = time.time()
        todo = [d for d in dl if d["legal"] and d["label"] == "guessed"]
        STATUS.update(stage="proven / guessed check", done=0, total=len(todo), extra="")
        cnt = collections.Counter()
        for d in todo:
            left = prove_budget - (time.time() - tp)
            if left <= 1:
                d["label"] = "unchecked"; cnt["unchecked"] += 1; STATUS["done"] += 1; continue
            alt = OverSolver(f, prior, patterns, forbid={(d["over"], d["ball"]): d})
            try:
                res = alt.solve(min(left, 30))
                timed_out = res is None and alt.deadline and time.time() > alt.deadline
            except Exception:
                res, timed_out = None, True
            d["label"] = "guessed" if res else ("unchecked" if timed_out else "proven")
            cnt[d["label"]] += 1
            STATUS["done"] += 1
            STATUS["extra"] = f"checking {d['over']}.{d['ball']} | proven {cnt['proven']}, guessed {cnt['guessed']}, unchecked {cnt['unchecked']}"
        info["proven"] = cnt["proven"]
        info["prove_seconds"] = round(time.time() - tp)
        log(f"  proven/guessed check: {dict(cnt)} ({info['prove_seconds']} s)")
    info["seconds"] = round(time.time() - t0)
    return dl, info
