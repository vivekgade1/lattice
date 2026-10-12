"""Lattice security attack module (spike 1b).

A SEPARATE package: it is not part of ``lattice check``, never an A-X layer, and never a
rebuild gate -- it can neither block nor edit a build, and a finding never edits the spec or
the code. It attacks only the app it starts itself, on a loopback host with a throwaway
database. It derives attacks deterministically from the spec model (authz, auth, bounds,
mass, state, leak), runs them through its own httpx transport (so the DSL's inability to
send headers or carried values does not apply here) and a judge replays every claimed
finding on a fresh database and seed, keeping only those that reproduce.

FOR THE OWNER'S OWN SYSTEMS ONLY. There is no URL or host argument.

CLI: ``python -m lattice.security plan|run|report --project DIR`` (and ``lattice attack``).
"""

from __future__ import annotations

BANNER = (
    "lattice attack -- a spec-driven security swarm, FOR THE OWNER'S OWN SYSTEMS ONLY. "
    "It attacks only the app it starts itself on a loopback throwaway database; it takes no "
    "URL or host. A report is for humans; it never edits the spec or code."
)

__all__ = ["BANNER"]
