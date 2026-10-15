"""Probe runner for the cross-project impact preview (``lattice contracts impact``, slice 4a).

Implements the probes of ``docs/results/2026-10-13-contracts-impact-prereg.md`` (section 3): I0a,
I0b, I0c, I1, I2, I3, I4, I5, I6, I7, I8 and T. Predictions and pass rules are copied from that
document; a probe that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/impact_probes.py --runs 10 --scratch DIR \
        --out docs/results/data/2026-10-13-contracts-impact [--only I1,I2,...] [--skip-regression]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 and of
Streak at a0db033 under ``DIR``; the real repositories are only read (I6 (4) proves it). No LLM
and no API key are used. Every deterministic probe runs ``--runs`` times (default 10) and all
canonical outputs (``elapsed_s`` excluded, scratch root -> ``$WORK``) must be identical. Raw
per-run outputs go to ``OUT/<ID>.json`` and ``OUT/summary.json``.

The helpers (Ctx, Edit, Result, canonical outputs, MCP and API access, the edit tables) are those
of ``probes.py``, ``streak_probes.py`` and ``exclude_probes.py``, imported from the files next to
this one. A candidate ref ``dN`` is the committed edit list of D(N) applied to a separate clone
(committed with the runners' fixed author and dates) and fetched into the provider clone before
the measured window.

**I6 tracing.** Every call of I0-I5 and I7 is wrapped: sha256 of every file under the probe's
workspace directory, consumers, provider clones and the lattice-impact worktree before and after,
files newer than the call's start, the entries left in the run's ``TMPDIR``, and a
``sitecustomize`` on ``PYTHONPATH`` that logs every ``subprocess.Popen`` argv and
``socket.connect`` of every Python process the call starts.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OLD_CHECKOUT = Path("/Users/vivekgade/Documents/lattice")  # main checkout, next-phases 67189af
OLD_HEAD = "67189af"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


ep = _load("lattice_contracts_exclude_probes_i", HERE / "exclude_probes.py")
streak = ep.streak
base = ep.base
Result = base.Result
canon_call = base.canon_call
canon_json = base.canon_json
strip_elapsed = base.strip_elapsed
sha = base.sha
mk = ep.mk
yn = ep.yn

COMMITTED = REPO / "docs" / "results" / "data"
EXP_J = COMMITTED / "2026-10-13-contracts"
EXP_S = COMMITTED / "2026-10-13-contracts-streak"
EXP_X = COMMITTED / "2026-10-13-contracts-exclude"
MANIFEST = base.MANIFEST
LOCK = base.LOCK
IMPORTS = "lattice.imports.toml"
EX = ep.EX
HARNESS = ep.HARNESS
CLOCK_IMPORT = ep.CLOCK_IMPORT
SCHEDULER_GAPS = ep.SCHEDULER_GAPS
STREAK_GAPS = ep.STREAK_GAPS
GIT_OK = {"rev-parse", "ls-tree", "cat-file", "status"}

POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "I1/I2: a first development run (--runs 2) failed every row on an extra check of my own, "
    "'candidate.resolved equals the committed Dn run's git_ref'; the prereg does not ask for it "
    "and it cannot hold: a clone's commit message carries the clone's name (`probe <name>`), so "
    "the sha differs from the committed run's although the tree is the same. The check is now "
    "'resolved is the commit that was fetched as dN'. No pass rule of the prereg changed.",
]
POST_HOC_PROCEDURE: list[str] = [
    "Helpers: JCtx and SX (exclude_probes.py: Ctx subclasses of probes.py and streak_probes.py) "
    "are reused; the manifests of the multi-consumer workspaces (I4) are written here. A "
    "candidate ref dN is the committed edit list applied to a clone of the clean clone "
    "(`ctx.clone`, the runners' fixed author and dates, so the sha is the committed Dn's) and "
    "fetched into the provider clone as refs/heads/dN, before the measured window.",
    "Each run of a deterministic probe makes the --json call and the text call; the canonical "
    "record holds both (the JSON without elapsed_s). Scratch paths are scrubbed to $WORK only "
    "when written out; equality across the 10 runs is on the raw canonical records.",
    "'= committed Dn' compares the row's `findings` and `gaps` with `links[0]` of `runs[0]` of "
    "the committed data file (job-scheduler D/C: runs[0].doc; Streak D: runs[0].check.doc; "
    "Streak C: runs[0].doc; exclude X2: runs[0].check_after_pin.doc, X3: runs[0].r6_check.doc).",
    "I6: a `sitecustomize` in a directory first on PYTHONPATH logs every Popen argv and socket "
    "connect (append-only file outside TMPDIR). Allowed argv: `git` with a subcommand in "
    "{rev-parse, ls-tree, cat-file, status} (global options skipped) or the export child "
    "(`-m lattice.contracts export`); export children counted from the Popen events of the "
    "call. The snapshot covers the probe's workspace directory (manifest variants, consumers), "
    "its provider clone(s), any candidate directory the call names and the lattice-impact "
    "worktree (the --out directory excluded); 'newer' is a file whose mtime is later than the "
    "call's start (the find -newer rule). I7's MCP and API calls are traced from the request "
    "(the server's start-up is not part of the call).",
    "I4 workspaces: A = scheduler (clean clone, spec.tickets:t5), ci-bot, ci-bot-2 (the "
    "`POST /auth/login` and `GET /applications` tables of ci-bot); B = scheduler, streak, "
    "multi (ci-bot's 7 and streak-widget's 7 tables) with static links to both. ci-bot-2 and "
    "multi are pinned with `contracts pin` on the clean clones.",
    "I8: the three runners run as subprocesses with --runs and read their last line; pytest "
    "and dogfood/studio/check.sh run in this process' environment (UV_NO_SYNC set); the "
    "byte comparison runs the same argv under PYTHONPATH=<this tree> and PYTHONPATH=<the main "
    "checkout at 67189af> on the I1 workspace; `pin` writes into two copies of the consumer.",
]

SHIM = """\
import json, os, socket, subprocess, sys
_LOG = os.environ.get("IMPACT_PROBE_LOG")
if _LOG:
    def _w(rec):
        rec["pid"] = os.getpid()
        fd = os.open(_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(rec) + "\\n").encode())
        finally:
            os.close(fd)

    _w({"start": [str(a) for a in sys.argv[:6]],
        "anthropic_env": any(k.startswith("ANTHROPIC") for k in os.environ)})
    _init = subprocess.Popen.__init__

    def _popen(self, args, *a, **kw):
        try:
            argv = [os.fspath(x) for x in (args if isinstance(args, (list, tuple)) else [args])]
        except Exception:
            argv = [repr(args)]
        _w({"popen": argv})
        return _init(self, args, *a, **kw)

    subprocess.Popen.__init__ = _popen
    _conn, _cex = socket.socket.connect, socket.socket.connect_ex

    def _connect(self, addr):
        _w({"connect": repr(addr)})
        return _conn(self, addr)

    def _connect_ex(self, addr):
        _w({"connect": repr(addr)})
        return _cex(self, addr)

    socket.socket.connect, socket.socket.connect_ex = _connect, _connect_ex
"""


# ---- small helpers ------------------------------------------------------------------------


def lk0(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["doc"]["links"][0]  # type: ignore[no-any-return]


def committed(path: Path, *keys: str | int) -> dict[str, Any]:
    """`links[0]` of a committed data file's `runs[0]` (through `keys` when given)."""
    node: Any = json.loads(path.read_text())["runs"][0]
    for k in keys:
        node = node[k]
    return node["doc"]["links"][0]  # type: ignore[no-any-return]


def row0(rec: dict[str, Any]) -> dict[str, Any]:
    return rec["doc"]["consumers"][0]  # type: ignore[no-any-return]


def rows_by(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["consumer"]: r for r in rec["doc"]["consumers"]}


def fnd(row: dict[str, Any], cls: str | None = None) -> list[tuple[str, str, str, Any]]:
    return [
        (f["kind"], f["endpoint"], f["item"], f.get("relied"))
        for f in row["findings"]
        if cls is None or f["class"] == cls
    ]


def same_as(row: dict[str, Any], link: dict[str, Any]) -> bool:
    return row["findings"] == link["findings"] and row["gaps"] == link["gaps"]  # type: ignore[no-any-return]


def gap_ids(row: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(g["kind"], g["endpoint"], g["item"]) for g in row["gaps"]]


def short(row: dict[str, Any]) -> str:
    return f"{row['label']}/{len(row['findings'])}f/{len(row['gaps'])}g"


def write_manifest(path: Path, projects: list[dict[str, str]], links: list[dict[str, str]]) -> Path:
    """A manifest: `projects` (name, path, role, optional spec / exclude as TOML) and `links`."""
    text = '[workspace]\nname = "probe"\n\n'
    for p in projects:
        text += f'[[project]]\nname = "{p["name"]}"\npath = "{p["path"]}"\nrole = "{p["role"]}"\n'
        if p.get("spec"):
            text += f'spec = "{p["spec"]}"\n'
        text += "\n"
    for lk in links:
        text += f'[[link]]\nfrom = "{lk["from"]}"\nto = "{lk["to"]}"\nkind = "uses"\n'
        if lk.get("contract"):
            text += f'contract = "{lk["contract"]}"\n'
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def import_tables(path: Path, endpoints: list[str]) -> str:
    """The `[[import]]` tables of `path` whose endpoint is in `endpoints`, verbatim."""
    chunks = path.read_text().split("[[import]]")[1:]
    keep = [c for c in chunks if any(f'endpoint = "{e}"' in c for e in endpoints)]
    assert len(keep) == len(endpoints), (len(keep), endpoints)
    return "".join("[[import]]" + c.rstrip("\n") + "\n\n" for c in keep)


def all_tables(path: Path) -> str:
    return "".join(
        "[[import]]" + c.rstrip("\n") + "\n\n" for c in path.read_text().split("[[import]]")[1:]
    )


# ---- tracing ------------------------------------------------------------------------------


def snapshot(roots: list[Path], skip: list[Path]) -> tuple[dict[str, str], dict[str, int]]:
    """({path: sha256 | 'link:T' | 'dir'}, {path: mtime_ns}) of everything under `roots`."""
    files: dict[str, str] = {}
    mtimes: dict[str, int] = {}
    for root in roots:
        for dp, dns, fns in os.walk(root):
            here = Path(dp)
            if any(here == s or s in here.parents for s in skip):
                dns[:] = []
                continue
            files[dp + "/"] = "dir"
            for f in fns:
                p = os.path.join(dp, f)
                try:
                    st = os.lstat(p)
                    if os.path.islink(p):
                        files[p] = "link:" + os.readlink(p)
                    else:
                        files[p] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
                    mtimes[p] = st.st_mtime_ns
                except OSError:
                    files[p] = "unreadable"
    return files, mtimes


def git_sub(argv: list[str]) -> str:
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-C", "-c"):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a
    return ""


def is_export_child(argv: list[str]) -> bool:
    return "-m" in argv and "lattice.contracts" in argv and "export" in argv


class Tracer:
    """The I6 record: one entry per traced call (see the module docstring)."""

    def __init__(self, log: Path, worktree: Path, skip: list[Path]) -> None:
        self.log, self.worktree, self.skip = log, worktree, skip
        self.records: list[dict[str, Any]] = []

    def _events(self, offset: int) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        data = self.log.read_bytes()[offset:].decode("utf-8", "replace")
        return [json.loads(ln) for ln in data.splitlines() if ln.strip()]

    def _offset(self) -> int:
        return self.log.stat().st_size if self.log.exists() else 0

    @contextlib.contextmanager
    def around(self, probe: str, label: str, ctx: Any, watch: list[Path], expect: int = 2) -> Any:
        roots = [self.worktree, *[w for w in watch if w.exists()]]
        before, _ = snapshot(roots, self.skip)
        off = self._offset()
        t0 = time.time_ns()
        box: dict[str, Any] = {}
        try:
            yield box
        finally:
            after, mtimes = snapshot(roots, self.skip)
            events = self._events(off)
            popens = [e["popen"] for e in events if "popen" in e]
            bad = [
                a
                for a in popens
                if not (
                    (a and os.path.basename(a[0]) == "git" and git_sub(a) in GIT_OK)
                    or is_export_child(a)
                )
            ]
            tmp_left = sorted(p.name for p in ctx.tmp.iterdir()) if ctx.tmp.exists() else []
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            rec = {
                "probe": probe,
                "call": label,
                "tree_unchanged": not changed,
                "changed": changed[:5],
                "newer": sorted(k for k, m in mtimes.items() if m > t0)[:5],
                "tmp_left": tmp_left,
                "bad_popen": bad,
                "connects": sum(1 for e in events if "connect" in e),
                "export_children": sum(1 for a in popens if is_export_child(a)),
                "expect_children": expect,
                "git_args_with_x": [a for a in popens if any(x.startswith("-x") for x in a)],
                "claude": [a for a in popens if a and os.path.basename(a[0]) == "claude"],
                "anthropic_env": any(e.get("anthropic_env") for e in events),
                "files_hashed": len(after),
            }
            self.records.append(rec)
            box["rec"] = rec

    def summary(self) -> dict[str, Any]:
        recs = self.records
        return {
            "calls": len(recs),
            "tree_changed": [r for r in recs if not r["tree_unchanged"]],
            "newer": [r for r in recs if r["newer"]],
            "tmp_left": [r for r in recs if r["tmp_left"]],
            "bad_popen": [r for r in recs if r["bad_popen"]],
            "connects": sum(r["connects"] for r in recs),
            "children_mismatch": [r for r in recs if r["export_children"] != r["expect_children"]],
            "claude_or_anthropic": [r for r in recs if r["claude"] or r["anthropic_env"]],
            "by_probe": {
                p: sum(1 for r in recs if r["probe"] == p)
                for p in sorted({r["probe"] for r in recs})
            },
        }


class H:
    """The harness: both contexts, the tracer and the shared set-up of the probes."""

    def __init__(self, ns: argparse.Namespace, jc: Any, sc: Any, tracer: Tracer) -> None:
        self.ns, self.jc, self.sc, self.tracer = ns, jc, sc, tracer
        self._refs: dict[tuple[str, str], tuple[Path, str]] = {}

    # -- calls
    def impact(
        self,
        ctx: Any,
        manifest: Path,
        provider: str,
        cand: str,
        *,
        probe: str,
        watch: list[Path],
        text: bool = False,
        cwd: Path | None = None,
        expect: int = 2,
        label: str | None = None,
    ) -> Any:
        argv = [sys.executable, "-m", "lattice.contracts", "impact", provider]
        argv += [f"--candidate={cand}"] if cand.startswith("-") else ["--candidate", cand]
        argv += ["--workspace", str(manifest)]
        if not text:
            argv.append("--json")
        with self.tracer.around(probe, label or f"impact {provider} {cand}", ctx, watch, expect):
            c = ctx.run(argv, cwd=cwd)
        return c

    def both(
        self,
        ctx: Any,
        manifest: Path,
        provider: str,
        cand: str,
        *,
        probe: str,
        watch: list[Path],
        expect: int = 2,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        """The JSON and the text call of one run, as one canonical record."""
        j = self.impact(
            ctx, manifest, provider, cand, probe=probe, watch=watch, expect=expect, cwd=cwd
        )
        t = self.impact(
            ctx,
            manifest,
            provider,
            cand,
            probe=probe,
            watch=watch,
            text=True,
            expect=expect,
            cwd=cwd,
            label=f"impact {provider} {cand} (text)",
        )
        rec = canon_call(j)
        rec["text"] = t.out
        rec["text_rc"] = t.rc
        rec["text_err"] = t.err
        return rec

    # -- set-up
    def ref(
        self,
        ctx: Any,
        provider: Path,
        name: str,
        edits: list[Any] | None,
        commit: bool = True,
        setup: Any = None,
    ) -> tuple[Path, str]:
        """The clone holding the committed edit list, and its fetch into `provider` as `name`
        (once per provider and name)."""
        key = (str(provider), name)
        if key in self._refs:
            return self._refs[key]
        c = ctx.clone(f"r-{provider.parent.name}-{name}", edits, commit=commit)
        if setup is not None:
            setup(c)
            ctx.git(c, "add", "-A")
            ctx.git(c, "commit", "-q", "-m", f"probe {name}")
        sha_ = ctx.git(c, "rev-parse", "HEAD").strip()
        ctx.git(provider, "fetch", "-q", str(c), f"HEAD:refs/heads/{name}")
        self._refs[key] = (c, sha_)
        return c, sha_


def text_ok(rec: dict[str, Any], *banned: str) -> bool:
    t = rec["text"]
    return (
        rec["text_rc"] == rec["rc"]
        and rec["text_err"] == ""
        and t.startswith("impact of ")
        and "\n" in t
        and t.rstrip("\n").splitlines()[-1].endswith(" error")
        and not any(b and b in t for b in banned)
    )


# ---- I0: identity ------------------------------------------------------------------------


def i0_single(
    h: H,
    pid: str,
    ctx: Any,
    tag: str,
    want_gaps: int,
    committed_link: dict[str, Any] | None,
    stale: Any = None,
) -> Result:
    """The shared body of I0a/I0b/I0c (`stale`: edits, I0c's clone at the D2 commit)."""
    if stale is not None:
        clone = ctx.clone(f"{pid}-provider", stale)
        mname = pid
    else:
        clone = ep.shared(ctx)
        mname = pid
    m = ctx.workspace(mname, clone)
    consumer = ctx.consumer
    watch = [m.parent.parent, clone]

    def one(_: int) -> dict[str, Any]:
        rec = h.both(ctx, m, ctx.provider, "HEAD", probe=pid, watch=watch)
        chk = canon_call(
            ctx.contracts(m, "check", consumer, "--static-only", "--json")
        )  # same window
        return {"impact": rec, "check": chk}

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    row, lk = row0(r0["impact"]), lk0(r0["check"])
    clean = stale is None
    checks: dict[str, Any] = {
        "exit": r0["impact"]["rc"] == (0 if clean else 1),
        "label": row["label"] == ("unaffected" if clean else "contradiction"),
        "current_label": row["current_label"] == ("unaffected" if clean else "contradiction"),
        "findings_equal_check": row["findings"] == lk["findings"],
        "gaps_equal_check": row["gaps"] == lk["gaps"],
        "pinned_equal_check": row["pinned"] == lk["pinned"],
        "text_ok": text_ok(r0["impact"]),
        "candidate_head_resolved": r0["impact"]["doc"]["candidate"]["kind"] == "ref"
        and r0["impact"]["doc"]["candidate"]["resolved"]
        == r0["impact"]["doc"]["candidate"]["version"]["git_ref"],
    }
    if clean:
        checks.update(
            findings_0=row["findings"] == [],
            gaps_n=len(row["gaps"]) == want_gaps,
            spec_hash_changed_false=row["spec_hash_changed"] is False,
            version_changed_false=row["version_changed"] is False,
            check_exit_0=r0["check"]["rc"] == 0,
        )
    else:
        assert committed_link is not None
        checks.update(
            findings_equal_committed_D2=row["findings"] == committed_link["findings"],
            check_equals_committed_D2=lk["findings"] == committed_link["findings"],
            gaps_equal_committed_D2=row["gaps"] == committed_link["gaps"],
            check_exit_1=r0["check"]["rc"] == 1,
        )
    obs = (
        f"{tag}: exit {r0['impact']['rc']}, {short(row)}, current_label {row['current_label']}, "
        f"hash_changed {row['spec_hash_changed']}, version_changed {row['version_changed']}; "
        f"check exit {r0['check']['rc']} {len(lk['findings'])}f/{len(lk['gaps'])}g; "
        f"findings==check {yn(checks['findings_equal_check'])} gaps==check "
        f"{yn(checks['gaps_equal_check'])}"
    )
    return mk(pid, "", obs, runs, ident, checks)


def i0a(h: H) -> Result:
    r = i0_single(h, "I0a", h.jc, "job-scheduler", 1, None)
    r.prediction = (
        "job-scheduler, --candidate HEAD, clean provider: ci-bot unaffected, 0 findings, 1 gap "
        "(access_token), findings and gaps equal to `check --static-only` in the same window, "
        "spec_hash_changed and version_changed false, current_label unaffected, exit 0"
    )
    return r


def i0b(h: H) -> Result:
    r = i0_single(h, "I0b", h.sc, "streak", 2, None)
    r.prediction = (
        "Streak, --candidate HEAD, clean provider: streak-widget unaffected, 0 findings, 2 gaps, "
        "equal to `check --static-only`, hash/version unchanged, current_label unaffected, exit 0"
    )
    return r


def i0c(h: H) -> Result:
    d2 = committed(EXP_J / "D2.json")
    d2_edits = next(d["edits"] for d in base.DRIFTS if d["id"] == "D2")
    r = i0_single(h, "I0c", h.jc, "stale pin", 1, d2, stale=d2_edits)
    r.prediction = (
        "stale pin (the provider clone's HEAD is the D2 commit), --candidate HEAD: contradiction, "
        "current_label contradiction, findings = `check --static-only` = committed D2, exit 1"
    )
    return r


# ---- I1, I2: the D series ------------------------------------------------------------------

J_EXPECT: dict[str, tuple[str, int, bool]] = {
    "D1": ("contradiction", 1, True),
    "D2": ("contradiction", 1, True),
    "D3": ("contradiction", 1, True),
    "D4": ("contradiction", 1, True),
    "D5": ("contradiction", 1, True),
    "D6": ("unaffected", 0, True),
    "D7": ("unaffected", 0, False),
    "D8": ("regression", 0, True),
    "D9": ("regression", 0, True),
    "D10": ("unaffected", 0, False),
}
S_EXPECT: dict[str, tuple[str, int, bool]] = {
    **{f"D{i}": ("contradiction", 1, True) for i in range(1, 8)},
    "D8": ("unaffected", 0, True),
    "D9": ("unaffected", 0, False),
    "D10": ("regression", 0, True),
    "D11": ("regression", 0, True),
    "D12": ("unaffected", 0, False),
    "D13": ("regression", 0, True),
}


def series(
    h: H,
    pid: str,
    ctx: Any,
    drifts: list[dict[str, Any]],
    expect: dict[str, tuple[str, int, bool]],
    exp_dir: Path,
    gaps_n: int,
    from_check: bool,
) -> Result:
    clone = ep.shared(ctx)
    m = ctx.workspace(pid, clone)
    watch = [m.parent.parent, clone]
    refs: dict[str, str] = {}
    for d in drifts:
        _c, refs[d["id"]] = h.ref(ctx, clone, d["id"].lower(), d["edits"])
    comm = {
        d["id"]: committed(exp_dir / f"{d['id']}.json", *(["check"] if from_check else []))
        for d in drifts
    }

    def one(_: int) -> dict[str, Any]:
        return {
            d["id"]: h.both(ctx, m, ctx.provider, d["id"].lower(), probe=pid, watch=watch)
            for d in drifts
        }

    runs, ident = ctx.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    per: dict[str, Any] = {}
    n_lab = n_row = 0
    for d in drifts:
        did = d["id"]
        rec = r0[did]
        row, link = row0(rec), comm[did]
        lab, ex_rc, hashed = expect[did]
        want_gaps = 0 if (did == "D7" and pid == "I2") else gaps_n
        ok_lab = row["label"] == lab and rec["rc"] == ex_rc
        ok_row = same_as(row, link)
        n_lab += ok_lab
        n_row += ok_row
        sub = {
            "label_and_exit": ok_lab,
            "row_equals_committed": ok_row,
            "current_label_unaffected": row["current_label"] == "unaffected",
            "version_changed_true": row["version_changed"] is True,
            "gaps": len(row["gaps"]) == want_gaps,
            "spec_hash_changed": row["spec_hash_changed"] is hashed,
            "counts": row["counts"]
            == {
                "broken": sum(f["class"] == "BROKEN" for f in row["findings"]),
                "changed_relied": sum(
                    f["class"] == "CHANGED" and f["relied"] for f in row["findings"]
                ),
                "changed_unrelied": sum(
                    f["class"] == "CHANGED" and not f["relied"] for f in row["findings"]
                ),
                "gaps": len(row["gaps"]),
            },
            "resolved_is_the_fetched_commit": rec["doc"]["candidate"]["resolved"] == refs[did],
            "text_ok": text_ok(rec),
            "exit_in_json": rec["doc"]["exit_code"] == rec["rc"],
        }
        for k, v in sub.items():
            checks[f"{did}_{k}"] = v
        per[did] = {"label": row["label"], "exit": rec["rc"], "ok": all(sub.values())}
        parts.append(f"{did} {row['label']}/{rec['rc']}{'' if all(sub.values()) else ' !'}")
    checks["labels_and_exits_all"] = n_lab == len(drifts)
    checks["rows_equal_committed_all"] = n_row == len(drifts)
    n = len(drifts)
    obs = f"{n_lab}/{n} labels+exits, {n_row}/{n} rows = committed; " + " ".join(parts)
    return mk(pid, "", obs, runs, ident, checks, {"per_drift": per, "refs": refs})


def i1(h: H) -> Result:
    drifts = [d for d in base.DRIFTS]
    r = series(h, "I1", h.jc, drifts, J_EXPECT, EXP_J, 1, False)
    r.prediction = (
        "job-scheduler D1-D10 (ci-bot): D1-D5 contradiction exit 1, D6 unaffected, D7 unaffected "
        "(miss), D8 regression, D9 regression, D10 unaffected (miss); 5/2/3; 10/10 rows = "
        "committed Dn; current_label unaffected, version_changed true, 1 gap"
    )
    return r


def i2(h: H) -> Result:
    drifts = [d for d in streak.DRIFTS]
    r = series(h, "I2", h.sc, drifts, S_EXPECT, EXP_S, 2, True)
    r.prediction = (
        "Streak D1-D13 (streak-widget): D1-D7 contradiction exit 1, D8 unaffected, D9 unaffected "
        "(miss), D10 regression, D11 regression, D12 unaffected (miss), D13 regression; 7/3/3; "
        "13/13 rows = committed Dn; current_label unaffected, version_changed true, 2 gaps "
        "(D7: 0)"
    )
    return r


# ---- I3: controls and the exclusion --------------------------------------------------------


def i3(h: H) -> Result:
    pred = (
        "12 controls unaffected, exit 0, rows = committed: J-C1..C3 0 findings 1 gap, J-C4 exactly "
        "1 unrelied CHANGED; S-C1..C3, C5, C6 0 findings 2 gaps (C2, C3, C5, C6 hash unchanged), "
        "S-C4 1 unrelied CHANGED; I3x (a) re-pinned under the exclusion: 0 findings 2 gaps; (b) "
        "force-pinned with a PUT /test/clock import: contradiction, 1 BROKEN endpoint_missing "
        "EXCLUDED_DETAIL, 2 gaps, exit 1"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm = jc.workspace("I3j", jp)
    sm = sc.workspace("I3s", sp)
    j_edits: dict[str, list[Any] | None] = {
        "C1": None,
        "C2": [base.C2_EDIT],
        "C3": None,
        "C4": [base.C4_EDIT],
    }
    s_ctrl: dict[str, Any] = {
        "C1": None,
        "C2": [streak.C2_EDIT],
        "C3": [streak.C3_EDIT],
        "C4": [streak.C4_EDIT],
        "C5": streak.C5_EDITS,
        "C6": [streak.C6_EDIT],
    }
    cases: list[dict[str, Any]] = []
    # job-scheduler: C1 is the base sha itself; C3 is the slice swap
    for cid in ("C1", "C2", "C3", "C4"):
        name = f"jc{cid[1]}"
        if cid == "C1":
            cand = "ef17cf8"
        elif cid == "C3":

            def swap(c: Path) -> None:
                base.c3_edit(c)

            h.ref(jc, jp, name, None, setup=swap)
            cand = name
        else:
            h.ref(jc, jp, name, j_edits[cid])
            cand = name
        cases.append(
            {
                "id": f"J-{cid}",
                "ctx": jc,
                "m": jm,
                "provider": "scheduler",
                "cand": cand,
                "watch": [jm.parent.parent, jp],
                "gaps": 1,
                "link": committed(EXP_J / f"{cid}.json"),
                "findings": 1 if cid == "C4" else 0,
                "hash_same": cid != "C4",
            }
        )
    for cid, edits in s_ctrl.items():
        if cid == "C1":
            cand = "a0db033"
        else:
            name = f"sc{cid[1]}"
            h.ref(sc, sp, name, edits)
            cand = name
        cases.append(
            {
                "id": f"S-{cid}",
                "ctx": sc,
                "m": sm,
                "provider": "streak",
                "cand": cand,
                "watch": [sm.parent.parent, sp],
                "gaps": 2,
                "link": committed(EXP_S / f"{cid}.json"),
                "findings": 1 if cid == "C4" else 0,
                "hash_same": cid != "C4",
            }
        )
    # I3x: Streak under the exclusion, candidate = S-C1 (the base sha)
    xa = sc.workspace("I3xa", sp, lock=None, variant="a", exclude=HARNESS)
    xb = sc.workspace("I3xb", sp, lock=None, variant="b", exclude=HARNESS)
    imp = xb.parent.parent / streak.CONSUMER / IMPORTS
    imp.write_text(imp.read_text() + CLOCK_IMPORT)
    xa_lock, xb_lock = sc.lock_path(xa), sc.lock_path(xb)

    def run_case(c: dict[str, Any]) -> dict[str, Any]:
        return h.both(c["ctx"], c["m"], c["provider"], c["cand"], probe="I3", watch=c["watch"])

    def one(_: int) -> dict[str, Any]:
        out = {c["id"]: run_case(c) for c in cases}
        xa_lock.unlink(missing_ok=True)
        xb_lock.unlink(missing_ok=True)
        pa = canon_call(sc.pin(xa))
        pb1 = canon_call(sc.pin(xb))
        pb2 = canon_call(sc.pin(xb, "--force"))
        w = [xa.parent.parent, sp, xb.parent.parent]
        out["I3x-a"] = h.both(sc, xa, "streak", "a0db033", probe="I3", watch=w)
        out["I3x-b"] = h.both(sc, xb, "streak", "a0db033", probe="I3", watch=w)
        out["pins"] = {"a": pa["rc"], "b_refused": pb1["rc"], "b_force": pb2["rc"]}
        return out

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    x_a_link = committed(EXP_X / "X2.json", "check_after_pin")
    x_b_link = committed(EXP_X / "X3.json", "r6_check")
    for c in cases:
        rec = r0[c["id"]]
        row = row0(rec)
        ok = (
            rec["rc"] == 0
            and row["label"] == "unaffected"
            and len(row["findings"]) == c["findings"]
            and len(row["gaps"]) == c["gaps"]
            and same_as(row, c["link"])
            and row["spec_hash_changed"] is (not c["hash_same"])
            and row["spec_hash_changed"] == c["link"]["spec_hash_changed"]
            and (
                c["findings"] == 0
                or (
                    not row["findings"][0].get("relied", True)
                    and row["findings"][0]["class"] == "CHANGED"
                )
            )
            and text_ok(rec)
        )
        checks[c["id"]] = ok
        parts.append(f"{c['id']} {row['label']}/{rec['rc']}/{len(row['findings'])}f")
    a, b = r0["I3x-a"], r0["I3x-b"]
    ra, rb = row0(a), row0(b)
    checks["I3x-a"] = (
        a["rc"] == 0
        and ra["label"] == "unaffected"
        and ra["findings"] == []
        and len(ra["gaps"]) == 2
        and same_as(ra, x_a_link)
        and r0["pins"]["a"] == 0
    )
    checks["I3x-b"] = (
        b["rc"] == 1
        and rb["label"] == "contradiction"
        and [(f["class"], f["kind"], f["endpoint"], f["detail"]) for f in rb["findings"]]
        == [("BROKEN", "endpoint_missing", "PUT /test/clock", EX)]
        and len(rb["gaps"]) == 2
        and same_as(rb, x_b_link)
        and r0["pins"] == {"a": 0, "b_refused": 1, "b_force": 0}
    )
    parts.append(f"I3x-a {ra['label']}/{a['rc']}/{len(ra['findings'])}f/{len(ra['gaps'])}g")
    parts.append(f"I3x-b {rb['label']}/{b['rc']}/{len(rb['findings'])}f/{len(rb['gaps'])}g")
    checks["all_12"] = sum(1 for k, v in checks.items() if k != "all_12" and v) == 12
    return mk("I3", pred, "; ".join(parts), runs, ident, checks)


# ---- I4: several consumers -----------------------------------------------------------------


def multi_a(
    h: H, name: str, clone: Path, *, ci2: str | None = "static", lock2: bool = True
) -> Path:
    """Workspace A: scheduler, ci-bot, ci-bot-2 (the login and applications tables of ci-bot)."""
    jc = h.jc
    d = jc.work / name
    for c in ("ci-bot", "ci-bot-2"):
        shutil.rmtree(d / c, ignore_errors=True)
    shutil.copytree(base.CI_BOT, d / "ci-bot", ignore=shutil.ignore_patterns("__pycache__", LOCK))
    (d / "ci-bot" / LOCK).write_bytes(jc.clean_lock())
    (d / "ci-bot-2").mkdir()
    (d / "ci-bot-2" / IMPORTS).write_text(
        "# ci-bot-2: ci-bot's login and applications tables, verbatim\n\n"
        + import_tables(base.CI_BOT / IMPORTS, [base.LOGIN, "GET /applications"])
    )
    projects = [
        {"name": "scheduler", "path": str(clone), "role": "provider", "spec": "spec.tickets:t5"},
        {"name": "ci-bot", "path": str(d / "ci-bot"), "role": "consumer"},
        {"name": "ci-bot-2", "path": str(d / "ci-bot-2"), "role": "consumer"},
    ]
    links = [
        {"from": "ci-bot", "to": "scheduler", "contract": "static"},
        {"from": "ci-bot-2", "to": "scheduler", "contract": ci2 or ""},
    ]
    m = write_manifest(d / "m" / MANIFEST, projects, links)
    if lock2:
        # pinned on the clean clone: a plain pin against the clean provider (ci-bot-2's own lock)
        clean = ep.shared(jc)
        pm = write_manifest(
            d / "pin" / MANIFEST,
            [{**projects[0], "path": str(clean)}, projects[2]],
            [{"from": "ci-bot-2", "to": "scheduler", "contract": "static"}],
        )
        c = jc.contracts(pm, "pin", "ci-bot-2", "--json")
        assert c.rc == 0, c.out + c.err
    return m


def multi_b(h: H, name: str, sclone: Path, tclone: Path) -> Path:
    """Workspace B: scheduler, streak, multi (ci-bot's 7 and streak-widget's 7 tables)."""
    jc = h.jc
    d = jc.work / name
    shutil.rmtree(d / "multi", ignore_errors=True)
    (d / "multi").mkdir(parents=True)
    (d / "multi" / IMPORTS).write_text(
        all_tables(base.CI_BOT / IMPORTS) + all_tables(streak.WIDGET / IMPORTS)
    )
    projects = [
        {"name": "scheduler", "path": str(sclone), "role": "provider", "spec": "spec.tickets:t5"},
        {"name": "streak", "path": str(tclone), "role": "provider"},
        {"name": "multi", "path": str(d / "multi"), "role": "consumer"},
    ]
    links = [
        {"from": "multi", "to": "scheduler", "contract": "static"},
        {"from": "multi", "to": "streak", "contract": "static"},
    ]
    m = write_manifest(d / "m" / MANIFEST, projects, links)
    c = jc.contracts(m, "pin", "multi", "--json")
    assert c.rc == 0, c.out + c.err
    return m


def i4(h: H) -> Result:
    pred = (
        "a D1: ci-bot contradiction, ci-bot-2 unaffected with exactly 1 unrelied CHANGED "
        "endpoint_removed (GET .../jobs/{}/runs), 1 gap, exit 1; b D3: ci-bot contradiction, "
        "ci-bot-2 unaffected with exactly 2 unrelied CHANGED @ cancel, exit 1; c D4: both "
        "contradiction = committed D4, exit 1; d D8: both regression = committed D8, exit 0; "
        "e ci-bot-2 live: live not_run/static-only; f ci-bot-2 without contract: not listed, "
        "checked 1; g ci-bot-2 lock deleted: no_pin, ci-bot contradiction, exit 2; h multi: "
        "scheduler+D2 = committed D2 (1 gap, exit 1), streak+Streak D13 = committed S-D13 (2 gaps, "
        "exit 0); i provider ci-bot: exit 0 checked 0 no_consumers resolved null; nobody: exit 2 "
        "unknown_project; rows ordered ci-bot, ci-bot-2"
    )
    jc, sc = h.jc, h.sc
    jp = ep.shared(jc)
    drifts = {d["id"]: d for d in base.DRIFTS}
    for did in ("D1", "D3", "D4", "D8"):
        h.ref(jc, jp, did.lower(), drifts[did]["edits"])
    # D2 for row h is fetched by I1 into the same shared clone; fetch again here (idempotent)
    h.ref(jc, jp, "d2", drifts["D2"]["edits"])
    sp = ep.shared(sc)
    h.ref(sc, sp, "d13", next(d for d in streak.DRIFTS if d["id"] == "D13")["edits"])
    ma = multi_a(h, "I4a", jp)
    me = multi_a(h, "I4e", jp, ci2="live")
    mf = multi_a(h, "I4f", jp, ci2=None)
    mg = multi_a(h, "I4g", jp)
    (mg.parent.parent / "ci-bot-2" / LOCK).unlink()
    mb = multi_b(h, "I4h", jp, sp)
    gold_entry = json.loads(jc.golden)["providers"]["scheduler"]
    pinned_entry = json.loads((ma.parent.parent / "ci-bot-2" / LOCK).read_text())["providers"][
        "scheduler"
    ]
    wa = [ma.parent.parent, jp]
    d_links = {k: committed(EXP_J / f"{k}.json") for k in ("D1", "D2", "D3", "D4", "D8")}
    s_d13 = committed(EXP_S / "D13.json", "check")

    def one(_: int) -> dict[str, Any]:
        r: dict[str, Any] = {}
        r["a"] = h.both(jc, ma, "scheduler", "d1", probe="I4", watch=wa)
        r["b"] = h.both(jc, ma, "scheduler", "d3", probe="I4", watch=wa)
        r["c"] = h.both(jc, ma, "scheduler", "d4", probe="I4", watch=wa)
        r["d"] = h.both(jc, ma, "scheduler", "d8", probe="I4", watch=wa)
        r["e"] = h.both(jc, me, "scheduler", "d1", probe="I4", watch=[me.parent.parent, jp])
        r["f"] = h.both(jc, mf, "scheduler", "d1", probe="I4", watch=[mf.parent.parent, jp])
        r["g"] = h.both(jc, mg, "scheduler", "d1", probe="I4", watch=[mg.parent.parent, jp])
        wb = [mb.parent.parent, jp, sp]
        r["h_sched"] = h.both(jc, mb, "scheduler", "d2", probe="I4", watch=wb)
        r["h_streak"] = h.both(jc, mb, "streak", "d13", probe="I4", watch=wb)
        r["i_ci_bot"] = h.both(jc, ma, "ci-bot", "HEAD", probe="I4", watch=wa, expect=0)
        r["i_nobody"] = h.both(jc, ma, "nobody", "HEAD", probe="I4", watch=wa, expect=0)
        return r

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    cb = {k: rows_by(v) for k, v in r0.items() if "consumers" in v["doc"]}
    a, b, c, d = cb["a"], cb["b"], cb["c"], cb["d"]
    run_order = all(
        [x["consumer"] for x in v["doc"]["consumers"]]
        == sorted(x["consumer"] for x in v["doc"]["consumers"])
        for v in r0.values()
    )
    cancel = base.CANCEL
    checks: dict[str, Any] = {
        "pins_identical_to_golden_entry": pinned_entry == gold_entry,
        "a_ci_bot": a["ci-bot"]["label"] == "contradiction" and same_as(a["ci-bot"], d_links["D1"]),
        "a_ci_bot_2": a["ci-bot-2"]["label"] == "unaffected"
        and [(f["class"], f["kind"], f["endpoint"], f["relied"]) for f in a["ci-bot-2"]["findings"]]
        == [("CHANGED", "endpoint_removed", base.LIST_RUNS, False)]
        and len(a["ci-bot-2"]["gaps"]) == 1,
        "a_exit_1": r0["a"]["rc"] == 1,
        "b_ci_bot": b["ci-bot"]["label"] == "contradiction" and same_as(b["ci-bot"], d_links["D3"]),
        "b_ci_bot_2": b["ci-bot-2"]["label"] == "unaffected"
        and sorted(
            (f["class"], f["kind"], f["endpoint"], f["item"], f["relied"])
            for f in b["ci-bot-2"]["findings"]
        )
        == [
            ("CHANGED", "field_added", cancel, "state", False),
            ("CHANGED", "field_removed", cancel, "status", False),
        ],
        "b_exit_1": r0["b"]["rc"] == 1,
        "c_both_contradiction_equal_D4": all(
            c[k]["label"] == "contradiction" and same_as(c[k], d_links["D4"])
            for k in ("ci-bot", "ci-bot-2")
        )
        and r0["c"]["rc"] == 1,
        "d_both_regression_equal_D8": all(
            d[k]["label"] == "regression" and same_as(d[k], d_links["D8"])
            for k in ("ci-bot", "ci-bot-2")
        )
        and r0["d"]["rc"] == 0,
        "e_live_not_run_static_only": cb["e"]["ci-bot-2"]["live"]
        == {"status": "not_run", "reason": "static-only"}
        and cb["e"]["ci-bot-2"]["contract"] == "live"
        and cb["e"]["ci-bot"]["live"] is None
        and cb["e"]["ci-bot-2"]["label"] == "unaffected",
        "f_not_listed": list(cb["f"]) == ["ci-bot"] and r0["f"]["doc"]["checked"] == 1,
        "g_no_pin_exit_2": cb["g"]["ci-bot-2"]["label"] is None
        and cb["g"]["ci-bot-2"]["error"]["code"] == "no_pin"
        and cb["g"]["ci-bot"]["label"] == "contradiction"
        and r0["g"]["rc"] == 2,
        "h_scheduler": r0["h_sched"]["rc"] == 1
        and cb["h_sched"]["multi"]["label"] == "contradiction"
        and same_as(cb["h_sched"]["multi"], d_links["D2"])
        and len(cb["h_sched"]["multi"]["gaps"]) == 1
        and not any(
            f["endpoint"].startswith("GET /v1") or "/v1/" in f["endpoint"]
            for f in cb["h_sched"]["multi"]["findings"]
        ),
        "h_streak": r0["h_streak"]["rc"] == 0
        and cb["h_streak"]["multi"]["label"] == "regression"
        and same_as(cb["h_streak"]["multi"], s_d13)
        and len(cb["h_streak"]["multi"]["gaps"]) == 2,
        "i_ci_bot": r0["i_ci_bot"]["rc"] == 0
        and r0["i_ci_bot"]["doc"]["checked"] == 0
        and r0["i_ci_bot"]["doc"]["candidate"]["resolved"] is None
        and [p["code"] for p in r0["i_ci_bot"]["doc"]["problems"]] == ["no_consumers"],
        "i_nobody": r0["i_nobody"]["rc"] == 2
        and [p["code"] for p in r0["i_nobody"]["doc"]["problems"]] == ["unknown_project"],
        "rows_ordered_everywhere": run_order,
        "texts_ok": all(
            v["text_rc"] == v["rc"] and v["text"].startswith("impact of ") for v in r0.values()
        ),
    }
    obs = (
        f"a {r0['a']['rc']} [{short(a['ci-bot'])} | {short(a['ci-bot-2'])}]; b {r0['b']['rc']} "
        f"[{short(b['ci-bot'])} | {short(b['ci-bot-2'])}]; c {r0['c']['rc']} "
        f"[{short(c['ci-bot'])} | {short(c['ci-bot-2'])}]; d {r0['d']['rc']} "
        f"[{short(d['ci-bot'])} | {short(d['ci-bot-2'])}]; e live "
        f"{cb['e']['ci-bot-2']['live']}; f checked {r0['f']['doc']['checked']}; g "
        f"{r0['g']['rc']} {cb['g']['ci-bot-2']['error']['code']}; h {r0['h_sched']['rc']} "
        f"{short(cb['h_sched']['multi'])} / {r0['h_streak']['rc']} "
        f"{short(cb['h_streak']['multi'])}; i {r0['i_ci_bot']['rc']} "
        f"{[p['code'] for p in r0['i_ci_bot']['doc']['problems']]} / {r0['i_nobody']['rc']} "
        f"{[p['code'] for p in r0['i_nobody']['doc']['problems']]}"
    )
    return mk("I4", pred, obs, runs, ident, checks)


# ---- I5: candidate forms -------------------------------------------------------------------


def prov_state(ctx: Any, clone: Path, skip: list[Path]) -> dict[str, Any]:
    g = ctx.git
    files, _m = snapshot([clone], skip)
    return {
        "status": g(clone, "--no-optional-locks", "status", "--porcelain=v2", "--ignored"),
        "head": g(clone, "rev-parse", "HEAD"),
        "refs": g(clone, "for-each-ref"),
        "reflog": g(clone, "reflog", "--all"),
        "files": files,
    }


def i5(h: H) -> Result:
    pred = (
        "(a) `d2` and the D2 sha: JSON equal but candidate.input; (b) the D2 clone's directory: "
        "kind path, findings = (a), version.git_ref = D2 sha; (c) a directory with C2's edit "
        "uncommitted: unaffected, version.dirty true; (d) no-such-ref: exit 2 unknown_candidate "
        "naming it, 0 export children; (e) --candidate=-x: exit 2 unknown_candidate, git never "
        "gets -x; (f) a ref whose spec ends in raise RuntimeError('not loaded'): exit 2 "
        "candidate_failed with 'not loaded', no temp path; (g) a directory d2 in the cwd: kind "
        "path, unaffected; (h) the provider clone untouched over (a)-(g) (status v2 --ignored, "
        "HEAD, refs, reflog, sha256 of every file)"
    )
    jc = h.jc
    prov = jc.clone("I5-provider")
    d2_edits = next(d["edits"] for d in base.DRIFTS if d["id"] == "D2")
    d2_clone, d2_sha = h.ref(jc, prov, "d2", d2_edits)

    def raise_not_loaded(c: Path) -> None:
        p = c / base.T
        p.write_text(p.read_text().rstrip("\n") + '\nraise RuntimeError("not loaded")\n')

    _c, bad_sha = h.ref(jc, prov, "bad", None, setup=raise_not_loaded)
    dirty = jc.clone("I5-dirty", [base.C2_EDIT], commit=False)
    cwd_dir = jc.work / "I5-cwd"
    shutil.rmtree(cwd_dir, ignore_errors=True)
    cwd_dir.mkdir(parents=True)
    shutil.copytree(ep.shared(jc), cwd_dir / "d2", symlinks=True)
    m = jc.workspace("I5", prov)
    wd = m.parent.parent
    skip = [h.ns.out.resolve()]
    watch = [wd, prov]
    d2_link = committed(EXP_J / "D2.json")

    def one(_: int) -> dict[str, Any]:
        r: dict[str, Any] = {"before": prov_state(jc, prov, skip)}
        r["a_name"] = h.both(jc, m, "scheduler", "d2", probe="I5", watch=watch)
        r["a_sha"] = h.both(jc, m, "scheduler", d2_sha, probe="I5", watch=watch)
        r["b"] = h.both(jc, m, "scheduler", str(d2_clone), probe="I5", watch=[*watch, d2_clone])
        r["c"] = h.both(jc, m, "scheduler", str(dirty), probe="I5", watch=[*watch, dirty])
        r["d"] = h.both(jc, m, "scheduler", "no-such-ref", probe="I5", watch=watch, expect=0)
        r["e"] = h.both(jc, m, "scheduler", "-x", probe="I5", watch=watch, expect=0)
        r["f"] = h.both(jc, m, "scheduler", "bad", probe="I5", watch=watch, expect=1)
        r["g"] = h.both(jc, m, "scheduler", "d2", probe="I5", watch=[*watch, cwd_dir], cwd=cwd_dir)
        r["after"] = prov_state(jc, prov, skip)
        return r

    runs, ident = jc.repeat(one)
    r0 = runs[0]
    trimmed = [
        {
            **r,
            "before": {k: v for k, v in r["before"].items() if k != "files"},
            "after": {k: v for k, v in r["after"].items() if k != "files"},
        }
        for r in runs
    ]

    def doc(k: str) -> dict[str, Any]:
        return r0[k]["doc"]  # type: ignore[no-any-return]

    def problem(k: str) -> dict[str, Any]:
        return doc(k)["problems"][-1]  # type: ignore[no-any-return]

    a1, a2 = dict(doc("a_name")), dict(doc("a_sha"))
    a1["candidate"] = {**a1["candidate"], "input": ""}
    a2["candidate"] = {**a2["candidate"], "input": ""}
    all_text = all(
        r0[k]["text_rc"] == r0[k]["rc"] and r0[k]["text"].startswith("impact of ")
        for k in ("a_name", "a_sha", "b", "c", "d", "e", "f", "g")
    )
    tmp_names = [str(jc.tmp), str(jc.tmp.resolve())]
    checks: dict[str, Any] = {
        "a_json_equal_but_input": a1 == a2
        and r0["a_name"]["rc"] == r0["a_sha"]["rc"] == 1
        and doc("a_name")["candidate"]["resolved"] == d2_sha,
        "a_row_equals_committed_D2": same_as(row0(r0["a_name"]), d2_link),
        "b_path_equal_a": doc("b")["candidate"]["kind"] == "path"
        and doc("b")["candidate"]["resolved"] == str(d2_clone.resolve())
        and row0(r0["b"])["findings"] == row0(r0["a_name"])["findings"]
        and doc("b")["candidate"]["version"]["git_ref"] == d2_sha
        and r0["b"]["rc"] == 1,
        "c_unaffected_dirty": row0(r0["c"])["label"] == "unaffected"
        and doc("c")["candidate"]["version"]["dirty"] is True
        and doc("c")["candidate"]["kind"] == "path"
        and r0["c"]["rc"] == 0,
        "d_unknown_candidate": r0["d"]["rc"] == 2
        and problem("d")["code"] == "unknown_candidate"
        and "no-such-ref" in problem("d")["message"]
        and doc("d")["consumers"] == [],
        "e_unknown_candidate": r0["e"]["rc"] == 2 and problem("e")["code"] == "unknown_candidate",
        "f_candidate_failed": r0["f"]["rc"] == 2
        and problem("f")["code"] == "candidate_failed"
        and "not loaded" in problem("f")["message"]
        and not any(t in json.dumps(doc("f")) or t in r0["f"]["text"] for t in tmp_names)
        and "lattice-impact-" not in json.dumps(doc("f")),
        "g_dir_shadows": doc("g")["candidate"]["kind"] == "path"
        and doc("g")["candidate"]["resolved"] == str((cwd_dir / "d2").resolve())
        and row0(r0["g"])["label"] == "unaffected"
        and r0["g"]["rc"] == 0,
        "h_provider_untouched": all(r["before"] == r["after"] for r in runs),
        "texts_ok": all_text,
        "bad_sha_resolved_in_f": doc("f")["candidate"]["resolved"] == bad_sha,
    }
    h_diffs = [
        k
        for k in set(r0["before"]["files"]) | set(r0["after"]["files"])
        if r0["before"]["files"].get(k) != r0["after"]["files"].get(k)
    ][:5]
    obs = (
        f"a name/sha exit {r0['a_name']['rc']}/{r0['a_sha']['rc']} {short(row0(r0['a_name']))} "
        f"equal {yn(checks['a_json_equal_but_input'])}; b {doc('b')['candidate']['kind']} "
        f"{short(row0(r0['b']))}; c {short(row0(r0['c']))} dirty "
        f"{doc('c')['candidate']['version']['dirty']}; d {r0['d']['rc']} {problem('d')['code']}; "
        f"e {r0['e']['rc']} {problem('e')['code']}; f {r0['f']['rc']} {problem('f')['code']}; "
        f"g {doc('g')['candidate']['kind']} {short(row0(r0['g']))}; h untouched "
        f"{yn(checks['h_provider_untouched'])} {h_diffs}"
    )
    extra = {"h_before_after_changed_files": h_diffs, "tmp_prefix_checked": tmp_names}
    return mk("I5", pred, obs, trimmed, ident, checks, extra)


# ---- I7: parity ----------------------------------------------------------------------------


def mcp_impact(
    h: H,
    ctx: Any,
    solo: Path,
    manifest: Path,
    provider: str,
    cand: str,
    probe: str,
    watch: list[Path],
) -> dict[str, Any]:
    def msg(i: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
        if params is not None:
            out["params"] = params
        return out

    msgs = [
        msg(
            0,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "probes", "version": "0"},
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        msg(1, "tools/list", {}),
        msg(
            2,
            "tools/call",
            {"name": "contract_impact", "arguments": {"provider": provider, "candidate": cand}},
        ),
    ]
    argv = [
        sys.executable,
        "-m",
        "lattice.mcp",
        "serve",
        "--project",
        str(solo),
        "--workspace",
        str(manifest),
        "--readonly",
    ]
    with h.tracer.around(probe, f"mcp contract_impact {provider} {cand}", ctx, watch):
        c = ctx.run(argv, stdin="\n".join(json.dumps(x) for x in msgs) + "\n", timeout=300)
    resp = {
        x["id"]: x for x in (json.loads(ln) for ln in c.out.splitlines() if ln.strip()) if "id" in x
    }
    res = resp[2]["result"]
    assert not res.get("isError"), res
    tools = {t["name"]: t for t in resp[1]["result"]["tools"]}
    return {
        "doc": strip_elapsed(json.loads(res["content"][0]["text"])),
        "listed": "contract_impact" in tools,
        "readonly_hint": tools["contract_impact"]["annotations"]["readOnlyHint"],
    }


def api_impact(
    h: H,
    ctx: Any,
    solo: Path,
    manifest: Path,
    provider: str,
    cand: str,
    probe: str,
    watch: list[Path],
    mock_port: int,
) -> dict[str, Any]:
    port = base.free_port()
    argv = [
        sys.executable,
        "-m",
        "lattice.studio",
        str(solo),
        "--port",
        str(port),
        "--quiet",
        "--stub-llm",
        "--workspace",
        str(manifest),
    ]
    from urllib.parse import quote

    path = f"/api/contracts/impact?provider={quote(provider)}&candidate={quote(cand)}"
    with base.background(ctx, argv, f"http://127.0.0.1:{port}/api/projects"):
        with h.tracer.around(probe, f"api {path}", ctx, watch):
            doc = base.http_get(f"http://127.0.0.1:{port}{path}")
    mock = base.http_get(f"http://127.0.0.1:{mock_port}{path}")
    return {"doc": strip_elapsed(doc), "mock": strip_elapsed(mock)}


def shape_diff(mock: dict[str, Any], real: dict[str, Any]) -> list[str]:
    def types(d: dict[str, Any]) -> dict[str, str]:
        return {k: type(v).__name__ for k, v in d.items()}

    bad = []
    if set(mock) != set(real) or types(mock) != types(real):
        bad.append("top-level keys/types")
    if set(mock["candidate"]) != set(real["candidate"]):
        bad.append("candidate keys")
    if mock["candidate"].get("version") and set(mock["candidate"]["version"]) != set(
        real["candidate"]["version"]
    ):
        bad.append("candidate.version keys")
    if mock["consumers"] and real["consumers"]:
        m0, r0 = mock["consumers"][0], real["consumers"][0]
        if set(m0) != set(r0) or types(m0) != types(r0):
            bad.append("row keys/types")
        mf = [set(f) for f in m0["findings"]]
        rf = [set(f) for f in r0["findings"]]
        if mf and rf and mf[0] != rf[0]:
            bad.append("finding keys")
    return bad


def i7(h: H) -> Result:
    pred = (
        "on I1-D2 and I4-a the MCP contract_impact and GET /api/contracts/impact equal `contracts "
        "impact --json` (elapsed_s dropped): 4 of 4; the mock has the same keys and types"
    )
    jc = h.jc
    jp = ep.shared(jc)
    drifts = {d["id"]: d for d in base.DRIFTS}
    h.ref(jc, jp, "d2", drifts["D2"]["edits"])
    h.ref(jc, jp, "d1", drifts["D1"]["edits"])
    solo = base.solo_project(jc.work / "I7-solo")
    m_d2 = jc.workspace("I7-d2", jp)
    m_a = multi_a(h, "I7-a", jp)
    targets = {
        "D2": (m_d2, "scheduler", "d2", [m_d2.parent.parent, jp]),
        "I4a": (m_a, "scheduler", "d1", [m_a.parent.parent, jp]),
    }
    mock_port = base.free_port()
    mock_argv = [sys.executable, "-m", "lattice.studio.mock_server", "--port", str(mock_port)]
    with base.background(jc, mock_argv, f"http://127.0.0.1:{mock_port}/api/projects"):

        def one(_: int) -> dict[str, Any]:
            rec: dict[str, Any] = {}
            for name, (m, prov, cand, watch) in targets.items():
                cli = canon_call(h.impact(jc, m, prov, cand, probe="I7", watch=watch))
                mcp = mcp_impact(h, jc, solo, m, prov, cand, "I7", watch)
                api = api_impact(h, jc, solo, m, prov, cand, "I7", watch, mock_port)
                rec[name] = {"cli": cli, "mcp": mcp, "api": api["doc"], "mock": api["mock"]}
            return rec

        runs, ident = jc.repeat(one)
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    for name in targets:
        t = r0[name]
        checks[f"{name}_mcp_equals_cli"] = t["mcp"]["doc"] == t["cli"]["doc"]
        checks[f"{name}_api_equals_cli"] = t["api"] == t["cli"]["doc"]
        checks[f"{name}_mcp_lists_tool_readonly"] = t["mcp"]["listed"] and t["mcp"]["readonly_hint"]
        diff = shape_diff(t["mock"], t["api"])
        checks[f"{name}_mock_same_keys_types"] = diff == []
        parts.append(
            f"{name}: mcp==cli {yn(checks[f'{name}_mcp_equals_cli'])}, api==cli "
            f"{yn(checks[f'{name}_api_equals_cli'])}, mock shape {diff or 'ok'}"
        )
    mock_flags = {n: r0[n]["mock"]["consumers"][0]["label"] for n in targets}
    return mk("I7", pred, "; ".join(parts), runs, ident, checks, {"mock_labels": mock_flags})


# ---- I8: no regression ---------------------------------------------------------------------


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env.pop("IMPACT_PROBE_LOG", None)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("UV_NO_SYNC", "1")
    env.setdefault("UV_PROJECT_ENVIRONMENT", str(Path(sys.prefix)))
    return env


def run_last_line(
    argv: list[str], env: dict[str, str], cwd: Path, dst: Path, name: str
) -> dict[str, Any]:
    import re

    t0 = time.time()
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    tail = [ln for ln in r.stdout.splitlines() if ln.startswith("probes: ")]
    m = re.fullmatch(r"probes: (\d+)/(\d+) pass", tail[-1]) if tail else None
    (dst / f"{name}-stdout.txt").write_text(r.stdout)
    return {
        "rc": r.returncode,
        "passed": int(m.group(1)) if m else None,
        "total": int(m.group(2)) if m else None,
        "last_line": tail[-1] if tail else None,
        "wall_s": round(time.time() - t0, 1),
        "stderr_tail": r.stderr.strip().splitlines()[-3:],
    }


def i8(h: H) -> Result:
    pred = (
        "probes.py --runs 10 35/35, streak_probes.py --allow-code-diff 32/32, exclude_probes.py "
        "12/12; pytest tests/contracts tests/workspace tests/mcp tests/studio 0 failures; "
        "dogfood/studio/check.sh PASS (415 + R95's 7 steps = 422 cases); workspace show|validate|"
        "impact, contracts export, check --json and the pin lock byte-identical to the main "
        "checkout at 67189af"
    )
    ns, jc = h.ns, h.jc
    out = ns.out
    dst = out / "I8-regression"
    dst.mkdir(parents=True, exist_ok=True)
    scratch = ns.scratch.resolve()
    env = clean_env()
    checks: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    # -- old code: lattice/ at 67189af materialised from this repository's object database (post
    # hoc, 2026-10-14: this probe used to compare with whatever the main checkout's HEAD was, which
    # stopped being 67189af once next-phases moved on; a commit is a stable reference, a
    # checkout is not)
    old_root = scratch / "I8-old-code"
    shutil.rmtree(old_root, ignore_errors=True)
    old_root.mkdir(parents=True)
    arch = subprocess.run(
        ["git", "-C", str(REPO), "archive", OLD_HEAD, "lattice"], capture_output=True, check=False
    )
    if arch.returncode == 0:
        subprocess.run(
            ["tar", "-x", "-C", str(old_root)], input=arch.stdout, check=False, capture_output=True
        )
    checks["old_code_materialised_from_67189af"] = (old_root / "lattice" / "workspace.py").is_file()
    extra["old_code"] = {
        "ref": OLD_HEAD,
        "root": "scratch/I8-old-code",
        "archive_rc": arch.returncode,
    }
    # -- byte comparison on the I1 workspace (own manifest and consumer copies)
    clone = ep.shared(jc)
    copies: dict[str, Path] = {}
    for tag in ("new", "old"):
        d = jc.work / f"I8-{tag}"
        shutil.rmtree(d, ignore_errors=True)
        (d / "m").mkdir(parents=True)
        shutil.copytree(
            base.CI_BOT, d / "ci-bot", ignore=shutil.ignore_patterns("__pycache__", LOCK)
        )
        (d / "ci-bot" / LOCK).write_bytes(jc.clean_lock())
        write_manifest(
            d / "m" / MANIFEST,
            [
                {
                    "name": "scheduler",
                    "path": str(clone),
                    "role": "provider",
                    "spec": "spec.tickets:t5",
                },
                {"name": "ci-bot", "path": str(d / "ci-bot"), "role": "consumer"},
            ],
            [{"from": "ci-bot", "to": "scheduler", "contract": "static"}],
        )
        copies[tag] = d / "m" / MANIFEST
    cmds = [
        ["-m", "lattice.workspace", "show"],
        ["-m", "lattice.workspace", "show", "--json"],
        ["-m", "lattice.workspace", "validate"],
        ["-m", "lattice.workspace", "validate", "--json"],
        ["-m", "lattice.workspace", "impact", "scheduler"],
        ["-m", "lattice.workspace", "impact", "scheduler", "--json"],
        ["-m", "lattice.contracts", "export", "scheduler"],
        ["-m", "lattice.contracts", "check", "ci-bot", "--json"],
        ["-m", "lattice.contracts", "check", "ci-bot"],
    ]
    cmp_rows = []
    for argv in cmds:
        res = {}
        for tag, root in (("new", REPO), ("old", old_root)):
            e = dict(env, PYTHONPATH=str(root))
            r = subprocess.run(
                [sys.executable, *argv, "--workspace", str(copies["new"])],
                cwd=jc.work,
                env=e,
                capture_output=True,
                text=True,
                check=False,
            )
            o = r.stdout
            if "--json" in argv and "check" in argv:
                o = json.dumps(strip_elapsed(json.loads(o)), sort_keys=True)
            res[tag] = (r.returncode, o, r.stderr)
        cmp_rows.append(
            {"argv": " ".join(argv[1:]), "equal": res["new"] == res["old"], "rc": res["new"][0]}
        )
    pins = {}
    for tag, root in (("new", REPO), ("old", old_root)):
        d = copies[tag].parent.parent
        (d / "ci-bot" / LOCK).unlink(missing_ok=True)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "lattice.contracts",
                "pin",
                "ci-bot",
                "--json",
                "--workspace",
                str(copies[tag]),
            ],
            cwd=jc.work,
            env=dict(env, PYTHONPATH=str(root)),
            capture_output=True,
            text=True,
            check=False,
        )
        pins[tag] = (r.returncode, (d / "ci-bot" / LOCK).read_bytes())
    cmp_rows.append(
        {
            "argv": "contracts pin ci-bot (lock bytes)",
            "equal": pins["new"] == pins["old"],
            "rc": pins["new"][0],
        }
    )
    checks["bytes_identical_to_67189af"] = all(r["equal"] for r in cmp_rows)
    checks["pin_lock_equals_golden"] = pins["new"][1] == jc.golden
    extra["byte_comparison"] = cmp_rows
    parts = [f"byte-identical {sum(r['equal'] for r in cmp_rows)}/{len(cmp_rows)}"]
    # -- the runners, pytest and the studio gate
    if ns.skip_regression:
        checks["regression_run"] = False
        parts.append("runners/pytest/check.sh SKIPPED (--skip-regression)")
    else:
        py = sys.executable
        jobs = {
            "probes": ([py, str(HERE / "probes.py"), "--runs", str(ns.runs)], 35),
            "streak_probes": (
                [py, str(HERE / "streak_probes.py"), "--runs", str(ns.runs), "--allow-code-diff"],
                32,
            ),
            "exclude_probes": ([py, str(HERE / "exclude_probes.py"), "--runs", str(ns.runs)], 12),
        }
        for name, (argv, want) in jobs.items():
            res = run_last_line(
                [
                    *argv,
                    "--scratch",
                    str(scratch / f"i8-{name}"),
                    "--out",
                    str(scratch / f"i8-{name}-data"),
                ],
                env,
                REPO,
                dst,
                name,
            )
            sdir = scratch / f"i8-{name}-data" / "summary.json"
            if sdir.exists():
                shutil.copy(sdir, dst / f"{name}-summary.json")
            extra[name] = res
            checks[f"{name}_{want}_of_{want}"] = (res["passed"], res["total"], res["rc"]) == (
                want,
                want,
                0,
            )
            parts.append(f"{name} {res['passed']}/{res['total']}")
        t0 = time.time()
        r = subprocess.run(
            [
                py,
                "-m",
                "pytest",
                "tests/contracts",
                "tests/workspace",
                "tests/mcp",
                "tests/studio",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (dst / "pytest-stdout.txt").write_text(r.stdout[-6000:])
        last = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln][-1:]
        extra["pytest"] = {
            "rc": r.returncode,
            "last_line": last,
            "wall_s": round(time.time() - t0, 1),
        }
        checks["pytest_0_failures"] = r.returncode == 0
        parts.append(f"pytest {last[0] if last else r.returncode}")
        t0 = time.time()
        r = subprocess.run(
            ["./check.sh"],
            cwd=REPO / "dogfood" / "studio",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (dst / "studio-check-stdout.txt").write_text(r.stdout[:3000])
        first = r.stdout.splitlines()[0] if r.stdout else ""
        extra["studio_check"] = {
            "rc": r.returncode,
            "first_line": first,
            "wall_s": round(time.time() - t0, 1),
        }
        # post hoc 2026-10-14: the dogfood spec has grown since (R96, R97, ...); the claim is "PASS,
        # 0 findings, at least R95's 422 cases", not the literal number
        m_cases = re.search(r"(\d+) cases run", first)
        checks["studio_check_pass_422_cases"] = (
            r.returncode == 0
            and first.startswith("PASS: 0 findings")
            and m_cases is not None
            and int(m_cases.group(1)) >= 422
        )
        parts.append(f"check.sh {first}")
    return mk("I8", pred, "; ".join(parts), [extra["byte_comparison"]], True, checks, extra)


# ---- T: cost ---------------------------------------------------------------------------------


def t_probe(h: H) -> Result:
    pred = (
        "median wall per preview over 10 runs <= 10 s (predicted <= 2 s job-scheduler, "
        "<= 3 s Streak)"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    d2j = next(d for d in base.DRIFTS if d["id"] == "D2")
    d2s = next(d for d in streak.DRIFTS if d["id"] == "D2")
    h.ref(jc, jp, "d2", d2j["edits"])
    h.ref(sc, sp, "d2", d2s["edits"])
    jm, sm = jc.workspace("T-j", jp), sc.workspace("T-s", sp)
    jw, sw, jel, sel = [], [], [], []
    for _ in range(h.ns.runs):
        c = h.impact(jc, jm, "scheduler", "d2", probe="T", watch=[jm.parent.parent, jp])
        jw.append(c.wall)
        jel.append(c.json()["elapsed_s"])
        c = h.impact(sc, sm, "streak", "d2", probe="T", watch=[sm.parent.parent, sp])
        sw.append(c.wall)
        sel.append(c.json()["elapsed_s"])
    mj, ms = statistics.median(jw), statistics.median(sw)
    checks = {
        "job_scheduler_median_le_10s": mj <= 10,
        "streak_median_le_10s": ms <= 10,
        "job_scheduler_prediction_le_2s": mj <= 2,
        "streak_prediction_le_3s": ms <= 3,
    }
    obs = (
        f"job-scheduler median {mj:.2f} s wall ({statistics.median(jel):.2f} s elapsed_s), Streak "
        f"median {ms:.2f} s wall ({statistics.median(sel):.2f} s elapsed_s)"
    )
    extra = {
        "job_scheduler_wall_s": [round(x, 3) for x in jw],
        "streak_wall_s": [round(x, 3) for x in sw],
        "job_scheduler_elapsed_s": jel,
        "streak_elapsed_s": sel,
        "note": "timing is not deterministic; 'identical' n/a",
    }
    passed = checks["job_scheduler_median_le_10s"] and checks["streak_median_le_10s"]
    return Result(
        "T", pred, obs, passed, [{"job_scheduler": jw, "streak": sw}], True, checks, extra
    )


# ---- I6: safety (aggregated) ----------------------------------------------------------------


def i6(h: H, repos: dict[str, Any], marker_checks: dict[str, Any]) -> Result:
    pred = (
        "over every traced call of I0-I5, I7: (1) sha256 of the workspace, consumers (locks), "
        "provider clones and the lattice-impact worktree unchanged and 0 files newer; (2) TMPDIR "
        "empty after each call; (3) 0 socket connects, every argv git {rev-parse, ls-tree, "
        "cat-file, status} or the export child, 2 export children per call (0 for I4-i and I5-d, "
        "1 for I5-f), no claude, no ANTHROPIC variable; (4) the real job-scheduler (ef17cf8, ' M "
        "uv.lock') and Streak (a0db033, ' M studio.toml') unchanged, 0 newer files"
    )
    s = h.tracer.summary()
    real = ep.s_probe(repos)
    checks: dict[str, Any] = {
        "calls_traced_gt_0": s["calls"] > 0,
        "1_tree_unchanged_every_call": not s["tree_changed"],
        "1_no_file_newer_than_a_call": not s["newer"],
        "2_tmpdir_empty_after_every_call": not s["tmp_left"],
        "3_only_allowed_argv": not s["bad_popen"],
        "3_zero_connects": s["connects"] == 0,
        "3_export_children_as_predicted": not s["children_mismatch"],
        "3_no_claude_no_anthropic": not s["claude_or_anthropic"],
        "3_no_x_to_git": all(not r["git_args_with_x"] for r in h.tracer.records),
        "4_real_repos_unchanged": real.passed,
        "worktree_newer_since_marker_0": marker_checks["newer"] == [],
    }
    obs = (
        f"{s['calls']} traced calls; tree changed {len(s['tree_changed'])}, newer "
        f"{len(s['newer'])}, tmp left {len(s['tmp_left'])}, bad argv {len(s['bad_popen'])}, "
        f"connects {s['connects']}, export-children mismatches {len(s['children_mismatch'])}, "
        f"claude/ANTHROPIC {len(s['claude_or_anthropic'])}; real repos: {real.observed}; "
        f"worktree newer since marker {len(marker_checks['newer'])}"
    )
    extra = {"summary": s, "real_repos": real.runs, "marker": marker_checks}
    return mk("I6", pred, obs, [s], True, checks, extra)


# ---- main -------------------------------------------------------------------------------------

PROBES = {
    "I0a": i0a,
    "I0b": i0b,
    "I0c": i0c,
    "I1": i1,
    "I2": i2,
    "I3": i3,
    "I4": i4,
    "I5": i5,
    "I7": i7,
    "I8": i8,
    "T": t_probe,
}
ORDER = ["I0a", "I0b", "I0c", "I1", "I2", "I3", "I4", "I5", "I7", "I8", "T", "I6"]


def leftover_processes(*ctxs: Any) -> list[str]:
    out: list[str] = []
    for c in ctxs:
        out += base.leftover_processes(c)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones (default: delete)")
    ap.add_argument(
        "--skip-regression",
        action="store_true",
        help="skip I8's runners, pytest and check.sh (development only)",
    )
    ns = ap.parse_args(argv)
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
    shim = scratch / f"shim-{os.getpid()}"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM)
    log = scratch / f"impact-trace-{os.getpid()}.log"
    log.write_text("")
    jc = ep.JCtx(ns.runs, scratch / "j", ns.out)
    sc = ep.SX(ns.runs, scratch / "s", ns.out)
    for c in (jc, sc):
        c.env["PYTHONPATH"] = f"{shim}{os.pathsep}{REPO}"
        c.env["IMPACT_PROBE_LOG"] = str(log)
    tracer = Tracer(log, REPO, [ns.out])
    h = H(ns, jc, sc, tracer)
    roots = {"$WORK_J": jc.root, "$WORK_S": sc.root, "$SCRATCH": scratch}
    repos = {
        "job-scheduler": ep.RealRepo(jc, base.PROVIDER, base.REF, " M uv.lock", [".venv"]),
        "streak": ep.RealRepo(sc, streak.STREAK, streak.REF, " M studio.toml", []),
    }
    marker = scratch / "worktree-marker"
    marker.write_text("start\n")
    time.sleep(1.1)  # a clear mtime gap for find -newer
    t_start = time.time()
    results: list[Result] = []

    def write(res: Result) -> None:
        doc = {
            "id": res.id,
            "prediction": res.prediction,
            "observed": res.observed,
            "pass": res.passed,
            "identical": res.identical,
            "n_runs": len(res.runs),
            "checks": res.checks,
            "extra": res.extra,
            "runs": res.runs,
        }
        (ns.out / f"{res.id}.json").write_text(json.dumps(ep.scrub(doc, roots), indent=1) + "\n")

    procs: list[str] = []
    tmp_left: list[str] = []
    try:
        for pid in ids:
            print(f"... {pid}", file=sys.stderr, flush=True)
            if pid == "I6":
                find = subprocess.run(
                    [
                        "find",
                        str(REPO),
                        "-newer",
                        str(marker),
                        "-type",
                        "f",
                        "-not",
                        "-path",
                        f"{ns.out}/*",
                        "-not",
                        "-path",
                        f"{REPO}/.git/*",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                newer = sorted(ln for ln in find.stdout.splitlines() if ln)
                res = i6(h, repos, {"newer": newer})
            else:
                res = PROBES[pid](h)
            results.append(res)
            write(res)
    finally:
        procs = leftover_processes(jc, sc)
        tmp_left = sorted(
            f"{t}/{p.name}"
            for t, c in (("j", jc), ("s", sc))
            for p in (c.tmp.iterdir() if c.tmp.exists() else [])
        )
        if not ns.keep:
            for c in (jc, sc):
                shutil.rmtree(c.root, ignore_errors=True)
            shutil.rmtree(shim, ignore_errors=True)
            log.unlink(missing_ok=True)
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<5} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(f"{r.id:<5} | {r.prediction} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    ).stdout.strip()
    summary = {
        "experiment": "2026-10-13-contracts-impact",
        "command": "python dogfood/contracts/impact_probes.py " + " ".join(sys.argv[1:]),
        "runs": ns.runs,
        "code_root": str(REPO),
        "code_head": head,
        "job_scheduler_ref": base.REF,
        "streak_ref": streak.REF,
        "wall_s": round(time.time() - t_start, 1),
        "probes": [
            {
                "id": r.id,
                "prediction": r.prediction,
                "observed": r.observed,
                "pass": r.passed,
                "runs": len(r.runs),
                "identical": r.identical,
                "checks": r.checks,
            }
            for r in results
        ],
        "totals": {"pass": passed, "total": len(results)},
        "regression_skipped": ns.skip_regression,
        "trace": tracer.summary(),
        "safety_before": {k: v.before for k, v in repos.items()},
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "scratch_removed": not ns.keep,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(ep.scrub(summary, roots), indent=1) + "\n")
    return 0 if passed == len(results) and not procs and not tmp_left else 1


if __name__ == "__main__":
    sys.exit(main())
