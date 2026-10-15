"""Probe runner for ``lattice contracts impact --transitive`` (multi-project slice 4c).

Implements the probes of ``docs/results/2026-10-15-transitive-impact-prereg.md`` (section 3):
T-basis, T-chain, T-diamond, T-cycle, T-long, T-edge, T-additive, T-flagoff, T-parity, T-safety
and T-cost, and (``--mutants``) the mutants TR1-TR14 of section 4. Predictions and pass rules are
copied from that document; a probe that does not match is reported as a finding with its raw
output, never adapted.

    python dogfood/contracts/transitive_probes.py --runs 10 --scratch DIR --base DIR/base \
        --out docs/results/data/2026-10-15-transitive-impact [--only T-chain,...] [--regression]
    python dogfood/contracts/transitive_probes.py --mutants --scratch DIR [--only TR1,TR5]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 and of
the committed ``dogfood/contracts/ci-bot`` under ``DIR``; the real repositories are only read
(T-safety proves it). No LLM, no API key, no network. Every deterministic probe runs ``--runs``
times (default 10) and all canonical outputs (``elapsed_s`` excluded) must be identical. Raw
per-run outputs go to ``OUT/<ID>.json`` and ``OUT/summary.json``. ``--base`` is the directory that
holds ``lattice/`` extracted with ``git archive a4ab4a1 lattice`` (T-flagoff's old code).

The helpers (Ctx, Edit, Result, canonical outputs, the edit tables, the tracer, the MCP access)
are those of ``probes.py``, ``exclude_probes.py`` and ``impact_probes.py``, imported from the
files next to this one. **Mutant mode** copies ``lattice/`` and ``tests/contracts`` into a scratch
directory per mutant, applies one edit, runs ``tests/contracts/test_transitive.py`` there and
requires it to fail (a control run of the unmodified copy must pass); the worktree is untouched.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:  # the in-process library calls of T-parity
    sys.path.insert(0, str(REPO))


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


ip = _load("lattice_contracts_impact_probes_t", HERE / "impact_probes.py")
ep = ip.ep
base = ip.base
streak = ip.streak
Result = base.Result
canon_call = base.canon_call
strip_elapsed = base.strip_elapsed
mk = ip.mk
yn = ip.yn

COMMITTED = REPO / "docs" / "results" / "data"
I1_COMMITTED = COMMITTED / "2026-10-13-contracts-impact" / "I1.json"
OLD_REF = "a4ab4a1"
LOCK = base.LOCK
IMPORTS = "lattice.imports.toml"
MANIFEST = base.MANIFEST
BUILDS = "GET /builds/{}"
DOC_BLOCK = "downstream ("
IMPACTED = ["ci-bot", "ci-dash", "ops"]

POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "A first development run (--runs 1) crashed T-additive with a KeyError: the flag-off-only "
    "records that T-flagoff stores (I4 rows, notify) were also registered as flagged pairs. They "
    "are now registered only when the case has a flag-on call.",
    "The same development run failed T-safety's 'export children equal with and without the "
    "flag' on a grouping error of my script: calls were paired by (probe, label) where a label "
    "named the provider and candidate only, so T-cycle's four manifests (2 children; 0 for the "
    "manifest error) shared one label. The label now ends with the manifest's world and "
    "variant directory. No pass rule changed; no product code changed.",
]
POST_HOC_PROCEDURE: list[str] = [
    "Helpers: JCtx and SX (exclude_probes.py), the Tracer, H.ref and mcp_impact "
    "(impact_probes.py) are reused. A candidate ref dN is the committed edit list applied to a "
    "clone of the clean clone (`ctx.clone`, the runners' fixed author and dates, so the sha is "
    "the committed Dn's) and fetched into the provider clone as refs/heads/dN before the "
    "measured window.",
    "Each case makes four traced calls per run: `impact --json` and `impact` (text), with and "
    "without --transitive. The canonical record holds all four (JSON without elapsed_s). "
    "T-additive and T-flagoff read them back from the first run (every run is identical to it "
    "by the 10/10 rule). The old-code (a4ab4a1) calls of T-flagoff are made once per case, "
    "untraced, with PYTHONPATH = the extracted `lattice/` and compared with the worktree's "
    "flag-off record of every run.",
    "The ci-bot provider is a scratch copy of dogfood/contracts/ci-bot + the committed golden "
    "lock (it is also a consumer of the scheduler) + a generated spec.py (one scenario, "
    "anonymous GET /builds/1 -> 200 with body {id, state}); ci-dash relies on `GET /builds/{}` "
    "of ci-bot (status [200], public, response_fields [state]) and is pinned with `contracts "
    "pin ci-dash` before the window; `ops` is an empty directory.",
    "`roots[].dependents` is a sorted list of project names (the prereg's 'root dependents 1' "
    "is its length).",
    "T-parity: the prereg says 4/4 but names three in-process library comparisons (T-chain D1, "
    "T-diamond b, T-cycle c) plus MCP and API: five checks are recorded, all must hold.",
]

CHAIN_EXPECT: dict[str, tuple[str, int, str | None]] = {
    "D1": ("contradiction", 1, base.LIST_RUNS),
    "D2": ("contradiction", 1, base.RUN_EP),
    "D3": ("contradiction", 1, base.CANCEL),
    "D4": ("contradiction", 1, base.LOGIN),
    "D5": ("contradiction", 1, base.RUN_GET),
    "D6": ("unaffected", 0, None),
    "D7": ("unaffected", 0, None),
    "D8": ("regression", 0, None),
    "D9": ("regression", 0, None),
    "D10": ("unaffected", 0, None),
}

MINIMAL_SPEC = """\
from lattice.profile import ApiProfile, Profile
from lattice.spec import Scenario, Spec, Step

SPEC = Spec(
    Profile(
        schemas={},
        api=ApiProfile(
            tables={}, table_names={}, roles=frozenset({"Public"}), error_body="fastapi"
        ),
    ),
    scenarios=(
        Scenario(
            "build",
            steps=(
                Step("GET", "/builds/1", None, expect_status=200,
                     expect_body={"id": 1, "state": "ok"}),
            ),
        ),
    ),
    users={"ann": ("ann@x.io", "pw")},
)
"""
DASH_IMPORT = (
    '[[import]]\nprovider = "{p}"\nendpoint = "GET /builds/{{id}}"\nstatus = [200]\n'
    'auth = "public"\nresponse_fields = ["state"]\n\n'
)
EVE_IMPORT = (
    '[[import]]\nprovider = "scheduler"\nendpoint = "GET /applications"\nstatus = [200]\n'
    'auth = "token"\nas_user = "eve"\nresponse_fields = ["id", "name"]\n\n'
)


# ---- workspaces -------------------------------------------------------------------------------


def write_ws(path: Path, projects: list[tuple[str, Path, str | None]],
             links: list[tuple[str, str, str, str | None]]) -> Path:  # fmt: skip
    """A manifest: `projects` (name, path, spec or None) and `links` (from, to, kind, contract
    or None)."""
    text = '[workspace]\nname = "probe"\n\n'
    for name, p, spec in projects:
        role = "provider" if name == "scheduler" else "consumer"
        text += f'[[project]]\nname = "{name}"\npath = "{p}"\nrole = "{role}"\n'
        if spec:
            text += f'spec = "{spec}"\n'
        text += "\n"
    for src, dst, kind, contract in links:
        text += f'[[link]]\nfrom = "{src}"\nto = "{dst}"\nkind = "{kind}"\n'
        if contract:
            text += f'contract = "{contract}"\n'
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class World:
    """A workspace directory: the provider clone, the project directories and manifest variants."""

    def __init__(self, jc: Any, name: str, clone: Path) -> None:
        self.jc, self.name, self.clone = jc, name, clone
        self.d = jc.work / name
        self.d.mkdir(parents=True, exist_ok=True)
        self.dirs: dict[str, Path] = {}

    @property
    def watch(self) -> list[Path]:
        return [self.d, self.clone]

    def ci_bot(self, name: str = "ci-bot") -> Path:
        """ci-bot (or ci-bot-2): the committed consumer + golden lock + a generated spec.py."""
        dst = self.d / name
        shutil.copytree(base.CI_BOT, dst, ignore=shutil.ignore_patterns("__pycache__", LOCK))
        (dst / LOCK).write_bytes(self.jc.clean_lock())
        (dst / "spec.py").write_text(MINIMAL_SPEC)
        self.dirs[name] = dst
        return dst

    def consumer(self, name: str, text: str = "") -> Path:
        """An empty directory, or one with a ``lattice.imports.toml`` holding `text`."""
        dst = self.d / name
        dst.mkdir(exist_ok=True)
        if text:
            (dst / IMPORTS).write_text(text)
        self.dirs[name] = dst
        return dst

    def manifest(self, variant: str, links: list[tuple[str, ...]],
                 names: list[str] | None = None, paths: dict[str, Path] | None = None,
                 specs: tuple[str, ...] = ("ci-bot", "ci-bot-2")) -> Path:  # fmt: skip
        paths = paths or {}
        projects: list[tuple[str, Path, str | None]] = [
            ("scheduler", self.clone, "spec.tickets:t5")
        ]
        for n in names if names is not None else sorted(self.dirs):
            projects.append((n, paths.get(n, self.dirs[n]), None))
        full = [(lk[0], lk[1], lk[2] if len(lk) > 2 else "uses",
                 (lk[3] if len(lk) > 3 else "static") or None) for lk in links]  # fmt: skip
        return write_ws(self.d / f"m-{variant}" / MANIFEST, projects, full)

    def pin(self, manifest: Path, consumer: str) -> None:
        c = self.jc.contracts(manifest, "pin", consumer, "--json")
        assert c.rc == 0, c.out + c.err


def chain_world(h: Any, name: str, clone: Path, *, ci_dash: bool = True) -> tuple[World, Path]:
    """T-chain: scheduler <- ci-bot <- ci-dash, ops -> ci-bot (no contract). Returns the world
    and its main manifest; ci-dash is pinned (on the clean ci-bot) before anything is measured."""
    w = World(h.jc, name, clone)
    w.ci_bot()
    if ci_dash:
        w.consumer("ci-dash", DASH_IMPORT.format(p="ci-bot"))
    w.consumer("ops")
    links: list[tuple[str, ...]] = [("ci-bot", "scheduler")]
    if ci_dash:
        links.append(("ci-dash", "ci-bot"))
    links.append(("ops", "ci-bot", "uses", ""))
    m = w.manifest("main", links)
    if ci_dash:
        w.pin(m, "ci-dash")
    return w, m


# ---- the runner state -------------------------------------------------------------------------


class T:
    """The shared state of a run: the calls, the per-case records for T-additive and
    T-flagoff, the committed rows."""

    def __init__(self, h: Any, base_dir: Path | None) -> None:
        self.h, self.jc, self.sc = h, h.jc, h.sc
        self.base_dir = base_dir
        self.pairs: dict[str, dict[str, Any]] = {}  # case -> record of the first run
        self.off: dict[str, list[dict[str, Any]]] = {}  # case -> flag-off records, every run
        self.bases: dict[str, dict[str, Any]] = {}  # case -> old-code record
        self.i1 = json.loads(I1_COMMITTED.read_text())["runs"][0]
        self.refs: dict[str, str] = {}

    def committed_row(self, did: str) -> dict[str, Any]:
        return self.i1[did]["doc"]["consumers"][0]  # type: ignore[no-any-return]

    def drift(self, did: str) -> dict[str, Any]:
        return next(d for d in base.DRIFTS if d["id"] == did)

    def ref(self, clone: Path, did: str) -> str:
        _c, sha = self.h.ref(self.jc, clone, did.lower(), self.drift(did)["edits"])
        self.refs[did] = sha
        return sha

    # -- calls
    def call(self, ctx: Any, m: Path, provider: str, cand: str, *, probe: str, watch: list[Path],
             transitive: bool = False, text: bool = False, expect: int = 2,
             old: bool = False, cmd: str = "impact",
             extra: tuple[str, ...] = ()) -> Any:  # fmt: skip
        argv = [sys.executable, "-m", "lattice.contracts", cmd, provider]
        argv += [f"--candidate={cand}"] if cand.startswith("-") else ["--candidate", cand]
        argv += ["--workspace", str(m), *extra]
        if transitive:
            argv.append("--transitive")
        if not text:
            argv.append("--json")
        try:
            if old:
                env = dict(ctx.env)
                env["PYTHONPATH"] = str(self.base_dir)
                env.pop("IMPACT_PROBE_LOG", None)
                return ctx.run(argv, env=env, timeout=120)
            label = (
                f"{cmd} {provider} {cand}{' --transitive' if transitive else ''}"
                f"{' (text)' if text else ''} @{m.parent.parent.name}/{m.parent.name}"
            )
            with self.h.tracer.around(probe, label, ctx, watch, expect):
                return ctx.run(argv, timeout=60)
        except subprocess.TimeoutExpired:
            return base.Call(argv, 124, "", "timeout", 60.0)

    def case(self, key: str, m: Path, provider: str, cand: str, *, probe: str, watch: list[Path],
             expect: int = 2, flag_on: bool = True, ctx: Any = None) -> dict[str, Any]:  # fmt: skip
        """The four calls of one case (and, once, the old-code pair), as one record."""
        ctx = ctx or self.jc
        rec: dict[str, Any] = {}
        for flag in (True, False) if flag_on else (False,):
            tag = "on" if flag else "off"
            j = self.call(ctx, m, provider, cand, probe=probe, watch=watch, transitive=flag,
                          expect=expect)  # fmt: skip
            t = self.call(ctx, m, provider, cand, probe=probe, watch=watch, transitive=flag,
                          text=True, expect=expect)  # fmt: skip
            rec[f"json_{tag}"] = canon_call(j)
            rec[f"text_{tag}"] = {"rc": t.rc, "out": t.out, "err": t.err}
        if self.base_dir is not None and key not in self.bases:
            j = self.call(ctx, m, provider, cand, probe=probe, watch=watch, old=True)
            t = self.call(ctx, m, provider, cand, probe=probe, watch=watch, text=True, old=True)
            self.bases[key] = {"json": canon_call(j),
                               "text": {"rc": t.rc, "out": t.out, "err": t.err}}  # fmt: skip
        if flag_on:
            self.pairs.setdefault(key, rec)
        self.off.setdefault(key, []).append({"json": rec["json_off"], "text": rec["text_off"]})
        return rec


def tdoc(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["json_on"]["doc"]["transitive"]  # type: ignore[no-any-return]


def fdoc(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["json_on"]["doc"]  # type: ignore[no-any-return]


def rows(rec: dict[str, Any]) -> list[dict[str, Any]]:
    return tdoc(rec)["downstream"]  # type: ignore[no-any-return]


def names(rec: dict[str, Any]) -> list[str]:
    return [r["consumer"] for r in rows(rec)]


def empty_t() -> dict[str, Any]:
    return {"basis": "declared_links", "verified": False, "propagates_from": ["contradiction"],
            "roots": [], "downstream": [], "notes": [],
            "counts": {"roots": 0, "downstream": 0}}  # fmt: skip


def first_order_eq(row: dict[str, Any], comm: dict[str, Any]) -> bool:
    return all(row[k] == comm[k] for k in ("label", "findings", "gaps", "counts"))


def row_text(rec: dict[str, Any]) -> str:
    return rec["text_on"]["out"]


def additive_ok(rec: dict[str, Any]) -> bool:
    on, off = rec["json_on"], rec["json_off"]
    ton, toff = rec["text_on"], rec["text_off"]
    if "doc" not in on or "doc" not in off:
        return False
    rest = {k: v for k, v in on["doc"].items() if k != "transitive"}
    return bool(
        "transitive" in on["doc"]
        and "transitive" not in off["doc"]
        and rest == off["doc"]
        and on["rc"] == off["rc"] == ton["rc"] == toff["rc"]
        and on["err"] == off["err"] == ton["err"] == toff["err"] == ""
        and ton["out"].startswith(toff["out"])
        and ton["out"][len(toff["out"]) :].startswith(DOC_BLOCK)
        and DOC_BLOCK not in toff["out"]
    )


def link_ok(row: dict[str, Any], provider: str, relies: Any, pinned: Any, notes: list[str],
            contract: str = "static") -> bool:  # fmt: skip
    return row["links"] == [{"provider": provider, "contract": contract, "relies_on": relies,
                             "pinned": pinned, "notes": notes}]  # fmt: skip


def workspace_impact(ctx: Any, m: Path, provider: str) -> list[str]:
    c = ctx.run([sys.executable, "-m", "lattice.workspace", "impact", provider, "--json",
                 "--workspace", str(m)])  # fmt: skip
    doc = c.json()
    return [str(x) if not isinstance(x, dict) else str(x.get("name")) for x in doc["impacted"]]


# ---- T-basis ----------------------------------------------------------------------------------


def t_basis(t: T) -> Result:
    pred = (
        "`contracts export ci-bot` with the scheduler clone at HEAD and at each of d1..d5 (outside "
        "any impact call): spec_hash identical 6 of 6 (ci-bot's export does not depend on the "
        "scheduler)"
    )
    jc = t.jc
    clean = ep.shared(jc)
    dids = ["D1", "D2", "D3", "D4", "D5"]
    for did in dids:
        t.ref(clean, did)
    w = World(jc, "T-basis", jc.clone("t-basis-prov"))
    w.ci_bot()
    m = w.manifest("main", [("ci-bot", "scheduler")])
    head = jc.git(w.clone, "rev-parse", "HEAD").strip()
    for did in dids:
        c_clone, _sha = t.h.ref(jc, clean, did.lower(), t.drift(did)["edits"])
        jc.git(w.clone, "fetch", "-q", str(c_clone), f"HEAD:refs/heads/{did.lower()}")
    order = [("HEAD", head)] + [(d, t.refs[d]) for d in dids]

    def one(_: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label, sha in order:
            jc.git(w.clone, "checkout", "-q", "--detach", sha)
            exp = jc.run([sys.executable, "-m", "lattice.contracts", "export", "ci-bot",
                          "--workspace", str(m)])  # fmt: skip
            sched = jc.run([sys.executable, "-m", "lattice.contracts", "export", "scheduler",
                            "--workspace", str(m)])  # fmt: skip
            out[label] = {
                "rc": exp.rc,
                "spec_hash": json.loads(exp.out)["version"]["spec_hash"] if exp.rc == 0 else None,
                "stdout_sha": base.sha(exp.out.encode()),
                "scheduler_spec_hash": json.loads(sched.out)["version"]["spec_hash"],
            }
        jc.git(w.clone, "checkout", "-q", "--detach", head)
        return out

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    hashes = {k: v["spec_hash"] for k, v in r0.items()}
    sched = {k: v["scheduler_spec_hash"] for k, v in r0.items()}
    checks = {
        "all_exports_ok": all(v["rc"] == 0 for v in r0.values()),
        "six_of_six_identical_spec_hash": len(set(hashes.values())) == 1 and len(hashes) == 6,
        "six_of_six_identical_stdout": len({v["stdout_sha"] for v in r0.values()}) == 1,
        "scheduler_export_does_move": len(set(sched.values())) > 1,
    }
    obs = (
        f"ci-bot spec_hash identical {sum(1 for v in hashes.values() if v == hashes['HEAD'])}/6 "
        f"({next(iter(hashes.values()))[:12]}); the scheduler's own spec_hash takes "
        f"{len(set(sched.values()))} distinct values over the same 6 checkouts"
    )
    return mk("T-basis", pred, obs, runs, ident, checks)


# ---- T-chain ----------------------------------------------------------------------------------


def t_chain(t: T) -> Result:
    pred = (
        "impact scheduler --candidate dN --transitive, N=1..10 (ci-bot <- ci-dash): D1-D5 "
        "first-order "
        "contradiction exit 1 = committed row, roots [ci-bot] with its one BROKEN endpoint, one "
        "downstream row ci-dash (depth 2, via/roots [ci-bot], 1 link: static, relies_on "
        "[GET /builds/{}], pinned = the setup pin, notes []); D6/D7/D10 unaffected exit 0, D8/D9 "
        "regression exit 0: roots [] downstream []; ops never listed; workspace impact lists 3 "
        "projects for every D; 5 roots, 5 rows in all"
    )
    jc = t.jc
    clone = ep.shared(jc)
    dids = list(CHAIN_EXPECT)
    for did in dids:
        t.ref(clone, did)
    w, m = chain_world(t.h, "T-chain", clone)
    pin = json.loads((w.dirs["ci-dash"] / LOCK).read_text())["providers"]["ci-bot"]
    pin_v = {"spec_ref": pin["spec_ref"], "spec_hash": pin["version"]["spec_hash"],
             "git_ref": pin["version"]["git_ref"], "dirty": pin["version"]["dirty"]}  # fmt: skip
    comm = {d: t.committed_row(d) for d in dids}

    def one(_: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for did in dids:
            rec = t.case(f"T-chain/{did}", m, "scheduler", did.lower(), probe="T-chain",
                         watch=w.watch)  # fmt: skip
            rec["workspace_impacted"] = workspace_impact(jc, m, "scheduler")
            out[did] = rec
        return out

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    n_roots = n_rows = 0
    for did in dids:
        rec = r0[did]
        label, rc, endpoint = CHAIN_EXPECT[did]
        doc = fdoc(rec)
        (row,) = doc["consumers"]
        tt = tdoc(rec)
        contradiction = endpoint is not None
        ok_first = (
            first_order_eq(row, comm[did])
            and row["label"] == label
            and rec["json_on"]["rc"] == rc
            and doc["exit_code"] == rc
        )
        if contradiction:
            ok_t = (
                tt["roots"] == [{"consumer": "ci-bot", "endpoints": [endpoint],
                                 "dependents": ["ci-dash"]}]
                and len(rows(rec)) == 1
                and rows(rec)[0]["consumer"] == "ci-dash"
                and rows(rec)[0]["label"] == "downstream"
                and rows(rec)[0]["depth"] == 2
                and rows(rec)[0]["via"] == ["ci-bot"]
                and rows(rec)[0]["roots"] == ["ci-bot"]
                and link_ok(rows(rec)[0], "ci-bot", [BUILDS], pin_v, [])
                and tt["notes"] == []
                and tt["counts"] == {"roots": 1, "downstream": 1}
            )  # fmt: skip
            n_roots += 1
            n_rows += 1
        else:
            ok_t = tt == empty_t()
        sub = {
            "first_order_equals_committed_label_exit": ok_first,
            "transitive_as_predicted": ok_t,
            "ops_never_listed": "ops" not in json.dumps(tt),
            "counts_untouched": doc["counts"]
            == {
                "contradiction": int(contradiction),
                "regression": int(label == "regression"),
                "unaffected": int(label == "unaffected"),
                "error": 0,
            },  # fmt: skip
            "checked_1": doc["checked"] == 1
            and [r["consumer"] for r in doc["consumers"]] == ["ci-bot"],  # fmt: skip
            "workspace_impact_lists_3": rec["workspace_impacted"] == IMPACTED,
            "additive": additive_ok(rec),
        }
        for k, v in sub.items():
            checks[f"{did}_{k}"] = v
        parts.append(f"{did} {row['label']}/{rc} roots {len(tt['roots'])} rows "
                     f"{len(tt['downstream'])}{'' if all(sub.values()) else ' !'}")  # fmt: skip
    checks["totals_5_roots_5_rows"] = (n_roots, n_rows) == (5, 5)
    obs = f"{'; '.join(parts)}; workspace impact {r0['D1']['workspace_impacted']}"
    return mk("T-chain", pred, obs, runs, ident, checks)


# ---- T-diamond --------------------------------------------------------------------------------


def diamond_world(t: T, name: str) -> tuple[World, Path]:
    """scheduler, ci-bot, ci-bot-2 (4a I4's workspace A) with the minimal specs, `dash`
    (imports `GET /builds/{}` from both, pinned on both), `dash-d` (+ the scheduler's
    `GET /applications` as eve) and ci-dash / dash2 for row e."""
    h, jc = t.h, t.jc
    clean = ep.shared(jc)
    ma = ip.multi_a(h, name, clean)  # scheduler, ci-bot, ci-bot-2 (pinned on the clean clone)
    w = World(jc, name, clean)
    w.dirs["ci-bot"] = w.d / "ci-bot"
    w.dirs["ci-bot-2"] = w.d / "ci-bot-2"
    for n in ("ci-bot", "ci-bot-2"):
        (w.dirs[n] / "spec.py").write_text(MINIMAL_SPEC)
    both = DASH_IMPORT.format(p="ci-bot") + DASH_IMPORT.format(p="ci-bot-2")
    w.consumer("dash", both)
    dd = w.d / "dash-d"
    dd.mkdir(exist_ok=True)
    (dd / IMPORTS).write_text(both + EVE_IMPORT)
    w.consumer("ci-dash", DASH_IMPORT.format(p="ci-bot"))
    w.consumer("dash2")
    base_links: list[tuple[str, ...]] = [("ci-bot", "scheduler"), ("ci-bot-2", "scheduler")]
    pins = w.manifest("pins", [*base_links, ("dash", "ci-bot"), ("dash", "ci-bot-2"),
                               ("ci-dash", "ci-bot")],
                      names=["ci-bot", "ci-bot-2", "dash", "ci-dash"])  # fmt: skip
    for c in ("dash", "ci-dash"):
        w.pin(pins, c)
    pin_d = w.manifest("pins-d", [*base_links, ("dash", "ci-bot"), ("dash", "ci-bot-2"),
                                  ("dash", "scheduler")],
                       names=["ci-bot", "ci-bot-2", "dash"], paths={"dash": dd})  # fmt: skip
    w.pin(pin_d, "dash")
    assert ma.is_file()
    return w, ma


def t_diamond(t: T) -> Result:
    pred = (
        "a D1: tier 1 = 4a I4-a (ci-bot contradiction, ci-bot-2 unaffected), roots [ci-bot], one "
        "row dash (depth 2, via [ci-bot], roots [ci-bot], 1 link), exit 1; b D4: both "
        "contradiction, roots [ci-bot, ci-bot-2], ONE row dash, via and roots both, 2 links, exit "
        "1; c D8: both regression, roots [] downstream [], exit 0; d = a + dash also imports the "
        "scheduler's GET /applications: tier 1 adds dash unaffected (exactly 1 unrelied CHANGED "
        "endpoint_removed, 0 gaps), dash also downstream as in a, checked 3, exit 1; e = a + "
        "ci-dash + dash2 (dash2 -> ci-bot, ci-dash): rows ci-dash, dash, dash2 (depth 2), "
        "dash2.via [ci-bot, ci-dash], roots [ci-bot], notes [no_imports, no_pin]"
    )
    jc = t.jc
    clean = ep.shared(jc)
    for did in ("D1", "D4", "D8"):
        t.ref(clean, did)
    w, _ma = diamond_world(t, "T-diamond")
    sched = [("ci-bot", "scheduler"), ("ci-bot-2", "scheduler")]
    dash = [("dash", "ci-bot"), ("dash", "ci-bot-2")]
    m_a = w.manifest("a", [*sched, *dash], names=["ci-bot", "ci-bot-2", "dash"])
    m_d = w.manifest("d", [*sched, *dash, ("dash", "scheduler")],
                     names=["ci-bot", "ci-bot-2", "dash"],
                     paths={"dash": w.d / "dash-d"})  # fmt: skip
    m_e = w.manifest("e", [*sched, *dash, ("ci-dash", "ci-bot"), ("dash2", "ci-bot"),
                           ("dash2", "ci-dash")],
                     names=["ci-bot", "ci-bot-2", "dash", "ci-dash", "dash2"])  # fmt: skip
    cases = {"a": (m_a, "d1"), "b": (m_a, "d4"), "c": (m_a, "d8"), "d": (m_d, "d1"),
             "e": (m_e, "d1")}  # fmt: skip

    def one(_: int) -> dict[str, Any]:
        return {k: t.case(f"T-diamond/{k}", m, "scheduler", c, probe="T-diamond", watch=w.watch)
                for k, (m, c) in cases.items()}  # fmt: skip

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    d_links = {k: t.committed_row(k) for k in ("D1", "D4", "D8")}
    cancel_fix = [("CHANGED", "endpoint_removed", base.LIST_RUNS, False)]

    def by(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {r["consumer"]: r for r in fdoc(rec)["consumers"]}

    def ci2_ok(row: dict[str, Any]) -> bool:
        return (row["label"] == "unaffected" and len(row["gaps"]) == 1
                and [(f["class"], f["kind"], f["endpoint"], f["relied"])
                     for f in row["findings"]] == cancel_fix)  # fmt: skip

    a, b, c, d, e = (r0[k] for k in "abcde")
    ra, rb, rc_, rd, re_ = by(a), by(b), by(c), by(d), by(e)
    checks: dict[str, Any] = {
        "a_tier1_is_4a_I4a": first_order_eq(ra["ci-bot"], d_links["D1"])
        and ci2_ok(ra["ci-bot-2"])
        and a["json_on"]["rc"] == 1,  # fmt: skip
        "a_roots": [r["consumer"] for r in tdoc(a)["roots"]] == ["ci-bot"],
        "a_one_row_dash": names(a) == ["dash"]
        and rows(a)[0]["depth"] == 2
        and rows(a)[0]["via"] == ["ci-bot"]
        and rows(a)[0]["roots"] == ["ci-bot"]
        and [lk["provider"] for lk in rows(a)[0]["links"]] == ["ci-bot"],  # fmt: skip
        "b_both_contradiction": all(first_order_eq(rb[k], d_links["D4"]) for k in rb)
        and set(rb) == {"ci-bot", "ci-bot-2"}
        and b["json_on"]["rc"] == 1,  # fmt: skip
        "b_roots": [r["consumer"] for r in tdoc(b)["roots"]] == ["ci-bot", "ci-bot-2"],
        "b_one_row_two_links": names(b) == ["dash"]
        and rows(b)[0]["via"] == ["ci-bot", "ci-bot-2"]
        and rows(b)[0]["roots"] == ["ci-bot", "ci-bot-2"]
        and len(rows(b)[0]["links"]) == 2
        and all(
            lk["relies_on"] == [BUILDS] and lk["notes"] == [] and lk["pinned"] is not None
            for lk in rows(b)[0]["links"]
        ),  # fmt: skip
        "c_both_regression_nothing": all(first_order_eq(rc_[k], d_links["D8"]) for k in rc_)
        and c["json_on"]["rc"] == 0
        and tdoc(c) == empty_t(),  # fmt: skip
        "d_dash_first_order_unaffected": rd["dash"]["label"] == "unaffected"
        and [(f["class"], f["kind"], f["relied"]) for f in rd["dash"]["findings"]]
        == [("CHANGED", "endpoint_removed", False)]
        and rd["dash"]["gaps"] == []
        and fdoc(d)["checked"] == 3
        and d["json_on"]["rc"] == 1,  # fmt: skip
        "d_dash_also_downstream": names(d) == ["dash"]
        and rows(d)[0]["via"] == ["ci-bot"]
        and rows(d)[0]["roots"] == ["ci-bot"]
        and rows(d)[0]["depth"] == 2,  # fmt: skip
        "e_rows": names(e) == ["ci-dash", "dash", "dash2"]
        and {r["depth"] for r in rows(e)} == {2},  # fmt: skip
        "e_dash2": next(r for r in rows(e) if r["consumer"] == "dash2")["via"]
        == ["ci-bot", "ci-dash"]
        and next(r for r in rows(e) if r["consumer"] == "dash2")["roots"] == ["ci-bot"]
        and [lk["notes"] for lk in next(r for r in rows(e) if r["consumer"] == "dash2")["links"]]
        == [["no_imports", "no_pin"]] * 2,  # fmt: skip
        "e_tier1_as_a": first_order_eq(re_["ci-bot"], d_links["D1"])
        and ci2_ok(re_["ci-bot-2"])
        and e["json_on"]["rc"] == 1,  # fmt: skip
        "additive_all": all(additive_ok(x) for x in r0.values()),
    }
    obs = (
        f"a rows {names(a)} roots {[r['consumer'] for r in tdoc(a)['roots']]} exit "
        f"{a['json_on']['rc']}; b rows {names(b)} via {rows(b)[0]['via'] if rows(b) else None} "
        f"links {len(rows(b)[0]['links']) if rows(b) else 0}; c roots {len(tdoc(c)['roots'])} "
        f"rows {len(rows(c))} exit {c['json_on']['rc']}; d checked {fdoc(d)['checked']} dash "
        f"{rd['dash']['label']} rows {names(d)}; e rows {names(e)}"
    )
    return mk("T-diamond", pred, obs, runs, ident, checks)


# ---- T-cycle ----------------------------------------------------------------------------------


def t_cycle(t: T) -> Result:
    pred = (
        "on the T-chain workspace, D1; each call terminates (60 s timeout): (a) ci-bot -> ci-dash "
        "(a uses cycle through the root): downstream [ci-dash] only, ci-bot not re-listed; (b) "
        "scheduler -> ci-bot: downstream [ci-dash], one note provider_downstream naming ci-bot, "
        "scheduler never a row, exit 1; (c) x <-> ci-dash: rows ci-dash (2) and x (3) once each, "
        "ci-dash.via [ci-bot, x], x.via [ci-dash], both roots [ci-bot]; (d) two depends_on links "
        "forming a cycle: manifest error depends_on_cycle, exit 2, consumers [], roots [], "
        "downstream [] (flag off: exit 2 too)"
    )
    jc = t.jc
    clone = ep.shared(jc)
    t.ref(clone, "D1")
    w, _m = chain_world(t.h, "T-cycle", clone)
    w.consumer("x")
    chain: list[tuple[str, ...]] = [("ci-bot", "scheduler"), ("ci-dash", "ci-bot"),
                                    ("ops", "ci-bot", "uses", "")]  # fmt: skip
    ms = {
        "a": w.manifest("a", [*chain, ("ci-bot", "ci-dash")]),
        "b": w.manifest("b", [*chain, ("scheduler", "ci-bot")]),
        "c": w.manifest("c", [*chain, ("x", "ci-dash"), ("ci-dash", "x")]),
        "d": w.manifest(
            "d", [*chain, ("x", "ci-dash", "depends_on", ""), ("ci-dash", "x", "depends_on", "")]
        ),  # fmt: skip
    }

    def one(_: int) -> dict[str, Any]:
        return {k: t.case(f"T-cycle/{k}", m, "scheduler", "d1", probe="T-cycle", watch=w.watch,
                          expect=0 if k == "d" else 2) for k, m in ms.items()}  # fmt: skip

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    a, b, c, d = (r0[k] for k in "abcd")
    cr = {r["consumer"]: r for r in rows(c)}
    checks: dict[str, Any] = {
        "a_downstream_ci_dash_only": names(a) == ["ci-dash"]
        and [r["consumer"] for r in tdoc(a)["roots"]] == ["ci-bot"]
        and a["json_on"]["rc"] == 1,
        "b_downstream_ci_dash_note_exit_1": names(b) == ["ci-dash"]
        and [n["code"] for n in tdoc(b)["notes"]] == ["provider_downstream"]
        and "ci-bot" in tdoc(b)["notes"][0]["message"]
        and b["json_on"]["rc"] == 1
        and "scheduler" not in names(b),  # fmt: skip
        "c_each_once_with_exposed_via": list(cr) == ["ci-dash", "x"]
        and len(rows(c)) == 2
        and (cr["ci-dash"]["depth"], cr["x"]["depth"]) == (2, 3)
        and cr["ci-dash"]["via"] == ["ci-bot", "x"]
        and cr["x"]["via"] == ["ci-dash"]
        and cr["ci-dash"]["roots"] == ["ci-bot"]
        and cr["x"]["roots"] == ["ci-bot"],  # fmt: skip
        "d_manifest_error_exit_2": d["json_on"]["rc"] == 2
        and d["json_off"]["rc"] == 2
        and fdoc(d)["consumers"] == []
        and tdoc(d)["roots"] == []
        and tdoc(d)["downstream"] == []
        and "depends_on_cycle" in [p["code"] for p in fdoc(d)["problems"]],  # fmt: skip
        "none_timed_out": all(
            x[f]["rc"] != 124 for x in r0.values() for f in ("json_on", "json_off")
        ),
        "additive_all": all(additive_ok(x) for x in r0.values()),
    }
    obs = (
        f"a rows {names(a)}; b rows {names(b)} notes {[n['code'] for n in tdoc(b)['notes']]} "
        f"exit {b['json_on']['rc']}; c rows "
        f"{[(r['consumer'], r['depth'], r['via']) for r in rows(c)]}; "
        f"d exit {d['json_on']['rc']}/{d['json_off']['rc']} problems "
        f"{[p['code'] for p in fdoc(d)['problems']]}"
    )
    return mk("T-cycle", pred, obs, runs, ident, checks)


# ---- T-long -----------------------------------------------------------------------------------


def t_long(t: T) -> Result:
    pred = (
        "scheduler <- ci-bot <- p2 <- p3 <- p4 <- p5 <- p6 (static, p2..p6 empty directories): D1 "
        "5 rows, depths 2..6 in order, each via its predecessor, roots [ci-bot], link relies_on "
        "null, pinned null, notes [no_imports, no_pin], exit 1; D6: 0 rows"
    )
    jc = t.jc
    clone = ep.shared(jc)
    for did in ("D1", "D6"):
        t.ref(clone, did)
    w = World(jc, "T-long", clone)
    w.ci_bot()
    ps = [f"p{i}" for i in range(2, 7)]
    for p in ps:
        w.consumer(p)
    links: list[tuple[str, ...]] = [("ci-bot", "scheduler"), ("p2", "ci-bot")]
    links += [(ps[i + 1], ps[i]) for i in range(len(ps) - 1)]
    m = w.manifest("main", links)

    def one(_: int) -> dict[str, Any]:
        return {k: t.case(f"T-long/{k}", m, "scheduler", k.lower(), probe="T-long", watch=w.watch)
                for k in ("D1", "D6")}  # fmt: skip

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    d1, d6 = r0["D1"], r0["D6"]
    prev = ["ci-bot", *ps]
    checks = {
        "D1_five_rows_depths_2_to_6_in_order": [(r["consumer"], r["depth"]) for r in rows(d1)]
        == [(p, i + 2) for i, p in enumerate(ps)]
        and d1["json_on"]["rc"] == 1,  # fmt: skip
        "D1_each_via_its_predecessor_roots_ci_bot": all(
            r["via"] == [prev[i]] and r["roots"] == ["ci-bot"] for i, r in enumerate(rows(d1))
        ),
        "D1_links_null_null_notes": all(
            link_ok(r, prev[i], None, None, ["no_imports", "no_pin"])
            for i, r in enumerate(rows(d1))
        ),
        "D6_zero_rows_exit_0": rows(d6) == []
        and d6["json_on"]["rc"] == 0
        and tdoc(d6) == empty_t(),  # fmt: skip
        "additive_all": all(additive_ok(x) for x in r0.values()),
    }
    obs = (
        f"D1 rows {[(r['consumer'], r['depth']) for r in rows(d1)]} exit {d1['json_on']['rc']}; "
        f"D6 rows {len(rows(d6))} exit {d6['json_on']['rc']}"
    )
    return mk("T-long", pred, obs, runs, ident, checks)


# ---- T-edge -----------------------------------------------------------------------------------


def t_edge(t: T) -> Result:
    pred = (
        "(a) ci-dash lock deleted, D1: link pinned null, notes [no_pin], relies_on still "
        "[GET /builds/{}], exit 1; (b) ci-dash's imports not TOML: relies_on null, notes "
        "[bad_imports], exit 1; (c) ci-bot's lock deleted: tier-1 row error no_pin, exit 2, roots "
        "[], downstream [], one note root_error naming ci-bot and 1 dependent; (d) ci-dash -> "
        "ci-bot live: link contract live, same row, 2 export children; (e) provider nobody: exit 2 "
        "unknown_project, transitive present and empty; (f) provider ci-dash: exit 0 "
        "no_consumers, transitive empty; (g) stale pin (clone HEAD at d2, --candidate HEAD): "
        "ci-bot contradiction, downstream [ci-dash]"
    )
    jc = t.jc
    clone = ep.shared(jc)
    t.ref(clone, "D1")
    d2_clone = jc.clone("t-edge-stale", t.drift("D2")["edits"])
    wa, ma = chain_world(t.h, "T-edge-a", clone)
    (wa.dirs["ci-dash"] / LOCK).unlink()
    wb, mb = chain_world(t.h, "T-edge-b", clone)
    (wb.dirs["ci-dash"] / IMPORTS).write_text("this is = not [valid toml\n")
    wc, mc = chain_world(t.h, "T-edge-c", clone)
    (wc.dirs["ci-bot"] / LOCK).unlink()
    wd, _md = chain_world(t.h, "T-edge-d", clone)
    md = wd.manifest("live", [("ci-bot", "scheduler"), ("ci-dash", "ci-bot", "uses", "live"),
                              ("ops", "ci-bot", "uses", "")])  # fmt: skip
    we, me = chain_world(t.h, "T-edge-e", clone)
    wg, mg = chain_world(t.h, "T-edge-g", d2_clone)
    specs: dict[str, tuple[World, Path, str, str, int]] = {
        "a": (wa, ma, "scheduler", "d1", 2),
        "b": (wb, mb, "scheduler", "d1", 2),
        "c": (wc, mc, "scheduler", "d1", 2),
        "d": (wd, md, "scheduler", "d1", 2),
        "e": (we, me, "nobody", "HEAD", 0),
        "f": (we, me, "ci-dash", "HEAD", 0),
        "g": (wg, mg, "scheduler", "HEAD", 2),
    }

    def one(_: int) -> dict[str, Any]:
        return {k: t.case(f"T-edge/{k}", m, prov, cand, probe="T-edge", watch=w.watch,
                          expect=ex) for k, (w, m, prov, cand, ex) in specs.items()}  # fmt: skip

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    a, b, c, d, e, f, g = (r0[k] for k in "abcdefg")
    pin_c = json.loads((wa.dirs["ci-bot"] / LOCK).read_text())  # the golden, for reference only
    assert pin_c
    la = rows(a)[0]["links"][0] if rows(a) else {}
    lb = rows(b)[0]["links"][0] if rows(b) else {}
    ld = rows(d)[0]["links"][0] if rows(d) else {}
    checks = {
        "a_pinned_null_no_pin_relies_kept_exit_1": a["json_on"]["rc"] == 1
        and names(a) == ["ci-dash"]
        and la.get("pinned") is None
        and la.get("notes") == ["no_pin"]
        and la.get("relies_on") == [BUILDS],  # fmt: skip
        "b_relies_null_bad_imports_exit_1": b["json_on"]["rc"] == 1
        and names(b) == ["ci-dash"]
        and lb.get("relies_on") is None
        and lb.get("notes") == ["bad_imports"],  # fmt: skip
        "c_row_error_no_pin_exit_2_empty_root_error_note": c["json_on"]["rc"] == 2
        and fdoc(c)["consumers"][0]["error"]["code"] == "no_pin"
        and tdoc(c)["roots"] == []
        and tdoc(c)["downstream"] == []
        and [n["code"] for n in tdoc(c)["notes"]] == ["root_error"]
        and "ci-bot" in tdoc(c)["notes"][0]["message"]
        and "1 dependent" in tdoc(c)["notes"][0]["message"],  # fmt: skip
        "d_live_listed_same_row_2_children": d["json_on"]["rc"] == 1
        and names(d) == ["ci-dash"]
        and ld.get("contract") == "live"
        and ld.get("relies_on") == [BUILDS]
        and ld.get("notes") == [],  # fmt: skip
        "e_unknown_project_exit_2_transitive_empty": e["json_on"]["rc"] == 2
        and [p["code"] for p in fdoc(e)["problems"]] == ["unknown_project"]
        and tdoc(e) == empty_t(),  # fmt: skip
        "f_no_consumers_exit_0_transitive_empty": f["json_on"]["rc"] == 0
        and [p["code"] for p in fdoc(f)["problems"]] == ["no_consumers"]
        and tdoc(f) == empty_t(),
        "g_stale_pin_contradiction_downstream": g["json_on"]["rc"] == 1
        and fdoc(g)["consumers"][0]["label"] == "contradiction"
        and fdoc(g)["consumers"][0]["current_label"] == "contradiction"
        and names(g) == ["ci-dash"],  # fmt: skip
        "additive_all": all(additive_ok(x) for x in r0.values()),
    }
    obs = (
        f"a {a['json_on']['rc']} {la.get('notes')}/{la.get('relies_on')}; b "
        f"{b['json_on']['rc']} {lb.get('notes')}/{lb.get('relies_on')}; c {c['json_on']['rc']} "
        f"{[n['code'] for n in tdoc(c)['notes']]}; d {d['json_on']['rc']} contract "
        f"{ld.get('contract')}; e {e['json_on']['rc']}; f {f['json_on']['rc']}; g "
        f"{g['json_on']['rc']} rows {names(g)}"
    )
    return mk("T-edge", pred, obs, runs, ident, checks)


# ---- T-additive, T-flagoff, T-safety (aggregated) ------------------------------------------------


def t_additive(t: T) -> Result:
    pred = (
        "every flagged call of the run: flag-on JSON minus `transitive` == flag-off JSON "
        "(elapsed_s "
        "dropped), flag-on text = the flag-off bytes + a block starting `downstream (`, exit "
        "codes equal"
    )
    bad = sorted(k for k, rec in t.pairs.items() if not additive_ok(rec))
    checks = {"every_pair_additive": not bad, "pairs_gt_0": len(t.pairs) > 0}
    obs = f"{len(t.pairs) - len(bad)}/{len(t.pairs)} pairs additive" + (f"; failing {bad}" if bad
                                                                          else "")  # fmt: skip
    return mk("T-additive", pred, obs, [sorted(t.pairs)], True, checks, {"failing": bad})


def run_last_line(argv: list[str], env: dict[str, str], cwd: Path, dst: Path, name: str,
                  pat: str = r"probes: (\d+)/(\d+) pass") -> dict[str, Any]:  # fmt: skip
    import re

    t0 = time.time()
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    tail = [ln for ln in r.stdout.splitlines() if re.fullmatch(pat, ln.strip())]
    m = re.fullmatch(pat, tail[-1].strip()) if tail else None
    dst.mkdir(parents=True, exist_ok=True)
    (dst / f"{name}-stdout.txt").write_text(r.stdout[-20000:])
    return {"rc": r.returncode, "passed": int(m.group(1)) if m else None,
            "total": int(m.group(2)) if m else None, "last_line": tail[-1] if tail else None,
            "wall_s": round(time.time() - t0, 1),
            "stderr_tail": r.stderr.strip().splitlines()[-3:]}  # fmt: skip


def t_flagoff(t: T, ns: argparse.Namespace, i4_cases: dict[str, Any]) -> Result:
    pred = (
        "without the flag the worktree's `impact` text and --json are byte-identical to a4ab4a1's "
        "(git archive a4ab4a1 lattice): T-chain D1..D10, T-diamond a..e, T-edge c, e, f, the "
        "regenerated 4a I4 rows, and `notify --dry-run --json` on T-chain D1; plus (--regression) "
        "impact_probes.py --runs 10 12/12, linkedq_probes.py as committed and pytest "
        "tests/contracts tests/workspace tests/mcp tests/studio 0 failures"
    )
    checks: dict[str, Any] = {}
    parts: list[str] = []
    wanted = [f"T-chain/D{i}" for i in range(1, 11)] + [f"T-diamond/{k}" for k in "abcde"]
    wanted += ["T-edge/c", "T-edge/e", "T-edge/f", *sorted(i4_cases)]
    wanted += ["notify/T-chain/D1"]
    missing = [k for k in wanted if k not in t.bases or k not in t.off]
    equal = 0
    for key in wanted:
        if key not in t.bases or key not in t.off:
            continue
        same = all(r == t.bases[key] for r in t.off[key])
        checks[f"flagoff_{key}"] = same
        equal += same
    checks["no_case_missing"] = not missing
    parts.append(f"{equal}/{len(wanted)} cases byte-identical to a4ab4a1 (10 runs each)")
    extra: dict[str, Any] = {"missing": missing, "cases": wanted}
    checks["regression_run"] = False
    parts.append("regression suites: not run yet")
    return mk("T-flagoff", pred, "; ".join(parts), [sorted(t.bases)], True, checks, extra)


def flagoff_regression(ns: argparse.Namespace, res: Result) -> Result:
    """The regression suites of T-flagoff, run last (after T-safety, so that nothing they do can
    touch the transitive calls' window): probes.py, streak_probes.py --allow-code-diff,
    impact_probes.py --skip-regression (its I8 fails on its own `regression_run` check by
    design, nothing else may), and pytest tests/contracts tests/workspace tests/mcp
    tests/studio. Updates `res` in place."""
    env = ip.clean_env()
    env["PYTHONPATH"] = str(REPO)
    dst = ns.out / "T-flagoff-regression"
    scratch = ns.scratch.resolve()
    py = sys.executable
    parts = [p for p in res.observed.split("; ") if not p.startswith("regression suites")]

    def argv_of(script: str, name: str, *flags: str) -> list[str]:
        return [py, str(HERE / script), "--runs", str(ns.runs), *flags,
                "--scratch", str(scratch / f"r-{name}"),
                "--out", str(scratch / f"r-{name}-data")]  # fmt: skip

    jobs = {
        "probes": (argv_of("probes.py", "probes"), 35),
        "streak_probes": (argv_of("streak_probes.py", "streak", "--allow-code-diff"), 32),
        "impact_probes_skip_regression": (
            argv_of("impact_probes.py", "impact", "--skip-regression"), 12),
    }  # fmt: skip
    extra = res.extra
    for name, (argv, want) in jobs.items():
        r = run_last_line(argv, env, REPO, dst, name)
        extra[name] = r
        if name.startswith("impact_probes"):
            i8 = scratch / "r-impact-data" / "I8.json"
            failing: list[str] = []
            if i8.is_file():
                failing = [k for k, v in json.loads(i8.read_text())["checks"].items() if not v]
            r["i8_failing_checks"] = failing
            res.checks[f"{name}_only_I8_regression_run_fails"] = (
                r["passed"] == want - 1 and r["total"] == want and failing == ["regression_run"]
            )
        else:
            res.checks[f"{name}_{want}_of_{want}"] = (r["passed"], r["total"], r["rc"]) == (
                want, want, 0)  # fmt: skip
        parts.append(f"{name} {r['passed']}/{r['total']} rc {r['rc']}")
    t0 = time.time()
    r2 = subprocess.run([py, "-m", "pytest", "tests/contracts", "tests/workspace", "tests/mcp",
                         "tests/studio", "-q", "-p", "no:cacheprovider"], cwd=REPO, env=env,
                        capture_output=True, text=True, check=False)  # fmt: skip
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "pytest-stdout.txt").write_text(r2.stdout[-6000:])
    last = [ln for ln in r2.stdout.splitlines() if " passed" in ln or " failed" in ln][-1:]
    extra["pytest"] = {"rc": r2.returncode, "last_line": last,
                       "wall_s": round(time.time() - t0, 1)}  # fmt: skip
    res.checks["pytest_0_failures"] = r2.returncode == 0
    parts.append(f"pytest {last[0] if last else r2.returncode}")
    res.checks["regression_run"] = True
    extra["not_run_here"] = ["linkedq_probes.py", "impact_probes.py without --skip-regression"]
    res.observed = "; ".join(parts)
    res.passed = res.identical and all(res.checks.values())
    return res


def t_safety(t: T, repos: dict[str, Any], marker: dict[str, Any]) -> Result:
    pred = (
        "over every traced call: sha256 of the workspace, consumers (locks, imports), provider "
        "clone and the worktree unchanged, 0 files newer; TMPDIR empty; 0 socket connects; only "
        "git {rev-parse, ls-tree, cat-file, status} and the export child, export children as "
        "expected (2; 0 for T-cycle d, T-edge e, f) and EQUAL with and without the flag; no "
        "claude, no ANTHROPIC variable; no linked_questions/ or state dir created; the real "
        "job-scheduler and Streak git status unchanged, 0 newer files"
    )
    s = t.h.tracer.summary()
    recs = t.h.tracer.records
    # children with and without the flag: the calls come in on/off pairs per label
    by_label: dict[tuple[str, str], set[int]] = {}
    for r in recs:
        lab = r["call"].replace(" --transitive", "")
        by_label.setdefault((r["probe"], lab), set()).add(r["export_children"])
    equal_with_without = all(len(v) == 1 for v in by_label.values())
    real = ep.s_probe(repos)
    created = [c for r in recs for c in r["changed"] if "linked_questions" in c or ".lattice" in c]
    checks: dict[str, Any] = {
        "calls_traced_gt_0": s["calls"] > 0,
        "tree_unchanged_every_call": not s["tree_changed"],
        "no_file_newer_than_a_call": not s["newer"],
        "tmpdir_empty_after_every_call": not s["tmp_left"],
        "only_allowed_argv": not s["bad_popen"],
        "zero_connects": s["connects"] == 0,
        "export_children_as_expected": not s["children_mismatch"],
        "children_equal_with_and_without_flag": equal_with_without,
        "no_claude_no_anthropic": not s["claude_or_anthropic"],
        "no_state_dir_or_linked_questions": not created,
        "real_repos_unchanged": real.passed,
        "worktree_newer_since_marker_0": marker["newer"] == [],
    }
    obs = (
        f"{s['calls']} traced calls; tree changed {len(s['tree_changed'])}, newer "
        f"{len(s['newer'])}, tmp left {len(s['tmp_left'])}, bad argv {len(s['bad_popen'])}, "
        f"connects {s['connects']}, children mismatches {len(s['children_mismatch'])}, labels "
        f"with differing children {sum(1 for v in by_label.values() if len(v) != 1)}, claude/"
        f"ANTHROPIC {len(s['claude_or_anthropic'])}; real repos: {real.observed}; worktree newer "
        f"since marker {len(marker['newer'])}"
    )
    return mk("T-safety", pred, obs, [s], True, checks,
              {"summary": s, "real_repos": real.runs, "marker": marker})  # fmt: skip


# ---- T-parity ---------------------------------------------------------------------------------


def t_parity(t: T, cycle_c: dict[str, Any]) -> Result:
    pred = (
        "CLI --transitive --json == run_impact(..., transitive=True).to_dict() in-process on "
        "T-chain D1, T-diamond b and T-cycle c; MCP contract_impact (stdio) and GET "
        "/api/contracts/impact (real server) on T-chain D1 == the CLI without the flag, with no "
        "`transitive` key"
    )
    h, jc = t.h, t.jc
    from lattice import workspace as wsmod
    from lattice.contracts import impact as imp

    clone = ep.shared(jc)
    t.ref(clone, "D1")
    w, m = chain_world(h, "T-parity", clone)
    wd, _ma = diamond_world(t, "T-parity-dia")
    sched = [("ci-bot", "scheduler"), ("ci-bot-2", "scheduler")]
    m_dia = wd.manifest("a", [*sched, ("dash", "ci-bot"), ("dash", "ci-bot-2")],
                        names=["ci-bot", "ci-bot-2", "dash"])  # fmt: skip
    t.ref(clone, "D4")
    wc, _mc = chain_world(h, "T-parity-cyc", clone)
    wc.consumer("x")
    m_cyc = wc.manifest("c", [("ci-bot", "scheduler"), ("ci-dash", "ci-bot"),
                              ("ops", "ci-bot", "uses", ""), ("x", "ci-dash"),
                              ("ci-dash", "x")])  # fmt: skip
    solo = base.solo_project(jc.work / "T-parity-solo")
    targets = {
        "T-chain-D1": (m, "d1", w.watch),
        "T-diamond-b": (m_dia, "d4", wd.watch),
        "T-cycle-c": (m_cyc, "d1", wc.watch),
    }

    def api_call(manifest: Path, prov: str, cand: str, watch: list[Path]) -> dict[str, Any]:
        from urllib.parse import quote

        port = base.free_port()
        argv = [sys.executable, "-m", "lattice.studio", str(solo), "--port", str(port),
                "--quiet", "--stub-llm", "--workspace", str(manifest)]  # fmt: skip
        path = f"/api/contracts/impact?provider={quote(prov)}&candidate={quote(cand)}"
        with base.background(jc, argv, f"http://127.0.0.1:{port}/api/projects"):
            with h.tracer.around("T-parity", f"api {path}", jc, watch):
                doc = base.http_get(f"http://127.0.0.1:{port}{path}")
        return strip_elapsed(doc)  # type: ignore[no-any-return]

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for k, (mm, cand, watch) in targets.items():
            cli = canon_call(t.call(jc, mm, "scheduler", cand, probe="T-parity", watch=watch,
                                    transitive=True))  # fmt: skip
            ws = wsmod.parse(mm)
            lib = imp.run_impact(ws, "scheduler", cand, base=jc.work, transitive=True)
            rec[k] = {"cli": cli,
                      "lib": strip_elapsed(json.loads(json.dumps(lib.to_dict())))}  # fmt: skip
        flag_off = canon_call(t.call(jc, m, "scheduler", "d1", probe="T-parity", watch=w.watch))
        rec["mcp"] = ip.mcp_impact(h, jc, solo, m, "scheduler", "d1", "T-parity", w.watch)
        rec["api"] = api_call(m, "scheduler", "d1", w.watch)
        rec["cli_flag_off"] = flag_off
        return rec

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {
        f"lib_equals_cli_{k}": r0[k]["cli"]["doc"] == r0[k]["lib"] for k in targets
    }
    checks.update(
        mcp_equals_cli_flag_off=r0["mcp"]["doc"] == r0["cli_flag_off"]["doc"],
        mcp_no_transitive_key="transitive" not in r0["mcp"]["doc"],
        api_equals_cli_flag_off=r0["api"] == r0["cli_flag_off"]["doc"],
        api_no_transitive_key="transitive" not in r0["api"],
        cli_has_transitive_key="transitive" in r0["T-chain-D1"]["cli"]["doc"],
        cycle_c_matches_t_cycle_record=bool(cycle_c)
        and r0["T-cycle-c"]["lib"]["transitive"]["counts"]
        == {"roots": 1, "downstream": 2},  # fmt: skip
    )
    parts = [f"{k}: lib==cli {yn(checks[f'lib_equals_cli_{k}'])}" for k in targets]
    parts += [f"mcp==cli(off) {yn(checks['mcp_equals_cli_flag_off'])}",
              f"api==cli(off) {yn(checks['api_equals_cli_flag_off'])}",
              f"no transitive key mcp {yn(checks['mcp_no_transitive_key'])} api "
              f"{yn(checks['api_no_transitive_key'])}"]  # fmt: skip
    return mk("T-parity", pred, "; ".join(parts), runs, ident, checks)


# ---- T-cost -----------------------------------------------------------------------------------


def t_cost(t: T) -> Result:
    pred = (
        "median wall of `impact --transitive` minus `impact` over 10 interleaved runs on T-chain "
        "D1 <= 0.2 s (predicted < 0.05 s: no subprocess, 2 small file reads per link)"
    )
    jc = t.jc
    clone = ep.shared(jc)
    t.ref(clone, "D1")
    w, m = chain_world(t.h, "T-cost", clone)
    on: list[float] = []
    off: list[float] = []
    on_el: list[float] = []
    off_el: list[float] = []
    for _ in range(max(t.jc.runs, 10)):
        c = t.call(jc, m, "scheduler", "d1", probe="T-cost", watch=w.watch)
        off.append(c.wall)
        off_el.append(c.json()["elapsed_s"])
        c = t.call(jc, m, "scheduler", "d1", probe="T-cost", watch=w.watch, transitive=True)
        on.append(c.wall)
        on_el.append(c.json()["elapsed_s"])
    d_wall = statistics.median(on) - statistics.median(off)
    d_el = statistics.median(on_el) - statistics.median(off_el)
    checks = {"wall_delta_le_0_2s": d_wall <= 0.2, "prediction_lt_0_05s": d_wall < 0.05}
    obs = (
        f"median wall off {statistics.median(off):.3f} s, on {statistics.median(on):.3f} s, delta "
        f"{d_wall:+.3f} s; median elapsed_s off {statistics.median(off_el):.3f}, on "
        f"{statistics.median(on_el):.3f}, delta {d_el:+.3f}"
    )
    extra = {"wall_off_s": [round(x, 3) for x in off], "wall_on_s": [round(x, 3) for x in on],
             "elapsed_off_s": off_el, "elapsed_on_s": on_el,
             "note": "timing is not deterministic; 'identical' n/a"}  # fmt: skip
    passed = checks["wall_delta_le_0_2s"]
    return Result("T-cost", pred, obs, passed, [{"off": off, "on": on}], True, checks, extra)


# ---- the 4a I4 rows, regenerated for T-flagoff -------------------------------------------------


def i4_flagoff(t: T) -> dict[str, Any]:
    """The 4a I4 rows a..i (workspaces A and B) run without the flag; only the worktree-vs-old
    comparison is made (T-flagoff), the committed rows are I4's own business."""
    h, jc, sc = t.h, t.jc, t.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    drifts = {d["id"]: d for d in base.DRIFTS}
    for did in ("D1", "D2", "D3", "D4", "D8"):
        h.ref(jc, jp, did.lower(), drifts[did]["edits"])
    h.ref(sc, sp, "d13", next(d for d in streak.DRIFTS if d["id"] == "D13")["edits"])
    ma = ip.multi_a(h, "TF-4a", jp)
    me = ip.multi_a(h, "TF-4e", jp, ci2="live")
    mf = ip.multi_a(h, "TF-4f", jp, ci2=None)
    mg = ip.multi_a(h, "TF-4g", jp)
    (mg.parent.parent / "ci-bot-2" / LOCK).unlink()
    mb = ip.multi_b(h, "TF-4h", jp, sp)
    wa = [ma.parent.parent, jp]
    wb = [mb.parent.parent, jp, sp]
    return {
        "I4/a": (ma, "scheduler", "d1", wa, 2),
        "I4/b": (ma, "scheduler", "d3", wa, 2),
        "I4/c": (ma, "scheduler", "d4", wa, 2),
        "I4/d": (ma, "scheduler", "d8", wa, 2),
        "I4/e": (me, "scheduler", "d1", [me.parent.parent, jp], 2),
        "I4/f": (mf, "scheduler", "d1", [mf.parent.parent, jp], 2),
        "I4/g": (mg, "scheduler", "d1", [mg.parent.parent, jp], 2),
        "I4/h_sched": (mb, "scheduler", "d2", wb, 2),
        "I4/h_streak": (mb, "streak", "d13", wb, 2),
        "I4/i_ci_bot": (ma, "ci-bot", "HEAD", wa, 0),
        "I4/i_nobody": (ma, "nobody", "HEAD", wa, 0),
    }


# ---- main -------------------------------------------------------------------------------------

ORDER = ["T-basis", "T-chain", "T-diamond", "T-cycle", "T-long", "T-edge", "T-flagoff",
         "T-additive", "T-parity", "T-cost", "T-safety"]  # fmt: skip


def run_probes(ns: argparse.Namespace) -> int:
    ids = [i for i in ns.only.split(",") if i] or ORDER
    unknown = [i for i in ids if i not in ORDER]
    if unknown:
        print(f"unknown probe ids: {unknown}", file=sys.stderr)
        return 2
    ids = [i for i in ORDER if i in ids]
    ns.out.mkdir(parents=True, exist_ok=True)
    ns.out = ns.out.resolve()
    scratch = ns.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    ns.scratch = scratch
    base_dir = ns.base.resolve() if ns.base else None
    if base_dir is not None and not (base_dir / "lattice" / "contracts" / "impact.py").is_file():
        print(f"--base {base_dir}: no lattice/contracts/impact.py", file=sys.stderr)
        return 2
    shim = scratch / f"shim-{os.getpid()}"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(ip.SHIM)
    log = scratch / f"transitive-trace-{os.getpid()}.log"
    log.write_text("")
    jc = ep.JCtx(ns.runs, scratch / "j", ns.out)
    sc = ep.SX(ns.runs, scratch / "s", ns.out)
    for c in (jc, sc):
        c.env["PYTHONPATH"] = f"{shim}{os.pathsep}{REPO}"
        c.env["IMPACT_PROBE_LOG"] = str(log)
    tracer = ip.Tracer(log, REPO, [ns.out])
    h = ip.H(ns, jc, sc, tracer)
    t = T(h, base_dir)
    roots = {"$WORK_J": jc.root, "$WORK_S": sc.root, "$SCRATCH": scratch}
    repos = {
        "job-scheduler": ep.RealRepo(jc, base.PROVIDER, base.REF, " M uv.lock", [".venv"]),
        "streak": ep.RealRepo(jc, streak.STREAK, streak.REF, " M studio.toml", []),
    }
    marker = scratch / "worktree-marker"
    marker.write_text("start\n")
    time.sleep(1.1)  # a clear mtime gap for find -newer
    t_start = time.time()
    results: list[Result] = []
    probes = {"T-basis": t_basis, "T-chain": t_chain, "T-diamond": t_diamond, "T-cycle": t_cycle,
              "T-long": t_long, "T-edge": t_edge, "T-cost": t_cost}  # fmt: skip

    def write(res: Result) -> None:
        doc = {"id": res.id, "prediction": res.prediction, "observed": res.observed,
               "pass": res.passed, "identical": res.identical, "n_runs": len(res.runs),
               "checks": res.checks, "extra": res.extra, "runs": res.runs}  # fmt: skip
        (ns.out / f"{res.id}.json").write_text(json.dumps(ep.scrub(doc, roots), indent=1) + "\n")

    procs: list[str] = []
    tmp_left: list[str] = []
    cycle_c: dict[str, Any] = {}
    try:
        for pid in ids:
            print(f"... {pid}", file=sys.stderr, flush=True)
            if pid == "T-flagoff":
                specs = i4_flagoff(t) if base_dir is not None else {}

                def one(_: int, specs: dict[str, Any] = specs) -> dict[str, Any]:
                    out: dict[str, Any] = {}
                    for key, (m, prov, cand, watch, expect) in specs.items():
                        out[key] = t.case(key, m, prov, cand, probe="T-flagoff", watch=watch,
                                          expect=expect, flag_on=False)  # fmt: skip
                    chain_m = t_chain_manifest(t)
                    cj = t.call(jc, chain_m[0], "scheduler", "d1", probe="T-flagoff",
                                watch=chain_m[1], cmd="notify", extra=("--dry-run",))  # fmt: skip
                    rec = {"json": canon_call(cj), "text": {"rc": cj.rc, "out": "", "err": ""}}
                    if base_dir is not None and "notify/T-chain/D1" not in t.bases:
                        oj = t.call(jc, chain_m[0], "scheduler", "d1", probe="T-flagoff",
                                    watch=chain_m[1], cmd="notify", extra=("--dry-run",),
                                    old=True)  # fmt: skip
                        t.bases["notify/T-chain/D1"] = {
                            "json": canon_call(oj),
                            "text": {"rc": oj.rc, "out": "", "err": ""},
                        }
                    t.off.setdefault("notify/T-chain/D1", []).append(rec)
                    out["notify"] = rec
                    return out

                ident_runs, ident = jc.repeat(one)
                res = t_flagoff(t, ns, specs)
                res.identical = res.identical and ident
                res.passed = res.passed and ident
                res.runs = [ident_runs[0]]
            elif pid == "T-additive":
                res = t_additive(t)
            elif pid == "T-parity":
                res = t_parity(t, cycle_c)
            elif pid == "T-safety":
                find = subprocess.run(
                    ["find", str(REPO), "-newer", str(marker), "-type", "f", "-not", "-path",
                     f"{ns.out}/*", "-not", "-path", f"{REPO}/.git/*"],
                    text=True, capture_output=True, check=False)  # fmt: skip
                newer = sorted(ln for ln in find.stdout.splitlines() if ln)
                res = t_safety(t, repos, {"newer": newer})
            else:
                res = probes[pid](t)
                if pid == "T-cycle":
                    cycle_c = {"ok": True}
            results.append(res)
            write(res)
        if ns.regression:
            for res in results:
                if res.id == "T-flagoff":
                    print("... T-flagoff regression suites", file=sys.stderr, flush=True)
                    flagoff_regression(ns, res)
                    write(res)
    finally:
        procs = ip.leftover_processes(jc, sc)
        tmp_left = sorted(
            f"{tag}/{p.name}"
            for tag, c in (("j", jc), ("s", sc))
            for p in (c.tmp.iterdir() if c.tmp.exists() else [])
        )
        if not ns.keep:
            for c in (jc, sc):
                shutil.rmtree(c.root, ignore_errors=True)
            shutil.rmtree(shim, ignore_errors=True)
            log.unlink(missing_ok=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<10} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(f"{r.id:<10} | {r.prediction} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True,
                          capture_output=True, check=False).stdout.strip()  # fmt: skip
    summary = {
        "experiment": "2026-10-15-transitive-impact",
        "command": "python dogfood/contracts/transitive_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs, "code_root": str(REPO), "code_head": head, "old_code": OLD_REF,
        "job_scheduler_ref": base.REF, "wall_s": round(time.time() - t_start, 1),
        "probes": [{"id": r.id, "prediction": r.prediction, "observed": r.observed,
                    "pass": r.passed, "runs": len(r.runs), "identical": r.identical,
                    "checks": r.checks} for r in results],
        "totals": {"pass": passed, "total": len(results)},
        "regression_run": ns.regression,
        "trace": tracer.summary(),
        "safety_before": {k: v.before for k, v in repos.items()},
        "leftover_processes": procs, "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {"script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
                     "procedure": POST_HOC_PROCEDURE},
    }  # fmt: skip
    (ns.out / "summary.json").write_text(json.dumps(ep.scrub(summary, roots), indent=1) + "\n")
    return 0 if passed == len(results) and not procs and not tmp_left else 1


_CHAIN: dict[str, tuple[Path, list[Path]]] = {}


def t_chain_manifest(t: T) -> tuple[Path, list[Path]]:
    """The T-chain workspace (built once per run of the runner) for the `notify` comparison."""
    if "m" not in _CHAIN:
        clone = ep.shared(t.jc)
        t.ref(clone, "D1")
        w, m = chain_world(t.h, "T-notify", clone)
        _CHAIN["m"] = (m, w.watch)
    return _CHAIN["m"]


# ---- mutants ----------------------------------------------------------------------------------

TR_FILE = "lattice/contracts/transitive.py"
IM_FILE = "lattice/contracts/impact.py"
MUTANTS: list[dict[str, str]] = [
    {"id": "TR1", "what": "regression rows are roots too", "file": TR_FILE,
     "old": 'ROOT_LABELS = ("contradiction",)',
     "new": 'ROOT_LABELS = ("contradiction", "regression")',
     "killer": "regression does not propagate (reg candidate)"},
    {"id": "TR2", "what": "every non-error row is a root", "file": TR_FILE,
     "old": '        elif row["label"] in ROOT_LABELS:', "new": "        elif True:",
     "killer": "only a contradiction propagates (add, HEAD)"},
    {"id": "TR3", "what": "downstream rows counted in counts.contradiction", "file": IM_FILE,
     "old": '            out[row["label"] or "error"] += 1',
     "new": '            out[row["label"] or "error"] += 1 + len((self.transitive or {}).get('
     '"downstream", [])) * (row["label"] == CONTRADICTION)',
     "killer": "first-order answer untouched by the flag (counts)"},
    {"id": "TR4", "what": "downstream label contradiction", "file": TR_FILE,
     "old": '                    "label": DOWNSTREAM,',
     "new": '                    "label": "contradiction",',
     "killer": "row shape (label downstream)"},
    {"id": "TR5", "what": "a downstream link note raises the exit code to 2", "file": IM_FILE,
     "old": '            or any(row["error"] is not None for row in self.consumers)',
     "new": '            or any(row["error"] is not None for row in self.consumers) '
     'or any(l["notes"] '
     'for r in (self.transitive or {}).get("downstream", []) for l in r["links"])',
     "killer": "a missing lock is a note, exit stays 1"},
    {"id": "TR6", "what": "no visited filter (revisits)", "file": TR_FILE,
     "old": "dependents.get(b, ())} - visited)", "new": "dependents.get(b, ())})",
     "killer": "cycles list each project once"},
    {"id": "TR7", "what": "provider not excluded", "file": TR_FILE,
     "old": "    visited = {provider}", "new": "    visited = set()",
     "killer": "the provider is never a row"},
    {"id": "TR8", "what": "roots not seeded as visited", "file": TR_FILE,
     "old": "    visited.update(roots)", "new": "    pass",
     "killer": "a cycle through the root does not re-list it"},
    {"id": "TR9", "what": "links without contract followed", "file": TR_FILE,
     "old": "        if lk.contract not in CONTRACTS:", "new": "        if False:",
     "killer": "ops (no contract) is never listed"},
    {"id": "TR10", "what": "via = BFS parents only", "file": TR_FILE,
     "old": "            via = sorted(set(relies_on_of.get(name, ())) & exposed)",
     "new": "            via = sorted(set(relies_on_of.get(name, ())) & set(levels[depth - 2]))",
     "killer": "diamond / cycle via lists every exposed link"},
    {"id": "TR11", "what": "depth off by one", "file": TR_FILE,
     "old": '                    "depth": depth,', "new": '                    "depth": depth + 1,',
     "killer": "depths 2..N"},
    {"id": "TR12", "what": "transitive key emitted with the flag off", "file": IM_FILE,
     "old": "        if self.transitive is not None:", "new": "        if True:",
     "killer": "flag off has no transitive key"},
    {"id": "TR13", "what": "downstream providers exported (compute_export)", "file": TR_FILE,
     "old": "            files[name] = (_imports_of(ws, name), _lock_of(ws, name))",
     "new": "            files[name] = (_imports_of(ws, name), _lock_of(ws, name))\n"
     '            with __import__("contextlib").suppress(Exception):\n'
     '                __import__("lattice.contracts.export", fromlist=["x"]).compute_export('
     "ws.project(name).path)",
     "killer": "export children equal with and without the flag"},
    {"id": "TR14", "what": "error rows propagate", "file": TR_FILE,
     "old": '        elif row["label"] in ROOT_LABELS:',
     "new": '        if row["error"] is not None or row["label"] in ROOT_LABELS:',
     "killer": "a root in error does not propagate"},
]  # fmt: skip


def run_mutants(ns: argparse.Namespace) -> int:
    scratch = ns.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    want = [i for i in ns.only.split(",") if i]
    muts = [m for m in MUTANTS if not want or m["id"] in want]
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    py = sys.executable
    results: list[dict[str, Any]] = []
    t_start = time.time()

    def prepare(tag: str) -> Path:
        dst = scratch / tag
        shutil.rmtree(dst, ignore_errors=True)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(REPO / "lattice", dst / "lattice", ignore=ignore)
        (dst / "tests").mkdir()
        shutil.copytree(REPO / "tests" / "contracts", dst / "tests" / "contracts", ignore=ignore)
        shutil.copy(REPO / "tests" / "conftest.py", dst / "tests" / "conftest.py")
        shutil.copy(REPO / "pyproject.toml", dst / "pyproject.toml")
        return dst

    def pytest(dst: Path) -> dict[str, Any]:
        e = dict(env, PYTHONPATH=str(dst), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(scratch))
        argv = [py, "-m", "pytest", "tests/contracts/test_transitive.py", "-q",
                "-p", "no:cacheprovider", f"--basetemp={dst / 'pt'}"]  # fmt: skip
        t0 = time.time()
        r = subprocess.run(argv, cwd=dst, env=e, capture_output=True, text=True, check=False)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        failed = [ln.split(" - ")[0].replace("FAILED ", "") for ln in lines
                  if ln.startswith("FAILED ")]  # fmt: skip
        return {"rc": r.returncode, "last": lines[-1] if lines else "", "failed": failed,
                "wall_s": round(time.time() - t0, 1)}  # fmt: skip

    print("... control", file=sys.stderr, flush=True)
    control = pytest(prepare("control"))
    control_ok = control["rc"] == 0
    for m in muts:
        print(f"... {m['id']}", file=sys.stderr, flush=True)
        dst = prepare(m["id"])
        path = dst / m["file"]
        text = path.read_text()
        n = text.count(m["old"])
        if n != 1:
            results.append({**m, "killed": False, "why": f"edit matched {n} times (need 1)",
                            "failed": [], "last": "", "rc": None, "wall_s": 0.0})  # fmt: skip
            continue
        path.write_text(text.replace(m["old"], m["new"]))
        r = pytest(dst)
        killed = r["rc"] == 1 and bool(r["failed"])  # a failing assertion, not a crash
        results.append({**m, "killed": killed, "why": "", **r})
        shutil.rmtree(dst, ignore_errors=True)
    shutil.rmtree(scratch / "control", ignore_errors=True)
    n_killed = sum(1 for r in results if r["killed"])
    print(f"{'id':<5} | {'mutant':<48} | {'predicted killer':<52} | observed")
    for r in results:
        first = r["failed"][0].split("::")[-1] if r["failed"] else (r["why"] or r["last"])
        more = f" (+{len(r['failed']) - 1} more)" if len(r["failed"]) > 1 else ""
        print(
            f"{r['id']:<5} | {r['what']:<48} | {r['killer']:<52} | "
            f"{'KILLED by ' + first + more if r['killed'] else 'SURVIVED ' + first}"
        )
    print(f"control (unmodified copy): {'pass' if control_ok else 'FAIL'} ({control['last']})")
    print(f"mutants: {n_killed}/{len(results)} killed")
    out = ns.out
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        doc = {"experiment": "2026-10-15-transitive-impact", "mode": "mutants",
               "command": "python dogfood/contracts/transitive_probes.py " + " ".join(sys.argv[1:]),
               "control": control, "mutants": results,
               "totals": {"killed": n_killed, "total": len(results)},
               "wall_s": round(time.time() - t_start, 1)}  # fmt: skip
        text = json.dumps(doc, indent=1).replace(str(scratch), "$SCRATCH") + "\n"
        (out / "mutants.json").write_text(text)
    return 0 if control_ok and n_killed == len(results) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--base",
        type=Path,
        default=None,
        help="a directory holding lattice/ of a4ab4a1 (git archive a4ab4a1 lattice)",
    )
    ap.add_argument("--only", default="", help="comma-separated probe (or mutant) ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones")
    ap.add_argument("--mutants", action="store_true", help="run mutants TR1-TR14 instead")
    ap.add_argument(
        "--regression",
        action="store_true",
        help="T-flagoff also runs impact_probes, linkedq_probes and pytest",
    )
    ns = ap.parse_args(argv)
    if ns.mutants:
        return run_mutants(ns)
    if ns.out is None:
        print("--out is required", file=sys.stderr)
        return 2
    return run_probes(ns)


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main())
