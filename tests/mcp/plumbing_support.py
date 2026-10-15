"""Shared helpers of the multi-project plumbing tests and probes (tests/mcp/test_plumbing_*.py,
dogfood/multi_plumbing_probes.py): a fake Studio hub on loopback and a batch driver for the real
``python -m lattice.mcp serve`` process. Not a test module."""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

INIT = {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "plumbing"}}


def clean_env(code: Path | None = None, **extra: str) -> dict[str, str]:
    """The environment of a server under test: no API key, no Claude session variables, no
    inherited workspace; a registry file that does not exist; `code` first on PYTHONPATH."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("ANTHROPIC", "CLAUDE_CODE")) and k != "LATTICE_WORKSPACE"
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LATTICE_PROJECTS_REGISTRY"] = str(
        Path(env.get("TMPDIR") or "/nonexistent") / "plumbing-no-registry.toml"
    )
    if code is not None:
        env["PYTHONPATH"] = str(code)
    env.update(extra)
    return env


def rpc(i: int, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        msg["params"] = dict(params)
    return msg


def tool_call(i: int, name: str, **args: Any) -> dict[str, Any]:
    return rpc(i, "tools/call", {"name": name, "arguments": args})


def run_server(
    argv: Sequence[str],
    requests: Sequence[Mapping[str, Any]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    via_cli: bool = False,
    timeout: float = 120.0,
) -> tuple[list[str], str]:
    """Start ``serve`` with `argv` (the arguments after ``serve``), send every request, close
    stdin and return (the raw reply lines in order, stderr). `via_cli`: through
    ``lattice.cli.main(["mcp", "serve", ...])``, the way the plugin's ``lattice mcp serve`` runs."""
    if via_cli:
        cmd = [
            sys.executable,
            "-c",
            "import sys; from lattice.cli import main; sys.exit(main(sys.argv[1:]))",
            "mcp",
            "serve",
            *argv,
        ]
    else:
        cmd = [sys.executable, "-m", "lattice.mcp", "serve", *argv]
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    r = subprocess.run(
        cmd, cwd=cwd, env=dict(env), input=stdin, capture_output=True, text=True,
        timeout=timeout, check=False,
    )  # fmt: skip
    return [ln for ln in r.stdout.splitlines() if ln.strip()], r.stderr


def tool_result(line: str) -> tuple[bool, Any]:
    """(isError, parsed JSON payload or the error text) of a ``tools/call`` reply line."""
    res = json.loads(line)["result"]
    text = res["content"][0]["text"]
    return bool(res["isError"]), (text if res["isError"] else json.loads(text))


class FakeStudio:
    """A Studio hub on 127.0.0.1 that logs every request (method, path).

    ``GET /api/projects`` (also under ``/p/<name>``) answers `listing_status` (200: the two
    rows alpha and beta by REAL path; 404 or 500: that status). ``GET .../api/model`` answers a
    model whose first rule says ``ALPHA via studio`` or ``BETA via studio``; ``/p/<unknown>``
    and ``GET .../api/changes/<id>`` of an unknown id are 404. POSTs are logged and answered
    with a minimal body, so a test can say a request never arrived."""

    def __init__(self, alpha: Path, beta: Path, *, listing_status: int = 200) -> None:
        self.alpha = os.path.realpath(alpha)
        self.beta = os.path.realpath(beta)
        self.listing_status = listing_status
        self.log: list[tuple[str, str]] = []
        self.changes: dict[str, dict[str, Any]] = {}
        self._srv: http.server.ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._srv is not None
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def requests(self, method: str | None = None, contains: str = "") -> list[tuple[str, str]]:
        return [(m, p) for m, p in self.log if (method in (None, m)) and contains in p]

    def model_of(self, which: str) -> dict[str, Any]:
        text = {"alpha": "ALPHA via studio", "beta": "BETA via studio"}[which]
        return {
            "project": {"name": which},
            "rules": [{"id": "R1", "status": "ok", "text": text}],
            "app": {"configured": False},
            "changes": [],
            "data_model": {"tables": []},
            "views": {"lint_gaps": []},
        }

    def _handle(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.log.append((method, path))
        which = "alpha"
        rest = path
        if path.startswith("/p/"):
            name, _, tail = path[3:].partition("/")
            if name not in ("alpha", "beta"):
                return 404, {"detail": f"no project {name}"}
            which, rest = name, "/" + tail
        if rest == "/api/projects" and method == "GET":
            if self.listing_status != 200:
                return self.listing_status, {"detail": "listing unavailable"}
            return 200, [
                {"name": "alpha", "root": self.alpha, "default": True},
                {"name": "beta", "root": self.beta},
            ]
        if rest == "/api/model" and method == "GET":
            return 200, self.model_of(which)
        if rest.startswith("/api/changes/") and method == "GET":
            cid = rest.rsplit("/", 1)[1]
            if cid in self.changes:
                return 200, self.changes[cid]
            return 404, {"detail": f"no change {cid}"}
        if rest == "/api/changes" and method == "POST":
            return 200, {"id": "c-fake"}
        if rest in ("/api/ask", "/api/check") and method == "POST":
            return 200, {"kind": "yes", "started": True}
        return 404, {"detail": f"no route {method} {path}"}

    def start(self) -> FakeStudio:
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _do(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                code, payload = outer._handle(method, self.path, body)
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._do("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._do("POST")

            def log_message(self, *_a: Any) -> None:
                pass

        self._srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None


# ---- the guard fixture and the 30-row table (plumbing D3, as amended) --------------------------


def make_project(d: Path, name: str) -> Path:
    """A directory the guard recognises as a project: the three spec files and a name."""
    d.mkdir(parents=True, exist_ok=True)
    for n in ("REQUIREMENTS.md", "spec.py", "spec.trace.json"):
        (d / n).write_text("x\n")
    (d / "studio.toml").write_text(f'[project]\nname = "{name}"\n')
    return d


def build_guard_fixture(lg: Path) -> dict[str, Path]:
    """The L-guard fixture under `lg` (see the pre-registration, section 3). `repo2` is a real
    ``git init``. Returns the paths the table needs."""
    lg.mkdir(parents=True, exist_ok=True)
    make_project(lg / "ws" / "p1", "ws-p1")
    (lg / "ws" / "p1" / "build").mkdir()
    make_project(lg / "ws" / "team" / "svc" / "p3", "ws-p3")
    (lg / "ws" / "plain").mkdir()
    make_project(lg / "ws" / "node_modules" / "pkg" / "p5", "ws-p5")
    (lg / "notes.txt").write_text("notes\n")
    (lg / "links").mkdir()
    (lg / "links" / "alias").symlink_to(lg / "ws" / "p1", target_is_directory=True)
    (lg / "wslink").symlink_to(lg / "ws", target_is_directory=True)
    (lg / "loop").mkdir()
    (lg / "loop" / "back").symlink_to(lg / "loop", target_is_directory=True)
    make_project(lg / "loop" / "q" / "p", "loop-p")
    p6 = make_project(lg / "cap" / "x1" / "x2" / "x3" / "x4" / "x5" / "p6", "cap-p6")
    wide = lg / "wide"
    wide.mkdir()
    for i in range(5000):
        (wide / f"d{i:04d}").mkdir()
    make_project(wide / "zzz" / "p", "wide-p")
    repo2 = lg / "repo2"
    (repo2 / "other").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo2)], check=True, capture_output=True)
    make_project(repo2 / "vendor" / "np", "ws-np")
    return {"lg": lg, "p6": p6}


# (row, cwd under lg ("" = lg, "@base" = lg's parent), tool, command or file_path, extra --project
#  (a path under lg), predicted new, predicted old, [amended: new differs from the registration])
# `{W}` in a command is this worktree. Expected value: the project name denied, or None = allow.
GUARD_ROWS: list[tuple[int, str, str, str, str | None, str | None, str | None]] = [
    (1, "", "Bash", "rm -rf ws", None, "ws-p1", None),
    (2, "ws/plain", "Bash", "rm -rf ../../ws", None, "ws-p1", None),
    (3, "ws", "Bash", "rm -rf team", None, "ws-p3", None),
    (4, "ws", "Bash", "rm -r ./team/../team/svc", None, "ws-p3", None),
    (5, "", "Bash", "cd ws && rm -rf team", None, "ws-p3", None),
    (6, "ws", "Bash", "rm -rf plain", None, None, None),
    (7, "ws", "Bash", "rm -rf p1/build", None, None, None),
    (8, "ws", "Bash", "rm -rf p1", None, "ws-p1", "ws-p1"),
    (9, "ws", "Bash", "rm -rf node_modules", None, None, None),
    (10, "", "Bash", "rm -rf links", None, None, None),
    (11, "", "Bash", "rm -rf wslink", None, "ws-p1", None),
    (12, "", "Bash", "rm -rf loop", None, "loop-p", None),
    (13, "", "Bash", "rm -rf cap/x1", None, "cap-p6", None),
    (14, "", "Bash", "rm -rf cap", None, None, None),
    (15, "", "Bash", "rm -rf cap", "cap/x1/x2/x3/x4/x5/p6", "cap-p6", "cap-p6"),
    (16, "", "Bash", "rm -rf wide", None, None, None),
    (17, "", "Bash", "rm -rf wide/zzz", None, "wide-p", None),
    (18, "", "Bash", "mv ws ws-old", None, "ws-p1", None),
    (19, "", "Bash", "mv notes.txt ws/", None, None, None),
    (20, "repo2", "Bash", "git clean -fdx", None, None, None),  # amended: allow
    (21, "repo2/other", "Bash", "git clean -fdx", None, None, None),
    (22, "ws/p1", "Bash", "git clean -fd", None, None, None),  # amended: allow
    (23, "repo2", "Bash", "git clean -n", None, None, None),  # amended: allow
    (24, "repo2", "Bash", "git rm -r -q vendor", None, None, None),  # amended: allow
    (25, "ws", "Bash", "git checkout -- team", None, "ws-p3", None),
    (26, "ws", "Bash", "git stash push -- team", None, "ws-p3", None),
    (27, "ws", "Bash", "rm -rf *", None, None, None),
    (28, "", "Bash", "find ws -delete", None, None, None),
    (29, "@base", "Bash", "rm -rf {W}/dogfood", None, "lattice-studio", None),
    (30, "", "Edit", "ws/p1/spec.py", None, "ws-p1", "ws-p1"),
]
