"""The consumers of the importer's extract on .NET (docs/results/2026-10-18-dotnet-prereg.md, N16, Stage 5, dev): the
implementation report (Spike C), the grounded digest and hints, the app map, change impact tier 3 and the code
citation checker on D1's inventory, plus layers R and B on `dnapp` (Q8, the .NET port of the Go S1 app) through
`dn_harness_rb`. Needs the .NET SDK; the Streak spec tests also need the Streak checkout (a0679e9, read only).
"""

# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.inventory import capture
from lattice.spec import implementation as impl
from lattice.spec.rules import Trace

ROOT = Path(__file__).resolve().parents[2]
D1_TGZ = ROOT / "docs" / "results" / "data" / "2026-10-18-dotnet" / "d1.tgz"
STREAK = Path.home() / "Documents" / "streak"
CANDIDATES = ROOT / "docs" / "results" / "data" / "2026-10-16-goto-place" / "dev-candidates"
NEEDS_DOTNET = pytest.mark.skipif(shutil.which("dotnet") is None, reason="needs the .NET SDK")


def _standin() -> Any:
    sp = importlib.util.spec_from_file_location(
        "dn_wf_tests", ROOT / "tests" / "importer" / "test_dotnet_workflows.py"
    )
    mod = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
    sys.modules["dn_wf_tests"] = mod
    sp.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def d1(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("dotnet") is None or not D1_TGZ.is_file():
        pytest.skip("needs the .NET SDK and the D1 archive")
    dest = tmp_path_factory.mktemp("d1")
    with tarfile.open(D1_TGZ) as tf:
        tf.extractall(dest, filter="data")
    app = dest / "out" / "tests" / "spec" / "dnstreak"
    for junk in app.rglob("._*"):
        junk.unlink()
    return app


@pytest.fixture(scope="module")
def inv_dir(d1: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """D1's inventory with a static workflows.json (the front end is installed for the module)."""
    mod = _standin()
    installed = mod.install_standin()
    try:
        from lattice.importer import workflow_run

        out = tmp_path_factory.mktemp("wf")
        assert (
            workflow_run.main(
                [str(d1), "--out", str(out), "--static", "--user", "ann", "--user", "ben"]
            )
            == 0
        )
        capture(d1, out / "inv", workflows=out / "workflows.json")
        return out / "inv"
    finally:
        if installed:
            sys.modules.pop("lattice.importer.frontends.dotnet_aspnet", None)


@pytest.fixture(scope="module")
def streak(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Streak's spec at a0679e9, imported from a copy (the checkout is only read)."""
    if not (STREAK / ".git").exists():
        pytest.skip("the Streak checkout is not here")
    dest = tmp_path_factory.mktemp("streak")
    for name in ("spec.py", "spec.trace.json", "REQUIREMENTS.md", "CONTEXT.md"):
        got = subprocess.run(
            ["git", "-C", str(STREAK), "show", f"a0679e9:{name}"], capture_output=True, check=False
        )
        if got.returncode != 0:
            pytest.skip("Streak a0679e9 is not in the checkout")
        (dest / name).write_bytes(got.stdout)
    sp = importlib.util.spec_from_file_location("dn_streak_spec", dest / "spec.py")
    mod = importlib.util.module_from_spec(sp)  # type: ignore[arg-type]
    sys.modules["dn_streak_spec"] = mod
    sp.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.SPEC, Trace.load(dest / "spec.trace.json"), dest


@pytest.fixture(scope="module")
def ctx(d1: Path, inv_dir: Path, streak: Any) -> Any:
    spec, trace, _ = streak
    return impl.ImplContext.of(spec, trace, impl.load_inventory(inv_dir, d1))


# ---- implementation report, grounded questions, citations, app map ----------------------------


@NEEDS_DOTNET
def test_the_inventory_is_current_and_names_the_dotnet_front_end(ctx: Any) -> None:
    inv = ctx.inv
    assert inv.meta["frontend"] == "dotnet_aspnet" and inv.stale is None and inv.mismatch is None
    assert inv.extract["schema"] == "lattice.dnextract/1"


@NEEDS_DOTNET
def test_c_impl_every_required_route_is_implemented_and_nothing_is_missing(ctx: Any) -> None:
    rep = ctx.report
    s = rep["summary"]
    assert s["requirements"]["required_not_implemented"] == 0
    assert (
        s["requirements"]["implemented_unverified"] == 142 and s["requirements"]["no_element"] == 6
    )
    assert s["routes"] == {"excluded": 1, "implemented_unverified": 25}
    assert [e["key"] for e in rep["excluded"]] == ["PUT /test/clock"] and rep[
        "implemented_not_required"
    ] == []
    health = next(r for r in rep["routes"] if r["key"] == "GET /health")
    assert health["in_spec"] and health["status"] == "implemented_unverified"
    (m,) = rep["machines"]
    assert m["key"] == "impl:machine:nudges.delivery" and m["edges"] == [
        "pending -> failed",
        "pending -> sent",
        "pending -> suppressed",
    ]
    assert m["rules"] == ["R123", "R127"]


@NEEDS_DOTNET
def test_c_implm_a_route_the_spec_does_not_name_and_routes_the_code_lacks(
    d1: Path, inv_dir: Path, streak: Any, tmp_path: Path
) -> None:
    spec, trace, _ = streak
    ex = json.loads((inv_dir / "extract.json").read_text())
    extra = json.loads(
        json.dumps(
            next(r for r in ex["routes"] if r["path_template"] == "/v1/me" and r["method"] == "GET")
        )
    )
    extra.update(path="/v1/extra", path_template="/v1/extra", method="GET")
    plus = {**ex, "routes": [*ex["routes"], extra]}
    capture(d1, tmp_path / "plus", extract=plus)
    rep = impl.build_report(spec, trace, impl.load_inventory(tmp_path / "plus", d1))
    assert [r["key"] for r in rep["implemented_not_required"]] == ["GET /v1/extra"]
    gone = {"DELETE /v1/devices/{id}", "POST /v1/nudges/{id}/dismiss"}
    less = {
        **ex,
        "routes": [r for r in ex["routes"] if f"{r['method']} {r['path_template']}" not in gone],
    }
    capture(d1, tmp_path / "less", extract=less)
    rep = impl.build_report(spec, trace, impl.load_inventory(tmp_path / "less", d1))
    spec_only = {e["key"] for e in rep["endpoints"] if e["status"] == "spec_only"}
    assert spec_only == {
        "DELETE /v1/devices/{}",
        "POST /v1/nudges/{}/dismiss",
    }  # exactly the two removed
    assert rep["summary"]["requirements"]["required_not_implemented"] == 9


@NEEDS_DOTNET
def test_c_ask_hints_and_the_digest(ctx: Any, streak: Any) -> None:
    from lattice.spec.ask import build_digest

    spec, trace, _ = streak
    text = build_digest(spec, trace, ctx.inv).text
    assert "impl:" in text
    for q, key in (
        ("is there a sync endpoint?", "GET /v1/sync"),
        ("can I check in a habit?", "POST /v1/habits/{}/completions"),
        ("who can dismiss a nudge?", "POST /v1/nudges/{}/dismiss"),
    ):
        r = impl.find_candidates(ctx, q, 5)
        assert r["hint"] == "exists" and any(key in c["target"] for c in r["candidates"]), q


@NEEDS_DOTNET
def test_c_mcp_the_workflow_tools_answer_with_cs_citations(ctx: Any) -> None:
    found = impl.find_candidates(ctx, "check in", 5)
    assert found["candidates"] and all(
        ".cs:" in e for c in found["candidates"] for e in c["evidence"]
    )
    m = impl.get_workflow(ctx, "nudges.delivery")
    assert (
        m["type"] == "machine"
        and m["states"] == ["failed", "pending", "sent", "suppressed"]
        and ".cs:" in m["evidence"][0]
    )
    fid = ctx.report["flows"][0]["id"]
    assert impl.get_workflow(ctx, fid)["type"] == "flow"


def _cites(blob: str) -> list[str]:
    return sorted(set(re.findall(r"code:([A-Za-z0-9_./-]+:\d+)", blob)))


@NEEDS_DOTNET
def test_c_cite_every_code_citation_resolves(ctx: Any, d1: Path, streak: Any) -> None:
    from lattice.spec.appmap import build_appmap

    spec, trace, _ = streak
    blob = (
        json.dumps(ctx.report)
        + json.dumps(ctx.inv.workflows)
        + json.dumps(build_appmap(spec, trace, ctx.inv))
    )
    cites = _cites(blob)
    assert len(cites) > 50
    for c in cites:
        rel, line = c.rsplit(":", 1)
        lines = (d1 / rel).read_text().split("\n")
        assert 1 <= int(line) <= len(lines), c
        text = lines[int(line) - 1].strip()
        if len(text.replace(" ", "")) >= 8:
            assert impl.check_code(d1, ctx.inv, {"target": f"code:{c}", "quote": text}) is None, c
    assert all(c.split(":")[0].endswith(".cs") for c in cites)


@NEEDS_DOTNET
def test_c_map_the_app_map_has_rules_workflows_machine_and_access(ctx: Any, streak: Any) -> None:
    from lattice.spec.appmap import build_appmap

    spec, trace, _ = streak
    am = build_appmap(spec, trace, ctx.inv)
    assert (
        am["state"] == "ok" and am["rules"] and am["workflows"] and am["access"] and am["endpoints"]
    )
    assert [m["key"] if "key" in m else m for m in am["machines"]]


# ---- change impact tier 3 ---------------------------------------------------------------------


@NEEDS_DOTNET
@pytest.mark.parametrize(
    "change", ["d1_limit", "d2_bound", "d4_removed_endpoint", "d5_access", "d6_reword"]
)
def test_c_imp_tier_3_lists_resolvable_cs_locations(
    change: str, d1: Path, inv_dir: Path, streak: Any, tmp_path: Path
) -> None:
    cand = CANDIDATES / change
    if not cand.is_dir():
        pytest.skip("the goto-place candidates are not in this checkout")
    _, _, proj = streak
    (proj / "studio.toml").write_text(
        f'[implementation]\ninventory = "{inv_dir}"\napp_root = "{d1}"\n'
    )
    out = tmp_path / "impact.json"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.spec", "impact", "--project", str(proj), "--candidate", str(cand),
         "--inventory", str(inv_dir), "--tiers", "3", "--json", "--out", str(out)],
        cwd=proj, capture_output=True, text=True, check=False, env={**__import__("os").environ, "PYTHONPATH": f"{proj}:{ROOT}"},
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-400:]
    locs = json.loads(done.stdout)["tier3"]["locations"]
    assert locs and all(loc["file"].endswith(".cs") for loc in locs)
    for loc in locs:
        lines = (d1 / loc["file"]).read_text().split("\n")
        assert 1 <= loc["line"] <= len(lines)


# ---- layers R and B on dnapp (Q8) -------------------------------------------------------------


@pytest.fixture
def dnapp(monkeypatch: pytest.MonkeyPatch) -> Any:
    if shutil.which("dotnet") is None:
        pytest.skip("needs the .NET SDK")
    from lattice.spec.process import live_processes
    from tests.spec import dn_harness_rb as rb

    def variant(bug: str = "") -> Any:
        monkeypatch.setenv("DNAPP_BUG", bug)
        return rb.make_app

    yield variant
    assert live_processes() == [], "a process outlived its check"


def run_rb(factory: Any, layers: str) -> Any:
    from lattice.spec import check
    from tests.spec import dn_harness_rb as rb

    return check(
        rb.SPEC,
        factory,
        rb.build_db,
        layers=layers,
        runtime=rb.RUNTIME,
        login=rb.LOGIN,
        rules=rb.RULES,
    )


def test_n_r1_layer_r_on_dnapp_matches_go_s1(dnapp: Any) -> None:
    rep = run_rb(dnapp(), "R")
    s = rep.rules.summary["items"]
    assert (s["verified"], s["refuted"], s["unclear"], s["unexercised"]) == (16, 0, 0, 7)
    assert abs(s["verified"] / sum(s.values()) - 0.6957) <= 0.10
    assert rep.rules.summary["routes"] == {"verified": 4, "total": 4}


@pytest.mark.parametrize(("bug", "case"), [("noauth", "no caller"), ("forbidden", "404 Not found")])
def test_n_r2_layer_r_refutes_the_mutants_on_get_run(dnapp: Any, bug: str, case: str) -> None:
    rep = run_rb(dnapp(bug), "R")
    found = [f for f in rep.findings if f.layer == "R"]
    assert (
        found
        and {f.endpoint for f in found} == {"GET /runs/{rid}"}
        and any(case in (f.case or "") for f in found)
    )


def test_n_b1_layer_b_is_quiet_on_the_safe_app_and_kills_the_variants(dnapp: Any) -> None:
    safe = run_rb(dnapp(), "B")
    assert safe.findings == [] and len(safe.cases) == 32
    for bug, endpoint in (
        ("nocheck", "POST /configs/{cid}/runs"),
        ("nocancel", "POST /runs/{rid}/cancel"),
    ):
        assert {f.endpoint for f in run_rb(dnapp(bug), "B").findings} == {endpoint}


def test_n_10_r_and_b_are_repeatable(dnapp: Any) -> None:
    a, b = run_rb(dnapp(), "RB"), run_rb(dnapp(), "RB")
    assert {i: v.verdict for i, v in a.rules.items.items()} == {
        i: v.verdict for i, v in b.rules.items.items()
    }
    assert [f.case for f in a.findings] == [f.case for f in b.findings]
