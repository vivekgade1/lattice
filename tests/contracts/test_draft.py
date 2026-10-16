"""Consumer-side drafting of a linked question (multi-project slice 4d,
``lattice.contracts.draft``, ``lattice contracts questions draft``): the template over synthetic
files (the job-scheduler / ci-bot shape of the pre-registration), the rule text match and its
traps, the replacement-only pairing, the refusals, byte determinism, and the read-only guarantee
over worlds under ``tmp_path``.

Pre-registration: ``docs/results/2026-10-15-consumer-draft-4c-prereg.md`` (D1-D3, section 3 P1/P2).
The planted rule fixture is ``dogfood/contracts/draft/ci-bot-spec``."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lattice.contracts import check as ck
from lattice.contracts import draft as dr
from lattice.contracts import linked
from lattice.contracts.__main__ import main as contracts_main

from .helpers import World
from .test_impact import commit_branch, status_201_to_200

FIXTURE = Path(__file__).resolve().parents[2] / "dogfood" / "contracts" / "draft" / "ci-bot-spec"
PIN = "9bcae0e24da96eab8eea3e4acad0535c899b15050aebaf0f400659e25ea769a6"
AFTER = "24c5fd8f480089fc4436b6752b017dda77f55c30c656ee6945afb3b8d88588dc"
GIT = "c73bea6e05b1497dd7e17aabd05e2252f969faac"

LOGIN = "POST /auth/login"
APPS = "GET /applications"
JOBS = "GET /applications/{}/jobs"
START = "POST /applications/{}/jobs/{}/runs"
POLL = "GET /applications/{}/runs/{}"
LIST_RUNS = "GET /applications/{}/jobs/{}/runs"
CANCEL = "POST /applications/{}/runs/{}/cancel"


def imp(
    endpoint: str,
    status: list[int],
    auth: str = "token",
    as_user: str | None = "eve",
    req: tuple[str, ...] = (),
    resp: tuple[str, ...] = (),
) -> ck.Import:
    return ck.Import("scheduler", endpoint, tuple(status), auth, as_user, req, resp)


# ci-bot's seven tables, in file order: #1 login ... #7 cancel
IMPORTS = [
    imp(LOGIN, [200], "public", None, ("email", "password"), ("access_token",)),
    imp(APPS, [200], resp=("id", "name")),
    imp(JOBS, [200], resp=("id", "job_name", "version")),
    imp(START, [201], resp=("id", "status")),
    imp(POLL, [200], resp=("status", "exit_code")),
    imp(LIST_RUNS, [200], resp=("id", "status")),
    imp(CANCEL, [200], resp=("status",)),
]


def ep(
    statuses: list[int],
    auth: str = "token",
    req: list[str] | None = None,
    resp: list[str] | None = None,
    granted: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "statuses": statuses,
        "auth": auth,
        "request_fields": req or [],
        "query_fields": [],
        "response_fields": resp or [],
        "granted": granted if granted is not None else ([] if auth == "public" else ["ann", "eve"]),
        "refused": [],
        "body": ["object"],
    }


LOCK = {
    "format": 1,
    "consumer": "ci-bot",
    "providers": {
        "scheduler": {
            "spec_ref": "spec.tickets:t5",
            "version": {"spec_hash": PIN, "git_ref": "ef17cf8" * 5 + "ef17c", "dirty": False},
            "export": {
                "format": 1,
                "basis": "observed",
                "error_body": "fastapi",
                "endpoints": {
                    LOGIN: ep(
                        [200, 401], "public", ["email", "password"], ["access_token", "token_type"]
                    ),
                    APPS: ep([200, 401], resp=["id", "name"]),
                    JOBS: ep([200, 401, 404], resp=["id", "job_name", "version"]),
                    START: ep([201, 401, 404, 409], resp=["id", "status", "trigger"]),
                    POLL: ep([200, 401, 404], resp=["exit_code", "status"]),
                    LIST_RUNS: ep([200, 401, 404], resp=["id", "status"]),
                    CANCEL: ep([200, 401, 404, 409], resp=["id", "status"]),
                },
            },
        }
    },
}


def B(endpoint: str, kind: str, item: str = "", detail: str = "d") -> dict[str, Any]:
    return {
        "class": "BROKEN",
        "provider": "scheduler",
        "endpoint": endpoint,
        "kind": kind,
        "item": item,
        "detail": detail,
    }


def C(
    endpoint: str,
    kind: str,
    item: str = "",
    where: str | None = None,
    detail: str = "d",
    relied: bool = True,
) -> dict[str, Any]:
    out = {
        "class": "CHANGED",
        "provider": "scheduler",
        "endpoint": endpoint,
        "kind": kind,
        "item": item,
        "relied": relied,
        "detail": detail,
    }
    if where:
        out["where"] = where
    return out


def Q(
    findings: list[dict[str, Any]],
    qid: str = "lq1",
    status: str = "open",
    superseded: dict[str, Any] | None = None,
    pin: str = PIN,
) -> dict[str, Any]:
    broken = [f for f in findings if f["class"] == "BROKEN"]
    return {
        "format": 1,
        "id": qid,
        "key": "sha256:x",
        "kind": "linked_question",
        "workspace": "probe",
        "provider": "scheduler",
        "consumer": "ci-bot",
        "contract": "static",
        "status": status,
        "label": "contradiction",
        "pinned": {
            "spec_ref": "spec.tickets:t5",
            "spec_hash": pin,
            "git_ref": "ef17cf8",
            "dirty": False,
        },
        "provider_after": {
            "spec_ref": "spec.tickets:t5",
            "spec_hash": AFTER,
            "git_ref": GIT,
            "dirty": False,
        },
        "candidate": {"input": "d", "kind": "ref", "resolved": GIT},
        "imports_affected": sorted({f["endpoint"] for f in broken}),
        "findings": findings,
        "counts": {},
        "text": "t",
        "suggestion": "s",
        "source": {"via": "cli", "change": None, "commit": GIT},
        "created_at": "x",
        "resolution": None,
        "superseded": superseded,
    }


# the five job-scheduler rows of the prereg's P1 table
D1 = [B(LIST_RUNS, "endpoint_missing", "", "the endpoint is not in the provider's spec")]
D2 = [
    B(START, "status_not_observed", "201", "the spec now shows statuses [200, 401, 404, 409]"),
    C(START, "status_added", "200", detail="status 200 is now observed"),
]
D3 = [
    B(
        CANCEL,
        "response_field_missing",
        "status",
        "the spec now shows response fields ['id', 'state']",
    ),
    C(CANCEL, "field_added", "state", "response", "response field state is now shown"),
]
D4 = [
    B(
        LOGIN,
        "request_field_missing",
        "email",
        "the spec now shows request fields ['password', 'username']",
    ),
    C(LOGIN, "field_added", "username", "request", "request field username is now shown"),
]
D5 = [
    B(POLL, "auth_changed", "as_user:eve", "granted handles are now ['ann']"),
    C(POLL, "status_added", "403", detail="status 403 is now observed"),
]

RULES = (FIXTURE / "REQUIREMENTS.md").read_text()
TRACE = json.loads((FIXTURE / "spec.trace.json").read_text())


def make(findings: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """The draft document of a question over the synthetic files (exit code 0 asserted)."""
    q = kw.pop("question", None) or Q(findings)
    args = {"imports": IMPORTS, "lock": LOCK, "rules_md": RULES, "trace": TRACE, **kw}
    rc, doc = dr.draft(q, args["imports"], args["lock"], args["rules_md"], args["trace"])
    assert rc == 0, doc
    return doc  # type: ignore[no-any-return]


def numbers(doc: dict[str, Any]) -> list[int]:
    return [r["number"] for r in doc["imports"]]


def rule_ids(doc: dict[str, Any]) -> list[str]:
    return [r["id"] for r in doc["rules"]]


def hunks(doc: dict[str, Any]) -> list[tuple[int, str, Any, Any]]:
    return [(e["import"], e["key"], e["old"], e["new"]) for e in doc["edits"]]


# ---- P1: the rows ---------------------------------------------------------------------------


def test_the_endpoint_gone(tmp_path: Path) -> None:
    doc = make(D1)
    assert numbers(doc) == [6]  # not #2, whose endpoint is a path prefix of this one
    assert hunks(doc) == []
    assert rule_ids(doc) == ["R4"]
    assert doc["rules"][0]["scenarios"] == ["timeout_cancel"]
    assert doc["rules"][0]["wording"] is None
    assert doc["no_edit"][0]["reason"] == f"{LIST_RUNS} is no longer in scheduler's spec"


def test_a_status_replaced() -> None:
    doc = make(D2)
    assert numbers(doc) == [4]
    assert hunks(doc) == [(4, "status", 201, 200)]
    assert doc["edits"][0]["text"] == "status = [201] -> [200]"
    assert rule_ids(doc) == ["R3"]  # R4 names GET of the same path: the method differs
    assert doc["rules"][0]["scenarios"] == ["start_run"]
    assert doc["rules"][0]["wording"].endswith("expects 200 with id and status.")
    assert doc["no_edit"] == []


def test_a_response_field_renamed() -> None:
    doc = make(D3)
    assert numbers(doc) == [7]
    assert hunks(doc) == [(7, "response_fields", "status", "state")]
    assert rule_ids(doc) == ["R4"]  # not R5: its path is a prefix of the cancel path
    assert doc["rules"][0]["wording"].endswith("cancel, reading state.")


def test_a_request_field_renamed() -> None:
    doc = make(D4)
    assert numbers(doc) == [1]
    assert hunks(doc) == [(1, "request_fields", "email", "username")]
    assert rule_ids(doc) == ["R1"]
    assert "using username and password" in doc["rules"][0]["wording"]
    assert doc["rules"][0]["scenarios"] == ["login"]


def test_auth_has_no_hunk_and_a_method_less_mention_names_the_rule() -> None:
    doc = make(D5)
    assert numbers(doc) == [5]
    assert hunks(doc) == []
    assert rule_ids(doc) == ["R5"]
    assert doc["rules"][0]["scenarios"] == ["poll"]
    assert "the auth of GET /applications/{}/runs/{} changed" in doc["no_edit"][0]["reason"]
    assert doc["no_edit"][0]["advice"].startswith("Ask scheduler's owner, or adapt the code")


def test_the_excluded_endpoint_has_no_hunk() -> None:
    f = [B(START, "endpoint_missing", "", ck.EXCLUDED_DETAIL)]
    doc = make(f)
    assert numbers(doc) == [4] and hunks(doc) == []
    assert "scheduler excludes" in doc["no_edit"][0]["reason"]


def test_nothing_is_ever_a_removal_only_hunk() -> None:
    for findings in (
        D1,
        D5,
        [B(START, "status_not_observed", "201")],
        [B(CANCEL, "response_field_missing", "status")],
    ):
        doc = make(findings)
        assert doc["edits"] == []
        assert "remove" not in " ".join(
            e["text"] for e in doc["edits"]
        )  # the replacement list only
        assert "A claim is never removed here" in doc["text"]


def test_two_added_items_do_not_pair() -> None:
    f = [
        B(CANCEL, "response_field_missing", "status"),
        C(CANCEL, "field_added", "state", "response"),
        C(CANCEL, "field_added", "phase", "response"),
    ]
    doc = make(f)
    assert doc["edits"] == []
    assert rule_ids(doc) == ["R4"] and doc["rules"][0]["wording"] is None


def test_two_missing_items_do_not_pair() -> None:
    f = [
        B(START, "response_field_missing", "id"),
        B(START, "response_field_missing", "status"),
        C(START, "field_added", "uuid", "response"),
    ]
    assert make(f)["edits"] == []


def test_a_rename_onto_a_claim_already_held_is_not_a_hunk() -> None:
    imports = [*IMPORTS[:6], imp(CANCEL, [200], resp=("status", "state"))]
    doc = make(D3, imports=imports)
    assert doc["edits"] == []
    assert "would only remove a claim" in doc["no_edit"][0]["reason"]


def test_the_two_sides_pair_independently() -> None:
    f = [*D2, *D3]
    doc = make(f)
    assert hunks(doc) == [(4, "status", 201, 200), (7, "response_fields", "status", "state")]
    assert rule_ids(doc) == ["R3", "R4"]


def test_import_numbers_are_file_positions_across_providers() -> None:
    other = ck.Import("other", START, (201,), "token", None, (), ())
    imports = [other, *IMPORTS]  # the provider's tables shift by one in the file
    doc = make(D2, imports=imports)
    assert numbers(doc) == [5]
    assert hunks(doc) == [(5, "status", 201, 200)]
    assert "import #5:" in doc["text"] and "import #4:" not in doc["text"]


def test_a_second_table_of_the_same_endpoint_is_named_too() -> None:
    imports = [*IMPORTS, imp(START, [201, 409], resp=("id",))]
    doc = make(D2, imports=imports)
    assert numbers(doc) == [4, 8]
    assert hunks(doc) == [(4, "status", 201, 200), (8, "status", 201, 200)]
    assert doc["edits"][1]["text"] == "status = [201, 409] -> [200, 409]"


# ---- the "before" facts and their guards ----------------------------------------------------


def test_before_facts_come_from_the_lock_after_facts_from_the_finding() -> None:
    doc = make(D2)
    brk, chg = doc["findings"]
    assert brk["before"] == "statuses [201, 401, 404, 409]"
    assert brk["after"] == "the spec now shows statuses [200, 401, 404, 409]"
    assert chg["before"] == "statuses [201, 401, 404, 409]"
    assert doc["before_note"] is None
    assert "    before: statuses [201, 401, 404, 409]" in doc["text"]


def test_a_moved_pin_omits_the_before_facts() -> None:
    q = Q(D2, pin="1" * 64)  # the lock holds PIN, the question was raised against another
    doc = make(D2, question=q)
    assert "pin moved" in doc["before_note"]
    assert all(f["before"] is None for f in doc["findings"])
    assert "before:" not in doc["text"]
    assert "pin moved" in doc["text"]
    assert hunks(doc) == [(4, "status", 201, 200)]  # the replacement does not need the lock


def test_no_lock_and_a_bad_lock_are_sentences() -> None:
    for lock, word in ((None, "no_pin"), ("bad_pin", "bad_pin")):
        doc = make(D2, lock=lock)
        assert word in doc["before_note"] and "before:" not in doc["text"]
        assert hunks(doc) == [(4, "status", 201, 200)]


def test_a_lock_without_the_provider() -> None:
    lock = copy.deepcopy(LOCK)
    lock["providers"] = {}
    assert "holds no pin of scheduler" in make(D2, lock=lock)["before_note"]


def test_the_whole_contract_names_no_import() -> None:
    f = [
        B(START, "status_not_observed", "201"),
        C("*", "error_body_changed", "error_body", detail="fastapi -> plain"),
    ]
    doc = make(f)
    assert numbers(doc) == [4]
    assert "the whole contract (no import is named)" in doc["text"]
    assert doc["findings"][1]["before"] == "error_body fastapi"


def test_an_endpoint_no_longer_imported() -> None:
    doc = make(D2, imports=IMPORTS[:3])
    assert doc["imports"] == [] and doc["no_longer_imported"] == [START]
    assert f"{START}: no longer imported" in doc["text"]
    assert doc["edits"] == []


def test_missing_and_bad_imports_keep_the_findings() -> None:
    for code in ("no_imports", "bad_imports"):
        doc = make(D2, imports=code)
        assert doc["imports_error"] == code and doc["imports"] == []
        assert code in doc["text"] and "BROKEN" in doc["text"]
        assert doc["edits"] == []
        assert "/Users" not in doc["text"] and "tmp" not in doc["text"].lower()


# ---- the rules: text match, trace guard -------------------------------------------------------


def test_the_trace_guard() -> None:
    stale = copy.deepcopy(TRACE)
    for r in stale["rules"]:
        if r["id"] == "R3":
            r["text"] += " (edited in the trace only)"
    doc = make(D2, trace=stale)
    (rule,) = doc["rules"]
    assert rule["id"] == "R3" and rule["trace"] == "stale" and rule["scenarios"] == []
    assert "trace stale for R3" in doc["text"]
    assert "start_run" not in doc["text"]


def test_no_trace_and_no_requirements() -> None:
    doc = make(D2, trace=None)
    assert doc["rules"][0]["trace"] == "none" and "no readable spec.trace.json" in doc["text"]
    doc = make(D2, rules_md=None, trace=None)
    assert doc["rules"] == []
    assert "ci-bot has no Lattice spec: no rule to edit" in doc["text"]


def test_a_rule_without_a_path_is_not_named() -> None:
    md = "- The bot starts a run of the job and cancels it on a timeout.\n"
    doc = make(D2, rules_md=md, trace=None)
    assert doc["rules"] == []
    assert "no rule of REQUIREMENTS.md names an affected endpoint" in doc["text"]


def test_templating_ints_and_uuids_and_punctuation() -> None:
    md = (
        "- Start with POST /applications/7/jobs/3/runs; then wait.\n"
        "- Start with POST /applications/123e4567-e89b-12d3-a456-426614174000/jobs/{id}/runs.\n"
        "- see https://example.com/applications/1/jobs/2/runs and a/b/or\n"
        "- both /applications/{app}/jobs/{job}/runs, and PUT /applications/{app}/jobs/{job}/runs\n"
    )
    doc = make(D2, rules_md=md, trace=None)
    assert rule_ids(doc) == ["R1", "R2", "R4"]  # R4: method-less mention; R3: URL and prose
    doc = make(D2, rules_md="- plain: and/or, TCP/IP, 50/50\n", trace=None)
    assert doc["rules"] == []


def test_a_lower_case_method_word_counts_and_a_different_method_does_not() -> None:
    md = "- the bot will get /applications/{a}/jobs/{j}/runs often.\n"
    assert rule_ids(make(D1, rules_md=md, trace=None)) == ["R1"]
    assert rule_ids(make(D2, rules_md=md, trace=None)) == []


def test_the_path_prefix_traps() -> None:
    # R2 names only GET /applications; R5's path is a prefix of the cancel path
    doc = make([B(APPS, "status_not_observed", "200")])
    assert rule_ids(doc) == ["R2"]
    doc = make([B(POLL, "endpoint_missing")])
    assert rule_ids(doc) == ["R5"]


def test_the_wording_replaces_a_whole_word_only() -> None:
    md = "- It reads statuses, statuses[0] and status on POST /applications/{a}/runs/{r}/cancel.\n"
    doc = make(D3, rules_md=md, trace=None)
    assert doc["rules"][0]["wording"].endswith(
        "and state on POST /applications/{a}/runs/{r}/cancel."
    )
    assert "reads statuses, statuses[0]" in doc["rules"][0]["wording"]


# ---- refusals, derived states, text -----------------------------------------------------------


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
def test_a_cleared_question_is_refused(status: str) -> None:
    rc, doc = dr.draft(Q(D2, status=status), IMPORTS, LOCK, RULES, TRACE)
    assert rc == 1 and doc["error"]["code"] == "not_open"
    assert status in doc["error"]["message"]


def test_a_superseded_question_is_drafted_with_the_line() -> None:
    sup = {"by": "lq2", "reason": "newer_question", "since": "t"}
    doc = make(D2, question=Q(D2, superseded=sup))
    assert "; superseded by lq2: its draft is the current one" in doc["text"].splitlines()[1]
    gone = {"by": None, "reason": "no_contradiction", "since": "t"}
    doc = make(D2, question=Q(D2, superseded=gone))
    assert "no longer contradicts the pin" in doc["text"].splitlines()[1]


def test_the_draft_row_label_is_checked() -> None:
    cand = {
        "input": "x",
        "kind": "ref",
        "resolved": GIT,
        "version": {"spec_hash": AFTER, "git_ref": GIT, "dirty": False},
    }
    row = {
        "provider": "scheduler",
        "consumer": "ci-bot",
        "contract": "static",
        "label": "contradiction",
        "error": None,
        "imports": 7,
        "pinned": {
            "spec_ref": "spec.tickets:t5",
            "spec_hash": PIN,
            "git_ref": "ef",
            "dirty": False,
        },
        "findings": D2,
        "gaps": [],
        "counts": {"broken": 1, "changed_relied": 1, "changed_unrelied": 0, "gaps": 0},
    }
    rc, doc = dr.draft_from_row(row, cand, IMPORTS, LOCK, RULES, TRACE)
    assert rc == 0 and doc["question"]["id"] == "row"
    assert hunks(doc) == [(4, "status", 201, 200)]
    for label in ("regression", "unaffected", "error"):
        rc, err = dr.draft_from_row({**row, "label": label}, cand, IMPORTS, LOCK, RULES, TRACE)
        assert rc == 1 and err["error"]["code"] == "not_contradiction"


GOLDEN_D2 = "\n".join(
    [
        "# Draft: adapt ci-bot to scheduler (linked question lq1)",
        "Question lq1, contradiction, open",
        "NOT APPLIED: this draft changes nothing in ci-bot; its owner decides and proposes any "
        "spec edit through the change flow.",
        "",
        "## 1. What changed",
        "pinned spec_hash 9bcae0e24da9; scheduler now spec_hash 24c5fd8f4800 (commit c73bea6e)",
        "  BROKEN   status_not_observed  201  @ POST /applications/{}/jobs/{}/runs",
        "    before: statuses [201, 401, 404, 409]",
        "    after: the spec now shows statuses [200, 401, 404, 409]",
        "  CHANGED* status_added  200  @ POST /applications/{}/jobs/{}/runs",
        "    before: statuses [201, 401, 404, 409]",
        "    after: status 200 is now observed",
        "",
        "## 2. Imports that rely on it",
        "import #4: POST /applications/{}/jobs/{}/runs  status [201], auth token as eve, "
        "request_fields [], response_fields [id, status]",
        "    broken claim: status_not_observed 201",
        "    relied change: status_added 200",
        "",
        "## 3. Suggested edits (verify in the code first)",
        "- change the code that sends/reads it first, then import #4: status = [201] -> [200]",
        "- A claim is never removed here: declaring less would make `check` pass without "
        "the code being right.",
        "",
        "## 4. Consumer spec rules that name the endpoint (text match, not verified reliance)",
        "R3: It starts the newest version of the job with "
        "`POST /applications/{app}/jobs/{job}/runs` and expects 201 with id and status.",
        "    names: POST /applications/{}/jobs/{}/runs",
        "    scenarios: start_run",
        "    suggested wording: It starts the newest version of the job with "
        "`POST /applications/{app}/jobs/{job}/runs` and expects 200 with id and status.",
        "",
        "## 5. Then",
        "1. Edit the code that calls the endpoint(s) above, then the matching [[import]] tables.",
        "2. `lattice contracts pin ci-bot`, then `lattice contracts check ci-bot`.",
        "3. Propose any rule edit with `lattice:change` or in Studio.",
        "4. Clear this question with `lattice contracts questions resolve ci-bot lq1`.",
        "",
        "## Not decided here",
        "- Whether scheduler's change is intended (its owner may revert it).",
        "- Where in the code each call lives.",
        "- Whether a rule's behaviour really depends on the endpoint.",
    ]
)


def test_the_text_is_exactly_the_template() -> None:
    doc = make(D2)
    assert doc["text"] == GOLDEN_D2
    assert doc["sha256"] == hashlib.sha256(GOLDEN_D2.encode()).hexdigest()
    assert doc["applied"] is False


def test_the_same_files_give_the_same_bytes() -> None:
    a = [json.dumps(make(f), sort_keys=True) for f in (D1, D2, D3, D4, D5)]
    b = [json.dumps(make(f), sort_keys=True) for f in (D1, D2, D3, D4, D5)]
    assert a == b
    for blob in a:
        assert "/Users" not in blob and "created_at" not in blob


def test_the_inputs_are_not_modified() -> None:
    q, imports = Q(D2), list(IMPORTS)
    before = json.dumps([q, LOCK, TRACE], sort_keys=True)
    make(D2, question=q, imports=imports)
    assert json.dumps([q, LOCK, TRACE], sort_keys=True) == before and imports == IMPORTS


# ---- worlds on disk: read only, the CLI -------------------------------------------------------


def tree(*roots: Path) -> dict[str, tuple[str, int, int]]:
    """(sha256, mtime_ns, mode) of every file, plus every directory, under the roots."""
    out: dict[str, tuple[str, int, int]] = {}
    for root in roots:
        for p in sorted([root, *root.rglob("*")]):
            st = p.lstat()
            digest = ""
            if p.is_file() and not p.is_symlink():
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            out[str(p)] = (digest, st.st_mtime_ns, st.st_mode)
    return out


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    w = World(tmp_path_factory.mktemp("draft") / "w", repo=True)
    assert w.pin()[0] == 0
    commit_branch(w, "brk", status_201_to_200())
    shutil.copy(FIXTURE / "REQUIREMENTS.md", w.consumer / "REQUIREMENTS.md")
    shutil.copy(FIXTURE / "spec.trace.json", w.consumer / "spec.trace.json")
    return w


@pytest.fixture()
def inbox(world: World) -> Iterator[Path]:
    rc, out = world.run("questions", "init", "cons")
    assert rc == 0, out
    rc, out = world.run("notify", "prov", "--candidate", "brk")
    assert rc == 0 and out["consumers"][0]["question"] == "lq1", out
    yield world.consumer / ".lattice" / "state" / "cons"
    for p in world.consumer.rglob("*"):
        with contextlib.suppress(OSError):
            p.chmod(0o755 if p.is_dir() else 0o644)
    shutil.rmtree(world.consumer / ".lattice", ignore_errors=True)


def cli(w: World, *argv: str, json_out: bool = False) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    flags = ["--json"] if json_out else []
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = contracts_main(["questions", "draft", *argv, *flags, "--workspace", str(w.root)])
    return rc, out.getvalue(), err.getvalue()


def test_the_cli_draft_of_a_stored_question(world: World, inbox: Path) -> None:
    rc, out, err = cli(world, "cons", "lq1")
    assert rc == 0 and err == ""
    assert out.startswith("# Draft: adapt cons to prov (linked question lq1)\n")
    assert (
        "- change the code that sends/reads it first, then import #4: status = [201] -> [200]"
        in out
    )
    assert "import #4: POST /jobs/{}/runs" in out
    assert "NOT APPLIED" in out
    rc, js, _ = cli(world, "cons", "lq1", json_out=True)
    doc = json.loads(js)
    assert rc == 0 and doc["text"] + "\n" == out and doc["applied"] is False
    assert doc["consumer"] == "cons" and doc["provider"] == "prov"


def test_nothing_is_written_not_even_a_timestamp(world: World, inbox: Path) -> None:
    before = tree(world.consumer, world.provider, world.root / "lattice.workspace.toml")
    texts = []
    for _ in range(3):
        rc, out, _err = cli(world, "cons", "lq1")
        assert rc == 0
        texts.append(out)
        rc, js, _ = cli(world, "cons", "lq1", json_out=True)
        assert rc == 0
    assert len(set(texts)) == 1
    assert tree(world.consumer, world.provider, world.root / "lattice.workspace.toml") == before
    assert not (inbox / "changes").exists()
    assert sorted(p.name for p in (inbox / linked.DIR).iterdir()) == [".lock", "links", "lq1.json"]


def test_it_works_with_the_consumer_made_read_only(world: World, inbox: Path) -> None:
    for p in [world.consumer, *world.consumer.rglob("*")]:
        if p.is_symlink():
            continue
        p.chmod(0o555 if p.is_dir() else 0o444)
    try:
        before = tree(world.consumer)
        rc, out, err = cli(world, "cons", "lq1")
        assert rc == 0, err
        assert tree(world.consumer) == before
    finally:
        for p in [world.consumer, *world.consumer.rglob("*")]:
            if not p.is_symlink():
                p.chmod(0o755 if p.is_dir() else 0o644)


def test_a_missing_state_directory_is_no_state_and_nothing_is_created(world: World) -> None:
    shutil.rmtree(world.consumer / ".lattice", ignore_errors=True)
    before = tree(world.consumer)
    rc, out, err = cli(world, "cons", "lq1")
    assert rc == 2 and out == "" and "[no_state]" in err
    rc, js, _ = cli(world, "cons", "lq1", json_out=True)
    assert rc == 2 and json.loads(js)["error"]["code"] == "no_state"
    assert tree(world.consumer) == before
    assert not (world.consumer / ".lattice").exists()


def test_unknown_question_unknown_project_and_not_open(world: World, inbox: Path) -> None:
    rc, _out, err = cli(world, "cons", "lq9")
    assert rc == 2 and "[unknown_question]" in err
    rc, _out, err = cli(world, "ghost", "lq1")
    assert rc == 2 and "[unknown_project]" in err
    rc, out = world.run("questions", "dismiss", "cons", "lq1")
    assert rc == 0
    before = tree(world.consumer)
    rc, _out, err = cli(world, "cons", "lq1")
    assert rc == 1 and "[not_open]" in err
    assert tree(world.consumer) == before


def test_the_draft_never_creates_a_change_or_touches_the_question(
    world: World, inbox: Path
) -> None:
    q = (inbox / linked.DIR / "lq1.json").read_bytes()
    cli(world, "cons", "lq1")
    assert (inbox / linked.DIR / "lq1.json").read_bytes() == q
    assert not list(world.consumer.rglob("changes"))
    rc, doc = world.run("questions", "list", "cons")
    assert rc == 0 and doc["counts"]["open"] == 1


def test_the_other_questions_commands_are_unchanged(world: World, inbox: Path) -> None:
    rc, doc = world.run("questions", "list", "cons")
    assert rc == 0 and [q["id"] for q in doc["questions"]] == ["lq1"]
    assert set(doc) == {
        "format",
        "consumer",
        "state",
        "state_dir",
        "questions",
        "counts",
        "problems",
        "legend",
    }
    assert "draft" not in json.dumps(doc["questions"][0]).lower().replace("drafted", "")
    assert os.access(inbox, os.W_OK)
