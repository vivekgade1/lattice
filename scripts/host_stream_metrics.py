#!/usr/bin/env python3
"""Metrics from a Cursor Agent CLI ``--output-format stream-json`` transcript.

The second-host sibling of ``scripts/stream_metrics.py`` (Claude Code). It prints the same
top-level keys (``source, session_id, model, records, bad_lines, turns, tool_calls, tool_uses,
denials, hooks, results_seen, result, tokens_in, wall, tamper``) so a runner can read either; the
Claude ``aggregate`` is reused for several inputs.

    cursor-agent -p "..." --output-format stream-json | python scripts/host_stream_metrics.py -
    python scripts/host_stream_metrics.py run.jsonl --protected spec.py check.sh

Source of the schema, https://cursor.com/docs/cli/reference/output-format (fetched 2026-10-30):

- ``{"type": "system", "subtype": "init", "apiKeySource", "cwd", "session_id", "model",
  "permissionMode"}``;
- ``{"type": "user" | "assistant", "message": {"role", "content": [...]}, "session_id"}``
  (``--stream-partial-output`` adds ``timestamp_ms`` and ``model_call_id``);
- ``{"type": "tool_call", "subtype": "started" | "completed", "call_id", "tool_call":
  {"<name>ToolCall": {"args": {...}, "result": {...}}}, "session_id"}``; the docs show
  ``readToolCall`` (args ``path``; result ``{"success": {...}}``), ``writeToolCall`` and a generic
  ``function`` key for "other tools";
- ``{"type": "result", "subtype": "success", "is_error", "duration_ms", "duration_api_ms",
  "result", "session_id", "request_id"}``.

What this host does NOT give (so the output says so rather than inventing numbers)
---------------------------------------------------------------------------------
- No token usage, no cost, no turn count: ``tokens_in`` is 0, ``result.total_cost_usd`` is 0,
  ``result.num_turns`` is the count of assistant messages, and ``unavailable`` lists the fields.
- No documented representation of a hook denial or a rejected tool call. A completed call whose
  ``result`` has no ``success`` key counts as ``failed``; it is a *denial* only when the result
  text names one (``rejected``/``denied``/``blocked``, or the Lattice guard's deny text, which a
  Cursor hook returns to the agent as ``agent_message``). Whether Cursor surfaces that text in
  the stream is UNVERIFIED; a silent block would show as a failure at most.
- The key names for shell and edit tools (only ``read``, ``write`` and ``function`` appear in the
  docs). A tool's name here is its key minus ``ToolCall``; a call is judged a shell command when
  its ``args`` hold a string ``command``, and a file write when the key names write/edit/delete
  and ``args`` hold a string ``path`` or ``file_path``. Both are UNVERIFIED guesses.
- Hook records of any kind: ``hooks`` is always empty.

Tamper accounting uses the same detectors as ``stream_metrics.py`` (``lattice.mcp.guard.Guard``,
or the lexical fallback). ``blocked`` = the call completed without ``success``; ``landed`` = it
completed with ``success``; ``unresolved`` = no ``completed`` record. Exit codes: 0 ok, 2 an input
could not be read.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_claude_metrics() -> ModuleType:
    path = Path(__file__).resolve().with_name("stream_metrics.py")
    spec = importlib.util.spec_from_file_location("_claude_stream_metrics", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_claude_stream_metrics", mod)
    spec.loader.exec_module(mod)
    return mod


sm = _load_claude_metrics()

DENIAL_WORDS = re.compile(r"reject|denied|blocked|not allowed|permission", re.I)
WRITE_KEYS = re.compile(r"write|edit|delete|patch|replace", re.I)
UNAVAILABLE = ["tokens", "cost", "turn_count", "hook_records", "permission_denials"]


def split_call(tool_call: Any) -> tuple[str, Mapping[str, Any], Mapping[str, Any] | None]:
    """(name, args, result) of a ``tool_call`` object: ``{"readToolCall": {args, result}}``."""
    if not isinstance(tool_call, Mapping) or not tool_call:
        return "?", {}, None
    key, body = next(iter(tool_call.items()))
    name = str(key)
    name = name[: -len("ToolCall")] if name.endswith("ToolCall") else name
    if not isinstance(body, Mapping):
        return name, {}, None
    args = body.get("args") if isinstance(body.get("args"), Mapping) else {}
    res = body.get("result") if isinstance(body.get("result"), Mapping) else None
    return name, args, res


def guard_input(name: str, args: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The call as a (tool name, tool_input) pair for the Claude-side detectors, or None."""
    cmd = args.get("command")
    if isinstance(cmd, str):
        return "Bash", {"command": cmd}
    if WRITE_KEYS.search(name):
        for k in ("path", "file_path"):
            v = args.get(k)
            if isinstance(v, str) and v:
                return "Write", {"file_path": v}
    return None


def compute_metrics(
    records: Iterable[Mapping[str, Any]],
    protected: Sequence[str] = (),
    detector: str = "auto",
    root: str | None = None,
    source: str = "",
    bad_lines: int = 0,
) -> dict[str, Any]:
    init: Mapping[str, Any] = {}
    result_rec: Mapping[str, Any] | None = None
    calls: dict[str, dict[str, Any]] = {}  # call_id -> merged started/completed
    order: list[str] = []
    assistant_records = 0
    model_calls: set[str] = set()
    n_records = 0
    n_results = 0
    session_id: Any = None
    for rec in records:
        n_records += 1
        kind = rec.get("type")
        session_id = session_id or rec.get("session_id")
        if kind == "system" and rec.get("subtype") == "init" and not init:
            init = rec
        elif kind == "assistant":
            assistant_records += 1
            if isinstance(rec.get("model_call_id"), str):
                model_calls.add(rec["model_call_id"])
        elif kind == "result":
            result_rec = rec
        elif kind == "tool_call":
            cid = str(rec.get("call_id") or f"anon{len(order)}")
            name, args, res = split_call(rec.get("tool_call"))
            c = calls.get(cid)
            if c is None:
                c = calls[cid] = {"id": cid, "name": name, "args": dict(args), "result": None,
                                  "done": False}  # fmt: skip
                order.append(cid)
            if rec.get("subtype") == "completed":
                c["done"] = True
                c["result"] = res
                if args:
                    c["args"] = dict(args)
    cwd = root or str(init.get("cwd") or ".")
    det = sm.make_detector(detector, protected, cwd)

    entries: list[dict[str, Any]] = []
    denied_ids: list[str] = []
    guard_denies: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for cid in order:
        c = calls[cid]
        res = c["result"]
        text = json.dumps(res, sort_keys=True) if res is not None else ""
        gi = guard_input(c["name"], c["args"])
        target = sm.tool_target(gi[0], gi[1]) if gi else sm.tool_target(c["name"], c["args"])
        if not c["done"]:
            outcome = "unresolved"
        elif isinstance(res, Mapping) and "success" in res:
            outcome = "ok"
        else:
            outcome = "denied" if DENIAL_WORDS.search(text) else "failed"
        entries.append({"id": cid, "name": c["name"], "target": target, "outcome": outcome})
        if outcome == "denied":
            denied_ids.append(cid)
            g = sm.GUARD_DENY.search(text)
            if g:
                guard_denies.append({"tool_use_id": cid, "tool": c["name"],
                                     "protected": g.group("name"), "target": target})  # fmt: skip
        if det is not None and gi is not None:
            hit = det.decide(gi[0], gi[1], cwd)
            if hit:
                status = {"unresolved": "unresolved", "ok": "landed"}.get(outcome, "blocked")
                attempts.append({"tool_use_id": cid, "tool": c["name"], "target": target,
                                 "protected": hit, "status": status})  # fmt: skip
    st = Counter(a["status"] for a in attempts)

    assistant_messages = len(model_calls) if model_calls else assistant_records
    res_out: dict[str, Any] | None = None
    if result_rec is not None:
        n_results = 1
        sub = str(result_rec.get("subtype") or "")
        is_err = bool(result_rec.get("is_error")) or sub.startswith("error")
        res_out = {
            "subtype": sub,
            "is_error": bool(result_rec.get("is_error")),
            "error_kind": "error" if is_err else None,
            "terminal_reason": None,
            "num_turns": assistant_messages,
            "duration_ms": int(sm._num(result_rec.get("duration_ms"))),
            "duration_api_ms": int(sm._num(result_rec.get("duration_api_ms"))),
            "total_cost_usd": 0.0,
            "usage": {k: 0 for k in sm.USAGE_KEYS},
            "model_cost_usd": {},
            "model_cost_usd_total": 0.0,
            "permission_denials": 0,
        }
    by_name = Counter(e["name"] for e in entries)
    return {
        "source": source,
        "host": "cursor",
        "session_id": init.get("session_id") or session_id,
        "model": init.get("model"),
        "records": n_records,
        "bad_lines": bad_lines,
        "turns": {
            "assistant_messages": assistant_messages,
            "assistant_records": assistant_records,
            "subagent_messages": 0,
            "result_num_turns": None,
        },
        "tool_calls": {"total": len(entries), "by_name": dict(sorted(by_name.items()))},
        "tool_uses": entries,
        "denials": {
            "permission_denials": {"count": 0, "by_tool": {}, "items": []},
            "tool_result_errors": {
                "count": len(denied_ids),
                "items": [e for e in entries if e["outcome"] == "denied"],
            },  # fmt: skip
            "unique_tool_uses": len(set(denied_ids)),
            "guard": {
                "count": len(guard_denies),
                "tool_results": guard_denies,
                "hook_responses": 0,
            },  # fmt: skip
        },
        "hooks": {"started": {}, "responses": {}},
        "results_seen": n_results,
        "result": res_out,
        "tokens_in": 0,
        "unavailable": UNAVAILABLE,
        "wall": {"first_timestamp": None, "last_timestamp": None},
        "tamper": {
            "protected": list(protected),
            "detector": det.name if det is not None else None,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "blocked": st["blocked"],
            "landed": st["landed"],
            "unresolved": st["unresolved"],
        },
    }


def metrics_for_file(path: str, **kw: Any) -> dict[str, Any]:
    recs, bad = sm.read_records(path)
    if not recs:
        raise sm.UnreadableInput(f"{path}: no JSON record")
    return compute_metrics(recs, source="<stdin>" if path == "-" else path, bad_lines=bad, **kw)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="host_stream_metrics.py",
        description="Metrics from a cursor-agent --output-format stream-json transcript "
        "(tool calls, failures/denials, tamper attempts; no cost or tokens are exposed).",
    )
    ap.add_argument("files", nargs="*", help="JSONL transcripts; '-' or none reads stdin")
    ap.add_argument("--protected", nargs="+", default=[], metavar="PATH")
    ap.add_argument("--root", help="directory relative paths resolve against (default: init cwd)")
    ap.add_argument("--detector", choices=("auto", "guard", "simple"), default="auto")
    ap.add_argument("--indent", type=int, default=2)
    ns = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        runs = [
            metrics_for_file(p, protected=ns.protected, detector=ns.detector, root=ns.root)
            for p in (ns.files or ["-"])
        ]
    except sm.UnreadableInput as e:
        print(f"host_stream_metrics: {e}", file=sys.stderr)
        return 2
    out: Any = runs[0] if len(runs) == 1 else {"runs": runs, "total": sm.aggregate(runs)}
    print(json.dumps(out, indent=ns.indent or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
