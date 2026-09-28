"""The thin emitter (thin-slice plan 2026-09-27, node E2): Lattice programs -> a generated
FastAPI router package, mounted into the vendored FastAPI template in place of the
template routes the program lifted (the others are kept).

``emit_into_template(lattice_sources, profile, out_dir)`` checks and lowers the program
and generates its routes with the existing codegen (``lattice.api.codegen``, proven equal
to the runtime), then writes ``out_dir`` as a relocatable Python package:

* ``routes.py`` -- the generated module, ``target="router"``: the same request pipeline
  as the standalone app, its routes on ``router = APIRouter()``, no app-level handlers;
  it imports ``get_db`` from ``.kit`` and its natives from ``.natives`` (relative
  imports, so the package loads under any name);
* ``kit.py`` -- ``get_db()``: a fresh ``sqlite3`` connection to ``DB_PATH`` (set by
  :func:`make_emitted_app`), with the platform kit's ``_on_connect`` hook;
* ``natives.py`` -- re-exports the profile's ``NATIVES`` by name. For a profile object
  they come from ``natives_from`` (default ``bench.template_profile``, whose natives
  wrap the template's own ``core/security.py``); for a profile *source* (e.g.
  ``lattice.importer.profile_gen`` output) the source is written as ``profile.py`` and
  its ``NATIVES`` are re-exported;
* ``schema.json`` -- the profile's tables (SQL name, columns, scalar types, nullability,
  keys), the emitted routes and the lifted ones, read back by :func:`make_emitted_app`.

Mounting. ``make_emitted_app(out_dir, db_path)`` builds the template app itself
(``bench.template_app.make_app``: settings, CORS, docs and error handling) and merges the
template's API routes with the generated ones (:func:`merge_routes`) with import
semantics: the emitted program replaces only the template routes it lifted, and every
other template route stays, unchanged, as a native island. Each merged route is included
in a router of its own, in the merged order (a template route under the API prefix, as
``create_app`` includes it; FastAPI keeps an included router as one entry, so this is
what orders them).

* **Matching.** A route's key is its method and its *normalized* path: every path
  parameter ``{name}`` (or ``{name:conv}``) becomes ``{}``, so ``{id}`` matches
  ``{item_id}``; nothing else is normalized (``/items/`` and ``/items`` differ, as they do
  in Starlette). The *replaced* keys are those of the emitted routes plus those of the
  ``lifted`` routes :func:`emit_into_template` was given (the routes of the program the
  edit started from: a lifted route the edited program no longer defines is removed from
  the app, not handed back to the template). A template route is removed when its keys
  are all replaced; the rest are kept.
* **Precedence** (Starlette routes a request to the first route in list order whose path
  *and* method match; a path match with another method does not stop the search). An
  emitted route that replaces a template route takes that route's place in the list, so
  the lifted routes keep the template's own order. An emitted route with no template
  counterpart (a new route) is inserted relative to every route it *overlaps* (same
  method, same number of segments, each segment pair equal or one side a parameter):
  before the first one it is *more specific* than (it has a literal segment wherever the
  two differ, e.g. ``/users/item-counts`` vs ``/users/{user_id}``), and after every other
  one (more specific, equally specific or incomparable: the route already there keeps
  precedence); should the two conflict (the list already has an inversion), not being
  shadowed wins. With no route it is more specific than, it goes at the end. The new
  routes are placed in the router's order, which the codegen sorts by specificity. So a
  new literal route is never shadowed by a kept or lifted parameter route, and a new
  route never shadows a kept one.

**Other apps** (:func:`emit_into_app`, e.g. an import of a held-out repo). The same
merge over the whole route list of the app a factory (``"module:function"``, recorded in
``schema.json``) builds: every route with its full path (an included router's prefix
applied), each kept route re-included alone with its include context (prefix, tags,
dependencies). The profile module's ``settings_from_app(app)``, when present, supplies
the NativeContext settings from that app; schema evolution runs once the app's tables
exist (at once on a seeded database, else after the app's own lifespan created them).

Lattice paths are absolute (``/api/v1/items/``), so the router is included without a
prefix; each route is tagged with its first path segment after the API prefix, so the
template's ``custom_generate_unique_id`` (``f"{tag}-{name}"``) gives the template's own
operation ids. The generated routes therefore sit next to the template's remaining
routes in one app instead of a sub-application mount, which would bring its own 404/405
handling and OpenAPI document. Every call loads a fresh copy of the package, so each app
has its own database path.

Schema evolution (:func:`evolve_schema`, run by :func:`make_emitted_app` after the
template's ``create_all``). A Lattice edit may add to the profile's schemas; the emitted
app brings the SQLite database in line, additively only:

* a profile table missing from the database is created (``CREATE TABLE``: the columns,
  ``NOT NULL`` unless nullable, the first key as the primary key, other keys ``UNIQUE``);
* a profile column missing from its table is added (``ALTER TABLE ... ADD COLUMN``) only
  when it is nullable (a Lattice schema declares no defaults, so a required column has
  no value for existing rows);
* anything else is a :class:`SchemaEvolutionError`, checked before any change is made:
  a new required column, a column whose declared SQL type cannot hold the profile's
  scalar (by SQLite type affinity), and a column the profile no longer has that is
  ``NOT NULL`` without a default (the generated inserts could not fill it). A database
  column the profile does not name but that is nullable, or has a default, is left as it
  is (never dropped).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import itertools
import json
import re
import sqlite3
import sys
from collections.abc import AsyncIterator, Collection, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from lattice.api.codegen import compile_source_to_fastapi
from lattice.api.ir import lower_api
from lattice.check import check_source
from lattice.diagnostics import Diagnostic
from lattice.profile import Profile
from lattice.typesys import Scalar

__all__ = [
    "EmitError",
    "EmitResult",
    "SchemaEvolutionError",
    "app_routes",
    "emit_into_app",
    "emit_into_template",
    "evolve_schema",
    "make_emitted_app",
    "merge_routes",
    "program_routes",
]

HEADER = "# lattice: v1\n"
API_PREFIX = "/api/v1"
DEFAULT_NATIVES = "bench.template_profile"
SCHEMA_FILE = "schema.json"

_INIT = '''"""Generated by lattice.emit.thin -- do not edit.

The Lattice program's routes (routes.py, a router), its natives (natives.py) and the
database connection (kit.py); mounted into the template by
lattice.emit.thin.make_emitted_app.
"""
'''

_KIT = '''"""Generated by lattice.emit.thin -- do not edit.

The database connection of the generated routes (the platform kit's get_db): a fresh
SQLite connection to DB_PATH, which lattice.emit.thin.make_emitted_app sets.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

DB_PATH = "app.db"


def _default_on_connect(_conn: sqlite3.Connection) -> None:
    """No-op; a harness may replace ``_on_connect`` (e.g. to count statements)."""


_on_connect: Callable[[sqlite3.Connection], None] = _default_on_connect


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _on_connect(conn)
    return conn
'''


class EmitError(Exception):
    """The Lattice program was rejected by the checker (nothing was written)."""

    def __init__(self, diagnostics: Sequence[Diagnostic]) -> None:
        self.diagnostics = list(diagnostics)
        rendered = "\n".join(d.render() for d in self.diagnostics)
        super().__init__(f"the Lattice program is rejected:\n{rendered}")


class SchemaEvolutionError(Exception):
    """The profile's schema differs from the database in a non-additive way."""


@dataclass(frozen=True)
class EmitResult:
    out_dir: Path
    files: tuple[Path, ...]  # every file written, in order
    lines: int  # lines of the emitted Python files
    ast_nodes: int  # AST nodes of the emitted Python files
    routes_emitted: tuple[str, ...]  # "METHOD /path" of the generated routes
    routes_replaced: tuple[str, ...]  # "METHOD /path" of the template routes removed
    routes_kept: tuple[str, ...] = ()  # "METHOD /path" of the template routes kept


# ---- emitting -------------------------------------------------------------------------------


def join_sources(lattice_sources: Mapping[str, str] | Sequence[str] | str) -> str:
    """One program from several Lattice sources (each file's ``# lattice: v1`` once)."""
    if isinstance(lattice_sources, str):
        return lattice_sources
    items = (
        list(lattice_sources.items())
        if isinstance(lattice_sources, Mapping)
        else [(f"source {i}", s) for i, s in enumerate(lattice_sources)]
    )
    parts = []
    for name, src in items:
        if not src.startswith(HEADER):
            raise ValueError(f"{name} must start with {HEADER!r}")
        parts.append(src.removeprefix(HEADER).strip("\n"))
    return HEADER + "\n\n\n".join(parts) + "\n"


def _natives_module(module: str) -> str:
    return (
        '"""Generated by lattice.emit.thin -- do not edit.\n\n'
        f"The natives of the generated routes, re-exported by name from {module}.\n"
        '"""\n\n'
        f"from {module} import NATIVES\n\n"
        "globals().update(NATIVES)\n"
        "__all__ = sorted(NATIVES)\n"
    )


def _load_profile(package: ModuleType) -> Profile:
    """The Profile a written ``profile.py`` defines: ``profile()``, else its one
    zero-argument ``*_profile()`` function."""
    module = importlib.import_module(f"{package.__name__}.profile")
    fn = getattr(module, "profile", None)
    if not callable(fn):
        found = [
            v
            for k, v in vars(module).items()
            if k.endswith("_profile")
            and callable(v)
            and getattr(v, "__module__", None) == module.__name__
        ]
        if len(found) != 1:
            raise ValueError("the profile source must define profile() returning a Profile")
        fn = found[0]
    profile = fn()
    if not isinstance(profile, Profile):
        raise ValueError(f"{fn.__name__}() returned {type(profile).__name__}, not a Profile")
    return profile


def _schema_manifest(profile: Profile) -> dict[str, Any]:
    api = profile.api
    if api is None:
        raise ValueError("the profile has no API part (ApiProfile)")
    tables = []
    for name, schema in api.tables.items():
        tables.append(
            {
                "schema": name,
                "table": api.table_names.get(name, name),
                "columns": [[c, s.value] for c, s in schema.columns],
                "nullable": sorted(schema.nullable),
                "keys": [list(k) for k in schema.keys],
            }
        )
    return {"uuid_storage": api.uuid_storage, "tables": tables}


def _template_routes() -> tuple[tuple[str, str], ...]:
    """(method, full path) of every route of the template's API router, in order."""
    from bench.template_app.app.api.main import api_router

    return tuple(
        (method, f"{API_PREFIX}{route.path}")
        for route in _leaf_routes(api_router)
        for method in sorted(route.methods)
    )


def _leaf_routes(router: APIRouter) -> list[APIRoute]:
    """The routes of ``router`` and of the routers it includes, in order. FastAPI keeps an
    included router as one entry (``original_router``; the template includes its routers
    without extra arguments); the routes carry their own router's prefix, tags and
    dependencies."""
    out: list[APIRoute] = []
    for route in router.routes:
        sub = getattr(route, "original_router", None)
        if sub is not None:
            out.extend(_leaf_routes(sub))
        elif isinstance(route, APIRoute):
            out.append(route)
        else:
            raise TypeError(f"cannot merge the route {route!r} (not an APIRoute)")
    return out


def _split_routes(routes: Iterable[str]) -> set[tuple[str, str]]:
    out = set()
    for r in routes:
        method, _, path = r.partition(" ")
        out.add((method.upper(), _normalize(path)))
    return out


def _replaced_keys(emitted: Iterable[str], lifted: Iterable[str]) -> set[tuple[str, str]]:
    return _split_routes(emitted) | _split_routes(lifted)


def program_routes(
    lattice_sources: Mapping[str, str] | Sequence[str] | str, profile: Profile
) -> tuple[str, ...]:
    """ "METHOD /path" of the endpoints of a Lattice program (e.g. the lifted routes of
    the program an edit starts from); a rejected program is an :class:`EmitError`."""
    result = check_source(join_sources(lattice_sources), profile)
    if result.program is None:
        raise EmitError(result.diagnostics)
    return tuple(f"{p.method} {p.path}" for p in lower_api(result.program))


def _size(files: Sequence[Path]) -> tuple[int, int]:
    lines = nodes = 0
    for path in files:
        if path.suffix == ".py":
            text = path.read_text()
            lines += len(text.splitlines())
            nodes += sum(1 for _ in ast.walk(ast.parse(text)))
    return lines, nodes


def emit_into_template(
    lattice_sources: Mapping[str, str] | Sequence[str] | str,
    profile_source_or_obj: Profile | str,
    out_dir: str | Path,
    *,
    natives_from: str = DEFAULT_NATIVES,
    lifted: Iterable[str] | None = None,
) -> EmitResult:
    """Generate the program's routes and write them, with their natives and database
    kit, as a package in ``out_dir``. ``lattice_sources``: one source, a list, or a
    mapping file name -> source (joined in order). ``profile_source_or_obj``: a
    :class:`Profile` (its natives are re-exported from ``natives_from``) or a profile
    module's source (written as ``profile.py``; ``profile()`` gives the Profile and its
    ``NATIVES`` are re-exported). A rejected program is an :class:`EmitError`.

    ``lifted``: "METHOD /path" of the routes the program lifted from the template (the
    routes of the program the edit started from, e.g. :func:`program_routes` of it);
    the template routes they match are removed from the app even when the edited program
    no longer defines them. Default: none beyond the emitted routes. The result reports
    the template routes replaced and kept (see the module docstring)."""
    return _emit(
        lattice_sources, profile_source_or_obj, out_dir, natives_from=natives_from,
        lifted=lifted, app_factory=None,
    )  # fmt: skip


def emit_into_app(
    lattice_sources: Mapping[str, str] | Sequence[str] | str,
    profile_source: str,
    out_dir: str | Path,
    *,
    app_factory: str,
    lifted: Iterable[str] | None = None,
) -> EmitResult:
    """:func:`emit_into_template` for any app: ``app_factory`` (``"module:function"``,
    ``function(db_path) -> FastAPI``) builds the original app the routes are merged into
    (:func:`make_emitted_app`, with the same matching and precedence over *all* of that
    app's routes, full paths). ``profile_source`` is a profile module's source (e.g. an
    import's ``profile.py``); when it defines ``settings_from_app(app)``, the values of
    its NativeContext settings are read from the original app when it is mounted."""
    return _emit(
        lattice_sources, profile_source, out_dir, natives_from=DEFAULT_NATIVES,
        lifted=lifted, app_factory=app_factory,
    )  # fmt: skip


def _emit(
    lattice_sources: Mapping[str, str] | Sequence[str] | str,
    profile_source_or_obj: Profile | str,
    out_dir: str | Path,
    *,
    natives_from: str,
    lifted: Iterable[str] | None,
    app_factory: str | None,
) -> EmitResult:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    def write(name: str, text: str) -> None:
        path = out / name
        path.write_text(text)
        files.append(path)

    source = join_sources(lattice_sources)
    write("__init__.py", _INIT)
    write("kit.py", _KIT)
    if isinstance(profile_source_or_obj, str):
        write("profile.py", profile_source_or_obj)
        natives = _natives_module(".profile")
        if app_factory is not None:  # filled from the app by make_emitted_app
            natives += _SETTINGS
        write("natives.py", natives)
        profile = _load_profile(_load_package(out))
    else:
        if app_factory is not None:
            raise ValueError("emit_into_app takes a profile module's source")
        profile = profile_source_or_obj
        write("natives.py", _natives_module(natives_from))

    result, code = compile_source_to_fastapi(
        source, profile, natives_module=".natives", target="router", kit_module=".kit"
    )
    if code is None:
        for path in files:
            path.unlink()
        raise EmitError(result.diagnostics)
    write("routes.py", code)
    assert result.program is not None
    emitted = tuple(f"{p.method} {p.path}" for p in lower_api(result.program))
    manifest = _schema_manifest(profile)
    manifest["routes_emitted"] = list(emitted)
    manifest["routes_lifted"] = list(lifted or ())
    if app_factory is not None:
        manifest["app_factory"] = app_factory
    write(SCHEMA_FILE, json.dumps(manifest, indent=2) + "\n")
    lines, nodes = _size(files)
    replaced = _replaced_keys(emitted, lifted or ())
    if app_factory is None:
        original = _template_routes()
    else:
        original = tuple(tuple(r.split(" ", 1)) for r in app_routes(app_factory))
    gone = tuple(f"{m} {p}" for m, p in original if (m, _normalize(p)) in replaced)
    kept = tuple(f"{m} {p}" for m, p in original if (m, _normalize(p)) not in replaced)
    return EmitResult(out, tuple(files), lines, nodes, emitted, gone, kept)


_SETTINGS = (
    "\n# NativeContext settings: read from the original app when it is mounted\n"
    "SETTINGS: dict[str, object] = {}\n"
)


def resolve_factory(spec: str) -> Any:
    """The callable ``"module:function"`` names."""
    module, sep, name = spec.partition(":")
    if not sep or not module or not name:
        raise ValueError(f"an app factory is 'module:function', got {spec!r}")
    obj: Any = importlib.import_module(module)
    for part in name.split("."):
        obj = getattr(obj, part)
    return obj


def _app_leaves(router: Any, ctx: Any = None) -> list[tuple[Any, frozenset[str], str]]:
    """Every route of ``router`` and of the routers it includes, in order, as (the entry
    that mounts that one route in an app's route list, its methods, its full path). An
    included router's route is re-wrapped with the include context it had (prefix, tags,
    dependencies, ...), alone, so it can be placed on its own."""
    from fastapi.routing import _IncludedRouter

    out: list[tuple[Any, frozenset[str], str]] = []
    for route in router.routes:
        if isinstance(route, _IncludedRouter):
            child = route.include_context if ctx is None else ctx.combine(route.include_context)
            out.extend(_app_leaves(route.original_router, child))
            continue
        methods = frozenset(getattr(route, "methods", None) or ())
        path = getattr(route, "path", "")
        if ctx is None:
            out.append((route, methods, path))
        else:
            single = APIRouter()
            single.routes.append(route)
            entry = _IncludedRouter(
                original_router=single, include_context=replace(ctx, included_router=single)
            )
            out.append((entry, methods, ctx.prefix + path))
    return out


def _dispose(app: Any) -> None:
    engine = getattr(getattr(app, "state", None), "engine", None)
    if engine is not None:
        engine.dispose()


def app_routes(app_factory: str) -> tuple[str, ...]:
    """"METHOD /path" of every route of the app ``app_factory`` builds (on a scratch
    database), in order, full paths (HEAD excluded)."""
    import tempfile

    factory = resolve_factory(app_factory)
    with tempfile.TemporaryDirectory(prefix="lattice-emit-routes-") as tmp:
        app = factory(Path(tmp) / "routes.db")
        try:
            leaves = _app_leaves(app.router)
        finally:
            _dispose(app)
    return tuple(
        f"{m} {path}" for _, methods, path in leaves for m in sorted(methods) if m != "HEAD"
    )


# ---- loading and mounting -------------------------------------------------------------------

_counter = itertools.count()


def _load_package(out_dir: Path) -> ModuleType:
    """A fresh copy of the emitted package under a unique module name."""
    name = f"_lattice_emitted_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(
        name, out_dir / "__init__.py", submodule_search_locations=[str(out_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the emitted package in {out_dir}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return package


def make_emitted_app(out_dir: str | Path, db_path: str | Path) -> FastAPI:
    """The template app on ``db_path`` with the emitted routes in place of the template
    routes they replace (see the module docstring: matching and precedence). The
    database is first brought in line with the profile's schema (:func:`evolve_schema`;
    the changes made are on ``app.state.schema_changes``). The template's engine is on
    ``app.state.engine`` (dispose it when done)."""
    from bench.template_app import make_app
    from bench.template_app.app import main as template_main

    out = Path(out_dir)
    manifest = json.loads((out / SCHEMA_FILE).read_text())
    replaced = _replaced_keys(manifest["routes_emitted"], manifest.get("routes_lifted", ()))
    if manifest.get("app_factory"):
        return _make_into_app(out, Path(db_path), manifest, replaced)

    # The template's create_app() includes the module-global api_router; for this app it
    # includes an empty one (the template module is restored), and the merged API routes
    # are included below, each in a router of its own, in their merged order.
    original = template_main.api_router
    template_routes = _leaf_routes(original)
    template_main.api_router = APIRouter()
    try:
        app = make_app(db_path)  # the template's tables (create_all) and everything else
    finally:
        template_main.api_router = original
    changes = _evolve(Path(db_path), manifest)

    package = _load_package(out)
    try:
        kit = importlib.import_module(f"{package.__name__}.kit")
        kit.DB_PATH = str(db_path)
        routes = importlib.import_module(f"{package.__name__}.routes")
    finally:  # the app holds what it needs; keep sys.modules from growing per app
        for name in [n for n in sys.modules if n.split(".")[0] == package.__name__]:
            del sys.modules[name]
    lattice_routes = _leaf_routes(routes.router)
    for route in lattice_routes:
        route.tags = [_tag(route.path)]
    order = _merge_order(
        [(r.methods, f"{API_PREFIX}{r.path}") for r in template_routes],
        [(r.methods, r.path) for r in lattice_routes],
        replaced,
    )
    for side, i in order:
        single = APIRouter()
        if side == "o":  # as create_app includes it: under the API prefix
            single.routes.append(template_routes[i])
            app.include_router(single, prefix=API_PREFIX)
        else:  # Lattice paths are absolute: no prefix
            single.routes.append(lattice_routes[i])
            app.include_router(single)
    app.state.schema_changes = changes
    app.state.emitted = package
    return app


def _make_into_app(
    out: Path, db_path: Path, manifest: Mapping[str, Any], replaced: set[tuple[str, str]]
) -> FastAPI:
    """make_emitted_app for an app built by ``manifest["app_factory"]``: its whole route
    list (full paths) merged with the emitted routes (:func:`_merge_order`)."""
    app = resolve_factory(manifest["app_factory"])(db_path)
    package = _load_package(out)
    try:
        profile_module = importlib.import_module(f"{package.__name__}.profile")
        natives = importlib.import_module(f"{package.__name__}.natives")
        reader = getattr(profile_module, "settings_from_app", None)
        if callable(reader):  # before routes.py binds its context natives
            natives.SETTINGS.update(reader(app))
        kit = importlib.import_module(f"{package.__name__}.kit")
        kit.DB_PATH = str(db_path)
        routes = importlib.import_module(f"{package.__name__}.routes")
    finally:  # the app holds what it needs; keep sys.modules from growing per app
        for name in [n for n in sys.modules if n.split(".")[0] == package.__name__]:
            del sys.modules[name]
    leaves = _app_leaves(app.router)
    lattice_routes = _leaf_routes(routes.router)
    order = _merge_order(
        [(methods, path) for _, methods, path in leaves],
        [(r.methods, r.path) for r in lattice_routes],
        replaced,
    )
    app.router.routes.clear()
    for side, i in order:
        if side == "o":
            app.router.routes.append(leaves[i][0])
        else:  # Lattice paths are absolute: no prefix
            single = APIRouter()
            single.routes.append(lattice_routes[i])
            app.include_router(single)
    mark = getattr(app.router, "_mark_routes_changed", None)
    if callable(mark):
        mark()
    app.state.schema_changes = _evolve_when_ready(app, db_path, manifest)
    app.state.emitted = package
    return app


def _evolve_when_ready(app: FastAPI, db_path: Path, manifest: Mapping[str, Any]) -> list[str]:
    """Schema evolution now when the app's tables exist (a seeded database); else when
    the app starts, after its own lifespan created them (the DDL run then is appended
    to the returned list)."""
    names = {t["table"] for t in manifest["tables"]}
    conn = sqlite3.connect(db_path)
    try:
        found = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    if names <= found:
        return _evolve(db_path, manifest)
    changes: list[str] = []
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        async with original(a) as state:
            changes.extend(_evolve(db_path, manifest))
            yield state

    app.router.lifespan_context = lifespan
    return changes


def _tag(path: str) -> str:
    rest = path.removeprefix(API_PREFIX).strip("/")
    return rest.split("/", 1)[0] or "root"


# ---- merging the route lists ----------------------------------------------------------------

_PARAM = re.compile(r"\{[^{}]*\}")


def _normalize(path: str) -> str:
    """The path with every parameter spelled ``{}`` (``{id}`` matches ``{item_id}``)."""
    return _PARAM.sub("{}", path)


def _is_param(segment: str) -> bool:
    return _PARAM.fullmatch(segment) is not None


def _overlap(a: str, b: str) -> bool:
    """Some request path matches both route paths (parameters match a non-empty segment)."""
    sa, sb = a.split("/"), b.split("/")
    return len(sa) == len(sb) and all(
        x == y or (_is_param(x) and y != "") or (_is_param(y) and x != "")
        for x, y in zip(sa, sb, strict=True)
    )


def _more_specific(a: str, b: str) -> bool:
    """``a`` overlaps ``b`` and has a literal segment wherever they differ (parameter
    names aside), and they do."""
    if not _overlap(a, b):
        return False
    pairs = zip(_normalize(a).split("/"), _normalize(b).split("/"), strict=True)
    diffs = [(x, y) for x, y in pairs if x != y]
    return bool(diffs) and all(y == "{}" for _, y in diffs)


def _merge_order(
    original: Sequence[tuple[Collection[str], str]],
    lattice: Sequence[tuple[Collection[str], str]],
    replaced: Collection[tuple[str, str]],
) -> list[tuple[str, int]]:
    """The merged route list as ``("o", i)`` (original ``i``) / ``("l", j)`` (Lattice
    ``j``) entries; routes are (methods, path). See the module docstring."""

    def is_replaced(methods: Collection[str], path: str) -> bool:
        return bool(methods) and all((m, _normalize(path)) in replaced for m in methods)

    merged: list[tuple[str, int]] = []
    slots: dict[tuple[str, str], int] = {}  # replaced key -> its original's position
    for i, (methods, path) in enumerate(original):
        if is_replaced(methods, path):
            for m in methods:
                slots.setdefault((m, _normalize(path)), len(merged))
            merged.append(("gone", i))
        else:
            merged.append(("o", i))
    new: list[int] = []
    for j, (methods, path) in enumerate(lattice):
        at = next((slots[k] for m in methods if (k := (m, _normalize(path))) in slots), None)
        if at is not None and merged[at][0] == "gone":
            merged[at] = ("l", j)
        else:
            new.append(j)
    merged = [e for e in merged if e[0] != "gone"]

    def key(entry: tuple[str, int]) -> tuple[Collection[str], str]:
        side, i = entry
        return original[i] if side == "o" else lattice[i]

    for j in new:
        methods, path = lattice[j]
        hi = None  # the first route this one is more specific than (else: the end)
        for idx, entry in enumerate(merged):
            other_methods, other = key(entry)
            if not set(methods) & set(other_methods) or not _overlap(path, other):
                continue
            if _more_specific(path, other):
                hi = idx
                break
        merged.insert(len(merged) if hi is None else hi, ("l", j))
    return merged


def merge_routes(
    original: Sequence[tuple[str, str]],
    lattice: Sequence[tuple[str, str]],
    replaced: Collection[tuple[str, str]],
) -> list[tuple[str, str]]:
    """The merged (method, path) route list of ``original`` (the template's) and
    ``lattice`` (the emitted router's), where ``replaced`` are the (method, path) keys the
    program replaces (paths normalized here); see the module docstring for the rule."""
    order = _merge_order(
        [({m}, p) for m, p in original],
        [({m}, p) for m, p in lattice],
        {(m, _normalize(p)) for m, p in replaced},
    )
    return [original[i] if side == "o" else lattice[i] for side, i in order]


# ---- schema evolution -----------------------------------------------------------------------

_SQL_TYPE = {
    Scalar.STR.value: "VARCHAR",
    Scalar.INT.value: "INTEGER",
    Scalar.FLOAT.value: "FLOAT",
    Scalar.BOOL.value: "BOOLEAN",
    Scalar.DATE.value: "DATE",
    Scalar.DATETIME.value: "DATETIME",
}
# SQLite type affinities that can hold each scalar as the runtime stores it.
_AFFINITIES = {
    Scalar.STR.value: {"TEXT"},
    Scalar.UUID.value: {"TEXT"},
    Scalar.INT.value: {"INTEGER", "NUMERIC"},
    Scalar.FLOAT.value: {"REAL", "NUMERIC"},
    Scalar.BOOL.value: {"INTEGER", "NUMERIC"},
    Scalar.DATE.value: {"TEXT", "NUMERIC"},
    Scalar.DATETIME.value: {"TEXT", "NUMERIC"},
}


def _affinity(declared: str) -> str:
    """SQLite's type affinity of a declared column type (sqlite.org/datatype3 §3.1)."""
    t = declared.upper()
    if "INT" in t:
        return "INTEGER"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or not t:
        return "BLOB"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _sql_type(scalar: str, uuid_storage: str) -> str:
    if scalar == Scalar.UUID.value:
        return "CHAR(32)" if uuid_storage == "hex32" else "CHAR(36)"
    return _SQL_TYPE[scalar]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _plan_changes(conn: sqlite3.Connection, manifest: Mapping[str, Any]) -> list[str]:
    """The DDL that brings the database in line, or a SchemaEvolutionError."""
    changes: list[str] = []
    errors: list[str] = []
    uuid_storage = manifest.get("uuid_storage", "canonical")
    for t in manifest["tables"]:
        table, nullable = t["table"], set(t["nullable"])
        info = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
        if not info:
            keys = t["keys"]
            cols = []
            for col, scalar in t["columns"]:
                spec = f"{_q(col)} {_sql_type(scalar, uuid_storage)}"
                if col not in nullable:
                    spec += " NOT NULL"
                cols.append(spec)
            if keys:
                cols.append(f"PRIMARY KEY ({', '.join(map(_q, keys[0]))})")
            cols += [f"UNIQUE ({', '.join(map(_q, k))})" for k in keys[1:]]
            changes.append(f"CREATE TABLE {_q(table)} ({', '.join(cols)})")
            continue
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        existing = {row[1]: row for row in info}
        profile_cols = {c for c, _ in t["columns"]}
        for col, scalar in t["columns"]:
            row = existing.get(col)
            if row is None:
                if col not in nullable:
                    errors.append(
                        f"{table}.{col}: a new column must be nullable (a Lattice schema "
                        "declares no default for the existing rows)"
                    )
                    continue
                changes.append(
                    f"ALTER TABLE {_q(table)} ADD COLUMN {_q(col)} "
                    f"{_sql_type(scalar, uuid_storage)}"
                )
                continue
            affinity = _affinity(row[2])
            if affinity != "BLOB" and affinity not in _AFFINITIES[scalar]:
                errors.append(
                    f"{table}.{col}: the database declares {row[2] or 'no type'} "
                    f"({affinity} affinity), which cannot hold the profile's {scalar}; "
                    "changing a column's type is not supported"
                )
        for col, row in existing.items():
            if col not in profile_cols and row[3] and row[4] is None and not row[5]:
                errors.append(
                    f"{table}.{col}: the profile no longer has this NOT NULL column (without "
                    "a default), so the emitted inserts could not fill it; dropping a "
                    "column is not supported"
                )
    if errors:
        raise SchemaEvolutionError(
            "the profile's schema is not an additive change of the database:\n  "
            + "\n  ".join(errors)
        )
    return changes


def _evolve(db_path: Path, manifest: Mapping[str, Any]) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        changes = _plan_changes(conn, manifest)
        if changes:
            with conn:
                for ddl in changes:
                    conn.execute(ddl)
        return changes
    finally:
        conn.close()


def evolve_schema(db_path: str | Path, profile: Profile) -> list[str]:
    """Bring the SQLite database at ``db_path`` in line with ``profile``'s tables,
    additively (see the module docstring); the DDL statements run, in order."""
    return _evolve(Path(db_path), _schema_manifest(profile))
