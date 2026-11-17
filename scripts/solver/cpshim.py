"""TEST-ONLY stand-in for ortools.sat.python.cp_model, solved with SciPy's MILP (HiGHS).

Only used when OR-Tools is not installed (it is in Codespaces: pip install ortools). It supports exactly the
subset of the CP-SAT API that rebuild.py uses, so the same model code can be checked on small innings.
It is much slower than CP-SAT - never use it for real runs.
"""
import itertools
import numpy as np

UNKNOWN, MODEL_INVALID, FEASIBLE, INFEASIBLE, OPTIMAL = 0, 1, 2, 3, 4
_NAMES = {0: "UNKNOWN", 1: "MODEL_INVALID", 2: "FEASIBLE", 3: "INFEASIBLE", 4: "OPTIMAL"}


class Lin:
    __slots__ = ("t", "c")

    def __init__(self, t=None, c=0):
        self.t, self.c = dict(t or {}), c

    @staticmethod
    def of(x):
        if isinstance(x, Lin):
            return x
        if isinstance(x, Var):
            return Lin({x.i: 1})
        if isinstance(x, Not):
            return Lin({x.v.i: -1}, 1)
        return Lin({}, x)

    def __add__(self, o):
        o = Lin.of(o); t = dict(self.t)
        for k, v in o.t.items():
            t[k] = t.get(k, 0) + v
        return Lin(t, self.c + o.c)
    __radd__ = __add__

    def __neg__(self):
        return Lin({k: -v for k, v in self.t.items()}, -self.c)

    def __sub__(self, o):
        return self + (-Lin.of(o))

    def __rsub__(self, o):
        return Lin.of(o) + (-self)

    def __mul__(self, k):
        return Lin({a: b * k for a, b in self.t.items()}, self.c * k)
    __rmul__ = __mul__

    def __eq__(self, o): return Bound(self - o, 0, 0)
    def __le__(self, o): return Bound(self - o, None, 0)
    def __ge__(self, o): return Bound(self - o, 0, None)
    def __lt__(self, o): return Bound(self - o, None, -1)
    def __gt__(self, o): return Bound(self - o, 1, None)
    def __ne__(self, o): return Bound(self - o, None, None, ne=True)
    __hash__ = object.__hash__


class Var(Lin):
    __slots__ = ("i", "lb", "ub", "name", "m")

    def __init__(self, m, i, lb, ub, name):
        self.m, self.i, self.lb, self.ub, self.name = m, i, lb, ub, name
        Lin.__init__(self, {i: 1}, 0)

    def Not(self):
        return Not(self)

    def Index(self):
        return self.i
    __hash__ = object.__hash__


class Not:
    def __init__(self, v):
        self.v = v

    def Not(self):
        return self.v


class Bound:
    def __init__(self, e, lo, hi, ne=False):
        self.e, self.lo, self.hi, self.ne = e, lo, hi, ne


class Con:
    def __init__(self, m, e, lo, hi, ne=False):
        self.m, self.e, self.lo, self.hi, self.ne, self.enf = m, e, lo, hi, ne, []
        m.cons.append(self)

    def OnlyEnforceIf(self, lits):
        if not isinstance(lits, (list, tuple)):
            lits = [lits]
        self.enf += list(lits)
        return self


class CpModel:
    def __init__(self):
        self.vars, self.cons, self.obj = [], [], Lin()

    def NewIntVar(self, lb, ub, name):
        v = Var(self, len(self.vars), lb, ub, name); self.vars.append(v); return v

    def NewBoolVar(self, name):
        return self.NewIntVar(0, 1, name)

    def NewConstant(self, k):
        return self.NewIntVar(k, k, f"c{k}")

    def Add(self, b):
        if isinstance(b, bool):
            if not b:
                raise ValueError("trivially false constraint")
            return Con(self, Lin(), 0, 0)
        return Con(self, b.e, b.lo, b.hi, b.ne)

    def AddExactlyOne(self, lits):
        return Con(self, sum((Lin.of(x) for x in lits), Lin()), 1, 1)

    def AddBoolOr(self, lits):
        return Con(self, sum((Lin.of(x) for x in lits), Lin()), 1, None)

    def AddImplication(self, a, b):
        return Con(self, Lin.of(b) - Lin.of(a), 0, None)

    def Minimize(self, e):
        self.obj = Lin.of(e)

    # ---- helpers for the MILP translation
    def _range(self, e):
        lo = hi = e.c
        for i, k in e.t.items():
            v = self.vars[i]
            lo += min(k * v.lb, k * v.ub); hi += max(k * v.lb, k * v.ub)
        return lo, hi


class _Params:
    max_time_in_seconds = 60.0
    num_workers = 1
    num_search_workers = 1
    random_seed = 0
    log_search_progress = False


class CpSolver:
    def __init__(self):
        self.parameters = _Params(); self.x = None

    def StatusName(self, s=None):
        return _NAMES.get(self._st if s is None else s, "UNKNOWN")

    def Solve(self, m):
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
        rows, lo_l, hi_l = [], [], []
        extra = []                                     # new binaries for != constraints

        def lit_lin(l):
            return Lin.of(l)
        for c in m.cons:
            e = c.e
            relax = Lin()
            for l in c.enf:
                relax = relax + (1 - lit_lin(l))       # 0 when all enforcement literals are true
            if c.ne:
                # e != 0 with a new binary z:  z=0 -> e <= -1 ;  z=1 -> e >= 1   (relaxed when not enforced)
                z = m.NewBoolVar("ne")
                lo, hi = m._range(e)
                M1, M2 = hi + 1, 1 - lo
                rows.append(e - M1 * Lin.of(z) - M1 * relax); lo_l.append(None); hi_l.append(-1)
                rows.append(e - M2 * Lin.of(z) + M2 * relax); lo_l.append(1 - M2); hi_l.append(None)
                continue
            lo, hi = m._range(e)
            if c.hi is not None:
                M = max(0, hi - c.hi)
                rows.append(e - M * relax); lo_l.append(None); hi_l.append(c.hi)
            if c.lo is not None:
                M = max(0, c.lo - lo)
                rows.append(e + M * relax); lo_l.append(c.lo); hi_l.append(None)
        n = len(m.vars)
        A = lil_matrix((len(rows), n))
        lb_v, ub_v = np.array([v.lb for v in m.vars], float), np.array([v.ub for v in m.vars], float)
        L, U = [], []
        for r, (e, lo, hi) in enumerate(zip(rows, lo_l, hi_l)):
            for i, k in e.t.items():
                A[r, i] = k
            L.append(lo - e.c if lo is not None else -np.inf); U.append(hi - e.c if hi is not None else np.inf)
        cvec = np.zeros(n)
        for i, k in m.obj.t.items():
            cvec[i] = k
        res = milp(cvec, constraints=LinearConstraint(A.tocsr(), np.array(L), np.array(U)),
                   integrality=np.ones(n), bounds=Bounds(lb_v, ub_v),
                   options={"time_limit": float(self.parameters.max_time_in_seconds), "disp": False})
        if res.x is None:
            self._st = INFEASIBLE if res.status == 2 else UNKNOWN
            return self._st
        self.x = np.round(res.x).astype(int)
        self._st = OPTIMAL if res.status == 0 else FEASIBLE
        self.obj = float(res.fun)
        return self._st

    def Value(self, e):
        e = Lin.of(e)
        return int(e.c + sum(k * self.x[i] for i, k in e.t.items()))

    def BooleanValue(self, l):
        return bool(self.Value(l))

    def ObjectiveValue(self):
        return self.obj
