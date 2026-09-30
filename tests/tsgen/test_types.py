"""L4 (spec 2026-09-29 ui-a §5.4): generated code type-checks under strict; a UI that ignores
a declared error kind does not compile."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lattice.tsgen import generate_ts
from lattice.tsgen.emit import camel, error_statuses, kind_of, pascal
from tests.tsgen.gate.apps import APP_NAMES, app
from tests.tsgen.node import need_node

TS = Path(__file__).resolve().parents[1] / "ts"


def tsc() -> list[str]:
    need_node()
    binary = TS / "node_modules" / ".bin" / "tsc"
    if not binary.exists():
        if os.environ.get("LATTICE_REQUIRE_NODE") == "1":
            pytest.fail("run: npm ci --prefix tests/ts")
        pytest.skip("tests/ts dependencies are not installed (npm ci --prefix tests/ts)")
    return [str(binary), "-p", str(TS / "tsconfig.json")]


def generate(names: list[str]) -> None:
    shutil.rmtree(TS / "gen", ignore_errors=True)
    for name in names:
        out = TS / "gen" / name
        out.mkdir(parents=True)
        for fname, text in generate_ts(app(name).plans).items():
            (out / fname).write_text(text)


def usage(name: str, drop_kind: bool) -> str:
    plan = next(p for p in app(name).plans if len(error_statuses(p)) >= 3)
    kinds = [kind_of(s) for s in error_statuses(plan)] + ["unexpected"]
    if drop_kind:
        kinds = kinds[:-2] + kinds[-1:]
    cases = "\n".join(f'    case "{k}": return "{k}";' for k in kinds)
    return f"""import {{ {camel(plan.name)}, type {pascal(plan.name)}Error }} from "./api.ts";
export function message(e: {pascal(plan.name)}Error): string {{
  switch (e.kind) {{
{cases}
    default: {{ const never: never = e; return never; }}
  }}
}}
export const call = {camel(plan.name)};
"""


def run_tsc() -> subprocess.CompletedProcess[str]:
    return subprocess.run(tsc(), capture_output=True, text=True, check=False, timeout=600)


def test_generated_code_is_strict_clean() -> None:
    tsc()
    generate(APP_NAMES)
    done = run_tsc()
    assert done.returncode == 0, done.stdout[-6000:]


def test_ignoring_a_declared_error_does_not_compile() -> None:
    tsc()
    generate(["realapps"])
    (TS / "gen" / "realapps" / "usage.ts").write_text(usage("realapps", drop_kind=False))
    assert run_tsc().returncode == 0
    (TS / "gen" / "realapps" / "usage.ts").write_text(usage("realapps", drop_kind=True))
    done = run_tsc()
    assert done.returncode != 0 and "never" in done.stdout, done.stdout
