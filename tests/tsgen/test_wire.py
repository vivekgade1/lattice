"""The client's wire encoding reads back in Python as the value the client validated."""

from __future__ import annotations

import json
import math
import urllib.parse
from pathlib import Path

from tests.tsgen.node import node_json

RT = Path(__file__).resolve().parents[2] / "lattice" / "tsgen" / "rt"
SCRIPT = f"""
import {{ readFileSync }} from "node:fs";
import * as rt from "{(RT / "lattice-rt.ts").as_uri()}";
const vals = [2 ** 60, -0, NaN, Infinity, -Infinity, 0.1, 1e21, 9007199254740993n, "é\\ud800",
  true, null, 5, -(2n ** 63n), 1.5e-7, [1, "x"], {{ a: undefined, b: 1 }}];
readFileSync(0, "utf8");
console.log(JSON.stringify({{
  json: vals.map((v) => rt.dumpsWire(v)),
  text: [2 ** 60, -0, NaN, 0.1, 9007199254740993n, true, "a/b é"].map(rt.wireText),
  pct: rt.pctEncode("a/b é\\ud800~._-"),
}}));
"""


def test_wire_reads_back(tmp_path: Path) -> None:
    out = node_json(SCRIPT, None, cwd=tmp_path)
    assert all(t.isascii() for t in out["json"])
    got = [json.loads(t) for t in out["json"]]
    assert got[0] == 2**60 and got[1] == 0.0 and math.copysign(1, got[1]) == -1
    assert math.isnan(got[2]) and got[3] == math.inf and got[4] == -math.inf
    assert got[5] == 0.1 and got[6] == 1e21 and got[7] == 9007199254740993
    assert got[8] == "é\ud800" and got[9] is True and got[10] is None and got[11] == 5
    assert got[12] == -(2**63) and got[13] == 1.5e-7 and got[14] == [1, "x"]
    assert got[15] == {"b": 1}
    assert out["text"] == ["1152921504606846976", "-0.0", "nan", "0.1", "9007199254740993",
                           "true", "a/b é"]  # fmt: skip
    assert float(out["text"][0]) == 2.0**60
    assert urllib.parse.unquote(out["pct"]) == "a/b é�~._-"
