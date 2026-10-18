# ruff: noqa: E501
"""Probes of the typed-conditions spike, round 2 (docs/results/2026-10-17-typed-conditions-round2-prereg.md, section 3).

    uv run python dogfood/forms_probes2.py --stage dev --scratch DIR --out DIR
    uv run python dogfood/forms_probes2.py --stage round1 --shapes DIR --manifest FILE --scratch DIR --out DIR
    uv run python dogfood/forms_probes2.py --stage frozen --shapes DIR --manifest FILE --freeze FILE --scratch DIR --out DIR

**dev** (the builder's own fixtures, never gated evidence): PC (one case per cause 0-7 on
``tests/spec/fixtures/forms_r2``), PR1 (the round-1 dev forms under the round-2 library), PB (byte-compat
against ``git archive`` of the merge base and of d7ba7de), PW, PA, PG3, PG4, PX, plus ungated G/H tables.

**round1** (PD1, post hoc): the round-1 frozen set (manifest hash-checked) scored with the round-2
library. Reported beside the round-1 values, never gated: that set has been read by three authors.

**frozen** (H2, written by a separate agent; scored once by the orchestrator): refuses to run unless the
set's ``MANIFEST.sha256`` equals the committed copy and the library files equal ``freeze2.sha256``.
The set is read as written (conditions untagged, ``approved_sha`` over the text as written).
A layout the runner does not recognise is NOT RUN, fixed in the runner only and disclosed.

Reuses the round-1 runner ``dogfood/forms_probes.py`` (unchanged) for helpers and the dev probes that
only depend on its fixture.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load_p1() -> Any:
    spec = importlib.util.spec_from_file_location(
        "forms_probes_r1", REPO / "dogfood/forms_probes.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["forms_probes_r1"] = mod
    spec.loader.exec_module(mod)
    return mod


P1 = _load_p1()
FIX1 = REPO / "tests/spec/fixtures/forms/minishop"
FIX2 = REPO / "tests/spec/fixtures/forms_r2/minishop"
IMPACT = "tests/spec/fixtures/impact"
MERGE_BASE = "c5e2595"  # the build branch after the Spike E merge (Amendment 1, Q7)
ROUND1_BASE = "d7ba7de"
# post-merge references (the frozen files of prereg section 7; __main__ carries Spike E's one line)
FROZEN_HASHES = {
    "lattice/spec/model.py": "a7f7b3f834f22a89004f6bfb69484f6c4ff35caf86a32f4254c6a1182c9ec9ce",
    "lattice/spec/check.py": "68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e",
    "lattice/spec/invariants.py": "c3f374756d0b4186bea4df00c1f910ed2942b8bfcb771a8df472793d48354a73",
    "lattice/spec/lint.py": "0462853748f3c0101db4f00633c708e98f209fde526fb7eb6563c665f087b442",
    "lattice/spec/preconditions.py": "f1d8abee4f8d92b26fac557081967757736a7f3c8d0aaaa806134b8136595687",
    "lattice/spec/rules.py": "e3c0bc041595de280abae7c9beebbab64f09e0fd55182e173ee623c4dfddb248",
    "lattice/spec/incremental.py": "29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6",
    "lattice/spec/rationale.py": "ea0f05dd47a27934d0e6d5d6e20d0964c1449f7667966e8c5c4e495de9c434e9",
    "lattice/spec/impact_tier3.py": "22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf",
    "lattice/spec/validate.py": "97691c98b66a49f656315dea00225e1465861e99533e5886b10718be1427b012",
    "lattice/spec/impact_tier2.py": "d0e95f902b1ba1f6435a8ffe2624912706815222c46964e687bbfedd063dc8a0",
    "lattice/spec/__main__.py": "94542f34058145783670ca8cf95e83e6016bcc8aa03688b9af259d28af141c24",
    "tests/spec/minishop.py": "90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd",
    "dogfood/forms_probes.py": "7449106201f93f6108addcd557f54ef353ccbb53322c05a3b4bda04eba3525c0",
    "dogfood/forms_mutants.py": "7b9483ff29f80b91164c594e0312beb50fc0e4b1ba5409cd83360967e2240597",
}
FROZEN_SETS = {
    "lattice/check": "ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf",
    "lattice/gocheck": "e9515e859e4aebff1020d8fffa721e8859396b9545d4d7b2b768d4980a367c3f",
    "tests/spec/fixtures/impact": "291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b",
    "tests/spec/fixtures/forms": "ee956fc0472296d5eeb58b743c79bcd8014249032a9699d63ec4a6cd6e5cd0be",
}
ROUND1_VALUES = {  # prereg section 1.1, round 1 (library blind, runner post hoc)
    "PG1": "249 cells with G != H and H == T",
    "PG1t": "150 / 400 (37.5%)",
    "PG1o": "G fails R6-R8, R14-R20; no check for R12, R13",
    "PG1k": "G 3, H 18 of 24; H-only 15",
    "PG2": "53 / 54 same layer set",
    "PG5": "0 / 29 invalid accepted; 5 / 16 valid refused",
    "PT2f": "precision 1.0; recall a-d 13/14; class e 1/3; 5 raises",
    "PT2x": "19 items, 0 without witness (weak)",
    "PT2w": "0 items (CLI)",
    "PNX": "0 / 8 accepted; 3 with F004/F002",
}
NOT_EXPR_NODE_CODES = ("F001", "F017")
sha256_file, row, run, snapshot = P1.sha256_file, P1.row, P1.run, P1.snapshot


def set_sha(rel: str) -> str:
    """The registered set hash: ``git ls-files -z DIR | sort -z | xargs -0 sha256sum | sha256sum``."""
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", rel], capture_output=True, check=True
    ).stdout.split(b"\0")
    lines = sorted(f.decode() for f in files if f)
    out = "".join(f"{sha256_file(REPO / f)}  {f}\n" for f in sorted(lines))
    return hashlib.sha256(out.encode()).hexdigest()


def frozen_check() -> dict[str, Any]:
    bad = [rel for rel, want in FROZEN_HASHES.items() if sha256_file(REPO / rel) != want]
    sets = {
        d: {"got": (g := set_sha(d)), "want": w, "match": g == w} for d, w in FROZEN_SETS.items()
    }
    return {
        "files_changed": bad,
        "sets": sets,
        "ok": not bad and all(v["match"] for v in sets.values()),
    }


# ---- imports of the library under test (after sys.path) -----------------------------------------


def lib() -> Any:
    from lattice.spec import forms as F
    from lattice.spec import forms_tier2 as T2
    from lattice.spec import impact_tier2
    from tests.spec import minishop as M

    return F, T2, impact_tier2, M


# ---- dev: PC, one case per cause 0-7 ----------------------------------------------------------


def _fx2() -> tuple[Any, Any, Any, Any, Any]:
    F, _T2, _it, M = lib()
    from lattice.spec.rules import Trace

    trace = Trace.load(FIX2 / "spec.trace.json")
    paths = F.forms_paths(FIX2 / "spec.trace.json")
    fs = F.load_set(trace, paths)
    assert fs is not None
    return F, M, trace, paths, fs


def _mk(F: Any, kind: str, form: dict[str, Any], fid: str = "F90", rule: str = "R6") -> Any:
    return F.Form(fid, rule, "0" * 64, kind, form, "approved", "hand", "t", F.form_sha(kind, form))


def pc(scratch: Path) -> list[dict[str, Any]]:
    """One dev case per cause; the fixture forms F1-F7 are the cases of causes 0-6, cause 7 is inline."""
    F, M, trace, _paths, fs = _fx2()
    spec = M.SPEC
    by = {r.form.id: r for r in fs.resolved}
    gen = F.generate(spec, fs.generating())
    sc = {s.name: s for s in gen.scenarios}
    step = lambda n: sc[n].steps[0]  # noqa: E731
    cases: dict[str, dict[str, Any]] = {}

    # 0 untagged node: accepted, state current (approved_sha over the text as written), generates
    f1 = by["F1"].form
    cases["PC0"] = {
        "pass": "node" not in f1.form["cond"]
        and by["F1"].state == "current"
        and "form:F1:invariant" in gen.names()
        and F.check_forms(spec, trace, [f1]) == [],
        "state": by["F1"].state,
    }
    # 1 accepted derived from the spec's own steps (the hand-written scenario adds a line: 201)
    cases["PC1"] = {
        "pass": step("form:F2:at_bound").expect_status == 201
        and not any(n.startswith("accepted_default:POST /orders/{}/items") for n in gen.notes),
        "at_bound_status": step("form:F2:at_bound").expect_status,
        "notes": list(gen.notes),
    }
    # 2 ownership row as a request path: every endpoint instantiated with the key
    cases["PC2"] = {
        "pass": step("form:F3:owner_get_orders").path == "/orders/1"
        and step("form:F3:owner_post_orders_cancel").path == "/orders/1/cancel",
        "paths": [
            step("form:F3:owner_get_orders").path,
            step("form:F3:owner_post_orders_cancel").path,
        ],
    }
    # 3 transition row {id}: completed (owner = ann's id, total 0.0), no IntegrityError at run time
    g = sc["form:F4:from_pending"].given["Order"][0]
    cases["PC3"] = {
        "pass": g == {"id": 20, "user_id": 1, "total": 0.0, "status": "pending"},
        "row": g,
    }
    # 4 row_limit with parent-only given: synthesised counted rows, parent kept
    below, lim = sc["form:F5:below"], sc["form:F5:at_limit"]
    cases["PC4"] = {
        "pass": len(below.given["OrderItem"]) == 4
        and len(lim.given["OrderItem"]) == 5
        and below.given["orders"][0]["id"] == 20
        and all(r["order_id"] == 20 for r in lim.given["OrderItem"]),
        "below_rows": len(below.given["OrderItem"]),
        "at_limit_rows": len(lim.given["OrderItem"]),
    }
    # 5 param bound without `stores`: accepted by check, `unchanged` asserted on the derived table
    db = step("form:F6:beyond").expect_db
    cases["PC5"] = {
        "pass": F.check_forms(spec, trace, [by["F6"].form]) == []
        and db is not None
        and "OrderItem" in db,
        "expect_db": db,
    }
    # 6 column-only bound: an invariant, no scenario (registered limitation, class K6)
    cases["PC6"] = {
        "pass": "form:F7:invariant" in gen.names()
        and not any(n.startswith("form:F7:") for n in sc),
    }

    # 7 non-Lit value: F017 for a node name, F001 for another non-literal; the literal is not typed first
    def codes(cond: dict[str, Any]) -> list[str]:
        form = {"field": {"column": "Product.stock"}, "cond": cond}
        return sorted({d.code for d in F.check_forms(spec, None, [_mk(F, "bound", form)])})

    got = {
        "value_param": codes(
            {"op": "ge", "value": {"param": "POST /orders/{}/items", "path": "body.quantity"}}
        ),
        "value_column": codes({"op": "ge", "value": {"column": "Product.price"}}),
        "value_sum": codes({"op": "ge", "value": {"sum": ["a"]}}),
        "value_list": codes({"op": "ge", "value": [1, 2]}),
        "values_item": codes({"values": [1, {"now": True}]}),
        "float_for_int": codes({"op": "ge", "value": 1.5}),
    }
    cases["PC7"] = {
        "pass": got["value_param"]
        == got["value_column"]
        == got["value_sum"]
        == got["values_item"]
        == ["F017"]
        and got["value_list"] == ["F001"]
        and got["float_for_int"] == ["F004"],
        "codes": got,
    }
    ok = all(c["pass"] for c in cases.values())
    return [row("PC", "one dev case per cause 0-7 (fixtures/forms_r2 + inline)", "PASS" if ok else "FAIL",
                passed=f"{sum(c['pass'] for c in cases.values())}/{len(cases)}", cases=cases)]  # fmt: skip


# ---- dev: PR1, the round-1 dev forms under round-2 code ---------------------------------------


def pr1(scratch: Path) -> list[dict[str, Any]]:
    orig = scratch / "pr1_orig"
    if not orig.exists():
        orig.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", MERGE_BASE], capture_output=True, check=True
        )
        subprocess.run(["tar", "-x", "-C", str(orig)], input=tar.stdout, check=True)
    argv = [sys.executable, "-m", "lattice.spec", "forms", "generate", "--trace", str(FIX1 / "spec.trace.json"),
            "--spec", "tests.spec.minishop:SPEC"]  # fmt: skip
    outs = {}
    for tag, tree in (("old", orig), ("new", REPO)):
        outs[tag] = run(argv, tree, {"PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"})
    rc_ok = outs["old"]["rc"] == 0 and outs["new"]["rc"] == 0
    a, b = (json.loads(outs[t]["out"]) for t in ("old", "new")) if rc_ok else ({}, {})
    names = lambda d: {x["name"]: x for x in d.get("scenarios", [])}  # noqa: E731
    na, nb = names(a), names(b)
    diff = sorted(n for n in set(na) | set(nb) if na.get(n) != nb.get(n))
    inv_same = a.get("invariants") == b.get("invariants") and a.get("links") == b.get("links")
    ok = (
        rc_ok
        and set(diff) <= {"form:F7:below", "form:F7:at_limit"}
        and inv_same
        and a.get("notes") == b.get("notes")
    )
    return [row("PR1", "round-1 dev forms: generate under round-2 code vs the merge base", "PASS" if ok else "FAIL",
                differing_scenarios=diff, invariants_and_links_identical=inv_same,
                notes_old=a.get("notes"), notes_new=b.get("notes"), rc=[outs["old"]["rc"], outs["new"]["rc"]])]  # fmt: skip


# ---- dev: PB, byte-compat ----------------------------------------------------------------------


def pb(scratch: Path) -> list[dict[str, Any]]:
    out = []
    for label, base in (("PB", MERGE_BASE), ("PB-d7ba7de", ROUND1_BASE)):
        sub = scratch / label
        sub.mkdir(parents=True, exist_ok=True)
        P1.BASE, P1.FROZEN_HASHES, P1.FROZEN_SETS = base, {}, {}
        r = P1.pb(sub)[0]
        fc = frozen_check()
        for c in r[
            "commands"
        ]:  # a wall-clock line on stderr ("impact: 0.1s") is a timing key, not output
            lines = c.get("diff_lines", [])
            if (
                not c["identical"]
                and lines
                and all(re.fullmatch(r"[\w -]+: [\d.]+s", x) for x in lines)
            ):
                c["identical"], c["timing_stderr_only"] = True, lines
        cmd_bad = [c["cmd"] for c in r["commands"] if not c["identical"]]
        r["probe"] = label
        r["what"] = (
            f"byte-compat against git archive {base} (no forms file anywhere); frozen hashes and sets"
        )
        r["frozen_check"] = fc
        r["differing_commands"] = cmd_bad
        r["status"] = (
            "PASS" if not cmd_bad and fc["ok"] else ("FAIL" if label == "PB" else "REPORTED")
        )
        (
            r.pop("frozen_sets", None),
            r.pop("frozen_files_changed", None),
            r.pop("git_diff_empty", None),
        )
        out.append(r)
    return out


# ---- dev: PW, PA, PG3, PG4, PX on both fixtures --------------------------------------------------


def _with_fix(fix: Path, fn: Any, scratch: Path, tag: str) -> list[dict[str, Any]]:
    P1.FIX = fix
    sub = scratch / tag
    sub.mkdir(parents=True, exist_ok=True)
    if not (sub / "apps").exists():
        shutil.copytree(scratch / "PB" / "apps", sub / "apps")
    rows = fn(sub)
    for r in rows:
        r["fixture"] = fix.parent.name + "/" + fix.name
        r["probe"] = f"{r['probe']}[{tag}]"
    return rows


def pa2(scratch: Path) -> list[dict[str, Any]]:
    """PA on the round-2 fixture: the untagged forms are `current` (no recompute), a hand edit makes
    an untagged form `edited`, a proposal still generates nothing."""
    F, M, trace, paths, fs = _fx2()
    st = {r.form.id: r.state for r in fs.resolved}
    project = scratch / "pa2"
    shutil.rmtree(project, ignore_errors=True)
    shutil.copytree(FIX2, project)
    auth = project / "REQUIREMENTS.forms.json"
    d = json.loads(auth.read_text())
    d["forms"][0]["form"]["cond"]["value"] = 2  # F1 is untagged; edit it by hand
    auth.write_text(json.dumps(d))
    fs2 = F.load_set(trace, F.forms_paths(project / "spec.trace.json"))
    st2 = {r.form.id: r.state for r in fs2.resolved}
    ok = (
        set(st.values()) == {"current"}
        and st2["F1"] == "edited"
        and "form:F1:invariant" not in F.generate(M.SPEC, fs2.generating()).names()
    )
    return [row("PA[r2]", "round-2 fixture: untagged forms are current; a hand edit reads edited", "PASS" if ok else "FAIL",
                states=st, after_edit={"F1": st2["F1"]})]  # fmt: skip


def dev_gh(scratch: Path) -> list[dict[str, Any]]:
    out = []
    for fix, tag in ((FIX1, "r1"), (FIX2, "r2")):
        P1.FIX = fix
        rows, timing = P1.gh_table(scratch / f"gh_{tag}")
        g = [r["app"] for r in rows[1:] if r["G_new_vs_ok"]]
        h = [r["app"] for r in rows[1:] if r["H_new_vs_ok"]]
        out.append(row(f"DEV-GH[{tag}]", f"ungated: dev forms' generated checks (G) and hand-written ones (H), {tag} fixture, 11 apps",
                       "REPORTED", g_kills=g, h_kills=h, g_findings_on_ok=rows[0]["G"], h_findings_on_ok=rows[0]["H"],
                       cells=rows, timing=timing))  # fmt: skip
    return out


def pt2w_dev(scratch: Path) -> list[dict[str, Any]]:
    """PT2w on the dev fixture through the CLI, and D's 7 fixtures are PB's business."""
    F, T2, _it, M = lib()
    from lattice.spec.rules import Trace

    proj, cand = scratch / "pt2w_dev" / "project", scratch / "pt2w_dev" / "candidate"
    for d in (proj, cand):
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(FIX1, d)
    fp = cand / "REQUIREMENTS.forms.json"
    data = json.loads(fp.read_text())
    e = json.loads(json.dumps(next(f for f in data["forms"] if f["id"] == "F3")))
    e.update(id="F90", rule="R7")
    e["form"]["cond"] = {"node": "Cmp", "op": "lt", "value": 1}
    e["approved_sha"] = F.form_sha(e["kind"], e["form"])
    data["forms"].append(e)
    fp.write_text(json.dumps(data, indent=2) + "\n")
    return [pt2w_run(proj, cand, M.SPEC, Trace.load(cand / "spec.trace.json"), fp, "PT2w[dev]")]


def pt2w_run(
    proj: Path, cand: Path, spec: Any, trace: Any, cand_forms: Path, label: str
) -> dict[str, Any]:
    F, T2, _it, _M = lib()
    cmd = [sys.executable, "-m", "lattice.spec", "impact", "--project", str(proj), "--candidate", str(cand),
           "--tiers", "23", "--json", "--no-flows", "--baseline", "none"]  # fmt: skip
    p = run(cmd, REPO, {"PYTHONPATH": str(REPO)})
    try:
        det = json.loads(p["out"])["tier2"]["determined"]
    except (ValueError, KeyError):
        return row(
            label,
            "impact CLI gains the form items",
            "FAIL",
            exit_code=p["rc"],
            stderr_tail=p["err"][-300:],
        )
    got = sorted(
        F.canon([d["class"], d["forms"], d["witness"]])
        for d in det
        if d.get("kind") == "form_contradiction"
    )
    forms = F.load_forms_file(cand_forms).forms
    base_forms = F.load_forms_file(proj / cand_forms.name).forms
    use = lambda fs: F.usable(F.resolve(trace, fs))  # noqa: E731
    mine = {
        F.canon([i["class"], i["forms"], i["witness"]]) for i in T2.contradictions(spec, use(forms))
    }
    had = {
        F.canon([i["class"], i["forms"], i["witness"]])
        for i in T2.contradictions(spec, use(base_forms))
    }
    want = sorted(mine - had)
    tiers = {d.get("tier") for d in det if d.get("kind") == "form_contradiction"}
    ok = bool(want) and got == want and tiers == {2}
    return row(label, "impact CLI: tier2.determined gains exactly the new form items (tier 2)", "PASS" if ok else "FAIL",
               expected_items=len(want), got_items=len(got), tiers=sorted(t for t in tiers if t is not None),
               determined_total=len(det), exit_code=p["rc"])  # fmt: skip


def dev(scratch: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    steps: list[Any] = [
        pc, pr1, pb,
        lambda s: _with_fix(FIX1, P1.pw, s, "r1") + _with_fix(FIX2, P1.pw, s, "r2"),
        lambda s: _with_fix(FIX1, P1.pa, s, "r1"),
        pa2,
        lambda s: _with_fix(FIX1, P1.pg3, s, "r1") + _with_fix(FIX2, P1.pg3, s, "r2"),
        lambda s: _with_fix(FIX1, P1.pg4_px, s, "r1") + _with_fix(FIX2, P1.pg4_px, s, "r2"),
        lambda s: _with_fix(FIX1, P1.dev_pairs, s, "pairs"),
        dev_gh, pt2w_dev,
    ]  # fmt: skip
    for fn in steps:
        t = time.perf_counter()
        rows += fn(scratch)
        print(
            f"{getattr(fn, '__name__', 'step')}: {time.perf_counter() - t:.1f} s", file=sys.stderr
        )
    return rows


# ---- the set stages: round1 (PD1, post hoc) and frozen (H2, gated) --------------------------------


def check_set(shapes: Path, manifest: Path | None, freeze: Path | None) -> str | None:
    """None when the set and the library files match their committed hashes; else the reason."""
    if manifest is None or not manifest.is_file():
        return "no committed manifest to check the set against"
    ok, detail = P1.manifest_ok(shapes, manifest)
    if not ok:
        return f"manifest check failed: {detail}"
    if freeze is not None:
        for line in freeze.read_text().splitlines():
            if line.strip():
                digest, _, rel = line.partition("  ")
                if sha256_file(REPO / rel.strip()) != digest:
                    return f"{rel.strip()} differs from freeze2.sha256"
    return None


# ---- readers for the layouts of a frozen set (H2: lists, tagged-or-not nodes, optional keys) ----


def _as_list(doc: Any, key: str) -> list[dict[str, Any]]:
    """A file that is a list (H2) or an object holding the list under ``key`` (round 1)."""
    return doc if isinstance(doc, list) else doc[key]


def _truth_cells(cells: Any) -> list[tuple[str, str, bool]]:
    """(rule, app, violates): a list of {rule, app, violates} (round 1) or {app: {rule: {violates}}} (H2)."""
    if isinstance(cells, dict):
        return [
            (rid, app, bool(c["violates"]))
            for app, by_rule in cells.items()
            for rid, c in by_rule.items()
        ]
    return [(c["rule"], c["app"], bool(c["violates"])) for c in cells]


def _mutant_edit(v: Any) -> tuple[str, str, str]:
    """(file, old, new): a bare triple (round 1) or {kind, aims, why, edit: [file, old, new]} (H2)."""
    e = v["edit"] if isinstance(v, dict) else v
    return (e[0], e[1], e[2])


def _entry(F: Any, x: dict[str, Any], rule_text: dict[str, str]) -> dict[str, Any]:
    """A form record from H2's {id, rule, kind, form}: approved, anchored on the rule's text when known."""
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
        "approved_sha": F.form_sha(x["kind"], x["form"]),
    }


def set_rows(shapes: Path, scratch: Path, label: str) -> list[dict[str, Any]]:
    """PG1, PG1t, PG1o, PG1k, PG2, PG5, PG5c, PT2f, PT2x (strong), PT2w, PNX on the set, as written."""
    F, T2, impact_tier2, M = lib()
    from lattice.spec.check import check
    from lattice.spec.model import Scenario, Step

    ms = shapes / "minishop"
    ms_spec = importlib.util.spec_from_file_location(f"hshapes_{label}", ms / "handwritten.py")
    assert ms_spec is not None and ms_spec.loader is not None
    mod = importlib.util.module_from_spec(ms_spec)
    ms_spec.loader.exec_module(mod)
    spec, trace = mod.SPEC, mod.TRACE
    raw = json.loads((ms / "forms.json").read_text())
    forms = tuple(F._parse_form(e, i) for i, e in enumerate(raw["forms"]))  # noqa: SLF001
    entries = {e["id"]: e for e in raw["forms"]}
    resolved_all = F.resolve(trace, forms)
    resolved = F.usable(resolved_all)
    states = {r.form.id: r.state for r in resolved_all}
    errors: dict[str, str] = {}
    parts = []
    for r in sorted(resolved, key=lambda r: (F._fnum(r.form.id), r.form.id)):  # noqa: SLF001
        try:
            parts.append(F.generate(spec, [r]))
        except Exception as err:  # noqa: BLE001 -- a raise on a form that passed check is itself a finding
            errors[r.form.id] = f"{type(err).__name__}: {err}"
    gen = F.Generated(
        tuple(s for p in parts for s in p.scenarios), tuple(i for p in parts for i in p.invariants),
        {k: v for p in parts for k, v in p.links.items()}, {k: v for p in parts for k, v in p.no_invariant.items()},
        tuple(n for p in parts for n in p.notes),
    )  # fmt: skip
    try:
        F.generate(spec, resolved)
        whole_error = None
    except Exception as err:  # noqa: BLE001
        whole_error = f"{type(err).__name__}: {err}"
    diags = F.check_forms(spec, trace, list(forms))
    eff = F.effective_spec(spec, gen)
    truth: dict[str, dict[str, bool]] = {}
    truth_doc = json.loads((ms / "truth.json").read_text())
    for rid, app, v in _truth_cells(truth_doc["cells"]):
        truth.setdefault(rid, {})[app] = v
    rule_text: dict[str, str] = dict(truth_doc.get("rules") or {})
    muts = {k: _mutant_edit(v) for k, v in json.loads((ms / "mutants.json").read_text()).items()}
    root = scratch / "apps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    apps = [
        (n.replace("sec:", "sec_", 1), v) for n, v in P1.apps_for(root, M)
    ]  # H2 names a sec app sec_<v>
    for mid, (mf, old, new) in muts.items():
        apps.append((mid, M.write_app(root, mid, edit=(mf, old, new))))
    form_rule = {r.form.id: r.rule for r in resolved}
    k6_rules = {
        r.rule for r in resolved
        if r.form.kind == "bound" and "endpoint" not in r.form.form and "column" in r.form.form["field"]
    }  # fmt: skip
    links = {t: rid for rid, ts in trace.links.items() for t in ts}
    inv_by_sentence = {i.sentence: i.name for i in spec.invariants}
    inv_names = {i.name for i in spec.invariants}

    def rule_of(text: str) -> tuple[bool, str | None]:
        m = re.search(r"form:(F\d+):", text) or re.search(r"\(form (F\d+)\)", text)
        if m:
            return True, form_rule.get(m.group(1))
        if text.startswith("scenario: "):
            return False, links.get("scenario:" + text[len("scenario: ") :])
        if text in inv_names:
            return False, links.get("invariant:" + text)
        return False, links.get("invariant:" + inv_by_sentence.get(text, ""))

    cells = []
    t_run = time.perf_counter()
    for name, (_d, pkg) in apps:
        rep = check(eff, M.app_factory(pkg), M.build_db, layers="CD", login=M.login)
        gfail: dict[str, set[str]] = {}
        hfail: dict[str, set[str]] = {}
        unattributed = []
        for f in rep.findings:
            is_gen, rid = rule_of(str(f.rule))
            if rid:
                (gfail if is_gen else hfail).setdefault(rid, set()).add(f.layer)
            elif is_gen:
                unattributed.append(str(f.rule))
        for rid in sorted(set(form_rule.values()), key=lambda r: int(r[1:])):
            cells.append({"rule": rid, "app": name, "G": rid in gfail, "H": rid in hfail, "T": truth.get(rid, {}).get(name),
                          "k6": rid in k6_rules, "Glayers": sorted(gfail.get(rid, ())), "Hlayers": sorted(hfail.get(rid, ())),
                          "unattributed": unattributed})  # fmt: skip
    run_wall = round(time.perf_counter() - t_run, 2)
    scored = [c for c in cells if c["T"] is not None]
    pg1_bad = [c for c in cells if c["G"] != c["H"] and c["H"] == c["T"]]
    pg1_diff = [c for c in cells if c["G"] != c["H"]]
    pg1t_bad = [c for c in scored if c["G"] != c["T"]]
    pg1t_bad_out = [c for c in pg1t_bad if not c["k6"]]
    ok_cells = [c for c in cells if c["app"] == "ok"]
    ok_bad = [c for c in ok_cells if c["G"] != bool(c["T"])]
    ok_g = {c["rule"] for c in ok_cells if c["G"]}
    ok_h = {c["rule"] for c in ok_cells if c["H"]}
    g_k = {c["app"] for c in cells if c["app"] != "ok" and c["G"] and c["rule"] not in ok_g}
    h_k = {c["app"] for c in cells if c["app"] != "ok" and c["H"] and c["rule"] not in ok_h}
    both = [c for c in cells if c["G"] and c["H"]]

    def slim(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: c[k] for k in ("rule", "app", "G", "H", "T", "k6")} for c in cs]

    k6_cells = [c for c in scored if c["k6"]]
    out = [
        row("PG1", "G vs H per cell", "PASS" if not pg1_bad else "FAIL", cells=len(cells), apps=len(apps),
            formed_rules=len(set(form_rule.values())), forms_usable=len(resolved), form_states=states,
            generate_whole_set_error=whole_error, forms_that_could_not_generate=errors,
            g_eq_h=sum(1 for c in cells if c["G"] == c["H"]), g_ne_h=slim(pg1_diff),
            bad_gne_h_and_h_eq_t=slim(pg1_bad), notes=list(gen.notes), run_wall_s=run_wall,
            unattributed_generated_findings=sorted({u for c in cells for u in c["unattributed"]})),
        row("PG1t", "G vs truth per cell (K6 cells apart)", "PASS" if not pg1t_bad_out else "FAIL", scored=len(scored),
            g_eq_t=sum(1 for c in scored if c["G"] == c["T"]), bad_outside_k6=slim(pg1t_bad_out),
            k6_cells=len(k6_cells), k6_misses=slim([c for c in k6_cells if c["G"] != c["T"]]),
            k6_rules=sorted(k6_rules)),
        row("PG1o", "on ok, G fails exactly the rules T marks violated", "PASS" if not ok_bad else "FAIL",
            g_fail_on_ok=sorted(ok_g), t_violated_on_ok=sorted(c["rule"] for c in ok_cells if c["T"]), bad=slim(ok_bad)),
        row("PG1k", "G kills (flips vs ok, formed rules) a superset of H's", "PASS" if h_k <= g_k else "FAIL",
            g_kills=sorted(g_k), h_kills=sorted(h_k), g_only=sorted(g_k - h_k), h_only=sorted(h_k - g_k),
            apps_non_ok=len(apps) - 1),
        row("PG2", "layer of the failing check where both fail", "REPORTED", cells_both_fail=len(both),
            same_layer_sets=sum(1 for c in both if c["Glayers"] == c["Hlayers"]),
            diff=[{k: c[k] for k in ("rule", "app", "Glayers", "Hlayers")} for c in both if c["Glayers"] != c["Hlayers"]]),
    ]  # fmt: skip

    # PG5 / PG5c
    inv = _as_list(json.loads((shapes / "invalid.json").read_text()), "invalid")
    wrong, accepted, exact = [], [], []
    for x in inv:
        x = {**x, "code": x.get("code", x.get("expect"))}  # H2 writes `expect`
        if "entries" in x:
            fs = [F._parse_form(e, 0) for e in x["entries"]]  # noqa: SLF001
        elif "entry" in x:
            fs = [*forms, F._parse_form(x["entry"], 0)]  # noqa: SLF001
        else:  # H2: {id, rule, kind, form}, one form added to the set's approved forms
            fs = [*forms, F._parse_form(_entry(F, x, rule_text), 0)]  # noqa: SLF001
        sp = spec
        if x.get("spec_extra_scenarios"):
            extra = (
                Scenario(s["name"], s.get("given"), tuple(Step(**t) for t in s["steps"]))
                for s in x["spec_extra_scenarios"]
            )
            sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra))
        got = sorted({d.code for d in F.check_forms(sp, trace, fs)})
        if not got:
            accepted.append(x["id"])
        elif x["code"] in got:
            exact.append(x["id"])
        else:
            wrong.append(
                {
                    "id": x["id"],
                    "want": x["code"],
                    "got": got,
                    "also_acceptable_hit": bool(
                        {x["code"], *x.get("also_acceptable", ())} & set(got)
                    ),
                }
            )
    valid_refused = [d.render() for d in diags]
    out += [
        row("PG5", "invalid.json refused; the set's valid forms accepted", "PASS" if not accepted and not valid_refused else "FAIL",
            invalid=len(inv), accepted_invalid=accepted, valid_refused=valid_refused, valid_forms=len(forms)),
        row("PG5c", "exact F-code on invalid.json", "REPORTED", exact=len(exact), of=len(inv), codes_not_exact=wrong),
    ]  # fmt: skip

    # PT2f / PT2x (strong)
    pairs = _as_list(json.loads((shapes / "pairs.json").read_text()), "pairs")
    hand = {f"scenario:{sc.name}" for sc in spec.scenarios}
    res = []
    for p in pairs:
        fs: list[Any] = []
        named: set[str] = set()
        extra = []
        if "forms" in p:  # H2: forms = [{id, rule, kind, form} | "scenario:<hand-written name>"]
            for s_ in p["forms"]:
                if isinstance(s_, str):
                    if s_ not in hand:
                        raise ValueError(f"pair {p['id']}: no hand-written {s_!r}")
                    named.add(s_)
                else:
                    fs.append(F._parse_form(_entry(F, s_, rule_text), 0))  # noqa: SLF001
        else:  # round-1 layout: a / b sides
            for side, fid in (("a", "PA"), ("b", "PB")):
                s_ = p[side]
                if "ref" in s_:
                    fs.append(F._parse_form(entries[s_["ref"]], 0))  # noqa: SLF001
                elif "scenario" in s_:
                    extra.append(
                        Scenario(
                            s_["scenario"]["name"],
                            s_["scenario"].get("given"),
                            tuple(Step(**t) for t in s_["scenario"]["steps"]),
                        )
                    )
                else:
                    fs.append(
                        F._parse_form(
                            {
                                "id": fid,
                                "rule": "R1",
                                "anchor": "0" * 64,
                                "kind": s_["kind"],
                                "form": s_["form"],
                                "status": "approved",
                                "author": "hand",
                                "approved_sha": "x",
                            },
                            0,
                        )
                    )  # noqa: SLF001
        sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra)) if extra else spec
        if len({f.id for f in fs}) < len(fs):
            fs[1] = dataclasses.replace(fs[1], id="F9999")
        err, notes = None, []
        out_of_pair = 0
        try:
            items = T2.contradictions(sp, fs, notes)
            ids = {f.id for f in fs}
            allowed = ids | named | {f"scenario:{x.name}" for x in extra}
            kept = [i for i in items if all(x in allowed for x in i["forms"])]
            out_of_pair = len(items) - len(kept)
            items = kept
        except Exception as e:  # noqa: BLE001
            items, err = [], f"{type(e).__name__}: {e}"
        expect = {"contradictory": "contradict"}.get(p["expect"], p["expect"])
        res.append({"id": p["id"], "class": p["class"], "expect": expect, "items": items, "error": err, "notes": notes,
                    "forms": fs, "spec": sp, "named": sorted(named), "witness": p.get("witness"),
                    "out_of_pair": out_of_pair})  # fmt: skip
    tp = [r for r in res if r["expect"] == "contradict" and r["items"]]
    fn = [r for r in res if r["expect"] == "contradict" and not r["items"]]
    fp = [r for r in res if r["expect"] == "compatible" and r["items"]]
    tn = [r for r in res if r["expect"] == "compatible" and not r["items"]]

    def by_class(cs: str) -> dict[str, Any]:
        sub = [r for r in res if r["class"] in cs]
        return {"contradict": sum(r["expect"] == "contradict" for r in sub),
                "found": sum(r["expect"] == "contradict" and bool(r["items"]) for r in sub),
                "compatible": sum(r["expect"] == "compatible" for r in sub),
                "false_pos": sum(r["expect"] == "compatible" and bool(r["items"]) for r in sub)}  # fmt: skip

    rabcd = by_class("abcd")
    out.append(row("PT2f", "forms contradictions on pairs.json", "PASS" if not fp and rabcd["found"] == rabcd["contradict"] and not any(r["error"] for r in res) else "FAIL",
                   pairs=len(res), true_pos=len(tp), false_neg=[r["id"] for r in fn],
                   false_pos=[{"id": r["id"], "items": [i["id"] for i in r["items"]]} for r in fp], true_neg=len(tn),
                   errors=[{"id": r["id"], "error": r["error"]} for r in res if r["error"]],
                   undecided=[{"id": r["id"], "notes": r["notes"]} for r in res if r["notes"]],
                   items_outside_the_pair=[{"id": r["id"], "n": r["out_of_pair"]} for r in res if r["out_of_pair"]],
                   precision=(len(tp) / (len(tp) + len(fp))) if tp or fp else None,
                   recall_abcd=f"{rabcd['found']}/{rabcd['contradict']}", class_e=by_class("e"),
                   per_class={c: by_class(c) for c in "abcde"}))  # fmt: skip

    weak: list[str] = []
    bad_w: list[str] = []
    n_items = 0
    for r in tp:
        for it in r["items"]:
            n_items += 1
            why = rederive(
                F, impact_tier2, r["spec"], {f.id: f for f in r["forms"]}, it, r.get("witness")
            )
            if why == "weak":
                weak.append(it["id"])
            elif why:
                bad_w.append(f"{it['id']}: {why}")
    out.append(row("PT2x", "witness, strong: the runner re-derives the witness and calls incompatible itself", "PASS" if not bad_w else "FAIL",
                   items=n_items, not_rederived_weak=weak, failed_rederivation=bad_w))  # fmt: skip

    # PNX
    ne = _as_list(json.loads((shapes / "not_expressible.json").read_text()), "rules")
    acc, wrong_code, tried = [], [], 0
    for x in ne:
        a = x.get("attempt")
        if not a:
            continue
        tried += 1
        f = F._parse_form(
            {
                "id": "F901",
                "rule": "R1",
                "anchor": "0" * 64,
                "kind": a["kind"],
                "form": a["form"],
                "status": "approved",
                "author": "hand",
            },
            0,
        )  # noqa: SLF001
        got = sorted({d.code for d in F.check_forms(spec, None, [f])})
        if not got:
            acc.append(x["id"])
        elif not set(got) & set(NOT_EXPR_NODE_CODES):
            wrong_code.append(
                {
                    "id": x["id"],
                    "got": got,
                    "set_expects": x.get("expect_any_of", a.get("expect_codes")),
                }
            )
    out.append(row("PNX", "not_expressible attempts refused with F001 or F017", "PASS" if not acc and not wrong_code else "FAIL",
                   rules=len(ne), with_attempt=tried, accepted=acc, refused_with_other_code=wrong_code))  # fmt: skip
    out.append(pt2w_set(shapes, scratch, spec, trace, forms, label))
    return out


def rederive(
    F: Any,
    impact_tier2: Any,
    spec: Any,
    by_id: dict[str, Any],
    it: dict[str, Any],
    pair_witness: Any = None,
) -> str | None:
    """None when the item's witness re-derives; 'weak' when no independent derivation is done; else the reason."""
    cls, ids, w = it["class"], it["forms"], it["witness"]
    forms = [by_id.get(i) for i in ids]
    if cls == "a" and all(forms) and "value" in w and "holds" not in w and "accepted_by" not in w:
        held = [F.cond_holds(F.norm_cond(f.form["cond"]), w["value"]) for f in forms]
        return None if sum(held) == 1 else "value does not separate"
    if cls == "a" and all(forms) and "value" in w and "holds" in w:
        holder, failer = by_id[w["holds"]], by_id[w["fails"]]
        return (
            None
            if F.cond_holds(F.norm_cond(holder.form["cond"]), w["value"])
            and not F.cond_holds(F.norm_cond(failer.form["cond"]), w["value"])
            else "value does not separate"
        )
    if cls == "a" and "accepted_by" in w:
        return (
            None
            if F.cond_holds(F.norm_cond(by_id[w["accepted_by"]].form["cond"]), w["value"])
            and not F.cond_holds(F.norm_cond(by_id[w["forbidden_by"]].form["cond"]), w["value"])
            else "stored value is not forbidden"
        )
    if cls == "b" and all(forms):
        small, large = sorted(f.form["max"] for f in forms)
        n = w["counted_rows"]
        return None if n == small and small < large else "counted rows do not separate the limits"
    if cls in ("d", "e"):

        def case(name: str) -> Any:
            fid = name.split(":")[1] if name.startswith("form:") else None
            if fid is None or fid not in by_id:
                return None
            g = F.generate(spec, [by_id[fid]])
            return next((s.steps[0] for s in g.scenarios if s.name == name), None)

        pw = pair_witness if isinstance(pair_witness, dict) else {}
        if cls == "e" and isinstance(
            pw.get("request"), dict
        ):  # H2: the pair's witness is a request
            rq = pw["request"]

            def steps_of(i: str) -> list[Any]:
                if i.startswith("scenario:"):
                    return [
                        st for sc in spec.scenarios if f"scenario:{sc.name}" == i for st in sc.steps
                    ]
                if i not in by_id:
                    return []
                return [st for sc in F.generate(spec, [by_id[i]]).scenarios for st in sc.steps]

            def hit(st: Any) -> bool:
                return (st.method, st.path, st.user) == (
                    rq["method"],
                    rq["path"],
                    rq.get("user"),
                ) and ("json" not in rq or st.json == rq["json"])

            sides = [[_ex(st) for st in steps_of(i) if hit(st)] for i in ids]
            if len(sides) != 2 or not all(sides):
                return "no case for the witness request on a side"
            return (
                None
                if any(impact_tier2.incompatible(a, b, spec) for a in sides[0] for b in sides[1])
                else "cases are not incompatible"
            )
        if cls == "e":
            names = w.get("cases", [])
            ex = []
            for n in names:
                st = case(n)
                if st is None:
                    st = next(
                        (s.steps[0] for s in spec.scenarios if f"scenario:{s.name}" == n), None
                    )
                if st is None:
                    return f"cannot find case {n}"
                ex.append(
                    {
                        "status": st.expect_status,
                        "body": st.expect_body,
                        "db": dict(st.expect_db) if st.expect_db else None,
                    }
                )
            return (
                None
                if len(ex) == 2 and impact_tier2.incompatible(ex[0], ex[1], spec)
                else "cases are not incompatible"
            )
        if all(forms) and "state" in w:
            s = w["state"]
            ex = []
            for f in forms:
                g = F.generate(spec, [f])
                nm = f"form:{f.id}:from_{s}" if s in f.form["from"] else f"form:{f.id}:refused_{s}"
                st = next((x.steps[0] for x in g.scenarios if x.name == nm), None)
                if st is None:
                    return f"no case {nm}"
                ex.append(
                    {
                        "status": st.expect_status,
                        "body": None,
                        "db": dict(st.expect_db) if st.expect_db else None,
                    }
                )
            return (
                None if impact_tier2.incompatible(ex[0], ex[1], spec) else "states not incompatible"
            )
    return "weak"


def _ex(st: Any) -> dict[str, Any]:
    return {
        "status": st.expect_status,
        "body": st.expect_body,
        "db": dict(st.expect_db) if st.expect_db else None,
    }


def pt2w_set(
    shapes: Path, scratch: Path, spec: Any, trace: Any, forms: tuple[Any, ...], label: str
) -> dict[str, Any]:
    """PT2w through the CLI: the set's forms as a minishop project; the candidate adds one form that
    contradicts a base form (a bound with the opposite ordering condition)."""
    F, _T2, _it, _M = lib()
    d = scratch / "pt2w_set"
    shutil.rmtree(d, ignore_errors=True)
    (d / "project").mkdir(parents=True)
    ms = shapes / "minishop"
    (
        d / "project" / "spec.py"
    ).write_text(  # a wrapper: handwritten.py reads REQUIREMENTS.md beside itself
        "import importlib.util\n"
        f"_s = importlib.util.spec_from_file_location('hw', {str(ms / 'handwritten.py')!r})\n"
        "_m = importlib.util.module_from_spec(_s)\n_s.loader.exec_module(_m)\nSPEC = _m.SPEC\nTRACE = _m.TRACE\n"
    )
    (d / "project" / "spec.trace.json").write_text(trace.to_json())
    (d / "project" / "REQUIREMENTS.md").write_text((ms / "REQUIREMENTS.md").read_text())
    (d / "project" / "REQUIREMENTS.forms.json").write_text((ms / "forms.json").read_text())
    shutil.copytree(d / "project", d / "candidate")
    flip = {"ge": "lt", "gt": "le", "le": "gt", "lt": "ge"}
    pick = None
    for f in sorted(forms, key=lambda f: (F._fnum(f.id), f.id)):  # noqa: SLF001
        c = F.norm_cond(f.form.get("cond")) if f.kind == "bound" else None
        if (
            isinstance(c, dict)
            and c.get("node") == "Cmp"
            and c.get("op") in flip
            and F.usable(F.resolve(trace, [f]))
        ):
            pick = f
            break
    if pick is None:
        return row(
            "PT2w",
            "impact CLI gains the form items",
            "NOT RUN",
            reason="no bound form with an ordering condition to contradict",
        )
    cond = {**pick.form["cond"], "op": flip[F.norm_cond(pick.form["cond"])["op"]]}
    entry = {**pick.to_dict(), "id": "F9001", "form": {**pick.form, "cond": cond}}
    entry["approved_sha"] = F.form_sha(entry["kind"], entry["form"])
    fp = d / "candidate" / "REQUIREMENTS.forms.json"
    data = json.loads(fp.read_text())
    data["forms"].append(entry)
    fp.write_text(json.dumps(data, indent=2) + "\n")
    return pt2w_run(d / "project", d / "candidate", spec, trace, fp, "PT2w")


def stage_set(
    stage: str, shapes: Path | None, manifest: Path | None, freeze: Path | None, scratch: Path
) -> list[dict[str, Any]]:
    names = ("PG1", "PG1t", "PG1o", "PG1k", "PG2", "PG5", "PG5c", "PT2f", "PT2x", "PT2w", "PNX")
    if shapes is None or not shapes.is_dir():
        return [row(n, "needs the set", "NOT RUN", reason="the set is not present") for n in names]
    why = check_set(shapes, manifest, freeze)
    if why is not None:
        raise SystemExit(why)
    try:
        rows = set_rows(shapes, scratch, stage)
    except Exception as err:  # noqa: BLE001 -- an unrecognised layout is NOT RUN (fixed in the runner only)
        reason = f"unrecognised layout: {type(err).__name__}: {err}"
        return [row(n, "needs the set", "NOT RUN", reason=reason) for n in names]
    if stage == "round1":
        for r in rows:
            r["post_hoc"] = True
            r["round1_value"] = ROUND1_VALUES.get(r["probe"], "n/a")
            r["status_gate"] = r["status"]
            r["status"] = "REPORTED"  # PD1 is never gated
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--stage", choices=("dev", "round1", "frozen"), required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--shapes", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--freeze", type=Path)
    ap.add_argument("--worker")
    args = ap.parse_args(argv)
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if args.stage == "dev":
        rows = dev(args.scratch)
    else:
        if args.stage == "frozen" and (args.freeze is None or args.manifest is None):
            ap.error("--stage frozen needs --manifest and --freeze")
        rows = stage_set(args.stage, args.shapes, args.manifest, args.freeze, args.scratch)
        wall = round(time.perf_counter() - t0, 1)
        rows.append(
            row(
                "PX",
                "wall time of the set arm",
                "PASS" if wall <= 180 else "FAIL",
                seconds=wall,
                gate="<= 180 s",
            )
        )
    rows.append(row("wall", "total", "REPORTED", seconds=round(time.perf_counter() - t0, 1)))
    (args.out / f"{args.stage}.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n"
    )
    for r in rows:
        print(f"{r['probe']}: {r['status']}" + (f" ({r['reason']})" if "reason" in r else ""))
    return 0 if all(r["status"] in ("PASS", "NOT RUN", "REPORTED") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
