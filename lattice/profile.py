"""A profile: the environment a Lattice program is checked against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from lattice.typesys import Scalar, Schema

# page_shape keys: the JSON fields of a Page response envelope.
PAGE_SHAPE_KEYS = frozenset({"items", "total", "limit", "offset"})


def _default_page_shape() -> Mapping[str, str]:
    return {"items": "items", "limit": "limit", "offset": "offset"}


@dataclass(frozen=True)
class NativeSig:
    """A typed native function `native.<name>(args...)`: a pure scalar function
    implemented in Python and registered with the database connection.

    `nullable`: the result may be missing (null), e.g. a token decoder on a bad token.
    `reads_private`: a verifier (e.g. verify_password(plain, hash) -> Bool) whose
    arguments may be private columns: reading one through it is not an oracle (L803)
    and its result does not carry the private value."""

    params: tuple[Scalar, ...]
    result: Scalar
    nullable: bool = False
    reads_private: bool = False


@dataclass(frozen=True)
class TokenColumn:
    """Auth: `Authorization: Bearer <token>` is looked up in this column of the users
    table (today's behaviour)."""

    column: str = "token"


@dataclass(frozen=True)
class NativeToken:
    """Auth: the bearer token is decoded to the caller's users-table key by a native
    (`Str -> <key scalar>`; a null result or a missing row answers 401)."""

    native: str


AuthScheme = TokenColumn | NativeToken


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
    # Response models: name -> the exact (ordered) fields a response object carries.
    responses: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Native functions callable as native.<name>(...).
    natives: Mapping[str, NativeSig] = field(default_factory=dict)
    auth: AuthScheme = field(default_factory=TokenColumn)
    backend: str = "sqlite"  # lattice.api.backend dialect name

    def __post_init__(self) -> None:
        self._validate_page_shape()
        self._validate_responses()
        self._validate_natives()
        self._validate_auth()
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("ApiProfile.backend must name a backend, e.g. 'sqlite'")

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
            if not all(isinstance(f, str) and f for f in fields):
                raise ValueError(f"response model {name!r} fields must be names")
            if len(set(fields)) != len(fields):
                raise ValueError(f"response model {name!r} lists a field twice")

    def _validate_natives(self) -> None:
        for name, sig in self.natives.items():
            if not name.isidentifier():
                raise ValueError(f"native name {name!r} is not an identifier")
            if not isinstance(sig, NativeSig):
                raise ValueError(f"native {name!r} needs a NativeSig")
            if not all(isinstance(p, Scalar) for p in sig.params):
                raise ValueError(f"native {name!r} parameters must be Scalars")
            if not isinstance(sig.result, Scalar):
                raise ValueError(f"native {name!r} result must be a Scalar")

    def _validate_auth(self) -> None:
        users = self.tables.get(self.users_table)
        match self.auth:
            case TokenColumn(column=column):
                if users is not None and column not in users:
                    raise ValueError(
                        f"token column {column!r} is not a column of {self.users_table}"
                    )
            case NativeToken(native=native):
                sig = self.natives.get(native)
                if sig is None:
                    raise ValueError(f"NativeToken names unknown native {native!r}")
                key = users.keys[0] if users is not None and users.keys else ()
                if len(key) != 1:
                    raise ValueError(
                        f"NativeToken needs a users table ({self.users_table}) "
                        "with a one-column key"
                    )
                want = users.get(key[0]) if users is not None else None
                if sig.params != (Scalar.STR,) or sig.result is not want:
                    shown = want.value if want is not None else "?"
                    raise ValueError(
                        f"NativeToken native {native!r} must map Str -> {shown} "
                        f"(the key of {self.users_table})"
                    )
            case _:
                raise ValueError("ApiProfile.auth must be TokenColumn(...) or NativeToken(...)")


@dataclass(frozen=True)
class Profile:
    schemas: Mapping[str, Schema]
    version: str = "v1"
    # Files a program may write (normalized: no leading "./"); None = unrestricted.
    outputs: frozenset[str] | None = None
    # Present for API programs (@endpoint); None for tabular pipelines.
    api: ApiProfile | None = None

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
