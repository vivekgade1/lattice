"""Probes of the go-to place (Spike F; docs/results/2026-10-16-goto-place-prereg.md, sections 3-5).

The ground truth below is computed from the sources the app map joins, not from the map: the Spike C
report from its own CLI (``python -m lattice.spec implementation --json``, a separate process),
``ReviewData`` from ``lattice.spec.review``, the raw ``workflows.json`` read with this runner's own
normalisation, and Spike D's report from its CLI. Nothing here imports ``lattice.spec.appmap`` or
``impact_ask`` to decide what is expected; a joined value is recomputed by the code in this file.

Stages (the driver venv has pymysql; the Streak checkout is only read with ``git archive``)::

    "$DRV" dogfood/goto_probes.py --stage dev --streak-ref a0679e9 --streak-ref2 1d00ede \\
        --workflows docs/results/data/2026-10-15-workflow-layer/workflows.json \\
        --scratch "$SCRATCH/p" --out docs/results/data/2026-10-16-goto-place
    "$DRV" dogfood/goto_probes.py --stage qset --questions "$SCRATCH/questions" \\
        --workflows ... --scratch "$SCRATCH/q" --out docs/results/data/2026-10-16-goto-place/qset

The qset stage reads ``qset.json`` and calls the frozen scorer ``goto_score.py`` (both live in the
questions directory, which the builder never opens by hand), records the six candidates as ready change
records, scores the map and the router surfaces once, and writes ``world.json`` for ``goto_agent.py``.
A differing probe is a finding with its raw output; nothing here edits an expectation to make a probe
pass.
"""

# ruff: noqa: E501 -- probe tables and mutant texts are data lines
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import implementation_probes as IP  # noqa: E402
import importer_go_probes as G  # noqa: E402

REPO = Path(os.environ.get("GOTO_PROBES_REPO", HERE.parent)).resolve()
STREAK = G.STREAK
BASE_REF = "HEAD"  # the byte-compat reference: the tip this build was cut from (merged D)
result, not_run, sha = IP.result, IP.not_run, IP.sha
ISO_RE = IP.ISO_RE
SECRET_RE = re.compile(r"(/Users|/private|/var/folders|/tmp/|127\.0\.0\.1|localhost|:\d{4,5}\b)")
OWNER = "gotop"
SECTIONS = ("summary", "rules", "workflows", "machines", "access", "endpoints", "unread")


# ---- small helpers ------------------------------------------------------------------------------


def tpl(path: str) -> str:
    """The runner's own template form of a route path: ``{x}``, ``:x``, integers and UUIDs are ``{}``."""
    path = path.split("?", 1)[0]
    segs = []
    for s in path.split("/"):
        if s[:1] in (":", "*") or (s.startswith("{") and s.endswith("}")):
            s = "{}"
        elif re.fullmatch(r"-?\d+", s) or re.fullmatch(r"[0-9a-fA-F-]{36}", s):
            s = "{}"
        segs.append(s)
    out = "/".join(segs)
    return out.rstrip("/") or "/"


def rkey(route: str) -> str:
    m, _, p = route.partition(" ")
    return f"{m.upper()} {tpl(p)}"


def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http(method: str, url: str, body: Any = None, timeout: float = 300) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def docker_up() -> bool:
    return IP.docker_available()


def all_lattice() -> dict[str, str]:
    """name -> owner label of every ``lattice-`` container."""
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=lattice-", "--format",
         '{{.Names}}|{{.Label "lattice.owner"}}'],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    out = {}
    for ln in proc.stdout.splitlines():
        name, _, owner = ln.partition("|")
        if name.startswith("lattice-"):
            out[name] = owner
    return out


def ours_now() -> list[str]:
    return sorted(n for n, o in all_lattice().items() if o.startswith(OWNER))


def remove_owner(token: str) -> None:
    from lattice.studio.state import remove_owned_containers  # noqa: PLC0415

    remove_owned_containers(token)


def shim_dir(scratch: Path) -> tuple[Path, Path]:
    """A recording ``claude`` shim, first on PATH: any call is logged (PW expects none)."""
    d = scratch / "shim"
    d.mkdir(parents=True, exist_ok=True)
    log = d / "calls.log"
    sh = d / "claude"
    sh.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    sh.chmod(0o755)
    return d, log


class Run:
    def __init__(self, rc: int, out: str, err: str, seconds: float, data: Any = None) -> None:
        self.rc, self.out, self.err, self.seconds, self.data = rc, out, err, seconds, data


class World:
    """The scratch world: clones, workflows, inventories, the dev candidates, the shim."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scratch = Path(args.scratch).resolve()
        self.out = Path(args.out).resolve()
        self.notes: list[str] = []
        self.dirs = {n: self.scratch / n for n in ("S", "S-root2", "S4", "S-stale")}
        self.inv: dict[str, Path] = {}
        self.state: dict[str, Path] = {}
        self.dev: dict[str, Path] = {}
        self.shim, self.shim_log = shim_dir(self.scratch)
        self.seq = 0

    def tmp(self, label: str) -> Path:
        self.seq += 1
        return IP.fresh_tmp(self.scratch, f"{self.seq:04d}-{label}")

    def env(self, tmp: Path, owner: str | None = None, pythonpath: str | None = None) -> dict:
        env = IP.env_for(tmp, pythonpath or REPO)
        env["PATH"] = f"{self.shim}{os.pathsep}{os.environ['PATH']}"
        if owner:
            env["LATTICE_CONTAINER_OWNER"] = owner
        return env

    def py(self, args: Sequence[str], *, cwd: Path | None = None, pythonpath: str | None = None,
           hashseed: str | None = None, timeout: int = 900, label: str = "py") -> Run:  # fmt: skip
        tmp = self.tmp(label)
        env = self.env(tmp, pythonpath=pythonpath)
        if hashseed is not None:
            env["PYTHONHASHSEED"] = hashseed
        t0 = time.perf_counter()
        p = subprocess.run(
            [sys.executable, *args], capture_output=True, text=True, env=env,
            cwd=cwd or REPO, timeout=timeout, check=False,
        )  # fmt: skip
        data = None
        with contextlib.suppress(ValueError):
            data = json.loads(p.stdout)
        return Run(p.returncode, p.stdout, p.stderr, time.perf_counter() - t0, data)


# ---- setup --------------------------------------------------------------------------------------


def set_tables(clone: Path, inventory: Path, app_root: Path, state: Path) -> None:
    """The [implementation], [impact] and [state] tables of a clone's studio.toml (the default
    state directory is inside the repository: Studio would write `.lattice/state/` into the target)."""
    p = clone / "studio.toml"
    text = p.read_text()
    for table in ("implementation", "state", "impact"):
        text = re.sub(rf"(?ms)\n\[{table}\].*?(?=\n\[|\Z)", "", text)
    text = text.rstrip("\n") + (
        f'\n\n[implementation]\ninventory = "{inventory}"\napp_root = "{app_root}"\n'
        f'\n[impact]\n\n[state]\ndir = "{state}"\n'
    )
    p.write_text(text)


def capture_inventory(w: World, name: str, root: Path, workflows: Path | None) -> Path:
    out = w.scratch / "inv" / name
    shutil.rmtree(out, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["-m", "lattice.cli", "import", "inventory", str(root), "--out", str(out)]
    if workflows is not None:
        cmd += ["--workflows", str(workflows)]
    r = w.py(cmd, label=f"capture-{name}")
    assert r.rc == 0, r.err
    w.inv[name] = out
    return out


def setup(w: World, *, build: bool) -> None:
    a = w.args
    w.scratch.mkdir(parents=True, exist_ok=True)
    w.out.mkdir(parents=True, exist_ok=True)
    wf = Path(a.workflows).resolve()
    assert sha(wf.read_bytes()).startswith("af1383db"), "the committed workflows.json"
    for name in ("S", "S-root2"):
        G.archive(STREAK, a.streak_ref, w.dirs[name])
        w.notes.append(IP.rewrite_go_src(w.dirs[name]))
    capture_inventory(w, "S", w.dirs["S"], wf)
    capture_inventory(w, "S-root2", w.dirs["S-root2"], wf)
    for name in ("S", "S-root2"):
        w.state[name] = w.scratch / f"state-{name}"
        set_tables(w.dirs[name], w.inv[name], w.dirs[name], w.state[name])
    if build:
        G.go_build(w.dirs["S"])
    # S-stale: S after its inventory was captured, one source file edited
    shutil.rmtree(w.dirs["S-stale"], ignore_errors=True)
    shutil.copytree(
        w.dirs["S"], w.dirs["S-stale"], symlinks=True, ignore=shutil.ignore_patterns("bin")
    )
    target = w.dirs["S-stale"] / "internal" / "habits" / "routes.go"
    target.write_text(target.read_text() + "\n// edited after the capture\n")
    w.state["S-stale"] = w.scratch / "state-S-stale"
    set_tables(w.dirs["S-stale"], w.inv["S"], w.dirs["S-stale"], w.state["S-stale"])
    # S4: the static inventory of 1d00ede
    G.archive(STREAK, a.streak_ref2, w.dirs["S4"])
    w.notes.append(IP.rewrite_go_src(w.dirs["S4"]))
    capture_inventory(w, "S4", w.dirs["S4"], None)
    w.state["S4"] = w.scratch / "state-S4"
    set_tables(w.dirs["S4"], w.inv["S4"], w.dirs["S4"], w.state["S4"])


# ---- the dev candidates (text edits of the clone's own files, one per kind of the registration) --


def make_dev_candidates(w: World) -> None:
    root = w.scratch / "dev-candidates"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    s = w.dirs["S"]
    md0 = (s / "REQUIREMENTS.md").read_text()
    sp0 = (s / "spec.py").read_text()
    tr0 = (s / "spec.trace.json").read_text()

    def swap(text: str, old: str, new: str, count: int = 1) -> str:
        assert old in text, old[:60]
        return text.replace(old, new, count)

    def make(name: str, md: str = md0, sp: str = sp0) -> None:
        d = root / name
        d.mkdir()
        (d / "REQUIREMENTS.md").write_text(md)
        (d / "spec.py").write_text(sp)
        (d / "spec.trace.json").write_text(tr0)
        w.dev[name] = d

    make(
        "d1_limit",
        swap(md0, "A user has at most 50 active (non-archived, non-deleted) habits;",
             "A user has at most 5 active (non-archived, non-deleted) habits;"),
        swap(sp0, "'is_null(Habit.deleted_at)) <= 50'", "'is_null(Habit.deleted_at)) <= 5'"),
    )  # fmt: skip
    block = swap(sp0[sp0.index('Scenario("habit_name_bounds"') :], '"n" * 101', '"n" * 61')
    start = sp0.index('Scenario("habit_name_bounds"')
    end = start + sp0[start:].index('Scenario("habit_description_bounds"')
    new_block = swap(swap(sp0[start:end], '"n" * 101', '"n" * 61'), '"n" * 100', '"n" * 60', 2)
    assert block
    make(
        "d2_bound",
        swap(md0, "`name` is 1 to 100 characters", "`name` is 1 to 60 characters"),
        sp0[:start] + new_block + sp0[end:],
    )
    inv_old = '        Invariant("habit_version_positive", "A habit\'s version starts at 1.",\n'
    make(
        "d3_invariant",
        md0 + "\n- A habit version is never negative.\n",
        swap(sp0, inv_old,
             '        Invariant("every_habit_version_nonnegative", "A habit version is not negative.",\n'
             '                  "Habit.version >= 0"),\n' + inv_old),
    )  # fmt: skip
    sp4 = swap(
        sp0, '        "delete_device": ("DELETE", "/v1/devices/1", None, ("devices", -1)),\n', ""
    )
    sp4 = swap(sp4, '"add_device": 401, "delete_device": 401,', '"add_device": 401,')
    sp4 = swap(
        sp4,
        '"progress": 404, "history": 404, "delete_device": 404,',
        '"progress": 404, "history": 404,',
    )
    sp4 = swap(sp4, '                "delete_device": 204,\n', "")
    md4 = swap(
        md0, "- `DELETE /v1/devices/{id}` answers 204 and deletes the caller's device row.\n", ""
    )
    make("d4_removed_endpoint", md4, sp4)
    sp5 = swap(sp0, '"get_habit": 404, "patch_habit": (404, None, HABIT_UNCHANGED),',
               '"get_habit": 403, "patch_habit": (404, None, HABIT_UNCHANGED),')  # fmt: skip
    make(
        "d5_access",
        swap(
            md0,
            "There are no roles or admins:",
            "There are no roles or admins (a stranger is refused):",
        ),
        sp5,
    )
    make(
        "d6_reword",
        swap(
            md0,
            "anything else answers 422 `validation_failed`",
            "anything else is answered with 422 `validation_failed`",
        ),
    )


class Scripted:
    """A generator that plays one candidate directory per drafted change (the edit returns its
    REQUIREMENTS.md, the draft its spec.py and spec.trace.json). Counts its calls."""

    def __init__(self) -> None:
        self.cand: Path | None = None
        self.calls = 0
        self.lock = threading.Lock()

    def generate(self, system: str, messages: Sequence[Any]) -> Any:
        from lattice.llm.client import Generation, Usage  # noqa: PLC0415
        from lattice.spec.draft import system_prompt  # noqa: PLC0415
        from lattice.studio import server as sm  # noqa: PLC0415

        with self.lock:
            self.calls += 1
        if system == sm.EDIT_SYSTEM:
            assert self.cand is not None
            text = f"{sm.BEGIN}\n{(self.cand / 'REQUIREMENTS.md').read_text()}{sm.END}\n"
        elif system == system_prompt():
            assert self.cand is not None
            text = (
                f"```python\n{(self.cand / 'spec.py').read_text()}```\n\n"
                f"```json\n{(self.cand / 'spec.trace.json').read_text()}\n```\n"
            )
        else:
            text = json.dumps(
                {"kind": "not_specified", "answer": "Not specified (scripted).",
                 "explanation": "The scripted generator answers every question this way.",
                 "citations": []}
            )  # fmt: skip
        return Generation(text=text, usage=Usage(0, 0), latency_s=0.0, stop_reason="end_turn")


class Studio:
    """A Studio server in this process over a project, with a scripted generator."""

    def __init__(self, w: World, project: Path, gen: Any = None, owner: str | None = None) -> None:
        from lattice.studio.server import make_server  # noqa: PLC0415

        self.w, self.project, self.owner = w, project, owner
        self.gen = gen if gen is not None else Scripted()
        self._cwd, self._path = os.getcwd(), list(sys.path)
        if owner:
            os.environ["LATTICE_CONTAINER_OWNER"] = owner
        self.srv = make_server(project, 0, generator=self.gen)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def get(self, path: str) -> tuple[int, Any]:
        return http("GET", self.base + path)

    def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        return http("POST", self.base + path, body if body is not None else {})

    def draft(self, cand: Path, text: str, limit: float = 1800) -> dict[str, Any]:
        self.gen.cand = cand
        status, out = self.post("/api/changes", {"request": text})
        assert status in (200, 201, 202), out
        cid = out["id"]
        end = time.monotonic() + limit
        while time.monotonic() < end:
            _, c = self.get(f"/api/changes/{cid}")
            if c["status"] not in ("drafting", "checking"):
                return c
            time.sleep(0.5)
        raise AssertionError(f"{cid} did not finish")

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        os.chdir(self._cwd)
        sys.path[:] = self._path
        if self.owner:
            os.environ.pop("LATTICE_CONTAINER_OWNER", None)
            remove_owner(self.owner)


# ---- the independent truth ----------------------------------------------------------------------


def load_spec_of(clone: Path) -> tuple[Any, Any]:
    return IP.load_clone_spec(clone, clone.name)


def report_cli(w: World, name: str) -> dict[str, Any]:
    """Spike C's report from its own CLI, in a separate process (the map's source)."""
    clone = w.dirs[name]
    inv = w.inv["S4"] if name == "S4" else w.inv["S"]
    r = w.py(
        ["-m", "lattice.spec", "implementation", "--spec", "spec:SPEC", "--trace",
         "spec.trace.json", "--inventory", str(inv), "--app-root", str(clone), "--json"],
        cwd=clone, label=f"report-{name}",
    )  # fmt: skip
    assert r.rc == 0 and r.data is not None, r.err[-500:]
    return r.data


def appmap_cli(w: World, name: str, *, section: str | None = None, root: Path | None = None,
               hashseed: str | None = None) -> Run:  # fmt: skip
    clone = root or w.dirs[name]
    inv = w.inv["S4"] if name == "S4" else w.inv["S"]
    cmd = ["-m", "lattice.spec", "appmap", "--spec", "spec:SPEC", "--trace", "spec.trace.json",
           "--inventory", str(inv), "--app-root", str(clone), "--json"]  # fmt: skip
    if section:
        cmd += ["--section", section]
    return w.py(cmd, cwd=clone, hashseed=hashseed, label=f"appmap-{name}")


def raw_flows(w: World) -> list[dict[str, Any]]:
    return json.loads(Path(w.args.workflows).read_text())["flows"]


def recount_observed(flows: list[dict[str, Any]], row: str, col: str) -> list[tuple[str, int, Any]]:
    """The observed answers of the cell (row, col), recounted from the raw workflows.json: each
    request step of a verified flow whose route is the cell's and whose caller (none: no token) is
    the row."""
    want = rkey(col)
    out = []
    for f in flows:
        if f.get("status") != "verified":
            continue
        for s in f.get("steps") or []:
            if s.get("kind") == "await" or not s.get("route"):
                continue
            if rkey(str(s["route"])) == want and (s.get("caller") or "no token") == row:
                out.append((str(f["id"]), int(s["i"]), s.get("expect_status")))
    return sorted(out)


def walk_codes(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        if obj.startswith("code:"):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out += walk_codes(v)
    elif isinstance(obj, list):
        for v in obj:
            out += walk_codes(v)
    return out


def review_of(w: World, name: str) -> Any:
    from lattice.spec.review import _build  # noqa: PLC0415

    spec, trace = load_spec_of(w.dirs[name])
    return spec, trace, _build(spec, trace, None, None, "x")


BASIS = {"implemented_verified": "observed", "implemented_unverified": "static",
         "required_not_implemented": "inferred", "no_element": "none"}  # fmt: skip


def compare_map(w: World, name: str, m: dict[str, Any]) -> dict[str, Any]:
    """Every field of the map against its source; the returned counts say how many were compared
    and the list of differences (empty means the probe holds)."""
    rep = report_cli(w, name)
    spec, trace, rd = review_of(w, name)
    flows_raw = raw_flows(w) if name != "S4" else []
    stale_now = rep["inventory"]["stale"] is not None  # a stale inventory observes nothing
    diffs: list[str] = []
    n = 0

    def eq(what: str, a: Any, b: Any) -> None:
        nonlocal n
        n += 1
        if a != b:
            diffs.append(f"{what}: map {json.dumps(a)[:120]} != source {json.dumps(b)[:120]}")

    rows = {r.id: r for r in rd.rule_rows}
    by = {e["id"]: e for e in rep["rules"]}
    eq("rules ids", [r["id"] for r in m["rules"]], [e["id"] for e in rep["rules"]])
    wf_of: dict[str, list[str]] = {}
    for f in rep["flows"]:
        for r in f["rules"]:
            wf_of.setdefault(r, []).append(f["id"])
    mc_of: dict[str, list[str]] = {}
    for mc in rep["machines"]:
        for r in mc["rules"]:
            mc_of.setdefault(r, []).append(mc["key"])
    for r in m["rules"]:
        e = by[r["id"]]
        eq(f"{r['id']} impl_status", r["impl_status"], e["status"])
        eq(f"{r['id']} basis", r["basis"], BASIS[e["status"]])
        eq(f"{r['id']} text", r["text"], rows[r["id"]].text)
        eq(f"{r['id']} spec_status", r["spec_status"], rows[r["id"]].status)
        eq(f"{r['id']} reasons", r["reasons"], e.get("reasons") or [])
        eq(f"{r['id']} missing", r["missing"], e.get("missing") or [])
        eq(f"{r['id']} impact_label", r["impact_label"], e.get("impact_label"))
        eq(f"{r['id']} elements", r["elements"],
           [{k: el[k] for k in ("key", "kind", "present", "verified", "basis")} for el in e["elements"]])  # fmt: skip
        eq(f"{r['id']} evidence", r["evidence"], sorted({x for el in e["elements"] for x in el["evidence"]}))  # fmt: skip
        eq(f"{r['id']} workflows", r["workflows"], sorted(wf_of.get(r["id"], [])))
        eq(f"{r['id']} machines", r["machines"], sorted(mc_of.get(r["id"], [])))
        eq(
            f"{r['id']} cells",
            r["cells"],
            [x for x in rows[r["id"]].links if x.startswith("cell:")],
        )
        eq(f"{r['id']} last_check", r["last_check"], None)
    raw_by = {str(f["id"]): f for f in flows_raw}
    eq(
        "workflow ids",
        sorted(f["id"] for f in m["workflows"]),
        sorted(f["id"] for f in rep["flows"]),
    )
    for f in m["workflows"]:
        src = next(x for x in rep["flows"] if x["id"] == f["id"])
        eq(f"{f['id']} row", {k: v for k, v in f.items() if k != "steps"}, src)
        steps = [
            {"i": s["i"], "method": str(s["method"]).upper(), "route": rkey(s["route"]),
             "caller": s.get("caller"), "expect_status": s.get("expect_status")}
            for s in raw_by.get(f["id"], {}).get("steps", []) if s.get("kind") != "await"
        ]  # fmt: skip
        got = [
            {k: s[k] for k in ("i", "method", "route", "caller", "expect_status")}
            for s in f["steps"]
        ]
        eq(f"{f['id']} steps", got, steps)
        eq(f"{f['id']} step evidence is code:", all(e.startswith("code:") for s in f["steps"] for e in s["evidence"]), True)  # fmt: skip
    # machines
    spec_edges = {(a, b) for sm in spec.machines for a, b in sm.edges}
    for mc in m["machines"]:
        src = next(x for x in rep["machines"] if x["key"] == mc["key"])
        eq(
            f"{mc['key']} row",
            {k: v for k, v in mc.items() if k not in ("spec_target", "edges_detail")},
            src,
        )
        code_edges = {tuple(e.split(" -> ", 1)) for e in src["edges"]}
        eq(f"{mc['key']} edges_detail",
           sorted((d["from"], d["to"], d["in_spec"], d["in_code"]) for d in mc["edges_detail"]),
           sorted((a, b, (a, b) in spec_edges, (a, b) in code_edges) for a, b in spec_edges | code_edges))  # fmt: skip
    eq("machine count", len(m["machines"]), len(rep["machines"]))
    # access
    eq("groups", m["access"]["groups"],
       [{"prefix": g.prefix, "rows": list(g.rows), "columns": list(g.columns)} for g in rd.access_groups])  # fmt: skip
    routes = {r["key"]: r for r in rep["routes"]}
    eps = {x["key"]: x for x in rep["endpoints"]}
    n_cells = 0
    for g in rd.access_groups:
        for row in g.rows:
            for col in g.columns:
                n_cells += 1
                c = next(x for x in m["access"]["cells"] if x["row"] == row and x["col"] == col)
                k = rkey(col)
                rt = routes.get(k)
                eq(f"cell {row}|{col} spec", c["spec"], g.cells[row, col])
                eq(f"cell {row}|{col} route", c["route"], k if rt else None)
                eq(f"cell {row}|{col} auth", c["auth"], rt["auth"] if rt else None)
                eq(f"cell {row}|{col} refusals", c["refusals"], rt["refusals"] if rt else None)
                eq(f"cell {row}|{col} route_status", c["route_status"], eps[k]["status"] if k in eps else None)  # fmt: skip
                eq(f"cell {row}|{col} evidence", c["evidence"], rt["evidence"] if rt else [])
                got = sorted((o["flow"], o["step"], o["status"]) for o in c["observed"])
                eq(
                    f"cell {row}|{col} observed",
                    got,
                    [] if stale_now else recount_observed(flows_raw, row, col),
                )
    eq("cells", len(m["access"]["cells"]), n_cells)
    eq("endpoints", m["endpoints"], rep["endpoints"])
    eq("excluded", m["excluded"], rep["excluded"])
    eq("unread", [{k: v for k, v in u.items() if k != "at"} for u in m["unread"]], rep["unread"])
    stale = rep["inventory"]["stale"]
    eq("inputs.inventory", m["inputs"]["inventory"],
       {"extract_sha256": rep["inventory"]["extract_sha256"], "workflows_sha256": rep["inventory"]["workflows_sha256"],
        "run_mode": rep["inventory"]["run_mode"], "stale": stale})  # fmt: skip
    return {"compared": n, "differences": diffs[:15], "n_differences": len(diffs), "report": rep}


def counts_of(m: dict[str, Any]) -> dict[str, Any]:
    s = m["summary"]
    cells = m["access"]["cells"]
    return {
        "rules": s["rules"]["impl_status"],
        "flows": s["workflows"]["effective"],
        "machines": [(x["key"], len(x["edges"])) for x in m["machines"]],
        "cells": len(cells),
        "unspecified": sum(1 for c in cells if c["spec"] == "·"),
        "observed_cells": sum(1 for c in cells if c["observed"]),
    }


# ---- PM rows ------------------------------------------------------------------------------------


def probe_pm1(w: World, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cmp_ = compare_map(w, "S", maps["S"])
    c = counts_of(maps["S"])
    want = {"implemented_verified": 108, "implemented_unverified": 34, "no_element": 6}
    ok_counts = (
        c["rules"] == want
        and c["flows"] == {"failed": 8, "unverified": 1, "verified": 59}
        and c["machines"] == [("impl:machine:nudges.delivery", 3)]
        and c["cells"] == 78
        and c["unspecified"] == 12
    )
    cmp_.pop("report")
    return result(
        "PM1",
        "S: every map field equals its source (Spike C report, ReviewData, a recount from workflows.json)",
        {"rules": "108/34/0/6", "flows": "59/8/1", "machine": "1 with 3 edges", "cells": "78 (12 ·)",
         "observed cells": "15-30 (p)", "differences": 0},
        {**c, **cmp_},
        cmp_["n_differences"] == 0 and ok_counts and 15 <= c["observed_cells"] <= 30,
    )  # fmt: skip


def probe_pm2(w: World, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from lattice.spec import implementation as impl  # noqa: PLC0415

    bad: list[str] = []
    total = 0
    for name, m in maps.items():
        clone = w.dirs[name]
        inv = impl.load_inventory(w.inv["S4"] if name == "S4" else w.inv["S"], clone)
        for loc in sorted(set(walk_codes(m))):
            total += 1
            parsed = impl.parse_code_target(loc)
            if parsed is None:
                bad.append(f"{name} {loc}: not code:<path>:<line>")
                continue
            rel, line = parsed
            f = clone / rel
            lines = f.read_text(errors="replace").split("\n") if f.is_file() else []
            quote = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
            if len("".join(quote.split())) < 8:
                why = None if (f.is_file() and 1 <= line <= len(lines)) else "no such line"
            else:
                why = impl.check_code(clone, inv, {"target": loc, "quote": quote})
            if why:
                bad.append(f"{name} {loc}: {why}")
    return result(
        "PM2", "every code: location of S and S4 passes Spike C's check_code",
        "100%", {"locations": total, "bad": bad[:10], "n_bad": len(bad)}, total > 0 and not bad,
    )  # fmt: skip


def probe_pm3(w: World, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    m = maps["S4"]
    c = counts_of(m)
    cells = m["access"]["cells"]
    spec_only = sorted(e["key"] for e in m["endpoints"] if e["status"] == "spec_only")
    null_cells = sum(1 for x in cells if x["route"] is None)
    notes = [n["code"] for n in m["summary"]["notes"]]
    ok = (
        c["rules"].get("required_not_implemented") == 29
        and spec_only
        == sorted(
            [
                "DELETE /v1/devices/{}",
                "GET /v1/home",
                "GET /v1/nudges",
                "GET /v1/sync",
                "POST /v1/devices",
                "POST /v1/nudges/{}/dismiss",
            ]
        )  # fmt: skip
        and null_cells == 18
        and "no_workflows_capture" in notes
        and m["machines"] == []
    )
    return result(
        "PM3", "S4: 29 rules not built, 6 spec_only endpoints = 18 cells with route null, no machines",
        {"required_not_implemented": 29, "spec_only": 6, "cells with route null": 18, "note": "no_workflows_capture", "machines": 0},
        {"required_not_implemented": c["rules"].get("required_not_implemented"), "spec_only": spec_only,
         "cells with route null": null_cells, "notes": notes, "machines": len(m["machines"])},
        ok,
    )  # fmt: skip


def probe_pm4(w: World, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    m = maps["S-stale"]
    st = m["inputs"]["inventory"]["stale"]
    c = counts_of(m)
    notes = [n["code"] for n in m["summary"]["notes"]]
    ok = (
        st is not None
        and st["counts"] == {"added": 0, "changed": 1, "removed": 0}
        and st["changed"] == ["internal/habits/routes.go"]
        and "implemented_verified" not in c["rules"]
        and c["observed_cells"] == 0
        and "stale" in notes
        and not any(r["basis"] == "observed" for r in m["rules"])
    )
    return result(
        "PM4", "S-stale: changed 1, 0 implemented_verified, nothing observed, the stale note",
        {"stale.counts": {"changed": 1}, "implemented_verified": 0, "observed cells": 0, "note": "stale"},
        {"stale": st and st["counts"], "rules": c["rules"], "observed cells": c["observed_cells"], "notes": notes},
        ok,
    )  # fmt: skip


def probe_pm6(w: World, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rep = report_cli(w, "S")
    m = maps["S"]
    want = sorted((u["file"], u["line"], u["what"], u["reason"]) for u in rep["unread"])
    got = sorted((u["file"], u["line"], u["what"], u["reason"]) for u in m["unread"])
    ats = all(u["at"] == f"code:{u['file']}:{u['line']}" for u in m["unread"])
    return result(
        "PM6", "unread: the 4 S items listed, each with its code: location",
        4, {"n": len(m["unread"]), "ats_ok": ats}, got == want and len(got) == 4 and ats,
    )  # fmt: skip


# ---- PE: elements and their neighbourhood -------------------------------------------------------


def join_truth(rep: dict[str, Any], rd: Any, trace: Any, flows_raw: list[dict[str, Any]], m: dict[str, Any],
               key: str) -> dict[str, list[str]]:  # fmt: skip
    """The neighbourhood of `key`, recomputed from the report, ReviewData, the trace and the raw
    workflows (not from the map's own tables)."""
    out: dict[str, set[str]] = {
        k: set() for k in ("rules", "workflows", "machines", "cells", "routes")
    }
    cells = [(row, col) for g in rd.access_groups for row in g.rows for col in g.columns]
    cell_keys = {f"cell:{r}|{c}" for r, c in cells}
    rules = {e["id"]: e for e in rep["rules"]}
    links = {r.id: list(r.links) for r in rd.rule_rows}
    if key.startswith("rule:"):
        rid = key[5:]
        for f in rep["flows"]:
            if rid in f["rules"]:
                out["workflows"].add(f["id"])
        for mc in rep["machines"]:
            if rid in mc["rules"]:
                out["machines"].add(mc["key"])
        out["cells"] |= {x for x in links[rid] if x.startswith("cell:") and x in cell_keys}
        out["routes"] |= {el["key"] for el in rules[rid]["elements"] if el["kind"] == "route"}
    elif key.startswith("wf-"):
        f = next(x for x in rep["flows"] if x["id"] == key)
        out["rules"] |= {f"rule:{r}" for r in f["rules"]}
        out["routes"] |= {f"impl:route:{r}" for r in f["routes"]}
        out["machines"] |= {mc["key"] for mc in rep["machines"] if mc["id"] in f["machine_refs"] or mc["key"] in f["machine_refs"]}  # fmt: skip
        if f["effective"] == "verified":
            for r, c in cells:
                if any(o[0] == key for o in recount_observed(flows_raw, r, c)):
                    out["cells"].add(f"cell:{r}|{c}")
    elif key.startswith("impl:route:"):
        k = key[len("impl:route:") :]
        rt = next((x for x in rep["routes"] if x["key"] == k), None)
        if rt:
            out["rules"] |= {f"rule:{r}" for r in rt["rules"]}
            out["workflows"] |= set(rt["flows"])
        out["cells"] |= {f"cell:{r}|{c}" for r, c in cells if rkey(c) == k}
    elif key.startswith("cell:"):
        row, _, col = key[5:].partition("|")
        k = rkey(col)
        out["rules"] |= {f"rule:{i}" for i, ls in links.items() if key in ls}
        if any(x["key"] == k for x in rep["endpoints"]):
            out["routes"].add(f"impl:route:{k}")
        if rep["inventory"]["stale"] is None:
            out["workflows"] |= {o[0] for o in recount_observed(flows_raw, row, col)}
    else:  # a machine
        mc = next(x for x in rep["machines"] if x["key"] == key)
        out["rules"] |= {f"rule:{r}" for r in mc["rules"]}
        out["workflows"] |= {f["id"] for f in rep["flows"] if mc["id"] in f["machine_refs"] or key in f["machine_refs"]}  # fmt: skip
    return {k: sorted(v) for k, v in out.items()}


def probe_pe(w: World, st: Any, maps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rep = report_cli(w, "S")
    spec, trace, rd = review_of(w, "S")
    flows_raw = raw_flows(w)
    m = maps["S"]
    rules_ = rep["rules"]
    unver = next(e["id"] for e in rules_ if e["status"] == "implemented_unverified")
    wf_ver = next(f["id"] for f in rep["flows"] if f["effective"] == "verified")
    wf_fail = next(f["id"] for f in rep["flows"] if f["effective"] == "failed")
    wf_unv = next(f["id"] for f in rep["flows"] if f["effective"] == "unverified")
    seen_cell = next(f"cell:{c['row']}|{c['col']}" for c in m["access"]["cells"] if c["observed"])
    keys = [
        "rule:R4", f"rule:{unver}", "rule:R123",
        wf_ver, wf_fail, wf_unv,
        seen_cell, "cell:ben|DELETE /v1/habits/{}", next(f"cell:{c['row']}|{c['col']}" for c in m["access"]["cells"] if c["spec"] == "·"),
        "impl:route:POST /v1/habits", "impl:route:GET /health", "impl:route:PUT /test/clock",
        "impl:machine:nudges.delivery",
    ]  # fmt: skip
    diffs = []
    for k in keys:
        status, e = st.get("/api/appmap/element?key=" + urllib.request.quote(k, safe=""))
        if status != 200:
            diffs.append(f"{k}: {status}")
            continue
        truth = join_truth(rep, rd, trace, flows_raw, m, k)
        if e["touches"] != truth:
            diffs.append(f"{k}: {json.dumps(e['touches'])[:160]} != {json.dumps(truth)[:160]}")
        if e["basis"] != "static" or "not decided" not in e["meaning"]:
            diffs.append(f"{k}: not labelled static")
    status, miss = st.get("/api/appmap/element?key=rule:R999")
    near_ok = (
        status == 404
        and miss.get("code") == "unknown_key"
        and len(miss.get("nearest", [])) == 5
        and all(
            n in {x["id"] for x in []} or n.startswith(("rule:", "impl:", "cell:", "wf-"))
            for n in miss["nearest"]
        )  # fmt: skip
    )
    return result(
        "PE", "GET /api/appmap/element: touches equals the join recomputed from the sources (13 keys); unknown key: 404 with 5 nearest",
        "12/12 + 404", {"keys": len(keys), "differences": diffs, "unknown": {"status": status, "nearest": miss.get("nearest")}},
        not diffs and near_ok,
    )  # fmt: skip


# ---- PI, PIS, PIC: the impact route -------------------------------------------------------------


def engine_cli(w: World, project: Path, cid: str, state_dir: Path, *, tiers: str = "23",
               out: Path | None = None, extra: Sequence[str] = ()) -> Run:  # fmt: skip
    cmd = ["-m", "lattice.spec", "impact", "--project", str(project), "--change", cid, "--state-dir",
           str(state_dir), "--tiers", tiers, "--baseline", "none", "--json", *extra]  # fmt: skip
    if out is not None:
        cmd += ["--out", str(out)]
    return w.py(cmd, label=f"impact-{cid}")


def record_changes(w: World, st: Studio, cands: dict[str, Path]) -> dict[str, dict[str, Any]]:
    """Draft each candidate as a ready change record, in order (c1, c2, ...)."""
    out = {}
    for name, d in cands.items():
        t0 = time.perf_counter()
        c = st.draft(d, f"dev change {name}")
        out[name] = {"id": c["id"], "status": c["status"], "seconds": round(time.perf_counter() - t0, 1),
                     "checks_impact": (c.get("checks") or {}).get("impact")}  # fmt: skip
        assert c["status"] == "ready", (name, c.get("error"))
    return out


def probe_pi_pic(w: World, st: Studio, recs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    state = w.state["S"]
    pi_rows, pic_rows = [], []
    for name, rec in recs.items():
        cid = rec["id"]
        status, body = st.get(f"/api/impact?change={cid}")
        cli = engine_cli(w, w.dirs["S"], cid, state)
        same = (
            status == 200
            and body["state"] == "ok"
            and cli.data is not None
            and canon(body["report"]) == canon(cli.data)
        )
        pi_rows.append({"change": name, "id": cid, "identical": same, "state": body.get("state"),
                        "tier2": len((body.get("report") or {}).get("tier2", {}).get("determined", [])),
                        "tier3": len((body.get("report") or {}).get("tier3", {}).get("locations", []))})  # fmt: skip
        on_disk = json.loads((state / "changes" / f"{cid}.json").read_text())["record"]["checks"][
            "impact"
        ]
        pic_rows.append({"change": name, "id": cid, "equal": body.get("studio_check") == on_disk,
                         "null": on_disk is None})  # fmt: skip
    pi = result("PI", "GET /api/impact report vs Spike D's CLI (change_impact's default flags), canonical bytes, on the 6 dev candidates",
                "6/6 identical", pi_rows, all(r["identical"] for r in pi_rows) and len(pi_rows) == 6)  # fmt: skip
    pic = result("PIC", "studio_check equals the record's checks.impact verbatim",
                 "6/6 equal", pic_rows, all(r["equal"] for r in pic_rows) and len(pic_rows) == 6)  # fmt: skip
    return [pi, pic]


def probe_pis(w: World, cid: str) -> dict[str, Any]:
    """tier1_saved on S: no key, no file, a matching saved report, the same file after the candidate
    changed by one byte (a hash of it stands for the byte)."""
    clone, state = w.dirs["S"], w.state["S"]
    reports = w.scratch / "reports"
    shutil.rmtree(reports, ignore_errors=True)
    reports.mkdir(parents=True)
    toml = (clone / "studio.toml").read_text()
    outs: dict[str, Any] = {}
    st0 = Studio(w, clone)
    try:
        outs["no key"] = st0.get(f"/api/impact?change={cid}")[1]["tier1_saved"]["state"]
    finally:
        st0.close()
    (clone / "studio.toml").write_text(
        toml.replace("[impact]\n", f'[impact]\nreports = "{reports}"\n', 1)
    )
    st1 = Studio(w, clone)
    try:
        outs["no file"] = st1.get(f"/api/impact?change={cid}")[1]["tier1_saved"]["state"]
        rep = engine_cli(w, clone, cid, state).data
        assert rep is not None
        saved = copy.deepcopy(rep)
        saved["tiers"]["1"] = "run"
        saved["tier1"]["failing"] = [
            {"id": "t1-probe", "kind": "check_fails", "target": "scenario:x", "rules": ["R1"],
             "message": "observed by the probe's synthetic tier 1", "tier": 1}
        ]  # fmt: skip
        path = reports / f"{cid}.json"
        path.write_text(json.dumps(saved))
        s = st1.get(f"/api/impact?change={cid}")[1]["tier1_saved"]
        outs["matching"] = (
            s["state"] if s["tier1"] == saved["tier1"] else f"{s['state']} (tier1 differs)"
        )
        changed = copy.deepcopy(saved)
        changed["input"]["candidate"]["spec"] = hashlib_flip(changed["input"]["candidate"]["spec"])
        path.write_text(json.dumps(changed))
        s = st1.get(f"/api/impact?change={cid}")[1]["tier1_saved"]
        outs["one byte of the candidate"] = (
            s["state"] if s["differs"] == ["candidate_sha256"] and s["tier1"] is None else f"{s['state']} {s['differs']}"
        )  # fmt: skip
    finally:
        st1.close()
        (clone / "studio.toml").write_text(toml)
        shutil.rmtree(reports, ignore_errors=True)
    want = {
        "no key": "not_configured",
        "no file": "absent",
        "matching": "shown",
        "one byte of the candidate": "mismatch",
    }
    return result(
        "PIS", "tier1_saved: shown only when the hashes match",
        want, outs, outs == want,
    )  # fmt: skip


def hashlib_flip(h: str) -> str:
    return ("0" if h[0] != "0" else "1") + h[1:]


# ---- PM5: the states ----------------------------------------------------------------------------


def probe_pm5(w: World) -> dict[str, Any]:
    root = w.scratch / "states"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    out: dict[str, Any] = {}

    def variant(name: str) -> Path:
        d = root / name
        shutil.copytree(w.dirs["S"], d, symlinks=True, ignore=shutil.ignore_patterns("bin"))
        set_tables(d, w.inv["S"], d, root / f"state-{name}")
        return d

    def state_of(d: Path) -> str:
        st = Studio(w, d)
        try:
            status, doc = st.get("/api/appmap")
            return (
                f"{status} {doc['state']} keys={sorted(doc)}"
                if doc["state"] != "ok"
                else f"{status} ok"
            )
        finally:
            st.close()

    d = variant("not_configured")
    t = (d / "studio.toml").read_text()
    (d / "studio.toml").write_text(re.sub(r"(?ms)\n\[implementation\].*?(?=\n\[|\Z)", "", t))
    out["not_configured"] = state_of(d)
    d = variant("inventory_missing")
    (d / "studio.toml").write_text(
        (d / "studio.toml").read_text().replace(str(w.inv["S"]), str(root / "nowhere"))
    )
    out["inventory_missing"] = state_of(d)
    d = variant("bad_inventory")
    inv = root / "inv-bad"
    shutil.copytree(w.inv["S"], inv)
    (inv / "extract.json").write_text((inv / "extract.json").read_text() + " ")
    (d / "studio.toml").write_text(
        (d / "studio.toml").read_text().replace(str(w.inv["S"]), str(inv))
    )
    out["bad_inventory"] = state_of(d)
    d = variant("no_spec")
    for n in ("spec.py", "spec.trace.json", "REQUIREMENTS.md"):
        (d / n).unlink()
    out["no_spec"] = state_of(d)
    want = "keys=['inputs', 'schema', 'state', 'why']"
    ok = all(v.startswith(f"200 {k}") and want in v for k, v in out.items())
    return result("PM5", "the four states: not_configured, inventory_missing, bad_inventory, no_spec",
                  "4/4 (200, only schema/state/inputs/why)", out, ok)  # fmt: skip


# ---- PS: the chat sessions ----------------------------------------------------------------------

PS_SCRIPT = r"""
import json, os, sys, time
from pathlib import Path
from lattice.studio import session as sess
out = {}
fake = Path(sys.argv[2]); 
for cfg in json.loads(sys.argv[1]):
    proj = Path(cfg["project"])
    mgr = sess.SessionManager(proj, claude_bin=str(fake))
    res = {}
    for mode in ("ask", "explain", "change"):
        s = mgr.create({"mode": mode})
        sid = s["id"]
        mgr.message(sid, {"text": "hello"})
        for _ in range(200):
            if mgr.last_argv.get(sid):
                break
            time.sleep(0.02)
        argv = list(mgr.last_argv[sid])
        tmp = mgr.sessions[sid].tmp
        res[mode] = {"argv": argv, "mcp": (tmp / "mcp.json").read_text(), "settings": (tmp / "settings.json").read_text()}
    res["rebuild_prompt"] = sess.system_prompt("rebuild", proj)
    mgr.close()
    out[cfg["name"]] = res
print(json.dumps(out))
"""


def run_in_tree(
    w: World, tree: Path, script: str, args: Sequence[str], label: str, cwd: Path | None = None
) -> Run:
    tmp = w.tmp(label)
    env = w.env(tmp, pythonpath=str(tree))
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-c", script, *args], capture_output=True, text=True,
                       env=env, cwd=cwd or tree, timeout=600, check=False)  # fmt: skip
    data = None
    with contextlib.suppress(ValueError):
        data = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    return Run(p.returncode, p.stdout, p.stderr, time.perf_counter() - t0, data)


def make_base(w: World, ref: str) -> Path:
    base = w.scratch / "base"
    if not (base / "lattice").is_dir():
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True)
        tar = subprocess.run(
            ["git", "-C", str(REPO), "archive", ref], capture_output=True, check=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(base)], input=tar, check=True)
    return base


SESSION_TMP = re.compile(r"/[^\s\"']*lattice-studio-session-[^/\s\"']+/s[0-9a-f]+")


def ps_norm(obj: Any) -> Any:
    """The argv, MCP config and settings of a session without its random temp directory."""
    if isinstance(obj, dict):
        return {k: ps_norm(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [ps_norm(v) for v in obj]
    if isinstance(obj, str):
        return SESSION_TMP.sub("<SESSION_TMP>", obj)
    return obj


def probe_ps(w: World, base: Path) -> dict[str, Any]:
    root = w.scratch / "ps"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    fake = root / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    cfgs = []
    for impl_on in (False, True):
        for ws_on in (False, True):
            name = f"impl{int(impl_on)}-ws{int(ws_on)}"
            proj = root / name / "project"
            shutil.copytree(MOCK_PROJECT, proj)
            for sub in ("app", "inventory"):
                shutil.copytree(MOCK_PROJECT.parent / sub, proj.parent / sub)
            toml = '[llm]\nmodel = "scripted"\n'
            if impl_on:
                toml += '\n[implementation]\ninventory = "../inventory"\napp_root = "../app"\n'
            (proj / "studio.toml").write_text(toml)
            if ws_on:
                (proj.parent / "lattice.workspace.toml").write_text(
                    '[workspace]\nname = "w"\n\n[[project]]\nname = "p"\npath = "project"\nrole = "app"\n'
                )
            cfgs.append({"name": name, "project": str(proj)})
    base_run = run_in_tree(w, base, PS_SCRIPT, [json.dumps(cfgs), str(fake)], "ps-base")
    new_run = run_in_tree(w, REPO, PS_SCRIPT, [json.dumps(cfgs), str(fake)], "ps-new")
    if base_run.data is None or new_run.data is None:
        return result(
            "PS",
            "sessions",
            "exact",
            {"base": base_run.err[-300:], "new": new_run.err[-300:]},
            False,
        )
    same = {k: ps_norm(base_run.data[k]) == ps_norm(new_run.data[k]) for k in base_run.data}
    # with [impact]: +1 allowed tool in ask, explain and change, +1 paragraph, rebuild unchanged
    imp_cfgs = []
    for c in cfgs:
        proj = Path(c["project"])
        d2 = proj.parent.parent / (proj.parent.name + "-impact") / "project"
        shutil.copytree(proj.parent, d2.parent)
        (d2 / "studio.toml").write_text((proj / "studio.toml").read_text() + "\n[impact]\n")
        imp_cfgs.append({"name": c["name"], "project": str(d2)})
    imp_run = run_in_tree(w, REPO, PS_SCRIPT, [json.dumps(imp_cfgs), str(fake)], "ps-impact")
    frozen = (REPO / "lattice/studio/session.py").read_text()
    delta: dict[str, Any] = {}
    ok_delta = imp_run.data is not None
    for c in cfgs:
        a, b = new_run.data[c["name"]], (imp_run.data or {}).get(c["name"], {})
        for mode in ("ask", "explain", "change"):
            if not b:
                ok_delta = False
                continue
            x, y = ps_norm(a[mode]["argv"]), ps_norm(b[mode]["argv"])
            ia, ib = x.index("--allowedTools") + 1, y.index("--allowedTools") + 1
            pa, pb = x.index("--append-system-prompt") + 1, y.index("--append-system-prompt") + 1
            only_tool = y[ib] == x[ia] + ",mcp__lattice__change_impact"
            only_para = y[pb].startswith(x[pb]) and y[pb][len(x[pb]):].startswith("\n\nFor 'what breaks if ...' questions use mcp__lattice__change_impact")  # fmt: skip
            rest = [e for k, e in enumerate(x) if k not in (ia, pa)] == [
                e for k, e in enumerate(y) if k not in (ib, pb)
            ]
            delta[f"{c['name']}/{mode}"] = bool(only_tool and only_para and rest)
            ok_delta &= bool(only_tool and only_para and rest)
        ok_delta &= a["rebuild_prompt"] == b.get("rebuild_prompt")
    del frozen
    return result(
        "PS", "sessions: byte-identical to base in 4 configs without [impact]; with it +1 tool, +1 paragraph (ask, explain, change), rebuild unchanged",
        "exact", {"identical_to_base": same, "with_impact": delta}, all(same.values()) and len(same) == 4 and ok_delta,
    )  # fmt: skip


MOCK_PROJECT = REPO / "lattice" / "studio" / "mock" / "appmap" / "project"


# ---- PW: nothing is written ---------------------------------------------------------------------

ROUTER_QUESTIONS = [
    "What breaks if {cid} is applied?",
    "Which workflows fail under {cid}?",
    "What would break if we raised the habit limit to 100?",
    "If we rename the route, what breaks?",
    "What happens when a check-in fails validation?",
    "Does the spec say anything about habit names?",
]


def docker_names() -> list[str]:
    p = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(p.stdout.split())


def probe_pw(
    w: World, st: Studio, cid: str, reports: Path, extra_questions: Sequence[str] = ()
) -> dict[str, Any]:
    reports.mkdir(parents=True, exist_ok=True)
    targets = {
        "project": w.dirs["S"], "inventory": w.inv["S"], "state": w.state["S"], "reports": reports,
    }  # fmt: skip
    # warm up once: the first request may build the model cache (in memory)
    for path in ("/api/appmap", f"/api/impact?change={cid}", "/api/appmap/element?key=rule:R4"):
        st.get(path)
    before = {k: IP.snapshot(v) for k, v in targets.items()}
    docker_before = docker_names()
    key = urllib.request.quote("rule:R4", safe="")
    calls = 0
    for _ in range(10):
        for path in ("/api/appmap", "/api/appmap?section=summary", "/api/appmap?section=rules",
                     f"/api/appmap/element?key={key}", f"/api/impact?change={cid}",
                     f"/api/impact?change={cid}&section=tier3", "/api/impact?change=c999"):  # fmt: skip
            assert st.get(path)[0] == 200, path
            calls += 1
    cli_runs = 0
    for _ in range(10):
        r = appmap_cli(w, "S")
        assert r.rc == 0
        cli_runs += 1
    asked = 0
    for q in [*ROUTER_QUESTIONS, *extra_questions]:
        st.post("/api/ask", {"question": q.format(cid=cid)})
        asked += 1
    after = {k: IP.snapshot(v) for k, v in targets.items()}
    diffs = {k: IP.diff_snapshots(before[k], after[k]) for k in targets}
    shim = w.shim_log.read_text() if w.shim_log.exists() else ""
    docker_after = docker_names()
    changed = {k: d for k, d in diffs.items() if d["changed"] or d["new"] or d["gone"]}
    ok = not changed and not shim.strip() and docker_before == docker_after
    return result(
        "PW", "no write: snapshots (sha256, mode, mtime, listing) of the project, inventory, state and reports directories around 10 calls of each GET route, the CLI x10 and the router; the claude shim; docker ps -a",
        {"changed": 0, "new": 0, "shim calls": 0, "new containers": 0},
        {"get calls": calls, "cli runs": cli_runs, "router questions": asked,
         "changed/new/gone": {k: {kk: len(vv) for kk, vv in d.items()} for k, d in diffs.items()},
         "first changes": {k: d for k, d in list(changed.items())[:2]},
         "shim calls": len([ln for ln in shim.splitlines() if ln]),
         "new containers": sorted(set(docker_after) - set(docker_before))},
        ok,
    )  # fmt: skip


# ---- PD: determinism ----------------------------------------------------------------------------


def serve_process(w: World, project: Path, *, hashseed: str) -> tuple[subprocess.Popen[str], str]:
    port = free_port()
    tmp = w.tmp("serve")
    env = w.env(tmp)
    env["PYTHONHASHSEED"] = hashseed
    p = subprocess.Popen(
        [sys.executable, "-m", "lattice.cli", "studio", str(project), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=env, cwd=REPO,
    )  # fmt: skip
    base = f"http://127.0.0.1:{port}"
    for _ in range(300):
        if p.poll() is not None:
            raise RuntimeError("studio exited: " + (p.stderr.read() if p.stderr else ""))
        try:
            urllib.request.urlopen(base + "/api/projects", timeout=1).read()
            return p, base
        except OSError:
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("studio did not start")


def raw_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read().decode()


def make_root2(w: World, name: str) -> Path:
    """A second scratch root of corpus `name`: the same project at another path."""
    d = w.scratch / "r2" / name
    shutil.rmtree(d, ignore_errors=True)
    d.parent.mkdir(parents=True, exist_ok=True)
    src = w.dirs[name]
    shutil.copytree(src, d, symlinks=True, ignore=shutil.ignore_patterns("bin"))
    inv = w.inv["S4"] if name == "S4" else w.inv["S"]
    set_tables(d, inv, d, w.state[name])
    return d


def probe_pd(w: World, runs: int, cid: str) -> dict[str, Any]:
    rows = {}
    seeds = [str(random.Random(i).randrange(1, 4_000_000)) for i in range(runs)]
    leaks: list[str] = []
    scan = re.compile(r"/Users|/private|/tmp|/var/folders|127\.0\.0\.1:\d|localhost:\d")
    iso = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
    # An ISO time in the map must come from the sources (Streak's rule texts name its test clock),
    # not from the run: the first version of this scan flagged every one and is a reported delta.
    src_blob = ""
    for name in ("S", "S4"):
        clone = w.dirs[name]
        for f in ("REQUIREMENTS.md", "spec.py", "spec.trace.json"):
            src_blob += (clone / f).read_text()
        inv = w.inv[name]
        for f in ("extract.json", "workflows.json"):
            if (inv / f).is_file():
                src_blob += (inv / f).read_text()
    for name in ("S", "S4", "S-stale"):
        roots = [w.dirs[name], make_root2(w, name)]
        outs: dict[str, set[str]] = {"cli": set(), "http_map": set(), "http_impact": set()}
        cli_http_equal = True
        for i, seed in enumerate(seeds):
            root = roots[i % 2]
            r = appmap_cli(w, name, root=root, hashseed=seed)
            assert r.rc == 0, r.err[-300:]
            outs["cli"].add(r.out)
            p, base = serve_process(w, root, hashseed=seed)
            try:
                text = raw_get(base + "/api/appmap")
                imp = raw_get(base + f"/api/impact?change={cid}")
            finally:
                p.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(10)
                if p.poll() is None:
                    p.kill()
            outs["http_map"].add(text)
            outs["http_impact"].add(imp)
            if canon(json.loads(text)) != canon(json.loads(r.out)):
                cli_http_equal = False
            for label, blob in (("cli", r.out), ("map", text), ("impact", imp)):
                m = scan.search(blob)
                if m:
                    leaks.append(f"{name} {label}: {m.group(0)}")
                for t in set(
                    iso.findall(blob) and re.findall(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", blob)
                ):
                    if t not in src_blob:
                        leaks.append(f"{name} {label}: a time not in the sources: {t}")
        rows[name] = {"distinct cli": len(outs["cli"]), "distinct http map": len(outs["http_map"]),
                      "distinct http impact": len(outs["http_impact"]), "cli equals http": cli_http_equal}  # fmt: skip
    ok = not leaks and all(
        v["distinct cli"] == 1
        and v["distinct http map"] == 1
        and v["distinct http impact"] == 1
        and v["cli equals http"]
        for v in rows.values()
    )
    return result(
        "PD", f"determinism: the map (CLI) and the two GET routes in {runs} processes with random PYTHONHASHSEED, alternating two scratch roots, on S, S4 and S-stale; no absolute path, time or port",
        f"{runs}/{runs} identical; none", {"rows": rows, "leaks": leaks[:10]}, ok,
    )  # fmt: skip


# ---- PX and PSZ ---------------------------------------------------------------------------------


def p50(xs: list[float]) -> float:
    return round(statistics.median(xs), 3)


def probe_px_psz(w: World, st: Studio, cid: str) -> list[dict[str, Any]]:
    cli = []
    for _ in range(7):
        t0 = time.perf_counter()
        r = appmap_cli(w, "S")
        cli.append(time.perf_counter() - t0)
        assert r.rc == 0
    get_map = []
    for _ in range(21):
        t0 = time.perf_counter()
        st.get("/api/appmap")
        get_map.append(time.perf_counter() - t0)
    get_imp = []
    for _ in range(11):
        t0 = time.perf_counter()
        st.get(f"/api/impact?change={cid}")
        get_imp.append(time.perf_counter() - t0)
    px = result(
        "PX", "time on S: CLI map; GET /api/appmap p50; GET /api/impact p50 (tiers 2+3)",
        {"cli": "<= 0.6 s (p)", "GET /api/appmap p50": "<= 0.5 s (p)", "GET /api/impact": "<= 3 s (p)", "gate": "<= 2 / 1.5 / 5 s"},
        {"cli p50 s": p50(cli), "cli max s": round(max(cli), 3), "GET /api/appmap p50 s": p50(get_map),
         "GET /api/appmap max s": round(max(get_map), 3), "GET /api/impact p50 s": p50(get_imp),
         "GET /api/impact max s": round(max(get_imp), 3)},
        p50(cli) <= 2 and p50(get_map) <= 1.5 and p50(get_imp) <= 5,
    )  # fmt: skip
    summary = raw_get(st.base + "/api/appmap?section=summary")
    whole = raw_get(st.base + "/api/appmap")
    psz = result(
        "PSZ", "size: the summary section < 4 kB; the whole map on S 120-350 kB (p)",
        {"summary": "< 4 kB", "whole": "120-350 kB (p)"},
        {"summary bytes": len(summary.encode()), "whole bytes": len(whole.encode())},
        len(summary.encode()) < 4000,
    )  # fmt: skip
    return [px, psz]


# ---- PB: byte-compat with the tip this build was cut from ---------------------------------------

SERVE_SCRIPT = r"""
import sys, threading
from pathlib import Path
from lattice.studio.server import make_server, StubGenerator
srv = make_server(Path(sys.argv[1]), 0, generator=StubGenerator())
print(srv.server_address[1], flush=True)
srv.serve_forever()
"""
MOCK_SCRIPT = r"""
import sys
from lattice.studio.mock_server import make_server
srv = make_server(0)
print(srv.server_address[1], flush=True)
srv.serve_forever()
"""
MCP_MSGS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    },  # noqa: E501
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]
ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?Z?")
VOLATILE = {"timings", "seconds", "latency_s", "elapsed_s", "total_s", "created", "at", "ts"}


def normalise(obj: Any, roots: Sequence[str]) -> Any:
    if isinstance(obj, dict):
        return {k: normalise(v, roots) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, (list, tuple)):
        return [normalise(v, roots) for v in obj]
    if isinstance(obj, str):
        for r in roots:
            obj = obj.replace(r, "<ROOT>")
        return ISO_TS.sub("<time>", obj)
    return obj


class Proc:
    def __init__(self, w: World, tree: Path, script: str, args: Sequence[str]) -> None:
        tmp = w.tmp("pb-serve")
        self.p = subprocess.Popen(
            [sys.executable, "-c", script, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=w.env(tmp, pythonpath=str(tree)), cwd=tree,
        )  # fmt: skip
        assert self.p.stdout is not None
        self.port = int(self.p.stdout.readline().strip())
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/api/projects", timeout=2).read()
                break
            except OSError:
                time.sleep(0.1)

    def close(self) -> None:
        self.p.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.p.wait(10)
        if self.p.poll() is None:
            self.p.kill()


def pb_server_answers(w: World, tree: Path, project: Path) -> dict[str, Any]:
    pr = Proc(w, tree, SERVE_SCRIPT, [str(project)])
    out: dict[str, Any] = {}
    try:
        for path in (
            "/api/model",
            "/api/projects",
            "/api/engines",
            "/api/improve",
            "/api/areas",
            "/api/branches",
            "/api/mcp",
        ):
            out["GET " + path] = http("GET", pr.base + path)
        out["POST /api/ask"] = http(
            "POST",
            pr.base + "/api/ask",
            {"question": "What happens when a note is read by another user?"},
        )
        out["POST /api/ask (impact words, no [impact])"] = http(
            "POST", pr.base + "/api/ask", {"question": "What would break if we raised the limit?"}
        )  # noqa: E501
        status, made = http(
            "POST", pr.base + "/api/changes", {"request": "stub: a note can be pinned"}
        )
        out["POST /api/changes"] = (status, made)
        for _ in range(200):
            _, c = http("GET", pr.base + f"/api/changes/{made.get('id', 'c1')}")
            if c.get("status") not in ("drafting", "checking"):
                break
            time.sleep(0.1)
        out["GET /api/changes/c1"] = (200, c)
        out["GET /api/appmap (the old tree has none)"] = http("GET", pr.base + "/api/appmap")
    finally:
        pr.close()
    return out


def pb_mock_answers(w: World, tree: Path) -> dict[str, Any]:
    pr = Proc(w, tree, MOCK_SCRIPT, [])
    out: dict[str, Any] = {}
    try:
        for path in ("/api/model", "/api/projects", "/api/graph", "/api/areas", "/api/branches", "/api/engines", "/api/changes/c0",
                     "/api/code/files", "/api/improve", "/api/plans", "/api/mcp", "/api/workspace", "/api/contracts",
                     "/api/linked-questions", "/api/flow?endpoint=POST%20/applications/%7B%7D/runs/%7B%7D/cancel&caller=ann",
                     "/p/lattice-studio/api/model"):  # fmt: skip
            out["GET " + path] = http("GET", pr.base + path)
        out["POST /api/ask"] = http(
            "POST", pr.base + "/api/ask", {"question": "Can an executor cancel a run?"}
        )
        out["POST /api/explain"] = http(
            "POST", pr.base + "/api/explain", {"question": "How does cancel work?"}
        )
    finally:
        pr.close()
    return out


def mcp_answers(w: World, tree: Path, project: Path, readonly: bool) -> dict[str, Any]:
    tmp = w.tmp("pb-mcp")
    args = [sys.executable, "-m", "lattice.mcp", "serve", "--project", str(project)] + (
        ["--readonly"] if readonly else []
    )
    p = subprocess.run(args, input="\n".join(json.dumps(m) for m in MCP_MSGS) + "\n", capture_output=True, text=True,
                       env=w.env(tmp, pythonpath=str(tree)), cwd=tree, timeout=120, check=False)  # fmt: skip
    msgs = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
    init = next(m["result"] for m in msgs if m.get("id") == 1)
    tools = next(m["result"] for m in msgs if m.get("id") == 2)
    return {
        "instructions": init["instructions"],
        "tools": tools["tools"],
        "names": [t["name"] for t in tools["tools"]],
    }


def text_of(cmd: Sequence[str], tree: Path, w: World, label: str) -> tuple[int, str, str]:
    tmp = w.tmp(label)
    p = subprocess.run(list(cmd), capture_output=True, text=True, env=w.env(tmp, pythonpath=str(tree)), cwd=tree, timeout=120, check=False)  # fmt: skip
    return p.returncode, p.stdout, p.stderr


PROTECTED = [
    "lattice/spec/implementation.py", "lattice/spec/impact_report.py", "lattice/spec/impact_tier2.py",
    "lattice/spec/impact_tier3.py", "lattice/spec/ask.py", "lattice/mcp/tools.py", "lattice/spec/check.py",
    "lattice/spec/incremental.py", "lattice/importer", "lattice/gocheck",
]  # fmt: skip
ADDITIVE_WEB = {
    "js/app.js",
    "js/api.js",
    "css/views.css",
    "js/views/appmap.js",
    "js/views/impact.js",
}


def probe_pb(
    w: World, base: Path, base_ref: str, pytest_log: Path | None, baseline_log: Path | None
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    # protected files: an empty diff against the base ref
    d = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--stat", base_ref, "--", *PROTECTED],
        capture_output=True,
        text=True,
        check=False,
    )
    checks["protected files: empty diff"] = d.stdout.strip() == ""
    tests_changed = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "diff",
            "--stat",
            base_ref,
            "--",
            "tests",
            "dogfood",
            "lattice/studio/mock/code",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout  # noqa: E501
    existing_tests = [
        ln
        for ln in tests_changed.splitlines()
        if ln.strip()
        and "|" in ln
        and not any(
            n in ln
            for n in (
                "appmap",
                "impact_ask",
                "goto_",
                "dogfood/studio/spec.py",
                "dogfood/studio/REQUIREMENTS.md",
                "spec.trace.json",
            )
        )
    ]  # noqa: E501
    checks["existing tests, goldens, fixtures: untouched"] = existing_tests == []
    checks["existing tests changed (names)"] = existing_tests[:5]
    # the web files
    web_diff = []
    for f in sorted((REPO / "lattice/studio/web").rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(REPO / "lattice/studio/web").as_posix()
        b = base / "lattice/studio/web" / rel
        if rel in ADDITIVE_WEB:
            continue
        if not b.is_file() or b.read_bytes() != f.read_bytes():
            web_diff.append(rel)
    checks["existing web files byte-identical (except app.js, api.js, views.css)"] = web_diff == []
    checks["web files differing"] = web_diff
    for rel in ("js/app.js", "js/api.js", "css/views.css"):
        ns = subprocess.run(
            [
                "git",
                "-C",
                str(REPO),
                "diff",
                "--numstat",
                base_ref,
                "--",
                f"lattice/studio/web/{rel}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()  # noqa: E501
        checks[f"{rel} is additive (lines added, lines deleted)"] = [ns[0], ns[1]] if ns else None
        checks[f"{rel} deletes nothing"] = bool(ns) and ns[1] == "0"
    # the real server, the old tree against the new, on one project (two copies of one repository)
    sys.path.insert(0, str(REPO))
    from tests.studio.test_server import make_project  # noqa: PLC0415

    shutil.rmtree(w.scratch / "pb-project", ignore_errors=True)
    proj = make_project(w.scratch / "pb-project", repo=True)
    pa, pb_ = w.scratch / "pb-a" / "project", w.scratch / "pb-b" / "project"
    for dst in (pa, pb_):
        shutil.rmtree(dst.parent, ignore_errors=True)
        dst.parent.mkdir(parents=True)
        shutil.copytree(proj, dst, symlinks=True)
    roots = [str(pa), str(pb_), str(proj)]
    old_srv = normalise(pb_server_answers(w, base, pa), roots)
    new_srv = normalise(pb_server_answers(w, REPO, pb_), roots)
    srv_diff = [k for k in old_srv if old_srv[k] != new_srv.get(k)]
    # the only route that may differ is the one the new tree adds
    new_route_only = srv_diff == ["GET /api/appmap (the old tree has none)"]
    checks["real server: existing routes identical"] = new_route_only
    checks["real server: routes that differ"] = srv_diff
    old_mock, new_mock = (
        normalise(pb_mock_answers(w, base), []),
        normalise(pb_mock_answers(w, REPO), []),
    )
    mock_diff = [k for k in old_mock if old_mock[k] != new_mock.get(k)]
    checks["mock server: existing routes identical"] = not mock_diff
    checks["mock routes that differ"] = mock_diff
    # tools/list and instructions, C's and D's configurations (readonly or not)
    mcp_rows = {}
    for cname, drop_impact in (
        ("C: [implementation]", True),
        ("D: [implementation] + [impact]", False),
    ):
        proj_dir = w.scratch / f"pb-mcp-{int(drop_impact)}"
        shutil.rmtree(proj_dir, ignore_errors=True)
        shutil.copytree(
            w.dirs["S-root2"], proj_dir, symlinks=True, ignore=shutil.ignore_patterns("bin")
        )
        set_tables(proj_dir, w.inv["S-root2"], proj_dir, w.scratch / f"state-pb-{int(drop_impact)}")
        if drop_impact:
            t = (proj_dir / "studio.toml").read_text()
            (proj_dir / "studio.toml").write_text(re.sub(r"(?m)^\[impact\]\n", "", t))
        for ro in (False, True):
            a, b = mcp_answers(w, base, proj_dir, ro), mcp_answers(w, REPO, proj_dir, ro)
            mcp_rows[f"{cname} readonly={ro}"] = {
                "identical": a == b,
                "tools": len(b["names"]),
                "has change_impact": "change_impact" in b["names"],
            }  # noqa: E501
    checks["mcp tools/list and instructions"] = mcp_rows
    help_same = True
    for cmd in (
        ["-m", "lattice.cli", "--help"],
        ["-m", "lattice.cli", "spec"],
        ["-m", "lattice.spec"],
    ):
        a = text_of([sys.executable, *cmd], base, w, "pb-help-a")
        b = text_of([sys.executable, *cmd], REPO, w, "pb-help-b")
        help_same &= a == b
    checks["lattice --help and spec usage identical"] = help_same
    # the pytest suites: the same failure ids as before
    if (
        pytest_log is not None
        and baseline_log is not None
        and pytest_log.is_file()
        and baseline_log.is_file()
    ):
        fail = lambda p: sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", p.read_text(), re.M)))  # noqa: E731
        fa, fb = fail(baseline_log), fail(pytest_log)
        last = pytest_log.read_text().strip().splitlines()[-1]
        checks["pytest: same failure ids as the base"] = fa == fb
        checks["pytest summary"] = last
        checks["pytest failure ids only in the new run"] = sorted(set(fb) - set(fa))
    else:
        checks["pytest: same failure ids as the base"] = None
    bools = [v for k, v in checks.items() if isinstance(v, bool)]
    ok = (
        all(bools)
        and all(r["identical"] for r in mcp_rows.values())
        and checks["pytest: same failure ids as the base"] is True
    )
    return result("PB", "byte-compat with the base tip: every existing route, tools/list, help, web files, protected files, pytest failure ids",
                  "all identical", checks, ok)  # fmt: skip


# ---- GATE and the pytest rows -------------------------------------------------------------------


def probe_gate(w: World) -> dict[str, Any]:
    tmp = w.tmp("gate")
    env = w.env(tmp)
    env.update(
        {k: os.environ[k] for k in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT") if k in os.environ}
    )
    t0 = time.perf_counter()
    p = subprocess.run(
        [str(REPO / "dogfood/studio/check.sh"), "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO / "dogfood/studio",
        timeout=1800,
        check=False,
    )  # noqa: E501
    data = None
    with contextlib.suppress(ValueError):
        data = json.loads(p.stdout)
    cases = (data or {}).get("cases", {})
    scen = {k.rsplit(" / step", 1)[0] for k in cases}
    ok = (
        p.returncode == 0
        and data is not None
        and data.get("verdict") == "pass"
        and all(c["verdict"] == "correct" for c in cases.values())
    )  # noqa: E501
    return result("GATE", "dogfood/studio/check.sh with R104-R108", "PASS, 481 + 10-20 cases, 57 + 4 scenarios (p)",
                  {"rc": p.returncode, "verdict": (data or {}).get("verdict"), "cases": len(cases), "scenarios": len(scen),
                   "findings": len((data or {}).get("findings", [])), "seconds": round(time.perf_counter() - t0, 1)}, ok)  # fmt: skip


def probe_pytests(w: World) -> list[dict[str, Any]]:
    env = w.env(w.tmp("pytest"))
    env.update(
        {k: os.environ[k] for k in ("UV_NO_SYNC", "UV_PROJECT_ENVIRONMENT") if k in os.environ}
    )
    rows = []
    for pid, desc, files in (
        (
            "PU",
            "UI facts without a browser: node --check, navigator, api.js, headings, forbidden words, fake-DOM walk of every state",
            ["tests/studio/test_appmap_ui.py"],
        ),
        ("PP", "mock parity and the API.md shapes (PP, PAPI)", ["tests/studio/test_appmap_api.py"]),
        (
            "PR*",
            "the router and the session allowance, unit level",
            ["tests/studio/test_impact_ask.py"],
        ),
        ("PM*", "the map, unit level", ["tests/spec/test_appmap.py"]),
    ):
        p = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "pytest",
                *files,
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=REPO,
            timeout=1800,
            check=False,
        )  # noqa: E501
        last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
        rows.append(
            result(pid, desc, "all pass", {"files": files, "summary": last}, p.returncode == 0)
        )
    return rows


# ---- the freeze ---------------------------------------------------------------------------------

FREEZE_FILES = [
    "dogfood/goto_probes.py", "dogfood/goto_mutants.py", "dogfood/goto_agent.py",
    "lattice/spec/appmap.py", "lattice/spec/impact_ask.py", "lattice/studio/server.py",
    "lattice/studio/session.py", "lattice/studio/mock_server.py", "lattice/studio/API.md",
    "lattice/studio/web/js/views/appmap.js", "lattice/studio/web/js/views/impact.js",
    "lattice/studio/web/js/api.js", "lattice/studio/web/js/app.js", "lattice/studio/web/css/views.css",
    "tests/spec/test_appmap.py", "tests/studio/test_appmap_api.py", "tests/studio/test_appmap_ui.py",
    "tests/studio/test_impact_ask.py",
]  # fmt: skip


def write_freeze(out: Path) -> dict[str, str]:
    """sha256 of the build's own runner, mutants and engine files, recorded before the question
    set is scored (the orchestrator's freeze.sha256 is not touched)."""
    rows = {f: sha((REPO / f).read_bytes()) for f in FREEZE_FILES if (REPO / f).is_file()}
    out.mkdir(parents=True, exist_ok=True)
    (out / "freeze-build.sha256").write_text(
        "".join(f"{h}  {f}\n" for f, h in sorted(rows.items()))
    )
    return rows


# ---- the dev stage ------------------------------------------------------------------------------


def server_map(st: Studio) -> dict[str, Any]:
    status, doc = st.get("/api/appmap")
    assert status == 200, doc
    return doc


def run_dev(args: argparse.Namespace) -> int:
    w = World(args)
    os.environ["PATH"] = f"{w.shim}{os.pathsep}{os.environ['PATH']}"
    only = set(filter(None, (args.only or "").split(",")))
    results: list[dict[str, Any]] = []
    token = f"{OWNER}-dev-{os.getpid()}"
    containers_before = ours_now()
    t0 = time.perf_counter()
    for d in ("state-S", "state-S-root2", "state-S4", "state-S-stale", "reports"):
        shutil.rmtree(w.scratch / d, ignore_errors=True)
    setup(w, build=True)
    make_dev_candidates(w)
    studios: list[Studio] = []

    def want(*ids: str) -> bool:
        return not only or any(i in only for i in ids)

    base = make_base(w, args.base_ref) if want("PS", "PB") else None

    try:
        st = Studio(w, w.dirs["S"], owner=token)
        studios.append(st)
        recs = {} if args.no_records else record_changes(w, st, w.dev)
        remove_owner(token)
        cid = recs["d1_limit"]["id"] if recs else "c1"
        st4, sts = Studio(w, w.dirs["S4"]), Studio(w, w.dirs["S-stale"])
        studios += [st4, sts]
        maps = {"S": server_map(st), "S4": server_map(st4), "S-stale": server_map(sts)}
        if want("PM1"):
            results.append(probe_pm1(w, maps))
        if want("PM1b"):
            for name in ("S4", "S-stale"):
                cmp_ = compare_map(w, name, maps[name])
                cmp_.pop("report")
                results.append(result(f"PM1-{name}", f"{name}: every map field equals its source", "0 differences", cmp_, cmp_["n_differences"] == 0))  # fmt: skip
        if want("PM2"):
            results.append(probe_pm2(w, maps))
        if want("PM3"):
            results.append(probe_pm3(w, maps))
        if want("PM4"):
            results.append(probe_pm4(w, maps))
        if want("PM5"):
            results.append(probe_pm5(w))
        if want("PM6"):
            results.append(probe_pm6(w, maps))
        if want("PE"):
            results.append(probe_pe(w, st, maps))
        if want("PI", "PIC"):
            results += probe_pi_pic(w, st, recs)
        if want("PIS"):
            results.append(probe_pis(w, cid))
        if want("PX", "PSZ"):
            results += probe_px_psz(w, st, cid)
        if want("PW"):
            results.append(probe_pw(w, st, cid, w.scratch / "reports"))
        if want("PS"):
            results.append(probe_ps(w, base))  # type: ignore[arg-type]
        if want("PD"):
            results.append(probe_pd(w, args.runs, cid))
        if want("PB"):
            results.append(
                probe_pb(
                    w, base, args.base_ref,  # type: ignore[arg-type]
                    Path(args.pytest_log) if args.pytest_log else None,
                    Path(args.baseline_log) if args.baseline_log else None,
                )
            )  # fmt: skip
        if want("GATE"):
            results.append(probe_gate(w))
        if want("PU", "PP", "PA"):
            results += probe_pytests(w)
    finally:
        for s in reversed(studios):
            with contextlib.suppress(Exception):
                s.close()
        remove_owner(token)
    if want("PBR"):
        results.append(not_run("PBR", "real-browser walk (prereg section 4)", args.pbr_reason))
    after = ours_now()
    results.append(result("CONTAINERS", "containers this runner started and left behind", 0,
                          {"before": containers_before, "after": after}, after == containers_before))  # fmt: skip
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes.json").write_text(
        json.dumps(
            {
                "seconds": round(time.perf_counter() - t0, 1),
                "setup_notes": w.notes,
                "probes": results,
            },
            indent=1,
            default=str,
        )
    )  # noqa: E501
    for name, d in w.dev.items():
        dst = out / "dev-candidates" / name
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(d, dst)
    for r in results:
        flag = "PASS" if r.get("pass") else ("NOT RUN" if r.get("ran") is False else "FAIL")
        print(f"{r['id']:<10} {flag:<8} {r['probe'][:100]}")
    bad = [r["id"] for r in results if r.get("ran") and not r.get("pass")]
    print("failed:", bad or "none")
    return 1 if bad else 0


# ---- the question-set stage (once) --------------------------------------------------------------


def run_qset(args: argparse.Namespace) -> int:
    qdir = Path(args.questions).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "qset-scored.json").exists() and not args.rerun:
        print(
            "the question set was scored here already; a second run is a rerun (--rerun)",
            file=sys.stderr,
        )
        return 2
    qs = json.loads((qdir / "qset.json").read_text())
    w = World(args)
    os.environ["PATH"] = f"{w.shim}{os.pathsep}{os.environ['PATH']}"
    for d in ("state-S", "state-S4", "reports", "reports-qset"):
        shutil.rmtree(w.scratch / d, ignore_errors=True)
    wf = Path(args.workflows).resolve()
    G.archive(STREAK, args.streak_ref, w.dirs["S"])
    w.notes.append(IP.rewrite_go_src(w.dirs["S"]))
    capture_inventory(w, "S", w.dirs["S"], wf)
    w.state["S"] = w.scratch / "state-S"
    set_tables(w.dirs["S"], w.inv["S"], w.dirs["S"], w.state["S"])
    G.go_build(w.dirs["S"])
    G.archive(STREAK, args.streak_ref2, w.dirs["S4"])
    w.notes.append(IP.rewrite_go_src(w.dirs["S4"]))
    capture_inventory(w, "S4", w.dirs["S4"], None)
    w.state["S4"] = w.scratch / "state-S4"
    set_tables(w.dirs["S4"], w.inv["S4"], w.dirs["S4"], w.state["S4"])
    reports = w.scratch / "reports-qset"
    reports.mkdir(parents=True)
    token = f"{OWNER}-q-{os.getpid()}"
    summary: dict[str, Any] = {"changes": {}, "notes": w.notes}
    st = Studio(w, w.dirs["S"], owner=token)
    try:
        # the six candidates, recorded in order c1..c6 through the scripted generator
        for cname in sorted(qs["changes"]):
            info = qs["changes"][cname]
            cand = (qdir / info["candidate"]).resolve()
            c = st.draft(cand, f"candidate {cname}")
            assert c["id"] == cname, (c["id"], cname)
            priv = json.loads((w.state["S"] / "changes" / f"{cname}.json").read_text())["private"]
            got = {"REQUIREMENTS.md": sha(priv["new_md"].encode()), "spec.py": sha(priv["new_module"].encode()),
                   "spec.trace.json": sha(priv["new_trace"].encode())}  # fmt: skip
            want = info["files_sha256"]
            patched = False
            if args.patch_records and got["spec.trace.json"] != want.get("spec.trace.json"):
                # Studio's pipeline adds `replaces` and `retired_targets` to a drafted trace whose
                # rules changed (run 1: qset-stop-run1.json), so the record's trace is not the
                # candidate's bytes. FORMATS stops on any difference; the stop was recorded, and
                # this rerun (the question set was never scored) writes the candidate's own bytes
                # into the record's new_trace, a deviation the results name.
                path = w.state["S"] / "changes" / f"{cname}.json"
                doc = json.loads(path.read_text())
                doc["private"]["new_trace"] = (cand / "spec.trace.json").read_text()
                path.write_text(json.dumps(doc))
                priv = doc["private"]
                got["spec.trace.json"] = sha(priv["new_trace"].encode())
                patched = True
            match = (
                all(got.get(k) == v for k, v in want.items())
                if isinstance(want, dict)
                else sorted(got.values()) == sorted(want)
            )  # noqa: E501
            summary["changes"][cname] = {
                "status": c["status"],
                "byte_equal": match,
                "trace_patched": patched,
                "got": got,
                "want": want,
            }
            if not match:
                print(f"STOP: {cname} was not recorded byte for byte", file=sys.stderr)
                (out / "qset-stop.json").write_text(json.dumps(summary, indent=1))
                return 3
            r = engine_cli(w, w.dirs["S"], cname, w.state["S"], out=reports / f"{cname}.json")
            assert r.rc == 0, r.err[-300:]
        remove_owner(token)
    finally:
        st.close()
        remove_owner(token)
    # the map surface: the whole map of each corpus, from the real route
    from lattice.llm.client import ScriptedGenerator  # noqa: PLC0415, F401
    from lattice.studio.server import StubGenerator  # noqa: PLC0415

    stub = StubGenerator()
    st2 = Studio(w, w.dirs["S"], gen=stub)
    st4 = Studio(w, w.dirs["S4"])
    answers_map = {"maps": {"S": server_map(st2), "S4": server_map(st4)}, "answers": {}}
    # the router surface
    base = make_base(w, args.base_ref)
    base_proc = Proc(w, base, SERVE_SCRIPT, [str(w.dirs["S"])])
    router: dict[str, Any] = {}
    try:
        for q in qs["questions"]:
            if q["set"] not in ("I_impact", "H_hypothetical", "C_control"):
                continue
            n0 = len(stub.calls)
            status, resp = st2.post("/api/ask", {"question": q["text"]})
            entry: dict[str, Any] = {
                "response": resp,
                "generator_calls": len(stub.calls) - n0,
                "status": status,
            }
            if q["set"] == "C_control":
                entry["base"] = http("POST", base_proc.base + "/api/ask", {"question": q["text"]})[
                    1
                ]
            router[q["id"]] = entry
    finally:
        base_proc.close()
        st4.close()
        st2.close()
    # no-write evidence around the router is PW's; here the shim must still be empty
    (out / "answers-map.json").write_text(json.dumps(answers_map))
    (out / "answers-router.json").write_text(json.dumps({"answers": router}, indent=1))
    scorer = qdir / "goto_score.py"
    scored: dict[str, Any] = {}
    for surface, ans, extra in (
        ("map", "answers-map.json", []),
        ("router", "answers-router.json", ["--reports", str(reports)]),
    ):
        r = subprocess.run(
            [
                sys.executable,
                str(scorer),
                "--qset",
                str(qdir / "qset.json"),
                "--surface",
                surface,
                "--answers",
                str(out / ans),
                "--json",
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )  # noqa: E501
        scored[surface] = {"rc": r.returncode, "stderr": r.stderr[-500:]}
        with contextlib.suppress(ValueError):
            scored[surface]["result"] = json.loads(r.stdout)
        (out / f"score-{surface}.json").write_text(r.stdout)
    shim = w.shim_log.read_text() if w.shim_log.exists() else ""
    summary["scored"] = {
        k: v.get("result", {}).get("summary") if isinstance(v.get("result"), dict) else v
        for k, v in scored.items()
    }
    summary["shim_calls"] = len([ln for ln in shim.splitlines() if ln])
    (out / "qset-scored.json").write_text(json.dumps(summary, indent=1))
    # for goto_agent.py
    (out / "world.json").write_text(json.dumps({
        "project": str(w.dirs["S"]), "state_dir": str(w.state["S"]), "reports": str(reports),
        "questions": str(qdir), "python": sys.executable, "scratch": str(w.scratch),
    }, indent=1))  # fmt: skip
    for surface, v in summary["scored"].items():
        print(surface, json.dumps(v)[:600])
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=("dev", "qset", "freeze"), required=True)
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument(
        "--workflows",
        default=str(REPO / "docs/results/data/2026-10-15-workflow-layer/workflows.json"),
    )
    ap.add_argument("--scratch", required=False)
    ap.add_argument("--out", required=True)
    ap.add_argument("--questions")
    ap.add_argument("--base-ref", default="e0086ff")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only", help="comma list of probe ids (dev stage)")
    ap.add_argument("--pytest-log")
    ap.add_argument("--baseline-log")
    ap.add_argument("--pbr-reason", default="NOT RUN (no browser connected)")
    ap.add_argument("--rerun", action="store_true")
    ap.add_argument(
        "--patch-records",
        action="store_true",
        help="qset: write the candidate's trace bytes into a record whose drafted trace differs",
    )
    ap.add_argument(
        "--no-records", action="store_true", help="skip the six change records (mutant runs)"
    )
    args = ap.parse_args(argv)
    if args.stage == "freeze":
        rows = write_freeze(Path(args.out))
        print(f"{len(rows)} files recorded")
        return 0
    if not args.scratch:
        ap.error("--scratch is required")
    if args.stage == "dev":
        return run_dev(args)
    return run_qset(args)


if __name__ == "__main__":
    sys.exit(main())
