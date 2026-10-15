"""Probe runner for the improvement loop in Studio (API v1.21): the probes P0-P8 of
``docs/results/2026-10-15-studio-ui-prereg.md`` (section 3, with Amendment 1). P9 (the dogfood
gate) is ``dogfood/studio/check.sh`` and P10 is ``tests/studio/test_studio_ui.py``.

    uv run python dogfood/studio_ui_probes.py [--runs 10] [--scratch DIR] \
        [--out docs/results/data/2026-10-15-studio-ui] [--only P0,P1,...]

Every probe starts REAL ``python -m lattice.studio`` processes on 127.0.0.1 (a scratch port) over
synthetic git repositories under the scratch root, and drives them with ``http.client`` (or, for
P2/P4 and the CLI side of P1, ``python -m lattice.improve``). Nothing is written outside the scratch
root and ``--out``; no LLM, no API key (the servers run ``--stub-llm`` and never see an Anthropic
variable), no network, no Docker. A probe that does not match its prediction is reported as a
finding with its raw output, never adapted. Deterministic probes run ``--runs`` times and all runs
must be identical after masking ``applied_at``, ``started_at``, ``finished_at``, commit shas and the
gate ``seconds`` and ``output_tail``. ``OUT/<probe>.json`` holds the first run's raw observation and
the per-run digests; ``OUT/summary.json`` the table.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs/results/data/2026-10-12-spike3"
DEFAULT_OUT = ROOT / "docs/results/data/2026-10-15-studio-ui"
BASE_REF = "2a8ac57"
SEED = ROOT / "dogfood/studio/project_seed"
# the tree whose servers, web files and Python sources are probed: this working tree, or (the
# mutation run, dogfood/studio_ui_mutants.py) a patched copy. The CLI side of P1 and P2 and the
# base archive are never mutated.
TREE = Path(os.environ.get("STUDIO_UI_TREE") or ROOT).resolve()
ID = "imp-3e1cd7f9"
X = "gate:level_regression:X"
LINE = 'level_gate_args = ["--x-repeats", "2"]'
ZEROS = "0" * 64
BASE_TOML = '[rebuild]\ngates = ["true"]\n'
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Studio UI Probe",
    "GIT_AUTHOR_EMAIL": "probe@example.com",
    "GIT_AUTHOR_DATE": "2026-10-10T12:00:00+00:00",
    "GIT_COMMITTER_NAME": "Studio UI Probe",
    "GIT_COMMITTER_EMAIL": "probe@example.com",
    "GIT_COMMITTER_DATE": "2026-10-10T12:00:00+00:00",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
FORBIDDEN_GIT = {"merge", "push", "reset", "checkout", "stash", "update-ref"}

RUNS = 10
SCRATCH: Path = Path()
FINDINGS: list[str] = []
DECISIONS: list[str] = [
    "The synthetic repositories also commit the three spec files (REQUIREMENTS.md, spec.py, "
    "spec.trace.json), which the prereg's list omits: `python -m lattice.studio DIR` refuses a "
    "directory without them (hubs do not need them).",
    "Every synthetic repository's first commit has fixed author and committer dates, so the "
    "twin repositories of P2 have the same base sha; the apply commit's date is its own "
    "applied_at (a masked field), so its sha is masked.",
    'P0 uses one project with `gates = ["sleep 5"]` for the control (row d) so that row l\'s '
    "second POST arrives while the first runs; every other row is checked before the control.",
    "P1's `state dir not created when absent` is measured after server start: Studio's own "
    "StateStore creates the directory when it loads the project (API v1.10, not a route); the "
    "routes add nothing to it. A project with `[state] enabled = false` has no directory at all "
    "before or after.",
    "The deterministic probes run RUNS times (the orchestrator's 10), not the prereg's 3.",
]
POST_HOC: list[str] = [
    "P5, P8 (first development run, 1 run each, before any reported run): (1) P5's static scan "
    "applied the subprocess/git patterns to the JavaScript too and matched the word "
    '"subprocesses" in a comment of contracts.js; the rule is a Python-side one (server.py, '
    "mock_server.py) and the llm words are scanned in every new file: scope fixed. (2) P8 counted "
    "its own sanity check (both POSTs answer 202) as an 18th item; the prereg's 17 are the key "
    "trees of 11 answers and the statuses of 6 rows: the check is a problem line now, not an "
    "item.",
]


# ------------------------------------------------------------------ plumbing


def env_for(tree: Path = ROOT, extra: dict[str, str] | None = None) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC")}
    e.update(GIT_ENV)
    e.update(PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE="1", COLUMNS="100")
    e.pop("LATTICE_WORKSPACE", None)
    e["TMPDIR"] = str(SCRATCH)
    e.update(extra or {})
    return e


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def digest(obj: Any) -> str:
    return sha(json.dumps(obj, sort_keys=True, default=str))[:16]


def git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV, **(env or {})},
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {cwd}: {r.stderr.strip()}")
    return r.stdout


def fresh(name: str) -> Path:
    d = SCRATCH / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d.resolve()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def i3_lines(n: int = 9) -> list[str]:
    return (DATA / "i3-events.jsonl").read_text().splitlines()[:n]


def synth(
    root: Path,
    name: str = "repo",
    toml: str = BASE_TOML,
    *,
    state: bool = True,
    ledger: list[dict[str, Any]] | None = None,
    sub: str | None = None,
    state_name: str | None = None,
) -> tuple[Path, Path, Path | None]:
    """``(top, project, state dir)``: a git repository (main, identity in its config,
    core.autocrlf=false) with a committed studio.toml (`toml` and `[state] dir`), the three spec
    files and, in `state_dir`, the first nine events of the spike-3 log. `sub`: the project is
    ``top/<sub>`` (several projects in one repository)."""
    top = root / name
    top.mkdir(parents=True)
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "core.autocrlf", "false")
    git(top, "config", "user.name", "Owner")
    git(top, "config", "user.email", "owner@example.com")
    project = top / sub if sub else top
    project.mkdir(parents=True, exist_ok=True)
    sd = root / (state_name or f"{name}-state") if state else None
    rel = os.path.relpath(sd, project) if sd is not None else None
    tail = f'\n[state]\ndir = "{rel}"\n' if sd is not None else "\n[state]\nenabled = false\n"
    (project / "studio.toml").write_text(toml + tail)
    (project / "REQUIREMENTS.md").write_text("# p\n\n- one rule.\n")
    (project / "spec.py").write_text("")
    (project / "spec.trace.json").write_text("{}")
    (top / "README").write_text("readme\n")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "init")
    if sd is not None:
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines()))
        if ledger is not None:
            (sd / "improvements.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
    return top, project, sd


def world(root: Path) -> dict[str, str]:
    """Every file under `root` (the .git directory included) by path -> sha256."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for n in sorted(filenames):
            p = Path(dirpath) / n
            out[str(p.relative_to(root))] = "link" if p.is_symlink() else sha(p.read_bytes())
    return out


def refs(top: Path) -> dict[str, str]:
    out = {}
    for ln in git(top, "for-each-ref", "--format=%(refname) %(objectname)").splitlines():
        k, _, v = ln.partition(" ")
        out[k] = v
    return out


def mask_record(doc: Any, *roots: Path) -> Any:
    """`doc` with the fields the prereg lets differ masked (applied_at, started_at, finished_at,
    the commit sha, gate seconds and output_tail) and the scratch paths replaced."""
    text = json.dumps(doc, sort_keys=True)
    commit = None
    if isinstance(doc, dict):
        rec = doc.get("result") if isinstance(doc.get("result"), dict) else doc
        c = rec.get("commit") if isinstance(rec, dict) else None
        commit = c.get("sha") if isinstance(c, dict) else None
    for r in roots:
        text = text.replace(json.dumps(str(r))[1:-1], "<root>")
    if commit:
        text = text.replace(commit, "<commit>")

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if k in ("applied_at", "started_at", "finished_at"):
                    out[k] = "<ts>" if x is not None else None
                elif k in ("seconds", "output_tail"):
                    out[k] = f"<{k}>"
                else:
                    out[k] = walk(x)
            return out
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(json.loads(text))


def row(pid: str, predicted: str, observed: str, ok: bool, **extra: Any) -> dict[str, Any]:
    return {"id": pid, "predicted": predicted, "observed": observed, "pass": bool(ok), **extra}


def repeat(runs: int, one: Callable[[int], dict[str, Any]]) -> dict[str, Any]:
    outs = [one(i) for i in range(runs)]
    digests = [digest(o["obs"]) for o in outs]
    problems = sorted({p for o in outs for p in o["problems"]})
    if len(set(digests)) != 1:
        problems.append(f"runs differ: {len(set(digests))} distinct of {runs}")
    return {
        "obs": outs[0]["obs"],
        "raw": outs[0].get("raw"),
        "problems": problems,
        "digests": digests,
        "runs": runs,
    }


# ------------------------------------------------------------------ servers


class Server:
    """``python -m lattice.studio`` on a scratch port, in a process of its own."""

    def __init__(
        self,
        dirs: list[Path] | Path,
        *,
        tree: Path | None = None,
        allow: bool = True,
        default: str | None = None,
        path_prefix: Path | None = None,
        extra_env: dict[str, str] | None = None,
        label: str = "srv",
    ) -> None:
        self.dirs = [dirs] if isinstance(dirs, Path) else list(dirs)
        self.port = free_port()
        env = env_for(tree or TREE, extra_env)
        if path_prefix is not None:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
        args = [
            sys.executable,
            "-m",
            "lattice.studio",
            *map(str, self.dirs),
            "--port",
            str(self.port),
            "--quiet",
            "--stub-llm",
        ]
        if allow:
            args.append("--allow-apply")
        if default:
            args += ["--default", default]
        self.log = SCRATCH / f"{label}-{self.port}.log"
        self.proc = subprocess.Popen(
            args, cwd=str(SCRATCH), env=env, stdout=self.log.open("w"), stderr=subprocess.STDOUT
        )
        self.args = args
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited {self.proc.returncode}: {self.log.read_text()}")
            try:
                if self.call("GET", "/api/projects")[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("server did not start")

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        json_type: bool = True,
        host: str | None = None,
        timeout: float = 120,
    ) -> tuple[int, Any]:
        st, data = self.call_raw(
            method,
            path,
            body,
            headers=headers,
            raw=raw,
            json_type=json_type,
            host=host,
            timeout=timeout,
        )
        try:
            return st, json.loads(data)
        except json.JSONDecodeError:
            return st, data.decode("utf-8", "replace")

    def call_raw(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        json_type: bool = True,
        host: str | None = None,
        timeout: float = 120,
    ) -> tuple[int, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        c.putrequest(method, path, skip_host=host is not None)
        if host is not None:
            c.putheader("Host", host)
        if json_type:
            c.putheader("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if method != "GET":
            c.putheader("Content-Length", str(len(data or b"")))
        c.endheaders(data)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, out

    def get(self, path: str) -> tuple[int, Any]:
        return self.call("GET", path)

    def draft_sha(self, ident: str = ID, prefix: str = "") -> str:
        st, d = self.get(f"{prefix}/api/improve/{ident}/draft")
        assert st == 200 and not d.get("refused"), d
        return sha(d["text"])

    def apply(
        self, body: dict[str, Any] | None = None, prefix: str = "", ident: str = ID, **kw: Any
    ) -> tuple[int, Any]:
        b = {"draft_sha256": self.draft_sha(ident, prefix), "gates": False, **(body or {})}
        return self.call("POST", f"{prefix}/api/improve/{ident}/apply", b, **kw)

    def wait_done(self, prefix: str = "", ident: str = ID, limit: float = 120) -> dict[str, Any]:
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit:
            st, d = self.get(f"{prefix}/api/improve/{ident}/apply?wait=30")
            if st == 200 and d["status"] == "done":
                return d
            if st != 200:
                return {"http": st, "body": d}
        raise RuntimeError("the apply job did not finish")

    def stop(self, sig: int = signal.SIGTERM, timeout: float = 60) -> float:
        t0 = time.monotonic()
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return time.monotonic() - t0


def cli(argv: list[str], *, cwd: Path | None = None, tree: Path = ROOT) -> dict[str, Any]:
    r = subprocess.run(
        [sys.executable, "-m", "lattice.improve", *argv],
        cwd=str(cwd or SCRATCH),
        env=env_for(tree),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    out: Any = None
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        pass
    return {"rc": r.returncode, "json": out, "err": r.stderr}


def ps_has(needle: str) -> list[str]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if needle in ln and "ps -axo" not in ln]


def toml_with(gates: list[str], extra: str = "") -> str:
    return "[rebuild]\ngates = [" + ", ".join(json.dumps(g) for g in gates) + f"]\n{extra}"


# ------------------------------------------------------------------ P0 guard ladder


def p0_one(i: int) -> dict[str, Any]:
    root = fresh(f"p0-{i}")
    top, project, sd = synth(root, "repo", toml_with(["sleep 5"]))
    assert sd is not None
    nstate = synth(root, "nostate", BASE_TOML, state=False)
    problems: list[str] = []
    rows: dict[str, Any] = {}

    def snap() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        return world(sd), refs(top), world(top)

    def rec(
        key: str,
        got: tuple[int, Any],
        want_status: int,
        want_code: str | None,
        before: Any = None,
        extra_ok: bool = True,
    ) -> None:
        st, body = got
        code = body.get("code") if isinstance(body, dict) else None
        ok = st == want_status and code == want_code and extra_ok
        if before is not None and st >= 300 and snap() != before:
            ok = False
            problems.append(f"{key}: the state, refs or working tree changed after a non-2xx")
        rows[key] = {
            "status": st,
            "code": code,
            "want": [want_status, want_code],
            "ok": ok,
            "detail": body.get("detail") if isinstance(body, dict) else str(body)[:80],
        }
        if not ok:
            problems.append(f"{key}: got {st} {code!r}, want {want_status} {want_code!r}")

    # a, n: the flag is off
    off = Server(top, allow=False, label=f"p0off-{i}")
    try:
        before = snap()
        sha1 = off.draft_sha()
        rec(
            "a",
            off.call("POST", f"/api/improve/{ID}/apply", {"draft_sha256": sha1}),
            403,
            "apply_disabled",
            before,
        )
        st, lst = off.get("/api/improve/applies")
        rows["a_route3"] = {"enabled": lst.get("enabled"), "why": lst.get("why_disabled")}
        if lst.get("enabled") is not False:
            problems.append("a: route 3 enabled is not false with the flag off")
        rec("n", off.call("POST", f"/api/improve/{ID}/discard", {}), 403, "apply_disabled", before)
    finally:
        off.stop()
    # the rest: flag on
    srv = Server(top, allow=True, label=f"p0on-{i}")
    try:
        p = srv.port
        url = f"/api/improve/{ID}/apply"
        sha1 = srv.draft_sha()
        good = {"draft_sha256": sha1, "gates": False}
        before = snap()
        rec(
            "b",
            srv.call("POST", url, good, headers={"Origin": "http://evil.example"}),
            403,
            "bad_origin",
            before,
        )
        rec("c", srv.call("POST", url, good, host=f"evil.example:{p}"), 403, "bad_origin", before)
        rec(
            "e",
            srv.call(
                "POST",
                url,
                raw=json.dumps(good).encode(),
                json_type=False,
                headers={"Content-Type": "text/plain"},
            ),
            415,
            "json_required",
            before,
        )
        rec("f", srv.call("POST", url, raw=b"[]"), 400, None, before)
        rec("g", srv.call("POST", "/api/improve/../apply", good), 422, "bad_id", before)
        g1 = srv.call("POST", "/api/improve/a%2Fb/apply", good)
        g2 = srv.get("/api/improve/a%2Fb/apply")
        rec("h", g1, 422, "bad_id", before, extra_ok=g2[0] == 422 and g2[1].get("code") == "bad_id")
        rec("i", srv.call("POST", url, {"gates": False}), 422, "bad_body", before)
        rec(
            "j",
            srv.call("POST", url, {"draft_sha256": sha1, "gates": "yes"}),
            422,
            "bad_body",
            before,
        )
        m = srv.call("POST", url, {"draft_sha256": ZEROS})
        rec(
            "m",
            m,
            409,
            "draft_changed",
            before,
            extra_ok=isinstance(m[1], dict) and m[1].get("draft_sha256") == sha1,
        )
        # d: the control, with the right Origin; l: a second POST while the sleep gate runs
        d = srv.call(
            "POST", url, good | {"gates": True}, headers={"Origin": f"http://127.0.0.1:{p}"}
        )
        rec(
            "d",
            d,
            202,
            None,
            None,
            extra_ok=isinstance(d[1], dict) and d[1].get("status") == "running",
        )
        t0 = time.monotonic()  # the record is written, then the gate sleeps: nothing else writes
        while time.monotonic() - t0 < 20:
            st, v = srv.get(url)
            if st == 200 and v.get("record"):
                break
            time.sleep(0.05)
        before_l = snap()
        rec("l", srv.call("POST", url, good | {"gates": True}), 409, "apply_running", before_l)
        done = srv.wait_done()
        rows["d_done"] = {
            "exit_code": done.get("exit_code"),
            "gates": (done.get("result") or {}).get("gates", {}).get("status"),
        }
        if done.get("exit_code") != 0:
            problems.append(f"d: the control apply ended with exit {done.get('exit_code')}")
    finally:
        srv.stop()
    # k: a project without durable state
    ns = Server(nstate[1], allow=True, label=f"p0ns-{i}")
    try:
        before_k = (world(nstate[0]), refs(nstate[0]))
        rec("k", ns.call("POST", url, {"draft_sha256": ZEROS}), 409, "no_state")
        if (world(nstate[0]), refs(nstate[0])) != before_k:
            problems.append("k: the repository changed")
    finally:
        ns.stop()
    ok_rows = sorted(k for k, v in rows.items() if isinstance(v, dict) and v.get("ok") is True)
    return {"obs": {"rows": rows, "ok_rows": ok_rows}, "problems": problems, "raw": rows}


def probe_p0(runs: int) -> dict[str, Any]:
    r = repeat(runs, p0_one)
    n = len(r["obs"]["ok_rows"])
    return {
        "id": "P0",
        "title": "guard ladder",
        "predicted": "14 of 14 exact status and code; after every non-2xx row the state dir, "
        ".git refs and working-tree hashes are unchanged",
        "observed": f"{n} of 14 rows exact (rows ok: {', '.join(r['obs']['ok_rows'])})",
        "pass": n == 14 and not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ P1 read routes = CLI


def p1_one(i: int) -> dict[str, Any]:
    root = fresh(f"p1-{i}")
    problems: list[str] = []
    cases: list[dict[str, Any]] = []
    closed = [
        {
            "id": "I-9",
            "signal": X,
            "status": "closed",
            "commit": "abc1234",
            "ts": "2026-10-12T23:08:00Z",
        }
    ]
    worlds = {
        "open": synth(root, "open", BASE_TOML),
        "closed": synth(root, "closed", BASE_TOML, ledger=closed),
    }
    nostate = synth(root, "nostate", BASE_TOML, state=False)
    for label, (top, project, sd) in worlds.items():
        assert sd is not None
        srv = Server(top, allow=True, label=f"p1-{label}-{i}")
        try:
            st0 = world(sd)
            where = ["--project", str(project), "--state", str(sd)]
            for ident in (ID, "imp-00000000"):
                want = cli(["draft", ident, "--json", *where])["json"]
                for prefix in ("", f"/p/{top.name}"):
                    st, got = srv.get(f"{prefix}/api/improve/{ident}/draft")
                    ok = st == 200 and got == want
                    cases.append(
                        {
                            "state": label,
                            "route": 1,
                            "id": ident,
                            "prefix": prefix,
                            "ok": ok,
                            "refused": bool(got.get("refused")),
                        }
                    )
                    if not ok:
                        problems.append(f"route 1 {label} {ident} {prefix!r} differs from the CLI")
                for at in (None, "2026-10-01T12:00:00Z"):
                    cmd = ["verify", ident, *(["--at", at] if at else []), "--json", *where]
                    want = cli(cmd)["json"]
                    for prefix in ("", f"/p/{top.name}"):
                        q = f"?at={at}" if at else ""
                        st, got = srv.get(f"{prefix}/api/improve/{ident}/verify{q}")
                        ok = st == 200 and got == want
                        cases.append(
                            {
                                "state": label,
                                "route": 2,
                                "id": ident,
                                "at": at,
                                "prefix": prefix,
                                "ok": ok,
                                "verdict": got.get("verdict") or got.get("reason"),
                            }
                        )
                        if not ok:
                            problems.append(f"route 2 {label} {ident} at={at} {prefix!r} differs")
            if world(sd) != st0:
                problems.append(f"{label}: the state directory changed by reading")
        finally:
            srv.stop()
    # a project without state: no state directory before or after
    srv = Server(nostate[1], allow=True, label=f"p1-ns-{i}")
    try:
        names = sorted(p.name for p in nostate[0].parent.iterdir())
        for path in (
            f"/api/improve/{ID}/draft",
            f"/api/improve/{ID}/verify",
            "/api/improve/applies",
        ):
            srv.get(path)
        if sorted(p.name for p in nostate[0].parent.iterdir()) != names:
            problems.append("nostate: a route created a directory")
        st, d = srv.get(f"/api/improve/{ID}/draft")
        cases.append({"state": "nostate", "route": 1, "refused": d.get("reason")})
    finally:
        srv.stop()
    n_ok = sum(1 for c in cases if c.get("ok") and c["prefix"] == "")
    n_total = sum(1 for c in cases if c.get("prefix") == "")
    n_prefixed = sum(1 for c in cases if c.get("ok") and c.get("prefix"))
    return {
        "obs": {"cases": cases, "bare_ok": n_ok, "bare_total": n_total, "prefixed_ok": n_prefixed},
        "problems": problems,
        "raw": cases,
    }


def probe_p1(runs: int) -> dict[str, Any]:
    r = repeat(runs, p1_one)
    o = r["obs"]
    return {
        "id": "P1",
        "title": "read routes equal the CLI",
        "predicted": "12 of 12 parsed-JSON equal (bare), the same under /p/<name>/, state dir "
        "bytes unchanged (and nothing created for a project without state)",
        "observed": f"{o['bare_ok']} of {o['bare_total']} equal bare; {o['prefixed_ok']} equal "
        "under /p/<name>/",
        "pass": o["bare_ok"] == 12
        and o["bare_total"] == 12
        and o["prefixed_ok"] == 12
        and not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ P2 apply = CLI apply


def apply_server(root: Path, i: int, label: str) -> dict[str, Any]:
    """POST apply {gates: false} on a fresh repo at root/repo; the record and the job."""
    top, project, sd = synth(root, "repo", BASE_TOML)
    assert sd is not None
    srv = Server(top, allow=True, label=f"{label}-{i}")
    try:
        t0 = time.monotonic()
        st, job = srv.apply()
        done = srv.wait_done()
        waited = time.monotonic() - t0
        st3, lst = srv.get("/api/improve/applies")
        out = {
            "post": st,
            "job": done,
            "waited": waited,
            "applies": lst,
            "next": (done.get("result") or {}).get("next"),
        }
        out["tree"] = git(top, "rev-parse", f"improve/{ID}^{{tree}}").strip()
        out["record_file"] = json.loads((sd / "applies" / f"{ID}.json").read_text())
        return out
    finally:
        srv.stop()


def cli_apply(root: Path) -> dict[str, Any]:
    top, project, sd = synth(root, "repo", BASE_TOML)
    assert sd is not None
    r = cli(["apply", ID, "--no-gates", "--json", "--project", str(project)])
    return {
        "rc": r["rc"],
        "rec": r["json"],
        "tree": git(top, "rev-parse", f"improve/{ID}^{{tree}}").strip(),
    }


def p2_one(i: int) -> dict[str, Any]:
    problems: list[str] = []
    root = fresh(f"p2-{i}")
    a = apply_server(root, i, "p2srv")
    shutil.rmtree(root)  # the twin: the same path, sequentially
    root = fresh(f"p2-{i}")
    b = cli_apply(root)
    job = a["job"]
    rec_s, rec_c = job.get("result"), b["rec"]
    if a["post"] != 202:
        problems.append(f"POST answered {a['post']}")
    if job.get("exit_code") != 0 or b["rc"] != 0:
        problems.append(f"exit codes: server {job.get('exit_code')}, CLI {b['rc']}")
    ms, mc = mask_record(rec_s, root), mask_record(rec_c, root)
    equal = ms == mc
    if not equal:
        problems.append("the masked records differ")
    if a["tree"] != b["tree"]:
        problems.append(f"tree ids differ: {a['tree']} vs {b['tree']}")
    e = (rec_s or {}).get("edit", {})
    if e.get("action") != "inserted" or e.get("line") != LINE:
        problems.append(f"edit: {e.get('action')!r} {e.get('line')!r}")
    if a["waited"] >= 30:
        problems.append(f"route 4 wait took {a['waited']:.1f} s")
    if [x["id"] for x in a["applies"]["applies"]] != [ID]:
        problems.append("route 3 does not list the apply")
    if a["record_file"] != rec_s:
        problems.append("the on-disk record is not the job's result")
    obs = {
        "exit": [job.get("exit_code"), b["rc"]],
        "masked_equal": equal,
        "tree_equal": a["tree"] == b["tree"],
        "action": e.get("action"),
        "line": e.get("line"),
        "listed": [x["id"] for x in a["applies"]["applies"]],
        "source": job.get("source"),
        "next_has_state": any("--state" in s["command"] for s in (a["next"] or [])),
    }
    raw = {"server_record_masked": ms, "cli_record_masked": mc, "waited_s": round(a["waited"], 2)}
    return {"obs": obs, "problems": problems, "raw": raw}


def p2c_one(i: int) -> dict[str, Any]:
    """A hub name that differs from the directory name: `next.close` runs verbatim."""
    root = fresh(f"p2c-{i}")
    problems: list[str] = []
    top, project, _sd = synth(root, "dirname", BASE_TOML, state=False)
    (project / "studio.toml").write_text(BASE_TOML)
    git(top, "commit", "-qam", "default state")
    store = top / ".lattice" / "state" / "hubname"
    store.mkdir(parents=True)
    (store / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines()))
    # a hub of one named "hubname": two dirs would be a hub; one dir is single mode named by the
    # directory, so the hub name is set with a [project] name in studio.toml
    (project / "studio.toml").write_text('[project]\nname = "hubname"\n' + BASE_TOML)
    git(top, "commit", "-qam", "name")
    srv = Server(project, allow=True, label=f"p2c-{i}")
    try:
        st, job = srv.apply()
        done = srv.wait_done()
        close = [s for s in (done.get("result") or {}).get("next", []) if s["step"] == "close"]
        cmd = close[0]["command"] if close else ""
        if done.get("exit_code") != 0:
            problems.append(f"apply exit {done.get('exit_code')}: {done.get('result')}")
        if f"--state {shlex.quote(str(store))}" not in cmd and f"--state {store}" not in cmd:
            problems.append(f"next.close lacks --state <store root>: {cmd}")
        # run next.close verbatim (the command line the owner would paste), as the owner would
        argv = shlex.split(cmd)
        argv[0:2] = [sys.executable, "-m", "lattice.improve"]
        r = subprocess.run(argv, cwd=str(SCRATCH), env=env_for(), capture_output=True, text=True)
        wrote = (store / "improvements.jsonl").is_file()
        if r.returncode != 0 or not wrote:
            problems.append(f"next.close rc {r.returncode}, improvements.jsonl written: {wrote}")
        entry = (store / "improvements.jsonl").read_text() if wrote else ""
        obs = {
            "close_has_state": "--state" in cmd,
            "close_rc": r.returncode,
            "wrote": wrote,
            "entry_signal": json.loads(entry.splitlines()[0])["signal"] if entry else None,
        }
    finally:
        srv.stop()
    return {"obs": obs, "problems": problems, "raw": {"cmd": cmd.replace(str(root), "<root>")}}


def probe_p2(runs: int) -> dict[str, Any]:
    r = repeat(runs, p2_one)
    c = repeat(runs, p2c_one)
    ok = not r["problems"] and not c["problems"]
    return {
        "id": "P2",
        "title": "apply equals the CLI's apply",
        "predicted": "exit 0 both; masked records equal; tree id equal; edit.action inserted, "
        f"edit.line {LINE}; route 4 ?wait=30 within 30 s; route 3 lists it. P2c: next holds "
        "--state <store root> and next.close verbatim writes <store root>/improvements.jsonl",
        "observed": f"P2: {r['obs']}; P2c: {c['obs']}",
        "pass": ok,
        "obs": {"p2": r["obs"], "p2c": c["obs"]},
        "raw": {"p2": r["raw"], "p2c": c["raw"]},
        "problems": [*r["problems"], *c["problems"]],
        "digests": [r["digests"], c["digests"]],
        "runs": runs,
    }


# ------------------------------------------------------------------ P3 gates and close


def p3_one(i: int) -> dict[str, Any]:
    problems: list[str] = []
    obs: dict[str, Any] = {}
    # a: a failing second gate
    root = fresh(f"p3a-{i}")
    top, project, sd = synth(root, "repo", toml_with(["true", "false"]))
    srv = Server(top, label=f"p3a-{i}")
    try:
        srv.apply({"gates": True})
        d = srv.wait_done()
    finally:
        srv.stop()
    g = (d.get("result") or {}).get("gates", {})
    obs["a"] = {
        "exit": d.get("exit_code"),
        "status": g.get("status"),
        "n_results": len(g.get("results", [])),
        "branch_kept": bool(git(top, "branch", "--list", f"improve/{ID}").strip()),
    }
    if obs["a"] != {"exit": 1, "status": "failed", "n_results": 2, "branch_kept": True}:
        problems.append(f"a: {obs['a']}")
    # b: a timeout
    root = fresh(f"p3b-{i}")
    top, project, sd = synth(root, "repo", toml_with(["sleep 30"], "gate_timeout = 2\n"))
    srv = Server(top, label=f"p3b-{i}")
    try:
        srv.apply({"gates": True})
        d = srv.wait_done()
    finally:
        srv.stop()
    g = (d.get("result") or {}).get("gates", {})
    r0 = (g.get("results") or [{}])[0]
    obs["b"] = {
        "exit": d.get("exit_code"),
        "rc": r0.get("exit_code"),
        "timed_out": r0.get("timed_out"),
        "seconds_in_2_9": 2 <= (r0.get("seconds") or 0) <= 9,
        "status": g.get("status"),
    }
    if obs["b"] != {
        "exit": 1,
        "rc": 124,
        "timed_out": True,
        "seconds_in_2_9": True,
        "status": "failed",
    }:
        problems.append(f"b: {obs['b']} seconds={r0.get('seconds')}")
    # c: SIGTERM to the server 2 s into the gate
    marker = f"29.{40000 + i}"
    root = fresh(f"p3c-{i}")
    top, project, sd = synth(root, "repo", toml_with([f"sleep {marker}"]))
    assert sd is not None
    srv = Server(top, label=f"p3c-{i}")
    grace = 5.0  # [rebuild] stop_grace default (lattice.studio.rebuild.DEFAULT_STOP_GRACE)
    try:
        from lattice.studio.rebuild import DEFAULT_STOP_GRACE

        grace = float(DEFAULT_STOP_GRACE)
    except Exception:  # noqa: BLE001
        pass
    srv.apply({"gates": True})
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30 and not ps_has(f"sleep {marker}"):
        time.sleep(0.1)
    time.sleep(2.0)
    took = srv.stop(signal.SIGTERM, timeout=60)
    left = ps_has(f"sleep {marker}")
    rec = json.loads((sd / "applies" / f"{ID}.json").read_text())
    obs["c"] = {
        "exited_in_bound": took <= grace + 10,
        "status": rec["gates"]["status"],
        "left": len(left),
        "branch_kept": bool(git(top, "branch", "--list", f"improve/{ID}").strip()),
    }
    if obs["c"] != {
        "exited_in_bound": True,
        "status": "interrupted",
        "left": 0,
        "branch_kept": True,
    }:
        problems.append(f"c: {obs['c']} took {took:.1f}s left {left}")
    # d: restart after c
    srv2 = Server(top, label=f"p3d-{i}")
    try:
        st, v = srv2.get(f"/api/improve/{ID}/apply")
        d1 = {
            "source": v.get("source"),
            "stale": v.get("stale"),
            "status": v.get("status"),
            "exit_code": v.get("exit_code"),
        }
        rec["gates"]["status"] = "running"
        (sd / "applies" / f"{ID}.json").write_text(json.dumps(rec, indent=1) + "\n")
        st, v2 = srv2.get(f"/api/improve/{ID}/apply")
        obs["d"] = {
            "restart": d1,
            "hand_set_running_stale": v2.get("stale"),
            "source": v2.get("source"),
        }
        if (
            d1 != {"source": "record", "stale": False, "status": "done", "exit_code": None}
            or v2.get("stale") is not True
        ):
            problems.append(f"d: {obs['d']}")
    finally:
        srv2.stop()
    return {"obs": obs, "problems": problems, "raw": {**obs, "c_took_s": round(took, 1)}}


def probe_p3(runs: int) -> dict[str, Any]:
    r = repeat(runs, p3_one)
    return {
        "id": "P3",
        "title": "gates and close",
        "predicted": "a: exit 1, failed, 2 results, branch kept; b: exit 1, 124, timed_out, secs "
        "in [2, 9]; c: the server exits within stop_grace + 10 s, record interrupted, 0 sleep 30 "
        "left; d: source record, stale false; a record hand-set to running: stale true. 4 of 4",
        "observed": json.dumps(r["obs"], sort_keys=True),
        "pass": not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ P4 discard, P5 safety


def p4_p5_one(i: int) -> dict[str, Any]:
    """P4 discard (three rows) with P5's measurements around P2 (no gates) and P4."""
    problems: list[str] = []
    obs: dict[str, Any] = {}
    root = fresh(f"p45-{i}")
    shim = root / "shim"
    shim.mkdir()
    real_git = shutil.which("git") or "/usr/bin/git"
    log = root / "git-argv.log"
    (shim / "git").write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    (shim / "git").chmod(0o755)
    top, project, sd = synth(root, "repo", BASE_TOML)
    assert sd is not None
    srv = Server(top, allow=True, path_prefix=shim, label=f"p45-{i}")
    try:
        st, imp0 = srv.call_raw("GET", "/api/improve")
        ev0 = (sd / "events.jsonl").read_bytes()
        before = {
            "git_files": {
                n: sha((top / ".git" / n).read_bytes())
                for n in ("index", "HEAD", "config", "packed-refs")
                if (top / ".git" / n).exists()
            },
            "refs": refs(top),
            "tree": {k: v for k, v in world(top).items() if not k.startswith(".git/")},
            "events": sha(ev0),
            "ledger": (sd / "improvements.jsonl").exists(),
        }
        st, job = srv.apply({"gates": True})
        done = srv.wait_done()
        rec = done.get("result") or {}
        obs["apply_exit"] = done.get("exit_code")
        after_apply = {
            "git_files": {
                n: sha((top / ".git" / n).read_bytes())
                for n in ("index", "HEAD", "config", "packed-refs")
                if (top / ".git" / n).exists()
            },
            "refs": refs(top),
            "tree": {k: v for k, v in world(top).items() if not k.startswith(".git/")},
            "events": sha((sd / "events.jsonl").read_bytes()),
            "ledger": (sd / "improvements.jsonl").exists(),
        }
        new_refs = sorted(set(after_apply["refs"]) - set(before["refs"]))
        changed_refs = sorted(
            k for k in before["refs"] if before["refs"][k] != after_apply["refs"].get(k)
        )
        checks = {
            "git_files_unchanged": before["git_files"] == after_apply["git_files"],
            "tree_unchanged": before["tree"] == after_apply["tree"],
            "events_unchanged": before["events"] == after_apply["events"],
            "no_ledger": before["ledger"] == after_apply["ledger"] is False,
            "only_new_ref": new_refs == [f"refs/heads/improve/{ID}"] and not changed_refs,
        }
        obs["p5_apply"] = checks
        problems += [f"P5 apply: {k}" for k, v in checks.items() if not v]
        # P4: discard
        record = sd / "applies" / f"{ID}.json"
        rec_bytes = record.read_bytes()
        wt = Path(rec["worktree"])
        st, d1 = srv.call("POST", f"/api/improve/{ID}/discard", {})
        kept = sd / "applies" / f"{ID}.discarded-1.json"
        lst = srv.get("/api/improve/applies")[1]
        r4 = srv.get(f"/api/improve/{ID}/apply")
        p4a = {
            "status": st,
            "exit": d1.get("exit_code"),
            "branch_gone": git(top, "branch", "--list", f"improve/{ID}").strip() == "",
            "worktree_gone": not wt.exists(),
            "record_renamed_identical": kept.is_file() and kept.read_bytes() == rec_bytes,
            "live_record_gone": not record.exists(),
            "route3_empty": lst["applies"] == [],
            "route4": r4[0],
        }
        obs["p4a"] = p4a
        want_a = {
            "status": 200,
            "exit": 0,
            "branch_gone": True,
            "worktree_gone": True,
            "record_renamed_identical": True,
            "live_record_gone": True,
            "route3_empty": True,
            "route4": 404,
        }
        if p4a != want_a:
            problems.append(f"P4a: {p4a}")
        st, d2 = srv.call("POST", f"/api/improve/{ID}/discard", {})
        p4b = {
            "status": st,
            "exit": d2.get("exit_code"),
            "reason": (d2.get("result") or {}).get("reason"),
        }
        obs["p4b"] = p4b
        if p4b != {"status": 200, "exit": 3, "reason": "not_applied"}:
            problems.append(f"P4b: {p4b}")
        # merged: a fresh apply, then another branch holds the commit
        srv.apply({"gates": False})
        d3 = srv.wait_done()
        git(top, "branch", "keep", d3["result"]["commit"]["sha"])
        st, d4 = srv.call("POST", f"/api/improve/{ID}/discard", {})
        p4c = {
            "status": st,
            "exit": d4.get("exit_code"),
            "reason": (d4.get("result") or {}).get("reason"),
        }
        obs["p4c"] = p4c
        if p4c != {"status": 200, "exit": 3, "reason": "merged"}:
            problems.append(f"P4c: {p4c}")
        # GET /api/improve byte-identical before and after (Studio logs nothing)
        st, imp1 = srv.call_raw("GET", "/api/improve")
        obs["improve_identical"] = imp1 == imp0
        if imp1 != imp0:
            problems.append("GET /api/improve changed")
        obs["events_identical"] = sha((sd / "events.jsonl").read_bytes()) == before["events"]
        if not obs["events_identical"]:
            problems.append("events.jsonl changed")
    finally:
        srv.stop()
    argv_lines = log.read_text().splitlines() if log.exists() else []
    bad = [ln for ln in argv_lines if FORBIDDEN_GIT & set(shlex.split(ln))]
    obs["git_calls_logged"] = len(argv_lines) > 0
    obs["forbidden_argv"] = len(bad)
    if bad:
        problems.append(f"forbidden git argv: {bad[:3]}")
    if not argv_lines:
        problems.append("the git shim logged nothing (it was not on the servers' PATH)")
    return {
        "obs": obs,
        "problems": problems,
        "raw": {
            **obs,
            "forbidden": bad[:5],
            "git_subcommands": sorted(
                {
                    next(
                        (
                            t
                            for t in shlex.split(ln)
                            if t
                            in {
                                "worktree",
                                "branch",
                                "status",
                                "rev-parse",
                                "commit",
                                "add",
                                "diff",
                                "cat-file",
                                "for-each-ref",
                                "symbolic-ref",
                                "show-ref",
                                "check-ref-format",
                                "merge-base",
                                "config",
                                "prune",
                                "log",
                            }
                        ),
                        "?",
                    )
                    for ln in argv_lines
                }
            ),
        },
    }


def diff_lines(rel: str) -> tuple[list[str], list[str]]:
    """``(added, removed)`` lines of TREE's `rel` against the base commit's (a plain line diff)."""
    import difflib

    a = (base_tree() / rel).read_text(encoding="utf-8").splitlines()
    b = (TREE / rel).read_text(encoding="utf-8").splitlines()
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += a[i1:i2]
        if tag in ("replace", "insert"):
            added += b[j1:j2]
    return added, removed


def static_new_code() -> dict[str, Any]:
    """P5's static rule: the lines this change adds to server.py, mock_server.py and the two new
    views hold no subprocess/git call and no claude, anthropic or lattice.llm."""
    added: dict[str, list[str]] = {}
    for f in (
        "lattice/studio/server.py",
        "lattice/studio/mock_server.py",
        "lattice/studio/web/js/api.js",
        "lattice/improve/apply.py",
    ):
        added[f] = diff_lines(f)[0]
    for f in ("lattice/studio/web/js/views/improve.js", "lattice/studio/web/js/views/contracts.js"):
        added[f] = (TREE / f).read_text().splitlines()
    pats = {
        "subprocess": re.compile(r"subprocess|Popen|os\.system|os\.exec"),
        "git_call": re.compile(r"""["']git["']|\bgit\("""),
        "llm": re.compile(r"claude|anthropic|lattice\.llm", re.I),
    }
    hits: dict[str, list[str]] = {k: [] for k in pats}
    for f, lines in added.items():
        for ln in lines:
            for k, rx in pats.items():
                if k != "llm" and not f.endswith(".py"):
                    continue  # subprocess and git calls are a Python-side rule
                if rx.search(ln):
                    hits[k].append(f"{f}: {ln.strip()[:120]}")
    return {"added_lines": {f: len(v) for f, v in added.items()}, "hits": hits}


def probe_p4_p5(runs: int) -> list[dict[str, Any]]:
    r = repeat(runs, p4_p5_one)
    o = r["obs"]
    st = static_new_code()
    st_ok = all(len(v) == 0 for v in st["hits"].values())
    p4_ok = o.get("p4a") and not any(p.startswith("P4") for p in r["problems"])
    p5_problems = [p for p in r["problems"] if not p.startswith("P4")]
    p4_problems = [p for p in r["problems"] if p.startswith("P4") or p.startswith("runs differ")]
    return [
        {
            "id": "P4",
            "title": "discard",
            "predicted": "200 exit 0, branch and worktree gone, record renamed "
            "<id>.discarded-1.json byte-identical, route 3 applies [], route 4 404; again: exit 3 "
            "not_applied; after git branch keep <commit>: exit 3 merged. 3 of 3",
            "observed": json.dumps({k: o[k] for k in ("p4a", "p4b", "p4c")}, sort_keys=True),
            "pass": bool(p4_ok) and not p4_problems,
            "obs": {k: o[k] for k in ("p4a", "p4b", "p4c")},
            "problems": p4_problems,
            "digests": r["digests"],
            "runs": runs,
            "raw": r["raw"],
        },
        {
            "id": "P5",
            "title": "safety",
            "predicted": "0 changes to the owner's .git/{index,HEAD,config,packed-refs}, every ref "
            "but the new one, every working-tree file, events.jsonl, improvements.jsonl; 0 "
            "merge|push|reset|checkout|stash|update-ref argv; GET /api/improve byte-identical; "
            "static: 0 subprocess/git calls and 0 claude/anthropic/lattice.llm in the new code",
            "observed": json.dumps(
                {"dynamic": {k: o[k] for k in o if k not in ("p4a", "p4b", "p4c")}, "static": st},
                sort_keys=True,
                default=str,
            )[:1500],
            "pass": not p5_problems and st_ok and o.get("forbidden_argv") == 0,
            "obs": {
                "dynamic": {k: o[k] for k in o if k not in ("p4a", "p4b", "p4c")},
                "static": st,
            },
            "problems": p5_problems + ([] if st_ok else [f"static hits: {st['hits']}"]),
            "digests": r["digests"],
            "runs": runs,
            "raw": r["raw"],
        },
    ]


# ------------------------------------------------------------------ P6 hub routing


def p6_one(i: int) -> dict[str, Any]:
    problems: list[str] = []
    obs: dict[str, Any] = {}
    root = fresh(f"p6-{i}")
    a_top, a_proj, a_sd = synth(root, "A", BASE_TOML)
    b_top, b_proj, b_sd = synth(root, "B", BASE_TOML)
    assert a_sd is not None and b_sd is not None
    srv = Server([a_top, b_top], allow=True, default="A", label=f"p6-{i}")
    try:
        a0 = world(a_top)
        a_state0 = world(a_sd)
        st, job = srv.apply(prefix="/p/B")
        done = srv.wait_done(prefix="/p/B")
        obs["b_exit"] = done.get("exit_code")
        obs["b_branch"] = bool(git(b_top, "branch", "--list", f"improve/{ID}").strip())
        obs["a_branch"] = bool(git(a_top, "branch", "--list", f"improve/{ID}").strip())
        obs["a_unchanged"] = world(a_top) == a0 and world(a_sd) == a_state0
        obs["b_record"] = (b_sd / "applies" / f"{ID}.json").is_file()
        st, la = srv.get("/api/improve/applies")
        obs["a_route3_applies"] = la["applies"]
        st, lb = srv.get("/p/B/api/improve/applies")
        obs["b_route3_ids"] = [x["id"] for x in lb["applies"]]
        st, nobody = srv.call("POST", f"/p/nobody/api/improve/{ID}/apply", {"draft_sha256": ZEROS})
        obs["nobody"] = [st, nobody]
        r1 = (
            obs["b_exit"] == 0
            and obs["b_branch"]
            and not obs["a_branch"]
            and obs["a_unchanged"]
            and obs["b_record"]
            and obs["a_route3_applies"] == []
            and obs["b_route3_ids"] == [ID]
            and st == 404
        )
        obs["row_separate"] = bool(r1)
        if not r1:
            problems.append(f"separate repos: {obs}")
    finally:
        srv.stop()
    # the same repository: two projects, concurrent POSTs
    root2 = fresh(f"p6s-{i}")
    slow = toml_with(["sleep 3"])
    top, pa, sda = synth(root2, "mono", slow, sub="a", state_name="state-a")
    # the second project of the same repository: its own directory, state and studio.toml
    pb = top / "b"
    pb.mkdir()
    sdb = root2 / "state-b"
    sdb.mkdir()
    (pb / "studio.toml").write_text(slow + f'\n[state]\ndir = "{os.path.relpath(sdb, pb)}"\n')
    (pb / "REQUIREMENTS.md").write_text("# b\n\n- one rule.\n")
    (pb / "spec.py").write_text("")
    (pb / "spec.trace.json").write_text("{}")
    (sdb / "events.jsonl").write_text("".join(x + "\n" for x in i3_lines()))
    git(top, "add", "-A")
    git(top, "commit", "-qm", "project b")
    srv = Server([pa, pb], allow=True, default="a", label=f"p6s-{i}")
    try:
        sha_a = srv.draft_sha(prefix="/p/a")
        sha_b = srv.draft_sha(prefix="/p/b")
        results: dict[str, tuple[int, Any]] = {}
        gate = threading.Barrier(2)

        def go(name: str, sha_: str) -> None:
            gate.wait()
            results[name] = srv.call(
                "POST", f"/p/{name}/api/improve/{ID}/apply", {"draft_sha256": sha_, "gates": True}
            )

        ts = [
            threading.Thread(target=go, args=("a", sha_a)),
            threading.Thread(target=go, args=("b", sha_b)),
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        codes = sorted(st for st, _ in results.values())
        busy = [b.get("code") for st, b in results.values() if st == 409]
        obs["same_repo"] = {"statuses": codes, "busy_codes": busy}
        if codes != [202, 409] or busy != ["apply_running"]:
            problems.append(f"same repository: {obs['same_repo']}")
        for name in ("a", "b"):
            if results[name][0] == 202:
                srv.wait_done(prefix=f"/p/{name}")
    finally:
        srv.stop()
    obs["row_nobody_404"] = obs["nobody"][0] == 404
    return {"obs": obs, "problems": problems, "raw": obs}


def probe_p6(runs: int) -> dict[str, Any]:
    r = repeat(runs, p6_one)
    return {
        "id": "P6",
        "title": "hub routing",
        "predicted": "/p/B/... writes only B's repo and state, A's hashes unchanged, route 3 of A "
        "empty, /p/nobody 404; same-repo hub: concurrent POST 5 gives one 202 and one 409 "
        "apply_running. 3 of 3",
        "observed": json.dumps(r["obs"], sort_keys=True, default=str)[:800],
        "pass": not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ P7 byte-compat


ROUTES_P7 = (
    "/api/model",
    "/api/projects",
    "/api/improve",
    "/api/workspace",
    "/api/contracts",
    "/api/contracts/impact?provider=nobody&candidate=HEAD",
    "/api/linked-questions",
    "/api/linked-notifications",
    "/api/engines",
    "/api/mcp",
    "/api/areas",
    "/api/branches",
    "/api/plans",
    "/api/rebuild",
    "/api/session",
)
ALLOWED_WEB_DIFF = {
    "js/app.js",
    "js/api.js",
    "css/views.css",
    "js/views/improve.js",
    "js/views/contracts.js",
}
ALLOWED_REMOVED = {  # the lines this change replaces in server.py and mock_server.py
    '    `workspace`: the manifest GET /api/workspace answers (API v1.16)."""',
    '    return StudioServer(("127.0.0.1", port), studio, web_dir, verbose, workspace)',
    '    return StudioServer(("127.0.0.1", port), hub, web_dir, verbose, workspace)',
    "    def _refusal(self, fn: Any) -> None:",
    "            self._json(fn())",
}


def base_tree() -> Path:
    d = SCRATCH / "base-tree"
    if (d / "lattice" / "studio" / "server.py").is_file():
        return d
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    p = subprocess.run(
        ["git", "-C", str(ROOT), "archive", BASE_REF], capture_output=True, check=True
    )
    subprocess.run(["tar", "-x", "-C", str(d)], input=p.stdout, check=True)
    return d


def seed_repo(root: Path) -> Path:
    proj = root / "notes"
    shutil.copytree(SEED, proj)
    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "seed")
    (proj / "SCRATCH.txt").write_text("an untracked file\n")
    return proj


def p7_one(i: int) -> dict[str, Any]:
    problems: list[str] = []
    base = base_tree()
    runs: dict[str, dict[str, tuple[int, bytes]]] = {}
    for label, tree, allow in (
        ("base", base, False),
        ("new", TREE, False),
        ("new_allow", TREE, True),
    ):
        root = fresh(f"p7-{i}")  # the same path for all three
        proj = seed_repo(root)
        srv = Server(proj, tree=tree, allow=allow, label=f"p7-{label}-{i}")
        try:
            if label == "base":  # the base has no --allow-apply: Server only adds it when asked
                pass
            runs[label] = {p: srv.call_raw("GET", p) for p in ROUTES_P7}
        finally:
            srv.stop()
    diffs: list[str] = []
    for p in ROUTES_P7:
        for label in ("new", "new_allow"):
            if runs["base"][p] != runs[label][p]:
                diffs.append(f"{p} base vs {label}: {runs['base'][p][0]} vs {runs[label][p][0]}")
    identical = sum(
        1 for p in ROUTES_P7 if all(runs["base"][p] == runs[x][p] for x in ("new", "new_allow"))
    )
    # static: web files
    wb, wn = base / "lattice/studio/web", TREE / "lattice/studio/web"
    fb = {str(p.relative_to(wb)): sha(p.read_bytes()) for p in wb.rglob("*") if p.is_file()}
    fn = {str(p.relative_to(wn)): sha(p.read_bytes()) for p in wn.rglob("*") if p.is_file()}
    changed = sorted(k for k in fb if k in fn and fb[k] != fn[k])
    added = sorted(set(fn) - set(fb))
    removed = sorted(set(fb) - set(fn))
    web_ok = set(changed) <= ALLOWED_WEB_DIFF and set(added) <= ALLOWED_WEB_DIFF and not removed
    # route bodies: the lines removed from server.py and mock_server.py
    removed_lines: dict[str, list[str]] = {}
    for f in ("lattice/studio/server.py", "lattice/studio/mock_server.py"):
        removed_lines[f] = diff_lines(f)[1]
    stray = [ln for v in removed_lines.values() for ln in v if ln not in ALLOWED_REMOVED]
    # --help
    h = {}
    for label, tree in (("base", base), ("new", TREE)):
        h[label] = subprocess.run(
            [sys.executable, "-m", "lattice.studio", "--help"],
            cwd=str(SCRATCH),
            env=env_for(tree),
            capture_output=True,
            text=True,
        ).stdout

    def norm(t: str) -> list[str]:
        keep: list[str] = []
        skipping = False
        for ln in t.splitlines():
            if ln.strip().startswith("--allow-apply"):
                skipping = True
                continue
            if skipping and ln.startswith("                "):  # the option's continuation lines
                continue
            skipping = False
            keep.append(ln)
        return " ".join(re.sub(r"\[--allow-apply\]\s*", "", "\n".join(keep)).split()).split(" ")

    help_ok = (
        norm(h["base"]) == norm(h["new"])
        and "--allow-apply" in h["new"]
        and "--allow-apply" not in h["base"]
    )
    if diffs:
        problems.append(f"route bodies differ: {diffs}")
    if not web_ok:
        problems.append(f"web files: changed {changed}, added {added}, removed {removed}")
    if stray:
        problems.append(f"removed lines other than the signature edits: {stray}")
    if not help_ok:
        problems.append("--help differs by more than --allow-apply")
    obs = {
        "routes_identical": identical,
        "web_changed": changed,
        "web_added": added,
        "web_removed": removed,
        "removed_lines": {f: len(v) for f, v in removed_lines.items()},
        "stray_removed": stray,
        "help_ok": help_ok,
    }
    return {"obs": obs, "problems": problems, "raw": {**obs, "diffs": diffs}}


def probe_p7(runs: int) -> dict[str, Any]:
    r = repeat(runs, p7_one)
    return {
        "id": "P7",
        "title": "byte compatibility",
        "predicted": "15 of 15 byte-identical bodies and statuses (base vs new, the new one with "
        "and without --allow-apply); every web file but js/app.js, js/api.js, css/views.css and "
        "the two new views equals the base's; --help differs only by --allow-apply",
        "observed": json.dumps(r["obs"], sort_keys=True),
        "pass": r["obs"]["routes_identical"] == 15 and not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ P8 mock parity


def keytree(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: keytree(x) for k, x in v.items()}
    if isinstance(v, list):
        return [keytree(v[0])] if v else []
    return type(v).__name__


class MockProc:
    def __init__(self, label: str) -> None:
        self.port = free_port()
        self.log = SCRATCH / f"{label}-{self.port}.log"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "lattice.studio", "--mock", "--port", str(self.port), "--quiet"],
            cwd=str(SCRATCH),
            env=env_for(TREE),
            stdout=self.log.open("w"),
            stderr=subprocess.STDOUT,
        )
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            try:
                if self.call("GET", "/api/projects")[0] == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("mock did not start")

    call = Server.call
    call_raw = Server.call_raw
    get = Server.get
    draft_sha = Server.draft_sha
    stop = Server.stop


def p8_one(i: int) -> dict[str, Any]:
    problems: list[str] = []
    root = fresh(f"p8-{i}")
    top, project, sd = synth(root, "repo", BASE_TOML)
    real = Server(top, allow=True, label=f"p8real-{i}")
    off = Server(synth(root, "off", BASE_TOML)[0], allow=False, label=f"p8off-{i}")
    mock = MockProc(f"p8mock-{i}")
    items: dict[str, bool] = {}
    detail: dict[str, Any] = {}

    def cmp(name: str, a: Any, b: Any) -> None:
        ok = keytree(a) == keytree(b)
        items[name] = ok
        if not ok:
            detail[name] = {"real": keytree(a), "mock": keytree(b)}
            problems.append(f"{name}: key trees differ")

    try:
        pre_m = "/p/lattice-studio"  # the mock's disabled project
        cmp(
            "draft config",
            real.get(f"/api/improve/{ID}/draft")[1],
            mock.get(f"/api/improve/{ID}/draft")[1],
        )
        cmp(
            "draft refused",
            real.get("/api/improve/imp-00000000/draft")[1],
            mock.get("/api/improve/imp-00000000/draft")[1],
        )
        cmp(
            "verify refused",
            real.get(f"/api/improve/{ID}/verify")[1],
            mock.get(f"/api/improve/{ID}/verify")[1],
        )
        cmp(
            "applies empty",
            real.get("/api/improve/applies")[1],
            mock.get("/api/improve/applies")[1],
        )
        cmp(
            "applies disabled",
            off.get("/api/improve/applies")[1],
            mock.get(f"{pre_m}/api/improve/applies")[1],
        )
        rs, rj = real.apply({"gates": True})
        ms, mj = mock.call(
            "POST", f"/api/improve/{ID}/apply", {"draft_sha256": mock.draft_sha(), "gates": True}
        )
        if not (rs == 202 and ms == 202):
            problems.append(f"POST apply statuses: real {rs}, mock {ms}")
        cmp("job running", rj, mj)
        rd = real.wait_done()
        md = mock.get(f"/api/improve/{ID}/apply?wait=30")[1]
        cmp("job done exit 0", rd, md)
        cmp("applies one", real.get("/api/improve/applies")[1], mock.get("/api/improve/applies")[1])
        # a refused job: apply again
        real.apply({"gates": True})
        mock.call(
            "POST", f"/api/improve/{ID}/apply", {"draft_sha256": mock.draft_sha(), "gates": True}
        )
        rrf = real.wait_done()
        mrf = mock.get(f"/api/improve/{ID}/apply?wait=30")[1]
        cmp("job refused", rrf, mrf)
        cmp(
            "discard 0",
            real.call("POST", f"/api/improve/{ID}/discard", {})[1],
            mock.call("POST", f"/api/improve/{ID}/discard", {})[1],
        )
        cmp(
            "discard 3",
            real.call("POST", f"/api/improve/{ID}/discard", {})[1],
            mock.call("POST", f"/api/improve/{ID}/discard", {})[1],
        )
        # statuses of P0 rows a, e, f, g, i, m
        url = f"/api/improve/{ID}/apply"
        sha_r, sha_m = real.draft_sha(), mock.draft_sha()
        rows = {
            "a": (
                off.call("POST", url, {"draft_sha256": ZEROS})[0],
                mock.call("POST", f"{pre_m}{url}", {"draft_sha256": ZEROS})[0],
                403,
            ),
            "e": (
                real.call(
                    "POST", url, raw=b"{}", json_type=False, headers={"Content-Type": "text/plain"}
                )[0],
                mock.call(
                    "POST", url, raw=b"{}", json_type=False, headers={"Content-Type": "text/plain"}
                )[0],
                415,
            ),
            "f": (real.call("POST", url, raw=b"[]")[0], mock.call("POST", url, raw=b"[]")[0], 400),
            "g": (
                real.call("POST", "/api/improve/../apply", {"draft_sha256": sha_r})[0],
                mock.call("POST", "/api/improve/../apply", {"draft_sha256": sha_m})[0],
                422,
            ),
            "i": (real.call("POST", url, {})[0], mock.call("POST", url, {})[0], 422),
            "m": (
                real.call("POST", url, {"draft_sha256": ZEROS})[0],
                mock.call("POST", url, {"draft_sha256": ZEROS})[0],
                409,
            ),
        }
        for k, (a, b, want) in rows.items():
            items[f"status {k}"] = a == b == want
            if a != b or a != want:
                problems.append(f"status row {k}: real {a}, mock {b}, want {want}")
        detail["status_rows"] = {k: [a, b] for k, (a, b, _w) in rows.items()}
    finally:
        for s in (real, off, mock):
            s.stop()
    n = sum(1 for v in items.values() if v)
    return {
        "obs": {"items": items, "ok": n, "total": len(items)},
        "problems": problems,
        "raw": detail,
    }


def probe_p8(runs: int) -> dict[str, Any]:
    r = repeat(runs, p8_one)
    o = r["obs"]
    differ = [k for k, v in o["items"].items() if not v]
    return {
        "id": "P8",
        "title": "mock parity",
        "predicted": "17 of 17: the same key tree and JSON types for draft config and refused, "
        "verify refused, applies (empty, one, disabled), job running, done, refused, discard 0 and "
        "3; the same status for P0 rows a, e, f, g, i, m",
        "observed": f"{o['ok']} of {o['total']} ({differ or 'none differ'})",
        "pass": o["ok"] == 17 and o["total"] == 17 and not r["problems"],
        **r,
    }


# ------------------------------------------------------------------ main

PROBES: dict[str, Callable[[int], Any]] = {
    "P0": probe_p0,
    "P1": probe_p1,
    "P2": probe_p2,
    "P3": probe_p3,
    "P4": probe_p4_p5,
    "P6": probe_p6,
    "P7": probe_p7,
    "P8": probe_p8,
}
NOT_RUN = {
    "P9": "the dogfood gate: dogfood/studio/check.sh (run separately, data in regression/)",
    "P10": "tests/studio/test_studio_ui.py (run with pytest; data in regression/)",
    "browser walk": "NOT RUN by this script (Amendment D)",
}


def main(argv: list[str] | None = None) -> int:
    global RUNS, SCRATCH
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument(
        "--scratch",
        type=Path,
        default=Path(os.environ.get("TMPDIR", "/private/tmp")) / "studio-ui-probes",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)
    RUNS = args.runs
    SCRATCH = args.scratch.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    wanted = [x for x in args.only.split(",") if x] or list(PROBES)
    table: list[dict[str, Any]] = []
    t_all = time.monotonic()
    for key in wanted:
        if key == "P5":
            key = "P4"
        if key not in PROBES:
            print(f"unknown probe {key}", file=sys.stderr)
            continue
        t0 = time.monotonic()
        res = PROBES[key](args.runs)
        res_list = res if isinstance(res, list) else [res]
        for r in res_list:
            r["status"] = "RAN"
            r["seconds"] = round(time.monotonic() - t0, 1)
            (args.out / f"{r['id']}.json").write_text(json.dumps(r, indent=1, default=str) + "\n")
            table.append(
                {
                    k: r[k]
                    for k in (
                        "id",
                        "title",
                        "status",
                        "predicted",
                        "observed",
                        "pass",
                        "seconds",
                        "runs",
                    )
                    if k in r
                }
            )
            print(
                f"{r['id']} {'PASS' if r['pass'] else 'FAIL'} ({r['seconds']}s): "
                f"{r['observed'][:160]}",
                flush=True,
            )
            for p in r.get("problems", []):
                print(f"   problem: {p[:300]}", flush=True)
    summary = {
        "runs": args.runs,
        "table": table,
        "not_run": NOT_RUN,
        "findings": FINDINGS,
        "decisions": DECISIONS,
        "post_hoc_corrections": POST_HOC,
        "wall_s": round(time.monotonic() - t_all, 1),
    }
    prev = args.out / "summary.json"
    if args.only and prev.is_file():  # merge a partial rerun into the table
        old = json.loads(prev.read_text())
        keep = [t for t in old.get("table", []) if t["id"] not in {t2["id"] for t2 in table}]
        summary["table"] = sorted(keep + table, key=lambda t: t["id"])
        summary["post_hoc_corrections"] = old.get("post_hoc_corrections", []) + POST_HOC
    prev.write_text(json.dumps(summary, indent=1) + "\n")
    return 0 if all(t["pass"] for t in table) else 1


if __name__ == "__main__":
    raise SystemExit(main())
