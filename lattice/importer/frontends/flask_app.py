"""The Flask + SQLAlchemy front end (Phase 2 plan §2.2, ``flask_app.py``; W2-B).

:func:`extract` builds the app once (the package's ``make_app(db_path)``, found in the
package or a parent package, on a throw-away SQLite file) and reads:

* **tables** from the live SQLAlchemy metadata (:mod:`.sqlalchemy_orm`), with the
  ``CheckConstraint`` s and composite uniques handed to mining (``RepoModel.constraints``);
* **routes** from ``app.url_map`` (blueprint prefixes and methods included; a
  ``MethodView`` 's methods each a route). Routes of the framework (static files, APIFairy
  docs, an index redirecting to them) are set apart as ``excluded_routes`` of kind
  ``framework``; routes probes cannot exercise (they call the network, or redirect to an
  outside service) stay in ``routes`` and are listed as ``unreachable``;
* **guards** (``Route.guards``), in the order a request meets them: the
  ``before_request`` functions of the app and the route's blueprints (their decorators,
  then the checks in their body), then the view's decorators, outermost first:

  - library decorators -- Flask-HTTPAuth ``<auth>.login_required`` (``role=`` a ``role``
    guard), APIFairy ``authenticate(<auth>, role=...)``, Flask-Login ``login_required``,
    flask-jwt-extended ``jwt_required`` -- are ``authenticated`` (or ``role``) guards;
  - the package's own decorators (flasky's ``permission_required(Permission.X)``): the
    wrapper's ``if <check on the caller>: <refusal>`` gives the refusal; the decorator is
    then *probed*, evaluated as written and called once per role with a stand-in caller,
    giving the roles it lets through (``role``; ``admin`` when only the admin role passes;
    ``custom`` when it cannot be probed);
  - a check ``not <caller>.<bool column>`` refusing is an ``active`` guard
    (``AuthModel.active_column``), e.g. flasky's unconfirmed account;
  - an owner comparison in the view body guarding a refusal is an ``object_owner`` guard
    with the roles that bypass it (``... and not g.current_user.can(Permission.ADMIN)``,
    evaluated per role);
* **owner evidence**: compares and assigns of a row's owner column (a foreign key to the
  users table, or a relationship to it) with the caller -- ``g.current_user``,
  Flask-Login's ``current_user``, ``<auth>.current_user()``, ``get_jwt_identity()`` or a
  local holding one -- including ``filter_by(<col>=<caller>)``, ``Row(<rel>=<caller>)``
  and the caller's own relationships (``user.posts``: ``posts.user_id``);
* **error sites**: ``abort(...)`` and ``*_or_404`` (``abort``), ``return <helper>('...')``
  / ``return ..., 4xx`` (``return``), ``raise`` of a werkzeug HTTP exception (``abort``)
  or of an exception an error handler answers (``handler``, also in the package
  functions the view calls);
* **roles**: a role table the users table references (flasky's ``roles`` rows with a
  permission bitmask: each role's permissions read by probing the package's permission
  decorator once per permission constant), or an enum column of the users table;
* **AuthModel**: the login route (an HTTP-Basic-guarded ``POST`` answering a token), the
  token's key in the answer, how protected routes take it (an ``HTTPTokenAuth`` scheme,
  or ``basic_token``: flasky's token as the Basic username), the users columns and the
  password hasher;
* request bodies and bounds from marshmallow schemas (APIFairy ``@body`` / ``@arguments``
  / ``@response``): ``Length``, ``Range``, ``OneOf``, ``Email``.

:meth:`FlaskFrontEnd.runtime` serves the app over WSGI (``transport.WsgiTransport``),
disposes of the engine through the package's ``dispose(app)`` (else every SQLAlchemy
engine the app holds), seeds with plain SQL, has no OpenAPI document, and recognises
Flask's error sites. :func:`auth_driver` logs callers in as the AuthModel says.

Importing this module registers the front end (``lattice.importer.frontends.register``).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lattice.importer.appmodel import (
    AuthModel,
    DataModel,
    ExcludedRoute,
    FieldSpec,
    Guard,
    OwnerUse,
    RaiseSite,
    RepoModel,
    RoleScheme,
    Route,
    RouteParam,
)
from lattice.importer.frontends import front_ends, register
from lattice.importer.frontends.pyindex import _Index, _lit
from lattice.importer.frontends.sqlalchemy_orm import OrmTables, package_modules, read_tables

if TYPE_CHECKING:
    from lattice.importer.graph import GraphHints
    from lattice.importer.transport import AppRuntime, AuthDriver

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ROUTE_DECORATORS = frozenset({"route", "get", "post", "put", "patch", "delete"})
ADMIN_HINTS = ("superuser", "admin", "staff")
NETWORK_MODULES = ("requests", "httpx", "urllib.request", "aiohttp", "smtplib", "http.client")
NOT_FOUND_CALLS = frozenset({"get_or_404", "first_or_404", "one_or_404"})
CALLER_G_ATTRS = frozenset({"current_user", "user"})
CALLER_CALLS = frozenset(
    {
        "flask_jwt_extended:get_jwt_identity",
        "flask_jwt_extended:get_current_user",
        "flask_jwt_extended.utils:get_jwt_identity",
        "flask_jwt_extended.utils:get_current_user",
    }
)
CALLER_NAMES = frozenset(
    {
        "flask_login:current_user",
        "flask_login.utils:current_user",
        "flask_jwt_extended:current_user",
        "flask_jwt_extended.utils:current_user",
        "flask_jwt:current_identity",
    }
)
CALLER_WRITES = frozenset({"update"})  # a method writing the caller's own row
HASHERS = ("generate_password_hash", "make_password", "hashpw", "hash_password")
_SENTINEL = object()


# ---- small AST helpers -----------------------------------------------------------------------


def _str(expr: ast.expr | None) -> str | None:
    value = _lit(expr)
    return value if isinstance(value, str) else None


def _int(expr: ast.expr | None) -> int | None:
    value = _lit(expr)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _name_of(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _body(fn: ast.AST) -> list[ast.stmt]:
    body = list(getattr(fn, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _walk_own(fn: ast.AST) -> Iterator[ast.AST]:
    """``ast.walk`` of a function's body, without descending into nested definitions."""
    todo = list(_body(fn)) if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) else [fn]
    while todo:
        node = todo.pop(0)
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                todo.append(child)


def flask_path(rule: str) -> str:
    """A Flask rule with ``{name}`` parameters: ``/posts/<int:id>`` -> ``/posts/{id}``."""
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule)


CONVERTER_TYPES = {"int": "Int", "float": "Float", "uuid": "Uuid"}


def _path_params(rule: str) -> tuple[RouteParam, ...]:
    out = []
    for m in re.finditer(r"<(?:([a-z]+)(?:\([^)]*\))?:)?([^<>]+)>", rule):
        conv, name = m.group(1) or "default", m.group(2)
        type_ = CONVERTER_TYPES.get(conv, "Str")
        out.append(RouteParam(name, conv, type_, "path"))
    return tuple(out)


# ---- the error sites (AST) -------------------------------------------------------------------


@dataclass
class ErrorSites:
    """Flask's error sites, from the package's source: ``helpers`` (a function building
    an error answer, e.g. flasky's ``forbidden(message)``: qualified name -> status) and
    ``handled`` (an exception class an error handler answers -> status)."""

    helpers: dict[str, int] = field(default_factory=dict)
    handled: dict[str, int] = field(default_factory=dict)
    handlers: list[str] = field(default_factory=list)  # "module:line" of each handler

    def by_short(self) -> tuple[dict[str, int], dict[str, int]]:
        short = lambda q: q.rsplit(":", 1)[-1].rsplit(".", 1)[-1]  # noqa: E731
        return (
            {short(q): s for q, s in self.helpers.items()},
            {short(q): s for q, s in self.handled.items()},
        )


def _werkzeug_code(name: str) -> int | None:
    try:
        from werkzeug import exceptions
    except ImportError:  # pragma: no cover
        return None
    cls = getattr(exceptions, name, None)
    code = getattr(cls, "code", None)
    return code if isinstance(cls, type) and isinstance(code, int) else None


def _helper_status(
    fn: ast.AST, resolve: Callable[[ast.expr], str | None], helpers: dict[str, int]
) -> int | None:
    for node in _walk_own(fn):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "status_code" for t in node.targets
        ):
            status = _int(node.value)
            if status is not None:
                return status
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Tuple) and len(v.elts) >= 2 and _int(v.elts[1]) is not None:
                return _int(v.elts[1])
            if isinstance(v, ast.Call):
                q = resolve(v.func)
                if q in helpers:
                    return helpers[q]
                if _name_of(v.func) == "make_response" and len(v.args) >= 2:
                    return _int(v.args[1])
        if isinstance(node, ast.Call) and _name_of(node.func) == "abort" and node.args:
            return _int(node.args[0])
    return None


def error_sites(index: _Index) -> ErrorSites:
    """The package's error helpers and handled exceptions (see :class:`ErrorSites`)."""
    sites = ErrorSites()
    for _ in range(2):  # a helper may return another helper's answer
        for mod, m in index.modules.items():
            for name, node in m.defs.items():
                if not isinstance(node, ast.FunctionDef):
                    continue
                if any(_is_errorhandler(d) for d in node.decorator_list):
                    continue
                status = _helper_status(
                    node, lambda e, mod=mod: index.resolve(mod, e), sites.helpers
                )
                if status is not None and status >= 400:
                    sites.helpers[f"{mod}:{name}"] = status
    for mod, m in index.modules.items():
        for node in ast.walk(m.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if not _is_errorhandler(dec) or not dec.args:  # type: ignore[union-attr]
                    continue
                sites.handlers.append(f"{mod}:{node.lineno}")
                exc = dec.args[0]  # type: ignore[union-attr]
                if _int(exc) is not None:
                    continue
                qual = index.resolve(mod, exc)
                if qual is None or qual.startswith("werkzeug.exceptions:"):
                    continue
                status = _helper_status(
                    node, lambda e, mod=mod: index.resolve(mod, e), sites.helpers
                )
                if status is not None:
                    sites.handled[qual] = status
    return sites


def _is_errorhandler(dec: ast.expr) -> bool:
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in ("errorhandler", "app_errorhandler")
    )


def make_error_site(sites: ErrorSites) -> Callable[[ast.AST], tuple[int | None, str | None] | None]:
    """The runtime's error-site predicate (``AppRuntime.error_site``): by name, as the
    branch tracer sees one function's source without its module."""
    helpers, handled = sites.by_short()

    def site(node: ast.AST) -> tuple[int | None, str | None] | None:
        if isinstance(node, ast.Call):
            name = _name_of(node.func)
            if name == "abort" and node.args:
                detail = _str(node.args[1]) if len(node.args) > 1 else None
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                return _int(node.args[0]), detail or _str(kw.get("description"))
            if name in NOT_FOUND_CALLS:
                return 404, None
            return None
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Call) and _name_of(v.func) in helpers:
                return helpers[_name_of(v.func)], _str(v.args[0]) if v.args else None  # type: ignore[index]
            if isinstance(v, ast.Tuple) and len(v.elts) >= 2:
                status = _int(v.elts[1])
                if status is not None and status >= 400:
                    return status, None
            return None
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            call = exc if isinstance(exc, ast.Call) else None
            name = _name_of(call.func if call is not None else exc)
            if name is None:
                return None
            detail = _str(call.args[0]) if call is not None and call.args else None
            if name in handled:
                return handled[name], detail
            code = _werkzeug_code(name)
            if code is not None:
                return code, detail
        return None

    return site


def make_error_class(sites: ErrorSites) -> Callable[[str], bool]:
    handled = set(sites.handled)

    def is_error(qual: str) -> bool:
        return qual.startswith("werkzeug.exceptions:") or qual in handled

    return is_error


# ---- finding and building the app ------------------------------------------------------------


def find_factory(package: str, app_factory: str | None = None) -> tuple[Callable, Callable | None]:
    """``(make_app, dispose or None)``: ``app_factory`` ("module:function"), else the
    ``make_app`` of the package or its nearest parent package that has one (the importer's
    convention for vendored apps), with that module's ``dispose`` when it has one."""
    if app_factory is not None:
        mod_name, fn = app_factory.split(":", 1)
        mod = importlib.import_module(mod_name)
        return getattr(mod, fn), getattr(mod, "dispose", None)
    parts = package.split(".")
    for n in range(len(parts), 0, -1):
        mod = importlib.import_module(".".join(parts[:n]))
        make = getattr(mod, "make_app", None)
        if callable(make):
            return make, getattr(mod, "dispose", None)
    raise LookupError(f"no make_app(db_path) in {package!r} or a parent package")


def dispose_engines(app: Any) -> None:
    """Dispose of every SQLAlchemy engine the app's extensions hold (Flask-SQLAlchemy's
    ``engines``; an extension with ``get_engine()``, as Alchemical)."""
    with app.app_context():
        for ext in {id(e): e for e in app.extensions.values()}.values():
            engines = getattr(ext, "engines", None)
            if isinstance(engines, Mapping):
                for engine in engines.values():
                    engine.dispose()
            elif callable(getattr(ext, "get_engine", None)):
                ext.get_engine().dispose()


@contextmanager
def built_app(package: str, app_factory: str | None = None) -> Iterator[tuple[Any, Path]]:
    """The app built on a throw-away SQLite file (disposed of and removed after)."""
    make, dispose = find_factory(package, app_factory)
    tmp = Path(tempfile.mkdtemp(prefix="lattice-flask-"))
    db = tmp / "extract.db"
    app = make(db)
    try:
        yield app, db
    finally:
        try:
            (dispose or dispose_engines)(app)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---- the extractor ---------------------------------------------------------------------------


@dataclass
class _Caller:
    """A stand-in caller for probing guards: a users row that is never stored."""

    role: str  # Lattice role name
    user: Any


class _Extractor:
    def __init__(self, root: Path, package: str, app: Any, db_path: Path) -> None:
        self.root = root
        self.package = package
        self.app = app
        self.db_path = db_path
        self.index = _Index(root, package)
        self.orm: OrmTables = read_tables(package)
        self.errors = error_sites(self.index)
        self.modules = {m.__name__: m for m in package_modules(package)}
        self.auths = self._http_auths()
        self.users = self._users_model()
        self.active_column: str | None = None
        self.callers: list[_Caller] = []
        self.role_scheme: RoleScheme | None = None
        self.role_table: str | None = None
        self.role_column: str | None = None
        self.admin_column: str | None = None
        self._session: Any = None
        self._engine: Any = None

    # -- resolution --

    def resolve(self, mod: str, expr: ast.expr | None) -> str | None:
        return self.index.resolve(mod, expr)

    def in_package(self, qual: str | None) -> bool:
        if qual is None:
            return False
        mod = qual.split(":", 1)[0]
        return mod == self.package or mod.startswith(f"{self.package}.")

    def table_class(self, mod: str, expr: ast.expr | None, locals_: set[str]) -> str | None:
        """The model name an expression names (a mapped class of the package)."""
        if isinstance(expr, ast.Name) and expr.id in locals_:
            return None
        qual = self.resolve(mod, expr)
        if qual is None or ":" not in qual:
            return None
        name = qual.rsplit(":", 1)[1]
        cls = self.orm.classes.get(name)
        if cls is not None and qual.split(":", 1)[0] == cls.__module__:
            return name
        return None

    def evaluate(self, mod: str, expr: ast.expr) -> Any:
        module = self.modules.get(mod) or importlib.import_module(mod)
        code = compile(ast.Expression(expr), f"<{mod}>", "eval")
        return eval(code, vars(module))  # noqa: S307 -- the app's own expression, as written

    def function_node(self, fn: Any) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
        """The AST of a live function (unwrapped) in the package."""
        fn = inspect.unwrap(fn)
        mod = getattr(fn, "__module__", None)
        code = getattr(fn, "__code__", None)
        m = self.index.modules.get(mod or "")
        if m is None or code is None:
            return None
        for node in ast.walk(m.tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == fn.__name__
            ):
                first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                if code.co_firstlineno in (first, node.lineno):
                    return mod, node  # type: ignore[return-value]
        return None

    def def_of(self, qual: str | None) -> tuple[str, ast.AST] | None:
        """The definition of a package function or method ("module:Name[.attr]")."""
        if not self.in_package(qual):
            return None
        mod, name = qual.split(":", 1)  # type: ignore[union-attr]
        m = self.index.modules.get(mod)
        if m is None:
            return None
        head, *rest = name.split(".")
        node = m.defs.get(head)
        for attr in rest:
            if not isinstance(node, ast.ClassDef):
                return None
            node = next(
                (
                    s
                    for s in node.body
                    if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef) and s.name == attr
                ),
                None,
            )
        return (mod, node) if node is not None else None

    # -- auth objects and the users table --

    def _http_auths(self) -> dict[int, tuple[str, str, Any]]:
        """Flask-HTTPAuth objects of the package: id -> (module, name, object)."""
        try:
            from flask_httpauth import HTTPAuth
        except ImportError:  # pragma: no cover
            return {}
        out: dict[int, tuple[str, str, Any]] = {}
        for mod, module in self.modules.items():
            assigned = self.index.modules.get(mod)
            for name, value in vars(module).items():
                if isinstance(value, HTTPAuth) and assigned is not None and name in assigned.defs:
                    out.setdefault(id(value), (mod, name, value))
        return out

    def _auth_callbacks(
        self, kind: str, only: str | None = None
    ) -> list[tuple[str, ast.FunctionDef]]:
        """``@<auth>.<kind>`` functions (``verify_password``, ``error_handler``...), of
        every auth object or of the one named ``only``."""
        out = []
        for mod, name, _ in self.auths.values():
            if only is not None and name != only:
                continue
            for node in self.index.modules[mod].tree.body:
                if isinstance(node, ast.FunctionDef) and any(
                    isinstance(d, ast.Attribute)
                    and d.attr == kind
                    and isinstance(d.value, ast.Name)
                    and d.value.id == name
                    for d in node.decorator_list
                ):
                    out.append((mod, node))
        return out

    def _users_model(self) -> str | None:
        """The users table: the mapped class the auth callbacks look callers up in, else
        a table named ``users`` / ``user``."""
        for kind in ("verify_password", "verify_token"):
            for mod, node in self._auth_callbacks(kind):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name | ast.Attribute):
                        name = self.table_class(mod, sub, set())
                        if name is not None:
                            return name
        for t in self.orm.tables:
            if t.table_name in ("users", "user") and self.orm.classes.get(t.name) is not None:
                return t.name
        return None

    def users_columns(self) -> dict[str, FieldSpec]:
        table = next((t for t in self.orm.tables if t.name == self.users), None)
        return {c.name: c for c in table.columns} if table else {}

    # -- callers --

    def is_caller(self, mod: str, e: ast.expr, callers: set[str]) -> bool:
        if isinstance(e, ast.Name):
            if e.id in callers:
                return True
            return self.resolve(mod, e) in CALLER_NAMES
        if isinstance(e, ast.Attribute):
            if e.attr in CALLER_G_ATTRS and self.resolve(mod, e.value) == "flask:g":
                return True
            return False
        if isinstance(e, ast.Call):
            if isinstance(e.func, ast.Attribute) and e.func.attr == "current_user":
                return True  # <auth>.current_user()
            return self.resolve(mod, e.func) in CALLER_CALLS
        return False

    def is_caller_id(self, mod: str, e: ast.expr, callers: set[str]) -> bool:
        if self.is_caller(mod, e, callers):
            return True
        return (
            isinstance(e, ast.Attribute)
            and e.attr == "id"
            and self.is_caller(mod, e.value, callers)
        )

    def mentions_caller(self, mod: str, e: ast.AST, callers: set[str]) -> bool:
        return any(isinstance(n, ast.expr) and self.is_caller(mod, n, callers) for n in ast.walk(e))

    # -- refusals --

    def refusal(self, mod: str, stmts: list[ast.stmt]) -> tuple[int | None, str | None, str] | None:
        """The error answer a block gives first: (status, detail, kind)."""
        for stmt in stmts:
            for node in ast.walk(stmt):
                found = self.site(mod, node)
                if found is not None:
                    return found
        return None

    def site(self, mod: str, node: ast.AST) -> tuple[int | None, str | None, str] | None:
        """An error site at ``node``: (status, detail, kind)."""
        if isinstance(node, ast.Call):
            qual = self.resolve(mod, node.func)
            if qual in ("flask:abort", "werkzeug.exceptions:abort") and node.args:
                detail = _str(node.args[1]) if len(node.args) > 1 else None
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                return _int(node.args[0]), detail or _str(kw.get("description")), "abort"
            if _name_of(node.func) in NOT_FOUND_CALLS:
                return 404, None, "abort"
            return None
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Call):
                qual = self.resolve(mod, v.func)
                if qual in self.errors.helpers:
                    return self.errors.helpers[qual], _str(v.args[0]) if v.args else None, "return"
            if isinstance(v, ast.Tuple) and len(v.elts) >= 2:
                status = _int(v.elts[1])
                if status is not None and status >= 400:
                    return status, None, "return"
            return None
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call | ast.Name):
            call = node.exc if isinstance(node.exc, ast.Call) else None
            qual = self.resolve(mod, call.func if call is not None else node.exc)
            detail = _str(call.args[0]) if call is not None and call.args else None
            if qual in self.errors.handled:
                return self.errors.handled[qual], detail, "handler"
            if qual is not None and qual.startswith("werkzeug.exceptions:"):
                return _werkzeug_code(qual.rsplit(":", 1)[1]), detail, "abort"
        return None

    # -- roles --

    def setup_roles(self) -> None:
        """The roles callers can hold, as stand-in callers (see the module docstring)."""
        import sqlalchemy as sa
        from sqlalchemy.orm import Session

        cls = self.orm.classes.get(self.users or "")
        if cls is None:
            return
        self._engine = sa.create_engine(f"sqlite:///{self.db_path}")
        self._session = Session(self._engine)
        mapper = sa.inspect(cls)
        cols = self.users_columns()
        self.active_column = self._find_active_column(cols)

        def stand_in(**values: Any) -> Any:
            user = mapper.class_manager.new_instance()
            if self.active_column:
                setattr(user, self.active_column, True)
            for k, v in values.items():
                setattr(user, k, v)
            return user

        # a role table the users table references (many-to-one)
        for key, rel in self.orm.relationships.get(self.users or "", {}).items():
            if rel.direction != "MANYTOONE" or rel.target == self.users:
                continue
            target = self.orm.classes.get(rel.target)
            table = self.orm.sa_tables.get(rel.target)
            label = next(
                (
                    c.name
                    for c in table.columns
                    if c.name in ("name", "title", "slug") and not c.primary_key
                ),
                None,
            )
            if target is None or label is None:
                continue
            rows = list(self._session.scalars(sa.select(target).order_by(*table.primary_key)))
            if not rows:
                continue
            pk = list(table.primary_key.columns)[0].name
            roles = []
            admin = None
            for row in rows:
                value = str(getattr(row, label))
                name = self._role_name(value, admin is None)
                if name == "Admin":
                    admin = name
                roles.append((value, str(getattr(row, pk)), name))
                self.callers.append(_Caller(name, stand_in(**{key: row})))
            self.role_scheme = RoleScheme(rel.column, tuple(roles), admin, None, None)
            self.role_table, self.role_column = rel.target, rel.column
            return
        # an enum role column of the users table
        enums = [c for c in cols.values() if c.choices and "role" in c.name.lower()]
        if len(enums) == 1:
            col = enums[0]
            attr = cls.__mapper__.columns[col.name].type
            enum_cls = getattr(attr, "enum_class", None)
            roles, admin = [], None
            members = list(enum_cls) if enum_cls is not None else list(col.choices)
            for i, member in enumerate(members):
                value = str(col.choices[i])
                stored = col.choice_names[i] if col.choice_names else value
                name = self._role_name(value, admin is None)
                if name == "Admin":
                    admin = name
                roles.append((value, stored, name))
                self.callers.append(_Caller(name, stand_in(**{col.name: member})))
            self.role_scheme = RoleScheme(col.name, tuple(roles), admin, None, None)
            self.role_column = col.name
            return
        # a boolean admin flag
        flag = next(
            (
                c.name
                for c in cols.values()
                if c.type == "Bool" and any(h in c.name.lower() for h in ADMIN_HINTS)
            ),
            None,
        )
        if flag is not None:
            self.admin_column = flag
            self.callers += [
                _Caller("User", stand_in(**{flag: False})),
                _Caller("Admin", stand_in(**{flag: True})),
            ]
            return
        self.callers.append(_Caller("User", stand_in()))

    @staticmethod
    def _role_name(value: str, admin_free: bool) -> str:
        from lattice.importer.frontends.fastapi import role_name

        if admin_free and any(h in value.lower() for h in ADMIN_HINTS):
            return "Admin"
        return role_name(value)

    def _find_active_column(self, cols: dict[str, FieldSpec]) -> str | None:
        """A Bool users column some check refuses callers on (``not <caller>.<col>``)."""
        for mod, m in self.index.modules.items():
            for node in ast.walk(m.tree):
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                    e = node.operand
                    if (
                        isinstance(e, ast.Attribute)
                        and e.attr in cols
                        and cols[e.attr].type == "Bool"
                        and not any(h in e.attr.lower() for h in ADMIN_HINTS)
                        and self.is_caller(mod, e.value, set())
                    ):
                        return e.attr
        return None

    def close(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        if self._engine is not None:
            self._engine.dispose()

    @contextmanager
    def as_caller(self, caller: _Caller | None) -> Iterator[None]:
        from flask import g

        with self.app.test_request_context():
            if caller is not None:
                g.current_user = caller.user
                g.flask_httpauth_user = caller.user
                g._login_user = caller.user
            yield

    def probe(self, call: Callable[[], Any]) -> dict[str, tuple[int | None, str | None] | None]:
        """Per role: None (let through) or the refusal (status, detail); an exception
        other than an HTTP error propagates."""
        from werkzeug.exceptions import HTTPException

        out: dict[str, tuple[int | None, str | None] | None] = {}
        for caller in self.callers:
            with self.as_caller(caller):
                try:
                    rv = call()
                except HTTPException as e:
                    out[caller.role] = (e.code, None)
                    continue
                if rv is _SENTINEL or rv is True:
                    out[caller.role] = None
                    continue
                if rv is False:
                    out[caller.role] = (None, None)
                    continue
                response = self.app.make_response(rv)
                body = response.get_json(silent=True)
                detail = None
                if isinstance(body, dict):
                    detail = next(
                        (
                            body[k]
                            for k in ("message", "detail", "error")
                            if isinstance(body.get(k), str)
                        ),
                        None,
                    )
                out[caller.role] = (response.status_code, detail)
        return out

    def role_order(self, roles: set[str]) -> tuple[str, ...]:
        return tuple(c.role for c in self.callers if c.role in roles)


# ---- guards ----------------------------------------------------------------------------------


def _lib(qual: str | None) -> str:
    return (qual or "").split(":", 1)[0].split(".", 1)[0]


class _Guards:
    """Classifies guards (see the module docstring)."""

    def __init__(self, ex: _Extractor) -> None:
        self.ex = ex
        self.permissions: dict[str, int] = {}
        self.role_permissions: dict[str, list[str]] = {}

    def auth_refusal(self, obj: Any) -> tuple[int, str | None]:
        """A Flask-HTTPAuth object's refusal: its error handler's answer (401 default)."""
        mod, name, _ = self.ex.auths[id(obj)]
        for m, node in self.ex._auth_callbacks("error_handler", name):
            found = self.ex.refusal(m, _body(node))
            if found is not None and found[0] is not None:
                return found[0], found[1]
        return 401, None

    def decorator(self, mod: str, dec: ast.expr) -> tuple[list[Guard], list[RaiseSite]]:
        """The guards a view decorator applies, and its other error sites."""
        ex = self.ex
        call = dec if isinstance(dec, ast.Call) else None
        func = call.func if call is not None else dec
        if isinstance(func, ast.Attribute) and func.attr in ROUTE_DECORATORS:
            return [], []
        kw = {k.arg: k.value for k in call.keywords if k.arg} if call is not None else {}
        line = dec.lineno
        # Flask-HTTPAuth: <auth>.login_required[(role=..., optional=...)]
        if isinstance(func, ast.Attribute) and func.attr == "login_required":
            obj = self._auth_object(mod, func.value)
            if obj is not None:
                return self._auth_guard(obj, ast.unparse(dec), kw, line), []
        qual = ex.resolve(mod, func)
        lib = _lib(qual)
        if lib == "apifairy" and qual.endswith(":authenticate") and call and call.args:  # type: ignore[union-attr]
            obj = self._auth_object(mod, call.args[0])
            if obj is not None:
                return self._auth_guard(obj, f"apifairy.{ast.unparse(dec)}", kw, line), []
        if lib == "flask_login" and qual.endswith(":login_required"):  # type: ignore[union-attr]
            return [
                Guard(
                    "authenticated", "flask_login.login_required", self._login_status(), line=line
                )
            ], []
        if lib == "flask_jwt_extended" and qual.endswith(("jwt_required", "fresh_jwt_required")):  # type: ignore[union-attr]
            if _lit(kw.get("optional")) is True:
                return [], []
            return [
                Guard("authenticated", f"flask_jwt_extended.{ast.unparse(dec)}", 401, line=line)
            ], []
        if ex.in_package(qual):
            return self.package_decorator(mod, dec, qual, line)  # type: ignore[arg-type]
        return [], []

    def _auth_object(self, mod: str, expr: ast.expr) -> Any:
        try:
            obj = self.ex.evaluate(mod, expr)
        except Exception:  # noqa: BLE001
            return None
        return obj if id(obj) in self.ex.auths else None

    def _auth_guard(self, obj: Any, source: str, kw: dict[str, ast.expr], line: int) -> list[Guard]:
        if _lit(kw.get("optional")) is True:
            return []
        status, detail = self.auth_refusal(obj)
        role = _lit(kw.get("role"))
        if role is None:
            return [Guard("authenticated", source, status, detail, line=line)]
        roles = role if isinstance(role, list | tuple) else [role]
        return [Guard("role", source, 403, None, tuple(map(str, roles)), line=line)]

    def _login_status(self) -> int:
        try:
            from flask_login import LoginManager
        except ImportError:  # pragma: no cover
            return 401
        for module in self.ex.modules.values():
            for value in vars(module).values():
                if isinstance(value, LoginManager):
                    if value.login_view and value.unauthorized_callback is None:
                        return 302
        return 401

    def package_decorator(
        self, mod: str, dec: ast.expr, qual: str, line: int
    ) -> tuple[list[Guard], list[RaiseSite]]:
        ex = self.ex
        found = ex.def_of(qual)
        if found is None:
            return [], []
        dmod, node = found
        wrapper = self._wrapper(node)
        if wrapper is None:
            return [], []
        guards: list[Guard] = []
        sites: list[RaiseSite] = []
        for stmt in _walk_own(wrapper):
            if not isinstance(stmt, ast.If):
                continue
            refusal = ex.refusal(dmod, stmt.body)
            if refusal is None:
                continue
            if not ex.mentions_caller(dmod, stmt.test, set()):
                continue
            guards.append(self._probed(mod, dec, dmod, stmt, refusal, line))
        guard_lines = {
            n.lineno
            for stmt in _walk_own(wrapper)
            if isinstance(stmt, ast.If) and ex.mentions_caller(dmod, stmt.test, set())
            for n in ast.walk(stmt)
            if hasattr(n, "lineno")
        }
        for n in _walk_own(wrapper):
            s = ex.site(dmod, n)
            if s is not None and n.lineno not in guard_lines:  # type: ignore[attr-defined]
                sites.append(RaiseSite(s[0], s[1], n.lineno, s[2]))  # type: ignore[attr-defined]
        return guards, sites

    @staticmethod
    def _wrapper(node: ast.AST) -> ast.FunctionDef | None:
        """The function a decorator returns around the view: the nested definition
        calling a parameter of an enclosing one (``f(*args, **kwargs)``)."""
        params: set[str] = set()

        def visit(fn: ast.AST, params: set[str]) -> ast.FunctionDef | None:
            if not isinstance(fn, ast.FunctionDef):
                return None
            mine = params | {a.arg for a in fn.args.args}
            for sub in _walk_own(fn):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and (sub.func.id in params)
                ):
                    return fn
            for sub in ast.walk(fn):
                if sub is not fn and isinstance(sub, ast.FunctionDef):
                    found = visit(sub, mine)
                    if found is not None:
                        return found
            return None

        return visit(node, params)

    def _probed(
        self,
        mod: str,
        dec: ast.expr,
        dmod: str,
        stmt: ast.If,
        refusal: tuple[int | None, str | None, str],
        line: int,
    ) -> Guard:
        """A package decorator's guard, its roles probed (see the module docstring)."""
        ex = self.ex
        status, detail, _ = refusal
        source = ast.unparse(dec)
        test = stmt.test
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and ex.is_caller(dmod, test.operand, set())
        ) or (
            isinstance(test, ast.Compare)
            and isinstance(test.ops[0], ast.Is)
            and ex.is_caller(dmod, test.left, set())
        ):
            return Guard("authenticated", source, status, detail, line=line)
        allowed = self._allowed(mod, dec)
        if allowed is None:
            return Guard("custom", source, status, detail, line=line)
        self._grants(mod, dec)
        everyone = {c.role for c in ex.callers}
        if allowed == {"Admin"} and everyone != {"Admin"}:
            return Guard("admin", source, status, detail, ("Admin",), line=line)
        return Guard("role", source, status, detail, ex.role_order(allowed), line=line)

    def _allowed(self, mod: str, dec: ast.expr) -> set[str] | None:
        ex = self.ex
        if not ex.callers:
            return None
        try:
            deco = ex.evaluate(mod, dec)
            wrapped = deco(lambda *a, **k: _SENTINEL)
            outcome = ex.probe(wrapped)
        except Exception:  # noqa: BLE001 -- not probe-able: a custom guard
            return None
        return {role for role, refused in outcome.items() if refused is None}

    def _grants(self, mod: str, dec: ast.expr) -> None:
        """A permission factory (``permission_required(Permission.X)``): the roles holding
        each permission constant of ``X``'s class, by probing it once per constant."""
        ex = self.ex
        if self.permissions or not isinstance(dec, ast.Call) or len(dec.args) != 1:
            return
        arg = dec.args[0]
        if not isinstance(arg, ast.Attribute):
            return
        found = ex.def_of(ex.resolve(mod, arg.value))
        if found is None or not isinstance(found[1], ast.ClassDef):
            return
        cls_name = ast.unparse(arg.value)
        consts = []
        for s in found[1].body:
            if (
                isinstance(s, ast.Assign)
                and len(s.targets) == 1
                and isinstance(s.targets[0], ast.Name)
                and _int(s.value) is not None
            ):
                consts.append((s.targets[0].id, _int(s.value)))
        grants: dict[str, list[str]] = {c.role: [] for c in ex.callers}
        for const, _ in consts:
            call = ast.Call(dec.func, [ast.Attribute(arg.value, const, ast.Load())], [])
            allowed = self._allowed(mod, ast.fix_missing_locations(ast.copy_location(call, dec)))
            if allowed is None:
                return
            for role in allowed:
                grants[role].append(f"{cls_name}.{const}")
        self.permissions = {f"{cls_name}.{c}": v for c, v in consts}  # type: ignore[misc]
        self.role_permissions = grants

    def body_guards(
        self, mod: str, fn: ast.AST, callers: set[str], owner: Callable[[ast.expr], OwnerUse | None]
    ) -> list[Guard]:
        """Checks in a function body refusing callers: ``active`` checks, owner checks
        (with the roles that bypass them) and checks on the caller (``custom``)."""
        ex = self.ex
        out: list[Guard] = []
        for stmt in _walk_own(fn):
            if not isinstance(stmt, ast.If):
                continue
            refusal = ex.refusal(mod, stmt.body)
            if refusal is None:
                continue
            status, detail, _ = refusal
            operands = (
                stmt.test.values
                if (isinstance(stmt.test, ast.BoolOp) and isinstance(stmt.test.op, ast.And))
                else [stmt.test]
            )
            owners = [o for o in operands if owner(o) is not None]
            if owners:
                rest = [o for o in operands if o not in owners]
                roles = self._bypass(mod, rest)
                out.append(
                    Guard(
                        "object_owner",
                        ast.unparse(owners[0]),
                        status,
                        detail,
                        roles,
                        line=stmt.lineno,
                    )
                )
                continue
            active = next(
                (
                    o
                    for o in operands
                    if isinstance(o, ast.UnaryOp)
                    and isinstance(o.op, ast.Not)
                    and isinstance(o.operand, ast.Attribute)
                    and o.operand.attr == ex.active_column
                    and ex.is_caller(mod, o.operand.value, callers)
                ),
                None,
            )
            if active is not None:
                out.append(Guard("active", ast.unparse(active), status, detail, line=stmt.lineno))
        return out

    def _bypass(self, mod: str, rest: list[ast.expr]) -> tuple[str, ...]:
        """The roles for which every other operand of an owner check is false (the
        check cannot refuse them): each operand evaluated as that role."""
        ex = self.ex
        if not rest or not ex.callers:
            return ()
        allowed = set()
        for caller in ex.callers:
            try:
                with ex.as_caller(caller):
                    values = [ex.evaluate(mod, o) for o in rest]
            except Exception:  # noqa: BLE001 -- not evaluable: no bypass claimed
                return ()
            if not all(values):
                allowed.add(caller.role)
        return ex.role_order(allowed)


# ---- routes ----------------------------------------------------------------------------------


class _RouteReader:
    def __init__(self, ex: _Extractor, guards: _Guards) -> None:
        self.ex = ex
        self.guards = guards
        self.excluded: list[ExcludedRoute] = []

    def routes(self) -> list[Route]:
        ex = self.ex
        app = ex.app
        out: list[Route] = []
        framework_endpoints = set()
        rules = list(app.url_map.iter_rules())
        for rule in rules:
            view = app.view_functions.get(rule.endpoint)
            if not ex.in_package(f"{getattr(inspect.unwrap(view), '__module__', '')}:x"):
                framework_endpoints.add(rule.endpoint)
        for rule in rules:
            path = flask_path(rule.rule)
            methods = [m for m in HTTP_METHODS if m in (rule.methods or ())]
            view = app.view_functions.get(rule.endpoint)
            if rule.endpoint in framework_endpoints:
                for m in methods:
                    self.excluded.append(
                        ExcludedRoute(m, path, "framework", f"the framework's {rule.endpoint}")
                    )
                continue
            for method in methods:
                found = self._handler(view, method)
                if found is None:
                    continue
                mod, fn, qualname, decorators = found
                target = self._redirect_only(mod, fn)
                if target is not None and target in framework_endpoints:
                    self.excluded.append(
                        ExcludedRoute(
                            method, path, "framework", f"redirects to the framework's {target}"
                        )
                    )
                    continue
                route = self.route(rule, method, path, view, mod, fn, qualname, decorators)
                reason = self._unreachable(mod, fn)
                if reason is not None:
                    self.excluded.append(ExcludedRoute(method, path, "unreachable", reason))
                out.append(route)
        return out

    def _handler(self, view: Any, method: str) -> tuple[str, Any, str, list[ast.expr]] | None:
        """(module, function AST, qualified name, decorators outermost first)."""
        ex = self.ex
        cls = getattr(view, "view_class", None)
        if cls is not None:  # MethodView / Flask-RESTful Resource
            meth = getattr(cls, method.lower(), None)
            if meth is None:
                return None
            found = ex.function_node(meth)
            if found is None:
                return None
            mod, fn = found
            cls_node = ex.def_of(f"{cls.__module__}:{cls.__name__}")
            class_decs: list[ast.expr] = []
            if cls_node is not None and isinstance(cls_node[1], ast.ClassDef):
                for s in cls_node[1].body:
                    if (
                        isinstance(s, ast.Assign)
                        and any(
                            isinstance(t, ast.Name) and t.id in ("decorators", "method_decorators")
                            for t in s.targets
                        )
                        and isinstance(s.value, ast.List | ast.Tuple)
                    ):
                        class_decs = list(reversed(s.value.elts))
            return mod, fn, f"{cls.__name__}.{fn.name}", [*class_decs, *fn.decorator_list]
        found = ex.function_node(view)
        if found is None:
            return None
        mod, fn = found
        return mod, fn, fn.name, list(fn.decorator_list)

    def _redirect_only(self, mod: str, fn: ast.AST) -> str | None:
        body = _body(fn)
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            return None
        v = body[0].value
        if not (isinstance(v, ast.Call) and _name_of(v.func) == "redirect" and v.args):
            return None
        inner = v.args[0]
        if isinstance(inner, ast.Call) and _name_of(inner.func) == "url_for" and inner.args:
            return _str(inner.args[0])
        return None

    def _unreachable(self, mod: str, fn: ast.AST) -> str | None:
        """Why probes cannot exercise a route: it calls the network (directly or through
        the package functions it calls), or answers a redirect to an outside service."""
        ex = self.ex
        seen: set[int] = set()
        todo = [(mod, fn)]
        while todo:
            m, node = todo.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            for sub in _walk_own(node):
                if isinstance(sub, ast.Call):
                    qual = ex.resolve(m, sub.func) or ""
                    if any(
                        qual == n or qual.startswith((f"{n}:", f"{n}.")) for n in NETWORK_MODULES
                    ):
                        return f"calls the network ({qual.replace(':', '.')})"
                    found = ex.def_of(qual)
                    if found is not None and len(seen) < 20:
                        todo.append(found)
        for sub in _walk_own(fn):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Tuple):
                elts = sub.value.elts
                if (
                    len(elts) >= 3
                    and _int(elts[1]) in (301, 302, 303, 307)
                    and isinstance(elts[2], ast.Dict)
                    and any(_str(k) == "Location" for k in elts[2].keys)
                ):
                    return "redirects to an outside service (the network)"
        return None

    def route(
        self,
        rule: Any,
        method: str,
        path: str,
        view: Any,
        mod: str,
        fn: Any,
        qualname: str,
        decorators: list[ast.expr],
    ) -> Route:
        ex = self.ex
        locals_ = _local_names(fn)
        callers, row_vars = self._locals(mod, fn, locals_)

        def owner(e: ast.expr) -> OwnerUse | None:
            return self._owner_compare(mod, e, callers, row_vars, locals_)

        guards: list[Guard] = []
        raises: list[RaiseSite] = []
        for bfn in self._before_requests(rule.endpoint):
            found = ex.function_node(bfn)
            if found is None:
                continue
            bmod, bnode = found
            for dec in bnode.decorator_list:
                g, _ = self.guards.decorator(bmod, dec)
                guards += g
            guards += self.guards.body_guards(bmod, bnode, set(), lambda e: None)
        deco_sites: list[RaiseSite] = []
        for dec in decorators:
            g, s = self.guards.decorator(mod, dec)
            guards += g
            deco_sites += s
        guards += self.guards.body_guards(mod, fn, callers, owner)

        for node in _walk_own(fn):
            s = ex.site(mod, node)
            if s is not None:
                raises.append(RaiseSite(s[0], s[1], node.lineno, s[2]))  # type: ignore[attr-defined]
        raises.sort(key=lambda r: r.line)
        raises += self._called_sites(mod, fn)
        raises += deco_sites

        owner_uses = self._owner_uses(mod, fn, callers, row_vars, locals_)
        calls = sorted(
            {
                q
                for sub in _walk_own(fn)
                if isinstance(sub, ast.Call)
                if ex.def_of(q := ex.resolve(mod, sub.func) or "") is not None
            }
        )
        spec = getattr(view, "_spec", None) or {}
        params = list(_path_params(rule.rule)) + self._query_params(mod, fn, spec)
        body_models: tuple[str, ...] = ()
        if spec.get("body"):
            schema = spec["body"][0]
            body_models = (type(schema).__name__,)
            params.append(RouteParam("body", body_models[0], body_models[0], "body"))
        elif self._reads_json(mod, fn):
            params.append(RouteParam("body", "json", None, "body"))
        response = spec.get("response")
        status = spec.get("status_code") if response is not None else None
        if status is None:
            status = self._return_status(fn)
        returns = None
        if response is not None:
            model = getattr(getattr(response, "Meta", None), "model", None)
            returns = ex.orm.model_of(model) if model is not None else None
        auth, reason = self._auth(guards)
        tables = sorted(
            {
                n
                for sub in ast.walk(fn)
                if isinstance(sub, ast.Name | ast.Attribute)
                if (n := ex.table_class(mod, sub, locals_)) is not None
            }
            | {u.table for u in owner_uses}
            | ({returns} if returns else set())
        )
        return Route(
            name=fn.name,
            module=mod,
            method=method,
            path=path,
            params=tuple(params),
            response_model=type(response).__name__ if response is not None else None,
            response_class=None,
            status_code=status if status not in (None, 200) else None,
            dependencies=(),
            auth=auth,
            auth_reason=reason,
            raises=tuple(raises),
            calls=tuple(calls),
            tables=tuple(tables),
            owner_uses=tuple(owner_uses),
            body_models=body_models,
            line=fn.lineno,
            enclosing=self._enclosing(mod, fn),
            returns=returns,
            guards=tuple(guards),
            view=f"{mod}:{qualname}",
        )

    def _enclosing(self, mod: str, fn: ast.AST) -> str | None:
        for name, node in self.ex.index.modules[mod].defs.items():
            if node is fn:
                return None
            if isinstance(node, ast.FunctionDef | ast.ClassDef) and any(
                sub is fn for sub in ast.walk(node)
            ):
                return name
        return None

    def _before_requests(self, endpoint: str) -> list[Any]:
        funcs = self.ex.app.before_request_funcs
        names: list[str | None] = [None]
        parts = endpoint.split(".")[:-1]
        names += [".".join(parts[: i + 1]) for i in range(len(parts))]
        return [f for n in names for f in funcs.get(n, ())]

    def _auth(self, guards: list[Guard]) -> tuple[str, str]:
        ex = self.ex
        checks = [g for g in guards if g.kind != "object_owner"]
        if not checks:
            return "Public", "no guard"
        reason = "; ".join(g.source for g in checks)
        if any(g.kind == "custom" for g in checks):
            return "unclear", reason
        if any(g.kind == "admin" for g in checks):
            return "Admin", reason
        everyone = {c.role for c in ex.callers}
        allowed = set(everyone)
        role_guards = [g for g in checks if g.kind == "role"]
        for g in role_guards:
            allowed &= set(g.roles)
        base = "Authenticated" if ex.role_scheme is not None else "Customer"
        if not role_guards or allowed == everyone:
            return base, reason
        if not allowed:
            return "unclear", reason + "; lets no role through"
        if allowed == {"Admin"}:
            return "Admin", reason
        return f"Roles({', '.join(sorted(allowed))})", reason

    # -- locals: callers and rows --

    def _locals(self, mod: str, fn: ast.AST, locals_: set[str]) -> tuple[set[str], dict[str, str]]:
        ex = self.ex
        callers: set[str] = set()
        rows: dict[str, str] = {}
        for node in _walk_own(fn):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            name = node.targets[0].id
            if ex.is_caller(mod, node.value, callers):
                callers.add(name)
                continue
            table = self._row_of(mod, node.value, locals_)
            if table is not None:
                rows[name] = table
        return callers, rows

    def _row_of(self, mod: str, e: ast.expr, locals_: set[str]) -> str | None:
        """The table whose row an expression gives: ``T(...)``, ``T.query.get_or_404()``,
        ``T.from_json(...)``, ``db.session.get(T, id)``, ``x or abort(404)``."""
        ex = self.ex
        if isinstance(e, ast.BoolOp):
            return self._row_of(mod, e.values[0], locals_)
        if not isinstance(e, ast.Call):
            return None
        if isinstance(e.func, ast.Attribute) and e.func.attr in ("get", "get_or_404") and e.args:
            table = ex.table_class(mod, e.args[0], locals_)
            if table is not None:
                return table
        root = e.func
        while isinstance(root, ast.Attribute | ast.Call):
            if isinstance(root, ast.Attribute) and root.attr in ("select", "paginate", "all"):
                return None
            root = root.value if isinstance(root, ast.Attribute) else root.func
        return ex.table_class(mod, root, locals_)

    # -- owner evidence --

    def _owner_column(self, table: str, attr: str) -> str | None:
        """The owner column ``table.attr`` names: a relationship to the users table (its
        foreign key) or a column with a foreign key to it."""
        ex = self.ex
        rel = ex.orm.relationships.get(table, {}).get(attr)
        if rel is not None:
            if rel.target == ex.users and rel.direction == "MANYTOONE" and rel.table == table:
                return rel.column
            return None
        users_table = next((t.table_name for t in ex.orm.tables if t.name == ex.users), None)
        model = next((t for t in ex.orm.tables if t.name == table), None)
        col = next((c for c in model.columns if c.name == attr), None) if model else None
        if (
            col is not None
            and col.foreign_key
            and users_table
            and (col.foreign_key.split(".")[0] == users_table)
        ):
            return col.name
        return None

    def _owner_compare(
        self, mod: str, e: ast.expr, callers: set[str], rows: dict[str, str], locals_: set[str]
    ) -> OwnerUse | None:
        ex = self.ex
        if not isinstance(e, ast.Compare) or len(e.ops) != 1:
            return None
        if not isinstance(e.ops[0], ast.Eq | ast.NotEq | ast.Is | ast.IsNot):
            return None
        for a, b in ((e.left, e.comparators[0]), (e.comparators[0], e.left)):
            if isinstance(a, ast.Attribute) and ex.is_caller_id(mod, b, callers):
                table = None
                if isinstance(a.value, ast.Name) and a.value.id in rows:
                    table = rows[a.value.id]
                elif isinstance(a.value, ast.Name | ast.Attribute):
                    table = ex.table_class(mod, a.value, locals_)
                if table is None:
                    continue
                col = self._owner_column(table, a.attr)
                if col is not None:
                    return OwnerUse(table, col, "compare", e.lineno)
        return None

    def _owner_uses(
        self, mod: str, fn: ast.AST, callers: set[str], rows: dict[str, str], locals_: set[str]
    ) -> list[OwnerUse]:
        ex = self.ex
        out: list[OwnerUse] = []

        def add(use: OwnerUse | None) -> None:
            if use is not None and all(
                (u.table, u.column, u.how) != (use.table, use.column, use.how) for u in out
            ):
                out.append(use)

        users_rels = ex.orm.relationships.get(ex.users or "", {})
        users_pk = next(
            (
                c.name
                for t in ex.orm.tables
                if t.name == ex.users
                for c in t.columns
                if c.primary_key
            ),
            None,
        )
        for node in _walk_own(fn):
            # the caller's own row written: ``user.x = ...``, ``user.update(data)``
            written = (
                [t.value for t in node.targets if isinstance(t, ast.Attribute)]
                if isinstance(node, ast.Assign)
                else [node.func.value]
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CALLER_WRITES
                else []
            )
            if users_pk and ex.users and any(ex.is_caller(mod, w, callers) for w in written):
                add(OwnerUse(ex.users, users_pk, "compare", node.lineno))  # type: ignore[attr-defined]
            if isinstance(node, ast.Compare):
                add(self._owner_compare(mod, node, callers, rows, locals_))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if (
                        isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id in rows
                        and ex.is_caller_id(mod, node.value, callers)
                    ):
                        col = self._owner_column(rows[t.value.id], t.attr)
                        if col is not None:
                            add(OwnerUse(rows[t.value.id], col, "assign", node.lineno))
            elif isinstance(node, ast.Call):
                kw = [k for k in node.keywords if k.arg]
                table = ex.table_class(mod, node.func, locals_)
                if table is not None:  # Row(owner=caller)
                    for k in kw:
                        if ex.is_caller_id(mod, k.value, callers):
                            col = self._owner_column(table, k.arg)  # type: ignore[arg-type]
                            if col is not None:
                                add(OwnerUse(table, col, "assign", node.lineno))
                if isinstance(node.func, ast.Attribute) and node.func.attr == "filter_by":
                    root = node.func.value
                    while isinstance(root, ast.Attribute | ast.Call):
                        root = root.value if isinstance(root, ast.Attribute) else root.func
                    table = ex.table_class(mod, root, locals_)
                    for k in kw:
                        if table is not None and ex.is_caller_id(mod, k.value, callers):
                            col = self._owner_column(table, k.arg)  # type: ignore[arg-type]
                            if col is not None:
                                add(OwnerUse(table, col, "compare", node.lineno))
            elif (
                isinstance(node, ast.Attribute)
                and node.attr in users_rels
                and ex.is_caller(mod, node.value, callers)
            ):
                rel = users_rels[node.attr]
                if rel.direction in ("ONETOMANY", "MANYTOMANY"):
                    add(OwnerUse(rel.table, rel.column, "compare", node.lineno))
        return out

    # -- errors, params --

    def _called_sites(self, mod: str, fn: ast.AST) -> list[RaiseSite]:
        """Exceptions the package functions a view calls raise for an error handler."""
        ex = self.ex
        out: list[RaiseSite] = []
        for sub in _walk_own(fn):
            if not isinstance(sub, ast.Call):
                continue
            found = ex.def_of(ex.resolve(mod, sub.func))
            if found is None:
                continue
            cmod, cnode = found
            for node in _walk_own(cnode):
                s = ex.site(cmod, node)
                if s is not None and s[2] == "handler":
                    out.append(RaiseSite(s[0], s[1], node.lineno, s[2]))  # type: ignore[attr-defined]
        return out

    def _query_params(self, mod: str, fn: ast.AST, spec: dict[str, Any]) -> list[RouteParam]:
        ex = self.ex
        out: dict[str, RouteParam] = {}
        for sub in _walk_own(fn):
            if not (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and sub.args
            ):
                continue
            src = sub.func.value
            if not (
                isinstance(src, ast.Attribute)
                and src.attr == "args"
                and ex.resolve(mod, src.value) == "flask:request"
            ):
                continue
            name = _str(sub.args[0])
            if name is None:
                continue
            kw = {k.arg: k.value for k in sub.keywords if k.arg}
            conv = kw.get("type")
            ann = conv.id if isinstance(conv, ast.Name) else "str"
            type_ = {"int": "Int", "float": "Float", "str": "Str", "bool": "Bool"}.get(ann)
            default = sub.args[1] if len(sub.args) > 1 else kw.get("default")
            out[name] = RouteParam(
                name,
                ann,
                type_,
                "query",
                ast.unparse(default) if default is not None else None,
                required=False,
            )
        for schema, location in spec.get("args") or ():
            if location != "query":
                continue
            for f in _schema_fields(schema):
                out.setdefault(
                    f.name,
                    RouteParam(
                        f.name, f.annotation, f.type, "query", f.default, required=f.required
                    ),
                )
        return list(out.values())

    def _reads_json(self, mod: str, fn: ast.AST) -> bool:
        for sub in _walk_own(fn):
            if (
                isinstance(sub, ast.Attribute)
                and sub.attr in ("json", "get_json")
                and (self.ex.resolve(mod, sub.value) == "flask:request")
            ):
                return True
        return False

    @staticmethod
    def _return_status(fn: ast.AST) -> int | None:
        for sub in _walk_own(fn):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Tuple):
                elts = sub.value.elts
                if len(elts) >= 2 and _int(elts[1]) is not None and _int(elts[1]) < 300:  # type: ignore[operator]
                    return _int(elts[1])
        return None


def _local_names(fn: ast.AST) -> set[str]:
    from lattice.importer.frontends.pyindex import _local_names as names

    return names(fn)


# ---- marshmallow schemas ---------------------------------------------------------------------

MARSHMALLOW_TYPES = {
    "String": "Str",
    "Str": "Str",
    "Email": "Email",
    "Integer": "Int",
    "Int": "Int",
    "Float": "Float",
    "Decimal": "Float",
    "Number": "Float",
    "Boolean": "Bool",
    "Bool": "Bool",
    "DateTime": "DateTime",
    "AwareDateTime": "DateTime",
    "NaiveDateTime": "DateTime",
    "Date": "Date",
    "UUID": "Uuid",
    "Url": "Str",
    "URL": "Str",
    "List": "List",
    "Dict": "Dict",
}


def _schema_fields(schema: Any) -> list[FieldSpec]:
    """A marshmallow schema's input fields (not ``dump_only``) as FieldSpecs."""
    try:
        from marshmallow import missing, validate
    except ImportError:  # pragma: no cover
        return []
    declared = getattr(schema, "_declared_fields", None) or getattr(schema, "fields", {})
    out = []
    for name, f in declared.items():
        if getattr(f, "dump_only", False):
            continue
        kind = type(f).__name__
        type_ = MARSHMALLOW_TYPES.get(kind)
        if kind == "Nested":
            nested = getattr(f, "nested", None)
            type_ = nested.__name__ if isinstance(nested, type) else type(nested).__name__
        bounds: dict[str, Any] = {}
        choices: tuple[Any, ...] = ()
        for v in getattr(f, "validators", ()):
            if isinstance(v, validate.Length):
                if v.equal is not None:
                    bounds.update(min_length=v.equal, max_length=v.equal)
                else:
                    if v.min is not None:
                        bounds["min_length"] = v.min
                    if v.max is not None:
                        bounds["max_length"] = v.max
            elif isinstance(v, validate.Range):
                if v.min is not None:
                    bounds["ge" if v.min_inclusive else "gt"] = v.min
                if v.max is not None:
                    bounds["le" if v.max_inclusive else "lt"] = v.max
            elif isinstance(v, validate.OneOf):
                choices = tuple(v.choices)
            elif isinstance(v, validate.Email) and type_ == "Str":
                type_ = "Email"
        default = getattr(f, "load_default", missing)
        out.append(
            FieldSpec(
                name=getattr(f, "data_key", None) or name,
                annotation=kind,
                type=type_,
                nullable=bool(getattr(f, "allow_none", False)),
                required=bool(getattr(f, "required", False)),
                default=None if default is missing or callable(default) else repr(default),
                choices=choices,
                **bounds,
            )
        )
    return out


def _schemas(ex: _Extractor) -> list[DataModel]:
    try:
        from marshmallow import Schema
    except ImportError:  # pragma: no cover
        return []
    out = []
    for mod, module in ex.modules.items():
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Schema) and value.__module__ == mod:
                bases = tuple(
                    f"{b.__module__}:{b.__name__}"
                    for b in value.__bases__
                    if ex.in_package(f"{b.__module__}:x")
                )
                out.append(DataModel(value.__name__, mod, tuple(_schema_fields(value)), bases))
    return out


# ---- the auth model --------------------------------------------------------------------------


def _auth_model(ex: _Extractor, routes: list[Route], guards: _Guards) -> AuthModel:
    from flask_httpauth import HTTPBasicAuth, HTTPTokenAuth

    cols = ex.users_columns()
    users_cls = ex.orm.classes.get(ex.users or "")
    # the users column the Basic credentials name: the verify callback's first lookup
    username_field = "username"
    basic_token = False
    for _, node in ex._auth_callbacks("verify_password"):
        lookups = [
            k.arg
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and _name_of(sub.func) == "filter_by"
            for k in sub.keywords
            if k.arg
        ]
        if lookups:
            username_field = lookups[0]
        params = [a.arg for a in node.args.args]
        if len(params) >= 2 and any(
            isinstance(sub, ast.Compare)
            and isinstance(sub.left, ast.Name)
            and sub.left.id == params[1]
            and _str(sub.comparators[0]) == ""
            for sub in ast.walk(node)
        ):
            basic_token = True  # verify_password(email_or_token, password == '')
    # how the protected routes take a token: the auth object most routes use
    used: dict[str, int] = {}
    for r in routes:
        for g in r.guards:
            if g.kind in ("authenticated", "role"):
                used[g.source] = used.get(g.source, 0) + 1
    scheme = "bearer"
    objs = {name: obj for _, name, obj in ex.auths.values()}
    common = max(used, key=lambda s: used[s]) if used else None
    obj = next((o for n, o in objs.items() if common and re.search(rf"\b{n}\b", common)), None)
    if isinstance(obj, HTTPTokenAuth):
        scheme = (obj.scheme or "Bearer").lower()
    elif isinstance(obj, HTTPBasicAuth):
        scheme = "basic_token" if basic_token else "basic"
    # the login route: a POST guarded by HTTP Basic answering a token
    basic_names = [n for n, o in objs.items() if isinstance(o, HTTPBasicAuth)]
    login = None
    for r in routes:
        if r.method != "POST" or not any(
            re.search(rf"\b{n}\b", g.source) for g in r.guards for n in basic_names
        ):
            continue
        if "token" in r.path.lower() or "login" in r.path.lower():
            login = r
            break
    token_path: tuple[str, ...] = ("access_token",)
    if login is not None:
        key = _token_key(ex, login)
        if key is not None:
            token_path = (key,)
    hasher = None
    if users_cls is not None:
        m = ex.index.modules.get(users_cls.__module__)
        for local, qual in m.imports.items() if m else ():
            if local in HASHERS or qual.rsplit(":", 1)[-1] in HASHERS:
                hasher = qual
                break
    perms = tuple(guards.permissions.items())
    grants = (
        tuple((c.role, tuple(guards.role_permissions.get(c.role, ()))) for c in ex.callers)
        if guards.permissions
        else ()
    )
    return AuthModel(
        login_path=login.path if login is not None else None,
        login_encoding="basic" if login is not None else "form",
        username_field=username_field,
        password_field="password",
        token_path=token_path,
        scheme=scheme,
        users_table=ex.users,
        email_column=next((c for c in cols if "email" in c.lower()), None),
        password_column=next((c for c in cols if "password" in c.lower()), None),
        hasher=hasher,
        active_column=ex.active_column,
        admin_column=ex.admin_column,
        role_column=ex.role_column,
        role_table=ex.role_table,
        permissions=perms,
        role_permissions=grants,
    )


def _token_key(ex: _Extractor, route: Route) -> str | None:
    """The key holding the token in the login route's answer: a dict literal it returns
    (through ``jsonify`` or a package helper)."""
    found = ex.def_of(route.view)
    if found is None:
        return None
    todo = [found]
    for _ in range(3):
        nxt = []
        for mod, fn in todo:
            for sub in _walk_own(fn):
                if isinstance(sub, ast.Dict):
                    keys = [_str(k) for k in sub.keys]
                    tokens = [k for k in keys if k and k.lower().endswith("token")]
                    if tokens:
                        return tokens[0]
                if isinstance(sub, ast.Call):
                    callee = ex.def_of(ex.resolve(mod, sub.func))
                    if callee is not None:
                        nxt.append(callee)
        todo = nxt
    return None


# ---- the entry points ------------------------------------------------------------------------


def extract(repo_root: Path, app_package: str, app_factory: str | None = None) -> RepoModel:
    """Analyse the Flask app ``app_package`` (built once, see the module docstring)."""
    with built_app(app_package, app_factory) as (app, db):
        ex = _Extractor(Path(repo_root), app_package, app, db)
        try:
            ex.setup_roles()
            guards = _Guards(ex)
            reader = _RouteReader(ex, guards)
            routes = reader.routes()
            auth = _auth_model(ex, routes, guards)
            scheme = ex.role_scheme
            if scheme is not None:
                refusals = {
                    (g.status, g.detail)
                    for r in routes
                    for g in r.guards
                    if g.kind in ("role", "admin")
                }
                if len(refusals) == 1:
                    status, detail = refusals.pop()
                    scheme = dataclasses.replace(scheme, status=status, detail=detail)
            uri = app.config.get("SQLALCHEMY_DATABASE_URI") or app.config.get(
                "ALCHEMICAL_DATABASE_URL"
            )
            models = _schemas(ex)
        finally:
            ex.close()
    return RepoModel(
        app_package=app_package,
        tables=ex.orm.tables,
        models=tuple(models),
        dependencies=(),
        routes=tuple(routes),
        natives=(),
        database_url=None if uri is None else "sqlite:///<db_path>",
        dialect="sqlite" if uri is not None else None,
        exception_handlers=tuple(ex.errors.handlers),
        custom_serialization=(),
        role_scheme=scheme,
        framework="flask",
        orm="sqlalchemy",
        auth=auth,
        constraints=ex.orm.constraints,
        excluded_routes=tuple(reader.excluded),
    )


def auth_driver(auth: AuthModel, credentials: Mapping[str, tuple[str, str]]) -> AuthDriver:
    """An ``AuthDriver`` logging in as the AuthModel says, with ``credentials`` (handle ->
    (the ``username_field`` value, password))."""
    from lattice.importer.transport import AuthDriver

    if auth.login_path is None:
        return AuthDriver(scheme=auth.scheme)
    if auth.login_encoding == "basic":
        return AuthDriver.basic_route(
            auth.login_path, credentials, token_path=auth.token_path, scheme=auth.scheme
        )
    if auth.login_encoding == "json":
        return AuthDriver.json(
            auth.login_path,
            credentials,
            username_field=auth.username_field,
            password_field=auth.password_field,
            token_path=auth.token_path,
            scheme=auth.scheme,
        )
    return AuthDriver.form(
        auth.login_path,
        credentials,
        username_field=auth.username_field,
        password_field=auth.password_field,
        token_field=".".join(auth.token_path),
        scheme=auth.scheme,
    )


def _is_flask_route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in ROUTE_DECORATORS
        for d in fn.decorator_list
    )


def _is_sa_table(node: ast.ClassDef) -> bool:
    return any(
        isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in s.targets)
        for s in node.body
    ) or any(ast.unparse(b) in ("db.Model", "Model", "Base") for b in node.bases)


class FlaskFrontEnd:
    """The Flask + SQLAlchemy front end (``lattice.importer.frontends.FrontEnd``)."""

    name = "flask"

    @property
    def graph_hints(self) -> GraphHints:
        from lattice.importer.graph import GraphHints

        return GraphHints(
            is_route=_is_flask_route,
            is_table=_is_sa_table,
            is_depends=lambda node: False,
            overrides_attr=None,
        )

    def detect(self, root: Path, package: str) -> bool:
        """Some module of the package imports from ``flask``."""
        index = _Index(Path(root), package)
        return any(
            q.split(":", 1)[0].split(".", 1)[0] == self.name
            for m in index.modules.values()
            for q in m.imports.values()
        )

    def extract(self, root: Path, package: str, app_factory: str | None = None) -> RepoModel:
        return extract(root, package, app_factory)

    def runtime(
        self,
        root: Path,
        package: str,
        *,
        model: RepoModel | None = None,
        credentials: Mapping[str, tuple[str, str]] | None = None,
        app_factory: str | None = None,
    ) -> AppRuntime:
        """WSGI, the package's dispose (else every engine), plain-SQL seeding, no OpenAPI,
        Flask's error sites; logged in through :func:`auth_driver` when ``model`` and
        ``credentials`` are given (else only the token scheme is set)."""
        from lattice.importer.transport import AppRuntime, AuthDriver, WsgiTransport

        _, dispose = find_factory(package, app_factory)
        sites = error_sites(_Index(Path(root), package))
        auth = AuthDriver()
        if model is not None and model.auth is not None:
            auth = (
                auth_driver(model.auth, credentials)
                if credentials is not None
                else AuthDriver(scheme=model.auth.scheme)
            )
        return AppRuntime(
            framework=self.name,
            transport=WsgiTransport,
            auth=auth,
            dispose=(dispose or dispose_engines,),
            seed_hook=None,
            error_site=make_error_site(sites),
            error_class=make_error_class(sites),
            openapi=None,
        )


front_ends()  # the built-in (FastAPI) front end stays first
FRONT_END = register(FlaskFrontEnd())
