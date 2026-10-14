"""Probe runner for linked pending questions in consumers (slice 4b: ``lattice contracts notify``,
``lattice contracts questions``, the provider Studio's accept trigger).

Implements the probes of ``docs/results/2026-10-14-linked-questions-prereg.md`` (section 3): Q0,
Q1, Q2, Q3, Q4, Q5, Q6, Q7 and T. Predictions and pass rules are copied from that document; a probe
that does not match is reported as a finding with its raw output, never adapted.

    python dogfood/contracts/linkedq_probes.py --runs 10 --scratch DIR \
        --out docs/results/data/2026-10-14-linked-questions [--only Q0,Q1,...] [--skip-regression]

Every probe works on ``git clone --no-hardlinks`` scratch copies of job-scheduler at ef17cf8 and of
Streak at a0db033 under ``DIR``; the real repositories are only read (S proves it). No LLM and no
API key are used. Every deterministic probe runs ``--runs`` times (default 10) and all canonical
outputs (``elapsed_s``, ``created_at``, ``at``, ``since``, ``started_at`` dropped) must be
identical. The three concurrency stress parts of Q3 ((f), (g), (h)) and the 50 writer kills of Q6
run once. Raw per-run outputs go to ``OUT/<ID>.json`` and ``OUT/summary.json``.

The helpers (Ctx, Result, canonical outputs, the edit tables, ``H.ref``, ``multi_a``, ``multi_b``)
are those of ``probes.py``, ``streak_probes.py``, ``exclude_probes.py`` and ``impact_probes.py``,
imported from the files next to this one. ``--skip-regression`` skips Q7's runners, pytest and
check.sh (development).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
OLD_CHECKOUT = Path("/Users/vivekgade/Documents/lattice")  # main checkout, next-phases 33d8de1
OLD_HEAD = "33d8de1"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


ip = _load("lattice_contracts_impact_probes_q", HERE / "impact_probes.py")
ep = ip.ep
streak = ip.streak
base = ip.base
Result = base.Result
canon_call = base.canon_call
mk = ep.mk
yn = ep.yn

COMMITTED = REPO / "docs" / "results" / "data"
EXP_J = COMMITTED / "2026-10-13-contracts"
EXP_S = COMMITTED / "2026-10-13-contracts-streak"
EXP_X = COMMITTED / "2026-10-13-contracts-exclude"
MANIFEST, LOCK, IMPORTS = base.MANIFEST, base.LOCK, ip.IMPORTS
HASH_J, HASH_S = ep.HASH_J, ep.HASH_S
EX = ep.EX
HARNESS = ep.HARNESS
CLOCK_IMPORT = ep.CLOCK_IMPORT
SEED = REPO / "dogfood" / "studio" / "project_seed"
PROJECT_FILES = ("REQUIREMENTS.md", "spec.py", "spec.trace.json")
DROP = {"elapsed_s", "created_at", "at", "since", "started_at"}
QDIR = "linked_questions"

POST_HOC_SCRIPT_CORRECTIONS: list[str] = [
    "Q1 (I4-g): a first full run stopped in its second run with FileNotFoundError: the probe "
    "deleted ci-bot-2's lock at every run and the file was already gone after the first. The "
    "unlink is now `missing_ok=True` (the lock stays deleted: `no_pin` every run). No pass rule "
    "or prediction changed; the first run's partial output was overwritten.",
]
POST_HOC_PROCEDURE: list[str] = [
    "Helpers: JCtx and SX (exclude_probes.py) and `H.ref`, `multi_a`, `multi_b` "
    "(impact_probes.py) are reused. A candidate ref dN is the committed edit list applied to a "
    "clone of the clean clone and fetched into the provider clone as refs/heads/dN, before the "
    "measured window. Each row of Q0-Q2 runs on a fresh consumer state: `questions init` after "
    "removing `.lattice/`.",
    "Q0 counts 18 as the prereg does: the 6 unaffected D rows and 11 controls are 17 rows, plus "
    "the --dry-run of job D2. Q2's table lists 10 steps (s1..s9 and a second 's8', the d8 "
    "regression, here s10) under 'Pass: 8 of 8': every step is a check and all 10 are reported.",
    "Q3 (c) and Q4 (ii) make the state directory read-only recursively (chmod -R 0555): a 0555 "
    "state directory whose `linked_questions/` was made earlier by `questions init` would still "
    "accept a write into that subdirectory. (f), (g), (h) and the 50 kills of Q6 are stress "
    "parts and run once; (a)-(e) run --runs times. (g) adds a hammer of 200 in-process delivers "
    "from two processes behind a start barrier next to the 50 rounds of two notify CLI "
    "processes. (h)'s 'after <= 6 s' is measured from the start of the notify (elapsed_s: the "
    "impact plus the 5 s wait), the wall time of the process is recorded beside it.",
    "Q1's Studio trigger uses one scratch clone per run (the name carries `-r<run>`); the "
    "suffix is removed from the recorded strings before the 10 runs are compared (a first full "
    "run failed 'identical 10 of 10' on exactly those paths, nothing else differed).",
    "Q1's Studio trigger and Q4 serve the Streak clone with `python -m lattice.studio DIR "
    "--stub-llm --workspace M` (one DIR: its name, from studio.toml, is the manifest name "
    "`streak`); the ready change is written to `<state dir>/changes/c1.json` with the same "
    "`StateStore.save` the Studio uses, its project_hash computed by `files_hash` of the clone, "
    "and the spec.py of the Dn edit. The accept is POST /p/streak/api/changes/c1/accept. Git "
    "identity and dates come from the server's environment (fixed), so the commit sha is "
    "reproducible and equal across the two code trees.",
    "Q5: the MCP server has no per-id operation, so `unknown_question` is checked on the CLI and "
    "the API and `unknown_project` on all three (the MCP tool error names the code). The two "
    "`/p/<name>/` Studios are seed copies (`dogfood/studio/project_seed` files, a studio.toml "
    "naming the project), `ci-bot` at the consumer's directory and `scheduler` at a separate "
    "directory (its store is the provider's own, empty one).",
    "Q7 and impact_probes.py: the prereg says 'impact_probes.py --runs 10 I0-I8 pass', but I8 of "
    "that runner compares with the main checkout at 67189af (its OLD_HEAD, written before 4a was "
    "merged) and the main checkout is now at 33d8de1, so its two head checks cannot hold. Q7 "
    "runs its other 11 probes (`--only I0a,I0b,I0c,I1,I2,I3,I4,I5,I7,T,I6`, --runs 10); the "
    "byte comparison against 33d8de1 that I8 stands for is Q7's own.",
    "Two invocations: the first full run's Q7 started the nested impact_probes.py without "
    "--only, which also runs I8 and re-runs the three runners, pytest and check.sh inside it "
    "(over 40 minutes); it was stopped, Q7 was fixed (above) and re-run with `--only Q7 --merge`, "
    "which lists Q0-Q6, T and S from the first invocation's files in the table and the totals "
    "(`merged_from_earlier_invocation` in summary.json; summary-invocation-1.json is the first "
    "invocation's own summary). The code under test did not change between the two.",
    "Q7: the byte comparison runs the same argv under PYTHONPATH=<this tree> and "
    "PYTHONPATH=<the main checkout at 33d8de1> on the Q1 workspace; `state_dir_for` paths are "
    "computed in a subprocess per tree for the drill project and the test projects.",
]


# ---- helpers ------------------------------------------------------------------------------------


def canon(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: canon(v) for k, v in x.items() if k not in DROP}
    if isinstance(x, list):
        return [canon(v) for v in x]
    return x


def deep_sub(x: Any, old: str, new: str) -> Any:
    """`x` with every `old` in its strings replaced (the per-run suffix of a scratch name)."""
    if isinstance(x, str):
        return x.replace(old, new)
    if isinstance(x, dict):
        return {k: deep_sub(v, old, new) for k, v in x.items()}
    if isinstance(x, list):
        return [deep_sub(v, old, new) for v in x]
    return x


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def lk(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def committed_link(path: Path, *keys: str | int) -> dict[str, Any]:
    return ip.committed(path, *keys)


def keep(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in findings if f["class"] == "BROKEN" or f.get("relied")]


def cdir(ctx: Any, m: Path, consumer: str | None = None) -> Path:
    return m.parent.parent / (consumer or ctx.consumer)


def sdir(ctx: Any, m: Path, consumer: str | None = None) -> Path:
    return cdir(ctx, m, consumer) / ".lattice" / "state" / (consumer or ctx.consumer)


def writable(path: Path) -> None:
    for dp, dns, fns in os.walk(path):
        for n in [*dns, *fns]:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(dp, n), 0o755 if n in dns else 0o644)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o755)


def init_state(ctx: Any, m: Path, consumer: str | None = None) -> Path:
    c = cdir(ctx, m, consumer)
    if (c / ".lattice").exists():
        writable(c / ".lattice")
        shutil.rmtree(c / ".lattice", ignore_errors=True)
    r = ctx.contracts(m, "questions", "init", consumer or ctx.consumer, "--json")
    assert r.rc == 0, r.out + r.err
    return sdir(ctx, m, consumer)


def notify(ctx: Any, m: Path, provider: str, cand: str, *flags: str) -> Any:
    return ctx.contracts(m, "notify", provider, "--candidate", cand, "--json", *flags)


def qlist(ctx: Any, m: Path, consumer: str | None = None, *flags: str) -> dict[str, Any]:
    r = ctx.contracts(m, "questions", "list", consumer or ctx.consumer, "--json", *flags)
    assert r.rc == 0, r.out + r.err
    return json.loads(r.out)  # type: ignore[no-any-return]


def qfiles(state: Path) -> dict[str, str]:
    """Every question and link file as text (the lock excluded)."""
    q = state / QDIR
    out: dict[str, str] = {}
    if q.is_dir():
        for p in sorted(q.rglob("*")):
            if p.is_file() and p.name != ".lock":
                out[str(p.relative_to(q))] = p.read_text()
    return out


def qnames(state: Path) -> list[str]:
    q = state / QDIR
    return sorted(str(p.relative_to(q)) for p in q.rglob("*")) if q.is_dir() else []


def qdocs(state: Path, links: bool = False) -> dict[str, Any]:
    """The parsed question files (and the link files when `links`), canonical."""
    return {
        k: canon(json.loads(v))
        for k, v in qfiles(state).items()
        if links or not k.startswith("links/")
    }


def snap(ctx: Any, m: Path, consumer: str | None = None) -> tuple[dict[str, str], dict[str, int]]:
    return ip.snapshot([cdir(ctx, m, consumer)], [])


def delta(before: Any, after: Any, state: Path, root: Path) -> dict[str, Any]:
    """Files added or changed (sha256 or mtime) between two snapshots; `outside` are those not
    under the state directory's linked_questions/."""
    (fb, mb), (fa, ma) = before, after
    keys = set(fb) | set(fa)
    changed = sorted(k for k in keys if fb.get(k) != fa.get(k) or mb.get(k) != ma.get(k))
    allowed = str(state / QDIR)
    outside = [k for k in changed if not k.startswith(allowed)]
    rel = lambda ks: [str(Path(k).relative_to(root)) for k in ks]  # noqa: E731
    return {"changed": rel(changed), "outside": rel(outside)}


def first_row(c: Any, consumer: str | None = None) -> dict[str, Any]:
    doc = c.json()
    rows = doc["consumers"]
    if consumer is not None:
        rows = [r for r in rows if r["consumer"] == consumer]
    return rows[0]  # type: ignore[no-any-return]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port: int = s.getsockname()[1]
    s.close()
    return port


def http(method: str, url: str, body: Any = None, timeout: float = 120) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback only
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def hj(method: str, url: str, body: Any = None) -> tuple[int, Any]:
    status, raw = http(method, url, body)
    return status, json.loads(raw)


class Server:
    """A real ``python -m lattice.studio`` process (own session; killed with a signal)."""

    def __init__(
        self,
        ctx: Any,
        dirs: list[Path],
        manifest: Path | None,
        *,
        root: Path = REPO,
        env: dict[str, str] | None = None,
        tag: str = "srv",
    ) -> None:
        self.ctx, self.port = ctx, free_port()
        self.env = dict(env or ctx.env)
        self.env["PYTHONPATH"] = str(root)
        argv = [
            sys.executable,
            "-m",
            "lattice.studio",
            *map(str, dirs),
            "--port",
            str(self.port),
            "--quiet",
            "--stub-llm",
        ]
        if manifest is not None:
            argv += ["--workspace", str(manifest)]
        self.log = (ctx.root / f"{tag}-{self.port}.log").open("w")
        self.proc = subprocess.Popen(
            argv,
            cwd=ctx.work,
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(600):
            if self.proc.poll() is not None:
                raise RuntimeError(f"studio exited {self.proc.returncode}: see {self.log.name}")
            try:
                urllib.request.urlopen(f"{self.base}/api/projects", timeout=2).close()  # noqa: S310
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("studio did not come up")

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, sig)
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(10)
        self.log.close()

    def __enter__(self) -> Server:
        return self

    def __exit__(self, *a: Any) -> None:
        self.stop()


def git_env(ctx: Any) -> dict[str, str]:
    env = dict(ctx.env)
    env.update(
        GIT_AUTHOR_NAME="probe",
        GIT_AUTHOR_EMAIL="probe@example.invalid",
        GIT_COMMITTER_NAME="probe",
        GIT_COMMITTER_EMAIL="probe@example.invalid",
        GIT_AUTHOR_DATE="2026-10-14T00:00:00+00:00",
        GIT_COMMITTER_DATE="2026-10-14T00:00:00+00:00",
    )
    return env


class LockHolder:
    """Another process that holds the flock of a consumer's ``.lock`` for `seconds`."""

    def __init__(self, lock: Path, seconds: float = 30) -> None:
        code = (
            "import fcntl,os,sys,time\n"
            "fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o644)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('locked', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n"
        )
        self.p = subprocess.Popen(
            [sys.executable, "-c", code, str(lock), str(seconds)], stdout=subprocess.PIPE, text=True
        )
        assert self.p.stdout is not None
        assert self.p.stdout.readline().strip() == "locked"

    def release(self) -> None:
        self.p.kill()
        self.p.wait()


class W:
    """The harness: both contexts and the refs of the committed drifts."""

    def __init__(self, ns: argparse.Namespace, jc: Any, sc: Any) -> None:
        self.ns, self.jc, self.sc = ns, jc, sc
        self.hh = ip.H(ns, jc, sc, None)
        self.jdrift = {d["id"]: d for d in base.DRIFTS}
        self.sdrift = {d["id"]: d for d in streak.DRIFTS}

    def ref(self, ctx: Any, clone: Path, name: str, edits: Any, **kw: Any) -> str:
        return self.hh.ref(ctx, clone, name, edits, **kw)[1]  # type: ignore[no-any-return]

    def jref(self, did: str) -> str:
        jp = ep.shared(self.jc)
        self.ref(self.jc, jp, did.lower(), self.jdrift[did]["edits"])
        return did.lower()

    def sref(self, did: str) -> str:
        sp = ep.shared(self.sc)
        self.ref(self.sc, sp, did.lower(), self.sdrift[did]["edits"])
        return did.lower()


# the committed rows' expectations (prereg section 3, Q1): (BROKEN, relied CHANGED, endpoints,
# unrelied dropped or None when the prereg does not give it); None = no question
J_Q: dict[str, tuple[int, int, int, int | None] | None] = {
    "D1": (1, 0, 1, None),
    "D2": (1, 1, 1, None),
    "D3": (1, 1, 1, None),
    "D4": (1, 1, 1, None),
    "D5": (1, 1, 1, None),
    "D6": None,
    "D7": None,
    "D8": None,
    "D9": None,
    "D10": None,
}
S_Q: dict[str, tuple[int, int, int, int | None] | None] = {
    "D1": (1, 0, 1, None),
    "D2": (1, 1, 1, None),
    "D3": (1, 1, 1, 2),
    "D4": (1, 1, 1, None),
    "D5": (1, 1, 1, None),
    "D6": (1, 1, 1, 4),
    "D7": (7, 0, 7, 41),
    "D8": None,
    "D9": None,
    "D10": None,
    "D11": None,
    "D12": None,
    "D13": None,
}


def controls(h: W, tag: str) -> list[dict[str, Any]]:
    """The 11 unaffected controls of I3 as (id, ctx, manifest, provider, candidate): J-C1..C4,
    S-C1..C6 and I3x-a (Streak under the exclusion, re-pinned)."""
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm = jc.workspace(f"{tag}j", jp)
    sm = sc.workspace(f"{tag}s", sp)
    out: list[dict[str, Any]] = []

    def add(cid: str, ctx: Any, m: Path, prov: str, cand: str) -> None:
        out.append({"id": cid, "ctx": ctx, "m": m, "provider": prov, "cand": cand})

    add("J-C1", jc, jm, "scheduler", "ef17cf8")
    h.ref(jc, jp, "jc2", [base.C2_EDIT])
    add("J-C2", jc, jm, "scheduler", "jc2")
    h.hh.ref(jc, jp, "jc3", None, setup=lambda c: base.c3_edit(c))
    add("J-C3", jc, jm, "scheduler", "jc3")
    h.ref(jc, jp, "jc4", [base.C4_EDIT])
    add("J-C4", jc, jm, "scheduler", "jc4")
    add("S-C1", sc, sm, "streak", "a0db033")
    s_edits: dict[str, Any] = {
        "C2": [streak.C2_EDIT],
        "C3": [streak.C3_EDIT],
        "C4": [streak.C4_EDIT],
        "C5": streak.C5_EDITS,
        "C6": [streak.C6_EDIT],
    }
    for cid, edits in s_edits.items():
        h.ref(sc, sp, f"sc{cid[1]}", edits)
        add(f"S-{cid}", sc, sm, "streak", f"sc{cid[1]}")
    xa = sc.workspace(f"{tag}xa", sp, lock=None, variant="a", exclude=HARNESS)
    sc.lock_path(xa).unlink(missing_ok=True)
    pin = sc.pin(xa)
    assert pin.rc == 0, pin.out + pin.err
    add("I3x-a", sc, xa, "streak", "a0db033")
    return out


# ---- Q0: no-op -------------------------------------------------------------------------------


def q0(h: W) -> Result:
    pred = (
        "job D6, D7, D10, Streak D8, D9, D12 and the 11 unaffected controls: every row `action: "
        "none`, 0 files added or changed in the consumer's directory after init, linked_questions/ "
        "empty, exit 0; --dry-run on job D2: created, dry_run true, 0 files written. 18/18"
    )
    jc, sc = h.jc, h.sc
    cases: list[dict[str, Any]] = []
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("Q0dj", jp), sc.workspace("Q0ds", sp)
    for did in ("D6", "D7", "D10"):
        cases.append(
            {"id": f"J-{did}", "ctx": jc, "m": jm, "provider": "scheduler", "cand": h.jref(did)}
        )
    for did in ("D8", "D9", "D12"):
        cases.append(
            {"id": f"S-{did}", "ctx": sc, "m": sm, "provider": "streak", "cand": h.sref(did)}
        )
    cases += controls(h, "Q0c")
    d2 = h.jref("D2")

    def one(_: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for c in cases:
            state = init_state(c["ctx"], c["m"])
            b = snap(c["ctx"], c["m"])
            r = notify(c["ctx"], c["m"], c["provider"], c["cand"])
            a = snap(c["ctx"], c["m"])
            row = first_row(r)
            out[c["id"]] = {
                "rc": r.rc,
                "label": row["label"],
                "action": row["action"],
                "question": row["question"],
                "exit_code": r.json()["exit_code"],
                "delta": delta(b, a, state, cdir(c["ctx"], c["m"])),
                "names": qnames(state),
            }
        state = init_state(jc, jm)
        b = snap(jc, jm)
        r = notify(jc, jm, "scheduler", d2, "--dry-run")
        a = snap(jc, jm)
        row, doc = first_row(r), r.json()
        out["dry-run J-D2"] = {
            "rc": r.rc,
            "action": row["action"],
            "question": row["question"],
            "dry_run": doc["dry_run"],
            "names": qnames(state),
            "delta": delta(b, a, state, cdir(jc, jm)),
        }
        return out

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    r0 = runs[0]
    checks: dict[str, Any] = {}
    for c in cases:
        x = r0[c["id"]]
        checks[c["id"]] = (
            x["rc"] == 0
            and x["action"] == "none"
            and x["question"] is None
            and x["delta"]["changed"] == []
            and x["names"] == []
        )
    d = r0["dry-run J-D2"]
    checks["dry-run J-D2"] = (
        d["rc"] == 0
        and d["action"] == "created"
        and d["dry_run"] is True
        and d["delta"]["changed"] == []
        and d["names"] == []
    )
    n = sum(1 for v in checks.values() if v)
    checks["all_18"] = n == 18 and len(checks) == 18
    obs = f"{n}/{len(checks) - 1} rows as predicted; " + ", ".join(
        f"{k} {r0[k]['action']}" for k in list(r0)[:3]
    )
    return mk("Q0", pred, obs, runs, ident, checks)


# ---- Q1: one question per contradiction ------------------------------------------------------


def q1_series(
    h: W,
    ctx: Any,
    m: Path,
    provider: str,
    drifts: dict[str, Any],
    expect: Any,
    exp_dir: Path,
    from_check: bool,
    pin_hash: str,
) -> dict[str, Any]:
    """The rows of one D series (one fresh state each) as a canonical record per run."""
    out: dict[str, Any] = {}
    for did in drifts:
        cand = h.jref(did) if provider == "scheduler" else h.sref(did)
        state = init_state(ctx, m)
        b = snap(ctx, m)
        r = notify(ctx, m, provider, cand)
        a = snap(ctx, m)
        row = first_row(r)
        docs = qdocs(state, links=True)
        qs = {k: v for k, v in docs.items() if re.fullmatch(r"lq\d+\.json", k)}
        out[did] = {
            "rc": r.rc,
            "label": row["label"],
            "action": row["action"],
            "question": row["question"],
            "questions": qs,
            "links": {k: v for k, v in docs.items() if k.startswith("links/")},
            "delta": delta(b, a, state, cdir(ctx, m)),
        }
    return out


def q1_judge(
    series: dict[str, Any],
    expect: dict[str, Any],
    exp_dir: Path,
    from_check: bool,
    pin_hash: str,
    tag: str,
) -> tuple[dict[str, bool], dict[str, Any], str]:
    checks: dict[str, bool] = {}
    keys: list[str] = []
    parts = []
    n_q = 0
    for did, want in expect.items():
        rec = series[did]
        link = committed_link(exp_dir / f"{did}.json", *(["check"] if from_check else []))
        if want is None:
            ok = (
                rec["rc"] == 0
                and rec["action"] == "none"
                and rec["questions"] == {}
                and rec["delta"]["changed"] == []
            )
            checks[f"{tag}-{did}"] = ok
            parts.append(f"{did} none" + ("" if ok else " !"))
            continue
        nb, nr, ne, dropped = want
        n_q += 1
        (q,) = rec["questions"].values() if len(rec["questions"]) == 1 else (None,)
        ok = q is not None and rec["rc"] == 0 and rec["action"] == "created"
        if q is not None:
            br = [f for f in q["findings"] if f["class"] == "BROKEN"]
            rl = [f for f in q["findings"] if f["class"] == "CHANGED"]
            keys.append(q["key"])
            ok = (
                ok
                and q["id"] == "lq1"
                and q["findings"] == keep(link["findings"])
                and len(br) == nb
                and len(rl) == nr
                and len(q["imports_affected"]) == ne
                and (dropped is None or q["counts"]["changed_unrelied"] == dropped)
                and q["pinned"]["spec_hash"].startswith(pin_hash)
                and q["status"] == "open"
                and q["label"] == "contradiction"
                and q["source"]["via"] == "cli"
                and set(rec["links"]) == {f"links/{provider_of(tag)}.static.json"}
                and rec["delta"]["outside"] == []
            )
        checks[f"{tag}-{did}"] = ok
        parts.append(f"{did} {nb}+{nr}" + ("" if ok else " !"))
    checks[f"{tag}-keys_distinct"] = len(keys) == len(set(keys)) == n_q
    return checks, {"questions": n_q}, f"{tag}: {n_q} questions; " + " ".join(parts)


def provider_of(tag: str) -> str:
    return "scheduler" if tag == "job" else "streak"


def i3xb_case(h: W) -> tuple[Any, Path]:
    sc = h.sc
    sp = ep.shared(sc)
    xb = sc.workspace("Q1xb", sp, lock=None, variant="b", exclude=HARNESS)
    imp = xb.parent.parent / streak.CONSUMER / IMPORTS
    imp.write_text(imp.read_text() + CLOCK_IMPORT)
    sc.lock_path(xb).unlink(missing_ok=True)
    assert sc.pin(xb).rc == 1  # refused: the clock import is BROKEN
    assert sc.pin(xb, "--force").rc == 0
    return sc, xb


def multi_cases(h: W) -> dict[str, Any]:
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    for did in ("D1", "D2", "D3", "D4", "D8"):
        h.jref(did)
    h.sref("D13")
    return {
        "a": ip.multi_a(h.hh, "Q1a", jp),
        "c": ip.multi_a(h.hh, "Q1c", jp),
        "d": ip.multi_a(h.hh, "Q1d", jp),
        "f": ip.multi_a(h.hh, "Q1f", jp, ci2=None),
        "g": ip.multi_a(h.hh, "Q1g", jp),
        "h": ip.multi_b(h.hh, "Q1h", jp, sp),
    }


def streak_accept(
    h: W,
    name: str,
    edits: list[Any],
    *,
    root: Path,
    edits2: list[Any] | None = None,
    consumer_mode: str = "delivered",
    hold: float = 0.0,
    second: bool = False,
    run_tag: str = "",
    compare_cli: bool = False,
) -> dict[str, Any]:
    """One accept of Streak's ready change c1 (spec.py = `edits` applied) through a real
    Studio server over a fresh clone, with the code tree `root`. consumer_mode: delivered
    (state initialised), readonly (state dir chmod -R 0555), missing (no state dir), lock (the
    consumer's .lock held by another process for `hold` seconds)."""
    from lattice.studio.server import empty_change, files_hash
    from lattice.studio.state import StateStore, state_dir_path

    sc = h.sc
    clone = sc.clone(f"{name}{run_tag}")
    m = sc.workspace(f"{name}{run_tag}", clone)
    consumer = cdir(sc, m)
    state = sdir(sc, m)
    # the ready change(s): their files come from a scratch copy with the edits applied
    work = sc.work / f"{name}{run_tag}-edit"
    work.mkdir(parents=True, exist_ok=True)
    for n in PROJECT_FILES:
        shutil.copy2(clone / n, work / n)
    h0 = files_hash(clone)
    for e in edits:
        e.apply(work)
    after1 = {n: (work / n).read_text() for n in PROJECT_FILES}
    h1 = files_hash(work)
    after2 = None
    if second:
        for e in edits2 or []:
            e.apply(work)
        after2 = {n: (work / n).read_text() for n in PROJECT_FILES}
    import tomllib

    toml = tomllib.loads((clone / "studio.toml").read_text())
    pstate = state_dir_path(clone, "streak", toml.get("state") or {}, toml.get("rebuild") or {})
    store = StateStore(pstate)
    from lattice.spec.rules import Trace

    def seed(cid: str, files: dict[str, str], project_hash: str) -> None:
        c = empty_change(cid, f"probe {cid}", None)
        c["status"] = "ready"
        priv = {
            "project_hash": project_hash,
            "selected": [],
            "new_md": files["REQUIREMENTS.md"],
            "new_module": files["spec.py"],
            "new_trace": Trace.from_json(files["spec.trace.json"]).to_json(),
        }
        store.save("changes", cid, c, priv)

    seed("c1", after1, h0)
    if second and after2 is not None:
        seed("c2", after2, h1)
    # the consumer's state
    holder = None
    if consumer_mode in ("delivered", "readonly", "lock"):
        init_state(sc, m)
    if consumer_mode == "readonly":
        for dp, dns, fns in os.walk(state, topdown=False):
            for n in [*fns, *dns]:
                os.chmod(os.path.join(dp, n), 0o555)
        os.chmod(state, 0o555)
    if consumer_mode == "lock":
        holder = LockHolder(state / QDIR / ".lock", hold)
    srv = Server(sc, [clone], m, root=root, env=git_env(sc), tag=f"acc-{name}")
    rec: dict[str, Any] = {}
    try:
        t0 = time.perf_counter()
        st, raw = http("POST", f"{srv.base}/p/streak/api/changes/c1/accept", {})
        rec["latency_s"] = time.perf_counter() - t0
        rec["status"], rec["body"] = st, raw.decode()
        if second:
            t0 = time.perf_counter()
            st2, raw2 = http("POST", f"{srv.base}/p/streak/api/changes/c2/accept", {})
            rec["latency2_s"] = time.perf_counter() - t0
            rec["status2"], rec["body2"] = st2, raw2.decode()
        notes: Any = None
        if root == REPO:
            want = 2 if second else 1
            t0 = time.time()
            while time.time() - t0 < 90:
                s, n = hj("GET", f"{srv.base}/p/streak/api/linked-notifications")
                notes = n["notifications"]
                if s == 200 and len(notes) >= want and all(x["status"] != "running" for x in notes):
                    break
                time.sleep(0.1)
        rec["notifications"] = notes
    finally:
        if holder is not None:
            holder.release()
        srv.stop()
        if consumer_mode == "readonly":
            writable(state)
    git = sc.git
    rec["head"] = git(clone, "rev-parse", "HEAD").strip()
    rec["tree"] = git(clone, "ls-tree", "-r", "HEAD")
    rec["message"] = git(clone, "log", "-1", "--format=%B")
    rec["log"] = git(clone, "log", "--format=%H %an %ae %ad %cd %s", "--date=iso-strict", "-3")
    rec["status_porcelain"] = git(clone, "status", "--porcelain")
    rec["provider_state"] = sorted(
        str(p.relative_to(pstate)) for p in pstate.rglob("*") if p.is_file()
    )
    rec["c1"] = canon_saved(pstate / "changes" / "c1.json")
    ev = pstate / "events.jsonl"
    rec["events"] = [canon_event(ln) for ln in ev.read_text().splitlines()] if ev.exists() else []
    rec["exclude"] = (clone / ".git" / "info" / "exclude").read_text()
    rec["consumer_questions"] = qdocs(state) if state.exists() else None
    rec["consumer_names"] = qnames(state) if state.exists() else None
    rec["consumer_state_exists"] = state.exists() or (consumer / ".lattice").exists()
    if compare_cli:  # the CLI's questions for the accepted commit, on a fresh consumer state
        st2 = init_state(sc, m)
        r = notify(sc, m, "streak", rec["head"])
        rec["cli_questions"] = qdocs(st2)
        rec["cli_rc"] = r.rc
    rec["clone"], rec["manifest"], rec["state"] = clone, m, state
    return rec


def canon_saved(path: Path) -> Any:
    doc = json.loads(path.read_text())
    doc.pop("saved_at", None)
    return canon(doc)


def canon_event(line: str) -> Any:
    ev = json.loads(line)
    for k in ("ts", "id", "at", "time", "timestamp"):  # the id is random (event_id)
        ev.pop(k, None)
    return ev


def q1(h: W) -> Result:
    pred = (
        "job D1-D5 and Streak D1-D7: one question each (id lq1; findings = committed Dn minus "
        "unrelied CHANGED; D1 1 BROKEN; D2-D5 1+1; Streak D3 2, D6 4, D7 41 unrelied dropped; D7 "
        "7 endpoints); job D6-D10, Streak D8-D13: none; I3x-b 1 BROKEN endpoint_missing PUT "
        "/test/clock; I0c stale pin: K(I0c) == K(D2) and `existing`; I4 a c d f g h as in the "
        "table; keys distinct 5 and 7; Studio trigger D2 -> 1 question = the CLI's but "
        "`source`, D8 and D10 -> 0 (3/3)"
    )
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("Q1j", jp), sc.workspace("Q1s", sp)
    xsc, xb = i3xb_case(h)
    multi = multi_cases(h)
    # I0c: the provider clone is at the D2 commit, the consumer is the Q1j one (state shared)
    d2_edits = h.jdrift["D2"]["edits"]
    stale = jc.clone("Q1-stale", d2_edits)
    i0c_m = ip.write_manifest(
        jc.work / "Q1i0c" / "m" / MANIFEST,
        [
            {
                "name": "scheduler",
                "path": str(stale),
                "role": "provider",
                "spec": "spec.tickets:t5",
            },
            {"name": "ci-bot", "path": str(cdir(jc, jm)), "role": "consumer"},
        ],
        [{"from": "ci-bot", "to": "scheduler", "contract": "static"}],
    )
    d2 = h.jref("D2")

    def one(i: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        rec["job"] = q1_series(h, jc, jm, "scheduler", h.jdrift, J_Q, EXP_J, False, HASH_J)
        rec["streak"] = q1_series(h, sc, sm, "streak", h.sdrift, S_Q, EXP_S, True, HASH_S)
        # I3x-b
        st = init_state(xsc, xb)
        r = notify(xsc, xb, "streak", "a0db033")
        rec["I3x-b"] = {"rc": r.rc, "row": first_row(r), "questions": qdocs(st)}
        # I0c, run after D2 on the same consumer state
        st = init_state(jc, jm)
        r1 = notify(jc, jm, "scheduler", d2)
        before = qfiles(st)
        snap0 = snap(jc, jm)
        r2 = jc.contracts(i0c_m, "notify", "scheduler", "--candidate", "HEAD", "--json")
        rec["I0c"] = {
            "d2": first_row(r1),
            "stale": first_row(r2),
            "rc": r2.rc,
            "same_files": qfiles(st) == before,
            "delta": delta(snap0, snap(jc, jm), st, cdir(jc, jm)),
            "questions": qdocs(st),
        }
        # I4
        i4: dict[str, Any] = {}
        for k, cand in (("a", "d1"), ("c", "d4"), ("d", "d8")):
            m = multi[k]
            for c in ("ci-bot", "ci-bot-2"):
                init_state(jc, m, c)
            r = notify(jc, m, "scheduler", cand)
            i4[k] = {
                "rc": r.rc,
                "rows": {x["consumer"]: x for x in r.json()["consumers"]},
                "q": {c: qdocs(sdir(jc, m, c)) for c in ("ci-bot", "ci-bot-2")},
            }
        m = multi["f"]
        init_state(jc, m, "ci-bot")
        b2 = snap(jc, m, "ci-bot-2")
        r = notify(jc, m, "scheduler", "d1")
        i4["f"] = {
            "rc": r.rc,
            "rows": [x["consumer"] for x in r.json()["consumers"]],
            "ci2_untouched": snap(jc, m, "ci-bot-2") == b2,
            "ci2_no_state": not (cdir(jc, m, "ci-bot-2") / ".lattice").exists(),
            "q": qdocs(sdir(jc, m, "ci-bot")),
        }
        m = multi["g"]
        for c in ("ci-bot", "ci-bot-2"):
            init_state(jc, m, c)
        (cdir(jc, m, "ci-bot-2") / LOCK).unlink(missing_ok=True)
        b2 = snap(jc, m, "ci-bot-2")
        r = notify(jc, m, "scheduler", "d1")
        i4["g"] = {
            "rc": r.rc,
            "rows": {x["consumer"]: x for x in r.json()["consumers"]},
            "q": qdocs(sdir(jc, m, "ci-bot")),
            "ci2_untouched": snap(jc, m, "ci-bot-2") == b2,
            "ci2_names": qnames(sdir(jc, m, "ci-bot-2")),
        }
        m = multi["h"]
        init_state(jc, m, "multi")
        r1 = notify(jc, m, "scheduler", "d2")
        r2 = notify(jc, m, "streak", "d13")
        st = sdir(jc, m, "multi")
        i4["h"] = {
            "sched": first_row(r1),
            "streak": first_row(r2),
            "rc": [r1.rc, r2.rc],
            "names": qnames(st),
        }
        rec["I4"] = i4
        # the Studio trigger: D2 -> 1 question, D8 -> 0, D10 -> 0 (accepted on a Streak Studio)
        trig = {}
        for did in ("D2", "D8", "D10"):
            a = streak_accept(
                h,
                f"Q1trig-{did}",
                h.sdrift[did]["edits"],
                root=REPO,
                run_tag=f"-r{i}",
                compare_cli=True,
            )
            trig[did] = deep_sub(
                {
                    k: a[k]
                    for k in (
                        "status",
                        "body",
                        "notifications",
                        "head",
                        "consumer_questions",
                        "consumer_names",
                        "cli_questions",
                    )
                },
                f"-r{i}",
                "",
            )
        rec["trigger"] = trig
        return rec

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    r0 = runs[0]
    checks: dict[str, Any] = {}
    parts = []
    c1, d1, p1 = q1_judge(r0["job"], J_Q, EXP_J, False, HASH_J, "job")
    c2, d2_, p2 = q1_judge(r0["streak"], S_Q, EXP_S, True, HASH_S, "streak")
    checks.update(c1)
    checks.update(c2)
    parts += [p1, p2]
    checks["totals_5_of_10_and_7_of_13"] = d1["questions"] == 5 and d2_["questions"] == 7
    xbr = r0["I3x-b"]
    (xq,) = xbr["questions"].values()
    checks["I3x-b"] = (
        xbr["rc"] == 0
        and xbr["row"]["action"] == "created"
        and [(f["class"], f["kind"], f["endpoint"], f["detail"]) for f in xq["findings"]]
        == [("BROKEN", "endpoint_missing", "PUT /test/clock", EX)]
    )
    ic = r0["I0c"]
    qd2 = [q for q in ic["questions"].values()]
    checks["I0c"] = (
        ic["stale"]["action"] == "existing"
        and ic["d2"]["action"] == "created"
        and ic["stale"]["question"] == "lq1"
        and ic["same_files"]
        and ic["delta"]["changed"] == []
        and len(qd2) == 1
        and ic["rc"] == 0
    )
    i4 = r0["I4"]
    ce = lambda k, c: i4[k]["rows"][c]["action"]  # noqa: E731
    q1c = lambda k, c: list(i4[k]["q"][c].values())  # noqa: E731
    checks["I4-a"] = (
        ce("a", "ci-bot") == "created"
        and ce("a", "ci-bot-2") == "none"
        and i4["a"]["rc"] == 0
        and len(q1c("a", "ci-bot")) == 1
        and len(q1c("a", "ci-bot-2")) == 0
    )
    q_ci = [v for k, v in i4["c"]["q"]["ci-bot"].items() if k.startswith("lq")]
    q_c2 = [v for k, v in i4["c"]["q"]["ci-bot-2"].items() if k.startswith("lq")]
    checks["I4-c"] = (
        len(q_ci) == len(q_c2) == 1
        and q_ci[0]["findings"] == q_c2[0]["findings"]
        and ce("c", "ci-bot") == ce("c", "ci-bot-2") == "created"
    )
    checks["I4-d"] = (
        ce("d", "ci-bot") == ce("d", "ci-bot-2") == "none"
        and i4["d"]["q"]["ci-bot"] == {}
        and i4["d"]["q"]["ci-bot-2"] == {}
    )
    checks["I4-f"] = (
        i4["f"]["rows"] == ["ci-bot"]
        and i4["f"]["ci2_untouched"]
        and i4["f"]["ci2_no_state"]
        and len([k for k in i4["f"]["q"] if k.startswith("lq")]) == 1
    )
    g = i4["g"]
    checks["I4-g"] = (
        g["rows"]["ci-bot-2"]["action"] == "skipped"
        and g["rows"]["ci-bot-2"]["error"]["code"] == "no_pin"
        and g["rows"]["ci-bot"]["action"] == "created"
        and g["rc"] == 1
        and g["ci2_untouched"]
        and g["ci2_names"] == []
    )
    hh = i4["h"]
    checks["I4-h"] = (
        hh["sched"]["action"] == "created"
        and hh["streak"]["action"] == "none"
        and "links/scheduler.static.json" in hh["names"]
        and "links/streak.static.json" not in hh["names"]
        and hh["rc"] == [0, 0]
    )
    tr = r0["trigger"]
    got = (tr["D2"]["consumer_questions"] or {}).get("lq1.json")
    want = (tr["D2"]["cli_questions"] or {}).get("lq1.json")
    n = tr["D2"]["notifications"]
    diff = sorted(
        k for k in set(want or {}) | set(got or {}) if (want or {}).get(k) != (got or {}).get(k)
    )
    checks["trigger_D2_equals_cli_but_source"] = (
        got is not None
        and want is not None
        and len(tr["D2"]["consumer_questions"]) == 1
        and diff == ["source"]
        and got["source"] == {"via": "accept", "change": "c1", "commit": tr["D2"]["head"]}
        and want["source"] == {"via": "cli", "change": None, "commit": tr["D2"]["head"]}
        and n is not None
        and n[0]["status"] == "done"
    )
    checks["trigger_D2_diff_keys"] = diff
    checks["trigger_D8_none"] = (
        tr["D8"]["consumer_questions"] == {} and tr["D8"]["notifications"][0]["status"] == "done"
    )
    checks["trigger_D10_none"] = (
        tr["D10"]["consumer_questions"] == {} and tr["D10"]["notifications"][0]["status"] == "done"
    )
    checks["trigger_3_of_3"] = all(
        checks[k]
        for k in ("trigger_D2_equals_cli_but_source", "trigger_D8_none", "trigger_D10_none")
    )
    gate = {k: v for k, v in checks.items() if k != "trigger_D2_diff_keys"}
    obs = (
        f"{p1}; {p2}; I3x-b {yn(checks['I3x-b'])}; I0c {yn(checks['I0c'])}; I4 a "
        f"{yn(checks['I4-a'])} c {yn(checks['I4-c'])} d {yn(checks['I4-d'])} f "
        f"{yn(checks['I4-f'])} g {yn(checks['I4-g'])} h {yn(checks['I4-h'])}; trigger "
        f"{yn(checks['trigger_3_of_3'])}"
    )
    res = mk("Q1", pred, obs, runs, ident, gate)
    res.checks = checks
    res.passed = ident and all(gate.values())
    return res


# ---- Q2: idempotence and supersede -----------------------------------------------------------


def q2(h: W) -> Result:
    pred = (
        "s1 created lq1; s2 existing, 0 files changed; s3 the D2 sha and d2c4 existing, 0 files "
        "changed; s4 d3 -> lq2, lq1 open and superseded {by lq2, newer_question}, lq1.json "
        "byte-identical; s5 HEAD: nothing created, both superseded {by null, no_contradiction}; "
        "s6 d2 -> existing lq1, lq2 superseded by lq1; s7 dismiss lq1 then d2: existing, 0 "
        "question files changed, 2 questions; s8 HEAD: none, current null (link rewritten); s9 "
        "d2 -> NEW lq3 (same key as dismissed lq1), lq1 stays dismissed, 3 questions; s10 d8 "
        "(regression): 0 created, current null, lq2 open"
    )
    jc = h.jc
    jp = ep.shared(jc)
    m = jc.workspace("Q2", jp)
    d2, d3, d8 = h.jref("D2"), h.jref("D3"), h.jref("D8")
    h.ref(jc, jp, "d2c4", [*h.jdrift["D2"]["edits"], base.C4_EDIT])
    d2sha = jc.git(jp, "rev-parse", "d2").strip()
    link = "links/scheduler.static.json"

    def one(_: int) -> dict[str, Any]:
        st = init_state(jc, m)
        c = cdir(jc, m)
        out: dict[str, Any] = {}
        status = lambda q: {k: v["status"] for k, v in q.items() if k.startswith("lq")}  # noqa: E731

        def lst() -> dict[str, Any]:
            return {q["id"]: q for q in qlist(jc, m)["questions"]}

        def run(cand: str) -> dict[str, Any]:
            b = snap(jc, m)
            r = notify(jc, m, "scheduler", cand)
            a = snap(jc, m)
            row = first_row(r)
            return {
                "action": row["action"],
                "question": row["question"],
                "rc": r.rc,
                "changed": delta(b, a, st, c)["changed"],
                "outside": delta(b, a, st, c)["outside"],
            }

        out["s1"] = {**run(d2), "current": lk(st / QDIR / link)["current"]}
        lq1_bytes = (st / QDIR / "lq1.json").read_bytes()
        out["s2"] = run(d2)
        out["s3a"], out["s3b"] = run(d2sha), run("d2c4")
        out["s4"] = run(d3)
        q = lst()
        out["s4"]["lq1"] = {
            "status": q["lq1"]["status"],
            "sup": q["lq1"]["superseded"],
            "same_bytes": (st / QDIR / "lq1.json").read_bytes() == lq1_bytes,
        }
        out["s4"]["lq2_sup"] = q["lq2"]["superseded"]
        out["s5"] = run("HEAD")
        q = lst()
        out["s5"]["q"] = {k: {"status": v["status"], "sup": v["superseded"]} for k, v in q.items()}
        out["s6"] = run(d2)
        q = lst()
        out["s6"]["q"] = {k: {"status": v["status"], "sup": v["superseded"]} for k, v in q.items()}
        r = jc.contracts(m, "questions", "dismiss", "ci-bot", "lq1", "--json")
        out["s7_dismiss"] = {"rc": r.rc, "status": json.loads(r.out)["status"]}
        before = qfiles(st)
        out["s7"] = run(d2)
        out["s7"]["files_equal"] = qfiles(st) == before
        out["s7"]["n_questions"] = len(lst())
        out["s8"] = run("HEAD")
        out["s8"]["current"] = lk(st / QDIR / link)["current"]
        out["s9"] = run(d2)
        q = lst()
        out["s9"]["q"] = {k: v["status"] for k, v in q.items()}
        out["s9"]["same_key"] = q["lq3"]["key"] == q["lq1"]["key"]
        out["s10"] = run(d8)
        q = lst()
        out["s10"]["current"] = lk(st / QDIR / link)["current"]
        out["s10"]["lq2"] = q["lq2"]["status"]
        out["final_status"] = status(qdocs(st))
        return out

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    x = runs[0]
    sup = lambda by, why: by is not None and by["by"] == why[0] and by["reason"] == why[1]  # noqa: E731
    checks = {
        "s1": x["s1"]["action"] == "created"
        and x["s1"]["question"] == "lq1"
        and x["s1"]["current"] == "lq1",
        "s2": x["s2"]["action"] == "existing" and x["s2"]["changed"] == [],
        "s3": all(x[k]["action"] == "existing" and x[k]["changed"] == [] for k in ("s3a", "s3b")),
        "s4": x["s4"]["action"] == "created"
        and x["s4"]["question"] == "lq2"
        and x["s4"]["lq1"]["status"] == "open"
        and sup(x["s4"]["lq1"]["sup"], ("lq2", "newer_question"))
        and x["s4"]["lq1"]["same_bytes"]
        and x["s4"]["lq2_sup"] is None,
        "s5": x["s5"]["action"] == "none"
        and x["s5"]["question"] is None
        and all(
            v["status"] == "open" and sup(v["sup"], (None, "no_contradiction"))
            for v in x["s5"]["q"].values()
        )
        and len(x["s5"]["q"]) == 2,
        "s6": x["s6"]["action"] == "existing"
        and x["s6"]["question"] == "lq1"
        and x["s6"]["q"]["lq1"]["sup"] is None
        and sup(x["s6"]["q"]["lq2"]["sup"], ("lq1", "newer_question")),
        "s7": x["s7_dismiss"] == {"rc": 0, "status": "dismissed"}
        and x["s7"]["action"] == "existing"
        and x["s7"]["files_equal"]
        and x["s7"]["n_questions"] == 2,
        "s8": x["s8"]["action"] == "none"
        and x["s8"]["current"] is None
        and any(p.endswith("scheduler.static.json") for p in x["s8"]["changed"]),
        "s9": x["s9"]["action"] == "created"
        and x["s9"]["question"] == "lq3"
        and x["s9"]["q"] == {"lq1": "dismissed", "lq2": "open", "lq3": "open"}
        and x["s9"]["same_key"],
        "s10": x["s10"]["action"] == "none"
        and x["s10"]["current"] is None
        and x["s10"]["lq2"] == "open",
        "no_status_changed_by_a_notification": x["final_status"]
        == {"lq1.json": "dismissed", "lq2.json": "open", "lq3.json": "open"},
        "nothing_outside_linked_questions": all(
            v["outside"] == [] for v in x.values() if isinstance(v, dict) and "outside" in v
        ),
    }
    n = sum(1 for k in ("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10") if checks[k])
    obs = f"{n}/10 steps; " + " ".join(
        f"{k}={x[k]['action']}{'/' + x[k]['question'] if x[k].get('question') else ''}"
        for k in ("s1", "s2", "s4", "s5", "s6", "s7", "s8", "s9", "s10")
    )
    return mk("Q2", pred, obs, runs, ident, checks)


# ---- Q3: cross-project write safety ----------------------------------------------------------


STRESS = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lattice.contracts import linked
state, rowfile, n, tag = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
base = json.loads(rowfile.read_text())
cand = base.pop("_candidate")
src = {"via": "cli", "change": None, "commit": "c" * 40}
ids = []
for i in range(n):
    row = json.loads(json.dumps(base))
    for f in row["findings"]:
        f["endpoint"] = f"{f['endpoint']} #{tag}{i}"
    res = linked.deliver(state, "w", row, cand, src)
    ids.append(res["question"])
if tag == "w":  # the writer also clears every question it made
    for qid in ids:
        linked.clear(state, qid, linked.RESOLVED, "x" * 50, "cli")
print(json.dumps(ids))
"""

READER = r"""
import json, sys, time
from pathlib import Path
q = Path(sys.argv[1]) / "linked_questions"
stop = Path(sys.argv[2])
bad = partial = seen = 0
while not stop.exists():
    paths = sorted(q.glob("lq*.json")) + sorted((q / "links").glob("*.json"))
    for p in paths:
        try:
            text = p.read_text()
        except OSError:
            continue
        seen += 1
        try:
            doc = json.loads(text)
        except ValueError:
            bad += 1
            continue
        if not isinstance(doc, dict) or not (
                {"id", "status", "findings"} <= set(doc) or {"current", "provider"} <= set(doc)):
            partial += 1
print(json.dumps({"seen": seen, "parse_errors": bad, "partial": partial}))
"""


def real_row(h: W, name: str) -> Path:
    """A real contradiction row (job D2) and its candidate as a JSON file for the stress scripts."""
    jc = h.jc
    jp = ep.shared(jc)
    m = jc.workspace(name, jp)
    r = jc.contracts(m, "impact", "scheduler", "--candidate", h.jref("D2"), "--json")
    doc = json.loads(r.out)
    row = doc["consumers"][0]
    row["_candidate"] = doc["candidate"]
    path = jc.work / f"{name}-row.json"
    path.write_text(json.dumps(row))
    return path


def q3(h: W) -> Result:
    pred = (
        "(a) outside linked_questions/ every consumer file byte- and mtime-identical, new files "
        "only at D3.3's paths (.git/info/exclude included); (b) a real StateStore dir's meta.json, "
        "plans/, builds/, changes/ byte-identical and the next allocate is c2; (c) a read-only "
        "state dir: undelivered consumer_state_unwritable, exit 1, others delivered; (d) missing: "
        "consumer_state_missing, nothing created; (e) [state] dir lands in st/, enabled = false "
        "is _disabled; (f) 0 parse errors and 0 partial objects under 200 notifications and 200 "
        "resolves; (g) ids unique and contiguous, no file overwritten over 50 rounds; (h) held "
        "lock: consumer_state_busy after <= 6 s"
    )
    jc, sc = h.jc, h.sc
    jp = ep.shared(jc)
    d1, d2, d3, d4, d8 = (h.jref(x) for x in ("D1", "D2", "D3", "D4", "D8"))
    ma = ip.multi_a(h.hh, "Q3c", jp)
    mgit = jc.workspace("Q3a", jp)
    mb_ = jc.workspace("Q3b", jp)
    md = jc.workspace("Q3d", jp)
    me = jc.workspace("Q3e", jp)
    git_consumer = cdir(jc, mgit)
    jc.run(["git", "init", "-q", str(git_consumer)])
    jc.git(git_consumer, "add", "-A")
    jc.git(
        git_consumer,
        "-c",
        "user.name=probe",
        "-c",
        "user.email=p@e.invalid",
        "commit",
        "-qm",
        "consumer",
    )
    from lattice.studio.state import StateStore

    def one(_: int) -> dict[str, Any]:
        rec: dict[str, Any] = {}
        # (a) a consumer that is a git repository, through a whole sequence
        st = init_state(jc, mgit)
        b = snap(jc, mgit)
        for cand in (d2, d3, "HEAD", d2, d8):
            notify(jc, mgit, "scheduler", cand)
        jc.contracts(mgit, "questions", "dismiss", "ci-bot", "lq1", "--json")
        notify(jc, mgit, "scheduler", d2)
        a = snap(jc, mgit)
        rec["a"] = delta(b, a, st, git_consumer)
        rec["a"]["names"] = qnames(st)
        # (b) a real StateStore dir
        stb = sdir(jc, mb_)
        if (cdir(jc, mb_) / ".lattice").exists():
            shutil.rmtree(cdir(jc, mb_) / ".lattice")
        store = StateStore(stb)
        n = store.allocate("changes")
        store.save("changes", f"c{n}", {"id": f"c{n}", "status": "accepted"}, {})
        store.save("plans", "p1", {"id": "p1", "status": "passed"}, {})
        store.save("builds", "b1", {"id": "b1", "status": "passed"}, {})
        keep_b = {
            str(p.relative_to(stb)): (sha(p.read_bytes()), p.stat().st_mtime_ns)
            for p in sorted(stb.rglob("*"))
            if p.is_file()
        }
        r = notify(jc, mb_, "scheduler", d2)
        after_b = {
            str(p.relative_to(stb)): (sha(p.read_bytes()), p.stat().st_mtime_ns)
            for p in sorted(stb.rglob("*"))
            if p.is_file() and QDIR not in p.parts
        }
        rec["b"] = {
            "action": first_row(r)["action"],
            "identical": after_b == keep_b,
            "next_changes_id": StateStore(stb).allocate("changes"),
            "top": sorted(p.name for p in stb.iterdir()),
        }
        # (c) a read-only state directory, the other consumer delivered (D4: both contradict)
        s1 = init_state(jc, ma, "ci-bot")
        init_state(jc, ma, "ci-bot-2")
        for dp, dns, fns in os.walk(s1, topdown=False):
            for nme in [*fns, *dns]:
                os.chmod(os.path.join(dp, nme), 0o555)
        os.chmod(s1, 0o555)
        try:
            r = notify(jc, ma, "scheduler", d4)
        finally:
            writable(s1)
        rows = {x["consumer"]: x for x in r.json()["consumers"]}
        rec["c"] = {
            "rc": r.rc,
            "actions": {k: v["action"] for k, v in rows.items()},
            "warning": (rows["ci-bot"]["warning"] or {}).get("code"),
            "ci_bot_names": qnames(s1),
            "ci_bot_2": sorted(k for k in qfiles(sdir(jc, ma, "ci-bot-2"))),
        }
        # (d) a missing state dir
        if (cdir(jc, md) / ".lattice").exists():
            writable(cdir(jc, md) / ".lattice")
            shutil.rmtree(cdir(jc, md) / ".lattice")
        bd = snap(jc, md)
        r = notify(jc, md, "scheduler", d2)
        row = first_row(r)
        found = subprocess.run(
            ["find", str(cdir(jc, md)), "-name", ".lattice"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        rec["d"] = {
            "rc": r.rc,
            "action": row["action"],
            "warning": (row["warning"] or {}).get("code"),
            "find": found,
            "unchanged": snap(jc, md) == bd,
        }
        # (e) [state] dir and enabled = false
        ce_ = cdir(jc, me)
        if (ce_ / ".lattice").exists():
            shutil.rmtree(ce_ / ".lattice")
        shutil.rmtree(ce_ / "st", ignore_errors=True)
        (ce_ / "st").mkdir()
        (ce_ / "studio.toml").write_text('[state]\ndir = "st"\n')
        r = notify(jc, me, "scheduler", d2)
        row = first_row(r)
        rec["e_dir"] = {
            "action": row["action"],
            "names": sorted(qfiles(ce_ / "st")),
            "no_lattice": not (ce_ / ".lattice").exists(),
            "state_dir_ok": row["state_dir"] == str((ce_ / "st").resolve()),
        }
        (ce_ / "studio.toml").write_text("[state]\nenabled = false\n")
        r = notify(jc, me, "scheduler", d2)
        row = first_row(r)
        rec["e_off"] = {
            "action": row["action"],
            "warning": (row["warning"] or {}).get("code"),
            "no_lattice": not (ce_ / ".lattice").exists(),
        }
        (ce_ / "studio.toml").unlink()
        shutil.rmtree(ce_ / "st", ignore_errors=True)
        return rec

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    r0 = runs[0]
    # (f), (g), (h): stress parts, once
    row_file = real_row(h, "Q3row")
    stress = jc.work / "stress.py"
    stress.write_text(STRESS)
    reader = jc.work / "reader.py"
    reader.write_text(READER)
    f_state = jc.work / "Q3f-state"
    shutil.rmtree(f_state, ignore_errors=True)
    f_state.mkdir()
    stop = jc.work / "Q3f-stop"
    stop.unlink(missing_ok=True)
    rd = subprocess.Popen(
        [sys.executable, str(reader), str(f_state), str(stop)], stdout=subprocess.PIPE, text=True
    )
    wr = subprocess.run(
        [sys.executable, str(stress), str(REPO), str(f_state), str(row_file), "200", "w"],
        capture_output=True,
        text=True,
        check=False,
    )
    stop.write_text("x")
    rd_out = json.loads(rd.communicate(timeout=120)[0])
    f_ids = json.loads(wr.stdout) if wr.returncode == 0 else []
    f_docs = qdocs(f_state)
    rec_f = {
        "writer_rc": wr.returncode,
        "ids": len(f_ids),
        "reader": rd_out,
        "resolved": sum(
            1 for k, v in f_docs.items() if k.startswith("lq") and v["status"] == "resolved"
        ),
        "tmp": [p.name for p in f_state.rglob(".*.tmp-*")],
    }
    # (g): two notify processes, two providers, one consumer, 50 rounds
    sp = ep.shared(sc)
    h.sref("D2")
    mbm = ip.multi_b(h.hh, "Q3g", jp, sp)
    g_rounds: list[dict[str, Any]] = []
    for _round in range(50):
        stg = init_state(jc, mbm, "multi")
        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "lattice.contracts",
                    "notify",
                    prov,
                    "--candidate",
                    cand,
                    "--json",
                    "--workspace",
                    str(mbm),
                ],
                cwd=jc.work,
                env=jc.env,
                stdout=subprocess.PIPE,
                text=True,
            )
            for prov, cand in (("scheduler", "d2"), ("streak", "d2"))
        ]
        outs = [p.communicate(timeout=300)[0] for p in procs]
        docs = qdocs(stg)
        qs = {k: v for k, v in docs.items() if k.startswith("lq")}
        g_rounds.append(
            {
                "ids": sorted(qs),
                "providers": sorted(v["provider"] for v in qs.values()),
                "actions": sorted(json.loads(o)["consumers"][0]["action"] for o in outs),
            }
        )
    # the hammer: two processes delivering 100 each behind the same start, one state
    hammer = jc.work / "Q3g-hammer"
    shutil.rmtree(hammer, ignore_errors=True)
    hammer.mkdir()
    hs = [
        subprocess.Popen(
            [sys.executable, str(stress), str(REPO), str(hammer), str(row_file), "100", t],
            stdout=subprocess.PIPE,
            text=True,
        )
        for t in ("p", "q")
    ]
    h_ids = [json.loads(p.communicate(timeout=300)[0]) for p in hs]
    h_files = sorted(int(p.name[2:-5]) for p in (hammer / QDIR).glob("lq*.json"))
    # (h): the lock is held by another process
    sth = init_state(jc, md)
    holder = LockHolder(sth / QDIR / ".lock", 60)
    t0 = time.time()
    try:
        r = notify(jc, md, "scheduler", d2)
        wall = time.time() - t0
    finally:
        holder.release()
    row = first_row(r)
    rec_h = {
        "rc": r.rc,
        "action": row["action"],
        "warning": (row["warning"] or {}).get("code"),
        "elapsed_s": r.json()["elapsed_s"],
        "wall_s": round(wall, 2),
        "names": [n for n in qnames(sth) if n != ".lock"],
    }
    checks = {
        "a_only_allowed_paths": r0["a"]["outside"] == []
        and len(r0["a"]["changed"]) > 0
        and r0["a"]["names"]
        == [".lock", "links", "links/scheduler.static.json", "lq1.json", "lq2.json", "lq3.json"],
        "b_statestore_untouched": r0["b"]["identical"]
        and r0["b"]["action"] == "created"
        and r0["b"]["next_changes_id"] == 2
        and r0["b"]["top"]
        == sorted(["builds", "changes", "linked_questions", "meta.json", "plans"]),
        "c_readonly_undelivered_others_delivered": r0["c"]["rc"] == 1
        and r0["c"]["actions"] == {"ci-bot": "undelivered", "ci-bot-2": "created"}
        and r0["c"]["warning"] == "consumer_state_unwritable"
        and r0["c"]["ci_bot_names"] == []
        and r0["c"]["ci_bot_2"] != [],
        "d_missing_nothing_created": r0["d"]["action"] == "undelivered"
        and r0["d"]["rc"] == 1
        and r0["d"]["warning"] == "consumer_state_missing"
        and r0["d"]["find"] == []
        and r0["d"]["unchanged"],
        "e_state_dir_and_disabled": r0["e_dir"]["action"] == "created"
        and r0["e_dir"]["names"] != []
        and r0["e_dir"]["no_lattice"]
        and r0["e_dir"]["state_dir_ok"]
        and r0["e_off"]["action"] == "undelivered"
        and r0["e_off"]["warning"] == "consumer_state_disabled"
        and r0["e_off"]["no_lattice"],
        "f_no_torn_reads": rec_f["writer_rc"] == 0
        and rec_f["ids"] == 200
        and rec_f["resolved"] == 200
        and rec_f["reader"]["parse_errors"] == 0
        and rec_f["reader"]["partial"] == 0
        and rec_f["reader"]["seen"] > 0
        and rec_f["tmp"] == [],
        "g_ids_unique_and_contiguous_50_rounds": all(
            x["ids"] == ["lq1.json", "lq2.json"]
            and x["providers"] == ["scheduler", "streak"]
            and x["actions"] == ["created", "created"]
            for x in g_rounds
        ),
        "g_hammer_200_ids_unique_and_contiguous": sorted(h_ids[0] + h_ids[1])
        == sorted(f"lq{i}" for i in range(1, 201))
        and h_files == list(range(1, 201)),
        "h_busy_within_6s": rec_h["action"] == "undelivered"
        and rec_h["rc"] == 1
        and rec_h["warning"] == "consumer_state_busy"
        and rec_h["elapsed_s"] <= 6
        and rec_h["names"] == [],
    }
    obs = (
        f"a outside {len(r0['a']['outside'])} / changed {len(r0['a']['changed'])}; b "
        f"identical {yn(r0['b']['identical'])} next c{r0['b']['next_changes_id']}; c "
        f"{r0['c']['actions']}; d {r0['d']['warning']} find {r0['d']['find']}; e "
        f"{r0['e_dir']['action']}/{r0['e_off']['warning']}; f {rec_f['ids']} ids, reader "
        f"{rec_f['reader']}; g {sum(1 for x in g_rounds if x['ids'] == ['lq1.json', 'lq2.json'])}"
        f"/50 rounds clean, hammer ids {len(set(h_ids[0] + h_ids[1]))}; h {rec_h['warning']} "
        f"elapsed {rec_h['elapsed_s']} s (wall {rec_h['wall_s']} s)"
    )
    res = mk(
        "Q3",
        pred,
        obs,
        runs,
        ident,
        checks,
        {
            "f": rec_f,
            "g_rounds": g_rounds[:3],
            "g_all_clean": all(x["ids"] == ["lq1.json", "lq2.json"] for x in g_rounds),
            "g_hammer": {"ids": len(set(h_ids[0] + h_ids[1])), "files": len(h_files)},
            "h": rec_h,
            "timings_note": "(f), (g), (h) ran once",
        },
    )
    return res


# ---- Q4: the provider's accept is unchanged ---------------------------------------------------

Q4_CONDS = [("delivered", 0.0), ("readonly", 0.0), ("missing", 0.0), ("lock", 30.0)]


def q4(h: W) -> Result:
    pred = (
        "the Streak D2 accept on this tree and on the main checkout at 33d8de1, each with the "
        "consumer (i) delivered, (ii) 0555, (iii) missing, (iv) its .lock held 30 s: the accept "
        "body byte-identical (same sha); spec files, commit tree and message identical; provider "
        "state dir: same file list, changes/c1.json equal but saved_at, events.jsonl equal but "
        "timestamps; median accept latency (10 runs) within 0.3 s of 33d8de1's, (iv) included "
        "and a second accept during (iv) succeeds; linked_questions appears only in "
        "contracts/linked.py, contracts/__main__.py, mcp/projects.py, the new routes, docs, tests"
    )
    head = subprocess.run(
        ["git", "-C", str(OLD_CHECKOUT), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    lat = subprocess.run(
        ["git", "-C", str(OLD_CHECKOUT), "diff", "--stat", OLD_HEAD, "--", "lattice/"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    d2e, d4e = h.sdrift["D2"]["edits"], h.sdrift["D4"]["edits"]
    results: dict[str, dict[str, list[dict[str, Any]]]] = {}
    runs_n = h.ns.runs
    for cond, hold in Q4_CONDS:
        results[cond] = {"new": [], "old": []}
        for i in range(runs_n):
            for tag, root in (("new", REPO), ("old", OLD_CHECKOUT)):
                a = streak_accept(
                    h,
                    f"Q4-{cond}-{tag}",
                    d2e,
                    root=root,
                    edits2=d4e,
                    consumer_mode=cond,
                    hold=hold,
                    second=cond == "lock",
                    run_tag=f"-{i}",
                )
                a["delivery"] = None
                if tag == "new" and a["notifications"]:
                    a["delivery"] = [
                        n["result"]["consumers"][0]["action"] if n["result"] else n
                        for n in a["notifications"]
                    ]
                    a["delivery_warn"] = [
                        (n["result"]["consumers"][0].get("warning") or {}).get("code")
                        for n in a["notifications"]
                        if n["result"]
                    ]
                results[cond][tag].append(a)
    cmp_keys = (
        "status",
        "body",
        "head",
        "tree",
        "message",
        "log",
        "status_porcelain",
        "provider_state",
        "c1",
        "events",
    )
    checks: dict[str, Any] = {}
    detail: dict[str, Any] = {}
    parts = []
    for cond, _hold in Q4_CONDS:
        new, old = results[cond]["new"], results[cond]["old"]
        eq = {k: all(n[k] == o[k] for n, o in zip(new, old, strict=True)) for k in cmp_keys}
        eq["bodies_one_sha"] = len({n["body"] for n in new} | {o["body"] for o in old}) == 1
        mn = statistics.median(a["latency_s"] for a in new)
        mo = statistics.median(a["latency_s"] for a in old)
        extra: dict[str, Any] = {}
        if cond == "lock":
            eq["second_accept_ok"] = all(a["status2"] == 200 for a in new + old)
            extra = {
                "second_latency_new": statistics.median(a["latency2_s"] for a in new),
                "second_latency_old": statistics.median(a["latency2_s"] for a in old),
            }
            eq["second_bodies_equal"] = all(
                n["body2"] == o["body2"] for n, o in zip(new, old, strict=True)
            )
        eq["median_latency_within_0.3s"] = abs(mn - mo) <= 0.3
        if cond == "delivered":
            eq["delivered"] = all(a["delivery"] == ["created"] for a in new)
        elif cond == "readonly":
            eq["undelivered_warning"] = all(
                a["delivery"] == ["undelivered"]
                and a["delivery_warn"] == ["consumer_state_unwritable"]
                for a in new
            )
        elif cond == "missing":
            eq["undelivered_warning"] = all(
                a["delivery"] == ["undelivered"]
                and a["delivery_warn"] == ["consumer_state_missing"]
                and not a["consumer_state_exists"]
                for a in new
            )
        else:
            eq["busy_warnings"] = all(
                a["delivery"] == ["undelivered", "undelivered"]
                and a["delivery_warn"] == ["consumer_state_busy"] * 2
                for a in new
            )
        checks[cond] = all(eq.values())
        detail[cond] = {
            "eq": eq,
            "median_new_s": round(mn, 3),
            "median_old_s": round(mo, 3),
            **extra,
        }
        parts.append(
            f"{cond}: "
            f"{'ok' if checks[cond] else 'DIFF ' + str([k for k, v in eq.items() if not v])}"
            f" (median {mn:.2f} s new, {mo:.2f} s old)"
        )
    checks["old_checkout_head_is_33d8de1"] = head.startswith(OLD_HEAD)
    checks["old_checkout_lattice_equals_33d8de1"] = lat == ""
    # the static check
    grep = subprocess.run(
        [
            "grep",
            "-rlE",
            "linked_questions|linked-questions|import linked",
            str(REPO / "lattice"),
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    rel = sorted(str(Path(p).relative_to(REPO / "lattice")) for p in grep)
    checks["static_linked_questions_only_where_allowed"] = rel == [
        "contracts/__main__.py",
        "contracts/linked.py",
        "mcp/projects.py",
        "studio/mock_server.py",
        "studio/server.py",
    ]
    obs = "; ".join(parts) + f"; static {rel}"
    # records are compared across runs on their canonical, run-independent parts
    keep_keys = ("status", "body", "head", "tree", "message", "provider_state", "c1")
    runs = [
        {
            c: {t: [{k: a[k] for k in keep_keys} for a in results[c][t]][i] for t in ("new", "old")}
            for c in results
        }
        for i in range(runs_n)
    ]
    ident = all(r == runs[0] for r in runs)
    return mk(
        "Q4",
        pred,
        obs,
        runs[:1],
        ident,
        checks,
        {
            "detail": detail,
            "static": rel,
            "old_checkout": {"head": head, "lattice_diff_stat": lat},
            "bodies": {c: results[c]["new"][0]["body"] for c in results},
        },
    )


# ---- Q5: clearing parity ----------------------------------------------------------------------


def seed_project(dst: Path, name: str) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for n in PROJECT_FILES:
        shutil.copy2(SEED / n, dst / n)
    (dst / "studio.toml").write_text(f'[project]\nname = "{name}"\n[llm]\nbackend = "stub"\n')
    return dst


def mcp_call(
    ctx: Any, manifest: Path, calls: list[tuple[str, dict[str, Any]]], readonly: bool = False
) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "probes", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ]
    for i, (name, args) in enumerate(calls):
        msgs.append(
            {
                "jsonrpc": "2.0",
                "id": 10 + i,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )
    argv = [
        sys.executable,
        "-m",
        "lattice.mcp",
        "serve",
        "--workspace",
        str(manifest),
        "--project",
        str(manifest.parent.parent / ctx.consumer),
    ]
    if readonly:
        argv.append("--readonly")
    c = ctx.run(argv, stdin="\n".join(json.dumps(x) for x in msgs) + "\n", timeout=300)
    by = {
        x["id"]: x for x in (json.loads(ln) for ln in c.out.splitlines() if ln.strip()) if "id" in x
    }
    out = [{"tools": [t["name"] for t in by[1]["result"]["tools"]]}]
    for i in range(len(calls)):
        r = by[10 + i]
        if "error" in r:
            out.append({"error": r["error"]})
        else:
            res = r["result"]
            text = res["content"][0]["text"]
            out.append(
                {
                    "isError": bool(res.get("isError")),
                    "text": text,
                    "doc": None if res.get("isError") else json.loads(text),
                }
            )
    return out


def q5(h: W) -> Result:
    pred = (
        "hub Studio with the workspace (names agree), state from Q2 s4: CLI --json, MCP (stdio, "
        "list only) and API list `all` and `open` bodies equal; CLI and API resolve lq1 (note "
        "adapted) and dismiss on a restored copy: equal bodies but via and at; unknown_question/"
        "404, not_open/409 with the stored note unchanged, bad_note/422, unknown_project (API "
        "404), the provider's own store 404; MCP tools/list (also --readonly) holds "
        "linked_question_list and no resolve/dismiss tool. 11/11"
    )
    jc = h.jc
    jp = ep.shared(jc)
    m = jc.workspace("Q5", jp)
    d2, d3 = h.jref("D2"), h.jref("D3")
    consumer = cdir(jc, m)
    seed_project(consumer, "ci-bot")  # the consumer's project files; imports and lock untouched
    prov_studio = seed_project(jc.work / "Q5-scheduler-studio", "scheduler")
    state = sdir(jc, m)
    qdir = state / QDIR

    def restore(saved: dict[str, bytes]) -> None:
        shutil.rmtree(qdir, ignore_errors=True)
        for rel, data in saved.items():
            p = qdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def one(_: int) -> dict[str, Any]:
        init_state(jc, m)
        # remove project files' effect on state: the Studio will create plans/ builds/ changes/
        for cand in (d2, d3):
            assert notify(jc, m, "scheduler", cand).rc == 0
        saved = {
            str(p.relative_to(qdir)): p.read_bytes() for p in sorted(qdir.rglob("*")) if p.is_file()
        }
        rec: dict[str, Any] = {}
        with Server(jc, [consumer, prov_studio], m, tag="q5") as srv:
            api = f"{srv.base}/p/ci-bot/api"
            for status in ("all", "open"):
                flag = ["--status", status]
                cli = qlist(jc, m, None, *flag)
                mcp = mcp_call(
                    jc, m, [("linked_question_list", {"consumer": "ci-bot", "status": status})]
                )
                s, raw = http("GET", f"{api}/linked-questions?status={status}")
                rec[f"list_{status}"] = {
                    "cli": cli,
                    "mcp": mcp[1]["doc"],
                    "api": json.loads(raw),
                    "status": s,
                }
            # clearing parity: restore the state before each clear
            out: dict[str, Any] = {}
            for verb in ("resolve", "dismiss"):
                restore(saved)
                c = jc.contracts(
                    m, "questions", verb, "ci-bot", "lq1", "--note", "adapted", "--json"
                )
                cli_body = json.loads(c.out)
                restore(saved)
                s, a_body = hj("POST", f"{api}/linked-questions/lq1/{verb}", {"note": "adapted"})
                out[verb] = {"cli": cli_body, "api": a_body, "api_status": s, "cli_rc": c.rc}
            rec["clear"] = out
            # errors (from the state after the last dismiss)
            c = jc.contracts(m, "questions", "resolve", "ci-bot", "lq9", "--json")
            s, a = hj("POST", f"{api}/linked-questions/lq9/resolve", {})
            rec["lq9"] = {
                "cli": json.loads(c.out)["error"]["code"],
                "cli_rc": c.rc,
                "api": a.get("code"),
                "api_status": s,
            }
            stored = (qdir / "lq1.json").read_bytes()
            c = jc.contracts(m, "questions", "resolve", "ci-bot", "lq1", "--json")
            s, a = hj("POST", f"{api}/linked-questions/lq1/resolve", {})
            rec["not_open"] = {
                "cli": json.loads(c.out)["error"]["code"],
                "cli_rc": c.rc,
                "api": a.get("code"),
                "api_status": s,
                "unchanged": (qdir / "lq1.json").read_bytes() == stored,
            }
            big = "x" * 2001
            restore(saved)
            c = jc.contracts(m, "questions", "resolve", "ci-bot", "lq2", "--note", big, "--json")
            s, a = hj("POST", f"{api}/linked-questions/lq2/resolve", {"note": big})
            rec["bad_note"] = {
                "cli": json.loads(c.out)["error"]["code"],
                "cli_rc": c.rc,
                "api": a.get("code"),
                "api_status": s,
                "still_open": json.loads((qdir / "lq2.json").read_text())["status"],
            }
            c = jc.contracts(m, "questions", "list", "nobody", "--json")
            s, a = hj("GET", f"{srv.base}/p/nobody/api/linked-questions")
            mc = mcp_call(jc, m, [("linked_question_list", {"consumer": "nobody"})])[1]
            rec["nobody"] = {
                "cli": json.loads(c.out)["error"]["code"],
                "cli_rc": c.rc,
                "api_status": s,
                "mcp": mc.get("text", "") if mc.get("isError") else mc,
            }
            restore(saved)
            before = (qdir / "lq1.json").read_bytes()
            s, a = hj("POST", f"{srv.base}/p/scheduler/api/linked-questions/lq1/resolve", {})
            rec["provider_store"] = {
                "api_status": s,
                "api": a.get("code"),
                "unchanged": (qdir / "lq1.json").read_bytes() == before,
            }
            s, a = hj("GET", f"{srv.base}/p/scheduler/api/linked-questions")
            rec["provider_list_empty"] = (
                s == 200 and a["questions"] == [] and a["consumer"] == "scheduler"
            )
        tools = mcp_call(jc, m, [], readonly=False)[0]["tools"]
        tools_ro = mcp_call(jc, m, [], readonly=True)[0]["tools"]
        rec["tools"] = {"rw": tools, "ro": tools_ro}
        return rec

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    x = runs[0]

    def strip_ca(b: dict[str, Any]) -> dict[str, Any]:
        d = json.loads(json.dumps(b))
        d["resolution"] = {k: v for k, v in d["resolution"].items() if k not in ("via", "at")}
        return d  # type: ignore[no-any-return]

    mock_note = (
        "same keys and types: tests/studio/test_linked_api.py::test_the_mock_has_the_real_shapes"
    )
    res_ = x["clear"]["resolve"]
    dis_ = x["clear"]["dismiss"]
    checks = {
        "list_all_equal": x["list_all"]["cli"] == x["list_all"]["mcp"] == x["list_all"]["api"]
        and [q["id"] for q in x["list_all"]["cli"]["questions"]] == ["lq1", "lq2"],
        "list_open_equal": x["list_open"]["cli"] == x["list_open"]["mcp"] == x["list_open"]["api"],
        "resolve_equal_but_via_at": strip_ca(res_["cli"]) == strip_ca(res_["api"])
        and res_["cli"]["status"] == "resolved"
        and res_["cli"]["resolution"]["note"] == "adapted",
        "dismiss_equal_but_via_at": strip_ca(dis_["cli"]) == strip_ca(dis_["api"])
        and dis_["cli"]["status"] == "dismissed",
        "via_resolve_cli_vs_studio": res_["cli"]["resolution"]["via"] == "cli"
        and res_["api"]["resolution"]["via"] == "studio",
        "via_dismiss_cli_vs_studio": dis_["cli"]["resolution"]["via"] == "cli"
        and dis_["api"]["resolution"]["via"] == "studio",
        "lq9_unknown_question_404": x["lq9"]["cli"] == x["lq9"]["api"] == "unknown_question"
        and x["lq9"]["api_status"] == 404
        and x["lq9"]["cli_rc"] == 2,
        "not_open_409_unchanged": x["not_open"]["cli"] == x["not_open"]["api"] == "not_open"
        and x["not_open"]["api_status"] == 409
        and x["not_open"]["unchanged"]
        and x["not_open"]["cli_rc"] == 1,
        "bad_note_422": x["bad_note"]["cli"] == x["bad_note"]["api"] == "bad_note"
        and x["bad_note"]["api_status"] == 422
        and x["bad_note"]["still_open"] == "open",
        "nobody_unknown_project": x["nobody"]["cli"] == "unknown_project"
        and x["nobody"]["api_status"] == 404
        and "unknown_project" in str(x["nobody"]["mcp"]),
        "provider_store_404": x["provider_store"]["api_status"] == 404
        and x["provider_store"]["unchanged"]
        and x["provider_list_empty"],
        "tools_list_only": all(
            "linked_question_list" in t and not [n for n in t if "resolve" in n or "dismiss" in n]
            for t in x["tools"].values()
        ),
    }
    n = sum(1 for v in checks.values() if v)
    obs = f"{n}/{len(checks)} checks; tools {[t for t in x['tools']['rw'] if 'link' in t]}"
    return mk("Q5", pred, obs, runs, ident, checks, {"mock": mock_note})


# ---- Q6: durability --------------------------------------------------------------------------

KILL_WRITER = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lattice.contracts import linked
state, rowfile, n = Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
base = json.loads(rowfile.read_text())
cand = base.pop("_candidate")
src = {"via": "cli", "change": None, "commit": "c" * 40}
for i in range(n):
    row = json.loads(json.dumps(base))
    for f in row["findings"]:
        f["endpoint"] = f"{f['endpoint']} #k{i}"
    res = linked.deliver(state, "w", row, cand, src)
    if i % 3 == 0 and res["question"]:
        try:
            linked.clear(state, res["question"], linked.RESOLVED, "n" * 200, "cli")
        except linked.LinkedError:
            pass
    print(i, flush=True)
"""


def q6(h: W) -> Result:
    pred = (
        "hub Studio serving ci-bot; notify d1, d2, d3 (lq1-lq3, lq3 current); dismiss lq1 via "
        "API; SIGKILL; restart: GET /api/linked-questions byte-identical, order lq1, lq2, lq3; "
        "notify d2: existing. A writer SIGKILLed at 50 random points: every lq*.json parses, no "
        "temp file listed, the next write sweeps them. 3/3"
    )
    jc = h.jc
    jp = ep.shared(jc)
    m = jc.workspace("Q6", jp)
    refs = [h.jref(x) for x in ("D1", "D2", "D3")]
    consumer = cdir(jc, m)
    seed_project(consumer, "ci-bot")
    row_file = real_row(h, "Q6row")
    writer = jc.work / "kill-writer.py"
    writer.write_text(KILL_WRITER)

    def one(_: int) -> dict[str, Any]:
        st = init_state(jc, m)
        rec: dict[str, Any] = {}
        for cand in refs:
            r = notify(jc, m, "scheduler", cand)
            assert r.rc == 0, r.out
        srv = Server(jc, [consumer], m, tag="q6a")
        try:
            s, a = hj("POST", f"{srv.base}/api/linked-questions/lq1/dismiss", {"note": "n"})
            assert s == 200, a
            _s, before = http("GET", f"{srv.base}/api/linked-questions")
        finally:
            srv.stop(signal.SIGKILL)
        srv2 = Server(jc, [consumer], m, tag="q6b")
        try:
            _s, after = http("GET", f"{srv2.base}/api/linked-questions")
            doc = json.loads(after)
        finally:
            srv2.stop()
        r = notify(jc, m, "scheduler", refs[1])
        rec["restart"] = {
            "identical": before == after,
            "order": [q["id"] for q in doc["questions"]],
            "statuses": {q["id"]: q["status"] for q in doc["questions"]},
            "d2": first_row(r)["action"],
            "d2_question": first_row(r)["question"],
            "current": lk(st / QDIR / "links/scheduler.static.json")["current"],
        }
        return rec

    runs, ident = jc.repeat(lambda i: canon(one(i)))
    # the 50 kills, once
    st = init_state(jc, m)
    rng = random.Random(6)
    kills: list[dict[str, Any]] = []
    for _kill in range(50):
        p = subprocess.Popen(
            [sys.executable, str(writer), str(REPO), str(st), str(row_file), "400"],
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(rng.uniform(0.05, 0.6))
        alive = p.poll() is None
        p.send_signal(signal.SIGKILL)
        p.wait()
        files = sorted((st / QDIR).glob("lq*.json")) + sorted((st / QDIR / "links").glob("*.json"))
        bad = []
        for f in files:
            try:
                json.loads(f.read_text())
            except ValueError:
                bad.append(f.name)
        tmp = [t.name for t in (st / QDIR).rglob(".*.tmp-*")]
        kills.append({"alive_at_kill": alive, "parse_errors": bad, "tmp_left": tmp})
    listed = [q["id"] for q in qdocs(st).values() if isinstance(q, dict) and "id" in q]
    swept = True
    row = json.loads(row_file.read_text())
    cand = row.pop("_candidate")
    from lattice.contracts import linked

    row["findings"][0]["endpoint"] += " #after"
    linked.deliver(st, "w", row, cand, {"via": "cli", "change": None, "commit": "c" * 40})
    swept = list((st / QDIR).rglob(".*.tmp-*")) == []
    x = runs[0]["restart"]
    checks = {
        "restart_list_byte_identical_in_order": x["identical"]
        and x["order"] == ["lq1", "lq2", "lq3"]
        and x["statuses"] == {"lq1": "dismissed", "lq2": "open", "lq3": "open"},
        "restart_notify_d2_existing": x["d2"] == "existing" and x["d2_question"] == "lq2",
        "kills_50_all_parse_no_temp_listed_next_write_sweeps": len(kills) == 50
        and all(k["parse_errors"] == [] for k in kills)
        and swept
        and len(listed) > 0
        and all(k["alive_at_kill"] for k in kills),
    }
    obs = (
        f"restart identical {yn(x['identical'])} order {x['order']} d2 {x['d2']}/"
        f"{x['d2_question']}; 50 kills: parse errors "
        f"{sum(len(k['parse_errors']) for k in kills)}, temp files left after a kill "
        f"{sum(1 for k in kills if k['tmp_left'])} (swept by the next write: {yn(swept)}), "
        f"alive at kill {sum(1 for k in kills if k['alive_at_kill'])}/50"
    )
    return mk("Q6", pred, obs, runs, ident, checks, {"kills": kills})


# ---- T ---------------------------------------------------------------------------------------


def t_probe(h: W) -> Result:
    pred = "median CLI notify, 10 runs, threshold <= 10 s, predicted <= 1.5 s"
    jc, sc = h.jc, h.sc
    jp, sp = ep.shared(jc), ep.shared(sc)
    jm, sm = jc.workspace("T-j", jp), sc.workspace("T-s", sp)
    jd, sd = h.jref("D2"), h.sref("D2")
    jw, sw, jimp = [], [], []
    for _ in range(h.ns.runs):
        init_state(jc, jm)
        c = notify(jc, jm, "scheduler", jd)
        jw.append(c.wall)
        init_state(sc, sm)
        c = notify(sc, sm, "streak", sd)
        sw.append(c.wall)
        i = jc.contracts(jm, "impact", "scheduler", "--candidate", jd, "--json")
        jimp.append(i.wall)
    mj, ms, mi = statistics.median(jw), statistics.median(sw), statistics.median(jimp)
    checks = {
        "job_scheduler_median_le_10s": mj <= 10,
        "streak_median_le_10s": ms <= 10,
        "job_scheduler_prediction_le_1.5s": mj <= 1.5,
        "streak_prediction_le_1.5s": ms <= 1.5,
    }
    obs = f"job-scheduler median {mj:.2f} s, Streak median {ms:.2f} s (impact alone {mi:.2f} s)"
    passed = checks["job_scheduler_median_le_10s"] and checks["streak_median_le_10s"]
    extra = {
        "job_scheduler_wall_s": [round(x, 3) for x in jw],
        "streak_wall_s": [round(x, 3) for x in sw],
        "impact_job_wall_s": [round(x, 3) for x in jimp],
        "note": "timing is not deterministic; 'identical' n/a",
    }
    return Result("T", pred, obs, passed, [{"job": jw, "streak": sw}], True, checks, extra)


# ---- Q7: no regression -----------------------------------------------------------------------


IMPACT_ONLY = "I0a,I0b,I0c,I1,I2,I3,I4,I5,I7,T,I6"


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("UV_NO_SYNC", "1")
    env.setdefault("UV_PROJECT_ENVIRONMENT", str(Path(sys.prefix)))
    return env


STATE_PATHS = (
    "import sys, json\nfrom pathlib import Path\nfrom lattice.studio.state import state_dir_for\n"
    "out = []\nfor p, name, st, rb in json.loads(sys.argv[1]):\n"
    "    out.append(str(state_dir_for(Path(p), name, st, rb)))\nprint(json.dumps(out))\n"
)


def q7(h: W) -> Result:
    pred = (
        "probes.py --runs 10 35/35, streak_probes.py --allow-code-diff 32/32, exclude_probes.py "
        "12/12, impact_probes.py --runs 10 12/12; pytest tests/contracts tests/workspace tests/mcp "
        "tests/studio 0 failures; dogfood/studio/check.sh PASS (422 + R96/R97's steps); "
        "restart_drill.py A B C F; on the Q1 workspace workspace show|validate|impact, contracts "
        "export|check|impact (text, --json) and the pin lock byte-identical to 33d8de1; "
        "state_dir_for paths equal"
    )
    ns, jc = h.ns, h.jc
    dst = ns.out / "Q7-regression"
    dst.mkdir(parents=True, exist_ok=True)
    scratch = ns.scratch.resolve()
    env = clean_env()
    checks: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    head = subprocess.run(
        ["git", "-C", str(OLD_CHECKOUT), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "-C", str(OLD_CHECKOUT), "diff", "--stat", OLD_HEAD, "--", "lattice/"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    checks["old_checkout_head_is_33d8de1"] = head.startswith(OLD_HEAD)
    checks["old_checkout_lattice_equals_33d8de1"] = diff == ""
    extra["old_checkout"] = {"head": head, "lattice_diff_stat": diff}
    clone = ep.shared(jc)
    copies: dict[str, Path] = {}
    for tag in ("new", "old"):
        d = jc.work / f"Q7-{tag}"
        shutil.rmtree(d, ignore_errors=True)
        (d / "m").mkdir(parents=True)
        shutil.copytree(
            base.CI_BOT, d / "ci-bot", ignore=shutil.ignore_patterns("__pycache__", LOCK)
        )
        (d / "ci-bot" / LOCK).write_bytes(jc.clean_lock())
        ip.write_manifest(
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
    d2 = h.jref("D2")
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
        ["-m", "lattice.contracts", "impact", "scheduler", "--candidate", d2, "--json"],
        ["-m", "lattice.contracts", "impact", "scheduler", "--candidate", d2],
    ]
    rows = []
    for argv in cmds:
        res = {}
        for tag, root in (("new", REPO), ("old", OLD_CHECKOUT)):
            r = subprocess.run(
                [sys.executable, *argv, "--workspace", str(copies["new"])],
                cwd=jc.work,
                env=dict(env, PYTHONPATH=str(root)),
                capture_output=True,
                text=True,
                check=False,
            )
            o = r.stdout
            if "--json" in argv and ("check" in argv or "impact" in argv):
                o = json.dumps(ip.strip_elapsed(json.loads(o)), sort_keys=True)
            res[tag] = (r.returncode, o, r.stderr)
        rows.append(
            {"argv": " ".join(argv[1:]), "equal": res["new"] == res["old"], "rc": res["new"][0]}
        )
    pins = {}
    for tag, root in (("new", REPO), ("old", OLD_CHECKOUT)):
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
    rows.append(
        {
            "argv": "contracts pin ci-bot (lock bytes)",
            "equal": pins["new"] == pins["old"],
            "rc": pins["new"][0],
        }
    )
    checks["bytes_identical_to_33d8de1"] = all(r["equal"] for r in rows)
    checks["pin_lock_equals_golden"] = pins["new"][1] == jc.golden
    extra["byte_comparison"] = rows
    # state_dir_for paths: the drill project (a seed copy) and the test projects
    projects = []
    for i, (name, st, rb) in enumerate(
        [("notes", {}, {}), ("x", {"dir": "st"}, {}), ("y", {}, {"repo": "."})]
    ):
        p = jc.work / f"Q7-sd-{i}"
        shutil.rmtree(p, ignore_errors=True)
        seed_project(p, name)
        subprocess.run(["git", "-C", str(p), "init", "-q"], check=False)
        projects.append((str(p), name, st, rb))
    got = {}
    for tag, root in (("new", REPO), ("old", OLD_CHECKOUT)):
        r = subprocess.run(
            [sys.executable, "-c", STATE_PATHS, json.dumps(projects)],
            cwd=jc.work,
            env=dict(env, PYTHONPATH=str(root)),
            capture_output=True,
            text=True,
            check=False,
        )
        got[tag] = r.stdout.strip()
    checks["state_dir_for_paths_equal_33d8de1"] = got["new"] == got["old"] and got["new"] != ""
    extra["state_dir_for"] = got
    parts = [
        f"byte-identical {sum(r['equal'] for r in rows)}/{len(rows)}",
        f"state_dir_for equal {yn(checks['state_dir_for_paths_equal_33d8de1'])}",
    ]
    if ns.skip_regression:
        checks["regression_run"] = False
        parts.append("runners/pytest/check.sh/drill SKIPPED (--skip-regression)")
        return mk("Q7", pred, "; ".join(parts), [extra["byte_comparison"]], True, checks, extra)
    py = sys.executable
    jobs = {
        "probes": ([py, str(HERE / "probes.py"), "--runs", str(ns.runs)], 35),
        "streak_probes": (
            [py, str(HERE / "streak_probes.py"), "--runs", str(ns.runs), "--allow-code-diff"],
            32,
        ),
        "exclude_probes": ([py, str(HERE / "exclude_probes.py"), "--runs", str(ns.runs)], 12),
        # I8 of that runner compares with the main checkout at 67189af (its OLD_HEAD), which is
        # now at 33d8de1: its two head checks cannot hold, so the other 11 probes run
        "impact_probes": (
            [py, str(HERE / "impact_probes.py"), "--runs", str(ns.runs), "--only", IMPACT_ONLY],
            11,
        ),
    }
    for name, (argv, want) in jobs.items():
        res = ip.run_last_line(
            [
                *argv,
                "--scratch",
                str(scratch / f"q7-{name}"),
                "--out",
                str(scratch / f"q7-{name}-data"),
            ],
            env,
            REPO,
            dst,
            name,
        )
        s = scratch / f"q7-{name}-data" / "summary.json"
        if s.exists():
            shutil.copy(s, dst / f"{name}-summary.json")
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
    extra["pytest"] = {"rc": r.returncode, "last_line": last, "wall_s": round(time.time() - t0, 1)}
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
    checks["studio_check_pass"] = r.returncode == 0 and first.startswith("PASS: 0 findings")
    parts.append(f"check.sh {first}")
    t0 = time.time()
    r = subprocess.run(
        [py, "scripts/restart_drill.py", "A", "B", "C", "F"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    (dst / "restart-drill-stdout.txt").write_text(r.stdout[-4000:])
    extra["restart_drill"] = {
        "rc": r.returncode,
        "wall_s": round(time.time() - t0, 1),
        "tail": r.stdout.strip().splitlines()[-6:],
    }
    checks["restart_drill_A_B_C_F"] = r.returncode == 0
    parts.append(f"restart_drill rc {r.returncode}")
    return mk("Q7", pred, "; ".join(parts), [extra["byte_comparison"]], True, checks, extra)


# ---- main -------------------------------------------------------------------------------------

PROBES = {
    "Q0": q0,
    "Q1": q1,
    "Q2": q2,
    "Q3": q3,
    "Q4": q4,
    "Q5": q5,
    "Q6": q6,
    "T": t_probe,
    "Q7": q7,
}
ORDER = ["Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "T", "Q7", "S"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated probe ids")
    ap.add_argument("--keep", action="store_true", help="keep the scratch clones")
    ap.add_argument(
        "--merge",
        action="store_true",
        help="with --only: list the other probes from OUT/<ID>.json of an earlier invocation",
    )
    ap.add_argument(
        "--skip-regression",
        action="store_true",
        help="skip Q7's runners, pytest, check.sh and the drill (development only)",
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
    jc = ep.JCtx(ns.runs, scratch / "j", ns.out)
    sc = ep.SX(ns.runs, scratch / "s", ns.out)
    for c in (jc, sc):
        c.env["PYTHONPATH"] = str(REPO)
    h = W(ns, jc, sc)
    roots = {"$WORK_J": jc.root, "$WORK_S": sc.root, "$SCRATCH": scratch}
    repos = {
        "job-scheduler": ep.RealRepo(jc, base.PROVIDER, base.REF, " M uv.lock", [".venv"]),
        "streak": ep.RealRepo(sc, streak.STREAK, streak.REF, " M studio.toml", []),
    }
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
        (ns.out / f"{res.id}.json").write_text(
            json.dumps(ep.scrub(doc, roots), indent=1, default=str) + "\n"
        )

    try:
        for pid in ids:
            print(f"... {pid}", file=sys.stderr, flush=True)
            if pid == "S":
                res = ep.s_probe(repos)
                res.prediction = (
                    "the real job-scheduler (HEAD ef17cf8, ' M uv.lock') and Streak (HEAD "
                    "a0db033, ' M studio.toml') have the same HEAD and git status before and "
                    "after the run, and 0 files newer than the start marker"
                )
            else:
                res = PROBES[pid](h)
            results.append(res)
            write(res)
    finally:
        procs = base.leftover_processes(jc) + base.leftover_processes(sc)
        tmp_left = sorted(
            f"{t}/{p.name}"
            for t, c in (("j", jc), ("s", sc))
            for p in (c.tmp.iterdir() if c.tmp.exists() else [])
        )
        for c in (jc, sc):
            for dp, _dns, _fns in os.walk(c.root):
                with contextlib.suppress(OSError):
                    os.chmod(dp, 0o755)
        if not ns.keep:
            for c in (jc, sc):
                writable(c.root)
                shutil.rmtree(c.root, ignore_errors=True)
    merged: list[str] = []
    if ns.merge:
        for pid in ORDER:
            f = ns.out / f"{pid}.json"
            if pid not in ids and f.exists():
                d = json.loads(f.read_text())
                results.append(
                    Result(
                        pid,
                        d["prediction"],
                        d["observed"],
                        d["pass"],
                        [None] * d["n_runs"],
                        d["identical"],
                        d["checks"],
                        d["extra"],
                    )
                )
                merged.append(pid)
        results.sort(key=lambda r: ORDER.index(r.id))
    passed = sum(1 for r in results if r.passed)
    print(f"{'id':<5} | {'predicted':<60} | {'observed':<80} | pass")
    for r in results:
        print(f"{r.id:<5} | {r.prediction} | {r.observed} | {'PASS' if r.passed else 'FAIL'}")
    print(f"probes: {passed}/{len(results)} pass")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
    ).stdout.strip()
    summary = {
        "experiment": "2026-10-14-linked-questions",
        "command": "python dogfood/contracts/linkedq_probes.py " + " ".join(sys.argv[1:]),
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
        "merged_from_earlier_invocation": merged,
        "safety_before": {k: v.before for k, v in repos.items()},
        "leftover_processes": procs,
        "leftover_tmp_entries": tmp_left,
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
    }
    (ns.out / "summary.json").write_text(json.dumps(ep.scrub(summary, roots), indent=1) + "\n")
    return 0 if passed == len(results) and not procs and not tmp_left else 1


if __name__ == "__main__":
    sys.exit(main())
