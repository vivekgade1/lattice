"""``lattice improve draft ID``: turn one open proposal into a document the owner can act on
(spike 3, second half; no model, no network).

A draft is *text*: a change request, a ``studio.toml`` snippet, a module skeleton or a
pre-registration stub, filled in only from what the log, the ledger and ``studio.toml`` hold.
Nothing is applied: the command never writes a project file, the log or the ledger (the one
write is ``--out``, refused inside the project or the state directory), and the same inputs
give the same bytes (the only time in a draft is the log's latest ``ts``). A proposal that
touches the checker or the spec language (``needs_owner``) or is already closed is refused.
The rules are those of ``docs/results/2026-10-13-improve2-prereg.md`` (section 2.8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from lattice.improve.analyze import BLIND_SPOTS, Cluster, _fmt, _proposal, clusters
from lattice.improve.verify import MIN_OCCURRENCES, canonical_ts, exposure_spec, family_of

__all__ = ["draft"]

NOT_APPLIED = "NOT APPLIED: this draft changes nothing; the owner decides."

# causes whose fix is one knob of [rebuild]; the value is the owner's
KNOBS = {
    "cause:timeout": "node_timeout",
    "cause:token_budget": "node_max_tokens",
    "cause:usage_limit": "max_parallel",
}


def _refuse(ident: str, reason: str, detail: str) -> tuple[int, dict[str, Any]]:
    return 3, {
        "format": 1,
        "command": "draft",
        "id": ident,
        "refused": True,
        "reason": reason,
        "detail": detail,
    }


def _literal(key: str, p: Mapping[str, Any]) -> tuple[str, str, Any] | None:
    """(setting, op, value) for the proposals whose change is a fixed literal of the template
    table (all in ``[rebuild]``); None when the owner has to decide it."""
    if p["kind"] != "change_set":
        return None
    head, _, rest = key.partition(":")
    role, _, layer = rest.partition(":")
    if head == "gate" and layer == "X" and role.startswith("level_"):
        return "level_gate_args", "=", ["--x-repeats", "2"]
    if head == "gate" and layer == "X" and role == "node":
        return "scoped_gate_args", "+=", ["--x-repeats", "1"]
    if head == "gate" and layer == "D" and role == "node":
        return "scoped_gate_args", "+=", ["--await-cap", "5"]
    if head == "gate" and layer == "D" and role in ("level_regression", "level_full"):
        return "level_gate_args", "+=", ["--await-cap", "5"]
    if key == "human:spec_sync":
        return "sync_spec", "=", "auto"
    return None


def _show(v: Any) -> str:
    return json.dumps(v) if isinstance(v, str | list) else repr(v)


def _signal_line(p: Mapping[str, Any]) -> str:
    if p["score"] is not None:
        sc = f"{p['frequency']} x {_fmt(p['cost_per'], p['unit'])} = {_fmt(p['score'], p['unit'])}"
    else:
        sc = f"f = {p['frequency']} (no cost recorded)"
    return f"signal {p['signal_key']}: {sc} [{p['score_class']}]"


def _evidence(c: Cluster, p: Mapping[str, Any]) -> str:
    lines = [_signal_line(p)]
    for o in c.occ[:25]:
        v = o.seconds if p["unit"] == "s" else o.usd if p["unit"] == "usd" else None
        lines.append(f"{o.ref} {o.ts} {_fmt(v, p['unit'])}")
    return "\n".join(lines)


def _minimum(c: Cluster) -> str:
    fam = family_of(c.key, c.occ)
    if fam == "cost":
        return (
            f"minimum {MIN_OCCURRENCES} occurrences after the close and {MIN_OCCURRENCES} before "
            "(the mean cost per occurrence is compared)"
        )
    if fam == "level":
        return "minimum 1 snapshot after the close (the latest level is compared)"
    label = exposure_spec(c.key)[1]
    return (
        f"minimum {MIN_OCCURRENCES} exposures ({label}) after the close (the change should stop "
        "the signal; exposure says how often it could have occurred)"
    )


def _measure(c: Cluster, pid: str) -> tuple[str, str, str]:
    ledger_cmd = (
        f"lattice improve ledger --signal {c.key} --status closed --id {pid} --commit <SHA>"
    )
    verify_cmd = f"lattice improve verify {pid}"
    body = (
        "Record the close when the change lands:\n\n"
        f"    {ledger_cmd}\n\n"
        "(run it when the change lands, before the runs that measure it), then, once the "
        "runs exist:\n\n"
        f"    {verify_cmd}\n\n"
        f"{_minimum(c)}."
    )
    return body, ledger_cmd, verify_cmd


def _gates(cfg: Mapping[str, Any]) -> list[str]:
    g = (cfg.get("rebuild") or {}).get("gates")
    if isinstance(g, str):
        return [g]
    if isinstance(g, list):
        return [str(x) for x in g]
    return []


NOT_DECIDED = (
    "- cost to fix: not estimated (the ranker does not take it as an input)\n"
    "- risk: not assessed (lost coverage or a wrong answer costs no seconds in the log)\n"
    "- human time: not counted (review, diagnosis and merging are not in the log)"
)


def draft(
    events: Sequence[Mapping[str, Any]],
    ledger: Sequence[Mapping[str, Any]],
    ident: str,
    cfg: Mapping[str, Any],
    *,
    out_inside: bool = False,
) -> tuple[int, dict[str, Any]]:
    """The draft of proposal `ident`: ``(exit code, document)``; 0 produced, 3 refused.
    `cfg` is the parsed ``studio.toml`` ({} when absent); `out_inside` says ``--out`` is inside
    the project or the state directory (refused first, so nothing is written)."""
    if out_inside:
        return _refuse(
            ident, "out_in_project", "--out is inside the project or the state directory"
        )
    found = None
    for c in clusters(events, ledger):
        p = _proposal(c)
        if p["id"] == ident:
            found = (c, p)
            break
    if found is None:
        return _refuse(ident, "unknown_id", f"no proposal {ident} in the log")
    c, p = found
    if c.closed is not None:
        return _refuse(
            ident,
            "closed",
            f"closed by {c.closed.get('id') or '-'} at {c.closed.get('ts') or '-'}",
        )
    if p["status"] == "needs_owner":
        return _refuse(ident, "needs_owner", p["why"])
    as_of = max((str(e["ts"]) for e in events if canonical_ts(e.get("ts"))), default="-")
    rebuild = cfg.get("rebuild") if isinstance(cfg.get("rebuild"), dict) else {}
    key = c.key
    kind = p["kind"]
    literal = _literal(key, p)
    measure, ledger_cmd, verify_cmd = _measure(c, ident)
    gates = _gates(cfg)
    gate_body = (
        "Run before accepting: " + ", ".join(f"`{g}`" for g in gates)
        if gates
        else "the owner names the gate (no [rebuild] gates in studio.toml)"
    )
    evidence = _evidence(c, p)
    toml: str | None = None
    sections: list[tuple[str, str]] = []
    if kind == "module_draft":
        template = "module"
        toml = "\n".join(
            [
                f"name = {json.dumps(key.partition(':')[2])}",
                'version = "0.0.0"',
                'kind = "custom"',
                f"description = {json.dumps(p['title'])}",
                "provides = []",
                "requires = []",
            ]
        )
        sections = [
            (
                "Module skeleton",
                f"`lattice-module.toml` (a skeleton; the owner fills it):\n\n```toml\n{toml}\n```",
            ),
            (
                "What it must provide",
                f"{p['title']}\n\nWhy: {p['why']}\n\n"
                f"Targets: {', '.join(p['target_paths']) or '-'}",
            ),
            ("Evidence", evidence),
            ("Acceptance gate", gate_body),
            ("Measure afterwards", measure),
            ("Not decided here", NOT_DECIDED),
        ]
    elif kind == "experiment":
        template = "prereg"
        sections = [
            (
                "1. Status",
                f"written BEFORE any probe. As of {as_of}. Nothing of the change exists yet.",
            ),
            ("2. Question", f"{p['title']}\n\nWhy this is asked: {p['why'] or '-'}"),
            ("3. Signal and evidence", evidence),
            (
                "4. Probe",
                "The owner writes the probe: what is run, on which input, how many times.\n"
                f"Targets: {', '.join(p['target_paths']) or '-'}",
            ),
            (
                "5. Pass rule and blind prediction",
                "Pass rule: the owner writes it before any probe runs.\n\nPREDICT:\nPREDICT:",
            ),
            ("6. Measure afterwards", measure),
            ("7. Blind spots", "\n".join(f"- {b}" for b in BLIND_SPOTS)),
        ]
    else:
        template = "config" if literal else "change_request"
        what = [p["title"], "", f"Targets: {', '.join(p['target_paths']) or '-'}"]
        if literal:
            name, op, value = literal
            cur = rebuild.get(name)
            new = value
            note = ""
            if op == "+=" and isinstance(cur, list):
                new = [*cur, *value]
            elif cur is not None and cur == value:
                note = " (already set: nothing to change)"
            elif cur is not None:
                note = " (replaced by the line above)"
            toml = f"[rebuild]\n{name} = {_show(new)}"
            cur_line = (
                f"current: {name} = {_show(cur)}{note}" if cur is not None else ("current: not set")
            )
            what = [
                "Edit `studio.toml` in the project:",
                "",
                f"```toml\n{toml}\n```",
                "",
                cur_line,
            ]
        elif key in KNOBS:
            cur = rebuild.get(KNOBS[key])
            what += [
                f"Knob: [rebuild] {KNOBS[key]} in studio.toml; value: the owner decides",
                f"current: {KNOBS[key]} = {_show(cur)}" if cur is not None else "current: not set",
            ]
        sections = [("What to change", "\n".join(what))]
        e = p.get("expected")
        if literal and e:
            sections.append(
                (
                    "Expected",
                    f"expected: X {e['x_seconds_now']} s -> saving {e['saving_s_per_gate']} s a "
                    f"gate ({e['drop']:.0%}; x_repeats {e['x_repeats_from']} -> "
                    f"{e['x_repeats_to']})",
                )
            )
        sections += [
            ("Why", p["why"] or "(the template gives no reason)"),
            ("Evidence", evidence),
            ("Acceptance gate", gate_body),
            ("Measure afterwards", measure),
            ("Not decided here", NOT_DECIDED),
        ]
    head = (
        f"# {p['title']}: pre-registration (stub)"
        if template == "prereg"
        else (f"# Draft: {p['title']}")
    )
    parts = [
        head,
        f"Proposal {ident} ({kind}), signal {key}; status open. As of {as_of}.",
        NOT_APPLIED,
    ]
    parts += [f"## {h}\n\n{b}" for h, b in sections]
    text = "\n\n".join(parts) + "\n"
    return 0, {
        "format": 1,
        "command": "draft",
        "proposal": p,
        "as_of": as_of,
        "template": template,
        "sections": [{"heading": h, "body": b} for h, b in sections],
        "toml": toml,
        "ledger_command": ledger_cmd,
        "verify_command": verify_cmd,
        "text": text,
    }
