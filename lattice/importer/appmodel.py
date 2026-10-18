"""The importer's app model (Phase 2 plan §2.1): what a front end reads from an app, as a
frozen dataclass tree of plain values, independent of the web framework and the ORM.

``RepoModel`` (alias ``AppModel``) holds the tables (:class:`TableModel` of
:class:`FieldSpec` columns), the other data models, the routes (:class:`Route`: method,
path, parameters, auth, error sites, owner evidence...), the natives and the database.
Front ends fill it (``lattice.importer.frontends``); the FastAPI front end is today's
extractor, so every field added for other frameworks has a default that reproduces its
output: ``framework`` / ``orm`` (``"fastapi"`` / ``"sqlmodel"``), ``auth`` (None: the
dependencies say it), ``Route.guards`` (``()``), ``Route.view`` (None) and
``RaiseSite.kind`` (``"HTTPException"``), ``constraints`` and ``excluded_routes``
(``()``). ``to_json()`` serialises it.

Fields only the Django / Flask front ends fill (``FieldSpec.write_only``,
``TableModel.unique_together`` / ``through`` / ``checks``) are marked
:data:`OMIT_DEFAULT`: ``to_dict()`` leaves them out while they hold their default, so a
FastAPI model's JSON is unchanged. What the Django front end leaves out of the model
(framework tables and routes) it reports as :class:`Excluded` items beside it."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any

# metadata of a field added for the Phase 2 front ends: ``to_dict()`` leaves it out
# while it holds its default, so a FastAPI model serialises as before (the W0 goldens)
OMIT_DEFAULT = {"omit_default": True}


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
    # a request field the app takes but never answers (DRF's ``write_only``): a
    # private-column candidate
    write_only: bool = field(default=False, metadata=OMIT_DEFAULT)
    # the text format the field validates (FORMATS: "slug", "url", "ip"): the seed and the
    # probes give it values of that format (a slug the router and the field accept)
    format: str | None = field(default=None, metadata=OMIT_DEFAULT)


@dataclass(frozen=True)
class TableModel:
    name: str  # class name
    table_name: str  # SQL table name
    module: str
    columns: tuple[FieldSpec, ...]
    bases: tuple[str, ...]  # package base classes (qualified "module:Name")
    # composite unique keys (column names), e.g. Django's unique_together
    unique_together: tuple[tuple[str, ...], ...] = field(default=(), metadata=OMIT_DEFAULT)
    # a many-to-many link table: the "Model.field" it is the through table of
    through: str | None = field(default=None, metadata=OMIT_DEFAULT)
    # CHECK constraints, as source text (for invariant mining)
    checks: tuple[str, ...] = field(default=(), metadata=OMIT_DEFAULT)


@dataclass(frozen=True)
class TableConstraint:
    """A table constraint no column's ``FieldSpec`` holds, for mining (Phase 2 plan §4):
    ``kind`` ``check`` (a ``CheckConstraint``: its SQL ``expr``, the table's ``columns``
    it names) or ``unique`` (a composite ``UniqueConstraint`` over ``columns``); ``table``
    is the table's model name (``TableModel.name``)."""

    table: str
    kind: str
    columns: tuple[str, ...]
    expr: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class DataModel:
    """A pydantic / non-table SQLModel class of the package."""

    name: str
    module: str
    fields: tuple[FieldSpec, ...]
    bases: tuple[str, ...]


@dataclass(frozen=True)
class RaiseSite:
    """An error site of a route or a dependency: the answer it gives (``status``,
    ``detail`` when literal). ``kind``: ``HTTPException`` (``raise HTTPException(...)``,
    FastAPI's), ``abort`` (Flask's ``abort(...)``), ``drf`` (a DRF exception:
    ``NotFound``, ``PermissionDenied``...), ``return`` (``return ..., 4xx``) or
    ``handler`` (an exception an error handler answers, e.g. Flask's ``errorhandler``)."""

    status: int | None
    detail: str | None
    line: int
    kind: str = "HTTPException"
    # a gRPC site (kind ``grpc_status``): the code's name (``NOT_FOUND``), ``status`` None
    code: str | None = field(default=None, metadata=OMIT_DEFAULT)


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
    # roles kind -- a dependency factory's checker (``Depends(grant(Perm.X))``) refusing
    # callers by the users table's enum column ``role_column``: the enum values it lets
    # through (probed on the original, one call per value), with its refusal
    role_column: str | None = None
    roles_allowed: tuple[str, ...] = ()
    roles_status: int | None = None
    roles_detail: str | None = None


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
    # bounds declared in the parameter's ``Query(...)`` / ``Path(...)`` (``gt``/``lt`` on
    # an int become ``ge``/``le``)
    ge: int | float | None = None
    le: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    # a path parameter looking its row up by a column that is not the key (DRF's
    # ``lookup_field = "slug"``): that column's name
    lookup: str | None = field(default=None, metadata=OMIT_DEFAULT)
    # exclusive bounds read from a protobuf validation rule (``gt`` / ``lt``)
    gt: int | float | None = field(default=None, metadata=OMIT_DEFAULT)
    lt: int | float | None = field(default=None, metadata=OMIT_DEFAULT)
    # the text format the parameter takes (FieldSpec.format: Django's ``<slug:...>``
    # path converter, a lookup column's field)
    format: str | None = field(default=None, metadata=OMIT_DEFAULT)


@dataclass(frozen=True)
class OwnerUse:
    """A route compares (``compare``) or sets (``assign``) ``table.column`` to the
    caller's id."""

    table: str
    column: str
    how: str
    line: int


@dataclass(frozen=True)
class Guard:
    """A check before a route's body refusing some callers (Django/DRF permission
    classes, Flask decorators): ``kind`` ``authenticated``, ``active`` (refuses an
    inactive user: ``AuthModel.active_column``), ``role``, ``admin``, ``object_owner`` or
    ``custom``; ``source`` the class or decorator it was read from; its refusal
    (``status``, ``detail``); the ``roles`` a ``role`` guard lets through (an
    ``object_owner`` guard: the roles that bypass it)."""

    kind: str
    source: str
    status: int | None = None
    detail: str | None = None
    roles: tuple[str, ...] = ()
    line: int = 0


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
    # the table the route returns rows of, read from its return values (None: unknown)
    returns: str | None = None
    # the route's guards (front ends without dependencies: Django/DRF, Flask); FastAPI
    # routes keep ``auth`` / ``dependencies`` and no guards
    guards: tuple[Guard, ...] = ()
    # the qualified handler ("module:function", "module:Class.method" for a class-based
    # view); None: ``module:name``
    view: str | None = None
    # why the front end's evidence of the tables the route reads or writes is incomplete
    # (its handler calls code the front end did not read); None: complete. A table is
    # set outside the API only when no route has a reason here
    unread: str | None = field(default=None, metadata=OMIT_DEFAULT)
    # the tables the route writes through ORM relationships (pivot plan 2026-10-05, A.2):
    # ``obj.rel.append(...)``, ``obj.rel = [...]``, ``obj.m2m.add(...)`` -- the link
    # table (a many-to-many's ``secondary`` / ``through``) or a one-to-many's target --
    # in its handler or the package code it calls. Table evidence: such a table is
    # never "outside the API"
    rel_tables: tuple[str, ...] = field(default=(), metadata=OMIT_DEFAULT)
    # gRPC (Spike I): an rpc is a route with ``method`` "RPC" and ``path``
    # "/<pkg.Service>/<Method>" (the wire path); ``transport`` "grpc", ``rpc`` its facts and
    # ``http_bindings`` the grpc-gateway bindings ((method, path) pairs) of the same rpc --
    # one endpoint, never a second route. HTTP routes keep the defaults, so their JSON is
    # unchanged.
    transport: str = field(default="http", metadata=OMIT_DEFAULT)
    rpc: RpcInfo | None = field(default=None, metadata=OMIT_DEFAULT)
    http_bindings: tuple[tuple[str, str], ...] = field(default=(), metadata=OMIT_DEFAULT)


@dataclass(frozen=True)
class RpcInfo:
    """What a gRPC route (``Route.transport == "grpc"``) is beyond a route: the service's
    full name, the method, the request and response message names, and the state of the
    rpc in the server -- ``implemented``, or ``unimplemented`` (in the contract, served by
    the generated Unimplemented stub: the gRPC analogue of ``spec_only``)."""

    service: str
    method: str
    input: str
    output: str
    state: str = "implemented"
    proto: str = ""  # the .proto file declaring the rpc (module-relative)


@dataclass(frozen=True)
class ExcludedRoute:
    """A route the front end found but sets apart: ``kind`` ``framework`` (the
    framework's own: docs, static files, an index redirecting to them -- not in the
    route denominator) or ``unreachable`` (one of ``RepoModel.routes`` that probes cannot
    exercise, e.g. it calls a network service), with the ``reason``."""

    method: str
    path: str
    kind: str
    reason: str


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
class RoleScheme:
    """The users table's enum role column, read from a dependency factory's checker:
    ``roles`` are (enum value, stored spelling, Lattice role name) per member, in the
    enum's order; the admin role is the member named like an admin (``ADMIN_HINTS``)."""

    column: str
    roles: tuple[tuple[str, str, str], ...]
    admin: str | None  # the Lattice name of the admin member, if any
    status: int | None  # the checkers' refusal
    detail: str | None

    def name(self, value: str) -> str:
        return next(n for v, _, n in self.roles if v == value)


@dataclass(frozen=True)
class AuthModel:
    """How callers log in, as a front end reads it: the login route (``login_path``,
    called with ``login_encoding`` ``form`` or ``json`` holding ``username_field`` /
    ``password_field``, answering the token at ``token_path``), the ``scheme`` the token
    is sent with (canonical lowercase, as ``transport.AuthDriver`` takes it: ``bearer`` /
    ``token`` / ``jwt`` -- the ``Authorization: Bearer|Token|JWT <token>`` header --,
    ``basic_token`` -- ``Authorization: Basic base64(<token>:)`` -- or ``session``: a
    cookie named ``cookie``), the password ``hasher`` and token ``minter``
    ("module:function"), and the users table (class name) with its columns. A
    ``login_encoding`` of ``basic`` sends the credentials as HTTP Basic (no body), the
    username being the users column ``username_field``. Roles kept as rows of a table
    (``role_table``, referenced by ``role_column``) hold ``permissions`` (name, value):
    ``role_permissions`` maps each role's Lattice name (``RoleScheme``) to the
    permissions it holds. None anywhere: not known.

    ``scheme`` may also be ``basic`` (``Authorization: Basic <base64 user:password>``, no
    login route). The answers the app gives to callers its auth refuses, each (status,
    detail): ``unauthenticated_answer`` no credentials, ``invalid_answer`` a bad token or
    bad credentials, ``unknown_answer`` a well-formed token naming no user (None: as
    ``invalid_answer``), ``inactive_answer`` an inactive user's valid token."""

    login_path: str | None = None
    login_encoding: str = "form"
    username_field: str = "username"
    password_field: str = "password"
    token_path: tuple[str, ...] = ("access_token",)
    scheme: str = "bearer"
    cookie: str | None = None
    hasher: str | None = None
    minter: str | None = None
    users_table: str | None = None
    email_column: str | None = None
    password_column: str | None = None
    active_column: str | None = None
    admin_column: str | None = None
    role_column: str | None = None
    role_table: str | None = None
    permissions: tuple[tuple[str, int], ...] = ()
    role_permissions: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unauthenticated_answer: tuple[int, str] | None = None
    invalid_answer: tuple[int, str] | None = None
    unknown_answer: tuple[int, str] | None = None
    inactive_answer: tuple[int, str] | None = None
    # the users model's own method setting the password column from a plain password
    # ("module:Class.method", e.g. ``User.set_password``): the seed hashes through it
    # when no ``hasher`` function is known
    password_setter: str | None = None


@dataclass(frozen=True)
class Excluded:
    """Something of the app a front end leaves out of the model, and why (``what``:
    ``table`` or ``route``): framework tables (Django's ``auth_permission``...),
    framework routes (admin, DRF's API root and ``api-auth`` pages), DRF's format-suffix
    duplicates of a route (folded into it)."""

    what: str
    name: str
    reason: str


def _plain(value: Any) -> Any:
    """``dataclasses.asdict``, leaving out the :data:`OMIT_DEFAULT` fields still at their
    default."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            v = getattr(value, f.name)
            if f.metadata.get("omit_default") and v == f.default:
                continue
            out[f.name] = _plain(v)
        return out
    if isinstance(value, tuple | list):
        return type(value)(_plain(v) for v in value)
    if isinstance(value, dict):
        return {_plain(k): _plain(v) for k, v in value.items()}
    return value


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
    role_scheme: RoleScheme | None = None  # an enum role column read by role checkers
    # the front end that read the app and its ORM (Phase 2 plan §2.1)
    framework: str = "fastapi"
    orm: str = "sqlmodel"
    auth: AuthModel | None = None  # None: the FastAPI front end's dependencies say it
    # table constraints for mining (CheckConstraint, composite unique); () for SQLModel
    constraints: tuple[TableConstraint, ...] = ()
    # framework routes and routes probes cannot reach (see ExcludedRoute)
    excluded_routes: tuple[ExcludedRoute, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return _plain(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False)


AppModel = RepoModel
