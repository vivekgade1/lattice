# ruff: noqa: E501
"""READER of the round-3 typed-conditions runner (docs/results/2026-10-18-typed-conditions-round3-prereg.md, D3).

Turns the files of a rule-shape set into the plain records the scorer (``dogfood/forms_probes3.py``) reads.
It computes no metric and no verdict, drops, merges and re-labels no record, and imports nothing from the
scorer. Three layouts are read:

* ``r1``  the round-1 set (sides ``a`` / ``b`` in pairs, ``entry`` / ``entries`` in invalid, truth as a list),
* ``h2``  the round-2 set (forms as ``{id, rule, kind, form}``, truth app -> rule, mutants as ``{kind, edit}``),
* ``h3``  the round-3 set (layout of its README.md: form entries everywhere, truth rule -> app, mutants as a
  list, pairs with a ``handwritten`` side, ``attempt`` as an entry).

``read(shapes, layout)`` returns::

    {layout, handwritten: Path, trace_json: Path, requirements: Path, forms_json: Path,
     forms: [entry], truth: [(rule, app, violates)], rule_text: {rule: text},
     mutants: [{name, edit: (file, old, new), tags}],
     invalid: [{id, code, also, mode ("replace" | "append"), entries, extra_scenarios}],
     pairs: [{id, class, expect, entries, named, extra, witness, path_or_query_bound}],
     not_expressible: [{id, attempt: {kind, form} | None, expect}]}

An app name ``sec:<v>`` is read as ``sec_<v>`` (the name the scorer builds the app under; h2 already writes it so,
the round-1 set writes ``sec:<v>``); nothing else is renamed. A reader change after a set has been opened is allowed only under D3 (2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LAYOUTS = ("r1", "h2", "h3")


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _as_list(doc: Any, key: str) -> list[Any]:
    """A file that is a list (h2) or an object holding the list under ``key``."""
    return doc if isinstance(doc, list) else doc[key]


def _truth(cells: Any) -> list[tuple[str, str, bool]]:
    """(rule, app, violates) from a list of {rule, app, violates}, or a nested object keyed by rule then app
    (h3) or by app then rule (h2): the orientation is read from the keys (rule ids are ``R<n>``)."""
    app_ = lambda a: a.replace("sec:", "sec_", 1)  # noqa: E731
    if isinstance(cells, list):
        return [(c["rule"], app_(c["app"]), bool(c["violates"])) for c in cells]
    out = []
    for k1, inner in cells.items():
        for k2, c in inner.items():
            rule, app = (k1, k2) if re.fullmatch(r"R\d+", k1) else (k2, k1)
            out.append((rule, app_(app), bool(c["violates"] if isinstance(c, dict) else c)))
    return out


def _entry_h2(x: dict[str, Any], rule_text: dict[str, str]) -> dict[str, Any]:
    """A form record from h2's {id, rule, kind, form}: approved, anchored on the rule's text when known."""
    from lattice.spec.forms import form_sha
    from lattice.spec.rationale import anchor_of

    return {
        "id": x["id"],
        "rule": x["rule"],
        "anchor": anchor_of(rule_text[x["rule"]]) if x["rule"] in rule_text else "0" * 64,
        "kind": x["kind"],
        "form": x["form"],
        "status": "approved",
        "author": "hand",
        "approved_by": "runner",
        "approved_sha": form_sha(x["kind"], x["form"]),
    }


def _bare_entry(fid: str, kind: str, form: Any) -> dict[str, Any]:
    return {
        "id": fid, "rule": "R1", "anchor": "0" * 64, "kind": kind, "form": form,
        "status": "approved", "author": "hand", "approved_sha": "x",
    }  # fmt: skip


def _mutants(layout: str, doc: Any) -> list[dict[str, Any]]:
    if layout == "h3":
        return [
            {"name": m["name"], "edit": (m["edit"]["file"], m["edit"]["old"], m["edit"]["new"]), "tags": list(m.get("tags", ()))}
            for m in doc["mutants"]
        ]  # fmt: skip
    out = []
    for k, v in doc.items():
        e = v["edit"] if isinstance(v, dict) else v
        out.append({"name": k, "edit": (e[0], e[1], e[2]), "tags": []})
    return out


def _invalid(
    layout: str, doc: Any, forms: list[dict[str, Any]], rule_text: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for x in _as_list(doc, "invalid"):
        if layout == "h3":
            mode, entries = "replace", list(x["forms"])
        elif "entries" in x:
            mode, entries = "replace", list(x["entries"])
        elif "entry" in x:
            mode, entries = "append", [x["entry"]]
        else:
            mode, entries = "append", [_entry_h2(x, rule_text)]
        out.append({
            "id": x["id"], "code": x.get("code", x.get("expect")),
            "also": list(x.get("also_acceptable", ())), "mode": mode, "entries": entries,
            "extra_scenarios": list(x.get("spec_extra_scenarios") or ()),
        })  # fmt: skip
    return out


def _pairs(
    layout: str, doc: Any, entries: dict[str, dict[str, Any]], rule_text: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for p in _as_list(doc, "pairs"):
        ents: list[dict[str, Any]] = []
        named: list[str] = []
        extra: list[dict[str, Any]] = []
        if layout == "h3":
            ents = list(p["forms"])
            hw = p.get("handwritten")
            if hw:
                extra.append({"name": hw["name"], **hw["scenario"]})
                named.append(f"scenario:{hw['name']}")
        elif "forms" in p:  # h2: a form record or the name of a hand-written scenario
            for s in p["forms"]:
                if isinstance(s, str):
                    named.append(s)
                else:
                    ents.append(_entry_h2(s, rule_text))
        else:  # r1: sides a / b
            for side, fid in (("a", "PA"), ("b", "PB")):
                s = p[side]
                if "ref" in s:
                    ents.append(entries[s["ref"]])
                elif "scenario" in s:
                    extra.append(s["scenario"])
                else:
                    ents.append(_bare_entry(fid, s["kind"], s["form"]))
        out.append({
            "id": p["id"], "class": p["class"], "expect": p["expect"], "entries": ents,
            "named": named, "extra": extra, "witness": p.get("witness"),
            "path_or_query_bound": p.get("path_or_query_bound"),
        })  # fmt: skip
    return out


def read(shapes: Path, layout: str) -> dict[str, Any]:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    ms = shapes / "minishop"
    raw = _load(ms / "forms.json")
    entries = {e["id"]: e for e in raw["forms"]}
    truth_doc = _load(ms / "truth.json")
    rules = truth_doc.get("rules")
    rule_text = dict(rules) if isinstance(rules, dict) else {}
    ne = []
    for x in _as_list(_load(shapes / "not_expressible.json"), "rules"):
        a = x.get("attempt")
        ne.append({
            "id": x["id"],
            "attempt": {"kind": a["kind"], "form": a["form"]} if a else None,
            "expect": x.get("expect_any_of", (a or {}).get("expect_codes")),
        })  # fmt: skip
    return {
        "layout": layout,
        "handwritten": ms / "handwritten.py",
        "trace_json": ms / "handwritten.trace.json",
        "requirements": ms / "REQUIREMENTS.md",
        "forms_json": ms / "forms.json",
        "forms": list(raw["forms"]),
        "truth": _truth(truth_doc["cells"]),
        "rule_text": rule_text,
        "mutants": _mutants(layout, _load(ms / "mutants.json")),
        "invalid": _invalid(layout, _load(shapes / "invalid.json"), raw["forms"], rule_text),
        "pairs": _pairs(layout, _load(shapes / "pairs.json"), entries, rule_text),
        "not_expressible": ne,
    }
