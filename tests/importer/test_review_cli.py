"""The rules import and its review (Phase 2 plan 2026-10-05, §5; W2-D):
``python -m lattice.importer ... --rules-only`` writes the rule layer into the report plus
``candidate_spec.py`` and ``proposals.json``; ``python -m lattice.importer review DIR``
lists, shows, accepts, rejects, edits and emits, every decision appended to
``review.jsonl`` (never rewritten); ``emit`` writes ``spec.py`` (validating, round-
tripping) and ``spec_meta.json``, consistent with the report.

Fixture import directory: one ``--rules-only`` run on the multi-vendor app (a
subprocess, as the CLI runs), plus a ``proposals.json`` of three proposals in W2-C's shape
(an invariant, a machine, an unchecked note), copied per test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lattice.importer.cli import main
from lattice.importer.emit_spec import load_spec, proposal_record, spec_source
from lattice.importer.report import SCHEMA
from lattice.spec import Invariant, Machine, Unchecked, validate

ROOT = Path(__file__).resolve().parents[2]
ARGS = [str(ROOT), "--package", "bench.import_repos.multivendor.app",
        "--app-factory", "bench.import_repos.multivendor:make_app"]  # fmt: skip


@dataclass(frozen=True)
class Proposal:
    kind: str
    spec_object: Any
    evidence: Any = ()
    support: int = 0
    source: str = ""


STOCK = Invariant("stock_nonneg", "A product's stock is never negative.",
                  "Product.stock_quantity >= 0")  # fmt: skip
EDGES = {
    "pending": ("confirmed", "cancelled"),
    "confirmed": ("shipped",),
    "shipped": ("delivered",),
}
MACHINE = Machine("Order", "status", EDGES)
NOTE = Unchecked("An order's total is the sum of its lines.", concerns=("Order",))
PROPOSALS = [
    Proposal(
        "invariant", STOCK, evidence=[{"rows": 8}], support=8, source="app/models/product.py:12"
    ),  # fmt: skip
    Proposal(
        "machine", MACHINE, support=3, source="app/services/order_service.py:VALID_TRANSITIONS"
    ),
    Proposal("unchecked", NOTE, source="app/services/order_service.py:40"),
]
RECORDS = [proposal_record(p) for p in PROPOSALS]
INV, MACH, UNCH = (r["id"] for r in RECORDS)


@pytest.fixture(scope="module")
def imported(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("rules") / "lattice_import"
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", *ARGS, "--out", str(out), "--rules-only",
         "--framework", "auto", "--no-llm", "--no-corpus"],
        cwd=ROOT, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    assert "rule items" in done.stdout
    (out / "proposals.json").write_text(json.dumps(RECORDS, indent=2) + "\n")
    return out


REFUTED = {
    # W3 fixed the importer's refutations of these two (a 403 carrying the forbidden
    # detail is a refusal; an owner_scope="any" list is not probed as scoped), so the
    # review workflow's refuted items are crafted here, as W2-D's import refuted them
    "R2 GET /products": ({"other_users_rows": [1, 3, 5, 7]},
                         "answered other users' Product rows [1, 3, 5, 7]"),
    "R1 POST /products": ({"allowed": ["Admin", "Buyer", "Vendor"]},
                          "callers answered against the auth: Admin admitted, Buyer admitted"),
}  # fmt: skip


@pytest.fixture
def d(imported, tmp_path) -> Path:
    target = tmp_path / "imp"
    shutil.copytree(imported, target)
    report = json.loads((target / "report.json").read_text())
    for item_id, (observed, reason) in REFUTED.items():
        record = report["rules"]["items"][item_id]
        record.update(verdict="refuted", observed=observed, reason=reason)
    (target / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return target


def review(d: Path, *args: str) -> int:
    return main(["review", str(d), "--user", "tester", *args])


def log_lines(d: Path) -> list[dict[str, Any]]:
    path = d / "review.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# ---- the rules import --------------------------------------------------------------------


def test_the_rules_import(imported):
    names = sorted(p.name for p in imported.iterdir())
    assert names == ["candidate_spec.py", "natives.py", "profile.py", "proposals.json",
                     "report.json", "report.md", "routes"]  # fmt: skip
    report = json.loads((imported / "report.json").read_text())
    assert report["schema"] == SCHEMA == "lattice-import-report/2"
    rules = report["rules"]
    assert rules["framework"] == "fastapi"
    assert report["coverage"]["rules"] == rules["summary"]
    assert sum(rules["summary"]["items"].values()) == len(rules["items"]) > 0
    assert rules["routes"] and rules["tables"]
    assert report["routes"] == []  # --rules-only lifts no behaviour
    candidate = load_spec(imported / "candidate_spec.py")
    assert validate(candidate.spec) == [] and len(candidate.spec.contracts) == 17
    # its STATUS: each rule item's verdict
    assert candidate.status == {i: v["verdict"] for i, v in rules["items"].items()}
    assert "## Rule layer" in (imported / "report.md").read_text()


def test_the_framework_flag(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main([*ARGS, "--out", str(tmp_path / "x"), "--rules-only", "--framework", "cobol"])
    assert "no front end 'cobol'" in capsys.readouterr().err


# ---- list and show -----------------------------------------------------------------------


def test_list_and_show(d, capsys):
    assert review(d, "list") == 0
    out = capsys.readouterr().out
    assert INV in out and MACH in out and UNCH in out
    assert "R2 GET /products" in out  # a refuted rule item, listed for review
    assert "R1 GET /orders/{order_id}" not in out  # verified: no review step
    assert review(d, "list", "--kind", "invariant") == 0
    out = capsys.readouterr().out
    assert INV in out and MACH not in out and "R2 " not in out
    assert review(d, "list", "--kind", "rule", "--status", "pending") == 0
    out = capsys.readouterr().out
    assert "R2 GET /products" in out and INV not in out
    assert review(d, "show", INV) == 0
    out = capsys.readouterr().out
    assert STOCK.expr in out and STOCK.sentence in out and "app/models/product.py:12" in out
    assert review(d, "show", "R2 GET /products") == 0
    out = capsys.readouterr().out
    assert "refuted" in out and "other users" in out
    assert review(d, "show", "nope") == 2


# ---- decisions ---------------------------------------------------------------------------


def test_review_jsonl_is_append_only(d, capsys):
    snapshots = []

    def step(*args: str, code: int = 0) -> None:
        assert review(d, *args) == code
        text = (d / "review.jsonl").read_text() if (d / "review.jsonl").exists() else ""
        if snapshots:
            assert text.startswith(snapshots[-1])  # never rewritten, only appended
        snapshots.append(text)

    step("accept", INV, MACH)
    step("reject", UNCH, "--reason", "not a requirement")
    step("reject", "R2 GET /products", code=2)  # a rejection needs a reason
    step("accept", "nope", code=2)  # an unknown id: nothing appended
    step("accept", INV, "nope", code=2)  # all or nothing
    step("reject", INV, "--reason", "changed my mind")  # a later decision wins
    lines = log_lines(d)
    assert [(x["id"], x["decision"]) for x in lines] == [
        (INV, "accept"), (MACH, "accept"), (UNCH, "reject"), (INV, "reject"),
    ]  # fmt: skip
    assert all(x["user"] == "tester" and x["time"] for x in lines)
    assert lines[2]["reason"] == "not a requirement"
    capsys.readouterr()
    assert review(d, "list", "--status", "rejected") == 0
    out = capsys.readouterr().out
    assert INV in out and UNCH in out and MACH not in out


def test_accept_reject_and_emit(d):
    assert review(d, "accept", INV, MACH) == 0
    assert review(d, "reject", UNCH, "--reason", "covered elsewhere") == 0
    # a rule item: rejected (dropped from the spec), and a refuted one kept on purpose
    assert review(d, "reject", "R3 POST /orders 409", "--reason", "stock, not a rule") == 0
    assert review(d, "accept", "R1 POST /products") == 0
    assert review(d, "emit") == 0
    spec_py = d / "spec.py"
    loaded = load_spec(spec_py)
    spec = loaded.spec
    assert validate(spec) == []
    assert spec.invariants == (STOCK,) and spec.machines == (MACHINE,) and spec.unchecked == ()
    c = {x.endpoint: x for x in spec.contracts}
    assert [e.status for e in c["POST /orders"].errors] == [422]  # the 409 rejected
    assert c["POST /products"].auth == "Roles(Vendor)"  # refuted, accepted: kept
    assert c["GET /products"].owner_scope == "unknown"  # refuted, not accepted: dropped
    text = spec_py.read_text()
    assert "# R1 GET /orders/{order_id}: verified" in text
    assert "# proposed-accepted" in text
    # round trip: the module, loaded and emitted again, is the same text
    again = spec_source(spec, status=loaded.status, package=loaded.package,
                        profile_file=loaded.profile_file)  # fmt: skip
    assert again == text
    meta = json.loads((d / "spec_meta.json").read_text())
    assert meta["items"]["R3 POST /orders 409"]["status"] == "rejected"
    assert meta["items"]["R2 GET /products"]["status"] == "dropped"
    assert meta["items"]["R1 POST /products"]["status"] == "inferred"
    assert meta["proposals"][INV]["status"] == "proposed-accepted"
    assert meta["proposals"][UNCH]["status"] == "rejected"


def test_edit_is_reprobed_before_it_can_be_accepted(d):
    # an expression that does not compile: refused, nothing recorded
    assert review(d, "edit", INV, "--expr", "Product.nope >= 0") == 2
    assert log_lines(d) == []
    # one the app breaks (every new user row has an id >= 0): re-probed, refuted
    assert review(d, "edit", INV, "--expr", "User.id < 0") == 0
    edit = log_lines(d)[-1]
    assert edit["decision"] == "edit" and edit["reprobe"]["ok"] is False
    assert edit["reprobe"]["violations"] > 0 and edit["reprobe"]["examples"]
    assert review(d, "accept", INV) == 2  # the re-probe refuted it
    # a sound revision: re-probed clean, then accepted
    assert review(d, "edit", INV, "--expr", "Product.price >= 0") == 0
    assert log_lines(d)[-1]["reprobe"]["ok"] is True
    assert review(d, "accept", INV) == 0
    assert review(d, "emit") == 0
    inv = load_spec(d / "spec.py").spec.invariants
    assert [i.expr for i in inv] == ["Product.price >= 0"]
    # edits apply to invariant proposals only
    assert review(d, "edit", MACH, "--expr", "Order.id >= 0") == 2


def test_status_and_the_report_agree_with_spec_meta(d, capsys):
    assert review(d, "status") == 0
    assert "not emitted" in capsys.readouterr().out
    assert review(d, "accept", INV) == 0
    assert review(d, "emit", "--out", str(d / "out" / "spec.py")) == 0
    assert (d / "out" / "profile.py").read_text() == (d / "profile.py").read_text()
    assert review(d, "status", "--out", str(d / "out" / "spec.py")) == 0
    out = capsys.readouterr().out
    assert "current" in out and "stale" not in out
    report = json.loads((d / "report.json").read_text())
    meta = json.loads((d / "out" / "spec_meta.json").read_text())
    items = report["rules"]["items"]
    assert set(meta["items"]) == set(items)
    for item_id, v in items.items():
        assert meta["items"][item_id]["verdict"] == v["verdict"], item_id
        expected = {"verified": "verified", "refuted": "dropped"}.get(v["verdict"], "inferred")
        assert meta["items"][item_id]["status"] == expected, item_id
    assert meta["rules_summary"] == report["rules"]["summary"]
    counts: dict[str, int] = {}
    for v in meta["items"].values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    assert meta["counts"]["items"] == counts
    # a later decision makes the emitted spec stale
    assert review(d, "reject", MACH, "--reason", "no") == 0
    assert review(d, "status", "--out", str(d / "out" / "spec.py")) == 0
    assert "stale" in capsys.readouterr().out


def test_review_through_python_m(d):
    done = subprocess.run(
        [sys.executable, "-m", "lattice.importer", "review", str(d), "status"],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert "proposals" in done.stdout
