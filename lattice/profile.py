"""A profile: the environment a Lattice program is checked against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lattice.catalog import DEFAULT_FUEL, DEFAULT_MAX_VALUE_SIZE, PUBLIC_ROLE, RESERVED_ROLE_NAMES
from lattice.typesys import Scalar, Schema

# page_shape keys: the JSON fields of a Page response envelope.
PAGE_SHAPE_KEYS = frozenset({"items", "total", "limit", "offset"})
# Stored spellings of Uuid / DateTime values (lattice.api.backend implements them).
UUID_STORAGES = ("canonical", "hex32")
DATETIME_STORAGES = ("iso_z", "sql_space")
# ApiProfile.error_body: the JSON shape of every error response.
ERROR_BODIES = ("lattice", "fastapi")
# ApiProfile.datetime_wire: how DateTime values are spelled in responses.
DATETIME_WIRES = ("iso_z", "naive")
# ApiProfile.inactive_status: the statuses an inactive caller may be answered with.
INACTIVE_STATUSES = (400, 401, 403)
# ApiProfile.null_order / Profile.null_order: where null keys go when a `sort` gives no
# `nulls=` (and in `window` order). "last": after every value, in either direction;
# "smallest": null is the smallest value -- first ascending, last descending (SQLite's
# own order, so an imported SQLite app keeps its behaviour).
NULL_ORDERS = ("last", "smallest")
# ApiProfile.settings: the scalar types a setting may have (a NativeContext value).
SETTING_SCALARS = (Scalar.STR, Scalar.INT, Scalar.FLOAT, Scalar.BOOL)


def _default_page_shape() -> Mapping[str, str]:
    return {"items": "items", "limit": "limit", "offset": "offset"}


@dataclass(frozen=True)
class NativeSig:
    """A typed native function `native.<name>(args...)`: a pure scalar function
    implemented in Python and registered with the database connection.

    `nullable`: the result may be missing (null), e.g. a token decoder on a bad token.
    `reads_private`: a verifier (e.g. verify_password(plain, hash) -> Bool) whose
    arguments may be private columns: reading one through it is not an oracle (L803)
    and its result does not carry the private value.
    `effect`: an effect native (send an email, call a webhook): it has no result
    (`result` is None), is never called inside a query or an expression (L438) and runs
    only through `for_each(t, native.<name>, args...)` -- once per row, after the
    endpoint's transaction commits, from the outbox (at-least-once: it must be
    idempotent)."""

    params: tuple[Scalar, ...]
    result: Scalar | None = None
    nullable: bool = False
    reads_private: bool = False
    effect: bool = False
    # T2: the profile capability an effect native needs (`@procedure(effects=[...])`
    # names it); None means the native's own name.
    capability: str | None = None
    # App configuration the native reads (names of `ApiProfile.settings`): its
    # implementation takes a read-only NativeContext -- a mapping of exactly these
    # settings, supplied when the app starts -- as its first argument.
    settings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenColumn:
    """Auth: `Authorization: Bearer <token>` is looked up in this column of the users
    table (today's behaviour)."""

    column: str = "token"


TOKEN_UNIQUE = ("declared", "runtime")
# the statuses NativeToken.invalid / .unknown may answer
TOKEN_ANSWER_STATUSES = (400, 401, 403, 404)


@dataclass(frozen=True)
class NativeToken:
    """Auth: the bearer token is decoded by a native (a null result or a missing row
    answers 401) to the caller's users-table key (`Str -> <key scalar>`) -- or, with
    `lookup`, to the value of that users column (`Str -> <its scalar>`), e.g. a JWT
    whose subject is the username. The lookup column must be unique (a declared key of
    the users table), so the one authentication statement finds at most one row --
    unless `unique="runtime"`: then the column need not be declared unique (an app that
    enforces uniqueness in code only), and the runtime checks it on every request: no
    row answers 401, more than one a loud 500 (R311, an ambiguous caller) -- never one
    of them picked silently.

    `invalid` / `unknown`: how a token naming no caller is answered, as (status,
    detail) -- `invalid` when the native decodes it to null (a bad, expired or claimless
    token), `unknown` when the decoded value matches no users row. None: 401 with
    `ApiProfile.unauthorized_detail`, like a missing token. An imported app sets them to
    the original's answers (its current-user dependency's raise sites), so every
    endpoint -- one added later included -- answers like the original."""

    native: str
    lookup: str | None = None
    unique: str = "declared"  # "declared": a key of the users table; "runtime": checked
    invalid: tuple[int, str] | None = None
    unknown: tuple[int, str] | None = None

    def __post_init__(self) -> None:
        if self.unique not in TOKEN_UNIQUE:
            raise ValueError(f"NativeToken.unique must be one of {TOKEN_UNIQUE}")
        for name in ("invalid", "unknown"):
            answer = getattr(self, name)
            if answer is None:
                continue
            ok = (
                isinstance(answer, tuple)
                and len(answer) == 2
                and type(answer[0]) is int
                and answer[0] in TOKEN_ANSWER_STATUSES
                and isinstance(answer[1], str)
                and bool(answer[1])
            )
            if not ok:
                raise ValueError(
                    f"NativeToken.{name} must be (status, detail): a status in "
                    f"{TOKEN_ANSWER_STATUSES} and a non-empty text, or None"
                )


AuthScheme = TokenColumn | NativeToken


@dataclass(frozen=True)
class RoleColumn:
    """The caller's role is the value of this (Str) column of the users table (today's
    behaviour). ``values`` maps the column's values, as the profile reads them, to role
    names (``{"buyer": "Buyer"}``; for a column with ``one_of_stored`` spellings, the
    decoded value); a value outside the map is a role no endpoint allows but
    ``auth=Authenticated``. None: the column holds role names."""

    column: str = "role"
    values: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.values, Mapping):
            object.__setattr__(self, "values", tuple(self.values.items()))


@dataclass(frozen=True)
class AdminFlag:
    """The caller's role comes from a Bool column of the users table (e.g. the FastAPI
    template's `is_superuser`): true -> the profile's admin role, false -> `role`."""

    column: str
    role: str


@dataclass(frozen=True)
class NoRoles:
    """Single-role auth, for an app with no admin concept (no role column, no admin
    flag): every authenticated caller has `role`, no caller is ever the admin role.
    So `owned()`/`accessible()` equal `mine()`, `is_admin(user)` is false, no users
    column is read for a role, and an endpoint requiring any other role -- `auth=Admin`
    included -- could never be called: the checker refuses it (L801)."""

    role: str = "Customer"


RoleSource = RoleColumn | AdminFlag | NoRoles


@dataclass(frozen=True)
class Nested:
    """A response model field holding a list of child rows (W1-B nested responses):
    ``Nested("items", "OrderItemRead", table="OrderItem", via="order_id")`` answers, in
    field ``items`` of each returned row, the rows of table ``OrderItem`` whose ``via``
    column equals the returned row's key (the first key of the table the returned rows
    come from), each shaped by response model ``model``, ordered by the child table's
    first key (else its insertion order); no children is ``[]``. The list is read in the
    returned rows' own statement (a correlated sub-select), so a read stays ONE SQL
    statement. The checker (L835) requires the model's fields to be public columns of
    ``table``, and the child rows to be the parent's: ``table`` has no owner column of
    its own, and its ``owner_via``, when set, is ``(via, <the parent table>)``."""

    field: str
    model: str
    table: str
    via: str


ResponseField = str | Nested


def flat_fields(fields: tuple[ResponseField, ...]) -> tuple[str, ...]:
    """A response model's column fields (its Nested fields left out), in order."""
    return tuple(f for f in fields if isinstance(f, str))


def nested_fields(fields: tuple[ResponseField, ...]) -> tuple[Nested, ...]:
    """A response model's Nested fields, in order."""
    return tuple(f for f in fields if isinstance(f, Nested))


def field_name(f: ResponseField) -> str:
    return f.field if isinstance(f, Nested) else f


@dataclass(frozen=True)
class ApiProfile:
    """The API-profile environment: database tables, roles, and the users table."""

    tables: Mapping[str, Schema]  # schema name -> schema (database tables)
    table_names: Mapping[str, str]  # schema name -> SQL table name
    roles: frozenset[str]  # e.g. {"Public", "Customer", "Admin"}
    admin_role: str = "Admin"
    users_table: str = "Users"  # schema name of the users table (has id, role)
    # Page envelope: "items"/"total"/"limit"/"offset" -> JSON field name ("" = omitted).
    page_shape: Mapping[str, str] = field(default_factory=_default_page_shape)
    # Response models: name -> the exact (ordered) fields a response object carries: a
    # column name, or Nested(...) -- a list of child rows (W1-B).
    responses: Mapping[str, tuple[ResponseField, ...]] = field(default_factory=dict)
    # Native functions callable as native.<name>(...).
    natives: Mapping[str, NativeSig] = field(default_factory=dict)
    auth: AuthScheme = field(default_factory=TokenColumn)
    backend: str = "sqlite"  # lattice.api.backend dialect name
    # Where an authenticated caller's role comes from.
    roles_from: RoleSource = field(default_factory=RoleColumn)
    # How Uuid / DateTime values are stored (lattice.api.backend.UUID_STORAGES /
    # DATETIME_STORAGES): Lattice's canonical text by default; e.g. "hex32" and
    # "sql_space" for a database written by SQLAlchemy on SQLite. Responses, parameters
    # and natives are unaffected.
    uuid_storage: str = "canonical"
    datetime_storage: str = "iso_z"
    # B1: a Bool column of the users table marking an active account. When set, auth
    # refuses a caller whose value is not true with `inactive_status` /
    # `inactive_detail` -- after the caller lookup (401), before the role check (403)
    # and before parameter validation (422). Public endpoints have no caller.
    active_column: str | None = None
    inactive_status: int = 400
    inactive_detail: str = "Inactive user"
    # B2: the error body shape (ERROR_BODIES). "lattice": {"error": code, "detail": ...}
    # (the automatic 401/403 carry a detail only when one is configured below);
    # "fastapi": FastAPI's {"detail": ...} for every error, the automatic 401/403
    # saying the texts below (default: the HTTP status phrase).
    error_body: str = "lattice"
    unauthorized_detail: str | None = None
    forbidden_detail: str | None = None
    # B3: how responses spell DateTime values (DATETIME_WIRES): "iso_z" UTC ISO-8601
    # with microseconds and "Z"; "naive" the UTC value without a zone, as pydantic
    # serializes a naive datetime ("2026-02-02T06:00:00", microseconds only when set).
    datetime_wire: str = "iso_z"
    # Round 2: the default place of null sort keys (NULL_ORDERS) for `sort` without
    # `nulls=` and for `window` order.
    null_order: str = "last"
    # T2: capabilities `@procedure(effects=[...])` may declare, besides the ones effect
    # natives name (see `effect_capabilities`).
    capabilities: frozenset[str] = frozenset()
    # T2: the fuel budget of one request -- every procedure it runs shares it (loop
    # iterations, calls and comprehension elements cost one each); exhausted: R620.
    fuel: int = DEFAULT_FUEL
    # T2: the largest value a procedure may build -- a str's characters, a list's /
    # dict's / set's elements, an int's bytes; a larger one is R622. Operations whose
    # result grows with an operand (`s * n`, `s.ljust(n)`, `2 ** n`, `a + b`, format
    # specs, json.dumps) are also charged their result's size in fuel before they run.
    max_value_size: int = DEFAULT_MAX_VALUE_SIZE
    # App configuration natives may read (setting name -> Str/Int/Float/Bool): a native
    # names the ones it reads (`NativeSig.settings`) and gets them in its NativeContext.
    # The values are the deployment's, supplied when the app starts
    # (`lattice.api.runtime.build_app(..., settings=)`; a generated app reads its
    # natives module's `SETTINGS`), never part of the program.
    settings: Mapping[str, Scalar] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_page_shape()
        self._validate_responses()
        self._validate_settings()
        self._validate_natives()
        self._validate_auth()
        self._validate_roles_from()
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("ApiProfile.backend must name a backend, e.g. 'sqlite'")
        if self.uuid_storage not in UUID_STORAGES:
            raise ValueError(f"ApiProfile.uuid_storage must be one of {UUID_STORAGES}")
        if self.datetime_storage not in DATETIME_STORAGES:
            raise ValueError(f"ApiProfile.datetime_storage must be one of {DATETIME_STORAGES}")
        self._validate_active()
        if self.error_body not in ERROR_BODIES:
            raise ValueError(f"ApiProfile.error_body must be one of {ERROR_BODIES}")
        for name in ("unauthorized_detail", "forbidden_detail"):
            value = getattr(self, name)
            if value is not None and not (isinstance(value, str) and value):
                raise ValueError(f"ApiProfile.{name} must be a non-empty text or None")
        if self.datetime_wire not in DATETIME_WIRES:
            raise ValueError(f"ApiProfile.datetime_wire must be one of {DATETIME_WIRES}")
        if self.null_order not in NULL_ORDERS:
            raise ValueError(f"ApiProfile.null_order must be one of {NULL_ORDERS}")
        if isinstance(self.fuel, bool) or not isinstance(self.fuel, int) or self.fuel < 1:
            raise ValueError("ApiProfile.fuel must be a positive int")
        size = self.max_value_size
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("ApiProfile.max_value_size must be a positive int")
        if not all(isinstance(c, str) and c.isidentifier() for c in self.capabilities):
            raise ValueError("ApiProfile.capabilities must be identifiers, e.g. 'mail'")

    def effect_capabilities(self) -> frozenset[str]:
        """Every capability a procedure may declare: `capabilities` and the capability
        of each effect native (its `capability`, else its name)."""
        named = {sig.capability or name for name, sig in self.natives.items() if sig.effect}
        return frozenset(self.capabilities) | frozenset(named)

    @staticmethod
    def default_page_shape() -> Mapping[str, str]:
        return _default_page_shape()

    def _validate_page_shape(self) -> None:
        shape = self.page_shape
        unknown = sorted(set(shape) - PAGE_SHAPE_KEYS)
        if unknown:
            raise ValueError(f"page_shape has unknown keys {unknown}; allowed: items, total, ...")
        if not all(isinstance(v, str) for v in shape.values()):
            raise ValueError("page_shape values must be JSON field names (str)")
        if not shape.get("items"):
            raise ValueError("page_shape must name the items field")
        named = [v for v in shape.values() if v]
        if len(named) != len(set(named)):
            raise ValueError(f"page_shape maps two keys to the same field: {named}")

    def _validate_responses(self) -> None:
        for name, fields in self.responses.items():
            if not isinstance(fields, tuple) or not fields:
                raise ValueError(f"response model {name!r} needs a non-empty tuple of fields")
            if not all((isinstance(f, str) and f) or isinstance(f, Nested) for f in fields):
                raise ValueError(f"response model {name!r} fields must be names or Nested(...)")
            for n in nested_fields(fields):
                parts = (n.field, n.model, n.table, n.via)
                if not all(isinstance(x, str) and x for x in parts) or not n.field.isidentifier():
                    raise ValueError(
                        f"response model {name!r}: Nested(field, model, table=, via=) takes "
                        "a field name (an identifier) and non-empty names"
                    )
            names = [field_name(f) for f in fields]
            if len(set(names)) != len(names):
                raise ValueError(f"response model {name!r} lists a field twice")

    def _validate_natives(self) -> None:
        for name, sig in self.natives.items():
            if not name.isidentifier():
                raise ValueError(f"native name {name!r} is not an identifier")
            if not isinstance(sig, NativeSig):
                raise ValueError(f"native {name!r} needs a NativeSig")
            if not all(isinstance(p, Scalar) for p in sig.params):
                raise ValueError(f"native {name!r} parameters must be Scalars")
            if sig.effect:
                if sig.result is not None or sig.nullable or sig.reads_private:
                    raise ValueError(
                        f"effect native {name!r} has no result (result=None) and is "
                        "neither nullable nor reads_private"
                    )
            elif not isinstance(sig.result, Scalar):
                raise ValueError(f"native {name!r} result must be a Scalar")
            unknown = [s for s in sig.settings if s not in self.settings]
            if unknown or len(set(sig.settings)) != len(sig.settings):
                raise ValueError(
                    f"native {name!r} reads an undeclared setting (or one twice): "
                    f"{list(sig.settings)}; declare each in ApiProfile.settings"
                )
            if sig.capability is not None and not (
                sig.effect and isinstance(sig.capability, str) and sig.capability.isidentifier()
            ):
                raise ValueError(
                    f"native {name!r}: only an effect native names a capability (an identifier)"
                )

    def _validate_settings(self) -> None:
        for name, scalar in self.settings.items():
            if not (isinstance(name, str) and name.isidentifier()):
                raise ValueError(f"setting name {name!r} is not an identifier")
            if scalar not in SETTING_SCALARS:
                shown = ", ".join(s.value for s in SETTING_SCALARS)
                raise ValueError(f"ApiProfile.settings[{name!r}] must be one of {shown}")

    def _validate_auth(self) -> None:
        users = self.tables.get(self.users_table)
        match self.auth:
            case TokenColumn(column=column):
                if users is not None and column not in users:
                    raise ValueError(
                        f"token column {column!r} is not a column of {self.users_table}"
                    )
            case NativeToken(native=native, lookup=lookup, unique=unique):
                sig = self.natives.get(native)
                if sig is None:
                    raise ValueError(f"NativeToken names unknown native {native!r}")
                if sig.effect:
                    raise ValueError(f"NativeToken native {native!r} is an effect native")
                key = users.keys[0] if users is not None and users.keys else ()
                if len(key) != 1:
                    raise ValueError(
                        f"NativeToken needs a users table ({self.users_table}) "
                        "with a one-column key"
                    )
                assert users is not None
                column, what = key[0], "the key"
                if lookup is not None:
                    if lookup not in users:
                        raise ValueError(
                            f"NativeToken lookup {lookup!r} is not a column of {self.users_table}"
                        )
                    if unique == "declared" and (lookup,) not in users.keys:
                        raise ValueError(
                            f"NativeToken lookup {lookup!r} must be a unique column of "
                            f"{self.users_table} (declare it a key: keys=[..., ({lookup!r},)]), "
                            'or checked at runtime (unique="runtime")'
                        )
                    column, what = lookup, f"the unique column {lookup!r}"
                elif unique == "runtime":
                    raise ValueError(
                        'NativeToken unique="runtime" needs a lookup column (the key is '
                        "unique by construction)"
                    )
                want = users.get(column)
                if sig.params != (Scalar.STR,) or sig.result is not want:
                    shown = want.value if want is not None else "?"
                    raise ValueError(
                        f"NativeToken native {native!r} must map Str -> {shown} "
                        f"({what} of {self.users_table})"
                    )
            case _:
                raise ValueError("ApiProfile.auth must be TokenColumn(...) or NativeToken(...)")

    def _validate_active(self) -> None:
        if self.inactive_status not in INACTIVE_STATUSES:
            raise ValueError(f"ApiProfile.inactive_status must be one of {INACTIVE_STATUSES}")
        if not (isinstance(self.inactive_detail, str) and self.inactive_detail):
            raise ValueError("ApiProfile.inactive_detail must be a non-empty text")
        if self.active_column is None:
            return
        users = self.tables.get(self.users_table)
        if users is None or users.get(self.active_column) is not Scalar.BOOL:
            raise ValueError(
                f"ApiProfile.active_column {self.active_column!r} must be a Bool column "
                f"of {self.users_table}"
            )

    def _validate_roles_from(self) -> None:
        reserved = sorted(self.roles & RESERVED_ROLE_NAMES)
        if reserved:
            raise ValueError(
                f"ApiProfile.roles may not include {', '.join(reserved)}: reserved by "
                "auth=Authenticated / auth=Roles(...)"
            )
        users = self.tables.get(self.users_table)
        match self.roles_from:
            case RoleColumn(values=values):
                for stored, role in values or ():
                    if role not in self.roles or role == PUBLIC_ROLE or not stored:
                        raise ValueError(
                            f"RoleColumn values {stored!r} -> {role!r} must name a "
                            "non-Public role of the profile"
                        )
            case NoRoles(role=role):
                if role not in self.roles or role == self.admin_role:
                    raise ValueError(
                        f"NoRoles role {role!r} must be a non-admin role of the profile"
                    )
            case AdminFlag(column=column, role=role):
                if users is not None and users.get(column) is not Scalar.BOOL:
                    raise ValueError(
                        f"AdminFlag column {column!r} must be a Bool column of {self.users_table}"
                    )
                if role not in self.roles or role == self.admin_role:
                    raise ValueError(
                        f"AdminFlag role {role!r} must be a non-admin role of the profile"
                    )
            case _:
                raise ValueError(
                    "ApiProfile.roles_from must be RoleColumn(...), AdminFlag(...) or NoRoles()"
                )


@dataclass(frozen=True)
class Profile:
    schemas: Mapping[str, Schema]
    version: str = "v1"
    # Files a program may write (normalized: no leading "./"); None = unrestricted.
    outputs: frozenset[str] | None = None
    # Present for API programs (@endpoint); None for tabular pipelines.
    api: ApiProfile | None = None
    # Tabular pipelines: the default place of null sort keys (NULL_ORDERS), as
    # ApiProfile.null_order is for API programs (which use the ApiProfile's).
    null_order: str = "last"

    def __post_init__(self) -> None:
        if self.null_order not in NULL_ORDERS:
            raise ValueError(f"Profile.null_order must be one of {NULL_ORDERS}")

    @property
    def sort_nulls(self) -> str:
        """The null order of this profile's programs: the API profile's for API
        programs, else the tabular one."""
        if self.api is not None:
            return getattr(self.api, "null_order", "last")
        return self.null_order

    @property
    def enforce_keys(self) -> bool:
        """The join fan-out check (L420) applies only when some schema declares keys."""
        return any(s.keys for s in self._all_schemas())

    @property
    def enforce_nullable(self) -> bool:
        """The aggregate null policy (L421) applies only when some schema declares a
        nullable column."""
        return any(s.nullable for s in self._all_schemas())

    def _all_schemas(self) -> list[Schema]:
        tables = list(self.api.tables.values()) if self.api is not None else []
        return [*self.schemas.values(), *tables]
