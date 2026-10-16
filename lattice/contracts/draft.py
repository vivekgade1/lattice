"""Consumer-side drafting of a linked question (multi-project slice 4d): a text-only, deterministic
draft of what the consumer's owner could change, built from the files already on disk.

    lattice contracts questions draft CONSUMER ID [--json] [--workspace PATH]
    MCP: linked_question_draft {consumer?, id}

**Nothing is applied.** The draft is a pure function of one stored question, the consumer's
``lattice.imports.toml``, its ``lattice.contracts.lock.json`` (the pinned export: the "before"
facts) and, when present, its ``REQUIREMENTS.md`` and ``spec.trace.json``. It runs no subprocess
(no export, no git), no model, takes no flock, writes no file, creates no state directory and no
change record, and reads no clock: the same files give the same bytes (``text`` holds no time
stamp and no absolute path). The "after" facts are the question's own findings; the candidate's
export is not stored and is not recomputed.

**What exists, exactly.** An impact row or a question carries no consumer rule and no step:
its findings are ``endpoint, kind, item, detail, relied``. What the draft can name exactly is the
set of affected ``[[import]]`` tables (by position in the file, ``import #k``, the label
``parse_imports`` gives them). Rules are found only by a stated text match: a rule *names* an
endpoint when its text holds ``[METHOD ]/path`` whose path, templated as ``parse_endpoint`` does,
equals the endpoint's path and the method is absent or equal. That is a text match, never
verified reliance; every draft says so.

**Suggested edits** only replace: a replacement exists only when, per (endpoint, side), exactly
one BROKEN missing item and exactly one relied CHANGED added item of the same kind (status,
request field, response field) are in the question. A hunk that only removes a claim is never
produced (declaring less makes ``check`` pass without the code being right).

Exit codes of :func:`draft` and :func:`draft_for`: 0 produced, 1 refused (``not_open``,
``not_contradiction``), 2 cannot (``unknown_project``, ``no_state``, ``unknown_question``,
``manifest_error``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lattice.contracts import check as ck
from lattice.contracts import impact as imp
from lattice.contracts import linked
from lattice.contracts.export import HTTP_METHODS, template_path

FORMAT = 1
REQUIREMENTS = "REQUIREMENTS.md"
TRACE = "spec.trace.json"
NOT_APPLIED = (
    "NOT APPLIED: this draft changes nothing in {consumer}; its owner decides and proposes any "
    "spec edit through the change flow."
)
ASK = (
    "Ask {provider}'s owner, or adapt the code; the claim stays until the code no longer "
    "relies on it."
)
CODE_FIRST = "change the code that sends/reads it first, then"
# a path inside a rule's text: a '/' at a word boundary (never inside "and/or" or a URL) and the
# characters a templated path holds; trailing punctuation is stripped by the caller
_PATH = re.compile(r"(?<![^\s`(\[\"'<])/[A-Za-z0-9_\-.{}~%:/]*")
_TRAIL = ".,;:)"
_METHOD_BEFORE = re.compile(r"([A-Za-z]+)\s+$")
_SIDES = ("status", "request", "response")


# ---- small helpers ------------------------------------------------------------------------------


def _err(code: str, message: str, rc: int) -> tuple[int, dict[str, Any]]:
    return rc, {"format": FORMAT, "error": {"code": code, "message": message}}


def _side_of(f: Mapping[str, Any]) -> str | None:
    """The (endpoint-level) side a finding belongs to for pairing: status, request, response."""
    kind = f["kind"]
    if kind in ("status_not_observed", "status_added"):
        return "status"
    if kind == "request_field_missing":
        return "request"
    if kind == "response_field_missing":
        return "response"
    if kind == "field_added":
        where = f.get("where")
        if where in ("request", "query"):
            return "request"
        if where == "response":
            return "response"
    return None


def _split(endpoint: str) -> tuple[str, str]:
    method, _, path = endpoint.partition(" ")
    return method, path


def _short(h: Any) -> str:
    return str(h)[:12] if h else "unknown"


def _list(values: Sequence[Any]) -> str:
    return "[" + ", ".join(str(v) for v in values) + "]"


def _facts(ep: Mapping[str, Any]) -> str:
    """One pinned endpoint's facts on one line."""
    auth = f"auth {ep.get('auth')}, granted {_list(ep.get('granted', []))}"
    if ep.get("refused"):
        auth += f", refused {_list(ep['refused'])}"
    return (f"statuses {_list(ep.get('statuses', []))}; request fields "
            f"{_list(ep.get('request_fields', []))}; query fields "
            f"{_list(ep.get('query_fields', []))}; response fields "
            f"{_list(ep.get('response_fields', []))}; {auth}")  # fmt: skip


def _before(f: Mapping[str, Any], export: Mapping[str, Any]) -> str:
    """The pinned facts a finding refers to (from the lock's export of the provider)."""
    endpoint, kind = f["endpoint"], f["kind"]
    if endpoint == "*":
        return f"error_body {export.get('error_body')}"
    ep = export.get("endpoints", {}).get(endpoint)
    if ep is None:
        return "not in the pinned export"
    if kind in ("status_not_observed", "status_added", "status_removed"):
        return f"statuses {_list(ep.get('statuses', []))}"
    if kind == "request_field_missing" or (
        kind in ("field_added", "field_removed") and f.get("where") in ("request", "query")
    ):
        return (f"request fields {_list(ep.get('request_fields', []))}; query fields "
                f"{_list(ep.get('query_fields', []))}")  # fmt: skip
    if kind == "response_field_missing" or (
        kind in ("field_added", "field_removed") and f.get("where") == "response"
    ):
        return f"response fields {_list(ep.get('response_fields', []))}"
    if kind in ("auth_changed", "auth_tightened", "auth_loosened"):
        refused = f", refused {_list(ep['refused'])}" if ep.get("refused") else ""
        return f"auth {ep.get('auth')}, granted {_list(ep.get('granted', []))}{refused}"
    if kind == "body_shape_changed":
        return f"body {_list(ep.get('body', []))}"
    return _facts(ep)


# ---- the pairing (suggested edits) --------------------------------------------------------------


def _pairs(findings: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], tuple[str, str]]:
    """``{(endpoint, side): (old item, new item)}``: per (endpoint, side) exactly one BROKEN
    missing item and exactly one relied CHANGED added item."""
    missing: dict[tuple[str, str], list[str]] = {}
    added: dict[tuple[str, str], list[str]] = {}
    for f in findings:
        side = _side_of(f)
        if side is None or f["endpoint"] == "*":
            continue
        key = (f["endpoint"], side)
        if f["class"] == "BROKEN" and f["kind"] in (
            "status_not_observed", "request_field_missing", "response_field_missing",
        ):  # fmt: skip
            missing.setdefault(key, []).append(f["item"])
        elif f["class"] == "CHANGED" and f["kind"] in ("status_added", "field_added"):
            added.setdefault(key, []).append(f["item"])
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for key in sorted(missing):
        old, new = missing[key], added.get(key, [])
        if len(old) == 1 and len(new) == 1:
            out[key] = (old[0], new[0])
    return out


_ATTR = {"status": "status", "request": "request_fields", "response": "response_fields"}


def _edits(
    pairs: Mapping[tuple[str, str], tuple[str, str]],
    numbered: Sequence[tuple[int, ck.Import]],
) -> list[dict[str, Any]]:
    """The replacement hunks: for each pair, each import table of the endpoint that holds the
    old item and does not already hold the new one (otherwise the hunk would only remove)."""
    out: list[dict[str, Any]] = []
    for (endpoint, side), (old, new) in pairs.items():
        for number, im in numbered:
            if im.endpoint != endpoint:
                continue
            key = _ATTR[side]
            held = list(getattr(im, key))
            o: Any = int(old) if side == "status" else old
            n: Any = int(new) if side == "status" else new
            if o not in held or n in held:
                continue
            after = [n if v == o else v for v in held]
            out.append({"import": number, "endpoint": endpoint, "key": key, "old": o, "new": n,
                        "before": held, "after": after,
                        "text": f"{key} = {_toml(held)} -> {_toml(after)}"})  # fmt: skip
    out.sort(key=lambda e: (e["import"], e["key"]))
    return out


def _toml(values: Sequence[Any]) -> str:
    return "[" + ", ".join(json.dumps(v) for v in values) + "]"


def _no_hunk(
    findings: Sequence[Mapping[str, Any]],
    pairs: Mapping[tuple[str, str], tuple[str, str]],
    edits: Sequence[Mapping[str, Any]],
    provider: str,
) -> list[dict[str, Any]]:
    """Why an endpoint has no hunk: one record per (endpoint, reason)."""
    done = {(e["endpoint"], e["key"]) for e in edits}
    notes: dict[tuple[str, str], list[str]] = {}
    for f in findings:
        if f["class"] != "BROKEN" or f["endpoint"] == "*":
            continue
        endpoint, kind, side = f["endpoint"], f["kind"], _side_of(f)
        if side is not None and (endpoint, side) in pairs:
            if (endpoint, _ATTR[side]) in done:
                continue
            why = (f"the replacement of {f['item']} -> {pairs[(endpoint, side)][1]} would only "
                   "remove a claim (no import table of this endpoint holds the old item without "
                   "the new one)")  # fmt: skip
        elif kind == "endpoint_missing" and f.get("detail") == ck.EXCLUDED_DETAIL:
            why = f"{provider} excludes {endpoint} from its contract (exclude in its manifest)"
        elif kind == "endpoint_missing":
            why = f"{endpoint} is no longer in {provider}'s spec"
        elif kind == "auth_changed":
            why = f"the auth of {endpoint} changed ({f.get('detail')})"
        else:
            why = (f"{kind} {f['item']} at {endpoint} has no single removed/added pair of the "
                   "same kind to replace it")  # fmt: skip
        notes.setdefault((endpoint, why), [])
    return [{"endpoint": e, "reason": w, "advice": ASK.format(provider=provider)}
            for (e, w) in sorted(notes)]  # fmt: skip


# ---- the rules (text match) ---------------------------------------------------------------------


def _mentions(text: str) -> list[tuple[str | None, str]]:
    """Every ``[METHOD ]/path`` of a rule's text as ``(METHOD or None, templated path)``."""
    out: list[tuple[str | None, str]] = []
    for m in _PATH.finditer(text):
        raw = m.group(0).rstrip(_TRAIL)
        method: str | None = None
        before = _METHOD_BEFORE.search(text[: m.start()])
        if before and before.group(1).upper() in HTTP_METHODS:
            method = before.group(1).upper()
        if raw == "/" and method is None:
            continue
        out.append((method, template_path(raw)))
    return out


def _names(text: str, endpoint: str) -> bool:
    method, path = _split(endpoint)
    return any(p == path and (mm is None or mm == method) for mm, p in _mentions(text))


def _rules(
    rules_md: str | None,
    trace: Mapping[str, Any] | str | None,
    endpoints: Sequence[str],
    pairs: Mapping[tuple[str, str], tuple[str, str]],
    consumer: str,
) -> tuple[list[dict[str, Any]], str]:
    """``(rules that name an endpoint, note)``."""
    if rules_md is None:
        return [], f"{consumer} has no Lattice spec: no rule to edit"
    from lattice.spec.rules import extract_rules

    traced: dict[str, str] = {}
    links: dict[str, list[str]] = {}
    if isinstance(trace, Mapping):
        for r in trace.get("rules", []) or []:
            if isinstance(r, Mapping) and "id" in r:
                traced[str(r["id"])] = str(r.get("text", ""))
        for k, v in (trace.get("links", {}) or {}).items():
            links[str(k)] = [str(x) for x in v] if isinstance(v, list) else []
    out: list[dict[str, Any]] = []
    for rule in extract_rules(rules_md):
        named = [e for e in endpoints if _names(rule.text, e)]
        if not named:
            continue
        rec: dict[str, Any] = {"id": rule.id, "text": rule.text, "endpoints": named,
                               "trace": "none", "scenarios": [], "wording": None}  # fmt: skip
        if isinstance(trace, Mapping):
            if traced.get(rule.id) == rule.text:
                rec["trace"] = "ok"
                rec["scenarios"] = [t.split(":", 1)[1] for t in links.get(rule.id, [])
                                    if t.startswith("scenario:")]  # fmt: skip
            else:
                rec["trace"] = "stale"
        wording = rule.text
        for endpoint in named:
            for side in _SIDES:
                pair = pairs.get((endpoint, side))
                if pair is None:
                    continue
                rx = re.compile(rf"(?<!\w){re.escape(pair[0])}(?!\w)")
                wording = rx.sub(pair[1], wording)
        if wording != rule.text:
            rec["wording"] = wording
        out.append(rec)
    return out, ""


# ---- the draft ----------------------------------------------------------------------------------


def draft(
    question: Mapping[str, Any],
    imports: Sequence[ck.Import] | str,
    lock: Mapping[str, Any] | str | None,
    rules_md: str | None,
    trace: Mapping[str, Any] | str | None,
) -> tuple[int, dict[str, Any]]:
    """The draft of one stored question (module docstring): ``(exit code, document)``.

    `imports` is the consumer's parsed imports or an error code (``no_imports``,
    ``bad_imports``); `lock` the parsed lock, an error code (``no_pin``, ``bad_pin``) or None
    (no lock); `rules_md` the text of REQUIREMENTS.md or None; `trace` the parsed
    ``spec.trace.json``, None, or an error code. Pure: no I/O, no clock."""
    qid = str(question.get("id"))
    if question.get("status") != linked.OPEN:
        return _err("not_open", f"{qid} is already {question.get('status')}: nothing to draft", 1)
    provider, consumer = str(question["provider"]), str(question["consumer"])
    findings: list[dict[str, Any]] = [dict(f) for f in question.get("findings", [])]
    sup = question.get("superseded")
    pinned = (question.get("pinned") or {}).get("spec_hash")
    after = question.get("provider_after") or {}

    # -- which tables of the consumer's imports rely on a finding's endpoint
    numbered: list[tuple[int, ck.Import]] = []
    imports_error: str | None = None
    if isinstance(imports, str):
        imports_error = imports
    else:
        numbered = [(i, im) for i, im in enumerate(imports, 1) if im.provider == provider]
    affected = sorted({f["endpoint"] for f in findings if f["endpoint"] != "*"})
    rows: list[dict[str, Any]] = []
    for number, im in numbered:
        if im.endpoint not in affected:
            continue
        claims = set(ck.claims_of(im))
        broken = [{"kind": f["kind"], "item": f["item"]} for f in findings
                  if f["class"] == "BROKEN" and f["endpoint"] == im.endpoint
                  and (f["kind"] == "endpoint_missing" or _claim(f) in claims)]  # fmt: skip
        changed = [{"kind": f["kind"], "item": f["item"]} for f in findings
                   if f["class"] == "CHANGED" and f["endpoint"] == im.endpoint]  # fmt: skip
        rows.append({"number": number, "endpoint": im.endpoint, "table": im.to_dict(),
                     "broken": broken, "changed": changed})  # fmt: skip
    imported = {r["endpoint"] for r in rows}
    gone = [e for e in affected if e not in imported] if imports_error is None else []

    pairs = _pairs(findings)
    edits = _edits(pairs, numbered) if imports_error is None else []
    no_hunk = _no_hunk(findings, pairs, edits, provider)
    rules, rules_note = _rules(rules_md, trace, affected, pairs, consumer)

    # -- before facts
    before_note: str | None = None
    export: Mapping[str, Any] | None = None
    if isinstance(lock, Mapping):
        entry = (lock.get("providers") or {}).get(provider)
        have = ((entry or {}).get("version") or {}).get("spec_hash")
        if entry is None:
            before_note = f"before facts omitted: the lock holds no pin of {provider}"
        elif have != pinned:
            before_note = (f"pin moved: the lock now holds spec_hash {_short(have)}, this "
                           f"question was raised against {_short(pinned)}; before facts are "
                           "omitted")  # fmt: skip
        else:
            export = entry.get("export") or {}
    elif isinstance(lock, str):
        before_note = f"before facts omitted: {lock}"
    else:
        before_note = "before facts omitted: no_pin (the consumer has no pinned lock)"

    # -- the text
    head = f"# Draft: adapt {consumer} to {provider} (linked question {qid})"
    line = f"Question {qid}, {question.get('label', 'contradiction')}, {question.get('status')}"
    if isinstance(sup, Mapping):
        if sup.get("by"):
            line += f"; superseded by {sup['by']}: its draft is the current one"
        else:
            line += ("; superseded: the provider's current contract no longer contradicts "
                     "the pin")  # fmt: skip
    t: list[str] = [head, line, NOT_APPLIED.format(consumer=consumer), ""]
    t.append("## 1. What changed")
    commit = str(after.get("git_ref") or "no-git")[:8]
    t.append(f"pinned spec_hash {_short(pinned)}; {provider} now spec_hash "
             f"{_short(after.get('spec_hash'))} (commit {commit})")  # fmt: skip
    if before_note:
        t.append(before_note)
    fnd: list[dict[str, Any]] = []
    for f in findings:
        item: dict[str, Any] = {"line": linked.finding_line(f), "endpoint": f["endpoint"],
                                "class": f["class"], "kind": f["kind"], "item": f["item"],
                                "before": None, "after": f.get("detail")}  # fmt: skip
        t.append(item["line"])
        if f["endpoint"] == "*":
            t.append("    the whole contract (no import is named)")
        if export is not None:
            item["before"] = _before(f, export)
            t.append(f"    before: {item['before']}")
        t.append(f"    after: {f.get('detail')}")
        fnd.append(item)
    t += ["", "## 2. Imports that rely on it"]
    if imports_error is not None:
        t.append(f"{imports_error}: the consumer's lattice.imports.toml is "
                 f"{'missing' if imports_error == 'no_imports' else 'not readable'}; the "
                 "findings above are listed without the import tables")  # fmt: skip
    else:
        for r in rows:
            im = r["table"]
            t.append(f"import #{r['number']}: {im['endpoint']}  status {_list(im['status'])}, "
                     f"auth {im['auth']}"
                     + (f" as {im['as_user']}" if im.get("as_user") else "")
                     + f", request_fields {_list(im['request_fields'])}, "
                     f"response_fields {_list(im['response_fields'])}")  # fmt: skip
            for b in r["broken"]:
                t.append(f"    broken claim: {b['kind']} {b['item']}".rstrip())
            for c in r["changed"]:
                t.append(f"    relied change: {c['kind']} {c['item']}".rstrip())
        for e in gone:
            t.append(f"{e}: no longer imported")
        if not rows and not gone:
            t.append("no finding names a single endpoint")
    t += ["", "## 3. Suggested edits (verify in the code first)"]
    for e in edits:
        t.append(f"- {CODE_FIRST} import #{e['import']}: {e['text']}")
    for n in no_hunk:
        t.append(f"- {n['endpoint']}: no edit suggested: {n['reason']}. {n['advice']}")
    if not edits and not no_hunk:
        t.append("- no edit suggested")
    t.append("- A claim is never removed here: declaring less would make `check` pass without "
             "the code being right.")  # fmt: skip
    t += ["", "## 4. Consumer spec rules that name the endpoint "
          "(text match, not verified reliance)"]  # fmt: skip
    if rules_note:
        t.append(rules_note)
    elif not rules:
        t.append("no rule of REQUIREMENTS.md names an affected endpoint (text match)")
    for r in rules:
        t.append(f"{r['id']}: {r['text']}")
        t.append(f"    names: {', '.join(r['endpoints'])}")
        if r["trace"] == "stale":
            t.append(f"    trace stale for {r['id']}: spec.trace.json holds other text for it, "
                     "so no scenario is listed")  # fmt: skip
        elif r["trace"] == "none":
            t.append("    no readable spec.trace.json: no scenario is listed")
        elif r["scenarios"]:
            t.append(f"    scenarios: {', '.join(r['scenarios'])}")
        else:
            t.append("    scenarios: none linked")
        if r["wording"] is not None:
            t.append(f"    suggested wording: {r['wording']}")
    t += ["", "## 5. Then",
          "1. Edit the code that calls the endpoint(s) above, then the matching [[import]] tables.",
          f"2. `lattice contracts pin {consumer}`, then `lattice contracts check {consumer}`.",
          "3. Propose any rule edit with `lattice:change` or in Studio.",
          f"4. Clear this question with `lattice contracts questions resolve {consumer} {qid}`.",
          "", "## Not decided here",
          f"- Whether {provider}'s change is intended (its owner may revert it).",
          "- Where in the code each call lives.",
          "- Whether a rule's behaviour really depends on the endpoint."]  # fmt: skip
    text = "\n".join(t)
    doc: dict[str, Any] = {
        "format": FORMAT, "kind": "consumer_draft", "applied": False,
        "consumer": consumer, "provider": provider, "contract": question.get("contract"),
        "question": {"id": qid, "status": question.get("status"),
                     "label": question.get("label"), "superseded": sup},
        "pinned_spec_hash": pinned, "provider_after_spec_hash": after.get("spec_hash"),
        "before_note": before_note, "findings": fnd,
        "imports": rows, "imports_error": imports_error, "no_longer_imported": gone,
        "edits": edits, "no_edit": no_hunk,
        "rules": rules, "rules_note": rules_note or None,
        "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }  # fmt: skip
    return 0, doc


def _claim(f: Mapping[str, Any]) -> tuple[str, str]:
    """The ``(claim kind, item)`` of a BROKEN finding (the inverse of ``BROKEN_KINDS``)."""
    kind = {v: k for k, v in ck.BROKEN_KINDS.items()}.get(f["kind"], f["kind"])
    return kind, f["item"]


def draft_from_row(
    row: Mapping[str, Any],
    candidate: Mapping[str, Any],
    imports: Sequence[ck.Import] | str,
    lock: Mapping[str, Any] | str | None,
    rules_md: str | None,
    trace: Mapping[str, Any] | str | None,
    *,
    ws_name: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """The draft of an impact row (library only; no CLI flag, ``impact`` output is unchanged):
    the same question :func:`lattice.contracts.linked.build_question` would store (id ``row``),
    built in memory, nothing written. A row whose label is not ``contradiction`` is refused
    (``not_contradiction``, exit 1)."""
    if row.get("label") != imp.CONTRADICTION:
        return _err("not_contradiction", f"the row is {row.get('label')!r}, not a "
                    "contradiction: no question exists and nothing is drafted", 1)  # fmt: skip
    key = linked.key_of(row["provider"], row["consumer"], row["contract"], row)
    q = linked.build_question("row", key, ws_name, row, candidate,
                              {"via": "row", "change": None, "commit": None}, "")  # fmt: skip
    return draft(q, imports, lock, rules_md, trace)


# ---- reading the consumer's files (read-only) -------------------------------------------------


def load_files(project: Path, consumer: str) -> tuple[Any, Any, str | None, Any]:
    """``(imports, lock, REQUIREMENTS.md text, trace)`` of a consumer directory, as
    :func:`draft` takes them: error codes instead of exceptions. Reads only."""
    project = Path(project)
    try:
        imports: Any = ck.parse_imports(project / ck.IMPORTS_FILE)
    except ck.ContractError as e:
        imports = e.code
    try:
        lock: Any = ck.read_lock(project / ck.LOCK_FILE, consumer)
        if lock is None:
            lock = "no_pin"
    except ck.ContractError as e:
        lock = e.code
    try:
        rules_md: str | None = (project / REQUIREMENTS).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        rules_md = None
    trace: Any = None
    tpath = project / TRACE
    if tpath.is_file():
        try:
            doc = json.loads(tpath.read_text(encoding="utf-8"))
            trace = doc if isinstance(doc, dict) else "bad_trace"
        except (OSError, ValueError):
            trace = "bad_trace"
    return imports, lock, rules_md, trace


def draft_for(ws: Any, consumer: str, qid: str) -> tuple[int, dict[str, Any]]:
    """The draft of the consumer's stored question `qid` (module docstring). Lock-free and
    write-free: ``linked.locate`` and ``linked.get_question`` read only, a missing state
    directory is ``no_state`` (nothing is created)."""
    if ws.errors:
        return _err("manifest_error", "; ".join(p.message for p in ws.errors), 2)
    wp = ws.project(consumer)
    if wp is None:
        have = ", ".join(ws.names()) or "none"
        return _err("unknown_project", f"no project {consumer!r} in the workspace "
                    f"(have {have})", 2)  # fmt: skip
    loc = linked.locate(wp.path, wp.name)
    if loc.state != "present" or loc.state_dir is None:
        return _err("no_state", f"{wp.name} has no state directory ({loc.state})", 2)
    try:
        q = linked.get_question(loc.state_dir, qid)
    except linked.LinkedError as e:
        return _err(e.code, e.message, 2)
    return draft(q, *load_files(wp.path, wp.name))
