"""Layer X: concurrent races of the writes behind the spec's "at most" rules.

Layers A-D send one request at a time, so they cannot see a race: two concurrent
creates that both pass a check-then-insert, or a worker that overwrites a cancel that
came in while it ran. Layer X races the scenario steps that write those rules' tables.

* **Targets.** Every count-bound invariant with an upper bound (``count(T, ...) <= n``
  or ``< n``, either side) and every :class:`~lattice.spec.model.Machine`; a target's
  table is the invariant's or the machine's.
* **Recording.** Each scenario runs once, sequentially, on its own fresh seed (with its
  ``given`` rows), every step's database difference on the target tables recorded. A
  step whose 2xx answer added or changed rows of a target table is a *racy step*. Every
  write endpoint's target tables are collected over all recordings (a worker drain that
  writes nothing in one scenario writes runs in another).
* **Self-race.** For a racy step i of a scenario: a fresh seed, steps 1..i-1 replayed
  sequentially, then step i fired ``k`` times at once -- each copy its own app instance
  and client on the same SQLite file, released together by a ``threading.Barrier``, as
  the step's own user.
* **Pair race.** Racy step i against the next later write step j of the same scenario
  (not GET/HEAD/OPTIONS) that writes one of i's target tables -- in the recording, or
  anywhere its endpoint was recorded -- after replaying 1..i-1: i once and j once, at
  once.
* **After a race** every invariant runs on the database (violations the database had
  before the race are not blamed on it), and every machine's rule: a 2xx answer whose
  JSON object shows a machine row (its key and the machine column, in the form the
  sequential recording confirmed against the database) promises the row is at that
  state or later, so its final state must be reachable from it; every row's final
  state must be reachable from its state before the race; a new row's from an initial
  state (when the machine names any).
* **Probabilistic: sound in one direction only.** Each race runs ``repeats`` times
  (stopping at its first violation). A violation is a finding (``layer="X"``, silent);
  an app error answer (5xx, or an unhandled error) of a step that answered below 500 in
  the sequential recording is a loud finding, rule "no server error under concurrency"
  (a step that fails sequentially too is layer D's). No violation is reported as *not
  refuted*, with the tries, never as verified. One finding per rule and
  racing-endpoint combination; the other races refuting it are listed on it
  (``details.also_in``).
* **Budget.** At most ``budget`` races, each within ``timeout`` seconds; what was
  skipped is a note. Identical races (the same given rows, prefix and racing steps) run
  once. The order is deterministic: scenario order, each racy step's self-race then its
  pair race, with the first race of every racing-endpoint combination (ids as holes)
  ahead of the races repeating one, so a budget reaches every endpoint first.

A runtime whose apps are each one live server (``AppRuntime.one_server``: a process
app) races its copies as ``k`` clients of one server instead: one app built, its
transport opened ``k`` times (an httpx client each, so the server sees ``k``
connections at once). With a ``monitor`` (layer C through the runtime's transport),
every request of the layer is watched too: the recordings' and replays' steps
(``X | <scenario> / step <n>``) and the racing requests (``X | race <race>``).

A scenario's ``Await`` steps (waits on a background service) are never raced: layer X
takes a scenario's steps before its first ``Await`` only, with a note.

An app whose own database engine cannot be used from several threads ("created in a
thread can only be used in that same thread") is a note, not a finding; a race the
harness could not run (an app that does not start, a login that fails, a replay that
answers differently from the recording) is a note too.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.spec.check import (
    AppFactory,
    CheckError,
    InvariantMonitor,
    Login,
    _body,
    _close,
    _insert_given,
    _key_column,
    _row_key,
    _template_of,
    db_diff,
    spec_plans,
)
from lattice.spec.invariants import _FLIPPED
from lattice.spec.model import Await, Invariant, Machine, Scenario, Spec, Step, resolve_table
from lattice.spec.report import CheckFinding, Report

if TYPE_CHECKING:
    from lattice.importer.transport import AppRuntime

X_K = 12
X_REPEATS = 3
X_BUDGET = 60
X_TIMEOUT = 30.0
SERVER_ERROR_RULE = "no_server_error_under_concurrency"
SERVER_ERROR_SENTENCE = "The app answers no server error under concurrency."
READS = frozenset({"GET", "HEAD", "OPTIONS"})
SAME_THREAD = "same thread"


# ---- targets --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A rule layer X races: an upper count bound (``invariant``) or a ``machine``, over
    ``table`` (its SQL name)."""

    rule_id: str
    sentence: str
    table: str
    invariant: Invariant | None = None
    machine: Machine | None = None


def upper_bound_table(inv: Invariant) -> str | None:
    """The table of ``inv`` when it is a count bound with an upper bound (``count(T,
    ...) <= n`` / ``< n``, or the flipped ``n >= count(...)``), else None."""
    try:
        body = ast.parse(inv.expr.strip(), mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(body, ast.Compare) and len(body.ops) == 1):
        return None
    left, right = body.left, body.comparators[0]
    for call, flip in ((left, False), (right, True)):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "count"
            and call.args
            and isinstance(call.args[0], ast.Name)
        ):
            op = type(body.ops[0])
            if flip:
                op = _FLIPPED.get(op, op)
            return call.args[0].id if op in (ast.LtE, ast.Lt) else None
    return None


def targets(spec: Spec) -> list[Target]:
    out: list[Target] = []
    for inv in spec.invariants:
        name = upper_bound_table(inv)
        found = resolve_table(spec.profile, name) if name else None
        if found:
            out.append(Target(inv.name, inv.sentence, found[1], invariant=inv))
    for m in spec.machines:
        found = resolve_table(spec.profile, m.table)
        if found:
            rule = f"{found[0]}.{m.column}"
            sentence = f"{rule} moves only along its state machine"
            out.append(Target(rule, sentence, found[1], machine=m))
    return out


# ---- the machine rule -----------------------------------------------------------------------


def reachable(machine: Machine, state: str) -> set[str]:
    """``state`` and every state reachable from it along the machine's edges."""
    seen = {state}
    todo = [state]
    while todo:
        s = todo.pop()
        for a, b in machine.edges:
            if a == s and b not in seen:
                seen.add(b)
                todo.append(b)
    return seen


def machine_violations(
    machine: Machine,
    before: Mapping[Any, Any],
    after: Mapping[Any, Any],
    promises: Sequence[tuple[Any, Any]],
) -> list[dict[str, Any]]:
    """The rows (key -> state, before and after a race) that break the machine: a
    promised state (a 2xx answer showed the row at it) the final state is not reachable
    from; a final state not reachable from the state before; a new row's final state not
    reachable from any initial state (when the machine names initial states)."""
    out: list[dict[str, Any]] = []
    blamed: set[Any] = set()
    for key, promised in promises:
        if key not in after or key in blamed:
            continue
        final = after[key]
        if final not in reachable(machine, promised):
            blamed.add(key)
            out.append({"key": key, "promised": promised, "final": final,
                        "why": f"an answer showed {promised!r}, but the row ended "
                               f"{final!r}, which is not reachable from it"})  # fmt: skip
    for key, final in after.items():
        if key in blamed:
            continue
        if key in before:
            prior = before[key]
            if final != prior and final not in reachable(machine, prior):
                blamed.add(key)
                out.append({"key": key, "before": prior, "final": final,
                            "why": f"{prior!r} -> {final!r} is not a move of the "
                                   "machine (nor a path of moves)"})  # fmt: skip
        elif machine.initial and not any(final in reachable(machine, s) for s in machine.initial):
            blamed.add(key)
            out.append({"key": key, "before": None, "final": final,
                        "why": f"a new row ended {final!r}, not reachable from an "
                               f"initial state {list(machine.initial)}"})  # fmt: skip
    return out


# ---- recording ------------------------------------------------------------------------------


@dataclass
class StepRecord:
    index: int  # 1-based, as layer D numbers steps
    step: Step
    status: int | None
    body: Any
    diff: dict[str, dict[str, list[Any]]]
    after: dict[str, list[dict[str, Any]]]


@dataclass
class Recording:
    scenario: Scenario
    records: list[StepRecord] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class RacePlan:
    scenario: Scenario
    i: int  # 1-based
    j: int | None = None  # 1-based, a pair race's second step

    @property
    def label(self) -> str:
        tail = f"+{self.j}" if self.j is not None else ""
        return f"{self.scenario.name}/{self.i}{tail}"

    def steps(self, k: int) -> list[tuple[int, Step]]:
        s = self.scenario.steps
        if self.j is None:
            return [(self.i, s[self.i - 1])] * k
        return [(self.i, s[self.i - 1]), (self.j, s[self.j - 1])]


def _read(db: Path, tables: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(db)
    try:
        out: dict[str, list[dict[str, Any]]] = {}
        for t in tables:
            try:
                cur = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            except sqlite3.Error:
                out[t] = []
                continue
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row, strict=True)) for row in cur]
        return out
    finally:
        conn.close()


def _answer(response: Any) -> tuple[int, Any]:
    try:
        from lattice.importer.transport import Answer
    except ImportError:  # pragma: no cover
        Answer = ()  # type: ignore[assignment]  # noqa: N806
    if isinstance(response, Answer):  # type: ignore[arg-type]
        return response.status, response.body
    return response.status_code, _body(response)


def _step_key(step: Step) -> str:
    return json.dumps(
        [step.method, step.path, step.user, step.json, dict(step.params or {})],
        sort_keys=True,
        default=str,
    )


def _step_view(index: int, step: Step) -> dict[str, Any]:
    return {"step": index, "method": step.method, "path": step.path, "user": step.user}


class _Harness:
    """What every race needs: the app, the database, the callers, the targets."""

    def __init__(
        self,
        spec: Spec,
        app_factory: AppFactory,
        make_db: Callable[[str, Path], Path],
        login: Login | None,
        runtime: AppRuntime | None,
        timeout: float,
        c_monitor: InvariantMonitor | None = None,
    ) -> None:
        self.spec = spec
        self.app_factory = app_factory
        self.make_db = make_db
        self.login = login
        self.runtime = runtime
        self.timeout = timeout
        # layer C watching this layer's requests (a runtime's transport only)
        self.c_monitor = c_monitor
        if c_monitor is not None and runtime is not None:
            self.app_factory, self.runtime = c_monitor.watch(app_factory, runtime)
        self.one_server = bool(getattr(runtime, "one_server", False))
        self.targets = targets(spec)
        self.tables = list(dict.fromkeys(t.table for t in self.targets))
        self.keys: dict[str, str] = {}
        for t in self.tables:
            found = resolve_table(spec.profile, t)
            key = _key_column(found[2]) if found else None
            if key:
                self.keys[t] = key
        try:
            self.plans = spec_plans(spec)
        except CheckError:
            self.plans = []
        self.monitor = InvariantMonitor.for_spec(spec)

    # -- serving

    def label(self, text: str) -> None:
        if self.c_monitor is not None:
            self.c_monitor.label = text

    def open(self, app: Any, *, raise_errors: bool) -> Any:
        if self.runtime is None:
            from fastapi.testclient import TestClient

            return TestClient(app, raise_server_exceptions=raise_errors)
        return self.runtime.open(app, raise_errors=raise_errors)

    def token(self, client: Any, user: str) -> str:
        if self.runtime is not None:
            return self.runtime.auth.token(client, user, self.login)
        if self.login is None:
            raise RuntimeError("no login given")
        return self.login(client, user)

    def headers(self, client: Any, token: str | None) -> dict[str, str]:
        if token is None:
            return {}
        if self.runtime is not None:
            return dict(self.runtime.auth.present(client, token))
        return {"Authorization": f"Bearer {token}"}

    def send(self, client: Any, step: Step, token: str | None) -> Any:
        return client.request(
            step.method,
            step.path,
            params=dict(step.params) if step.params else None,
            json=step.json,
            headers=self.headers(client, token),
        )

    def endpoint(self, step: Step) -> str:
        found = _template_of(step.path, self.plans, step.method) if self.plans else None
        return found or f"{step.method} {step.path}"

    def endpoint_class(self, step: Step) -> str:
        """The endpoint, with concrete ids as holes (for the writes map)."""
        found = _template_of(step.path, self.plans, step.method) if self.plans else None
        if found:
            return found
        path = re.sub(r"/(\d+|[0-9a-fA-F-]{36})(?=/|$)", "/{}", step.path)
        return f"{step.method} {path}"

    def fresh_db(self, scenario: Scenario, where: Path) -> Path:
        db = self.make_db("seed", where / "race.db")
        if scenario.given:
            _insert_given(self.spec, db, scenario.given)
        return db

    # -- the sequential pass

    def replay(
        self,
        scenario: Scenario,
        db: Path,
        upto: int,
        users: Sequence[str],
        record: bool,
    ) -> tuple[dict[str, str], list[StepRecord]]:
        """Steps 1..upto in order on one app; then every handle of ``users`` logged in.
        Answers each caller's token and (``record``) each step's record."""
        records: list[StepRecord] = []
        tokens: dict[str, str] = {}
        app = self.app_factory(db)
        try:
            with self.open(app, raise_errors=False) as client:
                before = _read(db, self.tables) if record else {}
                for n, step in enumerate(scenario.steps[:upto], 1):
                    self.label(f"X | {scenario.name} / step {n}")
                    if step.user is not None and step.user not in tokens:
                        tokens[step.user] = self.token(client, step.user)
                    response = self.send(client, step, tokens.get(step.user or ""))
                    status, body = _answer(response)
                    if record:
                        after = _read(db, self.tables)
                        diff = db_diff(before, after, self.keys)
                        records.append(StepRecord(n, step, status, body, diff, after))
                        before = after
                    else:
                        records.append(StepRecord(n, step, status, body, {}, {}))
                self.label(f"X | {scenario.name} / login")
                for user in users:
                    if user not in tokens:
                        tokens[user] = self.token(client, user)
        finally:
            _close(self.runtime, app)
        return tokens, records

    def record(self, scenario: Scenario) -> Recording:
        rec = Recording(scenario)
        with tempfile.TemporaryDirectory(prefix="lattice-check-x-") as name:
            try:
                db = self.fresh_db(scenario, Path(name))
                _, rec.records = self.replay(scenario, db, len(scenario.steps), (), True)
            except Exception as err:  # noqa: BLE001 -- noted; the scenario is not raced
                if _fatal(self, err):
                    raise
                rec.error = f"{type(err).__name__}: {err}"
        return rec


def _fatal(h: _Harness, err: Exception) -> bool:
    """Whether an error ends the check rather than a race: a server process that does not
    start (a CheckError of a one-server runtime) fails every race the same way."""
    return h.one_server and isinstance(err, CheckError)


def _written(record: StepRecord, tables: Sequence[str] | set[str]) -> set[str]:
    """The target tables a step's recorded answer added or changed rows of (2xx only)."""
    if record.status is None or not 200 <= record.status < 300:
        return set()
    return {
        t for t, d in record.diff.items() if t in tables and (d.get("added") or d.get("changed"))
    }


def plan_races(
    h: _Harness, recordings: Sequence[Recording]
) -> tuple[list[RacePlan], dict[str, set[str]]]:
    """The races, in a deterministic order (scenario order; per racy step its self-race,
    then its pair race), identical races once; and every endpoint's written tables."""
    writes: dict[str, set[str]] = {}
    for rec in recordings:
        for r in rec.records:
            if r.step.method not in READS:
                writes.setdefault(h.endpoint_class(r.step), set()).update(_written(r, h.tables))
    plans: list[RacePlan] = []
    seen: set[str] = set()
    for rec in recordings:
        if rec.error is not None:
            continue
        sc = rec.scenario
        given = json.dumps(sc.given or {}, sort_keys=True, default=str)
        for r in rec.records:
            racy = _written(r, h.tables)
            if not racy or r.step.method in READS:
                continue
            prefix = [_step_key(s) for s in sc.steps[: r.index - 1]]
            candidates: list[RacePlan] = [RacePlan(sc, r.index)]
            for later in rec.records[r.index :]:
                if later.step.method in READS:
                    continue
                touched = _written(later, h.tables) | writes.get(
                    h.endpoint_class(later.step), set()
                )
                if touched & racy:
                    candidates.append(RacePlan(sc, r.index, later.index))
                    break
            for plan in candidates:
                key = json.dumps(
                    [given, prefix, [_step_key(s) for _, s in plan.steps(2)], plan.j is None]
                )
                if key not in seen:
                    seen.add(key)
                    plans.append(plan)
    # the first race of every racing-endpoint combination first, then the rest (each in
    # plan order), so a budget covers every endpoint before it repeats one
    signature = _signature(h)
    firsts: list[RacePlan] = []
    rest: list[RacePlan] = []
    signatures: set[tuple[str, ...]] = set()
    for p in plans:
        sig = signature(p)
        (rest if sig in signatures else firsts).append(p)
        signatures.add(sig)
    return firsts + rest, writes


def _signature(h: _Harness) -> Callable[[RacePlan], tuple[str, ...]]:
    """A race's racing endpoints (ids as holes) and kind."""

    def signature(p: RacePlan) -> tuple[str, ...]:
        return (*(h.endpoint_class(s) for _, s in p.steps(2)), str(p.j is None))

    return signature


# ---- one race -------------------------------------------------------------------------------


@dataclass
class RaceOutcome:
    findings: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)
    # (rule_id, sentence, verdict, details)
    skipped: str | None = None  # why the race could not run (a note)
    engine_note: str | None = None


def _wire(schema: Any, column: str, value: Any) -> Any:
    values = dict(schema.one_of).get(column)
    stored = dict(schema.one_of_stored).get(column)
    if values and stored and value in stored:
        return values[list(stored).index(value)]
    return value


def _states(h: _Harness, m: Machine, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    found = resolve_table(h.spec.profile, m.table)
    if found is None:
        return {}
    _, sql, schema = found
    key = h.keys.get(sql)
    if key is None:
        return {}
    return {str(r.get(key)): _wire(schema, m.column, r.get(m.column)) for r in rows}


def shown_rows(h: _Harness, recordings: Sequence[Recording]) -> dict[tuple[str, str], bool]:
    """(endpoint class, machine rule) -> whether that endpoint's 2xx JSON objects show a
    machine row: every recorded one carrying the key and the machine column matched the
    database row right after the step (and at least one did)."""
    out: dict[tuple[str, str], bool] = {}
    for t in h.targets:
        m = t.machine
        if m is None:
            continue
        key = h.keys.get(t.table)
        if key is None:
            continue
        for rec in recordings:
            for r in rec.records:
                if r.status is None or not 200 <= r.status < 300:
                    continue
                if not (isinstance(r.body, Mapping) and key in r.body and m.column in r.body):
                    continue
                states = _states(h, m, r.after.get(t.table, []))
                ok = states.get(str(r.body[key])) == r.body[m.column]
                slot = (h.endpoint_class(r.step), t.rule_id)
                out[slot] = out.get(slot, True) and ok
    return out


def run_race(
    h: _Harness,
    plan: RacePlan,
    k: int,
    recording: Recording,
    shows: Mapping[tuple[str, str], bool],
) -> RaceOutcome:
    out = RaceOutcome()
    copies = plan.steps(k)
    prefix = plan.i - 1
    with tempfile.TemporaryDirectory(prefix="lattice-check-x-") as name:
        db = h.fresh_db(plan.scenario, Path(name))
        users = [s.user for _, s in copies if s.user is not None]
        try:
            tokens, replayed = h.replay(plan.scenario, db, prefix, users, False)
        except Exception as err:  # noqa: BLE001 -- the harness could not replay
            if _fatal(h, err):
                raise
            out.skipped = f"the replay of steps 1-{prefix} failed: {type(err).__name__}: {err}"
            return out
        for got, want in zip(replayed, recording.records, strict=False):
            if got.status != want.status:
                out.skipped = (
                    f"the replay's step {got.index} answered {got.status}, the recording "
                    f"{want.status}: the prefix is not deterministic"
                )
                return out
        base_inv = h.monitor.violation_keys(db)
        before = _read(db, h.tables)
        apps: list[Any] = []
        clients: list[Any] = []
        h.label(f"X | race {plan.label}")
        try:
            for _ in copies:
                # one server (a process app): every copy is one more client of one app
                if not (h.one_server and apps):
                    apps.append(h.app_factory(db))
                client = h.open(apps[-1], raise_errors=True)
                clients.append(client.__enter__())
        except Exception as err:  # noqa: BLE001 -- the harness could not start the copies
            if _fatal(h, err):
                _shutdown(h, apps, clients)
                raise
            out.skipped = f"cannot start {len(copies)} app copies: {type(err).__name__}: {err}"
            _shutdown(h, apps, clients)
            return out
        results: list[Any] = [None] * len(copies)
        barrier = threading.Barrier(len(copies))

        def fire(n: int) -> None:
            _, step = copies[n]
            try:
                barrier.wait(timeout=h.timeout)
            except threading.BrokenBarrierError as err:
                results[n] = ("harness", err)
                return
            try:
                response = h.send(clients[n], step, tokens.get(step.user or ""))
                results[n] = ("answer", _answer(response))
            except Exception as err:  # noqa: BLE001 -- the app raised
                results[n] = ("raised", err)

        threads = [
            threading.Thread(target=fire, args=(n,), daemon=True) for n in range(len(copies))
        ]
        for t in threads:
            t.start()
        deadline = time.monotonic() + h.timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            out.skipped = f"timed out after {h.timeout:g}s"
            if h.one_server:  # a server process is never left running
                _shutdown(h, apps, clients)
            return out  # the copies are left to their threads
        _shutdown(h, apps, clients)
        answers: list[dict[str, Any]] = []
        for (index, _step), res in zip(copies, results, strict=True):
            kind, value = res if res is not None else ("harness", "no result")
            if kind == "harness":
                out.skipped = f"the harness failed: {value}"
                return out
            if kind == "raised":
                text = f"{type(value).__name__}: {value}"
                text = text if len(text) <= 300 else text[:297] + "..."
                if SAME_THREAD in str(value):
                    out.engine_note = text
                answers.append({"step": index, "status": 500, "error": text})
            else:
                status, body = value
                answers.append({"step": index, "status": status, "body": body})
        if out.engine_note is not None:
            return out
        steps = [_step_view(i, s) for i, s in dict.fromkeys(copies)]
        common = {"race": plan.label, "steps": steps, "k": len(copies),
                  "answers": _answer_summary(answers)}  # fmt: skip
        # a step that answers 5xx sequentially too is layer D's finding, not a race's
        sequential = {r.index: r.status or 0 for r in recording.records}
        errors = [a for a in answers if a["status"] >= 500 and sequential.get(a["step"], 0) < 500]
        if errors:
            out.findings.append(
                (
                    SERVER_ERROR_RULE,
                    SERVER_ERROR_SENTENCE,
                    "loud",
                    common | {"rows": [], "errors": errors[:5]},
                )  # fmt: skip
            )
        found = h.monitor.violations(db)
        for inv, _ in h.monitor.invariants:
            new = [r for r in found[inv.name] if _row_key(r) not in base_inv.get(inv.name, set())]
            if new:
                out.findings.append((inv.name, inv.sentence, "silent",
                                     common | {"rows": new[:20], "expr": inv.expr}))  # fmt: skip
        after = _read(db, h.tables)
        for t in h.targets:
            m = t.machine
            if m is None:
                continue
            key = h.keys.get(t.table)
            promises: list[tuple[Any, Any]] = []
            for (_, step), a in zip(copies, answers, strict=True):
                body = a.get("body")
                if (
                    key is not None
                    and 200 <= a["status"] < 300
                    and isinstance(body, Mapping)
                    and key in body
                    and m.column in body
                    and shows.get((h.endpoint_class(step), t.rule_id))
                ):
                    promises.append((str(body[key]), body[m.column]))
            bad = machine_violations(
                m,
                _states(h, m, before.get(t.table, [])),
                _states(h, m, after.get(t.table, [])),
                promises,
            )
            if bad:
                out.findings.append((t.rule_id, t.sentence, "silent", common | {"rows": bad[:20]}))
    return out


def _answer_summary(answers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each racing step's answers: status -> how many (and the first error, if any)."""
    by_step: dict[int, dict[str, Any]] = {}
    for a in answers:
        s = by_step.setdefault(a["step"], {"step": a["step"], "statuses": {}})
        s["statuses"][a["status"]] = s["statuses"].get(a["status"], 0) + 1
        if "error" in a and "error" not in s:
            s["error"] = a["error"]
    return list(by_step.values())


def _shutdown(h: _Harness, apps: Sequence[Any], clients: Sequence[Any]) -> None:
    for c in clients:
        try:
            c.__exit__(None, None, None)
        except Exception:  # noqa: BLE001, S110 -- best effort
            pass
    for app in apps:
        try:
            _close(h.runtime, app)
        except Exception:  # noqa: BLE001, S110 -- best effort
            pass


# ---- the layer ------------------------------------------------------------------------------


def layer_x(
    spec: Spec,
    report: Report,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    *,
    login: Login | None,
    runtime: AppRuntime | None = None,
    monitor: InvariantMonitor | None = None,
    k: int = X_K,
    repeats: int = X_REPEATS,
    budget: int = X_BUDGET,
    timeout: float = X_TIMEOUT,
) -> None:
    """Layer X on ``report`` (see the module docstring). ``monitor``: layer C, watching
    every request of the layer through ``runtime``'s transport (None: not watched)."""
    if k < 2 or repeats < 1 or budget < 0:
        raise CheckError("layer X: x_k must be at least 2, x_repeats at least 1, x_budget >= 0")
    started = time.monotonic()
    h = _Harness(spec, app_factory, make_db, login, runtime, timeout, monitor)
    report.races = []
    if not h.targets:
        report.notes.append(
            "layer X: the spec has no upper count bound and no machine; nothing to race"
        )
        return
    if not spec.scenarios:
        report.notes.append("layer X: the spec has no scenarios; no steps to race")
        return
    recordings = []
    for sc in spec.scenarios:
        sc = _before_await(sc, report)
        rec = h.record(sc)
        if rec.error is not None:
            report.notes.append(f"layer X: scenario {sc.name!r} could not run ({rec.error}); "
                                "not raced")  # fmt: skip
        recordings.append(rec)
    by_name = {r.scenario.name: r for r in recordings}
    plans, _ = plan_races(h, recordings)
    shows = shown_rows(h, recordings)
    run, skipped = plans[:budget], plans[budget:]
    tries: dict[str, int] = {t.rule_id: 0 for t in h.targets}
    refuted: dict[str, int] = {}
    engine_notes: list[str] = []
    first: dict[tuple[str, tuple[str, ...]], CheckFinding] = {}
    signature = _signature(h)
    for plan in run:
        rec = by_name[plan.scenario.name]
        steps = dict.fromkeys(plan.steps(k))
        width = len(plan.steps(k))
        tables = _written(rec.records[plan.i - 1], h.tables)
        entry: dict[str, Any] = {
            "race": plan.label,
            "kind": "self" if plan.j is None else "pair",
            "steps": [_step_view(i, s) for i, s in steps],
            "k": width,
            "tables": sorted(tables),
            "tries": 0,
            "outcome": "not refuted",
            "rules": [],
        }
        report.races.append(entry)
        for _ in range(repeats):
            outcome = run_race(h, plan, k, rec, shows)
            if outcome.engine_note is not None:
                entry["outcome"] = "skipped: the app's engine cannot serve concurrent clients"
                engine_notes.append(f"{plan.label}: {outcome.engine_note}")
                break
            if outcome.skipped is not None:
                entry["outcome"] = f"skipped: {outcome.skipped}"
                report.notes.append(f"layer X: race {plan.label} skipped: {outcome.skipped}")
                break
            entry["tries"] += 1
            for t in h.targets:
                if t.table in tables:
                    tries[t.rule_id] += 1
            if outcome.findings:
                entry["outcome"] = "refuted"
                for rule_id, sentence, verdict, details in outcome.findings:
                    entry["rules"].append(rule_id)
                    refuted[rule_id] = refuted.get(rule_id, 0) + 1
                    # one finding per rule and racing-endpoint combination; the other
                    # races refuting it are listed on it
                    slot = (rule_id, signature(plan))
                    if slot in first:
                        first[slot].details.setdefault("also_in", []).append(plan.label)
                        continue
                    first[slot] = _finding(h, report, plan, rule_id, sentence, verdict,
                                           details, entry["tries"], repeats)  # fmt: skip
                break
    if engine_notes:
        report.notes.append(
            "layer X: the app's own database engine cannot be used from several threads "
            "(it needs e.g. check_same_thread=False); these races were not run: "
            + "; ".join(engine_notes[:5])
        )
    if skipped:
        report.notes.append(
            f"layer X: {len(skipped)} race(s) skipped over the budget of {budget}: "
            + ", ".join(p.label for p in skipped[:10])
            + (" ..." if len(skipped) > 10 else "")
        )
    _summary(h, report, run, refuted, tries, k, repeats, time.monotonic() - started)


def _before_await(scenario: Scenario, report: Report) -> Scenario:
    """The scenario up to its first ``Await`` step: layer X never races (nor replays) a
    wait on a background service, nor the steps after it (they rely on what the service
    did)."""
    first = next((i for i, s in enumerate(scenario.steps) if isinstance(s, Await)), None)
    if first is None:
        return scenario
    report.notes.append(
        f"layer X: scenario {scenario.name!r}: only its {first} step(s) before step "
        f"{first + 1} are raced; step {first + 1} awaits the "
        f"{scenario.steps[first].service} service"  # type: ignore[union-attr]
    )
    return replace(scenario, steps=scenario.steps[:first])


def _finding(
    h: _Harness,
    report: Report,
    plan: RacePlan,
    rule_id: str,
    sentence: str,
    verdict: str,
    details: dict[str, Any],
    attempt: int,
    repeats: int,
) -> CheckFinding:
    step = plan.scenario.steps[plan.i - 1]
    finding = CheckFinding(
        layer="X",
        verdict=verdict,  # type: ignore[arg-type]
        rule=sentence,
        endpoint=h.endpoint(step),
        case=plan.label,
        request={
            "method": step.method,
            "path": step.path,
            "user": step.user,
            "params": dict(step.params) if step.params else None,
            "json": step.json,
        },
        details=details
        | {
            "rule_id": rule_id,
            "scenario": plan.scenario.name,
            "try": attempt,
            "repeats": repeats,
        },  # fmt: skip
    )
    report.findings.append(finding)
    return finding


def _summary(
    h: _Harness,
    report: Report,
    run: Sequence[RacePlan],
    refuted: Mapping[str, int],
    tries: Mapping[str, int],
    k: int,
    repeats: int,
    seconds: float,
) -> None:
    selfs = sum(p.j is None for p in run)
    parts = []
    for t in h.targets:
        if t.rule_id in refuted:
            parts.append(f"{t.rule_id} refuted")
        elif tries[t.rule_id]:
            parts.append(f"{t.rule_id} not refuted ({tries[t.rule_id]} tries; not verified)")
        else:
            parts.append(f"{t.rule_id} not raced (no scenario step writes {t.table})")
    extra = [r for r in refuted if r not in tries]
    parts += [f"{r} refuted" for r in extra]
    report.notes.append(
        f"layer X: {len(run)} race(s) ({selfs} self-races of {k} copies, {len(run) - selfs} "
        f"pair races), each up to {repeats} time(s), in {seconds:.1f}s: "
        + "; ".join(parts)
        + ". Races are probabilistic: not refuted is never verified."
    )


__all__ = [
    "SERVER_ERROR_RULE",
    "Target",
    "layer_x",
    "machine_violations",
    "reachable",
    "targets",
    "upper_bound_table",
]
