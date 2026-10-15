"""``lattice contracts impact --transitive`` (multi-project slice 4c): the declared-reachability
tier below a first-order contradiction. Roots (contradiction rows only), downstream rows (BFS over
opted-in links, any depth, cycles, diamonds), the row and link shape, the notes, the additive
JSON and text, the unchanged exit codes and counts, and the read-only / no-export guarantee, over
synthetic worlds under ``tmp_path`` (tests/contracts/helpers.py). Pre-registration:
docs/results/2026-10-15-transitive-impact-prereg.md."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lattice import workspace as wsmod
from lattice.contracts import check as ck
from lattice.contracts import impact
from lattice.contracts import transitive as tr
from lattice.contracts.__main__ import main as contracts_main
from lattice.workspace import Link, Workspace, WsProject

from .helpers import BASE, IMPORTS, copy_data, git, git_init, imports_text, st, write_spec

TOP_KEYS = {
    "format", "workspace", "provider", "candidate", "current", "advisory", "static_only",
    "baseline", "checked", "consumers", "counts", "problems", "exit_code", "elapsed_s",
}  # fmt: skip
TR_KEYS = {"basis", "verified", "propagates_from", "roots", "downstream", "notes", "counts"}
ROW_KEYS = {"consumer", "label", "depth", "via", "roots", "links"}
LINK_KEYS = {"provider", "contract", "relies_on", "pinned", "notes"}
BUILDS = {"endpoint": "GET /builds/{}", "status": [200], "auth": "public",
          "response_fields": ["state"]}  # fmt: skip
BUILD_SPEC = [("build", [st("GET", "/builds/1", None, 200, expect_body={"id": 1, "state": "ok"})])]
RUN = "POST /jobs/{}/runs"


# ---- the world --------------------------------------------------------------------------------


def lines(links: list[tuple[str, ...]]) -> str:
    out = ""
    for lk in links:
        src, dst = lk[0], lk[1]
        kind = lk[2] if len(lk) > 2 else "uses"
        contract = lk[3] if len(lk) > 3 else "static"
        out += f'[[link]]\nfrom = "{src}"\nto = "{dst}"\nkind = "{kind}"\n'
        if contract:
            out += f'contract = "{contract}"\n'
        out += "\n"
    return out


class Chain:
    """prov (git, BASE; branches brk / reg / add / login) <- b (pinned on prov, itself a provider
    with a one-endpoint spec) <- c (relies on b ``GET /builds/{}``, pinned); b2 (relies on prov's
    login and jobs list), dash (relies on b and b2, pinned on both), dash2 and ops (empty
    directories). ``manifest`` writes a manifest variant over these directories."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        names = ("prov", "b", "b2", "c", "dash", "dash2", "ops", "x")
        self.dirs: dict[str, Path] = {n: root / n for n in names}
        for name in ("dash2", "ops", "x"):
            self.dirs[name].mkdir()
        write_spec(self.dirs["prov"], BASE)
        git_init(self.dirs["prov"])
        branches = [("brk", self.status_201_to_200()), ("reg", self.relied_added()),
                    ("add", self.endpoint_added()), ("login", self.login_field_gone())]  # fmt: skip
        for name, data in branches:
            git(self.dirs["prov"], "checkout", "-q", "-b", name)
            write_spec(self.dirs["prov"], data)
            git(self.dirs["prov"], "add", "-A")
            git(self.dirs["prov"], "commit", "-qm", name)
            git(self.dirs["prov"], "checkout", "-q", "-")
        for name in ("b", "b2", "c", "dash"):
            self.dirs[name].mkdir()
        write_spec(self.dirs["b"], BUILD_SPEC)
        write_spec(self.dirs["b2"], BUILD_SPEC)
        (self.dirs["b"] / ck.IMPORTS_FILE).write_text(imports_text(*IMPORTS))
        (self.dirs["b2"] / ck.IMPORTS_FILE).write_text(imports_text(IMPORTS[0], IMPORTS[1]))
        (self.dirs["c"] / ck.IMPORTS_FILE).write_text(imports_text({**BUILDS, "provider": "b"}))
        (self.dirs["dash"] / ck.IMPORTS_FILE).write_text(
            imports_text({**BUILDS, "provider": "b"}, {**BUILDS, "provider": "b2"},
                         IMPORTS[1]))  # fmt: skip
        # one manifest holds every link a pin needs
        full = self.manifest("pins", [("b", "prov"), ("b2", "prov"), ("c", "b"), ("dash", "b"),
                                      ("dash", "b2"), ("dash", "prov")])  # fmt: skip
        for consumer in ("b", "b2", "c", "dash"):
            rc, _out = self.cli("pin", consumer, workspace=full)
            assert rc == 0, (consumer, _out)

    # -- candidates
    @staticmethod
    def status_201_to_200() -> list[tuple[str, list[dict]]]:
        data = copy_data(BASE)
        for s in data[1][1]:
            if s["method"] == "POST" and s["path"] == "/jobs/7/runs" and s["expect_status"] == 201:
                s["expect_status"] = 200
        return data

    @staticmethod
    def relied_added() -> list[tuple[str, list[dict]]]:
        data = copy_data(BASE)
        data[1][1].append(st("GET", "/jobs/7", "bob", 403))
        return data

    @staticmethod
    def endpoint_added() -> list[tuple[str, list[dict]]]:
        data = copy_data(BASE)
        data[1][1].append(st("GET", "/health", None, 200, expect_body={"ok": True}))
        return data

    @staticmethod
    def login_field_gone() -> list[tuple[str, list[dict]]]:
        data = copy_data(BASE)
        data[0][1][0]["expect_body"] = {"token_type": "bearer"}
        return data

    # -- manifests and calls
    def manifest(self, name: str, links: list[tuple[str, ...]], extra: list[str] = (),
                 paths: dict[str, Path] | None = None) -> Path:  # fmt: skip
        """``<root>/m-<name>/lattice.workspace.toml`` over every directory (a name in `paths`
        points elsewhere); `links` are ``(from, to[, kind[, contract]])`` (contract default
        ``static``; ``""`` for none)."""
        paths = paths or {}
        names = [*self.dirs, *extra]
        text = '[workspace]\nname = "w"\n\n'
        for n in names:
            p = paths.get(n) or self.dirs.get(n) or self.root / n
            p.mkdir(exist_ok=True)
            spec = 'spec = "spec:SPEC"\n' if n in ("prov", "b", "b2") else ""
            text += f'[[project]]\nname = "{n}"\npath = "{p}"\nrole = "app"\n{spec}\n'
        d = self.root / f"m-{name}"
        d.mkdir(exist_ok=True)
        text += lines(list(links))
        (d / wsmod.MANIFEST).write_text(text)
        return d / wsmod.MANIFEST

    def cli(self, *argv: str, workspace: Path) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = contracts_main([*argv, "--workspace", str(workspace)])
        return rc, buf.getvalue()

    def impact(self, workspace: Path, candidate: str, *flags: str, provider: str = "prov",
               json_out: bool = True) -> tuple[int, Any]:  # fmt: skip
        argv = ["impact", provider, "--candidate", candidate, *flags]
        rc, out = self.cli(*argv, *(["--json"] if json_out else []), workspace=workspace)
        return rc, (json.loads(out) if json_out else out)


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Chain:
    return Chain(tmp_path_factory.mktemp("transitive") / "w")


LINKS = [("b", "prov"), ("c", "b"), ("ops", "b", "uses", "")]


@pytest.fixture(scope="module")
def main_ws(chain: Chain) -> Path:
    return chain.manifest("main", LINKS)


def calls(chain: Chain, ws: Path, cand: str, provider: str = "prov") -> dict[str, Any]:
    """The four answers of one window: JSON and text, with and without the flag."""
    out: dict[str, Any] = {}
    for flag in (False, True):
        fl = ("--transitive",) if flag else ()
        out[flag, "json"] = chain.impact(ws, cand, *fl, provider=provider)
        out[flag, "text"] = chain.impact(ws, cand, *fl, provider=provider, json_out=False)
    return out


@pytest.fixture(scope="module")
def brk(chain: Chain, main_ws: Path) -> dict[str, Any]:
    return calls(chain, main_ws, "brk")


def drop_elapsed(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "elapsed_s"}


def rows_of(doc: dict[str, Any]) -> list[str]:
    return [r["consumer"] for r in doc["transitive"]["downstream"]]


def tree_hashes(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---- the document -----------------------------------------------------------------------------


def test_a_contradiction_gives_a_root_and_its_dependent_downstream(
    chain: Chain, brk: dict[str, Any]
) -> None:
    rc, doc = brk[True, "json"]
    assert rc == 1 and doc["exit_code"] == 1
    t = doc["transitive"]
    assert set(t) == TR_KEYS
    assert (t["basis"], t["verified"], t["propagates_from"]) == (
        "declared_links", False, ["contradiction"])  # fmt: skip
    assert t["roots"] == [{"consumer": "b", "endpoints": [RUN], "dependents": ["c"]}]
    assert t["notes"] == [] and t["counts"] == {"roots": 1, "downstream": 1}
    (row,) = t["downstream"]
    assert set(row) == ROW_KEYS and set(row["links"][0]) == LINK_KEYS
    lock = json.loads((chain.dirs["c"] / ck.LOCK_FILE).read_text())["providers"]["b"]
    assert row == {
        "consumer": "c", "label": "downstream", "depth": 2, "via": ["b"], "roots": ["b"],
        "links": [{"provider": "b", "contract": "static", "relies_on": ["GET /builds/{}"],
                   "pinned": ck.version_of(lock), "notes": []}],
    }  # fmt: skip


def test_the_first_order_answer_is_untouched_by_the_flag(brk: dict[str, Any]) -> None:
    (_, off), (rc, on) = brk[False, "json"], brk[True, "json"]
    assert set(off) == TOP_KEYS and "transitive" not in off
    assert list(on).index("transitive") == list(on).index("problems") - 1
    assert drop_elapsed({k: v for k, v in on.items() if k != "transitive"}) == drop_elapsed(off)
    assert on["counts"] == {"contradiction": 1, "regression": 0, "unaffected": 0, "error": 0}
    assert on["checked"] == 1 and [r["consumer"] for r in on["consumers"]] == ["b"]
    assert {r["label"] for r in on["consumers"]} == {"contradiction"}
    assert brk[False, "json"][0] == rc == 1


def test_the_text_is_the_flag_off_text_plus_one_block(brk: dict[str, Any]) -> None:
    (_, off), (_, on) = brk[False, "text"], brk[True, "text"]
    assert on.startswith(off) and "downstream (" not in off
    block = on[len(off) :].splitlines()
    assert block[0] == ("downstream (declared links, unverified; from contradiction only): "
                        "1 root(s), 1 project(s)")  # fmt: skip
    assert block[1] == "c  DOWNSTREAM  depth 2 via b (roots: b)"
    assert block[2].startswith("  b static: relies on GET /builds/{}; pinned ")
    assert "no pin" not in block[2] and len(block) == 3


def test_downstream_never_enters_counts_labels_or_the_consumers(brk: dict[str, Any]) -> None:
    _rc, doc = brk[True, "json"]
    assert all(r["label"] in impact.LABELS for r in doc["consumers"])
    assert "c" not in [r["consumer"] for r in doc["consumers"]]
    assert sum(doc["counts"].values()) == doc["checked"]


def test_the_ops_link_without_a_contract_is_not_followed(brk: dict[str, Any]) -> None:
    _rc, doc = brk[True, "json"]
    assert "ops" not in json.dumps(doc["transitive"])


@pytest.mark.parametrize("cand", ["reg", "add", "HEAD"])
def test_only_a_contradiction_propagates(chain: Chain, main_ws: Path, cand: str) -> None:
    rc, doc = chain.impact(main_ws, cand, "--transitive")
    assert rc == 0
    label = doc["consumers"][0]["label"]
    assert label == {"reg": "regression", "add": "unaffected", "HEAD": "unaffected"}[cand]
    assert doc["transitive"] == tr.empty()


# ---- the graph: depth, cycles, diamonds ---------------------------------------------------------


def test_depth_has_no_cap_and_each_row_follows_its_predecessor(chain: Chain) -> None:
    extra = ["p2", "p3", "p4", "p5"]
    links = [*LINKS, ("p2", "b"), ("p3", "p2"), ("p4", "p3"), ("p5", "p4")]
    ws = chain.manifest("long", links, extra)
    rc, doc = chain.impact(ws, "brk", "--transitive")
    assert rc == 1
    rows = doc["transitive"]["downstream"]
    assert [(r["consumer"], r["depth"]) for r in rows] == [
        ("c", 2), ("p2", 2), ("p3", 3), ("p4", 4), ("p5", 5)]  # fmt: skip
    p4, p5 = rows[3], rows[4]
    assert (p4["via"], p4["roots"]) == (["p3"], ["b"])
    link = p5["links"][0]
    assert link["provider"] == "p4" and link["relies_on"] is None and link["pinned"] is None
    assert link["notes"] == ["no_imports", "no_pin"]
    _rc, doc = chain.impact(ws, "add", "--transitive")
    assert doc["transitive"]["downstream"] == []


def test_a_cycle_through_the_root_lists_each_project_once(chain: Chain) -> None:
    ws = chain.manifest("cyc-a", [*LINKS, ("b", "c")])
    rc, doc = chain.impact(ws, "brk", "--transitive")
    assert rc == 1 and rows_of(doc) == ["c"]
    assert [r["consumer"] for r in doc["transitive"]["roots"]] == ["b"]
    assert doc["transitive"]["downstream"][0]["via"] == ["b"]


def test_the_provider_is_never_a_row_and_a_cycle_through_it_is_noted(chain: Chain) -> None:
    ws = chain.manifest("cyc-b", [*LINKS, ("prov", "b")])
    rc, doc = chain.impact(ws, "brk", "--transitive")
    assert rc == 1 and rows_of(doc) == ["c"]
    (note,) = doc["transitive"]["notes"]
    assert note["code"] == "provider_downstream" and "b" in note["message"]
    assert doc["transitive"]["roots"][0]["dependents"] == ["c"]  # prov (the provider) excluded
    assert [p["code"] for p in doc["problems"]] == []


def test_mutual_links_below_the_root_are_each_listed_once_and_exposed(chain: Chain) -> None:
    ws = chain.manifest("cyc-c", [*LINKS, ("x", "c"), ("c", "x")])
    _rc, doc = chain.impact(ws, "brk", "--transitive")
    rows = {r["consumer"]: r for r in doc["transitive"]["downstream"]}
    assert list(rows) == ["c", "x"] and len(doc["transitive"]["downstream"]) == 2
    assert (rows["c"]["depth"], rows["x"]["depth"]) == (2, 3)
    assert rows["c"]["via"] == ["b", "x"] and rows["x"]["via"] == ["c"]
    assert rows["c"]["roots"] == rows["x"]["roots"] == ["b"]
    assert [lk["provider"] for lk in rows["c"]["links"]] == ["b", "x"]


def test_a_depends_on_cycle_is_a_manifest_error_with_nothing_downstream(chain: Chain) -> None:
    ws = chain.manifest(
        "cyc-d", [*LINKS, ("x", "c", "depends_on", ""), ("c", "x", "depends_on", "")]
    )
    rc_on, on = chain.impact(ws, "brk", "--transitive")
    rc_off, off = chain.impact(ws, "brk")
    assert rc_on == rc_off == 2 and on["consumers"] == [] and on["transitive"] == tr.empty()
    assert [p["code"] for p in on["problems"]] == ["depends_on_cycle"] == [
        p["code"] for p in off["problems"]]  # fmt: skip


def test_a_diamond_is_one_row_with_every_exposed_provider(chain: Chain) -> None:
    both = [("b", "prov"), ("b2", "prov"), ("dash", "b"), ("dash", "b2")]
    ws = chain.manifest("dia", both)
    _rc, a = chain.impact(ws, "brk", "--transitive")  # b contradiction, b2 unaffected
    labels = {r["consumer"]: r["label"] for r in a["consumers"]}
    assert labels == {"b": "contradiction", "b2": "unaffected"}
    (row,) = a["transitive"]["downstream"]
    assert (row["consumer"], row["depth"], row["via"], row["roots"]) == ("dash", 2, ["b"], ["b"])
    assert [lk["provider"] for lk in row["links"]] == ["b"]
    rc, b = chain.impact(ws, "login", "--transitive")  # both contradiction
    assert rc == 1 and [r["consumer"] for r in b["transitive"]["roots"]] == ["b", "b2"]
    (row,) = b["transitive"]["downstream"]
    assert (row["via"], row["roots"]) == (["b", "b2"], ["b", "b2"])
    assert [lk["provider"] for lk in row["links"]] == ["b", "b2"]
    assert all(lk["relies_on"] == ["GET /builds/{}"] and lk["notes"] == [] for lk in row["links"])
    assert b["transitive"]["counts"] == {"roots": 2, "downstream": 1}
    _rc, c = chain.impact(ws, "reg", "--transitive")
    assert c["transitive"] == tr.empty()


def test_a_first_order_consumer_that_is_downstream_keeps_both_answers(chain: Chain) -> None:
    ws = chain.manifest("dia-d", [("b", "prov"), ("dash", "b"), ("dash", "prov")])
    _rc, doc = chain.impact(ws, "brk", "--transitive")
    labels = {r["consumer"]: r["label"] for r in doc["consumers"]}
    assert labels == {"b": "contradiction", "dash": "unaffected"} and doc["checked"] == 2
    assert rows_of(doc) == ["dash"] and doc["transitive"]["downstream"][0]["via"] == ["b"]


def test_every_exposed_link_is_listed_not_only_the_bfs_parents(chain: Chain) -> None:
    ws = chain.manifest("dia-e", [("b", "prov"), ("c", "b"), ("dash", "b"), ("dash2", "b"),
                                  ("dash2", "c")])  # fmt: skip
    _rc, doc = chain.impact(ws, "brk", "--transitive")
    rows = {r["consumer"]: r for r in doc["transitive"]["downstream"]}
    assert list(rows) == ["c", "dash", "dash2"]
    assert {r["depth"] for r in rows.values()} == {2}
    assert rows["dash2"]["via"] == ["b", "c"] and rows["dash2"]["roots"] == ["b"]
    assert [lk["notes"] for lk in rows["dash2"]["links"]] == [["no_imports", "no_pin"]] * 2


# ---- the link notes, the errors ---------------------------------------------------------------


def variant(chain: Chain, tmp_path: Path, name: str, **drop: bool) -> Path:
    """A copy of project `name` (without its lock when ``lock=False``, with a bad imports file
    when ``imports="bad"``)."""
    dst = tmp_path / f"{name}-{'-'.join(sorted(drop))}"
    shutil.copytree(chain.dirs[name], dst)
    if drop.get("lock") is False:
        (dst / ck.LOCK_FILE).unlink()
    if drop.get("bad_imports"):
        (dst / ck.IMPORTS_FILE).write_text("this is = not [valid toml\n")
    if drop.get("bad_lock"):
        (dst / ck.LOCK_FILE).write_text("{not json")
    return dst


def test_a_missing_lock_is_a_note_and_never_changes_the_exit(chain: Chain, tmp_path: Path) -> None:
    paths = {"c": variant(chain, tmp_path, "c", lock=False)}
    ws = chain.manifest("nolock", LINKS, paths=paths)
    rc, doc = chain.impact(ws, "brk", "--transitive")
    (row,) = doc["transitive"]["downstream"]
    link = row["links"][0]
    assert rc == 1 and doc["exit_code"] == 1 and doc["problems"] == []
    assert (link["pinned"], link["notes"], link["relies_on"]) == (None, ["no_pin"],
                                                                  ["GET /builds/{}"])  # fmt: skip
    _rc, off = chain.impact(ws, "brk")
    assert drop_elapsed({k: v for k, v in doc.items() if k != "transitive"}) == drop_elapsed(off)


def test_a_bad_lock_and_a_bad_imports_file_are_notes_too(chain: Chain, tmp_path: Path) -> None:
    ws = chain.manifest("badimp", LINKS,
                        paths={"c": variant(chain, tmp_path, "c", bad_imports=True)})  # fmt: skip
    rc, doc = chain.impact(ws, "brk", "--transitive")
    link = doc["transitive"]["downstream"][0]["links"][0]
    assert rc == 1 and link["relies_on"] is None and link["notes"] == ["bad_imports"]
    assert link["pinned"] is not None
    ws = chain.manifest("badlock", LINKS,
                        paths={"c": variant(chain, tmp_path, "c", bad_lock=True)})  # fmt: skip
    rc, doc = chain.impact(ws, "brk", "--transitive")
    link = doc["transitive"]["downstream"][0]["links"][0]
    assert rc == 1 and link["pinned"] is None and link["notes"] == ["bad_pin"]
    assert link["relies_on"] == ["GET /builds/{}"]
    _rc, text = chain.impact(ws, "brk", "--transitive", json_out=False)
    assert "pinned no pin [bad_pin]" in text and "relies on GET /builds/{}" in text


def test_a_root_in_error_does_not_propagate_and_is_noted(chain: Chain, tmp_path: Path) -> None:
    ws = chain.manifest("rooterr", LINKS, paths={"b": variant(chain, tmp_path, "b", lock=False)})
    rc, doc = chain.impact(ws, "brk", "--transitive")
    assert rc == 2 and doc["consumers"][0]["error"]["code"] == "no_pin"
    t = doc["transitive"]
    assert t["roots"] == [] and t["downstream"] == []
    assert t["counts"] == {"roots": 0, "downstream": 0}
    (note,) = t["notes"]
    assert (
        note["code"] == "root_error" and "b" in note["message"] and "1 dependent" in note["message"]
    )
    assert doc["problems"] == []  # the tier's notes never enter `problems`


def test_a_live_link_is_listed_and_nothing_is_run(chain: Chain) -> None:
    ws = chain.manifest("live", [("b", "prov"), ("c", "b", "uses", "live")])
    rc, doc = chain.impact(ws, "brk", "--transitive")
    assert rc == 1
    (row,) = doc["transitive"]["downstream"]
    assert row["links"][0]["contract"] == "live" and row["links"][0]["relies_on"] == [
        "GET /builds/{}"]  # fmt: skip


def test_unknown_provider_and_a_provider_without_dependents(chain: Chain, main_ws: Path) -> None:
    rc, doc = chain.impact(main_ws, "HEAD", "--transitive", provider="nobody")
    assert rc == 2 and [p["code"] for p in doc["problems"]] == ["unknown_project"]
    assert doc["transitive"] == tr.empty()
    rc, doc = chain.impact(main_ws, "HEAD", "--transitive", provider="c")
    assert rc == 0 and [p["code"] for p in doc["problems"]] == ["no_consumers"]
    assert doc["transitive"] == tr.empty() and doc["consumers"] == []


def test_a_stale_pin_gives_downstream_rows_for_head_too(chain: Chain, tmp_path: Path) -> None:
    stale = tmp_path / "stale"
    shutil.copytree(chain.dirs["prov"], stale)
    git(stale, "checkout", "-q", "brk")
    ws = chain.manifest("stale", LINKS, paths={"prov": stale})
    rc, doc = chain.impact(ws, "HEAD", "--transitive")
    assert rc == 1 and doc["consumers"][0]["current_label"] == "contradiction"
    assert rows_of(doc) == ["c"]


# ---- the guarantees ---------------------------------------------------------------------------


def test_the_flag_changes_nothing_else_for_any_candidate(chain: Chain, main_ws: Path) -> None:
    for cand in ("brk", "reg", "add", "HEAD", "no-such-ref"):
        off = chain.impact(main_ws, cand)
        on = chain.impact(main_ws, cand, "--transitive")
        assert on[0] == off[0], cand
        stripped = {k: v for k, v in on[1].items() if k != "transitive"}
        assert drop_elapsed(stripped) == drop_elapsed(off[1]), cand
        t_off = chain.impact(main_ws, cand, json_out=False)
        t_on = chain.impact(main_ws, cand, "--transitive", json_out=False)
        assert t_on[1].startswith(t_off[1]), cand
        assert t_on[1][len(t_off[1]) :].startswith("downstream ("), cand


def test_the_answer_is_deterministic(chain: Chain, main_ws: Path) -> None:
    runs = [chain.impact(main_ws, "brk", "--transitive") for _ in range(3)]
    docs = [json.dumps(drop_elapsed(d), sort_keys=True) for _rc, d in runs]
    assert len(set(docs)) == 1
    texts = {chain.impact(main_ws, "brk", "--transitive", json_out=False)[1] for _ in range(2)}
    assert len(texts) == 1


def test_nothing_is_written_and_no_export_is_added(
    chain: Chain, main_ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argvs: list[list[str]] = []
    real = subprocess.run

    def spy(argv: Any, *a: Any, **kw: Any) -> Any:
        argvs.append([str(x) for x in argv])
        return real(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    before = tree_hashes(chain.root)
    counts = []
    for flags in ((), ("--transitive",)):
        argvs.clear()
        chain.impact(main_ws, "brk", *flags)
        children = [a for a in argvs if "lattice.contracts" in a and "export" in a]
        gits = {a[a.index("--no-optional-locks") + 1] for a in argvs if a[:1] == ["git"]}
        counts.append((len(children), gits))
    assert tree_hashes(chain.root) == before
    assert counts[0] == counts[1] and counts[0][0] == 2
    assert counts[1][1] <= {"rev-parse", "ls-tree", "cat-file", "status"}


def test_the_library_call_equals_the_cli(chain: Chain, main_ws: Path) -> None:
    rc, doc = chain.impact(main_ws, "brk", "--transitive")
    ws = wsmod.parse(main_ws)
    rep = impact.run_impact(ws, "prov", "brk", transitive=True)
    assert rep.exit_code == rc
    assert drop_elapsed(json.loads(json.dumps(rep.to_dict()))) == drop_elapsed(doc)
    plain = impact.run_impact(ws, "prov", "brk")
    assert plain.transitive is None and "transitive" not in plain.to_dict()


def test_the_flag_exists_on_impact_only(chain: Chain, main_ws: Path) -> None:
    with pytest.raises(SystemExit) as e:
        contracts_main(["notify", "prov", "--candidate", "brk", "--transitive",
                        "--workspace", str(main_ws)])  # fmt: skip
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        contracts_main(["check", "b", "--transitive", "--workspace", str(main_ws)])


# ---- the pure function --------------------------------------------------------------------------


def synthetic(tmp_path: Path, names: list[str], links: list[tuple[str, str, str | None]]
              ) -> Workspace:  # fmt: skip
    projects = [WsProject(n, tmp_path / n) for n in names]
    return Workspace("w", tmp_path / wsmod.MANIFEST, projects,
                     [Link(s, t, "uses", contract=c) for s, t, c in links])  # fmt: skip


def row(consumer: str, label: str | None, *broken: str, error: str | None = None) -> dict:
    return {"consumer": consumer, "label": label,
            "error": {"code": error, "message": ""} if error else None,
            "findings": [{"class": "BROKEN", "endpoint": e} for e in broken]}  # fmt: skip


def test_a_chain_of_twelve_has_a_row_per_project_at_its_distance(tmp_path: Path) -> None:
    names = [f"p{i}" for i in range(12)]
    links = [(names[i + 1], names[i], "static") for i in range(11)]
    ws = synthetic(tmp_path, names, links)
    doc = tr.downstream(ws, [row("p1", "contradiction", "GET /a")], "p0")
    assert [r["depth"] for r in doc["downstream"]] == list(range(2, 12))
    assert [r["consumer"] for r in doc["downstream"]] == names[2:]
    assert doc["counts"] == {"roots": 1, "downstream": 10}


def test_two_roots_share_the_rows_below_them(tmp_path: Path) -> None:
    ws = synthetic(tmp_path, ["a", "r1", "r2", "x", "y"],
                   [("r1", "a", "static"), ("r2", "a", "static"), ("x", "r1", "static"),
                    ("x", "r2", "live"), ("y", "x", "static")])  # fmt: skip
    rows = [row("r1", "contradiction", "GET /b", "GET /a"), row("r2", "contradiction", "GET /c")]
    doc = tr.downstream(ws, rows, "a")
    assert [(r["consumer"], r["depth"], r["via"], r["roots"]) for r in doc["downstream"]] == [
        ("x", 2, ["r1", "r2"], ["r1", "r2"]), ("y", 3, ["x"], ["r1", "r2"])]  # fmt: skip
    assert doc["roots"][0] == {"consumer": "r1", "endpoints": ["GET /a", "GET /b"],
                               "dependents": ["x"]}  # fmt: skip
    assert [lk["contract"] for lk in doc["downstream"][0]["links"]] == ["static", "live"]


def test_regression_unaffected_and_error_rows_are_not_roots(tmp_path: Path) -> None:
    ws = synthetic(tmp_path, ["a", "r", "s", "e", "d"],
                   [("r", "a", "static"), ("s", "a", "static"), ("e", "a", "static"),
                    ("d", "r", "static"), ("d", "s", "static"), ("d", "e", "static")])  # fmt: skip
    rows = [row("r", "regression"), row("s", "unaffected"), row("e", None, error="no_pin")]
    doc = tr.downstream(ws, rows, "a")
    assert doc["roots"] == [] and doc["downstream"] == []
    (note,) = doc["notes"]
    assert note["code"] == "root_error" and "e:" in note["message"]


def test_an_invalid_manifest_has_no_roots_and_no_rows(tmp_path: Path) -> None:
    ws = synthetic(tmp_path, ["a", "r", "d"], [("r", "a", "static"), ("d", "r", "static")])
    ws.problems.append(wsmod.Problem("depends_on_cycle", "x"))
    doc = tr.downstream(ws, [row("r", "contradiction", "GET /a")], "a")
    assert doc == tr.empty()


def test_unknown_ends_and_self_links_and_no_contract_are_not_edges(tmp_path: Path) -> None:
    ws = synthetic(
        tmp_path,
        ["a", "r", "d", "n"],
        [
            ("r", "a", "static"),
            ("d", "r", "static"),
            ("n", "r", None),
            ("ghost", "r", "static"),
            ("r", "r", "static"),
            ("d", "nobody", "static"),
        ],
    )
    doc = tr.downstream(ws, [row("r", "contradiction", "GET /a")], "a")
    assert [r["consumer"] for r in doc["downstream"]] == ["d"]
