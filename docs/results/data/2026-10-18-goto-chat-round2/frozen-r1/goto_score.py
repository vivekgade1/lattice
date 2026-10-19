#!/usr/bin/env python3
"""Spike F (go-to place) dogfood scorer for the frozen question set (prereg section 5).

Deterministic, standard library only. It never calls a model, a server or a subprocess: the
runner fetches the answers and hands them in as JSON; this file only compares them with the
known answers frozen in qset.json.

Surfaces and rules (prereg section 5, "Scorer (hash-frozen, deterministic)"):
  map     M and N (22): the map API's field values equal the known values exactly.
  router  I, H and C (22): I = routed, the stated tier 1 and tier 2 item ids equal the report's
          (set equality), tier 3 never called a failure, 0 model calls; H = kind "not_specified";
          C = not routed (and byte-identical to the base answer when the runner gives one).
  agent   all 44: M/N/C content checks, I = tier 1/2 id set equality and tier 3 never called a
          failure (plus verbatim-id compliance), H = the "not decided" meaning plus an offer to
          draft, with 0 failure claims.
Unsupported failure claim (every surface): a sentence matching FAIL_RE that cites no tier 1 or
tier 2 item id of the relevant report (the change's report for I; none otherwise) and is not
hedged within the sentence by HEDGE_RE. Counted per surface, per set and per question. This
literal count is the gate.
Lenient count (Amendment 2, report-only, never gated): the same claims minus (a) negations, a
sentence whose every failure word has a negation (NEG_WORD_RE) among the 6 words before it in
its clause ("nothing fails", "no workflow will fail", "does not break"), and (b) sentences
naming a recorded replay with its status: a flow id wf-<10 hex> or "the replay of", plus a
status (REPLAY_STATUS_RE: verified, unverified, failed, passed, did not pass, "status NNN",
"answered NNN").

Usage:
  goto_score.py --qset qset.json --surface map    --answers maps.json   [--json]
  goto_score.py --qset qset.json --surface router --answers router.json [--reports DIR] [--json]
  goto_score.py --qset qset.json --surface agent  --answers agent.json  [--reports DIR] [--json]
  goto_score.py --qset qset.json --self-test goto_score_selftest.json
Answers files:
  map:    {"maps": {"S": <lattice.appmap/1>, "S4": <lattice.appmap/1>}}
          and/or {"answers": {"<qid>": <GET /api/appmap[?section=...] body>}} (per question wins)
  router: {"answers": {"<qid>": {"response": <POST /api/ask body>,
                                  "generator_calls": <int, optional>,
                                  "base": <the base answer for the same question, optional>}}}
  agent:  {"answers": {"<qid>": {"text": "<final answer text>",
                                  "status": "ok" | "not_run" | "usage_limit" | "error"}}}
--reports DIR: live D reports named <cN>.json (change_impact default mode). Each is checked
against the frozen expectation: same input.candidate sha256 -> its tier 1/2/3 ids must equal the
frozen ones (else "report_drift", a scorer error, exit 3); a different candidate sha256 ->
"candidate_mismatch" and that change's I questions are not scored (counted unscored).
Exit: 0 scored (gates are reported, not enforced by the exit code), 2 bad input, 3 report drift,
1 self-test failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FAIL_RE = re.compile(r"\b(fail|fails|failing|failed|break|breaks|broke|broken)\b", re.IGNORECASE)
# The prereg hedge list "may|might|could|not decided|undecid|static|not run|no change", within the
# sentence: whole words, except `undecid` (a stem: undecided, undecidable) and the obvious
# inflections of `static` (statically) and `no change` (no changes).
HEDGE_RE = re.compile(
    r"\b(?:may|might|could|not decided|undecid\w*|static\w*|not run|no change\w*)\b", re.IGNORECASE
)
TID_RE = re.compile(r"\bt([1-4])-[0-9a-f]{10}\b")
TIER3_WORD_RE = re.compile(r"\btier\s*3\b", re.IGNORECASE)
ROUTE_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[A-Za-z0-9_/{}.-]*[A-Za-z0-9_}]")
H_FIXED = "Lattice does not predict failures."

NOT_DECIDED = [
    "not decided",
    "undecided",
    "undecidable",
    "not decidable",
    "does not predict",
    "doesn't predict",
    "do not predict",
    "don't predict",
    "cannot predict",
    "can't predict",
    "does not decide",
    "doesn't decide",
    "cannot decide",
    "can't decide",
    "only for a drafted change",
    "needs a drafted change",
    "need a drafted change",
    "requires a drafted change",
    "no drafted change",
    "without a drafted change",
    "not determined",
    "cannot be determined",
    "can't be determined",
    "cannot say",
    "can't say",
    "cannot tell",
    "can't tell",
]
DRAFT_RE = re.compile(r"\bdraft\w*\b", re.IGNORECASE)
# Amendment 2 (report-only lenient count)
NEG_WORD_RE = re.compile(
    r"^(?:nothing|none|no|not|never|neither|nor|without|cannot|\w+n't|nobody|nowhere)$",
    re.IGNORECASE,
)
REPLAY_NAME_RE = re.compile(r"\bwf-[0-9a-f]{10}\b|\bthe replay of\b", re.IGNORECASE)
REPLAY_STATUS_RE = re.compile(
    r"\b(?:verified|unverified|failed|passed|did not pass)\b|\b(?:status|answered)\s+\d{3}\b",
    re.IGNORECASE,
)

MAP_SETS = {"M_rules", "M_workflows", "M_machines", "M_access", "N_not_built"}
ROUTER_SETS = {"I_impact", "H_hypothetical", "C_control"}


# ---------------------------------------------------------------- text helpers


def sentences(text: str) -> list[str]:
    """Lines, then sentence ends (., ! or ? followed by white space)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line.strip()):
            if s.strip():
                out.append(s.strip())
    return out


def failure_claims(text: str, relevant_ids: set[str]) -> list[str]:
    """The unsupported failure claims of ``text`` (prereg section 5)."""
    bad = []
    for s in sentences(text):
        if not FAIL_RE.search(s):
            continue
        if any(i in s for i in relevant_ids):
            continue
        if HEDGE_RE.search(s):
            continue
        bad.append(s)
    return bad


def _negated(sentence: str) -> bool:
    """Every failure word of the sentence has a negation among the 6 words before it, within
    its clause (clauses split at , ; : and the words but, while, whereas)."""
    seen = False
    for clause in re.split(r"[,;:]|\b(?:but|while|whereas)\b", sentence, flags=re.IGNORECASE):
        words = re.findall(r"[A-Za-z']+", clause)
        for k, w in enumerate(words):
            if FAIL_RE.fullmatch(w):
                seen = True
                if not any(NEG_WORD_RE.match(x) for x in words[max(0, k - 6) : k]):
                    return False
    return seen


def _names_replay(sentence: str) -> bool:
    return bool(REPLAY_NAME_RE.search(sentence) and REPLAY_STATUS_RE.search(sentence))


def lenient_claims(claims: list[str]) -> list[str]:
    """Amendment 2, report-only: the literal claims minus negations and named replays."""
    return [c for c in claims if not _negated(c) and not _names_replay(c)]


def tier3_called_failure(text: str, tier3_ids: set[str], t12_ids: set[str]) -> list[str]:
    """Sentences that call a tier 3 item a failure: a failure word, no hedge, and a tier 3 item
    id (any t3- id), or the words "tier 3" with no tier 1/2 id of the report cited."""
    bad = []
    for s in sentences(text):
        if not FAIL_RE.search(s) or HEDGE_RE.search(s):
            continue
        has_t3 = any(i in s for i in tier3_ids) or any(
            m.group(1) == "3" for m in TID_RE.finditer(s)
        )
        if has_t3 or (TIER3_WORD_RE.search(s) and not any(i in s for i in t12_ids)):
            bad.append(s)
    return bad


def norm_text(text: str) -> str:
    t = (text or "").replace("`", "").replace("**", "")
    t = re.sub(r"\{[^}/\s]*\}", "{}", t)
    return re.sub(r"[ \t]+", " ", t)


def has_token(text: str, tok: str) -> bool:
    pat = r"(?<![A-Za-z0-9_])" + re.escape(norm_text(tok)) + r"(?![A-Za-z0-9_])"
    return re.search(pat, text, re.IGNORECASE) is not None


def stated_tids(obj: Any, tiers: str = "12") -> set[str]:
    """Every tier item id (t1-/t2- by default) anywhere in a JSON value or a string."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            for m in TID_RE.finditer(v):
                if m.group(1) in tiers:
                    found.add(m.group(0))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obj)
    return found


def find_key(obj: Any, key: str) -> list[Any]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out.extend(find_key(v, key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(find_key(v, key))
    return out


def has_string(obj: Any, s: str) -> bool:
    if isinstance(obj, str):
        return obj == s
    if isinstance(obj, dict):
        return any(has_string(v, s) for v in obj.values())
    if isinstance(obj, list):
        return any(has_string(v, s) for v in obj)
    return False


def canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rid_key(s: Any) -> Any:
    return (len(str(s)), str(s))


def as_set(v: Any) -> list[str] | None:
    if not isinstance(v, list):
        return None
    return sorted({canon(x) for x in v})


# ---------------------------------------------------------------- the map surface


def resolve(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _match(item: Any, where: dict, contains: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    if any(item.get(k) != v for k, v in where.items()):
        return False
    for k, v in (contains or {}).items():
        if not isinstance(item.get(k), list) or v not in item[k]:
            return False
    return True


def check_map(q: dict, doc: Any) -> dict:
    spec = q["known"]["map"]
    checks: dict[str, bool] = {}
    got: dict[str, Any] = {}
    if not isinstance(doc, dict):
        return {"correct": False, "checks": {"answered": False}, "got": {}}
    checks["state_ok"] = (
        doc.get("state", "ok") == "ok"
        and str(doc.get("schema", "lattice.appmap/1")) == "lattice.appmap/1"
    )
    arr = resolve(doc, spec["path"])
    checks["section_present"] = isinstance(arr, list)
    if not isinstance(arr, list):
        arr = []
    if spec["op"] in ("select", "count"):
        hits = [x for x in arr if _match(x, spec.get("where", {}), spec.get("contains"))]
        if spec["op"] == "select":
            vals = sorted((x.get(spec["field"]) for x in hits), key=rid_key)
            got["select"] = vals
            checks["select_equal"] = len(vals) == len(set(map(canon, vals))) and sorted(
                vals, key=rid_key
            ) == sorted(spec["expect"], key=rid_key)
        else:
            got["count"] = len(hits)
            checks["count_equal"] = len(hits) == spec["expect"]
        also = spec.get("also_item")
        if also:
            items = [x for x in arr if _match(x, also["key"], None)]
            checks["also_item_unique"] = len(items) == 1
            for f, v in also.get("fields", {}).items():
                checks[f"also.{f}"] = len(items) == 1 and items[0].get(f) == v
    elif spec["op"] == "item":
        items = [x for x in arr if _match(x, spec["key"], None)]
        checks["item_unique"] = len(items) == 1
        it = items[0] if len(items) == 1 else {}
        for f, v in spec.get("fields", {}).items():
            got[f] = it.get(f, "<absent>")
            checks[f"field.{f}"] = f in it and it.get(f) == v
        for f, v in spec.get("set_fields", {}).items():
            got[f] = it.get(f, "<absent>")
            checks[f"set.{f}"] = as_set(it.get(f)) == as_set(v)
        if "observed" in spec:
            obs = it.get("observed")
            ok_shape = isinstance(obs, list) and all(isinstance(o, dict) for o in obs)
            sts = sorted({o.get("status") for o in obs}, key=rid_key) if ok_shape else None
            got["observed"] = obs
            checks["observed.statuses"] = ok_shape and sts == spec["observed"]["statuses"]
            checks["observed.count"] = ok_shape and len(obs) == spec["observed"]["count"]
        if "steps" in spec:
            steps = it.get("steps")
            ok_shape = isinstance(steps, list) and all(isinstance(s, dict) for s in steps)
            ordered = sorted(steps, key=lambda s: s.get("i", 0)) if ok_shape else []
            for f, v in spec["steps"].items():
                got[f"steps.{f}"] = [s.get(f) for s in ordered]
                checks[f"steps.{f}"] = ok_shape and [s.get(f) for s in ordered] == v
    else:
        raise ValueError(f"unknown map op {spec['op']!r} in {q['id']}")
    return {"correct": all(checks.values()), "checks": checks, "got": got}


# ---------------------------------------------------------------- impact expectations


def expected_impact(qset: dict, cid: str) -> dict:
    ch = qset["changes"][cid]
    return {
        "t1": set(ch["tier1_ids"]),
        "t2": set(ch["tier2_ids"]),
        "t3": set(ch["tier3_ids"]),
        "tiers": ch["tiers"],
    }


def report_ids(rep: dict) -> dict:
    t1 = {
        i["id"]
        for k in ("failing", "flows", "resolved")
        for i in (rep.get("tier1") or {}).get(k, [])
    }
    t2 = {i["id"] for k in ("determined", "flows") for i in (rep.get("tier2") or {}).get(k, [])}
    t3 = {i["id"] for k in ("locations", "flows") for i in (rep.get("tier3") or {}).get(k, [])}
    return {"t1": t1, "t2": t2, "t3": t3, "tiers": rep.get("tiers")}


def check_reports(qset: dict, reports_dir: Path | None) -> tuple[dict[str, str], list[str]]:
    """Per change: "frozen" (no live report given), "verified" or "candidate_mismatch"; plus
    report-drift errors."""
    status: dict[str, str] = {}
    errors: list[str] = []
    for cid, ch in sorted(qset["changes"].items()):
        p = reports_dir / f"{cid}.json" if reports_dir else None
        if p is None or not p.exists():
            status[cid] = "frozen"
            continue
        rep = json.loads(p.read_text(encoding="utf-8"))
        if (rep.get("input") or {}).get("candidate") != ch["input"]["candidate"]:
            status[cid] = "candidate_mismatch"
            continue
        live, frozen = report_ids(rep), expected_impact(qset, cid)
        drift = [k for k in ("t1", "t2", "t3", "tiers") if live[k] != frozen[k]]
        if drift:
            errors.append(
                f"report_drift {cid}: {', '.join(drift)} differ from the frozen expectation"
            )
            status[cid] = "report_drift"
        else:
            status[cid] = "verified"
    return status, errors


# ---------------------------------------------------------------- the router surface


def _model_calls(entry: dict) -> int | None:
    resp = entry.get("response") or {}
    calls = entry.get("generator_calls")
    usage = resp.get("usage") if isinstance(resp, dict) else None
    tokens = usage.get("tokens") if isinstance(usage, dict) else None
    if calls is None and tokens is None:
        return None
    return int(calls or 0) + (1 if tokens else 0)


def check_router(q: dict, entry: dict | None, qset: dict) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("response"), dict):
        return {"correct": False, "checks": {"answered": False}, "claims": [], "routed_calls": None}
    resp = entry["response"]
    answer = resp.get("answer") if isinstance(resp.get("answer"), str) else ""
    checks: dict[str, bool] = {}
    got: dict[str, Any] = {}
    s = q["set"]
    relevant: set[str] = set()
    calls = _model_calls(entry)
    t3cf: list[str] = []
    if s == "I_impact":
        cid = q["known"]["impact"]["change"]
        exp = expected_impact(qset, cid)
        relevant = exp["t1"] | exp["t2"]
        imp = resp.get("impact")
        checks["routed"] = resp.get("kind") == "value" and isinstance(imp, dict)
        imp = imp if isinstance(imp, dict) else {}
        cites = [c for c in resp.get("citations") or [] if isinstance(c, str)]
        stated = stated_tids(imp) | stated_tids(cites)
        got["t1"] = sorted(i for i in stated if i.startswith("t1-"))
        got["t2"] = sorted(i for i in stated if i.startswith("t2-"))
        checks["change_id"] = has_string(imp, cid)
        checks["tier1_ids_equal"] = set(got["t1"]) == exp["t1"]
        checks["tier2_ids_equal"] = set(got["t2"]) == exp["t2"]
        tiers = find_key(imp, "tiers")
        checks["tiers_map_equal"] = bool(tiers) and all(t == exp["tiers"] for t in tiers)
        t3_in = stated_tids(imp, "3") | stated_tids(cites, "3")
        checks["tier3_ids_from_report"] = t3_in <= exp["t3"]
        t3cf = tier3_called_failure(answer, exp["t3"], relevant)
        checks["tier3_never_failure"] = not t3cf
        checks["zero_model_calls"] = calls in (None, 0)
    elif s == "H_hypothetical":
        checks["kind_not_specified"] = resp.get("kind") == "not_specified"
        checks["no_tier12_ids"] = not stated_tids(resp)
        checks["zero_model_calls"] = calls in (None, 0)
        got["fixed_text"] = H_FIXED in answer  # informational
    elif s == "C_control":
        checks["no_impact_key"] = resp.get("impact") is None
        checks["not_h_template"] = H_FIXED not in answer
        if isinstance(entry.get("base"), dict):
            checks["identical_to_base"] = canon(resp) == canon(entry["base"])
        got["base_compared"] = isinstance(entry.get("base"), dict)
    else:
        raise ValueError(f"{q['id']} is not a router question")
    claims = failure_claims(answer, relevant)
    if s in ("I_impact", "H_hypothetical"):
        checks["no_unsupported_claims"] = not claims
    return {
        "correct": all(checks.values()),
        "checks": checks,
        "got": got,
        "claims": claims,
        "claims_lenient": lenient_claims(claims),
        "tier3_called_failure": t3cf,
        "routed_calls": calls if s in ("I_impact", "H_hypothetical") else None,
    }


# ---------------------------------------------------------------- the agent surface


def check_content(spec: dict, text: str) -> tuple[dict[str, bool], dict[str, Any]]:
    t = norm_text(text)
    checks: dict[str, bool] = {}
    got: dict[str, Any] = {}
    for tok in spec.get("all", []):
        checks[f"all:{tok}"] = has_token(t, tok)
    for k, group in enumerate(spec.get("any", [])):
        checks[f"any:{k}"] = any(has_token(t, g) for g in group)
    for k, rx in enumerate(spec.get("none", [])):
        checks[f"none:{k}"] = re.search(rx, t, re.IGNORECASE) is None
    for k, ids in enumerate(spec.get("id_sets", [])):
        found = set(re.findall(ids["pattern"], t))
        exp = set(ids["expect"])
        got[f"id_set:{k}"] = sorted(found, key=rid_key)
        if ids.get("mode", "equal") == "superset":
            checks[f"id_set:{k}"] = exp <= found
        else:
            checks[f"id_set:{k}"] = found == exp
    if "route_set" in spec:
        found = {re.sub(r"\s+", " ", m.group(0)) for m in ROUTE_RE.finditer(t)}
        got["route_set"] = sorted(found)
        checks["route_set"] = found == set(spec["route_set"]["expect"])
    return checks, got


def check_agent(q: dict, entry: dict | None, qset: dict) -> dict:
    if (
        not isinstance(entry, dict)
        or entry.get("status", "ok") != "ok"
        or not isinstance(entry.get("text"), str)
    ):
        st = entry.get("status") if isinstance(entry, dict) else "missing"
        return {"correct": False, "not_run": True, "status": st, "checks": {}, "claims": []}
    text = entry["text"]
    s = q["set"]
    relevant: set[str] = set()
    checks: dict[str, bool] = {}
    got: dict[str, Any] = {}
    t3cf: list[str] = []
    compliant = None
    if s == "I_impact":
        exp = expected_impact(qset, q["known"]["impact"]["change"])
        relevant = exp["t1"] | exp["t2"]
        stated = stated_tids(text)
        got["t1"] = sorted(i for i in stated if i.startswith("t1-"))
        got["t2"] = sorted(i for i in stated if i.startswith("t2-"))
        checks["tier1_ids_equal"] = set(got["t1"]) == exp["t1"]
        checks["tier2_ids_equal"] = set(got["t2"]) == exp["t2"]
        t3cf = tier3_called_failure(text, exp["t3"], relevant)
        checks["tier3_never_failure"] = not t3cf
        every = stated_tids(text, "1234")
        unknown = sorted(every - (exp["t1"] | exp["t2"] | exp["t3"]))
        got["unknown_ids"] = unknown
        compliant = checks["tier1_ids_equal"] and checks["tier2_ids_equal"] and not unknown
    elif s == "H_hypothetical":
        t = norm_text(text)
        checks["not_decided"] = any(has_token(t, p) for p in NOT_DECIDED)
        checks["offer_to_draft"] = DRAFT_RE.search(t) is not None
        checks["no_tier12_ids"] = not stated_tids(text)
    else:
        c, got = check_content(q["known"]["agent"], text)
        checks.update(c)
    claims = failure_claims(text, relevant)
    if s == "H_hypothetical":
        checks["no_unsupported_claims"] = not claims
    out = {
        "correct": all(checks.values()),
        "not_run": False,
        "checks": checks,
        "got": got,
        "claims": claims,
        "claims_lenient": lenient_claims(claims),
        "tier3_called_failure": t3cf,
    }
    if compliant is not None:
        out["verbatim_id_compliant"] = compliant
    return out


# ---------------------------------------------------------------- scoring and gates


def score(qset: dict, surface: str, answers: dict, reports_dir: Path | None = None) -> dict:
    status, errors = check_reports(qset, reports_dir) if surface != "map" else ({}, [])
    per_q = []
    if surface == "map":
        qs = [q for q in qset["questions"] if q["set"] in MAP_SETS]
    elif surface == "router":
        qs = [q for q in qset["questions"] if q["set"] in ROUTER_SETS]
    elif surface == "agent":
        qs = list(qset["questions"])
    else:
        raise ValueError(surface)
    maps = answers.get("maps") or {}
    given = answers.get("answers") or {}
    for q in qs:
        cid = (q["known"].get("impact") or {}).get("change")
        if cid and status.get(cid) in ("candidate_mismatch", "report_drift"):
            per_q.append(
                {
                    "id": q["id"],
                    "set": q["set"],
                    "correct": False,
                    "unscored": status[cid],
                    "claims": [],
                }
            )
            continue
        if surface == "map":
            doc = given.get(q["id"], maps.get(q["corpus"]))
            r = check_map(q, doc)
            r["claims"] = []
        elif surface == "router":
            r = check_router(q, given.get(q["id"]), qset)
        else:
            r = check_agent(q, given.get(q["id"]), qset)
        r.update({"id": q["id"], "set": q["set"]})
        per_q.append(r)
    sets: dict[str, dict[str, int]] = {}
    for r in per_q:
        d = sets.setdefault(
            r["set"],
            {"n": 0, "correct": 0, "claims": 0, "claims_lenient": 0, "not_run": 0, "unscored": 0},
        )
        d["n"] += 1
        d["correct"] += bool(r["correct"])
        d["claims"] += len(r.get("claims", []))
        d["claims_lenient"] += len(r.get("claims_lenient", []))
        d["not_run"] += bool(r.get("not_run"))
        d["unscored"] += bool(r.get("unscored"))
    n = len(per_q)
    correct = sum(bool(r["correct"]) for r in per_q)
    claims = sum(len(r.get("claims", [])) for r in per_q)
    summary: dict[str, Any] = {
        "surface": surface,
        "n": n,
        "correct": correct,
        "unsupported_failure_claims": claims,
        "unsupported_failure_claims_lenient": sum(len(r.get("claims_lenient", [])) for r in per_q),
        "lenient_note": "report-only (Amendment 2): negations and named recorded replays exempted",
        "not_run": sum(bool(r.get("not_run")) for r in per_q),
        "unscored": sum(bool(r.get("unscored")) for r in per_q),
        "by_set": dict(sorted(sets.items())),
        "changes": status,
        "errors": errors,
    }
    if surface == "map":
        summary["gate"] = {"rule": "22/22 exact", "pass": n == 22 and correct == 22}
    elif surface == "router":
        calls = [r.get("routed_calls") for r in per_q if r["set"] in ("I_impact", "H_hypothetical")]
        summary["model_calls_on_routed"] = sum(c or 0 for c in calls)
        summary["model_calls_unknown"] = sum(c is None for c in calls)
        summary["gate"] = {
            "rule": "22/22 exact; 0 unsupported failure claims; 0 model calls on routed questions",
            "pass": n == 22
            and correct == 22
            and claims == 0
            and summary["model_calls_on_routed"] == 0,
        }
    else:
        comp = sum(bool(r.get("verbatim_id_compliant")) for r in per_q if r["set"] == "I_impact")
        summary["i_verbatim_id_compliance"] = comp
        summary["cost_note"] = "cost per correct answer = list estimate / correct (runner's usage)"
        run = n - summary["not_run"]
        summary["gate"] = {
            "rule": "0 unsupported failure claims (hard); >= 36/44 correct; "
            "I verbatim-id compliance >= 10/12; a not-run question is never a pass "
            "(partial: k of 44)",
            "pass": n == 44
            and summary["not_run"] == 0
            and claims == 0
            and correct >= 36
            and comp >= 10,
            "partial": None if summary["not_run"] == 0 else f"partial: {run} of {n}",
        }
    return {"summary": summary, "questions": per_q}


# ---------------------------------------------------------------- self-test


SET_COUNTS = {
    "M_rules": 5,
    "M_workflows": 5,
    "M_machines": 3,
    "M_access": 5,
    "N_not_built": 4,
    "I_impact": 12,
    "H_hypothetical": 6,
    "C_control": 4,
}
# The D5 router patterns of the prereg, used here only to check that each question is phrased for
# its set (case-insensitive: the prereg's own example starts with "What").
RA_ID = re.compile(r"\bc\d+\b", re.IGNORECASE)
RA_WORD = re.compile(r"\b(break|breaks|broken|fail|fails|failing|impact|affect)\b", re.IGNORECASE)
RB = {
    "B1": re.compile(r"\bwhat\s+(would\s+|will\s+)?(break|fail)s?\b.*\bif\b", re.IGNORECASE),
    "B2": re.compile(r"\bif\s+we\b.*\b(break|fail)s?\b", re.IGNORECASE),
    "B3": re.compile(r"^\s*what\s+breaks\b", re.IGNORECASE),
}


def qset_problems(qset: dict) -> list[str]:
    """The question set's own shape: the section 5 counts and each question's routing."""
    probs = []
    qs = qset["questions"]
    if len(qs) < 44:
        probs.append(f"{len(qs)} questions (< 44)")
    if len({q["id"] for q in qs}) != len(qs):
        probs.append("duplicate question ids")
    for s, n in SET_COUNTS.items():
        k = sum(q["set"] == s for q in qs)
        if k != n:
            probs.append(f"set {s}: {k} questions, section 5 says {n}")
    for q in qs:
        t = q["text"]
        a = bool(RA_ID.search(t) and RA_WORD.search(t))
        b = [k for k, rx in RB.items() if rx.search(t)]
        if q["set"] == "I_impact" and not a:
            probs.append(f"{q['id']}: does not match router pattern A")
        if (
            q["set"] == "I_impact"
            and not RA_ID.search(t).group(0) == q["known"]["impact"]["change"]
        ):
            probs.append(f"{q['id']}: names another change")
        if q["set"] == "H_hypothetical" and (a or RA_ID.search(t) or q["known"]["route"] not in b):
            probs.append(f"{q['id']}: not a (B) hypothetical without a change id ({b})")
        if q["set"] == "C_control" and (a or b):
            probs.append(f"{q['id']}: a control would be routed ({'A' if a else b})")
        if not q.get("sources"):
            probs.append(f"{q['id']}: no source cited")
    return probs


def self_test(qset: dict, path: Path) -> int:
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    qs = {q["id"]: q for q in qset["questions"]}
    fails = 0
    probs = qset_problems(qset)
    for p in probs:
        print(f"FAIL qset: {p}")
    print(
        f"{'ok  ' if not probs else 'FAIL'} qset shape: {len(qs)} questions, "
        "section 5 counts, routing phrasing"
    )
    fails += bool(probs)
    for c in cases:
        exp = c["expect"]
        if c["surface"] == "claims":
            got_claims = failure_claims(c["text"], set(c.get("relevant_ids", [])))
            got = {
                "unsupported": len(got_claims),
                "unsupported_lenient": len(lenient_claims(got_claims)),
            }
        elif c["surface"] == "sentences":
            got = {"sentences": sentences(c["text"])}
        else:
            q = qs[c["qid"]]
            if c["surface"] == "map":
                r = check_map(q, c["answer"])
                r["claims"] = []
            elif c["surface"] == "router":
                r = check_router(q, c["answer"], qset)
            else:
                r = check_agent(q, c["answer"], qset)
            got = {
                "correct": r["correct"],
                "unsupported": len(r.get("claims", [])),
                "unsupported_lenient": len(r.get("claims_lenient", [])),
                "tier3_called_failure": len(r.get("tier3_called_failure", []) or []),
                "verbatim_id_compliant": r.get("verbatim_id_compliant"),
                "not_run": r.get("not_run", False),
            }
            if "failed_checks" in exp:
                got["failed_checks"] = sorted(k for k, v in r.get("checks", {}).items() if not v)
        bad = {k: (got.get(k), v) for k, v in exp.items() if got.get(k) != v}
        mark = "ok  " if not bad else "FAIL"
        print(f"{mark} {c['name']}" + ("" if not bad else f"  got/expected: {bad}"))
        fails += bool(bad)
    print(
        f"self-test: {len(cases) + 1 - fails}/{len(cases) + 1} cases as specified "
        "(incl. the qset shape)"
    )
    return 1 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--qset", required=True)
    ap.add_argument("--surface", choices=("map", "router", "agent"))
    ap.add_argument("--answers")
    ap.add_argument("--reports")
    ap.add_argument("--self-test", dest="self_test")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        qset = json.loads(Path(a.qset).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"error: qset: {e}", file=sys.stderr)
        return 2
    if a.self_test:
        return self_test(qset, Path(a.self_test))
    if not a.surface or not a.answers:
        print("error: --surface and --answers are required (or --self-test)", file=sys.stderr)
        return 2
    try:
        answers = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"error: answers: {e}", file=sys.stderr)
        return 2
    res = score(qset, a.surface, answers, Path(a.reports) if a.reports else None)
    if a.json:
        print(json.dumps(res, indent=1, sort_keys=True, ensure_ascii=False))
    else:
        s = res["summary"]
        print(
            f"surface {s['surface']}: {s['correct']}/{s['n']} correct; unsupported failure claims "
            f"{s['unsupported_failure_claims']}; gate {'PASS' if s['gate']['pass'] else 'NOT MET'} "
            f"({s['gate']['rule']}); lenient count (report-only) "
            f"{s['unsupported_failure_claims_lenient']}"
        )
        for k, v in s["by_set"].items():
            print(
                f"  {k}: {v['correct']}/{v['n']} correct, {v['claims']} claims"
                + f" ({v['claims_lenient']} lenient)"
                + (f", {v['not_run']} not run" if v["not_run"] else "")
                + (f", {v['unscored']} unscored" if v["unscored"] else "")
            )
        for r in res["questions"]:
            if not r["correct"]:
                failed = sorted(k for k, v in r.get("checks", {}).items() if not v)
                print(f"  x {r['id']}: {r.get('unscored') or r.get('status') or failed}")
            for c in r.get("claims", []):
                tag = "" if c in r.get("claims_lenient", []) else " [exempt in lenient count]"
                print(f"  ! {r['id']} unsupported failure claim: {c}{tag}")
        for e in s["errors"]:
            print(f"  error: {e}")
    return 3 if res["summary"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
