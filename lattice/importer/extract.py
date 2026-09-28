"""Extract: a FastAPI + SQLModel repository -> ``RepoModel`` (spec 2026-09-27 importer §3.1).

Deterministic AST analysis of the app package; nothing in the repo is imported or run.
It finds:

* SQLModel table models (columns, types, nullability, ``Field`` bounds, unique, primary
  and foreign keys) and the other pydantic models (request bodies, response models);
* the dependency functions and how they classify: the DB session, the bearer-token
  scheme, the current-user loader (with its token-decoding step and active check), the
  superuser check -- and ``unknown`` for anything else, which makes a route's auth
  ``unclear`` (never Public);
* route functions: method, full path (router prefixes included), parameters, response
  model, dependencies, auth, ``raise HTTPException`` sites, the tables they touch and
  the places they compare or assign a column to the caller's id (owner evidence);
* native candidates: the non-ORM package functions routes call (directly or through ORM
  helpers such as ``crud``), typed from annotations and call sites, plus the token
  decoder synthesised from the current-user dependency;
* the database URL / dialect (from the package's URL literals, e.g. a settings default,
  when the engine's URL is not a constant), custom exception handlers and custom
  serialisation.

Beyond the template's shapes: naive / aware datetimes are ``DateTime``; ``Enum`` and
``Literal`` annotations are ``Str`` / ``Int`` with their ``choices``; an unannotated
generator yielding a ``Session`` is the session dependency and one wrapping it in a
package class (``return NoteCRUD(session)``) a ``repository`` whose methods are followed;
the users table is what the current-user dependency returns, read from its return
values when it has no annotation; the token decode may be PyJWT's or python-jose's, in
the dependency or a helper; ``Request``-like parameters are ``framework`` parameters, and
routes an app factory defines on its own local app are found (``enclosing``).

``RepoModel`` is a frozen dataclass tree of plain values; ``to_json()`` serialises it.
"""

from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path

# ---- the model ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """A model field or table column. ``type`` is a Lattice-style type name (Str, Email,
    Int, Float, Bool, Date, DateTime, Uuid; List/Tuple/Dict; a model name) or None when
    the annotation has no such reading; ``item`` is a List's element type."""

    name: str
    annotation: str
    type: str | None
    item: str | None = None
    nullable: bool = False
    required: bool = True
    default: str | None = None  # source text of the default (or default_factory)
    min_length: int | None = None
    max_length: int | None = None
    ge: int | float | None = None
    gt: int | float | None = None
    le: int | float | None = None
    lt: int | float | None = None
    unique: bool = False
    primary_key: bool = False
    foreign_key: str | None = None  # "table.column"
    index: bool = False
    tz_aware: bool = False  # sa_type=DateTime(timezone=True)
    # the allowed values of an Enum / Literal annotation (type Str or Int); () otherwise
    choices: tuple[str | int, ...] = ()
    # an Enum annotation's member names, in ``choices``' order (SQLAlchemy stores a
    # table column of an Enum type by member name); () for a Literal or an explicit
    # ``sa_type`` / ``sa_column``
    choice_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableModel:
    name: str  # class name
    table_name: str  # SQL table name
    module: str
    columns: tuple[FieldSpec, ...]
    bases: tuple[str, ...]  # package base classes (qualified "module:Name")


@dataclass(frozen=True)
class DataModel:
    """A pydantic / non-table SQLModel class of the package."""

    name: str
    module: str
    fields: tuple[FieldSpec, ...]
    bases: tuple[str, ...]


@dataclass(frozen=True)
class RaiseSite:
    status: int | None
    detail: str | None
    line: int


@dataclass(frozen=True)
class Dependency:
    """A FastAPI dependency. ``kind``: session, repository (a data-access object built
    from the session only -- ``provides`` is its class), token, current_user,
    active_user, admin or unknown (anything the extractor does not recognise)."""

    name: str
    module: str
    kind: str
    depends_on: tuple[str, ...] = ()
    returns: str | None = None
    raises: tuple[RaiseSite, ...] = ()
    active_column: str | None = None
    active_status: int | None = None
    active_detail: str | None = None
    admin_column: str | None = None
    admin_status: int | None = None
    admin_detail: str | None = None
    scheme: str | None = None  # token kind: the fastapi.security class
    reason: str = ""
    provides: str | None = None  # repository kind: the class ("module:Name") it returns


@dataclass(frozen=True)
class RouteParam:
    """``source``: path, query, body, form, dependency or framework (``Request``,
    ``Response``, ``BackgroundTasks``...: filled by FastAPI from the connection)."""

    name: str
    annotation: str
    type: str | None
    source: str
    default: str | None = None
    required: bool = True
    dependency: str | None = None
    item: str | None = None
    choices: tuple[str | int, ...] = ()  # an Enum / Literal parameter's allowed values


@dataclass(frozen=True)
class OwnerUse:
    """A route compares (``compare``) or sets (``assign``) ``table.column`` to the
    caller's id."""

    table: str
    column: str
    how: str
    line: int


@dataclass(frozen=True)
class Route:
    name: str
    module: str
    method: str
    path: str
    params: tuple[RouteParam, ...]
    response_model: str | None
    response_class: str | None
    status_code: int | None
    dependencies: tuple[str, ...]
    auth: str  # Public, Customer, Admin or unclear
    auth_reason: str
    raises: tuple[RaiseSite, ...]
    calls: tuple[str, ...]  # package functions called ("module:function")
    tables: tuple[str, ...]  # table classes the route (or its ORM helpers) references
    owner_uses: tuple[OwnerUse, ...]
    body_models: tuple[str, ...]
    line: int
    # the function the route is defined in (an app factory's local app/router), if any
    enclosing: str | None = None


@dataclass(frozen=True)
class NativeParam:
    name: str
    annotation: str
    type: str | None  # a scalar type name, or None when bound / not expressible
    bound: str | None = None  # a call-site expression the wrapper passes itself
    bound_imports: tuple[str, ...] = ()
    passed_columns: tuple[str, ...] = ()  # column names passed at call sites (x.<col>)
    keyword_only: bool = False
    positional_only: bool = False


@dataclass(frozen=True)
class NativeCandidate:
    """A native: a package function kept as Python and called from Lattice.

    ``kind`` "function" references ``origin`` (module:function); ``adapter`` "first"
    takes the first element of a tuple result (a verifier's Bool). ``kind``
    "token_decoder" is the decode step of the current-user dependency (``origin``),
    turned into ``Str -> <users key>``: ``decode_expr`` with ``token_param`` the token,
    the ``claim`` holding the key, ``catches`` the exceptions meaning a bad token."""

    name: str
    origin: str
    kind: str
    params: tuple[NativeParam, ...]
    result_annotation: str
    result_type: str | None
    nullable: bool = False
    effect: bool = False
    effect_reason: str = ""
    adapter: str | None = None
    expressible: bool = True
    reason: str = ""
    imports: tuple[str, ...] = ()
    decode_expr: str | None = None
    token_param: str | None = None
    claim: str | None = None
    catches: tuple[str, ...] = ()
    # the unique users column the claim names when it is not the key (NativeToken lookup)
    lookup: str | None = None
    # "declared": the lookup column is declared unique; "runtime": it is not (the app
    # checks it in code), so the runtime checks it per request (NativeToken unique=)
    lookup_unique: str = "declared"
    # app configuration the decode reads (``request.app.state.<path>``), as NativeContext
    # settings: (setting name, scalar type, the attribute path from the app object)
    settings: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class RepoModel:
    app_package: str
    tables: tuple[TableModel, ...]
    models: tuple[DataModel, ...]
    dependencies: tuple[Dependency, ...]
    routes: tuple[Route, ...]
    natives: tuple[NativeCandidate, ...]
    database_url: str | None
    dialect: str | None
    exception_handlers: tuple[str, ...]  # "module:line" of each custom handler
    custom_serialization: tuple[str, ...]  # descriptions of non-default serialisation

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


# ---- constants --------------------------------------------------------------------------------

SCALAR_TYPES = {
    "builtins:str": "Str",
    "builtins:int": "Int",
    "builtins:float": "Float",
    "builtins:bool": "Bool",
    "uuid:UUID": "Uuid",
    "datetime:datetime": "DateTime",
    "datetime:date": "Date",
}
SCALARS = frozenset({"Str", "Email", "Int", "Float", "Bool", "Date", "DateTime", "Uuid"})
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
TOKEN_SCHEMES = frozenset(
    {
        "OAuth2PasswordBearer",
        "OAuth2AuthorizationCodeBearer",
        "HTTPBearer",
        "APIKeyHeader",
        "APIKeyCookie",
        "APIKeyQuery",
    }
)
FORM_CLASSES = frozenset({"OAuth2PasswordRequestForm", "OAuth2PasswordRequestFormStrict"})
ADMIN_HINTS = ("superuser", "admin", "staff")
ACTIVE_HINTS = ("active", "enabled")
EFFECT_PREFIXES = (
    "send",
    "notify",
    "email",
    "mail",
    "publish",
    "emit",
    "enqueue",
    "dispatch",
    "post",
    "push",
    "write",
    "log",
    "save",
    "store",
    "upload",
    "delete",
    "remove",
    "sync",
    "trigger",
    "schedule",
)
EFFECT_CALLS = frozenset({"builtins:print", "builtins:open"})
EFFECT_MODULES = ("smtplib", "requests", "httpx", "urllib", "subprocess", "socket", "emails")
MUTATING_METHODS = frozenset({"append", "extend", "add", "update", "insert", "pop", "remove"})
USER_DEP_KINDS = frozenset({"current_user", "active_user", "admin"})
# The marker of a reason (an Unclear decision, an island) that is a gap in the language.
LANGUAGE_GAP = "language gap"
# pydantic's datetime aliases (naive / aware / past / future): all a DateTime.
DATETIME_ALIASES = ("NaiveDatetime", "AwareDatetime", "PastDatetime", "FutureDatetime")
ENUM_BASES = ("Enum", "StrEnum", "IntEnum")
# parameters FastAPI fills from the connection itself, not from the request's data
FRAMEWORK_PARAMS = (
    "Request", "Response", "BackgroundTasks", "WebSocket", "HTTPConnection", "SecurityScopes",
)  # fmt: skip
JWT_MODULES = frozenset({"jwt", "jose", "authlib", "joserfc"})  # decode(token, ...) libraries
BUILTIN_NAMES = frozenset(dir(builtins))
# SQLAlchemy database URL schemes, for the dialect when the engine's URL is not a constant
DB_URL = re.compile(r"\A(sqlite|postgres|postgresql|mysql|mariadb|mssql|oracle)(\+\w+)?://")


# ---- the package index ----------------------------------------------------------------------


@dataclass
class _Module:
    name: str
    tree: ast.Module
    imports: dict[str, str]  # local name -> "module" or "module:attr"
    defs: dict[str, ast.AST]  # top-level name -> ClassDef / FunctionDef / Assign value


class _Index:
    def __init__(self, repo_root: Path, package: str) -> None:
        self.package = package
        self.modules: dict[str, _Module] = {}
        pkg_dir = repo_root.joinpath(*package.split("."))
        if not pkg_dir.is_dir():
            raise FileNotFoundError(f"app package {package!r} not found under {repo_root}")
        paths = sorted(pkg_dir.rglob("*.py"))
        names = []
        for path in paths:
            parts = path.relative_to(repo_root).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            names.append(".".join(parts))
        known = set(names)
        for path, name in zip(paths, names, strict=True):
            tree = ast.parse(path.read_text(), filename=str(path))
            is_pkg = path.name == "__init__.py"
            self.modules[name] = _Module(
                name, tree, _imports(tree, name, is_pkg, known), _defs(tree)
            )

    # -- resolution --

    def resolve(self, mod: str, expr: ast.expr | None) -> str | None:
        """The qualified name an expression denotes: "module" or "module:Name[.attr]"."""
        if isinstance(expr, ast.Name):
            m = self.modules.get(mod)
            if m is None:
                return None
            if expr.id in m.defs:
                return f"{mod}:{expr.id}"
            if expr.id in m.imports:
                return self.chase(m.imports[expr.id])
            return f"builtins:{expr.id}"
        if isinstance(expr, ast.Attribute):
            base = self.resolve(mod, expr.value)
            if base is None or base.startswith("builtins:"):
                return None
            if ":" not in base:
                return self.chase(f"{base}:{expr.attr}")
            return f"{base}.{expr.attr}"
        return None

    def chase(self, qual: str, depth: int = 0) -> str:
        """Follow re-exports: "m:N" where m imports N from elsewhere."""
        if ":" not in qual or depth > 10:
            return qual
        mod, name = qual.split(":", 1)
        if "." in name:
            return qual
        m = self.modules.get(mod)
        if m is None:
            return qual
        if name not in m.defs and name in m.imports:
            return self.chase(m.imports[name], depth + 1)
        if name not in m.defs and f"{mod}.{name}" in self.modules:
            return f"{mod}.{name}"
        return qual

    def node(self, qual: str | None) -> tuple[str, ast.AST] | None:
        if qual is None or ":" not in qual:
            return None
        mod, name = qual.split(":", 1)
        m = self.modules.get(mod)
        if m is None or name not in m.defs:
            return None
        return mod, m.defs[name]

    def import_line(self, mod: str, name: str) -> str | None:
        """An import statement binding ``name`` as module ``mod`` binds it."""
        m = self.modules.get(mod)
        if m is None:
            return None
        if name in m.defs:
            return f"from {mod} import {name}"
        qual = m.imports.get(name)
        if qual is None:
            return None
        return _render_import(qual, name)


def _render_import(qual: str, local: str) -> str:
    alias = ""
    if ":" in qual:
        module, attr = qual.split(":", 1)
        if attr != local:
            alias = f" as {local}"
        return f"from {module} import {attr}{alias}"
    if "." in qual:
        parent, last = qual.rsplit(".", 1)
        if last != local:
            alias = f" as {local}"
        return f"from {parent} import {last}{alias}"
    if qual != local:
        alias = f" as {local}"
    return f"import {qual}{alias}"


def _imports(tree: ast.Module, mod: str, is_pkg: bool, known: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    out[top] = top
        elif isinstance(stmt, ast.ImportFrom):
            base = stmt.module or ""
            if stmt.level:
                parts = mod.split(".")
                if not is_pkg:
                    parts = parts[:-1]
                parts = parts[: len(parts) - (stmt.level - 1)] if stmt.level > 1 else parts
                base = ".".join([*parts, *([base] if base else [])])
            for alias in stmt.names:
                local = alias.asname or alias.name
                sub = f"{base}.{alias.name}"
                out[local] = sub if sub in known else f"{base}:{alias.name}"
    return out


def _defs(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out[stmt.name] = stmt
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.value is not None:
                out[stmt.target.id] = stmt.value
    return out


def _short(qual: str | None) -> str:
    if qual is None:
        return ""
    return qual.rsplit(":", 1)[-1].rsplit(".", 1)[-1]


def _ends(qual: str | None, *names: str) -> bool:
    return qual is not None and any(qual.endswith(f":{n}") for n in names)


# ---- types -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Type:
    type: str | None
    item: str | None = None
    nullable: bool = False
    choices: tuple[str | int, ...] = ()
    names: tuple[str, ...] = ()  # an Enum's member names, in ``choices``' order


class _Extractor:
    def __init__(self, repo_root: Path, package: str) -> None:
        self.ix = _Index(repo_root, package)
        self.package = package
        self.classes: dict[str, tuple[str, ast.ClassDef]] = {}  # qual -> (mod, node)
        for mod, m in self.ix.modules.items():
            for stmt in m.tree.body:
                if isinstance(stmt, ast.ClassDef):
                    self.classes[f"{mod}:{stmt.name}"] = (mod, stmt)
        self._model_memo: dict[str, bool] = {}
        self._field_memo: dict[str, dict[str, FieldSpec]] = {}
        self.tables: dict[str, TableModel] = {}  # qual -> table
        self.models: dict[str, DataModel] = {}  # qual -> non-table model
        for qual, (mod, node) in self.classes.items():
            if not self._is_model(qual):
                continue
            fields = tuple(self._fields(qual).values())
            bases = tuple(b for b in self._bases(mod, node) if b in self.classes)
            if _is_table(node):
                cols = tuple(c for c in fields if c.type != "Relationship")
                self.tables[qual] = TableModel(node.name, _table_name(node), mod, cols, bases)
            else:
                self.models[qual] = DataModel(node.name, mod, fields, bases)
        self.table_by_name = {t.name: t for t in self.tables.values()}
        self.dep_memo: dict[str, Dependency] = {}

    # -- classes and fields --

    def _bases(self, mod: str, node: ast.ClassDef) -> list[str]:
        return [q for b in node.bases if (q := self.ix.resolve(mod, b)) is not None]

    def _is_model(self, qual: str) -> bool:
        if qual in self._model_memo:
            return self._model_memo[qual]
        self._model_memo[qual] = False
        mod, node = self.classes[qual]
        result = False
        for base in self._bases(mod, node):
            if _ends(base, "SQLModel", "BaseModel") or (
                base in self.classes and self._is_model(base)
            ):
                result = True
        self._model_memo[qual] = result
        return result

    def _fields(self, qual: str) -> dict[str, FieldSpec]:
        if qual in self._field_memo:
            return self._field_memo[qual]
        mod, node = self.classes[qual]
        out: dict[str, FieldSpec] = {}
        for base in reversed(self._bases(mod, node)):
            if base in self.classes and self._is_model(base):
                out.update(self._fields(base))
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            name = stmt.target.id
            if name.startswith("_") or name == "model_config":
                continue
            if _ends(self.ix.resolve(mod, _ann_head(stmt.annotation)), "ClassVar"):
                continue
            spec = self._field(mod, name, stmt.annotation, stmt.value)
            if spec is not None:
                out[name] = spec
        self._field_memo[qual] = out
        return out

    def _field(
        self, mod: str, name: str, ann: ast.expr, value: ast.expr | None
    ) -> FieldSpec | None:
        kws: dict[str, ast.expr] = {}
        default: ast.expr | None = value
        if isinstance(value, ast.Call):
            fq = self.ix.resolve(mod, value.func)
            if _ends(fq, "Relationship"):
                return FieldSpec(name, ast.unparse(ann), "Relationship", required=False)
            if _ends(fq, "Field"):
                kws = {k.arg: k.value for k in value.keywords if k.arg}
                default = value.args[0] if value.args else kws.get("default")
        t = self.type_of(mod, ann)
        factory = kws.get("default_factory")
        required = default is None and factory is None
        if isinstance(default, ast.Constant) and default.value is Ellipsis:
            required = True
            default = None
        shown = ast.unparse(default) if default is not None else None
        if factory is not None:
            shown = f"{ast.unparse(factory)}()"
        primary = _lit(kws.get("primary_key")) is True
        nullable = t.nullable and not primary
        explicit = _lit(kws.get("nullable"))
        if isinstance(explicit, bool):
            nullable = explicit
        tz = False
        sa_type = kws.get("sa_type")
        if isinstance(sa_type, ast.Call):
            tz = any(k.arg == "timezone" and _lit(k.value) is True for k in sa_type.keywords)
        fk = _lit(kws.get("foreign_key"))
        return FieldSpec(
            name=name,
            annotation=ast.unparse(ann),
            type=t.type,
            item=t.item,
            nullable=nullable,
            required=required,
            default=shown,
            choices=t.choices,
            choice_names=t.names if not (sa_type or kws.get("sa_column")) else (),
            min_length=_num(kws.get("min_length")),
            max_length=_num(kws.get("max_length")),
            ge=_num(kws.get("ge")),
            gt=_num(kws.get("gt")),
            le=_num(kws.get("le")),
            lt=_num(kws.get("lt")),
            unique=_lit(kws.get("unique")) is True,
            primary_key=primary,
            foreign_key=fk if isinstance(fk, str) else None,
            index=_lit(kws.get("index")) is True,
            tz_aware=tz,
        )

    def type_of(self, mod: str, ann: ast.expr | None) -> _Type:
        if ann is None:
            return _Type(None)
        if isinstance(ann, ast.Constant):
            if ann.value is None:
                return _Type("None")
            if isinstance(ann.value, str):
                try:
                    parsed = ast.parse(ann.value, mode="eval").body
                except SyntaxError:
                    return _Type(None)
                return self.type_of(mod, parsed)
            return _Type(None)
        if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
            return self._union(mod, _flatten_union(ann))
        if isinstance(ann, ast.Subscript):
            head = self.ix.resolve(mod, ann.value)
            args = list(ann.slice.elts) if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            if _ends(head, "Optional"):
                inner = self.type_of(mod, args[0])
                return dataclasses.replace(inner, nullable=True)
            if _ends(head, "Literal"):
                return _choices_type([_lit(a) for a in args])
            if _ends(head, "Union"):
                return self._union(mod, args)
            if _ends(head, "Annotated"):
                return self.type_of(mod, args[0])
            if _ends(head, "list", "List", "Sequence"):
                return _Type("List", self.type_of(mod, args[0]).type)
            if _ends(head, "tuple", "Tuple"):
                return _Type("Tuple", self.type_of(mod, args[0]).type)
            if _ends(head, "dict", "Dict", "Mapping"):
                return _Type("Dict")
            if _ends(head, "Generator", "Iterator", "Iterable", "AsyncGenerator"):
                return _Type("Generator", self.type_of(mod, args[0]).type)
            return _Type(None)
        qual = self.ix.resolve(mod, ann)
        if qual is None:
            return _Type(None)
        if qual in SCALAR_TYPES:
            return _Type(SCALAR_TYPES[qual])
        if _ends(qual, "EmailStr"):
            return _Type("Email")
        if _ends(qual, *DATETIME_ALIASES):
            return _Type("DateTime")
        if qual in self.classes and self._is_enum(qual):
            return self._enum_type(qual)
        if qual in self.classes:
            return _Type(_short(qual))
        if qual == "builtins:None":
            return _Type("None")
        if _ends(qual, "Any"):
            return _Type("Any")
        return _Type(_short(qual) if not qual.startswith("builtins:") else None)

    def _union(self, mod: str, members: list[ast.expr]) -> _Type:
        types = [self.type_of(mod, m) for m in members]
        nullable = any(t.type == "None" for t in types)
        rest = [t for t in types if t.type != "None"]
        if len(rest) == 1:
            only = rest[0]
            return dataclasses.replace(only, nullable=nullable or only.nullable)
        return _Type(None, None, nullable)

    def _is_enum(self, qual: str, depth: int = 0) -> bool:
        mod, node = self.classes[qual]
        for base in self._bases(mod, node):
            if _ends(base, *ENUM_BASES):
                return True
            if base in self.classes and depth < 5 and self._is_enum(base, depth + 1):
                return True
        return False

    def _enum_type(self, qual: str) -> _Type:
        """An Enum class: its members' values (all text -> Str, all integers -> Int);
        members that are not literals make it unreadable."""
        _, node = self.classes[qual]
        values: list[object] = []
        names: list[str] = []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    values.append(_lit(stmt.value))
                    names.append(target.id)
        t = _choices_type(values)
        return dataclasses.replace(t, names=tuple(names)) if t.choices else t

    # -- constants (settings, module constants) --

    def const(self, mod: str, expr: ast.expr | None, depth: int = 0) -> object:
        if expr is None or depth > 10:
            return None
        if isinstance(expr, ast.Constant):
            return expr.value
        if isinstance(expr, ast.JoinedStr):
            parts = []
            for v in expr.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    inner = self.const(mod, v.value, depth + 1)
                    if inner is None:
                        return None
                    parts.append(str(inner))
            return "".join(parts)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add | ast.Mult):
            left = self.const(mod, expr.left, depth + 1)
            right = self.const(mod, expr.right, depth + 1)
            if left is None or right is None:
                return None
            try:
                return left + right if isinstance(expr.op, ast.Add) else left * right  # type: ignore[operator]
            except TypeError:
                return None
        if isinstance(expr, ast.Call) and _ends(self.ix.resolve(mod, expr.func), "str"):
            inner = self.const(mod, expr.args[0], depth + 1) if expr.args else None
            return None if inner is None else str(inner)
        if isinstance(expr, ast.Name):
            found = self.ix.node(self.ix.resolve(mod, expr))
            if found and isinstance(found[1], ast.expr) and not isinstance(found[1], ast.Call):
                return self.const(found[0], found[1], depth + 1)
            return None
        if isinstance(expr, ast.Attribute):
            found = self.ix.node(self.ix.resolve(mod, expr.value))
            if found and isinstance(found[1], ast.Call):
                cls = self.ix.resolve(found[0], found[1].func)
                if cls in self.classes:
                    return self._class_default(cls, expr.attr, depth)
            if found is None:
                owner = self.ix.resolve(mod, expr)
                target = self.ix.node(owner)
                if target and isinstance(target[1], ast.expr):
                    return self.const(target[0], target[1], depth + 1)
        return None

    def _class_default(self, qual: str, attr: str, depth: int) -> object:
        mod, node = self.classes[qual]
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id == attr
            ):
                return self.const(mod, stmt.value, depth + 1)
        for base in self._bases(mod, node):
            if base in self.classes:
                found = self._class_default(base, attr, depth)
                if found is not None:
                    return found
        return None

    # -- dependencies --

    def dep_target(
        self, mod: str, ann: ast.expr | None, default: ast.expr | None
    ) -> tuple[str | None, str | None] | None:
        """(dependency qual, annotated type qual) when a parameter is a dependency."""
        if isinstance(default, ast.Call) and _ends(self.ix.resolve(mod, default.func), "Depends"):
            target = self.ix.resolve(mod, default.args[0]) if default.args else None
            return target or self.ix.resolve(mod, ann), self.ix.resolve(mod, ann)
        found = self._annotated_depends(mod, ann, 0)
        return found

    def _annotated_depends(
        self, mod: str, ann: ast.expr | None, depth: int
    ) -> tuple[str | None, str | None] | None:
        if ann is None or depth > 5:
            return None
        if isinstance(ann, ast.Subscript) and _ends(self.ix.resolve(mod, ann.value), "Annotated"):
            elts = list(ann.slice.elts) if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            base = self.ix.resolve(mod, elts[0])
            for meta in elts[1:]:
                if isinstance(meta, ast.Call) and _ends(self.ix.resolve(mod, meta.func), "Depends"):
                    target = self.ix.resolve(mod, meta.args[0]) if meta.args else base
                    return target, base
            return None
        if isinstance(ann, ast.Name | ast.Attribute):
            found = self.ix.node(self.ix.resolve(mod, ann))
            if found and isinstance(found[1], ast.Subscript):
                return self._annotated_depends(found[0], found[1], depth + 1)
        return None

    def classify(self, qual: str | None) -> Dependency:
        name = _short(qual) or "?"
        if qual is None:
            return Dependency(name, "", "unknown", reason="unresolved dependency")
        if qual in self.dep_memo:
            return self.dep_memo[qual]
        self.dep_memo[qual] = Dependency(name, "", "unknown", reason="recursive dependency")
        dep = self._classify(qual, name)
        self.dep_memo[qual] = dep
        return dep

    def _classify(self, qual: str, name: str) -> Dependency:
        mod = qual.split(":", 1)[0]
        if _ends(qual, *FORM_CLASSES):
            return Dependency(name, mod, "form")
        found = self.ix.node(qual)
        if found is None:
            return Dependency(name, mod, "unknown", reason=f"{qual} is not in the package")
        mod, node = found
        if isinstance(node, ast.Call):
            cls = self.ix.resolve(mod, node.func)
            if _ends(cls, *TOKEN_SCHEMES):
                return Dependency(name, mod, "token", scheme=_short(cls))
            return Dependency(name, mod, "unknown", reason=f"instance of {_short(cls)}")
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return Dependency(name, mod, "unknown", reason="not a function")
        subs: dict[str, Dependency] = {}
        for arg, default in _args_with_defaults(node.args):
            target = self.dep_target(mod, arg.annotation, default)
            if target is not None:
                subs[arg.arg] = self.classify(target[0])
        ret = self.type_of(mod, node.returns)
        ret_qual = self.ix.resolve(mod, _ann_head(node.returns))
        depends_on = tuple(d.name for d in subs.values())
        raises = tuple(self.raise_sites(mod, node))
        base = {"name": name, "module": mod, "depends_on": depends_on, "raises": raises}
        if (
            _ends(ret_qual, "Session")
            or (ret.type == "Generator" and ret.item == "Session")
            or (ret.type == "Session")
            or (node.returns is None and self._yields_session(mod, node))
        ):
            return Dependency(**base, kind="session", returns="Session")
        kinds = {d.kind for d in subs.values()}
        if "unknown" in kinds:
            bad = ", ".join(d.name for d in subs.values() if d.kind == "unknown")
            return Dependency(**base, kind="unknown", reason=f"depends on unknown {bad}")
        provided = self._repository(mod, node, subs)
        if provided is not None:
            return Dependency(**base, kind="repository", provides=provided)
        returns = ret.type if ret.type in self.table_by_name else None
        if node.returns is None:
            returns = self._returned_table(mod, node)
        checks = self._flag_checks(mod, node)
        active = [c for c in checks if _hint(c[0]) == "active"]
        admin = [c for c in checks if _hint(c[0]) == "admin"]
        other = [c for c in checks if _hint(c[0]) is None]
        if other:
            cols = ", ".join(c[0] for c in other)
            return Dependency(
                **base, kind="unknown", returns=returns, reason=f"custom check on {cols}"
            )
        extra: dict[str, object] = {}
        if active:
            extra.update(
                active_column=active[0][0], active_status=active[0][1], active_detail=active[0][2]
            )
        if admin:
            extra.update(
                admin_column=admin[0][0], admin_status=admin[0][1], admin_detail=admin[0][2]
            )
        if "token" in kinds and returns is not None:
            return Dependency(**base, kind="current_user", returns=returns, **extra)
        if kinds & USER_DEP_KINDS and returns is not None:
            if admin:
                return Dependency(**base, kind="admin", returns=returns, **extra)
            if active:
                return Dependency(**base, kind="active_user", returns=returns, **extra)
            if _returns_param_only(node):
                return Dependency(**base, kind="current_user", returns=returns, **extra)
        return Dependency(
            **base, kind="unknown", returns=returns, reason="not a recognised auth pattern"
        )

    def _yields_session(self, mod: str, fn: ast.AST) -> bool:
        """An unannotated generator dependency yielding a ``Session(...)`` (directly, or
        a name bound to one by ``with`` / assignment)."""

        def is_session(e: ast.expr | None) -> bool:
            return isinstance(e, ast.Call) and _ends(
                self.ix.resolve(mod, e.func), "Session", "AsyncSession"
            )

        bound: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    if is_session(item.context_expr) and isinstance(item.optional_vars, ast.Name):
                        bound.add(item.optional_vars.id)
            elif isinstance(node, ast.Assign) and is_session(node.value):
                bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            if isinstance(node, ast.Yield | ast.YieldFrom):
                v = node.value
                if is_session(v) or (isinstance(v, ast.Name) and v.id in bound):
                    return True
        return False

    def _plain_class(self, qual: str | None) -> bool:
        """A package class that is not a model, table or enum (a service / repository)."""
        return qual in self.classes and not self._is_model(qual) and not self._is_enum(qual)

    def _repository(
        self, mod: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, subs: dict[str, Dependency]
    ) -> str | None:
        """A dependency that only wraps the session in a package class (``return
        NoteCRUD(session)``, or annotated to return one): the class."""
        if not subs or any(d.kind != "session" for d in subs.values()):
            return None
        annotated = self.ix.resolve(mod, _ann_head(fn.returns))
        if fn.returns is not None:
            return annotated if self._plain_class(annotated) else None
        body = [s for s in fn.body if not _is_doc(s)]
        if len(body) == 1 and isinstance(body[0], ast.Return):
            value = body[0].value
            if isinstance(value, ast.Call):
                cls = self.ix.resolve(mod, value.func)
                if self._plain_class(cls):
                    return cls
        return None

    def _receivers(
        self, mod: str, fn: ast.AST, self_class: str | None = None
    ) -> dict[str, str]:
        """Parameter name -> the package class it holds: annotated with a plain package
        class, or a repository dependency; ``self`` in a method."""
        out: dict[str, str] = {}
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            return out
        for arg, default in _args_with_defaults(fn.args):
            target = self.dep_target(mod, arg.annotation, default)
            if target is not None:
                dep = self.classify(target[0])
                if dep.kind == "repository" and dep.provides:
                    out[arg.arg] = dep.provides
                    continue
            ann = self.ix.resolve(mod, _ann_head(arg.annotation))
            if target is not None and target[1] is not None:
                ann = target[1]
            if self._plain_class(ann):
                out[arg.arg] = ann  # type: ignore[assignment]
        if self_class is not None and fn.args.args:
            out[fn.args.args[0].arg] = self_class
        return out

    def _method(
        self, cls: str, name: str, depth: int = 0
    ) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str] | None:
        """(module, method, class) of ``cls.name``, looked up through package bases."""
        if cls not in self.classes or depth > 5:
            return None
        mod, node = self.classes[cls]
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name == name:
                return mod, stmt, cls
        for base in self._bases(mod, node):
            found = self._method(base, name, depth + 1)
            if found is not None:
                return found
        return None

    def _receiver_call(
        self, call: ast.Call, receivers: dict[str, str]
    ) -> tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, str] | None:
        """``x.m(...)`` where ``x`` holds a package class: (module, method, class)."""
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in receivers
        ):
            return self._method(receivers[func.value.id], func.attr)
        return None

    def _returned_table(
        self, mod: str, fn: ast.AST, self_class: str | None = None, depth: int = 0
    ) -> str | None:
        """The table an unannotated function returns rows of, read from its ``return``
        values (a name's assignment, a query, a typed helper or repository method)."""
        if depth > 4:
            return None
        receivers = self._receivers(mod, fn, self_class)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Return) and node.value is not None):
                continue
            values: list[ast.expr] = [node.value]
            if isinstance(node.value, ast.Name):
                values = [
                    a.value
                    for a in ast.walk(fn)
                    if isinstance(a, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == node.value.id for t in a.targets)
                ]
            for value in values:
                table = self._value_table(mod, value, receivers, depth)
                if table is not None:
                    return table
        return None

    def _value_table(
        self, mod: str, expr: ast.expr, receivers: dict[str, str], depth: int
    ) -> str | None:
        table = self._expr_table(mod, expr)
        if table is not None:
            return table
        for node in ast.walk(expr):
            if not isinstance(node, ast.Call):
                continue
            method = self._receiver_call(node, receivers)
            if method is not None:
                mmod, mfn, cls = method
                if mfn.returns is not None:
                    t = self.type_of(mmod, mfn.returns)
                    return t.type if t.type in self.table_by_name else None
                return self._returned_table(mmod, mfn, cls, depth + 1)
            found = self.ix.node(self.ix.resolve(mod, node.func))
            if (
                found
                and isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef)
                and found[1].returns is None
                and not _is_route(found[1])
            ):
                return self._returned_table(found[0], found[1], None, depth + 1)
        return None

    def _flag_checks(self, mod: str, fn: ast.AST) -> list[tuple[str, int | None, str | None]]:
        """``if not <x>.<col>: raise HTTPException(status, detail)`` in ``fn`` -- also as
        an operand of ``or`` (``if user is None or not user.is_active: raise ...``)."""
        out = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            tests = [node.test]
            if isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.Or):
                tests = list(node.test.values)
            for test in tests:
                if not (
                    isinstance(test, ast.UnaryOp)
                    and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Attribute)
                    and isinstance(test.operand.value, ast.Name)
                ):
                    continue
                sites = [s for stmt in node.body for s in self.raise_sites(mod, stmt, fn)]
                if sites:
                    out.append((test.operand.attr, sites[0].status, sites[0].detail))
        return out

    def raise_sites(self, mod: str, fn: ast.AST, scope: ast.AST | None = None) -> list[RaiseSite]:
        """``raise HTTPException(...)`` in ``fn`` -- and ``raise <name>`` where ``name``
        is assigned an ``HTTPException(...)`` once in ``scope`` (default ``fn``)."""
        out = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Raise):
                continue
            call = node.exc
            if isinstance(call, ast.Name):
                call = _single_assignment(scope if scope is not None else fn, call.id)
            if not isinstance(call, ast.Call):
                continue
            if not _ends(self.ix.resolve(mod, call.func), "HTTPException"):
                continue
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            status_expr = kws.get("status_code", call.args[0] if call.args else None)
            detail_expr = kws.get("detail", call.args[1] if len(call.args) > 1 else None)
            status = _status(status_expr)
            detail = self.const(mod, detail_expr)
            out.append(RaiseSite(status, detail if isinstance(detail, str) else None, node.lineno))
        return out

    def discover_dependencies(self) -> None:
        """Classify dependencies no route uses: ``Annotated[..., Depends(f)]`` aliases and
        package functions that themselves take dependencies."""
        for mod, m in self.ix.modules.items():
            for name, value in m.defs.items():
                if isinstance(value, ast.Subscript):
                    target = self._annotated_depends(mod, value, 0)
                    if target is not None and target[0] in self._function_quals():
                        self.classify(target[0])
                elif isinstance(value, ast.FunctionDef | ast.AsyncFunctionDef):
                    if _is_route(value):
                        continue
                    if any(
                        self.dep_target(mod, a.annotation, d) is not None
                        for a, d in _args_with_defaults(value.args)
                    ):
                        self.classify(f"{mod}:{name}")

    def _function_quals(self) -> set[str]:
        return {
            f"{mod}:{name}"
            for mod, m in self.ix.modules.items()
            for name, v in m.defs.items()
            if isinstance(v, ast.FunctionDef | ast.AsyncFunctionDef | ast.Call)
        }

    # -- the app: engine, routers, handlers --

    def database(self) -> tuple[str | None, str | None]:
        """The engine's URL and dialect. When ``create_engine`` gets a URL that is not a
        constant (a settings field, a parameter), the database URL literals of the
        package decide -- when they all name one dialect (typically the settings'
        default)."""
        engine = False
        for mod, m in self.ix.modules.items():
            for node in ast.walk(m.tree):
                if isinstance(node, ast.Call) and _ends(
                    self.ix.resolve(mod, node.func), "create_engine"
                ):
                    engine = True
                    url = self.const(mod, node.args[0]) if node.args else None
                    if isinstance(url, str):
                        return url, _dialect(url)
        if not engine:
            return None, None
        urls = [
            node.value
            for m in self.ix.modules.values()
            for node in ast.walk(m.tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and DB_URL.match(node.value)
        ]
        dialects = {_dialect(u) for u in urls}
        if len(dialects) == 1:
            return urls[0], dialects.pop()
        return None, None

    def exception_handlers(self) -> tuple[str, ...]:
        out = []
        for mod, m in self.ix.modules.items():
            for node in ast.walk(m.tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("add_exception_handler", "exception_handler")
                ):
                    out.append(f"{mod}:{node.lineno}")
        return tuple(out)

    def custom_serialization(self) -> tuple[str, ...]:
        out = []
        for qual, (mod, node) in self.classes.items():
            if not self._is_model(qual):
                continue
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.FunctionDef):
                    for dec in stmt.decorator_list:
                        head = dec.func if isinstance(dec, ast.Call) else dec
                        if _ends(
                            self.ix.resolve(mod, head), "field_serializer", "model_serializer"
                        ):
                            out.append(f"{qual}: {_short(self.ix.resolve(mod, head))}")
                if isinstance(stmt, ast.Constant) and stmt.value == "json_encoders":
                    out.append(f"{qual}: json_encoders")
                if isinstance(stmt, ast.keyword) and stmt.arg == "json_encoders":
                    out.append(f"{qual}: json_encoders")
        for mod, m in self.ix.modules.items():
            for node in ast.walk(m.tree):
                if isinstance(node, ast.Call) and _ends(self.ix.resolve(mod, node.func), "FastAPI"):
                    for kw in node.keywords:
                        if kw.arg == "default_response_class":
                            out.append(f"{mod}: default_response_class")
        return tuple(out)

    def routers(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        """router qual -> (full path prefix, dependency quals along the include chain)."""
        own: dict[str, tuple[str, list[str]]] = {}
        for mod, m in self.ix.modules.items():
            for name, value in m.defs.items():
                if isinstance(value, ast.Call) and _ends(
                    self.ix.resolve(mod, value.func), "APIRouter", "FastAPI"
                ):
                    kws = {k.arg: k.value for k in value.keywords if k.arg}
                    prefix = self.const(mod, kws.get("prefix"))
                    deps = self._dep_list(mod, kws.get("dependencies"))
                    own[f"{mod}:{name}"] = (prefix if isinstance(prefix, str) else "", deps)
        parents: dict[str, tuple[str, str, list[str]]] = {}  # child -> (parent, prefix, deps)
        for mod, m in self.ix.modules.items():
            for node in ast.walk(m.tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "include_router"
                    and node.args
                ):
                    continue
                child = self.ix.resolve(mod, node.args[0])
                if child is None or child in parents:
                    continue
                parent = self.ix.resolve(mod, node.func.value) or "<app>"
                if parent not in own:
                    parent = "<app>"
                kws = {k.arg: k.value for k in node.keywords if k.arg}
                prefix = self.const(mod, kws.get("prefix"))
                parents[child] = (
                    parent,
                    prefix if isinstance(prefix, str) else "",
                    self._dep_list(mod, kws.get("dependencies")),
                )

        def chain(router: str, depth: int = 0) -> tuple[str, list[str]]:
            prefix, deps = own.get(router, ("", []))
            if router in parents and depth < 20:
                parent, inc_prefix, inc_deps = parents[router]
                p_prefix, p_deps = chain(parent, depth + 1) if parent != "<app>" else ("", [])
                return p_prefix + inc_prefix + prefix, [*p_deps, *inc_deps, *deps]
            return prefix, list(deps)

        return {r: (lambda c: (c[0], tuple(c[1])))(chain(r)) for r in own}

    def _dep_list(self, mod: str, expr: ast.expr | None) -> list[str]:
        out = []
        if isinstance(expr, ast.List | ast.Tuple):
            for elt in expr.elts:
                if isinstance(elt, ast.Call) and _ends(self.ix.resolve(mod, elt.func), "Depends"):
                    target = self.ix.resolve(mod, elt.args[0]) if elt.args else None
                    out.append(target or "<unresolved>")
        return out

    # -- routes --

    def routes(self) -> list[Route]:
        routers = self.routers()
        out = []
        for mod, m in self.ix.modules.items():
            for stmt in m.tree.body:
                if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                for dec in stmt.decorator_list:
                    route = self._route(mod, stmt, dec, routers)
                    if route is not None:
                        out.append(route)
                out += self._factory_routes(mod, stmt)
        return out

    def _factory_routes(
        self, mod: str, outer: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> list[Route]:
        """Routes an app factory defines on its own local ``FastAPI()`` / ``APIRouter()``
        (``def create_app(): app = FastAPI(); @app.get(...) def health(): ...``)."""
        local: dict[str, tuple[str, tuple[str, ...]]] = {}
        for node in ast.walk(outer):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and _ends(self.ix.resolve(mod, node.value.func), "APIRouter", "FastAPI")
            ):
                kws = {k.arg: k.value for k in node.value.keywords if k.arg}
                prefix = self.const(mod, kws.get("prefix"))
                deps = tuple(self._dep_list(mod, kws.get("dependencies")))
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        local[t.id] = (prefix if isinstance(prefix, str) else "", deps)
        out = []
        if not local:
            return out
        for node in ast.walk(outer):
            if node is outer or not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                route = self._route(mod, node, dec, {}, local=local, enclosing=outer.name)
                if route is not None:
                    out.append(route)
        return out

    def _route(
        self,
        mod: str,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        dec: ast.expr,
        routers: dict[str, tuple[str, tuple[str, ...]]],
        *,
        local: dict[str, tuple[str, tuple[str, ...]]] | None = None,
        enclosing: str | None = None,
    ) -> Route | None:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            return None
        receiver = dec.func.value
        if local is not None:
            if not (isinstance(receiver, ast.Name) and receiver.id in local):
                return None
            routers = {"<local>": local[receiver.id]}
            router: str | None = "<local>"
        else:
            router = self.ix.resolve(mod, receiver)
        if router not in routers:
            return None
        kws = {k.arg: k.value for k in dec.keywords if k.arg}
        verb = dec.func.attr
        if verb in HTTP_METHODS:
            method = verb.upper()
        elif verb == "api_route":
            methods = _lit(kws.get("methods"))
            method = methods[0].upper() if isinstance(methods, list) and methods else "GET"
        else:
            return None
        path = self.const(mod, dec.args[0] if dec.args else kws.get("path"))
        prefix, router_deps = routers[router]
        full_path = prefix + (path if isinstance(path, str) else "")
        path_names = set(re.findall(r"{(\w+)(?::[^}]*)?}", full_path))

        dep_quals: list[str] = [*router_deps, *self._dep_list(mod, kws.get("dependencies"))]
        params: list[RouteParam] = []
        caller_names: set[str] = set()
        for arg, default in _args_with_defaults(fn.args):
            params.append(self._param(mod, arg, default, path_names, dep_quals, caller_names))

        deps = [self.classify(q) for q in dep_quals]
        auth, reason = _route_auth(deps)
        response_model = self._model_name(mod, kws.get("response_model"))
        if response_model is None and "response_model" not in kws:
            response_model = self._model_name(mod, fn.returns)
        response_class = kws.get("response_class")
        status_code = _lit(kws.get("status_code"))
        calls = self._calls(mod, fn)
        tables = self._tables(mod, fn, set())
        users = self.users_table()
        if users and caller_names and _writes_caller(fn, caller_names):
            tables.add(users)
        uses = self._owner_uses(mod, fn, caller_names, tables)
        return Route(
            name=fn.name,
            module=mod,
            method=method,
            path=full_path,
            params=tuple(params),
            response_model=response_model,
            response_class=_short(self.ix.resolve(mod, response_class)) if response_class else None,
            status_code=status_code if isinstance(status_code, int) else None,
            dependencies=tuple(dict.fromkeys(d.name for d in deps)),
            auth=auth,
            auth_reason=reason,
            raises=tuple(self.raise_sites(mod, fn)),
            calls=tuple(calls),
            tables=tuple(sorted(tables)),
            owner_uses=tuple(uses),
            body_models=tuple(p.type for p in params if p.source == "body" and p.type),
            line=fn.lineno,
            enclosing=enclosing,
        )

    def _param(
        self,
        mod: str,
        arg: ast.arg,
        default: ast.expr | None,
        path_names: set[str],
        dep_quals: list[str],
        caller_names: set[str],
    ) -> RouteParam:
        ann = arg.annotation
        shown = ast.unparse(ann) if ann is not None else ""
        framework = self.ix.resolve(mod, _ann_head(ann))
        if default is None and _ends(framework, *FRAMEWORK_PARAMS):
            return RouteParam(arg.arg, shown, _short(framework), "framework")
        target = self.dep_target(mod, ann, default)
        if target is not None:
            dep_qual, base = target
            if _ends(dep_qual, *FORM_CLASSES) or _ends(base, *FORM_CLASSES):
                return RouteParam(arg.arg, shown, _short(base or dep_qual), "form")
            dep = self.classify(dep_qual)
            if dep.kind in USER_DEP_KINDS:
                caller_names.add(arg.arg)
            dep_quals.append(dep_qual or "<unresolved>")
            return RouteParam(arg.arg, shown, dep.returns, "dependency", dependency=dep.name)
        t = self.type_of(mod, ann)
        default_src = ast.unparse(default) if default is not None else None
        required = default is None
        if arg.arg in path_names:
            source = "path"
        elif t.type is not None and (
            t.type in {m.name for m in self.models.values()} or t.type in self.table_by_name
        ):
            source = "body"
        else:
            source = "query"
        return RouteParam(
            arg.arg, shown, t.type, source, default_src, required, None, t.item, t.choices
        )

    def _model_name(self, mod: str, expr: ast.expr | None) -> str | None:
        if expr is None:
            return None
        qual = self.ix.resolve(mod, expr)
        if qual in self.models or qual in self.tables:
            return _short(qual)
        return None

    def _calls(self, mod: str, fn: ast.AST) -> list[str]:
        out: list[str] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                qual = self.ix.resolve(mod, node.func)
                found = self.ix.node(qual)
                if found and isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                    if qual not in out:
                        out.append(qual)  # type: ignore[arg-type]
        return out

    def _tables(
        self, mod: str, fn: ast.AST, seen: set[str], self_class: str | None = None
    ) -> set[str]:
        out: set[str] = set()
        receivers = self._receivers(mod, fn, self_class)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name | ast.Attribute):
                qual = self.ix.resolve(mod, node)
                if qual in self.tables:
                    out.add(self.tables[qual].name)
            if isinstance(node, ast.Call):
                method = self._receiver_call(node, receivers)
                if method is not None:
                    mmod, mfn, cls = method
                    mqual = f"{cls}.{mfn.name}"
                    if mqual not in seen:
                        seen.add(mqual)
                        out |= self._tables(mmod, mfn, seen, cls)
                    continue
                qual = self.ix.resolve(mod, node.func)
                found = self.ix.node(qual)
                if (
                    found
                    and qual not in seen
                    and isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef)
                    and not _is_route(found[1])
                ):
                    seen.add(qual)  # type: ignore[arg-type]
                    out |= self._tables(found[0], found[1], seen)
        return out

    def users_table(self) -> str | None:
        for dep in self.dep_memo.values():
            if dep.kind == "current_user" and dep.returns:
                return dep.returns
        return None

    def _var_tables(self, mod: str, fn: ast.AST) -> dict[str, str]:
        out: dict[str, str] = {}
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            table = self._expr_table(mod, node.value)
            if table is not None:
                out[target.id] = table
        return out

    def _expr_table(self, mod: str, expr: ast.expr) -> str | None:
        for node in ast.walk(expr):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            qual = self.ix.resolve(mod, func)
            if qual in self.tables:
                return self.tables[qual].name
            if isinstance(func, ast.Attribute):
                base = self.ix.resolve(mod, func.value)
                if base in self.tables:
                    return self.tables[base].name
                if func.attr == "get" and node.args:
                    first = self.ix.resolve(mod, node.args[0])
                    if first in self.tables:
                        return self.tables[first].name
            if _ends(qual, "select") and node.args:
                first = self.ix.resolve(mod, node.args[0])
                if first in self.tables:
                    return self.tables[first].name
            found = self.ix.node(qual)
            if found and isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                ret = self.type_of(found[0], found[1].returns)
                if ret.type in self.table_by_name:
                    return ret.type
        return None

    def _owner_uses(
        self, mod: str, fn: ast.AST, callers: set[str], tables: set[str]
    ) -> list[OwnerUse]:
        if not callers:
            return []
        var_tables = self._var_tables(mod, fn)

        def is_caller_id(e: ast.expr) -> bool:
            return (
                isinstance(e, ast.Attribute)
                and isinstance(e.value, ast.Name)
                and e.value.id in callers
                and e.attr == "id"
            )

        def by_column(col: str) -> list[str]:
            return [
                t
                for t in sorted(tables)
                if any(c.name == col for c in self.table_by_name[t].columns)
            ]

        def col_ref(e: ast.expr) -> list[tuple[str, str]]:
            if isinstance(e, ast.Call) and _ends(self.ix.resolve(mod, e.func), "col") and e.args:
                e = e.args[0]
            if not isinstance(e, ast.Attribute):
                return []
            if isinstance(e.value, ast.Name) and e.value.id in callers:
                return []
            qual = self.ix.resolve(mod, e.value)
            if qual in self.tables:
                return [(self.tables[qual].name, e.attr)]
            if isinstance(e.value, ast.Name) and e.value.id in var_tables:
                return [(var_tables[e.value.id], e.attr)]
            return [(t, e.attr) for t in by_column(e.attr)]

        out: list[OwnerUse] = []
        seen: set[tuple[str, str, str]] = set()

        def add(table: str, col: str, how: str, line: int) -> None:
            if (table, col, how) not in seen:
                seen.add((table, col, how))
                out.append(OwnerUse(table, col, how, line))

        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                left, right = node.left, node.comparators[0]
                for mine, other in ((left, right), (right, left)):
                    if is_caller_id(mine):
                        for table, col in col_ref(other):
                            add(table, col, "compare", node.lineno)
            elif isinstance(node, ast.Call):
                target_table = self._expr_table(mod, node)
                direct = isinstance(node.func, ast.Name | ast.Attribute) and (
                    self.ix.resolve(mod, node.func) in self.tables
                    or (
                        isinstance(node.func, ast.Attribute)
                        and self.ix.resolve(mod, node.func.value) in self.tables
                    )
                )
                for kw in node.keywords:
                    if kw.arg and is_caller_id(kw.value):
                        for table in (
                            [target_table] if direct and target_table else by_column(kw.arg)
                        ):
                            add(table, kw.arg, "assign", node.lineno)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and is_caller_id(value)
                    ):
                        for table in by_column(key.value):
                            add(table, key.value, "assign", node.lineno)
            elif isinstance(node, ast.Assign) and is_caller_id(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        for table, col in col_ref(target):
                            add(table, col, "assign", node.lineno)
        return out

    # -- natives --

    def natives(self, routes: list[Route]) -> list[NativeCandidate]:
        sites: dict[str, list[tuple[str, ast.Call, ast.AST]]] = {}
        order: list[str] = []
        visited: set[str] = set()
        route_fns = []
        for route in routes:
            fn = route_function(self.ix, route)
            if fn is not None:
                route_fns.append((route.module, fn))
        queue: list[tuple[str, ast.AST, str | None]] = [(m, f, None) for m, f in route_fns]
        while queue:
            mod, fn, self_class = queue.pop(0)
            receivers = self._receivers(mod, fn, self_class)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                method = self._receiver_call(node, receivers)
                if method is not None:
                    # a repository / service method: followed (its helpers may be
                    # natives), never a native itself (it is bound to an instance)
                    mmod, mfn, cls = method
                    mqual = f"{cls}.{mfn.name}"
                    if mqual not in visited:
                        visited.add(mqual)
                        queue.append((mmod, mfn, cls))
                    continue
                qual = self.ix.resolve(mod, node.func)
                found = self.ix.node(qual)
                if not found or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                assert qual is not None
                target = found[1]
                if _is_route(target) or qual in self.dep_memo:
                    continue
                if self._is_orm(found[0], target):
                    if qual not in visited:
                        visited.add(qual)
                        queue.append((found[0], target, None))
                    continue
                if qual not in sites:
                    sites[qual] = []
                    order.append(qual)
                sites[qual].append((mod, node, fn))
        out = [self._native(q, sites[q]) for q in order]
        decoder = self._token_decoder()
        if decoder is not None:
            out.append(decoder)
        return out

    def _is_orm(self, mod: str, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for arg, _ in _args_with_defaults(fn.args):
            if arg.arg == "session" or _ends(
                self.ix.resolve(mod, _ann_head(arg.annotation)), "Session"
            ):
                return True
        for node in ast.walk(fn):
            if isinstance(node, ast.Name | ast.Attribute):
                qual = self.ix.resolve(mod, node)
                if qual in self.tables or _ends(qual, "select", "Session"):
                    return True
        return False

    def _native(self, qual: str, sites: list[tuple[str, ast.Call, ast.AST]]) -> NativeCandidate:
        found = self.ix.node(qual)
        assert found is not None
        mod, fn = found
        assert isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        name = fn.name
        problems: list[str] = []
        if fn.args.vararg or fn.args.kwarg:
            problems.append("takes *args/**kwargs")
        params: list[NativeParam] = []
        positional = [*fn.args.posonlyargs, *fn.args.args]
        for index, (arg, default) in enumerate(_args_with_defaults(fn.args)):
            kw_only = arg in fn.args.kwonlyargs
            pos_only = arg in fn.args.posonlyargs
            pos_index = positional.index(arg) if arg in positional else None
            passed = [
                (smod, a, caller)
                for smod, call, caller in sites
                if (a := _arg_at(call, arg.arg, pos_index, kw_only)) is not None
            ]
            del index
            param = self._native_param(mod, arg, default, passed, kw_only, pos_only, problems)
            if param is not None:
                params.append(param)
        ret = self.type_of(mod, fn.returns)
        result_type: str | None = None
        adapter = None
        nullable = False
        ret_shown = ast.unparse(fn.returns) if fn.returns is not None else ""
        if fn.returns is None:
            problems.append("no return annotation")
        elif ret.type == "None":
            result_type = None
        elif ret.type in SCALARS:
            result_type, nullable = ret.type, ret.nullable
        elif ret.type == "Tuple" and ret.item == "Bool":
            result_type, adapter = "Bool", "first"
        else:
            problems.append(f"result {ret_shown} is not a scalar")
        effect, why = self._effect(mod, fn, ret.type == "None")
        if effect:
            result_type, nullable, adapter = None, False, None
        return NativeCandidate(
            name=name,
            origin=qual,
            kind="function",
            params=tuple(params),
            result_annotation=ret_shown,
            result_type=result_type,
            nullable=nullable,
            effect=effect,
            effect_reason=why,
            adapter=adapter,
            expressible=not problems,
            reason="; ".join(problems),
        )

    def _native_param(
        self,
        mod: str,
        arg: ast.arg,
        default: ast.expr | None,
        passed: list[tuple[str, ast.expr, ast.AST]],
        kw_only: bool,
        pos_only: bool,
        problems: list[str],
    ) -> NativeParam | None:
        shown = ast.unparse(arg.annotation) if arg.annotation is not None else ""
        t = self.type_of(mod, arg.annotation)
        columns = tuple(dict.fromkeys(a.attr for _, a, _ in passed if isinstance(a, ast.Attribute)))
        common = {"name": arg.arg, "annotation": shown, "keyword_only": kw_only}
        common["positional_only"] = pos_only
        if t.type in SCALARS:
            return NativeParam(**common, type=t.type, passed_columns=columns)  # type: ignore[arg-type]
        if not passed and default is not None:
            return None  # a non-scalar parameter nobody passes keeps its default
        inferred = self._site_type(passed)
        if inferred is not None and t.type in (None, "Any"):
            return NativeParam(**common, type=inferred, passed_columns=columns)  # type: ignore[arg-type]
        bound = self._bound(passed)
        if bound is not None:
            expr, imports = bound
            return NativeParam(**common, type=None, bound=expr, bound_imports=imports)  # type: ignore[arg-type]
        problems.append(f"parameter {arg.arg}: {shown or 'unannotated'} is not a scalar")
        return NativeParam(**common, type=None, passed_columns=columns)  # type: ignore[arg-type]

    def _site_type(self, passed: list[tuple[str, ast.expr, ast.AST]]) -> str | None:
        types: set[str | None] = set()
        for _, arg, _caller in passed:
            if isinstance(arg, ast.Attribute):
                col_types = {
                    c.type for t in self.tables.values() for c in t.columns if c.name == arg.attr
                }
                types.add(col_types.pop() if len(col_types) == 1 else None)
            else:
                types.add(None)
        if len(types) == 1:
            only = types.pop()
            return only if only in SCALARS else None
        return None

    def _bound(
        self, passed: list[tuple[str, ast.expr, ast.AST]]
    ) -> tuple[str, tuple[str, ...]] | None:
        shown: set[str] = set()
        imports: list[str] = []
        if not passed:
            return None
        for smod, arg, caller in passed:
            expr = arg
            if isinstance(arg, ast.Name):
                assigned = _single_assignment(caller, arg.id)
                if assigned is None:
                    return None
                expr = assigned
            local = _local_names(caller)
            for node in ast.walk(expr):
                if isinstance(node, ast.Name):
                    if node.id in local:
                        return None
                    line = self.ix.import_line(smod, node.id)
                    if line is None:
                        return None
                    imports.append(line)
            shown.add(ast.unparse(expr))
        if len(shown) != 1:
            return None
        return shown.pop(), tuple(dict.fromkeys(imports))

    def _effect(
        self, mod: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, returns_none: bool
    ) -> tuple[bool, str]:
        lowered = fn.name.lower()
        for prefix in EFFECT_PREFIXES:
            if lowered == prefix or lowered.startswith(prefix + "_"):
                return True, f"name starts with {prefix!r}"
        module_names = set(self.ix.modules[mod].defs) | set(self.ix.modules[mod].imports)
        for node in ast.walk(fn):
            if isinstance(node, ast.Global | ast.Nonlocal):
                return True, "rebinds a global"
            if isinstance(node, ast.Call):
                qual = self.ix.resolve(mod, node.func)
                if qual in EFFECT_CALLS:
                    return True, f"calls {_short(qual)}"
                if qual and qual.split(":")[0].split(".")[0] in EFFECT_MODULES:
                    return True, f"calls {qual}"
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in MUTATING_METHODS
                    and isinstance(func.value, ast.Name)
                    and func.value.id in module_names
                    and func.value.id not in _local_names(fn)
                ):
                    return True, f"mutates module state ({func.value.id}.{func.attr})"
        if returns_none:
            return True, "returns None"
        return False, ""

    def _token_decoder(self) -> NativeCandidate | None:
        """The decode step of the current-user dependency: a ``jwt.decode`` (PyJWT,
        python-jose, ...) of the token parameter, in the dependency or in a package
        helper it passes the token to. Not expressible (with the reason) when the decode
        reads names a native cannot import (request or local state), or when the claim
        it yields names a users column other than the key."""
        users = self.users_table()
        if users is None:
            return None
        table = self.table_by_name[users]
        keys = [c for c in table.columns if c.primary_key]
        if len(keys) != 1:
            return None
        for dep in self.dep_memo.values():
            if dep.kind != "current_user" or not dep.module:
                continue
            found = self.ix.node(f"{dep.module}:{dep.name}")
            if found is None or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            mod, fn = found
            token_params = [
                a.arg
                for a, d in _args_with_defaults(fn.args)
                if (t := self.dep_target(mod, a.annotation, d)) is not None
                and self.classify(t[0]).kind == "token"
            ]
            if not token_params:
                continue
            token = token_params[0]
            decode = self._decode_call(mod, fn, token) or self._decode_helper(mod, fn, token)
            if decode is None:
                continue
            call, catches = decode
            call, settings = self._app_settings(mod, fn, call)
            names = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)} - {token}
            if settings:
                names.discard("ctx")  # the NativeContext the settings are read from
            names |= {c.split(".", 1)[0] for c in catches}
            imports: list[str] = []
            missing: list[str] = []
            for n in sorted(names):
                line = self.ix.import_line(mod, n)
                if line is not None:
                    imports.append(line)
                elif n not in BUILTIN_NAMES:
                    missing.append(n)
            problems = []
            if missing:
                problems.append(
                    f"the token decode reads {', '.join(missing)}, which a native cannot "
                    "import (request or local state)"
                )
            claim = _claim(fn)
            column = self._claim_column(mod, fn, claim, table)
            subject, lookup, lookup_unique = keys[0], None, "declared"
            if column is not None and column != keys[0].name:
                spec = next((c for c in table.columns if c.name == column), None)
                if spec is not None:
                    subject, lookup = spec, column  # NativeToken(lookup=column)
                    if not spec.unique:  # checked per request: two rows are a loud 500
                        lookup_unique = "runtime"
            return NativeCandidate(
                name="decode_token",
                origin=f"{mod}:{fn.name}",
                kind="token_decoder",
                params=(NativeParam(token, "str", "Str"),),
                result_annotation=f"{subject.annotation} | None",
                result_type=subject.type,
                nullable=True,
                expressible=not problems,
                reason="; ".join(problems),
                imports=tuple(imports),
                decode_expr=ast.unparse(call),
                token_param=token,
                claim=claim,
                catches=catches,
                lookup=lookup,
                lookup_unique=lookup_unique,
                settings=settings,
            )
        return None

    def _app_settings(
        self, mod: str, fn: ast.AST, call: ast.Call
    ) -> tuple[ast.Call, tuple[tuple[str, str, str], ...]]:
        """The decode call with each read of app configuration through the request
        (``request.app.state.<path>``, ``request`` a ``Request`` parameter of ``fn``)
        replaced by ``ctx[<setting>]``, and those settings: (name, scalar type, path from
        the app). The name is the path's last attribute; its type is the scalar a
        package class annotates a field of that name with (a settings class), else Str
        (checked against the app's value at start-up)."""
        assert isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        requests = {
            a.arg
            for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
            if _ends(self.ix.resolve(mod, _ann_head(a.annotation)), "Request")
        }
        found: dict[str, tuple[str, str, str]] = {}

        class Swap(ast.NodeTransformer):
            def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
                chain, cur = [], node
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                chain.reverse()
                if not (isinstance(cur, ast.Name) and cur.id in requests):
                    return self.generic_visit(node)
                if len(chain) < 3 or chain[:2] != ["app", "state"]:
                    return self.generic_visit(node)
                path = ".".join(chain[1:])
                name = chain[-1]
                if name in found and found[name][2] != path:
                    name = "_".join(chain[2:])
                found[name] = (name, outer.setting_type(chain[-1]), path)
                return ast.Subscript(ast.Name("ctx", ast.Load()), ast.Constant(name), ast.Load())

        outer = self
        swapped = Swap().visit(copy.deepcopy(call))
        return swapped, tuple(found.values())

    def setting_type(self, attr: str) -> str:
        """The scalar a package class annotates its field ``attr`` with (one reading),
        else Str."""
        types = set()
        for mod, node in self.classes.values():
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == attr
                ):
                    t = self.type_of(mod, stmt.annotation)
                    if t.type in ("Str", "Int", "Float", "Bool") and not t.nullable:
                        types.add(t.type)
        return types.pop() if len(types) == 1 else "Str"

    def _decode_call(
        self, mod: str, fn: ast.AST, token: str
    ) -> tuple[ast.Call, tuple[str, ...]] | None:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            for stmt in node.body:
                call = self._find_decode(mod, stmt, token)
                if call is not None:
                    catches: list[str] = []
                    for handler in node.handlers:
                        if handler.type is None:
                            continue
                        elts = (
                            handler.type.elts
                            if isinstance(handler.type, ast.Tuple)
                            else [handler.type]
                        )
                        catches.extend(ast.unparse(e) for e in elts)
                    return call, tuple(catches)
        call = self._find_decode(mod, fn, token)
        return (call, ()) if call is not None else None

    def _decode_helper(
        self, mod: str, fn: ast.AST, token: str
    ) -> tuple[ast.Call, tuple[str, ...]] | None:
        """``helper(token)``: a package function that decodes the parameter it gets the
        token as (and so answers the payload, or None on a bad token)."""
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            at = [
                i for i, a in enumerate(node.args) if isinstance(a, ast.Name) and a.id == token
            ]
            if not at:
                continue
            found = self.ix.node(self.ix.resolve(mod, node.func))
            if found is None or not isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            hmod, helper = found
            positional = [*helper.args.posonlyargs, *helper.args.args]
            if at[0] >= len(positional):
                continue
            if self._decode_call(hmod, helper, positional[at[0]].arg) is not None:
                return node, ()
        return None

    def _find_decode(self, mod: str, root: ast.AST, token: str) -> ast.Call | None:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            qual = self.ix.resolve(mod, node.func) or ""
            is_decode = _ends(qual, "decode") or (
                qual.endswith(".decode") and qual.split(":")[0].split(".")[0] in JWT_MODULES
            )
            if is_decode and any(isinstance(a, ast.Name) and a.id == token for a in node.args):
                return node
        return None

    def _claim_column(
        self, mod: str, fn: ast.AST, claim: str, table: TableModel
    ) -> str | None:
        """The users column the token's claim is looked up by: ``session.get(T, v)`` is
        the key; ``T.col == v`` (also inside a helper or repository method the claim is
        passed to) is ``col``. None when the claim's use is not found."""
        names: set[str] = set()
        for _ in range(4):
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and _mentions(node.value, claim, names):
                    names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
                elif (
                    isinstance(node, ast.AnnAssign)
                    and node.value is not None
                    and isinstance(node.target, ast.Name)
                    and _mentions(node.value, claim, names)
                ):
                    names.add(node.target.id)
        return self._column_of(mod, fn, names, table, None, 0, claim)

    def _column_of(
        self,
        mod: str,
        fn: ast.AST,
        names: set[str],
        table: TableModel,
        self_class: str | None,
        depth: int,
        claim: str | None = None,
    ) -> str | None:
        if depth > 3:
            return None
        key = next((c.name for c in table.columns if c.primary_key), None)
        receivers = self._receivers(mod, fn, self_class)

        def uses(e: ast.expr) -> bool:
            return _mentions(e, claim, names) if claim is not None else any(
                isinstance(n, ast.Name) and n.id in names for n in ast.walk(e)
            )

        def is_users(e: ast.expr) -> bool:
            qual = self.ix.resolve(mod, e)
            return qual in self.tables and self.tables[qual].name == table.name

        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                for a, b in ((node.left, node.comparators[0]), (node.comparators[0], node.left)):
                    if isinstance(a, ast.Attribute) and is_users(a.value) and uses(b):
                        return a.attr
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and len(node.args) == 2
                and is_users(node.args[0])
                and uses(node.args[1])
            ):
                return key
            at = [i for i, a in enumerate(node.args) if uses(a)]
            kw = [k.arg for k in node.keywords if k.arg and uses(k.value)]
            if not (at or kw):
                continue
            target = self._receiver_call(node, receivers)
            callee: tuple[str, ast.AST, str | None] | None = None
            if target is not None:
                callee = target
                offset = 1  # the method's self
            else:
                found = self.ix.node(self.ix.resolve(mod, func))
                if found and isinstance(found[1], ast.FunctionDef | ast.AsyncFunctionDef):
                    callee, offset = (found[0], found[1], None), 0
            if callee is None:
                continue
            cmod, cfn, ccls = callee
            assert isinstance(cfn, ast.FunctionDef | ast.AsyncFunctionDef)
            positional = [a.arg for a in [*cfn.args.posonlyargs, *cfn.args.args]][offset:]
            passed = {positional[i] for i in at if i < len(positional)} | set(kw)
            if passed:
                found_col = self._column_of(cmod, cfn, passed, table, ccls, depth + 1)
                if found_col is not None:
                    return found_col
        return None


# ---- helpers ---------------------------------------------------------------------------------


def _mentions(expr: ast.expr, claim: str, names: set[str]) -> bool:
    """``expr`` reads the claim (``payload["sub"]`` / ``payload.get("sub")``) or a
    name already holding it."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == claim
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == claim
        ):
            return True
    return False


def route_function(
    index: _Index, route: Route
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The route's function: top-level, or nested in its enclosing app factory."""
    m = index.modules.get(route.module)
    if m is None:
        return None
    if route.enclosing is None:
        fn = m.defs.get(route.name)
        return fn if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) else None
    outer = m.defs.get(route.enclosing)
    if outer is None:
        return None
    for node in ast.walk(outer):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == route.name
            and node.lineno == route.line
        ):
            return node
    return None


def _is_doc(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)


def _choices_type(values: list[object]) -> _Type:
    """Allowed values: all text -> Str, all integers -> Int, otherwise unreadable."""
    if values and all(isinstance(v, str) for v in values):
        return _Type("Str", choices=tuple(values))  # type: ignore[arg-type]
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return _Type("Int", choices=tuple(values))  # type: ignore[arg-type]
    return _Type(None)


def _is_table(node: ast.ClassDef) -> bool:
    return any(k.arg == "table" and _lit(k.value) is True for k in node.keywords)


def _table_name(node: ast.ClassDef) -> str:
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets
        ):
            value = _lit(stmt.value)
            if isinstance(value, str):
                return value
    return node.name.lower()


def _lit(expr: ast.expr | None) -> object:
    if expr is None:
        return None
    try:
        return ast.literal_eval(expr)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _num(expr: ast.expr | None) -> int | float | None:
    value = _lit(expr)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _status(expr: ast.expr | None) -> int | None:
    value = _lit(expr)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(expr, ast.Attribute | ast.Name):
        name = expr.attr if isinstance(expr, ast.Attribute) else expr.id
        match = re.match(r"HTTP_(\d{3})", name)
        if match:
            return int(match.group(1))
    return None


def _dialect(url: str) -> str:
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return "postgresql" if scheme == "postgres" else scheme


def _flatten_union(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
        return [*_flatten_union(expr.left), *_flatten_union(expr.right)]
    return [expr]


def _ann_head(ann: ast.expr | None) -> ast.expr | None:
    return ann.value if isinstance(ann, ast.Subscript) else ann


def _args_with_defaults(args: ast.arguments) -> list[tuple[ast.arg, ast.expr | None]]:
    positional = [*args.posonlyargs, *args.args]
    pad: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    out = list(zip(positional, [*pad, *args.defaults], strict=True))
    out += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
    return out


def _arg_at(call: ast.Call, name: str, pos: int | None, kw_only: bool) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if not kw_only and pos is not None and pos < len(call.args):
        arg = call.args[pos]
        return None if isinstance(arg, ast.Starred) else arg
    return None


def _single_assignment(fn: ast.AST, name: str) -> ast.expr | None:
    found = [
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    return found[0] if len(found) == 1 else None


def _local_names(fn: ast.AST) -> set[str]:
    out: set[str] = set()
    if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        out |= {a.arg for a, _ in _args_with_defaults(fn.args)}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
    return out


def _is_route(fn: ast.AST) -> bool:
    if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        return False
    for dec in fn.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in (*HTTP_METHODS, "api_route")
        ):
            return True
    return False


def _returns_param_only(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [
        s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return (
        len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Name)
    )


def _hint(column: str) -> str | None:
    lowered = column.lower()
    if any(h in lowered for h in ADMIN_HINTS):
        return "admin"
    if any(h in lowered for h in ACTIVE_HINTS):
        return "active"
    return None


def _route_auth(deps: list[Dependency]) -> tuple[str, str]:
    unknown = [d for d in deps if d.kind in ("unknown", "token")]
    if unknown:
        names = ", ".join(sorted({d.name for d in unknown}))
        return "unclear", f"depends on {names}, which the extractor does not recognise as auth"
    kinds = {d.kind for d in deps}
    if "admin" in kinds:
        return "Admin", "superuser dependency"
    if kinds & {"current_user", "active_user"}:
        return "Customer", "current-user dependency"
    return "Public", "no auth dependency"


def _writes_caller(fn: ast.AST, callers: set[str]) -> bool:
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("sqlmodel_update", "model_copy")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in callers
        ):
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in callers
                ):
                    return True
    return False


def _claim(fn: ast.AST) -> str:
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                return node.slice.value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
        if isinstance(node, ast.Attribute) and node.attr == "sub":
            return "sub"
    return "sub"


# ---- entry point -----------------------------------------------------------------------------


def extract(repo_root: Path, app_package: str) -> RepoModel:
    """Analyse ``app_package`` (a dotted package under ``repo_root``) into a RepoModel."""
    ex = _Extractor(Path(repo_root), app_package)
    routes = ex.routes()
    ex.discover_dependencies()
    natives = ex.natives(routes)
    url, dialect = ex.database()
    deps = [d for d in ex.dep_memo.values() if d.kind != "form"]
    return RepoModel(
        app_package=app_package,
        tables=tuple(ex.tables.values()),
        models=tuple(ex.models.values()),
        dependencies=tuple(deps),
        routes=tuple(routes),
        natives=tuple(natives),
        database_url=url,
        dialect=dialect,
        exception_handlers=ex.exception_handlers(),
        custom_serialization=ex.custom_serialization(),
    )
