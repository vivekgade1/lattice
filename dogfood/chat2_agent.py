"""The real-agent exercise of the chat round 2 (docs/results/2026-10-18-goto-chat-round2-prereg.md,
section 7): the real Studio session HTTP routes, the real ``claude`` CLI, the USD cap of the whole
exercise on one ledger.

    "$DRV" dogfood/chat2_agent.py --questions "$SCRATCH/questions" --usd-cap 8 \\
        --out docs/results/data/2026-10-18-goto-chat-round2/agent

It starts one Studio server per corpus (S and S4) in this process, each with ``[chat] route = true,
check = true`` and ``[llm] claude_bin`` pointing at a scratch wrapper that execs ``claude --max-turns
12 "$@"`` and tees stdout (stream-json) to ``streams/<qid>.jsonl``, the source of the raw arm; it
creates one fresh session per question (``POST /api/session {mode: ask}``, ``POST
/api/session/{id}/message``, ``GET /api/session/{id}/events``) on the question's own corpus server,
so it builds no argv itself. The projects are the ones the question-set stage of ``chat2_probes.py``
left (``--world``, default ``../qset/world.json`` beside ``--out``): Streak a0679e9 with its workflow
inventory and the six change records c1..c6 (S), Streak 1d00ede with a static inventory (S4).

Arms, in this order, all on **one ledger** (``ledger.json``, the CLI's list estimate of every call):

1. ``smoke``: one dev-set question, at most USD 0.35;
2. ``gated``: all 64 questions; no question starts once the spent total plus 1.5 times the largest
   per-question cost so far would exceed the cap;
3. ``ablation`` (report-only): the questions the session routed, again with ``route = false`` (the
   model plus the checker), if budget remains;
4. ``oneshot`` (report-only): the questions the model answered, through ``POST /api/ask`` with the real
   CLI generator and ``check = true``, if budget remains;
5. ``observe`` (report-only): two change-mode questions with ``observe: true`` (tier 1, Docker) whose
   tier 1 ids must survive the checker, if budget remains.

No API key is used: every ``ANTHROPIC_*`` variable is removed from this process and none may be
set. A usage limit stops the exercise: the answers so far are kept, the rest are ``usage_limit`` or
``not_run`` and the run is partial; it is never scored as a pass. The runner checks its own sha256
and that of the engine files against ``freeze-build.sha256`` before it spends anything; there is no
switch to skip the check.
"""

# ruff: noqa: E501 -- tables and texts are data lines
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
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

import chat2_probes as CP  # noqa: E402

REPO = CP.REPO
MODEL = "sonnet"
MAX_TURNS = 12
SESSION_TIMEOUT = 600
OBSERVE_TIMEOUT = 1500
SMOKE_CAP = 0.35
SMOKE_QUESTION = "What happens when a check-in fails validation?"  # round 1's C-1: dev data
OBSERVE = ("What fails on today's app if {cid} is applied? Run the observed check "
           "(change_impact with observe true) and report the failing items with their ids.")  # fmt: skip
TEE = """#!/bin/sh
case "$1" in
  -p) {claude} --max-turns {turns} "$@" | tee "$FC2_TEE_DIR/$FC2_QID.jsonl" ;;
  *) exec {claude} "$@" ;;
esac
"""


class Ledger:
    """The USD ledger of the whole exercise, on disk after every call (re-runs add to it)."""

    def __init__(self, path: Path, cap: float) -> None:
        self.path, self.cap = path, cap
        data = json.loads(path.read_text()) if path.is_file() else {"entries": []}
        self.entries: list[dict[str, Any]] = data["entries"]

    @property
    def spent(self) -> float:
        return round(sum(e["usd"] for e in self.entries), 6)

    @property
    def largest(self) -> float:
        return max(
            (
                e["usd"]
                for e in self.entries
                if e["arm"] in ("gated", "ablation", "oneshot", "observe")
            ),
            default=0.0,
        )

    def allows(self) -> bool:
        return self.spent + 1.5 * self.largest <= self.cap

    def add(self, arm: str, qid: str, usd: float, **extra: Any) -> None:
        self.entries.append({"arm": arm, "qid": qid, "usd": round(usd, 6), **extra})
        self.save()

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"cap": self.cap, "spent": self.spent, "entries": self.entries}, indent=1)
        )


def http(method: str, url: str, body: Any = None, timeout: float = 120) -> tuple[int, Any]:
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


def raw_text_of(tee: Path) -> str:
    """The assistant text blocks of a tee'd stream-json (the model's words before any check)."""
    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    if not tee.is_file():
        return ""
    for line in tee.read_text(errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant" or rec.get("parent_tool_use_id"):
            continue
        msg = rec.get("message") or {}
        for b in msg.get("content") or ():
            if b.get("type") == "text" and (msg.get("id"), b.get("text")) not in seen:
                seen.add((str(msg.get("id")), str(b.get("text"))))
                out.append(str(b.get("text") or ""))
    return "\n\n".join(out)


def make_wrapper(dst: Path, streams: Path) -> Path:
    claude = shutil.which("claude")
    assert claude, "the claude CLI is not installed"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(TEE.format(claude=claude, turns=MAX_TURNS))
    dst.chmod(0o755)
    streams.mkdir(parents=True, exist_ok=True)
    return dst


def project_copy(
    world: dict[str, Any],
    name: str,
    dst: Path,
    wrapper: Path,
    chat: str,
    stub_backend: bool,
    observe_env: bool = False,
) -> Path:
    """The question-set stage's project `name` as a server project with the wrapper as claude_bin."""
    import goto_probes as GP  # noqa: PLC0415

    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(world["dirs"][name], dst, symlinks=True, ignore=shutil.ignore_patterns("bin"))
    GP.set_tables(dst, Path(world["inv"][name]), dst, Path(world["state"][name]))
    text = (dst / "studio.toml").read_text()
    mine = f'model = "{MODEL}"\nsession_timeout = {SESSION_TIMEOUT}\nclaude_bin = "{wrapper}"\n' + (
        'backend = "stub"\n' if stub_backend else ""
    )
    text = re.sub(r"(?m)^(?:claude_bin|session_timeout|backend)\s*=.*\n", "", text)
    if "[llm]\n" in text:
        text = re.sub(
            r"(?m)^(\[llm\]\n)(?:model\s*=.*\n)?", lambda m: m.group(1) + mine, text, count=1
        )
    else:
        text += "\n[llm]\n" + mine
    if name == "S4":
        text = re.sub(r"(?ms)^\[impact\]\n", "", text)
    (dst / "studio.toml").write_text(text.rstrip("\n") + f"\n\n[chat]\n{chat}")
    return dst


class Meter:
    """The CLI generator of the one-shot arm with its list-estimate cost recorded per call."""

    def __init__(self) -> None:
        from lattice.llm.cli_client import ClaudeCliClient  # noqa: PLC0415

        self.inner = ClaudeCliClient(
            model=MODEL, max_tokens=32000, effort="", timeout=900, full_text=True
        )
        self.cost = 0.0
        self.calls = 0
        self.usage_limit = False
        self.lock = threading.Lock()

    def generate(self, system: str, messages: Sequence[Any]) -> Any:
        from lattice.llm.cli_client import CliUsageLimitError  # noqa: PLC0415

        try:
            gen = self.inner.generate(system, messages)
        except CliUsageLimitError:
            self.usage_limit = True
            raise
        with self.lock:
            self.calls += 1
            self.cost += float(gen.cost_usd or 0.0)
        return gen


def serve(project: Path, generator: Any = None, owner: str | None = None) -> tuple[Any, str]:
    from lattice.studio.server import make_server  # noqa: PLC0415

    if owner:
        os.environ["LATTICE_CONTAINER_OWNER"] = owner
    srv = make_server(project, 0, generator=generator)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def run_session(
    base: str, mode: str, text: str, qid: str, tee_dir: Path, limit: float
) -> dict[str, Any]:
    """One fresh session, one message, polled to the end. The scored text is every checked text
    event joined by a blank line; the raw text comes from the stream tee."""
    os.environ["FC2_QID"] = qid
    os.environ["FC2_TEE_DIR"] = str(tee_dir)
    t0 = time.perf_counter()
    status, made = http("POST", base + "/api/session", {"mode": mode})
    if status != 201:
        return {"status": "error", "error": f"session create {status}: {made}", "text": ""}
    sid = made["id"]
    status, body = http("POST", base + f"/api/session/{sid}/message", {"text": text})
    if status not in (200, 202):
        return {"status": "error", "error": f"message {status}: {body}", "text": ""}
    events: list[dict[str, Any]] = []
    after = 0
    end = time.monotonic() + limit + 60
    done = False
    while time.monotonic() < end:
        _, view = http("GET", base + f"/api/session/{sid}/events?after={after}")
        events += view["events"]
        after = view["next"]
        if view["done"]:
            done = True
            break
        time.sleep(0.5)
    wall = round(time.perf_counter() - t0, 1)
    texts = [e["text"] for e in events if e["type"] == "text"]
    results = [e for e in events if e["type"] == "result"]
    errors = [e for e in events if e["type"] == "error"]
    row: dict[str, Any] = {"wall_s": wall, "text": "\n\n".join(texts), "n_events": len(events), "tools": [e["name"] for e in events if e["type"] == "tool_use"],
                           "partial_text_events": sum(1 for e in events if e["type"] == "text" and e.get("partial"))}  # fmt: skip
    if results:
        r = results[-1]
        row.update(cost_usd=float(r.get("cost_usd") or 0.0), turns=int(r.get("num_turns") or 0), input_tokens=r.get("input_tokens", 0), output_tokens=r.get("output_tokens", 0),
                   cache_read_tokens=r.get("cache_read_input_tokens", 0), cache_creation_tokens=r.get("cache_creation_input_tokens", 0),
                   routed=r.get("routed") is not None, routed_kind=r.get("routed"), check=r.get("check"))  # fmt: skip
    if errors:
        kind = errors[-1].get("kind")
        row.update(
            status="usage_limit" if kind == "usage_limit" else "error",
            error=str(errors[-1].get("message"))[-300:],
            error_kind=kind,
        )
    elif not done:
        row.update(status="error", error=f"no end within {limit + 60:g} s")
    elif not results or not results[-1].get("ok", True):
        row.update(status="error", error="no result")
    else:
        row.update(status="ok")
    return row


def score(
    qdir: Path, qset: Path, out: Path, reports: Path, agent: Path, raw: Path | None
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(qdir / "chat2_score.py"),
        "--qset",
        str(qset),
        "--agent",
        str(agent),
        "--reports",
        str(reports),
        "--json",
    ]
    if raw is not None:
        cmd += ["--raw", str(raw)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    (out / f"score-{agent.stem}.json").write_text(r.stdout)
    data: dict[str, Any] = {"rc": r.returncode, "stderr": r.stderr[-300:]}
    with contextlib.suppress(ValueError):
        data["result"] = json.loads(r.stdout)
    return data


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--usd-cap", type=float, default=8.0)
    ap.add_argument(
        "--world",
        help="world.json of the question-set stage (default: ../qset/world.json beside --out)",
    )
    ap.add_argument("--only", help="comma list of question ids (a partial gated run)")
    ap.add_argument(
        "--observe",
        type=int,
        default=2,
        help="change-mode observe questions after the other arms (at most 2)",
    )
    ap.add_argument(
        "--arms",
        default="smoke,gated,ablation,oneshot,observe",
        help="the arms to run, in the fixed order",
    )
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    CP.verify_freeze(out.parent)  # the frozen build: exit 2 on a difference; there is no switch
    for k in [k for k in os.environ if k.startswith("ANTHROPIC_")]:
        del os.environ[k]
    assert not any(k.startswith("ANTHROPIC_") for k in os.environ), "no API key reaches the CLI"
    out.mkdir(parents=True, exist_ok=True)
    qdir = Path(args.questions).resolve()
    qset_path = qdir / "qset.json"
    qs = json.loads(qset_path.read_text())
    world_path = Path(args.world) if args.world else out.parent / "qset" / "world.json"
    arms = [
        a for a in ("smoke", "gated", "ablation", "oneshot", "observe") if a in args.arms.split(",")
    ]
    ledger = Ledger(out / "ledger.json", args.usd_cap)
    usage: dict[str, Any] = {
        "model": MODEL,
        "max_turns": MAX_TURNS,
        "session_timeout_s": SESSION_TIMEOUT,
        "usd_cap": args.usd_cap,
        "arms": {},
        "notes": [],
    }
    if shutil.which("claude") is None:
        usage["status"] = "NOT RUN: the claude CLI is not installed"
        (out / "agent-usage.json").write_text(json.dumps(usage, indent=1))
        print(usage["status"])
        return 2
    if not world_path.is_file():
        usage["status"] = (
            f"NOT RUN: no world at {world_path} (run chat2_probes.py --stage qset first)"
        )
        (out / "agent-usage.json").write_text(json.dumps(usage, indent=1))
        print(usage["status"])
        return 2
    world = json.loads(world_path.read_text())
    reports = Path(world["reports"])
    scratch = Path(world["scratch"]) / "agent"
    streams = out / "streams"
    wrapper = make_wrapper(scratch / "bin" / "claude-tee", streams)
    token = f"{CP.OWNER}-agent-{os.getpid()}"
    questions = [q for q in qs["questions"] if not args.only or q["id"] in args.only.split(",")]
    stopped: str | None = None
    servers: list[Any] = []
    detail: dict[str, Any] = {}
    answers: dict[str, Any] = {}
    raw_answers: dict[str, Any] = {}

    def start(corpus_tag: str, chat: str, stub: bool, generator: Any = None) -> dict[str, str]:
        urls = {}
        for name in ("S", "S4"):
            proj = project_copy(world, name, scratch / f"{corpus_tag}-{name}", wrapper, chat, stub)
            srv, url = serve(proj, generator, owner=token)
            servers.append(srv)
            urls[name] = url
        return urls

    urls = start("main", "route = true\ncheck = true\n", stub=True)
    # 1. smoke
    if "smoke" in arms:
        row = run_session(urls["S"], "ask", SMOKE_QUESTION, "smoke", streams, SESSION_TIMEOUT)
        ledger.add("smoke", "smoke", row.get("cost_usd", 0.0), status=row["status"])
        usage["arms"]["smoke"] = {k: v for k, v in row.items() if k != "text"} | {
            "cap": SMOKE_CAP,
            "within_cap": row.get("cost_usd", 0.0) <= SMOKE_CAP,
        }
        print(
            f"smoke      {row['status']:<12} ${row.get('cost_usd', 0):.3f} {row.get('turns', 0)} turns {row['wall_s']} s",
            flush=True,
        )
        if row["status"] == "usage_limit":
            stopped = "usage limit at the smoke question"
        elif row["status"] != "ok" or row.get("cost_usd", 0.0) > SMOKE_CAP:
            stopped = (
                f"smoke question: {row['status']} ${row.get('cost_usd', 0):.3f} (cap {SMOKE_CAP})"
            )
    # 2. the gated run
    if "gated" in arms and stopped is None:
        for q in questions:
            if not ledger.allows():
                stopped = f"cap: USD {ledger.spent:.2f} + 1.5 x {ledger.largest:.2f} > {ledger.cap:.2f} before {q['id']}"
                break
            row = run_session(
                urls[q.get("corpus", "S")], "ask", q["text"], q["id"], streams, SESSION_TIMEOUT
            )
            ledger.add(
                "gated",
                q["id"],
                row.get("cost_usd", 0.0),
                status=row["status"],
                routed=row.get("routed", False),
            )
            answers[q["id"]] = {
                "text": row.get("text", ""),
                "status": row["status"],
                "routed": bool(row.get("routed")),
            }
            raw_answers[q["id"]] = (
                {
                    "text": raw_text_of(streams / f"{q['id']}.jsonl"),
                    "status": row["status"],
                    "routed": False,
                }
                if not row.get("routed")
                else {"text": "", "status": "not_run", "routed": False}
            )
            detail[q["id"]] = {
                **{k: v for k, v in row.items() if k != "text"},
                "set": q["set"],
                "corpus": q.get("corpus", "S"),
                "final_chars": len(row.get("text", "")),
            }
            print(
                f"{q['id']:<10} {row['status']:<12} ${row.get('cost_usd', 0):.3f} {row.get('turns', 0)} turns {row['wall_s']} s{' [routed]' if row.get('routed') else ''}",
                flush=True,
            )
            if row["status"] == "usage_limit":
                stopped = f"usage limit at {q['id']}"
                break
        for q in questions:  # a question that never started is not_run, never a pass
            answers.setdefault(q["id"], {"text": "", "status": "not_run", "routed": False})
            raw_answers.setdefault(q["id"], {"text": "", "status": "not_run", "routed": False})
    # 3. the ablation: the routed questions again with route = false (the model plus the checker)
    abl_answers: dict[str, Any] = {}
    abl_detail: dict[str, Any] = {}
    routed_ids = [q["id"] for q in questions if detail.get(q["id"], {}).get("routed")]
    if "ablation" in arms and stopped is None and routed_ids:
        try:
            aurls = start("ablation", "check = true\n", stub=True)
        except Exception as exc:  # noqa: BLE001
            usage["notes"].append(f"ablation servers did not start: {exc}")
            aurls = {}
        for q in [q for q in questions if q["id"] in routed_ids] if aurls else []:
            if not ledger.allows():
                usage["notes"].append(f"ablation stopped by the cap before {q['id']}")
                break
            row = run_session(
                aurls[q.get("corpus", "S")],
                "ask",
                q["text"],
                f"abl-{q['id']}",
                streams,
                SESSION_TIMEOUT,
            )
            ledger.add("ablation", q["id"], row.get("cost_usd", 0.0), status=row["status"])
            abl_answers[q["id"]] = {
                "text": row.get("text", ""),
                "status": row["status"],
                "routed": False,
            }
            abl_detail[q["id"]] = {k: v for k, v in row.items() if k != "text"}
            raw_answers[q["id"]] = {
                "text": raw_text_of(streams / f"abl-{q['id']}.jsonl"),
                "status": row["status"],
                "routed": False,
            }
            print(f"abl-{q['id']:<6} {row['status']:<12} ${row.get('cost_usd', 0):.3f}", flush=True)
            if row["status"] == "usage_limit":
                stopped = f"usage limit at the ablation of {q['id']}"
                break
        usage["notes"].append(
            "the raw arm of a routed question comes from its ablation run (the session ran no model)"
        )
    # 4. the one-shot arm: the questions the model answered, through POST /api/ask with the CLI generator
    one_answers: dict[str, Any] = {}
    one_detail: dict[str, Any] = {}
    model_ids = [
        q["id"] for q in questions if q["id"] in detail and not detail[q["id"]].get("routed")
    ]
    if "oneshot" in arms and stopped is None and model_ids:
        meter = Meter()
        try:
            ourls = start("oneshot", "route = true\ncheck = true\n", stub=False, generator=meter)
        except Exception as exc:  # noqa: BLE001
            usage["notes"].append(f"one-shot servers did not start: {exc}")
            ourls = {}
        for q in [q for q in questions if q["id"] in model_ids] if ourls else []:
            if not ledger.allows():
                usage["notes"].append(f"one-shot arm stopped by the cap before {q['id']}")
                break
            c0 = meter.cost
            status, body = http(
                "POST",
                ourls[q.get("corpus", "S")] + "/api/ask",
                {"question": q["text"]},
                timeout=1200,
            )
            usd = meter.cost - c0
            ledger.add("oneshot", q["id"], usd, status=status)
            ok = status == 200 and isinstance(body, dict) and "answer" in body
            one_answers[q["id"]] = {
                "text": (body["answer"] + "\n\n" + body.get("explanation", "")).strip()
                if ok
                else "",
                "status": "ok" if ok else ("usage_limit" if meter.usage_limit else "error"),
                "routed": False,
            }
            one_detail[q["id"]] = {
                "http": status,
                "kind": body.get("kind") if isinstance(body, dict) else None,
                "check": body.get("check") if isinstance(body, dict) else None,
                "cost_usd": round(usd, 6),
            }
            print(f"one-{q['id']:<6} {one_answers[q['id']]['status']:<12} ${usd:.3f}", flush=True)
            if meter.usage_limit:
                stopped = f"usage limit at the one-shot answer of {q['id']}"
                break
    # 5. observe: two change-mode questions with tier 1 (Docker); their tier 1 ids must survive the checker
    observe_rows: dict[str, Any] = {}
    if "observe" in arms and stopped is None and args.observe > 0:
        try:
            observe_rows = run_observe(
                world, scratch, wrapper, streams, token, ledger, out, min(args.observe, 2), usage
            )
        except Exception as exc:  # noqa: BLE001
            usage["notes"].append(f"observe arm: {type(exc).__name__}: {exc}")
    # write everything, then score the gated run once with the wrapper scorer
    for srv in servers:
        with contextlib.suppress(Exception):
            if getattr(srv.studio, "_sessions", None) is not None:
                srv.studio._sessions.close()
            srv.shutdown()
            srv.server_close()
    with contextlib.suppress(Exception):
        from lattice.studio.state import remove_owned_containers  # noqa: PLC0415

        remove_owned_containers(token)
    (out / "answers-checked.json").write_text(json.dumps({"answers": answers}, indent=1))
    (out / "answers-raw.json").write_text(json.dumps({"answers": raw_answers}, indent=1))
    if abl_answers:
        (out / "answers-ablation.json").write_text(json.dumps({"answers": abl_answers}, indent=1))
    if one_answers:
        (out / "answers-oneshot.json").write_text(json.dumps({"answers": one_answers}, indent=1))
    ok_rows = [d for d in detail.values() if d["status"] == "ok"]
    usage.update(
        status="partial" if stopped or len(ok_rows) != len(questions) else "complete",
        stopped=stopped,
        asked=len(detail),
        ok=len(ok_rows),
        of=len(questions),
        routed=sum(1 for d in detail.values() if d.get("routed")),
        spent_usd_list_estimate=ledger.spent,
        ledger_entries=len(ledger.entries),
        per_arm_usd={
            a: round(sum(e["usd"] for e in ledger.entries if e["arm"] == a), 4)
            for a in ("smoke", "gated", "ablation", "oneshot", "observe")
        },
        input_tokens=sum(d.get("input_tokens", 0) for d in detail.values()),
        output_tokens=sum(d.get("output_tokens", 0) for d in detail.values()),
        cache_read_tokens=sum(d.get("cache_read_tokens", 0) for d in detail.values()),
        cache_creation_tokens=sum(d.get("cache_creation_tokens", 0) for d in detail.values()),
        turns=sum(d.get("turns", 0) for d in detail.values()),
        wall_s=round(sum(d.get("wall_s", 0) for d in detail.values()), 1),
        partial_text_events=sum(d.get("partial_text_events", 0) for d in detail.values()),
        change_impact_calls=sum(
            1
            for d in detail.values()
            for t in d.get("tools", [])
            if str(t).endswith("change_impact")
        ),
        per_question=detail,
        ablation=abl_detail,
        oneshot=one_detail,
        observe=observe_rows,
    )
    (out / "agent-usage.json").write_text(json.dumps(usage, indent=1, default=str))
    scored: dict[str, Any] = {}
    if "gated" in arms and answers:
        scored["gated"] = score(
            qdir, qset_path, out, reports, out / "answers-checked.json", out / "answers-raw.json"
        )
        res = (scored["gated"].get("result") or {}).get("G3")
        print(
            "G3",
            json.dumps(
                {
                    k: (res or {}).get(k)
                    for k in (
                        "run",
                        "pass",
                        "n",
                        "correct",
                        "partial",
                        "unsupported_failure_claims",
                        "i_verbatim_id_compliance",
                    )
                }
            ),
        )
    if abl_answers:
        scored["ablation"] = score(
            qdir, qset_path, out, reports, out / "answers-ablation.json", None
        )
    if one_answers:
        scored["oneshot"] = score(qdir, qset_path, out, reports, out / "answers-oneshot.json", None)
    print(f"spent USD {ledger.spent:.2f} of {ledger.cap:.2f}; stopped: {stopped}")
    return 0 if stopped is None else 3


def run_observe(
    world: dict[str, Any],
    scratch: Path,
    wrapper: Path,
    streams: Path,
    token: str,
    ledger: Ledger,
    out: Path,
    n: int,
    usage: dict[str, Any],
) -> dict[str, Any]:
    """Two change-mode questions with ``observe: true``: expected tier 1 ids from the CLI at
    ``--tiers 123`` (Docker, on a built app); the checked answer must state exactly those."""
    import goto_probes as GP  # noqa: PLC0415

    w = GP.World(
        argparse.Namespace(
            scratch=str(scratch / "obs-world"),
            out=str(out),
            workflows="",
            streak_ref="",
            streak_ref2="",
        )
    )
    w.state["S"] = Path(world["state"]["S"])
    root = project_copy(
        world,
        "S",
        scratch / "observe-S",
        wrapper,
        "route = true\ncheck = true\n",
        stub_backend=True,
    )
    GP.G.go_build(root)
    srv, url = serve(root, None, owner=token)
    rows: dict[str, Any] = {}
    try:
        for cid in ("c1", "c2")[:n]:
            if not ledger.allows():
                usage["notes"].append(f"observe arm stopped by the cap before {cid}")
                break
            exp_path = out / f"observe-expected-{cid}.json"
            r = GP.engine_cli(w, root, cid, w.state["S"], tiers="123", out=exp_path)
            exp = json.loads(exp_path.read_text()) if r.rc == 0 and exp_path.is_file() else None
            exp_ids = sorted(
                i["id"]
                for i in [
                    *(exp or {}).get("tier1", {}).get("failing", []),
                    *(exp or {}).get("tier1", {}).get("flows", []),
                ]
            )
            text = OBSERVE.format(cid=cid)
            row = run_session(url, "change", text, f"observe-{cid}", streams, OBSERVE_TIMEOUT)
            ledger.add("observe", cid, row.get("cost_usd", 0.0), status=row["status"])
            got = sorted(set(re.findall(r"\bt1-[0-9a-f]{10}\b", row.get("text", ""))))
            from lattice.spec import claim_check as cc  # noqa: PLC0415

            claims = cc.scorer_claims(
                row.get("text", ""),
                set(exp_ids)
                | {i["id"] for i in (exp or {}).get("tier2", {}).get("determined", [])},
            )
            rows[cid] = {"status": row["status"], "expected_tier1_ids": exp_ids, "stated_tier1_ids": got, "ids_survive": exp is not None and got == exp_ids,
                         "unsupported_claims": claims, "cost_usd": row.get("cost_usd", 0.0), "turns": row.get("turns", 0), "cli_rc": r.rc,
                         "check": row.get("check"), "tools": row.get("tools")}  # fmt: skip
            (out / f"observe-{cid}.json").write_text(
                json.dumps({**rows[cid], "text": row.get("text", "")}, indent=1)
            )
            print(
                f"observe {cid} {row['status']} ${row.get('cost_usd', 0):.3f} ids_survive={rows[cid]['ids_survive']}",
                flush=True,
            )
            if row["status"] == "usage_limit":
                break
    finally:
        with contextlib.suppress(Exception):
            if getattr(srv.studio, "_sessions", None) is not None:
                srv.studio._sessions.close()
            srv.shutdown()
            srv.server_close()
    return rows


if __name__ == "__main__":
    sys.exit(main())
