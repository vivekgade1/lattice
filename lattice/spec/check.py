"""The check engine: an app against its spec, in four layers (plan 2026-10-04, W2-A).

* **A** -- pycheck on the app's source (``app_dir``, ``package``) with a policy built
  from the spec: pycheck's derived policy, with the spec's owners, ``owner_via``, private
  columns and (when the policy has the field) state machines laid over it
  (:func:`spec_policy`). The app under check is never its own baseline: every finding
  counts. Skipped (with a note) when the source is not given. A Go app (Gin + GORM; a
  runtime whose ``framework`` is ``"go"``, or ``go_src`` given) is read by
  :mod:`lattice.gocheck` instead, on its module root (``go_src``, else ``app_dir``), with
  :meth:`lattice.gocheck.GoPolicy.from_spec`; the PY rules with no Go counterpart are
  named in a note.
* **B** -- for every endpoint the spec has a program for, a request suite
  (:func:`cases_for`, pluggable; callers may add ``extra_cases``) runs against the app
  and against the spec's programs served by the Lattice runtime, each case on a fresh
  copy of the same seeded database, compared by :func:`lattice.importer.verify.verify`
  (the app is verify's "original" side, so its representation rules apply unchanged).
  A mismatch is a finding with the request, both answers and the database difference.
* **C** -- after every request of B and D, every invariant's SQL
  (:func:`lattice.spec.invariants.compile_invariant`) runs on the app's database.
  :class:`InvariantMonitor` wraps the app factory, so it works for any app: each app the
  factory builds is wrapped in an ASGI probe that runs the invariants when a request
  completes. Rows that already violated before the request (in the seed, or in a
  scenario's ``given`` rows) are not blamed on it; the seed's own violations are
  reported once, as case ``(seed)``.
* **D** -- the spec's scenarios run against the app: a fresh seeded database per
  scenario, its ``given`` rows inserted (one-of values in their stored spellings), the
  steps in order (callers logged in through ``login(client, handle)``), each step's
  ``expect_status``, ``expect_body`` (a subset match for objects) and ``expect_db``
  checked. A scenario stops at its first failing step. An ``Await`` step starts the
  background service it names (``services``: the harness's ``{name: (db_path) ->
  ServiceCmd}``, :mod:`lattice.spec.services`) on the scenario's database, polls the
  database until its ``expect_db`` holds -- within ``within_s`` it passes, else a
  finding with the last observed counts -- and stops the service (always); layer C's
  invariants run after it, on the database the service wrote. A scenario awaiting a
  service the harness does not start is a CheckError, before anything runs.
* **R** -- the rule layer (plan 2026-10-05 phase 2, §1): every rule item of the spec's
  profile and route contracts (:mod:`lattice.spec.probes`) probed on the app, each probe
  a request on a fresh copy of the seed. Every item gets a verdict (``report.rules``); a
  refuted item is a finding (key ``R:<endpoint>:<item id> | <probe>``). With C, the
  invariants run after every probe too. Not in the default layers.
* **X** -- the concurrency layer (:mod:`lattice.spec.race`): the scenario steps that
  write an upper count bound's or a machine's table, each fired ``x_k`` times at once
  (and against the scenario's next write of the same table), on app copies sharing one
  SQLite file; every invariant and machine checked after each race. Probabilistic: a
  violation is a finding, no violation is "not refuted", never verified. Not in the
  default layers; it uses D's ``app_factory``, ``db_factory``, ``login`` and
  ``runtime``.

**gRPC** (Spike I, unary only): a scenario's ``Rpc`` step is a call through the app's rpc
transport (:mod:`lattice.spec.grpc`; a process app started with ``ready="grpc"``), the caller's
token the metadata ``authorization: Bearer <token>``; its ``expect_code`` (a ``google.rpc.Code``
name), ``expect_message`` (a subset of the response in ``lattice.grpcjson/1``) and ``expect_db``
are compared, and layer C's invariants run after it as after an HTTP step. A scenario may mix
``Rpc`` and ``Step`` steps (a gRPC gateway). Layers A and X do not run on a spec with ``Rpc``
steps (a report note ``rpc_layer_not_supported:<layer>``), and layers R and B take no contract or
program on an ``RPC`` endpoint (validate S029).

Callers: ``login(client, handle)`` answers a bearer token for a handle of
``Spec.users`` (or ``login_path``: a form login with ``Spec.users``' credentials).
Layer B logs every handle in once, on the app, on a scratch copy of the seed, and uses
those tokens on both sides; ``mint_token(user_key)``, when given, adds the
"unknown user" token case.

``db_factory`` writes the seeded database: ``db_factory(path)`` or, like the importer's,
``db_factory(variant, path)``.

**Databases** (``database=``: ``sqlite``, the default, ``postgres``, ``mysql``,
``clickhouse``). On SQLite everything is as it always was: the factories and services get
a file path. On a server dialect (:mod:`lattice.spec.db`, :mod:`lattice.spec.dbserver`)
the check runs on one server -- a throwaway Docker container, or ``database_url`` /
``$LATTICE_<DIALECT>_URL`` -- and every path above is instead the **canonical URL** of a
database on it: ``db_factory(url)`` gets an empty, freshly created database once per
variant (it has the app create its schema, e.g. ``server migrate $URL``, then inserts
the seed rows, e.g. through ``lattice.spec.db.open_db(url).insert(...)``); every
scenario, race and seed check then runs on a clone of that seed, dropped after it; the
app factory, a ``ProcessApp``'s ``db_env`` and the services get the clone's URL (a Go app
translates it with ``lattice.spec.db.url_for(url, "go-mysql")`` and the like). Layers C,
D and X run there through the database adapter (C's invariants compiled to the
dialect's SQL); layer B (the spec's programs run on SQLite) and layer R (whole-table
SQLite diffs) are skipped, with a note. On ClickHouse layer X does not race an upper
count bound (no unique constraints or transactions): a note says it is not applicable.

``runtime`` (``lattice.importer.transport.AppRuntime``; Phase 2 W3) serves a Django or
Flask app: B, D and R then open the app through its transport, log callers in through
its auth driver (``runtime.auth.token``, the ``login`` given winning), send tokens as
its scheme says (``runtime.auth.present`` / ``headers``) and dispose of every app with
its hooks (``runtime.close``). Without one, everything is as before: FastAPI's
TestClient, a bearer header, the engine's dispose. Layer C watches an ASGI app through
its probe, any other through the runtime's transport (:meth:`InvariantMonitor.watch`:
after every request the transport's client sends, in B, D, R and X).

A process app (:mod:`lattice.spec.process`: a compiled binary serving HTTP over the
SQLite file) runs through ``process_runtime()``; a factory annotated ``-> ProcessApp``
selects it when no runtime is given. Layer A is skipped, with a note, for an app whose
runtime is neither a Python framework nor Go.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.api.ir import EndpointPlan, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.check.api_info import MISSING
from lattice.diagnostics import Severity
from lattice.importer.verify import Case, CaseRecord, Rule, ValidationEnvelope, form_login, verify
from lattice.pycheck import CODES, Policy, derive_policy, run_pycheck
from lattice.spec.db import DB_ERRORS, DbRef, as_ref, check_dialect, open_db
from lattice.spec.invariants import InvariantError, compile_invariant
from lattice.spec.model import Await, Invariant, Rpc, Scenario, Spec, Step, resolve_table, tables_of
from lattice.spec.preconditions import unestablished
from lattice.spec.report import LAYERS, Accepted, CheckFinding, Report
from lattice.spec.services import POLL_S, Services, start_service
from lattice.spec.validate import validate
from lattice.typesys import Scalar, Schema

AppFactory = Callable[[Path], Any]
if TYPE_CHECKING:
    from lattice.gocheck import GoPolicy
    from lattice.importer.transport import AppRuntime
Login = Callable[[Any, str], str]
HEADER = "# lattice: v1\n"
MISSING_INT = 999_999
MISSING_UUID = "00000000-0000-4000-8000-000000000000"
VALID_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
BAD_TOKEN = "not-a-valid-token"
SEED_CASE = "(seed)"


class CheckError(Exception):
    """The check cannot run: an invalid spec, a program the checker refuses, an unknown
    layer, a database factory that fails (the CLI's exit code 2)."""


# ---- inputs --------------------------------------------------------------------------------


def release_db(make_db: Any, db: Any) -> None:
    """Drop a database ``make_db`` made, once its scenario or race is done (a server
    dialect's clone; a SQLite file goes with its temporary directory)."""
    release = getattr(make_db, "release", None)
    if release is not None:
        release(db)


def db_dialect(make_db: Any) -> str:
    """The dialect of the databases ``make_db`` makes (``sqlite`` unless it says)."""
    return str(getattr(make_db, "dialect", "sqlite"))


def as_db_factory(db_factory: Callable[..., Any]) -> Callable[[str, Path], Path]:
    """``db_factory`` as verify's ``(variant, path) -> path``; a one-argument factory
    ``(path)`` builds the ``seed`` variant only."""
    try:
        params = [
            p
            for p in inspect.signature(db_factory).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        ]
        two = len(params) >= 2
    except (TypeError, ValueError):
        two = True

    def done(result: Any, path: Path) -> Path:
        if isinstance(result, str | Path):
            return Path(result)
        if hasattr(result, "close"):  # a connection to the new database
            result.close()
        return Path(path)

    def make(variant: str, path: Path) -> Path:
        if two:
            return done(db_factory(variant, path), path)
        if variant != "seed":
            raise CheckError(f"database variant {variant!r}: the db factory takes only a path")
        return done(db_factory(path), path)

    return make


def _layers(layers: str) -> str:
    wanted = layers.upper()
    if not wanted or any(x not in LAYERS for x in wanted):
        raise CheckError(f"layers must be letters of {''.join(LAYERS)}, not {layers!r}")
    return "".join(x for x in LAYERS if x in wanted)


def spec_plans(spec: Spec) -> list[EndpointPlan]:
    """Every program of the spec, checked and lowered (CheckError if one is refused)."""
    plans: list[EndpointPlan] = []
    for name, source in spec.programs.items():
        text = source if source.lstrip().startswith("# lattice:") else HEADER + source
        checked = check_source(text, spec.profile)
        errors = [d.render() for d in checked.diagnostics if d.severity is Severity.ERROR]
        if checked.program is None or errors:
            raise CheckError(
                f"program {name!r} is not accepted:\n  "
                + "\n  ".join(errors or ["the program was not accepted"])
            )
        plans.extend(lower_api(checked.program))
    return plans


def _program_names(spec: Spec) -> dict[tuple[str, str], str]:
    """(METHOD, path) -> the name of the program defining it."""
    out: dict[tuple[str, str], str] = {}
    for name, source in spec.programs.items():
        for _, method, path in _endpoint_functions(source):
            out.setdefault((method, path), name)
    return out


def _endpoint_functions(source: str) -> list[tuple[ast.FunctionDef, str, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "endpoint"
                and len(dec.args) >= 2
                and all(isinstance(a, ast.Constant) for a in dec.args[:2])
            ):
                method, path = dec.args[0].value, dec.args[1].value  # type: ignore[attr-defined]
                found.append((node, str(method).upper(), str(path)))
    return found


def _fetched_tables(spec: Spec, endpoint: EndpointPlan) -> list[str]:
    """The tables the endpoint's program fetches, in order (schema names)."""
    for source in spec.programs.values():
        for fn, method, path in _endpoint_functions(source):
            if (method, path) != (endpoint.method, endpoint.path):
                continue
            out = []
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "fetch"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                ):
                    out.append(node.args[0].id)
            return out
    return []


def _read_tables(db: DbRef) -> dict[str, list[dict[str, Any]]]:
    with open_db(db) as conn:
        return {t: conn.read_table(t) for t in conn.tables()}


def _dispose(app: Any) -> None:
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None and hasattr(engine, "dispose"):
        engine.dispose()


# the frameworks layer A's static checker (pycheck) reads
PYTHON_FRAMEWORKS = frozenset({"fastapi", "flask", "django"})


def _asgi(runtime: AppRuntime | None) -> bool:
    """Whether the app is served as ASGI (layer C's probe wraps an ASGI app only)."""
    return runtime is None or runtime.framework == "fastapi"


def _close(runtime: AppRuntime | None, app: Any) -> None:
    """Dispose of an app: the runtime's hooks, else FastAPI's engine (the default)."""
    if runtime is None:
        _dispose(app)
    else:
        runtime.close(app)


# ---- layer A -------------------------------------------------------------------------------


def spec_policy(
    spec: Spec, app_dir: Path | str, package: str, policy: Policy | None = None
) -> Policy:
    """pycheck's policy for the app, with the spec's facts laid over it: owners,
    owner_via and private columns of every table the spec declares (the spec is the
    authority for its tables), and ``machines`` (table -> (column, legal edges)) when
    the Policy has that field. Without ``policy``, it is derived from the app itself
    and its baseline is emptied (the app under check is not its own reference)."""
    tables, _ = tables_of(spec.profile)
    owners = {t: s.owner for t, s in tables.items() if s.owner}
    owner_via = {t: tuple(s.owner_via) for t, s in tables.items() if s.owner_via}
    if policy is None:
        try:
            base = derive_policy(Path(app_dir), package, ownership=(owners, owner_via))
        except Exception as err:  # noqa: BLE001 -- reported as a check error
            raise CheckError(f"layer A: cannot derive the app's policy: {err}") from err
        base = replace(base, baseline=frozenset())
    else:
        base = policy
    declared = set(tables)
    merged_owners = {t: c for t, c in base.owners.items() if t not in declared} | owners
    merged_via = {t: v for t, v in base.owner_via.items() if t not in declared} | owner_via
    private = {t: c for t, c in base.private.items() if t not in declared}
    private |= {t: tuple(sorted(s.private)) for t, s in tables.items() if s.private}
    # a table the spec declares with no owner is public: shared, never a link table
    users = base.users_table
    public = sorted(t for t in declared if t not in owners and t not in owner_via and t != users)
    shared = tuple(t for t in base.shared if t not in merged_owners and t not in merged_via)
    shared += tuple(t for t in public if t not in shared)
    links = {t: v for t, v in base.links.items() if t not in declared}
    updates: dict[str, Any] = {
        "owners": merged_owners,
        "owner_via": merged_via,
        "private": private,
        "shared": shared,
        "links": links,
    }
    if "machines" in {f.name for f in fields(Policy)}:
        machines = dict(getattr(base, "machines", {}) or {})
        for m in spec.machines:
            found = resolve_table(spec.profile, m.table)
            name = found[0] if found else m.table
            machines[name] = (m.column, frozenset(m.edges))
        updates["machines"] = machines
    return replace(base, **updates)


def _endpoint_of(key: str, message: str) -> str | None:
    for part in key.split("|"):
        if re.match(r"^[A-Z]+ /", part):
            return part
    m = re.match(r"^([A-Z]+ /\S*)", message)
    return m.group(1) if m else None


def _layer_a_go(
    spec: Spec, report: Report, src: Path | str | None, policy: Policy | GoPolicy | None
) -> None:
    """Layer A on a Go (Gin + GORM) app: gocheck on its module root."""
    from lattice.gocheck import (
        GO_CODES,
        GoCheckError,
        GoPolicy,
        GoToolchainMissing,
        gated_note,
        run_gocheck,
    )

    if src is None:
        report.notes.append("layer A skipped: no Go source (go_src, or --go-src)")
        return
    pol = policy if isinstance(policy, GoPolicy) else GoPolicy.from_spec(spec)
    try:
        found = run_gocheck(Path(src), pol, strict=True)
    except GoToolchainMissing as err:
        report.notes.append(f"layer A skipped: {err}")
        return
    except GoCheckError as err:
        raise CheckError(f"layer A: gocheck failed: {err}") from err
    report.notes.append(gated_note())
    for f in found:
        report.findings.append(
            CheckFinding(
                layer="A",
                verdict="silent",
                rule=GO_CODES.get(f.code, f.code),
                endpoint=_endpoint_of(f.key, f.message),
                code=f.code,
                file=f.file,
                line=f.line,
                details={"message": f.message, "hint": f.hint, "key": f.key},
            )
        )


def _layer_a_dotnet(
    spec: Spec, report: Report, src: Path | str | None, policy: Policy | GoPolicy | None
) -> None:
    """Layer A on an ASP.NET Core + EF Core app: dncheck on its project directory."""
    from lattice.dncheck import DnCheckError, DotnetToolchainMissing
    from lattice.dncheck.policy import DN_CODES, DnPolicy, gated_note, run_dncheck

    if src is None:
        report.notes.append("layer A skipped: no .NET source (app_dir, or --app-dir)")
        return
    pol = policy if isinstance(policy, DnPolicy) else DnPolicy.from_spec(spec)
    try:
        found = run_dncheck(Path(src), pol, strict=True)
    except DotnetToolchainMissing as err:
        report.notes.append(f"layer A skipped: {err}")
        return
    except DnCheckError as err:
        raise CheckError(f"layer A: dncheck failed: {err}") from err
    report.notes.append(gated_note())
    for f in found:
        report.findings.append(
            CheckFinding(
                layer="A", verdict="silent", rule=DN_CODES.get(f.code, f.code),
                endpoint=_endpoint_of(f.key, f.message), code=f.code, file=f.file, line=f.line,
                details={"message": f.message, "hint": f.hint, "key": f.key},
            )
        )


def _layer_a(
    spec: Spec,
    report: Report,
    app_dir: Path | str | None,
    package: str | None,
    policy: Policy | GoPolicy | None,
    runtime: AppRuntime | None = None,
    go_src: Path | str | None = None,
) -> None:
    if go_src is not None or (runtime is not None and runtime.framework == "go"):
        _layer_a_go(spec, report, go_src if go_src is not None else app_dir, policy)
        return
    if runtime is not None and runtime.framework == "dotnet":
        _layer_a_dotnet(spec, report, app_dir, policy)
        return
    if runtime is not None and runtime.framework not in PYTHON_FRAMEWORKS:
        report.notes.append(
            f"layer A: no static checker for this language (the app is {runtime.framework!r}, "
            "not Python); skipped"
        )
        return
    if app_dir is None or package is None:
        report.notes.append("layer A skipped: no app source (app_dir and package)")
        return
    pol = spec_policy(spec, app_dir, package, policy if isinstance(policy, Policy) else None)
    try:
        found = run_pycheck(Path(app_dir), pol, strict=True)
    except Exception as err:  # noqa: BLE001 -- a checker failure is a check error
        raise CheckError(f"layer A: pycheck failed: {err}") from err
    for f in found:
        report.findings.append(
            CheckFinding(
                layer="A",
                verdict="silent",
                rule=CODES.get(f.code, f.code),
                endpoint=_endpoint_of(f.key, f.message),
                code=f.code,
                file=f.file,
                line=f.line,
                details={"message": f.message, "hint": f.hint, "key": f.key},
            )
        )


# ---- layer C: the invariant monitor ----------------------------------------------------------


def _row_key(row: Mapping[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


class _Probe:
    """An ASGI wrapper running the monitor's invariants after each HTTP request; every
    other attribute is the wrapped app's (``state``, ``routes``...)."""

    def __init__(self, app: Any, db: DbRef, monitor: InvariantMonitor) -> None:
        self._app = app
        self._db = db
        self._monitor = monitor
        self._baseline = monitor.violation_keys(db)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await self._app(scope, receive, send)
        finally:
            if scope.get("type") == "http":
                self._monitor.after(self._db, scope, self._baseline)


class _TransportProbe:
    """A transport running the monitor's invariants after each request its client sends
    (layer C through a runtime: any framework, a process). The hook is on the client's
    ``send``, so the requests a login makes through ``client`` are watched too; it runs
    once the answer's body is read in full -- the server has answered, and what it
    committed is what a new SQLite connection sees. Without an httpx-style client, the
    transport's own ``request`` is watched."""

    def __init__(
        self, inner: Any, db: DbRef, monitor: InvariantMonitor, baseline: Mapping[str, set[str]]
    ) -> None:
        self._inner = inner
        self._db = db
        self._monitor = monitor
        self._baseline = baseline
        self._hooked = False
        client = getattr(inner, "client", None)
        send = getattr(client, "send", None)
        if callable(send):

            def watched_send(request: Any, *args: Any, **kw: Any) -> Any:
                response = send(request, *args, **kw)
                if kw.get("stream"):
                    response.read()
                self._after(str(request.method), str(request.url.path))
                return response

            client.send = watched_send  # type: ignore[union-attr]
            self._hooked = True

    @property
    def client(self) -> Any:
        return self._inner.client

    def _after(self, method: str, path: str) -> None:
        with self._monitor.lock:
            self._monitor.after(self._db, {"method": method, "path": path}, self._baseline)

    def request(self, method: str, path: str, **kw: Any) -> Any:
        answer = self._inner.request(method, path, **kw)
        if not self._hooked:
            self._after(method, path)
        return answer

    def set_cookie(self, name: str, value: str) -> None:
        self._inner.set_cookie(name, value)

    def __enter__(self) -> _TransportProbe:
        self._inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self._inner.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class InvariantMonitor:
    """Runs every invariant on an app's database after each request (layer C).

    :meth:`wrap` turns an app factory into one whose apps are probed; ``label`` names
    the request being run (set it before each request, or give ``wrap`` an iterator of
    labels: one per app built, as verify builds one app per case). ``requests`` and
    ``endpoints`` map a label to its request and endpoint, for the findings."""

    def __init__(self, invariants: Sequence[tuple[Invariant, str]]) -> None:
        self.invariants = list(invariants)  # (invariant, its SQL in the database's dialect)
        self.label: str | None = None
        self.requests: dict[str, dict[str, Any]] = {}
        self.endpoints: dict[str, str] = {}
        self.findings: list[CheckFinding] = []
        self._seen: set[tuple[str, str]] = set()
        self._seed: dict[str, set[str]] = {}
        # a transport probe's checks run under it (layer X's clients run at once)
        self.lock = threading.Lock()
        # seconds spent running the invariants' SQL (the report's timings)
        self.seconds = 0.0

    @classmethod
    def for_spec(cls, spec: Spec, dialect: str = "sqlite") -> InvariantMonitor:
        """The spec's invariants, compiled to ``dialect``'s SQL (the database's)."""
        compiled = []
        for inv in spec.invariants:
            try:
                compiled.append((inv, compile_invariant(inv, spec.profile, dialect)))
            except InvariantError as err:
                raise CheckError(f"invariant {inv.name!r}: {err}") from err
        return cls(compiled)

    def violations(self, db: DbRef) -> dict[str, list[dict[str, Any]]]:
        if not self.invariants:
            return {}
        started = time.perf_counter()
        try:
            with open_db(db) as conn:
                out = {}
                for inv, sql in self.invariants:
                    try:
                        rows = conn.query_dicts(sql)
                    except DB_ERRORS as err:
                        rows = [{"error": f"the invariant's SQL failed: {err}"}]
                    out[inv.name] = rows
                return out
        finally:
            self.seconds += time.perf_counter() - started

    def violation_keys(self, db: DbRef) -> dict[str, set[str]]:
        return {n: {_row_key(r) for r in rows} for n, rows in self.violations(db).items()}

    def set_seed(self, db: DbRef) -> None:
        """The seed's violations: reported once (case ``(seed)``), never blamed on a
        request."""
        self._seed = self.violation_keys(db)
        self.check(db, SEED_CASE, baseline={})

    def check(
        self, db: DbRef, label: str, baseline: Mapping[str, set[str]] | None = None
    ) -> None:
        """Report the violations of ``db`` not in ``baseline`` (default: the seed's)."""
        base = self._seed if baseline is None else baseline
        found = self.violations(db)
        for inv, _ in self.invariants:
            new = [r for r in found[inv.name] if _row_key(r) not in base.get(inv.name, set())]
            if new:
                self._report(inv, label, new, None)

    def wrap(self, app_factory: AppFactory, labels: Iterator[str] | None = None) -> AppFactory:
        def factory(db_path: Path) -> Any:
            if labels is not None:
                self.label = next(labels, None)
            return _Probe(app_factory(db_path), as_ref(db_path), self)

        return factory

    def watch(
        self,
        app_factory: AppFactory,
        runtime: AppRuntime,
        labels: Iterator[str] | None = None,
    ) -> tuple[AppFactory, AppRuntime]:
        """Layer C for an app served through ``runtime``'s transport (any framework, a
        process): the factory and runtime to use instead. Each transport the runtime
        opens runs the invariants on the app's database after every request its client
        sends, once the answer is read in full; violations the database had when the app
        was built are not blamed on a request. ``labels`` as for :meth:`wrap`."""
        built: dict[int, tuple[Any, DbRef, dict[str, set[str]]]] = {}

        def factory(db_path: DbRef) -> Any:
            if labels is not None:
                self.label = next(labels, None)
            app = app_factory(db_path)
            built[id(app)] = (app, as_ref(db_path), self.violation_keys(as_ref(db_path)))
            return app

        def transport(app: Any, *, raise_errors: bool = False) -> Any:
            inner = runtime.open(app, raise_errors=raise_errors)
            entry = built.get(id(app))
            if entry is None:  # an app this factory did not build: not watched
                return inner
            _, db, baseline = entry
            return _TransportProbe(inner, db, self, baseline)

        def forget(app: Any) -> None:
            built.pop(id(app), None)

        watched = replace(runtime, transport=transport, dispose=(*runtime.dispose, forget))
        return factory, watched

    def after(
        self, db: DbRef, scope: Mapping[str, Any], baseline: Mapping[str, set[str]]
    ) -> None:
        label = self.label or f"{scope.get('method')} {scope.get('path')}"
        found = self.violations(db)
        for inv, _ in self.invariants:
            new = [r for r in found[inv.name] if _row_key(r) not in baseline.get(inv.name, set())]
            if new:
                self._report(inv, label, new, scope)

    def _report(
        self,
        inv: Invariant,
        label: str,
        rows: list[dict[str, Any]],
        scope: Mapping[str, Any] | None,
    ) -> None:
        if (inv.name, label) in self._seen:
            return
        self._seen.add((inv.name, label))
        request = self.requests.get(label)
        if request is None and scope is not None:
            request = {"method": scope.get("method"), "path": scope.get("path")}
        endpoint = self.endpoints.get(label)
        if endpoint is None and request is not None:
            endpoint = f"{request.get('method')} {request.get('path')}"
        violation = {
            "invariant": inv.name,
            "sentence": inv.sentence,
            "expr": inv.expr,
            "rows": rows[:20],
        }
        self.findings.append(
            CheckFinding(
                layer="C",
                verdict="silent",
                rule=inv.sentence,
                endpoint=endpoint,
                case=label,
                request=request,
                details={
                    "invariant": inv.name,
                    "expr": inv.expr,
                    "rows": rows[:20],
                    "more_rows": max(0, len(rows) - 20),
                },
                invariant_violations=[violation],
            )
        )


# ---- layer B: cases -------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseContext:
    """What a case generator knows: the seed's rows (SQL table -> rows), the caller
    handles that have a token, the SQL tables each case snapshots, ``mint_token`` (a
    token for a users key, or None), how many rows of a table to address by id."""

    seed: Mapping[str, Sequence[Mapping[str, Any]]]
    handles: tuple[str, ...]
    tables: tuple[str, ...] = ()
    mint_token: Callable[[Any], str] | None = None
    max_rows: int = 6
    bad_token: str = BAD_TOKEN


CasesFor = Callable[[Spec, EndpointPlan, CaseContext], list[Case]]


def _key_column(schema: Schema) -> str | None:
    single = [k[0] for k in schema.keys if len(k) == 1]
    return single[0] if single else ("id" if "id" in schema.names() else None)


def _table_for_param(spec: Spec, endpoint: EndpointPlan, name: str) -> str | None:
    """The schema name of the table a path parameter addresses: by its name
    (``order_id`` -> Order), else the path segment before it, else the first table the
    program fetches."""
    tables, sql_names = tables_of(spec.profile)

    def match(word: str) -> str | None:
        w = word.lower().replace("_", "")
        for t in tables:
            sql = sql_names.get(t, t).lower().replace("_", "")
            if w in (t.lower(), sql, sql.rstrip("s"), t.lower() + "s"):
                return t
        return None

    if name.endswith("_id") and (found := match(name[: -len("_id")])):
        return found
    segments = endpoint.path.split("/")
    hole = "{" + name + "}"
    if hole in segments:
        i = segments.index(hole)
        if i > 0 and (found := match(segments[i - 1])):
            return found
    fetched = _fetched_tables(spec, endpoint)
    return fetched[0] if fetched and fetched[0] in tables else None


def _owner_of(
    spec: Spec, table: str, row: Mapping[str, Any], seed: Mapping[str, Any], depth: int = 0
) -> Any:
    found = resolve_table(spec.profile, table)
    if found is None or depth > 4:
        return None
    _, _, schema = found
    if schema.owner:
        return row.get(schema.owner)
    if schema.owner_via:
        fk, parent = schema.owner_via
        pfound = resolve_table(spec.profile, parent)
        if pfound is None:
            return None
        _, psql, pschema = pfound
        pkey = _key_column(pschema)
        for prow in seed.get(psql, ()):
            if pkey and prow.get(pkey) == row.get(fk):
                return _owner_of(spec, parent, prow, seed, depth + 1)
    return None


def _row_ids(spec: Spec, table: str, ctx: CaseContext) -> list[Any]:
    """Up to ``max_rows`` keys of ``table``: the first row of every owner first (so each
    caller meets its own rows and other users'), then the next rows."""
    found = resolve_table(spec.profile, table)
    if found is None:
        return []
    _, sql, schema = found
    key = _key_column(schema)
    rows = list(ctx.seed.get(sql, ()))
    if key is None or not rows:
        return []
    first: dict[str, Any] = {}
    for row in rows:
        owner = json.dumps(_owner_of(spec, table, row, ctx.seed), default=str)
        first.setdefault(owner, row[key])
    ids = list(first.values())
    for row in rows:
        if len(ids) >= ctx.max_rows:
            break
        if row[key] not in ids:
            ids.append(row[key])
    return ids[: max(ctx.max_rows, len(first))]


def _clamp(value: float, p: Any) -> float:
    if p.ge is not None and value < p.ge:
        value = p.ge
    if p.le is not None and value > p.le:
        value = p.le
    return value


def _valid(spec: Spec, p: Any, ctx: CaseContext) -> Any:
    """A value the parameter accepts (a seeded row's key for an ``<x>_id`` Int)."""
    if p.one_of:
        return p.one_of[0]
    if p.default is not MISSING and not p.optional and p.default is not None:
        return p.default
    s = p.scalar
    if s is Scalar.INT:
        if p.name.endswith("_id"):
            table = _table_for_param(spec, _NO_ENDPOINT, p.name)
            ids = _row_ids(spec, table, ctx) if table else []
            if ids:
                return ids[0]
        return int(_clamp(1, p))
    if s is Scalar.FLOAT:
        return float(_clamp(1.0, p))
    if s is Scalar.BOOL:
        return True
    if s is Scalar.UUID:
        return VALID_UUID
    if s is Scalar.DATETIME:
        return "2026-06-01T12:00:00Z"
    if s is Scalar.DATE:
        return "2026-06-01"
    if getattr(p, "email", False):
        return "valid@example.com"
    text = f"valid {p.name.replace('_', ' ')}"
    if p.min_len is not None and len(text) < p.min_len:
        text += "x" * (p.min_len - len(text))
    if p.max_len is not None:
        text = text[: p.max_len]
    return text


@dataclass(frozen=True)
class _NoEndpoint:
    method: str = ""
    path: str = ""


_NO_ENDPOINT: Any = _NoEndpoint()


def _boundaries(p: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if p.one_of:
        return [("not_a_choice", "not-a-choice")]
    s = p.scalar
    if s in (Scalar.INT, Scalar.FLOAT):
        cast = int if s is Scalar.INT else float
        if p.ge is not None:
            out += [("min", cast(p.ge)), ("below_min", cast(p.ge) - 1)]
        if p.le is not None:
            out += [("max", cast(p.le)), ("above_max", cast(p.le) + 1)]
        out.append(("malformed", "not-a-number"))
    elif s is Scalar.STR:
        if p.min_len:
            out += [("min_len", "b" * p.min_len), ("below_min_len", "b" * (p.min_len - 1))]
        if p.max_len is not None:
            out += [("max_len", "b" * p.max_len), ("above_max_len", "b" * (p.max_len + 1))]
    elif s is Scalar.BOOL:
        out.append(("malformed", "not-a-bool"))
    elif s is Scalar.UUID:
        out.append(("malformed", "not-a-uuid"))
    elif s in (Scalar.DATETIME, Scalar.DATE):
        out.append(("malformed", "not-a-date"))
    return out


def _path_values(
    spec: Spec, endpoint: EndpointPlan, p: Any, ctx: CaseContext
) -> list[tuple[str, Any]]:
    table = _table_for_param(spec, endpoint, p.name)
    ids = _row_ids(spec, table, ctx) if table else []
    values: list[tuple[str, Any]] = [(f"{p.name}={i}", i) for i in ids]
    if not values:
        v = _valid(spec, p, ctx)
        values.append((f"{p.name}={v}", v))
    if p.scalar is Scalar.UUID:
        values += [("missing", MISSING_UUID), ("malformed", "not-a-uuid")]
    elif p.scalar is Scalar.INT:
        values += [("missing", MISSING_INT), ("malformed", "not-an-int")]
    elif p.scalar is Scalar.FLOAT:
        values.append(("malformed", "not-a-number"))
    return values


def cases_for(spec: Spec, endpoint: EndpointPlan, ctx: CaseContext) -> list[Case]:
    """The default suite for one endpoint (named ``METHOD /path | caller | what``):

    * every caller (each handle; for a public endpoint, no caller too) on every
      addressed row -- the first row of each owner, then more up to ``max_rows`` -- and
      on a missing and a malformed id;
    * for the first caller and row: each query and body parameter at its bounds and
      just past them, malformed, and (body) each required field missing;
    * without a token, with a garbage token, and (with ``mint_token``) as an unknown
      user."""
    path_params = [p for p in endpoint.params if p.source == "path"]
    query = [p for p in endpoint.params if p.source == "query"]
    body = [p for p in endpoint.params if p.source == "body"]
    public = endpoint.auth == "Public"
    callers: list[str | None] = list(ctx.handles)
    if public or not callers:
        callers.insert(0, None)
    values = {p.name: _path_values(spec, endpoint, p, ctx) for p in path_params}
    # the first values of every path parameter, then each other value of one of them
    combos: list[tuple[str, dict[str, Any]]] = []
    first = {n: vs[0] for n, vs in values.items()}
    combos.append(
        (" ".join(label for label, _ in first.values()), {n: v for n, (_, v) in first.items()})
    )
    for n, vs in values.items():
        for label, v in vs[1:]:
            chosen = {m: first[m][1] for m in first} | {n: v}
            combos.append((" ".join(label if m == n else first[m][0] for m in first), chosen))
    happy_body = {p.name: _valid(spec, p, ctx) for p in body} if body else None
    out: list[Case] = []
    seen: set[str] = set()

    def path_of(chosen: Mapping[str, Any]) -> str:
        path = endpoint.path
        for n, v in chosen.items():
            path = path.replace("{" + n + "}", str(v))
        return path

    def add(
        who: str,
        what: str,
        *,
        user: str | None,
        chosen: Mapping[str, Any],
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> None:
        case = Case(
            name=f"{endpoint.method} {endpoint.path} | {who} | {what}".rstrip(" |"),
            method=endpoint.method,
            path=path_of(chosen),
            user=user if token is None else None,
            token=token,
            params=params,
            json=json_body,
            tables=ctx.tables,
            route=f"{endpoint.method} {endpoint.path}",
        )
        key = repr((case.method, case.path, case.user, case.token, case.params, case.json))
        if key in seen:
            return
        seen.add(key)
        names = {c.name for c in out}
        name, k = case.name, 2
        while name in names:
            name, k = f"{case.name} #{k}", k + 1
        out.append(replace(case, name=name))

    for caller in callers:
        who = caller or "no caller"
        for label, chosen in combos:
            add(who, label or "call", user=caller, chosen=chosen, json_body=happy_body)
    caller = callers[0]
    who = caller or "no caller"
    base = combos[0][1]
    for p in query:
        for label, v in _boundaries(p):
            add(
                who,
                f"query {p.name} {label}",
                user=caller,
                chosen=base,
                params={p.name: v},
                json_body=happy_body,
            )
    for p in body:
        for label, v in _boundaries(p):
            add(
                who,
                f"body {p.name} {label}",
                user=caller,
                chosen=base,
                json_body={**(happy_body or {}), p.name: v},
            )
        if p.default is MISSING and not p.optional:
            add(
                who,
                f"body {p.name} missing",
                user=caller,
                chosen=base,
                json_body={k: v for k, v in (happy_body or {}).items() if k != p.name},
            )
    if not public:
        add("no token", "call", user=None, chosen=base, json_body=happy_body)
        add(
            "garbage token",
            "call",
            user=None,
            token=ctx.bad_token,
            chosen=base,
            json_body=happy_body,
        )
        if ctx.mint_token is not None:
            add(
                "unknown user",
                "call",
                user=None,
                token=ctx.mint_token(MISSING_INT),
                chosen=base,
                json_body=happy_body,
            )
    return out


# ---- layer B: running ------------------------------------------------------------------------


def _request(case: Case) -> dict[str, Any]:
    return {
        "method": case.method,
        "path": case.path,
        "user": case.user,
        "token": "<given>" if case.token is not None else None,
        "params": case.params,
        "json": case.json,
    }


def _table_keys(spec: Spec) -> dict[str, str]:
    tables, sql_names = tables_of(spec.profile)
    out = {}
    for t, schema in tables.items():
        key = _key_column(schema)
        if key:
            out[sql_names.get(t, t)] = key
    return out


def db_diff(
    expected: Mapping[str, list[dict[str, Any]]],
    actual: Mapping[str, list[dict[str, Any]]],
    keys: Mapping[str, str] | None = None,
) -> dict[str, dict[str, list[Any]]]:
    """Per table where the two differ: ``added`` (rows only the app has), ``removed``
    (rows only the spec has), ``changed`` (``{key, columns: {col: [expected,
    actual]}}``), rows matched by the table's key."""
    out: dict[str, dict[str, list[Any]]] = {}
    for t in dict.fromkeys([*expected, *actual]):
        e, a = expected.get(t, []), actual.get(t, [])
        if e == a:
            continue
        key = (keys or {}).get(t)
        if key is None or not all(key in r for r in [*e, *a]):
            out[t] = {
                "added": [r for r in a if r not in e],
                "removed": [r for r in e if r not in a],
                "changed": [],
            }
            continue
        ek, ak = {r[key]: r for r in e}, {r[key]: r for r in a}
        changed = []
        for k, er in ek.items():
            ar = ak.get(k)
            if ar is None or ar == er:
                continue
            cols = {
                c: [er.get(c), ar.get(c)]
                for c in dict.fromkeys([*er, *ar])
                if er.get(c) != ar.get(c)
            }
            changed.append({"key": {key: k}, "columns": cols})
        out[t] = {
            "added": [r for k, r in ak.items() if k not in ek],
            "removed": [r for k, r in ek.items() if k not in ak],
            "changed": changed,
        }
    return out


def _template_of(path: str, endpoints: Sequence[EndpointPlan], method: str) -> str | None:
    for e in endpoints:
        if e.method != method:
            continue
        pattern = "^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(e.path)) + "$"
        if re.match(pattern, path):
            return f"{e.method} {e.path}"
    return None


def _tokens(
    handles: Sequence[str],
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    login: Login,
    tmp: Path,
    runtime: AppRuntime | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Each handle's token, from the app's own login on a scratch copy of the seed
    (through ``runtime``'s transport and auth driver when given)."""
    from fastapi.testclient import TestClient

    tokens: dict[str, str] = {}
    notes: list[str] = []
    db = make_db("seed", tmp / "login.db")
    if runtime is not None:
        with runtime.serve(app_factory, db) as (_, transport):
            for h in handles:
                try:
                    tokens[h] = runtime.auth.token(transport, h, login)
                except Exception as err:  # noqa: BLE001 -- that caller is left out, noted
                    notes.append(f"layer B: cannot log in as {h!r} ({err}); no cases as {h}")
        return tokens, notes
    app = app_factory(db)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for h in handles:
                try:
                    tokens[h] = login(client, h)
                except Exception as err:  # noqa: BLE001 -- that caller is left out, noted
                    notes.append(f"layer B: cannot log in as {h!r} ({err}); no cases as {h}")
    finally:
        _dispose(app)
    return tokens, notes


def _layer_b(
    spec: Spec,
    report: Report,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    monitor: InvariantMonitor | None,
    *,
    login: Login | None,
    mint_token: Callable[[Any], str] | None,
    generate: CasesFor,
    extra_cases: Sequence[Case],
    rules: Sequence[Rule],
    natives: Mapping[str, Callable[..., object]] | None,
    settings: Mapping[str, object] | None,
    hash_columns: Sequence[str],
    max_rows: int,
    runtime: AppRuntime | None = None,
) -> None:
    plans = spec_plans(spec)
    if not plans and not extra_cases:
        report.notes.append("layer B: the spec has no programs; nothing to compare")
        return
    programs = _program_names(spec)
    with tempfile.TemporaryDirectory(prefix="lattice-check-b-") as name:
        tmp = Path(name)
        seed_db = make_db("seed", tmp / "seed.db")
        seed = _read_tables(seed_db)
        _, sql_names = tables_of(spec.profile)
        tables = tuple(t for t in dict.fromkeys(sql_names.values()) if t in seed)
        handles = tuple(spec.users)
        tokens: dict[str, str] = {}
        if handles and login is None:
            report.notes.append("layer B: no login given; cases run without callers")
        elif handles:
            tokens, notes = _tokens(handles, app_factory, make_db, login, tmp, runtime)  # type: ignore[arg-type]
            report.notes.extend(notes)
        ctx = CaseContext(seed, tuple(tokens), tables, mint_token, max_rows)
        cases: list[Case] = []
        endpoint_of: dict[str, str] = {}
        for plan in plans:
            for case in generate(spec, plan, ctx):
                cases.append(case)
                endpoint_of[case.name] = f"{plan.method} {plan.path}"
        for case in extra_cases:
            if not case.tables:
                case = replace(case, tables=tables)
            cases.append(case)
            endpoint_of[case.name] = _template_of(case.path, plans, case.method) or (
                f"{case.method} {case.path}"
            )
        names = [c.name for c in cases]
        if len(set(names)) != len(names):
            dup = sorted({n for n in names if names.count(n) > 1})
            raise CheckError(f"layer B: case names must be unique: {dup}")
        if not cases:
            report.notes.append("layer B: no cases generated")
            return

        def token_login(client: Any, handle: str) -> str:
            if handle not in tokens:
                raise RuntimeError(f"no token for {handle!r} (no login, or it failed)")
            return tokens[handle]

        def spec_app(path: Path) -> Any:
            return build_app(plans, path, natives=natives, settings=settings)

        app_side = app_factory
        if monitor is not None:
            for c in cases:
                monitor.requests[c.name] = _request(c)
                monitor.endpoints[c.name] = endpoint_of[c.name]
            if _asgi(runtime):
                app_side = monitor.wrap(app_factory, iter(names))
            else:
                app_side, runtime = monitor.watch(app_factory, runtime, iter(names))  # type: ignore[arg-type]
        try:
            result = verify(
                app_side,
                spec_app,
                make_db,
                cases,
                rules,
                {},
                login=token_login,
                hash_columns=hash_columns,
                **({} if runtime is None else {"runtime": runtime}),
            )
        except CheckError:
            raise
        except Exception as err:  # noqa: BLE001 -- the app could not run the suite: loud
            report.findings.append(
                CheckFinding(
                    layer="B",
                    verdict="loud",
                    rule="the app runs the spec's endpoints",
                    details={"error": f"{type(err).__name__}: {err}"},
                )
            )
            return
    keys = _table_keys(spec)
    by_name = {c.name: c for c in cases}
    for name, outcome in result.outcomes.items():
        case = by_name[name]
        app_rec: CaseRecord = result.original[name]
        spec_rec: CaseRecord = result.lattice[name]
        endpoint = endpoint_of[name]
        if outcome.kind == "mismatch":
            loud = app_rec.status >= 500 or spec_rec.status >= 500
            verdict = "loud" if loud else "silent"
        else:
            verdict = "correct"
        report.cases[name] = {
            "layer": "B",
            "endpoint": endpoint,
            "verdict": verdict,
            "reason": outcome.reason,
            "request": _request(case),
        }
        if verdict == "correct":
            continue
        method, _, path = endpoint.partition(" ")
        program = programs.get((method, path))
        rule = (
            f"{endpoint} answers as its spec program {program!r}"
            if program
            else f"{endpoint} answers as the spec programs"
        )
        report.findings.append(
            CheckFinding(
                layer="B",
                verdict=verdict,  # type: ignore[arg-type]
                rule=rule,
                endpoint=endpoint,
                case=name,
                request=_request(case),
                details={
                    "reason": outcome.reason,
                    "expected": {"status": spec_rec.status, "body": spec_rec.body},
                    "actual": {"status": app_rec.status, "body": app_rec.body},
                },
                db_diff=db_diff(spec_rec.db, app_rec.db, keys),
            )
        )


# ---- layer D --------------------------------------------------------------------------------


def _stored(schema: Schema, column: str, value: Any) -> Any:
    """A one-of value in its stored spelling."""
    values = dict(schema.one_of).get(column)
    stored = dict(schema.one_of_stored).get(column)
    if values and stored and isinstance(value, str) and value in values:
        return stored[values.index(value)]
    return value


def _insert_given(spec: Spec, db: DbRef, given: Mapping[str, list[dict[str, Any]]]) -> None:
    """A scenario's ``given`` rows (one-of values in their stored spellings), inserted
    through the database's adapter."""
    batches = []
    for table, rows in given.items():
        found = resolve_table(spec.profile, table)
        if found is None:
            raise CheckError(f"given rows of an unknown table {table!r}")
        _, sql, schema = found
        batches.append((sql, [{c: _stored(schema, c, row[c]) for c in row} for row in rows]))
    with open_db(db) as conn:
        for sql, rows in batches:
            conn.insert(sql, rows)


def _plain(value: Any) -> Any:
    """A server driver's value as SQLite would answer it: a Decimal as a number, a UUID
    as its text (a SQLite value is answered as it is)."""
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def _same_time(expected: Any, actual: Any) -> bool | None:
    """A date or datetime the driver answered, against the spec's ISO text (None: not
    that case)."""
    from datetime import UTC, date, datetime

    if not (isinstance(actual, date) and isinstance(expected, str)):
        return None
    try:
        want: Any = datetime.fromisoformat(expected.replace("Z", "+00:00"))
    except ValueError:
        return False
    if not isinstance(actual, datetime):
        return want.date() == actual if want.time() == datetime.min.time() else False
    if (want.tzinfo is None) != (actual.tzinfo is None):  # a naive value is UTC
        want = want.replace(tzinfo=UTC) if want.tzinfo is None else want
        actual = actual.replace(tzinfo=UTC) if actual.tzinfo is None else actual
    return bool(want == actual)


def _same(expected: Any, actual: Any) -> bool:
    timed = _same_time(expected, actual)
    if timed is not None:
        return timed
    expected, actual = _plain(expected), _plain(actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, float | int) and isinstance(actual, float | int):
        return abs(expected - actual) <= 1e-9 * max(1.0, abs(expected), abs(actual))
    return expected == actual


def subset(expected: Any, actual: Any) -> bool:
    """``expected`` within ``actual``: an object's keys (recursively), a list
    element-wise with the same length, a scalar equal."""
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(subset(e, a) for e, a in zip(expected, actual, strict=True))
        )
    return _same(expected, actual)


def _where(conn: Any, schema: Schema, where: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """An ``expect_db`` entry's ``where`` as SQL for ``conn``'s dialect (one-of values in
    their stored spellings; ``None`` is ``IS NULL``)."""
    return conn.where_sql({c: (None if v is None else _stored(schema, c, v))
                           for c, v in where.items()})  # fmt: skip


def _expect_db(spec: Spec, db: DbRef, expect: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    with open_db(db) as conn:
        for table, cond in expect.items():
            found = resolve_table(spec.profile, table)
            if found is None:
                problems.append(f"db {table}: unknown table")
                continue
            _, sql, schema = found
            where = dict(cond.get("where") or {})
            clause, args = _where(conn, schema, where)
            rows = conn.query_dicts(f"SELECT * FROM {conn.quote_ident(sql)}{clause}", args)
            what = f"db {table}" + (f" where {where}" if where else "")
            if "count" in cond and len(rows) != cond["count"]:
                problems.append(f"{what}: expected {cond['count']} rows, found {len(rows)}")
            if "equals" in cond:
                if not rows:
                    problems.append(f"{what}: no row, expected one with {cond['equals']}")
                for row in rows:
                    for c, v in cond["equals"].items():
                        want = _stored(schema, c, v)
                        if not _same(want, row.get(c)):
                            got = _plain(row.get(c))
                            problems.append(f"{what}: {c} is {got!r}, expected {want!r}")
    return problems


def _body(response: Any) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _step_request(step: Step | Await | Rpc) -> dict[str, Any]:
    if isinstance(step, Rpc):
        return {
            "rpc": f"{step.service}/{step.method}",
            "user": step.user,
            "message": step.message,
        }
    if isinstance(step, Await):
        return {
            "await": step.service,
            "within_s": step.within_s,
            "expect_db": dict(step.expect_db),
        }
    return {
        "method": step.method,
        "path": step.path,
        "user": step.user,
        "params": dict(step.params) if step.params else None,
        "json": step.json,
    }


def _step_endpoint(step: Step | Await | Rpc) -> str:
    """``METHOD /path`` of a request step; ``service <name>`` of an Await; ``RPC
    <Service>/<Method>`` of an Rpc."""
    if isinstance(step, Rpc):
        return step.endpoint
    if isinstance(step, Await):
        return f"service {step.service}"
    return f"{step.method} {step.path}"


def expect_text(expect: Mapping[str, Any]) -> str:
    """An ``expect_db`` in words: ``runs where {'id': 1} equals {'status': 'done'}``."""
    parts = []
    for table, cond in expect.items():
        where = cond.get("where") if isinstance(cond, Mapping) else None
        text = f"{table}" + (f" where {dict(where)}" if where else "")
        if isinstance(cond, Mapping) and "count" in cond:
            text += f" count={cond['count']}"
        if isinstance(cond, Mapping) and "equals" in cond:
            text += f" equals {dict(cond['equals'])}"
        parts.append(text)
    return "; ".join(parts)


def _observed_counts(spec: Spec, db: DbRef, expect: Mapping[str, Any]) -> dict[str, Any]:
    """Per ``expect_db`` entry, how many rows match its ``where`` now."""
    out: dict[str, Any] = {}
    with open_db(db) as conn:
        for table, cond in expect.items():
            where = dict(cond.get("where") or {}) if isinstance(cond, Mapping) else {}
            label = f"{table}" + (f" where {where}" if where else "")
            found = resolve_table(spec.profile, table)
            if found is None:
                out[label] = "unknown table"
                continue
            _, sql, schema = found
            clause, args = _where(conn, schema, where)
            try:
                out[label] = conn.scalar(
                    f"SELECT COUNT(*) FROM {conn.quote_ident(sql)}{clause}", args
                )
            except DB_ERRORS as err:
                out[label] = f"read failed: {err}"
    return out


def missing_services(spec: Spec, services: Mapping[str, Any]) -> list[str]:
    """The services the spec's scenarios await that ``services`` does not start."""
    awaited = [
        step.service
        for scenario in spec.scenarios
        for step in scenario.steps
        if isinstance(step, Await)
    ]
    return [name for name in dict.fromkeys(awaited) if name not in services]


def run_await(
    spec: Spec, step: Await, db: DbRef, services: Services
) -> tuple[str, list[str], dict[str, Any]]:
    """Start ``step``'s service on ``db``, poll until its ``expect_db`` holds or
    ``within_s`` runs out, and stop the service (always). Answers the verdict
    (``correct``, ``silent``: the service ran and did not bring it about, ``loud``: it
    could not start, or exited with an error), the problems and the details."""
    name = step.service
    want = expect_text(step.expect_db)
    details: dict[str, Any] = {"service": name, "within_s": step.within_s,
                               "expect_db": dict(step.expect_db)}  # fmt: skip
    try:
        cmd = services[name](db)
        details["command"] = cmd.describe()
        service = start_service(name, cmd)
    except Exception as err:  # noqa: BLE001 -- a service that cannot start is loud
        problem = f"service {name} could not start: {type(err).__name__}: {err}"
        return "loud", [problem], details | {"problems": [problem]}
    started = time.monotonic()
    deadline = started + step.within_s
    try:
        while True:
            code = service.poll()  # before the read: an exited service's writes are seen
            try:
                problems = _expect_db(spec, db, step.expect_db)
            except DB_ERRORS as err:
                problems = [f"db read failed: {err}"]
            if not problems:
                details["seconds"] = round(time.monotonic() - started, 3)
                return "correct", [], details
            now = time.monotonic()
            if code is not None or now >= deadline:
                break
            time.sleep(min(POLL_S, max(0.0, deadline - now)))
        observed = _observed_counts(spec, db, step.expect_db)
        verdict = "silent"
        head = f"service {name} did not bring about {want} within {step.within_s:g}s"
        if code is not None:
            head = f"service {name} exited with code {code} without bringing about {want}"
            details["exit_code"] = code
            details["stderr"] = service.stderr_text()
            if code != 0:
                verdict = "loud"
        lines = [f"{head} (last observed: {observed})", *problems]
        details["observed"] = observed
        details["problems"] = lines
        return verdict, lines, details
    finally:
        service.stop()


class ScenarioClock:
    """Where one scenario's seconds go (the report's ``timings.scenarios[]``).

    ``seconds``: the whole scenario, wall clock. Its parts, in order: ``db_clone_s``
    (``make_db``: a server database's clone, a SQLite seed file), ``given_s`` (the
    ``given`` rows), ``app_start_s`` (the app factory, the runtime's ``open`` and the
    client's ``__enter__``: a process app's spawn and health wait), ``steps_s`` (the
    steps, logins and ``Await`` steps included), ``app_stop_s`` (the runtime's dispose
    hooks: a process app's SIGTERM and reap) and ``db_release_s`` (dropping a server
    clone). Included in those, not added to them: ``login_s`` (getting tokens),
    ``await_s`` (``Await`` steps: a background service polled until its effect shows or
    ``within_s`` runs out), ``invariants_s`` (layer C's SQL, after every request) and,
    for a process app, ``process_spawn_s`` / ``health_wait_s`` (of ``app_start_s``) and
    ``process_stop_s`` (of ``app_stop_s``). ``requests``: the steps' requests sent;
    ``logins``: the tokens got; ``awaits``: the ``Await`` steps run. Measuring costs a
    few ``perf_counter`` calls per step."""

    PARTS = ("db_clone_s", "given_s", "app_start_s", "steps_s", "app_stop_s", "db_release_s")

    def __init__(self, name: str, monitor: InvariantMonitor | None = None) -> None:
        self.name = name
        self.monitor = monitor
        self.values: dict[str, float] = dict.fromkeys(self.PARTS, 0.0)
        self.values |= {"login_s": 0.0, "await_s": 0.0, "invariants_s": 0.0}
        self.requests = 0
        self.logins = 0
        self.awaits = 0
        self._started = time.perf_counter()
        self._inv0 = monitor.seconds if monitor is not None else 0.0
        self.seconds: float | None = None

    def part(self, key: str) -> _Lap:
        return _Lap(self, key)

    def add(self, key: str, seconds: float) -> None:
        self.values[key] = self.values.get(key, 0.0) + seconds

    def timed(self, key: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def run(*args: Any) -> Any:
            with self.part(key):
                return fn(*args)

        return run

    def process(self, app: Any) -> None:
        """A process app's own start and stop seconds (``ProcessApp.timings``)."""
        found = getattr(app, "timings", None)
        if isinstance(found, Mapping):
            for key in ("process_spawn_s", "health_wait_s", "process_stop_s"):
                if key in found:
                    self.values[key] = float(found[key])

    def stop(self) -> dict[str, Any]:
        if self.seconds is None:
            self.seconds = time.perf_counter() - self._started
            if self.monitor is not None:
                self.values["invariants_s"] = self.monitor.seconds - self._inv0
        out: dict[str, Any] = {"name": self.name, "seconds": round(self.seconds, 4)}
        out |= {k: round(v, 4) for k, v in self.values.items()}
        out |= {"requests": self.requests, "logins": self.logins, "awaits": self.awaits}
        return out


class _Lap:
    __slots__ = ("clock", "key", "t0")

    def __init__(self, clock: ScenarioClock, key: str) -> None:
        self.clock, self.key, self.t0 = clock, key, 0.0

    def __enter__(self) -> None:
        self.t0 = time.perf_counter()

    def __exit__(self, *exc: object) -> None:
        self.clock.add(self.key, time.perf_counter() - self.t0)


def _run_scenario(
    spec: Spec,
    scenario: Scenario,
    report: Report,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    monitor: InvariantMonitor | None,
    login: Login | None,
    runtime: AppRuntime | None = None,
    services: Services | None = None,
    clock: ScenarioClock | None = None,
) -> None:
    """One scenario on a fresh seeded database (see the module docstring). ``runtime``:
    the app's transport, auth driver and dispose hooks (default: FastAPI's TestClient,
    a bearer header, the engine's dispose). ``services``: how the services its
    ``Await`` steps name start. ``clock``: where its seconds go (:class:`ScenarioClock`)."""
    from fastapi.testclient import TestClient

    rule = f"scenario: {scenario.name}"
    if clock is None:
        clock = ScenarioClock(scenario.name, monitor)

    def fail(
        label: str, step: Step | Await | Rpc | None, verdict: str, details: dict[str, Any]
    ) -> None:
        report.findings.append(
            CheckFinding(
                layer="D",
                verdict=verdict,  # type: ignore[arg-type]
                rule=rule,
                endpoint=_step_endpoint(step) if step else None,
                case=label,
                request=_step_request(step) if step else None,
                details=details,
            )
        )

    with tempfile.TemporaryDirectory(prefix="lattice-check-d-") as name, ExitStack() as cleanup:
        with clock.part("db_clone_s"):
            db = make_db("seed", Path(name) / "scenario.db")
        cleanup.callback(clock.timed("db_release_s", release_db), make_db, db)
        if scenario.given:
            try:
                with clock.part("given_s"):
                    _insert_given(spec, db, scenario.given)
            except DB_ERRORS as err:
                # the app's own schema refuses the scenario's starting state (e.g. a unique
                # index the spec no longer has): a finding about this scenario, not a crash
                fail(
                    f"{scenario.name} / given",
                    None,
                    "loud",
                    {"error": f"the app's database refused the scenario's given rows: "
                              f"{type(err).__name__}: {err}"},
                )
                return
            if monitor is not None:
                monitor.check(db, f"{scenario.name} / given")
        factory = app_factory
        # the violations the database has before the app is built: an Await's layer C
        # check does not blame them on the service
        start_violations = monitor.violation_keys(db) if monitor is not None else {}
        if monitor is not None and _asgi(runtime):
            factory = monitor.wrap(app_factory)
        elif monitor is not None:
            factory, runtime = monitor.watch(app_factory, runtime)  # type: ignore[arg-type]
        try:
            with clock.part("app_start_s"):
                app = factory(db)
        except Exception as err:  # noqa: BLE001 -- an app that does not start is loud
            fail(
                f"{scenario.name} / start", None, "loud", {"error": f"{type(err).__name__}: {err}"}
            )
            return
        try:
            with ExitStack() as serve:
                with clock.part("app_start_s"):
                    serving = (
                        TestClient(app, raise_server_exceptions=False)
                        if runtime is None
                        else runtime.open(app)
                    )
                    client = serve.enter_context(serving)
                clock.process(app)
                prime = getattr(client, "rpc", None) if spec.protos else None
                if callable(prime):  # a gRPC app: the protos its rpc transport describes
                    prime(spec.protos)
                with clock.part("steps_s"):
                    tokens: dict[str, str] = {}
                    for i, step in enumerate(scenario.steps, 1):
                        label = f"{scenario.name} / step {i}"
                        if isinstance(step, Rpc):
                            with clock.part("rpc_s"):
                                goes_on = _rpc_step(spec, scenario, step, i, label, client,
                                                    runtime, login, tokens, monitor, db,
                                                    report, clock, fail)  # fmt: skip
                            if not goes_on:
                                return
                            continue
                        if isinstance(step, Await):
                            clock.awaits += 1
                            with clock.part("await_s"):
                                goes_on = _await_step(spec, step, label, db, report, monitor,
                                                      start_violations, services or {},
                                                      fail)  # fmt: skip
                            if not goes_on:
                                return
                            continue
                        headers = {}
                        if step.user is not None:
                            if step.user not in tokens:
                                if monitor is not None:
                                    monitor.label = f"{scenario.name} / login as {step.user}"
                                clock.logins += 1
                                try:
                                    with clock.part("login_s"):
                                        if runtime is not None:
                                            tokens[step.user] = runtime.auth.token(
                                                client, step.user, login
                                            )
                                        elif login is None:
                                            raise RuntimeError("no login given")
                                        else:
                                            tokens[step.user] = login(client, step.user)
                                except Exception as err:  # noqa: BLE001 -- loud: cannot run
                                    fail(
                                        label,
                                        step,
                                        "loud",
                                        {"error": f"cannot log in as {step.user!r}: {err}"},
                                    )
                                    report.cases[label] = {
                                        "layer": "D",
                                        "endpoint": rule,
                                        "verdict": "loud",
                                        "reason": str(err),
                                        "request": _step_request(step),
                                    }
                                    return
                            if runtime is not None:
                                headers = dict(runtime.auth.present(client, tokens[step.user]))
                            else:
                                headers["Authorization"] = f"Bearer {tokens[step.user]}"
                        if monitor is not None:
                            monitor.label = label
                            monitor.requests[label] = _step_request(step)
                            monitor.endpoints[label] = f"{step.method} {step.path}"
                        clock.requests += 1
                        response = client.request(
                            step.method,
                            step.path,
                            params=dict(step.params) if step.params else None,
                            json=step.json,
                            headers=headers,
                        )
                        body = _body(response)
                        problems = []
                        if response.status_code != step.expect_status:
                            problems.append(
                                f"status: expected {step.expect_status}, got {response.status_code}"
                            )
                        if step.expect_body is not None and not subset(step.expect_body, body):
                            problems.append(f"body: expected {step.expect_body!r} within {body!r}")
                        if step.expect_db:
                            problems += _expect_db(spec, db, step.expect_db)
                            problems += unestablished(scenario, i, step.expect_db, problems)
                        verdict = "correct"
                        if problems:
                            verdict = "loud" if response.status_code >= 500 else "silent"
                        report.cases[label] = {
                            "layer": "D",
                            "endpoint": f"{step.method} {step.path}",
                            "verdict": verdict,
                            "reason": "; ".join(problems),
                            "request": _step_request(step),
                        }
                        if problems:
                            fail(
                                label,
                                step,
                                verdict,
                                {
                                    "problems": problems,
                                    "expected": {
                                        "status": step.expect_status,
                                        "body": step.expect_body,
                                        "db": step.expect_db,
                                    },
                                    "actual": {"status": response.status_code, "body": body},
                                },
                            )
                            return
        finally:
            with clock.part("app_stop_s"):
                _close(runtime, app)
            clock.process(app)


# codes whose answer is a server fault, as a 5xx is for an HTTP step
LOUD_CODES = frozenset({"UNKNOWN", "DEADLINE_EXCEEDED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS"})


def _rpc_step(
    spec: Spec,
    scenario: Scenario,
    step: Rpc,
    i: int,
    label: str,
    client: Any,
    runtime: AppRuntime | None,
    login: Login | None,
    tokens: dict[str, str],
    monitor: InvariantMonitor | None,
    db: DbRef,
    report: Report,
    clock: ScenarioClock,
    fail: Callable[[str, Step | Await | Rpc | None, str, dict[str, Any]], None],
) -> bool:
    """An Rpc step of layer D (Spike I): the unary call through the app's gRPC transport,
    the caller's token as the metadata ``authorization: Bearer <token>``, then the code,
    the response and the database compared, and layer C's invariants run after it exactly
    as after an HTTP step. Whether the scenario goes on."""
    from lattice.spec.grpc import BridgeError, GrpcAnswer, bearer

    reach = getattr(client, "rpc", None)
    if not callable(reach):
        err = "the app's transport has no gRPC client: serve it with ProcessApp(ready='grpc')"
        fail(label, step, "loud", {"error": err})
        report.cases[label] = {"layer": "D", "endpoint": _step_endpoint(step), "verdict": "loud",
                               "reason": err, "request": _step_request(step)}  # fmt: skip
        return False
    rpc = reach(spec.protos)
    token = None
    if step.user is not None:
        if step.user not in tokens:
            if monitor is not None:
                monitor.label = f"{scenario.name} / login as {step.user}"
            clock.logins += 1
            try:
                with clock.part("login_s"):
                    if runtime is not None:
                        tokens[step.user] = runtime.auth.token(client, step.user, login)
                    elif login is None:
                        raise RuntimeError("no login given")
                    else:
                        tokens[step.user] = login(client, step.user)
            except Exception as err:  # noqa: BLE001 -- loud: cannot run
                fail(label, step, "loud", {"error": f"cannot log in as {step.user!r}: {err}"})
                report.cases[label] = {"layer": "D", "endpoint": _step_endpoint(step),
                                       "verdict": "loud", "reason": str(err),
                                       "request": _step_request(step)}  # fmt: skip
                return False
        token = tokens[step.user]
    if monitor is not None:
        monitor.label = label
        monitor.requests[label] = _step_request(step)
        monitor.endpoints[label] = _step_endpoint(step)
    clock.requests += 1
    try:
        answer = rpc.call(step.service, step.method, step.message, bearer(token, step.metadata))
    except BridgeError as err:
        problem = f"the rpc could not be made: {err}"
        fail(label, step, "loud", {"problems": [problem]})
        report.cases[label] = {"layer": "D", "endpoint": _step_endpoint(step), "verdict": "loud",
                               "reason": problem, "request": _step_request(step)}  # fmt: skip
        return False
    after = getattr(client, "_after", None)
    if monitor is not None and callable(after):  # layer C: the invariants after the rpc
        after("RPC", f"/{step.service}/{step.method}")
    problems: list[str] = []
    if answer.code != step.expect_code:
        problems.append(f"code: expected {step.expect_code}, got {answer.code}"
                        + (f" ({answer.message})" if answer.message else ""))  # fmt: skip
    got: Any = answer.as_body() if isinstance(answer, GrpcAnswer) else None
    if step.expect_message is not None and not subset(step.expect_message, got):
        problems.append(f"message: expected {step.expect_message!r} within {got!r}")
    if step.expect_db:
        problems += _expect_db(spec, db, step.expect_db)
        problems += unestablished(scenario, i, step.expect_db, problems)
    verdict = "correct"
    if problems:
        verdict = "loud" if answer.code in LOUD_CODES else "silent"
    report.cases[label] = {
        "layer": "D",
        "endpoint": _step_endpoint(step),
        "verdict": verdict,
        "reason": "; ".join(problems),
        "request": _step_request(step),
        "code": answer.code,
    }
    if problems:
        # "status" carries the code, so a reader of an HTTP finding (the ask verifier, the
        # studio) prints an rpc finding as it prints any: "expected NOT_FOUND, got OK"
        fail(label, step, verdict, {
            "problems": problems,
            "expected": {"status": step.expect_code, "code": step.expect_code,
                         "body": step.expect_message, "db": step.expect_db},
            "actual": {"status": answer.code, "code": answer.code, "body": got},
        })  # fmt: skip
        return False
    return True


def _await_step(
    spec: Spec,
    step: Await,
    label: str,
    db: DbRef,
    report: Report,
    monitor: InvariantMonitor | None,
    baseline: Mapping[str, set[str]],
    services: Services,
    fail: Callable[[str, Step | Await | None, str, dict[str, Any]], None],
) -> bool:
    """An Await step of layer D (with layer C's invariants after it, on the database the
    service wrote); whether the scenario goes on."""
    try:
        verdict, problems, details = run_await(spec, step, db, services)
    except Exception as err:  # noqa: BLE001 -- the wait could not run: loud
        verdict, problems = "loud", [f"service {step.service}: {type(err).__name__}: {err}"]
        details = {"service": step.service, "problems": problems}
    report.cases[label] = {
        "layer": "D",
        "endpoint": _step_endpoint(step),
        "verdict": verdict,
        "reason": "; ".join(problems),
        "request": _step_request(step),
    }
    if monitor is not None:
        monitor.label = label
        monitor.requests[label] = _step_request(step)
        monitor.endpoints[label] = _step_endpoint(step)
        with monitor.lock:
            monitor.check(db, label, baseline=baseline)
    if problems:
        fail(label, step, verdict, details)
        return False
    return True


# ---- layer R --------------------------------------------------------------------------------


def _layer_r(
    spec: Spec,
    report: Report,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    monitor: InvariantMonitor | None,
    *,
    login: Login | None,
    mint_token: Callable[[Any], str] | None,
    sessions: Any,
    scheme: str,
    only: Sequence[str] | None,
    runtime: AppRuntime | None = None,
    options: Mapping[str, Any] | None = None,
) -> None:
    from lattice.spec.probes import asgi_sessions, run_probes

    on_probe = None
    options = dict(options or {})
    if runtime is not None and runtime.framework == "dotnet":
        options.setdefault("relogin", True)  # ASP.NET Core signs tokens with a key made at start-up
    if sessions is None and runtime is not None:
        from contextlib import contextmanager

        rt, served_factory = runtime, app_factory
        if monitor is not None:
            served_factory, rt = monitor.watch(app_factory, runtime)

            def on_probe(probe: Any) -> None:
                monitor.label = f"R | {probe.name}"
                monitor.requests[monitor.label] = probe.request()
                monitor.endpoints[monitor.label] = probe.route

        @contextmanager
        def served(db: Path) -> Iterator[Any]:
            with rt.serve(served_factory, db) as (_, transport):
                yield transport

        sessions = served
        options.setdefault("auth_headers", runtime.auth.headers)
    if sessions is None:
        factory = app_factory
        if monitor is not None:
            factory = monitor.wrap(app_factory)

            def on_probe(probe: Any) -> None:
                monitor.label = f"R | {probe.name}"
                monitor.requests[monitor.label] = probe.request()
                monitor.endpoints[monitor.label] = probe.route

        sessions = asgi_sessions(factory)
    elif monitor is not None:
        report.notes.append("layer C does not watch layer R's probes through a given transport")
    try:
        result = run_probes(
            spec,
            sessions,
            make_db,
            login=login,
            mint_token=mint_token,
            scheme=scheme,
            only=only,
            on_probe=on_probe,
            **options,
        )
    except CheckError:
        raise
    except Exception as err:  # noqa: BLE001 -- the app could not run the probes: loud
        report.findings.append(
            CheckFinding(
                layer="R",
                verdict="loud",
                rule="the app answers the rule probes",
                details={"error": f"{type(err).__name__}: {err}"},
            )
        )
        return
    report.rules = result
    report.notes.extend(result.notes)
    for item_id, v in result.items.items():
        if v.verdict != "refuted":
            continue
        record = result.probes.get(v.witness or "", {})
        status = record.get("status")
        endpoint = record.get("route") or (v.item.subject if " /" in v.item.subject else None)
        report.findings.append(
            CheckFinding(
                layer="R",
                verdict="loud" if isinstance(status, int) and status >= 500 else "silent",
                rule=v.item.sentence,
                endpoint=endpoint,
                case=f"{item_id} | {v.witness}" if v.witness else item_id,
                request=record.get("request"),
                details={
                    "item": item_id,
                    "reason": v.reason,
                    "predicted": dict(v.item.predicted),
                    "observed": v.observed,
                    "actual": {"status": status, "body": record.get("body")},
                },
                db_diff=record.get("db_diff") or {},
            )
        )


# ---- the engine -----------------------------------------------------------------------------


def check(
    spec: Spec,
    app_factory: AppFactory,
    db_factory: Callable[..., Any],
    *,
    layers: str = "ABCD",
    app_dir: Path | str | None = None,
    package: str | None = None,
    policy: Policy | GoPolicy | None = None,
    go_src: Path | str | None = None,
    login: Login | None = None,
    login_path: str | None = None,
    mint_token: Callable[[Any], str] | None = None,
    cases_for: CasesFor = cases_for,
    extra_cases: Sequence[Case] = (),
    rules: Sequence[Rule] = (ValidationEnvelope(),),
    natives: Mapping[str, Callable[..., object]] | None = None,
    settings: Mapping[str, object] | None = None,
    hash_columns: Sequence[str] = ("hashed_password",),
    max_rows: int = 6,
    baseline: Sequence[Accepted] = (),
    sessions: Any = None,
    scheme: str = "Bearer",
    rule_items: Sequence[str] | None = None,
    runtime: AppRuntime | None = None,
    probe_options: Mapping[str, Any] | None = None,
    x_k: int = 12,
    x_repeats: int = 3,
    x_budget: int = 60,
    x_timeout: float = 30.0,
    services: Services | None = None,
    database: str = "sqlite",
    database_url: str | None = None,
    only: Sequence[str] | None = None,
    trace: Any = None,
    await_cap: float | None = None,
) -> Report:
    """Check the app built by ``app_factory(db_path)`` against ``spec`` in ``layers``
    (letters of ABCDRX; see the module docstring). Layer X: ``x_k`` copies per
    self-race, ``x_repeats`` tries per race, at most ``x_budget`` races, each within
    ``x_timeout`` seconds. Layer R: ``sessions`` is the probes'
    transport (:data:`lattice.spec.probes.SessionFactory`; default the ASGI TestClient
    over ``app_factory``), ``scheme`` the Authorization scheme, ``rule_items`` the item id
    prefixes to probe (default all), ``probe_options`` more of
    :func:`lattice.spec.probes.run_probes`' options (refusals, login route...).
    ``runtime`` (``lattice.importer.transport.AppRuntime``; default FastAPI's
    TestClient, a bearer header and the engine's dispose): how B, D and R serve the app
    and act as its users -- a Django or Flask app's. ``db_factory`` writes the seeded
    database. ``policy``: layer A's base policy (default: derived from the app; for a Go
    app a :class:`lattice.gocheck.GoPolicy`, default built from the spec). ``go_src``:
    a Go app's module root, read by layer A's gocheck (default ``app_dir`` when the
    runtime's framework is ``"go"``). ``services``: how the background services the
    scenarios' ``Await`` steps name start, ``{name: (db_path) -> ServiceCmd}``
    (:mod:`lattice.spec.services`; laid over ``runtime.services``).
    ``cases_for(spec, endpoint, ctx)``: layer B's case generator; ``extra_cases`` run
    too. ``rules``: verify's representation rules (default: 422 bodies compare by the
    fields they flag). ``natives``/``settings``: for the spec's programs. ``baseline``:
    the app's accepted findings (:class:`Accepted`; with ``spec.approved``, the spec's
    own), reported but not failing. ``database``: ``sqlite`` (the default: ``db_factory``,
    ``app_factory`` and services get a file path), ``postgres``, ``mysql`` or
    ``clickhouse`` (they get the canonical URL of a database on a server, see
    :mod:`lattice.spec.db`): a throwaway Docker container, or the server at
    ``database_url`` (or ``$LATTICE_<DIALECT>_URL``) -- :mod:`lattice.spec.dbserver`.
    ``only``: selectors (:mod:`lattice.spec.selection`) restricting layers D and X to
    the scenarios they name (None: every scenario); ``trace``: the project trace
    (a ``Trace``, its JSON mapping or file) that ``rule:<id>`` selectors read.
    ``await_cap``: seconds every ``Await`` waits at most (its ``within_s`` capped; a
    note in the report; an effect that does not show within the cap still fails the
    scenario); None: as the spec says. The
    report's ``timings`` say where the seconds went (per layer; per scenario of D:
    :class:`ScenarioClock`). Raises :class:`CheckError` when the check cannot run
    (:class:`lattice.spec.selection.SelectionError`: a selector matching nothing)."""
    started = time.perf_counter()
    try:
        dialect = check_dialect(database)
    except ValueError as err:
        raise CheckError(str(err)) from err
    if database_url is not None and dialect == "sqlite":
        raise CheckError("database_url is for a server database: give database= too")
    run = _layers(layers)
    problems = validate(spec)
    if problems:
        raise CheckError("the spec is not valid:\n  " + "\n  ".join(p.render() for p in problems))
    rpc_skipped = ""
    if any(isinstance(st, Rpc) for sc in spec.scenarios for st in sc.steps):
        # layers A and X do not read or race rpcs this round (Spike I, D9)
        rpc_skipped = "".join(x for x in "AX" if x in run)
        run = "".join(x for x in run if x not in rpc_skipped)
    if login is None and login_path is not None and spec.users:
        login = form_login(login_path, dict(spec.users))
    report = Report(layers=run, unchecked=[u.text for u in spec.unchecked])
    report.timings = {"total_s": 0.0, "layers": {}, "setup": {}, "scenarios": []}
    for x in rpc_skipped:
        report.notes.append(
            f"rpc_layer_not_supported:{x}: layer {x} is not run on a spec with Rpc steps "
            + (
                "(layer A reads Gin routes, not rpc handlers; exit: GO8xx rules take a handler set)"
                if x == "A"
                else "(races need concurrent calls; exit: the bridge supports concurrent calls)"
            )
        )
    scenario_spec = spec
    if only is not None:
        from lattice.spec.selection import select_scenarios

        names = select_scenarios(spec, only, trace)
        chosen = set(names)
        scenario_spec = replace(spec, scenarios=tuple(s for s in spec.scenarios
                                                      if s.name in chosen))  # fmt: skip
        report.selection = {"selectors": list(only), "scenarios": names,
                            "of": len(spec.scenarios)}  # fmt: skip
        report.notes.append(
            f"--only: layers D and X ran {len(names)} of the spec's {len(spec.scenarios)} "
            "scenarios; layers A, B, C and R are not narrowed"
        )
    if await_cap is not None:
        if isinstance(await_cap, bool) or not isinstance(await_cap, int | float) or \
                not await_cap > 0:  # fmt: skip
            raise CheckError(f"await_cap must be a positive number of seconds, not {await_cap!r}")
        cap = float(await_cap)
        capped = 0
        scenarios = []
        for sc in scenario_spec.scenarios:
            steps = []
            for st in sc.steps:
                if isinstance(st, Await) and st.within_s > cap:
                    st = replace(st, within_s=cap)
                    capped += 1
                steps.append(st)
            scenarios.append(replace(sc, steps=tuple(steps)))
        scenario_spec = replace(scenario_spec, scenarios=tuple(scenarios))
        report.notes.append(
            f"--await-cap {cap:g}: every Await waited at most {cap:g}s ({capped} step(s) "
            "capped); an effect that does not show within the cap still fails its scenario"
        )
    if runtime is None:
        from lattice.spec.process import process_runtime, returns_process_app

        if returns_process_app(app_factory):
            runtime = process_runtime()
    harness: dict[str, Any] = dict(getattr(runtime, "services", None) or {})
    harness.update(services or {})
    if "D" in run:
        missing = missing_services(scenario_spec, harness)
        if missing:
            raise CheckError(
                f"the spec's scenarios await service(s) {', '.join(missing)}, which the "
                "harness does not start: give check(services={name: (db_path) -> "
                "ServiceCmd}), process_runtime(services=...) or --services MOD:NAME"
            )

    if "A" in run:
        t0 = time.perf_counter()
        _layer_a(spec, report, app_dir, package, policy, runtime, go_src)
        report.timings["layers"]["A"] = round(time.perf_counter() - t0, 4)
    opts = {
        "login": login, "mint_token": mint_token, "runtime": runtime, "harness": harness,
        "cases_for": cases_for, "extra_cases": extra_cases, "rules": rules,
        "natives": natives, "settings": settings, "hash_columns": hash_columns,
        "max_rows": max_rows, "sessions": sessions, "scheme": scheme,
        "rule_items": rule_items, "probe_options": probe_options, "x_k": x_k,
        "x_repeats": x_repeats, "x_budget": x_budget, "x_timeout": x_timeout,
        "scenario_spec": scenario_spec,
    }  # fmt: skip
    if dialect == "sqlite":
        _run_layers(spec, report, run, app_factory, as_db_factory(db_factory), opts)
    else:
        _on_server(spec, report, run, app_factory, db_factory, dialect, database_url, opts)
    order = {layer: i for i, layer in enumerate(LAYERS)}
    report.findings.sort(key=lambda f: order[f.layer])
    report.apply_baseline((*spec.approved, *baseline))
    report.timings["layers"] = {x: report.timings["layers"][x] for x in LAYERS
                                if x in report.timings["layers"]}  # fmt: skip
    report.timings["total_s"] = round(time.perf_counter() - started, 4)
    return report


def _on_server(
    spec: Spec,
    report: Report,
    run: str,
    app_factory: AppFactory,
    db_factory: Callable[..., Any],
    dialect: str,
    database_url: str | None,
    opts: Mapping[str, Any],
) -> None:
    """The dynamic layers on a server database: one server for the check (a container
    removed at the end, or the given one), the seed built once, a clone per use."""
    from lattice.spec.dbserver import DbServerError, ServerDatabases, server_for

    skipped = [x for x in "BR" if x in run]
    for x in skipped:
        what = ("compares the app with the spec's programs, which the Lattice runtime "
                "serves on SQLite") if x == "B" else (
                "reads and diffs whole tables per probe, a SQLite-only harness")  # fmt: skip
        report.notes.append(f"layer {x} skipped on {dialect}: it {what}; run it with "
                            "database='sqlite'")  # fmt: skip
    run = "".join(x for x in run if x not in "BR")
    if not any(x in run for x in "CDX"):
        return
    setup = report.timings["setup"] if report.timings is not None else {}
    t0 = time.perf_counter()
    try:
        server, owned = server_for(dialect, database_url)
    except DbServerError as err:
        raise CheckError(str(err)) from err
    setup["database_server_s"] = round(time.perf_counter() - t0, 4)
    dbs = ServerDatabases(server, db_factory)
    try:
        _run_layers(spec, report, run, app_factory, dbs, opts)
        report.notes.append(dbs.timing_note())
    finally:
        for variant, seconds in dbs.seed_seconds.items():
            setup[f"seed_build_s.{variant}"] = round(seconds, 4)
        if dbs.timings:
            setup["db_clones"] = len(dbs.timings)
            setup["db_clone_mean_s"] = round(sum(dbs.timings) / len(dbs.timings), 4)
        t0 = time.perf_counter()
        dbs.close()
        # an owned server's container is removed; a given server's admin connection closed
        server.stop()
        setup["database_teardown_s"] = round(time.perf_counter() - t0, 4)
        if not owned:
            report.notes.append(f"databases created on {server.describe()} were dropped")


def _run_layers(
    spec: Spec,
    report: Report,
    run: str,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Any],
    opts: Mapping[str, Any],
) -> None:
    """Layers B, C, D, R and X (those of ``run``) on the databases ``make_db`` makes."""
    login = opts["login"]
    mint_token, runtime, harness = opts["mint_token"], opts["runtime"], opts["harness"]
    # layers D and X run the selected scenarios (check(only=...)); the others the spec's
    scenario_spec: Spec = opts.get("scenario_spec") or spec
    dialect = db_dialect(make_db)
    timings = report.timings if report.timings is not None else {"layers": {}, "scenarios": []}
    lap = time.perf_counter()

    def layer_done(x: str) -> None:
        nonlocal lap
        now = time.perf_counter()
        timings["layers"][x] = round(timings["layers"].get(x, 0.0) + now - lap, 4)
        lap = now

    monitor: InvariantMonitor | None = None
    if "C" in run:
        monitor = InvariantMonitor.for_spec(spec, dialect)
        if not monitor.invariants:
            report.notes.append("layer C: the spec has no invariants")
        with tempfile.TemporaryDirectory(prefix="lattice-check-c-") as name:
            try:
                seed_db = make_db("seed", Path(name) / "seed.db")
            except CheckError:
                raise
            except Exception as err:
                raise CheckError(f"the db factory failed: {type(err).__name__}: {err}") from err
            try:
                monitor.set_seed(seed_db)
            finally:
                release_db(make_db, seed_db)
        if "B" not in run and "D" not in run and "R" not in run:
            report.notes.append("layer C ran on the seed only: run it with B or D")
        layer_done("C")
    if "B" in run:
        _layer_b(
            spec,
            report,
            app_factory,
            make_db,
            monitor,
            login=login,
            mint_token=mint_token,
            generate=opts["cases_for"],
            extra_cases=opts["extra_cases"],
            rules=opts["rules"],
            natives=opts["natives"],
            settings=opts["settings"],
            hash_columns=opts["hash_columns"],
            max_rows=opts["max_rows"],
            runtime=runtime,
        )
        layer_done("B")
    if "D" in run:
        if not scenario_spec.scenarios:
            report.notes.append("layer D: the spec has no scenarios")
        for scenario in scenario_spec.scenarios:
            clock = ScenarioClock(scenario.name, monitor)
            _run_scenario(spec, scenario, report, app_factory, make_db, monitor, login, runtime,
                          harness, clock)  # fmt: skip
            timings["scenarios"].append(clock.stop())
        layer_done("D")
    if "R" in run:
        _layer_r(
            spec,
            report,
            app_factory,
            make_db,
            monitor,
            login=login,
            mint_token=mint_token,
            sessions=opts["sessions"],
            scheme=opts["scheme"],
            only=opts["rule_items"],
            runtime=runtime,
            options=opts["probe_options"],
        )
        layer_done("R")
    if "X" in run:
        from lattice.spec.race import layer_x

        layer_x(
            scenario_spec,
            report,
            app_factory,
            make_db,
            login=login,
            runtime=runtime,
            monitor=None if _asgi(runtime) else monitor,
            k=opts["x_k"],
            repeats=opts["x_repeats"],
            budget=opts["x_budget"],
            timeout=opts["x_timeout"],
        )
        layer_done("X")
    if monitor is not None:
        report.findings.extend(monitor.findings)
        timings["invariants_s"] = round(monitor.seconds, 4)


__all__ = [
    "CaseContext",
    "CasesFor",
    "CheckError",
    "InvariantMonitor",
    "as_db_factory",
    "cases_for",
    "check",
    "db_diff",
    "expect_text",
    "missing_services",
    "run_await",
    "spec_plans",
    "spec_policy",
    "subset",
]
