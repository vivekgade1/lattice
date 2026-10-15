"""``--engine-file`` and ``--modules`` on ``lattice spec ask`` and ``draft``, registered
before ``--database`` is read, with check's own error lines (marketplace prereg 2.7; M-flag).
In-process, no model: a parse error or a preload problem returns before any LLM call."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from lattice.modules import install
from lattice.spec import ask as ask_mod
from lattice.spec import draft as draft_mod
from lattice.spec.__main__ import _parser as check_parser
from lattice.spec.__main__ import _preload_engines
from lattice.spec.__main__ import main as spec_main
from lattice.spec.engines import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
MARIADB = ROOT / "docs/examples/engines/mariadb.toml"
BASE = ["--spec", "x:SPEC"]


@pytest.fixture(autouse=True)
def _scoped_registry():
    with REGISTRY.scoped():
        yield


def run(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as exc:  # a parser that exits
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def ask_argv(*extra: str) -> list[str]:
    return ["ask", "q?", *BASE, *extra]


def draft_argv(*extra: str) -> list[str]:
    return ["draft", "REQ.md", "--out", "o.py", *extra]


CMDS = {
    "ask": (lambda a: spec_main(a), ask_argv),
    "draft": (lambda a: spec_main(a), draft_argv),
}


def test_help_lists_the_engine_as_a_database_choice(tmp_path):
    for name in ("ask", "draft"):
        main, argv = CMDS[name]
        rc, out, _ = run(main, argv("--engine-file", str(MARIADB), "--help"))
        assert rc == 0
        flat = " ".join(out.split())
        assert "--database {sqlite,postgres,mysql,clickhouse,mariadb}" in flat
        assert "--engine-file FILE" in flat and "--modules DIR" in flat
        REGISTRY.remove("mariadb")


def test_the_parser_reads_the_flags_after_the_preload():
    assert _preload_engines(["--engine-file", str(MARIADB)]) is None
    for parser, argv in (
        (ask_mod._parser, ["q?", *BASE]),
        (draft_mod._parser, ["REQ.md", "--out", "o.py"]),
    ):
        ns = parser().parse_args([*argv, "--database", "mariadb", "--engine-file", str(MARIADB)])
        assert ns.database == "mariadb" and ns.engine_file == [str(MARIADB)]
    ns = check_parser().parse_args(
        ["check", *BASE, "--app", "a:b", "--db", "c:d", "--database", "mariadb"]
    )
    assert ns.database == "mariadb"


@pytest.mark.parametrize("name", ["ask", "draft"])
def test_problems_have_checks_own_line_and_exit_2(tmp_path, name):
    main, argv = CMDS[name]
    bad = tmp_path / "bogus.toml"
    bad.write_text(MARIADB.read_text() + "\nbogus = 1\n")
    taken = tmp_path / "mysql.toml"
    taken.write_text(MARIADB.read_text().replace('name = "mariadb"', 'name = "mysql"'))
    cases = [
        ["--engine-file", str(bad)],
        ["--engine-file", str(tmp_path / "missing.toml")],
        ["--engine-file", str(taken)],
        ["--engine-file=" + str(bad)],
    ]
    for extra in cases:
        rc, out, err = run(main, argv(*extra))
        check_rc, _o, check_err = run(
            spec_main, ["check", *BASE, "--app", "a:b", "--db", "c:d", *extra]
        )
        assert rc == check_rc == 2, err
        assert err.splitlines()[0].startswith("error: EngineError: ")
        assert err.splitlines()[0] == check_err.splitlines()[0]


@pytest.mark.parametrize("name", ["ask", "draft"])
def test_a_tampered_module_is_refused_like_checks(tmp_path, name):
    main, argv = CMDS[name]
    project = tmp_path / "proj"
    project.mkdir()
    mod = tmp_path / "m"
    mod.mkdir()
    (mod / "lattice-module.toml").write_text(
        'name = "m"\nversion = "1"\nkind = "engine"\ndescriptor = "engine.toml"\n'
    )
    (mod / "engine.toml").write_text(MARIADB.read_text())
    install(project, str(mod), yes=True, out=lambda s: None)
    rc, _out, err = run(main, argv("--modules", str(project)))
    assert rc != 2 or "ModuleError" not in err  # a good module loads (the run then needs a model)
    REGISTRY.remove("mariadb")
    (project / ".lattice/modules/m/engine.toml").write_text(MARIADB.read_text() + "\n# x\n")
    rc, _out, err = run(main, argv("--modules", str(project)))
    _, _, check_err = run(
        spec_main, ["check", *BASE, "--app", "a:b", "--db", "c:d", "--modules", str(project)]
    )
    assert rc == 2
    assert err.splitlines()[0].startswith("error: ModuleError: module 'm' (1) is changed")
    assert err.splitlines()[0] == check_err.splitlines()[0]


@pytest.mark.parametrize("name", ["ask", "draft"])
def test_a_database_not_registered_is_an_invalid_choice(name):
    main, argv = CMDS[name]
    rc, _out, err = run(main, argv("--database", "mariadb"))
    assert rc == 2 and "invalid choice: 'mariadb'" in err
