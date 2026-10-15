"""Probe runner for the improvement-loop hardening (spike 3, G10).

Implements the probes and mutants of ``docs/results/2026-10-15-improve-hardening-prereg.md``
(sections 3 and 4): E-stress, E-kill (+ holder kill), E-lock (a, b), E-import (p1, p2, p2-final,
p3), E-heal, E-short, E-cap, E-field (F1-F8), E-compat (+ forward), the improve2 probes on both
trees, E-ledger and E-cost. The predictions and pass rules are copied from that document; a
probe that does not match is reported as a finding with its raw output, never adapted.

    uv run python dogfood/improve_hardening_probes.py [--runs 10] [--scratch DIR] \
        [--out docs/results/data/2026-10-15-improve-hardening] [--arm tested|control] \
        [--only ID ...] [--mutants]

The runner itself is stdlib only: every call of ``lattice`` runs in a child process whose
``PYTHONPATH`` is the tree under test (``--arm tested``, this checkout) or the a4ab4a1 control
tree (``--arm control``: a shared clone of this repository at a4ab4a1, built in the scratch
directory). E-compat and the improve2 probes compare the two trees and run in the tested arm
only. No LLM, no API key, no network, no Docker. ``--out`` must point at a scratch directory
when this is a trial: the runner writes ``<id>.json`` and ``summary.json`` there.

``--mutants`` runs IH1-IH14 (the prereg's table) through ``dogfood/improve_mutants.py``'s
copy-patch-run code, imported and not edited; the suite is ``tests/improve``; the last line is
``mutants: N/N killed``.

``field_build`` (the E-field rig: one real Rebuilder build of the Studio seed with scripted
check reports) is shared with ``tests/improve/test_events.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
DATA = ROOT / "docs/results/data/2026-10-12-spike3"
PLANS = ROOT / "docs/results/data/streak-plans-2026-10-11"
DEFAULT_OUT = ROOT / "docs/results/data/2026-10-15-improve-hardening"
BASE_REF = "a4ab4a1"
PY = sys.executable
RUNS = 10
LOCK_WAIT = "events: waited 2.0 s for the lock on {path}; appended without it"

SETUP = {
    "database_server_s": 3.2101,
    "seed_build_s.default": 0.8123,
    "db_clones": 12,
    "db_clone_mean_s": 0.0412,
    "database_teardown_s": 1.5034,
}

# Disclosed notes, written to summary.json (and copied to the results by the orchestrator).
POST_HOC_SCRIPT_CORRECTIONS: list[str] = []
FINDINGS: list[str] = []  # differences from a prediction that are not a failed pass rule
DECISIONS: list[str] = [
    "append_lines gained the keyword `degrade` (True: write unlocked on a lock timeout; False: "
    "write nothing, extend's case) and `what` (the noun of the warning), and its `timeout` "
    "defaults to None meaning LOCK_TIMEOUT_S read at call time (so a test can patch it): the "
    "prereg fixed the name and the (written, warnings) result only.",
    "report_setup sits beside report_layer_seconds in rebuild.py (prereg 2.9); it is the helper "
    "the prereg's E-regress diff line names.",
    "A fsync failure after every line was written counts as a dropped event (as at a4ab4a1).",
    "E-stress/E-kill workers are subprocesses released by a go-file after their imports "
    "(equivalent to a Barrier after import), not multiprocessing children.",
    "The control tree is a shared clone of this repository checked out at a4ab4a1 in the "
    "scratch directory (improve2_probes.py needs a git repository).",
]

PADS = (80, 480, 4080, 8880)  # line sizes ~200 / 600 / 4200 / 9000 bytes


# ---------------------------------------------------------------- the E-field rig
# (shared with tests/improve/test_events.py; lattice and tests are imported lazily so the
# runner itself stays stdlib only and a worker takes its lattice from PYTHONPATH)

GATE_SCRIPT = """#!/bin/sh
exec {py} - "$@" <<'PY'
import json, sys
from pathlib import Path
TIMINGS, WRITE = json.loads({timings!r}), {write!r}
miss = [f for f in ("writing.txt",) if not Path(f).exists()]
findings = [{{"layer": "D", "verdict": "silent", "rule": "scenario: create_a_note",
             "case": f, "key": "D:GET /notes:" + f,
             "details": {{"problems": ["missing " + f]}}}} for f in miss]
report = {{"verdict": "fail" if findings else "pass", "layers": "ABCDX",
          "counts": {{"D": len(findings)}}, "findings": findings, "notes": [], "unchecked": []}}
if TIMINGS is not None:
    report["timings"] = TIMINGS
if WRITE:
    Path("metrics").mkdir(exist_ok=True)
    Path("metrics/last-check.json").write_text(json.dumps(report))
print(("FAIL" if findings else "PASS") + ": %d findings (D %d)" % (len(findings), len(findings)))
for f in miss:
    print("FAIL %s is missing (scenario:create_a_note)" % f)
sys.exit(1 if findings else 0)
PY
"""

T1 = {"total_s": 1.0, "layers": {"C": 0.1}, "setup": SETUP}
F5_SETUP = {"database_server_s": 2.5, "flag": True, "note": "x", "big": 1e309}
# scenario -> the gate commands' (timings or None, writes the report) specs
SCENARIOS: dict[str, list[tuple[Any, bool]]] = {
    "F1": [(T1, True)],
    "F2": [({**T1, "setup": {}}, True)],
    "F3": [(None, True)],
    "F4": [(T1, False)],
    "F5": [({**T1, "setup": F5_SETUP}, True)],
    "F6": [(T1, False), (T1, True)],
}


def field_build(
    tmp: Path, specs: list[tuple[Any, bool]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One scoped build of the seed's ``writing-notes`` area (a baseline gate, then two
    rounds) with one gate command per spec: ``(the report's timings or None, writes the
    report)``. Answers the build record and the gate_run events, in file order."""
    from lattice.improve.events import EventLog, read_events
    from lattice.studio.rebuild import Rebuilder, load_table
    from tests.studio.seed import cleanup_worktrees, git, seed_project

    root = seed_project(tmp)
    names = []
    for i, (timings, write) in enumerate(specs):
        name = "gate.sh" if i == 0 else f"gate{i}.sh"
        text = GATE_SCRIPT.format(py=sys.executable, timings=json.dumps(timings), write=write)
        (root / name).write_text(text)
        (root / name).chmod(0o755)
        names.append("./" + name)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "gates")
    log = tmp / "events.jsonl"
    table = {**load_table(root), "findings_json": "metrics/last-check.json", "gates": names}
    rb = Rebuilder(
        root, table, name="notes", model="sonnet", env=dict(os.environ),
        events=EventLog(log, "notes"),
    )  # fmt: skip
    try:
        rid = rb.start([], scope={"area": "writing-notes"}, baseline_wait=30)["id"]
        rec = rb.get(rid, wait=60)
        assert rec["status"] == "passed", rec["status"]
    finally:
        cleanup_worktrees(root)
    evs = [e for e in read_events(log).events if e["kind"] == "gate_run"]
    return rec, evs


# ---------------------------------------------------------------- workers (child processes)


def _wait_go(go: str) -> None:
    while not os.path.exists(go):
        time.sleep(0.0003)


def w_stress(path: str, go: str, w: str, threads: str, n: str) -> None:
    from lattice.improve.events import EventLog

    log = EventLog(path, "notes")
    print("ready", flush=True)
    _wait_go(go)

    def run(t: int) -> None:
        for seq in range(int(n)):
            log.append("finding_class", signal_key="D:stress", count=1, w=int(w), t=t, seq=seq,
                       pad="p" * PADS[seq % 4])  # fmt: skip

    ts = [threading.Thread(target=run, args=(t,)) for t in range(int(threads))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print(json.dumps({"warnings": log.warnings}), flush=True)


def w_kill(path: str, go: str, w: str, n: str, ack: str) -> None:
    from lattice.improve.events import EventLog

    log = EventLog(path, "notes")
    fd = os.open(ack, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    print("ready", flush=True)
    _wait_go(go)
    for seq in range(int(n)):
        log.append("finding_class", signal_key="D:kill", count=1, w=int(w), seq=seq,
                   pad="p" * PADS[seq % 4])  # fmt: skip
        os.write(fd, f"{seq}\n".encode())
    print(json.dumps({"warnings": log.warnings}), flush=True)


def w_append1(path: str, *fields: str) -> None:
    """One append, timed, after a line on stdin when the first field is ``wait``."""
    from lattice.improve.events import EventLog

    log = EventLog(path, "notes")
    print("ready", flush=True)
    if fields and fields[0] == "wait":
        sys.stdin.readline()
    t0 = time.monotonic()
    ev = log.append("human", what="mine", actor="owner")
    took = time.monotonic() - t0
    print(json.dumps({"elapsed": took, "event": ev, "warnings": log.warnings}), flush=True)


def w_holder(path: str, secs: str, line: str = "") -> None:
    import fcntl

    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    print("held", flush=True)
    time.sleep(float(secs))
    if line:
        os.write(fd, (line + "\n").encode())
    os.close(fd)
    print("done", flush=True)


def w_import(plan: str, project: str, state: str, go: str) -> None:
    from lattice.improve.__main__ import main

    print("ready", flush=True)
    _wait_go(go)
    sys.exit(main(["import-plan", plan, "--project", project, "--state", state]))


def w_heal(path: str) -> None:
    from lattice.improve.events import EventLog, read_events

    old = Path(path).read_bytes()
    log = EventLog(path, "notes")
    ev = log.append("human", what="new", actor="owner")
    mid = Path(path).read_bytes()
    res = read_events(path)
    ev2 = log.append("human", what="newer", actor="owner")
    end = Path(path).read_bytes()
    print(json.dumps({
        "old_len": len(old), "event": ev, "event2": ev2, "warnings_log": log.warnings,
        "read": {"n": len(res.events), "warnings": res.warnings,
                 "whats": [e.get("what") for e in res.events]},
        "mid_ok": mid.startswith(old) and mid[len(old):len(old) + 1] == b"\n",
        "mid_tail": mid[len(old):].decode(),
        "end_tail": end[len(mid):].decode(),
        "end_len": len(end), "mid_len": len(mid),
    }), flush=True)  # fmt: skip


def w_ledger(project: str, state: str, k: str, n: str, go: str) -> None:
    import contextlib
    import io

    from lattice.improve.__main__ import main

    print("ready", flush=True)
    _wait_go(go)
    rcs = 0
    for i in range(int(n)):
        with contextlib.redirect_stdout(io.StringIO()):
            rcs += main(["ledger", "--signal", f"s:w{k}", "--status", "open", "--id",
                         f"w{k}-{i}", "--project", project, "--state", state])  # fmt: skip
    print(json.dumps({"rc_sum": rcs}), flush=True)


def w_cost(path: str, n: str) -> None:
    from lattice.improve.events import EventLog

    log = EventLog(path, "notes")
    times = []
    for i in range(int(n)):
        t0 = time.perf_counter()
        log.append("finding_class", signal_key="D:cost", count=1, i=i)
        times.append((time.perf_counter() - t0) * 1000)
    print(json.dumps({"median_ms": statistics.median(times), "mean_ms": statistics.mean(times),
                      "p95_ms": sorted(times)[int(len(times) * 0.95)], "n": len(times)}),
          flush=True)  # fmt: skip


def w_cli(inp: str, out: str) -> None:
    """Run a list of ``lattice improve`` argv lists in this process: [{argv, rc, out, err}]."""
    import contextlib
    import io

    from lattice.improve.__main__ import main

    results = []
    for argv in json.loads(Path(inp).read_text()):
        o, e = io.StringIO(), io.StringIO()
        rc: Any = 0
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            try:
                rc = main(argv)
            except SystemExit as exc:
                rc = exc.code
        results.append({"argv": argv, "rc": rc, "out": o.getvalue(), "err": e.getvalue()})
    Path(out).write_text(json.dumps(results))


_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")
_SHA10 = re.compile(r"\b[0-9a-f]{10}\b")
_WT = re.compile(r"(/rebuilds/[^/\s]*-)[0-9a-f]{6}\b")  # the worktree directory's random suffix


def mask(obj: Any, tmp: str) -> Any:
    """A build record or event with ids, times and paths masked (hashes, timestamps, the
    scratch directory and wall seconds)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "line" or (k == "id" and isinstance(v, str) and v.startswith("e-")):
                continue
            wall = k in ("seconds", "wall_s") and isinstance(v, int | float)
            out[k] = "<s>" if wall else mask(v, tmp)
        return out
    if isinstance(obj, list):
        return [mask(v, tmp) for v in obj]
    if isinstance(obj, str):
        s = obj.replace(tmp, "<tmp>")
        s = _WT.sub(r"\1<r>", s)
        return _SHA10.sub("<sha10>", _SHA40.sub("<sha>", _TS.sub("<ts>", s)))
    return obj


def w_field(out: str, scratch: str) -> None:
    res: dict[str, Any] = {}
    for name, specs in SCENARIOS.items():
        tmp = Path(tempfile.mkdtemp(dir=scratch, prefix=f"{name}-"))
        rec, evs = field_build(tmp, specs)
        res[name] = {
            "events": [mask({k: v for k, v in e.items() if k != "ts"}, str(tmp)) for e in evs],
            "raw_events": evs,
            "record": mask(rec, str(tmp)),
        }
        shutil.rmtree(tmp, ignore_errors=True)
    Path(out).write_text(json.dumps(res, default=str))


WORKERS: dict[str, Callable[..., None]] = {
    "stress": w_stress, "kill": w_kill, "append1": w_append1, "holder": w_holder,
    "import": w_import, "heal": w_heal, "ledger": w_ledger, "cost": w_cost, "cli": w_cli,
    "field": w_field,
}  # fmt: skip


# ---------------------------------------------------------------- the parent: plumbing

STATE: dict[str, Any] = {"scratch": None, "n": 0, "tree": ROOT, "control": None, "arm": "tested"}
_LOCK = threading.Lock()


def env_for(tree: Path) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", COLUMNS="100")
    return e


def spawn(name: str, *args: Any, tree: Path | None = None, stdin: bool = False):
    return subprocess.Popen(
        [PY, str(SCRIPT), "--worker", name, *[str(a) for a in args]],
        env=env_for(tree or STATE["tree"]), cwd=STATE["scratch"], text=True,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip


def last_json(proc: subprocess.Popen[str], timeout: float = 300) -> dict[str, Any]:
    out, err = proc.communicate(timeout=timeout)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if proc.returncode not in (0, -signal.SIGKILL) or not lines:
        raise RuntimeError(f"worker failed rc={proc.returncode}: {err[-800:]} {out[-200:]}")
    return json.loads(lines[-1])


def read_ready(proc: subprocess.Popen[str], word: str = "ready") -> None:
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if line != word:
        raise RuntimeError(f"worker said {line!r}, wanted {word!r}: {proc.stderr.read()[-800:]}")


def workdir(label: str) -> Path:
    with _LOCK:
        STATE["n"] += 1
        d = Path(STATE["scratch"]) / f"{STATE['n']:04d}-{label}"
    d.mkdir(parents=True)
    return d


def sha(b: str | bytes) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()[:16]


def parse_log(path: Path) -> dict[str, Any]:
    """The raw view of a log: lines, blank lines, unparseable lines, events (file order)."""
    data = path.read_bytes() if path.exists() else b""
    pieces = data.split(b"\n")
    tail_open = bool(data) and not data.endswith(b"\n")
    if pieces and pieces[-1] == b"":
        pieces.pop()
    blank = bad = 0
    evs: list[dict[str, Any]] = []
    for p in pieces:
        if not p.strip():
            blank += 1
            continue
        try:
            e = json.loads(p)
        except (json.JSONDecodeError, UnicodeDecodeError):
            bad += 1
            continue
        if isinstance(e, dict):
            evs.append(e)
        else:
            bad += 1
    return {"lines": len(pieces), "blank": blank, "bad": bad, "events": evs, "tail_open": tail_open}


def result(pid: str, predicted: str, observed: str, ok: bool, **raw: Any) -> dict[str, Any]:
    return {"id": pid, "predicted": predicted, "observed": observed, "pass": bool(ok), **raw}


def skip_in_control(pid: str, predicted: str) -> dict[str, Any] | None:
    if STATE["arm"] == "control":
        return result(pid, predicted, "n/a in the control arm (compares the two trees)", True,
                      skipped=True)  # fmt: skip
    return None


# ---------------------------------------------------------------- probes


def probe_stress(runs: int) -> dict[str, Any]:
    pid = "E-stress"
    pred = f"{runs}/{runs} runs: 4000 lines, 0 torn, 0 lost, 0 dup ids, order kept, 0 timeouts"
    rows = []
    for _r in range(runs):
        d = workdir("stress")
        path, go = d / "events.jsonl", d / "go"
        procs = [spawn("stress", path, go, w, 2, 250) for w in range(8)]
        for p in procs:
            read_ready(p)
        t0 = time.monotonic()
        go.write_text("go")
        warns: list[str] = []
        for p in procs:
            warns += last_json(p)["warnings"]
        wall = time.monotonic() - t0
        lg = parse_log(path)
        evs = lg["events"]
        got = [(e.get("w"), e.get("t"), e.get("seq")) for e in evs]
        want = {(w, t, s) for w in range(8) for t in range(2) for s in range(250)}
        order_ok = all(
            [e["seq"] for e in evs if (e["w"], e["t"]) == (w, t)] == list(range(250))
            for w in range(8) for t in range(2)
        )  # fmt: skip
        row = {
            "lines": lg["lines"], "blank": lg["blank"], "unparseable": lg["bad"],
            "lost": len(want - set(got)), "duplicated": len(got) - len(set(got)),
            "dup_ids": len(evs) - len({e.get("id") for e in evs}), "order_ok": order_ok,
            "timeouts": sum("waited" in w for w in warns), "warnings": len(warns),
            "wall_s": round(wall, 2), "bytes": path.stat().st_size,
        }  # fmt: skip
        row["pass"] = (
            row["lines"] == 4000 and row["blank"] == 0 and row["unparseable"] == 0
            and row["lost"] == 0 and row["duplicated"] == 0 and row["dup_ids"] == 0
            and order_ok and row["timeouts"] == 0
        )  # fmt: skip
        rows.append(row)
        shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    walls = [r["wall_s"] for r in rows]
    obs = (
        f"{n}/{runs} runs pass; lines {sorted({r['lines'] for r in rows})}, torn "
        f"{sum(r['unparseable'] for r in rows)}, lost {sum(r['lost'] for r in rows)}, dup "
        f"{sum(r['duplicated'] + r['dup_ids'] for r in rows)}; wall {min(walls)}-{max(walls)} s"
    )
    return result(pid, pred, obs, n == runs, runs=rows)


def probe_kill(runs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    pred = f"{runs}/{runs}: 0 unparseable incl. tail; per writer 0..m-1, acked <= m <= acked+1"
    rows, hrows = [], []
    for r in range(runs):
        d = workdir("kill")
        path, go = d / "events.jsonl", d / "go"
        rnd = random.Random(r)
        kills = {w: rnd.uniform(0.05, 0.5) for w in range(4)}
        procs = [spawn("kill", path, go, w, 500, d / f"ack-{w}") for w in range(8)]
        for p in procs:
            read_ready(p)
        t0 = time.monotonic()
        go.write_text("go")
        for w, at in sorted(kills.items(), key=lambda kv: kv[1]):
            time.sleep(max(0.0, at - (time.monotonic() - t0)))
            try:
                os.kill(procs[w].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for p in procs:
            p.communicate(timeout=300)
        post = spawn("append1", path)
        read_ready(post)
        t1 = time.monotonic()
        res = last_json(post)
        post_wall = time.monotonic() - t1
        lg = parse_log(path)
        per: dict[int, list[int]] = {}
        for e in lg["events"]:
            if e.get("kind") == "finding_class":
                per.setdefault(int(e["w"]), []).append(int(e["seq"]))
        detail = []
        ok = lg["bad"] == 0 and lg["blank"] == 0 and not lg["tail_open"]
        core = ok  # every clause but "the killed writers have m < 500"
        for w in range(8):
            ack = d / f"ack-{w}"
            acked = len(ack.read_text().split()) if ack.exists() else 0
            seqs = per.get(w, [])
            m = len(seqs)
            good = seqs == list(range(m)) and acked <= m <= acked + 1
            core = core and good
            if w < 4:
                good = good and m < 500
            ok = ok and good
            detail.append({"w": w, "acked": acked, "m": m, "ok": good})
        post_ok = (res["event"] is not None and res["elapsed"] < 0.5
                   and any(e.get("what") == "mine" for e in lg["events"]))  # fmt: skip
        ok = ok and post_ok
        core = core and post_ok
        rows.append(
            {
                "kills_s": {str(w): round(a, 4) for w, a in kills.items()},
                "seed": r,
                "unparseable": lg["bad"],
                "blank": lg["blank"],
                "tail_open": lg["tail_open"],
                "writers": detail,
                "post_append_s": round(res["elapsed"], 4),
                "post_wall_s": round(post_wall, 3),
                "post_present": post_ok,
                "pass_all_clauses_but_m_lt_500": core,
                "killed_cut_short": sum(1 for x in detail[:4] if x["m"] < 500),
                "pass": ok,
            }
        )
        shutil.rmtree(d, ignore_errors=True)
        # the holder kill
        d = workdir("holderkill")
        path = d / "events.jsonl"
        h = spawn("holder", path, 60)
        read_ready(h, "held")
        os.kill(h.pid, signal.SIGKILL)
        h.communicate(timeout=30)
        a = spawn("append1", path)
        read_ready(a)
        res = last_json(a)
        hrows.append({"append_s": round(res["elapsed"], 4), "warnings": res["warnings"],
                      "present": res["event"] is not None,
                      "pass": res["elapsed"] < 0.2 and res["event"] is not None
                      and not res["warnings"]})  # fmt: skip
        shutil.rmtree(d, ignore_errors=True)
    n, hn = sum(r["pass"] for r in rows), sum(r["pass"] for r in hrows)
    ms = [m["m"] for r in rows for m in r["writers"][:4]]
    core = sum(r["pass_all_clauses_but_m_lt_500"] for r in rows)
    cut = sum(r["killed_cut_short"] for r in rows)
    k = result("E-kill", pred, f"{n}/{runs} runs pass; every clause but 'killed m < 500' "
               f"{core}/{runs}; killed writers cut short {cut}/{4 * runs}, wrote m = {min(ms)}-"
               f"{max(ms)} events (of 500); unparseable {sum(r['unparseable'] for r in rows)}",
               n == runs, runs=rows)  # fmt: skip
    hk = result("E-holder-kill", f"{runs}/{runs}: next append < 0.2 s (predicted ~0.01 s)",
                f"{hn}/{runs} pass; append {min(r['append_s'] for r in hrows)}-"
                f"{max(r['append_s'] for r in hrows)} s", hn == runs, runs=hrows)  # fmt: skip
    return k, hk


def _lock_run(hold: float, line: str) -> dict[str, Any]:
    d = workdir("lock")
    path = d / "events.jsonl"
    a = spawn("append1", path, "wait", stdin=True)
    read_ready(a)
    h = spawn("holder", path, hold, line)
    read_ready(h, "held")
    time.sleep(0.1)
    assert a.stdin is not None
    a.stdin.write("go\n")
    a.stdin.flush()
    res = last_json(a)
    h.communicate(timeout=60)
    evs = parse_log(path)["events"]
    ids = [e.get("id") for e in evs]
    pos_h = ids.index("e-holder") if "e-holder" in ids else -1
    pos_m = next((i for i, e in enumerate(evs) if e.get("what") == "mine"), -1)
    shutil.rmtree(d, ignore_errors=True)
    return {"elapsed": round(res["elapsed"], 3), "warnings": res["warnings"], "holder_at": pos_h,
            "mine_at": pos_m, "path": str(path)}  # fmt: skip


HOLDER_LINE = json.dumps({"v": 1, "kind": "human", "id": "e-holder", "what": "holder"})


def probe_lock(runs: int) -> list[dict[str, Any]]:
    rows_a = [_lock_run(0.5, HOLDER_LINE) for _ in range(runs)]
    for r in rows_a:
        r["pass"] = (0.35 <= r["elapsed"] <= 0.8 and not r["warnings"]
                     and 0 <= r["holder_at"] < r["mine_at"])  # fmt: skip
    with ThreadPoolExecutor(max_workers=runs) as ex:
        rows_b = list(ex.map(lambda _i: _lock_run(10, HOLDER_LINE), range(runs)))
    for r in rows_b:
        want = [LOCK_WAIT.format(path=r["path"])]
        r["pass"] = (2.0 <= r["elapsed"] <= 2.5 and r["warnings"] == want
                     and 0 <= r["mine_at"] < r["holder_at"])  # fmt: skip
    na, nb = sum(r["pass"] for r in rows_a), sum(r["pass"] for r in rows_b)
    a = result("E-lock-a", f"{runs}/{runs}: waits 0.35-0.8 s, line after the holder's, no warning",
               f"{na}/{runs} pass; elapsed {min(r['elapsed'] for r in rows_a)}-"
               f"{max(r['elapsed'] for r in rows_a)} s", na == runs, runs=rows_a)  # fmt: skip
    b = result("E-lock-b", f"{runs}/{runs}: returns in 2.0-2.5 s, the 2.3 warning, line first",
               f"{nb}/{runs} pass; elapsed {min(r['elapsed'] for r in rows_b)}-"
               f"{max(r['elapsed'] for r in rows_b)} s; warnings "
               f"{sorted({len(r['warnings']) for r in rows_b})}",
               nb == runs, runs=rows_b)  # fmt: skip
    return [a, b]


def _import_batch(tree: Path, plan: Path, n: int, d: Path) -> tuple[bytes, list[str]]:
    """`n` processes of `tree` import `plan` concurrently into the empty state dir `d`."""
    proj = d / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    state, go = d / "state", d / "go"
    procs = [spawn("import", plan, proj, state, go, tree=tree) for _ in range(n)]
    for p in procs:
        read_ready(p)
    go.write_text("go")
    outs = []
    for p in procs:
        o, e = p.communicate(timeout=120)
        if p.returncode != 0:
            outs.append(f"rc={p.returncode} {o.strip()} {e.strip()[-300:]}")
        else:
            outs.append(o.strip())
    log = state / "events.jsonl"
    return (log.read_bytes() if log.exists() else b""), outs


def probe_import(runs: int) -> list[dict[str, Any]]:
    res = []
    for name in ("p1", "p2", "p2-final", "p3"):
        pid = f"E-import-{name}"
        plan_src = PLANS / f"{name}.json"
        pred = (
            f"{runs}/{runs}: events.jsonl = one sequential import; stdout 1 x 'N written, 0 "
            "already there' + 3 x '0 written, N already there'"
        )
        d0 = workdir("import-ref")
        plan = d0 / f"{name}.json"
        shutil.copy(plan_src, plan)
        ref_bytes, ref_out = _import_batch(STATE["control"], plan, 1, d0)
        m = re.search(r"(\d+) event\(s\) written, 0 already there", ref_out[0])
        total = int(m.group(1)) if m else -1
        rows = []
        for _ in range(runs):
            d = workdir(f"import-{name}")
            plan = d / f"{name}.json"
            shutil.copy(plan_src, plan)
            data, outs = _import_batch(STATE["tree"], plan, 4, d)
            first = [o for o in outs if re.search(rf"{total} event\(s\) written, 0 already", o)]
            rest = [o for o in outs
                    if re.search(rf"0 event\(s\) written, {total} already there", o)]  # fmt: skip
            lg = parse_log(d / "state" / "events.jsonl")
            ids = [e.get("id") for e in lg["events"]]
            rows.append({
                "sha": sha(data), "ref_sha": sha(ref_bytes),
                "same_as_sequential": data == ref_bytes,
                "stdout_first": len(first), "stdout_rest": len(rest), "events": len(ids),
                "unique_ids": len(set(ids)), "outs": [re.sub(r"\(.*events.jsonl\)", "(<log>)", o)
                                                      for o in outs],
                "pass": data == ref_bytes and len(first) == 1 and len(rest) == 3,
            })  # fmt: skip
            shutil.rmtree(d, ignore_errors=True)
        n = sum(r["pass"] for r in rows)
        wrong = sum(r["events"] != r["unique_ids"] or r["events"] != total for r in rows)
        res.append(result(pid, pred, f"{n}/{runs} pass; {total} events; duplicates or a wrong "
                          f"count in {wrong} runs", n == runs, runs=rows))  # fmt: skip
    return res


def probe_heal(runs: int) -> dict[str, Any]:
    pred = f"{runs}/{runs}: file = old + newline + line; 4 events, 1 warning; 2nd append no blank"
    rows = []
    for _ in range(runs):
        d = workdir("heal")
        path = d / "events.jsonl"
        evs = [{"v": 1, "ts": "2026-10-15T00:00:00Z", "project": "notes", "kind": "human",
                "id": f"e-{i}", "what": f"w{i}"} for i in range(3)]  # fmt: skip
        body = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in evs).encode()
        frag = json.dumps({"v": 1, "ts": "2026-10-15T00:00:00Z", "project": "notes",
                           "kind": "gate_run", "id": "e-torn"}).encode()[:40]  # fmt: skip
        path.write_bytes(body + frag)
        h = spawn("heal", path)
        out = last_json(h)
        ev = out["event"]
        line = json.dumps(ev, separators=(",", ":"))
        row = {
            "mid_tail": out["mid_tail"], "end_tail": out["end_tail"],
            "read": out["read"], "bytes_ok": out["mid_tail"] == "\n" + line + "\n",
            "second_ok": out["end_tail"] == json.dumps(out["event2"], separators=(",", ":")) + "\n",
        }  # fmt: skip
        row["pass"] = (
            row["bytes_ok"] and row["second_ok"] and out["read"]["n"] == 4
            and out["read"]["warnings"] == ["events.jsonl line 4: not an event (skipped)"]
        )  # fmt: skip
        rows.append(row)
        shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    r0 = rows[0]
    return result("E-heal", pred, f"{n}/{runs} pass; read {r0['read']['n']} events, warnings "
                  f"{r0['read']['warnings']}", n == runs, runs=rows)  # fmt: skip


def probe_pytest(runs: int, pid: str, pred: str, expr: str) -> dict[str, Any]:
    if STATE["arm"] == "control":  # the tests import the new API (append_lines, MAX_LINE_BYTES)
        return result(pid, pred, "n/a in the control arm (the tests import the new API)", True,
                      skipped=True)  # fmt: skip
    rows = []
    for _ in range(runs):
        r = subprocess.run(
            [PY, "-m", "pytest", "tests/improve/test_events.py", "-q", "-k", expr,
             "-p", "no:cacheprovider", "--no-header"],
            cwd=str(ROOT), env=env_for(STATE["tree"]), capture_output=True, text=True,
            timeout=300,
        )  # fmt: skip
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        rows.append({"rc": r.returncode, "summary": tail, "pass": r.returncode == 0})
    n = sum(r["pass"] for r in rows)
    return result(pid, pred, f"{n}/{runs} pass; {rows[0]['summary']}", n == runs, runs=rows)


def _field_runs(tree: Path, runs: int) -> list[dict[str, Any]]:
    scratch = Path(STATE["scratch"])

    def one(i: int) -> dict[str, Any]:
        d = workdir(f"field-{tree.name}")
        out = d / "out.json"
        p = subprocess.run([PY, str(SCRIPT), "--worker", "field", str(out), str(d)],
                           env=env_for(tree), cwd=str(scratch), capture_output=True, text=True,
                           timeout=900)  # fmt: skip
        if p.returncode != 0:
            raise RuntimeError(f"field worker failed: {p.stderr[-1500:]}")
        doc = json.loads(out.read_text())
        shutil.rmtree(d, ignore_errors=True)
        return doc

    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, range(runs)))


def _setup_of(evs: list[dict[str, Any]]) -> list[Any]:
    return [e.get("setup", "<absent>") for e in evs]


def probe_field(runs: int) -> list[dict[str, Any]]:
    tested = _field_runs(STATE["tree"], runs)
    control = _field_runs(STATE["control"], 1)[0] if STATE["arm"] == "tested" else tested[0]
    first = tested[0]

    def identical(name: str, keys: tuple[str, ...]) -> bool:
        return (
            len({sha(json.dumps([r[name][k] for k in keys], sort_keys=True)) for r in tested}) == 1
        )

    rows: list[dict[str, Any]] = []

    def row(fid: str, pred: str, ok: bool, obs: str, **raw: Any) -> None:
        det = all(identical(s, ("events", "record")) for s in SCENARIOS)
        rows.append(result(f"E-field-{fid}", pred + f"; {runs} runs identical (masked)",
                           obs + f"; identical {det}", ok and det, **raw))  # fmt: skip

    f1 = first["F1"]["raw_events"]
    ok1 = (len(f1) == 3 and all(e.get("setup") == SETUP and list(e["setup"]) == list(SETUP)
                                and type(e["setup"]["db_clones"]) is int for e in f1))  # fmt: skip
    row(
        "F1",
        "3 gate_run events (round 0 build_baseline, 1, 2) with setup = the report's, same "
        "order, db_clones an int",
        ok1,
        f"{len(f1)} events; setup {[e.get('setup') == SETUP for e in f1]}; roles "
        f"{[(e['gate'], e['round']) for e in f1]}",
        events=f1,
    )
    f2 = first["F2"]["raw_events"]
    row(
        "F2",
        "an empty timings.setup: no key",
        bool(f2) and all("setup" not in e for e in f2),
        f"{len(f2)} events; setup values {_setup_of(f2)}",
    )
    f3 = first["F3"]["raw_events"]
    row(
        "F3",
        "no timings: no setup and no layer_seconds",
        bool(f3) and all("setup" not in e and "layer_seconds" not in e for e in f3),
        f"{len(f3)} events; keys setup/layer_seconds present: "
        f"{[('setup' in e, 'layer_seconds' in e) for e in f3]}",
    )
    f4 = first["F4"]["raw_events"]
    row(
        "F4",
        "report not rewritten during the gate: none",
        bool(f4) and all("setup" not in e for e in f4),
        f"{len(f4)} events; setup values {_setup_of(f4)}",
    )
    f5 = first["F5"]["raw_events"]
    row(
        "F5",
        "only finite numbers: {'database_server_s': 2.5}",
        bool(f5) and all(e.get("setup") == {"database_server_s": 2.5} for e in f5),
        f"{len(f5)} events; setup values {_setup_of(f5)}",
    )
    f6 = first["F6"]["raw_events"]
    cmds = sorted({e["command"] for e in f6})
    row(
        "F6",
        "two gate commands, only the second rewrites the report: only its events",
        len(cmds) == 2 and all(("setup" in e) == (e["command"] == "./gate1.sh") for e in f6),
        f"commands {cmds}; setup by command {sorted({(e['command'], 'setup' in e) for e in f6})}",
    )

    def keys_of(o: Any) -> set[str]:
        if isinstance(o, dict):
            return set(o) | {k for v in o.values() for k in keys_of(v)}
        if isinstance(o, list):
            return {k for v in o for k in keys_of(v)}
        return set()

    rec_same = all(first[s]["record"] == control[s]["record"] for s in SCENARIOS)
    no_setup_key = all("setup" not in keys_of(first[s]["record"]) for s in SCENARIOS)
    row(
        "F7",
        f"the saved build record has no 'setup' and equals the {BASE_REF} record (masked)",
        rec_same and no_setup_key,
        f"setup key anywhere: {not no_setup_key}; records equal: "
        f"{ {s: first[s]['record'] == control[s]['record'] for s in SCENARIOS} }",
    )

    def strip(e: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in e.items() if k != "setup"}

    ev_same = {s: [strip(e) for e in first[s]["events"]] == [strip(e) for e in control[s]["events"]]
               for s in SCENARIOS}  # fmt: skip
    row(
        "F8",
        f"apart from setup, id, ts each event equals its {BASE_REF} counterpart",
        all(ev_same.values()),
        f"events equal by scenario: {ev_same}",
    )
    return rows


# ---- E-compat

I2_INPUTS = ["i2-run1-events.jsonl", "i2-run2-events.jsonl", "i2-run3-events.jsonl",
             "i3-events.jsonl", "ir-replay-events.jsonl"]  # fmt: skip


def _cli_batch(tree: Path, argvs: list[list[str]], d: Path, tag: str) -> list[dict[str, Any]]:
    inp, out = d / f"{tag}-in.json", d / f"{tag}-out.json"
    inp.write_text(json.dumps(argvs))
    p = subprocess.run([PY, str(SCRIPT), "--worker", "cli", str(inp), str(out)],
                       env=env_for(tree), cwd=str(d), capture_output=True, text=True,
                       timeout=900)  # fmt: skip
    if p.returncode != 0:
        raise RuntimeError(f"cli worker failed: {p.stderr[-1500:]}")
    return json.loads(out.read_text())


def _compat(tree: Path, d: Path, forward: bool) -> dict[str, Any]:
    """Every compared command of E-compat run on `tree` with state dirs under `d`."""
    proj = d / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    outs: dict[str, Any] = {}
    first: list[list[str]] = []
    labels: list[str] = []
    for name in I2_INPUTS:
        st = d / "state" / name.removesuffix("-events.jsonl")
        st.mkdir(parents=True, exist_ok=True)
        text = (DATA / name).read_text()
        if forward and name == "i3-events.jsonl":
            lines = []
            for ln in text.splitlines():
                ev = json.loads(ln)
                if ev.get("kind") == "gate_run":
                    ev["setup"] = dict(SETUP)
                lines.append(json.dumps(ev))
            text = "\n".join(lines) + "\n"
        (st / "events.jsonl").write_text(text)
        if name == "ir-replay-events.jsonl":
            shutil.copy(DATA / "ir-replay-improvements.jsonl", st / "improvements.jsonl")
        base = ["--project", str(proj), "--state", str(st)]
        for tag, a in (("report", ["report"]), ("report-json", ["report", "--json"]),
                       ("report-top10", ["report", "--top", "10"]),
                       ("ledger", ["ledger"]), ("ledger-json", ["ledger", "--json"])):  # fmt: skip
            first.append([*a, *base])
            labels.append(f"{name}:{tag}")
    res1 = _cli_batch(tree, first, d, "b1")
    for lab, r in zip(labels, res1, strict=True):
        outs[lab] = {k: r[k] for k in ("rc", "out", "err")}
    second: list[list[str]] = []
    labels2: list[str] = []
    for name in I2_INPUTS:
        st = d / "state" / name.removesuffix("-events.jsonl")
        base = ["--project", str(proj), "--state", str(st)]
        rep = json.loads(outs[f"{name}:report-json"]["out"])
        ids = [p["id"] for p in rep["proposals"]]
        led = json.loads(outs[f"{name}:ledger-json"]["out"])
        ids += [e["id"] for e in led["entries"] if e.get("id")]
        for i in dict.fromkeys(ids):
            for cmd in ("verify", "draft"):
                for js in (False, True):
                    second.append([cmd, i, *(["--json"] if js else []), *base])
                    labels2.append(f"{name}:{cmd}:{i}{':json' if js else ''}")
    for plan in ("p1", "p2", "p2-final", "p3"):
        st = d / "state" / f"imp-{plan}"
        shutil.rmtree(st, ignore_errors=True)
        second.append(["import-plan", str(PLANS / f"{plan}.json"), "--project", str(proj),
                       "--state", str(st)])  # fmt: skip
        labels2.append(f"import:{plan}")
    res2 = _cli_batch(tree, second, d, "b2")
    for lab, r in zip(labels2, res2, strict=True):
        outs[lab] = {k: r[k] for k in ("rc", "out", "err")}
    for plan in ("p1", "p2", "p2-final", "p3"):
        f = d / "state" / f"imp-{plan}" / "events.jsonl"
        outs[f"import-file:{plan}"] = {"rc": 0, "out": f.read_text() if f.exists() else "",
                                       "err": ""}  # fmt: skip
        shutil.rmtree(d / "state" / f"imp-{plan}", ignore_errors=True)
    return outs


def _bytes_diff(a: dict[str, Any], b: dict[str, Any]) -> tuple[int, int, list[str]]:
    total = differing = 0
    which: list[str] = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        sx = json.dumps(x, sort_keys=True) if x is not None else ""
        sy = json.dumps(y, sort_keys=True) if y is not None else ""
        total += max(len(sx), len(sy))
        if sx != sy:
            which.append(k)
            differing += max(len(sx), len(sy))
    return total, differing, which


def probe_compat(runs: int) -> list[dict[str, Any]]:
    pid, fid = "E-compat", "E-compat-forward"
    skipped = skip_in_control(pid, "0 differing bytes")
    if skipped:
        return [skipped, {**skipped, "id": fid}]
    d = workdir("compat")  # one fixed path for both trees: the log path is in report --json
    old = _compat(STATE["control"], d, False)
    shutil.rmtree(d)
    d.mkdir()
    new = _compat(STATE["tree"], d, False)
    repeats = []
    for _ in range(runs - 1):  # a deterministic probe: every run identical
        shutil.rmtree(d)
        d.mkdir()
        repeats.append(_compat(STATE["tree"], d, False) == new)
    shutil.rmtree(d)
    d.mkdir()
    fwd_new = _compat(STATE["tree"], d, True)
    shutil.rmtree(d)
    d.mkdir()
    fwd_old = _compat(STATE["control"], d, True)
    tot, diff, which = _bytes_diff(old, new)
    ftot, fdiff, fwhich = _bytes_diff(
        {k: v for k, v in old.items() if k.startswith("i3-events")},
        {k: v for k, v in fwd_new.items() if k.startswith("i3-events")},
    )
    # the forward log on the control tree must give the unmodified log's outputs too
    ctot, cdiff, cwhich = _bytes_diff(
        {k: v for k, v in old.items() if k.startswith("i3-events")},
        {k: v for k, v in fwd_old.items() if k.startswith("i3-events")},
    )
    n_cmp = len(old)
    r1 = result(
        pid,
        "0 differing bytes between the two trees",
        f"{diff} differing bytes of {tot} over {n_cmp} comparisons (differing: {which[:5]}); "
        f"{runs} runs identical: {all(repeats)}",
        diff == 0 and all(repeats),
        comparisons=n_cmp,
        compared_bytes=tot,
        differing_bytes=diff,
        differing=which,
    )
    r2 = result(
        fid,
        "0 differing bytes: i3 log + setup on every gate_run = the unmodified log",
        f"tested tree: {fdiff} differing bytes of {ftot} over "
        f"{sum(1 for k in old if k.startswith('i3-events'))} comparisons "
        f"({fwhich[:5]}); control tree: {cdiff} of {ctot} ({cwhich[:5]})",
        fdiff == 0,
        differing_bytes=fdiff,
        control_differing_bytes=cdiff,
    )
    return [r1, r2]


def probe_i2(runs: int) -> dict[str, Any]:
    pid = "E-i2-probes"
    pred = "improve2_probes.py --runs N: 15/15 on both trees, per-probe sha256 lists equal"
    skipped = skip_in_control(pid, pred)
    if skipped:
        return skipped
    outs: dict[str, dict[str, Any]] = {}
    roots: dict[str, str] = {}
    lines: dict[str, str] = {}
    d = workdir("i2")  # the same paths for both trees: the probes' digests cover them
    for label, tree in (("tested", STATE["tree"]), ("control", STATE["control"])):
        out = d / f"out-{label}"
        p = subprocess.run(
            [PY, str(tree / "dogfood/improve2_probes.py"), "--runs", str(runs), "--scratch",
             str(d / "scratch"), "--out", str(out)],
            cwd=str(d), env=env_for(tree), capture_output=True, text=True, timeout=3600,
        )  # fmt: skip
        shutil.rmtree(d / "scratch", ignore_errors=True)
        lines[label] = next(
            (ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("probes:")),
            f"rc={p.returncode}: {p.stderr[-300:]}",
        )
        roots[label] = str(d)
        docs: dict[str, Any] = {}
        for f in sorted(out.glob("*.json")):
            if f.name != "summary.json":
                docs[f.stem] = json.loads(f.read_text())
        outs[label] = docs
    norm = {
        k: {pid_: _norm_paths(doc, roots[k]) for pid_, doc in v.items()} for k, v in outs.items()
    }
    same = norm["tested"] == norm["control"]
    diff = sorted(p for p in set(norm["tested"]) | set(norm["control"])
                  if norm["tested"].get(p) != norm["control"].get(p))  # fmt: skip
    ok = (
        same
        and lines["tested"] == "probes: 15/15 pass"
        and lines["control"] == "probes: 15/15 pass"
    )
    return result(
        pid,
        pred,
        f"tested '{lines['tested']}', control '{lines['control']}'; probe "
        f"documents equal: {same} (differing: {diff})",
        ok,
    )


def _norm_paths(doc: Any, root: str) -> Any:
    """`doc` with the scratch directory `root` replaced by a placeholder in every string."""
    if isinstance(doc, dict):
        return {k: _norm_paths(v, root) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_norm_paths(v, root) for v in doc]
    if isinstance(doc, str):
        return doc.replace(root, "<d>")
    return doc


# ---- E-ledger and E-cost


def probe_ledger(runs: int) -> dict[str, Any]:
    pid = "E-ledger"
    pred = f"{runs}/{runs}: 1600 entries, 0 not JSON, 0 lost or duplicated (measured, not a rule)"
    rows = []
    for _ in range(runs):
        d = workdir("ledger")
        proj, st, go = d / "proj", d / "state", d / "go"
        proj.mkdir()
        st.mkdir()
        procs = [spawn("ledger", proj, st, k, 200, go) for k in range(8)]
        for p in procs:
            read_ready(p)
        go.write_text("go")
        for p in procs:
            last_json(p)
        raw = (st / "improvements.jsonl").read_text().split("\n")
        raw = [ln for ln in raw if ln]
        bad, ids = 0, []
        for ln in raw:
            try:
                ids.append(json.loads(ln)["id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                bad += 1
        want = {f"w{k}-{i}" for k in range(8) for i in range(200)}
        row = {"entries": len(raw), "not_json": bad, "lost": len(want - set(ids)),
               "duplicated": len(ids) - len(set(ids))}  # fmt: skip
        row["pass"] = row["entries"] == 1600 and not (bad or row["lost"] or row["duplicated"])
        rows.append(row)
        shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    tot = {k: sum(r[k] for r in rows) for k in ("not_json", "lost", "duplicated")}
    return result(pid, pred, f"{n}/{runs} as predicted; entries "
                  f"{sorted({r['entries'] for r in rows})}, not JSON {tot['not_json']}, "
                  f"lost {tot['lost']}, dup {tot['duplicated']}",
                  n == runs, runs=rows)  # fmt: skip


def probe_cost(runs: int) -> dict[str, Any]:
    pid = "E-cost"
    pred = "median per append <= control + 1.0 ms (predicted < 0.1 ms: fsync dominates)"
    rows = []
    for _ in range(runs):
        d = workdir("cost")
        t = last_json(spawn("cost", d / "t.jsonl", 1000, tree=STATE["tree"]))
        c = last_json(spawn("cost", d / "c.jsonl", 1000, tree=STATE["control"]))
        rows.append({"tested_median_ms": round(t["median_ms"], 3),
                     "control_median_ms": round(c["median_ms"], 3),
                     "delta_ms": round(t["median_ms"] - c["median_ms"], 3),
                     "pass": t["median_ms"] <= c["median_ms"] + 1.0})  # fmt: skip
        shutil.rmtree(d, ignore_errors=True)
    n = sum(r["pass"] for r in rows)
    ds = [r["delta_ms"] for r in rows]
    return result(pid, pred, f"{n}/{runs} pass; delta of medians {min(ds)} to {max(ds)} ms "
                  f"(tested median {statistics.median(r['tested_median_ms'] for r in rows)} ms, "
                  f"control {statistics.median(r['control_median_ms'] for r in rows)} ms)",
                  n == runs, runs=rows)  # fmt: skip


# ---------------------------------------------------------------- mutants (IH1-IH14)

EV, RB, AN = "lattice/improve/events.py", "lattice/studio/rebuild.py", "lattice/improve/analyze.py"
IH_MUTANTS: dict[str, tuple[str, str, str, str]] = {
    "IH1": (
        "flock removed (append and extend)",
        EV,
        "                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n",
        "                    pass  # MUTANT IH1: no flock\n",
    ),
    "IH2": (
        "extend does not re-read the ids under the lock",
        EV,
        '            seen = set(known or ()) | {str(e.get("id")) for e in '
        "read_events(path).events}\n",
        "            seen = set(known or ())  # MUTANT IH2: no re-read\n",
    ),
    "IH3": ("no tail heal", EV, "        if torn:\n", "        if False:  # MUTANT IH3: no heal\n"),
    "IH4": (
        "os.write return ignored",
        EV,
        "        view = view[n:]\n",
        "        view = view[len(view) :]  # MUTANT IH4: the count is ignored\n",
    ),
    "IH5": (
        "unbounded lock wait (the deadline never comes)",
        EV,
        "            deadline = time.monotonic() + wait\n",
        '            deadline = float("inf")  # MUTANT IH5: waits for ever\n',
    ),
    "IH6": (
        "event dropped on lock timeout",
        EV,
        "                        if not degrade:\n",
        "                        if True:  # MUTANT IH6: dropped\n",
    ),
    "IH7": (
        "cap >= instead of >",
        EV,
        "        if len(line) > MAX_LINE_BYTES:\n",
        "        if len(line) >= MAX_LINE_BYTES:  # MUTANT IH7\n",
    ),
    "IH8": (
        "setup copied from timings.layers",
        RB,
        '    setup = data["timings"].get("setup")\n',
        '    setup = data["timings"].get("layers")  # MUTANT IH8\n',
    ),
    "IH9": (
        "empty timings.setup logged as {}",
        RB,
        "    return kept if kept else None\n",
        "    return kept  # MUTANT IH9: {} kept\n",
    ),
    "IH10": (
        "bool and non-finite values kept",
        RB,
        "        if isinstance(v, int | float) and not isinstance(v, bool) and v - v == 0:\n",
        "        if isinstance(v, int | float):  # MUTANT IH10\n",
    ),
    "IH11": (
        "setup on every gate of the round",
        RB,
        '        setups = [report_setup(g.get("_report")) for g in gates]\n',
        '        setups = [report_setup(g.get("_report")) for g in gates]\n'
        "        setups = [next((s for s in setups if s), None)] * len(setups)"
        "  # MUTANT IH11\n",
    ),
    "IH12": (
        "the ranker adds a gate:<role>:setup cluster",
        AN,
        "            role = _gate_role(ev, seen_shas)\n",
        "            role = _gate_role(ev, seen_shas)\n"
        "            if isinstance(ev.get('setup'), Mapping):  # MUTANT IH12\n"
        "                out.append((f'gate:{role}:setup', Occurrence(ref, ts, seconds="
        "sum(_num(v) or 0.0 for k, v in ev['setup'].items() if str(k).endswith('_s')))))\n",
    ),
    "IH13": (
        "setup stored on the gate dict (build records change)",
        RB,
        "        self._log_gates(run, n, gates, counts, setups)\n",
        "        for _g, _s in zip(gates, setups, strict=True):  # MUTANT IH13\n"
        "            if _s:\n"
        '                _g["setup"] = _s\n'
        "        self._log_gates(run, n, gates, counts, setups)\n",
    ),
    "IH14": (
        "heal prefix written after a final newline too",
        EV,
        '        torn = size > 0 and os.pread(fd, 1, size - 1) != b"\\n"\n',
        "        torn = size > 0  # MUTANT IH14: always the prefix\n",
    ),
}


def run_mutants(ids: list[str], out: Path | None) -> int:
    sys.path.insert(0, str(ROOT / "dogfood"))
    import improve_mutants as im  # the copy-patch-run code of spike 3, imported not edited

    im.MUTANTS.clear()
    im.MUTANTS.update(IH_MUTANTS)
    im.SUITE = ["tests/improve"]
    im.FILES = [EV, RB, AN, "dogfood/improve_hardening_probes.py", "tests/improve/test_events.py"]
    argv = [*(ids or list(IH_MUTANTS)), "--jobs", "2"]
    if out is not None:
        argv += ["--out", str(out)]
    return int(im.main(argv))


# ---------------------------------------------------------------- the runner


def make_control(scratch: Path) -> Path:
    """A shared clone of this repository checked out at a4ab4a1 (nothing is written to it)."""
    dst = scratch / "control"
    if dst.exists():
        return dst
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(dst)],
                   check=True, capture_output=True)  # fmt: skip
    subprocess.run(["git", "-C", str(dst), "checkout", "-q", BASE_REF], check=True,
                   capture_output=True)  # fmt: skip
    return dst


ORDER = ["E-stress", "E-kill", "E-holder-kill", "E-lock-a", "E-lock-b", "E-import-p1",
         "E-import-p2", "E-import-p2-final", "E-import-p3", "E-heal", "E-short", "E-cap",
         "E-field-F1", "E-field-F2", "E-field-F3", "E-field-F4", "E-field-F5", "E-field-F6",
         "E-field-F7", "E-field-F8", "E-compat", "E-compat-forward", "E-i2-probes", "E-ledger",
         "E-cost"]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        WORKERS[sys.argv[2]](*sys.argv[3:])
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--scratch", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--arm", choices=("tested", "control"), default="tested")
    ap.add_argument("--only", nargs="*", default=None, help="probe ids (prefix match)")
    ap.add_argument("--mutants", action="store_true")
    ap.add_argument("--mutant-ids", nargs="*", default=[])
    args = ap.parse_args(argv)
    if args.mutants:
        args.out.mkdir(parents=True, exist_ok=True)
        return run_mutants(args.mutant_ids, args.out / "mutants.json")
    own = args.scratch is None
    scratch = (Path(tempfile.mkdtemp(prefix="ihprobes-")) if own else args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    STATE["scratch"] = str(scratch)
    STATE["arm"] = args.arm
    STATE["control"] = make_control(scratch)
    STATE["tree"] = STATE["control"] if args.arm == "control" else ROOT
    out = args.out / "control" if args.arm == "control" else args.out
    out.mkdir(parents=True, exist_ok=True)
    runs = args.runs

    def want(*pids: str) -> bool:
        return args.only is None or any(p.startswith(o) for p in pids for o in args.only)

    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []

    def run(label: str, fn: Callable[[], Any], *pids: str) -> None:
        if not want(*pids):
            return
        t1 = time.monotonic()
        try:
            got = fn()
        except Exception as exc:  # a probe that cannot run is reported as failed, not hidden
            got = [result(pids[0], "(did not run)", f"ERROR {type(exc).__name__}: {exc}", False)]
        got = got if isinstance(got, list) else [got]
        for g in got:
            g["wall_s"] = round(time.monotonic() - t1, 1)
        rows.extend(got)
        print(f"  [{label} {round(time.monotonic() - t1, 1)} s]", flush=True)

    run("stress", lambda: probe_stress(runs), "E-stress")
    run("kill", lambda: list(probe_kill(runs)), "E-kill", "E-holder-kill")
    run("lock", lambda: probe_lock(runs), "E-lock-a", "E-lock-b")
    run(
        "import",
        lambda: probe_import(runs),
        "E-import-p1",
        "E-import-p2",
        "E-import-p2-final",
        "E-import-p3",
    )
    run("heal", lambda: probe_heal(runs), "E-heal")
    run(
        "short",
        lambda: probe_pytest(
            runs,
            "E-short",
            f"{runs}/{runs}: a <=7-byte write gives one whole line; an OSError on "
            "the 3rd call warns and the next append heals",
            "e_short",
        ),
        "E-short",
    )
    run(
        "cap",
        lambda: probe_pytest(
            runs, "E-cap", f"{runs}/{runs}: 65536 written, 65537 dropped", "e_cap"
        ),
        "E-cap",
    )
    run("field", lambda: probe_field(runs), "E-field")
    run("compat", lambda: probe_compat(runs), "E-compat")
    run("i2", lambda: probe_i2(runs), "E-i2-probes")
    run("ledger", lambda: probe_ledger(runs), "E-ledger")
    run("cost", lambda: probe_cost(runs), "E-cost")
    rows.sort(key=lambda r: ORDER.index(r["id"]) if r["id"] in ORDER else 99)
    print(f"arm: {args.arm}   tree: {STATE['tree']}")
    print("id | predicted | observed | pass")
    for r in rows:
        print(f"{r['id']} | {r['predicted']} | {r['observed']} | "
              f"{'pass' if r['pass'] else 'FAIL'}")  # fmt: skip
    passed = sum(1 for r in rows if r["pass"])
    for f in FINDINGS:
        print(f"finding: {f}")
    print(f"probes: {passed}/{len(rows)} pass")
    for r in rows:
        (out / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
    summary = {
        "arm": args.arm, "runs": runs, "base_ref": BASE_REF,
        "table": [{k: r[k] for k in ("id", "predicted", "observed", "pass")} for r in rows],
        "probes_passed": passed, "probes_total": len(rows),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "findings": FINDINGS, "post_hoc_script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
        "decisions": DECISIONS,
    }  # fmt: skip
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    if own:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
