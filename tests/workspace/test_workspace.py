"""The workspace manifest (lattice/workspace.py, multi-project layer 2): parsing, each problem
class, finding the manifest, impact over links, and the CLI (MP-3)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lattice import workspace as ws
from lattice.cli import main as lattice_main

GOOD = """[workspace]
name = "shop"

[[project]]
name = "billing-api"
path = "billing"
role = "provider"

[[project]]
name = "web-app"
path = "web"
role = "consumer"

[[project]]
name = "shared-auth"
path = "auth"
role = "library"

[[link]]
from = "web-app"
to = "billing-api"
kind = "uses"
note = "POST /invoices"

[[link]]
from = "web-app"
to = "shared-auth"
kind = "depends_on"

[[link]]
from = "billing-api"
to = "shared-auth"
kind = "depends_on"
"""


def manifest(d: Path, text: str = GOOD, dirs: tuple[str, ...] = ("billing", "web", "auth")) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for x in dirs:
        (d / x).mkdir(exist_ok=True)
    p = d / ws.MANIFEST
    p.write_text(text)
    return p


def codes(w: ws.Workspace) -> list[str]:
    return [p.code for p in w.problems]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ws.ENV, raising=False)


# ---------------------------------------------------------------- parsing


def test_a_provider_a_consumer_and_a_library(tmp_path: Path) -> None:
    w = ws.load(manifest(tmp_path))
    assert w.name == "shop" and w.problems == []
    assert w.names() == ["billing-api", "web-app", "shared-auth"]
    assert [p.role for p in w.projects] == ["provider", "consumer", "library"]
    assert w.project("web-app").path == (tmp_path / "web").resolve()
    assert w.by_path(tmp_path / "billing").name == "billing-api"
    assert [(lk.source, lk.target, lk.kind) for lk in w.links] == [
        ("web-app", "billing-api", "uses"),
        ("web-app", "shared-auth", "depends_on"),
        ("billing-api", "shared-auth", "depends_on"),
    ]
    assert w.links[0].note == "POST /invoices"
    assert w.to_dict()["links"][0] == {
        "from": "web-app", "to": "billing-api", "kind": "uses", "note": "POST /invoices",
    }  # fmt: skip


def test_impact_is_transitive_over_links(tmp_path: Path) -> None:
    w = ws.load(manifest(tmp_path))
    assert set(w.impact_of("shared-auth")) == {"web-app", "billing-api"}
    assert w.impact_of("web-app") == []
    assert w.impact_of("billing-api") == ["web-app"]
    with pytest.raises(KeyError):
        w.impact_of("nope")


def test_impact_follows_a_chain_and_survives_uses_cycles(tmp_path: Path) -> None:
    text = """[workspace]
name = "chain"
[[project]]
name = "a"
path = "a"
[[project]]
name = "b"
path = "b"
[[project]]
name = "c"
path = "c"
[[project]]
name = "d"
path = "d"
[[link]]
from = "b"
to = "a"
kind = "depends_on"
[[link]]
from = "c"
to = "b"
kind = "uses"
[[link]]
from = "d"
to = "c"
kind = "uses"
[[link]]
from = "c"
to = "d"
kind = "uses"
"""
    w = ws.load(manifest(tmp_path, text, ("a", "b", "c", "d")))
    assert w.impact_of("a") == ["b", "c", "d"], "three hops, breadth first"
    assert w.impact_of("d") == ["c"], "c relies on d (and d on c: the cycle ends there)"
    assert "a" not in w.impact_of("b") and w.impact_of("c") == ["d"]


ERRORS = {
    "unknown_project": GOOD + '\n[[link]]\nfrom = "web-app"\nto = "ghost"\nkind = "uses"\n',
    "duplicate_name": GOOD + '\n[[project]]\nname = "web-app"\npath = "web2"\n',
    "self_link": GOOD + '\n[[link]]\nfrom = "web-app"\nto = "web-app"\nkind = "uses"\n',
    "depends_on_cycle": GOOD
    + '\n[[link]]\nfrom = "shared-auth"\nto = "web-app"\nkind = "depends_on"\n',
}


@pytest.mark.parametrize("code", sorted(ERRORS))
def test_each_error_class_is_rejected_with_its_own_message(tmp_path: Path, code: str) -> None:
    p = manifest(tmp_path, ERRORS[code])
    w = ws.parse(p)
    errs = [x for x in w.problems if x.severity == ws.ERROR]
    assert [x.code for x in errs] == [code], w.problems
    with pytest.raises(ws.ManifestInvalid) as ei:
        ws.load(p)
    assert [x.code for x in ei.value.problems if x.severity == ws.ERROR] == [code]
    messages = {
        "unknown_project": "ghost is not a declared project",
        "duplicate_name": "web-app is declared twice",
        "self_link": "cannot link to itself",
        "depends_on_cycle": "depends_on links form a cycle",
    }
    assert messages[code] in errs[0].message
    assert messages[code] in str(ei.value)


def test_the_cycle_message_names_the_cycle(tmp_path: Path) -> None:
    w = ws.parse(manifest(tmp_path, ERRORS["depends_on_cycle"]))
    msg = next(p.message for p in w.problems if p.code == "depends_on_cycle")
    assert msg.endswith(
        ("web-app -> shared-auth -> web-app", "shared-auth -> web-app -> shared-auth")
    )


def test_a_uses_cycle_is_allowed(tmp_path: Path) -> None:
    text = GOOD + '\n[[link]]\nfrom = "billing-api"\nto = "web-app"\nkind = "uses"\n'
    assert ws.load(manifest(tmp_path, text)).problems == []


def test_a_missing_path_is_a_warning_not_an_error(tmp_path: Path) -> None:
    p = manifest(tmp_path, dirs=("billing", "web"))
    w = ws.load(p)  # does not raise
    assert codes(w) == ["missing_path"] and w.warnings[0].severity == "warning"
    assert "shared-auth" in w.warnings[0].message
    assert not w.project("shared-auth").exists
    assert set(w.impact_of("shared-auth")) == {"web-app", "billing-api"}


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ('[[project]]\nname = "Bad Name"\npath = "x"\n', "bad_name"),
        ('[[project]]\nname = "x"\npath = "x"\nrole = "boss"\n', "bad_role"),
        ('[[project]]\nname = "x"\n', "missing_field"),
        (
            '[[project]]\nname = "x"\npath = "x"\n[[link]]\nfrom = "x"\nto = "x"\nkind = "calls"\n',
            "bad_kind",
        ),
        ('project = "x"\n', "bad_type"),
    ],
)
def test_other_problems(tmp_path: Path, text: str, code: str) -> None:
    assert code in codes(ws.parse(manifest(tmp_path, text, ("x",))))


def test_unreadable_and_not_toml(tmp_path: Path) -> None:
    with pytest.raises(ws.ManifestUnreadable):
        ws.parse(tmp_path / "absent.toml")
    bad = manifest(tmp_path, "this is = not [toml")
    with pytest.raises(ws.ManifestUnreadable, match="not valid TOML"):
        ws.parse(bad)
    w, probs = ws.try_load(bad)
    assert w is None and [p.code for p in probs] == ["unreadable"]
    assert ws.try_load(None) == (None, [])


def test_relative_paths_resolve_against_the_manifest_and_absolute_ones_work(
    tmp_path: Path,
) -> None:
    other = tmp_path / "elsewhere" / "proj"
    other.mkdir(parents=True)
    text = f'[[project]]\nname = "far"\npath = "{other}"\n'
    text += '[[project]]\nname = "near"\npath = "../near"\n'
    (tmp_path / "near").mkdir()
    w = ws.load(manifest(tmp_path / "ws", text, ()))
    assert w.project("far").path == other.resolve()
    assert w.project("near").path == (tmp_path / "near").resolve()
    assert w.name == "ws", "no [workspace] name: the manifest's directory"


# ---------------------------------------------------------------- finding


def test_find_walks_up_then_env_then_explicit(tmp_path: Path, monkeypatch) -> None:
    p = manifest(tmp_path / "top")
    deep = tmp_path / "top" / "web" / "src" / "pkg"
    deep.mkdir(parents=True)
    assert ws.find(deep) == p.resolve()
    assert ws.find(tmp_path) is None
    other = manifest(tmp_path / "other")
    monkeypatch.setenv(ws.ENV, str(other.parent))  # a directory: the manifest inside it
    assert ws.find(deep) == other
    assert ws.find(deep, explicit=p) == p, "explicit wins over the environment"
    monkeypatch.setenv(ws.ENV, "")
    assert ws.find(deep) == p.resolve(), "an empty variable is unset"


# ---------------------------------------------------------------- CLI


def run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lattice.workspace", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
        env={**__import__("os").environ, ws.ENV: ""},
    )  # fmt: skip


def test_cli_validate_show_impact_exit_codes(tmp_path: Path) -> None:
    manifest(tmp_path)
    r = run_cli(tmp_path, "validate")
    assert r.returncode == 0 and "ok (3 project(s), 3 link(s))" in r.stdout
    r = run_cli(tmp_path / "web", "show")
    assert r.returncode == 0 and "billing-api" in r.stdout and "--uses-->" in r.stdout
    r = run_cli(tmp_path, "impact", "shared-auth", "--json")
    assert r.returncode == 0
    assert set(json.loads(r.stdout)["impacted"]) == {"web-app", "billing-api"}
    r = run_cli(tmp_path, "impact", "web-app")
    assert r.returncode == 0 and "no other project" in r.stdout
    r = run_cli(tmp_path, "impact", "nope")
    assert r.returncode == 1 and "no project 'nope'" in r.stderr


@pytest.mark.parametrize("code", sorted(ERRORS))
def test_cli_validate_rejects_each_error_class(tmp_path: Path, code: str) -> None:
    manifest(tmp_path, ERRORS[code])
    r = run_cli(tmp_path, "validate", "--json")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False and code in [p["code"] for p in out["problems"]]


def test_cli_cannot_read_is_exit_2(tmp_path: Path) -> None:
    r = run_cli(tmp_path, "validate")
    assert r.returncode == 2 and "no lattice.workspace.toml found" in r.stderr
    (tmp_path / ws.MANIFEST).write_text("[[[")
    assert run_cli(tmp_path, "show").returncode == 2
    assert run_cli(tmp_path, "--workspace", str(tmp_path / "missing.toml"), "show").returncode == 2


def test_lattice_workspace_dispatches(tmp_path: Path, capsys, monkeypatch) -> None:
    p = manifest(tmp_path)
    assert lattice_main(["workspace", "validate", "--workspace", str(p)]) == 0
    assert lattice_main(["workspace", "--workspace", str(p), "impact", "billing-api"]) == 0
    out = capsys.readouterr().out
    assert "billing-api changes -> web-app" in out
    manifest(tmp_path, ERRORS["self_link"])
    assert lattice_main(["workspace", "validate", "--workspace", str(p)]) == 1
