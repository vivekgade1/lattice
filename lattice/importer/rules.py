"""The rules-first import (Phase 2 plan 2026-10-05, Wave 3): an app's rule layer,
verified on the original by probing, with no behaviour lifting and no LLM.

``python -m lattice.importer ROOT --package P --rules-only`` runs :func:`import_rules`
and writes it (:func:`write_rules`):

1. **Front end.** The one ``--framework`` names, else the first that detects its
   framework (FastAPI, Django + DRF, Flask + SQLAlchemy); the app factory is
   ``--app-factory`` or the nearest ``make_app(db_path)`` of the package or a parent.
   The front end gives the AppModel; ``lift_repo`` (no route lifted) the profile and
   the suite inputs (tables, auth); ``emit_spec.candidate_spec`` one contract per route.
2. **Seed** (:class:`Seeder`), built from the AppModel, never from an app's own seed
   script: the suite's seed plan (an admin; ``alice``, ``bob`` and the inactive
   ``carol`` with the first other role; a ``role_<name>`` user per further role; rows of
   every table, owned rows over every user), then:

   * roles kept as rows of a table (flasky's ``Role``): one row per role, holding the
     permissions the AuthModel records for it, and every user's role column its key;
   * the login column: the AuthModel's ``username_field`` holds the handle when it is
     not the email column (credentials are ``(login, password)``);
   * foreign keys spread over every parent row (so every vendor has products), composite
     keys of foreign keys made of distinct pairs (a follower is never followed twice);
   * boundary rows: numbers well inside their bounds (stock for an order, a price a
     write can lower), never at the edge a probe tests; a table with one row per user
     (a vendor profile) leaves ``bob`` without one, so creating one can succeed;
   * no NULLs (foreign keys aside): a row the app could never write (a post without a
     body) would make a probe crash for the seed's sake.

   Variant ``variant2`` (the corrections' re-probe and the fidelity check) has another
   row count, other ids (integer keys shifted) and other values: a spec fitted to one
   seed fails on it.
3. **Layer R** (``lattice.spec.probes.run_probes``) through the front end's runtime:
   its transport, its auth driver (the AuthModel's login route, or HTTP Basic; with no
   login route the probes can call, the front end's minter -- simplejwt's
   ``AccessToken.for_user``, DRF's ``Token`` --; a session cookie set on each probe's
   session), the
   AuthModel's recorded auth answers as refusals, the login route taking HTTP Basic
   credentials, the inactive user logged in while active (logging in on the seed
   itself, so a token the app keeps as a row is in every probe's copy). Routes no probe
   may call are never called: the front end's ``unreachable`` ones, routes reaching a
   network client (:func:`network_routes`: an LLM server, an OAuth2 provider) and
   routes an earlier one shadows (:func:`shadowed_routes`); their items are
   ``unreachable-by-probe`` with the reason, and they stay in the denominator. So are
   the unexercised items of a multipart upload route (:func:`upload_routes`). The T1
   item of a table whose owner the profile left unclear is ``unclear``
   (:func:`mark_unclear`), not the ``shared`` the profile's silence predicts. A table
   no route reads or writes by the front end's evidence (:func:`outside_api`: route
   tables, owner evidence, returned rows -- never the probes) is ``outside API``
   (:func:`mark_outside`): its items unexercised with that reason, the table listed
   and left out of the table denominator.
4. **Corrections** (:func:`correct`): a refuted item whose observed fact the spec can
   state becomes a corrected item -- R1: the auth the observed callers spell; R2: the
   other owner scope; R3: the observed status (and detail); an unstated 5xx: a stated
   500 on that request plus a **baseline** entry with its reason (an upstream defect,
   reported, never hidden); T2: the column is not private. The corrected spec's items
   are re-probed once on ``variant2``: verified there, the original item is
   ``verified (corrected)``; otherwise it stays refuted. T1, T3, T4 and T5 refutations
   are not corrected (no single observed fact restates them).
5. **Mining** (``lattice.importer.mine``): every probe's database before and after is a
   run; the front end's facts (CHECK and unique constraints, composite keys, choices,
   transition maps in the package) are hints. The observed ``non_empty`` template
   (``c != ''``) is **dropped**: W2-C measured it 5/5 false on multi-vendor (a text
   column the app writes is non-empty because the probes send non-empty text, not
   because a rule says so).
6. **Variant 2** (the fidelity suite's check, §1.4): layer R of the corrected spec on
   ``variant2``; every refuted item is a finding (``rules.variant2``).
7. **Report**: ``report.json["rules"]`` holds every item (verdict, probes, reason, the
   correction), the table and route verdicts (``verified (corrected)`` counts),
   ``excluded_routes`` (framework routes, not in the denominator; unreachable ones, in
   it), the ``baseline``, the corrections, mining's counts, the variant-2 check and
   the wall time per stage; ``candidate_spec.py`` the corrected spec, ``proposals.json``
   the mined proposals (pending review).

8. **Go** (Spike J, node J1; :mod:`lattice.importer.go_rules`). The same import for a Go
   (Gin + GORM) app: with ``--framework gin`` and no ``--app-factory``, the factory comes
   from the module's ``studio.toml`` ``[app] app`` (``go_rules.harness_factory``); owner
   columns are inferred from the extract's ``owner_uses`` where GORM declares no association
   (``go_rules.infer_owners``, recorded as ``inferences`` in ``report.json["rules"]``, present
   only when non-empty); and layer A's derived policy is written as ``go_policy.json``
   (:mod:`lattice.gocheck.derive`). A Python front end goes through the paths above unchanged.

Django keeps process-global state: one Django project per process (the CLI is one).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from lattice.importer.appmodel import RepoModel
from lattice.importer.suite import AuthSpec, SeedPlan, TableSpec, seed_plan, sql_seed
from lattice.importer.transport import AppRuntime, AuthDriver, form_login, json_login
from lattice.spec import Contract, ContractError, Spec
from lattice.spec.probes import (
    OUTSIDE_API,
    VERIFIED_CORRECTED,
    ItemVerdict,
    RuleResult,
    is_verified,
    run_probes,
    summarise,
)

__all__ = [
    "MINING_TEMPLATES",
    "VARIANTS",
    "Callers",
    "Correction",
    "RulesImport",
    "Seeder",
    "auth_for",
    "callers",
    "correct",
    "find_factory",
    "import_rules",
    "mark_outside",
    "mark_unclear",
    "mark_uploads",
    "network_routes",
    "outside_api",
    "outside_gaps",
    "shadowed_routes",
    "summary_line",
    "upload_routes",
    "variant2_record",
    "write_rules",
]

# seed variant -> (seed, rows per table, integer key shift)
VARIANTS: dict[str, tuple[int, int, int]] = {"seed": (31, 8, 0), "variant2": (97, 11, 100)}
# mining's observed templates: all but non_empty (5/5 false on multi-vendor, W2-C)
MINING_TEMPLATES = ("sign", "not_null", "one_per_fk", "sum", "machine")
COMFORT = 50  # numbers sit this far inside their lower bound


# ---- the app ---------------------------------------------------------------------------------


def find_factory(package: str, spec: str | None = None) -> tuple[Callable[[Path], Any], str]:
    """The app factory ``fn(db_path) -> app`` and its ``module:function`` name:
    ``spec`` when given, else the ``make_app`` of the package or its nearest parent
    package that has one (the importer's convention for vendored apps)."""
    if spec is not None:
        module, sep, name = spec.partition(":")
        if not sep:
            raise ValueError(f"an app factory is module:function, not {spec!r}")
        obj: Any = importlib.import_module(module)
        for part in name.split("."):
            obj = getattr(obj, part)
        return obj, spec
    parts = package.split(".")
    for n in range(len(parts), 0, -1):
        name = ".".join(parts[:n])
        make = getattr(importlib.import_module(name), "make_app", None)
        if callable(make):
            return make, f"{name}:make_app"
    raise LookupError(f"no make_app(db_path) in {package!r} or a parent package")


NETWORK_MODULES = ("requests", "httpx", "urllib.request", "aiohttp", "smtplib", "http.client",
                   "openai", "anthropic")  # fmt: skip


def network_routes(root: Path, package: str, model: RepoModel) -> dict[str, str]:
    """Routes that reach the network (``METHOD /path`` -> why): the route function, or a
    package function it calls (transitively), imports or calls a network client. Such a
    route needs a service the app does not hold (an LLM server, an OAuth2 provider), so
    probes never call it: it is ``unreachable-by-probe``, and stays in the denominator."""
    import ast

    from lattice.importer.extract import _Index, route_function

    try:
        index = _Index(root, package)
    except (FileNotFoundError, SyntaxError):
        return {}

    def network(name: str | None) -> str | None:
        name = (name or "").replace(":", ".")
        return next((n for n in NETWORK_MODULES if name == n or name.startswith(n + ".")), None)

    def reach(mod: str, fn: ast.AST) -> str | None:
        seen: set[int] = set()
        todo = [(mod, fn)]
        while todo and len(seen) < 60:
            m, node = todo.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import | ast.ImportFrom):
                    names = [a.name for a in sub.names] if isinstance(sub, ast.Import) else [
                        sub.module or ""]  # fmt: skip
                    hit = next((network(n) for n in names if network(n)), None)
                    if hit:
                        return f"needs the network: imports {hit}"
                elif isinstance(sub, ast.Call):
                    qual = index.resolve(m, sub.func)
                    hit = network(qual)
                    if hit:
                        return f"needs the network: calls {qual.replace(':', '.')}"  # type: ignore[union-attr]
                    found = index.node(qual)
                    if found is not None and isinstance(
                        found[1], ast.FunctionDef | ast.AsyncFunctionDef
                    ):
                        todo.append(found)
        return None

    out: dict[str, str] = {}
    for r in model.routes:
        fn = route_function(index, r)
        if fn is None:
            continue
        why = reach(r.view.split(":", 1)[0] if r.view else r.module, fn)
        if why is not None:
            out.setdefault(f"{r.method} {r.path}", why)
    return out


def shadowed_routes(model: RepoModel) -> dict[str, str]:
    """Routes no request can reach (``METHOD /path`` -> why): an earlier route has the
    same method and path shape (path parameters renamed), and frameworks dispatch to the
    first match -- JustNotes' ``GET /notes/{note_id}/keywords`` behind ``GET
    /notes/{id}/keywords``. An app defect, reported; the route stays in the denominator."""
    import re

    first: dict[tuple[str, str], str] = {}
    out: dict[str, str] = {}
    for r in model.routes:
        types = {p.name: p.type for p in r.params if p.source == "path"}

        def hole(m: re.Match[str], types: dict[str, Any] = types) -> str:
            # a hole by its type: Flask's <int:id> and <username> are told apart
            return "{" + str(types.get(m.group(1))) + "}"

        shape = re.sub(r"\{([^}]+)\}", hole, r.path.rstrip("/") or "/")
        key = (r.method, shape)
        endpoint = f"{r.method} {r.path}"
        if key in first and first[key] != endpoint:
            out[endpoint] = (f"shadowed: {first[key]} has the same path and is matched first, "
                             "so no request reaches this route")  # fmt: skip
        first.setdefault(key, endpoint)
    return out


def _runtime(fe: Any, root: Path, package: str, model: RepoModel) -> AppRuntime:
    rt = fe.runtime(root, package)
    if model.auth is not None and rt.auth.scheme != model.auth.scheme:
        rt = dataclasses.replace(rt, auth=AuthDriver(scheme=model.auth.scheme))
    return rt


# ---- the seed --------------------------------------------------------------------------------


@dataclass
class Seeder:
    """The import's seed (see the module docstring): plans per variant, the database
    files (built once per variant, then copied), the credentials."""

    model: RepoModel
    tables: tuple[TableSpec, ...]
    auth: AuthSpec
    insert_hook: Callable[[Path, SeedPlan], None] | None
    runtime: AppRuntime
    app_factory: Callable[[Path], Any]
    uuid_storage: str = "hex32"
    datetime_storage: str = "sql_space"
    _plans: dict[str, SeedPlan] = field(default_factory=dict, repr=False)
    _files: dict[str, Path] = field(default_factory=dict, repr=False)
    _dir: Path | None = field(default=None, repr=False)

    # -- plans --

    def role_auth(self) -> AuthSpec:
        """The suite's AuthSpec, with the role values a role table's key spells."""
        scheme, fe = self.model.role_scheme, self.model.auth
        if scheme is not None and fe is not None and fe.role_table is not None:
            values = tuple((stored, name) for _, stored, name in scheme.roles)
            return replace(self.auth, role_column=scheme.column, role_values=values,
                           admin_role=scheme.admin or "Admin")  # fmt: skip
        return self.auth

    def plan(self, variant: str = "seed") -> SeedPlan:
        if variant not in self._plans:
            seed, rows, shift = VARIANTS[variant]
            auth = self.role_auth()
            plan = seed_plan(self.tables, auth, seed=seed, rows_per_table=rows)
            by_name = {t.name: t for t in self.tables}
            _role_rows(plan, self.model, auth, by_name)
            _login_names(plan, self.model, auth)
            _spread_keys(plan, self.tables, auth, self._role_table())
            _one_spare(plan, self.tables, auth)
            _no_nulls(plan, self.tables, auth)
            _comfortable(plan, self.tables, auth, self._role_table())
            if shift:
                _shift_keys(plan, self.tables, auth, shift, keep=self._role_table())
            self._plans[variant] = plan
        return self._plans[variant]

    def _role_table(self) -> str | None:
        fe = self.model.auth
        if fe is None or fe.role_table is None:
            return None
        t = next((t for t in self.model.tables if t.name == fe.role_table), None)
        return t.table_name if t is not None else None

    def credentials(self, variant: str = "seed") -> dict[str, tuple[str, str]]:
        """handle -> (login, password): the login column's value and the password."""
        plan = self.plan(variant)
        users = plan.rows.get(self.auth.users_table, [])
        column = _login_column(self.model, self.auth)
        out = {}
        for u, row in zip(plan.users, users, strict=False):
            out[u.handle] = (str(row.get(column, u.email)), u.password)
        return out

    # -- files --

    def make_db(self, variant: str, path: Path) -> Path:
        """Write ``variant``'s database at ``path`` (built once, then copied)."""
        path = Path(path)
        if variant not in self._files:
            if self._dir is None:
                self._dir = Path(tempfile.mkdtemp(prefix="lattice-rules-seed-"))
            target = self._dir / f"{variant}.db"
            self._write(variant, target)
            self._files[variant] = target
        if path.exists():
            path.unlink()
        shutil.copyfile(self._files[variant], path)
        return path

    def _write(self, variant: str, path: Path) -> None:
        plan = self.plan(variant)
        if self.insert_hook is not None:
            if path.exists():
                path.unlink()
            self.insert_hook(path, plan)
            return
        sql_seed(path, self.tables, plan, uuid_storage=self.uuid_storage,
                 datetime_storage=self.datetime_storage, schema_hook=self._schema)  # fmt: skip

    def _schema(self, path: Path) -> None:
        """The app's own schema on an empty file: the app built on it once (its factory
        creates its tables), then every row it wrote at start-up removed (the seed's
        rows replace them: flasky's roles)."""
        with self.runtime.serve(self.app_factory, path):
            pass
        con = sqlite3.connect(path)
        try:
            query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            names = [r[0] for r in con.execute(query)]
            for name in names:
                con.execute(f'DELETE FROM "{name}"')
            con.commit()
        finally:
            con.close()

    def close(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
            self._files.clear()


def _login_column(model: RepoModel, auth: AuthSpec) -> str:
    fe = model.auth
    users = next((t for t in model.tables if t.table_name == auth.users_table), None)
    names = {c.name for c in users.columns} if users is not None else set()
    if fe is not None and fe.username_field in names:
        return fe.username_field
    return auth.email_column


def _login_names(plan: SeedPlan, model: RepoModel, auth: AuthSpec) -> None:
    """The login column holds the handle when it is not the email column (microblog
    logs in by username): unique and readable."""
    column = _login_column(model, auth)
    if column == auth.email_column:
        return
    for u, row in zip(plan.users, plan.rows.get(auth.users_table, []), strict=False):
        row[column] = u.handle


def _role_rows(
    plan: SeedPlan, model: RepoModel, auth: AuthSpec, tables: Mapping[str, TableSpec]
) -> None:
    """Roles kept as rows of a table: one row per role (its key the role column's
    stored value, its name, the permissions the AuthModel records for it)."""
    fe, scheme = model.auth, model.role_scheme
    if fe is None or fe.role_table is None or scheme is None:
        return
    t = next((t for t in model.tables if t.name == fe.role_table), None)
    spec = tables.get(t.table_name) if t is not None else None
    if t is None or spec is None:
        return
    perms = dict(fe.permissions)
    grants = dict(fe.role_permissions)
    name_col = next((c.name for c in t.columns if c.unique and c.type == "Str"), None)
    perm_col = next((c.name for c in t.columns if c.type == "Int" and "permission" in c.name), None)
    default_col = next((c.name for c in t.columns if c.type == "Bool" and "default" in c.name),
                       None)  # fmt: skip
    first = next((n for _, _, n in scheme.roles if n != (scheme.admin or "Admin")), None)
    rows = []
    for value, stored, name in scheme.roles:
        row = {c.name: None for c in spec.columns}
        row[spec.key.name] = int(stored) if spec.key.type == "int" else stored
        if name_col:
            row[name_col] = value
        if perm_col:
            row[perm_col] = sum(perms.get(p, 0) for p in grants.get(name, ()))
        if default_col:
            row[default_col] = name == first
        for c in spec.columns:
            if row.get(c.name) is None and not c.nullable:
                row[c.name] = False if c.type == "bool" else (0 if c.type == "int" else "")
        rows.append(row)
    plan.rows[spec.name] = rows
    users = tables.get(auth.users_table)
    role_col = next((c for c in users.columns if c.name == scheme.column), None) if users else None
    if role_col is not None and role_col.type == "int":
        for row in plan.rows.get(auth.users_table, []):
            if row.get(role_col.name) is not None:
                row[role_col.name] = int(row[role_col.name])


def _parents(plan: SeedPlan, tables: Sequence[TableSpec]) -> dict[str, list[Any]]:
    keys = {}
    for t in tables:
        key = next((c.name for c in t.columns if c.primary_key), None)
        if key is not None:
            keys[t.name] = [r[key] for r in plan.rows.get(t.name, [])]
    return keys


def _spread_keys(
    plan: SeedPlan, tables: Sequence[TableSpec], auth: AuthSpec, role_table: str | None
) -> None:
    """Foreign keys over every parent row (row ``i`` -> parent ``i mod n``); a key (or
    a composite primary key) made of two foreign keys: distinct pairs, no row paired
    with itself. Users foreign keys (owners) keep the plan's round robin."""
    parents = _parents(plan, tables)
    for t in tables:
        rows = plan.rows.get(t.name, [])
        fks = [c for c in t.columns if c.foreign_key]
        if not rows or not fks:
            continue
        pk = [c.name for c in t.columns if c.primary_key]
        pair = [c for c in fks if c.name in pk] if len(pk) >= 2 else []
        if len(pair) == 2:
            a = parents.get(pair[0].foreign_key.split(".")[0], [])  # type: ignore[union-attr]
            b = parents.get(pair[1].foreign_key.split(".")[0], [])  # type: ignore[union-attr]
            combos = [(x, b[(i + k) % len(b)]) for k in range(1, len(b) + 1)
                      for i, x in enumerate(a) if b and x != b[(i + k) % len(b)]]  # fmt: skip
            combos = list(dict.fromkeys(combos))[: len(rows)]
            plan.rows[t.name] = rows = rows[: len(combos)]
            for row, (x, y) in zip(rows, combos, strict=True):
                row[pair[0].name], row[pair[1].name] = x, y
            continue
        for c in fks:
            target = c.foreign_key.split(".")[0]  # type: ignore[union-attr]
            if target == auth.users_table or c.name == t.owner:
                continue
            keys = parents.get(target, [])
            if target == role_table or not keys:
                continue
            for i, row in enumerate(rows):
                row[c.name] = keys[i % len(keys)]


def _one_spare(plan: SeedPlan, tables: Sequence[TableSpec], auth: AuthSpec) -> None:
    """A table with at most one row per user (a unique users foreign key: a vendor
    profile) leaves ``bob`` without one, so creating one can succeed."""
    from lattice.importer.suite import OTHER

    spare = next((u.id for u in plan.users if u.handle == OTHER), None)
    for t in tables:
        unique = [c.name for c in t.columns if c.unique and c.foreign_key
                  and c.foreign_key.split(".")[0] == auth.users_table]  # fmt: skip
        if unique and t.name != auth.users_table:
            plan.rows[t.name] = [r for r in plan.rows.get(t.name, []) if r.get(unique[0]) != spare]


def _no_nulls(plan: SeedPlan, tables: Sequence[TableSpec], auth: AuthSpec) -> None:
    """Every nullable column set (foreign keys aside): a row the app could never write
    (a post without a body) makes a probe crash for the seed's sake, not the app's. A
    unique column (or one of the users table) gets a value of its own per row."""
    for t in tables:
        rows = plan.rows.get(t.name, [])
        for c in t.columns:
            if c.foreign_key or c.primary_key:
                continue
            own = c.unique or t.name == auth.users_table
            filled = [r[c.name] for r in rows if r.get(c.name) is not None]
            for i, r in enumerate(rows):
                if r.get(c.name) is not None:
                    continue
                if c.type in ("str", "email") and (own or not filled):
                    text = f"{c.name.replace('_', ' ').title()} {i + 1:02d}"
                    if c.format and c.type != "email":  # a slug, a URL...: of the format
                        from lattice.importer.formats import seed_value

                        text = seed_value(c.format, text, i, c.max_len)
                    r[c.name] = f"{t.name}.{c.name}.{i}@example.com" if c.type == "email" else text
                elif filled and not own:
                    r[c.name] = filled[i % len(filled)]


def _comfortable(
    plan: SeedPlan, tables: Sequence[TableSpec], auth: AuthSpec, role_table: str | None = None
) -> None:
    """Numbers well inside their bounds: ``COMFORT`` past the lower bound (0 when none),
    below any upper bound -- stock an order can take, a price a write can lower."""
    for t in tables:
        if t.name in (auth.users_table, role_table):
            continue
        for c in t.columns:
            if c.type not in ("int", "float") or c.primary_key or c.foreign_key or c.choices:
                continue
            for i, row in enumerate(plan.rows.get(t.name, [])):
                if row.get(c.name) is None:
                    continue
                lo = c.ge if c.ge is not None else 0
                value = lo + COMFORT + i
                if c.le is not None:
                    value = min(value, c.le)
                row[c.name] = int(value) if c.type == "int" else float(value)


def _shift_keys(
    plan: SeedPlan, tables: Sequence[TableSpec], auth: AuthSpec, shift: int, keep: str | None
) -> None:
    """Integer keys shifted (users by half as much), every foreign key following."""
    moved: dict[str, dict[Any, Any]] = {}
    for t in tables:
        key = next((c for c in t.columns if c.primary_key), None)
        if key is None or key.type != "int" or t.name == keep or key.foreign_key:
            continue
        by = shift // 2 if t.name == auth.users_table else shift
        moved[t.name] = {r[key.name]: r[key.name] + by for r in plan.rows.get(t.name, [])}
        for r in plan.rows.get(t.name, []):
            r[key.name] += by
    for t in tables:
        for c in t.columns:
            target = c.foreign_key.split(".")[0] if c.foreign_key else None
            if target in moved:
                for r in plan.rows.get(t.name, []):
                    if r.get(c.name) in moved[target]:
                        r[c.name] = moved[target][r[c.name]]
    if auth.users_table in moved:
        users = moved[auth.users_table]
        plan.users = tuple(replace(u, id=users.get(u.id, u.id)) for u in plan.users)


# ---- logging in ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Callers:
    """How probes act as users: ``login(client, handle)``, the headers carrying a
    token, the refusals the AuthModel records, the Basic-credential login route, and
    whether the inactive user is logged in while active (else ``mint``)."""

    login: Callable[[Any, str], str] | None
    headers: Callable[[str], Mapping[str, str]] | None
    scheme: str
    refusals: tuple[tuple[int, str | None], ...] = ()
    login_route: tuple[str, str] | None = None
    inactive_login: bool = False
    mint: Callable[[Any], str] | None = None
    # a session cookie: put in each probe's session (AuthDriver.present)
    present: Callable[[Any, str], Mapping[str, str]] | None = None


def callers(result: Any, seeder: Seeder, variant: str = "seed") -> Callers:
    """The callers of an import on a seed variant (see :class:`Callers`). FastAPI: the
    suite's login (a form or JSON login route) and minter, as before; a front end's
    AuthModel: its auth driver over the variant's credentials."""
    from lattice.spec.check import BAD_TOKEN

    model: RepoModel = result.model
    creds = seeder.credentials(variant)
    fe = model.auth
    if fe is None:
        auth = result.context.inputs.auth
        login = None
        if auth.login_path is not None and auth.login_encoding == "json":
            login = json_login(auth.login_path, creds, username_field=auth.username_field,
                               password_field=auth.password_field,
                               token_path=tuple(auth.token_field.split(".")))  # fmt: skip
        elif auth.login_path is not None:
            login = form_login(auth.login_path, creds, username_field=auth.username_field,
                               password_field=auth.password_field,
                               token_field=auth.token_field)  # fmt: skip
        return Callers(
            login, None, "Bearer", mint=auth.mint_token, inactive_login=auth.mint_token is None
        )
    driver = AuthDriver.from_model(fe, creds)
    refusals = tuple(
        a for a in (fe.unauthenticated_answer, fe.invalid_answer, fe.unknown_answer,
                    fe.inactive_answer) if a is not None
    )  # fmt: skip
    guards = tuple(
        (g.status, g.detail)
        for r in model.routes
        for g in r.guards
        if g.kind in ("authenticated", "active", "role", "admin") and g.status and g.detail
    )
    route = None
    if fe.login_path is not None and fe.login_encoding == "basic":
        route = ("POST", fe.login_path)
    headers = driver.headers
    if driver.scheme == "basic":
        from lattice.importer.transport import basic_credentials

        def basic_headers(token: str) -> Mapping[str, str]:
            # the malformed caller: well-formed Basic credentials of no user
            if token == BAD_TOKEN:
                return {driver.header: basic_credentials("nobody@example.com", BAD_TOKEN)}
            return driver.headers(token)

        headers = basic_headers

    login = driver.login
    frontend = getattr(result.context, "frontend", None)
    mint = frontend.minter(fe) if login is None and hasattr(frontend, "minter") else None
    if mint is not None:
        # no login route the probes can use (simplejwt behind an OTP, DRF tokens without
        # an obtain route): the front end's minter makes each user's token, in the
        # database the app is open on (the seed: logging in happens there)
        keys = {u.handle: u.id for u in seeder.plan(variant).users}

        def login(client: Any, handle: str) -> str:
            return mint(keys[handle])

    return Callers(
        login,
        headers,
        driver.scheme,
        tuple(dict.fromkeys(refusals + guards)),
        route,
        inactive_login=True,
        present=driver.present if driver.cookie is not None else None,
    )


# ---- corrections -----------------------------------------------------------------------------


def auth_for(allowed: Sequence[str], api: Any) -> str | None:
    """The auth whose admitted callers are ``allowed`` (role names, ``anonymous``):
    Public, Authenticated, Admin or Roles(...); None when no auth says it. Every role a
    caller can hold admitted is ``Authenticated`` (a profile with ``NoRoles``: its one
    role)."""
    from lattice.spec.probes import _reachable_roles

    if "anonymous" in allowed:
        return "Public"
    roles = {r for r in api.roles if r != "Public"}
    got = {a for a in allowed if a in roles}
    if not got:
        return None
    if got == roles or got == _reachable_roles(api):
        return "Authenticated"
    if got == {api.admin_role}:
        return "Admin"
    return f"Roles({', '.join(sorted(got))})"


@dataclass
class Correction:
    """A refuted item, the corrected item id (None: the fact is dropped), what changed,
    and the baseline entry it records (an upstream 5xx)."""

    item: str
    corrected: str | None
    change: str
    baseline: dict[str, Any] | None = None
    verdict: str = "refuted"  # after the re-probe: verified (corrected) or refuted
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _contract(spec: Spec, endpoint: str) -> Contract | None:
    return next((c for c in spec.contracts if c.endpoint == endpoint), None)


def correct(
    spec: Spec, rules: RuleResult, *, owner_or_admin: Collection[str] = ()
) -> tuple[Spec, list[Correction]]:
    """The spec with every correctable refuted item corrected (see the module
    docstring), and the corrections. ``owner_or_admin``: the routes (``METHOD /path``)
    whose object permission lets the owner or the admin through (the front end's
    evidence, :func:`owner_or_admin_routes`): an R1 refutation there observing the
    admin alone admitted is the owner check refusing non-admins another user's row, so
    it proposes ``owner_scope="owner_or_admin"`` (auth unchanged), never auth Admin --
    and nothing when the table has no known owner."""
    from lattice.spec.probes import _error_ids, _schema

    api = spec.profile.api
    contracts = {c.endpoint: c for c in spec.contracts}
    out: list[Correction] = []
    private_drops: dict[str, set[str]] = {}
    for item_id, v in rules.items.items():
        if v.verdict != "refuted":
            continue
        kind, subject, obs = v.item.kind, v.item.subject, v.observed or {}
        c = contracts.get(subject)
        if kind == "R1" and c is not None:
            auth = auth_for(obs.get("allowed", ()), api)
            if auth == "Admin" and subject in owner_or_admin and c.auth != "Admin":
                found = _schema(spec, c.table)
                if found is not None and (found[2].owner or found[2].owner_via):
                    contracts[subject] = replace(c, owner_scope="owner_or_admin")
                    out.append(Correction(item_id, item_id, "owner_scope -> owner_or_admin "
                                          "(not auth Admin: an owner-or-admin object "
                                          "permission)"))  # fmt: skip
                continue
            if auth is not None and auth != c.auth:
                contracts[subject] = replace(c, auth=auth)
                out.append(Correction(item_id, item_id, f"auth {c.auth} -> {auth}"))
        elif kind == "R2" and c is not None and c.owner_scope in (None, "owner", "any"):
            # a scope naming a column (owner(<c>), owner_or_public(<c>)) is not corrected:
            # no single observed fact restates its condition
            scope = "owner" if obs.get("scope") == "owner" else "any"
            if scope != (c.owner_scope or "owner"):
                contracts[subject] = replace(c, owner_scope=scope)
                out.append(Correction(item_id, item_id, f"owner_scope -> {scope}"))
        elif kind == "R3" and c is not None and item_id.endswith(" 5xx"):
            record = rules.probes.get(v.witness or "", {})
            status = obs.get("status")
            request = record.get("request") or {}
            example = {k: request[k] for k in ("path", "params", "json", "form") if request.get(k)}
            if "{" in c.path:
                # the route's own path with a seed row's key: each variant fills its own
                example.pop("path", None)
            if request.get("user"):
                example["user"] = request["user"]
            error = ContractError(status, when="other", example=example)
            contracts[subject] = replace(c, errors=(*c.errors, error))
            base = {
                "item": item_id,
                "endpoint": subject,
                "status": status,
                "request": request,
                "body": record.get("body"),
                "reason": f"upstream defect: the original app answers {status} to this "
                "request (an unhandled error); recorded, not fixed by the import",
            }
            new_id = f"R3 {subject} {status}"
            out.append(Correction(item_id, new_id, f"states the app's {status}", base))
        elif kind == "R3" and c is not None and "status" in obs:
            ids = dict(_error_ids(c))
            e = ids.get(item_id)
            if e is None or obs["status"] is None or obs["status"] < 400:
                continue
            fixed = replace(e, status=obs["status"], detail=obs.get("detail"))
            errors = tuple(fixed if x is e else x for x in c.errors)
            contracts[subject] = replace(c, errors=errors)
            new_id = f"R3 {subject} {fixed.status}" + (f" {fixed.detail}" if fixed.detail else "")
            out.append(Correction(item_id, new_id, f"{e.status} {e.detail!r} -> "
                                  f"{fixed.status} {fixed.detail!r}"))  # fmt: skip
        elif kind == "T2":
            col = v.item.predicted.get("private")
            private_drops.setdefault(subject, set()).add(col)
            out.append(Correction(item_id, None, f"{subject}.{col} is not private"))
    profile = spec.profile
    if private_drops and api is not None:
        tables = dict(api.tables)
        for name, cols in private_drops.items():
            schema = tables[name]
            tables[name] = replace(schema, private=frozenset(schema.private) - cols)
        profile = replace(profile, api=replace(api, tables=tables))
    order = [c.endpoint for c in spec.contracts]
    fixed_spec = replace(spec, profile=profile,
                         contracts=tuple(contracts[e] for e in order))  # fmt: skip
    return fixed_spec, out


# ---- the import ------------------------------------------------------------------------------


@dataclass
class RulesImport:
    """A rules-only import: the import result (model, profile, context), the spec as
    corrected, the rule layer (corrections applied to its verdicts), the corrections,
    baseline, excluded routes, mining, and timing."""

    result: Any
    spec: Spec
    candidate: Spec
    rules: RuleResult
    corrections: list[Correction]
    baseline: list[dict[str, Any]]
    excluded_routes: list[dict[str, Any]]
    framework: str
    app_factory: str
    mining: Any = None
    timing: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    variant2: dict[str, Any] = field(default_factory=dict)
    # tables no route reads or writes (outside_api): listed, not in the denominator
    outside: dict[str, str] = field(default_factory=dict)
    # Go only (go_rules): the owner columns inferred from the extract, layer A's derived policy
    inferences: list[dict[str, Any]] = field(default_factory=list)
    go_policy: dict[str, Any] | None = None

    def rules_dict(self) -> dict[str, Any]:
        data = self.rules.to_dict()
        for c in self.corrections:
            record = data["items"].get(c.item)
            if record is not None:
                record["correction"] = c.to_dict()
        data.update(
            framework=self.framework,
            excluded_routes=self.excluded_routes,
            baseline=self.baseline,
            corrections=[c.to_dict() for c in self.corrections],
            mining=self.mining.to_dict()["counts"] if self.mining is not None else None,
            mining_templates=list(MINING_TEMPLATES),
            unreachable=sorted(
                {
                    i: v["reason"]
                    for i, v in data["items"].items()
                    if v["reason"].startswith("unreachable-by-probe")
                }.items()
            ),  # fmt: skip
            timing={k: round(v, 3) for k, v in self.timing.items()},
            variant2=self.variant2,
            # the tables left outside the API (one the probes reached stays in: a note)
            outside_api={
                t: why
                for t, why in self.outside.items()
                if self.rules.tables.get(t, {}).get("verdict") == OUTSIDE_API
            },
            seed={
                "variants": {
                    k: {"seed": s, "rows_per_table": n, "key_shift": d}
                    for k, (s, n, d) in VARIANTS.items()
                }
            },  # fmt: skip
        )
        data["notes"] = list(data.get("notes", [])) + self.notes
        if self.inferences:  # Go only, and only when something was inferred
            data["inferences"] = self.inferences
        return data


def _excluded(
    model: RepoModel, fe: Any, root: Path, package: str, unreachable: Mapping[str, str]
) -> list[dict[str, Any]]:
    """The routes set apart: the framework's (not in the route denominator) and the
    unreachable ones (in it, never probed), each with its reason."""
    out = [
        {"method": e.method, "path": e.path, "kind": e.kind, "reason": e.reason}
        for e in model.excluded_routes
    ]
    listed = {f"{e['method']} {e['path']}" for e in out}
    for endpoint, why in unreachable.items():
        if endpoint not in listed:
            method, _, path = endpoint.partition(" ")
            out.append({"method": method, "path": path, "kind": "unreachable", "reason": why})
    excluded = getattr(fe, "excluded", None)
    if excluded is not None:
        for e in excluded(root, package):
            if e.what == "route":
                # Django's names: "GET,POST,HEAD /snippets.{format}", "* /admin/"
                methods, _, path = e.name.partition(" ")
                if not path.startswith("/"):
                    methods, path = "*", e.name
                out.append({"method": methods, "path": path, "kind": "framework",
                            "reason": e.reason})  # fmt: skip
    return out


def _sessions(runtime: AppRuntime, factory: Callable[[Path], Any]) -> Any:
    @contextmanager
    def sessions(db: Path) -> Iterator[Any]:
        with runtime.serve(factory, db) as (_, transport):
            yield transport

    return sessions


def _hints(root: Path, package: str, model: RepoModel) -> Any:
    from lattice.importer.mine import CheckConstraint, MiningHints, Unique, transition_maps

    checks, uniques = [], []
    for c in model.constraints:
        if c.kind == "check" and c.expr:
            checks.append(CheckConstraint(c.table, c.expr, c.name, "front end: CHECK"))
        elif c.kind == "unique" and c.columns:
            uniques.append(Unique(c.table, c.columns, None, "front end: unique constraint"))
    for t in model.tables:
        for text in t.checks:
            checks.append(CheckConstraint(t.name, text, None, "front end: CHECK"))
        for cols in t.unique_together:
            uniques.append(Unique(t.name, cols, None, "front end: unique together"))
    try:
        transitions = tuple(transition_maps(root, package, model))
    except Exception:  # noqa: BLE001 -- an unfamiliar package: no transition map
        transitions = ()
    return MiningHints(tuple(checks), tuple(uniques), (), transitions)


def import_rules(
    root: Path | str,
    package: str,
    *,
    framework: str | None = None,
    app_factory: str | None = None,
    dialect: str | None = None,
    mine_proposals: bool = True,
) -> RulesImport:
    """The rules-only import of ``package`` under ``root`` (see the module docstring)."""
    import sys

    from lattice.importer.emit_spec import candidate_spec
    from lattice.importer.frontends import select
    from lattice.importer.lift import lift_repo

    clock = time.perf_counter
    timing: dict[str, float] = {}
    t0 = clock()
    root = Path(root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    fe = select(framework, root, package)
    go_factory = None
    if fe.name == "gin":  # Go (J1): the harness' factory, and owners inferred from the extract
        from lattice.importer import go_rules

        if app_factory is None:
            go_factory = go_rules.harness_factory(root, package)
        fe = go_rules.InferringFrontEnd(fe)
    factory, factory_name = go_factory or find_factory(package, app_factory)
    model = fe.extract(root, package)
    runtime = _runtime(fe, root, package, model)
    result = lift_repo(root, package, factory, only=(), dialect=dialect, frontend=fe,
                       runtime=runtime)  # fmt: skip
    if fe.name == "gin":  # E3, E5: the profile states the bind status and the private columns
        go_rules.patch_profile(result, fe.private, fe.validation_status)
    timing["extract"] = clock() - t0
    ctx = result.context
    api = result.lattice_profile.api
    seeder = Seeder(result.model, ctx.inputs.tables, ctx.inputs.auth, ctx.inputs.insert_hook,
                    runtime, factory, api.uuid_storage, api.datetime_storage)  # fmt: skip
    try:
        users = seeder.credentials()
        spec = candidate_spec(result, users=users)
        unreachable = {
            f"{e.method} {e.path}": e.reason
            for e in result.model.excluded_routes
            if e.kind == "unreachable"
        }
        for endpoint, why in network_routes(root, package, result.model).items():
            unreachable.setdefault(endpoint, why)
        for endpoint, why in shadowed_routes(result.model).items():
            unreachable.setdefault(endpoint, why)
        sessions = _sessions(runtime, factory)

        def args(variant: str) -> dict[str, Any]:
            who = callers(result, seeder, variant)
            return dict(login=who.login, mint_token=who.mint, scheme=who.scheme,
                        refusals=who.refusals, login_route=who.login_route,
                        auth_headers=who.headers, inactive_login=who.inactive_login,
                        unreachable=unreachable, variant=variant,
                        auth_present=who.present)  # fmt: skip

        t1 = clock()
        recorder = _Recording() if mine_proposals else None
        rules = run_probes(spec, sessions, seeder.make_db,
                           observe=recorder.observe if recorder else None,
                           **args("seed"))  # type: ignore[arg-type]  # fmt: skip
        unclear = {t: why for t, d in result.profile.ownership.items() if d == "unclear"
                   for why in [result.profile.ownership_reasons.get(t, "")]}  # fmt: skip
        mark_unclear(spec, rules, unclear)
        outside = outside_api(result.model)
        notes = list(getattr(ctx.inputs.insert_hook, "notes", ()))
        gaps = outside_gaps(result.model)
        if gaps:
            notes.append("no table is set outside the API: the front end did not read every "
                         "route's handler (" + "; ".join(gaps) + ")")  # fmt: skip
        mark_outside(spec, rules, outside)
        uploads = upload_routes(result.model)
        mark_uploads(rules, uploads)
        timing["probe"] = clock() - t1
        t2 = clock()
        fixed, corrections = correct(
            spec, rules, owner_or_admin=owner_or_admin_routes(result.model)
        )
        baseline = _reprobe(fixed, corrections, rules, sessions, seeder, args("variant2"))
        timing["correct"] = clock() - t2
        mining = None
        if recorder is not None:
            t3 = clock()
            from lattice.importer.mine import mine

            try:
                mining = mine(result.lattice_profile, recorder.runs(), model=result.model,
                              hints=_hints(root, package, result.model),
                              templates=MINING_TEMPLATES)  # fmt: skip
            finally:
                recorder.close()
            timing["mine"] = clock() - t3
        t4 = clock()
        again = run_probes(replace(fixed, users=seeder.credentials("variant2")), sessions,
                           seeder.make_db, **args("variant2"))  # fmt: skip
        mark_unclear(fixed, again, unclear)
        mark_outside(fixed, again, outside)
        mark_uploads(again, uploads)
        variant2 = variant2_record(again)
        timing["variant2"] = clock() - t4
        inferences = list(getattr(fe, "inferences", ()))
        go_policy = None
        if fe.name == "gin":
            t5 = clock()
            go_policy = _go_policy(root, package, inferences)
            timing["go_policy"] = clock() - t5
        timing["total"] = clock() - t0
        return RulesImport(
            result,
            fixed,
            spec,
            rules,
            corrections,
            baseline,
            _excluded(result.model, fe, root, package, unreachable),
            fe.name,
            factory_name,
            mining,
            timing,
            notes=notes,
            variant2=variant2,
            outside=outside,
            inferences=inferences,
            go_policy=go_policy,
        )
    finally:
        seeder.close()


def _go_policy(root: Path, package: str, inferences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``go_policy.json`` (Go only): layer A's policy derived from the app
    (:func:`lattice.gocheck.derive.derive_go_policy`) with its baseline, and the state
    machines the derivation drops (a machine is a decision: the app's own writes are not a
    spec)."""
    from lattice.gocheck.derive import derive_go_policy, policy_extract

    module_dir = Path(root) / package
    extract = policy_extract(module_dir)
    owners = {i["table"]: i["column"] for i in inferences if i.get("rule") == "owner_uses"
              and i.get("column")}  # fmt: skip
    policy = derive_go_policy(module_dir, extract, owners)
    dropped: dict[tuple[str, str], list[Any]] = {}
    for w in extract.get("status_writes", ()):
        values = dropped.setdefault((w["model"], w["column"]), [])
        if w["value"] not in values:
            values.append(w["value"])
    return {
        "schema": "lattice.gopolicy/1",
        "policy": policy.to_json(),
        "baseline": sorted(policy.baseline),
        "dropped_machines": [
            {"table": t, "column": c, "values": v} for (t, c), v in sorted(dropped.items())
        ],
    }


FILE_FIELDS = ("ImageField", "FileField", "UploadFile", "FileStorage", "File")


def upload_routes(model: RepoModel) -> dict[str, str]:
    """Routes whose body takes a file (``METHOD /path`` -> the field): a multipart
    upload, which probes (JSON bodies) cannot send."""
    models = {m.name: m for m in model.models}
    out: dict[str, str] = {}
    for r in model.routes:
        for p in r.params:
            m = models.get(p.type or "") if p.source in ("body", "form") else None
            files = [f.name for f in (m.fields if m else ()) if f.annotation in FILE_FIELDS]
            if p.annotation in FILE_FIELDS:
                files.append(p.name)
            if files:
                out[f"{r.method} {r.path}"] = files[0]
    return out


def mark_uploads(rules: RuleResult, uploads: Mapping[str, str]) -> None:
    """The unexercised items of an upload route are unreachable-by-probe: the upload
    itself (its success and its body's errors) needs a multipart request."""
    for v in rules.items.values():
        field_ = uploads.get(v.item.subject)
        if (
            field_
            and v.verdict == "unexercised"
            and not v.reason.startswith("unreachable-by-probe: a multipart")
        ):
            v.reason = (f"unreachable-by-probe: a multipart file upload ({field_}); probes send "
                        f"JSON -- {v.reason}")  # fmt: skip


def outside_api(model: RepoModel) -> dict[str, str]:
    """The tables no route reads or writes (table class name -> why), by the front
    end's evidence alone -- the tables a route references (``Route.tables``: its queries,
    inserts and deletes, its ORM helpers'), writes through an ORM relationship
    (``Route.rel_tables``: ``obj.tags.append(t)``, ``user.favorites.add(x)``, a link
    table), compares or sets an owner column of
    (``owner_uses``) or returns rows of -- never by what the probes reached. Framework
    routes are not in ``model.routes``; routes probes cannot call (the network) still
    count. The users table is never outside (logging in reads it). Such a table is
    ``outside API`` (plan 2026-10-05, measurement decision 1: flasky's ``roles``, read
    only inside permission checks, and ``follows``, written only by the HTML pages)."""
    if outside_gaps(model):
        return {}  # incomplete evidence: no table may be excluded (outside_gaps says why)
    users = model.auth.users_table if model.auth is not None else None
    reached: set[str] = set()
    for r in model.routes:
        reached |= set(r.tables) | set(r.rel_tables) | {u.table for u in r.owner_uses}
        if r.returns:
            reached.add(r.returns)
    out = {}
    for t in model.tables:
        if t.name in reached or t.name == users or _is_users(model, t):
            continue
        out[t.name] = f"no route reads or writes {t.name} ({t.table_name})"
    return out


def owner_or_admin_routes(model: RepoModel) -> set[str]:
    """Routes (``METHOD /path``) with an owner check the admin role bypasses (a guard
    ``object_owner`` naming the admin among the roles that bypass it: DRF's
    ``request.user.is_staff or obj.user == request.user``)."""
    admin = model.role_scheme.admin if model.role_scheme is not None else None
    names = {admin or "Admin", "Admin"}
    return {f"{r.method} {r.path}" for r in model.routes for g in r.guards
            if g.kind == "object_owner" and names & set(g.roles)}  # fmt: skip


def outside_gaps(model: RepoModel) -> list[str]:
    """Why the front end's evidence of what the routes read and write is incomplete:
    one entry per route whose handler it did not read (``Route.unread``). Any entry
    vetoes every "outside API" exclusion (:func:`outside_api`)."""
    return [f"{r.method} {r.path}: {r.unread}" for r in model.routes if r.unread]


def _is_users(model: RepoModel, table: Any) -> bool:
    """The users table of a FastAPI app (no AuthModel): the one other tables' owner
    foreign keys point at, as the dependencies' ``current_user`` returns it."""
    returns = {d.returns for d in model.dependencies if d.kind in ("current_user", "active_user")}
    return table.name in returns


def mark_outside(spec: Spec, rules: RuleResult, outside: Mapping[str, str]) -> None:
    """The items of a table outside the API (:func:`outside_api`) are unexercised with
    the reason ``outside API: <why>`` (an ``unclear`` T1 too: no route needs its owner);
    the table's verdict is then ``outside API`` and it leaves the table denominator. A
    table some probe reached after all (an item verified or refuted) stays in, noted:
    the evidence may miss a route, and probes only ever keep a table in."""
    changed = False
    for table, why in outside.items():
        found = [v for v in rules.items.values() if v.item.subject == table]
        reached = [v.item.id for v in found if v.verdict not in ("unexercised", "unclear")]
        if reached:
            rules.notes.append(f"{table}: no route reads or writes it by the front end's "
                               f"evidence, but the probes reached it ({', '.join(reached)}); "
                               "it stays in the table denominator")  # fmt: skip
            continue
        for v in found:
            rules.items[v.item.id] = ItemVerdict(v.item, "unexercised", f"{OUTSIDE_API}: {why}",
                                                 v.probes)  # fmt: skip
            changed = True
    if changed or outside:
        summarise(spec, rules)


def mark_unclear(spec: Spec, rules: RuleResult, owners: Mapping[str, str]) -> None:
    """The T1 item of a table whose owner the profile left unclear (two users foreign
    keys, routes comparing different columns) is ``unclear``, not the ``shared`` the
    profile's silence would predict: the front end produced no candidate."""
    changed = False
    for table, why in owners.items():
        v = rules.items.get(f"T1 {table}")
        if v is not None and v.verdict != "unclear":
            rules.items[v.item.id] = ItemVerdict(v.item, "unclear", f"the owner is unclear: {why}",
                                                 v.probes)  # fmt: skip
            changed = True
    if changed:
        summarise(spec, rules)


def variant2_record(again: RuleResult) -> dict[str, Any]:
    """Layer R of the corrected spec on seed variant 2 (the fidelity suite's check):
    every refuted item is a finding. ``clean``: none."""
    findings = []
    for item_id, v in again.items.items():
        if v.verdict != "refuted":
            continue
        record = again.probes.get(v.witness or "", {})
        findings.append({
            "item": item_id,
            "reason": v.reason,
            "observed": v.observed,
            "request": record.get("request"),
            "status": record.get("status"),
        })  # fmt: skip
    return {"variant": "variant2", "summary": again.summary, "findings": findings,
            "clean": not findings}  # fmt: skip


def _reprobe(
    fixed: Spec,
    corrections: list[Correction],
    rules: RuleResult,
    sessions: Any,
    seeder: Seeder,
    common: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Re-probe every corrected item once on ``variant2``; set the verdicts (the
    original item ``verified (corrected)`` when its corrected item holds there; a
    dropped private column when the column shows again) and summarise again. Returns
    the baseline entries of the corrections that held."""
    if not corrections:
        return []
    wanted = sorted({c.corrected or c.item for c in corrections})
    users = seeder.credentials("variant2")
    again = run_probes(replace(fixed, users=users), sessions, seeder.make_db,
                       only=wanted, **common)  # fmt: skip
    baseline = []
    for c in corrections:
        v = rules.items[c.item]
        if c.corrected is not None:
            new = again.items.get(c.corrected)
            ok = new is not None and is_verified(new.verdict)
            c.reason = new.reason if new is not None else "the corrected item was not probed"
        else:  # a dropped fact: the original item is contradicted again on variant2
            old = _original_again(c, fixed, rules, sessions, seeder, common)
            ok = old == "refuted"
            c.reason = f"on variant2 the original item is {old}"
        c.verdict = VERIFIED_CORRECTED if ok else "refuted"
        if ok:
            rules.items[c.item] = ItemVerdict(
                v.item, VERIFIED_CORRECTED, f"corrected ({c.change}); {c.reason}",
                v.probes, v.observed, list(v.notes), v.witness,
            )  # fmt: skip
            if c.baseline is not None:
                baseline.append(c.baseline)
    summarise(fixed, rules)
    return baseline


def _original_again(c: Correction, fixed: Spec, rules: RuleResult, sessions: Any,
                    seeder: Seeder, common: Mapping[str, Any]) -> str:  # fmt: skip
    """The verdict of a dropped item's original on variant2 (the uncorrected spec)."""
    v = rules.items[c.item]
    if v.item.kind != "T2":
        return "unexercised"
    api = fixed.profile.api
    table = v.item.subject
    schema = api.tables[table]
    col = v.item.predicted.get("private")
    tables = dict(api.tables)
    tables[table] = replace(schema, private=frozenset(schema.private) | {col})
    spec = replace(fixed, profile=replace(fixed.profile, api=replace(api, tables=tables)),
                   users=seeder.credentials("variant2"))  # fmt: skip
    # with the R1 probes: their answers show the rows of every route the column could be in
    again = run_probes(spec, sessions, seeder.make_db, only=[c.item, "R1 "], **common)
    found = again.items.get(c.item)
    return found.verdict if found is not None else "unexercised"


class _Recording:
    """Mining's runs from the probes: the seed (once) and each probe's database after."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="lattice-rules-mine-"))
        self.seed: dict[str, Path] = {}
        self._runs: list[Any] = []

    def observe(self, probe: Any, seed_db: Path, after: Path, answer: Any) -> None:
        from lattice.importer.mine import Run

        key = str(seed_db)
        if key not in self.seed:
            self.seed[key] = self.dir / f"seed-{len(self.seed)}.db"
            shutil.copyfile(seed_db, self.seed[key])
        n = len(self._runs)
        snap = self.dir / f"probe-{n}.db"
        shutil.copyfile(after, snap)
        request = {**probe.request(), "status": answer.status}
        self._runs.append(Run(f"R-{n:04d}", (self.seed[key], snap), (request,)))

    def runs(self) -> list[Any]:
        return list(self._runs)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# ---- writing ---------------------------------------------------------------------------------


def write_rules(r: RulesImport, out: Path | str, *, package: str) -> Path:
    """Write the import directory: ``report.json`` / ``report.md`` (with ``rules``),
    ``profile.py``, ``candidate_spec.py`` (the corrected spec, with each item's status)
    and ``proposals.json`` (the mined proposals, pending)."""
    from lattice.importer.emit_spec import proposal_record, spec_source
    from lattice.importer.llm_lift import no_llm
    from lattice.importer.report import write_import

    close = no_llm(r.result)
    status = {i: v.verdict for i, v in r.rules.items.items()}
    proposals = [proposal_record(p) for p in (r.mining.proposals if r.mining else [])]
    extra = {
        "candidate_spec.py": spec_source(r.spec, status=status, package=package),
        "proposals.json": json.dumps(proposals, indent=2, default=str) + "\n",
    }
    if r.go_policy is not None:
        extra["go_policy.json"] = json.dumps(r.go_policy, indent=2) + "\n"
    return write_import(close, out, repo_root=r.result.context.root, package=package,
                        app_factory=r.app_factory, rules=r.rules_dict(),
                        extra_files=extra)  # fmt: skip


def summary_line(out: Path, r: RulesImport) -> str:
    s = r.rules.summary
    items = ", ".join(f"{n} {v}" for v, n in s["items"].items())
    return (
        f"{out}: {r.framework}, {len(r.rules.items)} rule items ({items}); tables verified "
        f"{s['tables']['verified']}/{s['tables']['total']}, routes verified "
        f"{s['routes']['verified']}/{s['routes']['total']}; {len(r.corrections)} corrections, "
        f"{len(r.baseline)} baseline; {r.timing.get('total', 0):.1f}s"
    )
