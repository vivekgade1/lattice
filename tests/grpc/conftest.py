"""Shared fixtures of the gRPC tests (Spike I): the extracts of the notes app (N) and the
proto torture set (T), the bridge, and the notes server binary. Everything needing Go is
skipped when ``go`` is not on PATH."""

from __future__ import annotations

import shutil
from typing import Any

import pytest

from tests.grpc import oracle

HAVE_GO = shutil.which("go") is not None
needs_go = pytest.mark.skipif(not HAVE_GO, reason="go is not installed")


@pytest.fixture(scope="session")
def front_end() -> Any:
    from lattice.importer.frontends.go_grpc import GoGrpcFrontEnd

    return GoGrpcFrontEnd()


@pytest.fixture(scope="session")
def n_extract(front_end: Any) -> dict[str, Any]:
    return front_end.extract_json(oracle.FIXTURES, "notes")  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def t_extract(front_end: Any) -> dict[str, Any]:
    return front_end.extract_json(oracle.FIXTURES, "protos")  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def truth() -> dict[str, Any]:
    return oracle.load(oracle.NOTES / "truth.json")  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def notes_binary() -> Any:
    from tests.grpc.fixtures.notes import harness

    return harness.binary()
