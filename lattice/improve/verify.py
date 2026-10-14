"""``lattice improve verify ID``: did a closed improvement work? (spike 3, second half; no model)

The report ranks signals; the ledger records that the owner closed one. This module answers the
question that closes the loop: after the close, did the signal go away or get cheaper? It reads
the event log and the ledger and writes nothing. The rules are those of
``docs/results/2026-10-13-improve2-prereg.md`` (section 2).

* **Attribution** -- an occurrence belongs to the *before* window when its ``ts`` is earlier than
  the closing point (and later than a previous closing point of the same id), to the *after*
  window when it is later. ``ts`` is parsed with exactly ``%Y-%m-%dT%H:%M:%SZ``; one at the close
  second, or one that is not in that format, is excluded from both and counted. File order is
  irrelevant.
* **Families** -- the unit of an occurrence differs by what produced it. *cost* (``gate:*``, a
  gate run): occurrences continue by nature, so the mean cost per occurrence is compared;
  *level* (a snapshot ``finding_class``): the latest count before against the latest after;
  *occurrence* (a cause, a human intervention, a rejection, a lint): the change should stop
  them, so the verdict is whether they recurred, with the exposure of the after window as the
  denominator (how many things that could have produced one ran).
* **Minimums** -- 3 occurrences in each window (cost), 3 exposures (occurrence), 1 snapshot
  (level); below them the verdict is ``not_enough_data``, never a pass. A cost is ``improved``
  only when it fell by at least 5% and every after value is below every before value.
* **Prediction** -- the ledger stores none, so it is rebuilt from the before window alone with
  the ranker's own proposal (``expected``); only the layer X templates predict anything. The
  error is as spike 3's I-3: ``|measured - predicted| / |predicted|``, both signed.

Exit codes (see :func:`verify`): 0 improved, 1 any other verdict, 3 refused.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from lattice.improve.analyze import (
    Cluster,
    Occurrence,
    _closed_for,
    _proposal,
    occurrences,
)

__all__ = [
    "BLIND_SPOTS",
    "MIN_OCCURRENCES",
    "TS_FORMAT",
    "canonical_ts",
    "exposure_spec",
    "family_of",
    "verify",
    "verify_text",
]

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MIN_OCCURRENCES = 3  # occurrences (cost) or exposures (occurrence) a window needs
MATERIAL = 0.05  # a cost must fall by at least this share to be improved; +-5% is "flat"
MAX_EVIDENCE = 25
_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

BLIND_SPOTS = [
    "confounders: everything else that changed between the two windows is credited to this "
    "change (a new model, a slower machine, a different plan)",
    "small n: 3 against 3 is the floor; full separation of 3 against 3 is p = 0.05 one-sided, "
    "weak evidence, and it is one project's log",
    "the prediction is a heuristic: only the layer X templates predict (X proportional to "
    "repeats); frequency x cost is the cost of a problem, not a predicted saving",
    "Goodhart: hiding a signal (a renamed key, a gate that is not run) reads as improved; "
    "exposure catches 'not exercised', not 'not logged'",
    "unseen: human time, coverage and risk (fewer repeats save seconds and look for fewer races)",
    "timing: the close is when the owner ran the ledger (or the time given with --at), and a "
    "gate run is stamped when it ends",
]


def canonical_ts(ts: Any) -> bool:
    """``ts`` is a string in exactly ``%Y-%m-%dT%H:%M:%SZ`` (so it also sorts as time)."""
    if not isinstance(ts, str) or not _TS_RE.fullmatch(ts):
        return False
    try:
        datetime.strptime(ts, TS_FORMAT)
    except ValueError:
        return False
    return True


def family_of(key: str, occs: Iterable[Occurrence]) -> str:
    """cost (a gate's seconds), level (a snapshot count) or occurrence (the rest)."""
    if key.startswith("gate:"):
        return "cost"
    if any(o.snapshot for o in occs):
        return "level"
    return "occurrence"


def exposure_spec(key: str) -> tuple[str, str]:
    """(event kind, label): what could have produced an occurrence of `key` (the stand-in for
    an exposure field, which no event has)."""
    head = key.partition(":")[0]
    if head == "cause":
        return "node_ended", "node_ended events"
    if head == "human":
        return "plan_started", "plan_started events"
    if key == "change:rejected" or head == "lint":
        return "change", "change events"
    return "gate_run", "gate runs"


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd) + 0.0  # + 0.0: no "-0.0"


def _value(o: Occurrence, unit: str | None) -> float | None:
    return o.seconds if unit == "s" else o.usd if unit == "usd" else None


def _ref(ev: Mapping[str, Any]) -> str:
    return str(ev.get("id") or f"events.jsonl:{ev.get('line')}")


def _refuse(ident: str, reason: str, detail: str) -> tuple[int, dict[str, Any]]:
    return 3, {
        "format": 1,
        "command": "verify",
        "id": ident,
        "refused": True,
        "reason": reason,
        "detail": detail,
    }


def _closing(entries: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], int, Any] | None:
    """(the entry that closed it, how many closed entries in a row, the entry of the earlier
    close, if any) for entries in file order; None when the last one is not ``closed``."""
    runs: list[list[Any]] = []
    cur: list[Any] | None = None
    for e in entries:
        if e.get("status") == "closed":
            if cur is None:
                cur = [e, 0]
                runs.append(cur)
            cur[1] += 1
        else:
            cur = None
    if not entries or entries[-1].get("status") != "closed":
        return None
    first, n = runs[-1]
    return first, n, (runs[-2][0] if len(runs) > 1 else None)


def _merge_by_event(occs: Sequence[Occurrence]) -> list[Occurrence]:
    """Cost family: several matched keys of one gate run are one occurrence (summed)."""
    by: dict[str, Occurrence] = {}
    for o in occs:
        m = by.get(o.ref)
        if m is None:
            by[o.ref] = Occurrence(o.ref, o.ts, 1, o.seconds, o.usd, False, o.x_repeats)
            continue
        if o.seconds is not None:
            m.seconds = (m.seconds or 0.0) + o.seconds
        if o.usd is not None:
            m.usd = (m.usd or 0.0) + o.usd
    return list(by.values())


def _window(ts: str, close: str, prev: str | None) -> str | None:
    if not canonical_ts(ts):
        return "undated"
    if ts == close:
        return "at_close"
    if ts > close:
        return "after"
    if prev is None or ts > prev:
        return "before"
    return None  # at or before an earlier close of the same id: another measurement


def _stats(recs: Sequence[Occurrence], unit: str | None, family: str | None) -> dict[str, Any]:
    vals = [(o.count, v) for o in recs if (v := _value(o, unit)) is not None]
    weight = sum(c for c, _ in vals)
    mean = sum(c * v for c, v in vals) / weight if vals and weight > 0 else None
    if family == "level":
        snaps = [o for o in recs if o.snapshot]
        freq = int(max(snaps, key=lambda o: o.ts).count) if snaps else 0
    elif family == "cost":
        freq = len(recs)
    else:
        freq = sum(o.count for o in recs)
    ordered = sorted(recs, key=lambda o: (o.ts, o.ref))
    return {
        "n": len(recs),
        "frequency": freq,
        "cost_per": mean,
        "min": min((v for _, v in vals), default=None),
        "max": max((v for _, v in vals), default=None),
        "first_ts": ordered[0].ts if ordered else None,
        "last_ts": ordered[-1].ts if ordered else None,
        "exposure": None,
        "evidence": [o.ref for o in ordered][:MAX_EVIDENCE],
    }


def _direction(delta: float | None, rel: float | None) -> str:
    if rel is None:
        return "flat"
    if rel <= -MATERIAL:
        return "down"
    if rel >= MATERIAL:
        return "up"
    return "flat"


def _predict(
    keys: Sequence[str], family: str | None, before_occ: Sequence[Occurrence]
) -> tuple[dict[str, Any] | None, str]:
    """The saving the ranker would have shown at the close, from the before window alone:
    only for the layer X templates, one key, a single x_repeats in the window."""
    if len(keys) > 1:
        return None, "no prediction recorded (the signal matches more than one key)"
    head, _, rest = (keys[0] if keys else "").partition(":")
    role, _, layer = rest.partition(":")
    is_x = head == "gate" and layer == "X" and (role.startswith("level_") or role == "node")
    if family != "cost" or not is_x:
        return None, "no prediction recorded"
    reps = [o.x_repeats for o in before_occ if o.x_repeats]
    counts = sorted((reps.count(r) for r in set(reps)), reverse=True)
    if len(counts) > 1 and counts[0] == counts[1]:
        return None, "no prediction recorded (the before window ran with mixed x_repeats)"
    exp = _proposal(Cluster(keys[0], list(before_occ))).get("expected")
    if not exp:
        return None, "no prediction recorded (the ranker's proposal had none for this window)"
    return {
        "delta": -float(exp["saving_s_per_gate"]),
        "unit": "s",
        "source": "the report's expected saving, rebuilt from the before window alone "
        "(none was recorded)",
        "basis": f"{exp['basis']}; x_repeats {exp['x_repeats_from']} -> {exp['x_repeats_to']}",
    }, ""


def _measure(
    keys: Sequence[str],
    occs: Mapping[str, Sequence[Occurrence]],
    events: Sequence[Mapping[str, Any]],
    close: str,
    prev: str | None,
) -> dict[str, Any]:
    """The windows, the family and the verdict for `keys` (one key or a pattern's keys)."""
    fams = sorted({family_of(k, occs[k]) for k in keys})
    family = fams[0] if len(fams) == 1 else ("mixed" if fams else None)
    pool = [o for k in keys for o in occs[k]]
    if family == "cost":
        pool = _merge_by_event(pool)
    unit: str | None
    if family == "cost":
        unit = "s"
    else:
        unit = "s" if any(o.seconds is not None for o in pool) else None
        if unit is None and any(o.usd is not None for o in pool):
            unit = "usd"
    wins: dict[str, list[Occurrence]] = {"before": [], "after": [], "at_close": [], "undated": []}
    for o in pool:
        w = _window(o.ts, close, prev)
        if w:
            wins[w].append(o)
    before = _stats(wins["before"], unit, family)
    after = _stats(wins["after"], unit, family)
    if family == "occurrence":
        kinds = {exposure_spec(k)[0] for k in keys}
        for name, w in (("before", before), ("after", after)):
            w["exposure"] = sum(
                1
                for ev in events
                if ev.get("kind") in kinds
                and not (ev.get("kind") == "gate_run" and ev.get("reused"))
                and _window(str(ev.get("ts") or ""), close, prev) == name
            )
    out: dict[str, Any] = {
        "family": family,
        "unit": unit,
        "before": before,
        "after": after,
        "excluded": {"at_close": len(wins["at_close"]), "undated": len(wins["undated"])},
        "before_occ": wins["before"],
        "change": {"delta": None, "relative": None, "separated": None},
        "direction": "flat",
    }
    verdict, reason = _decide(family, before, after, wins, out)
    out["verdict"], out["reason"] = verdict, reason
    return out


def _decide(
    family: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
    wins: Mapping[str, Sequence[Occurrence]],
    out: dict[str, Any],
) -> tuple[str, str]:
    nb, na = before["n"], after["n"]
    if family is None:
        return "not_enough_data", "no occurrence of the signal in the log"
    if family == "mixed":
        return "not_enough_data", "the pattern spans families (cost, level, occurrence)"
    if family == "cost":
        if nb < MIN_OCCURRENCES or na < MIN_OCCURRENCES:
            return "not_enough_data", (
                f"needs {MIN_OCCURRENCES} occurrences in each window (before {nb}, after {na})"
            )
        mb, ma = before["cost_per"], after["cost_per"]
        if mb is None or ma is None:
            return "not_enough_data", "no seconds recorded in a window"
        delta = ma - mb
        rel = delta / mb if mb else None
        vb = [o.seconds for o in wins["before"] if o.seconds is not None]
        va = [o.seconds for o in wins["after"] if o.seconds is not None]
        separated = max(va) < min(vb)
        direction = _direction(delta, rel)
        out["change"] = {"delta": delta, "relative": rel, "separated": separated}
        out["direction"] = direction
        if direction == "down" and separated:
            return "improved", "cost fell and every after value is below every before value"
        return "not_improved", {
            "up": "cost rose",
            "flat": "no material change",
            "down": "overlap",
        }[direction]
    if family == "level":
        if nb == 0 or na == 0:
            return "not_enough_data", f"needs a snapshot in each window (before {nb}, after {na})"
        lb, la = before["frequency"], after["frequency"]
        delta = la - lb
        rel = delta / lb if lb else None
        out["change"] = {"delta": delta, "relative": rel, "separated": None}
        out["direction"] = _direction(delta, rel)
        if la < lb:
            return "improved", f"the level fell ({lb} -> {la})"
        return "not_improved", f"the level did not fall ({lb} -> {la})"
    fb, fa, exp = before["frequency"], after["frequency"], after["exposure"] or 0
    delta = fa - fb
    rel = delta / fb if fb else None
    out["change"] = {"delta": delta, "relative": rel, "separated": None}
    out["direction"] = _direction(delta, rel)
    if fb == 0:
        out["change"] = {"delta": None, "relative": None, "separated": None}
        return "not_enough_data", "nothing to verify: none before the close"
    if fa > 0:
        return "recurred", f"{fa} after the close (before {fb}); exposure after {exp}"
    if exp >= MIN_OCCURRENCES:
        return "improved", f"none after the close ({fb} before), exposure after {exp}"
    return "not_enough_data", (
        f"none after the close, but exposure after is {exp} (needs {MIN_OCCURRENCES})"
    )


def _gate_context(
    refs: Iterable[str], by_ref: Mapping[str, Mapping[str, Any]]
) -> dict[str, list[Any]]:
    xs: set[int] = set()
    sel: set[int] = set()
    modes: set[str] = set()
    shas: set[str] = set()
    for r in refs:
        ev = by_ref.get(r)
        if ev is None:
            continue
        if isinstance(ev.get("x_repeats"), int):
            xs.add(ev["x_repeats"])
        if isinstance(ev.get("selected"), int):
            sel.add(ev["selected"])
        if isinstance(ev.get("mode"), str):
            modes.add(ev["mode"])
        if isinstance(ev.get("sha"), str) and ev["sha"]:
            shas.add(ev["sha"][:7])
    return {
        "x_repeats": sorted(xs),
        "selected": sorted(sel),
        "mode": sorted(modes),
        "sha": sorted(shas),
    }


def verify(
    events: Sequence[Mapping[str, Any]],
    ledger: Sequence[Mapping[str, Any]],
    ident: str,
    *,
    at: str | None = None,
    warnings: Sequence[str] = (),
) -> tuple[int, dict[str, Any]]:
    """Verify ledger entry `ident` against the log: ``(exit code, document)``. 0 improved,
    1 any other verdict, 3 refused (``refused: true`` with ``reason`` and ``detail``)."""
    occ_all = occurrences(events)
    occs: dict[str, list[Occurrence]] = {}
    for key, o in occ_all:
        occs.setdefault(key, []).append(o)
    warn = list(warnings)
    # -- the ledger entry
    by_id = [e for e in ledger if isinstance(e.get("id"), str) and e["id"] == ident]
    only_key: str | None = None
    if by_id:
        entries = by_id
    else:
        only_key = next((k for k in sorted(occs) if _proposal(Cluster(k))["id"] == ident), None)
        if only_key is None:
            return _refuse(
                ident,
                "unknown_id",
                f"no ledger entry with id {ident} and no signal in the log with that proposal id",
            )
        c = _closed_for(only_key, ledger)
        if c is None:
            return _refuse(ident, "not_closed", f"no closed ledger entry matches signal {only_key}")
        entries = [e for e in ledger if e["signal"] == c["signal"]]
    closing = _closing(entries)
    if closing is None:
        return _refuse(
            ident, "not_closed", f"the latest ledger entry for {ident} is {entries[-1]['status']}"
        )
    entry, n_closed, earlier = closing
    if at is not None and not canonical_ts(at):
        return _refuse(ident, "bad_at", f"--at must be exactly YYYY-MM-DDTHH:MM:SSZ (got {at!r})")
    ledger_ts = entry.get("ts")
    if at is not None:
        close, source = at, "--at"
        warn.append(f"closing point overridden by --at (ledger ts {ledger_ts or '-'})")
    elif canonical_ts(ledger_ts):
        close, source = str(ledger_ts), "ledger"
    else:
        return _refuse(
            ident,
            "no_closing_point",
            "the closing entry has no ts in the format "
            "YYYY-MM-DDTHH:MM:SSZ (give the time the change took effect with --at)",
        )
    if n_closed > 1:
        warn.append(f"closed {n_closed} times in a row")
    signals = sorted({str(e["signal"]) for e in entries})
    if len(signals) > 1:
        warn.append(
            f"id {ident} has entries for several signals ({', '.join(signals)}); "
            "using the closing entry's"
        )
    if not entry.get("commit"):
        warn.append("no commit recorded")
    prev = earlier.get("ts") if earlier is not None else None
    if not (canonical_ts(prev) and prev < close):
        prev = None
    signal = str(entry["signal"])
    keys = [only_key] if only_key else sorted(k for k in occs if fnmatch.fnmatchcase(k, signal))
    # -- the measurement
    res = _measure(keys, occs, events, close, prev)
    per_key = []
    for k in keys:
        r = _measure([k], occs, events, close, prev)
        per_key.append(
            {
                "key": k,
                "family": r["family"],
                "verdict": r["verdict"],
                "reason": r["reason"],
                "n_before": r["before"]["n"],
                "n_after": r["after"]["n"],
                "frequency_before": r["before"]["frequency"],
                "frequency_after": r["after"]["frequency"],
                "cost_per_before": _r(r["before"]["cost_per"]),
                "cost_per_after": _r(r["after"]["cost_per"]),
                "delta": _r(r["change"]["delta"]),
            }
        )
    verdict = res["verdict"]
    # -- prediction and error
    predicted, note = _predict(keys, res["family"], res["before_occ"])
    error = None
    ch = res["change"]
    if predicted and verdict != "not_enough_data" and ch["delta"] is not None:
        pd = predicted["delta"]
        if pd:
            error = abs(ch["delta"] - pd) / abs(pd)
    # -- context
    by_ref = {_ref(ev): ev for ev in events if ev.get("kind") == "gate_run"}
    per_window: dict[str, Any] = {}
    if res["family"] == "cost":
        win_refs: dict[str, list[str]] = {"before": [], "after": []}
        for o in (o for k in keys for o in occs[k]):
            w = _window(o.ts, close, prev)
            if w in win_refs:
                win_refs[w].append(o.ref)
        per_window = {w: _gate_context(r, by_ref) for w, r in win_refs.items()}
    b, a = res["before"], res["after"]
    stamps = [t for t in (b["first_ts"], a["last_ts"]) if t]
    own = {id(e) for e in entries}
    others = []
    if len(stamps) == 2:
        for e in ledger:
            if id(e) in own or e.get("status") != "closed" or not canonical_ts(e.get("ts")):
                continue
            if stamps[0] <= e["ts"] <= stamps[1]:
                others.append(
                    {
                        "id": e.get("id"),
                        "signal": e["signal"],
                        "ts": e["ts"],
                        "commit": e.get("commit"),
                    }
                )
    doc: dict[str, Any] = {
        "format": 1,
        "command": "verify",
        "id": ident,
        "entry": {
            "id": entry.get("id"),
            "signal": signal,
            "status": entry.get("status"),
            "commit": entry.get("commit"),
            "ts": ledger_ts,
        },
        "closing_point": {"ts": close, "source": source},
        "keys": keys,
        "family": res["family"],
        "unit": res["unit"],
        "verdict": verdict,
        "reason": res["reason"],
        "direction": res["direction"],
        "before": _window_doc(b),
        "after": _window_doc(a),
        "change": {
            "delta": _r(ch["delta"]),
            "relative": _r(ch["relative"], 3),
            "separated": ch["separated"],
        },
        "predicted": predicted,
        "prediction_note": note,
        "error": _r(error, 3),
        "excluded": res["excluded"],
        "context": {"other_closes": others, "per_window": per_window},
        "per_key": per_key,
        "blind_spots": list(BLIND_SPOTS),
        "warnings": warn,
    }
    if predicted:
        doc["predicted"] = {**predicted, "delta": _r(predicted["delta"], 1)}
    return (0 if verdict == "improved" else 1), doc


def _window_doc(w: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(w)
    for k in ("cost_per", "min", "max"):
        out[k] = _r(out[k])
    return out


def _cost(v: float | None, unit: str | None) -> str:
    if v is None:
        return "-"
    return f"${v:.2f}" if unit == "usd" else f"{v:.2f} s" if unit == "s" else f"{v:.2f}"


def _win_line(w: Mapping[str, Any], unit: str | None, label: str) -> str:
    parts = [f"n {w['n']}", f"f {w['frequency']}"]
    if w["cost_per"] is not None:
        parts.append(
            f"mean {_cost(w['cost_per'], unit)} (min {_cost(w['min'], unit)}, "
            f"max {_cost(w['max'], unit)})"
        )
    else:
        parts.append("mean -")
    if w["exposure"] is not None:
        parts.append(f"exposure {w['exposure']}")
    if w["first_ts"]:
        parts.append(f"{w['first_ts']} .. {w['last_ts']}")
    return f"{label}: " + ", ".join(parts)


def verify_text(doc: Mapping[str, Any]) -> str:
    """The verification for a terminal (a refusal is printed by the CLI on stderr)."""
    e, cp = doc["entry"], doc["closing_point"]
    unit = doc["unit"]
    lines = [
        f"Lattice improve verify: {doc['id']} ({e['signal']}), closed {cp['ts']}, "
        f"commit {e.get('commit') or '-'}",
        "",
        f"Verdict: {doc['verdict']} ({doc['reason']})",
        f"Family: {doc['family'] or '-'}"
        + (f" (unit {unit})" if unit else "")
        + f"; keys: {', '.join(doc['keys']) or '-'}",
        _win_line(doc["before"], unit, "Before (ts < close)"),
        _win_line(doc["after"], unit, "After (ts > close)"),
    ]
    ch = doc["change"]
    if ch["delta"] is None:
        lines.append("Change: -")
    else:
        rel = f"{ch['relative']:+.1%}, " if ch["relative"] is not None else ""
        what = (
            f"{ch['delta']:+.2f} s"
            if doc["family"] == "cost" and unit == "s"
            else f"{ch['delta']:+g}"
        )
        sep = ""
        if ch["separated"] is not None:
            sep = f"; every after below every before: {'yes' if ch['separated'] else 'no'}"
        lines.append(f"Change: {what} ({rel}{doc['direction']}){sep}")
    p = doc["predicted"]
    if p:
        lines.append(f"Predicted: {p['delta']:+.1f} {p['unit']} ({p['source']}; {p['basis']})")
    else:
        lines.append(f"Predicted: {doc['prediction_note']}")
    err = doc["error"]
    lines.append(
        "Error: |measured - predicted| / |predicted| = "
        + (f"{err:.1%}" if err is not None else "-")
    )
    ex = doc["excluded"]
    lines.append(f"Excluded: at the close second {ex['at_close']}, undated {ex['undated']}")
    ctx = doc["context"]
    pw = ctx["per_window"]
    bits = []
    for field in ("x_repeats", "selected", "mode", "sha"):
        if pw:
            bits.append(
                f"{field} {','.join(map(str, pw['before'][field])) or '-'} -> "
                f"{','.join(map(str, pw['after'][field])) or '-'}"
            )
    others = ctx["other_closes"]
    if others:
        bits.append(
            "other closes in between: "
            + ", ".join(f"{o.get('id') or '-'} ({o['signal']}) {o['ts']}" for o in others)
        )
    lines.append("Context: " + ("; ".join(bits) if bits else "-"))
    for name in ("before", "after"):
        ev = doc[name]["evidence"]
        lines.append(
            f"Evidence ({name}): {', '.join(ev[:6]) or '-'}" + (" ..." if len(ev) > 6 else "")
        )
    if len(doc["per_key"]) > 1:
        lines.append("Per key:")
        for pk in doc["per_key"]:
            lines.append(
                f"  - {pk['key']}: {pk['verdict']} (n {pk['n_before']}/{pk['n_after']}, "
                f"{pk['cost_per_before']} -> {pk['cost_per_after']})"
            )
    lines.append("Blind spots:")
    lines += [f"  - {b}" for b in doc["blind_spots"]]
    for w in doc["warnings"]:
        lines.append(f"warning: {w}")
    return "\n".join(lines) + "\n"
