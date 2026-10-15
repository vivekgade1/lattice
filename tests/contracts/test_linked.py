"""Linked pending questions in consumers (multi-project slice 4b): ``lattice contracts notify`` and
``lattice contracts questions``. What creates a question (a contradiction only), the idempotence
key and the creation rule, the derived supersede, clearing, and the bounds of the first
cross-project write (only ``linked_questions/`` of a state directory that already exists, atomic,
under a flock), over synthetic worlds under ``tmp_path`` (tests/contracts/helpers.py)."""

from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.contracts import linked
from lattice.contracts.__main__ import main as contracts_main
from lattice.studio import state as studio_state

from .helpers import BASE, World, copy_data, git, st
from .test_impact import commit_branch, endpoint_added, relied_status_added, status_201_to_200

PIN = "9" * 64
SHA = "b" * 40
CAND = {"input": "x", "kind": "ref", "resolved": SHA, "spec_ref": "spec:SPEC",
        "version": {"spec_hash": "c" * 64, "git_ref": SHA, "dirty": False},
        "endpoints": 3}  # fmt: skip
SRC = {"via": "cli", "change": None, "commit": SHA}


def status_404_gone() -> list[tuple[str, list[dict]]]:
    """GET /jobs/8 no longer answers 404: the consumer's claim about GET /jobs/{id} breaks."""
    data = copy_data(BASE)
    data[1][1] = [
        s for s in data[1][1] if not (s["path"] == "/jobs/8" and s["expect_status"] == 404)
    ]
    return data


def fake_row(
    broken: tuple[tuple[str, str, str], ...] = (("POST /x", "status_not_observed", "201"),),
    label: str = "contradiction",
    provider: str = "prov",
    consumer: str = "cons",
    contract: str = "static",
    pin: str = PIN,
) -> dict[str, Any]:
    findings = [{"class": "BROKEN", "provider": provider, "endpoint": e, "kind": k, "item": i,
                 "detail": "d"} for e, k, i in broken]  # fmt: skip
    return {"consumer": consumer, "provider": provider, "contract": contract, "label": label,
            "error": None, "imports": 1,
            "pinned": {"spec_ref": "spec:SPEC", "spec_hash": pin, "git_ref": SHA, "dirty": False},
            "findings": findings, "gaps": [],
            "counts": {"broken": len(findings), "changed_relied": 0, "changed_unrelied": 0,
                       "gaps": 0}}  # fmt: skip


def tree(root: Path, skip: str | None = None) -> dict[str, tuple[str, int]]:
    """sha256 and mtime_ns of every file under `root`, without the subtree `skip`."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = str(p.relative_to(root))
            if skip and (rel == skip or rel.startswith(skip + "/")):
                continue
            out[rel] = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    return out


def qfiles(state: Path) -> dict[str, str]:
    q = state / linked.DIR
    return {str(p.relative_to(q)): p.read_text() for p in sorted(q.rglob("*"))
            if p.is_file() and p.name != linked.LOCK}  # fmt: skip


def question(state: Path, qid: str) -> dict[str, Any]:
    return json.loads((state / linked.DIR / f"{qid}.json").read_text())  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    """A pinned world: ``brk`` (a BROKEN 201 and a relied CHANGED 200), ``brk2`` (a different
    BROKEN: GET /jobs/{id} 404), ``brkadd`` (brk plus an unrelated endpoint: same BROKEN set,
    another spec_hash), ``reg`` (relied CHANGED only), ``add`` (an unrelied CHANGED). Tests of
    this fixture never modify the provider or the consumer's imports and lock."""
    w = World(tmp_path_factory.mktemp("linked") / "w", repo=True)
    assert w.pin()[0] == 0
    commit_branch(w, "brk", status_201_to_200())
    commit_branch(w, "brk2", status_404_gone())
    both = status_201_to_200()
    both[1][1].append(st("GET", "/health", None, 200, expect_body={"ok": True}))
    commit_branch(w, "brkadd", both)
    commit_branch(w, "reg", relied_status_added())
    commit_branch(w, "add", endpoint_added())
    return w


@pytest.fixture()
def inbox(world: World) -> Iterator[Path]:
    """The consumer's state directory, created by ``questions init`` and emptied afterwards."""
    rc, out = world.run("questions", "init", "cons")
    assert rc == 0, out
    yield world.consumer / ".lattice" / "state" / "cons"
    shutil.rmtree(world.consumer / ".lattice", ignore_errors=True)


def notify(w: World, cand: str, *flags: str, provider: str = "prov") -> tuple[int, dict[str, Any]]:
    return w.run("notify", provider, "--candidate", cand, *flags)


def row_of(doc: dict[str, Any], consumer: str = "cons") -> dict[str, Any]:
    (r,) = [x for x in doc["consumers"] if x["consumer"] == consumer]
    return r  # type: ignore[no-any-return]


# ---- what creates a question -----------------------------------------------------------------


def test_a_contradiction_creates_one_question_with_every_key(world: World, inbox: Path) -> None:
    _rc, imp = world.run("impact", "prov", "--candidate", "brk")
    rc, doc = notify(world, "brk")
    r = row_of(doc)
    assert rc == 0 and r["action"] == "created" and r["question"] == "lq1" == r["current"]
    assert r["label"] == "contradiction" and r["error"] is None and r["warning"] is None
    assert doc["advisory"] is True and doc["dry_run"] is False and doc["exit_code"] == 0
    assert doc["counts"] == {"created": 1, "existing": 0, "none": 0, "undelivered": 0, "skipped": 0}
    assert doc["source"] == {"via": "cli", "change": None, "commit": doc["candidate"]["resolved"]}
    assert set(doc) == {"format", "workspace", "provider", "candidate", "source", "advisory",
                        "dry_run", "consumers", "counts", "problems", "exit_code",
                        "elapsed_s"}  # fmt: skip
    assert r["state_dir"] == str(inbox)
    q = question(inbox, "lq1")
    assert set(q) == {"format", "id", "key", "kind", "workspace", "provider", "consumer",
                      "contract", "status", "label", "pinned", "provider_after", "candidate",
                      "imports_affected", "findings", "counts", "text", "suggestion", "source",
                      "created_at", "resolution"}  # fmt: skip
    assert (q["format"], q["id"], q["kind"], q["status"], q["label"]) == (
        1, "lq1", "linked_question", "open", "contradiction")  # fmt: skip
    assert (q["provider"], q["consumer"], q["contract"], q["workspace"]) == (
        "prov", "cons", "static", "w")  # fmt: skip
    crow = imp["consumers"][0]
    assert q["pinned"] == crow["pinned"] and q["counts"] == crow["counts"]
    assert q["findings"] == [f for f in crow["findings"]
                             if f["class"] == "BROKEN" or f.get("relied")]  # fmt: skip
    assert q["imports_affected"] == ["POST /jobs/{}/runs"]
    assert q["provider_after"] == {
        "spec_ref": "spec:SPEC",
        "spec_hash": imp["candidate"]["version"]["spec_hash"],
        "git_ref": imp["candidate"]["resolved"],
        "dirty": False,
    }
    assert q["candidate"] == {
        "input": "brk",
        "kind": "ref",
        "resolved": imp["candidate"]["resolved"],
    }
    assert q["resolution"] is None and q["source"] == doc["source"]
    assert q["key"].startswith("sha256:") and len(q["key"]) == 7 + 64
    head, *lines = q["text"].split("\n")
    assert head == (f"prov's accepted contract (spec_hash {q['provider_after']['spec_hash'][:12]}, "
                    f"commit {SHA[:0]}{q['provider_after']['git_ref'][:8]}) contradicts cons's "
                    f"pin (spec_hash {q['pinned']['spec_hash'][:12]}): 1 import(s) BROKEN on 1 "
                    "endpoint(s)")  # fmt: skip
    assert lines == [linked.finding_line(f) for f in q["findings"]]
    assert lines[0].split() == [
        "BROKEN",
        "status_not_observed",
        "201",
        "@",
        "POST",
        "/jobs/{}/runs",
    ]
    assert "CHANGED*" in lines[1]
    assert q["suggestion"].startswith("advisory, nothing was changed in cons: adapt cons")
    assert 'questions resolve|dismiss cons lq1"' in q["suggestion"]


def test_a_regression_and_an_unaffected_row_create_nothing(world: World, inbox: Path) -> None:
    before = tree(inbox)
    for cand in ("reg", "add", "HEAD"):
        rc, doc = notify(world, cand)
        assert rc == 0 and row_of(doc)["action"] == "none" and row_of(doc)["question"] is None
    assert row_of(notify(world, "reg")[1])["label"] == "regression"
    assert tree(inbox) == before
    assert [p.name for p in (inbox / linked.DIR).iterdir()] == []


def test_the_same_break_is_one_question_whatever_the_candidate(world: World, inbox: Path) -> None:
    _rc, a = notify(world, "brk")
    before = tree(inbox)
    _rc, b = notify(world, "brk")
    _rc, c = notify(world, "brkadd")  # the same BROKEN set, another spec_hash
    sha = git(world.provider, "rev-parse", "brk").strip()
    _rc, d = notify(world, sha)
    assert [row_of(x)["action"] for x in (a, b, c, d)] == ["created", "existing", "existing",
                                                          "existing"]  # fmt: skip
    assert {row_of(x)["question"] for x in (a, b, c, d)} == {"lq1"}
    assert tree(inbox) == before, "no byte and no mtime changed by a repeated notification"
    _rc, e = notify(world, "brk2")  # another BROKEN set: another key, a new question
    assert row_of(e)["action"] == "created" and row_of(e)["question"] == "lq2"
    assert question(inbox, "lq1")["key"] != question(inbox, "lq2")["key"]


def test_dry_run_decides_and_writes_nothing(world: World, inbox: Path) -> None:
    before = tree(inbox)
    rc, doc = notify(world, "brk", "--dry-run")
    r = row_of(doc)
    assert rc == 0 and doc["dry_run"] is True and r["action"] == "created"
    assert r["question"] == "lq1"
    assert tree(inbox) == before and list((inbox / linked.DIR).iterdir()) == []
    notify(world, "brk")
    again = notify(world, "brk", "--dry-run")[1]
    assert row_of(again)["action"] == "existing" and row_of(again)["question"] == "lq1"


def test_exit_codes(world: World, inbox: Path) -> None:
    assert notify(world, "brk")[0] == 0 and notify(world, "HEAD")[0] == 0
    rc, doc = notify(world, "no-such-ref")
    assert rc == 2 and doc["exit_code"] == 2 and doc["consumers"] == []
    assert doc["problems"][-1]["code"] == "unknown_candidate"
    rc, doc = notify(world, "brk", provider="nobody")
    assert rc == 2 and doc["problems"][-1]["code"] == "unknown_project"
    rc, doc = notify(world, "HEAD", provider="cons")  # nothing relies on cons: no consumers
    assert rc == 0 and doc["consumers"] == []
    assert [p["code"] for p in doc["problems"]] == ["no_consumers"]


# ---- supersede, the creation rule ------------------------------------------------------------


def test_the_supersede_walkthrough(world: World, inbox: Path) -> None:
    def listing(status: str = "all") -> dict[str, Any]:
        rc, doc = world.run("questions", "list", "cons", "--status", status)
        assert rc == 0
        return doc

    def by_id() -> dict[str, dict[str, Any]]:
        return {q["id"]: q for q in listing()["questions"]}

    link = inbox / linked.DIR / linked.LINKS / "prov.static.json"
    # s1
    r = row_of(notify(world, "brk")[1])
    assert (r["action"], r["question"]) == ("created", "lq1")
    assert json.loads(link.read_text())["current"] == "lq1"
    lq1_bytes = (inbox / linked.DIR / "lq1.json").read_bytes()
    # s2, s3
    before = tree(inbox)
    assert row_of(notify(world, "brk")[1])["action"] == "existing"
    assert row_of(notify(world, "brkadd")[1])["action"] == "existing"
    assert tree(inbox) == before
    # s4: another break; lq1 stays open, superseded by lq2
    r = row_of(notify(world, "brk2")[1])
    assert (r["action"], r["question"]) == ("created", "lq2")
    qs = by_id()
    assert qs["lq1"]["status"] == "open" and qs["lq1"]["superseded"]["by"] == "lq2"
    assert qs["lq1"]["superseded"]["reason"] == "newer_question"
    assert qs["lq2"]["superseded"] is None
    assert (inbox / linked.DIR / "lq1.json").read_bytes() == lq1_bytes, (
        "the provider never rewrites"
    )
    assert listing()["counts"] == {"open": 2, "resolved": 0, "dismissed": 0, "superseded": 1}
    # s5: a clean candidate: nothing created, both superseded by nothing, still open
    r = row_of(notify(world, "HEAD")[1])
    assert (r["action"], r["question"]) == ("none", None)
    assert json.loads(link.read_text())["current"] is None
    qs = by_id()
    assert {k: (v["status"], v["superseded"]["by"], v["superseded"]["reason"])
            for k, v in qs.items()} == {"lq1": ("open", None, "no_contradiction"),
                                        "lq2": ("open", None, "no_contradiction")}  # fmt: skip
    # s6: the first break comes back while lq1 is open: lq1 is current again
    r = row_of(notify(world, "brk")[1])
    assert (r["action"], r["question"]) == ("existing", "lq1")
    qs = by_id()
    assert qs["lq1"]["superseded"] is None and qs["lq2"]["superseded"]["by"] == "lq1"
    # s7: dismissed by the owner; the state is unchanged, so nothing is created
    rc, out = world.run("questions", "dismiss", "cons", "lq1", "--note", "not ours")
    assert rc == 0 and out["status"] == "dismissed" and out["resolution"]["via"] == "cli"
    before = qfiles(inbox)
    r = row_of(notify(world, "brk")[1])
    assert (r["action"], r["question"]) == ("existing", "lq1")
    assert qfiles(inbox) == before and len(listing()["questions"]) == 2
    # s8: clean again
    r = row_of(notify(world, "HEAD")[1])
    assert r["action"] == "none" and json.loads(link.read_text())["current"] is None
    # s9: the break returns after a clean state: a NEW question, lq1 stays dismissed
    r = row_of(notify(world, "brk")[1])
    assert (r["action"], r["question"]) == ("created", "lq3")
    qs = by_id()
    assert qs["lq1"]["status"] == "dismissed" and qs["lq3"]["status"] == "open"
    assert qs["lq3"]["key"] == qs["lq1"]["key"] and len(qs) == 3
    # a regression row leaves lq2 open and the link's current null
    r = row_of(notify(world, "reg")[1])
    assert r["action"] == "none" and json.loads(link.read_text())["current"] is None
    assert by_id()["lq2"]["status"] == "open"


def test_a_notification_never_changes_a_status(world: World, inbox: Path) -> None:
    notify(world, "brk")
    notify(world, "brk2")
    notify(world, "HEAD")
    notify(world, "reg")
    notify(world, "brk")
    assert {q["status"] for q in linked.read_state(inbox)[0]} == {"open"}


def test_a_link_is_never_created_with_current_null(world: World, inbox: Path) -> None:
    notify(world, "HEAD")
    notify(world, "reg")
    assert not (inbox / linked.DIR / linked.LINKS / "prov.static.json").exists()
    notify(world, "brk")
    assert (inbox / linked.DIR / linked.LINKS / "prov.static.json").exists()


# ---- clearing --------------------------------------------------------------------------------


def test_resolve_dismiss_and_the_refusals(world: World, inbox: Path) -> None:
    notify(world, "brk")
    notify(world, "brk2")
    rc, out = world.run("questions", "resolve", "cons", "lq1", "--note", "adapted")
    assert rc == 0 and out["status"] == "resolved" and out["superseded"] is None
    res = out["resolution"]
    assert (res["status"], res["note"], res["via"]) == ("resolved", "adapted", "cli")
    stored = (inbox / linked.DIR / "lq1.json").read_bytes()
    rc, err = world.run("questions", "dismiss", "cons", "lq1")
    assert rc == 1 and err["error"]["code"] == "not_open"
    assert (inbox / linked.DIR / "lq1.json").read_bytes() == stored, "the resolution is kept"
    rc, err = world.run("questions", "resolve", "cons", "lq2", "--note", "x" * 2001)
    assert rc == 1 and err["error"]["code"] == "bad_note"
    assert question(inbox, "lq2")["status"] == "open"
    rc, out = world.run("questions", "dismiss", "cons", "lq2", "--note", "x" * 2000)
    assert rc == 0 and out["status"] == "dismissed" and out["resolution"]["note"] == "x" * 2000
    rc, err = world.run("questions", "resolve", "cons", "lq9")
    assert rc == 2 and err["error"]["code"] == "unknown_question"
    rc, err = world.run("questions", "resolve", "cons", "../lq1")
    assert rc == 2 and err["error"]["code"] == "unknown_question"
    rc, err = world.run("questions", "list", "nobody")
    assert rc == 2 and err["error"]["code"] == "unknown_project"
    assert world.run("questions", "list", "cons", "--status", "resolved")[1]["counts"] == {
        "open": 0, "resolved": 1, "dismissed": 1, "superseded": 0}  # fmt: skip


def test_a_superseded_question_is_clearable(world: World, inbox: Path) -> None:
    notify(world, "brk")
    notify(world, "brk2")
    assert linked.get_question(inbox, "lq1")["superseded"]["by"] == "lq2"
    rc, out = world.run("questions", "resolve", "cons", "lq1")
    assert rc == 0 and out["status"] == "resolved"
    assert row_of(notify(world, "brk")[1])["question"] == "lq3", "resolved: never reopened"


def test_no_state_directory_cannot_be_cleared(world: World) -> None:
    rc, err = world.run("questions", "resolve", "cons", "lq1")
    assert rc == 2 and err["error"]["code"] == "no_state"
    assert not (world.consumer / ".lattice").exists()


def test_text_output_of_the_cli(
    world: World, inbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = ["--workspace", str(world.root)]
    assert contracts_main(["notify", "prov", "--candidate", "brk", *ws]) == 0
    out = capsys.readouterr().out
    assert "cons  [static]  CONTRADICTION  created  lq1" in out and out.rstrip().endswith(
        "0 skipped"
    )
    assert contracts_main(["questions", "list", "cons", *ws]) == 0
    out = capsys.readouterr().out
    assert "state present" in out and "lq1  OPEN  prov [static]" in out
    assert contracts_main(["questions", "resolve", "cons", "lq1", "--note", "ok", *ws]) == 0
    assert "lq1: resolved" in capsys.readouterr().out
    assert contracts_main(["questions", "resolve", "cons", "lq1", *ws]) == 1
    assert "[not_open]" in capsys.readouterr().err


# ---- where it is written ---------------------------------------------------------------------


def test_only_the_allowed_paths_change_in_the_consumer(world: World, inbox: Path) -> None:
    before = tree(world.consumer, skip=".lattice/state/cons/linked_questions")
    for cand in ("brk", "brk2", "HEAD", "brk"):
        notify(world, cand)
    world.run("questions", "dismiss", "cons", "lq1")
    assert tree(world.consumer, skip=".lattice/state/cons/linked_questions") == before
    names = sorted(p.name for p in (inbox / linked.DIR).iterdir())
    assert names == [".lock", "links", "lq1.json", "lq2.json"]
    assert [p.name for p in (inbox / linked.DIR / linked.LINKS).iterdir()] == ["prov.static.json"]
    assert [p.name for p in inbox.iterdir()] == [linked.DIR], "no meta.json, plans, builds"
    assert not list(inbox.rglob(".*.tmp-*"))


def test_a_missing_state_dir_is_never_created(world: World) -> None:
    assert not (world.consumer / ".lattice").exists()
    rc, doc = notify(world, "brk")
    r = row_of(doc)
    assert rc == 1 and doc["exit_code"] == 1 and r["action"] == "undelivered"
    assert r["warning"]["code"] == "consumer_state_missing" and r["question"] is None
    assert "questions init cons" in r["warning"]["message"]
    assert doc["counts"]["undelivered"] == 1
    assert not (world.consumer / ".lattice").exists()
    before = tree(world.consumer)
    rc, doc = notify(world, "brk", "--dry-run")
    assert row_of(doc)["action"] == "undelivered" and tree(world.consumer) == before
    assert not (world.consumer / ".lattice").exists()
    rc, lst = world.run("questions", "list", "cons")
    assert rc == 0 and lst["state"] == "missing" and lst["questions"] == []
    assert lst["counts"] == {"open": 0, "resolved": 0, "dismissed": 0, "superseded": 0}
    assert not (world.consumer / ".lattice").exists()


def test_init_creates_the_state_dir_and_is_idempotent(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    git(w.consumer, "init", "-q")
    assert w.run("questions", "init", "cons")[0] == 0
    state = w.consumer / ".lattice" / "state" / "cons"
    assert [p.name for p in state.iterdir()] == [linked.DIR]
    exclude = (w.consumer / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.lattice/state/") == 1, "the real state_dir_for made it git-ignored"
    before = tree(state)
    assert w.run("questions", "init", "cons")[0] == 0
    assert tree(state) == before
    assert (w.consumer / ".git" / "info" / "exclude").read_text() == exclude


def test_locating_never_touches_the_gitignore_or_creates_anything(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    git(w.consumer, "init", "-q")
    exclude = w.consumer / ".git" / "info" / "exclude"
    before = exclude.read_text()
    loc = linked.locate(w.consumer, "cons")
    assert loc.state == "missing" and loc.state_dir == (
        w.consumer.resolve() / ".lattice" / "state" / "cons"
    )  # fmt: skip
    assert studio_state.state_dir_path(w.consumer, "cons") == loc.state_dir
    assert exclude.read_text() == before and not (w.consumer / ".lattice").exists()
    studio_state.state_dir_for(w.consumer, "cons")  # the real one adds the line
    assert exclude.read_text() != before


def test_state_dir_and_disabled_from_the_consumers_studio_toml(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    assert w.pin()[0] == 0
    commit_branch(w, "brk", status_201_to_200())
    (w.consumer / "studio.toml").write_text('[state]\ndir = "st"\n')
    (w.consumer / "st").mkdir()
    rc, doc = notify(w, "brk")
    assert rc == 0 and row_of(doc)["question"] == "lq1"
    assert row_of(doc)["state_dir"] == str((w.consumer / "st").resolve())
    assert (w.consumer / "st" / linked.DIR / "lq1.json").is_file()
    assert not (w.consumer / ".lattice").exists()
    (w.consumer / "studio.toml").write_text("[state]\nenabled = false\n")
    rc, doc = notify(w, "brk")
    r = row_of(doc)
    assert rc == 1 and r["action"] == "undelivered"
    assert r["warning"]["code"] == "consumer_state_disabled"
    assert w.run("questions", "list", "cons")[1]["state"] == "disabled"


def test_the_manifest_name_is_tried_after_the_project_name(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True)
    (w.consumer / "studio.toml").write_text('[project]\nname = "ci"\n')
    base = w.consumer.resolve() / ".lattice" / "state"
    (base / "cons").mkdir(parents=True)  # the hub-with-workspace name
    loc = linked.locate(w.consumer, "cons")
    assert (loc.state, loc.state_dir, loc.warnings) == ("present", base / "cons", [])
    (base / "ci").mkdir()  # both exist: the project's own name wins, with a warning
    loc = linked.locate(w.consumer, "cons")
    assert loc.state_dir == base / "ci"
    assert [x["code"] for x in loc.warnings] == ["consumer_state_ambiguous"]


def test_a_consumer_state_the_studio_owns_is_not_rewritten(world: World, tmp_path: Path) -> None:
    """A real StateStore dir with a plan, a build and change c1: notify leaves meta.json, plans/,
    builds/ and changes/ byte-identical and the Studio's next id is still c2."""
    state = world.consumer / ".lattice" / "state" / "cons"
    try:
        store = studio_state.StateStore(state)
        n = store.allocate("changes")
        store.save("changes", f"c{n}", {"id": f"c{n}", "status": "accepted"}, {})
        store.save("plans", "p1", {"id": "p1"}, {})
        store.save("builds", "b1", {"id": "b1"}, {})
        before = tree(state)
        assert row_of(notify(world, "brk")[1])["action"] == "created"
        assert row_of(notify(world, "brk2")[1])["action"] == "created"
        assert tree(state, skip=linked.DIR) == {k: v for k, v in before.items()}
        assert studio_state.StateStore(state).allocate("changes") == 2
    finally:
        shutil.rmtree(world.consumer / ".lattice", ignore_errors=True)


def test_a_link_without_contract_or_outside_the_workspace_gets_nothing(tmp_path: Path) -> None:
    w = World(tmp_path / "w", repo=True, contract=None)
    w.run("questions", "init", "cons")
    commit_branch(w, "brk", status_201_to_200())
    rc, doc = notify(w, "brk")
    assert rc == 0 and doc["consumers"] == []
    assert [p["code"] for p in doc["problems"]] == ["no_consumers"]
    assert [p.name for p in (w.consumer / ".lattice/state/cons/linked_questions").iterdir()] == []


def test_a_symlinked_linked_questions_dir_is_unsafe(world: World, inbox: Path,
                                                    tmp_path: Path) -> None:  # fmt: skip
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(inbox / linked.DIR)
    (inbox / linked.DIR).symlink_to(elsewhere)
    rc, doc = notify(world, "brk")
    r = row_of(doc)
    assert rc == 1 and r["action"] == "undelivered"
    assert r["warning"]["code"] == "consumer_state_unsafe"
    assert list(elsewhere.iterdir()) == []


def test_an_unwritable_state_is_a_warning_and_nothing_is_written(world: World,
                                                                 inbox: Path) -> None:  # fmt: skip
    notify(world, "brk")
    qdir = inbox / linked.DIR
    before = qfiles(inbox)
    try:
        for d in (qdir / linked.LINKS, qdir, inbox):
            d.chmod(0o555)
        rc, doc = notify(world, "brk2")
        r = row_of(doc)
        assert rc == 1 and r["action"] == "undelivered"
        assert r["warning"]["code"] == "consumer_state_unwritable"
    finally:
        for d in (inbox, qdir, qdir / linked.LINKS):
            d.chmod(0o755)
    assert qfiles(inbox) == before


def test_a_held_lock_is_busy_after_the_wait(world: World, inbox: Path) -> None:
    from lattice import workspace as wsmod

    wsp = wsmod.load(world.root / wsmod.MANIFEST)
    fd = os.open(inbox / linked.DIR / linked.LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        doc = linked.notify(wsp, "prov", "brk", via="cli", base=world.root, lock_wait=0.3)
        waited = time.monotonic() - t0
    finally:
        os.close(fd)
    r = row_of(doc)
    assert r["action"] == "undelivered" and r["warning"]["code"] == "consumer_state_busy"
    assert doc["exit_code"] == 1 and waited < 30
    assert list((inbox / linked.DIR).glob("lq*.json")) == []


# ---- ids, unreadable files, formats ----------------------------------------------------------


def test_ids_are_never_reused_and_an_unreadable_file_takes_its_id(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / linked.DIR).mkdir()
    (state / linked.DIR / "lq4.json").write_text("{not json")
    (state / linked.DIR / "lq7.json.bad-20260101").write_text("{}")
    res = linked.deliver(state, "w", fake_row(), CAND, SRC)
    assert (res["action"], res["question"]) == ("created", "lq8")
    assert (state / linked.DIR / "lq4.json").read_text() == "{not json", "kept, not quarantined"
    qs, problems = linked.read_state(state)
    assert [q["id"] for q in qs] == ["lq8"]
    assert [(p["code"], p["file"]) for p in problems] == [("unreadable_question", "lq4.json")]
    (state / linked.DIR / "lq8.json").unlink()
    res = linked.deliver(state, "w", fake_row(), CAND, SRC)
    assert res["question"] == "lq8", "only a name that is gone can be taken again"


def test_a_newer_format_is_read_and_its_unknown_fields_are_kept(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / linked.DIR).mkdir(parents=True)
    linked.deliver(state, "w", fake_row(), CAND, SRC)
    path = state / linked.DIR / "lq1.json"
    doc = json.loads(path.read_text())
    doc.update(format=2, future={"x": [1, 2]})
    path.write_text(json.dumps(doc))
    qs, problems = linked.read_state(state)
    assert qs[0]["future"] == {"x": [1, 2]} and [p["code"] for p in problems] == ["newer_format"]
    out = linked.clear(state, "lq1", linked.RESOLVED, "ok", "cli")
    assert out["future"] == {"x": [1, 2]} and out["format"] == 2
    assert json.loads(path.read_text())["future"] == {"x": [1, 2]}


def test_stale_temporaries_are_swept_by_the_next_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / linked.DIR / linked.LINKS).mkdir(parents=True)
    stale = [state / linked.DIR / ".lq1.json.tmp-1-2", state / linked.DIR / "links" / ".a.tmp-3-4"]
    for p in stale:
        p.write_text("half")
    linked.deliver(state, "w", fake_row(), CAND, SRC)
    assert not any(p.exists() for p in stale)
    assert not list((state / linked.DIR).rglob(".*.tmp-*"))


def test_one_row_per_link_and_contract_get_separate_links(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    a = linked.deliver(state, "w", fake_row(contract="static"), CAND, SRC)
    b = linked.deliver(state, "w", fake_row(contract="live"), CAND, SRC)
    c = linked.deliver(state, "w", fake_row(provider="other"), CAND, SRC)
    assert [x["question"] for x in (a, b, c)] == ["lq1", "lq2", "lq3"]
    names = sorted(p.name for p in (state / linked.DIR / linked.LINKS).iterdir())
    assert names == ["other.static.json", "prov.live.json", "prov.static.json"]
    keys = {q["key"] for q in linked.read_state(state)[0]}
    assert len(keys) == 3


def test_the_key_depends_on_the_pin_and_the_broken_set_only(tmp_path: Path) -> None:
    k = linked.key_of
    base = fake_row()
    assert k("prov", "cons", "static", base) == k("prov", "cons", "static", copy.deepcopy(base))
    assert k("prov", "cons", "static", base) != k("prov", "cons", "static", fake_row(pin="8" * 64))
    other = fake_row(broken=(("POST /x", "status_not_observed", "200"),))
    assert k("prov", "cons", "static", base) != k("prov", "cons", "static", other)
    shuffled = fake_row(broken=(("B", "k", "1"), ("A", "k", "2")))
    ordered = fake_row(broken=(("A", "k", "2"), ("B", "k", "1")))
    assert k("p", "c", "s", shuffled) == k("p", "c", "s", ordered)
    changed = copy.deepcopy(base)
    changed["findings"].append({"class": "CHANGED", "endpoint": "POST /x", "kind": "k", "item": "i",
                                "relied": True, "detail": "d"})  # fmt: skip
    assert k("prov", "cons", "static", changed) == k("prov", "cons", "static", base)


# ---- atomic and exclusive --------------------------------------------------------------------


def test_every_id_is_unique_and_contiguous_under_concurrent_delivery(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    n = 24
    barrier = threading.Barrier(n)
    out: list[Any] = [None] * n

    def one(i: int) -> None:
        row = fake_row(broken=((f"POST /e{i}", "status_not_observed", "201"),))
        barrier.wait()
        out[i] = linked.deliver(state, "w", row, CAND, SRC)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = sorted(int(r["question"][2:]) for r in out)
    assert ids == list(range(1, n + 1))
    files = sorted((state / linked.DIR).glob("lq*.json"))
    assert len(files) == n
    assert len({json.loads(p.read_text())["key"] for p in files}) == n
    assert len({json.loads(p.read_text())["imports_affected"][0] for p in files}) == n


def test_a_reader_never_sees_a_torn_file(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    big = tuple((f"POST /e{i}", "status_not_observed", "x" * 200) for i in range(400))
    linked.deliver(state, "w", fake_row(broken=big), CAND, SRC)
    stop = threading.Event()
    bad: list[str] = []
    seen = [0]

    def reader() -> None:
        paths = [state / linked.DIR / "lq1.json"]
        while not stop.is_set():
            for p in paths:
                try:
                    text = p.read_text()
                except OSError:
                    continue
                seen[0] += 1
                try:
                    doc = json.loads(text)
                    if not isinstance(doc, dict) or "findings" not in doc:
                        bad.append("partial object")
                except ValueError:
                    bad.append(f"torn: {len(text)} bytes")

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(60):
            doc = json.loads((state / linked.DIR / "lq1.json").read_text())
            doc["status"] = "open"
            doc["n"] = i
            # a resolve rewrites the question file as `clear` does
            linked._put(state / linked.DIR / "lq1.json", doc)
    finally:
        stop.set()
        t.join()
    assert seen[0] > 0 and bad == []


def test_a_reader_during_clearing_sees_whole_objects(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    big = tuple((f"POST /e{i}", "status_not_observed", "x" * 200) for i in range(400))
    for i in range(30):
        linked.deliver(state, "w", fake_row(broken=(*big, (f"POST /z{i}", "k", "i"))), CAND, SRC)
    stop = threading.Event()
    bad: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            for p in sorted((state / linked.DIR).glob("lq*.json")) + list(
                (state / linked.DIR / linked.LINKS).glob("*.json")
            ):
                try:
                    json.loads(p.read_text())
                except FileNotFoundError:
                    continue
                except ValueError:
                    bad.append(p.name)

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(1, 31):
            linked.clear(state, f"lq{i}", linked.RESOLVED, "n" * 100, "cli")
    finally:
        stop.set()
        t.join()
    assert bad == []
    assert {q["status"] for q in linked.read_state(state)[0]} == {"resolved"}


def test_clear_validates_before_touching_anything(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(linked.LinkedError) as e:
        linked.clear(state, "lq1", linked.RESOLVED, None, "cli")
    assert e.value.code == "unknown_question" and not (state / linked.DIR).exists()
    linked.deliver(state, "w", fake_row(), CAND, SRC)
    with pytest.raises(linked.LinkedError) as e:
        linked.clear(state, "lq1", linked.RESOLVED, 5, "cli")
    assert e.value.code == "bad_note"
    out = linked.clear(state, "lq1", linked.DISMISSED, None, "studio")
    assert out["resolution"]["note"] == "" and out["resolution"]["via"] == "studio"


def test_the_list_body_filters_by_status_and_rejects_a_bad_one(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    linked.deliver(state, "w", fake_row(), CAND, SRC)
    linked.deliver(state, "w", fake_row(broken=(("POST /y", "k", "i"),)), CAND, SRC)
    linked.clear(state, "lq1", linked.RESOLVED, "", "cli")
    body = linked.list_body("cons", "present", state, "open")
    assert [q["id"] for q in body["questions"]] == ["lq2"]
    assert body["counts"] == {"open": 1, "resolved": 1, "dismissed": 0, "superseded": 0}
    assert list(body) == ["format", "consumer", "state", "state_dir", "questions", "counts",
                          "problems", "legend"]  # fmt: skip
    with pytest.raises(linked.LinkedError) as e:
        linked.list_body("cons", "present", state, "bogus")
    assert (e.value.code, e.value.status) == ("bad_status", 422)
    disabled = linked.list_body("cons", "disabled", None)
    assert disabled["state"] == "disabled" and disabled["state_dir"] is None
    assert disabled["questions"] == []


def test_no_reader_of_a_gate_looks_at_linked_questions() -> None:
    """Advisory: the word appears only in the modules that deliver, list or document it."""
    root = Path(__file__).resolve().parents[2] / "lattice"
    out = subprocess.run(["grep", "-rlE", "linked_questions|linked-questions|import linked",
                          str(root), "--include=*.py"], capture_output=True, text=True,
                         check=False).stdout.split()  # fmt: skip
    rel = sorted(str(Path(p).relative_to(root)) for p in out)
    assert rel == [
        "contracts/__main__.py",
        "contracts/linked.py",
        "mcp/projects.py",
        "studio/mock_server.py",
        "studio/server.py",
    ]


def test_stdout_of_json_is_one_document(world: World, inbox: Path) -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = contracts_main(["notify", "prov", "--candidate", "brk", "--workspace",
                             str(world.root), "--json"])  # fmt: skip
    assert rc == 0 and json.loads(buf.getvalue())["consumers"][0]["question"] == "lq1"
