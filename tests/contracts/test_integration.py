"""The real pair: ci-bot (dogfood/contracts/ci-bot) against job-scheduler at ef17cf8, cloned
into tmp_path (the real repository is never written; skipped when it is not on this machine)."""

from __future__ import annotations

import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lattice.contracts import check as ck
from lattice.contracts.__main__ import main
from lattice.workspace import MANIFEST

from .helpers import git

REPO = Path(__file__).resolve().parents[2]
PROVIDER = Path("/Users/vivekgade/Documents/job-scheduler")
COMMIT = "ef17cf8"
CI_BOT = REPO / "dogfood" / "contracts" / "ci-bot"
GOLDEN = REPO / "dogfood" / "contracts" / "golden" / ck.LOCK_FILE

pytestmark = pytest.mark.skipif(
    not (PROVIDER / ".git").exists() or not CI_BOT.is_dir(),
    reason="the job-scheduler repository is not on this machine",
)

MANIFEST_TEXT = """\
[workspace]
name = "probe"

[[project]]
name = "scheduler"
path = "scheduler"
role = "provider"
spec = "spec.tickets:t5"
app = "job_scheduler:make_app"
db = "spec.seed:build_seed_db"
login = "spec.seed:login"
src = "src"
env = { JOB_SCHEDULER_REPO_ROOT = "$PROJECT" }

[[project]]
name = "ci-bot"
path = "ci-bot"
role = "consumer"

[[link]]
from = "ci-bot"
to = "scheduler"
kind = "uses"
contract = "static"
"""


@pytest.fixture(scope="module")
def clone(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dst = tmp_path_factory.mktemp("js") / "scheduler"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(PROVIDER), str(dst)], check=True)
    subprocess.run(["git", "-C", str(dst), "checkout", "-q", COMMIT], check=True)
    return dst


@pytest.fixture
def probe(tmp_path: Path, clone: Path) -> Path:
    """A workspace: a fresh copy of the clean clone and a copy of ci-bot."""
    shutil.copytree(clone, tmp_path / "scheduler", symlinks=True)
    shutil.copytree(CI_BOT, tmp_path / "ci-bot")
    (tmp_path / MANIFEST).write_text(MANIFEST_TEXT)
    return tmp_path


def run(root: Path, *argv: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    rc = main([*argv, "--workspace", str(root)])
    return rc, capsys.readouterr().out


def test_clean_export(probe: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = run(probe, "export", "scheduler", capsys=capsys)
    assert rc == 0
    got = json.loads(out)
    export = got["export"]
    eps = export["endpoints"]
    assert got["spec_ref"] == "spec.tickets:t5"
    assert got["version"]["git_ref"].startswith(COMMIT) and got["version"]["dirty"] is False
    assert len(eps) == 20 and "PATCH /applications/{}/jobs/{}" not in eps  # the 405 probe
    assert collections.Counter(v["auth"] for v in eps.values()) == {
        "unknown": 15, "token": 4, "public": 1
    }  # fmt: skip
    assert eps["POST /auth/login"]["auth"] == "public"
    assert export["error_body"] == "fastapi"
    assert not any("detail" in v["response_fields"] for v in eps.values())
    assert "access_token" not in eps["POST /auth/login"]["response_fields"]
    assert eps["POST /applications/{}/jobs/{}/runs"]["statuses"] == [201, 401, 404, 409]


def test_export_is_byte_identical_over_three_runs(probe: Path) -> None:
    outs = [
        subprocess.run(
            [sys.executable, "-m", "lattice.contracts", "export", "scheduler",
             "--workspace", str(probe)],
            capture_output=True, text=True, check=True,
        ).stdout
        for _ in range(3)
    ]  # fmt: skip
    assert outs[0] == outs[1] == outs[2] and len(outs[0]) > 1000


def test_pin_check_clean_with_one_gap(probe: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, _ = run(probe, "pin", "ci-bot", capsys=capsys)
    assert rc == 0
    rc, out = run(probe, "check", "ci-bot", "--json", capsys=capsys)
    link = json.loads(out)["links"][0]
    assert rc == 0 and link["status"] == "ok" and link["imports"] == 7
    assert link["findings"] == [] and link["version_changed"] is False
    assert [(g["kind"], g["endpoint"], g["item"]) for g in link["gaps"]] == [
        ("response_field_unobserved", "POST /auth/login", "access_token")
    ]


def test_the_lock_matches_the_committed_golden(
    probe: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(2):  # twice: the second pin replaces the first, byte for byte
        rc, _out = run(probe, "pin", "ci-bot", capsys=capsys)
        assert rc == 0
        assert (probe / "ci-bot" / ck.LOCK_FILE).read_bytes() == GOLDEN.read_bytes()
    lock = json.loads(GOLDEN.read_text())
    entry = lock["providers"]["scheduler"]
    assert entry["spec_ref"] == "spec.tickets:t5"
    assert entry["version"]["spec_hash"] == ck.spec_hash(entry["export"])
    assert str(probe) not in GOLDEN.read_text()


def test_a_status_drift_in_the_provider_spec_is_broken(
    probe: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(probe, "pin", "ci-bot", capsys=capsys)
    tickets = probe / "scheduler" / "spec" / "tickets.py"
    text = tickets.read_text()
    for user, n in (("eve", 5), ("ann", 6)):  # every 201 of the start-a-run step becomes 200
        old = f'Step("POST", RUNS, "{user}", expect_status=201'
        assert text.count(old) == n
        text = text.replace(old, old.replace("201", "200"))
    old = 'Step("POST", f"{JOBS}/2/runs", "eve", expect_status=201'
    assert text.count(old) == 1
    text = text.replace(old, old.replace("201", "200"))
    tickets.write_text(text)
    git(probe / "scheduler", "commit", "-qam", "201 -> 200")
    rc, out = run(probe, "check", "ci-bot", "--json", capsys=capsys)
    link = json.loads(out)["links"][0]
    runs = "POST /applications/{}/jobs/{}/runs"
    assert rc == 1 and link["status"] == "broken" and link["version_changed"] is True
    assert [(f["class"], f["kind"], f["endpoint"], f["item"]) for f in link["findings"]] == [
        ("BROKEN", "status_not_observed", runs, "201"),
        ("CHANGED", "status_added", runs, "200"),
    ]
