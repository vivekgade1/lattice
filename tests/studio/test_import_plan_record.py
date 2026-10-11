"""scripts/import_plan_record.py: a pre-v1.10 plan record goes into the state store and
resumes (Streak's p2, the fixture of tests/studio/test_resume.py)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from lattice.studio import state as st
from lattice.studio.plan import resume_view

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "studio" / "fixtures" / "streak-p2-land-waiting.json"


def _script():
    spec = importlib.util.spec_from_file_location(
        "import_plan_record", ROOT / "scripts" / "import_plan_record.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "streak"
    p.mkdir()
    subprocess.run(["git", "-C", str(p), "init", "-q"], check=True)
    (p / "studio.toml").write_text('[project]\nname = "streak"\n[rebuild]\nrepo = "."\n')
    return p


def test_import_writes_the_record_and_it_resumes(tmp_path: Path) -> None:
    mod = _script()
    proj = _project(tmp_path)
    rec = json.loads(FIXTURE.read_text())
    rep = mod.plan_import(proj, rec)
    assert rep["written"] and rep["plan"] == "p2"
    # the fixture's commits are not in this scratch repository: reported, not pinned
    assert set(rep["missing"]) >= {"scaffold", "auth", "L0", "L1"} and rep["pinned"] == {}
    store = st.StateStore(Path(rep["state_dir"]))
    ((got, priv),) = store.load("plans")
    assert got["id"] == "p2" and got["status"] == "partial"
    assert priv["scope"]["kind"] == "all" and priv["planner"] == "agent"
    assert got["timeline"][-1]["event"].startswith("imported from a pre-v1.10")
    v = resume_view(got)
    assert v["kind"] == "land" and v["from_level"] == 1 and v["will_skip"] == ["scaffold", "auth"]
    # ids never repeat: the next plan after the imported p2 is p3 or later
    assert store.allocate("plans") >= 3


def test_import_pins_existing_commits_and_never_overwrites(tmp_path: Path) -> None:
    mod = _script()
    proj = _project(tmp_path)

    def run(*a: str) -> str:
        r = subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True, text=True)
        return r.stdout.strip()

    (proj / "f").write_text("x")
    run("add", "f")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    sha = run("rev-parse", "HEAD")
    rec = json.loads(FIXTURE.read_text())
    rec["nodes"][0]["commit"] = sha
    rep = mod.plan_import(proj, rec, dry_run=True)
    assert rep["pinned"] == {"scaffold": sha} and not rep["written"]
    assert not (Path(rep["state_dir"]) / "plans" / "p2.json").exists()
    rep = mod.plan_import(proj, rec)
    assert run("rev-parse", "refs/lattice/plans/p2/scaffold") == sha
    with pytest.raises(SystemExit):
        mod.plan_import(proj, rec)  # the file exists now


def test_a_running_plan_is_refused(tmp_path: Path) -> None:
    mod = _script()
    rec = {**json.loads(FIXTURE.read_text()), "status": "running"}
    with pytest.raises(SystemExit):
        mod.plan_import(_project(tmp_path), rec)
