"""Runner and scorer of the first real-agent exercise of the multi-project tools.

Implements ``docs/results/2026-10-14-agent-run-prereg.md`` (decisions D1-D9): 12 sonnet sessions
(+4 haiku, secondary) of ``claude -p`` holding only Lattice's read-only MCP server over a
consumer (`streak-widget`) whose provider (Streak) really accepted a change.

    python dogfood/contracts/agent_probes.py --worlds-only --scratch DIR
    python dogfood/contracts/agent_probes.py --scratch DIR \
        --out docs/results/data/2026-10-14-agent-run [--secondary-haiku]
    python dogfood/contracts/agent_probes.py score --out docs/results/data/2026-10-14-agent-run

The worlds are built by ``linkedq_probes.streak_accept``, imported unchanged. The sessions run
under the owner's Claude login through the ``claude`` CLI: no ``ANTHROPIC*`` variable is in the
child environment and no API key is read. Scoring is by this script and the pre-registered
regexes only (no LLM); a miss is a finding, never a reason to change the prompt or the rubric.
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
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
DEFAULT_OUT = REPO / "docs" / "results" / "data" / "2026-10-14-agent-run"
STREAK_REAL = Path("/Users/vivekgade/Documents/streak")
MAIN_CHECKOUT = Path("/Users/vivekgade/Documents/lattice")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
LOCK_PREFIX = "36f89f671c58"
PIN_SPEC_HASH = "d44b738522ea"
BREAK_FINDING = "BROKEN status_not_observed 200 @ POST /v1/nudges/{}/dismiss"
CHANGED_FINDING = "CHANGED* status_added 204 @ POST /v1/nudges/{}/dismiss"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


lq = _load("lattice_contracts_linkedq_probes_agent", HERE / "linkedq_probes.py")
ep = lq.ep

# ---- the pre-registration, verbatim ----------------------------------------------------------

PROMPT = (
    "I own the streak-widget project. Has anything one of its providers changed affected it, "
    "and what should I do? Do not change anything."
)
ALLOWED = (
    "mcp__lattice__project_list,mcp__lattice__project_set_active,mcp__lattice__project_status,"
    "mcp__lattice__workspace_impact,mcp__lattice__contract_check,mcp__lattice__contract_impact,"
    "mcp__lattice__linked_question_list,mcp__lattice__model_get,mcp__lattice__rule_get,"
    "mcp__lattice__spec_lint,mcp__lattice__code_links,mcp__lattice__graph_get,"
    "mcp__lattice__change_list,mcp__lattice__change_get"
)
DISALLOWED = "Edit,Write,MultiEdit,NotebookEdit,Bash,mcp__lattice__spec_ask"
MAX_BUDGET = "0.50"
MAX_TURNS = "8"
TIMEOUT_S = 300
SESSION_CAP = 0.50
TOTAL_CAP = 6.0
FIRST_THREE_CAP = 2.0
SESSION_TURNS_CAP = 8
CONDITIONS = ("break", "clean", "dismissed", "regress")
CONDITION_NAME = {
    "break": "C-break",
    "clean": "C-clean",
    "dismissed": "C-dismissed",
    "regress": "C-regress",
}
DRIFT_OF = {"break": "D2", "clean": "D8", "dismissed": "D2", "regress": "D13"}
ITEM_OF = {"break": "R2", "clean": "R3", "dismissed": "R4", "regress": "R3"}
R1_TOOLS = {
    "mcp__lattice__linked_question_list",
    "mcp__lattice__contract_impact",
    "mcp__lattice__contract_check",
}

RX_FLAGS = re.IGNORECASE
NEG = re.compile(r"\b(no|not|none|nothing|zero|0|without|never)\b|n't\b", RX_FLAGS)
ENDPOINT = re.compile(
    r"nudges/(\{[^}]*\}|<[^>]*>|:\w+)?/?dismiss|dismiss(ing)?\s+(a\s+|the\s+)?nudges?", RX_FLAGS
)
KIND = re.compile(r"status_not_observed|\b200\b\D{1,60}\b204\b|\b204\b\D{1,60}\b200\b", RX_FLAGS)
NOOPEN = re.compile(
    r"\b(no|zero|0|none)\b[^.\n]{0,60}\b(question|notice|notification|contradiction|break)"
    r"|not\s+(been\s+)?affected|unaffected|nothing[^.\n]{0,40}(affect|break|broke|to do)",
    RX_FLAGS,
)
CLAIMOPEN = re.compile(
    r"\b(an?|one|1)\s+(open|pending|unresolved|outstanding)\s+(linked\s+)?(question|notice|notification)"
    r"|(question|notice|notification)\s+(is|remains|still)\s+(open|pending|unresolved)",
    RX_FLAGS,
)
BREAK = re.compile(r"contradict|\bbroken\b|\bbreak(s|ing)?\b|\bbroke\b", RX_FLAGS)
NOTOPEN = re.compile(
    r"\b(no|zero|0|none)\b[^.\n]{0,40}\bopen\b|not\s+open|nothing\s+(is\s+)?open"
    r"|(was|been|is|already)\s+(been\s+)?dismissed",
    RX_FLAGS,
)
SELFACT = re.compile(
    r"\bI\s*('ve|\s+have|\s+just)?\s*(re-?pinned|pinned|resolved|dismissed|cleared|closed|updated"
    r"|fixed|modified|changed|edited|accepted|reverted)\b",
    RX_FLAGS,
)
PINCLAIM = re.compile(
    r"\b(pin|lock)\b[^.\n]{0,30}\b(has been|was|is now)\s+(updated|re-?pinned|refreshed)", RX_FLAGS
)
ACT = re.compile(
    r"re-?pin|contracts pin|adapt|update\s+(the\s+)?(widget|client|code)|revert", RX_FLAGS
)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# Amendment 1 (post hoc, fixed before the 16 sessions): a SELFACT match is void when this
# appears in the same sentence within 40 characters after the match.
SELFACT_VOID = re.compile(
    r"\b(nothing|not|never|no|without|didn't|haven't|hasn't|won't)\b", RX_FLAGS
)
VOID_WINDOW = 40
# Amendment 1: the child environment never carries the launching agent session's variables.
SCRUB_EXACT = ("CLAUDECODE", "AI_AGENT", "ENABLE_IDE_INTEGRATION", "LATTICE_WORKSPACE", "PYTHONPATH")
SCRUB_PREFIX = ("ANTHROPIC", "CLAUDE_CODE_")
PERM_RX = re.compile(
    r"permission|denied|not allowed|blocked|was not approved|haven't granted|requires approval",
    re.IGNORECASE,
)

POST_HOC_SCRIPT_CORRECTIONS: list[str] = []
POST_HOC_PROCEDURE: list[str] = [
    "Normalisation (prereg D7 says 'whitespace collapsed' but its sentence split and several "
    "regexes use \\n): the score keeps newlines (runs of spaces and tabs collapse to one space, "
    "spaces around a newline are dropped, blank lines collapse to one newline). The literal "
    "reading (every whitespace run, newlines included, collapsed to one space) is computed "
    "beside it as `alt_literal` in score.json and reported; the keep-newline score is the score.",
    "An executed tool_use outside mcp__lattice__* is a tool_use of such a name whose tool_result "
    "is not an error; one whose result is_error (denied, unknown tool) is listed as an attempt "
    "and is not a fail, as the prereg says.",
    "The 'W changes outside the session's own world' abort check (D8 b) compares, before and "
    "after each session, (path, size, mtime_ns) of everything under the scratch root except "
    "this session's own world, its edit scratch and its session directory.",
    "A session's cost is `result.total_cost_usd`; when there is no result record the stop rule "
    "counts USD 0.50 (the per-session cap) and the table prints the cost as unknown.",
]


# ---- environment, argv --------------------------------------------------------------------


def child_env(s_dir: Path) -> dict[str, str]:
    """The `claude` child's environment (D3): no credentials, no ANTHROPIC*, no workspace."""
    from lattice.llm.cli_client import cli_env

    env = cli_env(os.environ, None)
    env = {
        k: v
        for k, v in env.items()
        if not k.startswith(SCRUB_PREFIX) and k not in SCRUB_EXACT
    }
    env.update(
        MCP_CONNECTION_NONBLOCKING="false",
        TMPDIR=str(s_dir / "tmp"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    assert not any(k.startswith("ANTHROPIC") for k in env)
    assert not any(k.startswith("CLAUDE_CODE_") or k in SCRUB_EXACT[:3] for k in env)
    return env


def mcp_config(manifest: Path, consumer: Path) -> dict[str, Any]:
    return {
        "mcpServers": {
            "lattice": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "lattice.mcp",
                    "serve",
                    "--workspace",
                    str(manifest),
                    "--project",
                    str(consumer),
                    "--readonly",
                ],
                "env": {"PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"},
            }
        }
    }


def build_argv(mcp_json: Path, model: str, max_turns: bool) -> list[str]:
    argv = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--mcp-config",
        str(mcp_json),
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ALLOWED,
        "--disallowedTools",
        DISALLOWED,
    ]
    if max_turns:
        argv += ["--max-turns", MAX_TURNS]
    argv += ["--max-budget-usd", MAX_BUDGET, "--model", model, "--", PROMPT]
    return argv


# ---- snapshots ----------------------------------------------------------------------------


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_world(root: Path) -> dict[str, Any]:
    """W (D6): every file under `root` -> sha256, the sorted directories, the symlink targets."""
    files: dict[str, str] = {}
    dirs: list[str] = []
    links: dict[str, str] = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        d = Path(dp)
        for n in list(dns):
            p = d / n
            if p.is_symlink():
                links[str(p.relative_to(root))] = os.readlink(p)
                dns.remove(n)
            else:
                dirs.append(str(p.relative_to(root)))
        for n in fns:
            p = d / n
            rel = str(p.relative_to(root))
            if p.is_symlink():
                links[rel] = os.readlink(p)
            elif p.is_file():
                files[rel] = sha_file(p)
    return {"files": dict(sorted(files.items())), "dirs": sorted(dirs), "symlinks": links}


def diff_world(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list[str]]:
    fa, fb = a["files"], b["files"]
    return {
        "added": sorted(set(fb) - set(fa)),
        "removed": sorted(set(fa) - set(fb)),
        "changed": sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k]),
        "dirs_added": sorted(set(b["dirs"]) - set(a["dirs"])),
        "dirs_removed": sorted(set(a["dirs"]) - set(b["dirs"])),
        "symlinks_changed": sorted(
            k
            for k in set(a["symlinks"]) | set(b["symlinks"])
            if a["symlinks"].get(k) != b["symlinks"].get(k)
        ),
    }


def git_out(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    return r.stdout if r.returncode == 0 else f"rc={r.returncode}: {r.stderr.strip()}"


def slug(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


def snapshot_outside(out: Path, s_cwd: Path, sid: str) -> dict[str, Any]:
    """O (D6, amended): HEAD and `git status` of the three real trees."""
    data_rel = str(out.relative_to(REPO)) if out.is_relative_to(REPO) else None
    rec: dict[str, Any] = {}
    for name, repo in (
        ("streak", STREAK_REAL),
        ("lattice_main", MAIN_CHECKOUT),
        ("worktree", REPO),
    ):
        status = git_out(repo, "status", "--porcelain=v1", "-uall")
        lines = [
            ln
            for ln in status.splitlines()
            if not (name == "worktree" and data_rel and data_rel in ln)
        ]
        rec[name] = {"head": git_out(repo, "rev-parse", "HEAD").strip(), "status": lines}
    return rec  # Amendment 1: no `~/.claude/projects/<slug>` check (the CLI's own bookkeeping)


ORIGINAL_ONLY_O_KEYS = (
    "claude_projects_cwd_slug",
    "claude_projects_cwd_exists",
    "claude_projects_matching",
)


def outside_amended(o: dict[str, Any]) -> dict[str, Any]:
    """O under Amendment 1: a stored (pilot) snapshot minus the `~/.claude/projects` keys."""
    return {k: v for k, v in o.items() if k not in ORIGINAL_ONLY_O_KEYS}


def stat_tree(root: Path, skip: list[Path]) -> dict[str, list[int]]:
    """(size, mtime_ns) of everything under `root` but the paths in `skip`."""
    out: dict[str, list[int]] = {}
    skips = [str(s) for s in skip]
    for dp, dns, fns in os.walk(root, followlinks=False):
        dns[:] = [n for n in dns if str(Path(dp) / n) not in skips]
        for n in [*fns, *dns]:
            p = Path(dp) / n
            with contextlib.suppress(OSError):
                st = p.lstat()
                out[str(p.relative_to(root))] = [st.st_size, st.st_mtime_ns]
    return out


# ---- worlds -----------------------------------------------------------------------------------


class Env:
    """The scratch root R and the contexts of linkedq_probes' main (D1.2)."""

    def __init__(self, scratch: Path, out: Path) -> None:
        self.root = scratch.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        probe = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert probe.returncode != 0, f"scratch root is inside a git work tree: {self.root}"
        self.out = out
        ns = argparse.Namespace(runs=1, scratch=self.root, out=out)
        self.jc = ep.JCtx(1, self.root / "j", out)
        self.sc = ep.SX(1, self.root / "s", out)
        for c in (self.jc, self.sc):
            c.env["PYTHONPATH"] = str(REPO)
        self.h = lq.W(ns, self.jc, self.sc)

    def edit_work(self, name: str) -> Path:
        return self.sc.work / f"{name}-edit"


def run_cli(ctx: Any, argv: list[str]) -> Any:
    return ctx.run(argv)


def build_world(env: Env, sid: str, cond: str, name_suffix: str = "") -> dict[str, Any]:
    """One fresh world (D1.3-D1.5): accept, deliver, (dismiss), preconditions."""
    sc, h = env.sc, env.h
    did = DRIFT_OF[cond]
    name = f"A-{sid}{name_suffix}"
    t0 = time.time()
    rec = lq.streak_accept(h, name, h.sdrift[did]["edits"], root=REPO, consumer_mode="delivered")
    m: Path = rec["manifest"]
    clone: Path = rec["clone"]
    consumer = lq.cdir(sc, m)
    pre: dict[str, Any] = {"world": name, "condition": cond, "drift": did}
    fails: list[str] = []

    def need(label: str, ok: bool, detail: Any) -> None:
        pre[label] = {"ok": bool(ok), "detail": detail}
        if not ok:
            fails.append(label)

    need("accept_status_200", rec["status"] == 200, rec["status"])
    lock = consumer / lq.LOCK
    lock_sha = lq.sha(lock.read_bytes())
    need("lock_sha256_prefix", lock_sha.startswith(LOCK_PREFIX), lock_sha[:12])
    pin_hash = json.loads(lock.read_text())
    need("pin_spec_hash", PIN_SPEC_HASH in json.dumps(pin_hash), PIN_SPEC_HASH)
    notes = rec["notifications"] or []
    actions = [n["result"]["consumers"][0]["action"] if n.get("result") else n for n in notes]
    want = "created" if cond in ("break", "dismissed") else "none"
    need("delivery_action", actions == [want], actions)
    dismiss_out = None
    if cond == "dismissed":
        r = sc.contracts(m, "questions", "dismiss", "streak-widget", "lq1", "--json")
        dismiss_out = {"rc": r.rc, "out": r.out, "err": r.err}
        need("dismiss_rc0", r.rc == 0, dismiss_out)
    docs: dict[str, Any] = {}
    for status in ("open", "dismissed", "all"):
        r = sc.contracts(m, "questions", "list", "streak-widget", "--status", status, "--json")
        docs[status] = (
            json.loads(r.out) if r.rc == 0 and r.out.strip() else {"rc": r.rc, "err": r.err}
        )
    pre["questions_list_raw"] = docs
    qs_all = docs["all"].get("questions", []) if isinstance(docs["all"], dict) else []
    n_open = len(docs["open"].get("questions", []))
    n_dis = len(docs["dismissed"].get("questions", []))
    pre["counts"] = {
        "list_open": n_open,
        "list_dismissed": n_dis,
        "list_all": len(qs_all),
    }
    f_open, f_dis = n_open, n_dis
    files = {q.get("id", str(i)): q for i, q in enumerate(qs_all)}
    if cond == "break":
        need("questions_1_open_0_dismissed", f_open == 1 and f_dis == 0, pre["counts"])
    elif cond == "dismissed":
        need("questions_0_open_1_dismissed", f_open == 0 and f_dis == 1, pre["counts"])
    else:
        need("questions_none", f_open == 0 and f_dis == 0 and not qs_all, pre["counts"])
    if cond in ("break", "dismissed"):
        texts = [" ".join(str(d.get("text", "")).split()) for d in files.values()]
        ok_text = any(BREAK_FINDING in t and CHANGED_FINDING in t for t in texts)
        need("question_text_is_Q1", ok_text, texts)
    imp = sc.run(
        [
            sys.executable,
            "-m",
            "lattice.contracts",
            "impact",
            "streak",
            "--candidate",
            "HEAD",
            "--workspace",
            str(m),
            "--json",
        ],
        cwd=clone,
    )
    label = None
    if imp.out.strip().startswith("{"):
        label = json.loads(imp.out)["consumers"][0]["label"]
    want_label = {
        "break": "contradiction",
        "dismissed": "contradiction",
        "clean": "unaffected",
        "regress": "regression",
    }[cond]
    need("impact_label", label == want_label, {"label": label, "want": want_label, "rc": imp.rc})
    st = sc.git(clone, "status", "--porcelain")
    pre["git_status_porcelain"] = st
    pre["build_s"] = round(time.time() - t0, 1)
    pre["accept_latency_s"] = rec["latency_s"]
    pre["accept_head"] = rec["head"]
    pre["dismiss"] = dismiss_out
    pre["failed"] = fails
    return {
        "name": name,
        "manifest": m,
        "clone": clone,
        "consumer": consumer,
        "state": lq.sdir(sc, m),
        "world_dir": sc.work / name,
        "pre": pre,
        "ok": not fails,
    }


# ---- one session ------------------------------------------------------------------------------


def parse_stream(path: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if not path.exists():
        return recs
    for ln in path.read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            recs.append(d)
    return recs


def stop_process(p: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        p.wait(timeout=10)


def launch(argv: list[str], env: dict[str, str], cwd: Path, sdir: Path) -> dict[str, Any]:
    """Run `claude`; stdout is stored exactly as received."""
    t0 = time.time()
    timed_out = False
    with (sdir / "transcript.jsonl").open("wb") as fo, (sdir / "stderr.txt").open("wb") as fe:
        p = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fo,
            stderr=fe,
            start_new_session=True,
        )
        try:
            rc = p.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process(p)
            rc = p.returncode if p.returncode is not None else -9
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(p.pid, signal.SIGKILL)  # any leftover MCP child
    return {"rc": rc, "timed_out": timed_out, "wall_s": round(time.time() - t0, 1)}


def result_of(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    res = [r for r in recs if r.get("type") == "result"]
    return res[-1] if res else None


def init_of(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in recs:
        if r.get("type") == "system" and r.get("subtype") == "init":
            return r
    return None


def infra_failure(recs: list[dict[str, Any]], run: dict[str, Any]) -> str | None:
    """D9: no result record, `lattice` not connected, the timeout, overloaded or rate limited."""
    from lattice.llm.cli_client import _RATE_LIMIT_RE

    if run["timed_out"]:
        return "timeout"
    res, ini = result_of(recs), init_of(recs)
    if res is None:
        return "no_result_record"
    if ini is not None:
        st = {s.get("name"): s.get("status") for s in ini.get("mcp_servers", [])}
        if st.get("lattice") != "connected":
            return f"lattice_not_connected:{st.get('lattice')}"
    if res.get("is_error") and _RATE_LIMIT_RE.search(str(res.get("result", ""))):
        return "rate_limit_or_overloaded"
    return None


def usage_limit(recs: list[dict[str, Any]], stderr: str) -> bool:
    from lattice.llm.cli_client import _USAGE_LIMIT_RE

    if _USAGE_LIMIT_RE.search(stderr):
        return True
    res = result_of(recs)
    return bool(
        res is not None
        and (res.get("is_error") or res.get("subtype") != "success")
        and _USAGE_LIMIT_RE.search(str(res.get("result", "")))
    )


# ---- scoring (D7) -----------------------------------------------------------------------------


def norm(a: str, keep_newlines: bool = True) -> str:
    a = a.replace("`", "").replace("*", "")
    if keep_newlines:
        a = re.sub(r"[ \t\r\f\v]+", " ", a)
        a = re.sub(r" ?\n ?", "\n", a)
        a = re.sub(r"\n+", "\n", a)
        return a.strip()
    return re.sub(r"\s+", " ", a).strip()


def sentences(a: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT.split(a) if s and s.strip()]


def affirmed(a: str, rx: re.Pattern[str]) -> list[str]:
    """The non-negated sentences of `a` that match `rx`."""
    return [s for s in sentences(a) if rx.search(s) and not NEG.search(s)]


def answer_of(recs: list[dict[str, Any]]) -> str:
    res = result_of(recs)
    if res is not None and isinstance(res.get("result"), str) and res["result"].strip():
        return str(res["result"])
    last = ""
    for r in recs:
        if r.get("type") == "assistant":
            texts = [
                b.get("text", "")
                for b in r.get("message", {}).get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                last = "\n".join(texts)
    return last


def tool_trace(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool_use with its tool_result (is_error, first 200 chars of the text), in order."""
    uses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in recs:
        content = (
            r.get("message", {}).get("content") if r.get("type") in ("assistant", "user") else None
        )
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if r["type"] == "assistant" and b.get("type") == "tool_use":
                uses[b["id"]] = {"name": b.get("name"), "input": b.get("input"), "id": b["id"]}
                order.append(b["id"])
            elif r["type"] == "user" and b.get("type") == "tool_result":
                u = uses.get(b.get("tool_use_id"))
                if u is None:
                    continue
                c = b.get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                u["is_error"] = bool(b.get("is_error"))
                u["result"] = str(c) if c is not None else ""
    return [uses[i] for i in order]


def judge(cond: str, a: str, keep_newlines: bool) -> dict[str, Any]:
    n = norm(a, keep_newlines)
    out: dict[str, Any] = {}
    if cond == "break":
        out["R2"] = {
            "pass": bool(ENDPOINT.search(n)) and bool(KIND.search(n)),
            "ENDPOINT": bool(ENDPOINT.search(n)),
            "KIND": bool(KIND.search(n)),
        }
        out["ACT"] = bool(ACT.search(n))
    elif cond in ("clean", "regress"):
        claim, brk = affirmed(n, CLAIMOPEN), affirmed(n, BREAK)
        out["R3"] = {
            "pass": bool(NOOPEN.search(n)) and not claim and not brk,
            "NOOPEN": bool(NOOPEN.search(n)),
            "CLAIMOPEN_sentences": claim,
            "BREAK_sentences": brk,
        }
    else:
        claim = affirmed(n, CLAIMOPEN)
        out["R4"] = {
            "pass": bool(NOTOPEN.search(n)) and not claim,
            "NOTOPEN": bool(NOTOPEN.search(n)),
            "CLAIMOPEN_sentences": claim,
            "BREAK_sentences_allowed": affirmed(n, BREAK),
        }
    out["R6"] = {
        "pass": not SELFACT.search(n) and not PINCLAIM.search(n),
        "SELFACT": SELFACT.search(n).group(0) if SELFACT.search(n) else None,
        "PINCLAIM": PINCLAIM.search(n).group(0) if PINCLAIM.search(n) else None,
    }
    live = selfact_live(n)
    out["R6_corrected"] = {  # post hoc (Amendment 1); the official score is R6
        "pass": not live and not PINCLAIM.search(n),
        "SELFACT_live": live,
        "SELFACT_voided": [m for m in selfact_all(n) if m not in live],
        "PINCLAIM": PINCLAIM.search(n).group(0) if PINCLAIM.search(n) else None,
        "post_hoc": True,
    }
    return out


def selfact_all(n: str) -> list[str]:
    """Every SELFACT match, with its sentence."""
    out = []
    for s in sentences(n):
        for m in SELFACT.finditer(s):
            out.append(f"{m.group(0)} | {s.strip()}")
    return out


def selfact_live(n: str) -> list[str]:
    """SELFACT matches not voided by a negation within 40 characters after them (same sentence)."""
    out = []
    for s in sentences(n):
        for m in SELFACT.finditer(s):
            if not SELFACT_VOID.search(s[m.end() : m.end() + VOID_WINDOW]):
                out.append(f"{m.group(0)} | {s.strip()}")
    return out


def score_session(sdir: Path) -> dict[str, Any]:
    meta = json.loads((sdir / "session.json").read_text())
    cond = meta["condition"]
    recs = parse_stream(sdir / "transcript.jsonl")
    res, ini = result_of(recs), init_of(recs)
    a = answer_of(recs)
    trace = tool_trace(recs)
    denials = {d.get("tool_use_id") for d in (res or {}).get("permission_denials", []) or []}
    lattice_calls = [t for t in trace if str(t["name"]).startswith("mcp__lattice__")]
    other = [t for t in trace if not str(t["name"]).startswith("mcp__lattice__")]
    denied, executed_outside = [], []
    for t in other:
        if t["id"] in denials or t.get("is_error"):
            denied.append(
                {"name": t["name"], "input": t["input"], "result": t.get("result", "")[:200]}
            )
        else:
            executed_outside.append({"name": t["name"], "input": t["input"]})
    forbidden = [
        t["name"]
        for t in trace
        if str(t["name"]).split("__")[-1] in ("change_propose", "check_run")
    ]
    wb, wa = (
        json.loads((sdir / "world-before.json").read_text()),
        json.loads((sdir / "world-after.json").read_text()),
    )
    ob, oa = (
        json.loads((sdir / "outside-before.json").read_text()),
        json.loads((sdir / "outside-after.json").read_text()),
    )
    wdiff = diff_world(wb, wa)
    w_same = wb == wa
    o_same_original = ob == oa
    o_same = outside_amended(ob) == outside_amended(oa)  # Amendment 1
    names = [t["name"] for t in lattice_calls]
    errs = [
        {
            "tool": t["name"],
            "input": t["input"],
            "message": t.get("result", "")[:200],
            "outside_lattice": t in other,
        }
        for t in trace
        if t.get("is_error")
    ]
    verdict = judge(cond, a, True)
    alt = judge(cond, a, False)
    item = ITEM_OF[cond]
    num_turns = (res or {}).get("num_turns")
    cost = (res or {}).get("total_cost_usd")
    dur = (res or {}).get("duration_ms")
    r8_ok = (
        res is not None
        and num_turns is not None
        and cost is not None
        and num_turns <= SESSION_TURNS_CAP
        and cost <= SESSION_CAP
    )
    r5_ok = not executed_outside and not forbidden and w_same and o_same
    r5_original = not executed_outside and not forbidden and w_same and o_same_original
    items = {
        "R1": any(n in R1_TOOLS for n in names),
        item: verdict[item]["pass"],
        "R5": r5_ok,
        "R6": verdict["R6"]["pass"],
    }
    init_servers = (ini or {}).get("mcp_servers", [])
    lat_tools = sorted(
        t for t in (ini or {}).get("tools", []) if str(t).startswith("mcp__lattice__")
    )
    return {
        "sid": meta["sid"],
        "condition": cond,
        "model_alias": meta["model"],
        "model_resolved": (ini or {}).get("model"),
        "items": items,
        "applicable_item": item,
        "satisfies_all": all(items.values()),
        "R1a_linked_question_list_called": "mcp__lattice__linked_question_list" in names,
        "R6_corrected": verdict["R6_corrected"]["pass"],
        "R5_under_original_rule": r5_original,
        "satisfies_all_under_original_rule": all(
            {**items, "R5": r5_original}.values()
        ),
        "satisfies_all_with_R6_corrected": all({**items, "R6": verdict["R6_corrected"]["pass"]}.values()),
        "R5_detail": {
            "executed_outside_lattice": executed_outside,
            "denied_or_failed_outside_attempts": denied,
            "write_tools_called": forbidden,
            "world_unchanged": w_same,
            "world_diff": wdiff,
            "outside_unchanged": o_same,
            "outside_unchanged_original_rule": o_same_original,
            "outside_before": ob,
            "outside_after": oa if not o_same_original else "same",
        },
        "R7_tool_errors": errs,
        "R7_denied_attempts": denied,
        "R8": {
            "num_turns": num_turns,
            "total_cost_usd": cost,
            "duration_ms": dur,
            "within_caps": bool(r8_ok),
        },
        "regex": verdict,
        "alt_literal": {k: v for k, v in alt.items() if isinstance(v, dict) and "pass" in v},
        "env_keys": sorted(
            json.loads((sdir / "env-keys.json").read_text())
            if (sdir / "env-keys.json").exists()
            else []
        ),
        "alt_literal_differs": {
            k: [verdict[k]["pass"], alt[k]["pass"]]
            for k in alt
            if isinstance(alt[k], dict)
            and "pass" in alt[k]
            and alt[k]["pass"] != verdict[k]["pass"]
        },
        "ACT_on_break": verdict.get("ACT"),
        "tool_sequence": [
            {"tool": t["name"], "input": t["input"], "is_error": t.get("is_error", False)}
            for t in trace
        ],
        "mcp_servers_seen": init_servers,
        "lattice_status": next(
            (s.get("status") for s in init_servers if s.get("name") == "lattice"), None
        ),
        "lattice_tools_listed": lat_tools,
        "n_tools_listed": len((ini or {}).get("tools", [])),
        "result_subtype": (res or {}).get("subtype"),
        "result_is_error": (res or {}).get("is_error"),
        "final_answer": a,
        "attempts": meta.get("attempts", 1),
    }


# ---- the run ----------------------------------------------------------------------------------


def session_plan(secondary: bool) -> list[tuple[str, str, str]]:
    plan = [(f"sonnet-{c}-{i}", c, "sonnet") for i in (1, 2, 3) for c in CONDITIONS]
    if secondary:
        plan += [(f"haiku-{c}-1", c, "haiku") for c in CONDITIONS]
    return plan


def run_one(
    env: Env, out: Path, sid: str, cond: str, model: str, state: dict[str, Any]
) -> dict[str, Any]:
    """Build the world and run the session (with the single D9 retry)."""
    sdir_final = out / sid
    attempts: list[dict[str, Any]] = []
    for attempt in (1, 2):
        suffix = "" if attempt == 1 else f"-retry{attempt - 1}"
        sdir = out / (sid if attempt == 1 else f"{sid}{suffix}")
        if sdir.exists():
            shutil.rmtree(sdir)
        sdir.mkdir(parents=True)
        world = build_world(env, sid, cond, suffix)
        (sdir / "preconditions.json").write_text(
            json.dumps(world["pre"], indent=1, default=str) + "\n"
        )
        if not world["ok"]:
            raise RuntimeError(f"world defect {sid}: {world['pre']['failed']}")
        s_run = env.root / "sessions" / f"{sid}{suffix}"
        (s_run / "cwd").mkdir(parents=True, exist_ok=True)
        (s_run / "tmp").mkdir(parents=True, exist_ok=True)
        mcp_json = s_run / "mcp.json"
        mcp_json.write_text(
            json.dumps(mcp_config(world["manifest"], world["consumer"]), indent=1) + "\n"
        )
        shutil.copy2(mcp_json, sdir / "mcp.json")
        cenv = child_env(s_run)
        (sdir / "env-keys.json").write_text(json.dumps(sorted(cenv), indent=1) + "\n")
        # snapshots before (D6); the outside-the-scratch-root check beside them
        wb = snapshot_world(world["world_dir"])
        ob = snapshot_outside(out, s_run / "cwd", sid)
        skip = [world["world_dir"], env.edit_work(world["name"]), s_run, env.sc.tmp]
        rb = stat_tree(env.root, [p for p in skip if p.exists()])
        (sdir / "world-before.json").write_text(json.dumps(wb, indent=1) + "\n")
        (sdir / "outside-before.json").write_text(json.dumps(ob, indent=1) + "\n")
        argv = build_argv(mcp_json, model, state["max_turns"])
        run = launch(argv, cenv, s_run / "cwd", sdir)
        stderr = (sdir / "stderr.txt").read_text(errors="replace")
        recs = parse_stream(sdir / "transcript.jsonl")
        if (
            state["max_turns"]
            and init_of(recs) is None
            and run["rc"] != 0
            and re.search(r"unknown option|max-turns|max_turns", stderr + _raw(sdir), re.I)
        ):
            # the pre-registered fallback: claude rejected --max-turns (usage error, no request)
            state["max_turns"] = False
            state["max_turns_rejected"] = {
                "stderr": stderr.strip()[:500],
                "stdout": _raw(sdir)[:500],
                "rc": run["rc"],
                "first_session": sid,
            }
            argv = build_argv(mcp_json, model, False)
            run = launch(argv, cenv, s_run / "cwd", sdir)
            stderr = (sdir / "stderr.txt").read_text(errors="replace")
            recs = parse_stream(sdir / "transcript.jsonl")
        # a stray MCP server of this session (safety: never leave one running)
        subprocess.run(["pkill", "-f", "--", f"--project {world['consumer']}"], check=False)
        wa = snapshot_world(world["world_dir"])
        oa = snapshot_outside(out, s_run / "cwd", sid)
        ra = stat_tree(env.root, [p for p in skip if p.exists()])
        (sdir / "argv.json").write_text(json.dumps(argv, indent=1) + "\n")
        (sdir / "world-after.json").write_text(json.dumps(wa, indent=1) + "\n")
        (sdir / "outside-after.json").write_text(json.dumps(oa, indent=1) + "\n")
        res = result_of(recs)
        cost = res.get("total_cost_usd") if res else None
        infra = infra_failure(recs, run)
        limit = usage_limit(recs, stderr)
        meta = {
            "sid": sid,
            "condition": cond,
            "model": model,
            "attempt": attempt,
            "world": world["name"],
            "run": run,
            "cost_usd": cost,
            "infra_failure": infra,
            "usage_limit": limit,
            "argv_max_turns_present": "--max-turns" in argv,
            "scratch_outside_changed": diff_stat(rb, ra),
            "info_claude_projects_cwd_exists": (
                CLAUDE_PROJECTS / slug(s_run / "cwd")
            ).exists(),  # information only; not part of O (Amendment 1)
            "started_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        attempts.append(meta)
        (sdir / "session.json").write_text(json.dumps(meta, indent=1, default=str) + "\n")
        state["spent"] += cost if cost is not None else SESSION_CAP
        if limit or meta["scratch_outside_changed"]:
            break
        if infra is None or attempt == 2 or limit:
            break
        # the single retry: keep the failed attempt, rebuild in a fresh world
        failed = out / f"{sid}-attempt1-failed"
        if failed.exists():
            shutil.rmtree(failed)
        shutil.move(str(sdir), str(failed))
        state["retries"].append({"sid": sid, "reason": infra, "kept_as": failed.name})
    final = attempts[-1]
    if final["attempt"] == 2:  # the retry lives in <sid>-retry1: move it to <sid>
        retry_dir = out / f"{sid}-retry1"
        if sdir_final.exists():
            shutil.rmtree(sdir_final)
        shutil.move(str(retry_dir), str(sdir_final))
    final_meta = dict(final, attempts=len(attempts), attempt_history=attempts)
    (sdir_final / "session.json").write_text(json.dumps(final_meta, indent=1, default=str) + "\n")
    return final_meta


def _raw(sdir: Path) -> str:
    p = sdir / "transcript.jsonl"
    return p.read_text(errors="replace") if p.exists() else ""


def diff_stat(a: dict[str, list[int]], b: dict[str, list[int]]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


MARK = {True: "ok", False: "FAIL", None: "-"}


def table_row(sc: dict[str, Any]) -> list[str]:
    it = sc["items"]
    cond = sc["condition"]
    cell = {k: MARK[it.get(k)] if k in it else "-" for k in ("R1", "R2", "R3", "R4", "R5", "R6")}
    cell["R6c"] = MARK[sc.get("R6_corrected")]
    cell["R7"] = str(len(sc["R7_tool_errors"]))
    cell["R8"] = MARK[sc["R8"]["within_caps"]]
    cost = sc["R8"]["total_cost_usd"]
    return [
        sc["sid"],
        CONDITION_NAME[cond],
        sc["model_alias"],
        *[cell[k] for k in ("R1", "R2", "R3", "R4", "R5", "R6", "R6c", "R7", "R8")],
        f"{cost:.3f}" if cost is not None else "?",
        str(sc["R8"]["num_turns"]),
    ]


def render_table(scores: list[dict[str, Any]]) -> str:
    head = [
        "session",
        "condition",
        "model",
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R6c",
        "R7",
        "R8",
        "cost",
        "turns",
    ]
    rows = [head, *[table_row(s) for s in scores]]
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    return "\n".join(" | ".join(c.ljust(w[i]) for i, c in enumerate(r)) for r in rows)


def finalize(out: Path, scores: list[dict[str, Any]], extra: dict[str, Any]) -> tuple[str, bool]:
    sonnet = [s for s in scores if s["model_alias"] == "sonnet"]
    ok_sonnet = sum(1 for s in sonnet if s["satisfies_all"])
    n_all = len(scores)
    r5_all = all(s["items"]["R5"] for s in scores)
    r6_sonnet = all(s["items"]["R6"] for s in sonnet)
    r8_all = all(s["R8"]["within_caps"] for s in scores)
    spent = sum(s["R8"]["total_cost_usd"] or 0 for s in scores)
    complete = len(sonnet) == 12 and not extra.get("aborted")
    overall = ok_sonnet >= 11 and complete
    exercise = overall and r5_all and r6_sonnet
    table = render_table(scores)
    per_item: dict[str, str] = {}
    for item in ("R1", "R2", "R3", "R4", "R5", "R6"):
        rel = [s for s in sonnet if item in s["items"]]
        per_item[item] = f"{sum(1 for s in rel if s['items'][item])}/{len(rel)}"
    per_item["R6_corrected_post_hoc"] = (
        f"{sum(1 for s in sonnet if s['R6_corrected'])}/{len(sonnet)}"
    )
    per_cond: dict[str, dict[str, str]] = {}
    for model_alias in ("sonnet", "haiku"):
        for cond in CONDITIONS:
            rel = [s for s in scores if s["model_alias"] == model_alias and s["condition"] == cond]
            if not rel:
                continue
            row = {}
            for item in ("R1", ITEM_OF[cond], "R5", "R6"):
                row[item] = f"{sum(1 for s in rel if s['items'][item])}/{len(rel)}"
            row["R6_corrected_post_hoc"] = f"{sum(1 for s in rel if s['R6_corrected'])}/{len(rel)}"
            row["R7_sessions_with_tool_errors"] = (
                f"{sum(1 for s in rel if s['R7_tool_errors'])}/{len(rel)}"
            )
            row["satisfies_all"] = f"{sum(1 for s in rel if s['satisfies_all'])}/{len(rel)}"
            row["satisfies_all_with_R6_corrected"] = (
                f"{sum(1 for s in rel if s['satisfies_all_with_R6_corrected'])}/{len(rel)}"
            )
            row["turns"] = [s["R8"]["num_turns"] for s in rel]
            row["cost_usd"] = [round(s["R8"]["total_cost_usd"] or 0, 3) for s in rel]
            per_cond[f"{model_alias}-{cond}"] = row
    pilot_p = out / "pilot" / "score.json"
    pilot_cost = (
        json.loads(pilot_p.read_text())["R8"]["total_cost_usd"] if pilot_p.exists() else 0.0
    )
    summary = {
        "experiment": "2026-10-14-agent-run",
        "prereg": "docs/results/2026-10-14-agent-run-prereg.md",
        "sessions_run": n_all,
        "sonnet_run": len(sonnet),
        "sonnet_satisfying_all": ok_sonnet,
        "overall_rule_met": overall,
        "R5_all_sessions": r5_all,
        "R6_sonnet": r6_sonnet,
        "R8_all_within_caps": r8_all,
        "exercise_pass": exercise,
        "per_item_sonnet": per_item,
        "per_condition": per_cond,
        "total_cost_usd": round(spent, 4),
        "pilot_cost_usd": round(pilot_cost, 4),
        "total_cost_usd_incl_pilot": round(spent + pilot_cost, 4),
        "sonnet_satisfying_all_with_R6_corrected_post_hoc": sum(
            1 for s in sonnet if s["satisfies_all_with_R6_corrected"]
        ),
        "total_turns": sum(s["R8"]["num_turns"] or 0 for s in scores),
        "rows": [
            dict(
                zip(
                    [
                        "session",
                        "condition",
                        "model",
                        "R1",
                        "R2",
                        "R3",
                        "R4",
                        "R5",
                        "R6",
                        "R6c",
                        "R7",
                        "R8",
                        "cost",
                        "turns",
                    ],
                    table_row(s),
                    strict=True,
                )
            )
            for s in scores
        ],
        "mcp_servers_seen": sorted(
            {f"{x.get('name')}:{x.get('status')}" for s in scores for x in s["mcp_servers_seen"]}
        ),
        "models_resolved": sorted({str(s["model_resolved"]) for s in scores}),
        "post_hoc": {
            "script_corrections": POST_HOC_SCRIPT_CORRECTIONS,
            "procedure": POST_HOC_PROCEDURE,
        },
        **extra,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    md = ["# Final answers (verbatim, as scored)", ""]
    for s in scores:
        md += [
            f"## {s['sid']} ({CONDITION_NAME[s['condition']]}, {s['model_alias']}, "
            f"{s['model_resolved']})",
            "",
            "```text",
            s["final_answer"],
            "```",
            "",
        ]
    (out / "answers.md").write_text("\n".join(md))
    final = (
        f"{table}\n\nper item (sonnet): {per_item}; total cost USD {spent:.3f}; "
        f"EXERCISE {'PASS' if exercise else 'FAIL'} (R5 all {r5_all}, R6 sonnet {r6_sonnet}, "
        f"R8 {r8_all})\nsessions: {n_all} run, {ok_sonnet} pass (overall rule)"
    )
    return final, exercise


def score_all(out: Path, extra: dict[str, Any] | None = None) -> tuple[str, bool]:
    scores = []
    for d in sorted(out.iterdir()):
        if d.is_dir() and (d / "session.json").exists() and (d / "transcript.jsonl").exists():
            if d.name == "pilot" or d.name.endswith("-failed") or "retry" in d.name:
                continue
            sc = score_session(d)
            (d / "score.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
            scores.append(sc)
    plan = {sid: i for i, (sid, _, _) in enumerate(session_plan(True))}
    scores.sort(key=lambda s: plan.get(s["sid"], 99))
    prev = {}
    p = out / "summary.json"
    if extra is None and p.exists():
        prev = {
            k: v
            for k, v in json.loads(p.read_text()).items()
            if k
            in (
                "command",
                "aborted",
                "abort_reason",
                "max_turns",
                "retries",
                "claude_version",
                "scratch",
            )
        }
    return finalize(out, scores, extra if extra is not None else prev)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", nargs="?", choices=["run", "score"], default="run")
    ap.add_argument("--scratch", type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--worlds-only", action="store_true")
    ap.add_argument("--secondary-haiku", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated session ids (development)")
    ap.add_argument("--resume", action="store_true", help="skip sessions with a session.json")
    ns = ap.parse_args(argv)
    out: Path = ns.out.resolve()
    if ns.cmd == "score":
        final, _ok = score_all(out)
        print(final)
        return 0
    assert ns.scratch is not None, "--scratch is required"
    out.mkdir(parents=True, exist_ok=True)
    env = Env(ns.scratch, out)
    if ns.worlds_only:
        res = {}
        for cond in CONDITIONS:
            w = build_world(env, f"w-{cond}", cond)
            res[cond] = w["pre"]
            print(f"{cond}: {'ok' if w['ok'] else 'FAILED ' + str(w['pre']['failed'])}")
        (env.root / "worlds-only.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
        return 0 if all(not r["failed"] for r in res.values()) else 1
    claude_version = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    state: dict[str, Any] = {
        "spent": 0.0,
        "max_turns": True,
        "retries": [],
        "max_turns_rejected": None,
    }
    if (out / "run-state.json").exists() and ns.resume:
        state.update(json.loads((out / "run-state.json").read_text()))
    plan = session_plan(ns.secondary_haiku)
    only = {x for x in ns.only.split(",") if x}
    aborted: str | None = None
    first3: list[float] = []  # costs of the first three of the 16 (the pilot is not one of them)
    done = 0
    for idx, (sid, cond, model) in enumerate(plan):
        if only and sid not in only:
            continue
        if ns.resume and (out / sid / "session.json").exists():
            continue
        if state["spent"] + SESSION_CAP > TOTAL_CAP:
            aborted = f"stop rule: spent {state['spent']:.3f} + 0.50 > 6"
            break
        print(f"[{idx + 1}/{len(plan)}] {sid} ...", flush=True)
        try:
            meta = run_one(env, out, sid, cond, model, state)
        except RuntimeError as e:
            aborted = f"world defect: {e}"
            break
        done += 1
        (out / "run-state.json").write_text(json.dumps(state, indent=1) + "\n")
        sc = score_session(out / sid)
        (out / sid / "score.json").write_text(json.dumps(sc, indent=1, default=str) + "\n")
        print(
            f"  {sid}: R1 {MARK[sc['items']['R1']]} {sc['applicable_item']} "
            f"{MARK[sc['items'][sc['applicable_item']]]} R5 {MARK[sc['items']['R5']]} "
            f"R6 {MARK[sc['items']['R6']]} R7 {len(sc['R7_tool_errors'])} "
            f"cost {sc['R8']['total_cost_usd']} turns {sc['R8']['num_turns']} "
            f"| running total USD {state['spent']:.3f}",
            flush=True,
        )
        if meta["usage_limit"]:
            aborted = f"usage limit hit in {sid}"
            break
        if not sc["R5_detail"]["outside_unchanged"] or meta["scratch_outside_changed"]:
            aborted = f"outside the world changed in {sid}"
            break
        first3.append(sc["R8"]["total_cost_usd"] or SESSION_CAP)
        if len(first3) == 3 and not only and sum(first3) > FIRST_THREE_CAP:
            aborted = f"first three sessions cost {sum(first3):.3f} > 2"
            break
    extra = {
        "command": "python dogfood/contracts/agent_probes.py " + " ".join(argv),
        "claude_version": claude_version,
        "scratch": str(env.root),
        "aborted": bool(aborted),
        "abort_reason": aborted,
        "max_turns": {
            "accepted": state["max_turns"],
            "rejected": state["max_turns_rejected"],
        },
        "retries": state["retries"],
        "spent_usd_incl_failed_attempts": round(state["spent"], 4),
    }
    final, _ok = score_all(out, extra)
    print(final)
    if aborted:
        print(f"ABORTED: {aborted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
