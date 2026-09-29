"""pycheck's accuracy on the edit slice's known cases (round-5 spec 2026-09-28 §3).

* **Precision:** every hand-written reference (t1-t30) checks clean against its app's
  policy, derived from the original app.
* **Recall on the traps** (with pycheck's own derived policy, ``derived_policy``): the
  oracle tests' trap variants (``TRAPS`` in
  tests/bench/test_edit_slice_{oracle,repos,round4}.py); the measured hits are pinned
  below. Most traps are not guarantee violations at all (off-by-one bounds, ``GROUP BY``
  dropping zero rows, atomicity, repeated ids, check order, case folding) and no
  guarantee check can see them; within the six guarantees, the misses are ownership
  evidence that exists but is not applied to the query that answers (a filter replaced,
  widened by ``or``, a skip-instead-of-refuse loop, an explicit admin branch the spec
  allows) -- see :mod:`lattice.pycheck.checks`.
* **The ceiling for arm Q:** how many of round 4's arm-P *silent* runs pycheck would
  have flagged (and that it flags none of P's correct runs); the saved artefacts are
  git-ignored, so the test skips without them.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from bench.edit_slice.qcheck import check_files, derived_policy, repo_policy, start_ownership
from bench.edit_slice.reference import reference_files
from bench.edit_slice.tickets import ROUND4_TICKETS, ticket
from lattice.pycheck import ErrorAnswer

ROOT = Path(__file__).resolve().parents[2]
TRAP_MODULES = ("test_edit_slice_oracle", "test_edit_slice_repos", "test_edit_slice_round4")


# ---- the policies -------------------------------------------------------------------------


def test_the_policies_derived_from_the_three_apps():
    """pycheck's own derivation (every fact from the original code). Arm Q's policy
    takes its ownership facts from arm L's start profile instead (below)."""
    template = derived_policy("template")
    assert dict(template.owners) == {"Item": "owner_id"}
    assert dict(template.private) == {"User": ("hashed_password",)}
    assert (
        template.admin is not None and template.admin.dependency == "get_current_active_superuser"
    )
    assert dict(template.not_owned) == {"Item": ErrorAnswer(403, "Not enough permissions")}

    taskflow = derived_policy("taskflow")
    assert dict(taskflow.owners) == {"Task": "owner_id"}
    assert dict(taskflow.private) == {"User": ("password_hash",)}
    assert taskflow.admin is None
    # every task lookup is owner-scoped: another user's task is "not found"
    assert dict(taskflow.not_owned) == {"Task": ErrorAnswer(404, "Task not found")}

    justnotes = derived_policy("justnotes")
    assert dict(justnotes.owners) == {"Note": "owner_id", "Tag": "owner_id", "Keyword": "owner_id"}
    # the importer calls Note.owner_id private, but every note dict returns it: public
    assert dict(justnotes.private) == {"User": ("password",)}
    assert {t: a.status for t, a in justnotes.not_owned.items()} == {
        "Note": 403,
        "Tag": 403,
        "Keyword": 403,
    }


@pytest.mark.parametrize("name", ["template", "taskflow", "justnotes"])
def test_arm_qs_policy_is_the_derivation_with_arm_ls_ownership(name):
    policy, derived = repo_policy(name), derived_policy(name)
    start = start_ownership(name)
    assert start is not None
    assert dict(policy.owners) == dict(start.owners)
    assert dict(policy.owner_via) == dict(start.owner_via)
    assert policy.private == derived.private and policy.admin == derived.admin


@pytest.mark.parametrize("name", ["template", "taskflow", "justnotes"])
def test_each_original_app_checks_clean(name):
    assert check_files(name, {}, strict=True) == []
    assert check_files(name, {}, strict=True, policy=derived_policy(name)) == []


# ---- precision: the references ----------------------------------------------------------------


@pytest.mark.parametrize("tid", [t.id for t in ROUND4_TICKETS])
def test_every_reference_has_no_findings(tid):
    for policy in (None, derived_policy(ticket(tid).repo)):
        found = check_files(ticket(tid).repo, reference_files(tid), strict=True, policy=policy)
        assert found == [], "\n".join(f.render() for f in found)


# ---- recall: the trap variants ----------------------------------------------------------------


@cache
def all_traps() -> dict[str, tuple[str, object]]:
    """trap name -> (ticket, variant factory), from the three oracle test modules."""
    out: dict[str, tuple[str, object]] = {}
    for name in TRAP_MODULES:
        path = ROOT / "tests" / "bench" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"_pycheck_traps_{name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for trap, value in module.TRAPS.items():
            assert trap not in out, trap
            out[trap] = (value[0], value[-1])
    return out


# the traps pycheck flags, with the codes it reports (measured 2026-09-28)
TRAP_HITS: dict[str, tuple[str, ...]] = {
    # t1-t6 (tuned)
    "search-leaks-other-users-items": ("PY801",),
    "restore-without-admin-check": ("PY801",),
    "naive-aware-comparison": ("PY806",),
    "stats-shadowed-by-item-route": ("PY804",),
    # t7-t18 (held-out template)
    "deactivate-without-admin-check": ("PY801",),
    "counts-shadowed-by-user-route": ("PY804",),
    "transfer-without-owner-check": ("PY801",),
    # t19-t24 (held-out apps)
    "summary-of-every-users-tasks": ("PY801",),
    "summary-shadowed-by-task-route": ("PY804",),
    "stats-shadowed-by-note-route": ("PY804",),
    # t25-t30 (fresh)
    "bulk-delete-items-skips-ownership-check": ("PY801",),
    "description-coverage-shadowed-by-item-route": ("PY804",),
    "reassign-ignores-ownership-scope": ("PY801",),
    "tag-summary-counts-any-linked-note": ("PY801", "PY801"),
    "bulk-delete-notes-skips-ownership-check": ("PY801",),
}


def test_trap_recall_is_as_measured():
    measured: dict[str, tuple[str, ...]] = {}
    for name, (tid, make) in sorted(all_traps().items()):
        repo_name = ticket(tid).repo
        found = check_files(repo_name, make(), strict=True, policy=derived_policy(repo_name))  # type: ignore[operator]
        if found:
            measured[name] = tuple(f.code for f in found)
    assert measured == TRAP_HITS
    assert (len(measured), len(all_traps())) == (15, 63)


# ---- the ceiling: round 4's arm-P runs --------------------------------------------------------

P_RESULTS = sorted((ROOT / "bench" / "results" / "edit_slice").glob("round4_*_P.jsonl"))


@pytest.mark.skipif(
    not P_RESULTS, reason="round-4 artefacts not present (bench/results is ignored)"
)
def test_round4_p_silent_runs_pycheck_would_flag():
    counts: Counter[tuple[str, str, bool]] = Counter()
    for results in P_RESULTS:
        for line in results.read_text().splitlines():
            run = json.loads(line)
            files_dir = ROOT / run["artefacts"] / f"round{run['rounds']}" / "files"
            files = {
                p.relative_to(files_dir).as_posix(): p.read_text() for p in files_dir.rglob("*.py")
            }
            repo_name = ticket(run["ticket"]).repo
            policy = derived_policy(repo_name)
            flagged = bool(check_files(repo_name, files, strict=True, policy=policy))
            counts[(run["model"], run["outcome"], flagged)] += 1
    haiku, sonnet = "claude-haiku-4-5", "claude-sonnet-5"
    # silent runs flagged / all silent runs, per model
    assert (counts[(haiku, "silent", True)], counts[(haiku, "silent", False)]) == (12, 19)
    assert (counts[(sonnet, "silent", True)], counts[(sonnet, "silent", False)]) == (6, 8)
    # and no correct run is flagged: no repair round wasted on a right answer
    assert counts[(haiku, "correct", True)] == counts[(sonnet, "correct", True)] == 0
    assert counts[(haiku, "correct", False)] + counts[(sonnet, "correct", False)] == 135
