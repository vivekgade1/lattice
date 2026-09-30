"""L2 (spec 2026-09-29 ui-a §5.1 claim 1): TS parseParams == Python parse_params on the same
wire request, for every endpoint of every proof app. Corpora are checked in; regenerate with
LATTICE_REGEN_CORPUS=1."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.tsgen.gate.apps import APP_NAMES, app
from tests.tsgen.gate.corpus import wire_corpus
from tests.tsgen.node import run_node

HERE = Path(__file__).resolve().parent / "gate" / "corpus"
RT = Path(__file__).resolve().parents[2] / "lattice" / "tsgen" / "rt"


@pytest.mark.parametrize("name", APP_NAMES)
def test_direct_differential(name: str) -> None:
    corpus = wire_corpus(app(name))
    path = HERE / f"{name}.json"
    text = json.dumps(corpus, indent=0, ensure_ascii=True) + "\n"
    if os.environ.get("LATTICE_REGEN_CORPUS") == "1" or not path.exists():
        HERE.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert path.read_text() == text, "corpus drifted: regenerate with LATTICE_REGEN_CORPUS=1"
    done = run_node(["replay.ts", str(path)], cwd=RT)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["cases"] == len(corpus["params"])
    assert result["mismatches"] == [], "\n".join(result["mismatches"][:20])
