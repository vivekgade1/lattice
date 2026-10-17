"""Tier 2 of the change-impact report (docs/results/2026-10-16-change-impact-prereg.md, D4):
what *follows* from the spec change plus what was observed to pass on today's app, without
running the candidate.

A pure function of ``(base spec, candidate spec, the baseline's passed steps, the verified
flows)``. It never sees a candidate run and never calls a model, the app or Docker (except the
optional spec-runtime replay, D4c, which runs the candidate's *Lattice programs* on a throwaway
sqlite database, never the app).

**D4a, same request, same prior state, incompatible expectation.** A candidate step and a
*corpus* step (a base scenario step that passed in the baseline, or a step of a verified flow)
*match* when their given rows, every earlier request (method, path, user, json, params) and their
own request are canonically equal; the prefix holds no ``Await``; the spec's ``users`` and the
profile schemas of the given tables are the same in both specs. If the candidate's expectation
for that step cannot hold together with the corpus step's (no response can satisfy both: the
statuses differ; at one body path the scalars, list lengths or types differ; for one
``expect_db`` table and ``where`` the counts or an ``equals`` value differ, or ``count: 0``
meets ``equals``), the scenario fails at that step or an earlier one on today's app.
**D4b** is the same for a verified flow: it records today's observed answer, so a candidate
step it matches with another status makes the flow fail after the change, for any
implementation that passes the candidate. **D4c** replays corpus prefixes on the candidate's
Lattice runtime (only for endpoints with programs). Each changed rule or target also gets a
*decidability* entry: ``determined``, or one reason it is not.
"""

from __future__ import annotations

import ast
import json
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.spec.check import _same, subset
from lattice.spec.incremental import RuleDiff, spec_elements
from lattice.spec.model import Await, Rpc, Scenario, Spec, Step, resolve_table, tables_of
from lattice.spec.rules import Trace

REASONS = (
    "new_request",
    "compatible_expectation",
    "base_prefix_failed",
    "profile_or_users_changed",
    "await_in_prefix",
    "invariant_or_machine",
    "no_linked_check",
    "removed_only",
)
TOL_FACTOR = 2.0
_CASE_STEP = re.compile(r"^(.*) / step (\d+)$")


# ---- spec source lines (AST: a Scenario literal and its steps) ------------------------------


class SpecLines:
    """The lines of ``Scenario("name", ...)`` literals in a spec module's source, and of their
    ``steps`` elements when the steps are written out literally. A scenario built by a helper (an
    f-string name, a loop) does not resolve: callers fall back."""

    def __init__(self, source: str) -> None:
        self.scenario_line: dict[str, int] = {}
        self.step_lines: dict[str, list[int | None]] = {}
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name != "Scenario" or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            if first.value in self.scenario_line:
                continue
            self.scenario_line[first.value] = node.lineno
            steps = next((k.value for k in node.keywords if k.arg == "steps"), None)
            if steps is None and len(node.args) >= 3:
                steps = node.args[2]
            lines: list[int | None] = []
            if isinstance(steps, ast.Tuple | ast.List):
                for el in steps.elts:
                    if isinstance(el, ast.Starred):
                        break
                    lines.append(el.lineno)
            self.step_lines[first.value] = lines

    def scenario(self, name: str) -> int | None:
        return self.scenario_line.get(name)

    def step(self, name: str, k: int) -> int | None:
        lines = self.step_lines.get(name) or []
        if 1 <= k <= len(lines):
            return lines[k - 1]
        return self.scenario_line.get(name)


# ---- canonical forms ------------------------------------------------------------------------


def _cj(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def canon_given(given: Mapping[str, Any] | None) -> str:
    return _cj(dict(given or {}))


def canon_request(
    method: str, path: str, user: str | None, body: Any, params: Mapping[str, Any] | None
) -> str:
    return _cj([method.upper(), path, user, body, dict(params or {}) or None])


def _category(x: Any) -> str:
    if x is None:
        return "none"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, int | float):
        return "num"
    if isinstance(x, str):
        return "str"
    if isinstance(x, Mapping):
        return "map"
    if isinstance(x, list | tuple):
        return "list"
    return type(x).__name__


def differs(a: Any, b: Any, path: str = "$") -> str | None:
    """Where two expected body values cannot both be a subset of one answer (a path), else None.
    Mirrors the checker's ``subset`` and ``_same``: an object's keys recursively (a key only one
    side states is fine), a list element-wise with the same length, a scalar equal; numbers
    differ only by more than :data:`TOL_FACTOR` x the checker's tolerance."""
    ca, cb = _category(a), _category(b)
    if ca != cb:
        return path
    if ca == "map":
        for k in sorted(set(a) & set(b), key=str):
            r = differs(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return None
    if ca == "list":
        if len(a) != len(b):
            return path
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            r = differs(x, y, f"{path}[{i}]")
            if r:
                return r
        return None
    if ca == "num":
        tol = 1e-9 * max(1.0, abs(a), abs(b))
        return path if abs(a - b) > TOL_FACTOR * tol else None
    if ca == "none":
        return None
    return None if _same(a, b) else path


def _db_key(spec: Spec, table: str) -> str:
    found = resolve_table(spec.profile, table)
    return found[0] if found else table


def db_incompatible(
    a: Mapping[str, Any] | None, b: Mapping[str, Any] | None, spec: Spec
) -> str | None:
    """The first ``expect_db`` table whose two expectations cannot both hold, else None."""
    if not a or not b:
        return None
    na = {_db_key(spec, t): e for t, e in a.items()}
    nb = {_db_key(spec, t): e for t, e in b.items()}
    for table in sorted(set(na) & set(nb)):
        ea, eb = na[table] or {}, nb[table] or {}
        if _cj(ea.get("where") or {}) != _cj(eb.get("where") or {}):
            continue
        if "count" in ea and "count" in eb and ea["count"] != eb["count"]:
            return f"db:{table}:count"
        if "equals" in ea and "equals" in eb:
            for col in sorted(set(ea["equals"]) & set(eb["equals"])):
                if differs(ea["equals"][col], eb["equals"][col]):
                    return f"db:{table}:equals.{col}"
        for x, y in ((ea, eb), (eb, ea)):
            if x.get("count") == 0 and y.get("equals"):
                return f"db:{table}:count0_vs_equals"
    return None


def incompatible(cand: Mapping[str, Any], corpus: Mapping[str, Any], spec: Spec) -> str | None:
    """Why the candidate's expectation for a step cannot hold with the corpus step's, or None.
    Both are ``{status, body, db}``."""
    if cand["status"] != corpus["status"]:
        return f"status:{corpus['status']}->{cand['status']}"
    if cand.get("body") is not None and corpus.get("body") is not None:
        where = differs(cand["body"], corpus["body"])
        if where:
            return f"body:{where}"
    return db_incompatible(cand.get("db"), corpus.get("db"), spec)


def _expect_of_step(step: Step) -> dict[str, Any]:
    return {
        "status": step.expect_status,
        "body": step.expect_body,
        "db": dict(step.expect_db) if step.expect_db else None,
    }


def describe_expect(e: Mapping[str, Any]) -> str:
    parts = [f"status {e['status']}"]
    if e.get("body") is not None:
        parts.append("body " + _cj(e["body"])[:120])
    if e.get("db"):
        parts.append("db " + _cj(e["db"])[:120])
    return ", ".join(parts)


# ---- the corpus -----------------------------------------------------------------------------


def passed_steps(cases: Mapping[str, Any]) -> set[tuple[str, int]]:
    """``(scenario, step)`` of every layer D case a check report says was correct."""
    out: set[tuple[str, int]] = set()
    for label, rec in cases.items():
        m = _CASE_STEP.match(label)
        if m and (rec or {}).get("layer") == "D" and (rec or {}).get("verdict") == "correct":
            out.add((m.group(1), int(m.group(2))))
    return out


def _same_environment(base: Spec, cand: Spec) -> str | None:
    """Why the corpus cannot stand for the candidate's world, else None."""
    if _cj({k: list(v) for k, v in base.users.items()}) != _cj(
        {k: list(v) for k, v in cand.users.items()}
    ):
        return "users"
    return None


def _given_profile_same(base: Spec, cand: Spec, given: Mapping[str, Any] | None) -> bool:
    if not given:
        return True
    a, b = spec_elements(base), spec_elements(cand)
    bt, bs = tables_of(base.profile)
    for table in given:
        found = resolve_table(base.profile, table) or resolve_table(cand.profile, table)
        name = found[0] if found else table
        if a.get(f"profile:{name}") != b.get(f"profile:{name}"):
            return False
    del bt, bs
    return True


class Corpus:
    """Every observed-to-pass request, keyed by (given, earlier requests, request)."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, tuple[str, ...], str], list[dict[str, Any]]] = {}
        self.all_keys: set[tuple[str, tuple[str, ...], str]] = set()

    def add(self, key: tuple[str, tuple[str, ...], str], entry: dict[str, Any]) -> None:
        self.entries.setdefault(key, []).append(entry)

    @classmethod
    def build(
        cls,
        base: Spec,
        passed: set[tuple[str, int]] | None,
        provenance: str | None,
        flows: Sequence[Mapping[str, Any]],
        lines: SpecLines,
        flow_passed: Callable[[str], bool] | None = None,
    ) -> Corpus:
        c = cls()
        for sc in base.scenarios:
            given = canon_given(sc.given)
            prefix: list[str] = []
            has_await = False
            for k, step in enumerate(sc.steps, 1):
                if isinstance(step, Await | Rpc):  # Spike I: an rpc, like an Await, ends the prefix
                    has_await = True
                    continue
                req = canon_request(step.method, step.path, step.user, step.json, step.params)
                key = (given, tuple(prefix), req)
                if not has_await:
                    c.all_keys.add(key)
                    if passed is not None and (sc.name, k) in passed:
                        c.add(
                            key,
                            {
                                "source": f"scenario:{sc.name}",
                                "kind": "scenario",
                                "step": k,
                                "expect": _expect_of_step(step),
                                "provenance": provenance,
                                "line": lines.step(sc.name, k),
                            },
                        )
                prefix.append(req)
        for f in flows:
            if f.get("status") != "verified":
                continue
            if flow_passed is not None and not flow_passed(str(f["id"])):
                continue
            prefix = []
            has_await = False
            for s in f["steps"]:
                if s["kind"] == "await":
                    has_await = True
                    continue
                req = canon_request(s["method"], s["path"], s["caller"], s["json"], None)
                key = (canon_given(None), tuple(prefix), req)
                if not has_await:
                    c.all_keys.add(key)
                    c.add(
                        key,
                        {
                            "source": f"flow:{f['id']}",
                            "kind": "flow",
                            "step": int(s["i"]) if s.get("i") is not None else len(prefix) + 1,
                            "expect": {"status": s["expect_status"], "body": None, "db": None},
                            "provenance": "flow",
                            "line": None,
                        },
                    )
                prefix.append(req)
        for entries in c.entries.values():
            entries.sort(key=lambda e: (e["source"], e["step"]))
        return c


# ---- the analysis ---------------------------------------------------------------------------


def _rules_for(trace: Trace | None, target: str) -> list[str]:
    if trace is None:
        return []
    out = [r for r, ts in trace.links.items() if target in ts]
    return sorted(out, key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r))


def analyze_scenario(
    cand: Spec,
    base: Spec,
    sc: Scenario,
    corpus: Corpus,
    env_problem: str | None,
    have_baseline: bool,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """(claim or None, the scenario's decidability reason, detail). The claim is the first step
    whose expectation is incompatible with a matching corpus step."""
    given = canon_given(sc.given)
    prefix: list[str] = []
    has_await = False
    reason: str | None = None
    detail: dict[str, Any] = {}
    first_nonmatch: tuple[str, int] | None = None
    for k, step in enumerate(sc.steps, 1):
        if isinstance(step, Await | Rpc):  # Spike I: an rpc, like an Await, ends the prefix
            has_await = True
            continue
        req = canon_request(step.method, step.path, step.user, step.json, step.params)
        key = (given, tuple(prefix), req)
        prefix.append(req)
        if has_await:
            cls_ = "await_in_prefix"
        elif env_problem or not _given_profile_same(base, cand, sc.given):
            cls_ = "profile_or_users_changed"
        elif key in corpus.entries:
            exp = _expect_of_step(step)
            hit = None
            for e in corpus.entries[key]:
                why = incompatible(exp, e["expect"], cand)
                if why:
                    hit = (e, why)
                    break
            if hit is not None:
                e, why = hit
                return (
                    {"step": k, "against": e, "why": why, "expect": exp},
                    "determined",
                    {},
                )
            cls_ = "compatible_expectation"
        elif key in corpus.all_keys:
            cls_ = "base_prefix_failed"
            if not have_baseline:
                detail = {"detail": "no baseline run: only verified flows are observed"}
        else:
            cls_ = "new_request"
        if cls_ != "compatible_expectation" and reason is None:
            reason = cls_
            first_nonmatch = (cls_, k)
    del first_nonmatch
    return None, reason or "compatible_expectation", detail


def _target_scenario_names(targets: Mapping[str, list[str]]) -> list[str]:
    return sorted(
        t.partition(":")[2]
        for t in [*targets.get("added", []), *targets.get("modified", [])]
        if t.startswith("scenario:")
    )


def compute(
    base: Spec,
    cand: Spec,
    base_trace: Trace | None,
    cand_trace: Trace | None,
    diff: RuleDiff | None,
    targets: Mapping[str, list[str]],
    *,
    passed: set[tuple[str, int]] | None,
    provenance: str | None,
    flows: Sequence[Mapping[str, Any]],
    flow_note: str | None,
    base_lines: SpecLines,
    cand_lines: SpecLines,
    conflicts_fn: Callable[[Spec], list[Any]] | None = None,
    flow_passed: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """The Tier 2 section (see the module docstring): ``determined``, ``flows``,
    ``decidability``, plus the corpus size and what it was built from."""
    have_baseline = passed is not None
    corpus = Corpus.build(base, passed, provenance, flows, base_lines, flow_passed)
    env_problem = _same_environment(base, cand)
    determined: list[dict[str, Any]] = []
    flow_items: list[dict[str, Any]] = []
    scenario_reason: dict[str, tuple[str, dict[str, Any], dict[str, Any] | None]] = {}
    cand_scenarios = {s.name: s for s in cand.scenarios}
    for name in _target_scenario_names(targets):
        sc = cand_scenarios.get(name)
        if sc is None:
            continue
        claim, reason, detail = analyze_scenario(cand, base, sc, corpus, env_problem, have_baseline)
        target = f"scenario:{name}"
        rules = _rules_for(cand_trace, target)
        if claim is not None:
            e = claim["against"]
            status = "determined" if e["provenance"] in ("run", "flow") else "conditional"
            ev = [
                {
                    "kind": "spec",
                    "at": _spec_at("spec.py", cand_lines.step(name, claim["step"])),
                    "what": f"candidate step {claim['step']} expects "
                    f"{describe_expect(claim['expect'])}",
                }
            ]
            if e["kind"] == "scenario":
                ev.append(
                    {
                        "kind": "spec",
                        "at": _spec_at("base/spec.py", e["line"]),
                        "what": f"{e['source']} step {e['step']} passed on the app and expects "
                        f"{describe_expect(e['expect'])}",
                    }
                )
            else:
                ev.append(
                    {
                        "kind": "extract",
                        "at": f"{e['source']}:step {e['step']}",
                        "what": "a verified flow's observed answer: "
                        f"{describe_expect(e['expect'])}",
                    }
                )
            line = cand_lines.step(name, claim["step"])
            item: dict[str, Any] = {
                "kind": "scenario_fails",
                "target": target,
                "rules": rules,
                "status": status,
                "claim": f"{target} fails at step <= {claim['step']}",
                "step": claim["step"],
                "against": {
                    "source": e["source"],
                    "step": e["step"],
                    "provenance": e["provenance"],
                    "expect": e["expect"],
                },
                "candidate_expect": claim["expect"],
                "why": claim["why"],
                "evidence": ev,
                "file": "spec.py" if line else None,
                "line": line,
            }
            if line is None:
                item["why_no_line"] = "the scenario is not a literal Scenario(...) call in spec.py"
            determined.append(item)
            scenario_reason[target] = ("determined", {}, item)
        else:
            scenario_reason[target] = (reason, detail, None)
    # D4b: verified flows whose observed answer contradicts a candidate step they match
    if not env_problem:
        by_key: dict[tuple[str, tuple[str, ...], str], list[tuple[Scenario, int, Step]]] = {}
        for sc in cand.scenarios:
            if sc.given:
                continue
            prefix: list[str] = []
            has_await = False
            for k, step in enumerate(sc.steps, 1):
                if isinstance(step, Await | Rpc):  # Spike I: an rpc, like an Await, ends the prefix
                    has_await = True
                    continue
                req = canon_request(step.method, step.path, step.user, step.json, step.params)
                if not has_await:
                    by_key.setdefault((canon_given(None), tuple(prefix), req), []).append(
                        (sc, k, step)
                    )
                prefix.append(req)
        for f in flows:
            if f.get("status") != "verified":
                continue
            if flow_passed is not None and not flow_passed(str(f["id"])):
                continue
            prefix = []
            has_await = False
            done = False
            for s in f["steps"]:
                if s["kind"] == "await":
                    has_await = True
                    continue
                req = canon_request(s["method"], s["path"], s["caller"], s["json"], None)
                if not has_await and not done:
                    for sc, k, step in by_key.get((canon_given(None), tuple(prefix), req), []):
                        if step.expect_status != s["expect_status"]:
                            line = cand_lines.step(sc.name, k)
                            flow_items.append(
                                {
                                    "kind": "flow_fails_after_change",
                                    "target": f"flow:{f['id']}",
                                    "rules": _rules_for(cand_trace, f"scenario:{sc.name}"),
                                    "status": "determined",
                                    "claim": f"flow:{f['id']} fails at step {s['i']} after the "
                                    "change",
                                    "step": s["i"],
                                    "observed_status": s["expect_status"],
                                    "candidate_status": step.expect_status,
                                    "scenario": f"scenario:{sc.name}",
                                    "evidence": [
                                        {
                                            "kind": "spec",
                                            "at": _spec_at("spec.py", line),
                                            "what": f"candidate step {k} of {sc.name} expects "
                                            f"status {step.expect_status} for the same request "
                                            "after the same prefix",
                                        },
                                        {
                                            "kind": "extract",
                                            "at": f"flow:{f['id']}:step {s['i']}",
                                            "what": f"the flow's observed answer is status "
                                            f"{s['expect_status']}",
                                        },
                                    ],
                                    "file": "spec.py" if line else None,
                                    "line": line,
                                }
                            )
                            done = True
                            break
                prefix.append(req)
    # L011 pairs inside the candidate that the base did not have
    if conflicts_fn is not None:
        base_pairs = {(c.scenario_a, c.step_a, c.scenario_b, c.step_b) for c in conflicts_fn(base)}
        for c in conflicts_fn(cand):
            if (c.scenario_a, c.step_a, c.scenario_b, c.step_b) in base_pairs:
                continue
            line = cand_lines.step(c.scenario_a, c.step_a)
            determined.append(
                {
                    "kind": "spec_contradiction",
                    "target": f"scenario:{c.scenario_a}",
                    "rules": sorted(
                        {
                            *_rules_for(cand_trace, f"scenario:{c.scenario_a}"),
                            *_rules_for(cand_trace, f"scenario:{c.scenario_b}"),
                        },
                        key=lambda r: (int(r[1:]) if r[1:].isdigit() else 0, r),
                    ),
                    "status": "determined",
                    "claim": f"scenario:{c.scenario_a} step {c.step_a} and scenario:"
                    f"{c.scenario_b} step {c.step_b} expect statuses {c.status_a} and "
                    f"{c.status_b} for {c.endpoint}: one of them fails on any app (L011)",
                    "step": c.step_a,
                    "pair": [f"scenario:{c.scenario_a}", f"scenario:{c.scenario_b}"],
                    "evidence": [
                        {
                            "kind": "spec",
                            "at": _spec_at("spec.py", line),
                            "what": f"{c.scenario_a} step {c.step_a} expects {c.status_a}",
                        },
                        {
                            "kind": "spec",
                            "at": _spec_at("spec.py", cand_lines.step(c.scenario_b, c.step_b)),
                            "what": f"{c.scenario_b} step {c.step_b} expects {c.status_b}",
                        },
                    ],
                    "file": "spec.py" if line else None,
                    "line": line,
                }
            )
    return {
        "determined": determined,
        "flows": flow_items,
        "decidability": decidability(
            diff, targets, base_trace, cand_trace, scenario_reason, determined
        ),
        "corpus": {
            "scenario_steps": sum(
                1 for es in corpus.entries.values() for e in es if e["kind"] == "scenario"
            ),
            "flow_steps": sum(
                1 for es in corpus.entries.values() for e in es if e["kind"] == "flow"
            ),
            "baseline": provenance or "none",
            "flows_note": flow_note,
            "environment": env_problem,
        },
    }


def _spec_at(file: str, line: int | None) -> str:
    return f"spec:{file}:{line}" if line else f"spec:{file}"


# ---- decidability ---------------------------------------------------------------------------


def _rid_key(r: str) -> tuple[int, str]:
    return (int(r[1:]) if r[1:].isdigit() else 0, r)


def decidability(
    diff: RuleDiff | None,
    targets: Mapping[str, list[str]],
    base_trace: Trace | None,
    cand_trace: Trace | None,
    scenario_reason: Mapping[str, tuple[str, dict[str, Any], dict[str, Any] | None]],
    determined: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """One entry per added, changed or removed rule and per changed target: ``determined`` (with
    the claims), or one reason from :data:`REASONS`."""
    out: list[dict[str, Any]] = []
    changed = {t: kind for kind in ("added", "modified", "removed") for t in targets.get(kind, [])}
    claims_by_target: dict[str, list[str]] = {}
    for d in determined:
        if d["kind"] == "scenario_fails":
            claims_by_target.setdefault(d["target"], []).append(d["claim"])

    def target_entry(t: str) -> tuple[str, str | None]:
        """(state, reason) of one changed target."""
        kind, _, _ = t.partition(":")
        how = changed.get(t, "modified")
        if how == "removed":
            return "undecidable", "removed_only"
        if kind == "scenario":
            reason, _detail, item = scenario_reason.get(t, ("compatible_expectation", {}, None))
            return ("determined", None) if item is not None else ("undecidable", reason)
        if kind == "profile":
            return "undecidable", "profile_or_users_changed"
        return "undecidable", "invariant_or_machine"

    for t in sorted(changed):
        state, reason = target_entry(t)
        entry: dict[str, Any] = {"subject": t, "state": state, "kind": "target"}
        if reason:
            entry["reason"] = reason
        if state == "determined":
            entry["claims"] = claims_by_target.get(t, [])
        out.append(entry)
    if diff is not None:

        def rule_entry(rid: str, trace: Trace | None, how: str) -> dict[str, Any]:
            e: dict[str, Any] = {"subject": f"rule:{rid}", "state": "undecidable", "kind": how}
            links = sorted((trace.links.get(rid) or []) if trace else [])
            if how == "removed":
                e["reason"] = "removed_only"
                return e
            if not links:
                e["reason"] = "no_linked_check"
                return e
            hit = [t for t in links if t in changed]
            determined_t = [t for t in hit if target_entry(t)[0] == "determined"]
            if determined_t:
                e["state"] = "determined"
                e["claims"] = sorted(c for t in determined_t for c in claims_by_target.get(t, []))
                return e
            if not hit:
                e["reason"] = "compatible_expectation"
                return e
            e["reason"] = target_entry(hit[0])[1]
            return e

        for rid in sorted(diff.added, key=_rid_key):
            out.append(rule_entry(rid, cand_trace, "added"))
        for _o, n in sorted(diff.changed, key=lambda p: _rid_key(p[1])):
            out.append(rule_entry(n, cand_trace, "changed"))
        for rid in sorted(diff.removed, key=_rid_key):
            out.append(rule_entry(rid, base_trace, "removed"))
    return out


# ---- D4c: the candidate's Lattice runtime ---------------------------------------------------


def runtime_replay(
    base: Spec,
    cand: Spec,
    passed: set[tuple[str, int]] | None,
    env: Mapping[str, Any] | None,
    cand_lines: SpecLines,
) -> dict[str, Any]:
    """Replay the baseline-passed scenario prefixes on the candidate's Lattice runtime for the
    endpoints whose program changed. Returns ``{status, items}``; ``status`` is ``run``,
    ``not_applicable: 0 programs`` or ``not_run: <why>``."""
    from lattice.spec.check import _program_names, spec_plans

    if not cand.programs:
        return {"status": "not_applicable: 0 programs", "items": []}
    if passed is None:
        return {"status": "not_run: no baseline run", "items": []}
    if env is None:
        return {"status": "not_run: no harness", "items": []}
    plans = spec_plans(cand)
    names = _program_names(cand)
    changed = {
        f"{p.method} {p.path}"
        for p in plans
        if cand.programs.get(names.get((p.method, p.path), ""))
        != base.programs.get(names.get((p.method, p.path), ""))
    }
    if not changed:
        return {"status": "run", "items": [], "note": "no program changed"}
    from fastapi.testclient import TestClient

    from lattice.api.runtime import build_app
    from lattice.spec.check import _insert_given, _template_of, _tokens

    make_db = env["make_db"]
    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lattice-impact-d4c-") as name:
        tmp = Path(name)
        tokens, _notes = _tokens(
            tuple(cand.users),
            env["app_factory"],
            make_db,
            env.get("login"),
            tmp,
            env.get("runtime"),
        )
        for n, sc in enumerate(base.scenarios):
            tpls = [
                _template_of(s.path, plans, s.method) if isinstance(s, Step) else None
                for s in sc.steps
            ]
            if not any(t in changed for t in tpls if t):
                continue
            db = make_db("seed", tmp / f"s{n}.db")
            try:
                if sc.given:
                    _insert_given(cand, db, sc.given)
            except Exception:  # noqa: BLE001 -- a given the runtime cannot take: skip
                continue
            app = build_app(plans, db, natives=env.get("natives"), settings=env.get("settings"))
            with TestClient(app, raise_server_exceptions=False) as client:
                for k, step in enumerate(sc.steps, 1):
                    if isinstance(step, Await) or tpls[k - 1] is None:
                        break
                    if (sc.name, k) not in passed:
                        break
                    headers = {}
                    if step.user is not None:
                        if step.user not in tokens:
                            break
                        headers["Authorization"] = f"Bearer {tokens[step.user]}"
                    r = client.request(
                        step.method,
                        step.path,
                        params=dict(step.params) if step.params else None,
                        json=step.json,
                        headers=headers,
                    )
                    try:
                        body = r.json()
                    except ValueError:
                        body = None
                    bad = r.status_code != step.expect_status or (
                        step.expect_body is not None and not subset(step.expect_body, body)
                    )
                    if bad:
                        if tpls[k - 1] in changed:
                            items.append(
                                {
                                    "kind": "runtime_disagreement",
                                    "check": None,
                                    "target": f"scenario:{sc.name}",
                                    "step": k,
                                    "status": "determined",
                                    "claim": f"the candidate's program for "
                                    f"{tpls[k - 1]} answers {r.status_code}, not the "
                                    f"{step.expect_status} the app gave at scenario:{sc.name} "
                                    f"step {k}",
                                    "runtime_status": r.status_code,
                                    "app_expect_status": step.expect_status,
                                    "rules": [],
                                    "evidence": [
                                        {
                                            "kind": "spec",
                                            "at": _spec_at(
                                                "base/spec.py", cand_lines.step(sc.name, k)
                                            ),
                                            "what": "a step the app passed",
                                        }
                                    ],
                                    "file": None,
                                    "line": None,
                                    "why_no_line": "a program disagreement, not a step of the "
                                    "candidate",
                                }
                            )
                        break
    return {"status": "run", "items": items}


def merge_rules(items: Iterable[dict[str, Any]], trace: Trace | None) -> None:
    """Fill each item's ``rules`` from the candidate trace by its target (in place)."""
    for it in items:
        if not it.get("rules") and it.get("target"):
            it["rules"] = _rules_for(trace, str(it["target"]))
