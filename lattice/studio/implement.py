"""`lattice spec implement`: implement a candidate spec change on a branch and verify it
(docs/results/2026-10-17-implement-verify-prereg.md; Tier 4 of the change-impact report).

Given a Spike D impact report for a base and a candidate spec, this drives the existing rebuild
loop (:class:`lattice.studio.rebuild.Rebuilder`, unedited) in *change mode* on a detached "spec
worktree": a commit ``S`` = the owner's ``HEAD`` plus exactly the candidate's spec files, on
branch ``implement/<eid>``. The rebuild branch is ``rebuild/<eid>`` from ``S``. The candidate spec
plus the verified flows are then run on the code at ``S`` (*before*) and on the build commit
``B`` (*after*), each in its own fresh worktree; the two runs fill Tier 4
(:mod:`lattice.spec.impact_tier4`). Nothing is merged, pushed or fast-forwarded, and the owner's
checkout, index and other refs are never written (a before and an after snapshot of the owner's git
state is compared; any difference fails the verdict and exits 3).

::

    lattice spec implement --project DIR --impact REPORT.json (--candidate CDIR | --change cN
        [--state-dir SD]) [--inventory INV] [--flows FILE] [--agent claude|stub|oracle]
        [--ref-diff FILE] [--max-rounds N] [--model M] [--usd-cap X] [--work DIR] [--json]
        [--out FILE]

Exit 0: a report with ``tiers["4"]`` set (any verdict). 2: refused (a code, see
:class:`lattice.spec.impact_tier4.Refusal`) or the inputs are unusable. 3: the before or after run
raised, or the owner guard tripped (``tiers["4"]`` is ``not_run:<reason>`` and the report is still
written). Timings and commit shas go to stderr.

``stub`` and ``oracle`` are test-only agents (the rebuild loop's scripted agent; ``oracle`` turns a
reference diff into that script). They call no model and are never the default.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lattice.spec import impact_report as ir
from lattice.spec import impact_tier4 as t4
from lattice.spec.impact_tier4 import Refusal
from lattice.studio import rebuild as rb

EXIT_OK, EXIT_REFUSED, EXIT_RAISED = 0, 2, 3
AGENTS = ("claude", "stub", "oracle")
SIDE_TIMEOUT = 900.0
PROBE_ENVLOG = "IMPLEMENT_PROBE_ENVLOG"  # test hook: where a child records whether it saw a key

SideRunner = Callable[[Path, Sequence[Mapping[str, Any]], str], dict[str, Any]]


# ---- git ----------------------------------------------------------------------------------------


def _git(
    root: Path | str, *args: str, env: Mapping[str, str] | None = None, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
    )


def _git_ok(root: Path | str, *args: str, env: Mapping[str, str] | None = None) -> str:
    r = _git(root, *args, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _inside(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


# ---- the owner guard (D3) -----------------------------------------------------------------------


def _own_worktree(path: str, work: Path) -> bool:
    p = Path(path)
    return _inside(p, work) or p.name.startswith("lattice-rebuild-")


def owner_snapshot(repo: Path, work: Path) -> dict[str, str]:
    """The owner's git state, read without refreshing the index (``GIT_OPTIONAL_LOCKS=0``), minus
    what E itself makes: the ``implement/*`` and ``rebuild/*`` branches and E's own worktrees."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def g(*args: str) -> str:
        r = _git(repo, *args, env=env)
        return r.stdout if r.returncode == 0 else f"[exit {r.returncode}] {r.stdout}"

    refs = [
        ln
        for ln in g("for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        if not ln.startswith(("refs/heads/implement/", "refs/heads/rebuild/"))
    ]
    blocks = [b for b in g("worktree", "list", "--porcelain").split("\n\n") if b.strip()]
    kept = [
        b for b in blocks if not _own_worktree(b.splitlines()[0].removeprefix("worktree "), work)
    ]
    return {
        "head": g("rev-parse", "HEAD").strip(),
        "branch": g("symbolic-ref", "-q", "HEAD").strip(),
        "status": g("status", "--porcelain=v2", "-uall"),
        "cached": g("diff", "--cached", "--name-status"),
        "refs": "\n".join(refs),
        "stash": g("stash", "list"),
        "worktrees": "\n\n".join(kept),
    }


def snapshot_diff(a: Mapping[str, str], b: Mapping[str, str]) -> list[str]:
    return [k for k in a if a[k] != b.get(k)]


# ---- the side run: the candidate spec plus the verified flows on one tree ----------------------


def _finding_dict(f: Any, flow: str | None) -> dict[str, Any]:
    details = json.loads(json.dumps(f.details or {}, default=str))
    return {
        "layer": f.layer,
        "verdict": f.verdict,
        "rule": f.rule,
        "endpoint": f.endpoint,
        "case": f.case,
        "code": getattr(f, "code", None),
        "summary": f.summary(),
        "details": details,
        "request": json.loads(json.dumps(f.request, default=str)) if f.request else None,
        "flow": flow,
    }


def _side_main(argv: Sequence[str]) -> int:
    """Child process (cwd = the project dir of a worktree): run the spec found here, with the
    flows, through D's ``run_tier1`` on the harness the project's ``studio.toml`` names, and
    write ``{"findings", "flows_run"}`` to ``--out``."""
    p = argparse.ArgumentParser(prog="lattice.studio.implement _side")
    p.add_argument("--flows", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--layers", default="ACD")
    args = p.parse_args(list(argv))
    if os.environ.get(PROBE_ENVLOG):
        with open(os.environ[PROBE_ENVLOG], "a", encoding="utf-8") as fh:
            has_key = any(k.startswith("ANTHROPIC") for k in os.environ)
            fh.write(json.dumps({"who": "side", "key": has_key}) + "\n")
    from lattice.spec.impact_tier2 import SpecLines

    project = Path.cwd()
    flows = json.loads(args.flows.read_text(encoding="utf-8"))
    runner = ir.harness_runner(project)
    side = ir.read_side(project)
    inp = ir.build_inputs(project=None, base_side=side, cand_side=side)
    t1 = ir.run_tier1(
        inp,
        runner,
        layers=args.layers,
        baseline="none",
        flows=flows,
        flow_why=None,
        race=False,
        cand_lines=SpecLines(side.module),
    )
    flow_ids = {str(f["id"]) for f in flows}
    own, by_flow = ir._split(t1.cand_report, flow_ids)
    findings = [_finding_dict(f, None) for f in own]
    for fid in sorted(by_flow):
        findings += [_finding_dict(f, fid) for f in by_flow[fid]]
    out = {"findings": findings, "flows_run": sorted(flow_ids)}
    args.out.write_text(json.dumps(out, sort_keys=True), encoding="utf-8")
    return 0


class SideError(RuntimeError):
    """A before or after run could not complete."""


def subprocess_side_runner(
    repo: Path, project_rel: str, sys_path: Sequence[str] = (), timeout: float = SIDE_TIMEOUT
) -> SideRunner:
    """The default side runner: ``python -m lattice.studio.implement _side`` with the project
    dir of the worktree as its cwd, in the rebuild gates' environment (no API key)."""

    def run(worktree: Path, flows: Sequence[Mapping[str, Any]], which: str) -> dict[str, Any]:
        project = (worktree / project_rel).resolve() if project_rel != "." else worktree.resolve()
        env = rb.gate_env(
            os.environ, repo=repo, worktree=worktree, project_rel=project_rel, sys_path=sys_path
        )
        env.pop("LATTICE_REBUILD", None)
        with tempfile.TemporaryDirectory(prefix="lattice-implement-side-") as d:
            fpath, opath = Path(d) / "flows.json", Path(d) / "out.json"
            fpath.write_text(json.dumps(list(flows)), encoding="utf-8")
            if os.environ.get(ir.OWNER_ENV):
                env[ir.OWNER_ENV] = os.environ[ir.OWNER_ENV]
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "lattice.studio.implement",
                    "_side",
                    "--flows",
                    str(fpath),
                    "--out",
                    str(opath),
                ],  # fmt: skip
                cwd=str(project),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0 or not opath.is_file():
                tail = (proc.stderr or proc.stdout).strip()[-800:]
                raise SideError(f"the {which} run exited {proc.returncode}: {tail}")
            return json.loads(opath.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    return run


# ---- options and result -------------------------------------------------------------------------


@dataclass
class Options:
    project: Path
    impact: Path
    candidate: Path | None = None
    change: str | None = None
    state_dir: Path | None = None
    inventory: Path | None = None
    flows: Path | None = None
    agent: str = "claude"
    ref_diff: Path | None = None
    max_rounds: int | None = None
    model: str | None = None
    usd_cap: float | None = None
    work: Path | None = None
    out: Path | None = None
    claude_bin: str = "claude"


@dataclass
class Outcome:
    report: dict[str, Any]
    code: int
    notes: list[str] = field(default_factory=list)
    hint: str | None = None  # the rebuild's merge_hint, verbatim
    shas: dict[str, str] = field(default_factory=dict)  # stderr only: never in the report


# ---- inputs -------------------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_flows(
    opts: Options, inv: Any
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    """(verified flows, the inventory hashes to check the report against, stale)."""
    if opts.flows is not None:
        try:
            raw = opts.flows.read_bytes()
            doc = json.loads(raw)
        except (OSError, ValueError) as err:
            raise Refusal("bad_inputs", f"--flows {opts.flows.name}: {err}") from None
        flows = doc.get("flows") if isinstance(doc, dict) else doc
        if not isinstance(flows, list):
            raise Refusal("bad_inputs", "--flows must hold a list of flows (or {flows: [...]})")
        hashes = {"extract_sha256": None, "workflows_sha256": _sha(raw)}
        if inv is not None:
            hashes = {
                "extract_sha256": inv.extract_sha256,
                "workflows_sha256": inv.workflows_sha256,
            }
        return [f for f in flows if f.get("status") == "verified"], hashes, False
    if inv is None:
        return [], None, False
    hashes = {"extract_sha256": inv.extract_sha256, "workflows_sha256": inv.workflows_sha256}
    if inv.stale is not None:
        return [], hashes, True
    flows, _why = ir.verified_flows(inv)
    return flows, hashes, False


def _markdown_diff(base: str | None, cand: str | None) -> str:
    return "".join(
        difflib.unified_diff(
            (base or "").splitlines(True),
            (cand or "").splitlines(True),
            "REQUIREMENTS.md",
            "REQUIREMENTS.md",
            n=3,
        )
    )


def _candidate_steps(spec: Any) -> list[dict[str, Any]]:
    from lattice.spec.model import Step

    out = []
    for sc in spec.scenarios:
        for k, step in enumerate(sc.steps, 1):
            if isinstance(step, Step):
                out.append(
                    {
                        "scenario": sc.name,
                        "k": k,
                        "method": step.method,
                        "path": step.path,
                        "user": step.user,
                        "expect_status": step.expect_status,
                    }
                )
    return out


# ---- the oracle agent (R5) ---------------------------------------------------------------------


def oracle_script(repo: Path, project_rel: str, ref_diff: str, work: Path) -> dict[str, Any]:
    """A ``stub-rebuild.json`` whose round 1 holds the post-diff content of every file the
    reference diff touches: the diff is checked (``git apply --check``) and applied in a scratch
    copy of ``HEAD``, never in a worktree of the repository."""
    scratch = Path(tempfile.mkdtemp(prefix="oracle-", dir=work))
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "archive", "HEAD"], capture_output=True, timeout=120
        )
        if proc.returncode != 0:
            raise Refusal("bad_inputs", "cannot archive HEAD for the reference diff")
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
            tf.extractall(scratch, filter="data")
        patch = scratch.parent / (scratch.name + ".diff")
        patch.write_text(ref_diff, encoding="utf-8")
        for flag in (["--check"], []):
            r = subprocess.run(
                ["git", "apply", *flag, str(patch)], cwd=scratch, capture_output=True, text=True
            )
            if r.returncode != 0:
                raise Refusal(
                    "bad_inputs", f"the reference diff does not apply: {r.stderr.strip()}"
                )
        paths = re.findall(r"^diff --git a/(\S+) b/(\S+)$", ref_diff, re.M)
        pre = "" if project_rel == "." else project_rel.rstrip("/") + "/"
        files: dict[str, str | None] = {}
        for a, b in paths:
            for p in {a, b}:
                if pre and not p.startswith(pre):
                    raise Refusal(
                        "bad_inputs", f"the reference diff touches {p}, outside the project"
                    )
                f = scratch / p
                files[p[len(pre) :]] = f.read_text(encoding="utf-8") if f.is_file() else None
        patch.unlink(missing_ok=True)
        return {"rounds": [{"files": dict(sorted(files.items()))}]}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---- the work ----------------------------------------------------------------------------------


def _parse_hunks(diff: str) -> dict[str, list[list[int]]]:
    """path -> [[old_start, old_count], ...] of a ``-U0`` diff (the old side: the code at S)."""
    out: dict[str, list[list[int]]] = {}
    path = None
    for line in diff.splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)$", line)
        if m:
            path = m.group(1)
            out.setdefault(path, [])
        h = re.match(r"^@@ -(\d+)(?:,(\d+))? \+", line)
        if h and path is not None:
            out[path].append([int(h.group(1)), int(h.group(2) if h.group(2) is not None else 1)])
    return out


def _worktree_remove(repo: Path, path: Path) -> None:
    with contextlib.suppress(Exception):
        _git(repo, "worktree", "remove", "--force", str(path))
    shutil.rmtree(path, ignore_errors=True)


def _usd(rec: Mapping[str, Any]) -> float:
    return float((rec.get("usage") or {}).get("cost_usd_list_estimate") or 0.0)


def implement(
    opts: Options,
    *,
    side_runner: SideRunner | None = None,
    log: Callable[[str], None] = lambda _m: None,
    poll: float = 0.25,
) -> Outcome:
    """Run `lattice spec implement`: the Python API (``side_runner`` replaces the subprocess
    runner, for tests). Raises :class:`Refusal` for the exit-2 cases; any failure of a before or
    after run is an :class:`Outcome` with code 3."""
    t_start = time.perf_counter()
    if opts.agent not in AGENTS:
        raise Refusal("bad_inputs", f"--agent must be one of {', '.join(AGENTS)}")
    project = Path(opts.project).resolve()
    if opts.candidate is None and opts.change is None:
        raise Refusal("bad_inputs", "give --candidate DIR or --change cN")
    try:
        report_raw = Path(opts.impact).read_bytes()
        report = json.loads(report_raw)
    except (OSError, ValueError) as err:
        raise Refusal("bad_report", f"--impact {Path(opts.impact).name}: {err}") from None
    # ---- the sides (plain files; StateStore is never opened)
    try:
        inp = ir.build_inputs(
            project=project,
            candidate=opts.candidate,
            change=opts.change,
            state_dir=opts.state_dir,
            inventory=opts.inventory,
        )
    except ir.ImpactError as err:
        raise Refusal(
            "change_not_ready" if err.code == "change_not_ready" else "bad_inputs", err.detail
        ) from None
    if opts.inventory is not None and inp.inventory is None:
        raise Refusal("bad_inputs", f"the inventory cannot be loaded: {inp.inventory_error}")
    inv = inp.inventory
    flows, inv_hashes, stale = _load_flows(opts, inv)
    t4.check_report(
        report,
        base=inp.base.hashes(),
        cand=inp.cand.hashes(),
        inventory=inv_hashes,
        inventory_stale=stale,
    )
    eid = t4.eid_of(inp.base.hashes(), inp.cand.hashes())
    # ---- the repository, the table and the capability
    top = rb._toplevel(project)
    if top is None:
        raise Refusal("not_a_repo", f"{project} is not inside a git repository")
    project_rel = project.relative_to(top).as_posix() if project != top else "."
    table = dict(rb.load_table(project))
    table["worktrees"] = "temp"
    table["agent"] = "claude" if opts.agent == "claude" else "stub"
    if opts.max_rounds is not None:
        table["max_rounds"] = int(opts.max_rounds)
    if opts.model:
        table["model"] = opts.model
    ok, why = rb.capability(project, table, opts.claude_bin)
    if not ok:
        raise Refusal("not_a_repo" if "git repository" in why else "no_rebuild", why)
    cfg = rb.RebuildConfig(project, table)
    # ---- paths: --work and --out are outside the project, the repository, the app and inventory
    roots = [project, top, inv.app_root if inv else None, inv.directory if inv else None]
    own_work = opts.work is None
    work = Path(tempfile.mkdtemp(prefix="lattice-implement-")) if own_work else Path(opts.work)  # type: ignore[arg-type]
    work = work.resolve()
    for label, path in (("--work", work), ("--out", opts.out)):
        if path is not None and any(_inside(Path(path), r) for r in roots):
            if own_work and label == "--work":
                shutil.rmtree(work, ignore_errors=True)
            raise Refusal(
                "bad_paths",
                f"{label} is inside the project, the repository, the app or the inventory",
            )
    work.mkdir(parents=True, exist_ok=True)
    branch_spec, branch_build = f"implement/{eid}", f"rebuild/{eid}"
    try:
        if branch_spec in cfg.protected_branches or branch_build in cfg.protected_branches:
            raise Refusal("branch_protected", f"{branch_spec} is a protected branch")
        checked = rb._checked_out(top)
        for br in (branch_spec, branch_build):
            if br in checked:
                raise Refusal("branch_checked_out", f"{br} is checked out in {checked[br]}")
        for br in (branch_spec, branch_build):
            if _git(top, "show-ref", "--verify", "-q", f"refs/heads/{br}").returncode == 0:
                raise Refusal("branch_exists", f"branch {br} exists")
        return _implement(
            opts, report, inp, flows, eid, top, project, project_rel, table, cfg, work,
            side_runner, log, poll, t_start,
        )  # fmt: skip
    finally:
        if own_work:
            shutil.rmtree(work, ignore_errors=True)


def _implement(
    opts: Options,
    report: dict[str, Any],
    inp: ir.Inputs,
    flows: list[dict[str, Any]],
    eid: str,
    top: Path,
    project: Path,
    project_rel: str,
    table: dict[str, Any],
    cfg: rb.RebuildConfig,
    work: Path,
    side_runner: SideRunner | None,
    log: Callable[[str], None],
    poll: float,
    t_start: float,
) -> Outcome:
    branch_spec, branch_build = f"implement/{eid}", f"rebuild/{eid}"
    notes: list[str] = []
    shas: dict[str, str] = {}
    sys_path = _app_sys_path(project)
    runner = side_runner or subprocess_side_runner(top, project_rel, sys_path)
    stub_text: str | None = None
    test_only: list[str] = []
    if opts.agent == "stub":
        stub_path = project / rb.STUB_FILE
        if not stub_path.is_file():
            raise Refusal("no_rebuild", f"the stub agent needs {rb.STUB_FILE} in the project")
        stub_text = stub_path.read_text(encoding="utf-8")
        test_only = [rb.STUB_FILE]
    elif opts.agent == "oracle":
        if opts.ref_diff is None:
            raise Refusal("bad_inputs", "--agent oracle needs --ref-diff FILE")
        try:
            diff_text = Path(opts.ref_diff).read_text(encoding="utf-8")
        except OSError as err:
            raise Refusal("bad_inputs", f"--ref-diff: {err}") from None
        stub_text = json.dumps(oracle_script(top, project_rel, diff_text, work), indent=1) + "\n"
        test_only = [rb.STUB_FILE]
    spec_wt, before_wt, after_wt = (work / f"{n}-{eid}" for n in ("spec", "before", "after"))
    snap0 = owner_snapshot(top, work)
    state = "run"
    tier4: dict[str, Any]
    code = EXIT_OK
    hint: str | None = None
    rebuilder: rb.Rebuilder | None = None
    run_id: str | None = None
    try:
        # ---- the spec commit S (D3): exactly the candidate's spec files, git add by path
        _git_ok(top, "worktree", "add", "--detach", "-q", str(spec_wt), "HEAD")
        sp_proj = spec_wt if project_rel == "." else spec_wt / project_rel
        files = {
            "REQUIREMENTS.md": inp.cand.markdown,
            "spec.py": inp.cand.module,
            "spec.trace.json": inp.cand.trace_text,
        }
        paths: list[str] = []
        for name, text in files.items():
            if text is None:
                continue
            (sp_proj / name).write_text(text, encoding="utf-8")
            paths.append(name if project_rel == "." else f"{project_rel}/{name}")
        if stub_text is not None:
            (sp_proj / rb.STUB_FILE).write_text(stub_text, encoding="utf-8")
            paths.append(rb.STUB_FILE if project_rel == "." else f"{project_rel}/{rb.STUB_FILE}")
        _git_ok(spec_wt, "add", "--", *paths)
        ident: list[str] = []
        if not _git(spec_wt, "config", "user.email").stdout.strip():
            ident = ["-c", "user.name=Lattice implement", "-c", "user.email=lattice@localhost"]
        msg = f"spec: candidate {eid} (Lattice implement; not built)\n\nLattice-Implement: {eid}\n"
        _git_ok(spec_wt, *ident, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty",
                "--no-verify", "-m", msg)  # fmt: skip
        s_sha = _git_ok(spec_wt, "rev-parse", "HEAD")
        r = _git(top, "update-ref", f"refs/heads/{branch_spec}", s_sha, "")
        if r.returncode != 0:
            raise Refusal("branch_exists", f"branch {branch_spec} appeared meanwhile")
        shas["spec"] = s_sha
        spec_tree = _git_ok(top, "rev-parse", f"{s_sha}^{{tree}}")
        # ---- before: the candidate spec plus the flows on the code at S
        t0 = time.perf_counter()
        before = _side(top, runner, s_sha, before_wt, flows, "before")
        log(f"before run {time.perf_counter() - t0:.1f}s ({len(t4.failing_set(before))} failing)")
        impl_base = {"branch_spec": branch_spec, "spec_tree": spec_tree}
        t2_claims = len((report.get("tier2") or {}).get("determined") or []) + len(
            (report.get("tier2") or {}).get("flows") or []
        )
        if not t4.failing_set(before) and not t2_claims:
            tier4 = t4.placeholder("nothing_to_build", None, before, report)
        else:
            change = t4.change_dict(
                eid, report, request=_request_of(opts, inp),
                markdown_diff=_markdown_diff(inp.base.markdown, inp.cand.markdown), before=before,
            )  # fmt: skip
            env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
            rebuilder = rb.Rebuilder(
                sp_proj, table, name=project.name, model=opts.model or "sonnet",
                claude_bin=opts.claude_bin, sys_path=_app_sys_path(sp_proj), env=env,
            )  # fmt: skip
            run_id = rebuilder.start([change], model=opts.model, max_rounds=opts.max_rounds)["id"]
            rec = _wait(rebuilder, run_id, cfg, opts, notes, poll)
            hint = rec.get("merge_hint")
            gates = [
                {"command": g.get("command"), "exit_code": g.get("exit_code")}
                for g in (rec["rounds"][-1].get("gates") if rec.get("rounds") else None) or []
            ]
            info: dict[str, Any] = {
                "status": rec["status"],
                "rounds": len(rec.get("rounds") or []),
                "agent": opts.agent,
                "model": (opts.model or "sonnet") if opts.agent == "claude" else None,
                "gates": gates,
            }
            if opts.agent == "claude":
                info["usage"] = rec.get("usage")
            if rec["status"] != "passed":
                state = f"not_run:rebuild_{rec['status']}"
                tier4 = t4.placeholder(
                    "not_built", info, before, report,
                    implementation={**impl_base, "branch_build": rec.get("branch"),
                                    "build_tree": None, "diff_sha256": None, "files": []},
                    notes=notes,
                )  # fmt: skip
            else:
                b_sha = rec["commit"]["sha"]
                shas["build"] = b_sha
                build_tree = _git_ok(top, "rev-parse", f"{b_sha}^{{tree}}")
                names = _git_ok(top, "diff", "--name-only", s_sha, b_sha).splitlines()
                full_diff = _git(top, "diff", "--no-color", "--no-ext-diff", s_sha, b_sha).stdout
                hunks = _parse_hunks(_git(top, "diff", "-U0", "--no-color", s_sha, b_sha).stdout)
                impl = {
                    **impl_base,
                    "branch_build": rec["branch"],
                    "build_tree": build_tree,
                    "diff_sha256": _sha(full_diff.encode("utf-8")),
                    "files": sorted(names),
                }
                t0 = time.perf_counter()
                try:
                    after = _side(top, runner, b_sha, after_wt, flows, "after")
                except Exception as err:  # noqa: BLE001 -- the after run could not complete
                    state, code = f"not_run:error: {type(err).__name__}: {err}", EXIT_RAISED
                    tier4 = t4.placeholder(
                        "fail",
                        info,
                        before,
                        report,
                        implementation=impl,
                        notes=[*notes, f"the after run raised: {err}"],
                    )
                    after = None
                if after is not None:
                    log(f"after run {time.perf_counter() - t0:.1f}s")
                    edit = {
                        "files": t4.edit_files(names, project_rel=project_rel, test_only=test_only),
                        "hunks": hunks,
                    }
                    tier4 = t4.compute(
                        report, before, after, flows, info, edit,
                        cand_steps=_candidate_steps(inp.cand.spec),
                        links=inp.cand.trace.links, implementation=impl, notes=notes,
                        tree=_go_tree(inp), prefix=_app_prefix(inp, project_rel),
                    )  # fmt: skip
    except Refusal:
        raise
    except Exception as err:  # noqa: BLE001 -- a before run (or the loop) that could not complete
        state, code = f"not_run:error: {type(err).__name__}: {err}", EXIT_RAISED
        tier4 = t4.placeholder(
            "not_built", None, locals().get("before") or {"findings": [], "flows_run": []},
            report, implementation=None, notes=[*notes, f"{type(err).__name__}: {err}"],
        )  # fmt: skip
    finally:
        if rebuilder is not None:
            with contextlib.suppress(Exception):
                if run_id is not None and rebuilder.get(run_id)["status"] not in rb.FINISHED:
                    rebuilder.stop(run_id)
            with contextlib.suppress(Exception):
                rebuilder.close()
        for wt in (spec_wt, before_wt, after_wt):
            _worktree_remove(top, wt)
        with contextlib.suppress(Exception):
            _git(top, "worktree", "prune")
    snap1 = owner_snapshot(top, work)
    changed = snapshot_diff(snap0, snap1)
    owner = {"untouched": not changed, "checked": sorted(snap0)}
    if changed:
        state, code = "not_run:owner_changed", EXIT_RAISED
        notes.append(f"the owner's git state changed: {', '.join(changed)}")
        tier4["notes"] = sorted([*tier4.get("notes", []), notes[-1]])
    tier4 = t4.with_owner(tier4, owner)
    merged = t4.merge(report, state, tier4)
    log(f"total {time.perf_counter() - t_start:.1f}s")
    return Outcome(merged, code, notes, hint, shas)


def _request_of(opts: Options, inp: ir.Inputs) -> str | None:
    if opts.change is None:
        return None
    sd = opts.state_dir if opts.state_dir is not None else ir.default_state_dir(inp.project)  # type: ignore[arg-type]
    try:
        doc = json.loads((Path(sd) / "changes" / f"{opts.change}.json").read_text(encoding="utf-8"))
        req = doc["record"].get("request")
        return str(req) if req else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _app_sys_path(project: Path) -> list[str]:
    import tomllib

    try:
        data = tomllib.loads((Path(project) / "studio.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    app = data.get("app")
    sp = app.get("sys_path") if isinstance(app, dict) else None
    return [str(x) for x in sp] if isinstance(sp, list) else []


def _app_prefix(inp: ir.Inputs, project_rel: str) -> str:
    """What to strip from a repo path to get a path under the inventory's app root."""
    inv = inp.inventory
    if inv is None or inp.project is None:
        return ""
    try:
        top = rb._toplevel(Path(inp.project))
        return Path(inv.app_root).resolve().relative_to(top).as_posix() + "/" if top else ""
    except ValueError:
        return ""


def _go_tree(inp: ir.Inputs) -> Any:
    inv = inp.inventory
    if inv is None:
        return None
    from lattice.spec.impact_tier3 import GoTree

    return GoTree(inv)


def _side(
    top: Path,
    runner: SideRunner,
    sha: str,
    wt: Path,
    flows: Sequence[Mapping[str, Any]],
    which: str,
) -> dict[str, Any]:
    """One run in its own fresh detached worktree at `sha`, with a fresh container owner token;
    the worktree is removed whatever happens."""
    _git_ok(top, "worktree", "add", "--detach", "-q", str(wt), sha)
    try:
        with ir.owned_containers():
            return runner(wt, flows, which)
    finally:
        _worktree_remove(top, wt)


def _wait(
    rebuilder: rb.Rebuilder,
    rid: str,
    cfg: rb.RebuildConfig,
    opts: Options,
    notes: list[str],
    poll: float,
) -> dict[str, Any]:
    """Wait for the rebuild to end; ``--usd-cap`` (claude mode) stops it past the cap."""
    limit = (cfg.round_timeout + cfg.gate_timeout * max(1, len(cfg.gates))) * (
        int(opts.max_rounds or cfg.max_rounds) + 1
    ) + 120
    t0 = time.monotonic()
    capped = False
    while True:
        rec = rebuilder.get(rid, wait=poll)
        if rec["status"] in rb.FINISHED:
            return rec
        if opts.usd_cap is not None and opts.agent == "claude" and not capped:
            if _usd(rec) > opts.usd_cap:
                capped = True
                notes.append(
                    f"stopped: the list-price estimate passed the USD {opts.usd_cap:g} cap"
                )
                with contextlib.suppress(rb.RebuildError):
                    rebuilder.stop(rid)
        if time.monotonic() - t0 > limit:
            notes.append("stopped: the rebuild passed its wall-clock limit")
            with contextlib.suppress(rb.RebuildError):
                rebuilder.stop(rid)
            t0 = time.monotonic()


# ---- text ---------------------------------------------------------------------------------------


def render(report: Mapping[str, Any]) -> str:
    t = report.get("tier4") or {}
    out = [f"implement: {t.get('verdict')} (tier 4: {report['tiers']['4']})"]
    impl = t.get("implementation")
    if impl:
        out.append(f"branches: {impl['branch_spec']} (spec), {impl['branch_build']} (build)")
    rb_ = t.get("rebuild")
    if rb_:
        out.append(f"rebuild: {rb_['status']} in {rb_['rounds']} round(s), agent {rb_['agent']}")
    out.append(f"before matches the report: {(t.get('before') or {}).get('matches_report')}")
    c = t.get("counts") or {}
    if c:
        out.append(
            f"resolved {c['resolved']}, still failing {c['still_failing']}, regressions "
            f"{c['regressions']}, flows changed {c['flow_changed']} "
            f"(unreported {c['unreported']}), "
            f"workflow fails {c['workflow_fails']}, T2 contradicted {c['t2_contradicted']}"
        )
    for key in ("resolved", "still_failing", "regressions", "flows"):
        for i in t.get(key) or []:
            if key == "flows" and i["kind"] == "flow_unchanged":
                continue
            out.append(
                f"  [{i['kind']}] {i.get('target') or '-'}: {i.get('summary') or ''}".rstrip()
            )
    out.append(f"owner untouched: {(t.get('owner') or {}).get('untouched')}")
    return "\n".join(out) + "\n"


# ---- the CLI ------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lattice spec implement",
        description="Implement a candidate spec change on a branch (the rebuild loop, change "
        "mode, on a detached spec worktree) and verify it: before and after runs of the candidate "
        "spec and the verified flows fill Tier 4 of the impact report. Never merges.",
    )
    p.add_argument("--project", type=Path, required=True, help="the project directory")
    p.add_argument("--impact", type=Path, required=True, help="the lattice.impact/1 report")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidate", type=Path, help="a directory with the candidate's spec files")
    g.add_argument("--change", help="a Studio change id (cN), read from the state directory")
    p.add_argument("--state-dir", type=Path, help="the Studio state directory (default: derived)")
    p.add_argument("--inventory", type=Path, help="the inventory directory")
    p.add_argument("--flows", type=Path, help="a verified-flows file (workflows.json shape)")
    p.add_argument("--agent", choices=AGENTS, default="claude", help="stub and oracle: test only")
    p.add_argument("--ref-diff", type=Path, help="oracle: the reference diff to replay")
    p.add_argument("--max-rounds", type=int, help="rounds before the rebuild gives up")
    p.add_argument("--model", help="the agent's model")
    p.add_argument("--usd-cap", type=float, help="claude: stop past this list-price estimate")
    p.add_argument("--work", type=Path, help="where the spec, before and after worktrees go")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path, help="also write the report here")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_side":
        return _side_main(argv[1:])
    if argv and argv[0] == "implement":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0

    def log(msg: str) -> None:
        print(f"implement: {msg}", file=sys.stderr)

    opts = Options(
        project=args.project, impact=args.impact, candidate=args.candidate, change=args.change,
        state_dir=args.state_dir, inventory=args.inventory, flows=args.flows, agent=args.agent,
        ref_diff=args.ref_diff, max_rounds=args.max_rounds, model=args.model,
        usd_cap=args.usd_cap, work=args.work, out=args.out,
    )  # fmt: skip
    try:
        res = implement(opts, log=log)
    except Refusal as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as err:  # noqa: BLE001 -- anything else the inputs cannot explain
        print(f"error: {type(err).__name__}: {err}", file=sys.stderr)
        return EXIT_REFUSED
    text = ir.report_json(res.report)
    sys.stdout.write(text if args.json else render(res.report))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    for k, v in sorted(res.shas.items()):
        log(f"{k} commit {v}")
    if res.hint:
        print(f"implement: {res.hint}", file=sys.stderr)
    return res.code


if __name__ == "__main__":
    sys.exit(main())
