"""The ``lattice`` command (lattice/cli.py): a thin dispatcher over the ``python -m`` mains."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from lattice import cli

SUBCOMMANDS = ["studio", "check", "spec", "import", "mcp", "gogen", "projects"]


@pytest.mark.parametrize("sub", SUBCOMMANDS)
def test_subcommand_help_exits_zero(sub, capsys):
    argv = [sub, "--help"] if sub != "spec" else ["spec", "lint", "--help"]
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip()


def test_top_level_help_and_version(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for sub in SUBCOMMANDS:
        assert sub in out
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("lattice ")


def test_unknown_subcommand_exits_two(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "usage: lattice" in capsys.readouterr().err
    assert cli.main([]) == 2


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["studio", "a", "b", "--port", "9", "--mock", "fx"], "lattice.studio.__main__"),
        (["mcp", "serve", "--project", "p"], "lattice.mcp.__main__"),
        (["import", ".", "--package", "x"], "lattice.importer.cli"),
        (["gogen", "build", "s", "--profile", "m:a", "--out", "o"], "lattice.gogen.__main__"),
        (["spec", "draft", "REQ.md", "--out", "x.py"], "lattice.spec.__main__"),
        (["spec", "fidelity", "--spec", "s"], "lattice.spec.__main__"),
    ],
)
def test_dispatch_forwards_argv_verbatim(argv, target, monkeypatch):
    import importlib

    seen: list[list[str]] = []
    mod = importlib.import_module(target)
    monkeypatch.setattr(mod, "main", lambda a=None, **_: seen.append(list(a)) or 7)
    assert cli.main(argv) == 7
    assert seen == [argv[1:]]


def _project(tmp_path: Path, name="proj", toml: str | None = None, spec=True) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    if spec:
        (d / "spec.py").write_text("SPEC = None\n")
        (d / "spec.trace.json").write_text("{}")
    if toml is not None:
        (d / "studio.toml").write_text(toml)
    return d


def test_check_fills_flags_from_studio_toml(tmp_path, monkeypatch):
    d = _project(
        tmp_path,
        toml='[app]\napp = "h:make_app"\ndb = "h:build_db"\nlogin = "h:login"\n'
        'runtime = "go"\n[check]\nlayers = "ACD"\ngo_src = "gosrc"\n',
    )
    seen: list[list[str]] = []
    import lattice.spec.__main__ as spec_main

    monkeypatch.setattr(spec_main, "main", lambda a: seen.append(list(a)) or 0)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert cli.main(["check", "--project", str(d), "--json"]) == 0
    (argv,) = seen
    assert argv[0] == "check" and "--json" in argv

    def flag(name):
        return argv[argv.index(name) + 1]

    assert flag("--spec") == "spec:SPEC"
    assert flag("--app") == "h:make_app"
    assert flag("--db") == "h:build_db"
    assert flag("--login") == "h:login"
    assert flag("--runtime") == "go"
    assert flag("--layers") == "ACD"
    assert flag("--go-src") == "gosrc"
    assert str(d.resolve()) in sys.path


def test_check_explicit_flags_win(tmp_path, monkeypatch):
    d = _project(tmp_path, toml='[app]\napp = "h:a"\ndb = "h:d"\n[check]\nlayers = "AB"\n')
    seen: list[list[str]] = []
    import lattice.spec.__main__ as spec_main

    monkeypatch.setattr(spec_main, "main", lambda a: seen.append(list(a)) or 0)
    monkeypatch.setattr(sys, "path", list(sys.path))
    cli.main(["check", f"--project={d}", "--app", "x:y", "--layers=C"])
    (argv,) = seen
    assert argv.count("--app") == 1 and argv[argv.index("--app") + 1] == "x:y"
    assert "--layers=C" in argv and "--layers" not in argv


def test_check_names_what_is_missing(tmp_path, capsys):
    d = _project(tmp_path, toml="[app]\napp = 'h:a'\n", spec=False)
    assert cli.main(["check", "--project", str(d)]) == 2
    err = capsys.readouterr().err
    assert "--spec" in err and "--db" in err and "--app" not in err.replace("--app-dir", "")
    d2 = _project(tmp_path, name="bare")
    assert cli.main(["check", "--project", str(d2)]) == 2
    assert "studio.toml" in capsys.readouterr().err


def test_projects_discovery_matrix(tmp_path, monkeypatch, capsys):
    a = _project(tmp_path, "a", toml='[app]\napp = "m:f"\ndb = "m:d"\n')
    b = _project(tmp_path / "nest", "b")  # spec + trace, no toml
    c = _project(tmp_path, "c", toml="[app\nbroken", spec=False)  # broken toml tolerated
    (tmp_path / "plain").mkdir()
    found = {p.name: p for p in cli.discover_projects([tmp_path])}
    assert set(found) == {"a", "c"}  # children of the root only; nest/b is two levels down
    assert found["a"].configured and not found["c"].configured
    assert [p.name for p in cli.discover_projects([tmp_path / "nest"])] == ["b"]
    assert [p.path for p in cli.discover_projects([a, a])] == [a.resolve()]  # deduplicated
    assert not cli.discover_projects([tmp_path / "plain"])
    assert b.is_dir() and c.is_dir()

    # no roots: the cwd, dogfood/*, and children
    monkeypatch.chdir(tmp_path)
    _project(tmp_path / "dogfood", "d1")
    names = {p.name for p in cli.discover_projects()}
    assert {"a", "c", "d1"} <= names

    assert cli.main(["projects", str(a)]) == 0
    assert "app hooked up" in capsys.readouterr().out


def test_projects_none_exits_zero(tmp_path, capsys):
    assert cli.main(["projects", str(tmp_path)]) == 0
    assert "studio.toml" in capsys.readouterr().out


def test_help_startup_time(capsys):
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-m", "lattice.cli", "--help"], capture_output=True, text=True
    )
    dt = time.perf_counter() - t0
    print(f"lattice --help: {dt:.2f}s")
    assert r.returncode == 0
    code = "import sys, lattice.cli as c; c.main(['--help']); print('polars' in sys.modules)"
    r2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r2.stdout.strip().endswith("False")
