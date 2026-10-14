"""``lattice contracts impact`` (multi-project slice 4a): the advisory, read-only preview of a
candidate provider version against each consumer's pin. Labels and exit codes, the baseline (the
pin), the candidate as a ref or a directory, the exclusion recipe, the errors, determinism and the
read-only guarantee, over synthetic worlds under ``tmp_path`` (tests/contracts/helpers.py)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as wsmod
from lattice.contracts import check as ck
from lattice.contracts import export as ex
from lattice.contracts import impact
from lattice.contracts.__main__ import main as contracts_main

from .helpers import BASE, IMPORTS, World, copy_data, git, kinds, st, write_spec

EX = "the provider excludes it from its contract (exclude in the workspace manifest)"
ME = {"endpoint": "GET /users/me", "status": [200], "auth": "token", "response_fields": ["id"]}
ROW_KEYS = {
    "consumer", "provider", "contract", "label", "error", "imports", "pinned", "version_changed",
    "spec_hash_changed", "spec_ref_changed", "current_label", "findings", "gaps", "counts", "live",
}  # fmt: skip
TOP_KEYS = {
    "format", "workspace", "provider", "candidate", "current", "advisory", "static_only",
    "baseline", "checked", "consumers", "counts", "problems", "exit_code", "elapsed_s",
}  # fmt: skip


# ---- edits of BASE ---------------------------------------------------------------------------


def status_201_to_200() -> list[tuple[str, list[dict]]]:
    data = copy_data(BASE)
    for s in data[1][1]:
        if s["method"] == "POST" and s["path"] == "/jobs/7/runs" and s["expect_status"] == 201:
            s["expect_status"] = 200
    return data


def relied_status_added() -> list[tuple[str, list[dict]]]:
    """A 403 (bob) on GET /jobs/7, which the consumer relies on: only relied CHANGED findings."""
    data = copy_data(BASE)
    data[1][1].append(st("GET", "/jobs/7", "bob", 403))
    return data


def endpoint_added() -> list[tuple[str, list[dict]]]:
    data = copy_data(BASE)
    data[1][1].append(st("GET", "/health", None, 200, expect_body={"ok": True}))
    return data


def commit_branch(w: World, name: str, data: list[tuple[str, list[dict]]], **kw: Any) -> None:
    """A branch `name` holding `data` as the provider's spec; the working tree is left on the
    branch it was on, unchanged."""
    git(w.provider, "checkout", "-q", "-b", name)
    w.edit(data, **kw)
    git(w.provider, "add", "-A")
    git(w.provider, "commit", "-qm", name)
    git(w.provider, "checkout", "-q", "-")


def impact_of(w: World, *argv: str, provider: str = "prov") -> tuple[int, dict[str, Any]]:
    return w.run("impact", provider, *argv)


def one(out: dict[str, Any]) -> dict[str, Any]:
    assert len(out["consumers"]) == 1, out
    return out["consumers"][0]  # type: ignore[no-any-return]


def tree_hashes(root: Path) -> dict[str, str]:
    """sha256 of every file under `root`, `.git` included (the read-only guarantee)."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def git_state(project: Path) -> dict[str, str]:
    return {
        "head": git(project, "rev-parse", "HEAD"),
        "refs": git(project, "for-each-ref"),
        "status": git(project, "--no-optional-locks", "status", "--porcelain=v2", "--ignored"),
        "branch": git(project, "symbolic-ref", "-q", "HEAD"),
    }


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    """A pinned world, its provider a git repository with the candidate branches ``brk``
    (a BROKEN and a relied CHANGED), ``reg`` (relied CHANGED only), ``add`` (an unrelied
    CHANGED), and ``stale``: tests of this fixture never modify it."""
    w = World(tmp_path_factory.mktemp("impact") / "w", repo=True)
    rc, out = w.pin()
    assert rc == 0, out
    commit_branch(w, "brk", status_201_to_200())
    commit_branch(w, "reg", relied_status_added())
    commit_branch(w, "add", endpoint_added())
    return w


@contextlib.contextmanager
def cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


# ---- labels and exit codes -------------------------------------------------------------------


def test_identity_is_unaffected_and_equals_check(world: World) -> None:
    rc, out = impact_of(world, "--candidate", "HEAD")
    assert rc == 0 and out["exit_code"] == 0 and set(out) == TOP_KEYS
    row = one(out)
    assert set(row) == ROW_KEYS
    assert (row["label"], row["current_label"], row["error"]) == ("unaffected", "unaffected", None)
    assert (row["version_changed"], row["spec_hash_changed"], row["spec_ref_changed"]) == (
        False, False, False,
    )  # fmt: skip
    _rc, chk = world.check("--static-only")
    link = chk["links"][0]
    assert (row["findings"], row["gaps"]) == (link["findings"], link["gaps"])
    assert row["pinned"] == link["pinned"] and row["live"] is None
    assert out["advisory"] is True and out["static_only"] is True and out["baseline"] == "pin"
    assert out["counts"] == {"contradiction": 0, "regression": 0, "unaffected": 1, "error": 0}
    assert out["candidate"]["kind"] == "ref" and out["candidate"]["version"]["dirty"] is False
    assert out["candidate"]["resolved"] == out["candidate"]["version"]["git_ref"]
    assert out["current"] == link["current"]


def test_a_broken_reliance_is_a_contradiction_exit_1(world: World) -> None:
    rc, out = impact_of(world, "--candidate", "brk")
    row = one(out)
    assert rc == 1 and out["exit_code"] == 1 and row["label"] == "contradiction"
    assert kinds(row, "BROKEN") == [("status_not_observed", "POST /jobs/{}/runs", "201")]
    assert kinds(row, "CHANGED") == [("status_added", "POST /jobs/{}/runs", "200")]
    assert row["counts"] == {"broken": 1, "changed_relied": 1, "changed_unrelied": 0, "gaps": 0}
    assert row["version_changed"] is True and row["spec_hash_changed"] is True
    assert row["current_label"] == "unaffected"
    assert out["counts"]["contradiction"] == 1


def test_a_relied_change_alone_is_a_regression_and_exits_0(world: World) -> None:
    rc, out = impact_of(world, "--candidate", "reg")
    row = one(out)
    assert rc == 0 and out["exit_code"] == 0 and row["label"] == "regression"
    assert kinds(row, "BROKEN") == []
    assert {f["relied"] for f in row["findings"]} == {True}
    assert {f["kind"] for f in row["findings"]} == {"status_added", "auth_tightened"}
    assert out["counts"] == {"contradiction": 0, "regression": 1, "unaffected": 0, "error": 0}


def test_an_unrelied_change_is_unaffected(world: World) -> None:
    rc, out = impact_of(world, "--candidate", "add")
    row = one(out)
    assert rc == 0 and row["label"] == "unaffected"
    assert [(f["class"], f["kind"], f["endpoint"], f["relied"]) for f in row["findings"]] == [
        ("CHANGED", "endpoint_added", "GET /health", False)
    ]
    assert row["counts"]["changed_unrelied"] == 1 and row["counts"]["changed_relied"] == 0


def test_the_candidate_is_what_is_evaluated_not_the_current_export(world: World) -> None:
    _rc, out = impact_of(world, "--candidate", "brk")
    row = one(out)
    assert row["label"] == "contradiction" and row["current_label"] == "unaffected"
    assert out["current"]["git_ref"] != out["candidate"]["version"]["git_ref"]
    assert out["current"]["spec_hash"] != out["candidate"]["version"]["spec_hash"]


def test_rows_equal_what_check_says_when_the_candidate_ships(tmp_path: Path) -> None:
    """The row is `check` on the candidate with nobody re-pinned (the layer 3 findings)."""
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    commit_branch(w, "brk", status_201_to_200())
    _rc, out = impact_of(w, "--candidate", "brk")
    row = one(out)
    git(w.provider, "checkout", "-q", "brk")
    _rc, chk = w.check("--static-only")
    assert (row["findings"], row["gaps"]) == (chk["links"][0]["findings"],
                                              chk["links"][0]["gaps"])  # fmt: skip


# ---- the baseline is the pin -----------------------------------------------------------------


def test_a_stale_pin_reads_contradiction_even_for_head(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    w.edit(status_201_to_200())
    git(w.provider, "commit", "-qam", "drift after the pin")
    rc, out = impact_of(w, "--candidate", "HEAD")
    row = one(out)
    assert rc == 1 and row["label"] == "contradiction" and row["current_label"] == "contradiction"
    _rc, chk = w.check("--static-only")
    assert row["findings"] == chk["links"][0]["findings"]


def test_a_current_drift_does_not_hide_a_candidate_that_fixes_it(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    git(w.provider, "branch", "pinned")
    w.edit(status_201_to_200())
    git(w.provider, "commit", "-qam", "drift")
    rc, out = impact_of(w, "--candidate", "pinned")
    row = one(out)
    assert rc == 0 and row["label"] == "unaffected" and row["current_label"] == "contradiction"
    assert row["findings"] == [] and row["version_changed"] is False


# ---- the candidate: a ref or a directory -----------------------------------------------------


def test_a_ref_and_its_sha_give_the_same_answer(world: World) -> None:
    sha = git(world.provider, "rev-parse", "brk").strip()
    _rc, by_name = impact_of(world, "--candidate", "brk")
    _rc, by_sha = impact_of(world, "--candidate", sha)
    assert by_name["candidate"]["resolved"] == by_sha["candidate"]["resolved"] == sha
    by_name["candidate"]["input"] = by_sha["candidate"]["input"]
    by_name["elapsed_s"] = by_sha["elapsed_s"] = 0
    assert by_name == by_sha


def test_a_directory_is_a_path_candidate(world: World, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(world.provider, copy)
    git(copy, "checkout", "-q", "brk")
    rc, out = impact_of(world, "--candidate", str(copy))
    row = one(out)
    cand = out["candidate"]
    assert rc == 1 and row["label"] == "contradiction"
    assert cand["kind"] == "path" and cand["resolved"] == str(copy.resolve())
    assert cand["version"]["git_ref"] == git(copy, "rev-parse", "HEAD").strip()
    assert cand["version"]["dirty"] is False


def test_an_uncommitted_edit_is_a_dirty_path_candidate(world: World, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(world.provider, copy)
    write_spec(copy, BASE, comment="# an edit that changes no fact\n")
    rc, out = impact_of(world, "--candidate", str(copy))
    assert rc == 0 and one(out)["label"] == "unaffected"
    assert out["candidate"]["version"]["dirty"] is True


def test_a_directory_shadows_a_same_named_ref(world: World, tmp_path: Path) -> None:
    shadow = tmp_path / "cwd"
    shutil.copytree(world.provider, shadow / "brk")  # a directory called like the branch brk
    with cwd(shadow):
        rc, out = impact_of(world, "--candidate", "brk")
    assert out["candidate"]["kind"] == "path" and out["candidate"]["resolved"] == str(
        (shadow / "brk").resolve()
    )
    assert rc == 0 and one(out)["label"] == "unaffected", "the directory's HEAD, not the branch"


def test_the_ref_is_not_checked_out_and_nothing_is_written(world: World, tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(world.provider, copy)
    before_files, before_git = tree_hashes(world.root), git_state(world.provider)
    for cand in ("brk", "reg", "HEAD", str(copy)):
        impact_of(world, "--candidate", cand)
    assert tree_hashes(world.root) == before_files, "no file of the workspace or .git changed"
    assert git_state(world.provider) == before_git


def test_the_temp_directory_is_removed(world: World, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    for cand in ("brk", "no-such-ref"):
        impact_of(world, "--candidate", cand)
    assert list(scratch.iterdir()) == []


def test_unknown_candidates_exit_2_and_name_it(world: World, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:  # fmt: skip
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(argv: Any, *a: Any, **kw: Any) -> Any:
        calls.append(list(argv))
        return real(argv, *a, **kw)

    monkeypatch.setattr(impact.subprocess, "run", spy)
    exports: list[Any] = []
    real_export = impact.compute_export
    monkeypatch.setattr(impact, "compute_export",
                        lambda *a, **k: exports.append(a) or real_export(*a, **k))  # fmt: skip
    rc, out = impact_of(world, "--candidate", "no-such-ref")
    assert rc == 2 and out["consumers"] == [] and out["exit_code"] == 2
    prob = out["problems"][-1]
    assert (prob["code"], prob["severity"]) == ("unknown_candidate", "error")
    assert "no-such-ref" in prob["message"] and exports == []
    calls.clear()
    rc, out = world.run("impact", "prov", "--candidate=-x")
    assert rc == 2 and out["problems"][-1]["code"] == "unknown_candidate"
    assert not any(a in ("-x", "-x^{commit}") for c in calls for a in c), "git never sees -x"
    assert exports == []


def test_a_candidate_whose_spec_does_not_load_is_candidate_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    git(w.provider, "checkout", "-q", "-b", "bad")
    with (w.provider / "spec.py").open("a") as f:
        f.write('\nraise RuntimeError("not loaded")\n')
    git(w.provider, "commit", "-qam", "bad")
    git(w.provider, "checkout", "-q", "-")
    rc, out = impact_of(w, "--candidate", "bad")
    prob = out["problems"][-1]
    assert rc == 2 and prob["code"] == "candidate_failed" and "not loaded" in prob["message"]
    assert str(scratch) not in json.dumps(out) and str(scratch.resolve()) not in json.dumps(out)
    assert out["consumers"] == [] and out["candidate"]["version"] is None
    assert out["candidate"]["resolved"] == git(w.provider, "rev-parse", "bad").strip()
    assert list(scratch.iterdir()) == []


# ---- the candidate export is the live recipe -------------------------------------------------


def excl(*keys: str) -> str:
    return "exclude = " + json.dumps(list(keys)) + "\n"


def test_the_manifests_exclusion_applies_to_the_candidate(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    w.write_manifest(extra_project=excl("GET /users/me"))
    assert w.pin()[0] == 0  # pinned under the list
    rc, out = impact_of(w, "--candidate", "HEAD")
    row = one(out)
    assert rc == 0 and row["label"] == "unaffected" and row["findings"] == []
    assert out["candidate"]["endpoints"] == len(ex.compute_export(
        w.provider, "spec:SPEC", ["GET /users/me"])["export"]["endpoints"])  # fmt: skip


def test_an_import_of_an_excluded_endpoint_is_a_contradiction(tmp_path: Path) -> None:
    w = World(tmp_path / "w", imports=[*IMPORTS, ME], repo=True)
    w.write_manifest(extra_project=excl("GET /users/me"))
    assert w.pin("--force")[0] == 0
    rc, out = impact_of(w, "--candidate", "HEAD")
    row = one(out)
    assert rc == 1 and row["label"] == "contradiction"
    assert [(f["kind"], f["endpoint"], f["detail"]) for f in row["findings"]] == [
        ("endpoint_missing", "GET /users/me", EX)
    ]


def test_the_manifests_spec_ref_is_used_for_the_candidate(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    shutil.copy(w.provider / "spec.py", w.provider / "alt.py")
    w.write_manifest()
    text = (w.root / wsmod.MANIFEST).read_text().replace('spec = "spec:SPEC"', 'spec = "alt:SPEC"')
    (w.root / wsmod.MANIFEST).write_text(text)
    w.edit(status_201_to_200())  # spec.py now differs from alt.py (the pinned recipe)
    git(w.provider, "add", "-A")
    git(w.provider, "commit", "-qm", "alt")
    assert w.pin()[0] == 0
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 0 and one(out)["label"] == "unaffected"
    assert out["candidate"]["spec_ref"] == "alt:SPEC" and one(out)["spec_ref_changed"] is False


# ---- who is listed, in which order -----------------------------------------------------------


def add_consumer(w: World, name: str, imports: list[dict], *, contract: str | None = "static",
                 pinned: bool = True) -> None:  # fmt: skip
    """Another consumer of `prov`: imports, a lock copied from cons' (pinned on the same export)
    and a link, appended to the manifest."""
    d = w.root / name
    d.mkdir()
    from .helpers import imports_text

    (d / ck.IMPORTS_FILE).write_text(imports_text(*imports))
    if pinned:
        lock = json.loads(w.lock.read_text())
        lock["consumer"] = name
        ck.write_lock(d / ck.LOCK_FILE, lock)
    link = f'contract = "{contract}"\n' if contract else ""
    with (w.root / wsmod.MANIFEST).open("a") as f:
        f.write(f'\n[[project]]\nname = "{name}"\npath = "{name}"\nrole = "consumer"\n'
                f'\n[[link]]\nfrom = "{name}"\nto = "prov"\nkind = "uses"\n{link}')  # fmt: skip


def test_rows_are_sorted_by_consumer_and_one_per_link(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    names = ["zeta", "mid", "alpha", "kilo", "echo", "bravo"]
    for n in names:
        add_consumer(w, n, IMPORTS)
    commit_branch(w, "brk", status_201_to_200())
    rc, out = impact_of(w, "--candidate", "brk")
    assert [r["consumer"] for r in out["consumers"]] == sorted([*names, "cons"])
    assert out["checked"] == 7 and rc == 1
    assert out["counts"]["contradiction"] == 7


def test_a_link_without_a_contract_key_is_not_listed(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    add_consumer(w, "quiet", IMPORTS, contract=None, pinned=False)
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 0 and [r["consumer"] for r in out["consumers"]] == ["cons"]
    assert out["checked"] == 1


def test_each_consumer_is_judged_on_its_own_imports(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    login = [IMPORTS[0]]  # POST /auth/login only: the 201 on /jobs/7/runs is not its reliance
    add_consumer(w, "login-only", login)
    commit_branch(w, "brk", status_201_to_200())
    rc, out = impact_of(w, "--candidate", "brk")
    by = {r["consumer"]: r for r in out["consumers"]}
    assert rc == 1 and by["cons"]["label"] == "contradiction"
    assert by["login-only"]["label"] == "unaffected" and by["login-only"]["imports"] == 1
    assert [(f["kind"], f["relied"]) for f in by["login-only"]["findings"]] == [
        ("status_added", False),
        ("status_removed", False),
    ]


def test_only_the_imports_of_the_provider_are_evaluated(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    other = w.root / "prov2"
    write_spec(other, BASE)
    with (w.root / wsmod.MANIFEST).open("a") as f:
        f.write('\n[[project]]\nname = "prov2"\npath = "prov2"\nrole = "provider"\n'
                'spec = "spec:SPEC"\n\n[[link]]\nfrom = "cons"\nto = "prov2"\nkind = "uses"\n'
                'contract = "static"\n')  # fmt: skip
    other_imports = [{**IMPORTS[0], "provider": "prov2"}]
    from .helpers import imports_text

    (w.consumer / ck.IMPORTS_FILE).write_text(imports_text(*IMPORTS, *other_imports))
    assert w.pin()[0] == 0
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 0 and one(out)["imports"] == len(IMPORTS) and one(out)["provider"] == "prov"


def test_a_live_link_is_never_run_and_says_so(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True, contract="live")
    assert w.pin()[0] == 0
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 0 and one(out)["live"] == {"status": "not_run", "reason": "static-only"}
    assert not (w.provider / "app.db").exists()


def test_no_opted_in_link_checks_nothing_and_reads_no_candidate(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch,
                                                                ) -> None:  # fmt: skip
    w = World(tmp_path / "w", contract=None, repo=True)
    monkeypatch.setattr(impact, "compute_export", lambda *a, **k: pytest.fail("exported"))
    rc, out = impact_of(w, "--candidate", "no-such-ref")
    assert rc == 0 and out["checked"] == 0 and out["consumers"] == [] and set(out) == TOP_KEYS
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [("no_consumers", "warning")]
    assert out["candidate"]["resolved"] is None and out["candidate"]["version"] is None
    rc, out = impact_of(w, "--candidate", "HEAD", provider="cons")  # nothing points to cons
    assert rc == 0 and out["problems"][-1]["code"] == "no_consumers"


# ---- errors ----------------------------------------------------------------------------------


def test_a_consumer_without_a_pin_is_an_error_row_not_unaffected(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    add_consumer(w, "unpinned", IMPORTS, pinned=False)
    rc, out = impact_of(w, "--candidate", "HEAD")
    by = {r["consumer"]: r for r in out["consumers"]}
    bad = by["unpinned"]
    assert rc == 2 and out["exit_code"] == 2
    assert bad["label"] is None and bad["error"]["code"] == "no_pin"
    assert bad["findings"] == [] and bad["current_label"] is None and set(bad) == ROW_KEYS
    assert by["cons"]["label"] == "unaffected", "the other rows still run"
    assert out["counts"] == {"contradiction": 0, "regression": 0, "unaffected": 1, "error": 1}


@pytest.mark.parametrize("code", ["bad_pin", "no_imports", "bad_imports"])
def test_each_row_error_exits_2(tmp_path: Path, code: str) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    if code == "bad_pin":
        w.lock.write_text("not json")
    elif code == "no_imports":
        (w.consumer / ck.IMPORTS_FILE).unlink()
    else:
        (w.consumer / ck.IMPORTS_FILE).write_text("[[import]]\nprovider = 7\n")
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 2 and one(out)["error"]["code"] == code and one(out)["label"] is None


def test_a_manifest_error_answers_its_problems_and_no_rows(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    text = (w.root / wsmod.MANIFEST).read_text().replace('contract = "static"', 'contract = "no"')
    (w.root / wsmod.MANIFEST).write_text(text)
    rc, out = impact_of(w, "--candidate", "HEAD")
    assert rc == 2 and out["consumers"] == [] and out["candidate"]["resolved"] is None
    assert "bad_contract" in [p["code"] for p in out["problems"]]


def test_an_unknown_provider_is_a_problem(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    rc, out = impact_of(w, "--candidate", "HEAD", provider="ghost")
    assert rc == 2 and out["consumers"] == []
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [("unknown_project", "error")]
    assert "have prov, cons" in out["problems"][0]["message"]


def test_a_failing_current_export_only_warns(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    (w.provider / "spec.py").write_text("raise RuntimeError('broken in the working tree')\n")
    rc, out = impact_of(w, "--candidate", "HEAD")  # HEAD is the committed, loadable spec
    assert rc == 0 and out["current"] is None and one(out)["current_label"] is None
    assert one(out)["label"] == "unaffected"
    assert [(p["code"], p["severity"]) for p in out["problems"]] == [
        ("current_export_failed", "warning")
    ]


def test_a_provider_outside_a_git_repository_names_the_candidate(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=False)
    assert w.pin()[0] == 0
    rc, out = impact_of(w, "--candidate", "main")
    assert rc == 2 and out["problems"][-1]["code"] == "unknown_candidate"
    assert "main" in out["problems"][-1]["message"]
    rc, out = impact_of(w, "--candidate", str(w.provider))  # a directory still works
    assert rc == 0 and one(out)["label"] == "unaffected"
    assert out["candidate"]["version"]["git_ref"] is None


# ---- materialising a ref ---------------------------------------------------------------------


def test_a_subdirectory_project_is_materialised_with_symlinks_and_modes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    proj = repo / "svc"
    (proj / "pkg" / "deep").mkdir(parents=True)
    (repo / "other").mkdir()
    (repo / "other" / "x.txt").write_text("not the project\n")
    (proj / "a.txt").write_text("a\n")
    (proj / "pkg" / "deep" / "b.bin").write_bytes(b"\x00\xff\x01")
    (proj / "run.sh").write_text("#!/bin/sh\n")
    (proj / "run.sh").chmod(0o755)
    os.symlink("a.txt", proj / "link")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "add", "-A")
    # a gitlink (a submodule entry) has no content here: it is skipped
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},svc/sub")
    git(repo, "commit", "-qm", "init")
    sha = git(repo, "rev-parse", "HEAD").strip()
    dest = tmp_path / "out"
    dest.mkdir()
    n = impact.materialise(proj, sha, dest)
    assert sorted(
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() or p.is_symlink()
    ) == [  # fmt: skip
        "a.txt",
        "link",
        "pkg/deep/b.bin",
        "run.sh",
    ]
    assert n == 4
    assert (dest / "pkg" / "deep" / "b.bin").read_bytes() == b"\x00\xff\x01"
    assert os.readlink(dest / "link") == "a.txt"
    assert os.access(dest / "run.sh", os.X_OK) and not os.access(dest / "a.txt", os.X_OK)
    assert not (dest / "sub").exists() and not (dest / "other").exists()


def test_a_project_in_a_subdirectory_of_the_repository_is_previewed(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    repo = root / "mono"
    repo.mkdir()
    w_dir = repo / "prov"
    write_spec(w_dir, BASE)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    cons = root / "cons"
    cons.mkdir()
    from .helpers import imports_text

    (cons / ck.IMPORTS_FILE).write_text(imports_text(*IMPORTS))
    (root / wsmod.MANIFEST).write_text(
        '[workspace]\nname = "w"\n\n[[project]]\nname = "prov"\npath = "mono/prov"\n'
        'role = "provider"\nspec = "spec:SPEC"\n\n[[project]]\nname = "cons"\npath = "cons"\n'
        'role = "consumer"\n\n[[link]]\nfrom = "cons"\nto = "prov"\nkind = "uses"\n'
        'contract = "static"\n'
    )
    assert contracts_main(["pin", "cons", "--workspace", str(root)]) == 0
    write_spec(w_dir, status_201_to_200())
    git(repo, "checkout", "-q", "-b", "brk")
    git(repo, "commit", "-qam", "brk")
    git(repo, "checkout", "-q", "-")
    out, rc = io.StringIO(), 0
    with contextlib.redirect_stdout(out):
        rc = contracts_main(["impact", "prov", "--candidate", "brk", "--workspace", str(root),
                             "--json"])  # fmt: skip
    doc = json.loads(out.getvalue())
    assert rc == 1 and doc["consumers"][0]["label"] == "contradiction"


# ---- output ----------------------------------------------------------------------------------


def cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = contracts_main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def test_the_text_is_deterministic_and_has_no_temp_path(world: World) -> None:
    argv = ["impact", "prov", "--candidate", "brk", "--workspace", str(world.root)]
    rc1, a, err = cli(*argv)
    rc2, b, _ = cli(*argv)
    assert (rc1, rc2, err) == (1, 1, "") and a == b
    lines = a.splitlines()
    assert (
        lines[0].startswith("impact of brk (ref, ") and "on prov: advisory, static only" in lines[0]
    )
    assert "baseline = each consumer's pin; provider now " in lines[0]
    assert lines[1].startswith("cons  CONTRADICTION  4 import(s), pinned ")
    assert lines[1].endswith(", now: unaffected")
    assert any(ln.startswith("  BROKEN   status_not_observed") for ln in lines)
    assert any(ln.startswith("  CHANGED* status_added") for ln in lines)
    assert lines[-1] == "1 consumer(s): 1 contradiction, 0 regression, 0 unaffected, 0 error"
    assert tempfile.gettempdir() not in a and "lattice-impact-" not in a


def test_the_json_is_deterministic_but_for_elapsed(world: World) -> None:
    _rc, a = impact_of(world, "--candidate", "brk")
    _rc, b = impact_of(world, "--candidate", "brk")
    a.pop("elapsed_s"), b.pop("elapsed_s")
    assert json.dumps(a, indent=1) == json.dumps(b, indent=1)


def test_no_workspace_is_an_exit_2_message(tmp_path: Path) -> None:
    with cwd(tmp_path):
        rc, out, err = cli("impact", "prov", "--candidate", "HEAD", "--workspace",
                           str(tmp_path / "nowhere"))  # fmt: skip
    assert rc == 2 and out == "" and "lattice contracts" in err


def test_unavailable_has_every_key() -> None:
    doc = impact.unavailable("prov", "HEAD").to_dict()
    assert set(doc) == TOP_KEYS and doc["workspace"] is None and doc["exit_code"] == 2
    assert doc["consumers"] == [] and doc["checked"] == 0 and doc["current"] is None
    assert doc["candidate"] == {"input": "HEAD", "kind": "ref", "resolved": None,
                                "spec_ref": None, "version": None, "endpoints": None}  # fmt: skip


# ---- the one change to layer 3 ---------------------------------------------------------------


def test_git_version_never_takes_an_optional_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = World(tmp_path / "w", repo=True)
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(argv: Any, *a: Any, **kw: Any) -> Any:
        calls.append(list(argv))
        return real(argv, *a, **kw)

    monkeypatch.setattr(ex.subprocess, "run", spy)
    ref, dirty = ex.git_version(w.provider)
    seen = list(calls)
    assert ref == git(w.provider, "rev-parse", "HEAD").strip() and dirty is False
    assert [c[3] for c in seen] == ["--no-optional-locks"] * 2
    assert [c[4] for c in seen] == ["rev-parse", "status"]
