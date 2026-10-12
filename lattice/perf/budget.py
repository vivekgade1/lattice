"""Performance budgets: the side file ``perf.budget.json``, its validation, the fit and the
verdict (spike 1a; not spec syntax, not a build gate).

The file::

    {"version": 1,
     "prepare": "go build -o bin/streak-api ./cmd/server",      # optional, run in the project
     "budgets": [{
        "id": "home", "endpoint": "GET /v1/home", "user": "ann",
        "seed": {"items": "N", "steps": [
            {"method": "POST", "path": "/v1/habits", "user": "ann", "count": "N",
             "json": {"id": "{uuid}", "name": "habit-{i}", "period": "week", "target_count": 3}}]},
        "sizes": [5, 25],
        "queries": {"constant": 8, "per_item": 0},
        "latency_ms": null}]}                 # or {"median": 40, "size": 25, "margin": 0.5}

A budget is **statements per request = constant + per_item * N**, N the number of seeded
items. The measurement (``lattice.perf.measure``) seeds a fresh database at each of two or
more sizes through the app's API (the seed's steps, ``count`` "N" or a number; ``{i}`` in a
string is the 1-based index of the step's repetition, ``{uuid}`` a fresh UUID), sends the
endpoint ``runs`` times and counts the app's statements per request. :func:`fit` fits
constant and per-item from the exact counts (smallest and largest size; ``exact`` when every
run of every size lies on that line); :func:`judge` fails the budget when the fitted
constant or per-item exceeds the budget's, or when a size's count exceeds ``constant +
per_item * n`` (a count that is not linear in N). Latency is reported (min, median, max of
the runs) and compared only when ``latency_ms`` is set: the median at ``size`` must be at
most ``median * (1 + margin)``.

The report's JSON (:meth:`Report.to_dict`) is ``{budget_id, endpoint, sizes: [{n,
statements: [per run], latency_ms: {min, median, max}}], fit: {constant, per_item, exact},
budget, verdict: pass|fail|unmeasured, delta, notes}``.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

BUDGET_FILE = "perf.budget.json"
VERSION = 1
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class BudgetError(ValueError):
    """The budget file is missing, not JSON, or not valid (each problem on its own line)."""


@dataclass(frozen=True)
class SeedStep:
    """One seeding request, sent ``count`` times (``"N"``: the size being measured)."""

    method: str
    path: str
    user: str | None = None
    json: Any = None
    count: int | str = "N"

    def times(self, n: int) -> int:
        return n if self.count == "N" else int(self.count)


@dataclass(frozen=True)
class LatencyBudget:
    """The median latency (ms) at ``size`` must be at most ``median * (1 + margin)``."""

    median: float
    size: int
    margin: float

    def limit(self) -> float:
        return self.median * (1 + self.margin)


@dataclass(frozen=True)
class Budget:
    id: str
    method: str
    path: str
    user: str | None
    steps: tuple[SeedStep, ...]
    sizes: tuple[int, ...]
    constant: Fraction
    per_item: Fraction
    latency: LatencyBudget | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.method} {self.path}"

    def allowed(self, n: int) -> Fraction:
        """The statements allowed at size ``n``."""
        return self.constant + self.per_item * n

    def to_dict(self) -> dict[str, Any]:
        lat = self.latency
        return {
            "queries": {"constant": _num(self.constant), "per_item": _num(self.per_item)},
            "latency_ms": None
            if lat is None
            else {"median": lat.median, "size": lat.size, "margin": lat.margin},
        }


@dataclass(frozen=True)
class BudgetFile:
    path: Path | None
    budgets: tuple[Budget, ...]
    prepare: str | None = None


# ---- loading and validation ------------------------------------------------------------


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and v == v  # not NaN


def _frac(v: int | float) -> Fraction:
    return Fraction(v) if isinstance(v, int) else Fraction(str(v))


def _count(v: Any) -> bool:
    return v == "N" or (isinstance(v, int) and not isinstance(v, bool) and v >= 0)


def _endpoint(v: Any) -> tuple[str, str] | None:
    if not isinstance(v, str):
        return None
    method, _, path = v.strip().partition(" ")
    path = path.strip()
    if method.upper() not in METHODS or not path.startswith("/"):
        return None
    return method.upper(), path


def _steps(raw: Any, where: str, problems: list[str]) -> tuple[SeedStep, ...]:
    if not isinstance(raw, list):
        problems.append(f"{where}.steps: a list of seeding requests")
        return ()
    out = []
    for j, s in enumerate(raw):
        at = f"{where}.steps[{j}]"
        if not isinstance(s, dict):
            problems.append(f"{at}: an object")
            continue
        unknown = set(s) - {"method", "path", "user", "json", "count"}
        if unknown:
            problems.append(f"{at}: unknown key(s) {', '.join(sorted(unknown))}")
        method = s.get("method")
        if not isinstance(method, str) or method.upper() not in METHODS:
            problems.append(f"{at}.method: one of {', '.join(METHODS)}")
            continue
        path = s.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            problems.append(f"{at}.path: a path starting with /")
            continue
        user = s.get("user")
        if user is not None and not isinstance(user, str):
            problems.append(f"{at}.user: a handle (string) or null")
            continue
        count = s.get("count", "N")
        if not _count(count):
            problems.append(f'{at}.count: "N" or a non-negative integer')
            continue
        out.append(SeedStep(method.upper(), path, user, s.get("json"), count))
    if raw and not any(st.count == "N" for st in out) and len(out) == len(raw):
        problems.append(f'{where}.steps: no step has count "N", so the seed does not grow with N')
    return tuple(out)


def _latency(raw: Any, sizes: tuple[int, ...], where: str, problems: list[str]):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        problems.append(f"{where}: null or {{median, size, margin}}")
        return None
    median, margin = raw.get("median"), raw.get("margin")
    size = raw.get("size", max(sizes) if sizes else None)
    ok = True
    if not _number(median) or median <= 0:
        problems.append(f"{where}.median: a positive number of milliseconds")
        ok = False
    if not _number(margin) or margin < 0:
        problems.append(f"{where}.margin: a non-negative fraction (0.5 = 50%), stated in the file")
        ok = False
    if not isinstance(size, int) or isinstance(size, bool) or size not in sizes:
        problems.append(f"{where}.size: one of the budget's sizes {list(sizes)}")
        ok = False
    return LatencyBudget(float(median), int(size), float(margin)) if ok else None


def parse(data: Any, path: Path | None = None) -> BudgetFile:
    """The budget file's content, validated (BudgetError lists every problem)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        raise BudgetError("the budget file is a JSON object {version, budgets}")
    if data.get("version") != VERSION:
        problems.append(f"version: must be {VERSION}")
    unknown = set(data) - {"version", "budgets", "prepare", "comment"}
    if unknown:
        problems.append(f"unknown top-level key(s) {', '.join(sorted(unknown))}")
    prepare = data.get("prepare")
    if prepare is not None and (not isinstance(prepare, str) or not prepare.strip()):
        problems.append("prepare: a shell command (string) or absent")
    raw = data.get("budgets")
    if not isinstance(raw, list) or not raw:
        problems.append("budgets: a non-empty list")
        raw = []
    budgets: list[Budget] = []
    seen: set[str] = set()
    for i, b in enumerate(raw):
        where = f"budgets[{i}]"
        if not isinstance(b, dict):
            problems.append(f"{where}: an object")
            continue
        before = len(problems)
        unknown = set(b) - {"id", "endpoint", "user", "seed", "sizes", "queries", "latency_ms",
                            "comment"}  # fmt: skip
        if unknown:
            problems.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        bid = b.get("id")
        if not isinstance(bid, str) or not _ID.fullmatch(bid):
            problems.append(f"{where}.id: letters, digits, _ . - (at most 64)")
        elif bid in seen:
            problems.append(f"{where}.id: {bid!r} is used twice")
        else:
            seen.add(bid)
            where = f"budget {bid!r}"
        ep = _endpoint(b.get("endpoint"))
        if ep is None:
            problems.append(f'{where}.endpoint: "METHOD /path", METHOD one of {", ".join(METHODS)}')
        user = b.get("user")
        if user is not None and not isinstance(user, str):
            problems.append(f"{where}.user: a handle (string) or null")
        seed = b.get("seed")
        steps: tuple[SeedStep, ...] = ()
        if not isinstance(seed, dict):
            problems.append(f'{where}.seed: {{items: "N", steps: [...]}}')
        else:
            if seed.get("items", "N") != "N":
                problems.append(f'{where}.seed.items: "N" (the size being measured)')
            steps = _steps(seed.get("steps"), f"{where}.seed", problems)
        sizes_raw = b.get("sizes")
        sizes: tuple[int, ...] = ()
        if (
            not isinstance(sizes_raw, list)
            or not all(
                isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in sizes_raw
            )  # fmt: skip
            or len(set(sizes_raw)) < 2
        ):
            problems.append(f"{where}.sizes: two or more distinct non-negative integers")
        else:
            sizes = tuple(sorted(set(sizes_raw)))
        q = b.get("queries")
        const = per = None
        if not isinstance(q, dict):
            problems.append(f"{where}.queries: {{constant, per_item}}")
        else:
            c, p = q.get("constant"), q.get("per_item", 0)
            if not _number(c) or c < 0:
                problems.append(f"{where}.queries.constant: a non-negative number")
            else:
                const = _frac(c)
            if not _number(p) or p < 0:
                problems.append(f"{where}.queries.per_item: a non-negative number")
            else:
                per = _frac(p)
        lat = _latency(b.get("latency_ms"), sizes, f"{where}.latency_ms", problems)
        if len(problems) == before and ep is not None and const is not None and per is not None:
            budgets.append(Budget(bid, ep[0], ep[1], user, steps, sizes, const, per, lat))
    if problems:
        where = f"{path}: " if path else ""
        raise BudgetError(f"{where}invalid budget file:\n  " + "\n  ".join(problems))
    return BudgetFile(path, tuple(budgets), prepare.strip() if prepare else None)


def load(path: Path | str) -> BudgetFile:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        raise BudgetError(f"cannot read the budget file {p}: {err.strerror or err}") from err
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise BudgetError(f"{p}: not JSON ({err})") from err
    return parse(data, p)


def expand(value: Any, i: int, new_uuid) -> Any:
    """``value`` with ``{i}`` and ``{uuid}`` replaced in every string (recursively)."""
    if isinstance(value, str):
        if "{uuid}" in value:
            value = value.replace("{uuid}", new_uuid())
        return value.replace("{i}", str(i))
    if isinstance(value, list):
        return [expand(v, i, new_uuid) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, i, new_uuid) for k, v in value.items()}
    return value


# ---- the fit and the verdict -----------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    constant: Fraction
    per_item: Fraction
    exact: bool

    def at(self, n: int) -> Fraction:
        return self.constant + self.per_item * n

    def to_dict(self) -> dict[str, Any]:
        return {"constant": _num(self.constant), "per_item": _num(self.per_item),
                "exact": self.exact}  # fmt: skip


def fit(points: dict[int, list[int]]) -> Fit:
    """constant and per-item from statement counts per size (``{n: [count per run]}``):
    the line through the smallest and the largest size, each at its largest count;
    ``exact`` when every size's runs agree and lie on that line."""
    sizes = sorted(n for n, runs in points.items() if runs)
    if len(sizes) < 2:
        raise ValueError("a fit needs counts at two or more sizes")
    lo, hi = sizes[0], sizes[-1]
    c_lo, c_hi = max(points[lo]), max(points[hi])
    per_item = Fraction(c_hi - c_lo, hi - lo)
    constant = c_lo - per_item * lo
    exact = all(len(set(points[n])) == 1 and constant + per_item * n == points[n][0] for n in sizes)
    return Fit(constant, per_item, exact)


@dataclass
class SizeResult:
    n: int
    statements: list[int] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    shapes: list[tuple[int, str]] | None = None  # (count, statement shape), first run

    def latency(self) -> dict[str, float] | None:
        if not self.latency_ms:
            return None
        return {"min": round(min(self.latency_ms), 3),
                "median": round(statistics.median(self.latency_ms), 3),
                "max": round(max(self.latency_ms), 3)}  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"n": self.n, "statements": list(self.statements),
                             "latency_ms": self.latency()}  # fmt: skip
        if self.shapes is not None:
            d["shapes"] = [{"count": c, "sql": s} for c, s in self.shapes]
        return d


@dataclass
class Report:
    budget: Budget
    sizes: list[SizeResult]
    fit: Fit | None = None
    verdict: str = "unmeasured"  # pass | fail | unmeasured
    notes: list[str] = field(default_factory=list)

    def delta(self) -> dict[str, Any] | None:
        """fit minus budget (negative: better than the budget)."""
        if self.fit is None:
            return None
        return {"constant": _num(self.fit.constant - self.budget.constant),
                "per_item": _num(self.fit.per_item - self.budget.per_item)}  # fmt: skip

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget.id,
            "endpoint": self.budget.endpoint,
            "sizes": [s.to_dict() for s in self.sizes],
            "fit": None if self.fit is None else self.fit.to_dict(),
            "budget": self.budget.to_dict(),
            "verdict": self.verdict,
            "delta": self.delta(),
            "notes": list(self.notes),
        }


def judge(budget: Budget, sizes: list[SizeResult], notes: list[str] | None = None) -> Report:
    """The report of ``budget`` on the measured ``sizes`` (see the module docstring)."""
    report = Report(budget, sizes, notes=list(notes or []))
    points = {s.n: s.statements for s in sizes if s.statements}
    if len(points) < 2:
        report.notes.append("unmeasured: statement counts at fewer than two sizes")
        return report
    f = fit(points)
    report.fit = f
    failures: list[str] = []
    if f.constant > budget.constant:
        failures.append(f"constant {_show(f.constant)} > budget {_show(budget.constant)}")
    if f.per_item > budget.per_item:
        failures.append(f"per_item {_show(f.per_item)} > budget {_show(budget.per_item)}")
    for s in sizes:
        if s.statements and max(s.statements) > budget.allowed(s.n):
            failures.append(f"N={s.n}: {max(s.statements)} statements > allowed "
                            f"{_show(budget.allowed(s.n))}")  # fmt: skip
    if not f.exact:
        spread = {s.n: sorted(set(s.statements)) for s in sizes if len(set(s.statements)) > 1}
        report.notes.append(
            "fit not exact: "
            + (f"runs differ at {spread}; " if spread else "")
            + "the counts are not one line in N (the fit uses the largest count per size)"
        )
    if budget.latency is not None:
        lat = budget.latency
        at = next((s for s in sizes if s.n == lat.size and s.latency_ms), None)
        if at is None:
            failures.append(f"latency: no runs at N={lat.size}")
        else:
            med = statistics.median(at.latency_ms)
            if med > lat.limit():
                failures.append(
                    f"latency: median {med:.2f} ms at N={lat.size} > "
                    f"{lat.median:g} ms x (1 + {lat.margin:g}) = {lat.limit():.2f} ms"
                )
            else:
                report.notes.append(f"latency: median {med:.2f} ms at N={lat.size} within "
                                    f"{lat.limit():.2f} ms")  # fmt: skip
    better = []
    if f.constant < budget.constant:
        better.append(f"constant {_show(f.constant)} < {_show(budget.constant)}")
    if f.per_item < budget.per_item:
        better.append(f"per_item {_show(f.per_item)} < {_show(budget.per_item)}")
    if failures:
        report.verdict = "fail"
        report.notes[:0] = [f"over budget: {x}" for x in failures]
        if f.per_item < budget.per_item and all(
            not s.statements or max(s.statements) <= budget.allowed(s.n) for s in sizes
        ):
            report.notes.append(
                "every measured size is within its allowed count: the constant grew while the "
                "per-item cost fell (a trade the budget's rule counts as over budget)"
            )
    else:
        report.verdict = "pass"
    if better:
        report.notes.append("better than the budget: " + ", ".join(better))
    return report


def _num(x: Fraction) -> int | float:
    return int(x) if x.denominator == 1 else round(float(x), 6)


def _show(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{float(x):.4g}"


# ---- rendering -------------------------------------------------------------------------


def render(reports: list[Report]) -> str:
    """A text table per budget, then the overall verdict."""
    lines: list[str] = []
    for r in reports:
        b = r.budget
        lines.append(f"{b.id}: {b.endpoint}  budget {_show(b.constant)} + {_show(b.per_item)}*N "
                     f"statements  ->  {r.verdict.upper()}")  # fmt: skip
        lines.append(f"  {'N':>5}  {'statements per run':<28} {'allowed':>8}  "
                     f"{'latency ms min/median/max':>27}")  # fmt: skip
        for s in r.sizes:
            counts = sorted(set(s.statements))
            if not s.statements:
                shown = "-"
            elif len(counts) == 1:
                shown = f"{counts[0]} x{len(s.statements)}"
            else:
                shown = ",".join(str(c) for c in s.statements)
            lat = s.latency()
            lat_s = f"{lat['min']:.2f} / {lat['median']:.2f} / {lat['max']:.2f}" if lat else "-"
            lines.append(f"  {s.n:>5}  {shown:<28} {_show(b.allowed(s.n)):>8}  {lat_s:>27}")
            for c, sql in s.shapes or ():
                lines.append(f"         {c:>4} x {sql[:110]}")
        if r.fit is not None:
            lines.append(f"  fit: {_show(r.fit.constant)} + {_show(r.fit.per_item)}*N "
                         f"({'exact' if r.fit.exact else 'not exact'})")  # fmt: skip
        for note in r.notes:
            lines.append(f"  - {note}")
    verdicts = [r.verdict for r in reports]
    overall = overall_verdict(verdicts)
    lines.append(f"perf: {overall} ({verdicts.count('pass')} pass, {verdicts.count('fail')} fail, "
                 f"{verdicts.count('unmeasured')} unmeasured)")  # fmt: skip
    return "\n".join(lines)


def overall_verdict(verdicts: list[str]) -> str:
    """fail when any budget is exceeded (a definite answer), else unmeasured when any
    budget could not be measured, else pass."""
    if "fail" in verdicts:
        return "fail"
    if "unmeasured" in verdicts or not verdicts:
        return "unmeasured"
    return "pass"


__all__ = [
    "BUDGET_FILE",
    "Budget",
    "BudgetError",
    "BudgetFile",
    "Fit",
    "LatencyBudget",
    "Report",
    "SeedStep",
    "SizeResult",
    "expand",
    "fit",
    "judge",
    "load",
    "overall_verdict",
    "parse",
    "render",
]
