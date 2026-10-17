"""`lattice.spec.impact_report`: the change-impact report (docs/results/2026-10-16-change-impact-
prereg.md, D1-D8; CLI ``lattice spec impact``).

When the spec changes, which code does it touch and which checks and workflows fail? The report
answers in tiers, and says which tier each answer is:

* **T1 observed** -- the candidate spec (plus each verified flow of the inventory, frozen as a
  scenario) runs on today's app through layers A, C and D on a throwaway database; every finding
  is a fact. The base runs the same way, so a finding is marked ``also_fails_on_base`` and a base
  finding the candidate no longer has is ``resolved``.
* **T2 determined** -- :mod:`lattice.spec.impact_tier2`: what follows from the spec change and the
  steps observed to pass, without running the candidate.
* **T3 static** -- :mod:`lattice.spec.impact_tier3`: the code locations the change touches, read
  from the inventory's extract; sound but incomplete, with the unknowns listed.
* **T4 observed after the change** -- Spike E's; the slot is kept, ``not_run:spike_e``.

**Which workflows fail is mostly not decidable before the change exists.** A flow records the
answers the app gave *today*, so it passes today by construction. Before an implementation only a
changed invariant (T1) and an exact-prefix contradiction (T2) decide a workflow; the rest is T3
``may_fail``, settled by Spike E's tier 4.

The engine reads files and runs the app's own check; it writes nothing of the project, the
spec, the inventory or the state directory, never opens ``StateStore``, and calls no model. The
report (``lattice.impact/1``) is canonical JSON: sorted keys and arrays, relative ``/`` paths,
no absolute path, time, duration, host or port (timings go to stderr).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lattice.spec import impact_tier2, impact_tier3
from lattice.spec.impact_tier2 import SpecLines
from lattice.spec.incremental import RuleDiff, changed_targets, diff_rules, label_impact
from lattice.spec.model import Spec
from lattice.spec.rules import Trace

SCHEMA = "lattice.impact/1"
TIERS = ("1", "2", "3", "4")
SECTIONS = ("summary", "tier1", "tier2", "tier3", "flows", "unread", "decidability")
NOTE_WORKFLOWS = (
    "Which workflows fail is mostly not decidable before the change exists: a flow records the "
    "answers the app gives today, so it passes today by construction. Before an implementation "
    "only a changed invariant (tier 1, observed) and an exact-prefix contradiction (tier 2, "
    "determined) decide a workflow; every other flow listed is tier 3 'may_fail' (static, with "
    "unknowns), settled by tier 4 (Spike E, observed after the change)."
)
OWNER_ENV = "LATTICE_CONTAINER_OWNER"


class ImpactError(Exception):
    """An input the report cannot be built from. `code`: ``change_not_ready``,
    ``no_candidate``, ``inventory_missing``, ``bad_spec``, ``bad_inputs``, ``no_app``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


# ---- inputs (D1) ----------------------------------------------------------------------------


def _sha(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Side:
    """One side of the change: the requirements text (or None), the spec module and its trace."""

    markdown: str | None
    module: str
    trace_text: str
    spec: Spec
    trace: Trace
    forms: Path | None = None  # Spike H: the approved forms file beside the requirements

    def hashes(self) -> dict[str, str | None]:
        return {
            "requirements": _sha(self.markdown),
            "spec": _sha(self.module),
            "trace": _sha(self.trace_text),
        }


def make_side(markdown: str | None, module: str, trace_text: str | None) -> Side:
    from lattice.spec.draft import load_spec

    spec, errors = load_spec(module)
    if spec is None:
        raise ImpactError("bad_spec", "spec.py does not load: " + "; ".join(errors))
    text = trace_text if trace_text is not None else Trace(source="", rules=[]).to_json()
    try:
        trace = Trace.from_json(text)
    except (ValueError, KeyError, TypeError) as err:
        raise ImpactError("bad_spec", f"spec.trace.json is not a trace ({err})") from None
    return Side(markdown, module, text, spec, trace)


def read_side(directory: Path) -> Side:
    """The three files of a project directory (REQUIREMENTS.md may be missing)."""
    d = Path(directory)

    def read(name: str, required: bool) -> str | None:
        try:
            return (d / name).read_text(encoding="utf-8")
        except OSError:
            if required:
                raise ImpactError("bad_inputs", f"{d.name}/{name} cannot be read") from None
            return None

    module = read("spec.py", True)
    assert module is not None
    side = make_side(read("REQUIREMENTS.md", False), module, read("spec.trace.json", False))
    side.forms = next(iter(sorted(d.glob("*.forms.json"))), None)
    return side


def forms_items(base: Side, cand: Side) -> Callable[[], list[dict[str, Any]]] | None:
    """Spike H: the candidate's form contradictions that the base's lack, as Tier 2 items; None
    (the report unchanged) when neither side has a forms file."""
    if base.forms is None and cand.forms is None:
        return None
    from lattice.spec import forms as F
    from lattice.spec import forms_tier2

    def items(side: Side) -> list[dict[str, Any]]:
        if side.forms is None:
            return []
        try:
            usable = F.usable(F.resolve(side.trace, F.load_forms_file(side.forms).forms))
            return forms_tier2.contradictions(side.spec, usable)
        except F.FormsError:
            return []

    def key(it: dict[str, Any]) -> str:
        return F.canon([it["class"], it["forms"], it["witness"]])

    had = {key(i) for i in items(base)}
    return lambda: [i for i in items(cand) if key(i) not in had]


def read_change(state_dir: Path, cid: str) -> tuple[Side, Side]:
    """(base, candidate) of a Studio change record, read as a plain file (``StateStore`` is never
    opened: opening it writes counters). The record must be ``ready`` or ``accepted``."""
    if not re.fullmatch(r"c\d+", cid):
        raise ImpactError("no_candidate", f"{cid!r} is not a change id (cN)")
    path = Path(state_dir) / "changes" / f"{cid}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise ImpactError("no_candidate", f"no change record {cid}") from None
    except ValueError as err:
        raise ImpactError("bad_inputs", f"{cid}.json is not valid JSON ({err})") from None
    rec = doc.get("record") if isinstance(doc, dict) else None
    priv = doc.get("private") if isinstance(doc, dict) else None
    if not isinstance(rec, dict) or not isinstance(priv, dict):
        raise ImpactError("bad_inputs", f"{cid}.json is not a state record")
    if rec.get("status") not in ("ready", "accepted"):
        raise ImpactError("change_not_ready", f"{cid} is {rec.get('status')!r}, not ready")
    base, new_module = priv.get("base"), priv.get("new_module")
    if not isinstance(base, dict) or not isinstance(new_module, str):
        raise ImpactError("change_not_ready", f"{cid} has no drafted candidate yet")
    new_md = priv.get("new_md")
    return (
        make_side(base.get("markdown"), str(base["module"]), base.get("trace")),
        make_side(new_md if isinstance(new_md, str) else None, new_module, priv.get("new_trace")),
    )


@dataclass
class Inputs:
    base: Side
    cand: Side
    kind: str  # candidate | change
    change: str | None
    inventory: Any  # lattice.spec.implementation.Inventory | None
    inventory_error: str | None
    project: Path | None
    notes: list[str]

    @property
    def diff(self) -> RuleDiff:
        if self.base.markdown is None or self.cand.markdown is None:
            return _no_diff(self.base.trace, self.cand.trace)
        return diff_rules(self.base.markdown, self.cand.markdown)


def _no_diff(base: Trace, cand: Trace) -> RuleDiff:
    """An empty rule diff (no REQUIREMENTS.md on a side): every rule counts as unchanged."""
    same = [(r.id, r.id) for r in cand.rules if any(o.id == r.id for o in base.rules)]
    return RuleDiff(list(base.rules), list(cand.rules), unchanged=same)


def load_inventory_for(
    project: Path | None, inventory: Path | str | None
) -> tuple[Any, str | None]:
    """(inventory, error). The inventory is the argument, else the project's
    ``[implementation]`` table; Spike C's :func:`load_inventory` (it re-hashes the source set)."""
    from lattice.spec import implementation as impl

    cfg = impl.implementation_config(project) if project is not None else None
    path = Path(inventory) if inventory is not None else (cfg or {}).get("inventory")
    if path is None:
        return None, "inventory_missing: no [implementation] inventory and no --inventory"
    app_root = (cfg or {}).get("app_root") or project or Path(".")
    package = (cfg or {}).get("package")
    try:
        return impl.load_inventory(path, app_root, package), None
    except impl.ImplementationError as err:
        return None, str(err)


def build_inputs(
    *,
    project: Path | None,
    candidate: Path | None = None,
    change: str | None = None,
    state_dir: Path | None = None,
    inventory: Path | str | None = None,
    base_side: Side | None = None,
    cand_side: Side | None = None,
) -> Inputs:
    notes: list[str] = []
    kind = "candidate" if candidate is not None or cand_side is not None else "change"
    if base_side is not None and cand_side is not None:
        base, cand = base_side, cand_side
    elif candidate is not None:
        if project is None:
            raise ImpactError("bad_inputs", "a project directory is needed")
        base, cand = read_side(project), read_side(candidate)
    elif change is not None:
        if project is None:
            raise ImpactError("bad_inputs", "a project directory is needed")
        sd = state_dir if state_dir is not None else default_state_dir(project)
        base, cand = read_change(sd, change)
    else:
        raise ImpactError("no_candidate", "give a candidate directory or a change id")
    if base.markdown is None or cand.markdown is None:
        notes.append("no REQUIREMENTS.md on a side: the rule diff is empty")
    inv, err = load_inventory_for(project, inventory)
    return Inputs(base, cand, kind, change, inv, err, project, notes)


def default_state_dir(project: Path) -> Path:
    """The project's state directory with no side effect (``state_dir_path``)."""
    from lattice.cli import STUDIO_TOML, _read_toml, project_name
    from lattice.studio.state import state_dir_path

    p = Path(project).resolve()
    toml = _read_toml(p / STUDIO_TOML) if (p / STUDIO_TOML).is_file() else {}
    st = toml.get("state") if isinstance(toml.get("state"), dict) else None
    rb = toml.get("rebuild") if isinstance(toml.get("rebuild"), dict) else None
    return state_dir_path(p, project_name(p, toml), st, rb)


# ---- the running of a check (T1) ------------------------------------------------------------

Runner = Callable[[Spec, str], Any]  # (spec, layers) -> lattice.spec.report.Report


@contextlib.contextmanager
def owned_containers() -> Iterator[str]:
    """A fresh ``LATTICE_CONTAINER_OWNER`` for the check runs inside; whatever container carries
    it is removed afterwards, whatever happened (an exception, a timeout)."""
    from lattice.studio.state import remove_owned_containers

    prev = os.environ.get(OWNER_ENV)
    # a caller that set an owner (the MCP tool's subprocess) keeps it: its containers must carry
    # the token the caller will remove, or a killed run leaves them behind
    token = prev or f"impact-{uuid.uuid4().hex[:12]}"
    os.environ[OWNER_ENV] = token
    try:
        yield token
    finally:
        try:
            remove_owned_containers(token)
        finally:
            if prev is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = prev


def harness_runner(project: Path) -> Runner:
    """A runner over the project's own harness: the flags ``lattice check`` fills from
    ``studio.toml`` (``_fill_check_args``), parsed by ``python -m lattice.spec check``'s parser.
    Raises :class:`ImpactError` ``no_app`` when the project hooks up no app."""
    from lattice.cli import _fill_check_args
    from lattice.spec.__main__ import _parser, _preload_engines, _runtime, _services, load
    from lattice.spec.check import check

    filled = _fill_check_args(Path(project).resolve(), [])
    if isinstance(filled, str):
        raise ImpactError("no_app", filled)
    problem = _preload_engines(filled)
    if problem is not None:
        raise ImpactError("bad_inputs", problem)
    args = _parser().parse_args(["check", *filled])
    app_factory, db_factory = load(args.app), load(args.db)
    kwargs: dict[str, Any] = {
        "app_dir": args.app_dir,
        "package": args.package,
        "go_src": args.go_src,
        "login": load(args.login) if args.login else None,
        "login_path": args.login_path,
        "mint_token": load(args.mint_token) if args.mint_token else None,
        "natives": load(args.natives) if args.natives else None,
        "baseline": load(args.baseline) if args.baseline else (),
        "x_k": args.x_k,
        "x_repeats": args.x_repeats,
        "x_budget": args.x_budget,
        "runtime": _runtime(args.runtime),
        "services": _services(args.services),
        "database": args.database,
        "database_url": args.database_url,
        "await_cap": args.await_cap,
    }

    def run(spec: Spec, layers: str) -> Any:
        return check(spec, app_factory, db_factory, layers=layers, **kwargs)

    run.harness = {  # type: ignore[attr-defined]
        "app_factory": app_factory,
        "make_db": db_factory,
        "login": kwargs["login"],
        "runtime": kwargs["runtime"],
        "database": args.database,
        "natives": kwargs["natives"],
    }
    return run


def project_has_app(project: Path | None) -> bool:
    if project is None:
        return False
    try:
        data = tomllib.loads((Path(project) / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    app = data.get("app")
    return isinstance(app, dict) and bool(app.get("app") and app.get("db"))


# ---- findings -> items ----------------------------------------------------------------------

_GO808 = re.compile(r"\bthe (\w+)\.(\w+) machine\b")
_STEP_CASE = re.compile(r"^(.*) / step (\d+)$")


def finding_target(spec: Spec, f: Any) -> str | None:
    """The spec element a finding is about: a scenario (D), an invariant (C), the element of an
    X finding, or a machine named by a layer A state-machine finding (GO808)."""
    details = getattr(f, "details", None) or {}
    if f.layer == "D":
        return "scenario:" + str(f.rule).removeprefix("scenario: ")
    if f.layer == "C" and details.get("invariant"):
        return f"invariant:{details['invariant']}"
    if f.layer == "X":
        rule = str(details.get("rule_id") or "")
        if rule in {i.name for i in spec.invariants}:
            return f"invariant:{rule}"
        if "." in rule:
            return f"machine:{rule}"
        if details.get("scenario"):
            return f"scenario:{details['scenario']}"
    if f.layer == "A" and getattr(f, "code", None) == "GO808":
        m = _GO808.search(str(details.get("message") or ""))
        if m:
            return f"machine:{m.group(1)}.{m.group(2)}"
    return None


def _sha1(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def stamp(tier: int, item: dict[str, Any]) -> dict[str, Any]:
    """`item` with its ``tier`` and ``id`` (``t<k>-`` + 10 hex digits of the sha1 of its
    canonical content)."""
    item = dict(item)
    item["tier"] = tier
    item.pop("id", None)
    item = {"id": f"t{tier}-{_sha1(item)}", **item}
    return item


def _ident(f: Any) -> tuple[str, str, str, str]:
    return (f.layer, str(f.rule), str(f.case or ""), f.key)


def _case_label(f: Any) -> str:
    return f.case or (f"{f.code} {f.file}:{f.line}" if f.layer == "A" else "")


def _route_registration(inv: Any, endpoint: str | None) -> tuple[str, int] | None:
    if inv is None or not endpoint:
        return None
    from lattice.spec.implementation import code_view, route_key

    m, _, p = endpoint.partition(" ")
    cv = getattr(inv, "_impact_cv", None)
    if cv is None:
        cv = code_view(inv)
        inv._impact_cv = cv
    regs = cv.by_key.get(route_key(m, p))
    if not regs:
        return None
    r = regs[0]
    return inv.evidence_path(str(r["file"])), int(r["line"])


def finding_item(
    f: Any,
    *,
    spec: Spec,
    lines: SpecLines,
    inv: Any,
    labels: Mapping[str, Any],
    base_idents: set[tuple[str, str, str, str]],
    observation: str,
) -> dict[str, Any]:
    target = finding_target(spec, f)
    label = labels.get(target) if target else None
    ev: list[dict[str, Any]] = [
        {
            "kind": "check",
            "at": f"check:{f.layer}:{_case_label(f) or f.endpoint or ''}",
            "what": f.summary(),
        }
    ]
    file: str | None = None
    line: int | None = None
    why: str | None = None
    if f.layer == "A" and f.file:
        file, line = f.file, int(f.line or 0) or None
        ev.append({"kind": "code", "at": f"code:{f.file}:{f.line}", "what": f"{f.code}: {f.rule}"})
    else:
        m = _STEP_CASE.match(f.case or "")
        if m:
            sl = lines.step(m.group(1), int(m.group(2)))
            if sl:
                file, line = "spec.py", sl
                ev.append(
                    {
                        "kind": "spec",
                        "at": f"spec:spec.py:{sl}",
                        "what": f"{m.group(1)} step {m.group(2)}",
                    }
                )
        if line is None:
            reg = _route_registration(inv, f.endpoint)
            if reg is not None:
                file, line = reg
                ev.append(
                    {
                        "kind": "code",
                        "at": f"code:{file}:{line}",
                        "what": f"registration of {f.endpoint}",
                    }
                )
            else:
                why = "the step is not a literal in spec.py and the route is not in the inventory"
    problems = (f.details or {}).get("problems") if isinstance(f.details, dict) else None
    item: dict[str, Any] = {
        "kind": "check_failure",
        "target": target,
        "rules": sorted(label.rules, key=_rkey) if label is not None else [],
        "label": label.label if label is not None else "unlinked",
        "layer": f.layer,
        "rule": f.rule,
        "case": f.case,
        "endpoint": f.endpoint,
        "code": getattr(f, "code", None),
        "verdict": f.verdict,
        "summary": f.summary(),
        "problems": list(problems or [])[:3],
        "observation": observation,
        "also_fails_on_base": _ident(f) in base_idents,
        "evidence": ev,
        "file": file,
        "line": line,
    }
    if f.layer == "X":
        item["reproducible"] = False
    if why:
        item["why"] = why
    return item


def _rkey(r: str) -> tuple[int, str]:
    return (int(r[1:]) if r[1:].isdigit() else 0, r)


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda i: (
            str(i.get("kind")),
            str(i.get("target") or ""),
            str(i.get("layer") or ""),
            str(i.get("rule") or ""),
            str(i.get("case") or ""),
            str(i.get("file") or ""),
            int(i.get("line") or 0),
            str(i.get("id")),
        ),
    )


# ---- T1 -------------------------------------------------------------------------------------


def verified_flows(inv: Any) -> tuple[list[dict[str, Any]], str | None]:
    """(the inventory's verified flows, why there are none): none when the inventory is stale,
    its workflows do not match the extract, or were not observed."""
    if inv is None:
        return [], "no_inventory"
    if inv.workflows is None:
        return [], "no_workflows"
    if inv.stale is not None:
        return [], "stale"
    if inv.mismatch is not None:
        return [], "workflows_mismatch"
    if inv.workflows["mode"] != "observed":
        return [], "static_run"
    flows = [f for f in inv.workflows["flows"] if f["status"] == "verified"]
    return flows, None


def with_flows(spec: Spec, flows: Sequence[Mapping[str, Any]]) -> Spec:
    from lattice.spec.implementation import flow_scenario

    if not flows:
        return spec
    return dataclasses.replace(
        spec, scenarios=(*spec.scenarios, *(flow_scenario(f) for f in flows))
    )


def _split(report: Any, flow_ids: set[str]) -> tuple[list[Any], dict[str, list[Any]]]:
    """(findings that are not about a flow, findings by flow id)."""
    own: list[Any] = []
    by_flow: dict[str, list[Any]] = {}
    for f in report.findings:
        fid = None
        if f.layer == "D" and str(f.rule).startswith("scenario: flow_"):
            fid = str(f.rule).removeprefix("scenario: flow_")
        elif f.case and str(f.case).startswith("flow_"):
            fid = str(f.case).split(" / ", 1)[0].removeprefix("flow_")
        if fid is not None and fid in flow_ids:
            by_flow.setdefault(fid, []).append(f)
        else:
            own.append(f)
    return own, by_flow


@dataclass
class T1Result:
    section: dict[str, Any]
    cand_report: Any
    base_report: Any
    passed: set[tuple[str, int]] | None
    failing_routes: list[tuple[str, str]]


def run_tier1(
    inp: Inputs,
    runner: Runner,
    *,
    layers: str,
    baseline: str,
    flows: Sequence[Mapping[str, Any]],
    flow_why: str | None,
    race: bool,
    cand_lines: SpecLines,
    log: Callable[[str], None] = lambda _m: None,
) -> T1Result:
    """Tier 1: the candidate (with the verified flows) on today's app; with ``baseline == "run"``
    the base the same way."""
    run_layers = layers + ("X" if race and "X" not in layers else "")
    spec_c = with_flows(inp.cand.spec, flows)
    flow_ids = {str(f["id"]) for f in flows}
    t0 = time.perf_counter()
    with owned_containers():
        cand_report = runner(spec_c, run_layers)
    log(f"tier 1: candidate run {time.perf_counter() - t0:.1f}s")
    base_report = None
    if baseline == "run":
        t0 = time.perf_counter()
        with owned_containers():
            base_report = runner(with_flows(inp.base.spec, flows), run_layers)
        log(f"tier 1: base run {time.perf_counter() - t0:.1f}s")
    return finish_tier1(
        inp,
        cand_report,
        base_report,
        flows=flows,
        flow_ids=flow_ids,
        flow_why=flow_why,
        baseline=baseline,
        cand_lines=cand_lines,
        layers=run_layers,
    )


def finish_tier1(
    inp: Inputs,
    cand_report: Any,
    base_report: Any,
    *,
    flows: Sequence[Mapping[str, Any]],
    flow_ids: set[str],
    flow_why: str | None,
    baseline: str,
    cand_lines: SpecLines,
    layers: str,
) -> T1Result:
    spec, diff = inp.cand.spec, inp.diff
    own, by_flow = _split(cand_report, flow_ids)
    base_own: list[Any] = []
    base_by_flow: dict[str, list[Any]] = {}
    if base_report is not None:
        base_own, base_by_flow = _split(base_report, flow_ids)
    base_idents = {_ident(f) for f in base_own}
    # labels: label_impact on the findings that name a target, exactly as Studio does
    shim = type("R", (), {"findings": own})()
    items = label_impact(shim, spec, diff, inp.cand.trace, base_spec=inp.base.spec)
    labels = {i.target: i for i in items}
    failing = []
    for f in own:
        obs = "static_checker" if f.layer == "A" else "runtime"
        failing.append(
            stamp(
                1,
                finding_item(
                    f,
                    spec=spec,
                    lines=cand_lines,
                    inv=inp.inventory,
                    labels=labels,
                    base_idents=base_idents,
                    observation=obs,
                ),
            )
        )
    cand_idents = {_ident(f) for f in own}
    base_lines = SpecLines(inp.base.module)
    resolved = [
        stamp(
            1,
            {
                "kind": "resolved",
                "target": finding_target(inp.base.spec, f),
                "rules": [],
                "layer": f.layer,
                "rule": f.rule,
                "case": f.case,
                "endpoint": f.endpoint,
                "code": getattr(f, "code", None),
                "summary": f.summary(),
                "evidence": [
                    {
                        "kind": "check",
                        "at": f"check:{f.layer}:{_case_label(f)}",
                        "what": "fails on the base, passes on the candidate",
                    }
                ],
                "file": "base/spec.py" if _step_line(f, base_lines) else None,
                "line": _step_line(f, base_lines),
            },
        )
        for f in base_own
        if _ident(f) not in cand_idents
    ]
    flow_items: list[dict[str, Any]] = []
    base_c = {
        (str(f.rule), str(f.case)) for fs in base_by_flow.values() for f in fs if f.layer == "C"
    }
    names = {str(f["id"]): str(f.get("name") or "") for f in flows}
    for fid in sorted(by_flow):
        fs = by_flow[fid]
        d = [f for f in fs if f.layer == "D"]
        c = [f for f in fs if f.layer == "C" and (str(f.rule), str(f.case)) not in base_c]
        if d:
            f0 = d[0]
            flow_items.append(
                stamp(
                    1,
                    {
                        "kind": "baseline_drift",
                        "target": f"flow:{fid}",
                        "rules": [],
                        "flow": fid,
                        "name": names.get(fid, ""),
                        "case": f0.case,
                        "summary": f0.summary(),
                        "evidence": [
                            {"kind": "check", "at": f"check:D:{f0.case}", "what": f0.summary()}
                        ],
                        "file": None,
                        "line": None,
                        "why": "a flow records today's answers; a D failure is drift, "
                        "not a failing workflow",
                    },
                )
            )
        if c:
            f0 = c[0]
            tgt = finding_target(spec, f0)
            lab = labels.get(tgt) if tgt else None
            flow_items.append(
                stamp(
                    1,
                    {
                        "kind": "workflow_fails",
                        "target": f"flow:{fid}",
                        "rules": sorted(lab.rules, key=_rkey) if lab is not None else [],
                        "flow": fid,
                        "name": names.get(fid, ""),
                        "invariant": tgt,
                        "case": f0.case,
                        "summary": f0.summary(),
                        "evidence": [
                            {"kind": "check", "at": f"check:C:{f0.case}", "what": f0.summary()}
                        ],
                        "file": None,
                        "line": None,
                        "why": "a candidate invariant is violated on this flow and was not "
                        "violated under the base's",
                    },
                )
            )
    passed = impact_tier2.passed_steps(base_report.cases) if base_report is not None else None
    routes: list[tuple[str, str]] = []
    for f in own:
        if f.layer in ("D", "C") and f.request and f.request.get("path"):
            from lattice.spec.implementation import route_key

            routes.append(
                (route_key(str(f.request.get("method") or ""), str(f.request["path"])), str(f.case))
            )
    counts = {
        "failing": len(failing),
        "by_layer": _count(failing, "layer"),
        "by_label": _count(failing, "label"),
        "resolved": len(resolved),
        "flows_run": len(flows),
        "workflow_fails": sum(1 for i in flow_items if i["kind"] == "workflow_fails"),
        "baseline_drift": sum(1 for i in flow_items if i["kind"] == "baseline_drift"),
    }
    section = {
        "failing": _sort_items(failing),
        "resolved": _sort_items(resolved),
        "flows": _sort_items(flow_items),
        "baseline": {
            "run": base_report is not None,
            "source": baseline if baseline in ("run", "none") else "file",
            "failing": sorted(
                [
                    {
                        "layer": f.layer,
                        "rule": f.rule,
                        "case": f.case,
                        "code": getattr(f, "code", None),
                    }
                    for f in base_own
                ],
                key=lambda x: (x["layer"], str(x["rule"]), str(x["case"]), str(x["code"])),
            ),
        },
        "flows_note": flow_why,
        "layers": layers,
        "counts": counts,
    }
    return T1Result(section, cand_report, base_report, passed, sorted(set(routes)))


def _step_line(f: Any, lines: SpecLines) -> int | None:
    m = _STEP_CASE.match(f.case or "")
    return lines.step(m.group(1), int(m.group(2))) if m else None


def _count(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[str(i.get(key))] = out.get(str(i.get(key)), 0) + 1
    return dict(sorted(out.items()))


# ---- the report -----------------------------------------------------------------------------


def baseline_file(path: Path | str) -> set[tuple[str, int]]:
    """The passed ``(scenario, step)`` of a check ``--json`` report (or an impact report that
    carries one): a baseline of unknown provenance."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise ImpactError("bad_inputs", f"--baseline {Path(path).name}: {err}") from None
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, dict):
        raise ImpactError("bad_inputs", "--baseline FILE must be a check --json report (cases)")
    return impact_tier2.passed_steps(cases)


def baseline_report(inp: Inputs, runner: Runner, layers: str = "ACD") -> dict[str, Any]:
    """The base spec plus the verified flows through `runner`, as ``Report.to_dict()``: the file
    ``--baseline FILE`` reads (a premise of unknown provenance to the next reader)."""
    flows, _why = verified_flows(inp.inventory)
    with owned_containers():
        return runner(with_flows(inp.base.spec, flows), layers).to_dict()  # type: ignore[no-any-return]


def build_report(
    inp: Inputs,
    *,
    tiers: str = "123",
    runner: Runner | None = None,
    baseline: str = "run",
    layers: str = "ACD",
    flows_on: bool = True,
    race: bool = False,
    replay_env: Mapping[str, Any] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """The report (``lattice.impact/1``) for `inp`. `runner` runs a check on the app (T1 and the
    baseline run); without one T1 is ``not_run:no_app``. `baseline`: ``run`` (the base through
    `runner`), ``none``, or a path to a check ``--json`` report."""
    base, cand = inp.base, inp.cand
    diff = inp.diff
    targets = changed_targets(base.spec, cand.spec)
    inv = inp.inventory
    flows, flow_why = verified_flows(inv) if flows_on else ([], "disabled")
    cand_lines, base_lines = SpecLines(cand.module), SpecLines(base.module)
    want = {t for t in tiers if t in "123"}
    state: dict[str, str] = {t: "not_run:not_requested" for t in TIERS}
    state["4"] = "not_run:spike_e"
    t1: T1Result | None = None
    passed: set[tuple[str, int]] | None = None
    provenance: str | None = None
    tier1: dict[str, Any] = {
        "failing": [],
        "resolved": [],
        "flows": [],
        "baseline": {"run": False, "source": "none", "failing": []},
        "counts": {},
    }
    t1_error: str | None = None
    if "1" in want:
        if runner is None:
            state["1"] = "not_run:no_app"
        else:
            try:
                t1 = run_tier1(
                    inp,
                    runner,
                    layers=layers,
                    baseline=baseline,
                    flows=flows,
                    flow_why=flow_why,
                    race=race,
                    cand_lines=cand_lines,
                    log=log,
                )
                tier1 = t1.section
                state["1"] = "run"
            except Exception as err:  # noqa: BLE001 -- T1 could not run: reported, exit 3
                t1_error = f"{type(err).__name__}: {err}"
                state["1"] = f"not_run:error: {t1_error}"
    if t1 is not None and t1.passed is not None:
        passed, provenance = t1.passed, "run"
    elif "2" in want and baseline not in ("run", "none"):
        passed, provenance = baseline_file(baseline), "file"
    elif (
        "2" in want and baseline == "run" and t1 is None and runner is not None and t1_error is None
    ):
        try:
            with owned_containers():
                rep = runner(with_flows(base.spec, flows), layers)
            passed, provenance = impact_tier2.passed_steps(rep.cases), "run"
            tier1["baseline"] = {"run": True, "source": "run", "failing": []}
        except Exception as err:  # noqa: BLE001
            t1_error = f"{type(err).__name__}: {err}"
    tier2: dict[str, Any] = {"determined": [], "flows": [], "decidability": []}
    if "2" in want:
        from lattice.spec.lint import conflicts

        def flow_ok(fid: str) -> bool:
            if t1 is None or t1.base_report is None:
                return True
            return not any(
                f.layer == "D" and str(f.rule) == f"scenario: flow_{fid}"
                for f in t1.base_report.findings
            )

        tier2 = impact_tier2.compute(
            base.spec,
            cand.spec,
            base.trace,
            cand.trace,
            diff,
            targets,
            passed=passed,
            provenance=provenance,
            flows=flows,
            flow_note=flow_why,
            base_lines=base_lines,
            cand_lines=cand_lines,
            conflicts_fn=conflicts,
            flow_passed=flow_ok,
            **({"forms_items": fi} if (fi := forms_items(base, cand)) else {}),
        )
        replay = impact_tier2.runtime_replay(base.spec, cand.spec, passed, replay_env, cand_lines)
        tier2["runtime_replay"] = {"status": replay["status"]}
        tier2["determined"] = tier2["determined"] + replay["items"]
        impact_tier2.merge_rules(tier2["determined"], cand.trace)
        for key in ("determined", "flows"):
            tier2[key] = _sort_items([stamp(2, i) for i in tier2[key]])
        state["2"] = "run"
    tier3: dict[str, Any] = {
        "locations": [],
        "flows": [],
        "unread": [],
        "external": {"count": 0},
        "counts": {},
    }
    if "3" in want:
        if inv is None:
            state["3"] = "not_run:inventory_missing"
            tier3["unread"] = [
                {
                    "kind": "no_extract",
                    "what": "inventory",
                    "reason": f"no extract for this framework or project ({inp.inventory_error})",
                    "file": None,
                    "line": None,
                }
            ]
        else:
            tier3 = impact_tier3.compute(
                inv,
                base.spec,
                cand.spec,
                base.trace,
                cand.trace,
                diff,
                targets,
                t1_routes=t1.failing_routes if t1 is not None else (),
            )
            tier3["locations"] = [stamp(3, i) for i in tier3["locations"]]
            tier3["flows"] = [stamp(3, i) for i in tier3["flows"]]
            state["3"] = "run"
    summary = {
        "tiers": {
            "1": {
                "failing": len(tier1["failing"]),
                "resolved": len(tier1.get("resolved", [])),
                "flows": len(tier1.get("flows", [])),
            },
            "2": {
                "determined": len(tier2["determined"]),
                "flows": len(tier2["flows"]),
                "undecidable": sum(1 for d in tier2["decidability"] if d["state"] != "determined"),
                "decidable": sum(1 for d in tier2["decidability"] if d["state"] == "determined"),
            },
            "3": {
                "locations": len(tier3["locations"]),
                "flows": len(tier3["flows"]),
                "unread": len(tier3["unread"]),
            },
        },
        "kinds": _count([*tier1["failing"], *tier2["determined"], *tier3["locations"]], "kind"),
        "workflows": {
            "t1_fail": sum(1 for i in tier1.get("flows", []) if i["kind"] == "workflow_fails"),
            "t2_fail": len(tier2["flows"]),
            "t3_may_fail": len(tier3["flows"]),
        },
        "note": NOTE_WORKFLOWS,
    }
    report = {
        "schema": SCHEMA,
        "input": {
            "kind": inp.kind,
            "change": inp.change,
            "base": base.hashes(),
            "candidate": cand.hashes(),
            "inventory": (
                {
                    "extract_sha256": inv.extract_sha256,
                    "workflows_sha256": inv.workflows_sha256,
                    "stale": inv.stale is not None,
                }
                if inv is not None
                else None
            ),
            "app": {"configured": runner is not None, "layers": layers + ("X" if race else "")},
            "notes": sorted(inp.notes),
        },
        "diff": {
            "rules": _rules_diff(diff),
            "targets": targets,
        },
        "tiers": state,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "tier4": None,
        "summary": summary,
    }
    if t1_error is not None:
        report["error"] = t1_error
    return report


def _rules_diff(diff: RuleDiff) -> dict[str, Any]:
    d = diff.to_dict()
    return {
        "added": [{"id": a["id"], "text": a["text"]} for a in d["added"]],
        "changed": [
            {"old": c["old"], "new": c["new"], "old_text": c["old_text"], "new_text": c["new_text"]}
            for c in d["changed"]
        ],
        "removed": [{"id": r["id"], "text": r["text"]} for r in d["removed"]],
        "unchanged": len(d["unchanged"]),
    }


def report_json(report: Mapping[str, Any]) -> str:
    return json.dumps(report, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def section_of(report: Mapping[str, Any], section: str) -> dict[str, Any]:
    """One section of the report (``summary``, ``tier1``..``tier3``, ``flows``, ``unread``,
    ``decidability``), with the schema and tier states."""
    head = {"schema": report["schema"], "tiers": report["tiers"]}
    if section == "summary":
        return {**head, "summary": report["summary"], "diff": report["diff"]}
    if section in ("tier1", "tier2", "tier3"):
        return {**head, section: report[section]}
    if section == "flows":
        return {
            **head,
            "flows": {
                "tier1": report["tier1"].get("flows", []),
                "tier2": report["tier2"].get("flows", []),
                "tier3": report["tier3"].get("flows", []),
            },
        }
    if section == "unread":
        return {**head, "unread": report["tier3"].get("unread", [])}
    if section == "decidability":
        return {**head, "decidability": report["tier2"].get("decidability", [])}
    raise ImpactError("bad_section", f"section must be one of {', '.join(SECTIONS)}")


def render(report: Mapping[str, Any]) -> str:
    """The report as text, one line per item with its tier."""
    s = report["summary"]
    out = [
        f"change impact ({report['input']['kind']}"
        + (f" {report['input']['change']}" if report["input"]["change"] else "")
        + ")",
        "tiers: " + ", ".join(f"{k} {v}" for k, v in report["tiers"].items()),
    ]
    d = report["diff"]["rules"]
    out.append(
        f"rules: {len(d['added'])} added, {len(d['changed'])} changed, {len(d['removed'])} "
        f"removed, {d['unchanged']} unchanged"
    )
    t = report["diff"]["targets"]
    out.append(
        f"targets: {len(t['added'])} added, {len(t['modified'])} modified, "
        f"{len(t['removed'])} removed"
    )
    out += ["", "TIER 1 (observed on today's app)"]
    for i in report["tier1"]["failing"]:
        where = f" {i['file']}:{i['line']}" if i.get("file") else ""
        out.append(f"  [{i['label']}] {i['target'] or '-'}: {i['summary']}{where}")
    for i in report["tier1"].get("flows", []):
        out.append(f"  [{i['kind']}] {i['target']}: {i['summary']}")
    if not report["tier1"]["failing"] and report["tiers"]["1"] == "run":
        out.append("  (no failing check)")
    if report["tiers"]["1"] != "run":
        out.append(f"  ({report['tiers']['1']})")
    out += ["", "TIER 2 (determined by the spec change and what passed)"]
    for i in report["tier2"]["determined"]:
        where = f" {i['file']}:{i['line']}" if i.get("file") else ""
        out.append(f"  [{i['status']}] {i['claim']}{where}")
    for i in report["tier2"]["flows"]:
        out.append(f"  [{i['status']}] {i['claim']}")
    if not report["tier2"]["determined"] and not report["tier2"]["flows"]:
        out.append("  (no determined claim)")
    out += ["", "TIER 3 (static: where the code is touched; may_fail is not a claim)"]
    for i in report["tier3"]["locations"]:
        out.append(
            f"  [{i['kind']}] {i['file']}:{i['line']} {i['what']}"
            + (f" ({i['symbol']})" if i.get("symbol") else "")
        )
    for i in report["tier3"]["flows"]:
        out.append(f"  [may_fail] {i['target']}: {', '.join(i['routes'])}")
    out.append(f"  unread: {len(report['tier3']['unread'])}")
    out += ["", f"workflows: {s['workflows']}", s["note"]]
    return "\n".join(out) + "\n"


# ---- the CLI --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice spec impact",
        description="The impact of a spec change: the code it touches and the checks and "
        "workflows that fail (tiers 1 observed, 2 determined, 3 static). Read-only.",
    )
    p.add_argument("--project", type=Path, required=True, help="the project directory")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate", type=Path, help="a directory with the candidate's spec files")
    g.add_argument("--change", help="a Studio change id (cN), read from the state directory")
    p.add_argument("--state-dir", type=Path, help="the Studio state directory (default: derived)")
    p.add_argument("--inventory", type=Path, help="the inventory directory")
    p.add_argument("--tiers", default="123", help="tiers to run (default 123)")
    p.add_argument("--layers", default="ACD", help="tier 1 layers (default ACD)")
    p.add_argument("--baseline", default="run", help="run (default), none, or a check --json FILE")
    p.add_argument("--no-flows", action="store_true", help="do not run the verified flows")
    p.add_argument("--race", action="store_true", help="also layer X (never gated)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--section", choices=SECTIONS)
    p.add_argument("--out", type=Path, help="also write the report here")
    return p


def _inside(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0: the report was produced; 2: the inputs are unusable (or ``--out`` is inside the
    project or the app root); 3: tier 1 could not run (the report says why). Timings go to
    stderr."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "impact":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    t0 = time.perf_counter()

    def log(msg: str) -> None:
        print(f"impact: {msg}", file=sys.stderr)

    if not set(args.tiers) <= set("1234") or not args.tiers:
        print("error: --tiers is a subset of 123", file=sys.stderr)
        return 2
    project = args.project.resolve()
    try:
        inp = build_inputs(
            project=project,
            candidate=args.candidate,
            change=args.change,
            state_dir=args.state_dir,
            inventory=args.inventory,
        )
        if args.out is not None:
            roots = [
                project,
                inp.inventory.app_root if inp.inventory else None,
                inp.inventory.directory if inp.inventory else None,
                args.candidate,
            ]
            if any(_inside(args.out, r) for r in roots):
                print(
                    "error: --out is inside the project, the app root or the inventory",
                    file=sys.stderr,
                )
                return 2
        runner: Runner | None = None
        if "1" in args.tiers or ("2" in args.tiers and args.baseline == "run"):
            if project_has_app(project):
                runner = harness_runner(project)
        replay_env = getattr(runner, "harness", None) if runner is not None else None
        report = build_report(
            inp,
            tiers=args.tiers,
            runner=runner,
            baseline=args.baseline,
            layers=args.layers,
            flows_on=not args.no_flows,
            race=args.race,
            replay_env=replay_env,
            log=log,
        )
    except ImpactError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 -- any failure to report is exit 2
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    data: Mapping[str, Any] = section_of(report, args.section) if args.section else report
    text = report_json(data) if args.json or args.section else render(report)
    sys.stdout.write(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report_json(data), encoding="utf-8")
    log(f"{time.perf_counter() - t0:.1f}s")
    return 3 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
