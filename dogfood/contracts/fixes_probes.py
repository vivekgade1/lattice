"""Byte-compatibility probe of the five first-contact fixes (G9, cases B1-B6).

Implements section 2.6 of ``docs/results/2026-10-14-agent-run-3-prereg.md``: the MCP server of
the working tree (``lattice/``) and of the old code (``git archive 2407eaf lattice``) are both
served over stdio (``python -m lattice.mcp serve``) from this interpreter, with ``PYTHONPATH``
set to each tree, and the JSON-RPC responses are compared byte for byte.

    mkdir -p $SCRATCH/old-2407eaf && git archive 2407eaf lattice | tar -x -C $SCRATCH/old-2407eaf
    python dogfood/contracts/fixes_probes.py --old $SCRATCH/old-2407eaf --scratch $SCRATCH/b \
        [--out DIR] [--only B1,B2,...]

B1  one ``--project`` (a copy of dogfood/studio), no workspace, with and without ``--readonly``:
    ``initialize`` and ``tools/list`` identical.
B2  a project directory without a spec and without imports: also the ``model_get`` error identical.
B3  the same with ``lattice.imports.toml`` (and with the lock file): error = old + F5's sentence.
B4  a one-project workspace: ``initialize`` identical; ``tools/list`` differs ONLY in
    contract_check's and linked_question_list's ``description`` (the ``consumer`` clause) and
    linked_question_list's ``required``.
B5  the round-3 shape (provider + consumer, ``--project`` consumer, ``--readonly``): instructions
    = old + WORKSPACE_NOTE + READONLY_NOTE; ``tools/list`` same names, order and property names,
    B4's differences only.
B6  two ``--project``, no workspace: identical (+ READONLY_NOTE with ``--readonly``).

The expected texts below are copied from the pre-registration, not imported from the code under
test. No LLM and no API key are used. Exit 0 when every case passes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

WORKSPACE_NOTE = (
    " A workspace manifest links these projects; project_list shows each one's role and links. "
    'For "has anything a provider changed affected this project?": call project_list, then BOTH '
    "contract_check and linked_question_list for the consumer: contract_check shows what changed "
    "since its pin (BROKEN, CHANGED), linked_question_list the notes a provider left when an "
    "accepted change broke it (open, resolved or dismissed). Both take `consumer` and default to "
    "the active project when it is a consumer. workspace_impact lists who relies on a project; "
    "contract_impact previews a provider's candidate version against its consumers. These five "
    "tools are read-only."
)
READONLY_NOTE = (
    " This server is read-only: it has no tool that proposes a spec change or runs the checks; "
    "tell the owner what to change."
)
F5_FILES = ("lattice.imports.toml", "lattice.contracts.lock.json")


def f5_sentence(files: str) -> str:
    return (
        f" This directory is a contract consumer (it holds {files}), which needs no spec for its "
        "contracts: with a workspace, contract_check and linked_question_list show what its "
        "providers changed and the notes they left (otherwise `lattice contracts check` and "
        "`lattice contracts questions list` with --workspace)."
    )


CC_OLD = "`consumer`: a project name (default: every consumer with an opted-in link). "
CC_NEW = (
    "`consumer`: a project name (default: the active project when it is a consumer with an "
    "opted-in link, else every consumer with one). "
)
LQ_OLD = "`consumer`: a workspace project name; `status`"
LQ_NEW = (
    "`consumer`: a workspace project name (default: the active project when it is a consumer "
    "with an opted-in link); `status`"
)

INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "b"}},
}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
MODEL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "model_get", "arguments": {}},
}


class Server:
    """One tree's MCP server answers to one message list (raw stdout lines by response id)."""

    def __init__(self, tree: Path, label: str) -> None:
        self.tree, self.label = tree, label

    def run(self, cwd: Path, args: list[str], msgs: list[dict[str, Any]]) -> dict[int, str]:
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE_CODE"))
        }
        env.pop("LATTICE_WORKSPACE", None)
        env["PYTHONPATH"] = str(self.tree)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["LATTICE_PROJECTS_REGISTRY"] = str(cwd / "no-registry.toml")
        r = subprocess.run(
            [sys.executable, "-m", "lattice.mcp", "serve", *args],
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            cwd=cwd,
            env=env,
        )
        if r.returncode != 0:
            raise RuntimeError(f"{self.label} server exit {r.returncode}: {r.stderr[-400:]}")
        out: dict[int, str] = {}
        for line in r.stdout.splitlines():
            if line.strip():
                out[json.loads(line)["id"]] = line
        return out

    def where(self) -> str:
        env = {**os.environ, "PYTHONPATH": str(self.tree), "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run(
            [sys.executable, "-c", "import lattice; print(lattice.__file__)"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=self.tree,
        )
        return r.stdout.strip()


def leaf_diff(a: Any, b: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """Every path at which two JSON values differ (dict keys, list items, leaves)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[tuple[str, Any, Any]] = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}/{k}", a.get(k, "<absent>"), b.get(k, "<absent>")))
            else:
                out += leaf_diff(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [(path, f"list of {len(a)}", f"list of {len(b)}")]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += leaf_diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [(path, a, b)]


def tools_of(line: str) -> list[dict[str, Any]]:
    return json.loads(line)["result"]["tools"]  # type: ignore[no-any-return]


def instructions_of(line: str) -> str:
    return json.loads(line)["result"]["instructions"]  # type: ignore[no-any-return]


def tool_error_text(line: str) -> str:
    return json.loads(line)["result"]["content"][0]["text"]  # type: ignore[no-any-return]


class Case:
    def __init__(self, cid: str, title: str) -> None:
        self.id, self.title = cid, title
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: Any = None) -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail if not ok else None})

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)


def allowed_tools_diff(
    case: Case, old_tools: list[dict[str, Any]], new_tools: list[dict[str, Any]], tag: str
) -> None:
    """`tools/list` differs only as B4 allows: names, order, property names equal; the two
    descriptions' consumer clause and linked_question_list's `required`."""
    case.check(f"{tag}: same tool names, same order", [t["name"] for t in old_tools]
               == [t["name"] for t in new_tools])  # fmt: skip
    case.check(
        f"{tag}: same property names per tool",
        [list(t["inputSchema"]["properties"]) for t in old_tools]
        == [list(t["inputSchema"]["properties"]) for t in new_tools],
    )
    diffs = []
    for o, n in zip(old_tools, new_tools, strict=False):
        for path, a, b in leaf_diff(o, n):
            diffs.append((o["name"], path, a, b))
    paths = sorted((t, p) for t, p, _a, _b in diffs)
    want = sorted([
        ("contract_check", "/description"),
        ("linked_question_list", "/description"),
        ("linked_question_list", "/inputSchema/required"),
    ])  # fmt: skip
    case.check(f"{tag}: the only differences are the three allowed", paths == want, paths)
    by = {(t, p): (a, b) for t, p, a, b in diffs}
    cc = by.get(("contract_check", "/description"))
    lq = by.get(("linked_question_list", "/description"))
    olq = next((t for t in old_tools if t["name"] == "linked_question_list"), {})
    nlq = next((t for t in new_tools if t["name"] == "linked_question_list"), {})
    rq = (
        olq.get("inputSchema", {}).get("required"),
        nlq.get("inputSchema", {}).get("required"),
    )
    case.check(
        f"{tag}: contract_check description = old with only the consumer clause replaced",
        cc is not None and CC_OLD in cc[0] and cc[1] == cc[0].replace(CC_OLD, CC_NEW),
        cc,
    )
    case.check(
        f"{tag}: linked_question_list description = old with only the consumer clause replaced",
        lq is not None and LQ_OLD in lq[0] and lq[1] == lq[0].replace(LQ_OLD, LQ_NEW),
        lq,
    )
    case.check(
        f"{tag}: linked_question_list required ['consumer'] -> []",
        rq == (["consumer"], []),
        rq,
    )


def copy_project(dst: Path) -> Path:
    shutil.copytree(
        REPO / "dogfood" / "studio",
        dst,
        ignore=shutil.ignore_patterns("__pycache__", ".lattice", ".git", "node_modules"),
    )
    return dst


def fresh(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def identical(
    case: Case, tag: str, old: dict[int, str], new: dict[int, str], ids: tuple[int, ...]
) -> None:
    for i in ids:
        case.check(f"{tag}: response id {i} byte-identical", old.get(i) == new.get(i), None)


def b1(old: Server, new: Server, scratch: Path) -> Case:
    c = Case("B1", "one --project, no workspace: initialize and tools/list identical")
    root = fresh(scratch / "b1")
    proj = copy_project(root / "studio")
    for ro in (False, True):
        args = ["--project", str(proj), *(["--readonly"] if ro else [])]
        o, n = old.run(root, args, [INIT, LIST]), new.run(root, args, [INIT, LIST])
        tag = "readonly" if ro else "writable"
        identical(c, tag, o, n, (0, 1))
        c.check(
            f"{tag}: instructions are the base", "Several projects" not in instructions_of(n[0])
        )
    return c


def b2(old: Server, new: Server, scratch: Path) -> Case:
    c = Case("B2", "a spec-less project without imports: also the model_get error identical")
    root = fresh(scratch / "b2")
    proj = root / "bare"
    proj.mkdir()
    for ro in (False, True):
        args = ["--project", str(proj), *(["--readonly"] if ro else [])]
        o = old.run(root, args, [INIT, LIST, MODEL])
        n = new.run(root, args, [INIT, LIST, MODEL])
        tag = "readonly" if ro else "writable"
        identical(c, tag, o, n, (0, 1, 2))
        c.check(f"{tag}: the error is the not-a-project text", "not a Lattice project yet" in o[2])
    return c


def b3(old: Server, new: Server, scratch: Path) -> Case:
    c = Case("B3", "a spec-less contract consumer: error = old + F5's sentence")
    for files in (F5_FILES[:1], F5_FILES[1:], F5_FILES):
        root = fresh(
            scratch / ("b3-" + "-".join(str(i) for i, f in enumerate(F5_FILES) if f in files))
        )
        proj = root / "cons"
        proj.mkdir()
        for f in files:
            (proj / f).write_text("")
        args = ["--project", str(proj)]
        o = old.run(root, args, [INIT, LIST, MODEL])
        n = new.run(root, args, [INIT, LIST, MODEL])
        tag = " and ".join(files)
        identical(c, tag, o, n, (0, 1))
        old_text, new_text = tool_error_text(o[2]), tool_error_text(n[2])
        c.check(f"{tag}: error = old + sentence", new_text == old_text + f5_sentence(tag), new_text)
        c.check(
            f"{tag}: both are tool errors", '"isError":true' in o[2] and '"isError":true' in n[2]
        )
    return c


def manifest(root: Path, names: list[str], link: tuple[str, str] | None = None) -> Path:
    text = '[workspace]\nname = "fx"\n'
    for n in names:
        text += f'\n[[project]]\nname = "{n}"\npath = "{n}"\n'
    if link:
        text += (
            f'\n[[link]]\nfrom = "{link[0]}"\nto = "{link[1]}"\nkind = "uses"\n'
            'contract = "static"\n'
        )
    path = root / "lattice.workspace.toml"
    path.write_text(text)
    return path


def b4(old: Server, new: Server, scratch: Path) -> Case:
    c = Case(
        "B4", "a one-project workspace: initialize identical, tools/list only the 3 differences"
    )
    root = fresh(scratch / "b4")
    copy_project(root / "solo")
    m = manifest(root, ["solo"])
    for ro in (False, True):
        args = [
            "--project",
            str(root / "solo"),
            "--workspace",
            str(m),
            *(["--readonly"] if ro else []),
        ]
        o, n = old.run(root, args, [INIT, LIST]), new.run(root, args, [INIT, LIST])
        tag = "readonly" if ro else "writable"
        identical(c, tag, o, n, (0,))
        names = [t["name"] for t in tools_of(n[1])]
        c.check(f"{tag}: the workspace tools are listed", "contract_check" in names)
        c.check(
            f"{tag}: instructions are the base", "workspace manifest" not in instructions_of(n[0])
        )
        allowed_tools_diff(c, tools_of(o[1]), tools_of(n[1]), tag)
    return c


def b5(old: Server, new: Server, scratch: Path) -> Case:
    c = Case("B5", "round-3 shape: provider + consumer, --project consumer, --readonly")
    root = fresh(scratch / "b5")
    copy_project(root / "prov")
    copy_project(root / "cons")
    m = manifest(root, ["prov", "cons"], ("cons", "prov"))
    args = ["--project", str(root / "cons"), "--workspace", str(m), "--readonly"]
    o, n = old.run(root, args, [INIT, LIST]), new.run(root, args, [INIT, LIST])
    oi, ni = instructions_of(o[0]), instructions_of(n[0])
    c.check("old instructions end with the multi-project note", "Several projects are known" in oi)
    c.check(
        "instructions = old + WORKSPACE_NOTE + READONLY_NOTE",
        ni == oi + WORKSPACE_NOTE + READONLY_NOTE,
        ni[len(oi) :],
    )
    ot, nt = json.loads(o[0]), json.loads(n[0])
    ot["result"]["instructions"] = nt["result"]["instructions"] = ""
    c.check("initialize otherwise identical", ot == nt)
    allowed_tools_diff(c, tools_of(o[1]), tools_of(n[1]), "readonly")
    named = ni[len(oi) :]
    import re

    listed = {t["name"] for t in tools_of(n[1])}
    words = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", named))
    c.check("the appended text names only listed tools", words <= listed, sorted(words - listed))
    return c


def b6(old: Server, new: Server, scratch: Path) -> Case:
    c = Case("B6", "two --project, no workspace: identical (+ READONLY_NOTE with --readonly)")
    root = fresh(scratch / "b6")
    copy_project(root / "one")
    copy_project(root / "two")
    base_args = ["--project", str(root / "one"), "--project", str(root / "two")]
    o, n = old.run(root, base_args, [INIT, LIST]), new.run(root, base_args, [INIT, LIST])
    identical(c, "writable", o, n, (0, 1))
    args = [*base_args, "--readonly"]
    o, n = old.run(root, args, [INIT, LIST]), new.run(root, args, [INIT, LIST])
    identical(c, "readonly", o, n, (1,))
    oi, ni = instructions_of(o[0]), instructions_of(n[0])
    c.check("readonly: instructions = old + READONLY_NOTE", ni == oi + READONLY_NOTE, ni[len(oi) :])
    ot, nt = json.loads(o[0]), json.loads(n[0])
    ot["result"]["instructions"] = nt["result"]["instructions"] = ""
    c.check("readonly: initialize otherwise identical", ot == nt)
    return c


CASES = {"B1": b1, "B2": b2, "B3": b3, "B4": b4, "B5": b5, "B6": b6}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--old", type=Path, required=True, help="dir holding `lattice/` of 2407eaf")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ns = ap.parse_args(argv)
    old_tree, scratch = ns.old.resolve(), ns.scratch.resolve()
    assert (old_tree / "lattice" / "mcp" / "projects.py").is_file(), "--old holds no lattice/"
    old, new = Server(old_tree, "old"), Server(REPO, "new")
    print(f"old: {old.where()}\nnew: {new.where()}")
    only = {x for x in ns.only.split(",") if x}
    results = []
    for cid, fn in CASES.items():
        if only and cid not in only:
            continue
        case = fn(old, new, scratch)
        results.append(case)
        print(f"{case.id} {'PASS' if case.ok else 'FAIL'}  {case.title}")
        for ck in case.checks:
            print(f"   {'ok  ' if ck['ok'] else 'FAIL'} {ck['check']}")
            if not ck["ok"]:
                print(f"        {json.dumps(ck['detail'], default=str)[:1200]}")
    ok = all(c.ok for c in results)
    print(f"cases: {sum(c.ok for c in results)}/{len(results)} pass")
    if ns.out is not None:
        ns.out.mkdir(parents=True, exist_ok=True)
        doc = {
            "old": old.where(),
            "new": new.where(),
            "cases": [
                {"id": c.id, "title": c.title, "ok": c.ok, "checks": c.checks} for c in results
            ],
            "ok": ok,
        }
        (ns.out / "fixes-probes.json").write_text(json.dumps(doc, indent=1, default=str) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
