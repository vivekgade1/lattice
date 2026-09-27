"""A profile: the environment a Lattice program is checked against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lattice.typesys import Schema


@dataclass(frozen=True)
class Profile:
    schemas: Mapping[str, Schema]
    version: str = "v1"
    # Files a program may write (normalized: no leading "./"); None = unrestricted.
    outputs: frozenset[str] | None = None

    @property
    def enforce_keys(self) -> bool:
        """The join fan-out check (L420) applies only when some schema declares keys."""
        return any(s.keys for s in self.schemas.values())

    @property
    def enforce_nullable(self) -> bool:
        """The aggregate null policy (L421) applies only when some schema declares a
        nullable column."""
        return any(s.nullable for s in self.schemas.values())
