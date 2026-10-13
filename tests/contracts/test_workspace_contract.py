"""The layer 3 keys of the workspace manifest (lattice/workspace.py): ``contract`` on a link, the
project's ``spec``/``app``/``db``/``login``/``src``/``env``; and that a manifest without them is
printed exactly as before (workspace_old_output.json holds the output of the layer 2 code)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice import workspace as ws
from lattice.cli import main as lattice_main
from lattice.contracts.__main__ import main as contracts_main

from .helpers import World

OLD = json.loads((Path(__file__).parent / "workspace_old_output.json").read_text())
CASES = {
    "show": ["show"],
    "show_json": ["show", "--json"],
    "validate": ["validate"],
    "validate_json": ["validate", "--json"],
    "impact": ["impact", "shared-auth"],
    "impact_json": ["impact", "shared-auth", "--json"],
}


@pytest.mark.parametrize("label", ["good", "messy"])
@pytest.mark.parametrize("case", list(CASES))
def test_manifests_without_layer3_keys_print_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str, case: str
) -> None:
    root = tmp_path.resolve()
    for d in ("billing", "web", "auth"):
        (root / d).mkdir()
    (root / ws.MANIFEST).write_text(OLD[label]["manifest"])
    want = OLD[label]["cases"][case]
    rc = ws.main([*CASES[case], "--workspace", str(root)])
    got = capsys.readouterr().out.replace(str(root), "{root}")
    assert (rc, got) == (want["rc"], want["out"])


def test_to_dict_has_the_new_keys_only_when_set(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / ws.MANIFEST).write_text(
        '[[project]]\nname = "a"\npath = "a"\n\n[[project]]\nname = "b"\npath = "a"\n'
        'spec = "spec.tickets:t5"\napp = "m:make"\ndb = "s:db"\nlogin = "s:login"\n'
        'src = "src"\nenv = { K = "$PROJECT/x" }\n\n'
        '[[link]]\nfrom = "a"\nto = "b"\nkind = "uses"\n\n'
        '[[link]]\nfrom = "b"\nto = "a"\nkind = "uses"\ncontract = "live"\n'
        'live_url = "http://127.0.0.1:9"\n'
    )
    w = ws.parse(tmp_path / ws.MANIFEST)
    a, b = w.to_dict()["projects"]
    assert set(a) == {"name", "path", "role"}
    assert b["spec"] == "spec.tickets:t5" and b["env"] == {"K": "$PROJECT/x"}
    assert {k: b[k] for k in ("app", "db", "login", "src")} == {
        "app": "m:make", "db": "s:db", "login": "s:login", "src": "src"
    }  # fmt: skip
    wp = w.project("b")
    assert wp is not None and wp.expanded_env() == {"K": f"{wp.path}/x"}
    plain, opted = w.to_dict()["links"]
    assert set(plain) == {"from", "to", "kind", "note"}
    assert opted["contract"] == "live" and opted["live_url"] == "http://127.0.0.1:9"
    assert w.problems == [] and ws.Workspace.to_dict(w)["problems"] == []


@pytest.mark.parametrize("bad", ['"yes"', "true", "1", '""', '["static"]', '"Static"'])
def test_bad_contract_is_an_error_and_the_link_stays_listed(tmp_path: Path, bad: str) -> None:
    w = World(tmp_path)
    (tmp_path / ws.MANIFEST).write_text(
        (tmp_path / ws.MANIFEST).read_text().replace('contract = "static"', f"contract = {bad}")
    )
    parsed = ws.parse(tmp_path / ws.MANIFEST)
    assert [p.code for p in parsed.errors] == ["bad_contract"]
    assert len(parsed.links) == 1 and parsed.links[0].contract is None
    assert ws.main(["validate", "--workspace", str(tmp_path)]) == 1
    rc, out = w.check()  # a manifest with an error: nothing is checked
    assert rc == 2 and out["links"] == []
    assert [p["code"] for p in out["problems"]] == ["bad_contract"]


def test_bad_project_settings_are_bad_type(tmp_path: Path) -> None:
    (tmp_path / ws.MANIFEST).write_text(
        '[[project]]\nname = "a"\npath = "."\nspec = 5\napp = ""\n'
        'env = { K = 1 }\n\n[[project]]\nname = "b"\npath = "."\nenv = "x"\nsrc = ["s"]\n'
    )
    parsed = ws.parse(tmp_path / ws.MANIFEST)
    assert [p.code for p in parsed.errors] == ["bad_type"] * 5
    assert [p.name for p in parsed.projects] == ["a", "b"]  # still listed, key dropped
    assert parsed.projects[0].spec is None and parsed.projects[1].env is None


def test_show_prints_contract_only_when_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    World(tmp_path)
    assert ws.main(["show", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "cons --uses--> prov  [contract: static]" in out
    assert "spec = spec:SPEC" in out


def test_absent_contract_never_loads_the_provider_spec(tmp_path: Path) -> None:
    w = World(tmp_path, contract=None)
    (w.provider / "spec.py").write_text('raise RuntimeError("not loaded")\n')
    rc, out = w.check()
    assert rc == 0 and out["checked"] == 0 and out["links"] == []
    # the same link, opted in: the spec is loaded and fails
    w.contract = "static"
    w.write_manifest()
    rc, out = w.check()
    assert rc == 2 and out["links"][0]["error"]["code"] == "export_failed"
    assert "not loaded" in out["links"][0]["error"]["message"]


def test_check_with_nothing_opted_in_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    World(tmp_path, contract=None)
    assert contracts_main(["check", "--workspace", str(tmp_path)]) == 0
    assert "0 link(s) checked" in capsys.readouterr().out


def test_lattice_cli_dispatches_contracts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = World(tmp_path)
    where = ["--workspace", str(tmp_path)]
    assert lattice_main(["contracts", "pin", "cons", *where]) == 0
    assert w.lock.is_file()
    capsys.readouterr()
    assert lattice_main(["contracts", "check", *where, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 1
    assert lattice_main(["contracts", "export", "prov", *where]) == 0
    assert json.loads(capsys.readouterr().out)["spec_ref"] == "spec:SPEC"
    assert lattice_main(["contracts", "check", "--workspace", str(tmp_path / "nowhere")]) == 2
