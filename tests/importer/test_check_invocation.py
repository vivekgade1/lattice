"""Harness discovery from the project's own check invocation
(``lattice.importer.check_invocation``, workflow layer round 2, R2-D1 and R2-D2).

Every case is a tiny project under ``fixtures/check_invocation/``: a ``check.sh`` (or a gate
file, or a ``studio.toml``) plus a small harness module. Discovery reads the text and imports
the named references; it never runs a script. Needs neither Go nor Docker."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from lattice.importer import check_invocation as CI
from lattice.importer import workflow_run as R

FIXTURES = Path(__file__).parent / "fixtures" / "check_invocation"
SOURCES = ("studio.toml", "check.sh", "harness.py")


@pytest.fixture(autouse=True)
def _forget_fixture_modules():
    """Fixture projects share module names (``harness``): none may leak into another."""
    names = ("harness", "seedmod")
    for n in names:
        sys.modules.pop(n, None)
    yield
    for n in names:
        sys.modules.pop(n, None)


def find(name: str, tmp_path: Path) -> CI.Discovery:
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    return CI.discover(root)


# ---- reading the text -----------------------------------------------------------------------


def test_commands_split_outside_quotes_and_comments_and_continuations_go():
    text = (
        "# python -m lattice.spec check --app no:no\n"
        'echo "a; b && c"  # trailing\n'
        "one \\\n  two ; three && four || five | six\n"
    )
    assert CI.split_commands(text) == [
        'echo "a; b && c"',
        "one   two",
        "three",
        "four",
        "five",
        "six",
    ]


def test_an_invocation_in_a_comment_or_a_string_is_not_one():
    assert CI.parse_invocations("# python -m lattice.spec check --app a:b --db c:d\n") == []
    assert CI.parse_invocations('echo "python -m lattice.spec check --app a:b --db c:d"\n') == []


def test_set_dash_dash_is_spliced_into_the_args_placeholder():
    text = (
        'set -- --app m:a --db m:d "$@"\n'
        'python -m lattice.spec check "$@" --json > out.json\n'
        'python -m lattice.spec check "$@"\n'
    )
    got = CI.parse_invocations(text)
    assert len(got) == 2 and got[0].key() == got[1].key()
    assert got[0].flags["app"] == "m:a" and got[0].flags["db"] == "m:d"


def test_the_placeholder_with_no_set_dash_dash_adds_nothing():
    (inv,) = CI.parse_invocations('python -m lattice.spec check --app m:a --db m:d "$@"\n')
    assert inv.flags == {"app": "m:a", "db": "m:d"}


def test_a_later_set_replaces_an_earlier_one():
    text = (
        "set -- --app m:one --db m:d\n"
        "set -- --app m:two --db m:d\n"
        'uv run python -m lattice.spec check "$@"\n'
    )
    (inv,) = CI.parse_invocations(text)
    assert inv.flags["app"] == "m:two"


def test_both_flag_forms_are_read_and_the_last_one_wins():
    (inv,) = CI.parse_invocations(
        "python -m lattice.spec check --app=m:a --db m:d --app m:b --login-path /l\n"
    )
    assert inv.flags == {"app": "m:b", "db": "m:d"} and inv.presence == {"login-path"}


# ---- the sources, in order ------------------------------------------------------------------


def test_streak_form_resolves_through_the_set_dash_dash_list(tmp_path):
    d = find("streak_form", tmp_path)
    assert d.found and d.source == "check.sh" and d.file == "check.sh"
    assert d.refs == {
        "app": "harness:make_app",
        "db": "harness:build_db",
        "login": "harness:login",
        "runtime": "harness:RUNTIME",
        "services": "harness:SERVICES",
    }
    assert d.database == "mysql" and sorted(d.services) == ["scheduler", "worker"]
    assert d.runtime == "a-runtime" and d.login(None, "x") == "token"
    assert d.describe()["resolved"] == ["app", "db", "login", "runtime"]


def test_exec_with_continuations_and_no_database_means_sqlite(tmp_path):
    d = find("exec_continuation", tmp_path)
    assert (
        d.found and d.refs["db"] == "harness:build_seed_db" and d.refs["login"] == "seedmod:login"
    )
    assert d.database == "sqlite" and d.services == {}
    assert d.build_db("p") == ("seed", "p")


def test_the_equals_form_resolves(tmp_path):
    d = find("equals_form", tmp_path)
    assert d.found and d.database == "sqlite" and sorted(d.services) == ["scheduler", "worker"]


def test_runtime_keywords_map_to_the_process_runtime(tmp_path):
    go = find("runtime_go", tmp_path)
    process = find("runtime_process", tmp_path)
    assert go.found and process.found and go.login is None and go.services == {}
    assert go.runtime.framework == "go" and process.runtime.framework != "go"


def test_comments_and_strings_are_not_read_as_the_invocation(tmp_path):
    d = find("comments_and_strings", tmp_path)
    assert d.found and d.refs["app"] == "harness:make_app" and d.refs["db"] == "harness:build_db"


def test_services_are_never_taken_by_name(tmp_path):
    d = find("services_undeclared", tmp_path)
    assert d.found and d.services == {} and d.refs["services"] is None


def test_studio_toml_wins_over_a_disagreeing_check_sh(tmp_path):
    d = find("studio_wins", tmp_path)
    assert d.found and d.source == "studio.toml" and d.file == "studio.toml"
    assert d.refs["db"] == "harness:build_db" and d.database == "mysql" and d.services == {}


def test_a_gate_file_named_in_the_rebuild_table_is_read(tmp_path):
    d = find("gate_file", tmp_path)
    assert d.found and d.source == "check.sh" and d.file == "ci/gate.sh"
    assert d.database == "mysql"


def test_a_check_sh_without_an_invocation_counts_as_absent(tmp_path):
    d = find("no_invocation", tmp_path)
    assert d.found and d.source == "harness.py" and d.file == "harness.py"


def test_a_root_harness_py_is_the_round_one_fallback(tmp_path):
    d = find("harness_py_only", tmp_path)
    assert d.found and d.source == "harness.py" and d.database is None
    assert d.refs["app"] == "harness:make_app" and d.refs["db"] == "harness:build_db"
    assert sorted(d.services) == ["scheduler", "worker"] and d.runtime == "a-runtime"


# ---- unresolvable means static, with the cause ----------------------------------------------


def test_nothing_found_lists_the_four_sources(tmp_path):
    d = find("nothing", tmp_path)
    assert not d.found and d.source is None and d.file is None
    for part in ("studio.toml", "gate file", "check.sh", "harness.py"):
        assert part in (d.reason or "")


@pytest.mark.parametrize(
    ("case", "words"),
    [
        ("dollar_var", ["--app", "$APP", "shell variable"]),
        ("dollar_database", ["--database", "$DB", "shell variable"]),
        ("nope_ref", ["harness:nope", "AttributeError"]),
        ("import_error", ["harness:make_app", "ModuleNotFoundError"]),
        ("login_path_only", ["--login-path", "no --login"]),
        ("mint_token_only", ["--mint-token", "no --login"]),
        ("incomplete_app", ["studio.toml", "lacks app and db"]),
        ("harness_seed_only", ["harness.py", "no build_db"]),
        ("disagree", ["ambiguous", "--db"]),
        ("missing_db", ["no --db"]),
        ("services_not_mapping", ["harness:RUNTIME", "not a mapping"]),
        ("bad_toml", ["studio.toml", "TOMLDecodeError"]),
    ],
)
def test_an_unresolvable_source_is_static_with_its_cause_and_no_fall_through(case, words, tmp_path):
    d = find(case, tmp_path)
    assert not d.found and d.source is None
    for w in words:
        assert w in (d.reason or ""), (case, d.reason)
    # never a traceback, never an absolute path
    assert "Traceback" not in d.reason and str(tmp_path) not in d.reason


def test_a_broken_check_sh_does_not_fall_through_to_a_good_harness_py(tmp_path):
    # `nope_ref` has a perfectly good harness.py next to the unresolvable invocation
    d = find("nope_ref", tmp_path)
    assert (tmp_path / "nope_ref" / "harness.py").is_file() and not d.found


def test_an_incomplete_app_table_does_not_fall_through_to_check_sh(tmp_path):
    d = find("incomplete_app", tmp_path)
    assert not d.found and "check.sh" not in (d.reason or "")


# ---- the database (R2-D2) -------------------------------------------------------------------


def test_the_database_is_the_flag_else_sqlite_else_the_extract(tmp_path):
    assert CI.choose_database("mysql", "auto", "sqlite") == ("mysql", None)
    assert CI.choose_database("sqlite", "auto", "mysql") == ("sqlite", None)
    assert CI.choose_database(None, "auto", "sqlite") == ("sqlite", None)
    assert CI.choose_database(None, "auto", None) == ("mysql", None)


def test_an_explicit_database_wins_over_what_the_project_names():
    assert CI.choose_database("sqlite", "mysql", None) == ("mysql", None)
    assert CI.choose_database("mysql", "sqlite", None) == ("sqlite", None)


def test_postgres_is_found_but_the_run_is_static(tmp_path):
    d = find("postgres", tmp_path)
    assert d.found and d.database == "postgres"
    chosen, why = CI.choose_database(d.database, "auto", None)
    assert chosen is None and "postgres" in (why or "") and "mysql and sqlite" in (why or "")
    assert CI.choose_database("postgres", "sqlite", None) == ("sqlite", None)


# ---- through workflow_run -------------------------------------------------------------------


def test_find_harness_builds_a_harness_from_the_invocation(tmp_path):
    root = tmp_path / "streak_form"
    shutil.copytree(FIXTURES / "streak_form", root)
    h = R.find_harness(root)
    assert h is not None and h.source == "check.sh" and h.file == "check.sh"
    assert h.database == "mysql" and h.make_app(0) == ("app", 0)
    sys.modules.pop("harness", None)
    got, reason = R.discover_harness(tmp_path / "nothing")
    assert got is None and "no harness found" in (reason or "")


def test_discovery_never_runs_the_script(tmp_path):
    root = tmp_path / "streak_form"
    shutil.copytree(FIXTURES / "streak_form", root)
    (root / "check.sh").write_text("touch ran-the-script\n" + (root / "check.sh").read_text())
    before = sorted(p.name for p in root.iterdir())
    assert CI.discover(root).found
    assert sorted(p.name for p in root.iterdir()) == before
    assert not (root / "ran-the-script").exists()
