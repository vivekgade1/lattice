"""DN801/DN802 on what an earlier statement settled and where an answer is built (node F5): the fixture ``dnfindact`` has the covered
shapes (an owner compared in the query, a row loaded for the caller first and then removed or updated by the same key, a query handed to a
shaping helper that compares the owner) and the uncovered ones next to them. Generic C# over ASP.NET Core + EF Core; no app's names.
Needs the .NET SDK (skips without it)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from lattice import dncheck
from lattice.dncheck.policy import DnPolicy, all_dn_findings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dnfindact"
pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")

POLICY = DnPolicy(
    tables={"Doc": "docs", "User": "users"},
    users_table="User",
    owners={"Doc": "OwnerId"},
    private={"Doc": ("Secret",)},
    rules=("DN801", "DN802"),
)


@pytest.fixture(scope="module", autouse=True)
def helper(tmp_path_factory: pytest.TempPathFactory):
    old = os.environ.get("LATTICE_DNCHECK_CACHE")
    os.environ["LATTICE_DNCHECK_CACHE"] = str(tmp_path_factory.mktemp("dncheck-cache"))
    try:
        yield dncheck.build_dncheck()
    finally:
        if old is None:
            os.environ.pop("LATTICE_DNCHECK_CACHE", None)
        else:
            os.environ["LATTICE_DNCHECK_CACHE"] = old


@pytest.fixture(scope="module")
def keys() -> set[str]:
    return {f.key for f in all_dn_findings(FIXTURE, POLICY)}


def test_exactly_the_uncovered_shapes_are_found(keys):
    assert keys == {
        "DN801|GET /docs/{id:int}/raw|Doc",  # the query is handed to a helper that only shapes it
        "DN801|POST /docs/discard|Doc",  # a row bound from the body is removed, not one loaded for the caller
        "DN801|POST /docs/{id:int}/touch-other/{otherId:int}|Doc",  # the check was of another id than the write
        "DN801|POST /docs/{id:int}/touch-unchecked|Doc",  # the load's miss is not refused
        "DN801|POST /docs/for|Doc",  # the owner comes from the request
        "DN802|GET /docs/{id:int}/leak|Doc.Secret",  # copied into what a service returns, answered by StatusCode
    }


@pytest.mark.parametrize(
    "route",
    [
        "GET /docs",
        "GET /docs/{id:int}",
        "DELETE /docs/{id:int}",
        "POST /docs/{id:int}/touch",
        "POST /docs/{id:int}/touch-inline",
        "POST /docs",
        "GET /docs/{id:int}/view",
    ],
)
def test_the_covered_shapes_stay_silent(keys, route):
    assert not [k for k in keys if k.split("|")[1] == route], (route, keys)
