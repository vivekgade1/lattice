"""The contract check of gRPC round 4 (docs/results/2026-10-18-grpc-round4-prereg.md, section 6.3).

Run once by the orchestrator on the finished held-out app, before it is hashed, to catch a harness the
library cannot drive (the crash class of round 3). It imports ``harness.py`` in a fresh interpreter
(``python -I``), builds the database and the app exactly as ``studio.toml`` names them, logs in as every
``USERS`` handle through the harness's own ``RUNTIME.auth`` (D6 applies), and replays each truth flow
through the library's ``Rpc`` scenarios. It runs NO extractor, no model, no reconcile and no flows
recovery, and prints only::

    import ok | import failed: <class>
    build ok | build failed: <class>
    login <handle> ok | login <handle> failed: <class>
    flow <id> ok | flow <id> failed: step <n>

    python dogfood/grpc_contract4.py <app directory>      # holds harness.py, studio.toml, truth.json
"""

from __future__ import annotations

# ruff: noqa: E501 -- docstrings and data lines
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def child(app_dir: Path) -> int:
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(REPO), str(HERE)]
    scratch = Path(tempfile.mkdtemp(prefix="contract4-"))
    os.environ.setdefault("LATTICE_HELDOUT_BUILD", str(scratch / "hbuild"))
    os.environ.setdefault(f"{app_dir.name.upper()}_BIN_DIR", str(scratch / "bin"))
    try:
        spec_ = importlib.util.spec_from_file_location("contract4_harness", app_dir / "harness.py")
        assert spec_ and spec_.loader
        h = importlib.util.module_from_spec(spec_)
        sys.modules["contract4_harness"] = h
        sys.path.insert(0, str(app_dir))
        spec_.loader.exec_module(h)
        users, runtime = h.USERS, h.RUNTIME
        print("import ok")
    except Exception as err:  # noqa: BLE001
        print(f"import failed: {type(err).__name__}")
        return 1
    try:
        cfg = tomllib.loads((app_dir / "studio.toml").read_text())["app"]
        make_app = getattr(h, cfg["app"].split(":")[1])
        build_db = getattr(h, cfg["db"].split(":")[1])
        db = build_db(scratch / "c.db")
        app = make_app(db)
        print("build ok")
    except Exception as err:  # noqa: BLE001
        print(f"build failed: {type(err).__name__}")
        return 1
    status = 0
    try:
        with runtime.open(app) as tr:
            for handle in users:
                try:
                    runtime.auth.token(tr, handle, None)
                    print(f"login {handle} ok")
                except Exception as err:  # noqa: BLE001
                    print(f"login {handle} failed: {type(err).__name__}")
                    status = 1
    except Exception as err:  # noqa: BLE001
        print(f"build failed: {type(err).__name__}")
        return 1
    finally:
        runtime.close(app)
    try:
        import grpc_probes2 as q2

        from lattice.profile import ApiProfile, Profile
        from lattice.spec import Rpc, Scenario, Spec, check

        truth = q2.h2_truth(app_dir / "truth.json")
        protos = tuple(str(app_dir / p) for p in h.PROTOS)
        profile = Profile(
            schemas={},
            api=ApiProfile(
                tables={},
                table_names={},
                roles=frozenset({"Public", "Member"}),
                error_body="fastapi",
            ),
        )
        spec = Spec(profile=profile, users=users, protos=protos)
        for f in truth["flows"]:
            steps = tuple(
                Rpc(
                    s["rpc"].partition("/")[0],
                    s["rpc"].partition("/")[2],
                    s["as"],
                    s["message"],
                    expect_code=s["expect_code"],
                )
                for s in f["steps"]
            )
            rep = check(
                replace(spec, scenarios=(Scenario(f["name"], steps=steps),)),
                make_app,
                build_db,
                layers="D",
                runtime=runtime,
            )
            if rep.findings:
                import re

                m = re.search(r"step (\d+)", " ".join(str(x.message) for x in rep.findings))
                print(f"flow {f['name']} failed: step {m.group(1) if m else '?'}")
                status = 1
            else:
                print(f"flow {f['name']} ok")
    except Exception as err:  # noqa: BLE001
        print(f"flow replay failed: {type(err).__name__}")
        return 1
    return status


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--child"]:
        return child(Path(args[1]).resolve())
    if len(args) != 1:
        print(__doc__)
        return 2
    proc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--child", args[0]],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stdout.write(
        "".join(
            ln + "\n"
            for ln in proc.stdout.splitlines()
            if ln.split(" ")[0] in ("import", "build", "login", "flow")
        )
    )
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
