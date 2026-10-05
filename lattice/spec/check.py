"""The check engine: an app against its spec, in four layers (plan 2026-10-04, W2-A).

* **A** -- pycheck on the app's source (``app_dir``, ``package``) with a policy built
  from the spec: pycheck's derived policy, with the spec's owners, ``owner_via``, private
  columns and (when the policy has the field) state machines laid over it
  (:func:`spec_policy`). The app under check is never its own baseline: every finding
  counts. Skipped (with a note) when the source is not given.
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
  checked. A scenario stops at its first failing step.
* **R** -- the rule layer (plan 2026-10-05 phase 2, §1): every rule item of the spec's
  profile and route contracts (:mod:`lattice.spec.probes`) probed on the app, each probe
  a request on a fresh copy of the seed. Every item gets a verdict (``report.rules``); a
  refuted item is a finding (key ``R:<endpoint>:<item id> | <probe>``). With C, the
  invariants run after every probe too. Not in the default layers.

Callers: ``login(client, handle)`` answers a bearer token for a handle of
``Spec.users`` (or ``login_path``: a form login with ``Spec.users``' credentials).
Layer B logs every handle in once, on the app, on a scratch copy of the seed, and uses
those tokens on both sides; ``mint_token(user_key)``, when given, adds the
"unknown user" token case.

``db_factory`` writes the seeded database: ``db_factory(path)`` or, like the importer's,
``db_factory(variant, path)``.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from lattice.api.ir import EndpointPlan, lower_api
from lattice.api.runtime import build_app
from lattice.check import check_source
from lattice.check.api_info import MISSING
from lattice.diagnostics import Severity
from lattice.importer.verify import Case, CaseRecord, Rule, ValidationEnvelope, form_login, verify
from lattice.pycheck import CODES, Policy, derive_policy, run_pycheck
from lattice.spec.invariants import InvariantError, compile_invariant
from lattice.spec.model import Invariant, Scenario, Spec, Step, resolve_table, tables_of
from lattice.spec.report import LAYERS, Accepted, CheckFinding, Report
from lattice.spec.validate import validate
from lattice.typesys import Scalar, Schema

AppFactory = Callable[[Path], Any]
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


def _read_tables(db: Path) -> dict[str, list[dict[str, Any]]]:
    conn = sqlite3.connect(db)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        out: dict[str, list[dict[str, Any]]] = {}
        for t in names:
            cur = conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, row, strict=True)) for row in cur]
        return out
    finally:
        conn.close()


def _dispose(app: Any) -> None:
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None and hasattr(engine, "dispose"):
        engine.dispose()


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


def _layer_a(
    spec: Spec,
    report: Report,
    app_dir: Path | str | None,
    package: str | None,
    policy: Policy | None,
) -> None:
    if app_dir is None or package is None:
        report.notes.append("layer A skipped: no app source (app_dir and package)")
        return
    pol = spec_policy(spec, app_dir, package, policy)
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

    def __init__(self, app: Any, db: Path, monitor: InvariantMonitor) -> None:
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


class InvariantMonitor:
    """Runs every invariant on an app's database after each request (layer C).

    :meth:`wrap` turns an app factory into one whose apps are probed; ``label`` names
    the request being run (set it before each request, or give ``wrap`` an iterator of
    labels: one per app built, as verify builds one app per case). ``requests`` and
    ``endpoints`` map a label to its request and endpoint, for the findings."""

    def __init__(self, invariants: Sequence[tuple[Invariant, str]]) -> None:
        self.invariants = list(invariants)
        self.label: str | None = None
        self.requests: dict[str, dict[str, Any]] = {}
        self.endpoints: dict[str, str] = {}
        self.findings: list[CheckFinding] = []
        self._seen: set[tuple[str, str]] = set()
        self._seed: dict[str, set[str]] = {}

    @classmethod
    def for_spec(cls, spec: Spec) -> InvariantMonitor:
        compiled = []
        for inv in spec.invariants:
            try:
                compiled.append((inv, compile_invariant(inv, spec.profile)))
            except InvariantError as err:
                raise CheckError(f"invariant {inv.name!r}: {err}") from err
        return cls(compiled)

    def violations(self, db: Path) -> dict[str, list[dict[str, Any]]]:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            out = {}
            for inv, sql in self.invariants:
                try:
                    rows = [dict(r) for r in conn.execute(sql)]
                except sqlite3.Error as err:
                    rows = [{"error": f"the invariant's SQL failed: {err}"}]
                out[inv.name] = rows
            return out
        finally:
            conn.close()

    def violation_keys(self, db: Path) -> dict[str, set[str]]:
        return {n: {_row_key(r) for r in rows} for n, rows in self.violations(db).items()}

    def set_seed(self, db: Path) -> None:
        """The seed's violations: reported once (case ``(seed)``), never blamed on a
        request."""
        self._seed = self.violation_keys(db)
        self.check(db, SEED_CASE, baseline={})

    def check(self, db: Path, label: str, baseline: Mapping[str, set[str]] | None = None) -> None:
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
            return _Probe(app_factory(db_path), Path(db_path), self)

        return factory

    def after(self, db: Path, scope: Mapping[str, Any], baseline: Mapping[str, set[str]]) -> None:
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
) -> tuple[dict[str, str], list[str]]:
    """Each handle's token, from the app's own login on a scratch copy of the seed."""
    from fastapi.testclient import TestClient

    tokens: dict[str, str] = {}
    notes: list[str] = []
    db = make_db("seed", tmp / "login.db")
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
            tokens, notes = _tokens(handles, app_factory, make_db, login, tmp)  # type: ignore[arg-type]
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
            app_side = monitor.wrap(app_factory, iter(names))
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
            )
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


def _insert_given(spec: Spec, db: Path, given: Mapping[str, list[dict[str, Any]]]) -> None:
    conn = sqlite3.connect(db)
    try:
        for table, rows in given.items():
            found = resolve_table(spec.profile, table)
            if found is None:
                raise CheckError(f"given rows of an unknown table {table!r}")
            _, sql, schema = found
            for row in rows:
                cols = list(row)
                marks = ", ".join("?" for _ in cols)
                names = ", ".join(f'"{c}"' for c in cols)
                conn.execute(
                    f'INSERT INTO "{sql}" ({names}) VALUES ({marks})',
                    [_stored(schema, c, row[c]) for c in cols],
                )
        conn.commit()
    finally:
        conn.close()


def _same(expected: Any, actual: Any) -> bool:
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


def _expect_db(spec: Spec, db: Path, expect: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        for table, cond in expect.items():
            found = resolve_table(spec.profile, table)
            if found is None:
                problems.append(f"db {table}: unknown table")
                continue
            _, sql, schema = found
            where = dict(cond.get("where") or {})
            clauses, args = [], []
            for c, v in where.items():
                if v is None:
                    clauses.append(f'"{c}" IS NULL')
                else:
                    clauses.append(f'"{c}" = ?')
                    args.append(_stored(schema, c, v))
            query = f'SELECT * FROM "{sql}"' + (
                " WHERE " + " AND ".join(clauses) if clauses else ""
            )
            rows = [dict(r) for r in conn.execute(query, args)]
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
                            problems.append(f"{what}: {c} is {row.get(c)!r}, expected {want!r}")
    finally:
        conn.close()
    return problems


def _body(response: Any) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _step_request(step: Step) -> dict[str, Any]:
    return {
        "method": step.method,
        "path": step.path,
        "user": step.user,
        "params": dict(step.params) if step.params else None,
        "json": step.json,
    }


def _run_scenario(
    spec: Spec,
    scenario: Scenario,
    report: Report,
    app_factory: AppFactory,
    make_db: Callable[[str, Path], Path],
    monitor: InvariantMonitor | None,
    login: Login | None,
) -> None:
    from fastapi.testclient import TestClient

    rule = f"scenario: {scenario.name}"

    def fail(label: str, step: Step | None, verdict: str, details: dict[str, Any]) -> None:
        report.findings.append(
            CheckFinding(
                layer="D",
                verdict=verdict,  # type: ignore[arg-type]
                rule=rule,
                endpoint=f"{step.method} {step.path}" if step else None,
                case=label,
                request=_step_request(step) if step else None,
                details=details,
            )
        )

    with tempfile.TemporaryDirectory(prefix="lattice-check-d-") as name:
        db = make_db("seed", Path(name) / "scenario.db")
        if scenario.given:
            _insert_given(spec, db, scenario.given)
            if monitor is not None:
                monitor.check(db, f"{scenario.name} / given")
        factory = monitor.wrap(app_factory) if monitor is not None else app_factory
        try:
            app = factory(db)
        except Exception as err:  # noqa: BLE001 -- an app that does not start is loud
            fail(
                f"{scenario.name} / start", None, "loud", {"error": f"{type(err).__name__}: {err}"}
            )
            return
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                tokens: dict[str, str] = {}
                for i, step in enumerate(scenario.steps, 1):
                    label = f"{scenario.name} / step {i}"
                    headers = {}
                    if step.user is not None:
                        if step.user not in tokens:
                            if monitor is not None:
                                monitor.label = f"{scenario.name} / login as {step.user}"
                            try:
                                if login is None:
                                    raise RuntimeError("no login given")
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
                        headers["Authorization"] = f"Bearer {tokens[step.user]}"
                    if monitor is not None:
                        monitor.label = label
                        monitor.requests[label] = _step_request(step)
                        monitor.endpoints[label] = f"{step.method} {step.path}"
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
            _dispose(app)


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
) -> None:
    from lattice.spec.probes import asgi_sessions, run_probes

    on_probe = None
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
    policy: Policy | None = None,
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
) -> Report:
    """Check the app built by ``app_factory(db_path)`` against ``spec`` in ``layers``
    (letters of ABCDR; see the module docstring). Layer R: ``sessions`` is the probes'
    transport (:data:`lattice.spec.probes.SessionFactory`; default the ASGI TestClient
    over ``app_factory``), ``scheme`` the Authorization scheme, ``rule_items`` the item id
    prefixes to probe (default all). ``db_factory`` writes the seeded
    database. ``policy``: layer A's base policy (default: derived from the app).
    ``cases_for(spec, endpoint, ctx)``: layer B's case generator; ``extra_cases`` run
    too. ``rules``: verify's representation rules (default: 422 bodies compare by the
    fields they flag). ``natives``/``settings``: for the spec's programs. ``baseline``:
    the app's accepted findings (:class:`Accepted`; with ``spec.approved``, the spec's
    own), reported but not failing. Raises
    :class:`CheckError` when the check cannot run."""
    run = _layers(layers)
    problems = validate(spec)
    if problems:
        raise CheckError("the spec is not valid:\n  " + "\n  ".join(p.render() for p in problems))
    if login is None and login_path is not None and spec.users:
        login = form_login(login_path, dict(spec.users))
    report = Report(layers=run, unchecked=[u.text for u in spec.unchecked])
    make_db = as_db_factory(db_factory)

    if "A" in run:
        _layer_a(spec, report, app_dir, package, policy)
    monitor: InvariantMonitor | None = None
    if "C" in run:
        monitor = InvariantMonitor.for_spec(spec)
        if not monitor.invariants:
            report.notes.append("layer C: the spec has no invariants")
        with tempfile.TemporaryDirectory(prefix="lattice-check-c-") as name:
            try:
                seed_db = make_db("seed", Path(name) / "seed.db")
            except CheckError:
                raise
            except Exception as err:
                raise CheckError(f"the db factory failed: {type(err).__name__}: {err}") from err
            monitor.set_seed(seed_db)
        if "B" not in run and "D" not in run and "R" not in run:
            report.notes.append("layer C ran on the seed only: run it with B or D")
    if "B" in run:
        _layer_b(
            spec,
            report,
            app_factory,
            make_db,
            monitor,
            login=login,
            mint_token=mint_token,
            generate=cases_for,
            extra_cases=extra_cases,
            rules=rules,
            natives=natives,
            settings=settings,
            hash_columns=hash_columns,
            max_rows=max_rows,
        )
    if "D" in run:
        if not spec.scenarios:
            report.notes.append("layer D: the spec has no scenarios")
        for scenario in spec.scenarios:
            _run_scenario(spec, scenario, report, app_factory, make_db, monitor, login)
    if "R" in run:
        _layer_r(
            spec,
            report,
            app_factory,
            make_db,
            monitor,
            login=login,
            mint_token=mint_token,
            sessions=sessions,
            scheme=scheme,
            only=rule_items,
        )
    if monitor is not None:
        report.findings.extend(monitor.findings)
    order = {layer: i for i, layer in enumerate(LAYERS)}
    report.findings.sort(key=lambda f: order[f.layer])
    report.apply_baseline((*spec.approved, *baseline))
    return report


__all__ = [
    "CaseContext",
    "CasesFor",
    "CheckError",
    "InvariantMonitor",
    "as_db_factory",
    "cases_for",
    "check",
    "db_diff",
    "spec_plans",
    "spec_policy",
    "subset",
]
