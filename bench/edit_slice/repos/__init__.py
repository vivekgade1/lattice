"""The hidden oracles of the held-out repos' tickets (t19-t24, round 3).

Built like the template's (:mod:`bench.edit_slice.oracle`, same :class:`TicketOracle`
shape, run by the same :func:`~bench.edit_slice.oracle.evaluate`): the **generated
suite** (:func:`lattice.importer.suite.generate_suite`) for the routes the ticket touches,
described as :class:`RouteSpec` values of the reference, plus **ticket-specific cases**
(``<t>.<what>``) that pin each trap, on database variants that add the rows the trap
needs. The repo modules (:mod:`.taskflow`, :mod:`.justnotes`) hold the specs and cases;
this module adapts the generated suite to apps unlike the template, in the bench (the
suite itself is unchanged):

* **integer user ids.** The suite's seed plan names users by ``uuid5`` values; both apps
  key users (and so owner columns) by autoincrement integers. After generation the plan
  is rewritten: seed user k (``admin``, ``alice``, ``bob``, ``carol``) gets id k + 1 and
  every column holding a user's UUID follows. The auth hook ``mint_token`` translates a
  plan UUID the same way (the suite mints the inactive and unknown-user tokens while
  generating, before the rewrite); a UUID naming no seed user names no user.
* **no admins** (neither app has superusers: the suite's ``admin`` handle is a third
  ordinary user) and, for JustNotes, **no active flag** (its ``carol`` can sign in).
* **naive datetimes.** The plan's aware datetimes are stored naive UTC, as TaskFlow's
  ``utc_now()`` stores every timestamp.
* **seed fix-ups** only the app knows (TaskFlow's enum statuses and priorities; JustNotes'
  Unix-int timestamps, note visibility, readable titles and tag links): the repo module's
  ``fixup(plan)``, applied with the rewrite, before any database is written.
* **writing the seed** through the reference's own table classes (the app's isolated
  ``_lattice_registry``), table by table in the repo's insertion order.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session

from bench.edit_slice.apps import repo
from bench.edit_slice.oracle import TicketOracle
from bench.edit_slice.reference import reference_variant
from bench.edit_slice.tickets import ticket
from bench.edit_slice.variant import Variant
from bench.template_app import sqlite_engine
from lattice.importer.suite import (
    AuthSpec,
    GeneratedSuite,
    RouteSpec,
    SeedPlan,
    TableSpec,
    generate_suite,
    seed_plan,
)
from lattice.importer.verify import Case, CaseRecord, Deviation

Writer = Callable[[Engine], None]
IdMap = Mapping[uuid.UUID, int]


# ---- the seed ---------------------------------------------------------------------------------


def user_ids(tables: Sequence[TableSpec], auth: AuthSpec) -> dict[uuid.UUID, int]:
    """Plan UUID -> integer id of each seed user (insertion order, from 1). The plan is a
    pure function of its seed, so this is the map of the suite's own plan."""
    return {u.id: k + 1 for k, u in enumerate(seed_plan(tables, auth).users)}


def adapt_plan(plan: SeedPlan, ids: IdMap) -> None:
    """Rewrite ``plan`` in place: user UUIDs -> integer ids, aware datetimes -> naive UTC."""
    plan.users = tuple(replace(u, id=ids[u.id]) for u in plan.users)
    for rows in plan.rows.values():
        for row in rows:
            for k, v in row.items():
                if isinstance(v, uuid.UUID) and v in ids:
                    row[k] = ids[v]
                elif isinstance(v, datetime) and v.tzinfo is not None:
                    row[k] = v.astimezone(UTC).replace(tzinfo=None)


def mapped_classes(variant: Variant) -> dict[str, type]:
    """Table name -> the variant's table class (its isolated registry)."""
    reg = variant.module("_lattice_registry").metadata_registry
    return {m.local_table.name: m.class_ for m in reg.mappers}


def instance(model: type, row: Mapping[str, Any]) -> Any:
    """``model(**row)`` with enum fields given as their values (TaskFlow's statuses)."""
    values = dict(row)
    fields = getattr(model, "model_fields", {})
    for k, v in values.items():
        ann = fields[k].annotation if k in fields else None
        if v is not None and isinstance(ann, type) and issubclass(ann, Enum):
            values[k] = ann(v)
    return model(**values)


def add_rows(engine: Engine, ticket_id: str, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
    """Insert ``rows`` into ``table`` through the reference's table class."""
    model = mapped_classes(reference_variant(ticket_id))[table]
    with Session(engine) as session:
        for row in rows:
            session.add(instance(model, row))
        session.commit()


def insert_hook(ticket_id: str, order: Sequence[str]) -> Callable[[Path, SeedPlan], None]:
    """Write a plan with the reference's schema, tables in ``order``."""

    def hook(path: Path, plan: SeedPlan) -> None:
        ref = reference_variant(ticket_id)
        engine = sqlite_engine(path)
        try:
            ref.metadata.create_all(engine)
            for table in order:
                add_rows(engine, ticket_id, table, plan.rows.get(table, []))
        finally:
            engine.dispose()

    return hook


# ---- the oracle -------------------------------------------------------------------------------


@contextmanager
def frozen_time(now: float) -> Iterator[None]:
    """``time.time()`` answers ``now`` process-wide (the apps' ``int(time.time())``
    stamps; JWT expiry uses ``datetime.now`` and is unaffected)."""
    real = time.time
    time.time = lambda: now  # type: ignore[assignment]
    try:
        yield
    finally:
        time.time = real  # type: ignore[assignment]


@dataclass(frozen=True)
class RepoOracle:
    """What a repo module declares for one ticket (see :func:`build_oracle`)."""

    routes: tuple[RouteSpec, ...]
    tables: tuple[TableSpec, ...]
    auth: Callable[[IdMap], AuthSpec]  # given the plan-UUID -> id map (empty: seeding only)
    order: tuple[str, ...]
    fixup: Callable[[SeedPlan], None]
    specific: Callable[[SeedPlan], list[Case]]
    writers: Callable[[str, SeedPlan], dict[str, Writer]]
    branches: Callable[[Variant], dict[str, list[Callable[..., Any]]]]
    accepted: Mapping[str, tuple[Deviation, ...]] = field(default_factory=dict)
    clock: Callable[[], Any] | None = None
    canon: Callable[[CaseRecord], CaseRecord] | None = None
    canon_name: str = ""


def build_oracle(ticket_id: str, spec: RepoOracle) -> TicketOracle:
    """The :class:`TicketOracle` of a held-out repo's ticket (see the module docstring)."""
    app = repo(ticket(ticket_id).repo)
    ids = user_ids(spec.tables, spec.auth({}))
    suite: GeneratedSuite = generate_suite(
        spec.routes, spec.tables, spec.auth(ids), insert_hook=insert_hook(ticket_id, spec.order)
    )
    adapt_plan(suite.plan, ids)
    spec.fixup(suite.plan)
    writers = spec.writers(ticket_id, suite.plan)

    def db_factory(variant: str, path: Path) -> Path:
        suite.db_factory("seed", path)
        if variant != "seed":
            engine = sqlite_engine(path)
            try:
                writers[variant](engine)
            finally:
                engine.dispose()
        return path

    specific = spec.specific(suite.plan)
    cases = (*suite.cases, *specific)
    return TicketOracle(
        ticket_id=ticket_id,
        routes=spec.routes,
        suite=suite,
        cases=cases,
        specific=frozenset(c.name for c in specific),
        accepted=dict(spec.accepted),
        branches=spec.branches(reference_variant(ticket_id)),
        db_factory=db_factory,
        login=suite.login,
        hash_columns=app.hash_columns,
        clock=spec.clock,
        canon=spec.canon,
        canon_name=spec.canon_name,
    )


def repo_ticket_oracle(ticket_id: str) -> TicketOracle:
    """The oracle of a held-out repo's ticket (cached by :func:`oracle.ticket_oracle`)."""
    name = ticket(ticket_id).repo
    if name == "taskflow":
        from bench.edit_slice.repos import taskflow as module
    elif name == "justnotes":
        from bench.edit_slice.repos import justnotes as module
    else:
        raise KeyError(f"{ticket_id!r} is not a held-out repo's ticket")
    return build_oracle(ticket_id, module.oracle_spec(ticket_id))


__all__ = [
    "RepoOracle",
    "adapt_plan",
    "add_rows",
    "build_oracle",
    "frozen_time",
    "insert_hook",
    "mapped_classes",
    "repo_ticket_oracle",
    "user_ids",
]
