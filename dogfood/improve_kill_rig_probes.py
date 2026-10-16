"""Probe runner for the events-log kill rig (round 2 of the improvement-loop hardening).

Implements ``docs/results/2026-10-16-improve-hardening-kill-rig-prereg.md`` (sections 2-4 and
Amendment 1): E-null, E-kill2, E-kill2s, E-kill-import, E-interleave-t, E-interleave-p,
E-split-stress and E-sync, on the tested tree (this checkout) and on the control tree
(``git archive a4ab4a1 lattice`` into the scratch directory: no ``flock``, no tail heal), and
the mutants IK1-IK10 (``--mutants``). The predictions and pass rules are copied from the
prereg; a probe that does not match is reported as a finding with its raw rows, never adapted.

    uv run python dogfood/improve_kill_rig_probes.py [--arm tested|control] [--scratch DIR] \
        [--out DIR] [--only ID ...] [--quick] [--mutants] [--mutant-ids IK1 ...]

The runner is stdlib only. Every call of ``lattice`` runs in a child process whose ``PYTHONPATH``
is the tree under test. A writer child first installs a syscall shim on ``os.write`` and
``os.fsync`` (module attributes; acts only on the descriptor whose ``(st_dev, st_ino)`` is the
log's) and only then imports ``lattice``. The shim makes the kill points deterministic by
program position (prereg 2.2): at the chosen append it stops the process with SIGSTOP at the
point (P0 before the write, P1 after a prefix of the line, P2 before the fsync, P3 after it, S
after a legal short write), the parent confirms ``WIFSTOPPED`` with ``waitpid(WUNTRACED)`` and
only then sends SIGKILL. Every stop is inside ``append_lines``'s lock. No LLM, no API key, no
network, no Docker. ``--out`` writes ``<id>.json`` per probe and ``summary.json``.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import py_compile
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
PLAN = ROOT / "docs/results/data/streak-plans-2026-10-11/p2-final.json"
DEFAULT_OUT = ROOT / "docs/results/data/2026-10-16-improve-kill-rig"
BASE_REF = "a4ab4a1"
PY = sys.executable
PADS = (80, 480, 4080, 8880)  # round 1's line sizes: ~200 / 600 / 4200 / 9000 bytes
LOCK_WORD = "waited"  # the lock-timeout warning: "events: waited 2.0 s for the lock on ..."
STOP_TO_KILL_LIMIT_S = 2.0  # prereg amendment 1, ruling 3: over this is a finding, not retried

PROBE_IDS = (
    "E-null",
    "E-kill2",
    "E-kill2s",
    "E-kill-import",
    "E-interleave-t",
    "E-interleave-p",
    "E-split-stress",
    "E-sync",
)
CONTROL_MUST_FAIL = ("E-kill2", "E-kill-import", "E-interleave-t", "E-interleave-p")
# E-kill2s: the control's failure is round 1's short-write defect (amendment 1, ruling 1)
COUNTS = {  # probe -> (full, quick)
    "E-null": (10, 2),
    "E-kill2": (50, 10),
    "E-kill2s": (10, 3),
    "E-kill-import": (20, 5),
    "E-interleave-t": (100, 20),
    "E-interleave-p": (100, 20),
    "E-split-stress": (10, 2),
    "E-sync": (1, 1),
}

POST_HOC_SCRIPT_CORRECTIONS: list[dict[str, str]] = [
    {
        "when": "after the tested and control record runs",
        "old": "the control arm wrote to <--out>/control (round 1's convention)",
        "new": "every arm writes to --out as given: the prereg's control command already passes "
        "--out .../control, so the old code nested control/control/; the record run's files "
        "were moved up one level by hand (contents untouched)",
    },
    {
        "when": "after the first --mutants run (aborted at IK7; log kept as "
        "run-mutants-aborted.txt, no mutants.json written)",
        "old": "IK4 hunk new text 'os.pread(fd, 1, size)  # MUTANT IK4' and IK7 hunk new text "
        "'fcntl.LOCK_SH | fcntl.LOCK_NB  # MUTANT IK7' (trailing comments in mid-expression)",
        "new": "no trailing comment in either hunk: the comment swallowed the rest of the line, so "
        "IK4 became 'torn = size > 0 and b\"\"' (no heal at all: IK2's semantics, not the "
        "intended always-a-prefix) and IK7 did not compile; the aborted run's IK4 verdict is "
        "void. make_mutant also now runs inside the per-mutant try (an uncompilable mutant "
        "is reported as an error, not a crash)",
    },
    {
        "when": "smoke trial (--quick, scratch out) before any recorded run",
        "old": "Shim._stop printed the stop line with print(...) to sys.stdout",
        "new": "print(..., file=sys.__stdout__): an import-plan worker redirects sys.stdout "
        "while the CLI runs, so the stop line went into a StringIO and the parent never saw it "
        "(a stopped worker hung the trial; killed by hand)",
    },
    {
        "when": "control smoke trial before any recorded run",
        "old": "E-split-stress / E-kill2 analysis indexed e['w'], e['seq'] of every parsed event",
        "new": "an event lacking them is counted as 'malformed_parseable' (a glued head with a "
        "foreign tail that still parses as JSON, a control-arm phenomenon) and excluded from "
        "the (w, t, seq) sets; the old code raised KeyError and reported the probe as ERROR",
    },
]
FINDINGS: list[str] = []
DECISIONS: list[str] = [
    "K is the 0-based index of the log write (the K-th line the shim sees, counting a "
    "continuation write of a short write as part of its line): K lines are complete before it, "
    "so acked == K and, for a stop in write K, m == K; the prereg's formulas need this reading.",
    "k (the cut of P1 and S) is drawn over the line WITHOUT its newline, 1..len(body)-1: with "
    "the prereg's literal 1..len(line)-1 the largest k would write a complete JSON object "
    "without its newline, which parses and is not a fragment. The heal newline, when present, "
    "is extra.",
    "A planned stop that never happens (the shim never reached its point, e.g. a mutant that "
    "removes the fsync the P2/P3 stop waits on), a kill that is not preceded by WIFSTOPPED, a "
    "worker that exits non-zero or whose shim count is below its appends is INVALID (reported, "
    "never a pass).",
    "The log file is created empty by the parent before the writers are spawned (the shim "
    "matches it by device and inode); the writers open it O_CREAT as usual.",
    "'max survivor append wall while a holder was stopped' is the largest append wall of any "
    "survivor in the run (the run contains every holder stop); it is not split per stop.",
    "E-interleave-t: the 0.2 s window starts after B has entered append (B signals first), so "
    "B's append wall is >= 0.2 s by construction when the lock holds; B's wall is measured in B.",
    "E-interleave-p: B's go-to-done wall is measured by the parent; 'B done in the window' means "
    "its done line arrived within 0.2 s of the go line.",
    "E-kill-import: K ~ U{1..25} is the 0-based write index, so K events are complete and the "
    "re-import writes 27-K events.",
    "The control is built by 'git archive a4ab4a1 lattice' into <scratch>/control (not the "
    "shared clone of round 1); the tested arm's workers run with PYTHONPATH=this checkout.",
]

# ---------------------------------------------------------------- the writer child's shim


class Shim:
    """Replaces ``os.write`` and ``os.fsync`` in a worker; acts only on the log's descriptor.

    ``plan`` keys: ``K`` (0-based line index) with ``point`` (P0/P1/P2/P3/S) and ``seed`` (for
    k); ``every_short`` (every line: a legal short write of half the line, no stop); ``split``
    (every line: two real writes 0.2 ms apart); ``wait`` (P1 waits on an Event instead of
    stopping: the thread test)."""

    def __init__(self, tag: str, jrn: str | None = None) -> None:
        self.ow, self.ofs, self.ofstat = os.write, os.fsync, os.fstat
        self.tag, self.jrn = tag, jrn
        self.mu = threading.Lock()
        self.ids: set[tuple[int, int]] = set()
        self.plan: dict[str, Any] = {}
        self.writes = self.fsyncs = 0
        self.cont = self.stop_pending = False
        self.k_last = 0
        self.trace: list[str] = []
        os.write, os.fsync = self._write, self._fsync  # type: ignore[assignment]

    def arm(self, path: str, plan: dict[str, Any]) -> None:
        st = os.stat(path)
        self.ids = {(st.st_dev, st.st_ino)}
        self.plan = plan
        self.writes = self.fsyncs = 0
        self.cont = self.stop_pending = False
        self.trace = []

    def _is_log(self, fd: int) -> bool:
        try:
            st = self.ofstat(fd)
        except OSError:
            return False
        return (st.st_dev, st.st_ino) in self.ids

    @staticmethod
    def cut(buf: bytes, seed: int | None, half: bool = False) -> tuple[int, int]:
        """(heal prefix length, k): the first ``pre + k`` bytes are the cut of the line."""
        pre = 1 if buf[:1] == b"\n" else 0
        blen = len(buf) - pre - (1 if buf.endswith(b"\n") else 0)
        if blen < 2:
            return pre, max(1, blen)
        return pre, (blen // 2 if half else random.Random(seed).randint(1, blen - 1))

    def _journal(self, buf: bytes) -> None:
        if self.jrn:
            with open(self.jrn, "wb") as f:
                f.write(buf)

    def _stop(self, point: str, k: int = 0) -> None:
        # sys.__stdout__: an import worker redirects sys.stdout while the CLI runs
        print(
            f"stop {self.tag} {self.plan.get('K', -1)} {point} {k}", file=sys.__stdout__, flush=True
        )
        os.kill(os.getpid(), signal.SIGSTOP)

    def _put_all(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[self.ow(fd, view) :]

    def _write(self, fd: int, data: Any) -> int:
        if not self._is_log(fd):
            return self.ow(fd, data)
        buf = bytes(data)
        p = self.plan
        if p.get("watch") and threading.get_ident() != p.get("a_tid") and not p["seen"].is_set():
            p["seen_at"] = time.monotonic()
            p["seen"].set()
        with self.mu:
            self.trace.append("W")
            cont, self.cont = self.cont, False
            idx = -1
            if not cont:
                idx = self.writes
                self.writes += 1
        if cont:  # the continuation of a short write this shim returned
            if self.stop_pending:
                self.stop_pending = False
                self._stop("S", self.k_last)
            return self.ow(fd, buf)
        if p.get("split") and len(buf) > 1:
            half = len(buf) // 2
            self._put_all(fd, buf[:half])
            time.sleep(0.0002)
            self._put_all(fd, buf[half:])
            return len(buf)
        if p.get("every_short") and len(buf) > 2:
            pre, k = self.cut(buf, None, half=True)
            n = self.ow(fd, buf[: pre + k])
            self.cont = True
            return n
        if idx == p.get("K"):
            point = p["point"]
            if point == "P0":
                self._stop("P0")
            elif point in ("P1", "S"):
                pre, k = self.cut(buf, p.get("seed"))
                self._journal(buf)
                p["k_used"] = k
                n = self.ow(fd, buf[: pre + k])
                if point == "S":
                    self.cont, self.stop_pending, self.k_last = True, True, k
                    return n
                if p.get("wait"):
                    p["mid"].set()
                    p["release"].wait(30)
                else:
                    self._stop("P1", k)
                self._put_all(fd, buf[pre + k :])  # only reached after a SIGCONT / release
                return len(buf)
        return self.ow(fd, buf)

    def _fsync(self, fd: int) -> None:
        if not self._is_log(fd):
            return self.ofs(fd)
        with self.mu:
            self.trace.append("F")
            self.cont = False
            idx = self.fsyncs
            self.fsyncs += 1
            stop, self.stop_pending = self.stop_pending, False
        if stop:  # a4ab4a1 ignores the short count: its next log syscall is the fsync
            self._stop("S", self.k_last)
        p = self.plan
        if idx == p.get("K") and p.get("point") == "P2":
            self._stop("P2")
        out = self.ofs(fd)
        if idx == p.get("K") and p.get("point") == "P3":
            self._stop("P3")
        return out


# ---------------------------------------------------------------- workers (child processes)


def _wait_go(go: str) -> None:
    while not os.path.exists(go):
        time.sleep(0.0003)


def _pad(seq: int) -> str:
    return "p" * PADS[seq % 4]


def w_kill(spec: dict[str, Any]) -> None:
    """One writer of E-null / E-kill2 / E-kill2s: n appends after the go file, an ack per append."""
    sh = Shim(str(spec["w"]), spec["jrn"])
    sh.arm(spec["path"], spec["plan"])
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    ack = os.open(spec["ack"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    print("ready", flush=True)
    _wait_go(spec["go"])
    walls: list[float] = []
    nones = 0
    for seq in range(spec["n"]):
        t0 = time.perf_counter()
        ev = log.append("finding_class", signal_key="D:kill", count=1, w=spec["w"], seq=seq,
                        pad=_pad(seq))  # fmt: skip
        walls.append(time.perf_counter() - t0)
        nones += ev is None
        sh.ow(ack, f"{seq}\n".encode())
    print("done " + json.dumps({
        "warnings": log.warnings, "intercepted": sh.writes, "fsyncs": sh.fsyncs, "nones": nones,
        "max_append_s": max(walls), "lattice": lattice.__file__,
    }), flush=True)  # fmt: skip


def w_append1(spec: dict[str, Any]) -> None:
    """A fresh process: one append (no shim), timed."""
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    print("ready", flush=True)
    t0 = time.monotonic()
    ev = log.append("human", what="mine", actor="owner")
    took = time.monotonic() - t0
    print("done " + json.dumps({"elapsed": took, "event": ev, "warnings": log.warnings,
                                "lattice": lattice.__file__}), flush=True)  # fmt: skip


def _run_import(plan: str, proj: str, state: str) -> tuple[Any, str]:
    from lattice.improve.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc: Any = main(["import-plan", plan, "--project", proj, "--state", state])
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue().strip() + (" ERR " + err.getvalue().strip() if err.getvalue() else "")


def w_import_kill(spec: dict[str, Any]) -> None:
    """An import-plan with the shim armed on the state dir's log (P1 at write K)."""
    sh = Shim("import", spec["jrn"])
    sh.arm(spec["log"], spec["plan"])
    import lattice

    print("ready", flush=True)
    _wait_go(spec["go"])
    rc, out = _run_import(spec["record"], spec["proj"], spec["state"])
    print("done " + json.dumps({"rc": rc, "out": out, "intercepted": sh.writes,
                                "lattice": lattice.__file__}), flush=True)  # fmt: skip


def w_import_plain(spec: dict[str, Any]) -> None:
    import lattice

    print("ready", flush=True)
    rc, out = _run_import(spec["record"], spec["proj"], spec["state"])
    print("done " + json.dumps({"rc": rc, "out": out, "lattice": lattice.__file__}), flush=True)


def _ilv_event(who: str, pad: int) -> dict[str, Any]:
    return {"signal_key": f"D:ilv:{who}", "count": 1, "w": who, "pad": "p" * pad}


def w_ilv_t(spec: dict[str, Any]) -> None:
    """E-interleave-t: `n` trials; two EventLog objects of one process on one path."""
    sh = Shim("t")
    import lattice
    from lattice.improve.events import EventLog

    rows = []
    for i in range(spec["n"]):
        path = f"{spec['dir']}/t{spec['pad']}-{i}.jsonl"
        Path(path).touch()
        plan: dict[str, Any] = {"K": 0, "point": "P1", "seed": spec["seed0"] + i, "wait": True,
                                "watch": True, "a_tid": None, "mid": threading.Event(),
                                "release": threading.Event(), "seen": threading.Event(),
                                "seen_at": None}  # fmt: skip
        sh.arm(path, plan)
        la, lb = EventLog(path, "notes"), EventLog(path, "notes")
        res: dict[str, Any] = {}

        def run_a(la: EventLog = la, plan: dict[str, Any] = plan, pad: int = spec["pad"]) -> None:
            plan["a_tid"] = threading.get_ident()
            la.append("finding_class", **_ilv_event("A", pad))

        entered = threading.Event()

        def run_b(
            lb: EventLog = lb,
            res: dict[str, Any] = res,
            pad: int = spec["pad"],
            entered: threading.Event = entered,
        ) -> None:
            res["b_t0"] = time.monotonic()
            entered.set()
            lb.append("finding_class", **_ilv_event("B", pad))
            res["b_elapsed"] = time.monotonic() - res["b_t0"]

        ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
        ta.start()
        mid = plan["mid"].wait(10)
        tb.start()
        entered.wait(10)
        seen = plan["seen"].wait(0.2)
        plan["release"].set()
        ta.join(30)
        tb.join(30)
        rows.append({
            "i": i, "pad": spec["pad"], "seed": plan["seed"], "k": plan.get("k_used"),
            "mid": mid, "b_seen": seen,
            "b_seen_after_s": (plan["seen_at"] - res["b_t0"]) if plan["seen_at"] else None,
            "b_elapsed": res.get("b_elapsed"), "a_writes": sh.writes,
            "warnings": la.warnings + lb.warnings, "path": path,
        })  # fmt: skip
    print("done " + json.dumps({"rows": rows, "lattice": lattice.__file__}), flush=True)


def w_ilv_pa(spec: dict[str, Any]) -> None:
    """E-interleave-p, process A: P1-stops itself in its first append, completes on SIGCONT."""
    sh = Shim("A", spec["jrn"])
    sh.arm(spec["path"], spec["plan"])
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    print("ready", flush=True)
    sys.stdin.readline()
    log.append("finding_class", **_ilv_event("A", spec["pad"]))
    print("done " + json.dumps({"warnings": log.warnings, "intercepted": sh.writes,
                                "lattice": lattice.__file__}), flush=True)  # fmt: skip


def w_ilv_pb(spec: dict[str, Any]) -> None:
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    print("ready", flush=True)
    sys.stdin.readline()
    t0 = time.monotonic()
    log.append("finding_class", **_ilv_event("B", spec["pad"]))
    print("done " + json.dumps({"elapsed": time.monotonic() - t0, "warnings": log.warnings,
                                "lattice": lattice.__file__}), flush=True)  # fmt: skip


def w_split(spec: dict[str, Any]) -> None:
    """E-split-stress: round 1's E-stress worker (2 threads) with every log write split in two."""
    sh = Shim(str(spec["w"]))
    sh.arm(spec["path"], {"split": True})
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    print("ready", flush=True)
    _wait_go(spec["go"])

    def run(t: int) -> None:
        for seq in range(spec["n"]):
            log.append("finding_class", signal_key="D:stress", count=1, w=spec["w"], t=t,
                       seq=seq, pad=_pad(seq))  # fmt: skip

    ts = [threading.Thread(target=run, args=(t,)) for t in range(spec["threads"])]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print("done " + json.dumps({"warnings": log.warnings, "intercepted": sh.writes,
                                "lattice": lattice.__file__}), flush=True)  # fmt: skip


def w_sync(spec: dict[str, Any]) -> None:
    sh = Shim("sync")
    sh.arm(spec["path"], {})
    import lattice
    from lattice.improve.events import EventLog

    log = EventLog(spec["path"], "notes")
    traces: collections.Counter[str] = collections.Counter()
    for seq in range(spec["n"]):
        sh.trace = []
        log.append("finding_class", signal_key="D:sync", count=1, seq=seq, pad=_pad(seq))
        traces["".join(sh.trace)] += 1
    print("done " + json.dumps({"traces": traces, "intercepted": sh.writes,
                                "warnings": log.warnings, "lattice": lattice.__file__}),
          flush=True)  # fmt: skip


WORKERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "kill": w_kill, "append1": w_append1, "import_kill": w_import_kill,
    "import_plain": w_import_plain, "ilv_t": w_ilv_t, "ilv_pa": w_ilv_pa, "ilv_pb": w_ilv_pb,
    "split": w_split, "sync": w_sync,
}  # fmt: skip

# ---------------------------------------------------------------- the parent: processes

STATE: dict[str, Any] = {"scratch": None, "n": 0, "tree": ROOT, "control": None, "arm": "tested"}


def env_for(tree: Path) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", COLUMNS="100")
    return e


def workdir(label: str) -> Path:
    STATE["n"] += 1
    d = Path(STATE["scratch"]) / f"{STATE['n']:04d}-{label}"
    d.mkdir(parents=True)
    return d


class Proc:
    """A worker child with a reader thread; ``autokill``: on its ``stop`` line confirm
    ``WIFSTOPPED`` with ``waitpid(WUNTRACED)`` and only then SIGKILL it."""

    def __init__(self, d: Path, label: str, name: str, spec: dict[str, Any], *,
                 stdin: bool = False, autokill: bool = False) -> None:  # fmt: skip
        self.label, self.autokill = label, autokill
        self.errf = open(d / f"err-{label}", "w")  # noqa: SIM115
        self.p = subprocess.Popen(
            [PY, str(SCRIPT), "--worker", name, json.dumps(spec)],
            env=env_for(STATE["tree"]), cwd=STATE["scratch"], text=True,
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=self.errf,
        )  # fmt: skip
        self.ev = {w: threading.Event() for w in ("ready", "stop", "done", "eof")}
        self.at: dict[str, float] = {}
        self.lines: list[str] = []
        self.stopped = False
        self.rc: int | None = None
        self.latency: float | None = None
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()

    def _read(self) -> None:
        assert self.p.stdout is not None
        for raw in self.p.stdout:
            now = time.monotonic()
            line = raw.rstrip("\n")
            word = line.split(" ", 1)[0]
            self.lines.append(line)
            if word == "stop" and self.autokill:
                self.stop_kill(now)
            if word in self.ev:
                self.at.setdefault(word, now)
                self.ev[word].set()
        self.ev["eof"].set()

    def confirm_stopped(self) -> bool:
        """waitpid(WUNTRACED): did the child stop (and not exit)?"""
        _, st = os.waitpid(self.p.pid, os.WUNTRACED)
        if os.WIFSTOPPED(st):
            self.stopped = True
            return True
        self._reaped(st)
        return False

    def _reaped(self, st: int) -> None:
        self.rc = -os.WTERMSIG(st) if os.WIFSIGNALED(st) else os.WEXITSTATUS(st)
        self.p.returncode = self.rc  # Popen must not wait for it again

    def stop_kill(self, t_line: float) -> None:
        try:
            if not self.confirm_stopped():
                return
            os.kill(self.p.pid, signal.SIGKILL)
            t_kill = time.monotonic()
            _, st = os.waitpid(self.p.pid, 0)
            self._reaped(st)
            self.latency = t_kill - t_line
        except ChildProcessError:
            pass

    def say(self, text: str) -> None:
        assert self.p.stdin is not None
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()

    def wait(self, word: str, timeout: float) -> bool:
        return self.ev[word].wait(timeout)

    def stop_fields(self) -> list[str] | None:
        for ln in self.lines:
            if ln.startswith("stop "):
                return ln.split()
        return None

    def done(self) -> dict[str, Any] | None:
        for ln in self.lines:
            if ln.startswith("done "):
                return json.loads(ln[5:])
        return None

    def finish(self, timeout: float = 60.0) -> bool:
        """Wait for the child to end; a hung child is killed (and the run is INVALID)."""
        ok = self.ev["eof"].wait(timeout)
        if not ok:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.p.pid, signal.SIGCONT)
                os.kill(self.p.pid, signal.SIGKILL)
            self.ev["eof"].wait(10)
        with contextlib.suppress(Exception):
            self.p.wait(timeout=10)
        self.rc = self.p.returncode if self.rc is None else self.rc
        self.errf.close()
        return ok


def scan(path: Path) -> dict[str, Any]:
    """The raw view of a log: events (file order), unparseable lines (bytes), blank count."""
    data = path.read_bytes() if path.exists() else b""
    pieces = data.split(b"\n")
    tail_open = bool(data) and not data.endswith(b"\n")
    if pieces and pieces[-1] == b"":
        pieces.pop()
    blank, bad, evs = 0, [], []
    for piece in pieces:
        if not piece.strip():
            blank += 1
            continue
        try:
            e = json.loads(piece)
        except (json.JSONDecodeError, UnicodeDecodeError):
            bad.append(piece)
            continue
        if isinstance(e, dict) and "kind" in e:
            evs.append(e)
        else:
            bad.append(piece)
    return {"lines": len(pieces), "blank": blank, "bad": bad, "events": evs,
            "tail_open": tail_open, "bytes": len(data)}  # fmt: skip


def result(pid: str, predicted: str, observed: str, ok: bool, **raw: Any) -> dict[str, Any]:
    return {"id": pid, "predicted": predicted, "observed": observed, "pass": bool(ok), **raw}


def lock_warnings(ws: list[str]) -> int:
    return sum(LOCK_WORD in w for w in ws)


def _is_tree(done: dict[str, Any] | None) -> bool:
    return bool(done) and str(done["lattice"]).startswith(str(STATE["tree"]))  # type: ignore[index]


def fragment_of(jrn: Path, k: int) -> bytes:
    buf = jrn.read_bytes()
    pre = 1 if buf[:1] == b"\n" else 0
    return buf[pre : pre + k]


# ---------------------------------------------------------------- E-null / E-kill2 / E-kill2s


def make_plans(variant: str, r: int) -> list[dict[str, Any]]:
    rnd = random.Random(r)
    plans: list[dict[str, Any]] = [{} for _ in range(8)]
    if variant == "kill2":
        for w in range(4):
            plans[w] = {"K": rnd.randint(20, 180), "point": "P1", "seed": r * 100 + w}
        plans[4] = {"K": rnd.randint(20, 180), "point": ("P0", "P2", "P3")[r % 3]}
    elif variant == "kill2s":
        for w in range(4):
            plans[w] = {"K": rnd.randint(20, 180), "point": "S", "seed": r * 100 + w}
        for w in range(4, 8):
            plans[w] = {"every_short": True}
    return plans


def run_kill(variant: str, r: int, n: int = 200) -> dict[str, Any]:
    d = workdir(variant)
    path, go = d / "events.jsonl", d / "go"
    path.touch()
    plans = make_plans(variant, r)
    procs = []
    for w in range(8):
        spec = {"path": str(path), "go": str(go), "w": w, "n": n, "plan": plans[w],
                "jrn": str(d / f"jrn-{w}"), "ack": str(d / f"ack-{w}")}  # fmt: skip
        procs.append(Proc(d, f"w{w}", "kill", spec, autokill="point" in plans[w]))
    invalid: list[str] = []
    for w, p in enumerate(procs):
        if not p.wait("ready", 60):
            invalid.append(f"writer {w} never said ready")
    go.write_text("go")
    for w, p in enumerate(procs):
        if not p.finish(120):
            invalid.append(f"writer {w} hung (killed after 120 s)")
    fresh = Proc(d, "fresh", "append1", {"path": str(path)})
    fresh.finish(60)
    fd = fresh.done() or {"elapsed": 99.0, "event": None, "warnings": [], "lattice": ""}
    if not _is_tree(fresh.done()):
        invalid.append("fresh process did not import lattice from the tree under test")

    lg = scan(path)
    per: dict[int, list[int]] = {}
    ids: list[Any] = []
    malformed = 0
    for e in lg["events"]:
        ids.append(e.get("id"))
        if e.get("kind") == "finding_class" and "w" in e and "seq" in e:
            per.setdefault(int(e["w"]), []).append(int(e["seq"]))
        elif e.get("kind") == "finding_class":
            malformed += 1  # a spliced line that still parses (a glued head with a foreign tail)
    expect_frags: collections.Counter[bytes] = collections.Counter()
    writers, stops = [], []
    lost = dup = 0
    warns = list(fd["warnings"])
    c2 = c3 = True
    max_append = 0.0
    for w, p in enumerate(procs):
        plan = plans[w]
        seqs = per.get(w, [])
        m = len(seqs)
        ackf = d / f"ack-{w}"
        acked = len(ackf.read_text().split()) if ackf.exists() else 0
        dup += m - len(set(seqs))
        dn = p.done()
        if "point" in plan:
            sf = p.stop_fields()
            point, K = plan["point"], plan["K"]
            if sf is None or not p.stopped or p.rc != -signal.SIGKILL:
                invalid.append(
                    f"writer {w}: planned {point} stop at {K} not confirmed "
                    f"(stop line {sf}, stopped {p.stopped}, rc {p.rc})"
                )
            elif point in ("P1", "S"):
                expect_frags[fragment_of(d / f"jrn-{w}", int(sf[4]))] += 1
            if sf is not None:
                stops.append({"w": w, "point": point, "K": K, "k": int(sf[4]),
                              "stopped": p.stopped, "rc": p.rc,
                              "latency_ms": round((p.latency or -1) * 1000, 2)})  # fmt: skip
            want_m = K + (1 if point in ("P2", "P3") else 0)
            good = seqs == list(range(m)) and acked == K and m == want_m
            c2 = c2 and good
            lost += len(set(range(want_m)) - set(seqs))
            writers.append({"w": w, "role": point, "K": K, "acked": acked, "m": m,
                            "want_m": want_m, "ok": good})  # fmt: skip
        else:
            if dn is None or p.rc != 0:
                invalid.append(f"writer {w}: no done line / rc {p.rc}")
            else:
                warns += dn["warnings"]
                max_append = max(max_append, dn["max_append_s"])
                if dn["intercepted"] < n:
                    invalid.append(f"writer {w}: shim saw {dn['intercepted']} < {n} log writes")
                if not _is_tree(dn):
                    invalid.append(f"writer {w}: lattice not imported from the tree under test")
            good = seqs == list(range(n))
            c3 = c3 and good
            lost += len(set(range(n)) - set(seqs))
            writers.append({"w": w, "role": "survivor", "acked": acked, "m": m, "ok": good})
    frag_total = sum(expect_frags.values())
    matched = sum((collections.Counter(lg["bad"]) & expect_frags).values())
    glued = len(lg["bad"]) - matched
    dup_ids = len(ids) - len(set(ids))
    fresh_present = any(e.get("what") == "mine" for e in lg["events"]) and fd["event"] is not None
    if variant == "null":
        c4 = len(lg["bad"]) == 0 and lg["blank"] == 0
        c4 = c4 and len(lg["events"]) == 8 * n + (1 if fresh_present else 0)
        c5 = fresh_present
        c2 = True
        kills = confirmed = 0
        c4 = c4 and dup_ids == 0 and dup == 0
    else:
        c4 = (len(lg["bad"]) == frag_total == matched and lg["blank"] == 0 and dup_ids == 0
              and dup == 0)  # fmt: skip
        c5 = not lg["tail_open"] and fresh_present and fd["elapsed"] < 0.5
        kills = sum(1 for pl in plans if "point" in pl)
        confirmed = sum(1 for s in stops if s["stopped"] and s["rc"] == -signal.SIGKILL)
    c6 = lock_warnings(warns) == 0
    lat = [s["latency_ms"] for s in stops]
    slow = [s for s in stops if s["latency_ms"] > STOP_TO_KILL_LIMIT_S * 1000]
    row = {
        "seed": r, "plans": plans, "stops": stops, "writers": writers, "kills": kills,
        "kills_confirmed": confirmed, "unparseable": len(lg["bad"]),
        "fragments_expected": frag_total,
        "fragments_matched": matched, "glued": glued, "blank": lg["blank"],
        "tail_open": lg["tail_open"], "events": len(lg["events"]), "lost": lost,
        "duplicated": dup, "dup_ids": dup_ids, "malformed_parseable": malformed,
        "fresh_present": fresh_present,
        "fresh_s": round(fd["elapsed"], 4), "lock_warnings": lock_warnings(warns),
        "intercepted": sum((p.done() or {}).get("intercepted", 0) for p in procs),
        "max_latency_ms": max(lat) if lat else None,
        "max_survivor_append_s": round(max_append, 4), "slow_kills": slow, "invalid": invalid,
        "clauses": {"c2_killed": c2, "c3_survivors": c3, "c4_lines": c4, "c5_tail_fresh": c5,
                    "c6_no_lock_warning": c6},
    }  # fmt: skip
    row["pass"] = not invalid and c2 and c3 and c4 and c5 and c6 and not slow
    if variant != "null":
        row["pass"] = row["pass"] and confirmed == kills
    shutil.rmtree(d, ignore_errors=True)
    return row


def _tot(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(r[key]) for r in rows)


def kill_probe(pid: str, variant: str, runs: int, pred: str) -> dict[str, Any]:
    rows = [run_kill(variant, r) for r in range(runs)]
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r["invalid"]) for r in rows)
    lat = [r["max_latency_ms"] for r in rows if r["max_latency_ms"] is not None]
    mx = [r["max_survivor_append_s"] for r in rows]
    obs = (
        f"{n}/{runs} runs pass; INVALID {inv}; kills confirmed {_tot(rows, 'kills_confirmed')}/"
        f"{_tot(rows, 'kills')}; unparseable {_tot(rows, 'unparseable')} (matching a journal "
        f"prefix {_tot(rows, 'fragments_matched')} of {_tot(rows, 'fragments_expected')}), glued "
        f"{_tot(rows, 'glued')}, spliced-but-parseable {_tot(rows, 'malformed_parseable')}, "
        f"blank {_tot(rows, 'blank')}; lost events {_tot(rows, 'lost')} "
        f"(runs with a loss {sum(r['lost'] > 0 for r in rows)}), duplicated "
        f"{_tot(rows, 'duplicated')}; lock warnings {_tot(rows, 'lock_warnings')}; shim writes "
        f"seen by survivors {_tot(rows, 'intercepted')}; stop-to-kill max "
        f"{max(lat) if lat else 'n/a'} ms; max survivor append {max(mx)} s"
    )
    res = result(pid, pred, obs, n == runs and inv == 0, runs=rows, invalid=inv)
    keys = (
        "kills", "kills_confirmed", "unparseable", "fragments_expected", "fragments_matched",
        "malformed_parseable", "glued", "blank", "lost", "duplicated", "dup_ids",
        "lock_warnings", "intercepted",
    )  # fmt: skip
    res["totals"] = {k: _tot(rows, k) for k in keys}
    return res


def probe_null(runs: int) -> dict[str, Any]:
    pred = (f"{runs}/{runs}: 1600 events, 0 unparseable, 0 blank, intercepted writes = 1600 "
            "(both arms)")  # fmt: skip
    res = kill_probe("E-null", "null", runs, pred)
    return res


def probe_kill2(runs: int) -> dict[str, Any]:
    pred = (f"tested {runs}/{runs}: 5/5 kills confirmed per run, 4 unparseable = the 4 P1 journal "
            "prefixes, 0 lost/duplicated, fresh append present, 0 lock warnings, stop-to-kill "
            "< 50 ms; control 0/N: >= 1 lost event in every run (3-4), >= 150 lost in total, "
            "3-4 unparseable per run, each glued")  # fmt: skip
    return kill_probe("E-kill2", "kill2", runs, pred)


def probe_kill2s(runs: int) -> dict[str, Any]:
    pred = (f"tested {runs}/{runs}: 4 kills per run, 4 fragments = journal prefixes, survivors "
            "complete; control 0/N (round 1's short-write defect: ~800 of 800 survivor lines "
            "lost or glued per run)")  # fmt: skip
    return kill_probe("E-kill2s", "kill2s", runs, pred)


# ---------------------------------------------------------------- E-kill-import


def run_kill_import(r: int, ref_ids: list[str]) -> dict[str, Any]:
    d = workdir("killimport")
    proj, state = d / "proj", d / "state"
    proj.mkdir()
    state.mkdir()
    log = state / "events.jsonl"
    log.touch()
    record = d / "p2-final.json"
    shutil.copy(PLAN, record)
    K = random.Random(r).randint(1, 25)
    total = len(ref_ids)
    plan = {"K": K, "point": "P1", "seed": r}
    go = d / "go"
    spec = {"log": str(log), "plan": plan, "jrn": str(d / "jrn"), "go": str(go),
            "record": str(record), "proj": str(proj), "state": str(state)}  # fmt: skip
    p = Proc(d, "imp", "import_kill", spec, autokill=True)
    invalid: list[str] = []
    if not p.wait("ready", 60):
        invalid.append("importer never said ready")
    go.write_text("go")
    p.finish(120)
    sf = p.stop_fields()
    if sf is None or not p.stopped or p.rc != -signal.SIGKILL:
        invalid.append(f"planned P1 stop at write {K} not confirmed (stop {sf}, rc {p.rc})")
    frag = fragment_of(d / "jrn", int(sf[4])) if sf and (d / "jrn").exists() else b""
    fresh = Proc(d, "fresh", "import_plain", spec)
    fresh.finish(120)
    fd = fresh.done()
    if fd is None:
        invalid.append("re-import: no done line")
        fd = {"rc": None, "out": "", "lattice": ""}
    if not _is_tree(fd):
        invalid.append("re-import did not import lattice from the tree under test")
    lg = scan(log)
    got = [e.get("id") for e in lg["events"]]
    printed = f"{total - K} event(s) written"
    ok_ids = sorted(got) == sorted(ref_ids) and len(got) == len(set(got))
    ok_frag = len(lg["bad"]) == 1 and lg["bad"][0] == frag and lg["blank"] == 0
    ok_re = fd["rc"] == 0 and printed in fd["out"]
    row = {
        "seed": r, "K": K, "k": int(sf[4]) if sf else None, "events": len(got),
        "unique": len(set(got)), "missing": len(set(ref_ids) - set(got)),
        "order_equal": got == ref_ids, "unparseable": len(lg["bad"]),
        "fragment_equals_journal_prefix": ok_frag, "blank": lg["blank"],
        "tail_open": lg["tail_open"], "reimport_rc": fd["rc"], "reimport_out": fd["out"][:200],
        "wanted_out": printed, "latency_ms": round((p.latency or -1) * 1000, 2),
        "invalid": invalid, "ids_ok": ok_ids, "reimport_ok": ok_re,
    }  # fmt: skip
    row["pass"] = not invalid and ok_ids and ok_frag and ok_re and not lg["tail_open"]
    shutil.rmtree(d, ignore_errors=True)
    return row


def reference_ids() -> list[str]:
    d = workdir("importref")
    proj, state = d / "proj", d / "state"
    proj.mkdir()
    record = d / "p2-final.json"
    shutil.copy(PLAN, record)
    p = Proc(d, "ref", "import_plain",
             {"record": str(record), "proj": str(proj), "state": str(state)})  # fmt: skip
    p.finish(120)
    ids = [e.get("id") for e in scan(state / "events.jsonl")["events"]]
    shutil.rmtree(d, ignore_errors=True)
    return ids


def probe_kill_import(runs: int) -> dict[str, Any]:
    pred = (
        f"tested {runs}/{runs}: parseable = the 27 clean-import ids once each, one "
        "unparseable line = the journal prefix, re-import prints '<27-K> event(s) written' "
        "rc 0; control 0/N (event K glued to its fragment: 26 events after the re-import)"
    )
    ref = reference_ids()
    if len(ref) != 27:
        return result("E-kill-import", pred, f"INVALID: reference import wrote {len(ref)} events",
                      False, invalid=1)  # fmt: skip
    rows = [run_kill_import(r, ref) for r in range(runs)]
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r["invalid"]) for r in rows)
    obs = (
        f"{n}/{runs} runs pass; INVALID {inv}; parseable events "
        f"{sorted({r['events'] for r in rows})} (missing {_tot(rows, 'missing')}); "
        f"unparseable {_tot(rows, 'unparseable')} (equal to the journal prefix "
        f"{sum(r['fragment_equals_journal_prefix'] for r in rows)}); re-import line as wanted "
        f"{sum(r['reimport_ok'] for r in rows)}/{runs}"
    )
    return result("E-kill-import", pred, obs, n == runs and inv == 0, runs=rows, invalid=inv)


# ---------------------------------------------------------------- E-interleave-t / -p


def probe_interleave_t(trials: int) -> dict[str, Any]:
    pred = (f"tested {trials}/{trials}: B's write not seen in the 0.2 s window, file = A's line "
            "then B's, 0 unparseable, 0 blank, B append >= 0.2 s, 0 warnings; control: B's "
            "write seen within 5 ms, 'A1 B / A2' = 2 unparseable lines, both events lost, "
            f"{trials}/{trials}")  # fmt: skip
    d = workdir("ilvt")
    per = max(1, trials // 4)
    procs = [Proc(d, f"t{i}", "ilv_t", {"dir": str(d), "pad": pad, "n": per, "seed0": 1000 * i})
             for i, pad in enumerate(PADS)]  # fmt: skip
    rows: list[dict[str, Any]] = []
    for p in procs:
        p.finish(600)
        dn = p.done()
        if dn is None:
            rows.append({"pass": False, "invalid": [f"worker {p.label}: no done line rc {p.rc}"]})
            continue
        for tr in dn["rows"]:
            lg = scan(Path(tr["path"]))
            invalid = []
            if not tr["mid"]:
                invalid.append("A never reached its mid-write point")
            if not _is_tree(dn):
                invalid.append("lattice not imported from the tree under test")
            who = [e.get("w") for e in lg["events"]]
            tr.update(
                unparseable=len(lg["bad"]), blank=lg["blank"], tail_open=lg["tail_open"],
                order=who, lost=2 - len(lg["events"]), invalid=invalid,
                glued=len(lg["bad"]), lock_warnings=lock_warnings(tr["warnings"]),
            )  # fmt: skip
            tr["pass"] = (
                not invalid and not tr["b_seen"] and who == ["A", "B"] and not lg["bad"]
                and lg["blank"] == 0 and not lg["tail_open"] and (tr["b_elapsed"] or 0) >= 0.2
                and not tr["warnings"]
            )  # fmt: skip
            rows.append(tr)
    shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r.get("invalid")) for r in rows)
    seen = [r["b_seen_after_s"] for r in rows if r.get("b_seen_after_s") is not None]
    obs = (f"{n}/{len(rows)} trials pass; INVALID {inv}; B's write seen in the window "
           f"{sum(bool(r.get('b_seen')) for r in rows)}/{len(rows)}"
           + (f" (after {min(seen) * 1000:.2f}-{max(seen) * 1000:.2f} ms)" if seen else "")
           + f"; unparseable {_tot_get(rows, 'unparseable')}, blank {_tot_get(rows, 'blank')}, "
           f"events lost {_tot_get(rows, 'lost')}; warnings "
           f"{sum(len(r.get('warnings', [])) for r in rows)}; B append wall min "
           f"{min((r['b_elapsed'] for r in rows if r.get('b_elapsed') is not None), default=0):.3f}"
           " s")  # fmt: skip
    return result(
        "E-interleave-t",
        pred,
        obs,
        n == len(rows) and inv == 0 and len(rows) > 0,
        runs=rows,
        invalid=inv,
    )


def _tot_get(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(r.get(key, 0)) for r in rows)


def run_ilv_p(i: int) -> dict[str, Any]:
    d = workdir("ilvp")
    path = d / "events.jsonl"
    path.touch()
    pad = PADS[i % 4]
    spec = {"path": str(path), "pad": pad, "jrn": str(d / "jrn"),
            "plan": {"K": 0, "point": "P1", "seed": i}}  # fmt: skip
    a = Proc(d, "A", "ilv_pa", spec, stdin=True)
    b = Proc(d, "B", "ilv_pb", spec, stdin=True)
    invalid: list[str] = []
    if not (a.wait("ready", 60) and b.wait("ready", 60)):
        invalid.append("A or B never said ready")
    a.say("go")
    sf = None
    if a.wait("stop", 30):
        sf = a.stop_fields()
        if not a.confirm_stopped():
            invalid.append("A did not stop (waitpid WUNTRACED)")
    else:
        invalid.append("A never stopped mid-write")
    t_go = time.monotonic()
    b.say("go")
    b_in_window = b.wait("done", 0.2)
    window_s = time.monotonic() - t_go
    t_cont = time.monotonic()
    with contextlib.suppress(ProcessLookupError):
        os.kill(a.p.pid, signal.SIGCONT)
    a.finish(60)
    b.finish(60)
    da, db = a.done(), b.done()
    if da is None or db is None:
        invalid.append(f"missing done line (A rc {a.rc}, B rc {b.rc})")
        da = da or {"warnings": [], "lattice": ""}
        db = db or {"warnings": [], "elapsed": 0.0, "lattice": ""}
    elif not (_is_tree(da) and _is_tree(db)):
        invalid.append("lattice not imported from the tree under test")
    b_done_after_cont = b.at.get("done", 0.0) >= t_cont
    lg = scan(path)
    who = [e.get("w") for e in lg["events"]]
    warns = da["warnings"] + db["warnings"]
    row = {
        "trial": i, "pad": pad, "k": int(sf[4]) if sf else None, "b_done_in_window": b_in_window,
        "window_s": round(window_s, 4), "b_done_after_sigcont": b_done_after_cont,
        "b_wall_from_go_s": round(b.at.get("done", 0.0) - t_go, 4),
        "b_elapsed": db.get("elapsed"), "unparseable": len(lg["bad"]), "blank": lg["blank"],
        "tail_open": lg["tail_open"], "order": who, "lost": 2 - len(lg["events"]),
        "glued": len(lg["bad"]), "warnings": warns, "invalid": invalid,
        "lock_warnings": lock_warnings(warns),
    }  # fmt: skip
    row["pass"] = (
        not invalid and not b_in_window and b_done_after_cont and who == ["A", "B"]
        and not lg["bad"] and lg["blank"] == 0 and not lg["tail_open"] and not warns
    )  # fmt: skip
    shutil.rmtree(d, ignore_errors=True)
    return row


def probe_interleave_p(trials: int) -> dict[str, Any]:
    pred = (f"tested {trials}/{trials}: B's done only after A resumes, file = A's line then B's, "
            "0 unparseable, 0 blank, 0 warnings; control: B done within the window, "
            f"'A1 B / A2' corrupt {trials}/{trials}")  # fmt: skip
    rows = [run_ilv_p(i) for i in range(trials)]
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r["invalid"]) for r in rows)
    walls = [r["b_wall_from_go_s"] for r in rows]
    obs = (
        f"{n}/{trials} trials pass; INVALID {inv}; B done inside the 0.2 s window "
        f"{sum(r['b_done_in_window'] for r in rows)}/{trials} (go-to-done "
        f"{min(walls):.4f}-{max(walls):.4f} s); unparseable {_tot(rows, 'unparseable')}, "
        f"blank {_tot(rows, 'blank')}, events lost {_tot(rows, 'lost')}, warnings "
        f"{sum(len(r['warnings']) for r in rows)}"
    )
    return result("E-interleave-p", pred, obs, n == trials and inv == 0, runs=rows, invalid=inv)


# ---------------------------------------------------------------- E-split-stress / E-sync


def run_split(r: int) -> dict[str, Any]:
    d = workdir("split")
    path, go = d / "events.jsonl", d / "go"
    path.touch()
    procs = [Proc(d, f"w{w}", "split", {"path": str(path), "go": str(go), "w": w, "threads": 2,
                                        "n": 250}) for w in range(8)]  # fmt: skip
    invalid: list[str] = []
    for w, p in enumerate(procs):
        if not p.wait("ready", 60):
            invalid.append(f"writer {w} never said ready")
    t0 = time.monotonic()
    go.write_text("go")
    warns: list[str] = []
    for w, p in enumerate(procs):
        p.finish(300)
        dn = p.done()
        if dn is None or p.rc != 0:
            invalid.append(f"writer {w}: no done line / rc {p.rc}")
            continue
        warns += dn["warnings"]
        if dn["intercepted"] < 500:
            invalid.append(f"writer {w}: shim saw {dn['intercepted']} < 500 log writes")
        if not _is_tree(dn):
            invalid.append(f"writer {w}: lattice not imported from the tree under test")
    wall = time.monotonic() - t0
    lg = scan(path)
    evs = lg["events"]
    want = {(w, t, s) for w in range(8) for t in range(2) for s in range(250)}
    full = [e for e in evs if all(k in e for k in ("w", "t", "seq"))]
    got = [(e["w"], e["t"], e["seq"]) for e in full]
    malformed = len(evs) - len(full)  # a glued head with a foreign tail that still parses
    order_ok = True
    for w in range(8):
        for t in range(2):
            seqs = [e["seq"] for e in full if (e["w"], e["t"]) == (w, t)]
            order_ok = order_ok and seqs == sorted(seqs)
    ids = [e.get("id") for e in evs]
    row = {
        "seed": r, "lines": lg["lines"], "blank": lg["blank"], "unparseable": len(lg["bad"]),
        "glued": len(lg["bad"]), "lost": len(want - set(got)),
        "duplicated": len(got) - len(set(got)), "dup_ids": len(ids) - len(set(ids)),
        "malformed_parseable": malformed, "order_ok": order_ok,
        "lock_warnings": lock_warnings(warns), "wall_s": round(wall, 2),
        "events": len(evs), "invalid": invalid,
    }  # fmt: skip
    row["pass"] = (
        not invalid and lg["lines"] == 4000 and lg["blank"] == 0 and not lg["bad"]
        and row["lost"] == 0 and row["duplicated"] == 0 and row["dup_ids"] == 0 and order_ok
        and malformed == 0
        and row["lock_warnings"] == 0 and not lg["tail_open"]
    )  # fmt: skip
    shutil.rmtree(d, ignore_errors=True)
    return row


def probe_split(runs: int) -> dict[str, Any]:
    pred = (f"tested {runs}/{runs}: 4000 lines, 0 unparseable, 0 blank, 0 lost/duplicated, "
            "order kept, 0 lock warnings; control (diagnostic, stochastic, not a rule): >= 1 "
            "glued line in every run, >= 10 per run (~85% confident)")  # fmt: skip
    rows = [run_split(r) for r in range(runs)]
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r["invalid"]) for r in rows)
    obs = (
        f"{n}/{runs} runs pass; INVALID {inv}; lines {sorted({r['lines'] for r in rows})}; "
        f"unparseable (glued) {_tot(rows, 'unparseable')} and spliced-but-parseable "
        f"{_tot(rows, 'malformed_parseable')}, per run "
        f"{[r['unparseable'] for r in rows]}; blank {_tot(rows, 'blank')}; lost "
        f"{_tot(rows, 'lost')}; duplicated {_tot(rows, 'duplicated')}; lock warnings "
        f"{_tot(rows, 'lock_warnings')}"
    )
    return result("E-split-stress", pred, obs, n == runs and inv == 0, runs=rows, invalid=inv)


def probe_sync(runs: int) -> dict[str, Any]:
    pred = "1/1: between two append returns the trace is exactly W F, 1000/1000 (both arms)"
    rows = []
    for r in range(runs):
        d = workdir("sync")
        path = d / "events.jsonl"
        path.touch()
        p = Proc(d, "s", "sync", {"path": str(path), "n": 1000})
        p.finish(300)
        dn = p.done()
        invalid = [] if dn is not None and p.rc == 0 else [f"no done line, rc {p.rc}"]
        traces = dn["traces"] if dn else {}
        if dn and not _is_tree(dn):
            invalid.append("lattice not imported from the tree under test")
        ok_n = traces.get("WF", 0)
        good = not invalid and ok_n == 1000 and sum(traces.values()) == 1000
        rows.append({"seed": r, "traces": traces, "w_f": ok_n, "invalid": invalid,
                     "events": len(scan(path)["events"]), "pass": good})  # fmt: skip
        shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    inv = sum(bool(r["invalid"]) for r in rows)
    obs = f"{n}/{runs} runs pass; INVALID {inv}; traces {rows[0]['traces'] if rows else {}}"
    return result("E-sync", pred, obs, n == runs and inv == 0, runs=rows, invalid=inv)


# ---------------------------------------------------------------- mutants (IK1-IK10)

EV = "lattice/improve/events.py"
_FLOCK = "                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
_SIZE = (
    "        size = os.fstat(fd).st_size\n"
    '        torn = size > 0 and os.pread(fd, 1, size - 1) != b"\\n"\n'
)
IK_MUTANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "IK1": ("flock call removed", [(_FLOCK, "                    pass  # MUTANT IK1: no flock\n")]),
    "IK2": ("no heal", [("        if torn:\n", "        if False:  # MUTANT IK2: no heal\n")]),
    "IK3": ("os.fsync removed", [("        os.fsync(fd)\n", "        pass  # MUTANT IK3\n")]),
    "IK4": (
        "heal reads byte `size` not `size-1` (always a prefix)",
        [("os.pread(fd, 1, size - 1)", "os.pread(fd, 1, size)")],
    ),
    "IK5": (
        "LOCK_UN right after the lock is taken",
        [(_FLOCK, _FLOCK + "                    fcntl.flock(fd, fcntl.LOCK_UN)  # MUTANT IK5\n")],
    ),
    "IK6": (
        "size/pread (torn?) decided before the lock loop",
        [(_SIZE, ""), ("        if fcntl is None:\n", _SIZE + "        if fcntl is None:\n")],
    ),
    "IK7": (
        "LOCK_SH instead of LOCK_EX",
        [("fcntl.LOCK_EX | fcntl.LOCK_NB", "fcntl.LOCK_SH | fcntl.LOCK_NB")],
    ),
    "IK8": (
        "_write_all ignores the count",
        [("        view = view[n:]\n", "        view = view[len(view) :]  # MUTANT IK8\n")],
    ),
    "IK9": (
        "LOCK_TIMEOUT_S = 0.0",
        [("LOCK_TIMEOUT_S = 2.0", "LOCK_TIMEOUT_S = 0.0  # MUTANT IK9")],
    ),
    "IK10": (
        "fsync moved before the write loop",
        [
            (
                "        for b in batch:\n            _write_all(fd, b)\n            done += 1\n"
                "        os.fsync(fd)\n",
                "        os.fsync(fd)  # MUTANT IK10\n        for b in batch:\n"
                "            _write_all(fd, b)\n            done += 1\n",
            )
        ],
    ),
}
IK_PREDICTED = {
    "IK1": ["E-interleave-t", "E-interleave-p", "E-split-stress"],
    "IK2": ["E-kill2", "E-kill2s", "E-kill-import"],
    "IK3": ["E-sync"],
    "IK4": ["E-null", "E-kill2"],
    "IK5": ["E-interleave-t", "E-interleave-p"],
    "IK6": ["E-interleave-t", "E-interleave-p"],
    "IK7": ["E-interleave-t", "E-interleave-p"],
    "IK8": ["E-kill2s"],
    "IK9": ["E-interleave-t", "E-interleave-p", "E-kill2"],
    "IK10": ["E-sync"],
}


def make_mutant(mid: str, scratch: Path) -> Path:
    """<scratch>/mut/<id>/lattice: a copy of this checkout's lattice with the hunks applied
    (each must match once); `base` is the unmutated copy. Answers the PYTHONPATH."""
    dst = scratch / "mut" / mid
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "lattice", dst / "lattice", ignore=shutil.ignore_patterns("__pycache__"))
    if mid != "base":
        target = dst / EV
        text = target.read_text()
        for old, new in IK_MUTANTS[mid][1]:
            if text.count(old) != 1:
                raise RuntimeError(f"{mid}: hunk matches {text.count(old)} times: {old!r}")
            text = text.replace(old, new)
        target.write_text(text)
        py_compile.compile(str(target), cfile=str(dst / "events.pyc"), doraise=True)
    return dst


def quick_probes() -> list[dict[str, Any]]:
    out = []
    for pid, fn in PROBE_FUNCS.items():
        out.append(fn(COUNTS[pid][1]))
    return out


def run_mutants(ids: list[str], out: Path, scratch: Path) -> int:
    saved = STATE["tree"]
    results: dict[str, Any] = {}
    sys.stdout.flush()
    t0 = time.monotonic()
    STATE["tree"] = make_mutant("base", scratch)
    base = quick_probes()
    base_fail = [r["id"] for r in base if not r["pass"]]
    results["unmutated"] = {"pass": not base_fail, "failing": base_fail,
                            "table": [(r["id"], r["observed"]) for r in base]}  # fmt: skip
    print(f"unmutated copy: {'pass' if not base_fail else 'FAIL ' + str(base_fail)}", flush=True)
    killed = 0
    todo = ids or list(IK_MUTANTS)
    if not base_fail:
        for mid in todo:
            t1 = time.monotonic()
            try:
                STATE["tree"] = make_mutant(mid, scratch)
                rs = quick_probes()
            except Exception as exc:  # a mutant the rig cannot run is reported, not hidden
                results[mid] = {"error": f"{type(exc).__name__}: {exc}", "killed": False}
                print(f"{mid}: ERROR {exc}", flush=True)
                continue
            failing = [r["id"] for r in rs if not r["pass"]]
            invalid_only = [r["id"] for r in rs if not r["pass"] and r.get("invalid")]
            caught = bool(failing)
            killed += caught
            results[mid] = {
                "what": IK_MUTANTS[mid][0], "predicted_catchers": IK_PREDICTED[mid],
                "failing_probes": failing, "failing_with_invalid_runs": invalid_only,
                "killed": caught,
                "caught_by_predicted": bool(set(failing) & set(IK_PREDICTED[mid])),
                "table": [(r["id"], r["pass"], r["observed"]) for r in rs],
                "wall_s": round(time.monotonic() - t1, 1),
            }  # fmt: skip
            print(
                f"{mid}: {'killed' if caught else 'SURVIVED'} by {failing} "
                f"(predicted {IK_PREDICTED[mid]}) [{results[mid]['wall_s']} s]",
                flush=True,
            )
    STATE["tree"] = saved
    summary = {"mutants_killed": killed, "mutants_total": len(todo),
               "all_caught_by_predicted": all(
                   results.get(m, {}).get("caught_by_predicted") for m in todo),
               "elapsed_s": round(time.monotonic() - t0, 1), "results": results,
               "decisions": DECISIONS,
               "post_hoc_script_corrections": POST_HOC_SCRIPT_CORRECTIONS}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / "mutants.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(f"mutants: {killed}/{len(todo)} killed")
    return 0 if killed == len(todo) and not base_fail else 1


# ---------------------------------------------------------------- the runner

PROBE_FUNCS: dict[str, Callable[[int], dict[str, Any]]] = {
    "E-null": probe_null,
    "E-kill2": probe_kill2,
    "E-kill2s": probe_kill2s,
    "E-kill-import": probe_kill_import,
    "E-interleave-t": probe_interleave_t,
    "E-interleave-p": probe_interleave_p,
    "E-split-stress": probe_split,
    "E-sync": probe_sync,
}


def make_control(scratch: Path) -> Path:
    """The a4ab4a1 `lattice` tree (git archive), extracted into <scratch>/control."""
    dst = scratch / "control"
    if (dst / "lattice").exists():
        return dst
    dst.mkdir(parents=True, exist_ok=True)
    arch = subprocess.run(["git", "-C", str(ROOT), "archive", BASE_REF, "lattice"], check=True,
                          capture_output=True)  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(dst)], input=arch.stdout, check=True)
    return dst


def main(argv: list[str] | None = None) -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        WORKERS[sys.argv[2]](json.loads(sys.argv[3]))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--arm", choices=("tested", "control"), default="tested")
    ap.add_argument("--only", nargs="*", default=None, help="probe ids (exact)")
    ap.add_argument("--quick", action="store_true", help="the mutant-mode counts")
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--mutant-ids", nargs="*", default=[])
    args = ap.parse_args(argv)
    own = args.scratch is None
    scratch = (Path(tempfile.mkdtemp(prefix="ekill-")) if own else args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    STATE["scratch"] = str(scratch)
    STATE["arm"] = args.arm
    if args.mutants:
        return run_mutants(args.mutant_ids, args.out, scratch)
    control = make_control(scratch)
    STATE["control"] = control
    STATE["tree"] = control if args.arm == "control" else ROOT
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    ran: dict[str, str] = {}
    for pid in PROBE_IDS:
        if args.only is not None and pid not in args.only:
            ran[pid] = "NOT RUN"
            continue
        runs = COUNTS[pid][1 if args.quick else 0]
        t1 = time.monotonic()
        try:
            got = PROBE_FUNCS[pid](runs)
        except Exception as exc:  # a probe that cannot run is reported as failed, not hidden
            got = result(pid, "(did not run)", f"ERROR {type(exc).__name__}: {exc}", False)
        got["wall_s"] = round(time.monotonic() - t1, 1)
        got["count"] = runs
        rows.append(got)
        ran[pid] = "RAN"
        print(f"  [{pid} {got['wall_s']} s] {'pass' if got['pass'] else 'FAIL'}: "
              f"{got['observed']}", flush=True)  # fmt: skip
    print(f"arm: {args.arm}   tree: {STATE['tree']}")
    passed = sum(1 for r in rows if r["pass"])
    for r in rows:
        (out / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    summary: dict[str, Any] = {
        "arm": args.arm, "tree": str(STATE["tree"]), "base_ref": BASE_REF,
        "mode": "quick" if args.quick else "full", "ran": ran,
        "table": [{k: r[k] for k in ("id", "predicted", "observed", "pass", "count", "wall_s")}
                  for r in rows],
        "probes_passed": passed, "probes_total": len(rows),
        "elapsed_s": round(time.monotonic() - t0, 1), "findings": FINDINGS,
        "post_hoc_script_corrections": POST_HOC_SCRIPT_CORRECTIONS, "decisions": DECISIONS,
    }  # fmt: skip
    if args.arm == "control":
        by_id = {r["id"]: r for r in rows}
        summary["control_must_fail"] = {
            pid: ("NOT RUN" if pid not in by_id else
                  ("FAILED (discriminates)" if not by_id[pid]["pass"]
                   else "PASSED (non-discrimination)"))
            for pid in CONTROL_MUST_FAIL
        }  # fmt: skip
        print("control must fail:", summary["control_must_fail"])
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"probes passing the tested-arm rule: {passed}/{len(rows)}")
    if own:
        shutil.rmtree(scratch, ignore_errors=True)
    if args.arm == "control":
        return 0
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
