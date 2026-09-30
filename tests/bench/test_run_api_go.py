"""Arm G of the 5b Go pilot (bench.runners.run_api_go, bench.go_arm): the model's
handlers.go is screened (goast), built, vetted and run in the sandbox, and scored by the
same hidden cases and scorer as arm B (bench.api_runner.run_cases_go + classify).

Tests that build or run Go carry ``@pytest.mark.go`` (skipped without the toolchain,
tests/conftest.py). The adversarial tests assert that the sandbox actually blocked each
effect, not only that the run failed.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from bench import go_arm
from bench.api_datasets import build_db
from bench.api_tasks import API_TASKS
from bench.runners import run_api_go
from bench.sandbox import deny_reads_under, run_python_script, sandbox_exec_available
from lattice.llm.client import (
    Generation,
    ScriptedGenerator,
    Usage,
    extract_code,
    extract_go_code,
)

TASKS = {t.id: t for t in API_TASKS}
O01 = TASKS["o01"]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "go_examples"

needs_sandbox = pytest.mark.skipif(
    not sandbox_exec_available(), reason="the adversarial checks need macOS sandbox-exec"
)

# ---- a correct o01 (GET /orders/{order_id}, customers see only their own) -----------------

_PRELUDE = """\
package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
%(imports)s
	"platformkit"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

type order struct {
	ID        int64   `json:"id"`
	Status    string  `json:"status"`
	CreatedAt string  `json:"created_at"`
	Total     float64 `json:"total"`
}
"""

_O01_BODY = """
func Register(mux *http.ServeMux, k *platformkit.Kit) {
	mux.HandleFunc("GET /orders/{order_id}", func(w http.ResponseWriter, r *http.Request) {
		user, ok := k.Authenticate(w, r, "Customer")
		if !ok {
			return
		}
		id, err := strconv.ParseInt(r.PathValue("order_id"), 10, 64)
		if err != nil || id < 1 {
			writeJSON(w, 422, map[string]any{"error": "validation", "detail": "order_id"})
			return
		}
%(extra)s		q := "SELECT id, status, created_at, total FROM orders WHERE id = ?"
		args := []any{id}
		if %(owned)s {
			q += " AND customer_id = ?"
			args = append(args, user.ID)
		}
		var o order
		err = k.DB.QueryRow(q, args...).Scan(&o.ID, &o.Status, &o.CreatedAt, &o.Total)
		if errors.Is(err, sql.ErrNoRows) {
			writeJSON(w, 404, map[string]any{"error": "not_found", "detail": "order not found"})
			return
		}
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		writeJSON(w, 200, o)
	})
}
"""


def o01(*, owned: str = 'user.Role != "Admin"', extra: str = "", imports: str = "") -> str:
    return _PRELUDE % {"imports": imports} + _O01_BODY % {"owned": owned, "extra": extra}


O01_CORRECT = o01()
# The ownership filter dropped: every signed-in customer reads every order.
O01_NO_OWNERSHIP = o01(owned="false")
# Does not compile: an undefined identifier.
O01_COMPILE_ERROR = O01_CORRECT.replace("writeJSON(w, 200, o)", "writeJSON(w, 200, ordr)")
# Compiles, but go vet flags it (a Printf verb with no argument).
O01_VET_ERROR = o01(imports='\t"fmt"\n', extra='\t\t_ = fmt.Sprintf("%d")\n')
# Opens its own (uncounted) connection with database/sql.
O01_SQL_OPEN = o01(
    extra='\t\tif db, err := sql.Open("sqlite", "app.db"); err == nil {\n'
    "\t\t\t_ = db.Close()\n\t\t}\n"
)


def fenced(code: str) -> str:
    return f"Here is the file.\n\n```go\n{code}```\n"


@pytest.fixture(scope="module")
def pristine_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("go_arm_db") / "pristine.db"
    build_db(path)
    return path


@pytest.fixture
def db_builder(pristine_db: Path):
    def build(path: Path) -> None:
        shutil.copyfile(pristine_db, path)

    return build


def run(task, texts, workdir, db_builder, **kwargs):
    gen = ScriptedGenerator(texts)
    outcome = run_api_go(task, gen, workdir, db_builder=db_builder, **kwargs)
    return outcome, gen


# ---- the extractor ------------------------------------------------------------------------


def test_extract_go_code_takes_the_first_go_block() -> None:
    text = "intro\n```go\npackage main\n```\nmore\n```go\npackage other\n```\n"
    assert extract_go_code(text) == "package main\n"


def test_extract_go_code_accepts_golang_and_crlf() -> None:
    assert extract_go_code("```golang\r\npackage main\r\n```") == "package main\n"
    assert extract_go_code("```Go\npackage main\n```") == "package main\n"


def test_extract_go_code_prefers_a_go_block_over_a_bare_fence() -> None:
    text = "```\nnot this\n```\n```go\npackage main\n```\n"
    assert extract_go_code(text) == "package main\n"


def test_extract_go_code_falls_back_to_a_bare_fence_like_extract_code() -> None:
    text = "```\npackage main\n```\n"
    assert extract_go_code(text) == extract_code(text) == "package main\n"


def test_extract_go_code_never_takes_another_language() -> None:
    assert extract_go_code("```python\nprint(1)\n```") is None
    assert extract_go_code("no code at all") is None
    # and the Python extractor still ignores Go
    assert extract_code("```go\npackage main\n```") is None


# ---- goast ------------------------------------------------------------------------------------


@pytest.mark.go
def test_goast_measures_like_program_metrics(tmp_path: Path) -> None:
    path = tmp_path / "h.go"
    path.write_text("package main\n\n// a comment\n\nfunc f() int {\n\tx := 1\n\treturn x\n}\n")
    report = go_arm.analyze(path)
    assert report.parse_error == ""
    assert report.lines == 5  # blank lines and whole-line // comments are not counted
    assert report.statements == 3  # the block, the assignment, the return
    assert report.ast_nodes > report.statements
    assert report.chars == len(path.read_text())
    assert report.violations == ()


@pytest.mark.go
def test_goast_reports_a_parse_error_with_lines_only(tmp_path: Path) -> None:
    path = tmp_path / "h.go"
    path.write_text("package main\nfunc {\n")
    report = go_arm.analyze(path)
    assert report.parse_error
    assert report.lines == 2 and report.ast_nodes == 0 and report.statements == 0


@pytest.mark.go
def test_goast_accepts_the_worked_examples() -> None:
    for handlers in sorted(FIXTURES.glob("*/handlers.go")):
        report = go_arm.analyze(handlers)
        assert report.parse_error == "", handlers
        assert report.violations == (), (handlers, report.violations)


@pytest.mark.go
@pytest.mark.parametrize(
    ("snippet", "imports", "needle"),
    [
        ('_, _ = sql.Open("sqlite", "x.db")', "", "sql.Open"),
        ("_ = sql.OpenDB(nil)", "", "sql.OpenDB"),
        ("f := sql.Open\n\t_ = f", "", "sql.Open"),
        ('_, _ = db.Open("sqlite", "x.db")', "", "db.Open"),  # aliased import
        ("_ = k.DB.Driver()", "", ".Driver()"),
        ("var _ driver.Value", '\t"database/sql/driver"\n', "database/sql/driver"),
        ("", '\t_ "modernc.org/sqlite"\n', "modernc.org/sqlite"),
        ("", '\t_ "github.com/mattn/go-sqlite3"\n', "go-sqlite3"),
        ('_, _ = k.DB.Exec("UPDATE a SET x = 1; DELETE FROM b")', "", "more than one statement"),
        (
            '_, _ = k.DB.Exec("UPDATE a SET x = 1;" +\n\t\t" DELETE FROM b")',
            "",
            "more than one statement",
        ),
    ],
)
def test_goast_flags_forbidden_constructs(
    tmp_path: Path, snippet: str, imports: str, needle: str
) -> None:
    sql_import = '\tdb "database/sql"\n' if "db.Open" in snippet else '\t"database/sql"\n'
    code = (
        "package main\n\nimport (\n"
        + sql_import
        + '\t"net/http"\n'
        + imports
        + '\n\t"platformkit"\n)\n\nvar _ = '
        + ("db" if "db.Open" in snippet else "sql")
        + ".ErrNoRows\n\n"
        "func Register(mux *http.ServeMux, k *platformkit.Kit) {\n\t" + snippet + "\n}\n"
    )
    path = tmp_path / "h.go"
    path.write_text(code)
    report = go_arm.analyze(path)
    assert report.parse_error == ""
    assert report.violations, code
    assert needle in report.render_violations()


@pytest.mark.go
@pytest.mark.parametrize(
    "sql_text",
    [
        "SELECT id FROM orders WHERE note = 'a;b'",  # a ; inside a SQL string literal
        "SELECT id FROM orders;",  # one statement with its terminator
        "SELECT id FROM orders; -- trailing comment",
        "done; see you",  # not SQL
    ],
)
def test_goast_allows_single_statements(tmp_path: Path, sql_text: str) -> None:
    path = tmp_path / "h.go"
    path.write_text("package main\n\nvar q = `" + sql_text + "`\n")
    assert go_arm.analyze(path).violations == ()


# ---- the runner ---------------------------------------------------------------------------


@pytest.mark.go
def test_correct_solution_scores_correct(tmp_path: Path, db_builder) -> None:
    outcome, gen = run(O01, [fenced(O01_CORRECT)], tmp_path / "w", db_builder)

    assert outcome.correct is True, outcome.detail
    assert outcome.arm == "G"
    assert outcome.failure is None
    assert outcome.rounds == 1
    assert outcome.failed_cases == ()
    assert outcome.diagnostics_per_round == ((),)
    assert outcome.mean_queries >= 2  # the caller lookup and the order query, counted
    assert outcome.check_passed is None
    # program size from goast (Go's AST), not the Python measure (which is 0 for Go)
    assert outcome.program_ast_nodes > 100
    assert outcome.program_statements > 10
    assert outcome.program_lines > 30
    assert outcome.program_chars == len(O01_CORRECT)
    # the G prompt and the "go" task message
    system, messages = gen.calls[0]
    assert "platformkit" in system
    assert messages[0]["content"].endswith(
        "Write `handlers.go` whose `Register` adds the routes to the kit's `ServeMux`."
    )
    assert (tmp_path / "w" / "program_round1.go").read_text() == O01_CORRECT


@pytest.mark.go
def test_compile_error_is_repaired_with_the_compiler_output(tmp_path: Path, db_builder) -> None:
    outcome, gen = run(
        O01, [fenced(O01_COMPILE_ERROR), fenced(O01_CORRECT)], tmp_path / "w", db_builder
    )

    assert outcome.correct is True, outcome.detail
    assert outcome.rounds == 2
    assert outcome.diagnostics_per_round == (("build_error",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The app failed: build_error. Error output:")
    assert "undefined: ordr" in feedback
    assert "handlers.go:" in feedback
    assert str(tmp_path) not in feedback  # the host path is shown as "."
    assert feedback.endswith("Return the complete corrected handlers.go in one ```go block.")
    assert gen.calls[1][1][-2] == {"role": "assistant", "content": fenced(O01_COMPILE_ERROR)}


@pytest.mark.go
def test_compile_error_never_fixed_is_a_scored_loud_failure(tmp_path: Path, db_builder) -> None:
    outcome, _ = run(O01, [fenced(O01_COMPILE_ERROR)] * 3, tmp_path / "w", db_builder)

    assert outcome.correct is False
    assert outcome.failure == "build_error"  # loud (bench.safety.outcome_class), counted
    assert outcome.rounds == 3
    assert outcome.diagnostics_per_round == (("build_error",),) * 3
    assert "undefined: ordr" in outcome.detail
    assert outcome.program_ast_nodes > 0  # it parses: goast measures it


@pytest.mark.go
def test_vet_findings_alone_never_gate_or_trigger_a_repair(tmp_path: Path, db_builder) -> None:
    """go vet is advisory: a program vet flags still runs and is scored by the cases
    alone, with no extra repair round (arm P has no such chance)."""
    outcome, gen = run(
        O01, [fenced(O01_VET_ERROR), fenced(O01_CORRECT)], tmp_path / "w", db_builder
    )

    assert outcome.correct is True, outcome.detail
    assert outcome.rounds == 1
    assert len(gen.calls) == 1
    assert outcome.diagnostics_per_round == ((),)


@pytest.mark.go
def test_vet_findings_ride_along_on_a_repair_a_visible_failure_triggered(
    tmp_path: Path, db_builder
) -> None:
    crashing_and_vetted = O01_VET_ERROR.replace(
        "writeJSON(w, 200, o)", 'panic("boom: the handler blew up")'
    )
    outcome, gen = run(
        O01, [fenced(crashing_and_vetted), fenced(O01_CORRECT)], tmp_path / "w", db_builder
    )

    assert outcome.correct is True, outcome.detail
    assert outcome.diagnostics_per_round == (("crash",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The app failed: crash. Error output:")
    assert "go vet also reported:" in feedback
    assert "Sprintf" in feedback
    assert feedback.endswith("Return the complete corrected handlers.go in one ```go block.")


@pytest.mark.go
def test_a_build_over_its_time_limit_is_a_scored_build_error(
    tmp_path: Path, db_builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    go_arm.ensure_ready()
    monkeypatch.setattr(go_arm, "BUILD_TIMEOUT_S", 0.001)
    outcome, _ = run(O01, [fenced(O01_CORRECT)], tmp_path / "w", db_builder, max_repairs=0)
    assert outcome.correct is False
    assert outcome.failure == "build_error"  # loud and counted, not a harness error
    assert "time limit" in outcome.detail


@pytest.mark.go
def test_goast_failing_on_the_file_is_a_scored_build_error(
    tmp_path: Path, db_builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    go_arm.ensure_ready()
    monkeypatch.setattr(go_arm, "_goast", lambda: Path("/usr/bin/false"))
    outcome, gen = run(O01, [fenced(O01_CORRECT)] * 2, tmp_path / "w", db_builder, max_repairs=1)
    assert outcome.correct is False
    assert outcome.failure == "build_error"
    assert outcome.diagnostics_per_round == (("build_error",), ("build_error",))
    assert "could not be analyzed" in gen.calls[1][1][-1]["content"]
    assert outcome.program_lines > 30 and outcome.program_ast_nodes == 0


@pytest.mark.go
def test_a_runtime_multi_statement_query_is_refused_by_the_kit(tmp_path: Path, db_builder) -> None:
    """SQL assembled at run time escapes the static screen; the kit refuses it when
    it is prepared, and the handler sees the error (as Python's sqlite3.execute)."""
    code = o01(
        imports='\t"strings"\n',
        extra='\t\tif _, err := k.DB.Exec(strings.Repeat("DELETE FROM orders;", 2)); err == nil {\n'
        '\t\t\tpanic("a multi-statement query ran")\n'
        '\t\t} else if !strings.Contains(err.Error(), "one statement at a time") {\n'
        "\t\t\tpanic(err)\n"
        "\t\t}\n",
    )
    outcome, _ = run(O01, [fenced(code)], tmp_path / "w", db_builder, max_repairs=0)
    assert outcome.correct is True, outcome.detail  # the deletes never ran either


@pytest.mark.go
def test_missing_ownership_filter_scores_silent(tmp_path: Path, db_builder) -> None:
    outcome, gen = run(O01, [fenced(O01_NO_OWNERSHIP)] * 3, tmp_path / "w", db_builder)

    assert outcome.correct is False
    assert outcome.failure == "wrong_result"  # silent: only trap cases failed
    assert outcome.rounds == 1  # trap failures are never surfaced for repair
    assert set(outcome.failed_cases) == {"alice_reads_bobs", "bob_reads_alices"}
    assert len(gen.calls) == 1


@pytest.mark.go
def test_forbidden_sql_open_is_repair_feedback(tmp_path: Path, db_builder) -> None:
    outcome, gen = run(O01, [fenced(O01_SQL_OPEN), fenced(O01_CORRECT)], tmp_path / "w", db_builder)

    assert outcome.correct is True, outcome.detail
    assert outcome.diagnostics_per_round == (("forbidden",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The app failed: forbidden. Error output:")
    assert "sql.Open is not allowed" in feedback
    assert "k.DB" in feedback
    # rejected before building: no binary was made in round 1
    assert not (tmp_path / "w" / "go_round1" / "module" / "app").exists()


@pytest.mark.go
def test_forbidden_sql_open_never_fixed_scores_loud(tmp_path: Path, db_builder) -> None:
    outcome, _ = run(O01, [fenced(O01_SQL_OPEN)] * 3, tmp_path / "w", db_builder)

    assert outcome.correct is False
    assert outcome.failure == "forbidden"  # a scored loud failure, never a harness error
    assert outcome.rounds == 3
    assert "sql.Open" in outcome.detail


@pytest.mark.go
def test_no_code_block_is_repair_feedback(tmp_path: Path, db_builder) -> None:
    outcome, gen = run(
        O01, ["I cannot see a code block here.", fenced(O01_CORRECT)], tmp_path / "w", db_builder
    )
    assert outcome.correct is True
    assert outcome.diagnostics_per_round == (("no_code",), ())
    assert "no ```go code block" in gen.calls[1][1][-1]["content"]


@pytest.mark.go
def test_handler_panic_feedback_carries_the_panic(tmp_path: Path, db_builder) -> None:
    crashing = O01_CORRECT.replace("writeJSON(w, 200, o)", 'panic("boom: the handler blew up")')
    outcome, gen = run(O01, [fenced(crashing), fenced(O01_CORRECT)], tmp_path / "w", db_builder)
    assert outcome.correct is True
    assert outcome.diagnostics_per_round == (("crash",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert feedback.startswith("The app failed: crash. Error output:")
    assert "boom: the handler blew up" in feedback  # the stack the kit logs, as P's traceback


@pytest.mark.go
def test_register_panic_is_a_start_error_with_its_message(tmp_path: Path, db_builder) -> None:
    dup = O01_CORRECT.replace(
        "func Register(mux *http.ServeMux, k *platformkit.Kit) {",
        "func Register(mux *http.ServeMux, k *platformkit.Kit) {\n"
        '\tmux.HandleFunc("GET /x", func(http.ResponseWriter, *http.Request) {})\n'
        '\tmux.HandleFunc("GET /x", func(http.ResponseWriter, *http.Request) {})',
    )
    outcome, gen = run(O01, [fenced(dup), fenced(O01_CORRECT)], tmp_path / "w", db_builder)
    assert outcome.correct is True
    assert outcome.diagnostics_per_round == (("start_error",), ())
    feedback = gen.calls[1][1][-1]["content"]
    assert "the app did not start" in feedback
    assert "GET /x" in feedback  # net/http's panic on the duplicate pattern


# ---- records ----------------------------------------------------------------------------------


class _PricedGenerator:
    """Scripted texts with a transport-reported cost, as the CLI transport's."""

    def __init__(self, texts: list[str]) -> None:
        self.inner = ScriptedGenerator(texts)

    def generate(self, system, messages):
        g = self.inner.generate(system, messages)
        return dataclasses.replace(
            g,
            usage=Usage(1000, 200),
            model="claude-sonnet-5-20260901",
            cost_usd=0.01,
            cost_basis="cli_list_estimate",
        )


@pytest.mark.go
def test_records_carry_cost_and_cost_basis(tmp_path: Path, db_builder) -> None:
    gen = _PricedGenerator([fenced(O01_COMPILE_ERROR), fenced(O01_CORRECT)])
    outcome = run_api_go(O01, gen, tmp_path / "w", db_builder=db_builder)
    assert outcome.correct is True
    assert outcome.cost_usd == pytest.approx(0.02)
    assert outcome.cost_basis == "cli_list_estimate"
    assert outcome.model == "claude-sonnet-5-20260901"
    assert outcome.output_tokens == 400


@pytest.mark.go
def test_cli_records_requested_model_rep_and_cost(tmp_path: Path) -> None:
    """``bench.cli`` runs arm G offline and writes requested_model/model/rep/cost_usd/
    cost_basis on every record, as for the other arms."""
    from bench.cli import main
    from bench.report import read_outcomes

    offline = tmp_path / "offline"
    (offline / "G").mkdir(parents=True)
    (offline / "G" / "o01.txt").write_text(fenced(O01_CORRECT))
    out = tmp_path / "out"
    status = main(
        [
            "run",
            "--suite",
            "api",
            "--arms",
            "G",
            "--tasks",
            "o01",
            "--offline",
            str(offline),
            "--out",
            str(out),
            "--model",
            "claude-sonnet-5",
            "--reps",
            "2",
        ]
    )
    assert status == 0
    records = read_outcomes(out / "outcomes.jsonl")
    assert [(r.arm, r.task_id, r.rep) for r in records] == [("G", "o01", 0), ("G", "o01", 1)]
    for r in records:
        assert r.correct is True, r.detail
        assert r.requested_model == "claude-sonnet-5"
        assert r.model == "claude-sonnet-5"  # scripted: no served model, the requested one
        assert r.cost_usd == 0.0 and r.cost_basis == ""  # scripted calls report no cost
    # per-(model, arm, task, rep) workdirs, no shared paths
    assert (out / "work" / "claude-sonnet-5" / "G" / "o01" / "rep0" / "go_round1").is_dir()
    assert (out / "work" / "claude-sonnet-5" / "G" / "o01" / "rep1" / "go_round1").is_dir()
    assert "G" in (out / "report.md").read_text()


def test_cli_rejects_arm_g_outside_the_api_suite(tmp_path: Path) -> None:
    from bench.cli import main

    with pytest.raises(SystemExit):
        main(
            [
                "run",
                "--suite",
                "seed",
                "--arms",
                "G",
                "--offline",
                str(tmp_path),
                "--out",
                str(tmp_path / "out"),
            ]
        )


# ---- harness errors ---------------------------------------------------------------------------


def test_missing_toolchain_is_a_harness_error_before_any_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lattice.gogen import build as gobuild

    def missing(version=None):
        raise gobuild.GoBuildError("the go toolchain is not installed (go not on PATH)")

    monkeypatch.setattr(gobuild, "check_toolchain", missing)
    gen = ScriptedGenerator([fenced(O01_CORRECT)])
    with pytest.raises(gobuild.GoBuildError):
        run_api_go(O01, gen, tmp_path / "w", db_builder=lambda p: None)
    assert gen.calls == []  # nothing was generated, nothing was paid for


@pytest.mark.go
def test_a_kit_that_does_not_build_is_a_harness_error(tmp_path: Path) -> None:
    kit = tmp_path / "kit"
    shutil.copytree(go_arm.KIT_DIR, kit)
    (kit / "kit.go").write_text((kit / "kit.go").read_text() + "\nfunc broken() { undefined() }\n")
    gen = ScriptedGenerator([fenced(O01_CORRECT)])
    with pytest.raises(go_arm.GoHarnessError, match="does not build on its own"):
        run_api_go(O01, gen, tmp_path / "w", db_builder=lambda p: None, kit_dir=kit)
    assert gen.calls == []


# ---- adversarial solutions: contained by the sandbox ------------------------------------------


def _adversarial(extra: str, imports: str) -> str:
    """o01, correct, but running ``extra`` in the handler first."""
    return o01(extra=extra, imports=imports)


@pytest.mark.go
@needs_sandbox
def test_adversarial_network_socket_is_blocked(tmp_path: Path, db_builder) -> None:
    """The handler dials a listener the test runs on the loopback interface: the
    sandbox denies the outbound connection, so the listener never sees one."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    accepted: list[object] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            accepted.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    code = _adversarial(
        f'\t\tif c, err := net.Dial("tcp", "127.0.0.1:{port}"); err == nil {{\n'
        "\t\t\t_ = c.Close()\n"
        '\t\t\tpanic("network escape: dialed out")\n'
        "\t\t}\n"
        '\t\tif c, err := net.Dial("tcp", "1.1.1.1:80"); err == nil {\n'
        "\t\t\t_ = c.Close()\n"
        '\t\t\tpanic("network escape: dialed the internet")\n'
        "\t\t}\n",
        '\t"net"\n',
    )
    try:
        outcome, _ = run(O01, [fenced(code)], tmp_path / "w", db_builder, max_repairs=0)
    finally:
        stop.set()
        thread.join(2)
        listener.close()
    assert accepted == []  # the sandbox blocked every connection
    # the dials failed inside the handler, which then served the task correctly
    assert outcome.correct is True, outcome.detail


@pytest.mark.go
@needs_sandbox
def test_adversarial_write_outside_the_workdir_is_blocked(tmp_path: Path, db_builder) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "escaped.txt"
    workdir = tmp_path / "w"
    code = _adversarial(
        f'\t\tif err := os.WriteFile("{target}", []byte("x"), 0o644); err == nil {{\n'
        '\t\t\tpanic("filesystem escape")\n'
        "\t\t}\n"
        '\t\tif err := os.WriteFile("inside.txt", []byte("x"), 0o644); err != nil {\n'
        '\t\t\tpanic("cannot write in the run directory: " + err.Error())\n'
        "\t\t}\n",
        '\t"os"\n',
    )
    outcome, _ = run(O01, [fenced(code)], workdir, db_builder, max_repairs=0)
    assert not target.exists()  # blocked
    assert outcome.correct is True, outcome.detail  # and writes in its run dir still work
    assert (workdir / "go_round1" / "run" / "inside.txt").exists()
    # nor can it replace the harness's own files next to the run dir
    assert (workdir / "go_round1" / "launch.py").read_text().startswith("#!")


@pytest.mark.go
def test_adversarial_infinite_loop_is_killed_as_a_timeout(tmp_path: Path, db_builder) -> None:
    spin = "\t\tspin := 0\n\t\tfor spin >= 0 {\n\t\t\tspin = (spin + 1) % 7\n\t\t}\n"
    code = _adversarial(spin, "")
    started = time.monotonic()
    outcome, gen = run(
        O01, [fenced(code)] * 2, tmp_path / "w", db_builder, max_repairs=1, timeout_s=4
    )
    elapsed = time.monotonic() - started
    assert outcome.correct is False
    assert outcome.failure == "timeout"  # loud, as arm P's time-out
    assert outcome.diagnostics_per_round == (("timeout",), ("timeout",))
    feedback = gen.calls[1][1][-1]["content"]
    assert "The app failed: timeout." in feedback
    assert "exceeded the 4s time limit" in feedback
    assert elapsed < 60
    assert _no_process_running(tmp_path / "w")


@pytest.mark.go
def test_adversarial_hang_in_register_is_killed_as_a_timeout(tmp_path: Path, db_builder) -> None:
    """A Register that never returns (so the server never prints LISTENING) is killed
    by the launcher's wall clock: the proxy's wait for the LISTENING line ends."""
    code = O01_CORRECT.replace(
        "func Register(mux *http.ServeMux, k *platformkit.Kit) {",
        "func Register(mux *http.ServeMux, k *platformkit.Kit) {\n"
        "\tspin := 0\n\tfor spin >= 0 {\n\t\tspin = (spin + 1) % 7\n\t}",
    )
    started = time.monotonic()
    outcome, _ = run(O01, [fenced(code)], tmp_path / "w", db_builder, max_repairs=0, timeout_s=3)
    assert outcome.failure == "timeout"
    assert time.monotonic() - started < 60
    assert _no_process_running(tmp_path / "w")


@pytest.mark.go
@needs_sandbox
def test_adversarial_fork_exec_is_blocked(tmp_path: Path, db_builder) -> None:
    marker = tmp_path / "w" / "go_round1" / "run" / "spawned.txt"
    code = _adversarial(
        '\t\tcmd := exec.Command("/bin/sh", "-c", "touch spawned.txt; sleep 600")\n'
        "\t\tif err := cmd.Start(); err == nil {\n"
        '\t\t\tpanic("process escape: spawned a child")\n'
        "\t\t}\n",
        '\t"os/exec"\n',
    )
    outcome, _ = run(O01, [fenced(code)], tmp_path / "w", db_builder, max_repairs=0)
    assert not marker.exists()  # the child never ran
    assert outcome.correct is True, outcome.detail
    assert _no_process_running(tmp_path / "w")
    assert not _sleep_600_running()


def _no_process_running(workdir: Path) -> bool:
    """No process of this run (the binary, its launcher) is still alive."""
    root = str(workdir.resolve())
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    alive = [line for line in out.splitlines() if root in line]
    return alive == []


def _sleep_600_running() -> bool:
    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    return any(line.strip() == "sleep 600" for line in out.splitlines())


@pytest.mark.go
def test_rounds_do_not_share_directories(tmp_path: Path, db_builder) -> None:
    run(O01, [fenced(O01_COMPILE_ERROR), fenced(O01_CORRECT)], tmp_path / "w", db_builder)
    w = tmp_path / "w"
    assert (w / "go_round1" / "module" / "handlers.go").read_text() == O01_COMPILE_ERROR
    assert (w / "go_round2" / "module" / "handlers.go").read_text() == O01_CORRECT
    assert os.access(w / "go_round2" / "module" / "app", os.X_OK)


def test_generation_type_is_unchanged() -> None:
    # guard: the runner relies on Generation's transport-cost fields
    assert {f.name for f in dataclasses.fields(Generation)} >= {"cost_usd", "cost_basis", "model"}


# ---- read confinement (both arms) ---------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
HIDDEN_CASES = REPO / "bench" / "api_tasks.py"


@pytest.mark.go
@needs_sandbox
def test_adversarial_go_cannot_read_the_hidden_cases_or_the_out_dir(
    tmp_path: Path, db_builder
) -> None:
    out = tmp_path / "out"
    secret = out / "outcomes.jsonl"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret\n")
    deny_reads_under(out)
    workdir = out / "work" / "m" / "G" / "o01" / "rep0"
    code = _adversarial(
        f'\t\tif _, err := os.ReadFile("{HIDDEN_CASES}"); err == nil {{\n'
        '\t\t\tpanic("read escape: the hidden cases")\n'
        "\t\t}\n"
        f'\t\tif _, err := os.ReadFile("{secret}"); err == nil {{\n'
        '\t\t\tpanic("read escape: the out dir")\n'
        "\t\t}\n"
        '\t\tif _, err := os.ReadFile("pristine.db"); err != nil {\n'
        '\t\t\tpanic("cannot read its own run directory: " + err.Error())\n'
        "\t\t}\n",
        '\t"os"\n',
    )
    outcome, _ = run(O01, [fenced(code)], workdir, db_builder, max_repairs=0)
    assert outcome.correct is True, outcome.detail  # every forbidden read was denied


@needs_sandbox
def test_adversarial_python_cannot_read_the_hidden_cases_or_the_out_dir(
    tmp_path: Path,
) -> None:
    """The Python arms' sandbox (bench.sandbox.run_python_script, which arm P's
    run_cases_fastapi uses) denies the same reads."""
    out = tmp_path / "out"
    secret = out / "outcomes.jsonl"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret\n")
    deny_reads_under(out)
    workdir = out / "work" / "m" / "P" / "o01" / "rep0"
    workdir.mkdir(parents=True)
    (workdir / "mine.txt").write_text("mine\n")
    script = (
        "results = []\n"
        f"for path in [{str(HIDDEN_CASES)!r}, {str(secret)!r}, 'mine.txt']:\n"
        "    try:\n"
        "        open(path).read()\n"
        "        results.append('read')\n"
        "    except PermissionError:\n"
        "        results.append('denied')\n"
        "print(','.join(results))\n"
    )
    result = run_python_script(script, workdir, timeout_s=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "denied,denied,read"


def test_read_denied_paths_cover_bench_and_tests() -> None:
    from bench.sandbox import read_denied_paths

    denied = read_denied_paths()
    assert REPO / "bench" in denied and REPO / "tests" in denied


# ---- arm B's Go backend runs its binary exactly as arm G's ---------------------------------


@pytest.mark.go
def test_arm_b_go_backend_uses_the_same_sandboxed_budgeted_case_run(
    tmp_path: Path, db_builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench.api_datasets import api_profile
    from bench.runners import run_api_lattice

    calls: list[dict] = []
    real = go_arm.run_cases

    def spy(binary, task, **kwargs):
        calls.append(kwargs)
        return real(binary, task, **kwargs)

    monkeypatch.setattr(go_arm, "run_cases", spy)
    source = Path(__file__).resolve().parents[1] / "fixtures" / "api" / "o01" / "lattice.py"
    gen = ScriptedGenerator([f"```python\n{source.read_text()}```\n"])
    outcome = run_api_lattice(
        O01,
        gen,
        tmp_path / "w",
        db_builder=db_builder,
        profile=api_profile(),
        arm="C",
        max_repairs=0,
        backend="go",
    )
    assert outcome.correct is True, outcome.detail
    assert len(calls) == 1
    assert calls[0]["timeout_s"] == go_arm.DEFAULT_CASE_TIMEOUT_S
    assert calls[0]["round_dir"] == tmp_path / "w" / "go_round1"
    assert (tmp_path / "w" / "go_round1" / "launch.py").exists()  # the same launcher
