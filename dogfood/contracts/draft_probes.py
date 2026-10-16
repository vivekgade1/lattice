"""Probe runner for the consumer-side draft of a linked question (slice 4d, ``lattice contracts
questions draft``, the MCP tool ``linked_question_draft``).

Implements the deterministic probes of ``docs/results/2026-10-15-consumer-draft-4c-prereg.md``
(section 3, Amendment 1 D): P1 (template correctness, blind), P2 (read only, no write), P4
(byte-compat with ``git archive 223adff``) and T (timing). P3, the real-agent exercise, is
``draft_agent.py`` and runs separately and last. Predictions and pass rules are copied from the
prereg; a probe that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/draft_probes.py --runs 10 --scratch DIR --out DIR --old DIR/base
        [--only P1,P2,P4,T] [--skip-regression]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 and of
Streak at a0db033 (the runners' REF; the committed D-series data and the golden locks are of that
commit) under ``DIR``; the real repositories are only read. No model and no API key are used. Every
deterministic probe runs ``--runs`` times (default 10) and all canonical outputs must be identical,
and equal across a second scratch root. The helpers are those of ``linkedq_probes.py``,
``impact_probes.py``, ``exclude_probes.py`` and ``probes.py``, imported from the files next to this
one. ``--old`` is the directory ``git archive 223adff`` was extracted into (``lattice/`` below it).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FIXTURE = HERE / "draft" / "ci-bot-spec"
OLD_HEAD = "223adff"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


lq = _load("lattice_contracts_linkedq_probes_draft", HERE / "linkedq_probes.py")
ep = lq.ep
ip = lq.ip
base = lq.base
streak = lq.streak
Result = base.Result
mk = ep.mk

from lattice import workspace as wsmod  # noqa: E402
from lattice.contracts import check as ck  # noqa: E402
from lattice.contracts import draft as dr  # noqa: E402

POST_HOC_SCRIPT_CORRECTIONS: list[str] = []
POST_HOC_PROCEDURE: list[str] = [
    "Streak is cloned at a0db033 (streak_probes.REF, the commit of the committed D-series data, "
    "the golden lock and every earlier runner), not at a0679e9: the merge a0679e9 adds build "
    "code, not spec, and the pin's spec_hash prefix d44b738522ea is a0db033's.",
    "P1 'identical 10/10' compares, per run, every row's CLI text, CLI --json document (and its "
    "sha256) of a FRESH state (`questions init` after removing `.lattice/`, then `notify dN`), "
    "without and with the planted spec for the job rows; 'identical across two scratch roots' "
    "compares run 0 of this root with a second, independent root (own clones, own manifests) "
    "of the 13 contradiction rows with and without the spec.",
    "No-draft rows are asked twice: through `draft_from_row` on the `impact --json` row and the "
    "consumer's files (library, refused `not_contradiction`, exit 1), and through the CLI on the "
    "state `notify` left (no question: `unknown_question`, exit 2).",
    "The 'pin moved' variant re-pins the consumer's lock against a provider clone at D3 "
    "(`pin --force`, the BROKEN cancel import), then drafts the D2 question against the "
    "original manifest; 'imports file deleted' removes lattice.imports.toml of the consumer; the "
    "two-adds question is lq2 written into the consumer's state directory beside lq1 "
    "(D2's findings with a second relied CHANGED field_added on the BROKEN response field).",
    "P2: the window is `chmod -R a-w` of the consumer directory (its state directory is inside "
    "it), exact original modes restored; the provider clone, the manifest and the consumer are "
    "snapshotted (sha256, mtime_ns, mode, sorted dirs and symlink targets) before and after; "
    "`.lock` is removed from half of the rows' state directories first and must stay absent. "
    "`change_list` is the MCP tool of the consumer's own server, called before and after.",
    "P4 runs each argv under PYTHONPATH=<this tree> and PYTHONPATH=<--old> on the same files: a "
    "command that writes (pin, notify, resolve, dismiss, init) runs on a restored copy of the "
    "consumer directory for each tree. Times are masked (timestamps, elapsed_s). `improve "
    "draft` byte-compat is the improve2 runner's own D-series of the old and new trees compared.",
]

DROP = lq.DROP
QDIR = lq.QDIR
FLOCK = ".lock"  # the flock file of linked_questions/ (lq.LOCK is the contracts lock)
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)?")
ELAPSED = re.compile(r'"elapsed_s": [0-9.eE+-]+')

# ---- P1 expectations: the prereg's table, verbatim --------------------------------------------

# (imports named, hunks (import, key, old, new), rules named (+spec), scenarios, wording contains)
JOB_ROWS: dict[str, dict[str, Any]] = {
    "D1": {
        "imports": [6],
        "edits": [],
        "rules": ["R4"],
        "scen": {"R4": ["timeout_cancel"]},
        "wording": {},
    },
    "D2": {
        "imports": [4],
        "edits": [(4, "status", 201, 200)],
        "rules": ["R3"],
        "scen": {"R3": ["start_run"]},
        "wording": {"R3": "expects 200 with id and status."},
    },
    "D3": {
        "imports": [7],
        "edits": [(7, "response_fields", "status", "state")],
        "rules": ["R4"],
        "scen": {"R4": ["timeout_cancel"]},
        "wording": {"R4": "reading state."},
    },
    "D4": {
        "imports": [1],
        "edits": [(1, "request_fields", "email", "username")],
        "rules": ["R1"],
        "scen": {"R1": ["login"]},
        "wording": {"R1": "using username and password"},
    },
    "D5": {"imports": [5], "edits": [], "rules": ["R5"], "scen": {"R5": ["poll"]}, "wording": {}},
}
STREAK_ROWS: dict[str, dict[str, Any]] = {
    "D1": {"imports": [3], "edits": []},
    "D2": {"imports": [7], "edits": [(7, "status", 200, 204)]},
    "D3": {"imports": [1], "edits": [(1, "response_fields", "expires_in", "expires_in_s")]},
    "D4": {"imports": [1], "edits": [(1, "request_fields", "password", "passphrase")]},
    "D5": {"imports": [3], "edits": []},
    "D6": {"imports": [4], "edits": [(4, "response_fields", "next_cursor", "next")]},
    "D7": {"imports": [1, 2, 3, 4, 5, 6, 7], "edits": []},
}
JOB_NODRAFT = ["D6", "D7", "D8", "D9", "D10"]
STREAK_NODRAFT = ["D8", "D9", "D10", "D11", "D12", "D13"]
XB = {"endpoint": "PUT /test/clock"}  # I3x-b: the importing table is computed from the file


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def mask(text: str) -> str:
    return ELAPSED.sub('"elapsed_s": 0', TS.sub("<time>", text))


def call_rec(c: Any) -> dict[str, Any]:
    return {"rc": c.rc, "out": c.out, "err": c.err}


def draft_cli(ctx: Any, m: Path, consumer: str, qid: str, *flags: str) -> Any:
    return ctx.contracts(m, "questions", "draft", consumer, qid, *flags)


def judge_row(rec: dict[str, Any], exp: dict[str, Any], with_spec: bool) -> list[str]:
    """The reasons a row differs from the prereg's table (empty: it is as predicted)."""
    why: list[str] = []
    doc = rec["json"]
    if rec["rc"] != 0 or rec["text_rc"] != 0:
        why.append(f"exit {rec['rc']}/{rec['text_rc']}, not 0")
        return why
    nums = sorted(r["number"] for r in doc["imports"])
    if nums != sorted(exp["imports"]):
        why.append(f"imports {nums} != {sorted(exp['imports'])}")
    edits = [(e["import"], e["key"], e["old"], e["new"]) for e in doc["edits"]]
    if edits != [tuple(e) for e in exp["edits"]]:
        why.append(f"edits {edits} != {exp['edits']}")
    if rec["text"] != doc["text"] + "\n":
        why.append("CLI text differs from json.text")
    if doc["sha256"] != hashlib.sha256(doc["text"].encode()).hexdigest():
        why.append("sha256 does not match text")
    if doc["applied"] is not False:
        why.append("applied is not false")
    if "/Users" in doc["text"] or "created_at" in json.dumps(doc):
        why.append("absolute path or created_at in the draft")
    if "rules" in exp:
        want = exp["rules"] if with_spec else []
        got = [r["id"] for r in doc["rules"]]
        if got != want:
            why.append(f"rules {got} != {want}")
        if with_spec:
            for r in doc["rules"]:
                if r["scenarios"] != exp["scen"].get(r["id"], []):
                    why.append(f"{r['id']} scenarios {r['scenarios']}")
                w = exp["wording"].get(r["id"])
                if w is not None and w not in (r["wording"] or ""):
                    why.append(f"{r['id']} wording lacks {w!r}")
                if w is None and r["wording"] is not None:
                    why.append(f"{r['id']} has a wording where none is predicted")
        else:
            if "has no Lattice spec: no rule to edit" not in doc["text"]:
                why.append("no-spec sentence missing")
    else:
        if "has no Lattice spec: no rule to edit" not in doc["text"]:
            why.append("no-spec sentence missing")
        if doc["rules"]:
            why.append(f"rules named on a consumer without a spec: {doc['rules']}")
    if not exp["edits"]:
        if "A claim is never removed here" not in doc["text"]:
            why.append("the never-remove sentence is missing")
    return why


def one_row(ctx: Any, m: Path, consumer: str, qid: str = "lq1") -> dict[str, Any]:
    j = draft_cli(ctx, m, consumer, qid, "--json")
    t = draft_cli(ctx, m, consumer, qid)
    rec: dict[str, Any] = {
        "rc": j.rc,
        "text_rc": t.rc,
        "text": t.out,
        "err": j.err + t.err,
        "json_raw_sha": sha(j.out.encode()),
    }
    rec["json"] = json.loads(j.out) if j.out.strip().startswith("{") else {"raw": j.out}
    return rec


def consumer_files(c: Path, spec: bool) -> None:
    for n in ("REQUIREMENTS.md", "spec.trace.json"):
        if spec:
            shutil.copy2(FIXTURE / n, c / n)
        else:
            (c / n).unlink(missing_ok=True)


def impact_row(ctx: Any, m: Path, provider: str, cand: str, consumer: str) -> tuple[dict, dict]:
    r = ctx.contracts(m, "impact", provider, "--candidate", cand, "--json")
    doc = json.loads(r.out)
    row = next(x for x in doc["consumers"] if x["consumer"] == consumer)
    return row, doc["candidate"]


def from_row(ctx: Any, m: Path, provider: str, cand: str, consumer: str, cdir: Path) -> Any:
    row, candidate = impact_row(ctx, m, provider, cand, consumer)
    files = dr.load_files(cdir, consumer)
    return dr.draft_from_row(row, candidate, *files)


# ---- P1 ---------------------------------------------------------------------------------------


def series(
    h: Any,
    ctx: Any,
    m: Path,
    provider: str,
    drifts: dict[str, Any],
    contra: dict[str, Any],
    nodraft: list[str],
    with_spec_rows: bool,
) -> dict[str, Any]:
    """One run of one D series on fresh states: the draft of every contradiction row (without the
    planted spec; with it for the job series) and the refusals of the other rows."""
    out: dict[str, Any] = {}
    consumer = ctx.consumer
    c = lq.cdir(ctx, m)
    for did in contra:
        cand = h.jref(did) if provider == "scheduler" else h.sref(did)
        lq.init_state(ctx, m)
        n = lq.notify(ctx, m, provider, cand)
        row = lq.first_row(n)
        rec: dict[str, Any] = {"notify_action": row["action"], "question": row["question"]}
        consumer_files(c, False)
        rec["nospec"] = one_row(ctx, m, consumer)
        if with_spec_rows:
            consumer_files(c, True)
            rec["spec"] = one_row(ctx, m, consumer)
            consumer_files(c, False)
        out[did] = rec
    for did in nodraft:
        cand = h.jref(did) if provider == "scheduler" else h.sref(did)
        lq.init_state(ctx, m)
        n = lq.notify(ctx, m, provider, cand)
        row = lq.first_row(n)
        lib_rc, lib = from_row(ctx, m, provider, cand, consumer, c)
        cli = draft_cli(ctx, m, consumer, "lq1", "--json")
        out[did] = {
            "notify_action": row["action"],
            "label": row["label"],
            "library": {"rc": lib_rc, "code": lib.get("error", {}).get("code")},
            "cli": {
                "rc": cli.rc,
                "code": json.loads(cli.out)["error"]["code"]
                if cli.out.strip().startswith("{")
                else cli.err.strip()[:80],
            },
        }
    return out


def row_crosscheck(h: Any, ctx: Any, m: Path, provider: str, cand: str, consumer: str) -> dict:
    """The library draft of the impact row equals the CLI draft of the stored question (but id)."""
    c = lq.cdir(ctx, m)
    lq.init_state(ctx, m)
    lq.notify(ctx, m, provider, cand)
    rc, lib = from_row(ctx, m, provider, cand, consumer, c)
    cli = json.loads(draft_cli(ctx, m, consumer, "lq1", "--json").out)
    keys = (
        "findings",
        "imports",
        "edits",
        "no_edit",
        "rules",
        "no_longer_imported",
        "before_note",
        "imports_error",
    )
    return {"rc": rc, "same": all(lib[k] == cli[k] for k in keys), "id": lib["question"]["id"]}


def p1(h: Any, h2: Any) -> Result:
    pred = (
        "13/13 contradiction rows as the table (imports exactly, hunks exactly, rules exactly "
        "with the planted spec, none without), identical 10/10 and across two scratch roots; "
        "11 + 11 no-draft (library not_contradiction exit 1, CLI unknown_question exit 2); "
        "dismissed D2 not_open exit 1; superseded lq1 produced with the line; variants stale "
        "trace, pin moved, imports deleted, two-adds 4/4 as described; 28/28 in all"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("P1j", jp), sc.workspace("P1s", sp)
    xsc, xb = lq.i3xb_case(h)
    j_contra = {k: h.jdrift[k] for k in JOB_ROWS}
    s_contra = {k: h.sdrift[k] for k in STREAK_ROWS}

    def core(hh: Any, jm_: Path, sm_: Path, xsc_: Any, xb_: Path) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        rec["job"] = series(hh, hh.jc, jm_, "scheduler", hh.jdrift, j_contra, JOB_NODRAFT, True)
        rec["streak"] = series(hh, hh.sc, sm_, "streak", hh.sdrift, s_contra, STREAK_NODRAFT, False)
        c = lq.cdir(xsc_, xb_)
        lq.init_state(xsc_, xb_)
        lq.notify(xsc_, xb_, "streak", "a0db033")
        rec["I3x-b"] = {
            "nospec": one_row(xsc_, xb_, xsc_.consumer),
            "table": [
                i
                for i, im in enumerate(ck.parse_imports(c / lq.IMPORTS), 1)
                if im.endpoint == XB["endpoint"]
            ],
        }
        return rec

    def canon_run(rec: dict[str, Any]) -> Any:
        return json.loads(json.dumps(rec, sort_keys=True, default=str))

    runs, ident = jc.repeat(lambda i: canon_run(core(h, jm, sm, xsc, xb)))
    first = runs[0]

    # -- a second, independent scratch root
    second = canon_run(
        core(
            h2,
            h2.jc.workspace("P1j2", ep.shared(h2.jc)),
            h2.sc.workspace("P1s2", ep.shared(h2.sc)),
            *lq.i3xb_case(h2),
        )
    )
    same_root = first == second

    checks: dict[str, bool] = {}
    diffs: dict[str, list[str]] = {}
    for did, exp in JOB_ROWS.items():
        rec = first["job"][did]
        d = judge_row(rec["nospec"], exp, False) + [
            f"spec: {w}" for w in judge_row(rec["spec"], exp, True)
        ]
        if rec["notify_action"] != "created" or rec["question"] != "lq1":
            d.append(f"notify {rec['notify_action']}/{rec['question']}")
        checks[f"job-{did}"] = not d
        diffs[f"job-{did}"] = d
    for did, exp in STREAK_ROWS.items():
        rec = first["streak"][did]
        d = judge_row(rec["nospec"], exp, False)
        if rec["notify_action"] != "created":
            d.append(f"notify {rec['notify_action']}")
        checks[f"streak-{did}"] = not d
        diffs[f"streak-{did}"] = d
    xrec = first["I3x-b"]
    d = judge_row(xrec["nospec"], {"imports": xrec["table"], "edits": []}, False)
    if len(xrec["table"]) != 1:
        d.append(f"importing tables {xrec['table']}")
    if not any("excludes" in e["reason"] for e in xrec["nospec"]["json"].get("no_edit", [])):
        d.append("no 'excludes' reason")
    checks["I3x-b"] = not d
    diffs["I3x-b"] = d
    # the no-draft rows
    nd_ok = 0
    for series_name, ids in (("job", JOB_NODRAFT), ("streak", STREAK_NODRAFT)):
        for did in ids:
            r = first[series_name][did]
            ok = (
                r["library"] == {"rc": 1, "code": "not_contradiction"}
                and r["cli"] == {"rc": 2, "code": "unknown_question"}
                and r["notify_action"] == "none"
            )
            nd_ok += ok
            checks[f"nodraft-{series_name}-{did}"] = ok
            diffs[f"nodraft-{series_name}-{did}"] = [] if ok else [json.dumps(r, sort_keys=True)]
    checks[f"identical_{len(runs)}_runs"] = ident
    checks["identical_two_roots"] = same_root

    # -- dismissed, superseded
    c = lq.cdir(jc, jm)
    d2, d3 = h.jref("D2"), h.jref("D3")
    lq.init_state(jc, jm)
    lq.notify(jc, jm, "scheduler", d2)
    dis = jc.contracts(jm, "questions", "dismiss", jc.consumer, "lq1", "--json")
    dd = draft_cli(jc, jm, jc.consumer, "lq1", "--json")
    dj = json.loads(dd.out) if dd.out.strip().startswith("{") else {}
    checks["dismissed_not_open"] = (dis.rc, dd.rc, dj.get("error", {}).get("code")) == (
        0,
        1,
        "not_open",
    )
    lq.init_state(jc, jm)
    lq.notify(jc, jm, "scheduler", d2)
    lq.notify(jc, jm, "scheduler", d3)
    sup = one_row(jc, jm, jc.consumer, "lq1")
    sup_line = sup["text"].splitlines()[1] if sup["text"] else ""
    checks["superseded_line"] = sup["rc"] == 0 and sup_line.endswith(
        "; superseded by lq2: its draft is the current one"
    )

    # -- variants (+spec, D2)
    var: dict[str, Any] = {}
    consumer_files(c, True)
    lq.init_state(jc, jm)
    lq.notify(jc, jm, "scheduler", d2)
    st = lq.sdir(jc, m=jm)
    trace_p = c / "spec.trace.json"
    saved = trace_p.read_text()
    tr = json.loads(saved)
    for r in tr["rules"]:
        if r["id"] == "R3":
            r["text"] += " (edited in the trace only)"
    trace_p.write_text(json.dumps(tr, indent=2) + "\n")
    v = one_row(jc, jm, jc.consumer)
    trace_p.write_text(saved)
    rules = v["json"].get("rules", [])
    var["stale_trace"] = {
        "rules": [r["id"] for r in rules],
        "trace": [r["trace"] for r in rules],
        "scen": [r["scenarios"] for r in rules],
        "has_sentence": "trace stale for R3" in v["text"],
    }
    checks["variant_stale_trace"] = (
        var["stale_trace"]["rules"] == ["R3"]
        and var["stale_trace"]["trace"] == ["stale"]
        and var["stale_trace"]["scen"] == [[]]
        and var["stale_trace"]["has_sentence"]
    )
    # pin moved: re-pin the consumer's lock to D3's export (provider clone at D3)
    d3_clone, _sha = h.hh.ref(jc, jp, d3.lower(), h.jdrift["D3"]["edits"])
    lockp = c / lq.LOCK
    lock_before = lockp.read_bytes()
    pm = ip.write_manifest(
        jc.work / "P1pm" / "m" / lq.MANIFEST,
        [
            {
                "name": "scheduler",
                "path": str(d3_clone),
                "role": "provider",
                "spec": "spec.tickets:t5",
            },
            {"name": "ci-bot", "path": str(c), "role": "consumer"},
        ],
        [{"from": "ci-bot", "to": "scheduler", "contract": "static"}],
    )
    pin = jc.contracts(pm, "pin", "ci-bot", "--force", "--json")
    v = one_row(jc, jm, jc.consumer)
    lock_changed = lockp.read_bytes() != lock_before
    lockp.write_bytes(lock_before)
    var["pin_moved"] = {
        "pin_rc": pin.rc,
        "lock_changed": lock_changed,
        "before_note": v["json"].get("before_note"),
        "any_before": any(f["before"] for f in v["json"].get("findings", [])),
        "edits": [(e["import"], e["key"], e["old"], e["new"]) for e in v["json"].get("edits", [])],
    }
    checks["variant_pin_moved"] = (
        pin.rc == 0
        and "pin moved" in (var["pin_moved"]["before_note"] or "")
        and not var["pin_moved"]["any_before"]
        and "before:" not in v["text"]
    )
    # imports deleted
    ip_p = c / lq.IMPORTS
    saved_i = ip_p.read_bytes()
    ip_p.unlink()
    v = one_row(jc, jm, jc.consumer)
    ip_p.write_bytes(saved_i)
    var["imports_deleted"] = {
        "rc": v["rc"],
        "imports_error": v["json"].get("imports_error"),
        "n_findings": len(v["json"].get("findings", [])),
        "edits": v["json"].get("edits"),
    }
    checks["variant_imports_deleted"] = (
        v["rc"] == 0
        and var["imports_deleted"]["imports_error"] == "no_imports"
        and var["imports_deleted"]["n_findings"] == 2
        and var["imports_deleted"]["edits"] == []
        and "no_imports" in v["text"]
    )
    # two adds on one endpoint (hand-built lq2 beside lq1)
    q1 = json.loads((st / QDIR / "lq1.json").read_text())
    q2 = json.loads(json.dumps(q1))
    q2["id"], q2["key"] = "lq2", "sha256:hand-built"
    ep_ = "POST /applications/{}/runs/{}/cancel"
    q2["imports_affected"] = [ep_]
    q2["findings"] = [
        {
            "class": "BROKEN",
            "provider": "scheduler",
            "endpoint": ep_,
            "kind": "response_field_missing",
            "item": "status",
            "detail": "hand-built",
        },
        {
            "class": "CHANGED",
            "provider": "scheduler",
            "endpoint": ep_,
            "kind": "field_added",
            "item": "state",
            "where": "response",
            "relied": True,
            "detail": "hand-built",
        },
        {
            "class": "CHANGED",
            "provider": "scheduler",
            "endpoint": ep_,
            "kind": "field_added",
            "item": "phase",
            "where": "response",
            "relied": True,
            "detail": "hand-built",
        },
    ]
    (st / QDIR / "lq2.json").write_text(json.dumps(q2, indent=1))
    v = one_row(jc, jm, jc.consumer, "lq2")
    var["two_adds"] = {
        "rc": v["rc"],
        "edits": v["json"].get("edits"),
        "rules": [r["id"] for r in v["json"].get("rules", [])],
        "wording": [r["wording"] for r in v["json"].get("rules", [])],
    }
    checks["variant_two_adds"] = v["rc"] == 0 and var["two_adds"]["edits"] == []
    consumer_files(c, False)
    # library draft of the row equals the CLI draft (job D2 and Streak D2)
    cross = {
        "job-D2": row_crosscheck(h, jc, jm, "scheduler", h.jref("D2"), jc.consumer),
        "streak-D2": row_crosscheck(h, sc, sm, "streak", h.sref("D2"), sc.consumer),
    }
    checks["row_equals_question_draft"] = all(
        x["rc"] == 0 and x["same"] and x["id"] == "row" for x in cross.values()
    )

    n_rows = sum(
        1 for k, v in checks.items() if (k.startswith(("job-", "streak-")) or k == "I3x-b") and v
    )
    obs = (
        f"{n_rows}/13 contradiction rows as predicted; no-draft {nd_ok}/11 (x2 channels); "
        f"identical {len(runs)} runs {ident}, two roots {same_root}; dismissed "
        f"{checks['dismissed_not_open']}, superseded {checks['superseded_line']}; variants "
        f"stale {checks['variant_stale_trace']}, pin {checks['variant_pin_moved']}, imports "
        f"{checks['variant_imports_deleted']}, two-adds {checks['variant_two_adds']}; "
        f"row==question {checks['row_equals_question_draft']}"
    )
    extra = {
        "diffs": {k: v for k, v in diffs.items() if v},
        "variants": var,
        "cross": cross,
        "first_run": first,
        "second_root_equal": same_root,
    }
    return mk("P1", pred, obs, [], ident, checks, extra)


# ---- P2 ---------------------------------------------------------------------------------------


def fsnap(roots: list[Path]) -> dict[str, Any]:
    """(sha256, mtime_ns, mode) of every file, mode and mtime of every directory, the symlink
    targets and the sorted names of every directory, under the roots (an empty `.lock` too)."""
    out: dict[str, Any] = {}
    for root in roots:
        if root.is_file():
            st = root.lstat()
            out[str(root)] = ["file", sha(root.read_bytes()), st.st_mtime_ns, st.st_mode]
            continue
        for dp, dns, fns in os.walk(root, followlinks=False):
            d = Path(dp)
            st = d.lstat()
            out[str(d)] = ["dir", sorted([*dns, *fns]), st.st_mtime_ns, st.st_mode]
            for n in fns:
                p = d / n
                s = p.lstat()
                if p.is_symlink():
                    out[str(p)] = ["link", os.readlink(p), s.st_mtime_ns, s.st_mode]
                else:
                    out[str(p)] = ["file", sha(p.read_bytes()), s.st_mtime_ns, s.st_mode]
    return out


def make_read_only(root: Path) -> dict[str, int]:
    modes: dict[str, int] = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        for n in [*dns, *fns]:
            p = Path(dp) / n
            if not p.is_symlink():
                modes[str(p)] = p.lstat().st_mode & 0o7777
    modes[str(root)] = root.lstat().st_mode & 0o7777
    for p, mode in sorted(modes.items(), key=lambda kv: -len(kv[0])):
        os.chmod(p, mode & ~0o222)
    return modes


def restore_modes(modes: dict[str, int]) -> None:
    for p, mode in sorted(modes.items(), key=lambda kv: len(kv[0])):
        with contextlib.suppress(OSError):
            os.chmod(p, mode)


def find_names(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def p2_row(ctx: Any, m: Path, provider_clone: Path, tag: str, no_lock: bool, runs: int) -> dict:
    c = lq.cdir(ctx, m)
    consumer = ctx.consumer
    st = lq.sdir(ctx, m)
    if no_lock:
        (st / QDIR / FLOCK).unlink(missing_ok=True)
    roots = [c, provider_clone, m]
    cl0 = lq.mcp_call(ctx, m, [("change_list", {})], readonly=True)
    before = fsnap(roots)
    qb = {k: v.encode() for k, v in lq.qfiles(st).items()}
    lock_before = (st / QDIR / FLOCK).exists()
    modes = make_read_only(c)
    outs: dict[str, Any] = {"cli_json": [], "cli_text": [], "mcp": [], "lib": []}
    errs: list[str] = []
    try:
        for _ in range(runs):
            j = draft_cli(ctx, m, consumer, "lq1", "--json")
            t = draft_cli(ctx, m, consumer, "lq1")
            if j.rc != 0 or t.rc != 0:
                errs.append(f"cli rc {j.rc}/{t.rc}: {j.err.strip()[:200]} {t.err.strip()[:200]}")
            outs["cli_json"].append(j.out)
            outs["cli_text"].append(t.out)
        mc = lq.mcp_call(
            ctx,
            m,
            [("linked_question_draft", {"consumer": consumer, "id": "lq1"})] * runs,
            readonly=True,
        )
        for r in mc[1:]:
            if r.get("isError") or "error" in r:
                errs.append(f"mcp: {json.dumps(r)[:200]}")
            outs["mcp"].append(r.get("doc"))
        ws = wsmod.parse(m)
        for _ in range(runs):
            rc, doc = dr.draft_for(ws, consumer, "lq1")
            if rc != 0:
                errs.append(f"draft_for rc {rc}: {doc}")
            outs["lib"].append(doc)
    finally:
        restore_modes(modes)
    after = fsnap(roots)
    cl1 = lq.mcp_call(ctx, m, [("change_list", {})], readonly=True)
    qa = {k: v.encode() for k, v in lq.qfiles(st).items()}
    docs = [json.loads(x) for x in outs["cli_json"]]
    same = (
        all(d == docs[0] for d in docs)
        and all(d == docs[0] for d in outs["mcp"])
        and all(d == docs[0] for d in outs["lib"])
        and all(t == docs[0]["text"] + "\n" for t in outs["cli_text"])
    )
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    checks = {
        "window_ran_without_error": not errs and same,
        "snapshot_identical": not changed,
        "no_changes_entry": not [p for p in find_names(c) if "/changes" in "/" + p],
        "change_list_equal": cl0 == cl1,
        "question_files_identical": qa == qb,
        "lock_unchanged": (st / QDIR / FLOCK).exists() == lock_before,
    }
    return {
        "tag": tag,
        "checks": checks,
        "errors": errs[:3],
        "changed": changed[:5],
        "lock_present_before": lock_before,
        "change_list_before": cl0[1:],
        "sha256": docs[0]["sha256"] if docs else None,
        "n_outputs": runs * 4,
    }


def p2(h: Any) -> Result:
    pred = (
        "for each of the 13 contradiction rows: 6 of 6 checks (window ran with no error and all "
        "outputs equal, snapshot identical, no changes/ entry, change_list equal, question files "
        "byte-identical, no .lock created); a missing state directory: exit 2 no_state and nothing "
        "created; 14/14"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("P2j", jp), sc.workspace("P2s", sp)
    xsc, xb = lq.i3xb_case(h)
    runs = jc.runs
    res: dict[str, Any] = {}
    i = 0
    for did in JOB_ROWS:
        lq.init_state(jc, jm)
        lq.notify(jc, jm, "scheduler", h.jref(did))
        consumer_files(lq.cdir(jc, jm), True)
        res[f"job-{did}"] = p2_row(jc, jm, jp, f"job-{did}", i % 2 == 0, runs)
        consumer_files(lq.cdir(jc, jm), False)
        i += 1
    for did in STREAK_ROWS:
        lq.init_state(sc, sm)
        lq.notify(sc, sm, "streak", h.sref(did))
        res[f"streak-{did}"] = p2_row(sc, sm, sp, f"streak-{did}", i % 2 == 0, runs)
        i += 1
    lq.init_state(xsc, xb)
    lq.notify(xsc, xb, "streak", "a0db033")
    res["I3x-b"] = p2_row(xsc, xb, sp, "I3x-b", i % 2 == 0, runs)
    # a missing state directory
    lq.init_state(jc, jm)
    lq.notify(jc, jm, "scheduler", h.jref("D2"))
    c = lq.cdir(jc, jm)
    shutil.rmtree(c / ".lattice")
    names0 = find_names(c)
    cj = draft_cli(jc, jm, jc.consumer, "lq1", "--json")
    ct = draft_cli(jc, jm, jc.consumer, "lq1")
    mc = lq.mcp_call(
        jc, jm, [("linked_question_draft", {"consumer": jc.consumer, "id": "lq1"})], readonly=True
    )
    code = json.loads(cj.out)["error"]["code"] if cj.out.strip().startswith("{") else None
    missing_ok = (
        (cj.rc, ct.rc, code) == (2, 2, "no_state")
        and find_names(c) == names0
        and not (c / ".lattice").exists()
        and mc[1].get("isError") is True
        and mc[1].get("text", "").startswith("no_state:")
    )
    res["missing_state"] = {
        "ok": missing_ok,
        "cli": [cj.rc, ct.rc, code],
        "mcp": mc[1].get("text"),
        "created": find_names(c) != names0,
    }
    rows_ok = sum(1 for k, v in res.items() if k != "missing_state" and all(v["checks"].values()))
    n = len(res) - 1
    checks = {f"{k}": all(v["checks"].values()) for k, v in res.items() if k != "missing_state"}
    checks["missing_state"] = missing_ok
    obs = f"{rows_ok}/{n} rows 6/6; missing state dir {'ok' if missing_ok else 'DIFFERS'}"
    return mk("P2", pred, obs, [], True, checks, {"rows": res})


# ---- T ----------------------------------------------------------------------------------------


def t_probe(h: Any) -> Result:
    pred = "median wall time of `questions draft` over 10 runs <= 1.0 s (predicted 0.2-0.5 s)"
    jc = h.jc
    jp = ep.shared(jc)
    jm = jc.workspace("T", jp)
    lq.init_state(jc, jm)
    lq.notify(jc, jm, "scheduler", h.jref("D2"))
    consumer_files(lq.cdir(jc, jm), True)
    walls = [draft_cli(jc, jm, jc.consumer, "lq1").wall for _ in range(10)]
    consumer_files(lq.cdir(jc, jm), False)
    walls_json = [draft_cli(jc, jm, jc.consumer, "lq1", "--json").wall for _ in range(10)]
    med = statistics.median(walls)
    obs = (
        f"median {med:.2f} s (min {min(walls):.2f}, max {max(walls):.2f}); --json median "
        f"{statistics.median(walls_json):.2f} s"
    )
    return mk(
        "T",
        pred,
        obs,
        [],
        True,
        {"median_le_1s": med <= 1.0},
        {"walls_s": [round(w, 3) for w in walls]},
    )


# ---- P4 ---------------------------------------------------------------------------------------


def tree_env(ctx: Any, root: Path) -> dict[str, str]:
    env = dict(ctx.env)
    env["PYTHONPATH"] = str(root)
    return env


def run_tree(ctx: Any, root: Path, argv: list[str], stdin: str | None = None) -> dict[str, Any]:
    c = ctx.run([sys.executable, *argv], env=tree_env(ctx, root), stdin=stdin, timeout=600)
    return {"rc": c.rc, "out": mask(c.out), "err": mask(c.err)}


def copy_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        lq.writable(dst)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, symlinks=True)


def compare(
    ctx: Any,
    new: Path,
    old: Path,
    m: Path,
    args: list[str],
    consumer_dir: Path,
    scratch: Path,
    writes: bool,
) -> dict[str, Any]:
    argv = ["-m", "lattice.contracts", *args, "--workspace", str(m)]
    bak = scratch / "bak"
    if writes:
        copy_dir(consumer_dir, bak)
    a = run_tree(ctx, new, argv)
    if writes:
        copy_dir(bak, consumer_dir)
    b = run_tree(ctx, old, argv)
    if writes:
        copy_dir(bak, consumer_dir)
    return {
        "argv": " ".join(args),
        "same": a == b,
        "rc": [a["rc"], b["rc"]],
        "new": a if a != b else None,
        "old": b if a != b else None,
    }


def mcp_msgs() -> str:
    msgs: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "probes", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ]
    return "\n".join(json.dumps(x) for x in msgs) + "\n"


def mcp_roundtrip(ctx: Any, root: Path, tail: list[str]) -> dict[str, Any]:
    c = ctx.run(
        [sys.executable, "-m", "lattice.mcp", "serve", *tail],
        env=tree_env(ctx, root),
        stdin=mcp_msgs(),
        timeout=300,
    )
    by = {
        x["id"]: x for x in (json.loads(ln) for ln in c.out.splitlines() if ln.strip()) if "id" in x
    }
    return {
        "instructions": by[0]["result"].get("instructions"),
        "init_raw": json.dumps(by[0], sort_keys=True),
        "tools_raw": json.dumps(by[1], sort_keys=True),
        "tools": by[1]["result"]["tools"],
    }


def p4(h: Any, old_root: Path, scratch: Path, skip_regression: bool) -> Result:
    pred = (
        "old-command output identical to git archive 223adff (text and --json, times masked) for "
        "export, pin, check, impact [--transitive], notify [--dry-run], questions list, resolve, "
        "dismiss, init; MCP initialize instructions identical in 6 configurations; tools/list "
        "byte-identical without a workspace and base + exactly one appended definition with one; "
        "lattice/ differs from 223adff only in draft.py (new), contracts/__main__.py and "
        "mcp/projects.py; API.md and every studio file unchanged"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("P4j", jp), sc.workspace("P4s", sp)
    new = REPO
    checks: dict[str, bool] = {}
    cmp: list[dict[str, Any]] = []
    wk = scratch / "p4"
    wk.mkdir(parents=True, exist_ok=True)
    cands = {
        "scheduler": [h.jref("D2"), h.jref("D3"), h.jref("D6"), h.jref("D8")],
        "streak": [h.sref("D2"), h.sref("D6"), h.sref("D8")],
    }

    def cmp_add(ctx: Any, m: Path, cd: Path, args: list[str], writes: bool) -> None:
        cmp.append(compare(ctx, new, old_root, m, args, cd, wk, writes))

    for ctx, m, prov, cons in ((jc, jm, "scheduler", jc.consumer), (sc, sm, "streak", sc.consumer)):
        cd = lq.cdir(ctx, m)
        lq.init_state(ctx, m)
        lq.notify(ctx, m, prov, cands[prov][0])  # a question exists for list/resolve/dismiss
        for cand in cands[prov]:
            for extra in ([], ["--json"], ["--transitive"], ["--transitive", "--json"]):
                cmp_add(ctx, m, cd, ["impact", prov, "--candidate", cand, *extra], False)
        cmp_add(ctx, m, cd, ["export", prov], False)
        for extra in ([], ["--json"], ["--static-only", "--json"]):
            cmp_add(ctx, m, cd, ["check", cons, *extra], False)
        for flags in ([], ["--json"], ["--status", "open"], ["--status", "open", "--json"]):
            cmp_add(ctx, m, cd, ["questions", "list", cons, *flags], False)
        for cand in cands[prov]:
            for flags in ([], ["--dry-run"], ["--json"], ["--dry-run", "--json"]):
                cmp_add(ctx, m, cd, ["notify", prov, "--candidate", cand, *flags], True)
        for verb in ("resolve", "dismiss"):
            for flags in ([], ["--json"], ["--note", "x", "--json"]):
                cmp_add(ctx, m, cd, ["questions", verb, cons, "lq1", *flags], True)
        cmp_add(ctx, m, cd, ["questions", "resolve", cons, "lq9"], True)
        cmp_add(ctx, m, cd, ["pin", cons], True)
        cmp_add(ctx, m, cd, ["pin", cons, "--json", "--force"], True)
        cmp_add(ctx, m, cd, ["questions", "init", cons], True)
        cmp_add(ctx, m, cd, ["questions", "init", cons, "--json"], True)
        # a consumer without a state directory: list, resolve, init
        gone = wk / f"gone-{prov}"
        copy_dir(cd, gone)
        shutil.rmtree(cd / ".lattice", ignore_errors=True)
        cmp_add(ctx, m, cd, ["questions", "list", cons], True)
        cmp_add(ctx, m, cd, ["questions", "resolve", cons, "lq1"], True)
        cmp_add(ctx, m, cd, ["questions", "init", cons], True)
        copy_dir(gone, cd)
    checks["cli_identical"] = all(x["same"] for x in cmp)

    # -- MCP: initialize instructions in six configurations, tools/list
    seeds = {n: lq.seed_project(wk / "seed" / n, n) for n in ("alpha", "beta")}
    ws1 = wk / "ws1" / "lattice.workspace.toml"
    ws1.parent.mkdir(parents=True, exist_ok=True)
    ws1.write_text(
        '[workspace]\nname = "w1"\n\n'
        f'[[project]]\nname = "alpha"\npath = "{seeds["alpha"]}"\nrole = "provider"\n'
    )
    ws2 = wk / "ws2" / "lattice.workspace.toml"
    ws2.parent.mkdir(parents=True, exist_ok=True)
    ws2.write_text(
        '[workspace]\nname = "w2"\n\n'
        f'[[project]]\nname = "alpha"\npath = "{seeds["alpha"]}"\nrole = "provider"\n\n'
        f'[[project]]\nname = "beta"\npath = "{seeds["beta"]}"\nrole = "consumer"\n\n'
        '[[link]]\nfrom = "beta"\nto = "alpha"\nkind = "uses"\ncontract = "static"\n'
    )
    a, b = str(seeds["alpha"]), str(seeds["beta"])
    configs = {
        "one-project": (["--project", a], False),
        "one-project-readonly": (["--project", a, "--readonly"], False),
        "one-project-workspace": (["--project", a, "--workspace", str(ws1)], True),
        "several": (["--project", a, "--project", b], False),
        "several-workspace": (["--project", a, "--project", b, "--workspace", str(ws2)], True),
        "several-workspace-readonly": (
            ["--project", a, "--project", b, "--workspace", str(ws2), "--readonly"],
            True,
        ),
    }
    mcp: dict[str, Any] = {}
    for name, (tail, with_ws) in configs.items():
        n_, o_ = mcp_roundtrip(jc, new, tail), mcp_roundtrip(jc, old_root, tail)
        rec: dict[str, Any] = {
            "instructions_same": n_["instructions"] == o_["instructions"],
            "init_same": n_["init_raw"] == o_["init_raw"],
            "with_workspace": with_ws,
        }
        if with_ws:
            rec["tools_old_prefix"] = n_["tools"][:-1] == o_["tools"]
            rec["appended"] = [t["name"] for t in n_["tools"][len(o_["tools"]) :]]
            rec["tools_same"] = None
        else:
            rec["tools_same"] = n_["tools_raw"] == o_["tools_raw"]
        mcp[name] = rec
    checks["mcp_instructions_6_of_6"] = all(r["instructions_same"] for r in mcp.values())
    checks["mcp_tools_no_workspace_identical"] = all(
        r["tools_same"] for r in mcp.values() if not r["with_workspace"]
    )
    checks["mcp_tools_workspace_plus_one"] = all(
        r["tools_old_prefix"] and r["appended"] == ["linked_question_draft"]
        for r in mcp.values()
        if r["with_workspace"]
    )

    # -- the tree: only the owned files of lattice/ differ
    diff = subprocess.run(
        [
            "diff",
            "-rq",
            str(old_root / "lattice"),
            str(REPO / "lattice"),
            "-x",
            "__pycache__",
            "-x",
            "*.pyc",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    new_prefix = str(REPO) + "/"
    touched = sorted(
        ln.split(" and ")[1].split(" differ")[0].replace(new_prefix, "")
        for ln in diff
        if ln.startswith("Files ")
    )
    new_files = sorted(
        ln.split("Only in ", 1)[1].replace(new_prefix, "").replace(": ", "/")
        for ln in diff
        if ln.startswith("Only in")
    )
    owned = {"lattice/contracts/__main__.py", "lattice/mcp/projects.py"}
    checks["tree_owned_files_only"] = set(touched) == owned and new_files == [
        "lattice/contracts/draft.py"
    ]
    studio_same = (
        subprocess.run(
            [
                "diff",
                "-rq",
                str(old_root / "lattice" / "studio"),
                str(REPO / "lattice" / "studio"),
                "-x",
                "__pycache__",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        == ""
    )
    checks["studio_and_api_md_unchanged"] = studio_same
    extra: dict[str, Any] = {
        "cli_commands": len(cmp),
        "cli_differing": [x for x in cmp if not x["same"]][:10],
        "mcp": mcp,
        "tree_touched": touched,
        "tree_new": new_files,
    }
    obs = (
        f"{sum(1 for x in cmp if x['same'])}/{len(cmp)} CLI argv identical; MCP instructions "
        f"{sum(1 for r in mcp.values() if r['instructions_same'])}/6; tools no-workspace "
        f"{checks['mcp_tools_no_workspace_identical']}, workspace +1 "
        f"{checks['mcp_tools_workspace_plus_one']}; tree {touched} + new {new_files}; studio "
        f"unchanged {studio_same}"
    )
    return mk("P4", pred, obs, [], True, checks, extra)


# ---- main -------------------------------------------------------------------------------------

ORDER = ["P1", "P2", "T", "P4"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--old", type=Path, default=None, help="directory holding git archive 223adff")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args(argv)
    ids = [i for i in ns.only.split(",") if i] or ORDER
    unknown = [i for i in ids if i not in ORDER]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    if "P4" in ids and (ns.old is None or not (ns.old / "lattice" / "workspace.py").is_file()):
        print("P4 needs --old DIR (git archive 223adff extracted)", file=sys.stderr)
        return 2
    ns.out.mkdir(parents=True, exist_ok=True)
    ns.out = ns.out.resolve()
    scratch = ns.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    ns.scratch = scratch
    jc = ep.JCtx(ns.runs, scratch / "j", ns.out)
    sc = ep.SX(ns.runs, scratch / "s", ns.out)
    jc2 = ep.JCtx(1, scratch / "j2", ns.out)
    sc2 = ep.SX(1, scratch / "s2", ns.out)
    for c in (jc, sc, jc2, sc2):
        c.env["PYTHONPATH"] = str(REPO)
    h = lq.W(ns, jc, sc)
    h2 = lq.W(ns, jc2, sc2)
    roots = {
        "$WORK_J": jc.root,
        "$WORK_S": sc.root,
        "$WORK_J2": jc2.root,
        "$WORK_S2": sc2.root,
        "$SCRATCH": scratch,
    }
    repos = {
        "job-scheduler": ep.RealRepo(jc, base.PROVIDER, base.REF, " M uv.lock", [".venv"]),
        "streak": ep.RealRepo(sc, streak.STREAK, streak.REF, " M studio.toml", []),
    }
    t_start = time.time()
    results: list[Result] = []

    def write(res: Result) -> None:
        doc = {
            "id": res.id,
            "prediction": res.prediction,
            "observed": res.observed,
            "pass": res.passed,
            "identical": res.identical,
            "n_runs": ns.runs,
            "checks": res.checks,
            "extra": res.extra,
        }
        (ns.out / f"{res.id}.json").write_text(
            json.dumps(ep.scrub(doc, roots), indent=1, default=str) + "\n"
        )

    try:
        for pid in ids:
            print(f"... {pid}", file=sys.stderr, flush=True)
            t0 = time.time()
            if pid == "P1":
                res = p1(h, h2)
            elif pid == "P2":
                res = p2(h)
            elif pid == "T":
                res = t_probe(h)
            else:
                res = p4(h, ns.old, scratch, False)
            res.extra["wall_s"] = round(time.time() - t0, 1)
            results.append(res)
            write(res)
        s = ep.s_probe(repos)
        s.id = "S"
        s.prediction = (
            "the real job-scheduler (HEAD ef17cf8, ' M uv.lock') and Streak (HEAD a0db033, "
            "' M studio.toml') have the same HEAD and git status before and after the run, and "
            "0 files newer than the start marker"
        )
        results.append(s)
        write(s)
    finally:
        procs = base.leftover_processes(jc) + base.leftover_processes(sc)
        tmp_left = sorted(
            f"{t}/{p.name}"
            for t, c in (("j", jc), ("s", sc), ("j2", jc2), ("s2", sc2))
            for p in (c.tmp.iterdir() if c.tmp.exists() else [])
        )
        if not ns.keep:
            for c in (jc, sc, jc2, sc2):
                lq.writable(c.root)
                shutil.rmtree(c.root, ignore_errors=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<4} | {'observed':<150} | pass")
    for r in results:
        print(f"{r.id:<4} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    ).stdout.strip()
    summary = {
        "experiment": "2026-10-15-consumer-draft-4d",
        "command": "python dogfood/contracts/draft_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_head": head,
        "old_head": OLD_HEAD,
        "job_scheduler_ref": base.REF,
        "streak_ref": streak.REF,
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "ran": "RAN",
                "checks": r.checks,
            }
            for r in results
        ],
        "totals": {"pass": passed, "total": len(results)},
        "p3": "NOT RUN here: draft_agent.py runs separately and last",
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(ep.scrub(summary, roots), indent=1) + "\n")
    return 0 if passed == len(results) and not procs and not tmp_left else 1


if __name__ == "__main__":
    sys.exit(main())
