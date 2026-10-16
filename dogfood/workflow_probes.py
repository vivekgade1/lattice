"""Probes of the workflow layer (docs/results/2026-10-15-workflow-layer-prereg.md, section 3).

The ground truth below is independent of the workflow layer: the 11 spec-implied dependency
edges and the 20 multi-route spec skeletons were measured on Streak a0679e9 before any code
was written (the pre-registration, section 1); the Nudge machine is the spec's own; the
source checks read the Go files with regular expressions. Nothing here reads the layer's
output to decide what is expected.

Run (see the pre-registration, section 6; the MySQL arms need the driver venv)::

    "$DRV" dogfood/workflow_probes.py --streak-ref a0679e9 --users ann,ben --static-runs 10 \\
        --runs 10 --scratch "$SCRATCH/p" --out "$SCRATCH/p-data"

The held-out stage (``--held-out``) takes the app directories, their build commands and the
spec as arguments and is run by the orchestrator, once, never by the builder.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importer_go_probes as G  # noqa: E402

REPO = Path(os.environ.get("WORKFLOW_PROBES_REPO", HERE.parent)).resolve()
STREAK = G.STREAK
GO_ENV = G.GO_ENV
CONTAINER = "lattice-wf-"
PRE_REG_COMMIT = "454a8e6"
SPIKE_A_COMMIT = "a5fed2e"
DATA_A = "docs/results/data/2026-10-15-go-frontend"

# ---- ground truth: the 11 spec-implied dependency edges (pre-registration, section 1) -----
# (table, consumer with every path parameter {}, index of the path parameter): a path id
# owned by a `given` row or an earlier body of table T at route C, in Streak's spec.

GT_EDGES: list[tuple[str, str, int]] = [
    ("habits", "GET /v1/habits/{}", 0),
    ("habits", "PATCH /v1/habits/{}", 0),
    ("habits", "DELETE /v1/habits/{}", 0),
    ("habits", "POST /v1/habits/{}/completions", 0),
    ("habits", "GET /v1/habits/{}/completions", 0),
    ("habits", "DELETE /v1/habits/{}/completions/{}", 0),
    ("habits", "GET /v1/habits/{}/progress", 0),
    ("habits", "GET /v1/habits/{}/history", 0),
    ("completions", "DELETE /v1/habits/{}/completions/{}", 1),
    ("devices", "DELETE /v1/devices/{}", 0),
    ("nudges", "POST /v1/nudges/{}/dismiss", 0),
]

# ---- ground truth: the 20 multi-route skeletons of Streak's spec (section 1) --------------
# (method, path with every id {}), `PUT /test/clock` dropped, consecutive repeats collapsed.

H = "/v1/habits/{}"
GT_SKELETONS: list[tuple[str, ...]] = [
    (f"DELETE {H}", f"GET {H}", f"POST {H}/completions", f"DELETE {H}"),
    ("DELETE /v1/me", "GET /v1/me", "POST /v1/auth/login"),
    (
        f"GET {H}",
        f"PATCH {H}",
        f"DELETE {H}",
        f"POST {H}/completions",
        f"GET {H}/progress",
        "POST /v1/nudges/{}/dismiss",
        "DELETE /v1/devices/{}",
    ),
    (f"GET {H}/progress", f"POST {H}/completions"),
    (
        "GET /v1/me",
        "POST /v1/completions/batch",
        "GET /v1/me",
        "POST /v1/habits",
        "GET /v1/me",
    ),
    ("GET /v1/me", "POST /v1/habits"),
    (
        "GET /v1/nudges",
        "POST /v1/nudges/{}/dismiss",
        "GET /v1/nudges",
        "GET /v1/home",
        "GET /v1/sync",
    ),
    (f"PATCH {H}", "GET /v1/habits", f"PATCH {H}"),
    (f"PATCH {H}", f"GET {H}/progress"),
    ("POST /v1/habits", f"PATCH {H}", "POST /v1/habits", f"PATCH {H}", "POST /v1/habits"),
    ("POST /v1/habits", f"POST {H}/completions", f"GET {H}/completions"),
    ("POST /v1/habits", f"POST {H}/completions", "GET /v1/sync", f"DELETE {H}", "GET /v1/sync"),
    ("POST /v1/habits", f"POST {H}/completions", f"PATCH {H}", f"DELETE {H}"),
    (f"POST {H}/completions", f"DELETE {H}/completions/{{}}"),
    (
        f"POST {H}/completions",
        f"DELETE {H}/completions/{{}}",
        f"POST {H}/completions",
    ),
    (
        "POST /v1/nudges/{}/dismiss",
        "GET /v1/nudges",
        "POST /v1/nudges/{}/dismiss",
    ),
    ("POST /v1/nudges/{}/dismiss", "PUT /v1/me"),
    ("PUT /v1/me", "GET /v1/nudges"),
    ("PUT /v1/me", "POST /v1/habits", f"POST {H}/completions"),
    ("PUT /v1/me", f"POST {H}/completions"),
]

# the plan's known flows (probe PK)
K1_ACCOUNT = ("POST /v1/auth/register", "POST /v1/auth/login")
K1_HABIT = ("POST /v1/habits", f"POST {H}/completions", f"GET {H}/progress")
K2_BATCH = ("POST /v1/habits", "POST /v1/completions/batch")
K3_DEVICE = ("POST /v1/devices", "DELETE /v1/devices/{}")

STRICT_SENT = re.compile(r"(/Users|/private|127\.0\.0\.1|localhost|lattice-wf-)")
PORT = re.compile(r":\d{5}\b")
ISO_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def result(pid: str, desc: str, predicted: Any, observed: Any, passed: bool | None, **ev: Any):
    return {
        "id": pid,
        "probe": desc,
        "ran": True,
        "predicted": predicted,
        "observed": observed,
        "pass": passed,
        **ev,
    }


# ---- running the command ------------------------------------------------------------------


def wf_containers() -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(n for n in proc.stdout.split() if n)


def cli_env(tmp: Path) -> dict[str, str]:
    env = dict(os.environ, **GO_ENV)
    env.update(TMPDIR=str(tmp), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(REPO))
    return env


def fresh_tmp(scratch: Path, label: str) -> Path:
    tmp = scratch / "tmp" / label
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    return tmp


def run_cli(
    root: Path, out: Path, scratch: Path, label: str, extra: Sequence[str]
) -> dict[str, Any]:
    """``lattice import workflows ROOT --out OUT``: exit code, wall seconds, the bytes of
    ``workflows.json``, the tmp files it left, and the containers after it."""
    tmp = fresh_tmp(scratch, label)
    shutil.rmtree(out, ignore_errors=True)
    cmd = [sys.executable, "-m", "lattice.cli", "import", "workflows", str(root), "--out", str(out)]
    t0 = time.perf_counter()
    proc = subprocess.run(
        [*cmd, *extra],
        capture_output=True,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
        check=False,
    )
    wall = time.perf_counter() - t0
    f = out / "workflows.json"
    return {
        "rc": proc.returncode,
        "wall": wall,
        "bytes": f.read_bytes() if f.is_file() else b"",
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip()[-600:],
        "tmp_left": sorted(p.name for p in tmp.iterdir()),
        "out_listing": sorted(p.name for p in out.iterdir()) if out.is_dir() else [],
        "containers_after": wf_containers(),
    }


# ---- helpers on a document ----------------------------------------------------------------


def slot_of(route: str, param: str) -> tuple[str, int]:
    """(skeleton, index of the path parameter ``param``) of a ``METHOD /p/{x}`` route."""
    method, _, path = route.partition(" ")
    names = re.findall(r"\{([^}]*)\}", path)
    return f"{method} {G.tpl(path)}", names.index(param) if param in names else -1


def strict_match(sk: Sequence[str], k: Sequence[str]) -> bool:
    return list(sk) == list(k)


def lenient_match(sk: Sequence[str], focus: int, k: Sequence[str]) -> bool:
    """K equals the flow once some of its setup steps are dropped (R4)."""
    return any(list(sk[j:]) == list(k) for j in range(0, focus + 1))


def flow_matches(doc: dict[str, Any], k: Sequence[str], verified_only: bool = False):
    strict, lenient = [], []
    for f in doc["flows"]:
        if verified_only and f["status"] != "verified":
            continue
        if strict_match(f["skeleton"], k):
            strict.append(f["id"])
        if lenient_match(f["skeleton"], f["focus_from"], k):
            lenient.append(f["id"])
    return strict, lenient


def walk_strings(o: Any, skip: Callable[[str], bool], path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}"
            if skip(p):
                continue
            out += walk_strings(v, skip, p)
    elif isinstance(o, list):
        for v in o:
            out += walk_strings(v, skip, path + "[]")
    elif isinstance(o, str):
        out.append((path, o))
    return out


# ---- the probes ---------------------------------------------------------------------------


def probe_pe(static: dict[str, Any], dyn: dict[str, Any] | None) -> dict[str, Any]:
    """Dependency edges against the 11 spec-implied (table, route, param)."""
    doc = dyn or static
    path_edges = [e for e in doc["edges"] if e["param_source"] == "path"]
    body_edges = [e for e in doc["edges"] if e["param_source"] == "body"]
    got = {(e["table"], *slot_of(e["consumer"], e["param"])) for e in path_edges}
    want = {(t, c, i) for t, c, i in GT_EDGES}
    hit = sorted(want & got)
    missing = sorted(want - got)
    extra = sorted(got - want)
    sources: dict[str, set[str]] = {}
    for e in doc["edges"]:
        if e["param_source"] == "path":
            sources.setdefault(e["table"], set()).add(e["id_source"])
    static_got = {
        (e["table"], *slot_of(e["consumer"], e["param"]))
        for e in static["edges"]
        if e["param_source"] == "path"
    }
    return result(
        "PE",
        "dependency edges vs the 11 spec-implied; extras; id sources; the batch body edge",
        {
            "hit": "11/11 (9-11)",
            "extra": "0 (0-2)",
            "id_source": {
                "habits": "client",
                "completions": "client",
                "devices": "response",
                "nudges": "list",
            },
        },
        {
            "hit": f"{len(hit)}/{len(want)}",
            "missing": missing,
            "extra": extra,
            "id_source": {k: sorted(v) for k, v in sorted(sources.items())},
            "body_edges": [
                {k: e[k] for k in ("table", "producer", "consumer", "param", "id_source")}
                for e in body_edges
            ],
            "static_equals_dynamic": static_got == got,
            "mode": "observed" if dyn else "static",
        },
        len(hit) >= 10 and len(extra) <= 2,
    )


def probe_ps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Structural recall against the 20 multi-route spec skeletons, strict and lenient;
    precision (flows matching any skeleton) is reported."""
    rows = []
    for k in GT_SKELETONS:
        s_all, l_all = flow_matches(doc, k)
        s_ver, l_ver = flow_matches(doc, k, True)
        rows.append(
            {
                "skeleton": list(k),
                "strict": len(s_all),
                "lenient": len(l_all),
                "strict_verified": len(s_ver),
                "lenient_verified": len(l_ver),
            }
        )
    strict = sum(1 for r in rows if r["strict"])
    lenient = sum(1 for r in rows if r["lenient"])
    strict_v = sum(1 for r in rows if r["strict_verified"])
    lenient_v = sum(1 for r in rows if r["lenient_verified"])
    n = len(doc["flows"])
    prec_strict = sum(
        1 for f in doc["flows"] if any(strict_match(f["skeleton"], k) for k in GT_SKELETONS)
    )
    prec_len = sum(
        1
        for f in doc["flows"]
        if any(lenient_match(f["skeleton"], f["focus_from"], k) for k in GT_SKELETONS)
    )
    ps = result(
        "PS",
        "structural recall vs the 20 multi-route spec skeletons (strict, lenient)",
        {"lenient (the gate, R4)": "3/20 (1-5)", "verified-only lenient": "1-3"},
        {
            "lenient": f"{lenient}/20",
            "strict": f"{strict}/20",
            "lenient_verified_only": f"{lenient_v}/20",
            "strict_verified_only": f"{strict_v}/20",
            "matched_lenient": [r["skeleton"] for r in rows if r["lenient"]],
            "matched_strict": [r["skeleton"] for r in rows if r["strict"]],
        },
        lenient >= 1,
        rows=rows,
    )
    ps2 = result(
        "PS2",
        "spec precision: flows matching any spec skeleton / flows (report only)",
        "0.03-0.15",
        {
            "lenient": round(prec_len / n, 4) if n else None,
            "strict": round(prec_strict / n, 4) if n else None,
            "flows": n,
        },
        None,
    )
    return [ps, ps2]


def probe_pk(doc: dict[str, Any]) -> dict[str, Any]:
    """The plan's known flows: synthesised, and verified."""

    def find(k: Sequence[str]):
        # the flows of the default caller: an owner_guard / guard flow shares the skeleton
        # but expects a refusal, so it is not the known flow
        s, lenient = flow_matches(doc, k)
        ids = set(s) | set(lenient)
        return [
            f for f in doc["flows"] if f["id"] in ids and f["kind"] not in ("owner_guard", "guard")
        ]

    k1a, k1h, k2, k3 = find(K1_ACCOUNT), find(K1_HABIT), find(K2_BATCH), find(K3_DEVICE)

    def best(fs: list[dict[str, Any]]) -> str | None:
        order = {"verified": 0, "unverified": 1, "failed": 2}
        return min((f["status"] for f in fs), key=lambda s: order[s]) if fs else None

    synth = {"K1": bool(k1a and k1h), "K2": bool(k2), "K3": bool(k3)}
    status = {
        "K1": [best(k1a), best(k1h)],
        "K2": best(k2),
        "K3": best(k3),
    }
    verified = {
        "K1": best(k1a) == "verified" and best(k1h) == "verified",
        "K2": best(k2) == "verified",
        "K3": best(k3) == "verified",
    }
    return result(
        "PK",
        "the plan's known flows: K1 account + habit, K2 batch, K3 device",
        {"synthesised": "3/3", "verified": "K1 and K3; K2 failed (nested items, 422)"},
        {
            "synthesised": f"{sum(synth.values())}/3",
            "verified": f"{sum(verified.values())}/3",
            "synth": synth,
            "status": status,
            "k2_reason": (k2[0]["reason"] if k2 else None),
        },
        all(synth.values()) and sum(verified.values()) >= 2,
        flows={
            "K1_account": [f["id"] for f in k1a],
            "K1_habit": [f["id"] for f in k1h],
            "K2": [f["id"] for f in k2],
            "K3": [f["id"] for f in k3],
        },
    )


def probe_pf(doc: dict[str, Any]) -> dict[str, Any]:
    n = len(doc["flows"])
    c = {"verified": 0, "unverified": 0, "failed": 0}
    for f in doc["flows"]:
        c[f["status"]] += 1
    frac = {k: round(v / n, 4) if n else 0.0 for k, v in c.items()}
    kinds: dict[str, dict[str, int]] = {}
    for f in doc["flows"]:
        kinds.setdefault(f["kind"], {}).setdefault(f["status"], 0)
        kinds[f["kind"]][f["status"]] += 1
    return result(
        "PF",
        "flows on S: count; verified / unverified / failed fractions",
        {
            "count": "63 (45-80)",
            "verified": "0.55-0.90",
            "unverified": "0.05-0.20",
            "failed": "0.00-0.20",
        },
        {
            "count": n,
            "counts": c,
            "fractions": frac,
            "by_kind": kinds,
            "in_range": {
                "count": 45 <= n <= 80,
                "verified": 0.55 <= frac["verified"] <= 0.90,
                "unverified": 0.05 <= frac["unverified"] <= 0.20,
                "failed": frac["failed"] <= 0.20,
            },
        },
        45 <= n <= 80 and frac["verified"] >= 0.50,
        reasons={f["id"]: f["reason"] for f in doc["flows"] if f["status"] != "verified"},
    )


def grep_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def gin_tail(route_path: str) -> str:
    return re.sub(r"\{([^}]*)\}", r":\1", route_path)


def route_line_holds(root: Path, route: str, at: str) -> bool:
    """The registration line holds the method and a path literal that ends the route's path."""
    method, _, path = route.partition(" ")
    f, _, line = at.rpartition(":")
    lines = grep_lines(root / f)
    text = lines[int(line) - 1]
    if f".{method}(" not in text and ".Handle(" not in text and ".Match(" not in text:
        return False
    full = gin_tail(path)
    for lit in re.findall(r'"([^"]*)"', text):
        if lit and full.endswith(lit):
            return True
    return False


def handler_line_holds(root: Path, route: str, at: str) -> bool:
    method, _, path = route.partition(" ")
    key = (method, G.tpl(path))
    if key not in G.GT_HANDLERS:
        return False
    want = G.gt_handler_line(root, key)
    return want is not None and f"{want[0]}:{want[1]}" == at


def probe_pg(root: Path, doc: dict[str, Any], extract: dict[str, Any]) -> dict[str, Any]:
    """Evidence: the route and handler lines hold their symbols (read from the Go source
    with regular expressions), every binding has a param_use, every expected status >= 400 is
    in the route's raises / guards."""
    mws = {m["func"]: m for m in extract["middleware"]}
    refusal: dict[str, set[int]] = {}
    for r in extract["routes"]:
        st = {x["status"] for x in r["raises"] if x["status"]}
        st |= {g["status"] for g in r["guards"] if g["status"]}
        for ref in r["middleware"]:
            st |= {x["status"] for x in mws.get(ref["func"], {}).get("raises", []) if x["status"]}
        refusal[f"{r['method']} {r['path_template']}"] = st
    total = {"route": 0, "handler": 0, "binding": 0, "status": 0, "param_use_line": 0}
    bad: dict[str, list[str]] = {k: [] for k in total}
    seen: set[tuple[str, str]] = set()
    for f in doc["flows"]:
        for s in f["steps"]:
            if s["kind"] != "request":
                continue
            ev = {e["what"]: e["at"] for e in s["evidence"]}
            if ("route", s["route"]) not in seen:
                seen.add(("route", s["route"]))
                total["route"] += 1
                if not route_line_holds(root, s["route"], ev["route"]):
                    bad["route"].append(f"{s['route']} @ {ev['route']}")
                total["handler"] += 1
                if "handler" not in ev or not handler_line_holds(root, s["route"], ev["handler"]):
                    bad["handler"].append(f"{s['route']} @ {ev.get('handler')}")
            uses = [e["at"] for e in s["evidence"] if e["what"] == "param_use"]
            for b in s["bindings"]:
                total["binding"] += 1
                if not uses:
                    bad["binding"].append(f"{f['id']} step {s['i']} {b['param']}")
            for at in uses:
                total["param_use_line"] += 1
                fl, _, ln = at.rpartition(":")
                try:
                    text = grep_lines(root / fl)[int(ln) - 1].strip()
                except (OSError, IndexError, ValueError):
                    text = ""
                if not text:
                    bad["param_use_line"].append(at)
            exp = s["expect_status"]
            if isinstance(exp, int) and exp >= 400:
                total["status"] += 1
                if exp not in refusal.get(s["route"], set()):
                    bad["status"].append(f"{f['id']} step {s['i']} {s['route']} {exp}")
    ok = all(not v for v in bad.values())
    return result(
        "PG",
        "evidence: route and handler lines hold the symbol; bindings have param_uses; "
        "statuses >= 400 are in raises/guards",
        "100%",
        {k: f"{total[k] - len(bad[k])}/{total[k]}" for k in total},
        ok,
        bad={k: v[:20] for k, v in bad.items() if v},
    )


def probe_pm(root: Path, doc: dict[str, Any]) -> dict[str, Any]:
    """Nudge.delivery against the spec's own Machine."""
    sys.path.insert(0, str(root))
    sys.modules.pop("spec", None)
    try:
        import spec as streak_spec  # noqa: PLC0415

        (truth,) = [
            m for m in streak_spec.SPEC.machines if (m.table, m.column) == ("Nudge", "delivery")
        ]
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("spec", None)
    want_edges = sorted(truth.edges)
    want_states = sorted(truth.states())
    want_initial = sorted(truth.initial)
    ms = [m for m in doc["machines"] if (m["model"], m["column"]) == ("Nudge", "delivery")]
    if not ms:
        return result("PM", "Nudge.delivery", "a machine", "none", False)
    m = ms[0]
    edges = sorted((e["from"], e["to"]) for e in m["edges"])
    moves = sum(e["observed"] for e in m["edges"])
    refuted = m["status"] == "refuted"
    obs_initial = m.get("observed_initial")
    mode = doc["run"]["mode"]
    states_ok = sorted(m["states"]) == want_states
    edges_ok = edges == want_edges
    initial_ok = sorted(m["initial"]) == want_initial
    return result(
        "PM",
        "Nudge.delivery: states, static edges, initial vs the spec's Machine; observed",
        {
            "states": "4/4",
            "edges": "pending->{sent,failed,suppressed} exactly",
            "initial": "{pending, suppressed}",
            "observed_initial": ">= {pending}",
            "observed_moves": "0 (0-1)",
            "status": "static",
            "refuted": 0,
        },
        {
            "states": m["states"],
            "edges": edges,
            "initial": m["initial"],
            "observed_initial": obs_initial,
            "observed_moves": moves,
            "status": m["status"],
            "sweeps": m["sweeps"],
            "mode": mode,
            "spec": {"states": want_states, "edges": want_edges, "initial": want_initial},
        },
        states_ok and edges_ok and initial_ok and not refuted,
    )


def probe_pv(doc: dict[str, Any]) -> dict[str, Any]:
    """Value sources: period from the literals; installation_id, timezone, email from the
    probe list; the check-in date bound from start_date."""
    found: dict[str, Any] = {}
    for f in doc["flows"]:
        for s in f["steps"]:
            if s["kind"] != "request":
                continue
            vs, body = s["value_sources"], s["json"] or {}
            if s["route"] == "POST /v1/habits" and "period" in vs:
                found.setdefault("period", (vs["period"], body.get("period")))
            if s["route"] == "POST /v1/auth/login" and "installation_id" in vs:
                found.setdefault(
                    "installation_id", (vs["installation_id"], body.get("installation_id"))
                )
            if s["route"] == "POST /v1/devices" and "timezone" in vs:
                found.setdefault("timezone", (vs["timezone"], body.get("timezone")))
            if s["route"] == "POST /v1/auth/register" and "email" in vs:
                found.setdefault("email", (vs["email"], body.get("email")))
            if s["route"] == "POST /v1/habits/{id}/completions" and "date" in vs:
                found.setdefault("date", (vs["date"], body.get("date")))
    want = {
        "period": "literal",
        "installation_id": "probe",
        "timezone": "probe",
        "email": "probe",
        "date": "bound",
    }
    got = {k: v[0] for k, v in found.items()}
    return result(
        "PV",
        "value sources: period (literal), installation_id / timezone / email (probe), date (bound)",
        want,
        {
            "sources": got,
            "values": {k: v[1] for k, v in found.items()},
            "matches": {k: got.get(k) == want[k] for k in want},
            "mode": doc["run"]["mode"],
        },
        None,
    )


def probe_pd(
    static_blobs: list[bytes], dyn_blobs: list[bytes], docs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Byte identity (static runs including one from a second clone path; dynamic runs) and
    the content scan: no absolute path, port, container name or timestamp outside values."""

    def same(blobs: list[bytes]) -> str:
        return f"{sum(1 for b in blobs if b == blobs[0])}/{len(blobs)}" if blobs else "0/0"

    def skip(p: str) -> bool:
        return p.endswith(".json") or p.endswith("bindings[].value") or ".expect_db" in p

    leaks: list[str] = []
    for doc in docs:
        for path, text in walk_strings(doc, skip):
            if STRICT_SENT.search(text) or PORT.search(text) or ISO_STAMP.search(text):
                leaks.append(f"{path}: {text[:80]}")
    ok = (
        bool(static_blobs)
        and all(b == static_blobs[0] for b in static_blobs)
        and (not dyn_blobs or all(b == dyn_blobs[0] for b in dyn_blobs))
        and not leaks
    )
    return result(
        "PD",
        "static runs (+1 from a second clone path) and dynamic runs byte-identical; no "
        "absolute path / port / container / timestamp outside values",
        {"static": "11/11", "dynamic": "10/10", "leaks": []},
        {
            "static": same(static_blobs),
            "dynamic": same(dyn_blobs),
            "leaks": sorted(set(leaks))[:20],
        },
        ok,
        sha256_static=hashlib.sha256(static_blobs[0]).hexdigest() if static_blobs else None,
        sha256_dynamic=hashlib.sha256(dyn_blobs[0]).hexdigest() if dyn_blobs else None,
    )


def probe_pw(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    changed = sorted(k for k in before if after.get(k) != before[k])
    new = sorted(set(after) - set(before))
    return result(
        "PW",
        "tree listing + sha256 of every S file: after the build vs after the dynamic runs",
        {"changed": 0, "new": 0},
        {"changed": len(changed), "new": len(new)},
        not changed and not new,
        changed=changed,
        new=new,
        files=len(before),
    )


# ---- the independent replay (PR) ----------------------------------------------------------


def replay_worker(args: argparse.Namespace) -> int:
    """A separate process: rebuild the verified flows' Scenarios from workflows.json and run
    ``check(layers="D")`` on a fresh container. Prints a JSON verdict."""
    import dataclasses  # noqa: PLC0415

    from lattice.importer.emit_spec import build_spec  # noqa: PLC0415
    from lattice.importer.frontends.go_gin import (  # noqa: PLC0415
        GoGinFrontEnd,
        _harness_attr,
        _studio_app,
    )
    from lattice.importer.lift import load_profile  # noqa: PLC0415
    from lattice.importer.profile_gen import generate_profile  # noqa: PLC0415
    from lattice.spec import dbserver  # noqa: PLC0415
    from lattice.spec.check import check  # noqa: PLC0415
    from lattice.spec.model import Await, Scenario, Service, Step  # noqa: PLC0415

    root = Path(args.root)
    doc = json.loads(Path(args.doc).read_text())
    cfg = _studio_app(root)
    make_app = _harness_attr(root, cfg["app"])
    build_db = _harness_attr(root, cfg["db"])
    login = _harness_attr(root, cfg["login"]) if "login" in cfg else None
    runtime = _harness_attr(root, cfg["runtime"]) if "runtime" in cfg else None
    services = dict(_harness_attr(root, cfg["services"])) if "services" in cfg else {}
    scenarios, users = [], set()
    for f in doc["flows"]:
        if f["status"] != "verified":
            continue
        steps = []
        for s in f["steps"]:
            if s["kind"] == "request":
                steps.append(
                    Step(
                        s["method"],
                        s["path"],
                        user=s["caller"],
                        json=s["json"],
                        expect_status=s["expect_status"],
                    )
                )
                if s["caller"]:
                    users.add(s["caller"])
            else:
                steps.append(Await(s["service"], s["expect_db"], within_s=s["within_s"]))
        scenarios.append(Scenario(f["id"], steps=tuple(steps)))
    fe = GoGinFrontEnd()
    model = fe.extract(root, ".")
    module = load_profile(generate_profile(model), "lattice_wf_replay_profile")
    spec = build_spec(model, module.profile(), users={})
    spec = dataclasses.replace(
        spec,
        scenarios=tuple(scenarios),
        invariants=(),
        machines=(),
        users={u: (u, u) for u in sorted(users)},
        services=tuple(Service(n) for n in sorted(services)),
    )
    saved = {k: v for k, v in os.environ.items() if k.startswith("LATTICE_") and k.endswith("_URL")}
    for k in saved:
        del os.environ[k]
    server, _ = dbserver.server_for("mysql", None, name_prefix=CONTAINER)
    failures: dict[str, str] = {}
    try:
        report = check(
            spec,
            make_app,
            build_db,
            layers="D",
            runtime=runtime,
            login=login,
            services=services or None,
            database="mysql",
            database_url=server.url,
        )
        for fd in report.findings:
            if fd.layer == "D" and fd.rule.startswith("scenario: "):
                failures.setdefault(
                    fd.rule[len("scenario: ") :], json.dumps(fd.details, default=str)[:200]
                )
    finally:
        server.stop()
    print(
        json.dumps({"scenarios": len(scenarios), "failures": failures, "n_failures": len(failures)})
    )
    return 0


def probe_pr(root: Path, doc_path: Path, scratch: Path) -> dict[str, Any]:
    tmp = fresh_tmp(scratch, "replay")
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--replay-worker",
            "--root",
            str(root),
            "--doc",
            str(doc_path),
        ],
        capture_output=True,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
        check=False,
    )
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        return result(
            "PR",
            "independent replay of the verified flows",
            "0 failures",
            {"error": proc.stderr[-500:]},
            False,
            wall=round(wall, 1),
        )
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    return result(
        "PR",
        "independent replay: a separate process rebuilds each verified flow's Scenario from "
        "workflows.json and runs check(layers='D') on a fresh container",
        "0 failures",
        {"scenarios": got["scenarios"], "failures": got["n_failures"], "failed": got["failures"]},
        got["n_failures"] == 0 and got["scenarios"] > 0,
        wall_s=round(wall, 1),
    )


# ---- containers, interrupts ---------------------------------------------------------------


def probe_pt_sigint(root: Path, scratch: Path, extra: Sequence[str]) -> dict[str, Any]:
    """A run killed with SIGINT mid-flow leaves no ``lattice-wf-`` container and no app."""
    out = scratch / "sigint-out"
    shutil.rmtree(out, ignore_errors=True)
    tmp = fresh_tmp(scratch, "sigint")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lattice.cli",
            "import",
            "workflows",
            str(root),
            "--out",
            str(out),
            *extra,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
    )
    seen = False
    snaps = 0
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and proc.poll() is None:
        if wf_containers():
            seen = True
            sc = out / ".scratch"
            snaps = len(list(sc.glob("snap-*.db"))) if sc.is_dir() else 0
            if snaps >= 12:
                break
        time.sleep(0.2)
    mid_flow = proc.poll() is None
    if mid_flow:
        proc.send_signal(signal.SIGINT)
    try:
        proc.communicate(timeout=90)
        exited = True
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        exited = False
    time.sleep(1.0)
    left = wf_containers()
    apps = subprocess.run(
        ["pgrep", "-f", str(root / "bin")], capture_output=True, text=True, check=False
    ).stdout.split()
    return result(
        "PT-sigint",
        "containers lattice-wf-* (and app processes) after a run killed with SIGINT mid-flow",
        {"containers": 0, "apps": 0},
        {
            "containers": len(left),
            "names": left,
            "apps": len(apps),
            "interrupted_mid_flow": mid_flow,
            "container_seen": seen,
            "snapshots_when_interrupted": snaps,
            "exited": exited,
            "returncode": proc.returncode,
            "scratch_removed": not (out / ".scratch").exists(),
        },
        mid_flow and exited and not left and not apps,
    )


# ---- byte-compat (PB) ---------------------------------------------------------------------


def old_binary(scratch: Path, ref: str) -> Path:
    src = scratch / f"gocheck-{ref}"
    out = scratch / f"gocheck-{ref}-bin"
    if out.is_file():
        return out
    G.archive(REPO, ref, src)
    env = dict(os.environ, **GO_ENV)
    subprocess.run(
        ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(out), "."],
        cwd=src / "lattice/gocheck/src",
        env=env,
        check=True,
        capture_output=True,
    )
    return out


def probe_pb_extract(scratch: Path, new_bin: Path, s_root: Path, s4_root: Path) -> dict[str, Any]:
    """The default ``gocheck extract`` JSON on S, S4 and every fixture is identical to the
    binary of a5fed2e; the sidecars and the model equal the committed Spike A output."""
    old = old_binary(scratch, SPIKE_A_COMMIT)
    jobs = {"S": s_root, "S4": s4_root}
    for d in sorted((REPO / "tests/gocheck/fixtures/goextract").parent.glob("goextract*")):
        if d.name == "goextract" or d.name == "goextract_workflow":
            jobs[f"fixture:{d.name}"] = d
    same, diffs = 0, []
    digests = {}
    for label, root in jobs.items():
        a, _ = G.extract_bytes(old, root)
        b, _ = G.extract_bytes(new_bin, root)
        digests[label] = hashlib.sha256(b).hexdigest()
        if a == b:
            same += 1
        else:
            diffs.append(label)
    committed = {}
    for label, name in (("S", "s-extract.json"), ("S4", "s4-extract.json")):
        blob = (REPO / DATA_A / name).read_bytes()
        committed[label] = hashlib.sha256(blob).hexdigest() == digests[label]
    from lattice.importer.frontends.go_gin import model_from_extract  # noqa: PLC0415

    raw, _ = G.extract_bytes(new_bin, s_root)
    model_json = model_from_extract(json.loads(raw)).to_json()
    committed["S model.to_json"] = (REPO / DATA_A / "s-model.json").read_text() == model_json
    ok = not diffs and all(committed.values())
    return result(
        "PB-extract",
        "default gocheck extract on S, S4 and the fixtures: byte-identical to a5fed2e's binary "
        "and to the committed Spike A output (sidecar and to_json sha256)",
        {"identical": "all", "committed": "equal"},
        {"identical": f"{same}/{len(jobs)}", "different": diffs, "committed_equal": committed},
        ok,
        sha256=digests,
    )


def git(*a: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=True)
    return [x for x in out.stdout.split("\n") if x.strip()]


OWNED_EXACT = {
    "lattice/gocheck/src/goextract.go",
    "lattice/gocheck/src/goextract_workflow.go",
    "lattice/importer/workflows.py",
    "lattice/importer/workflow_run.py",
    "lattice/importer/frontends/go_gin.py",
    "lattice/importer/cli.py",
    "tests/gocheck/test_goextract_workflow.py",
    "tests/importer/test_workflows.py",
    "dogfood/workflow_probes.py",
    "dogfood/workflow_mutants.py",
}
OWNED_PREFIX = (
    "tests/gocheck/fixtures/goextract_workflow/",
    "docs/results/2026-10-15-workflow-layer-results.md",
    "docs/results/data/2026-10-15-workflow-layer/",
)
FROZEN = [
    "tests/importer/goldens",
    "lattice/importer/mine.py",
    "lattice/importer/review.py",
    "lattice/importer/emit_spec.py",
    "lattice/importer/appmodel.py",
    "lattice/importer/profile_gen.py",
    "lattice/importer/rules.py",
    "lattice/importer/transport.py",
    "lattice/spec",
    "lattice/gocheck/src/main.go",
]


def probe_pb_diff() -> dict[str, Any]:
    """The change set against HEAD (the pre-registration commit) is the owned files only; the
    modules the pre-registration freezes, the goldens, the other gocheck files and the
    existing tests have an empty diff."""
    changed = sorted(
        {x.split("\t")[-1] for x in git("diff", "HEAD", "--name-status")}
        | set(git("ls-files", "--others", "--exclude-standard"))
    )
    foreign = [c for c in changed if c not in OWNED_EXACT and not c.startswith(OWNED_PREFIX)]
    frozen = git("diff", "HEAD", "--stat", "--", *FROZEN)
    other_go = [c for c in changed if c.startswith("lattice/gocheck/src/") and c not in OWNED_EXACT]
    return result(
        "PB-diff",
        "changes against HEAD: owned files only; frozen modules, goldens, existing tests untouched",
        {"foreign": [], "frozen_diff": []},
        {
            "foreign": foreign,
            "frozen_diff": frozen,
            "other_gocheck_files": other_go,
            "changed": changed,
        },
        not foreign and not frozen and not other_go,
    )


def run_pytest(scratch: Path) -> dict[str, Any]:
    tmp = fresh_tmp(scratch, "pytest")
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/importer",
            "tests/gocheck",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
        check=False,
    )
    tail = proc.stdout.strip().splitlines()[-3:]
    return result(
        "PB-pytest",
        "pytest tests/importer tests/gocheck",
        "passes",
        tail,
        proc.returncode == 0,
        wall_s=round(time.perf_counter() - t0, 1),
    )


def run_spike_a(scratch: Path, out: Path) -> dict[str, Any]:
    """The Spike A probes that need no Docker (PR1-PU, PD, PW) against the changed extractor."""
    tmp = fresh_tmp(scratch, "spike-a")
    only = "PR1,PR2,PR3,PA,PA2,PT,PE,PF,PO,PM,PU,PS,PD,PW"
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "importer_go_probes.py"),
            "--runs",
            "10",
            "--scratch",
            str(scratch / "spike-a"),
            "--out",
            str(out / "spike-a"),
            "--only",
            only,
        ],
        capture_output=True,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
        check=False,
    )
    probes = {}
    pj = out / "spike-a" / "probes.json"
    if pj.is_file():
        for r in json.loads(pj.read_text())["results"]:
            probes[r["id"]] = r["pass"]
    wanted = [p for p in only.split(",")]
    failed = sorted(k for k, v in probes.items() if v is False and k in wanted)
    return result(
        "PB-spike-a",
        "Spike A probes PR1-PU, PD, PW against the changed extractor",
        "all pass",
        {"ran": sorted(probes), "failed": failed, "rc": proc.returncode},
        proc.returncode == 0 and not failed and bool(probes),
        wall_s=round(time.perf_counter() - t0, 1),
    )


# ---- go / python hygiene (PB-lint) --------------------------------------------------------


def probe_lint() -> dict[str, Any]:
    env = dict(os.environ, **GO_ENV)
    src = REPO / "lattice/gocheck/src"
    vet = subprocess.run(["go", "vet", "./..."], cwd=src, env=env, capture_output=True, text=True)
    fmt = subprocess.run(["gofmt", "-l", "."], cwd=src, capture_output=True, text=True)
    pys = sorted(
        {
            c
            for c in git("diff", "HEAD", "--name-only")
            + git("ls-files", "--others", "--exclude-standard")
            if c.endswith(".py")
        }
    )
    ruff = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *pys], cwd=REPO, capture_output=True, text=True
    )
    new_py = [p for p in pys if p in OWNED_EXACT and (REPO / p).is_file()]
    fmt_py = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", *new_py],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return result(
        "PB-lint",
        "go vet, gofmt -l, ruff check and ruff format --check on the changed files",
        "clean",
        {
            "go_vet_rc": vet.returncode,
            "gofmt": fmt.stdout.split(),
            "ruff_rc": ruff.returncode,
            "ruff_format_rc": fmt_py.returncode,
            "files": pys,
        },
        vet.returncode == 0
        and not fmt.stdout.strip()
        and ruff.returncode == 0
        and fmt_py.returncode == 0,
    )


# ---- the Streak stage ---------------------------------------------------------------------


GATED = (
    "PE",
    "PS",
    "PK",
    "PF",
    "PR",
    "PG",
    "PM",
    "PD",
    "PW",
    "PT",
    "PB-extract",
    "PB-diff",
    "PX",
)


def run_streak(args: argparse.Namespace) -> int:
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    not_run: list[str] = []
    results: list[dict[str, Any]] = []
    users = [u for u in args.users.split(",") if u]
    user_args = [a for u in users for a in ("--user", u)]

    def want(pid: str) -> bool:
        return only is None or pid in only

    def record(r: dict[str, Any] | list[dict[str, Any]]) -> None:
        for item in r if isinstance(r, list) else [r]:
            results.append(item)
            print(
                f"{item['id']}: {item['pass']} {json.dumps(item['observed'], default=str)[:220]}",
                flush=True,
            )

    s_root = scratch / "S"
    s_second = scratch / "S2" / "deeper" / "S"
    s4_root = scratch / "S4"
    G.archive(STREAK, args.streak_ref, s_root)
    s_second.parent.mkdir(parents=True, exist_ok=True)
    G.archive(STREAK, args.streak_ref, s_second)
    G.archive(STREAK, args.streak_ref2, s4_root)
    G.go_build(s_root)
    snap0 = G.tree_digest(s_root)
    binary = G._gocheck_binary()
    extract_raw, _ = G.extract_bytes(binary, s_root)
    extract = json.loads(extract_raw)

    # static: 10 runs + 1 from a second clone path, and the synthesis wall
    static_blobs: list[bytes] = []
    static_walls: list[float] = []
    static_doc: dict[str, Any] | None = None
    if want("PD") or want("PE") or want("PX") or want("PS") or want("PK") or want("PF"):
        for i in range(args.static_runs):
            r = run_cli(
                s_root, out / "static-run", scratch, f"static-{i}", ["--static", *user_args]
            )
            if r["rc"] != 0:
                record(result("PD", "static run", "exit 0", r["stderr"], False))
                return 1
            static_blobs.append(r["bytes"])
            static_walls.append(r["wall"])
        r2 = run_cli(
            s_second, out / "static-run2", scratch, "static-second", ["--static", *user_args]
        )
        static_blobs.append(r2["bytes"])
        static_doc = json.loads(static_blobs[0])
        (out / "static-workflows.json").write_bytes(static_blobs[0])

    # dynamic: N runs
    dyn_blobs: list[bytes] = []
    dyn_walls: list[float] = []
    dyn_doc: dict[str, Any] | None = None
    leaked_after: list[list[str]] = []
    if args.runs > 0 and not args.no_docker:
        for i in range(args.runs):
            r = run_cli(s_root, out / "dyn-run", scratch, f"dyn-{i}", user_args)
            leaked_after.append(r["containers_after"])
            if r["rc"] != 0:
                record(result("PD", f"dynamic run {i}", "exit 0", r["stderr"], False))
                return 1
            dyn_blobs.append(r["bytes"])
            dyn_walls.append(r["wall"])
            if i == 0:
                (out / "workflows.json").write_bytes(r["bytes"])
                dyn_doc = json.loads(r["bytes"])
        snap1 = G.tree_digest(s_root)
    else:
        snap1 = G.tree_digest(s_root)
        not_run += ["PR", "PT-sigint"]
    doc = dyn_doc or static_doc
    assert doc is not None or only is not None
    if static_doc is not None and want("PE"):
        record(probe_pe(static_doc, dyn_doc))
    if doc is not None and want("PS"):
        record(probe_ps(doc))
    if doc is not None and want("PK"):
        record(probe_pk(doc))
    if doc is not None and want("PF"):
        record(probe_pf(doc))
    if doc is not None and want("PG"):
        record(probe_pg(s_root, doc, extract))
    if doc is not None and want("PM"):
        record(probe_pm(s_root, doc))
    if doc is not None and want("PV"):
        record(probe_pv(doc))
    if want("PD") and static_blobs:
        docs = [json.loads(b) for b in {*static_blobs, *dyn_blobs}]
        record(probe_pd(static_blobs, dyn_blobs, docs))
    if want("PW"):
        record(probe_pw(snap0, snap1))
    if want("PT"):
        flat = sorted({c for lst in leaked_after for c in lst})
        record(
            result(
                "PT",
                "containers lattice-wf-* after every run",
                0,
                {
                    "after_each_run": [len(x) for x in leaked_after],
                    "names": flat,
                    "now": wf_containers(),
                },
                not flat and not wf_containers(),
            )
        )
    if want("PX"):
        med = sorted(static_walls)[len(static_walls) // 2] if static_walls else None
        record(
            result(
                "PX",
                "wall: synthesis (static) and a full dynamic run on S",
                {"static_s": "0.1-1 (<= 5)", "dynamic_min": "3-12 (<= 30)"},
                {
                    "static_median_s": round(med, 2) if med is not None else None,
                    "static_max_s": round(max(static_walls), 2) if static_walls else None,
                    "dynamic_s": [round(w, 1) for w in dyn_walls],
                    "dynamic_median_s": round(sorted(dyn_walls)[len(dyn_walls) // 2], 1)
                    if dyn_walls
                    else None,
                },
                (med is not None and med <= 5.0) and all(w <= 1800 for w in dyn_walls),
            )
        )
    if want("PR") and dyn_doc is not None and not args.no_docker:
        record(probe_pr(s_root, out / "workflows.json", scratch))
    elif want("PR"):
        not_run.append("PR")
    if want("PT-sigint") and not args.no_docker:
        record(probe_pt_sigint(s_root, scratch, user_args))
    elif want("PT-sigint"):
        not_run.append("PT-sigint")

    def want_pb(sub: str) -> bool:
        return only is None or "PB" in only or sub in only

    if want_pb("PB-extract"):
        record(probe_pb_extract(scratch, binary, s_root, s4_root))
    if want_pb("PB-gocheck"):
        record(G.probe_pb(scratch, binary, s_root))
    if want_pb("PB-diff"):
        record(probe_pb_diff())
    if want_pb("PB-lint"):
        record(probe_lint())
    if args.spike_a and want_pb("PB-spike-a"):
        record(run_spike_a(scratch, out))
    if args.pytest and want_pb("PB-pytest"):
        record(run_pytest(scratch))
    if want("PW") and not args.no_docker:
        snap2 = G.tree_digest(s_root)
        record(
            result(
                "PW-final",
                "tree listing + sha256 of every S file: after the build vs the end of the stage",
                {"changed": 0, "new": 0},
                {
                    "changed": sum(1 for k in snap0 if snap2.get(k) != snap0[k]),
                    "new": len(set(snap2) - set(snap0)),
                },
                all(snap2.get(k) == v for k, v in snap0.items()) and set(snap2) == set(snap0),
            )
        )
    final_left = wf_containers()
    record(
        result(
            "LEAK",
            "lattice-wf- containers after the stage",
            0,
            len(final_left),
            not final_left,
            names=final_left,
        )
    )
    ran = sorted({r["id"] for r in results})
    for pid in (
        "PE",
        "PS",
        "PS2",
        "PK",
        "PF",
        "PR",
        "PG",
        "PM",
        "PV",
        "PD",
        "PW",
        "PT",
        "PT-sigint",
        "PB-extract",
        "PB-gocheck",
        "PB-diff",
        "PB-lint",
        "PB-spike-a",
        "PB-pytest",
        "PX",
    ):
        if pid not in ran and pid not in not_run:
            not_run.append(pid)
    doc_out = {
        "streak_ref": args.streak_ref,
        "streak_ref2": args.streak_ref2,
        "users": users,
        "static_runs": args.static_runs,
        "runs": args.runs,
        "ran": ran,
        "not_run": sorted(set(not_run)),
        "results": results,
    }
    (out / "probes.json").write_text(json.dumps(doc_out, indent=2, default=str) + "\n")
    lines = ["# Workflow layer probes (Streak stage)", ""]
    for r in results:
        verdict = {True: "PASS", False: "FAIL", None: "REPORT"}[r["pass"]]
        lines.append(
            f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed "
            f"{json.dumps(r['observed'], default=str)[:400]}"
        )
    for pid in sorted(set(not_run)):
        lines.append(f"- NOT RUN {pid}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    bad = [r for r in results if r["pass"] is False]
    return 0 if not bad else 1


# ---- the held-out stage (run by the orchestrator, once) -----------------------------------


def load_spec(spec_ref: str) -> Any:
    """``DIR:MODULE:CALL`` -> the Spec (``MODULE.CALL()``, or the attribute ``CALL``)."""
    d, _, rest = spec_ref.partition(":")
    mod, _, call = rest.partition(":")
    sys.path.insert(0, d)
    try:
        for name in [m for m in sys.modules if m == mod.split(".")[0]]:
            sys.modules.pop(name, None)
        m = importlib.import_module(mod)
        obj = getattr(m, call)
        return obj() if callable(obj) else obj
    finally:
        sys.path.remove(d)


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def spec_truth(spec: Any) -> dict[str, Any]:
    """From a spec: the path-parameter slots, the multi-route skeletons, the machines and the
    users, normalised as the pre-registration's section 1 (ids -> {}, repeats collapsed)."""
    from lattice.spec.model import Step  # noqa: PLC0415

    def skel(p: str) -> str:
        p = p.split("?")[0]
        p = UUID_RE.sub("{}", p)
        return re.sub(r"/\d+(?=/|$)", "/{}", p)

    slots: set[tuple[str, int]] = set()
    skels: dict[tuple[str, ...], int] = {}
    for sc in spec.scenarios:
        ks: list[str] = []
        for st in sc.steps:
            if not isinstance(st, Step):
                continue
            k = f"{st.method} {skel(st.path)}"
            idx = 0
            for seg in k.split(" ", 1)[1].split("/"):
                if seg == "{}":
                    slots.add((k, idx))
                    idx += 1
            if k.endswith("/test/clock"):
                continue
            if ks and ks[-1] == k:
                continue
            ks.append(k)
        if len(ks) >= 2:
            skels[tuple(ks)] = skels.get(tuple(ks), 0) + 1
    machines = [
        {
            "table": m.table,
            "column": m.column,
            "states": sorted(m.states()),
            "edges": sorted(m.edges),
            "initial": sorted(m.initial),
        }
        for m in spec.machines
    ]
    return {
        "slots": sorted(slots),
        "skeletons": sorted(skels),
        "machines": machines,
        "users": list(spec.users),
        "services": [s.name for s in spec.services],
    }


def score_held_out(doc: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """One app's workflows.json against the spec's truth. Report-only."""
    got_slots = set()
    for e in doc["edges"]:
        if e["param_source"] == "path":
            got_slots.add(slot_of(e["consumer"], e["param"]))
    want_slots = {tuple(s) for s in truth["slots"]}
    skels = [tuple(k) for k in truth["skeletons"]]
    strict = sum(1 for k in skels if flow_matches(doc, k)[0])
    lenient = sum(1 for k in skels if flow_matches(doc, k)[1])
    strict_v = sum(1 for k in skels if flow_matches(doc, k, True)[0])
    lenient_v = sum(1 for k in skels if flow_matches(doc, k, True)[1])
    machines = []
    for tm in truth["machines"]:
        ms = [
            m
            for m in doc["machines"]
            if tm["table"].lower() in (m["model"].lower(), m["table"].lower())
            and m["column"] == tm["column"]
        ]
        if not ms:
            machines.append({"table": tm["table"], "found": False})
            continue
        m = ms[0]
        want_edges = {tuple(e) for e in tm["edges"]}
        static_edges = {(e["from"], e["to"]) for e in m["edges"]}
        observed_edges = {(e["from"], e["to"]) for e in m["edges"] if e["observed"]}
        machines.append(
            {
                "table": tm["table"],
                "found": True,
                "states": f"{len(set(m['states']) & set(tm['states']))}/{len(tm['states'])}",
                "static_edges": f"{len(static_edges & want_edges)}/{len(want_edges)}",
                "observed_edges": f"{len(observed_edges & want_edges)}/{len(want_edges)}",
                "initial_exact": sorted(m["initial"]) == tm["initial"],
                "status": m["status"],
            }
        )
    n = len(doc["flows"])
    return {
        "slot_recall": f"{len(want_slots & got_slots)}/{len(want_slots)}",
        "slot_recall_value": round(len(want_slots & got_slots) / len(want_slots), 3)
        if want_slots
        else None,
        "structural_lenient": f"{lenient}/{len(skels)}",
        "structural_strict": f"{strict}/{len(skels)}",
        "structural_lenient_verified": f"{lenient_v}/{len(skels)}",
        "structural_strict_verified": f"{strict_v}/{len(skels)}",
        "machines": machines,
        "flows": n,
        "summary": doc["summary"],
        "verified_fraction": round(doc["summary"]["verified"] / n, 3) if n else None,
        "mode": doc["run"]["mode"],
    }


def run_held_out(args: argparse.Namespace) -> int:
    """``--app NAME=DIR@REF`` (``--build NAME=CMD``) ... ``--spec DIR:MODULE:CALL``: freeze the
    hashes, hash the scorer, archive each app into scratch, run the layer once, score it."""
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted((REPO / "lattice/gocheck/src").glob("goextract*.go")) + [
        REPO / "lattice/importer/frontends/go_gin.py",
        REPO / "lattice/importer/workflows.py",
        REPO / "lattice/importer/workflow_run.py",
    ]
    freeze = {
        p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    scorer = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "freeze.sha256").write_text(
        "".join(f"{h}  {n}\n" for n, h in freeze.items())
        + f"{scorer}  dogfood/workflow_probes.py (the scorer)\n"
    )
    spec = load_spec(args.spec)
    truth = spec_truth(spec)
    users = args.users.split(",") if args.users else truth["users"]
    builds = dict(b.partition("=")[::2] for b in args.build)
    apps: dict[str, Any] = {}
    for item in args.app:
        name, _, rest = item.partition("=")
        src, _, ref = rest.partition("@")
        dest = scratch / f"h-{name}"
        G.archive(Path(src), ref or "HEAD", dest)
        row: dict[str, Any] = {}
        try:
            if name in builds:
                subprocess.run(
                    builds[name],
                    shell=True,
                    cwd=dest,
                    check=True,
                    capture_output=True,
                    env=dict(os.environ, **GO_ENV),
                )
            has_harness = (dest / "harness.py").is_file() or (dest / "studio.toml").is_file()
            extra = [a for u in users for a in ("--user", u)]
            if not has_harness or args.static:
                extra.append("--static")
            r = run_cli(dest, out / f"h-{name}", scratch, f"h-{name}", extra)
            row.update(
                rc=r["rc"],
                wall_s=round(r["wall"], 1),
                stderr=r["stderr"],
                containers_after=r["containers_after"],
                static=("--static" in extra),
            )
            if r["rc"] != 0:
                row["crashed"] = True
            else:
                d = json.loads(r["bytes"])
                row["score"] = score_held_out(d, truth)
                if row["score"]["summary"]["verified"] and not row["static"]:
                    pr = probe_pr(dest, out / f"h-{name}" / "workflows.json", scratch)
                    row["pr"] = {
                        "failures": pr["observed"].get("failures"),
                        "scenarios": pr["observed"].get("scenarios"),
                    }
        except Exception as err:  # noqa: BLE001 -- a crash is a result
            row.update(crashed=True, error=f"{type(err).__name__}: {err}"[:400])
        apps[name] = row
    doc = {
        "freeze": freeze,
        "scorer_sha256": scorer,
        "truth": {k: truth[k] for k in ("slots", "skeletons", "machines", "users", "services")},
        "apps": apps,
        "crashes": sum(1 for r in apps.values() if r.get("crashed")),
        "containers_left": wf_containers(),
    }
    (out / "held-out.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n"
    )
    print(json.dumps(doc, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--streak-ref", default="a0679e9")
    ap.add_argument("--streak-ref2", default="1d00ede")
    ap.add_argument("--users", default="ann,ben")
    ap.add_argument("--static-runs", type=int, default=10)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--scratch")
    ap.add_argument("--out")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-docker", action="store_true")
    ap.add_argument("--spike-a", action="store_true", help="run the Spike A probes too")
    ap.add_argument("--pytest", action="store_true", help="run pytest tests/importer tests/gocheck")
    ap.add_argument("--held-out", action="store_true")
    ap.add_argument("--app", action="append", default=[], help="NAME=DIR@REF (held out)")
    ap.add_argument("--build", action="append", default=[], help="NAME=SHELL COMMAND (held out)")
    ap.add_argument("--spec", default="", help="DIR:MODULE:CALL of the spec (held out)")
    ap.add_argument("--static", action="store_true", help="held out: plan only")
    ap.add_argument("--replay-worker", action="store_true")
    ap.add_argument("--root")
    ap.add_argument("--doc")
    args = ap.parse_args(argv)
    if args.replay_worker:
        return replay_worker(args)
    if not args.scratch or not args.out:
        ap.error("--scratch and --out are required")
    return run_held_out(args) if args.held_out else run_streak(args)


if __name__ == "__main__":
    raise SystemExit(main())
