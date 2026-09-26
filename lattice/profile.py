"""A profile: the environment a Lattice program is checked against."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lattice.typesys import Schema


@dataclass(frozen=True)
class Profile:
    schemas: Mapping[str, Schema]
    version: str = "v1"
