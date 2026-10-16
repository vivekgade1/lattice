"""Running the workflow plan against the app (Spike B, D5-D7): the live run L, the frozen
Scenario and its replay R, the state-machine confirmation, the service sweeps, and the
command (``lattice import workflows``).

* **L**: per flow a fresh database (a clone of the harness's seed), the harness's
  ``make_app``, the declared users' logins as the checker makes them. Values are threaded
  (:mod:`lattice.importer.workflows`, D4); after each request every table of the model is
  read into a SQLite snapshot. The expected status of a step is the one observed, when it
  is in the route's code evidence; otherwise the step fails.
* **Freeze, R**: the concrete requests become a :class:`~lattice.spec.model.Scenario`;
  :func:`lattice.spec.check.check` (layer D) replays them on fresh databases. A flow is
  ``verified`` only when L passed and the replay has no finding for it.
* **Machines**: the snapshots become :class:`~lattice.importer.mine.Run` s and ``mine``
  runs unchanged; a machine is ``confirmed`` / ``partial`` / ``static`` / ``observed`` /
  ``refuted``. A harness service is swept once per machine (D7).

Nothing is written into the target; the scratch directory sits under ``--out`` and is
removed at the end. A missing Docker, binary or driver stops the run with an error
naming it; teardown is in ``finally``.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import decimal
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.importer import workflows as W

CONTAINER_PREFIX = "lattice-wf-"
SWEEP_SECONDS = 8.0
SWEEP_POLL_S = 0.5
AWAIT_WITHIN_S = 10.0
# flows whose last step is expected to be refused (the flow documents the refusal)
REFUSAL_KINDS = ("owner_guard", "after_delete", "guard")
# which GET over a table lists its ids (D2): `specific` (the one touching the fewest tables,
# round 2) or `first` (the first in registration order, round 1)
LIST_RULES = ("specific", "first")
DEFAULT_LIST_RULE = "specific"


class WorkflowError(RuntimeError):
    """The run cannot proceed (a missing Docker, binary, driver or harness)."""


# ---- the harness (R2) ---------------------------------------------------------------------


@dataclass
class Harness:
    make_app: Callable[..., Any]
    build_db: Callable[..., Any]
    login: Callable[..., Any] | None
    services: Mapping[str, Callable[..., Any]]
    runtime: Any
    source: str  # studio.toml | check.sh | harness.py
    file: str | None = None  # the file it came from, relative to the root
    database: str | None = None  # the database the project names (None: the extract's dialect)


def discover_harness(root: Path) -> tuple[Harness | None, str | None]:
    """(harness, reason): the app's harness found by :func:`check_invocation.discover`
    (``studio.toml`` ``[app]``, then the project's own check invocation, then a root
    ``harness.py``), or None and why the run is static. Nothing is executed but the imports."""
    from lattice.importer.check_invocation import discover

    d = discover(root)
    if not d.found:
        return None, d.reason or "no harness found"
    return (
        Harness(
            make_app=d.make_app,
            build_db=d.build_db,
            login=d.login,
            services=dict(d.services),
            runtime=d.runtime,
            source=d.source or "",
            file=d.file,
            database=d.database,
        ),
        None,
    )


def find_harness(root: Path) -> Harness | None:
    """The app's harness (:func:`discover_harness` without the reason)."""
    return discover_harness(root)[0]


# ---- databases ----------------------------------------------------------------------------


def _scrub(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, bool):
        return int(value)
    return value


def write_sqlite(dest: Path, data: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    """The rows of each table written to a SQLite file (untyped columns, in row order)."""
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(dest)
    try:
        for table in sorted(data):
            rows = data[table]
            if not rows:
                conn.execute(f'CREATE TABLE "{table}" ("_empty")')
                continue
            cols = list(rows[0])
            names = ", ".join('"' + c + '"' for c in cols)
            conn.execute(f'CREATE TABLE "{table}" ({names})')
            marks = ", ".join("?" for _ in cols)
            conn.executemany(
                f'INSERT INTO "{table}" VALUES ({marks})',
                [[_scrub(r[c]) for c in cols] for r in rows],
            )
        conn.commit()
    finally:
        conn.close()


class MysqlProvider:
    """One throwaway MySQL (``lattice-wf-*``): the seed built once, a clone per flow."""

    dialect = "mysql"

    def __init__(self, build_db: Callable[..., Any]) -> None:
        from lattice.spec import dbserver

        if not dbserver.docker_available():
            raise WorkflowError("the run needs Docker (not available)")
        self._saved = {
            k: v for k, v in os.environ.items() if k.startswith("LATTICE_") and k.endswith("_URL")
        }
        for k in self._saved:
            del os.environ[k]
        try:
            self.server, self.owned = dbserver.server_for(
                "mysql", None, name_prefix=CONTAINER_PREFIX
            )
        except Exception as err:
            os.environ.update(self._saved)
            raise WorkflowError(f"cannot start the MySQL server: {err}") from err
        self.dbs = dbserver.ServerDatabases(self.server, build_db)

    @property
    def url(self) -> str | None:
        return str(self.server.url)

    def fresh(self) -> Any:
        return self.dbs("seed")

    def release(self, ref: Any) -> None:
        with contextlib.suppress(Exception):
            self.dbs.release(ref)

    def read(self, ref: Any, table: str, order_by: str | None) -> list[dict[str, Any]]:
        from lattice.spec.db import open_db

        with open_db(ref) as conn:
            present = {t.lower(): t for t in conn.tables()}
            real = present.get(table.lower())
            return conn.read_table(real, order_by=order_by) if real else []

    def snapshot(
        self, ref: Any, tables: Sequence[str], keys: Mapping[str, str], dest: Path
    ) -> None:
        from lattice.spec.db import open_db

        data: dict[str, list[dict[str, Any]]] = {}
        with open_db(ref) as conn:
            present = {t.lower(): t for t in conn.tables()}
            for t in tables:
                real = present.get(t.lower())
                if real is not None:
                    data[t] = conn.read_table(real, order_by=keys.get(t))
        write_sqlite(dest, data)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.dbs.close()
        with contextlib.suppress(Exception):
            self.server.stop()
        os.environ.update(self._saved)


class SqliteProvider:
    """SQLite files: the seed built once into a scratch directory, a copy per flow."""

    dialect = "sqlite"
    url: str | None = None

    def __init__(self, build_db: Callable[..., Any], scratch: Path) -> None:
        from lattice.spec.check import as_db_factory

        self.dir = scratch / "sqlite"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seed = self.dir / "seed.db"
        as_db_factory(build_db)("seed", self.seed)
        self._n = 0

    def fresh(self) -> Any:
        self._n += 1
        path = self.dir / f"flow-{self._n}.db"
        shutil.copyfile(self.seed, path)
        return path

    def release(self, ref: Any) -> None:
        with contextlib.suppress(OSError):
            Path(ref).unlink()

    def read(self, ref: Any, table: str, order_by: str | None) -> list[dict[str, Any]]:
        conn = sqlite3.connect(f"file:{ref}?mode=ro", uri=True)
        try:
            sql = f'SELECT * FROM "{table}"' + (f' ORDER BY "{order_by}"' if order_by else "")
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def snapshot(
        self, ref: Any, tables: Sequence[str], keys: Mapping[str, str], dest: Path
    ) -> None:
        src = sqlite3.connect(f"file:{ref}?mode=ro", uri=True)
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- one flow's live run (L) --------------------------------------------------------------


@dataclass
class StepRun:
    """A request that was made (the last attempt), or planned (a static run)."""

    spec: W.StepSpec
    route: W.RouteV
    caller: str | None
    plan_index: int | None = None
    path: str = ""
    body: dict[str, Any] | None = None
    status: int | None = None
    response: Any = None
    sources: dict[str, str] = field(default_factory=dict)
    attempts: int = 0
    refusal: dict[str, Any] | None = None
    bound: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    reason: str = ""
    key_values: dict[str, Any] = field(default_factory=dict)  # table -> client key sent
    id_values: dict[str, Any] = field(default_factory=dict)  # table -> id taken from it
    id_sources: dict[str, str] = field(default_factory=dict)
    expected_refusal: bool = False
    inserted: str | None = None


@dataclass
class FlowOutcome:
    runs: list[StepRun] = field(default_factory=list)
    ok: bool = False
    failed_at: int | None = None
    reason: str = ""
    snapshots: list[Path] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    sweeps: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    ref: Any = None


def code_statuses(route: W.RouteV) -> list[int]:
    """The statuses the code gives evidence for: the 2xx it writes, and its refusals."""
    return sorted(set(route.status_codes) | set(route.refusals))


class Executor:
    """Runs flows live on fresh databases of one provider."""

    def __init__(
        self,
        view: W.AppView,
        harness: Harness,
        provider: Any,
        runtime: Any,
        users: Sequence[str],
        scratch: Path,
        planner: W.Planner,
        sweeps: list[dict[str, Any]],
        list_rule: str = DEFAULT_LIST_RULE,
    ) -> None:
        self.view = view
        self.harness = harness
        self.provider = provider
        self.runtime = runtime
        self.users = list(users)
        self.scratch = scratch
        self.planner = planner
        self.sweeps = sweeps
        self.list_rule = list_rule
        self.tables = [t["table"] for t in view.x["tables"]]
        self.keys = dict(view.pk)
        self._n = 0
        self.attempts_total = 0
        self._seed_ids: dict[str, list[Any]] = {}

    # -- plumbing

    def _auth(self, transport: Any, tokens: dict[str, str], handle: str | None) -> dict[str, str]:
        if handle is None:
            return {}
        if handle not in tokens:
            tokens[handle] = self.runtime.auth.token(transport, handle, self.harness.login)
        return dict(self.runtime.auth.present(transport, tokens[handle]))

    def _snap(self, out: FlowOutcome, request: dict[str, Any] | None) -> None:
        self._n += 1
        dest = self.scratch / f"snap-{self._n:07d}.db"
        self.provider.snapshot(out.ref, self.tables, self.keys, dest)
        out.snapshots.append(dest)
        if request is not None:
            out.requests.append(request)

    @staticmethod
    def render(route: W.RouteV, values: Mapping[str, Any]) -> str:
        """The path with its parameters filled; a parameter with no value is a KeyError (it is
        never filled with a guess: R2-D4, :meth:`_step` refuses the step first)."""
        path = route.path
        for name in route.path_params:
            path = path.replace("{" + name + "}", str(values[name]))
        return path

    def _producer(self, out: FlowOutcome, table: str) -> StepRun | None:
        for rec in reversed(out.runs):
            if rec.ok and rec.status is not None and 200 <= rec.status < 300:
                if table in self.view.created_tables(rec.route):
                    return rec
        return None

    # -- ids (D2): client, response, list, seed

    def _resolve(
        self,
        out: FlowOutcome,
        table: str,
        caller: str | None,
        ctx: W.ValueCtx,
        transport: Any,
        tokens: dict[str, str],
    ) -> tuple[Any, str]:
        prod = self._producer(out, table)
        if prod is None:
            return None, "no earlier step creates it"
        if table in prod.id_values:
            return prod.id_values[table], prod.id_sources[table]
        if table in prod.key_values:
            prod.id_values[table], prod.id_sources[table] = prod.key_values[table], "client"
            return prod.id_values[table], "client"
        # the answer's id is the id of the first table the route creates (a row it creates as a
        # side effect, a nudge after a check-in, is not what the answer holds)
        got = W.extract_id(prod.response)
        first = self.view.created_tables(prod.route)[:1]
        if got is not None and first == [table]:
            prod.id_values[table], prod.id_sources[table] = got, "response"
            return got, "response"
        struct = self.view.struct_of.get(table)
        lists = [
            r
            for r in self.view.routes
            if r.method == "GET" and not r.path_params and struct in r.tables
        ]
        if self.list_rule == "specific":
            lists.sort(key=lambda r: (len(r.tables), r.index))
        for r in lists[:1]:
            who = None if r.public else caller
            headers = self._auth(transport, tokens, who)
            ans = transport.request("GET", r.path, headers=headers)
            self._snap(out, {"method": "GET", "path": r.path, "status": ans.status})
            ok = 200 <= ans.status < 300
            found = W.list_id(ans.body) if ok else None
            spec = W.StepSpec(r.key, who, "setup", inserted="id_source")
            rec = StepRun(spec, r, who, None, r.path, None, ans.status, ans.body if ok else None)
            rec.attempts, rec.ok, rec.inserted = 1, ok and found is not None, "id_source"
            rec.sources = {}
            out.runs.append(rec)
            if found is not None:
                prod.id_values[table], prod.id_sources[table] = found, "list"
                return found, "list"
        first_ids = self._seed_ids.get(table)
        if first_ids:
            prod.id_values[table], prod.id_sources[table] = first_ids[0], "seed"
            return first_ids[0], "seed"
        return None, "no id: the producer's answer has none and no list route yields one"

    # -- a flow

    def run(self, flow: W.Flow) -> FlowOutcome:
        view = self.view
        tag = flow.ident(view)[3:9]
        ctx = W.ValueCtx(tag)
        out = FlowOutcome()
        if any(not view.by_key[s.route].public for s in flow.steps) and not self.users:
            out.reason = "no declared user (--user): the flow has Member steps"
            out.attempts = 0
            return out
        out.ref = self.provider.fresh()
        app = self.harness.make_app(out.ref)
        tokens: dict[str, str] = {}
        caller_map: dict[str, str] = {}
        state = {"preconditions": 0}
        try:
            with self.runtime.open(app) as transport:
                self._snap(out, None)
                self._seed_ids = {}
                for t, pk in view.pk.items():
                    ids = sorted(
                        r[pk] for r in self.provider.read(out.ref, t, pk) if r.get(pk) is not None
                    )
                    if ids:
                        self._seed_ids[t] = ids
                n = len(flow.steps)
                for i, spec in enumerate(flow.steps):
                    caller = caller_map.get(spec.caller or "", spec.caller)
                    rec = StepRun(spec, view.by_key[spec.route], caller, i)
                    self._step(
                        flow, rec, out, ctx, transport, tokens, caller_map, i == n - 1, state
                    )
                    out.runs.append(rec)
                    if not rec.ok:
                        out.failed_at = len(out.runs) - 1
                        out.reason = rec.reason
                        break
                else:
                    out.ok = True
                if out.ok:
                    self._sweep_due(flow, out)
        finally:
            with contextlib.suppress(Exception):
                self.runtime.close(app)
            self.provider.release(out.ref)
        return out

    def _step(
        self,
        flow: W.Flow,
        rec: StepRun,
        out: FlowOutcome,
        ctx: W.ValueCtx,
        transport: Any,
        tokens: dict[str, str],
        caller_map: dict[str, str],
        is_last: bool,
        state: dict[str, int],
    ) -> None:
        """One step with the repair rules of D4: ``rec.ok`` when its status is in the code
        evidence (a 2xx; a refusal too for the last step of a guard flow)."""
        view, route, spec = self.view, rec.route, rec.spec
        path_values: dict[str, Any] = {}
        body_bound: dict[str, Any] = {}
        for b in spec.bindings:
            value, src = self._resolve(out, b.table, rec.caller, ctx, transport, tokens)
            if value is None:
                rec.reason = f"{b.param}: {src}"
                return
            (path_values if b.source == "path" else body_bound)[b.param] = value
            prod = self._producer(out, b.table)
            rec.bound.append(
                {
                    "param": b.param,
                    "source": b.source,
                    "value": value,
                    "table": b.table,
                    "id_source": src,
                    "from_run": out.runs.index(prod) if prod in out.runs else None,
                }
            )
        for name in route.path_params:
            if name not in path_values:
                # R2-D4: no silent id. The step is not sent and the flow fails, naming it.
                rec.reason = f"unbound path param {name}: no producer"
                return
        rec.path = self.render(route, path_values)
        creates = view.creates.get(route.key, [])
        key_fields = {c["key_from"] for c in creates if c["key_from"] != "server"}
        fields = [p for p in W.required_fields(route, None) if p["name"] not in body_bound]
        have = {p["name"] for p in fields}
        fields += [
            p
            for p in route.params
            if p["source"] == "body" and p["name"] in key_fields and p["name"] not in have
        ]
        fields.sort(key=lambda p: p["name"])
        cands: dict[str, list[tuple[Any, str]]] = {}
        idx: dict[str, int] = {}
        ctx.new_step()
        for p in fields:
            lits = view.literals.get((route.key, "body", p["name"]), [])
            cands[p["name"]] = W.field_candidates(p, lits, ctx, key=p["name"] in key_fields)
            idx[p["name"]] = 0
        evidence = code_statuses(route)
        tries = 0
        tried: list[str | None] = [rec.caller]
        expects_refusal = flow.kind in REFUSAL_KINDS and is_last
        while True:
            body: dict[str, Any] | None = None
            if fields or body_bound or route.method in ("POST", "PUT", "PATCH"):
                body = dict(body_bound)
                for p in fields:
                    c = cands[p["name"]]
                    if c:
                        i = min(idx[p["name"]], len(c) - 1)
                        body[p["name"]] = ctx.pick(p["name"], i, c[i][0])
                body = {k: body[k] for k in sorted(body)}
            headers = self._auth(transport, tokens, rec.caller)
            rec.attempts += 1
            tries += 1
            out.attempts += 1
            self.attempts_total += 1
            ans = transport.request(route.method, rec.path, json=body, headers=headers)
            self._snap(out, {"method": route.method, "path": rec.path, "status": ans.status})
            rec.status, rec.response, rec.body = ans.status, ans.body, body
            success = 200 <= ans.status < 300
            rec.refusal = (
                None if success else {"status": ans.status, "code": W.refusal_code(ans.body)}
            )
            if ans.status in evidence and (success or expects_refusal):
                rec.ok, rec.reason = True, ""
                rec.expected_refusal = expects_refusal and not success
                for p in fields:
                    c = cands[p["name"]]
                    if c:
                        rec.sources[p["name"]] = c[min(idx[p["name"]], len(c) - 1)][1]
                for c in creates:
                    if c["key_from"] != "server" and body and c["key_from"] in body:
                        rec.key_values[c["table"]] = body[c["key_from"]]
                if success:
                    if body is not None:
                        ctx.requests.append(body)
                    if ans.body is not None:
                        ctx.responses.append(ans.body)
                return
            rec.reason = (
                f"status {ans.status} is not in the code evidence {evidence}"
                if ans.status not in evidence
                else f"status {ans.status}"
            )
            if out.attempts >= W.ATTEMPTS_PER_FLOW or tries >= W.ATTEMPTS_PER_STEP:
                return
            if ans.status == 422 and idx:
                named = W.error_field(ans.body, body or {})
                order = ([named] if named in idx else []) + sorted(idx)
                for name in order:
                    if idx[name] + 1 < len(cands[name]):
                        idx[name] += 1
                        break
                else:
                    return
                continue
            if ans.status in (401, 403) and not route.public:
                nxt = [u for u in self.users if u not in tried]
                if not nxt:
                    return
                old = rec.caller
                tried.append(nxt[0])
                rec.caller = nxt[0]
                if old is not None:
                    caller_map[old] = nxt[0]
                continue
            if 400 <= ans.status < 500 and not (is_last and ans.status in (401, 404)):
                if self._precondition(flow, rec, out, ctx, transport, tokens, caller_map, state):
                    continue
            return

    def _precondition(
        self,
        flow: W.Flow,
        rec: StepRun,
        out: FlowOutcome,
        ctx: W.ValueCtx,
        transport: Any,
        tokens: dict[str, str],
        caller_map: dict[str, str],
        state: dict[str, int],
    ) -> bool:
        """Run the next action route on a table the step consumes before it (D4); True
        when one was inserted and answered 2xx."""
        tables = [b.table for b in rec.spec.bindings if b.source == "path"]
        for table in tables:
            for key in self.planner._action_routes(table):
                if state["preconditions"] >= W.PRECONDITIONS:
                    return False
                if key == rec.route.key or key in {r.route.key for r in out.runs if r.inserted}:
                    continue
                r = self.view.by_key[key]
                binds = [
                    W.Binding(e.param, e.source, e.table, 0)
                    for e in self.planner.by_consumer.get(key, [])
                ]
                spec = W.StepSpec(key, self.planner.caller(r), "setup", binds, "precondition")
                pre = StepRun(spec, r, caller_map.get(spec.caller or "", spec.caller))
                pre.inserted = "precondition"
                self._step(
                    dataclasses.replace(flow, kind="pair"),
                    pre,
                    out,
                    ctx,
                    transport,
                    tokens,
                    caller_map,
                    False,
                    {"preconditions": state["preconditions"]},
                )
                if pre.ok and pre.status is not None and 200 <= pre.status < 300:
                    out.runs.append(pre)
                    state["preconditions"] += 1
                    return True
        return False

    # -- sweeps (D7)

    def _sweep_due(self, flow: W.Flow, out: FlowOutcome) -> None:
        for sweep in self.sweeps:
            if sweep["done"]:
                continue
            m = sweep["machine"]
            if not self.provider.read(out.ref, m["table"], self.keys.get(m["table"])):
                continue
            sweep["done"] = True
            sweep.update(self._sweep(sweep["service"], m, out))
            sweep["flow"] = flow
            sweep["outcome"] = out

    def _sweep(self, service: str, machine: dict[str, Any], out: FlowOutcome) -> dict[str, Any]:
        from lattice.spec.services import start_service

        table, col = machine["table"], machine["column"]
        key = self.keys.get(table)
        before = self.provider.read(out.ref, table, key)
        svc = start_service(service, self.harness.services[service](out.ref))
        changed = False
        last = json.dumps(before, sort_keys=True, default=str)
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < SWEEP_SECONDS:
                time.sleep(SWEEP_POLL_S)
                self._snap(out, {"method": "SERVICE", "path": service, "status": 0})
                now = json.dumps(
                    self.provider.read(out.ref, table, key), sort_keys=True, default=str
                )
                if now != last:
                    changed, last = True, now
                elif changed:
                    break
        finally:
            svc.stop()
        after = self.provider.read(out.ref, table, key)
        old = {r.get(key): r for r in before} if key else {}
        moved = [
            {"key": r.get(key), "to": r.get(col)}
            for r in after
            if key and r.get(key) in old and old[r.get(key)].get(col) != r.get(col)
        ]
        return {"moved": bool(moved), "rows": moved[:1], "key": key}


# ---- static steps (no run) ----------------------------------------------------------------


def static_outcome(view: W.AppView, flow: W.Flow) -> FlowOutcome:
    """The steps of a flow with D4's first candidates and no run: nothing is sent."""
    ctx = W.ValueCtx(flow.ident(view)[3:9])
    out = FlowOutcome()
    for i, spec in enumerate(flow.steps):
        route = view.by_key[spec.route]
        rec = StepRun(spec, route, spec.caller, i)
        path_values: dict[str, Any] = {}
        body_bound: dict[str, Any] = {}
        for b in spec.bindings:
            prod = next(
                (r for r in reversed(out.runs) if b.table in view.created_tables(r.route)),
                None,
            )
            value = prod.key_values.get(b.table) if prod else None
            (path_values if b.source == "path" else body_bound)[b.param] = value
            rec.bound.append(
                {
                    "param": b.param,
                    "source": b.source,
                    "value": value,
                    "table": b.table,
                    "id_source": "client" if value is not None else "response",
                    "from_run": out.runs.index(prod) if prod else None,
                }
            )
        creates = view.creates.get(route.key, [])
        key_fields = {c["key_from"] for c in creates if c["key_from"] != "server"}
        fields = [p for p in W.required_fields(route, None) if p["name"] not in body_bound]
        have = {p["name"] for p in fields}
        fields += [
            p
            for p in route.params
            if p["source"] == "body" and p["name"] in key_fields and p["name"] not in have
        ]
        fields.sort(key=lambda p: p["name"])
        body = {k: v for k, v in body_bound.items() if v is not None}
        ctx.new_step()
        for p in fields:
            lits = view.literals.get((route.key, "body", p["name"]), [])
            c = W.field_candidates(p, lits, ctx, key=p["name"] in key_fields)
            if c:
                body[p["name"]] = ctx.pick(p["name"], 0, c[0][0])
                rec.sources[p["name"]] = c[0][1]
        if fields or body_bound or route.method in ("POST", "PUT", "PATCH"):
            rec.body = {k: body[k] for k in sorted(body)}
        for c in creates:
            if c["key_from"] != "server" and rec.body and c["key_from"] in rec.body:
                rec.key_values[c["table"]] = rec.body[c["key_from"]]
        rec.path = route.path
        for name in route.path_params:
            val = next((v for k, v in path_values.items() if k == name and v is not None), None)
            if val is not None:
                rec.path = rec.path.replace("{" + name + "}", str(val))
        if rec.body:
            ctx.requests.append(rec.body)
        out.runs.append(rec)
    return out


# ---- freeze, replay, machines -------------------------------------------------------------


def final_id(view: W.AppView, flow: W.Flow, out: FlowOutcome | None) -> str:
    """The flow's id over the requests it made (inserted steps included)."""
    if out is None or not out.runs:
        return flow.ident(view)
    sk = [r.route.skeleton for r in out.runs]
    callers = [r.caller or "-" for r in out.runs]
    raw = "|".join((flow.kind, " > ".join(sk), ",".join(callers)))
    return "wf-" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def scenario_of(name: str, out: FlowOutcome, awaits: Sequence[Mapping[str, Any]] = ()) -> Any:
    from lattice.spec.model import Await, Scenario, Step

    steps: list[Any] = []
    for rec in out.runs:
        if rec.status is None or not rec.ok:
            break
        steps.append(
            Step(
                rec.route.method,
                rec.path,
                user=rec.caller,
                json=rec.body,
                expect_status=int(rec.status),
            )
        )
    steps += [Await(a["service"], a["expect_db"], within_s=a["within_s"]) for a in awaits]
    return Scenario(name, steps=tuple(steps))


def with_one_of(profile: Any, struct: str, column: str, states: Sequence[str]) -> Any:
    """The profile with ``column`` of ``struct`` an enum of ``states``."""
    api = profile.api
    schema = api.tables[struct]
    if column in {c for c, _ in schema.one_of}:
        return profile
    new = dataclasses.replace(schema, one_of=(*schema.one_of, (column, tuple(states))))
    return dataclasses.replace(
        profile, api=dataclasses.replace(api, tables={**api.tables, struct: new})
    )


def scan_snapshots(
    paths: Sequence[Sequence[Path]], table: str, key: str | None, column: str
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """From the consecutive snapshots of each run: how often each (from, to) move of
    ``table.column`` is seen (rows matched by ``key``), and how often each state is the one
    a new row appears in."""
    moves: dict[tuple[str, str], int] = {}
    created: dict[str, int] = {}
    if key is None:
        return moves, created

    def rows(path: Path) -> dict[Any, Any] | None:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return dict(conn.execute(f'SELECT "{key}", "{column}" FROM "{table}"').fetchall())
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    for snaps in paths:
        prev: dict[Any, Any] | None = None
        for p in snaps:
            cur = rows(p)
            if prev is not None and cur is not None:
                for k, v in cur.items():
                    if k not in prev:
                        created[str(v)] = created.get(str(v), 0) + 1
                    elif prev[k] != v:
                        pair = (str(prev[k]), str(v))
                        moves[pair] = moves.get(pair, 0) + 1
            prev = cur if cur is not None else prev
    return moves, created


# ---- the pipeline -------------------------------------------------------------------------


@dataclass
class Result:
    doc: dict[str, Any]
    wall: dict[str, float]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _evidence(view: W.AppView, rec: StepRun) -> list[dict[str, str]]:
    r = rec.route
    ev = [{"what": "route", "at": f"{r.file}:{r.line}"}]
    if r.handler:
        ev.append({"what": "handler", "at": f"{r.handler['file']}:{r.handler['line']}"})
    for b in rec.spec.bindings:
        for use in view.uses.get(r.key, []):
            if (
                use["how"] == "compare"
                and use["param"] == b.param
                and use["source"] == b.source
                and use["table"] == b.table
                and view.pk.get(b.table) == use["column"]
            ):
                item = {"what": "param_use", "at": f"{use['file']}:{use['line']}"}
                if item not in ev:
                    ev.append(item)
    for c in view.creates.get(r.key, []):
        item = {"what": "create", "at": f"{c['file']}:{c['line']}"}
        if item not in ev:
            ev.append(item)
    if rec.refusal and rec.refusal["status"] is not None:
        for s in r.raises:
            if s["status"] == rec.refusal["status"]:
                item = {"what": "raise", "at": f"{s['file']}:{s['line']}"}
                if item not in ev:
                    ev.append(item)
    return ev


class Pipeline:
    """The whole run on one app root."""

    def __init__(
        self,
        root: Path,
        out_dir: Path,
        *,
        package: str = ".",
        users: Sequence[str] = (),
        static: bool = False,
        max_flows: int = W.MAX_FLOWS,
        max_steps: int = W.MAX_STEPS,
        services: bool = True,
        database: str = "auto",
        list_rule: str = DEFAULT_LIST_RULE,
    ) -> None:
        self.root = Path(root).resolve()
        self.out_dir = Path(out_dir).resolve()
        self.package = package
        self.users = list(users)
        self.static = static
        self.max_flows, self.max_steps = max_flows, max_steps
        self.services = services
        self.database = database
        self.list_rule = list_rule
        self.wall: dict[str, float] = {}
        try:
            self.out_dir.relative_to(self.root)
        except ValueError:
            pass
        else:
            raise WorkflowError(f"--out {self.out_dir} is under the target {self.root}")

    # -- extraction

    def extract(self) -> tuple[dict[str, Any], str, str]:
        from lattice.importer.frontends.go_gin import GoGinFrontEnd

        fe = GoGinFrontEnd()
        base = fe.extract_json(self.root, self.package)
        raw = json.dumps(base, indent=2, sort_keys=False).encode()
        wf = fe.extract_json(self.root, self.package, workflow=True)
        raw_wf = json.dumps(wf, indent=2, sort_keys=False).encode()
        return wf, _sha(raw), _sha(raw_wf)

    # -- the run

    def run(self) -> Result:
        t0 = time.perf_counter()
        x, sha, sha_wf = self.extract()
        self.wall["extract"] = round(time.perf_counter() - t0, 3)
        t1 = time.perf_counter()
        plan = W.plan(x, self.users, max_flows=self.max_flows, max_steps=self.max_steps)
        self.wall["plan"] = round(time.perf_counter() - t1, 3)
        view = plan.view
        harness: Harness | None = None
        reason: str | None = "static run requested (--static)"
        dialect: str | None = None
        if not self.static:
            harness, reason = discover_harness(self.root)
            if harness is not None:
                from lattice.importer.check_invocation import choose_database

                dialect, why = choose_database(
                    harness.database, self.database, x.get("dialect") or None
                )
                if dialect is None:
                    harness, reason = None, f"{harness.file}: {why}"
        doc: dict[str, Any] = {"schema": W.SCHEMA}
        doc["app"] = {
            "module": x["module"],
            "framework": "gin",
            "extract_sha256": sha,
            "workflow_extract_sha256": sha_wf,
        }
        mode = "observed" if harness is not None and not self.static else "static"
        self.scratch = self.out_dir / ".scratch"
        pl = W.Planner(view, plan.edges, self.users)
        flows_out: list[dict[str, Any]]
        machines_out: list[dict[str, Any]]
        id_sources: dict[tuple[str, str, str], str] = {}
        swept: list[str] = []
        if mode == "static" or harness is None:
            flows_out = self._static_flows(plan)
            machines_out = self._machines(plan, None, [], None, None)
        else:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self.scratch.mkdir(parents=True, exist_ok=True)
            try:
                flows_out, machines_out, id_sources, swept = self._dynamic(
                    plan, pl, harness, dialect or "mysql"
                )
            finally:
                shutil.rmtree(self.scratch, ignore_errors=True)
        observed = harness is not None and mode == "observed"
        doc["run"] = {
            "mode": mode,
            "harness": harness.source if harness is not None and observed else None,
            "harness_file": harness.file if harness is not None and observed else None,
            "harness_reason": None if observed else reason,
            "users": list(self.users),
            "services_swept": sorted(swept),
            "caps": {"max_flows": self.max_flows, "max_steps": self.max_steps},
            "list_rule": self.list_rule,
            "database": dialect if observed else None,
        }
        doc["edges"] = [
            W.edge_row(
                view,
                e,
                id_sources.get(
                    (e.consumer, e.param, e.table),
                    "client" if e.key_from != "server" else "response",
                ),
            )
            for e in plan.edges
        ]
        doc["machines"] = machines_out
        doc["flows"] = flows_out
        doc["unread"] = sorted(
            [dict(u) for u in view.unread]
            + [dict(u) for u in x.get("workflow_unread", [])]
            + [
                {
                    "file": r.file,
                    "line": r.line,
                    "what": "route",
                    "reason": f"{r.key} is registered under `{r.condition}`: never used",
                }
                for r in view.excluded_conditional
            ],
            key=lambda u: (u["file"], u["line"], u["what"], u["reason"]),
        )
        doc["capped"] = plan.capped
        doc["summary"] = W.summary(flows_out, len(plan.edges), len(machines_out))
        self.wall["total"] = round(time.perf_counter() - t0, 3)
        return Result(doc, self.wall)

    # -- static

    def _static_flows(self, plan: W.Plan) -> list[dict[str, Any]]:
        out = []
        for f in plan.flows:
            outcome = static_outcome(plan.view, f)
            out.append(
                self._flow_json(
                    plan,
                    f,
                    outcome,
                    "unverified",
                    "static",
                    "static run: the flow was not executed",
                    False,
                )
            )
        return self._dedupe(out)

    # -- the dynamic run

    def _dynamic(
        self, plan: W.Plan, pl: W.Planner, h: Harness, dialect: str
    ) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str, str], str], list[str]
    ]:
        view = plan.view
        x = view.x
        from lattice.importer.frontends.go_gin import GoGinFrontEnd, model_from_extract

        runtime = h.runtime
        if runtime is None:
            runtime = GoGinFrontEnd().runtime(self.root, self.package, model=model_from_extract(x))
        provider: Any
        if dialect == "sqlite":
            provider = SqliteProvider(h.build_db, self.scratch)
        else:
            provider = MysqlProvider(h.build_db)
        t0 = time.perf_counter()
        try:
            sweeps: list[dict[str, Any]] = []
            if self.services and h.services:
                for m in plan.machines:
                    for name in sorted(h.services):
                        sweeps.append({"service": name, "machine": m, "done": False})
            ex = Executor(
                view, h, provider, runtime, self.users, self.scratch, pl, sweeps, self.list_rule
            )
            queue = list(plan.flows)
            outcomes: list[tuple[W.Flow, FlowOutcome]] = []
            seen_ids: set[str] = set()
            login_keys: set[str] | None = None
            i = 0
            while i < len(queue):
                flow = queue[i]
                i += 1
                try:
                    outcome = ex.run(flow)
                except Exception as err:  # noqa: BLE001 -- one flow's error is that flow's result
                    if not any(o.ok for _, o in outcomes) and _cannot_start(err):
                        raise WorkflowError(f"the app does not start: {err}") from err
                    outcome = FlowOutcome(reason=f"run error: {type(err).__name__}: {err}"[:300])
                    outcome.failed_at = None
                outcomes.append((flow, outcome))
                if flow.kind == "account" and outcome.ok and len(outcome.runs) >= 2:
                    ans = outcome.runs[-1].response
                    if isinstance(ans, Mapping):
                        login_keys = set(ans)
                        queue += self._account_extensions(plan, flow, login_keys, queue)
                if not outcome.ok and outcome.failed_at is not None:
                    g = self._guard_flow(plan, flow, outcome)
                    if g is not None and g.ident(view) not in seen_ids:
                        seen_ids.add(g.ident(view))
                        queue.append(g)
            self.wall["L"] = round(time.perf_counter() - t0, 3)
            # services: a flow after a sweep that moved the column
            extra: list[tuple[W.Flow, FlowOutcome, list[dict[str, Any]]]] = []
            for sw in sweeps:
                if sw.get("done") and sw.get("moved"):
                    m = sw["machine"]
                    row = sw["rows"][0]
                    aw = {
                        "service": sw["service"],
                        "expect_db": {
                            m["table"]: {
                                "where": {sw["key"]: row["key"]},
                                "equals": {m["column"]: row["to"]},
                            }
                        },
                        "within_s": AWAIT_WITHIN_S,
                    }
                    base: W.Flow = sw["flow"]
                    sflow = dataclasses.replace(
                        base, kind="service", machine_refs=[m["id"]], merged_kinds=[], edge_refs=[]
                    )
                    extra.append((sflow, sw["outcome"], [aw]))
            t2 = time.perf_counter()
            verdicts = self._replay(plan, h, runtime, provider, dialect, outcomes, extra)
            self.wall["R"] = round(time.perf_counter() - t2, 3)
            t3 = time.perf_counter()
            machines = self._machines(plan, outcomes, sweeps, provider, runtime)
            self.wall["machines"] = round(time.perf_counter() - t3, 3)
            flows_json: list[dict[str, Any]] = []
            for flow, outcome in outcomes:
                fid = final_id(view, flow, outcome if outcome.runs else None)
                status, reason = self._status(outcome, verdicts.get(fid), flow)
                flows_json.append(
                    self._flow_json(
                        plan,
                        flow,
                        outcome,
                        status,
                        "observed",
                        reason,
                        True,
                        verified=status == "verified",
                    )
                )
            for flow, outcome, aws in extra:
                fid = (
                    "wf-"
                    + hashlib.sha256(
                        (flow.kind + "|" + final_id(view, flow, outcome)).encode()
                    ).hexdigest()[:10]
                )
                v = verdicts.get(fid)
                status = "verified" if v is True else "unverified"
                reason = "" if v is True else (v or "the replay did not run")
                flows_json.append(
                    self._flow_json(
                        plan,
                        flow,
                        outcome,
                        status,
                        "observed",
                        reason,
                        True,
                        verified=v is True,
                        awaits=aws,
                        ident=fid,
                    )
                )
            sources: dict[tuple[str, str, str], str] = {}
            for _, outcome in outcomes:
                for rec in outcome.runs:
                    for b in rec.bound:
                        sources.setdefault((rec.route.key, b["param"], b["table"]), b["id_source"])
            swept = sorted({sw["service"] for sw in sweeps if sw.get("done")})
            return self._dedupe(flows_json), machines, sources, swept
        finally:
            provider.close()

    # -- extensions

    def _account_extensions(
        self, plan: W.Plan, flow: W.Flow, keys: set[str], queue: list[W.Flow]
    ) -> list[W.Flow]:
        """The public POST routes whose required body keys are all keys of the login answer
        (a refresh), run after the account flow."""
        view = plan.view
        names = {s.route for s in flow.steps}
        out: list[W.Flow] = []
        for r in view.routes:
            if r.method != "POST" or not r.public or r.key in names:
                continue
            need = [p["name"] for p in r.params if p["source"] == "body" and p["required"]]
            if not need or not set(need) <= keys:
                continue
            steps = [dataclasses.replace(s, bindings=[]) for s in flow.steps]
            steps.append(W.StepSpec(r.key, None, "focus"))
            for s in steps[:-1]:
                s.role = "setup"
            out.append(W.Flow("account", steps, len(steps) - 1))
        known = {q.ident(view) for q in queue}
        return [f for f in out if f.ident(view) not in known]

    def _guard_flow(self, plan: W.Plan, flow: W.Flow, out: FlowOutcome) -> W.Flow | None:
        """A failed flow whose failing step answered one of its route's refusals: the prefix
        up to that step, expecting it (D5)."""
        if out.failed_at is None or flow.kind in ("guard", "owner_guard"):
            return None
        rec = out.runs[out.failed_at]
        st = rec.status
        if st is None or st < 400 or st not in rec.route.refusals:
            return None
        steps = [
            dataclasses.replace(
                r.spec, bindings=[dataclasses.replace(b) for b in r.spec.bindings], caller=r.caller
            )
            for r in out.runs[: out.failed_at + 1]
            if r.inserted != "id_source"
        ]
        return W.Flow("guard", steps, max(len(steps) - 2, 0))

    # -- replay

    def _profile(self, x: dict[str, Any]) -> tuple[Any, Any, str | None]:
        """(model, profile, error) of the extraction."""
        try:
            from lattice.importer.frontends.go_gin import model_from_extract
            from lattice.importer.lift import load_profile
            from lattice.importer.profile_gen import generate_profile

            model = model_from_extract(x)
            gen = generate_profile(model)
            module = load_profile(gen, "lattice_wf_profile")
            return model, module.profile(), None
        except Exception as err:  # noqa: BLE001 -- reported, the run goes on
            return None, None, f"{type(err).__name__}: {err}"

    def _replay(
        self,
        plan: W.Plan,
        h: Harness,
        runtime: Any,
        provider: Any,
        dialect: str,
        outcomes: list[tuple[W.Flow, FlowOutcome]],
        extra: list[tuple[W.Flow, FlowOutcome, list[dict[str, Any]]]],
    ) -> dict[str, Any]:
        """Verdicts per flow id: True (no finding), or the reason string."""
        from lattice.importer.emit_spec import build_spec
        from lattice.spec.check import check
        from lattice.spec.model import Service

        view = plan.view
        model, profile, err = self._profile(view.x)
        verdicts: dict[str, Any] = {}
        scenarios: list[Any] = []
        names: set[str] = set()
        for flow, outcome in outcomes:
            if not outcome.ok:
                continue
            fid = final_id(view, flow, outcome)
            if fid in names:
                continue
            names.add(fid)
            scenarios.append(scenario_of(fid, outcome))
        for flow, outcome, aws in extra:
            fid = (
                "wf-"
                + hashlib.sha256(
                    (flow.kind + "|" + final_id(view, flow, outcome)).encode()
                ).hexdigest()[:10]
            )
            names.add(fid)
            scenarios.append(scenario_of(fid, outcome, aws))
        if not scenarios:
            return verdicts
        if err is not None:
            for s in scenarios:
                verdicts[s.name] = f"the replay could not run: {err}"
            return verdicts
        spec = build_spec(model, profile, users={})
        used = sorted({r.caller for _, o in outcomes for r in o.runs if r.caller})
        spec = dataclasses.replace(
            spec,
            scenarios=tuple(scenarios),
            invariants=(),
            machines=(),
            users={u: (u, u) for u in used},
            services=tuple(Service(n) for n in sorted(h.services)),
        )
        try:
            report = check(
                spec,
                h.make_app,
                h.build_db,
                layers="D",
                runtime=runtime,
                login=h.login,
                services=dict(h.services) or None,
                database=dialect,
                database_url=provider.url if dialect != "sqlite" else None,
            )
        except Exception as e:  # noqa: BLE001 -- every flow stays unverified
            for s in scenarios:
                verdicts[s.name] = f"the replay could not run: {type(e).__name__}: {e}"
            return verdicts
        for s in scenarios:
            verdicts[s.name] = True
        for f in report.findings:
            if f.layer != "D" or not f.rule.startswith("scenario: "):
                continue
            name = f.rule[len("scenario: ") :]
            if verdicts.get(name) is True:
                what = f.details.get("problems") or f.details.get("error") or f.details
                step = (f.case or "").rsplit(" / ", 1)[-1]
                verdicts[name] = f"replay failed at {step}: {_short(what)}"
        return verdicts

    # -- status

    def _status(self, outcome: FlowOutcome, verdict: Any, flow: W.Flow) -> tuple[str, str]:
        if not outcome.ok:
            if outcome.failed_at is None:
                if outcome.reason.startswith("run error"):
                    return "failed", outcome.reason
                return "unverified", outcome.reason or "not run"
            rec = outcome.runs[outcome.failed_at]
            if rec.status is None:
                return (
                    "failed",
                    f"step {outcome.failed_at} {rec.route.key} was not sent: {rec.reason}",
                )
            code = (rec.refusal or {}).get("code")
            return "failed", (
                f"step {outcome.failed_at} {rec.route.key} answered {rec.status}"
                + (f" ({code})" if code else "")
                + f": {rec.reason}"
            )
        if verdict is True:
            return "verified", ""
        return "unverified", str(verdict or "the replay did not run")

    # -- machines

    def _machines(
        self,
        plan: W.Plan,
        outcomes: list[tuple[W.Flow, FlowOutcome]] | None,
        sweeps: list[dict[str, Any]],
        provider: Any,
        runtime: Any,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        mined: dict[str, Any] = {}
        if outcomes:
            mined = self._mine(plan, outcomes)
        for m in plan.machines:
            edges = [dict(e) for e in m["edges"]]
            for e in edges:
                e.pop("roots", None)
            info = mined.get(m["id"], {})
            counts: dict[tuple[str, str], int] = info.get("counts", {})
            observed_edges = info.get("observed", set())
            status = "static"
            if outcomes is None:
                status = "static"
            elif info.get("refuted"):
                status = "refuted"
            elif m["static"]:
                hit = sum(1 for e in edges if counts.get((e["from"], e["to"]), 0) > 0)
                status = (
                    "confirmed" if hit == len(edges) and edges else ("partial" if hit else "static")
                )
            elif observed_edges:
                status = "observed"
            for e in edges:
                n = counts.get((e["from"], e["to"]), 0)
                e["observed"] = n
                e["source"] = "both" if n else "static"
            known = {(e["from"], e["to"]) for e in edges}
            if not m["static"] or info.get("refuted"):
                for pair in sorted(counts):
                    if pair not in known:
                        edges.append(
                            {
                                "from": pair[0],
                                "to": pair[1],
                                "source": "observed",
                                "observed": counts[pair],
                                "evidence": [],
                            }
                        )
            edges.sort(key=lambda e: (e["from"], e["to"]))
            out.append(
                {
                    "id": m["id"],
                    "table": m["table"],
                    "model": m["model"],
                    "column": m["column"],
                    "states": m["states"],
                    "initial": m["initial"],
                    "observed_initial": info.get("created", []),
                    "edges": edges,
                    "status": status,
                    "spec_object": {
                        "table": m["model"],
                        "column": m["column"],
                        "edges": [[e["from"], e["to"]] for e in edges],
                        "initial": m["initial"],
                    },
                    "sweeps": [
                        {"service": s["service"], "moved": bool(s.get("moved"))}
                        for s in sweeps
                        if s["machine"]["id"] == m["id"] and s.get("done")
                    ],
                    "unread": m["unread"],
                    "witness": info.get("witness"),
                }
            )
        for o in out:
            if o.get("witness") is None:
                o.pop("witness", None)
        out.sort(key=lambda m: (m["table"], m["column"]))
        return out

    def _mine(self, plan: W.Plan, outcomes: list[tuple[W.Flow, FlowOutcome]]) -> dict[str, Any]:
        """Machine evidence from the snapshots: ``mine`` unchanged, plus own move counts."""
        from lattice.importer.mine import MiningHints, Run, TransitionMap, mine

        view = plan.view
        model, profile, err = self._profile(view.x)
        result: dict[str, Any] = {}
        runs = []
        paths: list[list[Path]] = []
        for flow, o in outcomes:
            if len(o.snapshots) < 2:
                continue
            fid = final_id(view, flow, o)
            reqs = tuple(o.requests)
            runs.append(Run(fid, tuple(o.snapshots), reqs))
            paths.append(list(o.snapshots))
        for m in plan.machines:
            counts, created = scan_snapshots(
                paths, m["table"], view.pk.get(m["table"]), m["column"]
            )
            info: dict[str, Any] = {
                "counts": counts,
                "observed": set(counts),
                "created": sorted(created),
            }
            result[m["id"]] = info
            if profile is None or not runs:
                continue
            try:
                prof = with_one_of(profile, m["model"], m["column"], m["states"])
                maps: tuple[TransitionMap, ...] = ()
                if m["static"]:
                    edges: dict[str, list[str]] = {}
                    for e in m["edges"]:
                        edges.setdefault(e["from"], []).append(e["to"])
                    maps = (
                        TransitionMap(
                            m["model"],
                            m["column"],
                            edges,
                            tuple(m["initial"]),
                            "workflow layer: status writes",
                        ),
                    )
                res = mine(prof, runs, hints=MiningHints(transitions=maps), templates=("machine",))
                for r in res.refuted:
                    if r.kind == "machine" and r.expr.startswith(f"{m['model']}.{m['column']}:"):
                        info["refuted"] = True
                        info["witness"] = r.witness
                for p in res.proposals:
                    if p.kind == "machine":
                        obj = p.spec_object
                        if getattr(obj, "table", None) == m["model"] and obj.column == m["column"]:
                            info["observed"] |= (
                                {(a, b) for a, b in obj.edges} if not m["static"] else set()
                            )
            except Exception as e:  # noqa: BLE001 -- the machine keeps its static status
                info["mine_error"] = f"{type(e).__name__}: {e}"
        return result

    # -- output

    def _dedupe(self, flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out = []
        for f in flows:
            if f["id"] in seen:
                continue
            seen.add(f["id"])
            out.append(f)
        prio = {k: i for i, k in enumerate(W.KIND_PRIORITY)}
        out.sort(key=lambda f: (prio[f["kind"]], f["skeleton"], f["_callers"], f["id"]))
        for f in out:
            f.pop("_callers", None)
        return out

    def _flow_json(
        self,
        plan: W.Plan,
        flow: W.Flow,
        outcome: FlowOutcome,
        status: str,
        source: str,
        reason: str,
        executed: bool,
        *,
        verified: bool = False,
        awaits: Sequence[Mapping[str, Any]] = (),
        ident: str | None = None,
    ) -> dict[str, Any]:
        view = plan.view
        runs = outcome.runs if outcome.runs else []
        fid = ident or final_id(view, flow, outcome if outcome.runs else None)
        steps: list[dict[str, Any]] = []
        focus = flow.focus_from
        for i, rec in enumerate(runs):
            if rec.plan_index == flow.focus_from:
                focus = i
            show = verified or not executed
            bindings = []
            for b in rec.bound:
                shown = b["value"] if (b["id_source"] == "client" or show) else None
                bindings.append(
                    {
                        "param": b["param"],
                        "source": b["source"],
                        "value": shown,
                        "from": b.get("from_run"),
                        "id_source": b["id_source"],
                    }
                )
            path = rec.path or rec.route.path
            if executed and not verified:
                path = _mask_path(rec, path)
            body = rec.body
            if executed and not verified and body is not None:
                sent = [v for r in runs[:i] if r.body for v in r.body.values()]
                body = _mask_body(rec, body, sent)
            st = rec.status if executed else None
            if not executed:
                firsts = sorted(s for s in rec.route.status_codes if 200 <= s < 300)
                st = firsts[0] if firsts else None
            steps.append(
                {
                    "i": i,
                    "kind": "request",
                    "method": rec.route.method,
                    "route": rec.route.key,
                    "path": path,
                    "caller": rec.caller,
                    "role": "focus" if i == focus else "setup",
                    "json": body,
                    "expect_status": st,
                    "expect_from": "observed" if executed else "static",
                    "code_statuses": code_statuses(rec.route),
                    "bindings": bindings,
                    "value_sources": dict(sorted(rec.sources.items())),
                    "inserted": rec.inserted or rec.spec.inserted,
                    "attempts": rec.attempts,
                    "refusal": rec.refusal
                    if (executed and (not rec.ok or rec.expected_refusal))
                    else None,
                    "evidence": _evidence(view, rec),
                }
            )
        for aw in awaits:
            steps.append(
                {
                    "i": len(steps),
                    "kind": "await",
                    "service": aw["service"],
                    "expect_db": aw["expect_db"],
                    "within_s": aw["within_s"],
                }
            )
        skeleton = [
            s["route"].split(" ", 1)[0] + " " + W.tpl(s["route"].split(" ", 1)[1])
            for s in steps
            if s["kind"] == "request"
        ]
        if awaits:
            skeleton += [f"AWAIT {a['service']}" for a in awaits]
        d = {
            "id": fid,
            "name": W.flow_name(view, flow) if not runs else _name(view, runs),
            "inferred_name": True,
            "kind": flow.kind,
            "skeleton": skeleton,
            "routes": [s["route"] for s in steps if s["kind"] == "request"],
            "focus_from": focus,
            "status": status,
            "source": source,
            "reason": reason,
            "machine_refs": sorted(flow.machine_refs),
            "edge_refs": sorted(set(flow.edge_refs)),
            "steps": steps,
            "_callers": ",".join(r.caller or "-" for r in runs),
        }
        if flow.merged_kinds:
            d["merged_kinds"] = sorted(flow.merged_kinds)
        return d


def _cannot_start(err: BaseException) -> bool:
    """The app's process cannot start (no binary, exits, never healthy): nothing can run."""
    from lattice.spec.process import ProcessStartError

    return isinstance(err, (ProcessStartError, FileNotFoundError, PermissionError))


def _mask_path(rec: StepRun, path: str) -> str:
    """An unverified flow shows no server-assigned id: it stays a template."""
    for b in rec.bound:
        if b["id_source"] != "client" and b["source"] == "path":
            path = path.replace(str(b["value"]), "{id}")
    return path


def _mask_body(rec: StepRun, body: dict[str, Any], earlier: Sequence[Any]) -> dict[str, Any]:
    """An unverified flow shows no server-assigned value: an id taken from an answer, and a
    field bound from an answer (a value no earlier request of the flow sent)."""
    out = dict(body)
    for b in rec.bound:
        if b["id_source"] != "client" and b["source"] == "body" and b["param"] in out:
            out[b["param"]] = None
    for name, source in rec.sources.items():
        if source == "bound" and name in out and out[name] not in earlier:
            out[name] = None
    return out


def _name(view: W.AppView, runs: Sequence[StepRun]) -> str:
    flow = W.Flow("pair", [r.spec for r in runs], 0)
    return W.flow_name(view, flow)


def _short(what: Any) -> str:
    text = json.dumps(what, default=str, sort_keys=True) if not isinstance(what, str) else what
    text = text.replace("\n", " ")
    return text if len(text) <= 160 else text[:157] + "..."


# ---- the command --------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice import workflows",
        description="The implemented flows and state machines of a Go (Gin + GORM) app.",
    )
    p.add_argument("root", type=Path, help="the app's repository root (read-only)")
    p.add_argument("--out", type=Path, required=True, help="the output directory (not under root)")
    p.add_argument("--package", default=".", help="the Go module directory under root")
    p.add_argument(
        "--user",
        action="append",
        default=[],
        metavar="HANDLE",
        help="a declared user handle (repeat; the first calls)",
    )
    p.add_argument("--static", action="store_true", help="plan only: run nothing")
    p.add_argument("--max-flows", type=int, default=W.MAX_FLOWS)
    p.add_argument("--max-steps", type=int, default=W.MAX_STEPS)
    p.add_argument("--no-services", action="store_true", help="do not sweep the harness services")
    p.add_argument("--database", default="auto", choices=("auto", "mysql", "sqlite"))
    p.add_argument(
        "--list-rule",
        default=DEFAULT_LIST_RULE,
        choices=LIST_RULES,
        help="which GET over a table lists its ids: the one touching the fewest tables "
        "(default, round 2) or the first in registration order (round 1, D2)",
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    args = parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        pipe = Pipeline(
            args.root,
            args.out,
            package=args.package,
            users=args.user,
            static=args.static,
            max_flows=args.max_flows,
            max_steps=args.max_steps,
            services=not args.no_services,
            database=args.database,
            list_rule=args.list_rule,
        )
        result = pipe.run()
    except WorkflowError as err:
        print(f"workflows: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- named, never a traceback in the output
        print(f"workflows: {type(err).__name__}: {err}", file=sys.stderr)
        return 3
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "workflows.json").write_text(W.to_json(result.doc))
    s = result.doc["summary"]
    print(
        f"workflows: {s['flows']} flows ({s['verified']} verified, {s['unverified']} unverified, "
        f"{s['failed']} failed), {s['edges']} edges, {s['machines']} machines -> "
        f"{args.out / 'workflows.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
