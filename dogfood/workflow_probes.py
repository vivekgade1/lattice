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
    if getattr(args, "refs_json", None):
        # round 2: references hard-coded from the project's check.sh text, not discovered
        cfg = json.loads(args.refs_json)
    make_app = _harness_attr(root, cfg["app"])
    build_db = _harness_attr(root, cfg["db"])
    login = _harness_attr(root, cfg["login"]) if "login" in cfg else None
    runtime = _harness_attr(root, cfg["runtime"]) if "runtime" in cfg else None
    services = dict(_harness_attr(root, cfg["services"])) if "services" in cfg else {}
    dialect = getattr(args, "database", None) or "mysql"
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
    server = None
    if dialect == "mysql":
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
            database=dialect,
            database_url=server.url if server is not None else None,
        )
        for fd in report.findings:
            if fd.layer == "D" and fd.rule.startswith("scenario: "):
                failures.setdefault(
                    fd.rule[len("scenario: ") :], json.dumps(fd.details, default=str)[:200]
                )
    finally:
        if server is not None:
            server.stop()
    print(
        json.dumps({"scenarios": len(scenarios), "failures": failures, "n_failures": len(failures)})
    )
    return 0


def probe_pr(
    root: Path,
    doc_path: Path,
    scratch: Path,
    refs: dict[str, str] | None = None,
    database: str = "mysql",
) -> dict[str, Any]:
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
            *(["--refs-json", json.dumps(refs), "--database", database] if refs else []),
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
        got["n_failures"] == 0 and (got["scenarios"] > 0 or refs is not None),
        wall_s=round(wall, 1),
    )


# ---- containers, interrupts ---------------------------------------------------------------


def probe_pt_sigint(
    root: Path,
    scratch: Path,
    extra: Sequence[str],
    *,
    containers: bool = True,
    min_snaps: int = 12,
    label: str = "PT-sigint",
) -> dict[str, Any]:
    """A run killed with SIGINT mid-flow leaves no ``lattice-wf-`` container and no app.
    ``containers=False`` (a SQLite run) waits for ``min_snaps`` snapshots instead of a
    container."""
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
        if not containers or wf_containers():
            seen = True
            sc = out / ".scratch"
            snaps = len(list(sc.glob("snap-*.db"))) if sc.is_dir() else 0
            if snaps >= min_snaps:
                break
        time.sleep(0.05 if not containers else 0.2)
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
        label,
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


# ---- round 2: harness discovery, list rule, dev-set dynamic run ---------------------------
# (docs/results/2026-10-16-workflow-layer-round2-prereg.md, section 3). The ground truth below
# is written from the pre-registration, not from the layer's output.

ROUND1_DATA = "docs/results/data/2026-10-15-workflow-layer"
NEW_RUN_KEYS = ("harness_file", "harness_reason", "list_rule", "database")
FOUR_SOURCES = ("studio.toml", "gate file", "check.sh", "harness.py")
STREAK_REFS = {
    "app": "harness:make_app",
    "db": "harness:build_db",
    "login": "harness:login",
    "runtime": "harness:RUNTIME",
    "services": "harness:SERVICES",
}
J_REFS = {
    "app": "harness:make_app",
    "db": "harness:build_seed_db",
    "login": "spec.seed:login",
    "runtime": "harness:RUNTIME",
    "services": "harness:SERVICES",
}
J_NAMES = {
    "go": "job-scheduler-go",
    "mso": "job-scheduler-go-min-spec-opus",
    "mss": "job-scheduler-go-min-spec-sonnet",
    "base": "job-scheduler-go-baseline",
    "mbo": "job-scheduler-go-min-base-opus",
    "mbs": "job-scheduler-go-min-base-sonnet",
}
J_HARNESS = ("go", "mso", "mss")
J_STATIC = ("base", "mbo", "mbs")
# PH: what discovery must say per app: (source, file, refs, services, database)
PH_APPS: dict[str, dict[str, Any]] = {
    "S": dict(
        source="studio.toml", file="studio.toml", refs=STREAK_REFS, services=["nudger"],
        database="mysql",
    ),
    "S-prime": dict(
        source="check.sh", file="check.sh", refs=STREAK_REFS, services=["nudger"],
        database="mysql",
    ),
    "go": dict(
        source="check.sh", file="check.sh", refs=J_REFS, services=["scheduler", "worker"],
        database="sqlite",
    ),
    "mso": dict(
        source="check.sh", file="check.sh", refs={**J_REFS, "services": None}, services=[],
        database="sqlite",
    ),
    "mss": dict(
        source="check.sh", file="check.sh", refs={**J_REFS, "services": None}, services=[],
        database="sqlite",
    ),
}  # fmt: skip
# the J refs hard-coded from each check.sh text (PR must not use the layer's discovery)
J_PR_REFS = {
    "go": {**J_REFS},
    "mso": {k: v for k, v in J_REFS.items() if k != "services"},
    "mss": {k: v for k, v in J_REFS.items() if k != "services"},
}
# PH fixtures: case -> (found, source, file, database, services, words in the reason)
PH_FIXTURES: dict[str, tuple[bool, str | None, str | None, str | None, list[str], list[str]]] = {
    "streak_form": (True, "check.sh", "check.sh", "mysql", ["scheduler", "worker"], []),
    "exec_continuation": (True, "check.sh", "check.sh", "sqlite", [], []),
    "equals_form": (True, "check.sh", "check.sh", "sqlite", ["scheduler", "worker"], []),
    "runtime_go": (True, "check.sh", "check.sh", "sqlite", [], []),
    "runtime_process": (True, "check.sh", "check.sh", "sqlite", [], []),
    "comments_and_strings": (True, "check.sh", "check.sh", "sqlite", [], []),
    "services_undeclared": (True, "check.sh", "check.sh", "sqlite", [], []),
    "studio_wins": (True, "studio.toml", "studio.toml", "mysql", [], []),
    "gate_file": (True, "check.sh", "ci/gate.sh", "mysql", [], []),
    "no_invocation": (True, "harness.py", "harness.py", None, ["scheduler", "worker"], []),
    "harness_py_only": (True, "harness.py", "harness.py", None, ["scheduler", "worker"], []),
    "postgres": (True, "check.sh", "check.sh", "postgres", [], []),
    "nothing": (False, None, None, None, [], list(FOUR_SOURCES)),
    "dollar_var": (False, None, None, None, [], ["--app", "$APP", "shell variable"]),
    "dollar_database": (False, None, None, None, [], ["--database", "$DB", "shell variable"]),
    "nope_ref": (False, None, None, None, [], ["harness:nope", "AttributeError"]),
    "import_error": (False, None, None, None, [], ["harness:make_app", "ModuleNotFoundError"]),
    "login_path_only": (False, None, None, None, [], ["--login-path", "no --login"]),
    "mint_token_only": (False, None, None, None, [], ["--mint-token", "no --login"]),
    "incomplete_app": (False, None, None, None, [], ["studio.toml", "lacks app and db"]),
    "harness_seed_only": (False, None, None, None, [], ["harness.py", "no build_db"]),
    "disagree": (False, None, None, None, [], ["ambiguous", "--db"]),
    "missing_db": (False, None, None, None, [], ["no --db"]),
    "services_not_mapping": (False, None, None, None, [], ["harness:RUNTIME", "not a mapping"]),
    "bad_toml": (False, None, None, None, [], ["studio.toml", "TOMLDecodeError"]),
}  # fmt: skip
# PJ predictions (registered, report-only): flows, verified, failed, slot recall, skeletons,
# JobRun states / static edges, observed edges, sweeps
PJ_PRED = {
    "go": dict(flows="80 (75-100)", verified="0.20 (0.05-0.40)", failed="0.70 (0.45-0.90)",
               slots="16/24", skeletons="lenient 0 (0-2) / strict 0 (0-1)",
               jobrun="5/5 states, 3/5 static, initial exact", observed="0/5 (0-1); static",
               sweeps="none run (0-2)"),
    "mso": dict(flows="74 (70-95)", verified="0.50 (0.15-0.80)", failed="0.40 (0.10-0.80)",
                slots="21/24", skeletons="lenient 3 (1-6) / strict 0 (0-2)",
                jobrun="5/5 states, 3/5 static, initial exact",
                observed="1/5 queued->cancelled (0-1); observed", sweeps="none"),
    "mss": dict(flows="15 (15-25)", verified="0.50 (0.15-0.85)", failed="0.40 (0.10-0.80)",
                slots="21/24", skeletons="lenient 3 (1-6) / strict 0 (0-2)",
                jobrun="3/5 states, 1/5 static, initial exact",
                observed="1/5 (0-1); observed", sweeps="none"),
}  # fmt: skip


def discover_worker(args: argparse.Namespace) -> int:
    """A separate process per app: the layer's discovery on ``--root``, as plain data."""
    sys.dont_write_bytecode = True
    from lattice.importer.check_invocation import choose_database, discover  # noqa: PLC0415

    d = discover(Path(args.root))
    out = d.describe()
    chosen, why = choose_database(d.database, "auto", None) if d.found else (None, None)
    out["database_choice"] = [chosen, why]
    print(json.dumps(out, sort_keys=True))
    return 0


def run_discover(root: Path, scratch: Path, label: str) -> dict[str, Any]:
    tmp = fresh_tmp(scratch, f"disc-{label}")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--discover-worker", "--root", str(root)],
        capture_output=True,
        text=True,
        env=cli_env(tmp),
        cwd=REPO,
        check=False,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-400:]}
    return json.loads(proc.stdout.strip().splitlines()[-1])


def reason_clean(reason: str | None, root: Path) -> bool:
    return reason is None or ("Traceback" not in reason and str(root) not in reason)


def probe_ph(
    scratch: Path,
    roots: dict[str, Path],
    static_roots: dict[str, Path],
    cli_runs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Discovery per app and per fixture, exact against the pre-registered table."""
    rows: dict[str, Any] = {}
    bad: list[str] = []
    for name, want in PH_APPS.items():
        got = run_discover(roots[name], scratch, name)
        rows[name] = got
        ok = (
            got.get("source") == want["source"]
            and got.get("file") == want["file"]
            and got.get("reason") is None
            and {k: v for k, v in (got.get("refs") or {}).items() if v is not None}
            == {k: v for k, v in want["refs"].items() if v is not None}
            and got.get("services") == want["services"]
            and got.get("database") == want["database"]
            and got.get("resolved") == ["app", "db", "login", "runtime"]
        )
        if not ok:
            bad.append(name)
    for name, root in static_roots.items():
        got = run_discover(root, scratch, name)
        rows[name] = got
        reason = got.get("reason") or ""
        ok = (
            got.get("source") is None
            and got.get("file") is None
            and all(w in reason for w in FOUR_SOURCES)
            and reason_clean(reason, root)
        )
        run = cli_runs.get(name) or {}
        reason_cli = run.get("harness_reason") or ""
        rows[name]["cli_run"] = run
        if not (
            run.get("mode") == "static"
            and run.get("harness") is None
            and run.get("harness_file") is None
            and run.get("database") is None
            and all(w in reason_cli for w in FOUR_SOURCES)
            and reason_clean(reason_cli, root)
        ):
            ok = False
        if not ok:
            bad.append(name)
    fx_root = scratch / "ph-fixtures"
    shutil.rmtree(fx_root, ignore_errors=True)
    shutil.copytree(REPO / "tests/importer/fixtures/check_invocation", fx_root)
    fixtures: dict[str, Any] = {}
    for case, (found, source, file, db, services, words) in PH_FIXTURES.items():
        root = fx_root / case
        got = run_discover(root, scratch, f"fx-{case}")
        fixtures[case] = got
        reason = got.get("reason")
        ok = (
            (got.get("reason") is None) == found
            and got.get("source") == source
            and got.get("file") == file
            and got.get("database") == db
            and got.get("services") == services
            and all(w in (reason or "") for w in words)
            and reason_clean(reason, root)
        )
        if case == "postgres":
            choice = got.get("database_choice") or [None, None]
            ok = ok and choice[0] is None and "postgres" in (choice[1] or "")
        if not ok:
            bad.append(f"fixture:{case}")
    return result(
        "PH",
        "discovery per app and per fixture: source, file, resolved refs, services, database, "
        "reason",
        "all exact",
        {"apps": rows, "fixtures": fixtures, "mismatch": bad},
        not bad,
    )


def strip_new_keys(doc: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(doc))
    for k in NEW_RUN_KEYS:
        out["run"].pop(k, None)
    return out


def canonical(doc: dict[str, Any]) -> str:
    from lattice.importer import workflows as W  # noqa: PLC0415

    return W.to_json(doc)


def unbound_sent(doc: dict[str, Any]) -> list[str]:
    """Steps that were sent (attempts > 0) while a path parameter of their route has no path
    binding: the layer filled it with a guess."""
    bad: list[str] = []
    for f in doc["flows"]:
        for st in f["steps"]:
            if st["kind"] != "request" or not st["attempts"]:
                continue
            params = re.findall(r"\{([^}]*)\}", st["route"].partition(" ")[2])
            bound = {b["param"] for b in st["bindings"] if b["source"] == "path"}
            bad += [f"{f['id']} {st['route']} {p}" for p in params if p not in bound]
    return bad


def unbound_reasons(doc: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in doc["flows"]:
        m = re.search(r"unbound path param (\w+): no producer", f.get("reason") or "")
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return dict(sorted(counts.items()))


def unbound_planned(doc: dict[str, Any]) -> dict[str, int]:
    """Static plan: steps of the flows whose route has a path parameter with no path binding."""
    n = 0
    flows = 0
    for f in doc["flows"]:
        hit = False
        for st in f["steps"]:
            if st["kind"] != "request":
                continue
            params = re.findall(r"\{([^}]*)\}", st["route"].partition(" ")[2])
            bound = {b["param"] for b in st["bindings"] if b["source"] == "path"}
            n += sum(1 for p in params if p not in bound)
            hit = hit or any(p not in bound for p in params)
        flows += hit
    return {"steps_with_unbound_param": n, "flows": flows}


def listed(doc: dict[str, Any]) -> dict[str, Any]:
    """Which GET routes served as id lists, and how many bindings came from a list."""
    routes: dict[str, int] = {}
    for f in doc["flows"]:
        for st in f["steps"]:
            if st["kind"] == "request" and st["inserted"] == "id_source":
                routes[st["route"]] = routes.get(st["route"], 0) + 1
    return dict(sorted(routes.items()))


def probe_pl(doc: dict[str, Any]) -> dict[str, Any]:
    n = len(doc["flows"])
    c = {"verified": 0, "unverified": 0, "failed": 0}
    for f in doc["flows"]:
        c[f["status"]] += 1
    routes = listed(doc)
    home = routes.get("GET /v1/home", 0)
    frac = round(c["verified"] / n, 3) if n else 0.0
    return result(
        "PL",
        "list rule on S, default (specific): flows, statuses, the nudge list route, no "
        "/v1/home id binding",
        {
            "flows": "69 (65-75)",
            "verified": "58 (>= 0.80)",
            "unverified": 5,
            "failed": "6 (<= 6)",
            "list_routes": ["GET /v1/nudges"],
            "home_bindings": 0,
        },
        {"flows": n, **c, "verified_fraction": frac, "list_routes": routes, "home_bindings": home},
        65 <= n <= 75 and frac >= 0.80 and c["failed"] <= 6 and home == 0,
        run=doc["run"],
        failed_reasons={f["id"]: f["reason"] for f in doc["flows"] if f["status"] == "failed"},
    )


def probe_pl1(doc: dict[str, Any], static_doc: dict[str, Any] | None) -> dict[str, Any]:
    """--list-rule first on S equals the committed round-1 workflows.json once the four new
    run keys are dropped, byte for byte."""
    old = (REPO / ROUND1_DATA / "workflows.json").read_text()
    new = canonical(strip_new_keys(doc))
    static_ok = None
    if static_doc is not None:
        old_static = (REPO / ROUND1_DATA / "static-workflows.json").read_text()
        static_ok = canonical(strip_new_keys(static_doc)) == old_static
    return result(
        "PL1",
        "--list-rule first on S equals the round-1 workflows.json after dropping the 4 new run "
        "keys (68 / 59 / 1 / 8)",
        "byte-identical",
        {
            "byte_identical": new == old,
            "summary": doc["summary"],
            "run": doc["run"],
            "static_equals_round1_static": static_ok,
        },
        new == old,
    )


def probe_pu(docs: dict[str, dict[str, Any]], static_go: dict[str, Any] | None) -> dict[str, Any]:
    sent = {k: unbound_sent(d) for k, d in docs.items()}
    total = sum(len(v) for v in sent.values())
    return result(
        "PU",
        "sent requests with an unbound path parameter (S, S', J dynamic)",
        {"sent_unbound": 0, "go_failed_reasons": "name job_id / user_id"},
        {
            "sent_unbound": {k: len(v) for k, v in sent.items()},
            "examples": {k: v[:3] for k, v in sent.items() if v},
            "failed_reasons_by_param": {k: unbound_reasons(d) for k, d in docs.items()},
            "planned_unbound_in_static_go": unbound_planned(static_go) if static_go else None,
        },
        total == 0,
    )


def strip_run_harness(doc: dict[str, Any]) -> str:
    d = json.loads(json.dumps(doc))
    for k in ("harness", "harness_file"):
        d["run"].pop(k, None)
    return canonical(d)


def second_dyn(args: argparse.Namespace) -> bool:
    return args.runs > 0 and not args.no_docker


def listing(root: Path) -> dict[str, str]:
    """A cheap, read-only listing (path -> size:mtime) of a checkout, ``.git`` excluded."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for f in filenames:
            p = Path(dirpath) / f
            try:
                st = p.lstat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = f"{st.st_size}:{st.st_mtime_ns}"
    return out


def porcelain(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(proc.stdout.splitlines())


def target_state(roots: dict[str, Path]) -> dict[str, Any]:
    return {k: {"porcelain": porcelain(v), "listing": listing(v)} for k, v in roots.items()}


def app_processes(scratch: Path) -> list[str]:
    """Leftover app binaries started from a scratch archive (``streak-api`` / ``bin/server``)."""
    proc = subprocess.run(
        ["pgrep", "-f", f"{re.escape(str(scratch))}/.*/bin/(server|streak-api)"],
        capture_output=True,
        text=True,
        check=False,
    )
    me = str(os.getpid())
    return [p for p in proc.stdout.split() if p != me]


def scorer_unchanged() -> dict[str, Any]:
    """`score_held_out` and `spec_truth` have byte-identical source to the committed ones."""
    import ast  # noqa: PLC0415

    now = Path(__file__).read_text()
    old = subprocess.run(
        ["git", "-C", str(REPO), "show", "HEAD:dogfood/workflow_probes.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    def seg(src: str, name: str) -> str | None:
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(src, node)
        return None

    same = {n: seg(now, n) == seg(old, n) and seg(now, n) is not None for n in
            ("score_held_out", "spec_truth")}  # fmt: skip
    return same


R2_OWNED_EXACT = {
    "lattice/importer/check_invocation.py",
    "tests/importer/test_check_invocation.py",
    "lattice/importer/workflow_run.py",
    "tests/importer/test_workflows.py",
    "dogfood/workflow_probes.py",
    "dogfood/workflow_mutants.py",
    "docs/results/2026-10-16-workflow-layer-round2-results.md",
}
R2_OWNED_PREFIX = (
    "tests/importer/fixtures/check_invocation/",
    "docs/results/data/2026-10-16-workflow-layer-round2/",
)
R2_FROZEN = [
    *FROZEN,
    "lattice/gocheck/src",
    "lattice/importer/frontends/go_gin.py",
    "lattice/importer/cli.py",
    "lattice/importer/workflows.py",
]


def probe_pb_diff_r2() -> dict[str, Any]:
    changed = sorted(
        {x.split("\t")[-1] for x in git("diff", "HEAD", "--name-status")}
        | set(git("ls-files", "--others", "--exclude-standard"))
    )
    foreign = [c for c in changed if c not in R2_OWNED_EXACT and not c.startswith(R2_OWNED_PREFIX)]
    frozen = git("diff", "HEAD", "--stat", "--", *R2_FROZEN)
    scorer = scorer_unchanged()
    return result(
        "PB-diff",
        "changes against HEAD: owned files only (section 7); frozen modules, gocheck, goldens, "
        "round-1 data untouched; score_held_out and spec_truth byte-identical",
        {"foreign": [], "frozen_diff": [], "scorer_unchanged": True},
        {"foreign": foreign, "frozen_diff": frozen, "scorer_unchanged": scorer, "changed": changed},
        not foreign and not frozen and all(scorer.values()),
    )


def probe_lint_r2() -> dict[str, Any]:
    env = dict(os.environ, **GO_ENV)
    src = REPO / "lattice/gocheck/src"
    vet = subprocess.run(["go", "vet", "./..."], cwd=src, env=env, capture_output=True, text=True)
    fmt = subprocess.run(["gofmt", "-l", "."], cwd=src, capture_output=True, text=True)
    pys = sorted(
        {
            c
            for c in git("diff", "HEAD", "--name-only")
            + git("ls-files", "--others", "--exclude-standard")
            if c.endswith(".py") and (REPO / c).is_file()
        }
    )
    ruff = subprocess.run(
        [sys.executable, "-m", "ruff", "check", *pys], cwd=REPO, capture_output=True, text=True
    )
    fmt_py = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", *pys],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return result(
        "PB-lint",
        "go vet, gofmt -l, ruff check and ruff format --check on every changed .py",
        "clean",
        {
            "go_vet_rc": vet.returncode,
            "gofmt": fmt.stdout.split(),
            "ruff_rc": ruff.returncode,
            "ruff_format_rc": fmt_py.returncode,
            "ruff_out": (ruff.stdout + fmt_py.stdout)[-400:],
            "files": pys,
        },
        vet.returncode == 0
        and not fmt.stdout.strip()
        and ruff.returncode == 0
        and fmt_py.returncode == 0,
    )


def parse_app_args(items: Sequence[str]) -> dict[str, tuple[Path, str]]:
    out: dict[str, tuple[Path, str]] = {}
    for item in items:
        name, _, rest = item.partition("=")
        src, _, ref = rest.partition("@")
        out[name] = (Path(src), ref or "HEAD")
    return out


def series(root: Path, out: Path, scratch: Path, label: str, extra: Sequence[str], n: int):
    """``n`` CLI runs: blobs, walls, containers after each, the first rc != 0 stops it."""
    blobs: list[bytes] = []
    walls: list[float] = []
    left: list[list[str]] = []
    stderr = ""
    rc = 0
    for i in range(n):
        r = run_cli(root, out, scratch, f"{label}-{i}", extra)
        left.append(r["containers_after"])
        walls.append(r["wall"])
        if r["rc"] != 0:
            rc, stderr = r["rc"], r["stderr"]
            break
        blobs.append(r["bytes"])
    return {"blobs": blobs, "walls": walls, "containers": left, "rc": rc, "stderr": stderr}


def run_round2(args: argparse.Namespace) -> int:
    scratch, out = Path(args.scratch).resolve(), Path(args.out).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    not_run: list[tuple[str, str]] = []
    results: list[dict[str, Any]] = []
    users = [u for u in args.users.split(",") if u]
    user_args = [a for u in users for a in ("--user", u)]
    j_users = [u for u in args.j_users.split(",") if u]
    j_user_args = [a for u in j_users for a in ("--user", u)]
    t_stage = time.perf_counter()

    def want(*ids: str) -> bool:
        return only is None or any(i in only for i in ids)

    def record(r: dict[str, Any] | list[dict[str, Any]]) -> None:
        for item in r if isinstance(r, list) else [r]:
            results.append(item)
            print(
                f"{item['id']}: {item['pass']} {json.dumps(item['observed'], default=str)[:220]}",
                flush=True,
            )

    def skip(pid: str, why: str) -> None:
        not_run.append((pid, why))
        print(f"{pid}: NOT RUN ({why})", flush=True)

    # ---- the targets that must stay untouched (listing + porcelain, before)
    apps = parse_app_args(args.app)
    target_dirs = {"streak": STREAK, **{n: p for n, (p, _) in apps.items()}}
    spec_dir = None
    if args.spec:
        spec_dir = Path(args.spec.partition(":")[0].partition("@")[0])
        target_dirs["job-scheduler"] = spec_dir
    targets_before = target_state(target_dirs) if want("PW") else None

    # ---- archives
    s_root, s_prime, s_second = scratch / "S", scratch / "S-prime", scratch / "S2" / "deeper" / "S"
    s4_root = scratch / "S4"
    G.archive(STREAK, args.streak_ref, s_root)
    G.archive(STREAK, args.streak_ref, s_prime)
    (s_prime / "studio.toml").unlink()
    s_second.parent.mkdir(parents=True, exist_ok=True)
    G.archive(STREAK, args.streak_ref, s_second)
    G.archive(STREAK, args.streak_ref2, s4_root)
    for r in (s_root, s_prime):
        G.go_build(r)
    jroots: dict[str, Path] = {}
    for name, (src, ref) in apps.items():
        dest = scratch / f"j-{name}"
        G.archive(src, ref, dest)
        jroots[name] = dest
    builds = dict(b.partition("=")[::2] for b in args.build)
    for name, cmd in builds.items():
        subprocess.run(
            cmd,
            shell=True,
            cwd=jroots[name],
            check=True,
            capture_output=True,
            env=dict(os.environ, **GO_ENV),
        )
    digest0 = {
        "S": G.tree_digest(s_root),
        **{n: G.tree_digest(p) for n, p in jroots.items()},
    }
    binary = G._gocheck_binary()
    extract = json.loads(G.extract_bytes(binary, s_root)[0])

    # ---- freeze: the hashes of the changed files and the scorer, before the J stage
    freeze_files = [
        "lattice/importer/check_invocation.py",
        "lattice/importer/workflow_run.py",
        "lattice/importer/workflows.py",
        "lattice/importer/frontends/go_gin.py",
        "lattice/importer/cli.py",
        "dogfood/workflow_probes.py",
    ]
    (out / "freeze.sha256").write_text(
        "".join(
            f"{hashlib.sha256((REPO / f).read_bytes()).hexdigest()}  {f}\n" for f in freeze_files
        )
        + "".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(REPO).as_posix()}\n"
            for p in sorted((REPO / "lattice/gocheck/src").glob("goextract*.go"))
        )
    )

    # ---- PH
    if want("PH"):
        static_roots = {n: jroots[n] for n in J_STATIC if n in jroots}
        roots_ph = {
            "S": s_root,
            "S-prime": s_prime,
            **{n: jroots[n] for n in J_HARNESS if n in jroots},
        }
        if len(roots_ph) == 5:
            cli_runs: dict[str, dict[str, Any]] = {}
            for n, root in static_roots.items():
                c = run_cli(root, out / f"ph-{n}", scratch, f"ph-{n}", j_user_args)
                cli_runs[n] = json.loads(c["bytes"])["run"] if c["rc"] == 0 else {}
            r = probe_ph(scratch, roots_ph, static_roots, cli_runs)
            (out / "ph.json").write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
            record(r)
        else:
            skip("PH", "the J apps were not all given")

    # ---- S static (PD, PL1's static half)
    static_blobs: list[bytes] = []
    static_walls: list[float] = []
    static_doc: dict[str, Any] | None = None
    if want("PD", "PE", "PX", "PL1"):
        ser = series(s_root, out / "static-run", scratch, "static", ["--static", *user_args],
                     args.static_runs)  # fmt: skip
        if ser["rc"] != 0:
            record(result("PD", "static run", "exit 0", ser["stderr"], False))
            return 1
        static_blobs, static_walls = ser["blobs"], ser["walls"]
        r2 = run_cli(s_second, out / "static-run2", scratch, "static-second",
                     ["--static", *user_args])  # fmt: skip
        static_blobs.append(r2["bytes"])
        static_doc = json.loads(static_blobs[0])
        (out / "static-workflows.json").write_bytes(static_blobs[0])

    # ---- S dynamic (default list rule): N runs; S' once; --list-rule first once
    docker = not args.no_docker and args.runs > 0
    dyn_blobs: list[bytes] = []
    dyn_walls: list[float] = []
    leaked: list[list[str]] = []
    dyn_doc: dict[str, Any] | None = None
    need_s_dyn = want("PL", "PE", "PS", "PK", "PF", "PG", "PM", "PV", "PR", "PU", "PD", "PX", "PT")
    if need_s_dyn and docker:
        ser = series(s_root, out / "dyn-run", scratch, "dyn", user_args, args.runs)
        dyn_blobs, dyn_walls, leaked = ser["blobs"], ser["walls"], ser["containers"]
        if ser["rc"] != 0:
            record(result("PD", "dynamic run", "exit 0", ser["stderr"], False))
            return 1
        dyn_doc = json.loads(dyn_blobs[0])
        (out / "workflows.json").write_bytes(dyn_blobs[0])
    sp_doc: dict[str, Any] | None = None
    if want("PL", "PH", "PU") and docker:
        r = run_cli(s_prime, out / "sp-run", scratch, "sprime", user_args)
        leaked.append(r["containers_after"])
        if r["rc"] == 0:
            sp_doc = json.loads(r["bytes"])
            (out / "workflows-S-prime.json").write_bytes(r["bytes"])
    first_doc: dict[str, Any] | None = None
    if want("PL1") and docker:
        r = run_cli(
            s_root, out / "first-run", scratch, "first", ["--list-rule", "first", *user_args]
        )
        leaked.append(r["containers_after"])
        if r["rc"] == 0:
            first_doc = json.loads(r["bytes"])
            (out / "workflows-S-first.json").write_bytes(r["bytes"])
        else:
            record(result("PL1", "--list-rule first", "exit 0", r["stderr"], False))
    doc = dyn_doc or static_doc
    if dyn_doc is not None and want("PL"):
        pl = probe_pl(dyn_doc)
        if sp_doc is not None:
            pl["observed"]["s_prime"] = {
                "run": sp_doc["run"],
                "flows_and_statuses_equal_S": (
                    [(f["id"], f["status"]) for f in sp_doc["flows"]]
                    == [(f["id"], f["status"]) for f in dyn_doc["flows"]]
                ),
                "equal_after_dropping_harness_keys": strip_run_harness(sp_doc)
                == strip_run_harness(dyn_doc),
            }
            pl["pass"] = (
                bool(pl["pass"]) and pl["observed"]["s_prime"]["flows_and_statuses_equal_S"]
            )
        record(pl)
    elif want("PL"):
        skip("PL", "no S dynamic run (docker off or --runs 0)")
    if first_doc is not None and want("PL1"):
        record(probe_pl1(first_doc, static_doc))
    elif want("PL1"):
        skip("PL1", "no --list-rule first dynamic run")
    if static_doc is not None and want("PE") and dyn_doc is not None:
        r = probe_pe(static_doc, dyn_doc)
        record(r)
    if doc is not None and want("PS"):
        record(probe_ps(doc))
    if doc is not None and want("PK"):
        record(probe_pk(doc))
    if doc is not None and want("PF"):
        r = probe_pf(doc)
        r["predicted"] = {"count": "69 (65-75)", "verified": 0.841, "unverified": 0.072,
                          "failed": 0.087}  # fmt: skip
        record(r)
    if doc is not None and want("PG"):
        record(probe_pg(s_root, doc, extract))
    if doc is not None and want("PM"):
        record(probe_pm(s_root, doc))
    if doc is not None and want("PV"):
        record(probe_pv(doc))

    # ---- J: dynamic runs per harness app, static for the others
    j_docs: dict[str, dict[str, Any]] = {}
    j_blobs: dict[str, list[bytes]] = {}
    j_walls: dict[str, list[float]] = {}
    j_rc: dict[str, int] = {}
    j_stderr: dict[str, str] = {}
    truth = None
    if args.spec and want("PJ", "PJs", "PU"):
        sref, _, rest = args.spec.partition(":")
        sdir, _, sver = sref.partition("@")
        spec_scratch = scratch / "spec"
        G.archive(Path(sdir), sver or "HEAD", spec_scratch)
        saved_path = list(sys.path)
        truth = spec_truth(load_spec(f"{spec_scratch}:{rest}"))
        sys.path[:] = saved_path
        for m in [m for m in sys.modules if m == "spec" or m.startswith("spec.")]:
            sys.modules.pop(m, None)
    for name in J_HARNESS:
        if name not in jroots or not want("PJ", "PU", "PD", "PR"):
            continue
        ser = series(jroots[name], out / f"j-{name}-run", scratch, f"j-{name}", j_user_args,
                     args.j_runs)  # fmt: skip
        j_rc[name], j_stderr[name] = ser["rc"], ser["stderr"]
        j_blobs[name], j_walls[name] = ser["blobs"], ser["walls"]
        leaked.append([c for lst in ser["containers"] for c in lst])
        if ser["blobs"]:
            j_docs[name] = json.loads(ser["blobs"][0])
            (out / f"workflows-j-{name}.json").write_bytes(ser["blobs"][0])
    # J-my: -go on MySQL, one run, report only
    jmy_doc = None
    jmy_wall = None
    jmy_left: list[str] = []
    if args.j_mysql and args.j_mysql in jroots and docker and want("PJ"):
        r = run_cli(jroots[args.j_mysql], out / "jmy-run", scratch, "jmy",
                    ["--database", "mysql", *j_user_args])  # fmt: skip
        jmy_wall, jmy_left = r["wall"], r["containers_after"]
        if r["rc"] == 0:
            jmy_doc = json.loads(r["bytes"])
            (out / f"workflows-j-{args.j_mysql}-mysql.json").write_bytes(r["bytes"])
        else:
            jmy_doc = {"error": r["stderr"]}
    # harness-less apps: discovery must make them static, identical to round 1
    pjs_rows: dict[str, Any] = {}
    if want("PJs"):
        for name in J_STATIC:
            if name not in jroots:
                continue
            r = run_cli(jroots[name], out / f"j-{name}-run", scratch, f"js-{name}", j_user_args)
            old_p = REPO / ROUND1_DATA / "held-out" / f"h-{J_NAMES[name]}.workflows.json"
            same = None
            run = None
            if r["rc"] == 0:
                d = json.loads(r["bytes"])
                run = d["run"]
                same = canonical(strip_new_keys(d)) == old_p.read_text()
                (out / f"workflows-j-{name}.json").write_bytes(r["bytes"])
                j_docs[name] = d
            pjs_rows[name] = {"identical_to_round1": same, "run": run, "rc": r["rc"],
                              "containers": r["containers_after"]}  # fmt: skip
        record(
            result(
                "PJs",
                "the 3 harness-less apps: static, workflows.json = round 1 minus the new run keys",
                "3/3 identical",
                pjs_rows,
                len(pjs_rows) == 3
                and all(
                    v["identical_to_round1"] is True
                    and v["run"]["mode"] == "static"
                    and v["run"]["harness"] is None
                    for v in pjs_rows.values()
                ),
            )
        )

    if want("PJ") and truth is not None:
        rows: dict[str, Any] = {}
        for name in J_HARNESS:
            if name not in jroots:
                continue
            d = j_docs.get(name)
            row: dict[str, Any] = {"predicted": PJ_PRED[name], "rc": j_rc.get(name)}
            if d is None:
                row.update(crashed=True, stderr=j_stderr.get(name, ""))
                rows[name] = row
                continue
            sc = score_held_out(d, truth)
            statuses: dict[str, int] = {}
            first_fail: dict[str, int] = {}
            for f in d["flows"]:
                if f["status"] != "verified":
                    key = re.sub(r"\d+", "N", f["reason"].split(":")[0])[:90]
                    first_fail[key] = first_fail.get(key, 0) + 1
                for st in f["steps"]:
                    if st["kind"] == "request" and st["refusal"]:
                        k = str(st["refusal"]["status"])
                        statuses[k] = statuses.get(k, 0) + 1
            list_edges = [e for e in d["edges"] if e["id_source"] == "list"]
            walls = j_walls[name]
            row.update(
                mode=d["run"]["mode"],
                harness=d["run"]["harness"],
                harness_file=d["run"]["harness_file"],
                harness_reason=d["run"]["harness_reason"],
                database=d["run"]["database"],
                list_rule=d["run"]["list_rule"],
                flows=sc["flows"],
                summary=d["summary"],
                verified_fraction=sc["verified_fraction"],
                failed_fraction=round(d["summary"]["failed"] / sc["flows"], 3)
                if sc["flows"]
                else None,
                slot_recall=sc["slot_recall"],
                skeletons_lenient=sc["structural_lenient"],
                skeletons_strict=sc["structural_strict"],
                skeletons_lenient_verified=sc["structural_lenient_verified"],
                skeletons_strict_verified=sc["structural_strict_verified"],
                machines=sc["machines"],
                services_swept=d["run"]["services_swept"],
                list_id_sources=len(list_edges),
                refusal_statuses=dict(sorted(statuses.items())),
                non_verified_reasons=dict(sorted(first_fail.items())),
                wall_s=[round(w, 1) for w in walls],
                wall_median_s=round(sorted(walls)[len(walls) // 2], 1) if walls else None,
                runs=len(walls),
                crashed=False,
            )
            rows[name] = row
        gate = (
            all(
                r.get("mode") == "observed" and r.get("harness") == "check.sh"
                for r in rows.values()
            )
            and len(rows) == 3
            and not any(r.get("crashed") for r in rows.values())
        )
        record(
            result(
                "PJ",
                "J dynamic run per harness app (development set): mode, harness, flows, "
                "statuses, slots, skeletons, JobRun machine, sweeps",
                "mode observed and harness check.sh 3/3; crashes 0/3 (values report-only)",
                rows,
                gate,
            )
        )
        if jmy_doc is not None and "error" not in jmy_doc and args.j_mysql in j_docs:
            a, b = jmy_doc, j_docs[args.j_mysql]
            record(
                result(
                    "PJ-my",
                    f"{args.j_mysql} on MySQL (report only): flows and statuses vs its SQLite run",
                    "same flows and statuses as the SQLite run; 0 containers left",
                    {
                        "run": a["run"],
                        "summary": a["summary"],
                        "same_flow_statuses": [(f["id"], f["status"]) for f in a["flows"]]
                        == [(f["id"], f["status"]) for f in b["flows"]],
                        "wall_s": round(jmy_wall or 0, 1),
                        "containers_left": jmy_left,
                    },
                    None,
                )
            )
        elif want("PJ") and args.j_mysql:
            skip(
                "PJ-my",
                "no docker, or the run failed: " + str((jmy_doc or {}).get("error", ""))[:200],
            )
    elif want("PJ"):
        skip("PJ", "no --spec / --app given")

    # ---- PU over S, S', J
    if want("PU"):
        docs = {k: v for k, v in {"S": dyn_doc, "S-prime": sp_doc, **j_docs}.items() if v}
        docs = {k: v for k, v in docs.items() if v["run"]["mode"] == "observed"}
        static_go = None
        p = out / "workflows-j-go-static-plan.json"
        if "go" in jroots:
            r = run_cli(jroots["go"], out / "go-static", scratch, "go-static",
                        ["--static", *j_user_args])  # fmt: skip
            if r["rc"] == 0:
                static_go = json.loads(r["bytes"])
                p.write_bytes(r["bytes"])
        if docs:
            record(probe_pu(docs, static_go))
        else:
            skip("PU", "no dynamic document")

    # ---- PD
    if want("PD") and static_blobs:
        docs_pd = [json.loads(b) for b in {*static_blobs, *dyn_blobs}]
        r = probe_pd(static_blobs, dyn_blobs, docs_pd)
        j_same = {n: f"{sum(1 for b in bl if b == bl[0])}/{len(bl)}" for n, bl in j_blobs.items()}
        j_leaks: list[str] = []
        for n, bl in j_blobs.items():
            for pth, text in walk_strings(
                json.loads(bl[0]),
                lambda p: (
                    p.endswith(".json") or p.endswith("bindings[].value") or ".expect_db" in p
                ),  # fmt: skip
            ):
                if STRICT_SENT.search(text) or PORT.search(text) or ISO_STAMP.search(text):
                    j_leaks.append(f"{n}{pth}: {text[:80]}")
        r["predicted"] = {"S static": "11/11", "S dynamic": "10/10", "J dynamic": "10/10 x3",
                          "leaks": []}  # fmt: skip
        r["observed"]["j_dynamic"] = j_same
        r["observed"]["j_leaks"] = sorted(set(j_leaks))[:20]
        r["pass"] = bool(
            r["pass"]
            and all(v.split("/")[0] == v.split("/")[1] and v != "0/0" for v in j_same.values())
            and not j_leaks
        )
        record(r)
    elif want("PD"):
        skip("PD", "no static runs")

    # ---- PR: independent replay (S on MySQL; each J harness app with refs from check.sh text)
    if want("PR") and dyn_doc is not None and docker:
        r = probe_pr(s_root, out / "workflows.json", scratch)
        r["id"] = "PR"
        pr_rows = {"S": {"observed": r["observed"], "pass": r["pass"]}}
        ok = r["pass"]
        for name in J_HARNESS:
            if name in j_docs and name in jroots:
                jr = probe_pr(jroots[name], out / f"workflows-j-{name}.json", scratch,
                              refs=J_PR_REFS[name], database="sqlite")  # fmt: skip
                pr_rows[name] = {"observed": jr["observed"], "pass": jr["pass"]}
                ok = ok and bool(jr["pass"])
        r["observed"] = pr_rows
        r["pass"] = ok
        r["predicted"] = {"S": "58 scenarios, 0 failures", "J": "0 failures"}
        record(r)
    elif want("PR"):
        skip("PR", "no S dynamic run")

    # ---- PG: S gated; J not run (it would open .go files: R2-D7)
    if want("PG"):
        skip("PG-J", "report-only on J would need the runner to open the apps' .go files (R2-D7)")

    # ---- PT
    if want("PT"):
        flat = sorted({c for lst in leaked for c in lst})
        record(
            result(
                "PT",
                "containers lattice-wf-* and app processes after every run",
                0,
                {
                    "containers_after_each_run": [len(x) for x in leaked],
                    "names": flat,
                    "now": wf_containers(),
                    "app_processes": app_processes(scratch),
                },
                not flat and not wf_containers() and not app_processes(scratch),
            )
        )
        if docker:
            record(probe_pt_sigint(s_root, scratch, user_args))
        else:
            skip("PT-sigint-S", "no docker")
        if "go" in jroots:
            record(probe_pt_sigint(jroots["go"], scratch, j_user_args, containers=False,
                                   min_snaps=4, label="PT-sigint-go"))  # fmt: skip

    # ---- PX
    if want("PX"):
        med = sorted(static_walls)[len(static_walls) // 2] if static_walls else None
        jmax = max((w for ws in j_walls.values() for w in ws), default=None)
        record(
            result(
                "PX",
                "wall: S static / dynamic; J dynamic per run; J-my",
                {
                    "S_static_s": "0.2-0.3 (<= 5)",
                    "S_dynamic_s": "25-45 (<= 30 min)",
                    "J_run_s": "20-150 (<= 10 min)",
                    "J_my_s": "30-240",
                },  # fmt: skip
                {
                    "S_static_median_s": round(med, 2) if med is not None else None,
                    "S_dynamic_s": [round(w, 1) for w in dyn_walls],
                    "J_run_max_s": round(jmax, 1) if jmax is not None else None,
                    "J_median_s": {
                        n: round(sorted(w)[len(w) // 2], 1) for n, w in j_walls.items() if w
                    },
                    "J_my_s": round(jmy_wall, 1) if jmy_wall else None,
                    "stage_s": round(time.perf_counter() - t_stage, 1),
                },
                (med is not None and med <= 5.0)
                and all(w <= 1800 for w in dyn_walls)
                and (jmax is None or jmax <= 600),
            )
        )

    # ---- PW
    if want("PW") and targets_before is not None:
        digest1 = {
            "S": G.tree_digest(s_root),
            **{n: G.tree_digest(p) for n, p in jroots.items()},
        }
        rows_w = {}
        for k in digest0:
            rows_w[k] = probe_pw(digest0[k], digest1[k])["observed"]
        after = target_state(target_dirs)
        t_rows = {}
        for k, v in targets_before.items():
            t_rows[k] = {
                "porcelain_equal": v["porcelain"] == after[k]["porcelain"],
                "listing_equal": v["listing"] == after[k]["listing"],
                "changed": sorted(
                    p for p in v["listing"] if after[k]["listing"].get(p) != v["listing"][p]
                )[:10],  # fmt: skip
                "new": sorted(set(after[k]["listing"]) - set(v["listing"]))[:10],
            }
        ok = all(not r["changed"] and not r["new"] for r in rows_w.values()) and all(
            r["porcelain_equal"] and r["listing_equal"] for r in t_rows.values()
        )
        record(
            result(
                "PW",
                "archives (tree + sha256 after the build vs after all runs) and the real "
                "checkouts (porcelain + listing before vs after)",
                {"changed": 0, "new": 0},
                {"archives": rows_w, "targets": t_rows},
                ok,
            )
        )

    # ---- PB
    def want_pb(sub: str) -> bool:
        return only is None or "PB" in only or sub in only

    if want_pb("PB-extract"):
        record(probe_pb_extract(scratch, binary, s_root, s4_root))
    if want_pb("PB-gocheck"):
        record(G.probe_pb(scratch, binary, s_root))
    if want_pb("PB-diff"):
        record(probe_pb_diff_r2())
    if want_pb("PB-lint"):
        record(probe_lint_r2())
    if args.spike_a and want_pb("PB-spike-a"):
        record(run_spike_a(scratch, out))
    final_left = wf_containers()
    record(result("LEAK", "lattice-wf- containers after the stage", 0, len(final_left),
                  not final_left, names=final_left))  # fmt: skip

    ran = sorted({r["id"] for r in results})
    all_ids = ["PH", "PL", "PL1", "PU", "PE", "PS", "PK", "PM", "PF", "PR", "PG", "PJ", "PJs",
               "PD", "PW", "PT", "PT-sigint", "PT-sigint-go", "PB-extract", "PB-gocheck",
               "PB-diff", "PB-lint", "PB-spike-a", "PX"]  # fmt: skip
    nr = {pid: why for pid, why in not_run}
    for pid in all_ids:
        if pid not in ran and pid not in nr:
            nr[pid] = "not selected (--only) or its inputs were not given"
    doc_out = {
        "round": 2,
        "streak_ref": args.streak_ref,
        "users": users,
        "j_users": j_users,
        "static_runs": args.static_runs,
        "runs": args.runs,
        "j_runs": args.j_runs,
        "ran": ran,
        "not_run": nr,
        "results": results,
    }
    (out / "probes.json").write_text(json.dumps(doc_out, indent=2, default=str) + "\n")
    lines = ["# Workflow layer round 2 probes", ""]
    for r in results:
        verdict = {True: "PASS", False: "FAIL", None: "REPORT"}[r["pass"]]
        lines.append(
            f"- RAN {r['id']}: {verdict} -- {r['probe']}: observed "
            f"{json.dumps(r['observed'], default=str)[:400]}"
        )
    for pid, why in sorted(nr.items()):
        lines.append(f"- NOT RUN {pid}: {why}")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    bad = [r for r in results if r["pass"] is False]
    return 0 if not bad else 1


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
    ap.add_argument("--refs-json", help="replay worker: harness references (round 2)")
    ap.add_argument(
        "--round2", action="store_true", help="the round-2 stage (discovery, list rule, J)"
    )
    ap.add_argument("--discover-worker", action="store_true")
    ap.add_argument("--j-users", default="root,ann")
    ap.add_argument("--j-runs", type=int, default=10)
    ap.add_argument("--j-mysql", default="", help="round 2: the app to also run on MySQL once")
    ap.add_argument("--database", help="replay worker: mysql | sqlite (round 2)")
    args = ap.parse_args(argv)
    if args.replay_worker:
        return replay_worker(args)
    if args.discover_worker:
        return discover_worker(args)
    if not args.scratch or not args.out:
        ap.error("--scratch and --out are required")
    if args.round2:
        return run_round2(args)
    return run_held_out(args) if args.held_out else run_streak(args)


if __name__ == "__main__":
    raise SystemExit(main())
