"""The contract check of gRPC round 5 (docs/results/2026-10-18-grpc-round5-prereg.md, section 2, H6/A6).

Run on a finished app (the app directory itself: ``harness.py``, ``studio.toml``, ``truth.json``) in a
fresh ``python -I`` process, before its manifest. It prints ONLY one line per fact, never a
traceback, extractor output, reconcile or flow recovery; the app passes when every line is ``ok``
(exit status 0)::

    truth schema ok | truth schema <first error>
    codes ok (<n> normalised) | codes <TruthError>
    import ok | import <ExceptionClass>: <message>
    RUNTIME is process_runtime ok | RUNTIME is <type>
    build_db returned the path ok | build_db returned <type>
    make_app returned ProcessApp ok | make_app returned <type>
    ProcessApp ready=<v> protos=<n> exist ok | ProcessApp ready=<v> protos=<n> <what is wrong>
    server started ok | server started exit code <rc>: <last 5 stderr lines>
    login <handle> ok | login <handle> <code> <server message>
    flow <name> ok | flow <name> step <i>: <reason class> (<code> vs <expected>)

A MySQL app (``[app] database = "mysql"``, PM-SG) gets one more line, ``database server ok|<error>``,
and its databases come from a throwaway server (label ``lattice.owner=w-gr``). The flows are replayed
by ``grpc_truth5.replay_flow`` (links applied), the same function the runner uses.

    python dogfood/grpc_contract5.py <app directory>
"""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MARK = "@@ "
EXPECTED_PREFIXES = (
    "truth schema",
    "codes",
    "import",
    "RUNTIME",
    "build_db",
    "make_app",
    "ProcessApp",
    "database server",
    "server started",
    "login ",
    "flow ",
)


def _one_line(text: str, limit: int = 400) -> str:
    return " / ".join(x.strip() for x in str(text).strip().splitlines() if x.strip())[:limit]


def facts(app_dir: Path) -> list[tuple[str, bool]]:
    sys.dont_write_bytecode = True
    for extra in filter(None, os.environ.get("CONTRACT5_EXTRA_PATH", "").split(os.pathsep)):
        sys.path.append(
            extra
        )  # python -I ignores PYTHONPATH: a driver environment's extra packages
    for p in (str(HERE), str(REPO)):
        if p not in sys.path and (p == str(HERE) or (REPO / "lattice").is_dir()):
            sys.path.insert(0, p)
    import grpc_truth5 as t5

    out: list[tuple[str, bool]] = []

    def say(line: str, ok: bool) -> None:
        out.append((line, ok))

    truth_path = app_dir / "truth.json"
    truth = None
    try:
        raw = json.loads(truth_path.read_text())
    except (OSError, ValueError) as err:
        raw = None
        say(f"truth schema {type(err).__name__}: {_one_line(err, 160)}", False)
    if raw is not None:
        errs = t5.schema_errors(raw)
        say("truth schema ok" if not errs else f"truth schema {errs[0]}", not errs)
        try:
            _, n = t5.normalise_codes_in(raw)
            say(f"codes ok ({n} normalised)", True)
        except t5.TruthError as err:
            say(f"codes {err}", False)
        if not errs:
            with contextlib.suppress(t5.TruthError):
                truth = t5.load_truth(truth_path)
    # the harness, imported exactly as the library imports it
    try:
        from lattice.importer.frontends.go_gin import _harness_attr

        cfg = tomllib.loads((app_dir / "studio.toml").read_text())["app"]
        make_app = _harness_attr(app_dir, cfg["app"])
        build_db = _harness_attr(app_dir, cfg["db"])
        runtime = _harness_attr(app_dir, cfg["runtime"])
        users = _harness_attr(app_dir, cfg["app"].partition(":")[0] + ":USERS")
        say("import ok", True)
    except Exception as err:  # noqa: BLE001 -- the class and message are the printout
        say(f"import {type(err).__name__}: {_one_line(err, 300)}", False)
        return out
    from lattice.importer.transport import AppRuntime
    from lattice.spec.process import ProcessApp, open_process

    if not (isinstance(runtime, AppRuntime) and runtime.transport is open_process):
        say(f"RUNTIME is {type(runtime).__name__}", False)
        return out
    say("RUNTIME is process_runtime ok", True)
    source = t5.SqliteSource()
    server = None
    if cfg.get("database") == "mysql":
        try:
            os.environ.setdefault("LATTICE_CONTAINER_OWNER", "w-gr")
            from lattice.spec.dbserver import server_for

            server, _owned = server_for("mysql")
            source = t5.ServerSource(server)
            say("database server ok", True)
        except Exception as err:  # noqa: BLE001
            say(f"database server {type(err).__name__}: {_one_line(err, 300)}", False)
            return out
    try:
        with source.fresh() as target:
            try:
                ref = build_db(target)
            except Exception as err:  # noqa: BLE001
                say(f"build_db raised {type(err).__name__}: {_one_line(err, 300)}", False)
                return out
            if ref is None or str(ref) != str(target):
                say(
                    f"build_db returned {type(ref).__name__ if ref is None or not isinstance(ref, (str, os.PathLike)) else 'a different path'}",
                    False,
                )
            else:
                say("build_db returned the path ok", True)
            try:
                app = make_app(target if ref is None else ref)
            except Exception as err:  # noqa: BLE001
                say(f"make_app raised {type(err).__name__}: {_one_line(err, 300)}", False)
                return out
            if not isinstance(app, ProcessApp):
                say(f"make_app returned {type(app).__name__}", False)
                return out
            say("make_app returned ProcessApp ok", True)
            base = Path(app.proto_root) if app.proto_root else Path.cwd()
            protos = list(app.protos)
            missing = next((p for p in protos if not (base / p).is_file()), None)
            head = f"ProcessApp ready={app.ready} protos={len(protos)}"
            if app.ready != "grpc":
                say(f'{head} ready must be "grpc"', False)
                return out
            if not protos:
                say(f"{head} none named", False)
                return out
            if missing is not None:
                say(f"{head} missing {missing}", False)
                return out
            say(f"{head} exist ok", True)
            try:
                with runtime.open(app) as transport:
                    say("server started ok", True)
                    for handle in users:
                        try:
                            runtime.auth.token(transport, handle, None)
                            say(f"login {handle} ok", True)
                        except Exception as err:  # noqa: BLE001
                            msg = str(err)
                            tail = (
                                msg.split("failed:", 1)[1].strip()
                                if "failed:" in msg
                                else f"{type(err).__name__}: {msg}"
                            )
                            say(f"login {handle} {_one_line(tail, 300)}", False)
            except Exception as err:  # noqa: BLE001
                stderr = ""
                with contextlib.suppress(Exception):
                    stderr = "\n".join(app.stderr_text().splitlines()[-5:])
                say(
                    f"server started exit code {app.returncode}: {_one_line(stderr or str(err), 500)}",
                    False,
                )
                return out
            finally:
                runtime.close(app)
        if truth is not None:
            for flow in truth["flows"]:
                res = t5.replay_flow(
                    flow,
                    truth["flow_links"][flow["name"]],
                    make_app=make_app,
                    build_db=build_db,
                    runtime=runtime,
                    source=source,
                )
                say(f"flow {flow['name']} {res.label(truth['step_index_base'])}", res.ok)
    finally:
        if server is not None:
            with contextlib.suppress(Exception):
                from lattice.spec.dbserver import remove_all

                remove_all()
    return out


def child(app_dir: Path) -> int:
    try:
        lines = facts(app_dir)
    except Exception as err:  # noqa: BLE001 -- never a traceback on the author's screen
        print(f"{MARK}contract5 internal error: {type(err).__name__}", flush=True)
        return 2
    for line, _ok in lines:
        print(MARK + line, flush=True)
    return 0 if lines and all(ok for _, ok in lines) else 1


def run(app_dir: str | Path, *, timeout: float = 900.0) -> tuple[int, list[str]]:
    """The check in a fresh ``python -I`` process: ``(exit status, the printed lines)``."""
    scratch = Path(tempfile.mkdtemp(prefix="contract5-cwd-"))
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--child",
                str(Path(app_dir).resolve()),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=scratch,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "CONTRACT5_EXTRA_PATH": os.environ.get(
                    "CONTRACT5_EXTRA_PATH", os.environ.get("PYTHONPATH", "")
                ),
            },
        )
    except subprocess.TimeoutExpired:
        return 2, ["contract5 internal error: timeout"]
    finally:
        with contextlib.suppress(OSError):
            scratch.rmdir()
    lines = [ln[len(MARK) :] for ln in proc.stdout.splitlines() if ln.startswith(MARK)]
    if not lines:
        lines = ["contract5 internal error: no output"]
    return proc.returncode, lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--child"]:
        return child(Path(args[1]).resolve())
    if len(args) != 1:
        print(__doc__)
        return 2
    code, lines = run(args[0])
    sys.stdout.write("".join(ln + "\n" for ln in lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
