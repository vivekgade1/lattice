"""Extract: a FastAPI + SQLModel repository -> ``RepoModel`` (spec 2026-09-27 importer §3.1).

A re-export shim (Phase 2 plan, W1-A): the extractor moved, unchanged, to
:mod:`lattice.importer.frontends.fastapi` (the FastAPI front end), the package index and
its literal helpers to :mod:`lattice.importer.frontends.pyindex`, and the app model to
:mod:`lattice.importer.appmodel`. Every name importable from here before the move still
is -- the private helpers included -- and is the same object as in its new module.

(No ``__all__``: the re-exports are ``import X as X``. Short string literals in a module
the importer loads feed Hypothesis's local-constant pool, which the request suite's
generated bodies draw from -- see ``lattice.importer.suite``.)
"""

from __future__ import annotations

from lattice.importer.appmodel import AppModel as AppModel
from lattice.importer.appmodel import AuthModel as AuthModel
from lattice.importer.appmodel import DataModel as DataModel
from lattice.importer.appmodel import Dependency as Dependency
from lattice.importer.appmodel import FieldSpec as FieldSpec
from lattice.importer.appmodel import Guard as Guard
from lattice.importer.appmodel import NativeCandidate as NativeCandidate
from lattice.importer.appmodel import NativeParam as NativeParam
from lattice.importer.appmodel import OwnerUse as OwnerUse
from lattice.importer.appmodel import RaiseSite as RaiseSite
from lattice.importer.appmodel import RepoModel as RepoModel
from lattice.importer.appmodel import RoleScheme as RoleScheme
from lattice.importer.appmodel import Route as Route
from lattice.importer.appmodel import RouteParam as RouteParam
from lattice.importer.appmodel import TableModel as TableModel
from lattice.importer.frontends import fastapi as _fastapi
from lattice.importer.frontends import pyindex as _pyindex
from lattice.importer.frontends.fastapi import LANGUAGE_GAP as LANGUAGE_GAP
from lattice.importer.frontends.fastapi import extract as extract
from lattice.importer.frontends.fastapi import role_name as role_name
from lattice.importer.frontends.fastapi import role_scheme as role_scheme
from lattice.importer.frontends.fastapi import route_function as route_function
from lattice.importer.frontends.pyindex import _Index as _Index
from lattice.importer.frontends.pyindex import _single_assignment as _single_assignment

# every other name of the moved modules (constants, private helpers), as before the move
for _moved in (_pyindex, _fastapi):
    globals().update({k: v for k, v in vars(_moved).items() if not k.startswith("__")})
