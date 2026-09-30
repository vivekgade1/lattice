"""Regenerates dict_answers.json: how CPython 3.14's dicts (and T2Sets, which are dicts)
behave when changed while they are iterated -- which keys the iterator yields and
where it raises RuntimeError. That depends on CPython's dict layout (entries, holes,
resizes), which rt/omap.go reproduces; colls_test.go replays every scenario on
rt.Dict. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_dict_answers.py

A scenario: `make` builds the dict (keys are ints), `pre` changes it before the loop,
`body[i]` is what the loop does after yielding its i-th key; the answer is the keys
yielded, the error (None, or the RuntimeError's message) and the dict's keys after.

make: ["empty"] | ["lit", n] (a dict literal of keys 0..n-1, compiled as written) |
["comp", n] (a comprehension) | ["set", n] (T2Set of range(n): its dict) |
["setcopy", n] (T2Set(range(n)).copy()) | ["fromsetlit", n] (a set literal).
pre / body ops: ["add", k] | ["del", k] | ["copy"] | ["clear"] | ["merge", n, base]
(update with a comprehension of keys base..base+n-1) | ["mergelit", n, base] (update
with a literal).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lattice.t2.support import T2Set  # noqa: E402

OUT = Path(__file__).with_name("dict_answers.json")


def literal(keys: list[int]) -> dict[int, int]:
    return eval("{" + "".join(f"{k}: 0, " for k in keys) + "}")  # noqa: S307 - compiled as written


def make(spec: list) -> dict:
    kind = spec[0]
    if kind == "empty":
        return {}
    n = spec[1]
    if kind == "lit":
        return literal(list(range(n)))
    if kind == "comp":
        return {i: 0 for i in range(n)}  # noqa: C420 - a comprehension on purpose
    if kind == "set":
        return T2Set(range(n))._d
    if kind == "setcopy":
        return T2Set(range(n)).copy()._d
    if kind == "fromsetlit":
        return T2Set(tuple(range(n)))._d
    raise ValueError(kind)


def apply(d: dict, op: list) -> dict:
    kind = op[0]
    if kind == "add":
        d[op[1]] = 0
    elif kind == "del":
        d.pop(op[1], None)
    elif kind == "copy":
        return d.copy()
    elif kind == "clear":
        d.clear()
    elif kind == "merge":
        d.update({op[2] + i: 0 for i in range(op[1])})
    elif kind == "mergelit":
        d.update(literal([op[2] + i for i in range(op[1])]))
    return d


def run(sc: dict) -> dict:
    d = make(sc["make"])
    for op in sc["pre"]:
        d = apply(d, op)
    yielded: list[int] = []
    error = None
    try:
        for i, k in enumerate(d):
            yielded.append(k)
            if i >= 200:
                break
            for op in sc["body"][i] if i < len(sc["body"]) else []:
                apply(d, op)
    except RuntimeError as e:
        error = str(e)
    return {"yielded": yielded, "error": error, "after": list(d)}


def scenario(rng: random.Random) -> dict:
    kind = rng.choice(["empty", "lit", "lit", "comp", "set", "setcopy", "fromsetlit"])
    n = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 15, 16, 17, 20, 33, 40])
    spec = ["empty"] if kind == "empty" else [kind, n]
    size = 0 if kind == "empty" else n
    pre = []
    for _ in range(rng.randint(0, 4)):
        op = rng.choice(["add", "del", "del", "copy", "merge", "mergelit", "clear"])
        if op in ("add", "del"):
            pre.append([op, rng.randrange(size + 10)])
        elif op in ("merge", "mergelit"):
            pre.append([op, rng.randrange(1, 20), rng.randrange(60)])
        elif op == "clear" and rng.random() < 0.3:
            pre.append(["clear"])
        elif op == "copy":
            pre.append(["copy"])
    body = []
    fresh = 1000
    for _ in range(rng.randint(1, 30)):
        ops = []
        r = rng.random()
        if r < 0.8:  # replace a key: the size stays
            ops = [["del", rng.randrange(size + 10)], ["add", fresh]]
            fresh += 1
        elif r < 0.83:
            ops = [["add", fresh]]
            fresh += 1
        elif r < 0.86:
            ops = [["del", rng.randrange(size + 10)]]
        elif r < 0.9:
            ops = [["add", rng.randrange(size + 10)]]
        body.append(ops)
    return {"make": spec, "pre": pre, "body": body}


def main() -> None:
    rng = random.Random(20260929)
    out = []
    for _ in range(3000):
        sc = scenario(rng)
        sc["answer"] = run(sc)
        out.append(sc)
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    errors = sum(1 for s in out if s["answer"]["error"])
    print(f"{len(out)} scenarios ({errors} raise) -> {OUT}")


if __name__ == "__main__":
    main()
