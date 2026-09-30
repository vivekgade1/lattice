"""hooks.ts: one hook per endpoint, never throwing, keyed bigint-safely."""

from __future__ import annotations

from pathlib import Path

from lattice.tsgen import generate_ts
from lattice.tsgen.emit import pascal
from tests.tsgen.node import node_json
from tests.tsgen.test_emit import plans_of


def test_one_hook_per_endpoint() -> None:
    plans = plans_of("realapps")
    hooks = generate_ts(plans)["hooks.ts"]
    assert 'from "@tanstack/react-query"' in hooks
    for plan in plans:
        assert f"export function use{pascal(plan.name)}(" in hooks
        assert ("useQuery(" if plan.method == "GET" else "useMutation(") in hooks


KEY = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
console.log(JSON.stringify([
  rt.canonicalKey({ b: 1n, a: "x", c: undefined }) === rt.canonicalKey({ a: "x", b: 1n }),
  rt.canonicalKey({ n: 1n }) !== rt.canonicalKey({ n: 1 }),
  rt.canonicalKey({ n: NaN }) !== rt.canonicalKey({ n: null }),
]));
"""


def test_canonical_key(tmp_path: Path) -> None:
    for name, text in generate_ts(plans_of("realapps")).items():
        (tmp_path / name).write_text(text)
    assert node_json(KEY, tmp_path.as_uri(), cwd=tmp_path) == [True, True, True]
