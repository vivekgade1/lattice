"""T1-4 (real-model C-ASK on Streak, corrected citation definition): the driver's pure parts, offline.

docs/results/2026-10-30-t14-casko-corrected-citation-prereg.md. The audit (section 4) on synthetic digests; the shim's cap and
ledger with a fake ``claude``; the pre-call guard on a scratch git repository; and, when the Streak repository and Go are on
this machine, the whole dry run (fake model answering from the gold key, and with wrong citations). No model call anywhere."""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dogfood"))

import t14_casko_audit as A  # noqa: E402
import t14_casko_run as R  # noqa: E402

from lattice.spec.ask import Element  # noqa: E402

SHIM = ROOT / "dogfood" / "t14_casko_shim.py"
FAKE = ROOT / "dogfood" / "t14_casko_fake_claude.py"


# ---- a synthetic digest -----------------------------------------------------------------------------------------------------------------


@pytest.fixture()
def resolver(tmp_path: Path) -> A.Resolver:
    root = tmp_path / "app"
    (root / "internal" / "sync").mkdir(parents=True)
    (root / "internal" / "sync" / "routes.go").write_text("".join(f"// line {i}\n" for i in range(1, 41)))
    els = {
        "impl:route:GET /v1/sync": Element("impl:route:GET /v1/sync", "impl_route", "implemented_unverified; registered code:internal/sync/routes.go:17"),
        "impl:route:POST /v1/habits": Element("impl:route:POST /v1/habits", "impl_route", "implemented_unverified"),
        "impl:rule:R21": Element("impl:rule:R21", "impl_rule", "implemented_unverified"),
        "cell:no token|GET /v1/sync": Element("cell:no token|GET /v1/sync", "cell", "401"),
        "rule:R21": Element("rule:R21", "rule", "Every other `/v1` route except register, login and refresh needs a token"),
        "rule:R89": Element("rule:R89", "rule", "`GET /v1/sync?cursor=<opaque>&limit=<n>` answers `{changes, next_cursor, has_more}`"),
        "rule:R6": Element("rule:R6", "rule", "A request may send X-Client-Version"),
        "rule:R7": Element("rule:R7", "rule", "A refused request writes nothing"),
        "unchecked:1": Element("unchecked:1", "unchecked", "R6: X-Client-Version and the 426 upgrade_required answer"),
        "invariant:one_account_per_email": Element("invariant:one_account_per_email", "invariant", "An email is registered at most once."),
        "scenario:sync_order": Element(
            "scenario:sync_order", "scenario", "sync_order",
            steps=("POST /v1/habits as ann -> 201", "GET /v1/sync as ann -> 200"),
            raw=(SimpleNamespace(method="POST", path="/v1/habits"), SimpleNamespace(method="GET", path="/v1/sync?cursor=x")),
        ),
        "rationale:R21": Element("rationale:R21", "rationale", "why"),
    }
    return A.Resolver(
        elements=els,
        route_files={"GET /v1/sync": {"internal/sync/routes.go"}, "POST /v1/habits": {"internal/habits/routes.go"}},
        rule_endpoints=lambda rid: {"GET /v1/sync"} if rid == "R21" else set(),
        app_root=root,
    )


def cite(target: str, step: int | None = None) -> dict:
    return {"target": target, "step": step, "quote": "q"}


# ---- the audit (prereg section 4) -------------------------------------------------------------------------------------------------------


def test_an_impl_route_citation_that_resolves_cites_its_route(resolver: A.Resolver) -> None:
    # the .NET C-ASK miss: the registered audit counted this answer as uncited; the corrected definition does not
    out = A.audit("Yes. GET /v1/sync exists and needs a token.", [cite("impl:route:GET /v1/sync")], resolver)
    assert out["uncited"] == 0
    (claim,) = out["claims"]
    assert claim["claim"] == "GET /v1/sync" and claim["kind"] == "route" and claim["known"] and claim["cited"]
    assert claim["binders"] == ["impl:route:GET /v1/sync"]


def test_an_impl_route_citation_that_does_not_resolve_cites_nothing(resolver: A.Resolver) -> None:
    out = A.audit("GET /v1/sync answers 200.", [cite("impl:route:GET /v1/sink"), cite("impl:route:GET /v1/sync/")], resolver)
    assert out["uncited"] == 1
    assert [c["resolves"] for c in out["citations"]] == [False, False]
    assert out["nonresolving"] == 2


def test_a_route_claim_with_only_an_unrelated_citation_is_uncited(resolver: A.Resolver) -> None:
    out = A.audit("GET /v1/sync answers 200.", [cite("invariant:one_account_per_email")], resolver)
    assert out["uncited"] == 1
    assert A.audit("GET /v1/sync answers 200.", [], resolver)["uncited"] == 1


def test_the_binding_kinds_of_a_route_claim(resolver: A.Resolver) -> None:
    text = "GET /v1/sync/ answers 401 without a token."  # a trailing slash is the same route key
    for target, step in (
        ("cell:no token|GET /v1/sync", None),
        ("scenario:sync_order", None),
        ("scenario:sync_order", 2),
        ("rule:R89", None),  # the rule's text names the route
        ("rule:R21", None),  # the inventory says R21 needs the route
        ("impl:rule:R21", None),
        ("code:internal/sync/routes.go:17", None),  # the route's registration file
    ):
        assert A.audit(text, [cite(target, step)], resolver)["uncited"] == 0, target
    for target, step in (
        ("scenario:sync_order", 1),  # that step is another route
        ("code:internal/sync/routes.go:99", None),  # the line does not exist
        ("code:internal/sync/../sync/routes.go:17", None),
        ("cell:no token|GET /v1/syncs", None),
        ("rule:R7", None),
    ):
        assert A.audit(text, [cite(target, step)], resolver)["uncited"] == 1, (target, step)


def test_rule_claims_and_their_binders(resolver: A.Resolver) -> None:
    assert A.audit("R21 requires a token.", [cite("rule:R21")], resolver)["uncited"] == 0
    assert A.audit("rule:R21 requires a token.", [cite("impl:rule:R21")], resolver)["uncited"] == 0
    assert A.audit("R6 is not checked.", [cite("unchecked:1")], resolver)["uncited"] == 0
    assert A.audit("R21 requires a token.", [cite("rule:R7")], resolver)["uncited"] == 1
    out = A.audit("R999 says so; impl:rule:R21 is not a claim by RULE_RE.", [], resolver)
    assert out["unknown_rules"] == ["R999"] and out["uncited"] == 0


def test_spec_prefix_and_notes_never_resolve(resolver: A.Resolver) -> None:
    for t in ("spec:R21", "spec:GET /v1/sync", "rationale:R21", "term:streak", "R21", ""):
        ok, why = resolver.resolves(t)
        assert not ok and why, t
    assert A.audit("R21 requires a token.", [cite("rationale:R21")], resolver)["uncited"] == 1


def test_unknown_route_mentions_are_counted_apart(resolver: A.Resolver) -> None:
    out = A.audit("There is no DELETE /v1/nudges/{id} route; GET /v1/sync exists.", [cite("cell:no token|GET /v1/sync")], resolver)
    assert out["unknown_routes"] == ["DELETE /v1/nudges/{}"]
    assert out["uncited"] == 0 and out["known"] == 1


def test_claims_the_claim_checker_inserted_are_reported_apart(resolver: A.Resolver) -> None:
    out = A.audit("Fine.\nPOST /v1/habits (impl:route) is implemented.", [], resolver, raw_text="Fine.")
    assert out["uncited"] == 0 and out["inserted"] == ["POST /v1/habits"]
    out = A.audit("POST /v1/habits is implemented.", [], resolver, raw_text="POST /v1/habits is implemented.")
    assert out["uncited"] == 1 and out["inserted"] == []


def test_failure_claims_use_the_frozen_round1_scorer(resolver: A.Resolver) -> None:
    out = A.audit("The sync fails for ann. It may fail for ben.", [], resolver)
    assert out["failure_claims"] == ["The sync fails for ann."]


def test_score_against_the_key() -> None:
    key = {"kinds": ["value"], "routes_required": ["GET /health"], "must": [["\\b401\\b"], ["token"]], "must_not": ["admin can"], "abstain_ok": False}
    ok = A.score({"kind": "value", "answer": "GET /health and 401", "explanation": "", "citations": [{"target": "rule:R21", "quote": "token"}]}, key)
    assert ok["correct"], ok
    assert not A.score({"kind": "yes", "answer": "GET /health 401 token", "explanation": ""}, key)["correct"]
    assert not A.score({"kind": "value", "answer": "GET /v1/me 401 token", "explanation": ""}, key)["correct"]
    assert not A.score({"kind": "value", "answer": "GET /health 401 token; the admin can", "explanation": ""}, key)["correct"]
    abst = dict(key, kinds=["value", "not_specified"], abstain_ok=True)
    assert A.score({"kind": "not_specified", "answer": "Not specified.", "explanation": ""}, abst)["correct"]
    assert not A.score({"kind": "not_specified", "answer": "Not specified.", "explanation": ""}, dict(abst, abstain_ok=False))["correct"]


def test_the_question_set_is_the_preregistered_one() -> None:
    every = R.load_questions(live_only=False)
    assert [q["id"] for q in every] == [f"Q{i:02d}" for i in range(1, 13)]  # the 12 of section 6, none deleted
    qs = R.load_questions()  # Amendment 1: six live, one per kind, run order kept
    assert [q["id"] for q in qs] == ["Q01", "Q03", "Q04", "Q06", "Q07", "Q08"]
    assert [q["category"] for q in qs] == ["routes", "rule requirement", "not implemented", "who can call", "not specified", "trap (false premise)"]
    assert {q["id"] for q in every if q["status"] == "dropped"} == {"Q02", "Q05", "Q09", "Q10", "Q11", "Q12"}
    assert all(q["status"] in ("live", "dropped") for q in every)
    for q in every:
        assert set(q["key"]) >= {"kinds", "must", "must_not", "routes_required", "abstain_ok", "claims"}
        assert q["gold_reply"]["kind"] in q["key"]["kinds"]


# ---- the shim: cap and ledger (prereg section 7) ----------------------------------------------------------------------------------------


def run_shim(tmp: Path, *, cost: float, spent_lines: list[float] = ()) -> tuple[int, list[dict], list[str]]:
    ledger = tmp / "ledger.jsonl"
    for c in spent_lines:
        with ledger.open("a") as f:
            f.write(json.dumps({"phase": "ask", "cost_usd": c}) + "\n")
    mark = tmp / "fake-argv.jsonl"
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "DNRA_"))
    } | R.shim_env(tmp, real=str(FAKE)) | {"T14_FAKE_COST": str(cost), "T14_FAKE_MARK": str(mark), "T14_FAKE_MODE": "gold"}
    p = subprocess.run([str(SHIM), "-p", "--output-format", "json", "--model", "sonnet"], input="Question: Which API routes does the app have for check-ins?\n",
                       capture_output=True, text=True, env=env, timeout=60)
    lines = [json.loads(x) for x in ledger.read_text().splitlines()][len(spent_lines):] if ledger.exists() else []
    argv = [json.loads(x) for x in mark.read_text().splitlines()] if mark.exists() else []
    return p.returncode, lines, argv


def test_the_shim_passes_the_remaining_budget_and_writes_one_ledger_line(tmp_path: Path) -> None:
    rc, lines, argv = run_shim(tmp_path, cost=0.04, spent_lines=[0.40])
    assert rc == 0 and len(lines) == 1 and len(argv) == 1
    assert lines[0]["cost_usd"] == 0.04 and lines[0]["budget_flag_usd"] == 0.13  # 0.60 - 0.07 - 0.40
    a = argv[0]
    assert a[a.index("--max-budget-usd") + 1] == "0.13"
    rc, lines, argv = run_shim(tmp_path / "fresh", cost=0.0)
    assert lines[0]["budget_flag_usd"] == 0.30  # the per-call ceiling


def test_the_shim_refuses_without_a_model_call_near_the_cap(tmp_path: Path) -> None:
    rc, lines, argv = run_shim(tmp_path, cost=0.04, spent_lines=[0.30, 0.225])  # 0.60 - 0.07 - 0.525 = 0.005 < 0.01
    assert rc == 3 and argv == []
    assert lines == [lines[0]] and lines[0]["refused"] == "cap reached" and lines[0]["cost_usd"] == 0.0


def test_the_ledger_stays_under_the_cap_with_the_registered_call_costs(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    rc = 0
    calls = 0
    run_shim(tmp_path, cost=0.16)  # the first call writes the cache
    while rc == 0 and calls < 50:
        rc, _lines, _ = run_shim(tmp_path, cost=0.046)
        calls += 1
    assert rc == 3
    total = sum(json.loads(x)["cost_usd"] for x in ledger.read_text().splitlines())
    assert 0.52 < total <= R.CAP


def test_a_question_starts_only_with_its_reserve_left() -> None:
    assert R.may_start(0.0) and R.may_start(0.48)
    assert not R.may_start(0.4801) and not R.may_start(0.60)


# ---- the guard (prereg section 7) -------------------------------------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> SimpleNamespace:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    prereg, driver, other = r / "prereg.md", r / "run.py", r / "audit.py"
    prereg.write_text("prereg\n")
    git(r, "add", "prereg.md")
    git(r, "commit", "-q", "-m", "prereg")
    driver.write_text("driver\n")
    other.write_text("audit\n")
    git(r, "add", "run.py", "audit.py")
    git(r, "commit", "-q", "-m", "driver")
    return SimpleNamespace(root=r, prereg=prereg, driver=driver, files=[prereg, driver, other], out=tmp_path / "out")


def guard(repo: SimpleNamespace, **kw: object) -> dict:
    args = dict(confirm_spend=True, repo=repo.root, prereg=repo.prereg, driver=repo.driver, files=repo.files, claude_version=lambda: "test")
    args.update(kw)
    return R.guard(repo.out, **args)


def test_the_guard_passes_a_clean_committed_tree_and_writes_the_freeze(repo: SimpleNamespace) -> None:
    info = guard(repo)
    assert info["head"] == git(repo.root, "rev-parse", "HEAD")
    freeze = json.loads((repo.out / "ask-freeze.json").read_text())
    assert set(freeze["files"]) == {"prereg.md", "run.py", "audit.py"} and freeze["claude_version"] == "test"
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_the_guard_refuses_without_confirm_spend(repo: SimpleNamespace) -> None:
    with pytest.raises(SystemExit, match="--confirm-spend"):
        guard(repo, confirm_spend=False)


def test_the_guard_refuses_a_dirty_or_untracked_file(repo: SimpleNamespace) -> None:
    repo.driver.write_text("changed\n")
    with pytest.raises(SystemExit, match="not committed"):
        guard(repo)
    git(repo.root, "checkout", "--", "run.py")
    extra = repo.root / "shim.py"
    extra.write_text("x\n")
    with pytest.raises(SystemExit, match="not committed"):
        guard(repo, files=[*repo.files, extra])


def test_the_guard_refuses_a_prereg_committed_after_the_driver(tmp_path: Path) -> None:
    r = tmp_path / "late"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "run.py").write_text("driver\n")
    git(r, "add", "run.py")
    git(r, "commit", "-q", "-m", "driver first")
    (r / "prereg.md").write_text("prereg\n")
    git(r, "add", "prereg.md")
    git(r, "commit", "-q", "-m", "prereg after")
    ns = SimpleNamespace(root=r, prereg=r / "prereg.md", driver=r / "run.py", files=[r / "prereg.md", r / "run.py"], out=tmp_path / "out")
    with pytest.raises(SystemExit, match="before the driver"):
        guard(ns)


def test_the_guard_refuses_a_prereg_amended_after_the_last_driver_commit(repo: SimpleNamespace) -> None:
    repo.prereg.write_text("prereg\n## Amendment 1\n")
    git(repo.root, "add", "prereg.md")
    git(repo.root, "commit", "-q", "-m", "amendment after the driver")
    with pytest.raises(SystemExit, match="amendment"):
        guard(repo)
    repo.driver.write_text("driver implementing the amendment\n")
    git(repo.root, "add", "run.py")
    git(repo.root, "commit", "-q", "-m", "driver after the amendment")
    guard(repo)  # amend first, then the driver: accepted


def test_the_guard_refuses_a_second_run(repo: SimpleNamespace) -> None:
    repo.out.mkdir()
    (repo.out / "ask-result.json").write_text("{}")
    with pytest.raises(SystemExit, match="happens once"):
        guard(repo)
    (repo.out / "ask-result.json").unlink()
    (repo.out / "ledger.jsonl").write_text(json.dumps({"n": 1, "phase": "ask", "cost_usd": 0.16}) + "\n")
    with pytest.raises(SystemExit, match="model call"):
        guard(repo)
    (repo.out / "ledger.jsonl").write_text(json.dumps({"n": 1, "phase": "ask", "refused": "cap reached", "cost_usd": 0.0}) + "\n")
    guard(repo)  # a refused call is not a model call: a relaunch after a crash before the first model call is allowed


# ---- the dry run end to end (fake model; needs the Streak repository and Go) ------------------------------------------------------------

needs_streak = pytest.mark.skipif(
    not (R.STREAK / ".git").exists() or shutil.which("go") is None, reason="needs ~/Documents/streak and the Go toolchain"
)


@needs_streak
def test_the_dry_run_from_the_gold_key_passes_every_gate(tmp_path: Path) -> None:
    out = tmp_path / "gold"
    assert R.main(["ask", "--dry-run", "gold", "--out", str(out), "--scratch", str(tmp_path / "s")]) == 0
    res = json.loads((out / "ask-result.json").read_text())
    v = res["verdicts"]
    assert v["A-ANSWERED"] and v["A-CITED"] and v["A-COST"], res["counts"]
    c = res["counts"]
    assert c["questions"] == 6 and c["answered"] == 6 and c["uncited_known_claims"] == 0 and c["rejected_citations"] == 0 and c["known_claims"] > 15
    assert c["correct_checked"] == 6 and c["correct_raw"] == 6 and c["total_usd"] == 0.0
    assert [r["id"] for r in res["rows"]] == ["Q01", "Q03", "Q04", "Q06", "Q07", "Q08"]
    ledger = [json.loads(x) for x in (out / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 6 and all(x["cost_usd"] == 0.0 for x in ledger)
    q8 = next(r for r in res["rows"] if r["id"] == "Q08")
    assert q8["audit_checked"]["unknown_routes"] == ["DELETE /v1/nudges/{}"]


@needs_streak
def test_the_dry_run_with_wrong_citations_fails_a_cited(tmp_path: Path) -> None:
    out = tmp_path / "wrong"
    assert R.main(["ask", "--dry-run", "wrongcite", "--out", str(out), "--scratch", str(tmp_path / "s")]) == 0
    res = json.loads((out / "ask-result.json").read_text())
    assert res["verdicts"]["A-ANSWERED"] and not res["verdicts"]["A-CITED"]
    c = res["counts"]
    assert c["answered"] == 6 and c["uncited_known_claims"] >= 6 and c["rejected_citations"] == 6 and c["raw_nonresolving_citations"] == 6
