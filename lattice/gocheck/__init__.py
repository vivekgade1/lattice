"""gocheck: Lattice's layer A on Go apps written with Gin + GORM (the Go counterpart of
:mod:`lattice.pycheck`).

Usage: build the policy from the spec, then check the app's Go module::

    policy = GoPolicy.from_spec(spec)
    findings = run_gocheck(go_src_dir, policy)   # [Finding(code, file, line, message, hint)]

Findings are :class:`lattice.pycheck.Finding`\\ s (``file`` relative to the source root),
so reports render exactly as pycheck's do. The original app's own findings can be
accepted through ``GoPolicy.baseline`` (their keys), as with pycheck.

**Why a Go program.** The checker is a small Go program (``src/``, standard library
only: ``go/parser`` and ``go/ast``), built on demand with ``go build`` into a cache
directory keyed by the hash of its sources and ``go version``, fed the policy as JSON on
stdin and answering the findings as JSON. Go's own parser reads every Go file exactly as
the compiler does (generics, raw strings, struct tags, composite literals), which a
Python reimplementation could only approximate -- and an approximation is where false
findings come from. ``go/types`` is deliberately not used: it needs the app's
dependencies (Gin, GORM) loaded, and a checker that falls silent whenever the module
cache is cold would be no checker. Types are read syntactically instead -- declared
parameter, field and variable types, ``:=`` from composite literals, conversions,
``new``, the module's own function results and GORM's chain methods -- which is enough to
know which names hold a ``*gorm.DB``, a ``*gin.Context`` or a model row, and every rule
is silent where a type is unknown.

Every rule prefers silence to a guess: a false finding costs the model a repair round,
so each fires only on a pattern it can read with certainty. The rules mean what their
PY8xx counterparts mean (``docs/lattice-language-rules.md``: one meaning, gated per
target); what each cannot see is listed with it.

* **GO800 parse.** A ``.go`` file that does not parse (the app cannot build).
* **GO801 ownership** (PY801). A route (registered with ``GET/POST/...``, its handler
  and middleware all resolved) that is not admin-only and whose owner scope the spec
  does not state otherwise, that reads or writes rows of an owned table (a GORM chain
  whose ``Model``/``Table``/destination is the table's struct) -- directly or in the
  helpers it calls -- while nothing it runs (the handler, its middleware, those helpers)
  filters, compares or sets the owner column: the column in a GORM call's arguments (a
  condition string, a map or struct condition), its field compared with ``==``/``!=``,
  assigned, or set in a model literal. A table owned through a parent needs that
  evidence on the parent. Admin-only: the spec says so, a middleware of the route or an
  ``if`` of the handler names an admin concept. Silent when the route calls code the
  checker cannot read that could check ownership (a method of an interface with several
  implementations, an outside package handed the request, the database or a row), or
  when its router's middleware cannot be read (a router handed in by more than one
  caller, or by none).
  *Cannot see:* evidence that exists but is not applied to the query that reaches the
  response (PY801's limit; here any mention of the owner column counts, so a compare
  against something other than the caller's id passes too), the caller's identity
  (Gin apps keep it under app-chosen ``c.Get`` keys), the users table's own rows
  (PY801's account-write check is not carried over: a Gin app's "the caller's own
  account" is read from those app-chosen keys).
* **GO802 private columns** (PY802). In a non-admin route (or a helper it calls), a
  ``c.JSON``-family answer whose body serializes a private column: a model struct (or a
  slice, map, embedding or named field of one) whose private column's field is exported
  with no ``json:"-"`` tag; or a ``gin.H``/map/struct literal (in the body, a variable it
  was built in, or a helper's return) carrying ``row.PrivateField``. A struct with its
  own ``MarshalJSON`` is not judged. *Cannot see:* values copied through intermediate
  variables or containers, serialization by hand (``json.Marshal`` then ``c.Data``),
  a literal key naming a private column with a value that is not read from a row (the
  "return a freshly minted secret once" idiom is legal and indistinguishable).
* **GO803 unbounded inputs** (PY803). A request value -- a field of a struct bound with
  ``c.ShouldBindJSON``/``ShouldBind``/... (a builtin string or number with no ``max``,
  ``len``, ``lte``, ``lt``, ``oneof``, ``eq`` -- strings also ``email``, ``uuid``,
  ... -- in its ``binding``/``validate`` tag), or a ``c.Query``/``c.PostForm`` string --
  written to a table (a model field assignment or literal that reaches a GORM write,
  ``Update("col", v)``, ``Updates(map{...})``, or the bound model itself created or saved
  whole, minus the fields set after binding and its primary key) with no hand bound
  (``len(v)`` compared, or ``v`` in an ordering comparison or ``min``/``max``); and a
  query number (``strconv.Atoi(c.Query(...))``, a bound int field) used as a GORM
  ``.Limit(...)`` (directly, through a copy, or in a helper it is handed to) with no
  such bound. A comparison with 0, 1 or a negative number checks a minimum, not a bound.
  Named (enum) types count as bounded; a request struct with a method called on it, or
  handed to other code, is not judged; a row handed to a helper that writes it is
  written. *Cannot see:* request values reaching the database through containers, or
  handed to a helper as a value (not a row), bounds enforced by code it cannot read.
* **GO807 partial writes** (PY807; Lattice's one transaction per write). In a function
  a route runs (the handler, a middleware, or a module helper they call, to
  ``helper_depth``), a committed write -- a GORM ``Create``, ``Save``, ``Update``,
  ``Updates``, ``UpdateColumn(s)``, ``Delete``, ``Exec``, ``FirstOrCreate`` outside a
  transaction, a ``tx.Commit()``, a ``db.Transaction(...)`` that writes, or a call to a
  helper that does one -- that a refusal can follow on some path: a later ``c.JSON``,
  ``c.AbortWithStatusJSON``, ``c.Status``, ``c.AbortWithStatus`` (and the other
  answering methods) with a known 4xx status, a call to a helper that answers one (a
  status passed as its argument is read at the call), or the caller refusing on an
  error a helper returns after it committed. Paths are followed: a branch that returns
  before the refusal is not one, and a loop's next pass can refuse after this pass
  wrote. Writes on a transaction handle (``db.Transaction(func(tx *gorm.DB) error
  {...})``'s ``tx``, ``tx := db.Begin()``, a helper handed one, a value built from one
  such as ``repo := NewRepo(tx)``) do not commit; an error returned from the
  transaction's function rolls them back. A handle read from the request
  (``c.MustGet("db").(*gorm.DB)``, a helper handed the context) is treated as a
  transaction too: a middleware may commit or roll it back. A helper's ``return err``
  or ``return db.Create(x).Error`` returns nil when it succeeded, so its commits count
  on its normal return too. Not refusals: the branch handling a write's own failure
  (``if err := db.Create(&x).Error; err != nil``, ``if res.Error != nil``: the write did
  not happen; like PY807 the rule does not judge database failures), a 5xx answer (it
  reports a failure -- the database's, a library's -- not a refusal by the app's rules),
  and, for a conditional write, the branch where it changed no row (``if
  res.RowsAffected == 0``). A transaction commits only where its result is nil: a
  refusal on its error (``if err != nil``, ``errors.Is(err, ...)``, ``isX(err)``, a
  ``switch`` case after ``case err == nil``, or a helper handed the error that refuses
  only where that parameter is not nil) follows a rollback. A helper whose bool result
  is its conditional write's ``res.RowsAffected == 1`` / ``> 0`` / ``!= 0`` (or a literal
  ``false`` before writing) wrote nothing where the caller sees it false (``if !ok``).
  *Cannot see:* failures raised by libraries or Gin itself, a
  helper deeper than ``helper_depth``, a refusal with a status it cannot read (a
  variable), a commit through a callable passed as a value, a transaction function that
  refuses and then returns nil, errors routed through ``c.Error`` to a middleware,
  conditions correlated across branches (a flag set where a write happened, tested
  later).
* **GO808 state machines** (PY808; only with ``machines``, which come from a spec). In
  any function, a write of a known state (a string constant, a module constant of one,
  or a conversion of one, matched to the machine's states case-insensitively) to the
  machine column of a model struct -- ``Update("status", X)`` / ``UpdateColumn``,
  ``Updates(map[string]any{"status": X})`` or ``Updates(Job{Status: X})``, or ``row.Status
  = X`` followed by a ``Save(&row)`` -- where the states the row can be in are known and
  one of them, other than the written state itself, has no edge to it. Known from a
  dominating check in the same function (``if row.Status != A { return }``, ``== A`` in a
  branch, ``switch row.Status``, ``slices.Contains([]...{A, B}, row.Status)``; ``&&``,
  ``||`` and ``!`` combine), or from the update chain's own ``.Where("status = ?", A)`` /
  ``.Where("status IN ?", []string{A, B})`` / ``.Where(map{...})`` (literal values too;
  placeholders are matched to their arguments; the states may be constants of any
  module package, in an inline slice or a package-level var). An update by id
  (``db.Model(&T{}).Where("id = ?", x)``) is tied to a guarded row of ``T`` fetched by
  the same ``x`` (``db.First(&row, x)``) or written as ``row.ID``, while neither is
  rebound.
  A check of the written state against the app's transition table
  (``slices.Contains(T[row.Status], X)``, ``T[row.Status][X]``, through a name) is a full
  guard. Silent: no check, a value that is not a known state, a row rebound, reloaded,
  handed to other code or changed in a loop since the check, a condition on the row it
  cannot read (a helper ``canMove(row.Status, X)``), a ``Where`` with ``OR``/``NOT`` on
  the column. *Cannot see:* checks in another function, writes in a helper, rows
  reached through fields or containers, raw SQL (``Exec``), the initial state of a new
  row.

Not available for Go (gated, :data:`GO_GATED`; each with its reason, reported in layer
A's notes):

* **GO804 route order** (PY804): Gin's router is a radix tree, not a list matched in
  order: a static segment always wins over a ``:param`` at the same position, whatever
  the registration order (and a conflicting wildcard pattern panics when the route is
  registered, at start-up). No declaration order can swallow a literal path, so the
  guarantee holds by construction.
* **GO805 error conventions** (PY805): the app's own conventions (the status it answers
  another user's row, its admin dependency's answer) are derived by pycheck from a
  Python original (``derive_not_owned``, the admin dependency); a Gin app has no
  declared admin dependency and its caller is app-defined, and no Go derivation exists
  yet. The spec's per-route error answers are checked dynamically by layer R. Lifted
  when: a Go derivation of the app's not-owned and admin answers exists.
* **GO806 naive / aware datetimes** (PY806): Go has no naive datetime -- every
  ``time.Time`` carries a location, and ``Before``/``After``/``Sub``/``Compare`` compare
  instants -- so the mix PY806 catches cannot be written.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.pycheck.findings import Finding, render_findings

if TYPE_CHECKING:
    from lattice.spec.model import Spec

log = logging.getLogger(__name__)

SRC = Path(__file__).resolve().parent / "src"
HELPER_DEPTH = 3  # how deep helpers are followed (pycheck's HELPER_DEPTH)
GO_ENV = {"GOFLAGS": "-mod=mod", "GOPROXY": "off", "GOWORK": "off", "GOTOOLCHAIN": "local"}
RUN_TIMEOUT_S = 120
CACHE_KEEP = 4  # checker builds kept in the cache directory (the newest)

# code -> the guarantee (the PY8xx guarantee of the same number)
GO_CODES: dict[str, str] = {
    "GO800": "the app's code does not parse",
    "GO801": "ownership: an owned table's rows reach a caller who was not checked to own them",
    "GO802": "private columns: a private column reaches a response",
    "GO803": "bounds: a request value is written to the database or used as a limit unbounded",
    "GO807": "partial writes: a commit that a later refusal of the same request can follow",
    "GO808": "state machines: a state written from a guarded state with no edge to it",
}

# the PY8xx rules with no Go counterpart: code -> why (see the module docstring)
GO_GATED: dict[str, str] = {
    "GO804": "route order (PY804) is moot on Gin: its radix-tree router matches a static "
    "segment before a parameter whatever the registration order, and panics at start-up "
    "on a conflicting pattern",
    "GO805": "error conventions (PY805) are derived from a Python original; a Gin app's "
    "caller and admin concept are app-defined and no Go derivation exists yet (layer R "
    "probes the spec's error answers)",
    "GO806": "naive/aware datetimes (PY806) cannot be mixed in Go: every time.Time carries "
    "a location and Before/After/Sub compare instants",
}


class GoCheckError(Exception):
    """gocheck could not run: no Go toolchain, a build failure, or a checker failure."""


class GoToolchainMissing(GoCheckError):
    """``go`` is not on PATH."""


@dataclass(frozen=True)
class GoPolicy:
    """The guarantees gocheck checks a Go app against (all names are the spec's: table
    schema names, SQL column names).

    ``tables``: schema name -> SQL name; ``owners`` / ``owner_via`` / ``private`` /
    ``machines`` as in :class:`lattice.pycheck.Policy`; ``admin_routes``: (METHOD, path)
    of the routes only the admin role may call; ``open_routes``: (METHOD, path) of the
    routes whose owner scope the spec states as other than the owner -- a contract's
    ``owner_scope``, a program with no owner filter (GO801 does not judge them; as in the
    spec, a route's role does not change its owner scope); ``baseline``: keys of accepted
    findings."""

    tables: Mapping[str, str] = field(default_factory=dict)
    users_table: str | None = None
    owners: Mapping[str, str] = field(default_factory=dict)
    owner_via: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    private: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    machines: Mapping[str, tuple[str, frozenset[tuple[str, str]]]] = field(default_factory=dict)
    admin_routes: tuple[tuple[str, str], ...] = ()
    open_routes: tuple[tuple[str, str], ...] = ()
    admin_role: str = "Admin"
    helper_depth: int = HELPER_DEPTH
    rules: tuple[str, ...] = ()  # only these codes (default: all)
    baseline: frozenset[str] = frozenset()

    @classmethod
    def from_spec(cls, spec: Spec) -> GoPolicy:
        """The policy a spec states: its tables' owners, owner_via and private columns,
        its machines, and (from its contracts and programs) the admin-only routes and
        the routes with another owner scope."""
        from lattice.spec.model import resolve_table, tables_of

        tables, sql_names = tables_of(spec.profile)
        api = spec.profile.api
        admin_role = api.admin_role if api is not None else "Admin"
        machines: dict[str, tuple[str, frozenset[tuple[str, str]]]] = {}
        for m in spec.machines:
            found = resolve_table(spec.profile, m.table)
            machines[found[0] if found else m.table] = (m.column, frozenset(m.edges))
        admin: list[tuple[str, str]] = []
        open_: list[tuple[str, str]] = []

        def place(method: str, path: str, auth: str | None) -> None:
            if auth == admin_role:
                admin.append((method, path))

        for c in spec.contracts:
            place(c.method, c.path, c.auth)
            if c.owner_scope not in (None, "owner", "owner_or_admin"):
                open_.append((c.method, c.path))
        for method, path, auth, scoped in _program_endpoints(spec):
            place(method, path, auth)
            if not scoped:
                open_.append((method, path))  # the spec's program reads anyone's rows
        return cls(
            tables={t: sql_names.get(t, t) for t in tables},
            users_table=api.users_table if api is not None else None,
            owners={t: s.owner for t, s in tables.items() if s.owner},
            owner_via={t: tuple(s.owner_via) for t, s in tables.items() if s.owner_via},  # type: ignore[misc]
            private={t: tuple(sorted(s.private)) for t, s in tables.items() if s.private},
            machines=machines,
            admin_routes=tuple(dict.fromkeys(admin)),
            open_routes=tuple(dict.fromkeys(open_)),
            admin_role=admin_role,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "tables": {t: {"sql": s} for t, s in self.tables.items()},
            "users_table": self.users_table or "",
            "owners": dict(self.owners),
            "owner_via": {t: list(v) for t, v in self.owner_via.items()},
            "private": {t: list(c) for t, c in self.private.items()},
            "machines": {
                t: {"column": col, "edges": sorted([a, b] for a, b in edges)}
                for t, (col, edges) in self.machines.items()
            },
            "admin_routes": [list(r) for r in self.admin_routes],
            "open_routes": [list(r) for r in self.open_routes],
            "admin_role": self.admin_role,
            "helper_depth": self.helper_depth,
            "rules": list(self.rules),
        }


OWNER_FILTERS = ("owned(", "mine(", "owner_is(", "accessible(")


def _program_endpoints(spec: Spec) -> list[tuple[str, str, str | None, bool]]:
    """(METHOD, path, auth, scoped) of the spec's programs: ``auth`` the role the
    endpoint requires (None when not stated), ``scoped`` whether it applies an owner
    filter (``owned()``, ``mine()``, ``owner_is()``, ``accessible()``)."""
    import ast

    out: list[tuple[str, str, str | None, bool]] = []
    for source in spec.programs.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and getattr(dec.func, "id", None) == "endpoint"):
                    continue
                if len(dec.args) < 2 or not all(isinstance(a, ast.Constant) for a in dec.args[:2]):
                    continue
                auth: str | None = None
                for kw in dec.keywords:
                    v = kw.value
                    if kw.arg == "auth":
                        name = v.id if isinstance(v, ast.Name) else getattr(v, "value", None)
                        auth = name if isinstance(name, str) else None
                body = ast.get_source_segment(source, node) or ""
                scoped = any(f in body for f in OWNER_FILTERS)
                method, path = dec.args[0].value, dec.args[1].value  # type: ignore[attr-defined]
                out.append((str(method).upper(), str(path), auth, scoped))
    return out


# ---- the binary --------------------------------------------------------------------------


@functools.cache
def go_version() -> str:
    go = shutil.which("go")
    if go is None:
        raise GoToolchainMissing("gocheck needs the Go toolchain: `go` is not on PATH")
    proc = subprocess.run(  # noqa: S603
        [go, "version"], capture_output=True, text=True, check=False, env=_env()
    )
    return proc.stdout.strip()


def _env() -> dict[str, str]:
    return {**os.environ, **GO_ENV}


def source_hash() -> str:
    """The hash of the checker's sources and the toolchain (the build cache key)."""
    h = hashlib.sha256()
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and (p.suffix == ".go" or p.name in ("go.mod", "go.sum")):
            if p.name.endswith("_test.go"):
                continue
            h.update(p.relative_to(SRC).as_posix().encode() + b"\0")
            h.update(p.read_bytes() + b"\0")
    h.update(go_version().encode())
    return h.hexdigest()


def cache_dir() -> Path:
    """Where built checkers are kept: ``$LATTICE_GOCHECK_CACHE``, else
    ``$XDG_CACHE_HOME/lattice/gocheck`` (``~/.cache/lattice/gocheck``)."""
    if env := os.environ.get("LATTICE_GOCHECK_CACHE"):
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "lattice" / "gocheck"


def build_gocheck() -> Path:
    """The checker binary, built with ``go build`` once per source hash (offline: the
    checker needs nothing outside the standard library)."""
    key = source_hash()[:24]
    out = cache_dir() / key / "gocheck"
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".gocheck-{os.getpid()}")
    go = shutil.which("go")
    if go is None:
        raise GoToolchainMissing("gocheck needs the Go toolchain: `go` is not on PATH")
    proc = subprocess.run(  # noqa: S603
        [go, "build", "-trimpath", "-buildvcs=false", "-o", str(tmp), "."],
        cwd=SRC,
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise GoCheckError(f"go build of gocheck failed:\n{proc.stderr.strip()}")
    os.replace(tmp, out)
    prune_cache(cache_dir())
    return out


def prune_cache(cache: Path, keep: int = CACHE_KEEP) -> None:
    """Keep the ``keep`` most recently built checkers under ``cache``."""
    builds = sorted(
        (p for p in cache.iterdir() if p.is_dir() and (p / "gocheck").is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in builds[keep:]:
        shutil.rmtree(old, ignore_errors=True)


# ---- running ------------------------------------------------------------------------------


def all_go_findings(src_dir: Path | str, policy: GoPolicy) -> list[Finding]:
    """Every finding on the Go module at ``src_dir``, the baseline included (raises
    :class:`GoCheckError` when the checker cannot run)."""
    binary = build_gocheck()
    payload = json.dumps({"root": str(Path(src_dir).resolve()), "policy": policy.to_json()})
    try:
        proc = subprocess.run(  # noqa: S603
            [str(binary)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as err:
        raise GoCheckError(f"gocheck timed out after {RUN_TIMEOUT_S}s") from err
    if proc.returncode != 0:
        raise GoCheckError(f"gocheck failed (exit {proc.returncode}): {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    return [
        Finding(f["code"], f["file"], int(f["line"]), f["message"], f["hint"], key=f["key"])
        for f in data.get("findings", ())
    ]


def run_gocheck(src_dir: Path | str, policy: GoPolicy, *, strict: bool = False) -> list[Finding]:
    """The guarantee findings on the Go module at ``src_dir``, minus the policy's
    baseline, sorted by file and line. A checker failure answers no findings (it must not
    cost the model a repair round) unless ``strict``; a missing toolchain always raises
    :class:`GoToolchainMissing`."""
    if not Path(src_dir).is_dir():
        raise GoCheckError(f"no Go source directory at {src_dir}")
    try:
        found = all_go_findings(src_dir, policy)
    except GoToolchainMissing:
        raise
    except GoCheckError:
        if strict:
            raise
        log.exception("gocheck failed on %s", src_dir)
        return []
    return [f for f in found if f.key not in policy.baseline]


def gated_note() -> str:
    """Layer A's note naming the PY rules with no Go counterpart, and why."""
    return "layer A (gocheck): not available for Go: " + "; ".join(
        f"{code} -- {why}" for code, why in GO_GATED.items()
    )


def render(findings: Iterable[Finding], prefix: str = "") -> str:
    return render_findings(findings, prefix)


__all__ = [
    "GO_CODES",
    "GO_GATED",
    "GoCheckError",
    "GoPolicy",
    "GoToolchainMissing",
    "all_go_findings",
    "build_gocheck",
    "cache_dir",
    "prune_cache",
    "gated_note",
    "render",
    "run_gocheck",
    "source_hash",
]
