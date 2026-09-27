"""A profile: the environment a Lattice program is checked against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lattice.typesys import Schema


@dataclass(frozen=True)
class ApiProfile:
    """The API-profile environment: database tables, roles, and the users table."""

    tables: Mapping[str, Schema]  # schema name -> schema (database tables)
    table_names: Mapping[str, str]  # schema name -> SQL table name
    roles: frozenset[str]  # e.g. {"Public", "Customer", "Admin"}
    admin_role: str = "Admin"
    users_table: str = "Users"  # schema name of the users table (has id, role)


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
        return any(s.keys for s in self.schemas.values())

    @property
    def enforce_nullable(self) -> bool:
        """The aggregate null policy (L421) applies only when some schema declares a
        nullable column."""
        return any(s.nullable for s in self.schemas.values())
