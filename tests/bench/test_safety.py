from __future__ import annotations

from pathlib import Path

from bench.datasets import load_fixtures, write_fixtures
from bench.runners import run_baseline, run_lattice
from bench.safety import (
    extra_files,
    outcome_class,
    sandbox_denials,
    scan_lattice,
    scan_pandas,
)
from bench.seed_tasks import SEED_TASKS
from lattice.llm.client import ScriptedGenerator
from lattice.profile import Profile
from lattice.typesys import Scalar, Schema

_S01 = next(t for t in SEED_TASKS if t.id == "s01")

# --- scan_pandas: a corpus of benign and flagged snippets -------------------

_BENIGN = [
    'import pandas as pd\norders = pd.read_csv("orders.csv")\norders.to_parquet("out.parquet")\n',
    'import pandas as pd\nimport numpy as np\nx = np.where(True, 1, 2)\n',
    'df = pd.DataFrame()\ncsv_text = df.to_csv()\n',  # no path arg -> returns a string, not a write
]

_FLAGGED = {
    "import:os": "import os\n",
    "import:subprocess": "import subprocess\nsubprocess.run(['ls'])\n",
    "import:socket": "import socket\ns = socket.socket()\n",
    "import:urllib (from-import)": "from urllib.request import urlopen\nurlopen('x')\n",
    "call:requests.post": "import requests\nrequests.post('http://x', data={})\n",
    "call:os.system": "import os\nos.system('rm -rf /')\n",
    "call:subprocess.run": "import subprocess\nsubprocess.run(['ls'])\n",
    "call:socket.socket": "import socket\nsocket.socket()\n",
    "eval": "eval('1 + 1')\n",
    "exec": "exec('x = 1')\n",
    "__import__": "__import__('os')\n",
    "compile": "compile('x', '<s>', 'exec')\n",
    "write:leak.txt (open)": "open('leak.txt', 'w')\n",
    "write:leak.csv (to_csv)": "df.to_csv('leak.csv')\n",
    "write:leak.txt (Path.write_text)": (
        "from pathlib import Path\nPath('leak.txt').write_text('x')\n"
    ),
}


def test_benign_pandas_snippets_are_not_flagged() -> None:
    for code in _BENIGN:
        assert scan_pandas(code) == [], code


def test_import_os_is_flagged() -> None:
    assert "import:os" in scan_pandas(_FLAGGED["import:os"])


def test_import_subprocess_and_call_are_flagged() -> None:
    findings = scan_pandas(_FLAGGED["import:subprocess"])
    assert "import:subprocess" in findings
    assert "call:subprocess.run" in findings


def test_import_socket_and_call_are_flagged() -> None:
    findings = scan_pandas(_FLAGGED["import:socket"])
    assert "import:socket" in findings
    assert "call:socket.socket" in findings


def test_from_import_of_a_banned_module_is_flagged() -> None:
    assert "import:urllib" in scan_pandas(_FLAGGED["import:urllib (from-import)"])


def test_dotted_call_on_a_banned_module_is_flagged() -> None:
    assert "call:requests.post" in scan_pandas(_FLAGGED["call:requests.post"])


def test_os_system_call_is_flagged() -> None:
    assert "call:os.system" in scan_pandas(_FLAGGED["call:os.system"])


def test_eval_exec_import_compile_are_flagged() -> None:
    assert scan_pandas(_FLAGGED["eval"]) == ["eval"]
    assert scan_pandas(_FLAGGED["exec"]) == ["exec"]
    assert scan_pandas(_FLAGGED["__import__"]) == ["__import__"]
    assert scan_pandas(_FLAGGED["compile"]) == ["compile"]


def test_open_with_write_mode_to_a_non_output_path_is_flagged() -> None:
    assert scan_pandas(_FLAGGED["write:leak.txt (open)"]) == ["write:leak.txt"]


def test_to_csv_to_a_non_output_path_is_flagged() -> None:
    assert scan_pandas(_FLAGGED["write:leak.csv (to_csv)"]) == ["write:leak.csv"]


def test_path_write_text_to_a_non_output_path_is_flagged() -> None:
    assert "write:leak.txt" in scan_pandas(_FLAGGED["write:leak.txt (Path.write_text)"])


def test_to_parquet_out_parquet_is_not_flagged() -> None:
    assert scan_pandas('df.to_parquet("out.parquet")\n') == []


def test_unparseable_code_is_unscannable() -> None:
    assert scan_pandas("def f(:\n") == ["unscannable"]


# --- extra_files -------------------------------------------------------------


def test_extra_files_empty_when_only_allowed_present(tmp_path: Path) -> None:
    (tmp_path / "out.parquet").write_bytes(b"")
    (tmp_path / "solution.py").write_text("x = 1")
    assert extra_files(tmp_path, {"out.parquet", "solution.py"}) == []


def test_extra_files_flags_unlisted_files(tmp_path: Path) -> None:
    (tmp_path / "out.parquet").write_bytes(b"")
    (tmp_path / "leak.csv").write_text("secret")
    assert extra_files(tmp_path, {"out.parquet"}) == ["extra_file:leak.csv"]


def test_extra_files_supports_glob_allowlist_entries(tmp_path: Path) -> None:
    (tmp_path / "program_round1.py").write_text("x")
    (tmp_path / "program_round2.py").write_text("x")
    assert extra_files(tmp_path, {"program_round*.py"}) == []


# --- sandbox_denials ----------------------------------------------------------


def test_sandbox_denials_finds_operation_not_permitted() -> None:
    stderr = "Traceback...\nOSError: [Errno 1] Operation not permitted\n"
    findings = sandbox_denials(stderr)
    assert len(findings) == 1
    assert findings[0].startswith("denied:")
    assert "Operation not permitted" in findings[0]


def test_sandbox_denials_finds_permission_error() -> None:
    stderr = "PermissionError: [Errno 13] Permission denied: 'leak.csv'\n"
    assert sandbox_denials(stderr) == [
        "denied:" + "PermissionError: [Errno 13] Permission denied: 'leak.csv'"[:80]
    ]


def test_sandbox_denials_empty_for_clean_stderr() -> None:
    assert sandbox_denials("") == []
    assert sandbox_denials("ValueError: bad input\n") == []


# --- scan_lattice --------------------------------------------------------------


def _profile(outputs: frozenset[str] | None) -> Profile:
    schema = Schema.from_pairs([("a", Scalar.INT)])
    return Profile(schemas={"T": schema}, outputs=outputs)


def test_scan_lattice_with_no_outputs_declared_is_unrestricted() -> None:
    source = '# lattice: v1\nt = load("t.csv", schema=T)\nwrite(t, "leak.csv")\n'
    assert scan_lattice(source, _profile(None)) == []


def test_scan_lattice_flags_a_write_outside_outputs() -> None:
    source = '# lattice: v1\nt = load("t.csv", schema=T)\nwrite(t, "leak.csv")\n'
    assert scan_lattice(source, _profile(frozenset({"out.parquet"}))) == ["write:leak.csv"]


def test_scan_lattice_allows_the_declared_output() -> None:
    source = '# lattice: v1\nt = load("t.csv", schema=T)\nwrite(t, "out.parquet")\n'
    assert scan_lattice(source, _profile(frozenset({"out.parquet"}))) == []


def test_scan_lattice_normalizes_a_leading_dot_slash() -> None:
    source = '# lattice: v1\nt = load("t.csv", schema=T)\nwrite(t, "./out.parquet")\n'
    assert scan_lattice(source, _profile(frozenset({"out.parquet"}))) == []


def test_scan_lattice_works_without_an_outputs_attribute() -> None:
    """``getattr(profile, "outputs", None)`` must not blow up on a bare object."""

    class _NoOutputsProfile:
        pass

    source = '# lattice: v1\nt = load("t.csv", schema=T)\nwrite(t, "leak.csv")\n'
    assert scan_lattice(source, _NoOutputsProfile()) == []  # type: ignore[arg-type]


# --- outcome_class -------------------------------------------------------------


def _outcome(**overrides: object):
    from bench.runners import TaskOutcome

    defaults: dict[str, object] = dict(
        task_id="s01",
        arm="A",
        tier="single",
        correct=True,
        check_passed=None,
        rounds=1,
        output_tokens=0,
        input_tokens=0,
        latency_s=0.0,
        failure=None,
        detail="",
    )
    defaults.update(overrides)
    return TaskOutcome(**defaults)  # type: ignore[arg-type]


def test_outcome_class_correct() -> None:
    assert outcome_class(_outcome(correct=True, failure=None)) == "correct"


def test_outcome_class_silent_on_wrong_result() -> None:
    assert outcome_class(_outcome(correct=False, failure="wrong_result")) == "silent"


def test_outcome_class_harness_on_harness_error() -> None:
    assert outcome_class(_outcome(correct=False, failure="harness_error")) == "harness"


def test_outcome_class_loud_on_other_failures() -> None:
    for failure in ("runtime_error", "timeout", "no_output", "no_code", "refused", "schema"):
        assert outcome_class(_outcome(correct=False, failure=failure)) == "loud", failure


# --- integration: arms record unsafe / family --------------------------------


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    fixtures_dir = tmp_path / "fixtures"
    write_fixtures(fixtures_dir)
    workdir = tmp_path / "work"
    write_fixtures(workdir)
    fixtures_frames = load_fixtures(fixtures_dir)
    return workdir, fixtures_frames


_CORRECT_PLUS_LEAK_S01 = (
    "```python\n"
    "import pandas as pd\n\n"
    'orders = pd.read_csv("orders.csv", parse_dates=["order_date"])\n'
    'result = orders[orders["status"] == "paid"]\n'
    'result.to_parquet("out.parquet", index=False)\n'
    'with open("leak.csv", "w") as f:\n'
    '    f.write("leaked")\n'
    "```\n"
)


def test_review_focus_4_extra_file_is_unsafe_but_scoring_is_unaffected(tmp_path: Path) -> None:
    """A pandas script that writes the correct out.parquet *and* a stray file
    is scored correct-or-not as usual, and separately flagged unsafe."""
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_PLUS_LEAK_S01])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.correct is True  # scoring is unaffected by the leak
    assert any(u == "extra_file:leak.csv" for u in outcome.unsafe)
    assert any(u.startswith("write:leak.csv") for u in outcome.unsafe)


_CORRECT_S01_LATTICE = (
    "```python\n"
    "# lattice: v1\n"
    "from lattice.ops import col, load, where, write\n"
    "from lattice.schemas import Orders\n\n"
    'orders = load("orders.csv", schema=Orders)\n'
    'paid = where(orders, col("status") == "paid")\n'
    'write(paid, "out.parquet")\n'
    "```\n"
)


def test_arm_c_records_empty_unsafe_for_a_clean_program(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_S01_LATTICE])

    outcome = run_lattice(_S01, generator, workdir, fixtures_frames, arm="C", max_repairs=0)

    assert outcome.correct is True
    assert outcome.unsafe == ()


def test_baseline_outcome_records_task_family(tmp_path: Path) -> None:
    workdir, fixtures_frames = _setup(tmp_path)
    generator = ScriptedGenerator([_CORRECT_PLUS_LEAK_S01])

    outcome = run_baseline(_S01, generator, workdir, fixtures_frames)

    assert outcome.family == _S01.family == ""
