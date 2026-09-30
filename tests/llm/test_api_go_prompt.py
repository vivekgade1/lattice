"""Arm G's system prompt (5b pilot, spec 2026-09-29 lattice-go-pilot): Go handlers on
the auth-only Go platform kit (bench/api_kit_go).

The prompt must be FAIR against arm P's FastAPI prompt: the same task information,
conventions, hints and worked examples, rewritten for Go and the Go kit, and nothing
more. So the shared conventions are asserted section by section against the FastAPI
prompt's; the prompt names only the kit's real API; no Lattice term leaks into it; and
the three worked examples (the FastAPI examples, translated by hand) are gofmt-clean,
build and vet against the kit, and answer the example requests the FastAPI examples
answer (tests/llm/test_api_prompt.py's cases), through the Go proxy as arm G's
solutions are scored.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bench.api_datasets import build_db
from bench.api_model import ApiTask
from bench.api_runner import CaseResults, run_cases_go
from lattice.catalog import API_ERRORS
from lattice.gogen.build import GO_ENV
from lattice.llm.api_prompt import (
    ARM_NOTES,
    BASELINE_ADVICE,
    BASELINE_OWN_ROWS,
    ERROR_BODY_NAMES,
    EXAMPLE_PROBLEMS,
    GO_ADVICE,
    GO_EXAMPLE_NAMES,
    GO_EXAMPLES,
    GO_OWN_ROWS,
    _go_kit_section,
    api_baseline_system_prompt,
    api_go_system_prompt,
    api_task_message,
)

ROOT = Path(__file__).resolve().parents[2]
KIT_GO = ROOT / "bench" / "api_kit_go"
FIXTURES = ROOT / "tests" / "fixtures" / "go_examples"
_GO_FENCE = re.compile(r"```go\n(.*?)```", re.DOTALL)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _sections(prompt: str) -> dict[str, str]:
    """``## Heading`` -> its text (the intro is ``""``)."""
    parts = re.split(r"^## (.+)$", prompt, flags=re.MULTILINE)
    out = {"": parts[0]}
    for heading, body in zip(parts[1::2], parts[2::2], strict=True):
        out[heading.strip()] = body
    return out


def _bullets(section: str) -> list[str]:
    """A section's top-level ``- `` bullets, each flattened to one line."""
    items: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            items.append(line)
        elif items and line.strip() and not line.startswith(("```", "#")):
            items[-1] += " " + line.strip()
    return [_flat(i) for i in items]


GO = api_go_system_prompt(hints=True)
PY = api_baseline_system_prompt(hints=True)


# ------------------------------------------------------------------ shape --


@pytest.mark.parametrize("hints", [True, False])
def test_prompt_is_deterministic(hints: bool) -> None:
    assert api_go_system_prompt(hints=hints) == api_go_system_prompt(hints=hints)


def test_sections_mirror_the_fastapi_prompt_one_for_one() -> None:
    go, py = list(_sections(GO)), list(_sections(PY))
    # only the kit heading differs; every other section is the same, in the same order
    assert [h for h in go if h != "platformkit"] == [h for h in py if h != "platform_kit"]
    assert go.index("platformkit") == py.index("platform_kit") == 1
    assert go == [
        "",
        "platformkit",
        "Conventions",
        "Row-by-row work, formats and uniqueness",
        "Step-by-step logic",
        "Optional values, search, time and ordering",
        "Advice",
        "Examples",
        "Output format",
    ]


def test_hints_toggle_only_the_advice() -> None:
    bare = api_go_system_prompt(hints=False)
    assert "## Advice" in GO and "## Advice" not in bare
    for bullet in GO_ADVICE:
        assert bullet in GO and bullet not in bare
    assert bare == GO.replace("## Advice\n\n" + "\n".join(GO_ADVICE) + "\n\n", "")


def test_output_format_is_one_go_block() -> None:
    assert GO.rstrip().endswith("no prose, no other code blocks.")
    assert "Respond with exactly one ```go code block holding the complete `handlers.go`" in GO


def test_intro_mirrors_the_fastapi_intro_and_is_neutral() -> None:
    go, py = _flat(_sections(GO)[""]), _flat(_sections(PY)[""])
    assert go.startswith("You are an expert backend engineer.")
    shared = (
        "its endpoints are then called over HTTP and their responses (status codes, JSON "
        "bodies and database effects) are compared with the specification, so follow the "
        "task and the conventions below precisely."
    )
    assert shared in go and shared in py
    text = GO.lower()
    for phrase in ("whitelist", "rejected", "before it ever runs", "sandbox", "attack"):
        assert phrase not in text, phrase


# -------------------------------------------- conventions, section by section --


def test_conventions_carry_the_same_facts_as_the_fastapi_prompt() -> None:
    go = _flat(_sections(GO)["Conventions"])
    py = _flat(_sections(PY)["Conventions"])
    assert len(_bullets(_sections(GO)["Conventions"])) == len(
        _bullets(_sections(PY)["Conventions"])
    )
    for fact in (
        # parameters
        "path segments come from the path",
        "come from the query string for GET/DELETE",
        "from the JSON body",
        "for POST/PUT/PATCH",
        # success shapes and statuses
        "a single row is its JSON object (200)",
        '`{"items": [...], "limit": n, "offset": n}` (200)',
        "a created row is its JSON object with status 201",
        "a delete or update without a body is",
        "204",
        # the error envelope and table
        "Invalid input (missing, wrong type or outside its constraints) answers 422 "
        '`{"error": "validation", "detail": [...]}`',
        '"error": "validation", "detail": detail',
        # ownership
        "Rows of a table with an owner column belong to that user; Admin callers may see "
        "and change every row. Columns listed as private never appear in a response.",
        "To hand rows to another user, set the owner column (`UPDATE ... SET owner_id = ?`) "
        "only on rows the caller may change, and answer 404 when no user has the new id.",
        # transactions
        "Everything a request writes succeeds or fails together",
        "only after every check",
        # dates, imports
        "Dates are ISO `YYYY-MM-DD` strings.",
        "Import only the standard library",
        "no network",
    ):
        assert fact in go, fact
        assert fact in py, fact
    table = ", ".join(
        f'`{kind}` {status} (`"{ERROR_BODY_NAMES[kind]}"`)' for kind, status in API_ERRORS.items()
    )
    assert table in go and table in py
    envelope = '{"error": "not_found", "detail": "order"}'
    assert envelope in go and envelope.replace('"', "") in py.replace('"', "")


def test_own_rows_rule_is_the_same_sentence_in_go_terms() -> None:
    assert GO_OWN_ROWS in GO and BASELINE_OWN_ROWS in PY
    for part in (
        "- For the caller's own rows only ('my …' features), filter by",
        "(admins too); the access filter `(owner_id = ? OR ? = 'Admin')` lets superusers "
        "see all rows.",
    ):
        assert part in GO_OWN_ROWS and part in BASELINE_OWN_ROWS, part


def test_transaction_rule_in_go_terms() -> None:
    go = _flat(_sections(GO)["Conventions"])
    # P's "commit only after every check passed" also covers "write once, then a
    # check fails" (sqlite3 rolls back an uncommitted write), so G's rule covers it too
    assert (
        "a request that writes more than once, or writes before a check that can still "
        "fail, runs in one `k.DB.Begin()` transaction"
    ) in go
    assert "calls `tx.Commit()` only after every check has passed" in go
    assert "otherwise each statement commits on its own" in go


def test_empty_list_is_an_empty_array() -> None:
    go = _flat(_sections(GO)["Conventions"])
    assert "An empty list is `[]`, never `null`" in go
    assert "a nil Go slice encodes as `null`, so start every list from `[]T{}`" in go


def test_case_insensitive_search_pages_the_filtered_slice() -> None:
    opt = _flat(_sections(GO)["Optional values, search, time and ordering"])
    for fact in (
        "query without `LIMIT`/`OFFSET` (every other filter still in SQL)",
        "`strings.Contains(strings.ToLower(title), strings.ToLower(q))` in Go",
        "then apply `offset` and `limit` to that filtered slice",
        "compute any total from it",
        "an empty `q` matches every string",
        "This is the one exception to `LIMIT ? OFFSET ?` in SQL.",
    ):
        assert fact in opt, fact


def test_forbidden_constructs_are_stated() -> None:
    go = _flat(_sections(GO)["Conventions"])
    assert "Open no other database connection (use only `k.DB`)" in go
    assert "one SQL statement per `Exec`/`Query`/`QueryRow` call" in go


def test_validation_contract_names_the_exact_422_body() -> None:
    go = _flat(_sections(GO)["Conventions"])
    # the body the example cases check ({"error": "validation"} subset) and FastAPI's
    # handler produces ({"error": "validation", "detail": [<one per error>]})
    assert '422 `{"error": "validation", "detail": [...]}`' in go
    assert "`detail` being a list with one message per invalid parameter" in go
    assert "check every one after authenticating and before any other statement" in go
    assert "func invalid(w http.ResponseWriter, detail ...string)" in GO


def test_shared_reference_sections_keep_the_sql_word_for_word() -> None:
    for heading in (
        "Row-by-row work, formats and uniqueness",
        "Optional values, search, time and ordering",
    ):
        go = _bullets(_sections(GO)[heading])
        py = _bullets(_sections(PY)[heading])
        assert len(go) == len(py), heading
    loops_go = _bullets(_sections(GO)["Row-by-row work, formats and uniqueness"])
    loops_py = _bullets(_sections(PY)["Row-by-row work, formats and uniqueness"])
    assert loops_go[:2] == loops_py[:2]  # window functions, tree walks
    opt_go = _bullets(_sections(GO)["Optional values, search, time and ordering"])
    opt_py = _bullets(_sections(PY)["Optional values, search, time and ordering"])
    assert opt_go[-2:] == opt_py[-2:]  # clearing a column, sort direction
    for fact in (
        "at most 100 rows",
        "at-least-once",
        "the whole value must match",
        "a bad value answers 422",
        "answer 409 with the JSON error body",
    ):
        assert fact in " ".join(loops_go) and fact in " ".join(loops_py), fact
    t2_go = _flat(_sections(GO)["Step-by-step logic"])
    t2_py = _flat(_sections(PY)["Step-by-step logic"])
    assert len(_bullets(_sections(GO)["Step-by-step logic"])) == len(
        _bullets(_sections(PY)["Step-by-step logic"])
    )
    for fact in (
        "Prefer a single SQL statement",
        "a request that runs too long is killed outright with no response at all",
        "so every caller can still rely on its own copy afterward",
        "before the transaction is known to have succeeded",
    ):
        assert fact in t2_go and fact in t2_py, fact


def test_advice_has_the_same_substance_as_the_fastapi_advice() -> None:
    assert len(GO_ADVICE) == len(BASELINE_ADVICE) == 4
    topics = ("- Ownership:", "- Bounds:", "- Errors:", "- Lists and related data:")
    for topic, go, py in zip(topics, GO_ADVICE, BASELINE_ADVICE, strict=True):
        assert go.startswith(topic) and py.startswith(topic)
        assert 0.7 <= len(go) / len(py) <= 1.3, topic
    assert GO_ADVICE[0] == BASELINE_ADVICE[0]  # ownership is all SQL
    go, py = "\n".join(GO_ADVICE), "\n".join(BASELINE_ADVICE)
    for fact in (
        "through a parent",
        "groups by, filters on and aggregates only",
        '"may be aggregated publicly"',
        "never fetch or change a row by id alone",
        "needs both bounds",
        "so out-of-range input answers 422 and never reaches the database",
        "map a missing row to 404",
        "== 0",
        "instead of letting it fail with a 500",
        "with 409, 403 or 400 as the task says",
        "in the order the task lists the checks",
        "`LIMIT ? OFFSET ?` to every list query",
        "100",
        "JOIN / GROUP BY",
        "instead of one query per row",  # the query-count efficiency rule
    ):
        assert fact in go and fact in py, fact


# -------------------------------------------------------------- kit API only --


def _kit_exports() -> tuple[set[str], set[str]]:
    """(package-level exported names, exported Kit fields/methods) of kit.go."""
    src = (KIT_GO / "kit.go").read_text()
    package = set(re.findall(r"^func ([A-Z]\w*)\(", src, re.M))
    package |= set(re.findall(r"^type ([A-Z]\w*) ", src, re.M))
    methods = set(re.findall(r"^func \(k \*Kit\) ([A-Z]\w*)\(", src, re.M))
    methods |= set(re.findall(r"^\t([A-Z]\w*) +\*sql\.DB", src, re.M))
    return package, methods


def test_kit_section_is_the_readme_section() -> None:
    readme = (KIT_GO / "README.md").read_text()
    section = readme.split("<!-- prompt:begin -->", 1)[1].split("<!-- prompt:end -->", 1)[0]
    assert _go_kit_section() == section.strip()
    assert section.strip() in GO


def test_prompt_names_only_the_kits_api() -> None:
    package, methods = _kit_exports()
    used_package = set(re.findall(r"\bplatformkit\.([A-Za-z_]\w*)", GO))
    used_methods = set(re.findall(r"\bk\.([A-Za-z_]\w*)", GO))
    assert used_package <= package, used_package - package
    assert used_methods <= methods, used_methods - methods
    # and it documents all of what a handler uses
    for name in ("k.DB", "k.Authenticate(w, r)", 'k.Authenticate(w, r, "Customer")'):
        assert name in GO, name
    for name in ("k.Caller(r)", "k.RequireRole(c, roles...)", "platformkit.WriteError(w, err)"):
        assert name in GO, name
    assert "func Register(mux *http.ServeMux, k *platformkit.Kit)" in GO
    assert "{ID int64; Role, Email, Name string}" in GO
    for status in ("401", "403", "422", "201", "204"):
        assert status in GO, status
    assert "The kit only authenticates" in GO and "The kit only authenticates" in PY
    assert "(Admin may call every endpoint)" in GO and "(Admin may call every endpoint)" in PY
    assert '{"error": "unauthorized"}' in GO and '{"error": "unauthorized"}' in PY
    assert '{"error": "forbidden"}' in GO and '{"error": "forbidden"}' in PY


@pytest.mark.parametrize("hints", [True, False])
def test_no_lattice_terms(hints: bool) -> None:
    text = api_go_system_prompt(hints=hints)
    assert "lattice" not in text.lower()
    for term in (
        "@endpoint",
        "@step",
        "@procedure",
        "CurrentUser",
        "accessible(",
        "mine(",
        "fetch(",
        "require(",
        "for_each",
        "traverse(",
        "window(",
        "agg.",
        "fn.",
        "col(",
        "X-Lattice",
        "TRACE_SQL",
        "DSL",
        "validator",
    ):
        assert term not in text, term


def test_go_task_message_differs_only_in_the_arm_line() -> None:
    task = ApiTask(id="t01", family="control", spec="Endpoint: GET /widgets.", cases=())
    go, py = api_task_message(task, "go"), api_task_message(task, "baseline")
    assert go.endswith(ARM_NOTES["go"])
    assert go.splitlines()[:-1] == py.splitlines()[:-1]
    assert "lattice" not in ARM_NOTES["go"].lower()


# --------------------------------------------------------------- examples --


def test_examples_work_the_same_three_problems() -> None:
    examples = GO.split("## Examples", 1)[1].split("## Output format", 1)[0]
    positions = [examples.index(p) for p in EXAMPLE_PROBLEMS]
    assert positions == sorted(positions)
    blocks = _GO_FENCE.findall(examples)
    assert [b.rstrip() for b in blocks] == [e.rstrip() for e in GO_EXAMPLES]
    routes = (
        '"GET /addresses"',
        '"PATCH /products/{product_id}/stock"',
        '"POST /products/{product_id}/reviews"',
    )
    for code, route in zip(GO_EXAMPLES, routes, strict=True):
        assert f"mux.HandleFunc({route}," in code


@pytest.mark.parametrize("index", range(3))
def test_example_fixture_is_the_prompt_example(index: int) -> None:
    path = FIXTURES / GO_EXAMPLE_NAMES[index] / "handlers.go"
    assert path.read_text() == GO_EXAMPLES[index]


@pytest.mark.parametrize("index", range(3))
def test_examples_import_only_the_standard_library_and_the_kit(index: int) -> None:
    block = GO_EXAMPLES[index].split("import (", 1)[1].split(")", 1)[0]
    imports = re.findall(r'"([^"]+)"', block)
    assert "platformkit" in imports
    for name in imports:
        assert name == "platformkit" or "." not in name.split("/")[0], name


# The one statement per call, one connection rules hold in the examples too.
@pytest.mark.parametrize("index", range(3))
def test_examples_follow_the_forbidden_rules(index: int) -> None:
    code = GO_EXAMPLES[index]
    assert "sql.Open" not in code and "OpenDB" not in code
    for literal in re.findall(r'"((?:SELECT|INSERT|UPDATE|DELETE)[^"]*)"', code):
        assert ";" not in literal, literal


def _go_env() -> dict[str, str]:
    import os

    return {**os.environ, **GO_ENV}


def _module(example: int, out: Path) -> Path:
    """The example as arm G's solutions are built: handlers.go next to a main.go calling
    platformkit.Serve(Register), in a module that reaches the kit through a replace."""
    out.mkdir(parents=True)
    (out / "go.mod").write_text(
        "module app\n\ngo 1.26.0\n\nrequire platformkit v0.0.0\n\n"
        f"replace platformkit => {KIT_GO}\n"
    )
    shutil.copy(KIT_GO / "go.sum", out / "go.sum")
    (out / "main.go").write_text(
        'package main\n\nimport "platformkit"\n\nfunc main() { platformkit.Serve(Register) }\n'
    )
    shutil.copy(FIXTURES / GO_EXAMPLE_NAMES[example] / "handlers.go", out / "handlers.go")
    return out


def _go(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["go", *args],  # noqa: S607
        cwd=cwd,
        env=_go_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


@pytest.fixture(scope="module")
def example_binaries(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    binaries = []
    for i in range(3):
        mod = _module(i, tmp_path_factory.mktemp(f"goex{i}") / "mod")
        built = _go(["build", "-trimpath", "-buildvcs=false", "-o", "app", "."], mod)
        assert built.returncode == 0, built.stderr
        binaries.append(mod / "app")
    return binaries


@pytest.mark.go
def test_examples_are_gofmt_clean() -> None:
    gofmt = shutil.which("gofmt")
    assert gofmt is not None
    proc = subprocess.run(  # noqa: S603
        [gofmt, "-l", str(FIXTURES)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0 and proc.stdout == "", proc.stdout + proc.stderr


@pytest.mark.go
@pytest.mark.parametrize("index", range(3))
def test_examples_build_and_vet_against_the_kit(index: int, example_binaries: list[Path]) -> None:
    mod = example_binaries[index].parent
    vetted = _go(["vet", "./..."], mod)
    assert vetted.returncode == 0, vetted.stdout + vetted.stderr


@pytest.fixture(scope="module")
def example_tasks(tmp_path_factory: pytest.TempPathFactory) -> list[ApiTask]:
    from tests.llm.test_api_prompt import _cases

    db = tmp_path_factory.mktemp("api_db") / "commerce.db"
    build_db(db)
    return _cases(db)


@pytest.mark.go
@pytest.mark.parametrize("index", range(3))
def test_examples_answer_the_example_requests(
    index: int, example_binaries: list[Path], example_tasks: list[ApiTask]
) -> None:
    """The same cases the FastAPI examples pass (tests/llm/test_api_prompt.py), run
    through the Go proxy with SQL counts as arm G is scored."""
    results: CaseResults = run_cases_go(
        example_binaries[index], example_tasks[index], db_builder=build_db
    )
    assert results.started, results.start_error
    failed = [(c.name, c.reason) for c in results.cases if not c.passed]
    assert not failed, failed
