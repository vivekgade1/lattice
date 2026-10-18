# ruff: noqa: E501
"""Probes of the typed-conditions spike (docs/results/2026-10-17-typed-conditions-prereg.md, section 3).

    uv run python dogfood/forms_probes.py --stage dev --scratch DIR --out DIR
    uv run python dogfood/forms_probes.py --stage frozen --shapes DIR --manifest FILE --scratch DIR --out DIR

**dev** (the builder's own forms on the mini-shop, ``tests/spec/fixtures/forms/minishop``): PB
(byte-compat against ``git archive 6a318e5``), PW (writes nothing, no model call), PA (approval),
PG3 (determinism under random hash seeds and two scratch roots), PG4 (every generated element
compiles and validates), PX (wall time), plus an ungated *dev* table of what the dev forms'
generated checks (G) and the hand-written ones (H) find on the 11 mini-shop apps. None of the dev
rows is a gated score of the frozen set.

**frozen** (the separate agent's held-out set; scored once by the orchestrator): refuses to run
unless ``--shapes`` exists and its ``MANIFEST.sha256`` matches ``--manifest``. Every row that
needs the set is recorded ``NOT RUN`` with the reason when the set or a file of it is absent or
in a format this runner does not recognise (the formats are the section 3.1 table's; the exact
JSON layouts below are this runner's reading of it and are UNVALIDATED against the real set).

Layouts of the frozen set (as scored): ``minishop/handwritten.py`` (``SPEC``, ``TRACE``);
``minishop/forms.json`` (``lattice.forms/1``, condition nodes written UNTAGGED: ``{op, value}``,
``{values}``; the runner adds the ``Cmp|Len|In`` tag and recomputes ``approved_sha`` of the tagged
form, nothing else); ``minishop/mutants.json`` (``{id: [file, old, new]}``); ``minishop/truth.json``
(``cells: [{rule, app, violates}]``); ``invalid.json`` (``invalid: [{id, code, entry | entries,
also_acceptable?, spec_extra_scenarios?}]``, each checked beside the valid forms); ``pairs.json``
(``pairs: [{id, class, expect, a, b}]``, a side is ``{kind, form}``, ``{ref}`` or ``{scenario}``);
``not_expressible.json`` (``rules: [{id, category, attempt?: {kind, form, expect_codes}}]``).
A form the library cannot generate is recorded, its rule's G is "no check", and the run goes on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BASE = "6a318e5"
FIX = REPO / "tests/spec/fixtures/forms/minishop"
IMPACT = "tests/spec/fixtures/impact"
FROZEN_HASHES = {
    "lattice/spec/model.py": "a7f7b3f834f22a89004f6bfb69484f6c4ff35caf86a32f4254c6a1182c9ec9ce",
    "lattice/spec/check.py": "68f13b288cde4a3fa348412495ba560815e003b342ea9cca90b1a779c1bb525e",
    "lattice/spec/invariants.py": "c3f374756d0b4186bea4df00c1f910ed2942b8bfcb771a8df472793d48354a73",
    "lattice/spec/lint.py": "0462853748f3c0101db4f00633c708e98f209fde526fb7eb6563c665f087b442",
    "lattice/spec/preconditions.py": "f1d8abee4f8d92b26fac557081967757736a7f3c8d0aaaa806134b8136595687",
    "lattice/spec/rules.py": "e3c0bc041595de280abae7c9beebbab64f09e0fd55182e173ee623c4dfddb248",
    "lattice/spec/incremental.py": "29ded6ecbbddcc55a5d94914cf9bce82fb9ee61a10d3453d70c336565c6090e6",
    "lattice/spec/rationale.py": "ea0f05dd47a27934d0e6d5d6e20d0964c1449f7667966e8c5c4e495de9c434e9",
    "lattice/spec/impact_report.py": "a3e11f1c266a813a66e33d8943b8773705a419ce8dde806615203d750ae0137c",
    "lattice/spec/impact_tier3.py": "22228513beba44fcff4b0d5ab48c662d2010e7ac320b73ac860f134ffb6e2faf",
    "lattice/spec/validate.py": "97691c98b66a49f656315dea00225e1465861e99533e5886b10718be1427b012",
    "tests/spec/minishop.py": "90117594e10c61272b8896bfc631623c5d23c4689d8e1853649cf73810baccfd",
}
FROZEN_SETS = {  # directory -> (glob test, sha256 of the sorted sha256sum lines)
    "lattice/check": ("*.py", "ca52b5a0076a04328c97c1cdafdd50af1a557ac671795697f9ec448146ba57bf"),
    "lattice/gocheck": ("*", "b7a4dacfe0dd049a19fe58fb838b9c83e25cf0931433770d16e52df1dc761ba1"),
    "tests/spec/fixtures/impact": (
        "*",
        "291eab2058ca5c86079b73287d1feff42e97afcd9a53f2358322df7b75473d1b",
    ),
}
NOT_RUN_FROZEN = (
    ("PG1", "G vs H per cell"),
    ("PG1t", "G vs truth per cell"),
    ("PG1o", "every generated element passes on ok"),
    ("PG1k", "mutants killed, G a superset of H"),
    ("PG2", "layer of the failing check"),
    ("PG5", "invalid.json refused with the expected code; valid forms accepted"),
    ("PT2f", "forms contradictions on pairs.json"),
    ("PT2x", "witness self-check on the frozen pairs"),
    ("PT2w", "impact report with two contradictory forms"),
    ("PNX", "not_expressible.json refused"),
)


# ---- small helpers -------------------------------------------------------------------------


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(
    argv: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 300
) -> dict[str, Any]:
    e = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "UV_NO_SYNC": "1", **(env or {})}
    e.pop("ANTHROPIC_API_KEY", None)
    p = subprocess.run(  # noqa: S603
        argv, cwd=cwd, env=e, capture_output=True, text=True, timeout=timeout, check=False
    )
    return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}


def snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    """(sha256, mode, mtime_ns) of every file and the directories, hidden files included."""
    out: dict[str, tuple[str, int, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        rel = str(p.relative_to(root))
        out[rel] = (sha256_file(p) if p.is_file() else "dir", st.st_mode, st.st_mtime_ns)
    return out


def row(pid: str, what: str, status: str, **kw: Any) -> dict[str, Any]:
    return {"probe": pid, "what": what, "status": status, **kw}


# ---- dev: PB ---------------------------------------------------------------------------------


def group_sha(path: Path, pattern: str) -> str:
    files = sorted(f for f in path.rglob(pattern) if f.is_file() and "__pycache__" not in f.parts)
    lines = "".join(f"{sha256_file(f)}  {f.relative_to(REPO)}\n" for f in files)
    return hashlib.sha256(lines.encode()).hexdigest()


_TIMING = re.compile(r"(_s|seconds|timings?)$")


def strip_timings(text: str) -> str:
    """The JSON of ``text`` without wall-clock fields (``total_s``, ``seconds``, ``timings``...),
    canonically printed; text that is not JSON is returned as it is."""

    def clean(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if not _TIMING.search(k)}
        if isinstance(x, list):
            return [clean(v) for v in x]
        return x

    try:
        return json.dumps(clean(json.loads(text)), indent=1, sort_keys=True)
    except ValueError:
        return text


def pb(scratch: Path) -> list[dict[str, Any]]:
    orig = scratch / "orig"
    if not orig.exists():
        orig.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", BASE], capture_output=True, check=True
        )
        subprocess.run(["tar", "-x", "-C", str(orig)], input=tar.stdout, check=True)
    apps = scratch / "apps"
    pkg = apps / "shopapp"
    if not pkg.exists():
        sys.path.insert(0, str(REPO))
        from tests.spec import minishop as M

        pkg.mkdir(parents=True)
        for rel, src in M.sources("ok").items():
            (pkg / rel).write_text(src.replace("PKG", "shopapp"))
    py = sys.executable
    spec_cmds: list[tuple[str, list[str], str]] = [
        ("lattice --help", [py, "-c", "import sys; from lattice.cli import main; sys.exit(main(['--help']))"], "."),
        ("spec check --help", [py, "-m", "lattice.spec", "check", "--help"], "."),
        ("spec lint --help", [py, "-m", "lattice.spec", "lint", "--help"], "."),
        ("spec impact --help", [py, "-m", "lattice.spec", "impact", "--help"], "."),
        ("spec usage", [py, "-m", "lattice.spec"], "."),
    ]  # fmt: skip
    base_trace = f"{IMPACT}/base/spec.trace.json"
    for label, spec in (
        ("base", f"{IMPACT}/base/spec.py"),
        ("attack", "tests.spec.minishop:attack_spec"),
    ):
        spec_cmds.append(
            (f"lint --json {label}",
             [py, "-m", "lattice.spec", "lint", "--spec", spec, "--trace", base_trace, "--json"], "."),
        )  # fmt: skip
        chk_spec = "tests.spec.minishop:SPEC" if label == "base" else spec
        spec_cmds.append(
            (f"check --json CD {label}",
             [py, "-m", "lattice.spec", "check", "--spec", chk_spec, "--app", "shopapp.main:make_app",
              "--db", "tests.spec.minishop:build_db", "--login", "tests.spec.minishop:login",
              "--layers", "CD", "--json"], "."),
        )  # fmt: skip
    for name in (
        "base",
        "bad_invariant",
        "conflict",
        "new_request",
        "removed_rule",
        "reword",
        "status_changed",
    ):
        spec_cmds.append(
            (f"impact --tiers 23 --json {name}",
             [py, "-m", "lattice.spec", "impact", "--project", f"{IMPACT}/base", "--candidate",
              f"{IMPACT}/{name}", "--tiers", "23", "--baseline", "none", "--no-flows", "--json"], "."),
        )  # fmt: skip
    mock = "lattice/studio/mock/appmap/project"
    spec_cmds.append(
        ("appmap --json (mock project)",
         [py, "-m", "lattice.spec", "appmap", "--spec", "spec:SPEC", "--trace", "spec.trace.json",
          "--inventory", "../inventory", "--app-root", "../app", "--json"], mock),
    )  # fmt: skip
    spec_cmds.append(
        ("rationale coverage --json (mock project)",
         [py, "-m", "lattice.spec", "rationale", "coverage", "--trace", "spec.trace.json", "--json"],
         mock),
    )  # fmt: skip
    rows = []
    for label, argv, cwd in spec_cmds:
        res = {}
        for tag, tree in (("old", orig), ("new", REPO)):
            env = {"PYTHONPATH": f"{tree}:{apps}", "PYTHONHASHSEED": "0", "HOME": "/nonexistent"}
            res[tag] = run(argv, tree / cwd, env)
        raw_same = res["old"] == res["new"]
        same = raw_same or (
            res["old"]["rc"] == res["new"]["rc"]
            and res["old"]["err"] == res["new"]["err"]
            and strip_timings(res["old"]["out"]) == strip_timings(res["new"]["out"])
        )
        entry: dict[str, Any] = {
            "cmd": label,
            "identical": same,
            "raw_bytes_identical": raw_same,
            "rc": res["new"]["rc"],
        }
        if not same:
            a = (res["old"]["out"] + res["old"]["err"]).splitlines()
            b = (res["new"]["out"] + res["new"]["err"]).splitlines()
            entry["diff_lines"] = [x for x in b if x not in a][:6]
            entry["rc_old"] = res["old"]["rc"]
        rows.append(entry)
    ident = sum(1 for r in rows if r["identical"])
    hashes_bad = []
    for rel, want in FROZEN_HASHES.items():
        if sha256_file(REPO / rel) != want:
            hashes_bad.append(rel)
    sets = {}
    for d, (pat, want) in FROZEN_SETS.items():
        got = group_sha(REPO / d, pat)
        sets[d] = {"got": got, "want": want, "match": got == want}
    git_same = {}
    for d in (
        "lattice/check",
        "lattice/gocheck",
        "lattice/mcp",
        "lattice/studio",
        "tests/spec/fixtures/impact",
    ):
        diff = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--stat", BASE, "--", d],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
        git_same[d] = diff == ""
    status = "PASS" if ident == len(rows) and not hashes_bad and all(git_same.values()) else "FAIL"
    return [
        row(
            "PB",
            "byte-compat against git archive 6a318e5 (no forms file anywhere)",
            status,
            identical=f"{ident}/{len(rows)}",
            commands=rows,
            frozen_files_changed=hashes_bad,
            frozen_sets=sets,
            git_diff_empty=git_same,
        )
    ]


# ---- dev: the fixtures, a shim, and G/H runs ------------------------------------------------


def fresh_project(scratch: Path, name: str) -> Path:
    d = scratch / name
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(FIX, d)
    return d


def load_dev(project: Path):  # noqa: ANN201
    sys.path.insert(0, str(REPO))
    from lattice.spec import forms as F
    from lattice.spec.rules import Trace
    from tests.spec import minishop as M

    trace = Trace.load(project / "spec.trace.json")
    paths = F.forms_paths(project / "spec.trace.json")
    fs = F.load_set(trace, paths)
    assert fs is not None
    return F, M, trace, paths, fs


def pw(scratch: Path) -> list[dict[str, Any]]:
    project = fresh_project(scratch, "pw_proj")
    shim = scratch / "shim"
    shim.mkdir(exist_ok=True)
    log = scratch / "shim.log"
    log.write_text("")
    (shim / "claude").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    (shim / "claude").chmod(0o755)
    apps = scratch / "apps"
    py = sys.executable
    env = {
        "PYTHONPATH": f"{REPO}:{apps}",
        "PATH": f"{shim}:{os.environ.get('PATH', '')}",
        "PYTHONHASHSEED": "0",
    }
    t = str(project / "spec.trace.json")
    cmds = {
        "show": ["show", "--trace", t],
        "check": ["check", "--trace", t, "--spec", "tests.spec.minishop:SPEC"],
        "generate": ["generate", "--trace", t, "--spec", "tests.spec.minishop:SPEC"],
        "contradictions": ["contradictions", "--trace", t, "--spec", "tests.spec.minishop:SPEC"],
        "coverage": ["coverage", "--trace", t],
    }
    dirs = {"project": project, "app": apps / "shopapp"}
    before = {k: snapshot(v) for k, v in dirs.items()}
    bad_runs = 0
    for _ in range(10):
        for argv in cmds.values():
            r = run([py, "-m", "lattice.spec", "forms", *argv], REPO, env)
            bad_runs += r["rc"] != 0
    # a check run with forms (in-process, shim first on PATH)
    F, M, trace, paths, _fs = load_dev(project)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = env["PATH"]
    sys.path.insert(0, str(apps))
    try:
        import importlib

        rep, gen = F.check_with_forms(
            M.SPEC, trace, paths, importlib.import_module("shopapp.main").make_app,
            M.build_db, layers="CD", login=M.login,
        )  # fmt: skip
    finally:
        os.environ["PATH"] = old_path
    after = {k: snapshot(v) for k, v in dirs.items()}
    changed = {k: sorted(x for x in after[k] if after[k][x] != before[k].get(x)) for k in dirs}
    new = {k: sorted(set(after[k]) - set(before[k])) for k in dirs}
    calls = len([x for x in log.read_text().splitlines() if x.strip()])
    ok = (
        not any(changed.values()) and not any(new.values()) and calls == 0 and bad_runs == 0
        and gen is not None and len(rep.findings) >= 0
    )  # fmt: skip
    return [
        row(
            "PW",
            "snapshot around show/check/generate/contradictions/coverage x10 and a check run with forms",
            "PASS" if ok else "FAIL",
            changed=changed,
            new=new,
            shim_calls=calls,
            nonzero_exits=bad_runs,
            generated=len(gen.names()) if gen else 0,
        )  # fmt: skip
    ]


def pa(scratch: Path) -> list[dict[str, Any]]:
    project = fresh_project(scratch, "pa_proj")
    py = sys.executable
    F, M, trace, paths, fs = load_dev(project)
    out: dict[str, Any] = {}
    # approve refuses with no terminal (a subprocess has none) and with CLAUDECODE set
    prop = project / "REQUIREMENTS.forms.proposed.json"
    data = json.loads((project / "REQUIREMENTS.forms.json").read_text())
    for f in data["forms"]:
        f.update(status="proposed", approved_by=None, approved_sha=None)
    prop.write_text(json.dumps(data))
    auth = project / "REQUIREMENTS.forms.json"
    held = auth.read_bytes()
    auth.unlink()
    argv = [
        py, "-m", "lattice.spec", "forms", "approve", "--trace", str(project / "spec.trace.json"),
        "--spec", "tests.spec.minishop:SPEC", "--from", str(prop), "--form", "F2",
        "--approver", "probe",
    ]  # fmt: skip
    env = {"PYTHONPATH": str(REPO)}
    r1 = run(argv, REPO, env)
    r2 = run(argv, REPO, {**env, "CLAUDECODE": "1"})
    out["approve_no_tty"] = r1["rc"] == 2 and "approve_needs_terminal" in r1["err"]
    out["approve_claudecode"] = r2["rc"] == 2 and "approve_needs_terminal" in r2["err"]
    out["nothing_written"] = not auth.exists()
    # a proposal generates nothing
    fs2 = F.load_set(trace, paths)
    out["proposal_generates_nothing"] = fs2 is not None and fs2.generating() == []
    out["no_door_for_proposals"] = F.generated_for(M.SPEC, trace, paths) is None
    auth.write_bytes(held)
    prop.unlink()
    # an approved form edited by hand is `edited` and generates nothing
    d2 = json.loads(held)
    d2["forms"][1]["form"]["cond"]["value"] = 1
    auth.write_text(json.dumps(d2))
    fs3 = F.load_set(trace, paths)
    assert fs3 is not None
    states = {r.form.id: r.state for r in fs3.resolved}
    out["edited_state"] = states["F2"]
    out["edited_generates_nothing"] = (
        "form:F2:invariant" not in F.generate(M.SPEC, fs3.generating()).names()
    )
    auth.write_bytes(held)
    # a rule rewording makes the form stale; moved text keeps it
    from lattice.spec.rules import Rule, Trace

    rules = list(trace.rules)
    rules[5] = Rule(rules[5].id, "A line's quantity is positive.", rules[5].section, rules[5].line)
    st = {r.form.id: r.state for r in F.resolve(Trace(trace.source, rules), fs.approved)}
    out["reworded_state"] = st["F3"]
    shuffled = [trace.rules[5], *trace.rules[:5], *trace.rules[6:]]
    ren = [Rule(f"R{i + 1}", r.text, r.section, r.line) for i, r in enumerate(shuffled)]
    st2 = {r.form.id: (r.state, r.rule) for r in F.resolve(Trace(trace.source, ren), fs.approved)}
    out["moved_state"] = st2["F3"]
    # draft writes only the proposals file (a recording generator; the claude CLI is not called)
    before = {p.name: p.read_bytes() for p in project.iterdir()}

    class Gen:
        calls = 0

        def generate(self, system: str, messages: list[Any]) -> Any:
            Gen.calls += 1
            from types import SimpleNamespace

            body = {"kind": "bound", "form": {"field": {"column": "Product.stock"},
                    "cond": {"node": "Cmp", "op": "ge", "value": 0}}, "status": "approved"}  # fmt: skip
            return SimpleNamespace(text=json.dumps(body), cost_usd=0.0)

    rep = F.draft_forms(
        M.SPEC, trace, paths, rules=["R4"], out=prop, generator=Gen(), model="probe"
    )
    after = {p.name: p.read_bytes() for p in project.iterdir()}
    out["draft_new_files"] = sorted(set(after) - set(before))
    out["draft_changed_files"] = sorted(k for k in before if before[k] != after.get(k))
    out["draft_status"] = json.loads(prop.read_text())["forms"][0]["status"]
    out["draft_drafted"] = rep["drafted"]
    ok = (
        out["approve_no_tty"] and out["approve_claudecode"] and out["nothing_written"]
        and out["proposal_generates_nothing"] and out["no_door_for_proposals"]
        and out["edited_state"] == "edited" and out["edited_generates_nothing"]
        and out["reworded_state"] == "stale" and out["moved_state"] == ("moved", "R1")
        and out["draft_new_files"] == [prop.name] and out["draft_changed_files"] == []
        and out["draft_status"] == "proposed"
    )  # fmt: skip
    out["moved_state"] = list(out["moved_state"])
    return [row("PA", "approval, states and draft", "PASS" if ok else "FAIL", **out)]


# ---- dev: G and H on the 11 apps ------------------------------------------------------------


def is_g(rule: str) -> bool:
    return "form:" in rule or "(form F" in rule


def apps_for(root: Path, M: Any) -> list[tuple[str, tuple[Path, str]]]:
    out = [("ok", M.write_app(root, "ok"))]
    out += [(v, M.write_app(root, v)) for v in M.BROKEN]
    out += [(f"sec:{v}", M.write_sec_app(root, v)) for v in M.SEC_BROKEN]
    return out


def gh_table(scratch: Path) -> tuple[list[dict[str, Any]], dict[str, float]]:
    from lattice.spec.check import check

    project = fresh_project(scratch, "gh_proj")
    F, M, trace, paths, fs = load_dev(project)
    gen = F.generate(M.SPEC, fs.generating())
    eff = F.effective_spec(M.SPEC, gen)
    root = scratch / "gh_apps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    rows = []
    t0 = time.perf_counter()
    for name, (_d, pkg) in apps_for(root, M):
        rep = check(eff, M.app_factory(pkg), M.build_db, layers="CD", login=M.login)
        g = sorted((f.layer, str(f.rule)) for f in rep.findings if is_g(str(f.rule)))
        h = sorted((f.layer, str(f.rule)) for f in rep.findings if not is_g(str(f.rule)))
        rows.append({"app": name, "G": g, "H": h})
    wall = time.perf_counter() - t0
    base = rows[0]
    for r in rows:
        r["G_new_vs_ok"] = [x for x in r["G"] if x not in base["G"]]
        r["H_new_vs_ok"] = [x for x in r["H"] if x not in base["H"]]
    return rows, {"apps": len(rows), "wall_s": round(wall, 2)}


def dev_table(scratch: Path) -> list[dict[str, Any]]:
    rows, timing = gh_table(scratch)
    g_kills = [r["app"] for r in rows[1:] if r["G_new_vs_ok"]]
    h_kills = [r["app"] for r in rows[1:] if r["H_new_vs_ok"]]
    return [
        row(
            "DEV-GH",
            "ungated: the dev forms' generated checks (G) and the hand-written ones (H) on 11 apps",
            "REPORTED",
            g_kills=g_kills,
            h_kills=h_kills,
            g_superset_of_h=set(h_kills) <= set(g_kills),
            g_findings_on_ok=rows[0]["G"],
            h_findings_on_ok=rows[0]["H"],
            cells=rows,
            timing=timing,
        )  # fmt: skip
    ]


def dev_pairs(scratch: Path) -> list[dict[str, Any]]:
    """Ungated: the builder's own planted pairs (precision/recall on classes a-d) and the
    impact-report widening (PT2w) on the mini-shop, with the dev forms."""
    project = fresh_project(scratch, "pairs_proj")
    F, M, _trace, _paths, _fs = load_dev(project)
    from lattice.spec import forms_tier2 as T2
    from lattice.spec import impact_tier2

    def b(fid: str, op: str, v: Any, col: str = "OrderItem.quantity") -> Any:
        form = {"field": {"column": col}, "cond": {"node": "Cmp", "op": op, "value": v}}
        return F.Form(fid, "R6", "0" * 64, "bound", form, "approved", "hand", "t", None)

    def lim(fid: str, mx: int, where: dict[str, Any] | None = None) -> Any:
        row = {"id": 1, "order_id": 10, "product_id": 50, "quantity": 1, "price": 0.0}
        form: dict[str, Any] = {
            "table": "OrderItem", "by": ["order_id"], "max": mx, "endpoint": "POST /orders/{}/items",
            "caller": "ann", "request": {"path": "/orders/10/items", "json": {"product_id": 50, "quantity": 5}},
            "refused": {"status": 409}, "given": {"order_items": [row]},
        }  # fmt: skip
        if where:
            form["where"] = where
        return F.Form(fid, "R10", "0" * 64, "row_limit", form, "approved", "hand", "t", None)

    def own(fid: str, scope: str = "owner", other: int = 404) -> Any:
        form = {
            "table": "Order", "scope": scope, "endpoints": ["GET /orders/{}"], "owner": "ann",
            "other": "ben", "row": 1, "refused_other": {"status": other},
            "refused_anon": {"status": 401},
        }  # fmt: skip
        return F.Form(fid, "R9", "0" * 64, "ownership", form, "approved", "hand", "t", None)

    def trn(fid: str, to: str) -> Any:
        form = {
            "machine": "Order.status", "trigger": "POST /orders/{}/cancel", "caller": "ann",
            "request": {"path": "/orders/10/cancel"}, "row": {"id": 10, "user_id": 1, "total": 0.0},
            "from": ["pending"], "to": to, "accepted": {"status": 200}, "refused": {"status": 409},
        }  # fmt: skip
        return F.Form(fid, "R1", "0" * 64, "transition", form, "approved", "hand", "t", None)

    contradictory = [
        ("a", [b("F1", "ge", 5), b("F2", "le", 3)]),
        ("a", [b("F1", "gt", 3), b("F2", "lt", 4)]),
        ("a", [b("F1", "ge", 1), b("F2", "lt", 1)]),
        ("b", [lim("F1", 50), lim("F2", 60)]),
        ("c", [own("F1"), own("F2", "any")]),
        ("c", [own("F1"), own("F2", other=403)]),
        ("d", [trn("F1", "cancelled"), trn("F2", "paid")]),
    ]
    compatible = [
        ("a", [b("F1", "ge", 1), b("F2", "le", 1)]),
        ("a", [b("F1", "gt", 3, "Product.price"), b("F2", "lt", 4, "Product.price")]),
        ("a", [b("F1", "ge", 1), b("F2", "le", 100)]),
        ("b", [lim("F1", 5), lim("F2", 5)]),
        (
            "b",
            [lim("F1", 5, {"product_id": 50}), lim("F2", 7, {"product_id": 51})],
        ),
        ("c", [own("F1"), own("F2")]),
        ("d", [trn("F1", "cancelled"), trn("F2", "cancelled")]),
    ]
    tp = fp = fn = 0
    for cls, pair in contradictory:
        got = [i for i in T2.contradictions(M.SPEC, pair) if i["class"] == cls]
        tp += bool(got)
        fn += not got
    for cls, pair in compatible:
        got = [i for i in T2.contradictions(M.SPEC, pair) if i["class"] == cls]
        fp += bool(got)
    # PT2w: two contradictory forms (one on each side) widen tier2.determined by exactly their items
    items = T2.contradictions(M.SPEC, [b("F1", "ge", 5), b("F2", "le", 3)])
    kw: dict[str, Any] = {
        "base": M.SPEC, "cand": M.SPEC, "base_trace": None, "cand_trace": None, "diff": None,
        "targets": {}, "passed": None, "provenance": None, "flows": [], "flow_note": None,
        "base_lines": None, "cand_lines": None,
    }  # fmt: skip
    plain = impact_tier2.compute(**kw)
    wide = impact_tier2.compute(**kw, forms_items=lambda: items)
    pt2w = wide["determined"] == plain["determined"] + items and len(items) == 1
    return [
        row(
            "DEV-PT2f",
            "ungated: the builder's own planted pairs, classes a-d",
            "REPORTED",
            true_positive=tp,
            false_negative=fn,
            false_positive=fp,
            pairs=len(contradictory) + len(compatible),
        ),  # fmt: skip
        row(
            "DEV-PT2w",
            "ungated: tier2.determined gains exactly the form items (compute() level)",
            "PASS" if pt2w else "FAIL",
            items=len(items),
            note="impact_report.py is frozen, so the CLI path does not pass forms_items",
        ),  # fmt: skip
    ]


# ---- dev: PG3, PG4, PX ---------------------------------------------------------------------


def worker_cd(variant: str, root: Path, project: Path) -> None:
    """One CD run in a fresh process: the canonical findings of G + H on one app."""
    F, M, trace, paths, fs = load_dev(project)
    from lattice.spec.check import check

    eff = F.effective_spec(M.SPEC, F.generate(M.SPEC, fs.generating()))
    root.mkdir(parents=True, exist_ok=True)
    _d, pkg = M.write_app(root, variant)
    rep = check(eff, M.app_factory(pkg), M.build_db, layers="CD", login=M.login)
    d = rep.to_dict()
    print(json.dumps({"verdict": d["verdict"], "counts": d["counts"], "findings": d["findings"]},
                     sort_keys=True))  # fmt: skip


def pg3(scratch: Path) -> list[dict[str, Any]]:
    py = sys.executable
    roots = [fresh_project(scratch, "pg3_a"), fresh_project(scratch, "pg3_b")]
    gens, reports = set(), {"ok": set(), "total_drift": set()}
    rng = random.Random(20261017)
    leaks = []
    for i in range(10):
        proj = roots[i % 2]
        seed = str(rng.randrange(1, 2**31))
        env = {"PYTHONPATH": str(REPO), "PYTHONHASHSEED": seed}
        t = str(proj / "spec.trace.json")
        r = run([py, "-m", "lattice.spec", "forms", "generate", "--trace", t, "--spec",
                 "tests.spec.minishop:SPEC"], REPO, env)  # fmt: skip
        gens.add(r["out"])
        if re.search(r"/(tmp|home|Users|private)/|\d{4}-\d\d-\d\dT", r["out"]):
            leaks.append(i)
        for variant in reports:
            w = run(
                [py, str(REPO / "dogfood/forms_probes.py"), "--worker", "cd", "--variant", variant,
                 "--root", str(scratch / f"pg3_apps_{i}"), "--project", str(proj)], REPO, env,
            )  # fmt: skip
            reports[variant].add(w["out"] if w["rc"] == 0 else f"ERR {w['err'][-200:]}")
    ok = len(gens) == 1 and all(len(v) == 1 for v in reports.values()) and not leaks
    return [
        row(
            "PG3",
            "forms generate x10 and CD reports x10, random PYTHONHASHSEED, two scratch roots",
            "PASS" if ok else "FAIL",
            generate_distinct=len(gens),
            cd_distinct={k: len(v) for k, v in reports.items()},
            path_or_time_leaks=leaks,
        )  # fmt: skip
    ]


def pg4_px(scratch: Path) -> list[dict[str, Any]]:
    project = fresh_project(scratch, "pg4_proj")
    F, M, trace, paths, fs = load_dev(project)
    gen = F.generate(M.SPEC, fs.generating())
    problems = F.self_check(M.SPEC, gen)
    ts = []
    for _ in range(50):
        t0 = time.perf_counter()
        F.generate(M.SPEC, fs.generating())
        ts.append(time.perf_counter() - t0)
    ts.sort()
    gmed = ts[len(ts) // 2]
    return [
        row(
            "PG4",
            "every generated invariant compiles (sqlite, mysql); scenarios validate; no L011/L012",
            "PASS" if not problems else "FAIL",
            invariants=len(gen.invariants),
            scenarios=len(gen.scenarios),
            problems=problems,
        ),  # fmt: skip
        row(
            "PX-generate",
            "generate on the dev forms (median of 50)",
            "PASS" if gmed <= 1.0 else "FAIL",
            median_s=round(gmed, 5),
            predicted="0.05-0.3 s",
            gate="<= 1 s",
        ),  # fmt: skip
    ]


# ---- frozen: the held-out set ---------------------------------------------------------------


def manifest_ok(shapes: Path, manifest: Path) -> tuple[bool, str]:
    mine = shapes / "MANIFEST.sha256"
    if not mine.is_file():
        return False, "the shapes directory has no MANIFEST.sha256"
    if mine.read_text() != manifest.read_text():
        return False, "MANIFEST.sha256 differs from the committed copy"
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        f = shapes / rel.strip()
        if not f.is_file() or sha256_file(f) != digest:
            return False, f"{rel.strip()}: missing or hash mismatch"
    return True, "ok"


def frozen(shapes: Path | None, manifest: Path | None, scratch: Path) -> list[dict[str, Any]]:
    why = None
    if shapes is None or not shapes.is_dir():
        why = "the separate agent's frozen rule-shape set is not present in this worktree"
    elif manifest is None or not manifest.is_file():
        why = "no committed shapes.manifest.sha256 to check the set against"
    else:
        ok, detail = manifest_ok(shapes, manifest)
        if not ok:
            raise SystemExit(f"manifest check failed: {detail}")
    if why is not None:
        return [row(p, w, "NOT RUN", reason=why) for p, w in NOT_RUN_FROZEN]
    assert shapes is not None
    try:
        return score_frozen(shapes, scratch)
    except Exception as err:  # noqa: BLE001 -- the layouts above are unvalidated
        reason = f"unrecognised layout in the frozen set: {type(err).__name__}: {err}"
        return [row(p, w, "NOT RUN", reason=reason) for p, w in NOT_RUN_FROZEN]


def adapt_cond(kind: str, form: dict[str, Any]) -> dict[str, Any]:
    """The frozen set writes condition nodes untagged (``{op, value}``, ``{values}``); the
    library's closed node set is tagged (``Cmp``, ``Len``, ``In``). Add the tag, change nothing
    else; a node with no derivable tag (``{any: [...]}``) is left as written for the checker."""
    out = json.loads(json.dumps(form))
    c = out.get("cond")
    if kind == "bound" and isinstance(c, dict) and "node" not in c:
        if "op" in c:
            tag = "Len" if c["op"] in ("min_len", "max_len") else "Cmp"
            out["cond"] = {"node": tag, **c}
        elif "values" in c:
            out["cond"] = {"node": "In", **c}
    return out


def adapt_entry(F: Any, e: dict[str, Any]) -> dict[str, Any]:
    """A forms-file entry with tagged conditions and the approved_sha of the tagged form (the
    set's sha is over the untagged text, which would make every form ``edited``)."""
    e = dict(e)
    e["form"] = adapt_cond(e["kind"], e["form"])
    if e.get("approved_sha") is not None:
        e["approved_sha"] = F.form_sha(e["kind"], e["form"])
    return e


def to_form(F: Any, e: dict[str, Any]) -> Any:
    return F._parse_form(adapt_entry(F, e), 0)  # noqa: SLF001


def scenario_of(d: dict[str, Any]) -> Any:
    from lattice.spec.model import Scenario, Step

    return Scenario(d["name"], d.get("given"), tuple(Step(**s) for s in d["steps"]))


def score_frozen(shapes: Path, scratch: Path) -> list[dict[str, Any]]:
    """PG1, PG1t, PG1o, PG1k, PG2, PG5, PT2f, PT2x, PT2w, PNX on the real layouts of the set."""
    import dataclasses
    import importlib.util

    sys.path.insert(0, str(REPO))
    from lattice.spec import forms as F
    from lattice.spec import forms_tier2 as T2
    from lattice.spec.check import check
    from tests.spec import minishop as M

    ms = shapes / "minishop"
    spec_ = importlib.util.spec_from_file_location("hshapes_handwritten", ms / "handwritten.py")
    assert spec_ is not None and spec_.loader is not None
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    spec, trace = mod.SPEC, mod.TRACE
    raw_forms = json.loads((ms / "forms.json").read_text())
    entries = [adapt_entry(F, e) for e in raw_forms["forms"]]
    forms = tuple(F._parse_form(e, i) for i, e in enumerate(entries))  # noqa: SLF001
    resolved = F.usable(F.resolve(trace, forms))
    states = {r.form.id: r.state for r in F.resolve(trace, forms)}
    try:
        F.generate(spec, resolved)
        whole_set_error = None
    except Exception as err:  # noqa: BLE001 -- recorded as a finding; the CLI exits 2 on it
        whole_set_error = f"{type(err).__name__}: {err}"
    gen_errors: dict[str, str] = {}
    parts = []
    for r in sorted(resolved, key=lambda r: (F._fnum(r.form.id), r.form.id)):  # noqa: SLF001
        try:
            parts.append(F.generate(spec, [r]))
        except Exception as err:  # noqa: BLE001 -- a form that cannot generate yields no check
            gen_errors[r.form.id] = f"{type(err).__name__}: {err}"
    gen = F.Generated(
        tuple(s_ for p_ in parts for s_ in p_.scenarios),
        tuple(i_ for p_ in parts for i_ in p_.invariants),
        {k: v for p_ in parts for k, v in p_.links.items()},
        {k: v for p_ in parts for k, v in p_.no_invariant.items()},
    )
    diags = F.check_forms(spec, trace, list(forms))
    eff = F.effective_spec(spec, gen)
    truth: dict[str, dict[str, bool]] = {}
    for c in json.loads((ms / "truth.json").read_text())["cells"]:
        truth.setdefault(c["rule"], {})[c["app"]] = bool(c["violates"])
    muts = json.loads((ms / "mutants.json").read_text())
    root = scratch / "apps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    apps = apps_for(root, M)
    for mid, (mf, old, new) in muts.items():
        apps.append((mid, M.write_app(root, mid, edit=(mf, old, new))))
    form_rule = {r.form.id: r.rule for r in resolved}
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
            t = truth.get(rid, {}).get(name)
            cells.append({"rule": rid, "app": name, "G": rid in gfail, "H": rid in hfail, "T": t,
                          "Glayers": sorted(gfail.get(rid, ())), "Hlayers": sorted(hfail.get(rid, ())),
                          "unattributed": unattributed})  # fmt: skip
    run_wall = round(time.perf_counter() - t_run, 2)
    scored = [c for c in cells if c["T"] is not None]
    pg1_bad = [c for c in cells if c["G"] != c["H"] and c["H"] == c["T"]]
    pg1_diff = [c for c in cells if c["G"] != c["H"]]
    pg1t_bad = [c for c in scored if c["G"] != c["T"]]
    ok_bad = [c for c in cells if c["app"] == "ok" and c["G"] != bool(c["T"])]
    ok_g = {c["rule"] for c in cells if c["app"] == "ok" and c["G"]}
    ok_h = {c["rule"] for c in cells if c["app"] == "ok" and c["H"]}
    # killed = some cell flips to fail relative to the same rule on ok (prereg 3.2 PG1k)
    g_k = {c["app"] for c in cells if c["app"] != "ok" and c["G"] and c["rule"] not in ok_g}
    h_k = {c["app"] for c in cells if c["app"] != "ok" and c["H"] and c["rule"] not in ok_h}
    g_k_raw = {c["app"] for c in cells if c["app"] != "ok" and c["G"]}
    h_k_raw = {c["app"] for c in cells if c["app"] != "ok" and c["H"]}
    both = [c for c in cells if c["G"] and c["H"]]

    def slim(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{k: c[k] for k in ("rule", "app", "G", "H", "T")} for c in cs]

    out = [
        row("PG1", "G vs H per cell", "PASS" if not pg1_bad else "FAIL", cells=len(cells),
            apps=len(apps), formed_rules=len(set(form_rule.values())), forms_usable=len(resolved),
            form_states=states, generate_whole_set_error=whole_set_error,
            forms_that_could_not_generate=gen_errors, g_eq_h=sum(1 for c in cells if c["G"] == c["H"]),
            g_ne_h=slim(pg1_diff), bad_gne_h_and_h_eq_t=slim(pg1_bad),
            unattributed_generated_findings=sorted({u for c in cells for u in c["unattributed"]}),
            run_wall_s=run_wall),
        row("PG1t", "G vs truth per cell", "PASS" if not pg1t_bad else "FAIL", scored=len(scored),
            g_eq_t=sum(1 for c in scored if c["G"] == c["T"]), bad=slim(pg1t_bad)),
        row("PG1o", "on ok, G fails exactly the rules T marks violated", "PASS" if not ok_bad else "FAIL",
            g_fail_on_ok=sorted(c["rule"] for c in cells if c["app"] == "ok" and c["G"]),
            t_violated_on_ok=sorted(c["rule"] for c in cells if c["app"] == "ok" and c["T"]),
            bad=slim(ok_bad)),
        row("PG1k", "G kills a superset of H's kills", "PASS" if h_k <= g_k else "FAIL",
            g_kills=sorted(g_k), h_kills=sorted(h_k), g_only=sorted(g_k - h_k), h_only=sorted(h_k - g_k),
            g_kills_raw_any_fail=len(g_k_raw), h_kills_raw_any_fail=len(h_k_raw),
            apps_non_ok=len(apps) - 1),
        row("PG2", "layer of the failing check where both fail", "REPORTED", cells_both_fail=len(both),
            same_layer_sets=sum(1 for c in both if c["Glayers"] == c["Hlayers"]),
            overlapping_layers=sum(1 for c in both if set(c["Glayers"]) & set(c["Hlayers"])),
            diff=[{k: c[k] for k in ("rule", "app", "Glayers", "Hlayers")}
                  for c in both if c["Glayers"] != c["Hlayers"]]),
    ]  # fmt: skip

    # PG5: each invalid entry in a forms file of the valid forms plus the entry
    inv = json.loads((shapes / "invalid.json").read_text())["invalid"]
    wrong, accepted, refused_ok = [], [], []
    for x in inv:
        if "entries" in x:
            fs = [to_form(F, e) for e in x["entries"]]
        else:
            fs = [*forms, to_form(F, x["entry"])]
        sp = spec
        if x.get("spec_extra_scenarios"):
            sp = dataclasses.replace(
                spec,
                scenarios=(*spec.scenarios, *(scenario_of(s) for s in x["spec_extra_scenarios"])),
            )
        got = sorted({d.code for d in F.check_forms(sp, trace, fs)})
        want = {x["code"], *x.get("also_acceptable", ())}
        if not got:
            accepted.append(x["id"])
        elif x["code"] not in got:
            wrong.append({"id": x["id"], "want": x["code"], "got": got,
                          "also_acceptable_hit": bool(want & set(got))})  # fmt: skip
        else:
            refused_ok.append(x["id"])
    valid_refused = [d.render() for d in diags]
    out.append(
        row("PG5", "invalid.json refused with the expected code; valid forms accepted",
            "PASS" if not accepted and not valid_refused else "FAIL", invalid=len(inv),
            refused_with_expected_code=len(refused_ok), accepted_invalid=accepted,
            valid_refused=valid_refused, valid_forms=len(forms), codes_not_exact=wrong)
    )  # fmt: skip

    # PT2f / PT2x on pairs.json
    pairs = json.loads((shapes / "pairs.json").read_text())["pairs"]
    byid = {e["id"]: e for e in entries}
    res = []
    for p in pairs:
        fs: list[Any] = []
        extra = []
        for side, fid in (("a", "PA"), ("b", "PB")):
            s = p[side]
            if "ref" in s:
                fs.append(F._parse_form(byid[s["ref"]], 0))  # noqa: SLF001
            elif "scenario" in s:
                extra.append(scenario_of(s["scenario"]))
            else:
                fs.append(to_form(F, {"id": fid, "rule": "R1", "anchor": "0" * 64, "kind": s["kind"],
                                      "form": s["form"], "status": "approved", "author": "hand",
                                      "approved_sha": "x"}))  # fmt: skip
        sp = dataclasses.replace(spec, scenarios=(*spec.scenarios, *extra)) if extra else spec
        if len({f.id for f in fs}) < len(fs):
            fs[1] = dataclasses.replace(fs[1], id="F9999")
        err = None
        try:
            items = T2.contradictions(sp, fs)
            ids = {f.id for f in fs}
            items = [
                i for i in items if all(x in ids or x.startswith("scenario:") for x in i["forms"])
            ]
        except Exception as e:  # noqa: BLE001 -- recorded: the pair was not decided
            items, err = [], f"{type(e).__name__}: {e}"
        res.append({"id": p["id"], "class": p["class"], "expect": p["expect"], "items": items,
                    "error": err, "got": "error" if err else "contradict" if items else "compatible"})  # fmt: skip
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

    recall_abcd = by_class("abcd")
    ok_abcd = recall_abcd["found"] == recall_abcd["contradict"]
    out.append(
        row("PT2f", "forms contradictions on pairs.json",
            "PASS" if not fp and ok_abcd and not any(r["error"] for r in res) else "FAIL",
            pairs=len(res), true_pos=len(tp), false_neg=[r["id"] for r in fn],
            false_pos=[{"id": r["id"], "items": [i["id"] for i in r["items"]]} for r in fp],
            true_neg=len(tn), errors=[{"id": r["id"], "expect": r["expect"], "error": r["error"]}
                                      for r in res if r["error"]],
            precision=(len(tp) / (len(tp) + len(fp))) if tp or fp else None,
            recall_abcd=f"{recall_abcd['found']}/{recall_abcd['contradict']}",
            class_e=by_class("e"), per_class={c: by_class(c) for c in "abcde"})
    )  # fmt: skip
    no_witness = [r["id"] for r in tp if any(i.get("witness") is None for i in r["items"])]
    out.append(
        row("PT2x", "every emitted item carries a witness (the library emits only self-checked items)",
            "PASS" if not no_witness else "FAIL", items=sum(len(r["items"]) for r in res),
            items_without_witness=no_witness,
            note="no independent re-derivation of the incompatible expectations is done by this runner")
    )  # fmt: skip

    ne = json.loads((shapes / "not_expressible.json").read_text())["rules"]
    ne_acc, ne_wrong, tried = [], [], 0
    for x in ne:
        a = x.get("attempt")
        if not a:
            continue
        tried += 1
        f = to_form(F, {"id": "F901", "rule": "R1", "anchor": "0" * 64, "kind": a["kind"],
                        "form": a["form"], "status": "approved", "author": "hand"})  # fmt: skip
        got = sorted({d.code for d in F.check_forms(spec, None, [f])})
        if not got:
            ne_acc.append(x["id"])
        elif not set(got) & set(a.get("expect_codes", ())):
            ne_wrong.append({"id": x["id"], "want": a.get("expect_codes"), "got": got})
    out.append(
        row("PNX", "not_expressible attempts refused", "PASS" if not ne_acc else "FAIL",
            rules=len(ne), with_attempt=tried, accepted=ne_acc, refused_with_other_code=ne_wrong,
            without_attempt=[x["id"] for x in ne if not x.get("attempt")])
    )  # fmt: skip
    out.append(pt2w_cli(scratch))
    return out


def pt2w_cli(scratch: Path) -> dict[str, Any]:
    """PT2w through ``lattice spec impact`` (CLI): project = the dev minishop fixture with its
    forms file, candidate = a copy whose forms file gains a form contradicting an existing one."""
    py = sys.executable
    proj = scratch / "pt2w" / "project"
    cand = scratch / "pt2w" / "candidate"
    for d in (proj, cand):
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(FIX, d)
    fp = next(cand.glob("*.forms.json"))
    data = json.loads(fp.read_text())
    from lattice.spec import forms as F

    base = next(f for f in data["forms"] if f["id"] == "F3")
    e = json.loads(json.dumps(base))
    e["id"] = "F90"
    e["rule"] = "R7"
    e["form"]["cond"] = {"node": "Cmp", "op": "lt", "value": 1}
    e["approved_sha"] = F.form_sha(e["kind"], e["form"])
    data["forms"].append(e)
    fp.write_text(json.dumps(data, indent=2) + "\n")
    cmd = [py, "-m", "lattice.spec", "impact", "--project", str(proj), "--candidate", str(cand),
           "--tiers", "2", "--json", "--no-flows", "--baseline", "none"]  # fmt: skip
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, timeout=300)
    try:
        rep = json.loads(p.stdout)
        tier2_present = "tier2" in rep
        det = rep.get("tier2", {}).get("determined", [])
        form_items = [d for d in det if str(d.get("id", "")).startswith("fc:")]
    except ValueError:
        tier2_present, det, form_items = False, None, None
    ok = bool(form_items)
    return row("PT2w", "impact report (CLI) gains the form contradiction items",
               "PASS" if ok else "FAIL", exit_code=p.returncode, tier2_present=tier2_present,
               form_items_in_tier2_determined=None if form_items is None else len(form_items),
               determined_total=None if det is None else len(det), stderr_tail=p.stderr[-300:],
               note="impact_report.py is frozen (hash-checked) and never passes forms_items")  # fmt: skip


# ---- main ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--stage", choices=("dev", "frozen"))
    ap.add_argument("--scratch", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--shapes", type=Path)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--worker")
    ap.add_argument("--variant", default="ok")
    ap.add_argument("--root", type=Path)
    ap.add_argument("--project", type=Path)
    args = ap.parse_args(argv)
    if args.worker == "cd":
        worker_cd(args.variant, args.root, args.project)
        return 0
    if not args.stage or not args.scratch or not args.out:
        ap.error("--stage, --scratch and --out are required")
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    if args.stage == "dev":
        for fn in (pb, pw, pa, pg3, pg4_px, dev_pairs, dev_table):
            t = time.perf_counter()
            rows += fn(args.scratch)
            print(f"{fn.__name__}: {time.perf_counter() - t:.1f} s", file=sys.stderr)
    else:
        rows += frozen(args.shapes, args.manifest, args.scratch)
    rows.append(row("wall", "total", "REPORTED", seconds=round(time.perf_counter() - t0, 1)))
    (args.out / f"{args.stage}.json").write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    for r in rows:
        print(f"{r['probe']}: {r['status']}" + (f" ({r['reason']})" if "reason" in r else ""))
    return 0 if all(r["status"] in ("PASS", "NOT RUN", "REPORTED") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
