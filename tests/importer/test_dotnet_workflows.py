"""The workflow layer on .NET (docs/results/2026-10-18-dotnet-prereg.md, N16 and Stage 5): the C# Workflow pass
(`dncheck extract` with {"workflow": true}), the per-front-end dispatch (`extract_source`) and `workflows`/`inventory` run
on D1 (dev) and the Arm 0 fixture. Needs the .NET SDK. D1 is unpacked from its archive into a temporary directory.

``lattice.importer.frontends.dotnet_aspnet`` is B2's: until it is merged a stand-in with the same surface
(``FRONT_END``, ``model_from_extract``) is installed for the session, built on the Go mapper.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import types
from pathlib import Path
from typing import Any

import pytest

from lattice import dncheck
from lattice.importer import extract_source as ES
from lattice.importer import workflows as W

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "dncheck" / "fixtures" / "dnparity1"
D1_TGZ = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "d1.tgz"
WF_KEYS = {
    "param_uses",
    "creates",
    "param_literals",
    "status_facts",
    "workflow_unread",
    "workflow_notes",
}

pytestmark = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")


def install_standin() -> bool:
    """Install a stand-in for B2's front end when it is not merged yet; True when installed."""
    try:
        import lattice.importer.frontends.dotnet_aspnet  # noqa: F401
    except ImportError:
        pass
    else:
        return False
    mod = types.ModuleType("lattice.importer.frontends.dotnet_aspnet")

    class DotnetAspnetFrontEnd:
        name = "aspnetcore"

        def detect(self, root: Path, package: str) -> bool:
            return any((Path(root) / package).glob("*.csproj"))

        def extract_json(
            self, root: Path, package: str = ".", workflow: bool = False
        ) -> dict[str, Any]:
            return dncheck.extract_json(Path(root) / package, workflow=workflow)

        def runtime(
            self, root: Path, package: str = ".", *, model: Any = None, credentials: Any = None
        ) -> Any:
            from lattice.importer.frontends.go_gin import _harness_attr, _studio_app
            from lattice.spec.process import process_runtime

            ref = _studio_app(Path(root)).get("runtime")
            return _harness_attr(Path(root), ref) if ref else process_runtime(framework="dotnet")

    DotnetAspnetFrontEnd.__module__ = mod.__name__

    def model_from_extract(doc: dict[str, Any], package: str = ".") -> Any:
        from lattice.importer.frontends import go_gin

        return go_gin.model_from_extract({**doc, "schema": go_gin.SCHEMA}, package)

    mod.FRONT_END = DotnetAspnetFrontEnd()  # type: ignore[attr-defined]
    mod.model_from_extract = model_from_extract  # type: ignore[attr-defined]
    sys.modules[mod.__name__] = mod
    return True


@pytest.fixture(scope="session", autouse=True)
def standin() -> Any:
    installed = install_standin()
    yield
    if installed:
        sys.modules.pop("lattice.importer.frontends.dotnet_aspnet", None)


@pytest.fixture(scope="session")
def d1(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not D1_TGZ.is_file():
        pytest.skip("D1 archive is not in this checkout")
    dest = tmp_path_factory.mktemp("d1")
    with tarfile.open(D1_TGZ) as tf:
        tf.extractall(dest, filter="data")
    return dest / "out" / "tests" / "spec" / "dnstreak"


@pytest.fixture(scope="session")
def x(d1: Path) -> dict[str, Any]:
    return dncheck.extract_json(d1, workflow=True)


@pytest.fixture(scope="session")
def fx() -> dict[str, Any]:
    return dncheck.extract_json(FIXTURE, workflow=True)


@pytest.fixture(scope="session")
def plan(x: dict[str, Any]) -> W.Plan:
    return W.plan(x, ["ann", "ben"])


# ---- the C# Workflow pass ---------------------------------------------------------------------


def test_the_default_part_of_the_document_is_the_default_document(fx: dict[str, Any]) -> None:
    base = dncheck.extract_json(FIXTURE)
    assert {k: v for k, v in fx.items() if k not in WF_KEYS} == {
        k: v for k, v in base.items() if k not in WF_KEYS
    }
    assert all(isinstance(fx[k], list) for k in WF_KEYS)


def test_workflow_keys_are_absent_without_the_flag() -> None:
    assert not (WF_KEYS - {"status_facts"}) & set(dncheck.extract_json(FIXTURE))


def test_d1_default_part_differs_only_by_the_tuple_resolved_status_writes(
    d1: Path, x: dict[str, Any]
) -> None:
    base = dncheck.extract_json(d1)
    diff = {k for k in base if k not in WF_KEYS and base[k] != x[k]}
    assert diff == {"status_writes"}
    assert any("tuple local" in n for n in x["workflow_notes"])


def test_path_and_body_values_reach_the_columns_they_are_compared_to(x: dict[str, Any]) -> None:
    uses = {
        (u["route"], u["source"], u["param"], u["table"], u["column"], u["how"])
        for u in x["param_uses"]
    }
    assert ("GET /v1/habits/{id}", "path", "id", "habits", "id", "compare") in uses
    assert ("POST /v1/habits", "body", "id", "habits", "id", "compare") in uses
    assert ("POST /v1/completions/batch", "body", "habit_id", "habits", "id", "compare") in uses
    assert ("POST /v1/nudges/{id}/dismiss", "path", "id", "nudges", "id", "compare") in uses
    assert (
        "DELETE /v1/habits/{habit_id}/completions/{id}",
        "path",
        "id",
        "completions",
        "id",
        "compare",
    ) in uses
    # records keep their members apart: a check-in's date never reaches its id
    assert not any(
        u["column"] == "id" and u["table"] == "completions" and u["param"] == "date"
        for u in x["param_uses"]
    )


def test_creates_say_where_the_key_comes_from(x: dict[str, Any]) -> None:
    keys = {(c["route"], c["table"]): c["key_from"] for c in x["creates"]}
    assert keys[("POST /v1/habits", "habits")] == "id"
    assert keys[("POST /v1/habits/{habit_id}/completions", "completions")] == "id"
    assert keys[("POST /v1/devices", "devices")] == "server"
    assert keys[("POST /v1/habits/{habit_id}/completions", "nudges")] == "server"


def test_literals_are_the_constants_a_request_value_is_compared_to(x: dict[str, Any]) -> None:
    lits = {(lit["route"], lit["param"]): lit["values"] for lit in x["param_literals"]}
    assert lits[("POST /v1/habits", "period")] == ["week", "month", "year"]
    assert lits[("POST /v1/devices", "platform")] == ["ios", "android"]
    assert len(lits) == 2


def test_the_nudge_delivery_machine_is_read_through_the_workers_tuple(plan: W.Plan) -> None:
    (m,) = plan.machines
    assert (m["table"], m["column"]) == ("nudges", "delivery")
    assert m["states"] == ["failed", "pending", "sent", "suppressed"]
    assert m["initial"] == ["pending", "suppressed"]
    assert m["static"] is True
    assert {(e["from"], e["to"]) for e in m["edges"]} == {
        ("pending", "sent"),
        ("pending", "failed"),
        ("pending", "suppressed"),
    }


def test_the_dependency_graph_has_the_streak_flows(plan: W.Plan) -> None:
    edges = {(e.table, e.producer, e.consumer, e.param, e.source) for e in plan.edges}
    assert ("habits", "POST /v1/habits", "GET /v1/habits/{id}/progress", "id", "path") in edges
    assert ("habits", "POST /v1/habits", "POST /v1/completions/batch", "habit_id", "body") in edges
    assert (
        "completions",
        "POST /v1/habits/{habit_id}/completions",
        "DELETE /v1/habits/{habit_id}/completions/{id}",
        "id",
        "path",
    ) in edges
    assert ("devices", "POST /v1/devices", "DELETE /v1/devices/{id}", "id", "path") in edges
    assert (
        "nudges",
        "POST /v1/habits/{habit_id}/completions",
        "POST /v1/nudges/{id}/dismiss",
        "id",
        "path",
    ) in edges
    assert len(edges) == 12 and plan.view.unread == []
    sk = {" > ".join(f.skeleton(plan.view)) for f in plan.flows}
    assert "POST /v1/auth/register > POST /v1/auth/login" in sk
    assert "POST /v1/habits > POST /v1/habits/{}/completions > POST /v1/nudges/{}/dismiss" in sk


def test_the_fixtures_machine_and_edges(fx: dict[str, Any]) -> None:
    p = W.plan(fx, ["ann", "ben"])
    (m,) = p.machines
    assert (
        m["states"] == ["Archived", "Draft", "Published"]
        and m["initial"] == ["Draft"]
        and m["static"]
    )
    assert ("notes", "POST /api/Notes", "GET /api/Notes/{id}", "id", "path") in {
        (e.table, e.producer, e.consumer, e.param, e.source) for e in p.edges
    }


def test_the_static_extract_is_deterministic(d1: Path, x: dict[str, Any]) -> None:
    for _ in range(2):
        assert dncheck.extract_json(d1, workflow=True) == x


# ---- the dispatch (extract_source) ------------------------------------------------------------


def test_front_end_for_picks_by_detection(d1: Path) -> None:
    assert ES.front_end_for(d1).name == "aspnetcore"
    assert ES.frontend_name(ES.front_end_for(d1)) == "dotnet_aspnet"
    go = ES.front_end_for(ROOT / "tests" / "gocheck" / "fixtures" / "goextract_workflow")
    assert go.name == "gin" and ES.frontend_name(go) == "go_gin"


def test_a_directory_neither_detects_goes_to_the_go_front_end(tmp_path: Path) -> None:
    assert ES.front_end_for(tmp_path).name == "gin"


def test_frontend_of_and_the_source_sets() -> None:
    assert ES.frontend_of({"schema": "lattice.dnextract/1"}) == "dotnet_aspnet"
    assert ES.frontend_of({"schema": "lattice.goextract/1"}) == "go_gin"
    assert ES.frontend_of({}) == "go_gin"
    assert ES.source_suffixes(None)[0] == (".go",) and ES.source_suffixes("go_gin")[1] == (
        "go.mod",
        "go.sum",
    )
    suffixes, names, skip = ES.source_suffixes("dotnet_aspnet")
    assert (
        ".cs" in suffixes
        and ".csproj" in suffixes
        and "global.json" in names
        and skip == {"bin", "obj"}
    )


def test_model_from_extract_dispatches_on_the_schema_and_refuses_unknown(x: dict[str, Any]) -> None:
    model = ES.model_from_extract(x)
    assert {t.table_name for t in model.tables} >= {"habits", "nudges", "completions"}
    from lattice.importer.frontends.go_gin import GoExtractError

    with pytest.raises(GoExtractError):
        ES.model_from_extract({"schema": "lattice.other/1"})


def test_the_source_set_of_a_dotnet_app_skips_bin_and_obj(d1: Path, tmp_path: Path) -> None:
    from lattice.spec.implementation import source_files

    app = tmp_path / "app"
    shutil.copytree(d1, app)
    (app / "bin" / "Release").mkdir(parents=True)
    (app / "bin" / "Release" / "x.cs").write_text("class X {}")
    (app / "obj").mkdir()
    (app / "obj" / "y.cs").write_text("class Y {}")
    (app / "main.go").write_text("package main")
    files = source_files(app, "dotnet_aspnet")
    assert "dnstreak.csproj" in files and "packages.lock.json" in files and "Program.cs" in files
    assert not any(f.startswith(("bin/", "obj/")) or f.endswith(".go") for f in files)
    assert source_files(app) == ["main.go"]  # the Go set is what it was


def test_inventory_of_d1_names_the_frontend_and_counts_the_extract(
    d1: Path, x: dict[str, Any], tmp_path: Path
) -> None:
    from lattice.importer.inventory import capture

    meta = capture(d1, tmp_path / "inv")
    assert meta["frontend"] == "dotnet_aspnet" and meta["package"] == "."
    ex = json.loads((tmp_path / "inv" / "extract.json").read_text())
    assert (
        ex["schema"] == "lattice.dnextract/1" and len(ex["routes"]) == 26 and len(ex["tables"]) == 9
    )
    assert all(
        f["path"].endswith((".cs", ".csproj", "packages.lock.json"))
        for f in meta["source"]["files"]
    )
    assert len(meta["source"]["files"]) == 51


# ---- `workflows` end to end on D1 -------------------------------------------------------------


@pytest.fixture(scope="session")
def d1_root(d1: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """D1 as a project root whose harness is importable by a top-level name (the repository's own `tests`
    package would shadow the archive's `tests.spec`)."""
    root = tmp_path_factory.mktemp("d1root")
    shutil.copytree(d1, root / "dnstreak", ignore=shutil.ignore_patterns("bin", "obj", "._*"))
    shutil.copy(d1.parent / "dnstreak_harness.py", root / "dnstreak_harness.py")
    (root / "studio.toml").write_text(
        '[app]\napp = "dnstreak_harness:make_app"\ndb = "dnstreak_harness:build_db"\n'
        'runtime = "dnstreak_harness:RUNTIME"\nservices = "dnstreak_harness:SERVICES"\n'
    )
    return root


def run_workflows(root: Path, out: Path, *extra: str) -> dict[str, Any]:
    from lattice.importer import workflow_run

    code = workflow_run.main(
        [
            str(root),
            "--out",
            str(out),
            "--package",
            "dnstreak",
            "--user",
            "ann",
            "--user",
            "ben",
            *extra,
        ]
    )
    assert code == 0
    return json.loads((out / "workflows.json").read_text())


def test_workflows_static_names_the_framework_and_is_repeatable(
    d1_root: Path, tmp_path: Path
) -> None:
    docs = [run_workflows(d1_root, tmp_path / f"s{i}", "--static") for i in range(3)]
    assert docs[0] == docs[1] == docs[2]
    d = docs[0]
    assert d["app"]["framework"] == "aspnetcore" and d["app"]["module"] == "DnStreak"
    assert (
        d["summary"]["edges"] == 12
        and d["summary"]["machines"] == 1
        and d["summary"]["verified"] == 0
    )
    (m,) = d["machines"]
    assert (m["table"], m["column"]) == ("nudges", "delivery")
    for sk in (
        "POST /v1/auth/register > POST /v1/auth/login",
        "POST /v1/devices > DELETE /v1/devices/{}",
    ):
        assert any(" > ".join(f["skeleton"]) == sk for f in d["flows"])


def test_workflows_observed_on_sqlite_runs_the_flows_through_the_process_runtime(
    d1_root: Path, tmp_path: Path
) -> None:
    d = run_workflows(d1_root, tmp_path / "dyn", "--database", "sqlite")
    assert d["run"]["mode"] == "observed" and d["run"]["database"] == "sqlite"
    by = {f["status"] for f in d["flows"]}
    assert "verified" in by
    verified = [f for f in d["flows"] if f["status"] == "verified"]
    assert any(f["kind"] == "effect" for f in verified)
    assert d["machines"][0]["status"] in ("static", "confirmed", "observed")
