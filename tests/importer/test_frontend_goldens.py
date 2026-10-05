"""The FastAPI importer's outputs, pinned (Phase 2 plan, Wave 0): the W1 refactor
(front ends, transport, runtime) must leave them byte-identical.

For each of the template, TaskFlow, JustNotes and multi-vendor apps, one
``python -m lattice.importer ... --no-llm --no-corpus`` run (in a subprocess: one app
per process, as the CLI runs) gives, under ``tests/importer/goldens/<app>/``:

- ``model.json``: the ``RepoModel`` (``extract``) as ``to_json()`` writes it;
- ``profile.py``: the generated profile source (the CLI's ``profile.py``);
- ``cases.json``: the request suite over every route with a spec
  (``VerifyContext.full_suite()``; the lifted routes' suite is a subset of it): every
  ``Case``'s name, request (method, path, user, token, params, json, form), tables,
  note, db and route;
- ``report.json``: the CLI's report, with the volatile fields replaced (below);
- ``natives.py`` and ``routes/<router>.lattice.py``: the CLI's other outputs.

Normalisations (test-side only; the importer is not changed):

- ``report.json``: ``repo`` (the absolute checkout path), ``schema`` (the report
  version) and ``cost.wall_s`` (wall time) become placeholders; the ``rules`` section
  and ``coverage.rules`` that W2-D adds are dropped (the plan's goldens ignore them);
- ``cases.json``: a JWT the suite mints (``_minted_cases``: the app's own signer, with an
  ``exp`` a fixed time from now) becomes ``<jwt header=... claims=...>``, its time claims
  (``exp``, ``iat``, ``nbf``) spelled ``<future>`` / ``<past>`` and its signature dropped;
- ``model.json`` / ``cases.json``: a field named in :data:`ADDED_DEFAULTS` is dropped
  wherever it holds its default (empty now: W1-A adds ``RepoModel`` fields "with a
  default that reproduces today's output", and lists each one there).

``report.md`` is not pinned: it renders ``report.json`` (pinned) plus the wall time.

Regenerate with ``LATTICE_REGEN_GOLDENS=1`` (a missing golden fails otherwise).
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDENS = Path(__file__).resolve().parent / "goldens"
REGEN = os.environ.get("LATTICE_REGEN_GOLDENS") == "1"

# app -> (package, app factory)
APPS: dict[str, tuple[str, str]] = {
    "template": ("bench.template_app.app", "bench.template_app:make_app"),
    "taskflow": ("bench.import_repos.taskflow.app", "bench.import_repos.taskflow:make_app"),
    "justnotes": ("bench.import_repos.justnotes.app", "bench.import_repos.justnotes:make_app"),
    "multivendor": (
        "bench.import_repos.multivendor.app", "bench.import_repos.multivendor:make_app",
    ),
}  # fmt: skip

# fields added to RepoModel / Case after W0, with the default that reproduces the W0
# output: dropped (at any depth) where they hold it, so the goldens stay byte-identical
ADDED_DEFAULTS: dict[str, Any] = {}
# report.json: volatile fields -> placeholder, and the sections the goldens ignore
REPORT_PLACEHOLDERS = {("repo",): "<repo>", ("schema",): "<schema>",
                       ("cost", "wall_s"): "<wall_s>"}  # fmt: skip
REPORT_IGNORED = (("rules",), ("coverage", "rules"))
JWT_TIME_CLAIMS = ("exp", "iat", "nbf")


# ---------------------------------------------------------------- the capture (worker) --


def _worker(app: str, out: Path) -> None:
    """Run the CLI on ``app`` into ``out/lattice_import`` and write the raw RepoModel
    and suite cases beside it (the ImportResult is taken from the CLI's own
    ``write_import`` call)."""
    import lattice.importer.cli as cli

    package, factory = APPS[app]
    captured: list[Any] = []
    write_import = cli.write_import

    def capture(close: Any, *args: Any, **kwargs: Any) -> Any:
        captured.append(close)
        return write_import(close, *args, **kwargs)

    cli.write_import = capture  # type: ignore[assignment]
    code = cli.main([str(REPO_ROOT), "--package", package, "--app-factory", factory,
                     "--out", str(out / "lattice_import"), "--no-llm", "--no-corpus"])  # fmt: skip
    assert code == 0 and len(captured) == 1, (code, len(captured))
    result = captured[0].result
    (out / "model.json").write_text(result.model.to_json())
    suite = result.context.full_suite() if result.context is not None else None
    cases = [dataclasses.asdict(c) for c in suite.cases] if suite is not None else []
    (out / "cases.json").write_text(json.dumps(cases))


def _run_worker(app: str, out: Path) -> float:
    started = time.perf_counter()
    done = subprocess.run(
        [sys.executable, "-m", "tests.importer.test_frontend_goldens", app, str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-3000:]
    return time.perf_counter() - started


# --------------------------------------------------------------------- normalisation --


def _drop_added_defaults(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _drop_added_defaults(v)
            for k, v in value.items()
            if not (k in ADDED_DEFAULTS and v == ADDED_DEFAULTS[k])
        }
    if isinstance(value, list):
        return [_drop_added_defaults(v) for v in value]
    return value


def _b64json(part: str) -> Any:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def _jwt(token: str, now: float) -> str:
    """A minted JWT without its time: header and claims, time claims as past/future."""
    parts = token.split(".")
    if len(parts) != 3 or not token.startswith("eyJ"):
        return token
    try:
        header, claims = _b64json(parts[0]), _b64json(parts[1])
    except (ValueError, UnicodeDecodeError):
        return token
    if not isinstance(claims, dict):
        return token
    for k in JWT_TIME_CLAIMS:
        if isinstance(claims.get(k), int | float):
            claims[k] = "<future>" if claims[k] > now else "<past>"
    spell = lambda d: json.dumps(d, sort_keys=True, separators=(",", ":"))  # noqa: E731
    return f"<jwt header={spell(header)} claims={spell(claims)}>"


def _cases(text: str) -> str:
    now = time.time()
    cases = _drop_added_defaults(json.loads(text))
    for c in cases:
        if isinstance(c.get("token"), str):
            c["token"] = _jwt(c["token"], now)
    return json.dumps(cases, indent=2) + "\n"


def _model(text: str) -> str:
    return json.dumps(_drop_added_defaults(json.loads(text)), indent=2) + "\n"


def _report(text: str) -> str:
    report = json.loads(text)
    for path in REPORT_IGNORED:
        *parents, last = path
        node = report
        for p in parents:
            node = node.get(p, {})
        node.pop(last, None)
    for path, placeholder in REPORT_PLACEHOLDERS.items():
        *parents, last = path
        node = report
        for p in parents:
            node = node[p]
        assert last in node, path
        node[last] = placeholder
    return json.dumps(report, indent=2) + "\n"


def outputs(raw: Path) -> dict[str, str]:
    """The golden files (relative path -> text) from one worker's raw output."""
    imp = raw / "lattice_import"
    files = {
        "model.json": _model((raw / "model.json").read_text()),
        "cases.json": _cases((raw / "cases.json").read_text()),
        "profile.py": (imp / "profile.py").read_text(),
        "natives.py": (imp / "natives.py").read_text(),
        "report.json": _report((imp / "report.json").read_text()),
    }
    for path in sorted((imp / "routes").iterdir()):
        files[f"routes/{path.name}"] = path.read_text()
    return files


# ------------------------------------------------------------------------------ tests --


@pytest.fixture(scope="module", params=sorted(APPS))
def captured(request, tmp_path_factory) -> tuple[str, dict[str, str]]:
    app = request.param
    raw = tmp_path_factory.mktemp(f"golden-{app}")
    _run_worker(app, raw)
    files = outputs(raw)
    if REGEN:
        target = GOLDENS / app
        for old in sorted(target.rglob("*"), reverse=True) if target.exists() else []:
            old.unlink() if old.is_file() else old.rmdir()
        for name, text in files.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_text(text)
    return app, files


def _golden_files(app: str) -> dict[str, str]:
    root = GOLDENS / app
    assert root.is_dir(), f"no goldens for {app}: generate them with LATTICE_REGEN_GOLDENS=1"
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_the_same_files(captured):
    app, files = captured
    assert sorted(files) == sorted(_golden_files(app)), "regenerate with LATTICE_REGEN_GOLDENS=1"


@pytest.mark.parametrize(
    "name", ["model.json", "profile.py", "cases.json", "report.json", "natives.py", "routes"]
)
def test_the_output_is_byte_identical_to_the_golden(captured, name):
    app, files = captured
    golden = _golden_files(app)
    names = [n for n in files if n == name or n.startswith(f"{name}/")]
    assert names or name == "routes", (app, name)  # multi-vendor verifies no route
    for n in names:
        assert n in golden, f"{app}/{n}: no golden (regenerate with LATTICE_REGEN_GOLDENS=1)"
        assert files[n] == golden[n], f"{app}/{n} drifted (regenerate with LATTICE_REGEN_GOLDENS=1)"


def test_the_jwt_normalisation():
    def enc(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    token = f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc({'exp': 2000, 'sub': '4', 'iat': 10})}.sig"
    assert _jwt(token, 1000.0) == (
        '<jwt header={"alg":"HS256","typ":"JWT"} '
        'claims={"exp":"<future>","iat":"<past>","sub":"4"}>'
    )
    assert _jwt("not-a-jwt", 1000.0) == "not-a-jwt"


if __name__ == "__main__":
    _worker(sys.argv[1], Path(sys.argv[2]))
