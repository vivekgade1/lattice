"""Probes of the chat round 2 (Spike F, round 2; docs/results/2026-10-18-goto-chat-round2-prereg.md,
sections 3 to 5, with Amendments 1 and 2): the claim checker, the map router, the checked session
path, byte-compat with `[chat]` absent, no write and no process, determinism, time.

The ground truth of a replay is the frozen round-1 scorer (``frozen-r1/goto_score.py``, sha256
checked at load) and round 1's own questions and answers, which are **dev data** now: tuning on them
is allowed and is not blind evidence. Nothing here reads the round-2 question set in the dev stage.

Stages (the driver venv has pymysql; the Streak checkout is only read with ``git archive``)::

    "$DRV" dogfood/chat2_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede \\
        --workflows docs/results/data/2026-10-15-workflow-layer/workflows.json \\
        --round1 docs/results/data/2026-10-18-goto-chat-round2/frozen-r1 \\
        --round1-answers docs/results/data/2026-10-16-goto-place \\
        --scratch "$SCRATCH/p" --out docs/results/data/2026-10-18-goto-chat-round2
    "$DRV" dogfood/chat2_probes.py --stage freeze --out docs/results/data/2026-10-18-goto-chat-round2
    "$DRV" dogfood/chat2_probes.py --stage qset --questions "$QDIR" --scratch "$SCRATCH/q" \\
        --out docs/results/data/2026-10-18-goto-chat-round2/qset

The ``qset`` stage (the orchestrator's, once) first checks the sha256 of every file listed in
``freeze-build.sha256`` next to its ``--out`` (this runner included) and exits 2 on a difference; the
check has no switch. It reads ``qset.json``, records the six candidates as ready change records
(byte-equality checked against the question set's own hashes), serves the S and S4 corpora from one
Studio server each, collects the map and router answers and calls the wrapper scorer ``chat2_score.py``
of the question directory. It never edits an expectation to make a probe pass; a differing probe is a
finding with its raw output.
"""

# ruff: noqa: E501 -- probe tables and mutant texts are data lines
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import goto_probes as GP  # noqa: E402
import implementation_probes as IP  # noqa: E402

REPO = GP.REPO
OWNER = "fc2-build"
GP.OWNER = OWNER
result, not_run, sha = IP.result, IP.not_run, IP.sha
BASE_REF = "c75cb48"
R1_FREEZE_PREFIX = "d7e5e5aa"  # the frozen round-1 scorer
FREEZE_FILES = [
    "lattice/spec/claim_check.py",
    "lattice/spec/chat_route.py",
    "lattice/studio/session.py",
    "lattice/studio/server.py",
    "lattice/studio/mock_server.py",
    "lattice/studio/API.md",
    "lattice/studio/web/js/chat.js",
    "lattice/studio/web/js/session.js",
    "lattice/studio/web/css/views.css",
    "dogfood/chat2_probes.py",
    "dogfood/chat2_mutants.py",
    "dogfood/chat2_agent.py",
    "tests/spec/test_claim_check.py",
    "tests/spec/test_chat_route.py",
    "tests/spec/chat_world.py",
    "tests/studio/test_chat_check_session.py",
    "tests/studio/test_chat_check_api.py",
]  # fmt: skip
FAKE_CLAUDE = """#!{python}
import json, os, sys
with open(os.environ["FC2_FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
for line in open(os.environ["FC2_FAKE_SCRIPT"]):
    print(line.strip(), flush=True)
"""


# ---- the frozen round-1 material (dev data) ------------------------------------------------------


def load_r1(args: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    """The frozen round-1 scorer (sha256 checked) as a module, and round 1's question set."""
    r1 = Path(args.round1).resolve()
    scorer = r1 / "goto_score.py"
    if not sha(scorer.read_bytes()).startswith(R1_FREEZE_PREFIX):
        raise SystemExit(f"the round-1 scorer {scorer} is not the frozen one")
    spec = importlib.util.spec_from_file_location("goto_score_frozen", scorer)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["goto_score_frozen"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod, json.loads((r1 / "qset.json").read_text())


def r1_answers(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """Round 1's 44 agent answers with its N questions re-asked on S4 (the merged file)."""
    d = Path(args.round1_answers)
    return json.loads((d / "agent-n-on-s4" / "answers-merged.json").read_text())["answers"]


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---- the dev world --------------------------------------------------------------------------------


class Dev:
    """S and S4 as Studio objects with `[chat]` on, the round-1 candidates recorded as c1..c6 of S."""

    def __init__(self, w: GP.World, args: argparse.Namespace) -> None:
        self.w, self.args = w, args
        self.gs, self.qs = load_r1(args)
        self.answers = r1_answers(args)
        self.fake_dir = w.scratch / "fake"
        self.fake_dir.mkdir(parents=True, exist_ok=True)
        self.fake_bin = self.fake_dir / "claude"
        self.fake_bin.write_text(FAKE_CLAUDE.format(python=sys.executable))
        self.fake_bin.chmod(0o755)
        self.fake_log = self.fake_dir / "calls.log"
        self.fake_script = self.fake_dir / "script.jsonl"
        os.environ["FC2_FAKE_LOG"] = str(self.fake_log)
        os.environ["FC2_FAKE_SCRIPT"] = str(self.fake_script)
        self.chat: dict[str, Path] = {}
        self.studios: dict[str, Any] = {}

    def clone(self, name: str, tag: str, chat: str | None) -> Path:
        dst = self.w.scratch / "chat" / f"{name}-{tag}"
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.w.dirs[name], dst, symlinks=True, ignore=shutil.ignore_patterns("bin"))
        inv = self.w.inv["S4" if name == "S4" else "S"]
        GP.set_tables(dst, inv, dst, self.w.state[name])
        text = (dst / "studio.toml").read_text()
        text = text.replace("[llm]\n", f'[llm]\nclaude_bin = "{self.fake_bin}"\n', 1)
        if name == "S4":
            text = re.sub(r"(?ms)\n\[impact\]\n", "\n", text)
        if chat is not None:
            text += f"\n[chat]\n{chat}"
        (dst / "studio.toml").write_text(text)
        return dst

    def studio(
        self, name: str, tag: str = "on", chat: str | None = "route = true\ncheck = true\n"
    ) -> Any:
        from lattice.studio.server import StubGenerator, Studio  # noqa: PLC0415

        key = f"{name}-{tag}"
        if key not in self.studios:
            root = self.clone(name, tag, chat)
            self.chat[key] = root
            # the stub generator keeps the session from discovering connected servers with the fake claude
            self.studios[key] = Studio(root, generator=StubGenerator())
        return self.studios[key]

    def corpus_of(self, q: dict[str, Any]) -> str:
        return "S4" if q["set"] == "N_not_built" else "S"

    def ctx(self, q: dict[str, Any], tag: str = "on") -> Any:
        return self.studio(self.corpus_of(q), tag).chat_context(q["text"])


def record_r1(dev: Dev) -> dict[str, Any]:
    """The six round-1 candidates as ready change records c1..c6 of S, byte-equal checked against
    the round-1 question set; their D reports from the CLI (outside the project)."""
    w = dev.w
    r1 = Path(dev.args.round1).resolve()
    shutil.rmtree(w.state["S"], ignore_errors=True)
    reports = w.scratch / "reports-r1"
    shutil.rmtree(reports, ignore_errors=True)
    reports.mkdir(parents=True)
    token = f"{OWNER}-rec-{os.getpid()}"
    rows: dict[str, Any] = {}
    st = GP.Studio(w, w.dirs["S"], owner=token)
    try:
        for cname in sorted(dev.qs["changes"]):
            info = dev.qs["changes"][cname]
            cand = r1 / "candidates" / Path(info["candidate"]).name
            c = st.draft(cand, f"round-1 candidate {cname}")
            assert c["id"] == cname, (c["id"], cname)
            priv = json.loads((w.state["S"] / "changes" / f"{cname}.json").read_text())["private"]
            got = {"REQUIREMENTS.md": sha(priv["new_md"].encode()), "spec.py": sha(priv["new_module"].encode()),
                   "spec.trace.json": sha(priv["new_trace"].encode())}  # fmt: skip
            want = info["files_sha256"]
            rows[cname] = {"status": c["status"], "byte_equal": all(got.get(k) == v for k, v in want.items())
                           if isinstance(want, dict) else sorted(got.values()) == sorted(want)}  # fmt: skip
            r = GP.engine_cli(w, w.dirs["S"], cname, w.state["S"], out=reports / f"{cname}.json")
            assert r.rc == 0, r.err[-300:]
    finally:
        st.close()
        GP.remove_owner(token)
    return {"changes": rows, "reports": str(reports)}


# ---- PC1, PC3: the offline replay of round 1 ---------------------------------------------------------


def replay(dev: Dev, tag: str = "on") -> dict[str, Any]:
    """Round 1's agent answers through the checker (context rebuilt from the S / S4 worlds), scored
    by the frozen scorer: before and after."""
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    gs, qs = dev.gs, dev.qs
    rows: list[dict[str, Any]] = []
    damage: list[str] = []
    acted_ok = True
    exotic: list[str] = []
    for q in qs["questions"]:
        entry = dev.answers.get(q["id"])
        if not entry:
            continue
        t0 = time.perf_counter()
        res = cc.check_text(entry["text"], dev.ctx(q, tag))
        secs = time.perf_counter() - t0
        before = gs.check_agent(q, entry, qs)
        after = gs.check_agent(q, {**entry, "text": res.text}, qs)
        relevant = dev.ctx(q, tag).support_ids(entry["text"])
        scorer = set(gs.failure_claims(entry["text"], relevant))
        acted = {u["before"] for u in res.units if u["disposition"] in cc._ACTED}
        spec = {u["before"] for u in res.units if u["speculative"]}
        unknown = {u["before"] for u in res.units if u["reason"].startswith("unknown")}
        if not scorer <= acted or not (acted - scorer) <= (spec | unknown):
            acted_ok = False
            exotic.append(q["id"])
        if before["correct"] and not after["correct"]:
            damage.append(q["id"])
        rows.append(
            {
                "id": q["id"], "set": q["set"], "before": bool(before["correct"]), "after": bool(after["correct"]),
                "claims_before": len(before.get("claims", [])), "claims_after": len(after.get("claims", [])),
                "lenient_after": len(after.get("claims_lenient", [])), "compliant": after.get("verbatim_id_compliant"),
                "seconds": round(secs, 3), "counts": {k: v for k, v in res.counts.items() if v},
                "missed": sorted(k for k, v in after["checks"].items() if not v),
            }
        )  # fmt: skip
    by_set: dict[str, list[int]] = {}
    for r in rows:
        d = by_set.setdefault(r["set"], [0, 0, 0])
        d[0] += 1
        d[1] += r["before"]
        d[2] += r["after"]
    return {
        "n": len(rows), "correct_before": sum(r["before"] for r in rows), "correct_after": sum(r["after"] for r in rows),
        "claims_before": sum(r["claims_before"] for r in rows), "claims_after": sum(r["claims_after"] for r in rows),
        "lenient_after": sum(r["lenient_after"] for r in rows),
        "i_compliance": sum(1 for r in rows if r["set"] == "I_impact" and r["compliant"]),
        "damage": damage, "by_set_n_before_after": by_set, "pc1_acted_equals_scorer": acted_ok,
        "pc1_exceptions": exotic, "rows": rows,
    }  # fmt: skip


BUILDER_FIXTURES = [
    "It fails.", "The app will break.", "Nothing breaks.", "This may fail.", "It might break under load.",
    "That could fail.", "Whether it fails is not decided.", "Statically it fails.", "It is static and fails.",
    "A failure is not run here.", "No change fails.", "Nothing fails, so no changes break.", "Broken links break pages.",
    "WF failed.", "wf-053146a70c failed.", "The replay of wf-0c6bdd7ab0 broke.", "States: pending, sent, failed.",
    "R56 says the habit limit fails the 51st habit.", "A check-in that fails validation is refused.",
    "POST /v1/habits will fail.", "t2-aaaaaaaaaa fails.", "t1-bbbbbbbbbb: it failed.", "t3-cccccccccc breaks it.",
    "FAILS", "Fails! Breaks? Broke.", "It does not fail. It does not break.", "Tier 3 may fail but decides nothing.",
    "Tier 2 (determined): 1 failing.", "failing, failed and fails", "The break is clean.", "He broke the build.",
    "- a bullet that fails\n- a bullet that is fine", "| a | fails |\n|---|---|\n| b | fine |", "Line one fails\nline two breaks",
    "First. It fails. Third.", "undecidable but fails", "A flow that breaks is static.", "It could not break.", "may break and fail",
    "Nothing to report.",
]  # fmt: skip


def probe_pc1(dev: Dev, rep: dict[str, Any]) -> dict[str, Any]:
    """The checker's flagged units equal the frozen scorer's claims (plus may/might/could units) on
    round 1's answers, the scorer self-test's claims cases and the builder's fixtures."""
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    gs = dev.gs
    pat_equal = (cc.FAIL_RE.pattern, cc.FAIL_RE.flags) == (gs.FAIL_RE.pattern, gs.FAIL_RE.flags) and (
        cc.HEDGE_RE.pattern, cc.HEDGE_RE.flags) == (gs.HEDGE_RE.pattern, gs.HEDGE_RE.flags)  # fmt: skip
    st = json.loads((Path(dev.args.round1) / "goto_score_selftest.json").read_text())
    selftest = [c for c in st["cases"] if c["surface"] == "claims"]
    bad: list[str] = []
    n_units = 0
    for c in selftest:
        ids = set(c.get("relevant_ids") or [])
        ctx = cc.CheckContext("")
        res = cc.check_text(c["text"], ctx)
        scorer = set(gs.failure_claims(c["text"], ids))
        acted = {u["before"] for u in res.units if u["disposition"] in cc._ACTED}
        n_units += len(cc.sentences(c["text"]))
        if len(scorer) != len(gs.failure_claims(c["text"], ids)) or not scorer <= acted:
            bad.append(c["name"])
        if gs.failure_claims(c["text"], ids) != cc.scorer_claims(c["text"], ids):
            bad.append("splitter:" + c["name"])
    for text in BUILDER_FIXTURES:
        res = cc.check_text(text, cc.CheckContext(""))
        scorer = set(gs.failure_claims(text, set()))
        acted = {u["before"] for u in res.units if u["disposition"] in cc._ACTED}
        spec = {u["before"] for u in res.units if u["speculative"]}
        if not scorer <= acted or not (acted - scorer) <= spec:
            bad.append("fixture:" + text[:30])
        if gs.sentences(text) != cc.sentences(text):
            bad.append("splitter:" + text[:30])
    ok = pat_equal and rep["pc1_acted_equals_scorer"] and not bad
    return result("PC1", "the checker's flagged units equal the frozen scorer's claims (plus the may/might/could ones) on round-1 answers, the scorer self-test's claims cases and the builder's fixtures; the patterns are the scorer's",
                  "equal", {"patterns_equal": pat_equal, "round1_answers": rep["n"], "round1_exceptions": rep["pc1_exceptions"],
                            "selftest_claims_cases": len(selftest), "builder_fixtures": len(BUILDER_FIXTURES), "differences": bad}, ok)  # fmt: skip


def all_fixed_sentences(dev: Dev) -> list[str]:
    """Every sentence the checker or the router can emit over the S map: footers, the router's fixed
    sentences, the D4 rule quotes, the D5 rows, the D6 element lines, the D4 machine line."""
    from lattice.spec import claim_check as cc  # noqa: PLC0415
    from lattice.spec import impact_ask  # noqa: PLC0415

    st = dev.studio("S", "on")
    am = st._appmap()
    out = [
        cc.FOOTER_FIX.format(k=3),
        cc.FOOTER_ID.format(k=2),
        cc.TIER3_FIXED,
        impact_ask.NO_PREDICTION,
    ]
    for r in am.doc["rules"]:
        out.append(cc.render_rule_quote(r["id"], r["text"]))
    for wf in am.doc["workflows"]:
        out.append(cc.render_workflow(wf))
    for m in am.doc["machines"]:
        out.append(cc.render_machine(m))
    for key in _appmap_keys(am):
        out.append(cc.render_element(key, key, am))
    return out


def _appmap_keys(am: Any) -> list[str]:
    from lattice.spec import appmap  # noqa: PLC0415

    return appmap.element_keys(am)


def probe_pc2(dev: Dev, rep: dict[str, Any]) -> dict[str, Any]:
    """The frozen scorer counts 0 literal claims on every checker output; the checker is idempotent;
    every fixed sentence is a fixed point and has no unhedged failure word."""
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    gs = dev.gs
    bad_claims: list[str] = []
    not_idem: list[str] = []
    n = 0
    for q in dev.qs["questions"]:
        entry = dev.answers.get(q["id"])
        if not entry:
            continue
        ctx = dev.ctx(q)
        res = cc.check_text(entry["text"], ctx)
        again = cc.check_text(res.text, dev.ctx(q))
        n += 1
        if gs.failure_claims(res.text, ctx.support_ids(res.text)):
            bad_claims.append(q["id"])
        if again.text != res.text:
            not_idem.append(q["id"])
    for text in BUILDER_FIXTURES:
        res = cc.check_text(text, cc.CheckContext(""))
        again = cc.check_text(res.text, cc.CheckContext(""))
        n += 1
        if gs.failure_claims(res.text, set()):
            bad_claims.append("fixture:" + text[:30])
        if again.text != res.text:
            not_idem.append("fixture:" + text[:30])
    fixed = all_fixed_sentences(dev)
    ctx = dev.studio("S", "on").chat_context("")
    not_fixed = [
        s[:60] for s in fixed if cc.check_text(s, ctx).text != s or gs.failure_claims(s, set())
    ]
    ok = not bad_claims and not not_idem and not not_fixed
    return result("PC2", "fixed point: the frozen scorer counts 0 claims on every checker output; check(check(x)) == check(x); every fixed sentence (footers, rule quotes, workflow rows, element lines, the lifecycle line) is a fixed point with no unhedged failure word",
                  "0; idempotent", {"outputs": n, "claims": bad_claims, "not_idempotent": not_idem,
                                    "fixed_sentences": len(fixed), "not_fixed_points": not_fixed[:5]}, ok)  # fmt: skip


def probe_pc3(rep: dict[str, Any]) -> dict[str, Any]:
    ok = (
        rep["claims_after"] == 0 and rep["lenient_after"] == 0 and not rep["damage"]
        and rep["i_compliance"] == 12 and 33 <= rep["correct_after"] <= 44
    )  # fmt: skip
    obs = {k: rep[k] for k in ("n", "correct_before", "correct_after", "claims_before", "claims_after",
                               "lenient_after", "i_compliance", "damage", "by_set_n_before_after")}  # fmt: skip
    obs["misses_after"] = {r["id"]: r["missed"] for r in rep["rows"] if not r["after"]}
    return result("PC3", "offline replay of round 1: round-1 agent answers (the N questions on S4) through the checker, scored by the frozen scorer",
                  "literal 0, lenient 0; correct 33-37/44; I compliance 12/12; damage 0", obs, ok)  # fmt: skip


def probe_pc4() -> dict[str, Any]:
    """The disposition fixtures: the unit tests assert the exact output bytes of >= 3 cases per
    D0-D7 and E1-E3 (tests/spec/test_claim_check.py)."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/spec/test_claim_check.py", "-q", "--no-header", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=REPO, env=dict(os.environ, PYTHONPATH=str(REPO)), check=False,
    )  # fmt: skip
    src = (REPO / "tests/spec/test_claim_check.py").read_text()
    per = {
        k: len(re.findall(rf'"{k}[a-z]?",', src))
        for k in ("D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "E1", "E2", "E3")
    }
    ok = p.returncode == 0 and all(v >= 3 for v in per.values())
    return result("PC4", "disposition fixtures: >= 3 per D0-D7 and E1-E3, each asserting the exact output bytes", "all",
                  {"fixtures": per, "pytest": (p.stdout.strip().splitlines() or [p.stderr[-200:]])[-1]}, ok)  # fmt: skip


# ---- PR2: the router on round 1's questions -----------------------------------------------------------


def probe_pr2(dev: Dev) -> dict[str, Any]:
    from lattice.spec import impact_ask  # noqa: PLC0415

    gs, qs = dev.gs, dev.qs
    rows = []
    mn = [
        q
        for q in qs["questions"]
        if q["set"] in ("M_rules", "M_workflows", "M_machines", "M_access", "N_not_built")
    ]
    routed_wrong: list[str] = []
    c_routed: list[str] = []
    ih_changed: list[str] = []
    for q in qs["questions"]:
        st = dev.studio(dev.corpus_of(q), "on")
        got = st.chat_pre_route(q["text"])
        f_routed = impact_ask.is_routed(q["text"])
        if q["set"] in ("I_impact", "H_hypothetical"):
            expected_kind = None
            if f_routed:
                expected_kind = "impact" if q["set"] == "I_impact" else "hypothetical"
            # I routes through F exactly as before; H through F or the broad cue (never to the map)
            if q["set"] == "I_impact" and (got is None or got[1] != expected_kind):
                ih_changed.append(q["id"])
            if q["set"] == "H_hypothetical" and (
                got is None or got[1] not in ("hypothetical", "impact")
            ):
                ih_changed.append(q["id"])
            continue
        if got is None:
            if q["set"] == "C_control":
                continue
            rows.append({"id": q["id"], "routed": None})
            continue
        if q["set"] == "C_control":
            c_routed.append(q["id"])
        from lattice.spec import claim_check as cc  # noqa: PLC0415

        res = cc.check_text(got[0], st.chat_context(q["text"]))
        sc = gs.check_agent(q, {"text": res.text, "status": "ok"}, qs)
        unchanged = res.text == got[0]
        rows.append(
            {
                "id": q["id"],
                "routed": got[1],
                "correct": bool(sc["correct"]),
                "claims": len(sc["claims"]),
                "unchanged_by_checker": unchanged,
            }
        )
        if not sc["correct"] or sc["claims"] or not unchanged:
            routed_wrong.append(q["id"])
    n_routed = sum(1 for r in rows if r.get("routed"))
    share = n_routed / len(mn)
    ok = not routed_wrong and not c_routed and not ih_changed and 0.4 <= share <= 0.8
    return result("PR2", "router on round 1's questions: every routed answer correct under the frozen scorer's agent checks and unchanged by the checker; 0 C routed; I and H routing as F's",
                  "routed share of M+N 40-80% (p); precision 100%; 0 C routed",
                  {"routed": n_routed, "of_M_N": len(mn), "share": round(share, 2), "routed_wrong": routed_wrong, "c_routed": c_routed,
                   "i_h_changed": ih_changed, "not_routed": [r["id"] for r in rows if not r.get("routed")], "rows": rows}, ok)  # fmt: skip


# ---- PS2: the session path --------------------------------------------------------------------------------

SID = "11111111-2222-3333-4444-555555555555"


def _se(e: dict[str, Any]) -> dict[str, Any]:
    return {"type": "stream_event", "event": e}


def stream_of(
    blocks: Sequence[str], *, tool_after: int | None = None, result_text: str | None = None
) -> list[dict[str, Any]]:
    """A stream-json turn: each text a streamed block (deltas, then the assistant record), an
    optional tool call after block `tool_after`, and the result."""
    recs: list[dict[str, Any]] = [{"type": "system", "subtype": "init", "session_id": SID}]
    for i, text in enumerate(blocks):
        mid = f"m{i + 1}"
        half = len(text) // 2
        recs += [
            _se({"type": "message_start", "message": {"id": mid}}),
            _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}),
            _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[:half]}}),
            _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[half:]}}),
            _se({"type": "content_block_stop", "index": 0}),
            {"type": "assistant", "message": {"id": mid, "content": [{"type": "text", "text": text}]}},
        ]  # fmt: skip
        if tool_after == i:
            recs += [
                {"type": "assistant", "message": {"id": mid, "content": [{"type": "tool_use", "id": f"t{i}", "name": "mcp__lattice__spec_ask", "input": {"question": "x"}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "ok"}]}},
            ]  # fmt: skip
    final = result_text if result_text is not None else (blocks[-1] if blocks else "")
    recs.append({"type": "result", "subtype": "success", "is_error": False, "result": final, "session_id": SID,
                 "total_cost_usd": 0.05, "duration_ms": 1200, "num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 5}})  # fmt: skip
    return recs


def probe_ps2(dev: Dev) -> dict[str, Any]:
    """Check mode with a fake claude replaying stream-json made from round-1 answers and planted
    violations: no partial text event, every block (also before a tool call) checked, result.check
    sums the counts, routed turns spawn no process."""
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    st = dev.studio("S", "on")
    sess = st.sessions
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for qid in ("H-1", "I-c4-1", "M-W3", "M-M1", "C-1", "H-5"):
        text = dev.answers[qid]["text"]
        cut = text.find("\n\n", len(text) // 3)
        cut = cut if cut > 0 else len(text) // 2
        fixtures[f"r1-{qid}"] = stream_of(
            [text[:cut], text[cut:].lstrip()], tool_after=0, result_text=text[cut:].lstrip()
        )
    fixtures["planted-pre-tool"] = stream_of(
        ["Before the tool call it will break everything.", "After it, nothing fails."], tool_after=0
    )
    fixtures["planted-single"] = stream_of(
        ["The R56 rule breaks the 51st habit and wf-053146a70c failed."]
    )
    rows: dict[str, Any] = {}
    ok = True
    for name, recs in fixtures.items():
        dev.fake_script.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        sid = sess.create({"mode": "ask"})["id"]
        qtext = "Walk me through rule R56 in the spec."  # a question the router does not take
        sess.message(sid, {"text": qtext})
        end = time.monotonic() + 60
        view = sess.events(sid, 0)
        while not view["done"] and time.monotonic() < end:
            time.sleep(0.05)
            view = sess.events(sid, 0)
        ev = view["events"]
        texts = [e for e in ev if e["type"] == "text"]
        res = [e for e in ev if e["type"] == "result"]
        shown = "\n\n".join(e["text"] for e in texts)
        raw_blocks = [
            b["message"]["content"][0]["text"]
            for b in recs
            if b.get("type") == "assistant" and b["message"]["content"][0].get("type") == "text"
        ]
        ctx_ref = st.chat_context(qtext)
        expected = cc.new_counts()
        for blk in raw_blocks:
            expected = cc.merge_counts(expected, cc.check_block(blk, ctx_ref).counts)
        row = {
            "partial_events": sum(1 for e in texts if e.get("partial")),
            "texts_without_checked": sum(1 for e in texts if "checked" not in e),
            "raw_block_in_events": any(
                b and b in json.dumps(ev)
                for b in raw_blocks
                if cc.FAIL_RE.search(b) and not cc.HEDGE_RE.search(b)
            ),
            "result_has_check": bool(res)
            and "check" in res[0]
            and res[0].get("routed") is None
            and all(res[0]["check"][k] == expected[k] for k in expected if k != "appended"),
            "result_text_is_shown": bool(res) and res[0]["text"] == shown,
            "event_types": [e["type"] for e in ev],
            "check_vs_expected": [
                {k: v for k, v in (res[0].get("check") or {}).items() if v},
                {k: v for k, v in expected.items() if v},
            ],
            "claims": cc.scorer_claims(shown, st.chat_context(qtext).support_ids(shown)),
        }
        row["ok"] = (
            row["partial_events"] == 0 and row["texts_without_checked"] == 0 and not row["raw_block_in_events"]
            and row["result_has_check"] and row["result_text_is_shown"] and not row["claims"]
        )  # fmt: skip
        rows[name] = row
        ok &= row["ok"]
    calls_before = len(dev.fake_log.read_text().splitlines()) if dev.fake_log.exists() else 0
    routed_rows = {}
    for q in (
        "Which rules are required by the spec but not built yet?",
        "Which workflows failed?",
        "What if we raised the habit limit to 100?",
    ):
        sid = sess.create({"mode": "ask"})["id"]
        sess.message(sid, {"text": q})
        view = sess.events(sid, 0)
        types = [e["type"] for e in view["events"]]
        routed_rows[q[:40]] = {
            "events": types,
            "routed": view["events"][2].get("routed"),
            "done": view["done"],
        }
        ok &= (
            types == ["user", "text", "result", "status"]
            and view["done"]
            and view["events"][2].get("routed") in ("map", "hypothetical")
        )
    calls_after = len(dev.fake_log.read_text().splitlines()) if dev.fake_log.exists() else 0
    ok &= calls_after == calls_before
    return result("PS2", "session with a fake claude replaying stream-json (round-1 answers plus planted violations): check mode emits 0 partial text events; every text block, also before a tool call, is checked; result.check and routed are reported; routed turns spawn 0 processes",
                  "as stated", {"fixtures": rows, "routed": routed_rows, "fake_claude_calls_during_routed": calls_after - calls_before}, ok)  # fmt: skip


# ---- PB2: byte-compat with [chat] absent --------------------------------------------------------------------

TRANSCRIPT_SCRIPT = r"""
import json, re, sys
from lattice.studio.session import Transcript
out = {}
for path in sys.argv[1:]:
    t = Transcript()
    t.add_user("q", [])
    t.set_state("starting")
    for line in open(path):
        t.feed(json.loads(line))
    t.set_state("idle")
    evs = [{k: v for k, v in e.items() if k not in ("ts", "seq")} for e in t.events]
    out[path] = evs
print(json.dumps(out, sort_keys=True))
"""


def probe_pb2(
    dev: Dev, base: Path, pytest_log: Path | None, baseline_log: Path | None
) -> dict[str, Any]:
    w = dev.w
    checks: dict[str, Any] = {}
    ps = GP.probe_ps(w, base)
    checks["session argv, settings, prompt (4 configs) identical to base"] = ps["pass"]
    checks["session detail"] = ps["observed"]
    # protected files: an empty diff against the base ref
    protected = ["lattice/spec/ask.py", "lattice/spec/impact_ask.py", "lattice/spec/appmap.py", "lattice/spec/impact_report.py",
                 "lattice/spec/impact_tier2.py", "lattice/spec/impact_tier3.py", "lattice/spec/impact_tier4.py",
                 "lattice/spec/implementation.py", "lattice/mcp/tools.py", "lattice/spec/check.py", "lattice/importer", "lattice/gocheck"]  # fmt: skip
    d = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--stat", BASE_REF, "--", *protected],
        capture_output=True,
        text=True,
        check=False,
    )
    checks["protected files: empty diff"] = d.stdout.strip() == ""
    # existing tests, goldens and fixtures untouched, but the one-line header assertion of API.md
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "diff",
            "--numstat",
            BASE_REF,
            "--",
            "tests",
            "lattice/studio/mock",
            "docs/results/data/2026-10-16-goto-place",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    changed = [
        c for c in (ln.split("\t") for ln in diff.splitlines() if ln.strip())
        if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{BASE_REF}:{c[2]}"], capture_output=True, check=False).returncode == 0
    ]  # fmt: skip
    checks["existing tests/fixtures changed (added, deleted, path)"] = changed
    checks["existing tests: only the API header assertion changed"] = [c[2] for c in changed] == [
        "tests/studio/test_appmap_api.py"
    ] and changed[0][:2] == ["1", "1"]
    # the Transcript on replayed stream fixtures: identical events, ts and seq dropped
    fixtures = sorted((REPO / "tests/studio/fixtures/chat2").glob("*.jsonl"))
    a = GP.run_in_tree(w, base, TRANSCRIPT_SCRIPT, [str(f) for f in fixtures], "pb2-tr-base")
    b = GP.run_in_tree(w, REPO, TRANSCRIPT_SCRIPT, [str(f) for f in fixtures], "pb2-tr-new")
    checks["Transcript events on 6 stream fixtures: identical"] = (
        a.data is not None and a.data == b.data and len(fixtures) == 6
    )
    # the real server and the mock: every existing route identical
    sys.path.insert(0, str(REPO))
    from tests.studio.test_server import make_project  # noqa: PLC0415

    shutil.rmtree(w.scratch / "pb2-project", ignore_errors=True)
    proj = make_project(w.scratch / "pb2-project", repo=True)
    pa, pb_ = w.scratch / "pb2-a" / "project", w.scratch / "pb2-b" / "project"
    for dst in (pa, pb_):
        shutil.rmtree(dst.parent, ignore_errors=True)
        dst.parent.mkdir(parents=True)
        shutil.copytree(proj, dst, symlinks=True)
    roots = [str(pa), str(pb_), str(proj)]
    old_srv = GP.normalise(GP.pb_server_answers(w, base, pa), roots)
    new_srv = GP.normalise(GP.pb_server_answers(w, REPO, pb_), roots)
    checks["real server: every existing route identical"] = old_srv == new_srv
    old_mock, new_mock = (
        GP.normalise(GP.pb_mock_answers(w, base), []),
        GP.normalise(GP.pb_mock_answers(w, REPO), []),
    )
    checks["mock server: every existing route identical"] = old_mock == new_mock
    # POST /api/ask on the fixture world (routed by F and not routed, with [impact]), real, both trees
    mock_fix = REPO / "lattice/studio/mock/appmap"
    ask_rows = {}
    for tree_name, tree in (("base", base), ("new", REPO)):
        root = w.scratch / f"pb2-ask-{tree_name}"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        for sub in ("app", "inventory", "candidate"):
            shutil.copytree(mock_fix / sub, root / sub)
        shutil.copytree(mock_fix / "project", root / "project")
        (root / "project" / "studio.toml").write_text(
            '[llm]\nmodel = "scripted"\n\n[implementation]\ninventory = "../inventory"\napp_root = "../app"\n\n[impact]\n\n'
            + f'[state]\ndir = "{root / "state"}"\n'
        )
        pr = GP.Proc(w, tree, GP.SERVE_SCRIPT, [str(root / "project")])
        try:
            ask_rows[tree_name] = {q: GP.http("POST", pr.base + "/api/ask", {"question": q}) for q in (
                "What breaks if c1 is applied?", "What would break if we raised the limit?", "Which rules are required by the spec but not built yet?",
                "Is POST /api/login implemented?", "What if we removed GET /health?", "A project can be restored after it is archived?")}  # fmt: skip
        finally:
            pr.close()
    checks["POST /api/ask identical (routed by F, shapes of the map router, free)"] = GP.normalise(
        ask_rows["base"], []
    ) == GP.normalise(ask_rows["new"], [])
    # tools/list and instructions
    mcp_rows = {}
    for cname, drop_impact in (("[implementation]", True), ("[implementation] + [impact]", False)):
        proj_dir = w.scratch / f"pb2-mcp-{int(drop_impact)}"
        shutil.rmtree(proj_dir, ignore_errors=True)
        shutil.copytree(
            w.dirs["S-root2"], proj_dir, symlinks=True, ignore=shutil.ignore_patterns("bin")
        )
        GP.set_tables(
            proj_dir, w.inv["S-root2"], proj_dir, w.scratch / f"state-pb2-{int(drop_impact)}"
        )
        if drop_impact:
            t = (proj_dir / "studio.toml").read_text()
            (proj_dir / "studio.toml").write_text(re.sub(r"(?m)^\[impact\]\n", "", t))
        for ro in (False, True):
            x, y = GP.mcp_answers(w, base, proj_dir, ro), GP.mcp_answers(w, REPO, proj_dir, ro)
            mcp_rows[f"{cname} readonly={ro}"] = {"identical": x == y, "tools": len(y["names"])}
    checks["mcp tools/list and instructions"] = mcp_rows
    help_same = True
    for cmd in (
        ["-m", "lattice.cli", "--help"],
        ["-m", "lattice.cli", "spec"],
        ["-m", "lattice.spec"],
    ):
        help_same &= GP.text_of([sys.executable, *cmd], base, w, "pb2-help-a") == GP.text_of(
            [sys.executable, *cmd], REPO, w, "pb2-help-b"
        )
    checks["lattice --help and spec usage identical"] = help_same
    # the web files: only chat.js, session.js and views.css differ, additively
    web_diff = []
    for f in sorted((REPO / "lattice/studio/web").rglob("*")):
        if f.is_file():
            rel = f.relative_to(REPO / "lattice/studio/web").as_posix()
            bf = base / "lattice/studio/web" / rel
            if not bf.is_file() or bf.read_bytes() != f.read_bytes():
                web_diff.append(rel)
    checks["web files that differ"] = web_diff
    adds = {}
    for rel in web_diff:
        ns = subprocess.run(
            [
                "git",
                "-C",
                str(REPO),
                "diff",
                "--numstat",
                BASE_REF,
                "--",
                f"lattice/studio/web/{rel}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        adds[rel] = ns[:2]
    checks["web edits (added, deleted)"] = adds
    checks["web edits are additive in chat.js, session.js, views.css only"] = set(web_diff) <= {"js/chat.js", "js/session.js", "css/views.css"} and all(int(v[1]) <= 2 for v in adds.values()) and len(web_diff) > 0  # fmt: skip
    if (
        pytest_log is not None
        and baseline_log is not None
        and pytest_log.is_file()
        and baseline_log.is_file()
    ):

        def fail(p: Path) -> list[str]:
            txt = p.read_text()
            heads = re.findall(r"^_{3,} (\S+) _{3,}$", txt, re.M)
            return sorted(set(heads) | set(re.findall(r"^(?:FAILED|ERROR) (\S+)", txt, re.M)))

        def errors(p: Path) -> str:
            m = re.search(r"(\d+) errors?", p.read_text().strip().splitlines()[-1])
            return m.group(1) if m else "0"

        fa, fb = fail(baseline_log), fail(pytest_log)
        only_new = sorted(set(fb) - set(fa))
        # a guard that compares the working tree with HEAD (tests/studio/test_appmap_ui.py) fails until
        # the commit that holds the registered web edits exists; it passes in a scratch clone with them
        explained = {"test_existing_view_files_are_byte_unchanged"}
        checks["pytest: same failure ids as the base (but the HEAD guard)"] = set(fa) == set(
            fb
        ) - explained and errors(baseline_log) == errors(pytest_log)
        checks["pytest failure ids only in the new run"] = only_new
        checks["pytest failure ids only in the base run"] = sorted(set(fa) - set(fb))
        checks["pytest summary (new)"] = pytest_log.read_text().strip().splitlines()[-1]
        checks["pytest summary (base)"] = baseline_log.read_text().strip().splitlines()[-1]
    else:
        checks["pytest: same failure ids as the base (but the HEAD guard)"] = None
    bools = [v for k, v in checks.items() if isinstance(v, bool)]
    ok = (
        all(bools)
        and all(r["identical"] for r in mcp_rows.values())
        and checks["pytest: same failure ids as the base (but the HEAD guard)"] is True
    )
    return result("PB2", "byte-compat with `[chat]` absent against git archive of the base: session argv/settings/prompt, Transcript events, every existing route (real and mock), tools/list, help, protected files, pytest failure ids",
                  "identical", checks, ok)  # fmt: skip


# ---- PW2: no write, no process ----------------------------------------------------------------------------


def probe_pw2(dev: Dev) -> dict[str, Any]:
    from lattice.spec import chat_route as CR  # noqa: PLC0415
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    w = dev.w
    st = dev.studio("S", "on")
    root = dev.chat["S-on"]
    targets = {
        "project": root,
        "inventory": w.inv["S"],
        "state": w.state["S"],
        "reports": w.scratch / "reports-r1",
    }
    st.chat_context("warm").am()  # the first map build may fill an in-memory cache
    before = {k: IP.snapshot(v) for k, v in targets.items()}
    docker_before = GP.docker_names()
    texts = [dev.answers[q["id"]]["text"] for q in dev.qs["questions"] if q["id"] in dev.answers]
    n_checked = n_routed = 0
    for i in range(50):
        q = dev.qs["questions"][i % len(dev.qs["questions"])]
        if q["id"] not in dev.answers:
            continue
        cc.check_text(dev.answers[q["id"]]["text"], st.chat_context(q["text"]))
        n_checked += 1
    routed_questions = [
        q["text"]
        for q in dev.qs["questions"]
        if q["set"] in ("M_rules", "M_workflows", "M_machines", "M_access", "H_hypothetical")
    ]
    for i in range(50):
        got = st.chat_pre_route(routed_questions[i % len(routed_questions)])
        if got is not None:
            cc.check_text(got[0], st.chat_context("x"))
            n_routed += 1
    # a routed session turn: the PATH shim (and the fake binary) must see no call
    shim_before = w.shim_log.read_text() if w.shim_log.exists() else ""
    calls_before = len(dev.fake_log.read_text().splitlines()) if dev.fake_log.exists() else 0
    sess = st.sessions
    for q in (
        "Which rules have no element in the code at all?",
        "Which workflows did not pass when replayed?",
    ):
        sid = sess.create({"mode": "ask"})["id"]
        sess.message(sid, {"text": q})
    calls_after = len(dev.fake_log.read_text().splitlines()) if dev.fake_log.exists() else 0
    after = {k: IP.snapshot(v) for k, v in targets.items()}
    diffs = {k: IP.diff_snapshots(before[k], after[k]) for k in targets}
    changed = {k: d for k, d in diffs.items() if d["changed"] or d["new"] or d["gone"]}
    shim = w.shim_log.read_text() if w.shim_log.exists() else ""
    docker_after = GP.docker_names()
    ok = (
        not changed
        and shim == shim_before
        and calls_after == calls_before
        and docker_before == docker_after
    )
    return result("PW2", "no write, no process: snapshots of the project, inventory, state and reports directories around 50 checked answers, 50 routed answers and routed session turns; the claude shim and the fake binary; docker ps -a",
                  {"changed": 0, "new": 0, "calls": 0}, {"checked": n_checked, "routed": n_routed, "changed/new/gone": {k: {kk: len(vv) for kk, vv in d.items()} for k, d in diffs.items()},
                  "first changes": dict(list(changed.items())[:2]), "claude calls": calls_after - calls_before, "shim grew": shim != shim_before,
                  "new containers": sorted(set(docker_after) - set(docker_before)), "texts": len(texts), "routing module": CR.SCHEMA}, ok)  # fmt: skip


# ---- PD2: determinism ----------------------------------------------------------------------------------------

PD_SCRIPT = r"""
import hashlib, json, sys
from pathlib import Path
from lattice.studio.server import Studio
from lattice.spec import claim_check as cc, chat_route as CR
root, corpus = Path(sys.argv[1]), json.load(open(sys.argv[2]))
st = Studio(root, generator=None)
h = hashlib.sha256()
for row in corpus:
    ctx = st.chat_context(row["q"])
    res = cc.check_text(row["text"], ctx)
    h.update(res.canonical().encode())
    r = CR.route(row["q"], appmap=st._map_or_none)
    h.update(json.dumps(r.to_ask() if r else None, sort_keys=True).encode())
    got = st.chat_pre_route(row["q"])
    h.update(json.dumps(got).encode())
print(h.hexdigest())
"""


def probe_pd2(dev: Dev, runs: int) -> dict[str, Any]:
    w = dev.w
    corpus = [
        {"q": q["text"], "text": dev.answers[q["id"]]["text"]}
        for q in dev.qs["questions"]
        if q["set"] != "N_not_built" and q["id"] in dev.answers
    ]
    cfile = w.scratch / "pd2-corpus.json"
    cfile.write_text(json.dumps(corpus))
    root_a = dev.clone("S", "pd-a", "route = true\ncheck = true\n")
    root_b = dev.clone("S", "pd-b", "route = true\ncheck = true\n")
    seeds = [str(random.Random(100 + i).randrange(1, 4_000_000)) for i in range(runs)]
    digests = []
    for i, seed in enumerate(seeds):
        r = w.py(
            ["-c", PD_SCRIPT, str(root_a if i % 2 == 0 else root_b), str(cfile)],
            hashseed=seed,
            label="pd2",
        )
        digests.append(
            r.out.strip().splitlines()[-1]
            if r.rc == 0 and r.out.strip()
            else f"rc={r.rc}:{r.err[-200:]}"
        )
    ok = len(set(digests)) == 1 and not digests[0].startswith("rc=")
    return result("PD2", f"determinism: check_text and the router over round-1 questions and answers in {runs} processes with random PYTHONHASHSEED and two scratch roots", "1 distinct output",
                  {"runs": runs, "distinct": len(set(digests)), "digest": digests[0][:16]}, ok)  # fmt: skip


# ---- PX2: time ---------------------------------------------------------------------------------------------------


def probe_px2(dev: Dev) -> dict[str, Any]:
    from lattice.spec import claim_check as cc  # noqa: PLC0415

    st = dev.studio("S", "on")
    cold, warm = [], []
    for q in dev.qs["questions"]:
        entry = dev.answers.get(q["id"])
        if not entry or q["set"] == "N_not_built":
            continue
        ctx = st.chat_context(q["text"])
        t0 = time.perf_counter()
        cc.check_text(entry["text"], ctx)
        cold.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        cc.check_text(entry["text"], ctx)
        warm.append(time.perf_counter() - t0)
    pre = []
    for q in dev.qs["questions"]:
        t0 = time.perf_counter()
        st.chat_pre_route(q["text"])
        pre.append(time.perf_counter() - t0)

    def pct(xs: list[float], p: float) -> float:
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)

    obs = {"check_text cold (ctx built per answer) p50/p95 s": [round(statistics.median(cold), 3), pct(cold, 0.95)],
           "check_text warm (ctx cached for the turn) p50/p95 s": [round(statistics.median(warm), 3), pct(warm, 0.95)],
           "pre-route p50/p95 s": [round(statistics.median(pre), 3), pct(pre, 0.95)], "n": [len(cold), len(pre)]}  # fmt: skip
    ok = pct(cold, 0.95) <= 1.0 and statistics.median(pre) <= 0.5
    return result("PX2", "time on S: check_text per answer and the pre-route", "0.05-0.4 s; p95 <= 1.0 s; pre-route p50 <= 0.5 s (gate); p50 <= 0.25 s (p)", obs, ok)  # fmt: skip


# ---- PU2, PAPI2, PP2, GATE and the pytest rows -------------------------------------------------------------------

FORBIDDEN = re.compile(
    r"\b(?:will fail|would fail|will break|would break)\b|predict", re.IGNORECASE
)


def probe_pu2() -> dict[str, Any]:
    """node --check of the changed scripts, the pure functions under node (reduce keeps `checked`,
    checkedWords), and the forbidden-word scan of every line the build added to the web files."""
    web = REPO / "lattice/studio/web/js"
    checks: dict[str, Any] = {}
    for f in ("chat.js", "session.js"):
        p = subprocess.run(
            ["node", "--check", str(web / f)], capture_output=True, text=True, check=False
        )
        checks[f"node --check {f}"] = p.returncode == 0
    script = """
class F { constructor(t){this.tagName=t;this.children=[];this.attrs={};this.style={};} append(){} setAttribute(){} addEventListener(){} }
globalThis.Node = class {};
globalThis.document = { createElement: (t) => new F(t), createElementNS: (_n, t) => new F(t), createTextNode: (t) => ({text:t}), addEventListener(){}, getElementById(){return null}, body: new F("body")};
globalThis.window = { addEventListener(){}, dispatchEvent(){}, matchMedia: () => ({ matches: false }), location: {hash: ""} };
globalThis.localStorage = {getItem(){return null}, setItem(){}};
const c = await import(process.argv[1] + "/chat.js");
const s = await import(process.argv[1] + "/session.js");
const t = s.newThread();
s.reduce(t, [{type:"text", text:"hi", partial:false, message_id:"m1", checked:{withheld:1, replaced_item:2, replaced_quote:1}},
             {type:"text", text:"plain", partial:false, message_id:"m2"},
             {type:"result", ok:true, text:"x", cost_usd:0, duration_ms:0, num_turns:0, routed:"map", check:{}}]);
console.log(JSON.stringify({words: c.checkedWords(t.items[0].checked), nochecked: "checked" in t.items[1], kinds: t.items.map(i=>i.kind), zero: c.checkedWords({})}));
"""
    p = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(web)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"error": p.stderr[-300:]}
    checks["pure functions under node"] = out
    checks["badge words"] = (
        out.get("words") == "Checked by Lattice: 1 withheld, 3 replaced"
        and out.get("zero") == "Checked by Lattice: 0 withheld, 0 replaced"
        and out.get("nochecked") is False
    )
    added = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "diff",
            "-U0",
            BASE_REF,
            "--",
            "lattice/studio/web/js/chat.js",
            "lattice/studio/web/js/session.js",
            "lattice/studio/web/css/views.css",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    lines = [ln[1:] for ln in added.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    bad = [ln.strip()[:100] for ln in lines if FORBIDDEN.search(ln)]
    checks["added lines"] = len(lines)
    checks["forbidden words in added lines"] = bad
    ok = (
        all(v for k, v in checks.items() if k.startswith("node --check"))
        and checks["badge words"]
        and not bad
    )
    return result("PU2", "chat.js badge: node --check, the pure functions under node, the forbidden-word scan of the added lines (a fake-DOM render of a whole transcript is NOT RUN: chat.js's renderer is not exported)",
                  "all", checks, ok)  # fmt: skip


def pytest_row(pid: str, desc: str, files: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)
    p = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            *files,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=1800,
        check=False,
    )
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
    return result(pid, desc, "all pass", {"files": files, "summary": last}, p.returncode == 0)


def probe_gate(out: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    env = dict(os.environ)
    p = subprocess.run(
        [str(REPO / "dogfood/studio/check.sh"), "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO / "dogfood/studio",
        timeout=1800,
        check=False,
    )
    data = None
    with contextlib.suppress(ValueError):
        data = json.loads(p.stdout)
    (out / "studio-check-sh.json").write_text(p.stdout)
    cases = (data or {}).get("cases", {})
    scen = {k.rsplit(" / step", 1)[0] for k in cases}
    ok = (
        p.returncode == 0
        and data is not None
        and data.get("verdict") == "pass"
        and all(c["verdict"] == "correct" for c in cases.values())
    )
    return result("GATE", "dogfood/studio/check.sh with R109-R112", "PASS; 509 + 3-16 cases; 62 + 1-4 scenarios (p)",
                  {"rc": p.returncode, "verdict": (data or {}).get("verdict"), "cases": len(cases), "scenarios": len(scen), "findings": len((data or {}).get("findings", [])),
                   "seconds": round(time.perf_counter() - t0, 1)}, ok)  # fmt: skip


def write_freeze(out: Path) -> dict[str, str]:
    rows = {f: sha((REPO / f).read_bytes()) for f in FREEZE_FILES if (REPO / f).is_file()}
    rows["lattice/studio/web/css/views.css"] = sha(
        (REPO / "lattice/studio/web/css/views.css").read_bytes()
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "freeze-build.sha256").write_text(
        "".join(f"{h}  {f}\n" for f, h in sorted(rows.items()))
    )
    return rows


def verify_freeze(out: Path) -> None:
    """Exit 2 when a frozen file changed since freeze-build.sha256. No switch."""
    path = out / "freeze-build.sha256"
    if not path.is_file():
        raise SystemExit(f"no {path}: freeze the build first (--stage freeze)")
    bad = []
    for ln in path.read_text().splitlines():
        h, _, f = ln.partition("  ")
        if not (REPO / f).is_file() or sha((REPO / f).read_bytes()) != h:
            bad.append(f)
    if bad:
        print(
            "the frozen build changed since freeze-build.sha256:", ", ".join(bad), file=sys.stderr
        )
        raise SystemExit(2)


# ---- the dev stage ------------------------------------------------------------------------------------------------


def run_dev(args: argparse.Namespace) -> int:
    w = GP.World(args)
    os.environ["PATH"] = f"{w.shim}{os.pathsep}{os.environ['PATH']}"
    only = {s for s in (args.only or "").split(",") if s}
    results: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    containers_before = GP.ours_now()
    for d in ("state-S", "state-S-root2", "state-S4", "state-S-stale"):
        shutil.rmtree(w.scratch / d, ignore_errors=True)
    GP.setup(w, build=False)
    dev = Dev(w, args)
    rec = record_r1(dev)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def want(*ids: str) -> bool:
        return not only or any(i in only for i in ids)

    rep = replay(dev)
    (out / "replay-round1.json").write_text(
        json.dumps({k: v for k, v in rep.items()}, indent=1, default=str)
    )
    base = GP.make_base(w, BASE_REF) if want("PB2") else None
    try:
        if want("PC1"):
            results.append(probe_pc1(dev, rep))
        if want("PC2"):
            results.append(probe_pc2(dev, rep))
        if want("PC3"):
            results.append(probe_pc3(rep))
        if want("PC4"):
            results.append(probe_pc4())
        if want("PR2"):
            results.append(probe_pr2(dev))
        if want("PS2"):
            results.append(probe_ps2(dev))
        if want("PB2"):
            results.append(
                probe_pb2(
                    dev,
                    base,
                    Path(args.pytest_log) if args.pytest_log else None,
                    Path(args.baseline_log) if args.baseline_log else None,
                )
            )  # type: ignore[arg-type]
        if want("PW2"):
            results.append(probe_pw2(dev))
        if want("PD2"):
            results.append(probe_pd2(dev, args.runs))
        if want("PX2"):
            results.append(probe_px2(dev))
        if want("PU2"):
            results.append(probe_pu2())
        if want("PAPI2", "PP2"):
            results.append(
                pytest_row(
                    "PAPI2",
                    "## v1.26 fenced shapes equal the real and the mock answers recursively; header v1.26",
                    [
                        "tests/studio/test_chat_check_api.py::test_the_documented_shapes_are_the_real_ones"
                    ],
                )
            )
            results.append(
                pytest_row(
                    "PP2",
                    "the mock uses the same chat_route and claim_check over its fixture; mock equals real key for key",
                    [
                        "tests/studio/test_chat_check_api.py::test_the_mock_answers_key_for_key_what_the_real_server_does"
                    ],
                )
            )
        if want("PTEST"):
            results.append(
                pytest_row(
                    "PTEST",
                    "the new unit, session and API tests",
                    [
                        "tests/spec/test_claim_check.py",
                        "tests/spec/test_chat_route.py",
                        "tests/studio/test_chat_check_session.py",
                        "tests/studio/test_chat_check_api.py",
                    ],
                )
            )
        if want("GATE"):
            results.append(probe_gate(out))
    finally:
        for s in dev.studios.values():
            with contextlib.suppress(Exception):
                s.close()
        if (
            hasattr(dev.studios.get("S-on"), "_sessions")
            and dev.studios["S-on"]._sessions is not None
        ):
            with contextlib.suppress(Exception):
                dev.studios["S-on"]._sessions.close()
        GP.remove_owner(f"{OWNER}-dev-{os.getpid()}")
    after = GP.ours_now()
    results.append(
        result(
            "CONTAINERS",
            "containers this runner started and left behind",
            0,
            {"before": containers_before, "after": after},
            after == containers_before,
        )
    )
    (out / "probes.json").write_text(
        json.dumps(
            {
                "seconds": round(time.perf_counter() - t0, 1),
                "records": rec,
                "setup_notes": w.notes,
                "probes": results,
            },
            indent=1,
            default=str,
        )
    )
    for r in results:
        flag = "PASS" if r.get("pass") else ("NOT RUN" if r.get("ran") is False else "FAIL")
        print(f"{r['id']:<10} {flag:<8} {r['probe'][:100]}")
    bad = [r["id"] for r in results if r.get("ran") and not r.get("pass")]
    print("failed:", bad or "none")
    return 1 if bad else 0


# ---- the replay stage (the mutants' killer) ------------------------------------------------------------------------


def run_replay(args: argparse.Namespace) -> int:
    """PC1, PC2 and PC3 only, from an existing dev world (``--scratch`` of a dev run): the mutants'
    killer, run with PYTHONPATH on a mutated copy of the package."""
    w = GP.World(args)
    for name in ("S", "S4"):
        w.inv[name] = w.scratch / "inv" / name
        w.state[name] = w.scratch / f"state-{name}"
    for name in ("S", "S4", "S-root2", "S-stale"):
        w.dirs[name] = w.scratch / name
    dev = Dev(w, args)
    rep = replay(dev)
    rows = [probe_pc1(dev, rep), probe_pc2(dev, rep), probe_pc3(rep)]
    for s in dev.studios.values():
        with contextlib.suppress(Exception):
            s.close()
    print(json.dumps({r["id"]: r["pass"] for r in rows}))
    return 0 if all(r["pass"] for r in rows) else 1


# ---- the question-set stage (the orchestrator's, once) --------------------------------------------------------------


class CountingStub:
    """The stub generator of the router surface, counting its calls."""

    def __init__(self) -> None:
        from lattice.studio.server import StubGenerator  # noqa: PLC0415

        self.inner = StubGenerator()
        self.calls = 0

    def generate(self, system: str, messages: Sequence[Any]) -> Any:
        self.calls += 1
        return self.inner.generate(system, messages)


def run_qset(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    verify_freeze(out.parent)
    qdir = Path(args.questions).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "qset-scored.json").exists() and not args.rerun:
        print(
            "the question set was scored here already; a second run is a rerun (--rerun)",
            file=sys.stderr,
        )
        return 2
    qs = json.loads((qdir / "qset.json").read_text())
    scorer = qdir / "chat2_score.py"
    w = GP.World(args)
    os.environ["PATH"] = f"{w.shim}{os.pathsep}{os.environ['PATH']}"
    for d in ("state-S", "state-S4", "reports", "chat"):
        shutil.rmtree(w.scratch / d, ignore_errors=True)
    wf = Path(args.workflows).resolve()
    GP.G.archive(GP.STREAK, args.streak_ref, w.dirs["S"])
    w.notes.append(IP.rewrite_go_src(w.dirs["S"]))
    GP.capture_inventory(w, "S", w.dirs["S"], wf)
    w.state["S"] = w.scratch / "state-S"
    GP.set_tables(w.dirs["S"], w.inv["S"], w.dirs["S"], w.state["S"])
    GP.G.archive(GP.STREAK, args.streak_ref2, w.dirs["S4"])
    w.notes.append(IP.rewrite_go_src(w.dirs["S4"]))
    GP.capture_inventory(w, "S4", w.dirs["S4"], None)
    w.state["S4"] = w.scratch / "state-S4"
    GP.set_tables(w.dirs["S4"], w.inv["S4"], w.dirs["S4"], w.state["S4"])
    reports = w.scratch / "reports"
    reports.mkdir(parents=True)
    token = f"{OWNER}-q-{os.getpid()}"
    summary: dict[str, Any] = {"changes": {}, "notes": w.notes}
    # the six candidates, recorded in order c1..c6 through the scripted generator, byte-equal checked
    st = GP.Studio(w, w.dirs["S"], owner=token)
    try:
        for cname in sorted(qs["changes"]):
            info = qs["changes"][cname]
            cand = (qdir / info["candidate"]).resolve()
            c = st.draft(cand, f"candidate {cname}")
            assert c["id"] == cname, (c["id"], cname)
            priv = json.loads((w.state["S"] / "changes" / f"{cname}.json").read_text())["private"]
            got = {"REQUIREMENTS.md": sha(priv["new_md"].encode()), "spec.py": sha(priv["new_module"].encode()),
                   "spec.trace.json": sha(priv["new_trace"].encode())}  # fmt: skip
            want = info["files_sha256"]
            match = all(got.get(k) == v for k, v in want.items())
            summary["changes"][cname] = {
                "status": c["status"],
                "byte_equal": match,
                "got": got,
                "want": want,
            }
            if not match:
                print(f"STOP: {cname} was not recorded byte for byte", file=sys.stderr)
                (out / "qset-stop.json").write_text(json.dumps(summary, indent=1))
                return 3
            r = GP.engine_cli(w, w.dirs["S"], cname, w.state["S"], out=reports / f"{cname}.json")
            assert r.rc == 0, r.err[-300:]
    finally:
        st.close()
        GP.remove_owner(token)

    # one server per corpus, [chat] on, a counting stub; the base server for the C controls
    def with_chat(name: str) -> Path:
        d = w.scratch / "chat" / name
        shutil.copytree(w.dirs[name], d, symlinks=True, ignore=shutil.ignore_patterns("bin"))
        GP.set_tables(d, w.inv[name], d, w.state[name])
        (d / "studio.toml").write_text(
            (d / "studio.toml").read_text() + "\n[chat]\nroute = true\ncheck = true\n"
        )
        return d

    from lattice.studio.server import make_server  # noqa: PLC0415

    stub_s, stub_s4 = CountingStub(), CountingStub()
    root_s, root_s4 = with_chat("S"), with_chat("S4")
    srv_s, srv_s4 = (
        make_server(root_s, 0, generator=stub_s),
        make_server(root_s4, 0, generator=stub_s4),
    )
    for srv in (srv_s, srv_s4):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    base_url = {
        "S": f"http://127.0.0.1:{srv_s.server_address[1]}",
        "S4": f"http://127.0.0.1:{srv_s4.server_address[1]}",
    }
    base_tree = GP.make_base(w, args.base_ref)
    base_proc = GP.Proc(w, base_tree, GP.SERVE_SCRIPT, [str(w.dirs["S"])])
    maps: dict[str, Any] = {}
    router: dict[str, Any] = {}
    try:
        for corpus in ("S", "S4"):
            status, doc = GP.http("GET", base_url[corpus] + "/api/appmap")
            assert status == 200, doc
            maps[corpus] = doc
        for q in qs["questions"]:
            corpus = q.get("corpus", "S")
            if q["set"] not in ("I_impact", "H_hypothetical", "C_control"):
                continue
            stub = stub_s if corpus == "S" else stub_s4
            n0 = stub.calls
            status, resp = GP.http("POST", base_url[corpus] + "/api/ask", {"question": q["text"]})
            entry: dict[str, Any] = {"response": resp, "generator_calls": stub.calls - n0}
            if q["set"] == "C_control":
                entry["base"] = GP.http(
                    "POST", base_proc.base + "/api/ask", {"question": q["text"]}
                )[1]
            router[q["id"]] = entry
    finally:
        base_proc.close()
        for srv in (srv_s, srv_s4):
            srv.shutdown()
            srv.server_close()
    (out / "maps.json").write_text(json.dumps({"maps": maps, "answers": {}}))
    (out / "router.json").write_text(json.dumps({"answers": router}, indent=1))
    cmd = [
        sys.executable,
        str(scorer),
        "--qset",
        str(qdir / "qset.json"),
        "--map",
        str(out / "maps.json"),
        "--router",
        str(out / "router.json"),
        "--reports",
        str(reports),
        "--json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    (out / "score-qset.json").write_text(r.stdout)
    scored: dict[str, Any] = {"rc": r.returncode, "stderr": r.stderr[-500:]}
    with contextlib.suppress(ValueError):
        scored["result"] = json.loads(r.stdout)
    shim = w.shim_log.read_text() if w.shim_log.exists() else ""
    summary["scored"] = {
        k: (scored["result"].get(k) if isinstance(scored.get("result"), dict) else None)
        for k in ("G1", "G2")
    }
    summary["scorer_rc"] = r.returncode
    summary["shim_calls"] = len([ln for ln in shim.splitlines() if ln])
    (out / "qset-scored.json").write_text(json.dumps(summary, indent=1, default=str))
    # for chat2_agent.py: the servers' projects are re-built there; the world is just where things are
    (out / "world.json").write_text(
        json.dumps(
            {
                "scratch": str(w.scratch),
                "reports": str(reports),
                "questions": str(qdir),
                "python": sys.executable,
                "dirs": {k: str(v) for k, v in w.dirs.items()},
                "inv": {k: str(v) for k, v in w.inv.items()},
                "state": {k: str(v) for k, v in w.state.items()},
            },
            indent=1,
        )
    )
    for g in ("G1", "G2"):
        v = (scored.get("result") or {}).get(g) if isinstance(scored.get("result"), dict) else None
        print(g, json.dumps({k: (v or {}).get(k) for k in ("rule", "run", "pass", "n", "correct")}))
    print("scorer rc", r.returncode)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=("dev", "replay", "freeze", "qset"), required=True)
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument(
        "--workflows",
        default=str(REPO / "docs/results/data/2026-10-15-workflow-layer/workflows.json"),
    )
    ap.add_argument(
        "--round1", default=str(REPO / "docs/results/data/2026-10-18-goto-chat-round2/frozen-r1")
    )
    ap.add_argument(
        "--round1-answers", default=str(REPO / "docs/results/data/2026-10-16-goto-place")
    )
    ap.add_argument("--scratch")
    ap.add_argument("--out", required=True)
    ap.add_argument("--questions")
    ap.add_argument("--base-ref", default=BASE_REF)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only")
    ap.add_argument("--pytest-log")
    ap.add_argument("--baseline-log")
    ap.add_argument("--rerun", action="store_true")
    args = ap.parse_args(argv)
    if args.stage == "freeze":
        rows = write_freeze(Path(args.out))
        print(f"{len(rows)} files recorded")
        return 0
    if not args.scratch:
        ap.error("--scratch is required")
    if args.stage == "dev":
        return run_dev(args)
    if args.stage == "replay":
        return run_replay(args)
    if not args.questions:
        ap.error("--questions is required")
    return run_qset(args)


if __name__ == "__main__":
    sys.exit(main())
