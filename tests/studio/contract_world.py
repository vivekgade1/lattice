"""A synthetic two-provider workspace for the contract surfaces (``GET /api/contracts``, the MCP
tool ``contract_check``), built under ``tmp_path`` from tests/contracts/helpers.py: a consumer
``cons`` with a static link to ``prov`` and a live link to ``prov2``. ``prov`` has drifted since
the pin (a 201 became a 200: a BROKEN and a CHANGED finding) and the consumer relies on one status
the spec never showed (a gap). Nothing here starts an app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.contracts.helpers import BASE, IMPORTS, World, copy_data, write_spec

EXTRA_IMPORT = {"endpoint": "GET /jobs", "status": [500], "auth": "token", "as_user": "ann"}


def drifted() -> list[tuple[str, list[dict]]]:
    data = copy_data(BASE)
    for s in data[1][1]:
        if s["method"] == "POST" and s["path"] == "/jobs/7/runs" and s["expect_status"] == 201:
            s["expect_status"] = 200
    return data


def make_world(root: Path, *, drift: bool = True, second_link: bool = True) -> World:
    """The workspace ``w`` (manifest ``root/lattice.workspace.toml``), pinned, then drifted."""
    w = World(root, imports=[*IMPORTS, EXTRA_IMPORT], repo=True)
    if second_link:
        write_spec(root / "prov2", BASE)
        w.write_manifest(
            '\n[[project]]\nname = "prov2"\npath = "prov2"\nrole = "provider"\n'
            'spec = "spec:SPEC"\n\n[[link]]\nfrom = "cons"\nto = "prov2"\nkind = "uses"\n'
            'contract = "live"\n'
        )
    rc, _out = w.pin()
    assert rc == 0, _out
    if drift:
        w.edit(drifted())
    return w


def without_elapsed(out: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in out.items() if k != "elapsed_s"}


def types(d: dict[str, Any]) -> dict[str, str]:
    return {k: type(v).__name__ for k, v in d.items()}
