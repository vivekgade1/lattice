"""Regenerates json_answers.json: what Python 3.14 answers for json.dumps (with
sort_keys and indent) of Json values, json.loads of valid and invalid text (its value,
or the ValueError message), and the operations a Json value allows: str(), repr(),
==, truth and indexing. jsonv_test.go asserts against it. Run from the repository
root:

    uv run python lattice/gogen/rt/testdata/gen_json_answers.py

Values are tagged trees: ["n"] None, ["b", bool], ["i", decimal], ["f", float.hex],
["s", [code points]], ["l", [items]], ["t", [items]] (a tuple), ["d", [[key, value],
...]] (a dict; keys are ["s", ...]). An answer is {"op", "args", "out"}: `out` is a
str, a tagged value (loads, index), or "!<Kind>: <message>".
"""

# ruff: noqa: E501 - data: long program texts and answers read better on one line

from __future__ import annotations

import json
import random
import string
from pathlib import Path

OUT = Path(__file__).with_name("json_answers.json")


def tag(v: object) -> list:
    if v is None:
        return ["n"]
    if isinstance(v, bool):
        return ["b", v]
    if isinstance(v, int):
        return ["i", str(v)]
    if isinstance(v, float):
        return ["f", float.hex(v)]
    if isinstance(v, str):
        return ["s", [ord(c) for c in v]]
    if isinstance(v, list):
        return ["l", [tag(x) for x in v]]
    if isinstance(v, tuple):
        return ["t", [tag(x) for x in v]]
    if isinstance(v, dict):
        return ["d", [[tag(k), tag(x)] for k, x in v.items()]]
    raise TypeError(v)


def outcome(f, tagged: bool = False) -> object:  # noqa: ANN001
    try:
        v = f()
        return tag(v) if tagged else (v if isinstance(v, str) else repr(v))
    except Exception as e:  # noqa: BLE001 - the answer is the exception
        return f"!{type(e).__name__}: {e}"


CHARS = (
    list(string.ascii_letters + string.digits + " _-")
    + ['"', "\\", "\n", "\t", "\r", "\b", "\f", "\x00", "\x1f", "\x7f", "é", "ü", "€", "\u2028"]
    + ["\U0001f44b", "\U00010000", "\uffff", "日", "'", "/"]
)


def rand_str(rng: random.Random) -> str:
    return "".join(rng.choice(CHARS) for _ in range(rng.randint(0, 6)))


def rand_value(rng: random.Random, depth: int = 0) -> object:
    r = rng.random()
    if depth > 3 or r < 0.5:
        return rng.choice(
            [
                None, True, False, 0, -1, 7, 2**63 - 1, -(2**63), 10**20,
                0.0, -0.0, 1.5, 0.1, 1e16, 1e-7, 123456789.125, 1e300, float("nan"),
                float("inf"), -float("inf"), rand_str(rng), rand_str(rng),
                rng.uniform(-1e6, 1e6), rng.randint(-(10**6), 10**6),
            ]
        )  # fmt: skip
    if r < 0.7:
        return [rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if r < 0.75:
        return tuple(rand_value(rng, depth + 1) for _ in range(rng.randint(0, 3)))
    return {rand_str(rng): rand_value(rng, depth + 1) for _ in range(rng.randint(0, 4))}


VALID = [
    '{"a": 1, "b": [1, 2.5, -3e2, true, false, null], "c": {"d": "e"}}', "[]", "{}", " 1 ", "-0",
    "0.5", "1E400", "-1e-400", "123456789012345678901234567890", '"\\u00e9\\ud83d\\udc4b"',
    '"\\ud800"', '"\\udc00x"', '"\\ud800\\u0041"', "NaN", "Infinity", "-Infinity", '{"a": 1, "a": 2}',
    '[1, [2, [3, [4]]]]', "\t\n\r [1] \n", '"\\"\\\\\\/\\b\\f\\n\\r\\t"', "1.0", "1e5", "-12.5E-3",
]  # fmt: skip
INVALID = [
    "", " ", "{invalid}", "[1,]", '{"a":1,}', "[1 2]", '{"a" 1}', '{"a":}', "{1: 2}", "tru", "nul",
    "01", "1.", ".5", "-", "--1", "1e", "1e+", '"abc', '"\\x"', '"\\u12"', '"\\u12G4"', '"a\nb"',
    "[1]x", "\ufeff[1]", "[", "{", '{"a"', '{"a":1', "[1,", "[\n1,\n]", "[1,\n2\n,]x", "\u00e9",
    '"\\ud800\\u12"', "1" * 4400, "-" + "2" * 4301, "[1] [2]", "NaNx", "-Infinit", "Inf",
    '"\\ud800\\uzzzz"', '"\\u"', '"\\', "\"\\u1234", '"\x1f"', "[\"\\u0000\"]x",
]  # fmt: skip


def main() -> None:
    rng = random.Random(20260929)
    answers: list[dict] = []
    values = [rand_value(rng) for _ in range(1500)]
    values += [{"b": 1, "a": 2, "": [], "é": {}}, [], {}, (), (1,), ("x", [None]), "é👋", 1e16]
    for v in values:
        for sort_keys in (False, True):
            answers.append({"op": "dumps", "args": [tag(v), sort_keys, None],
                            "out": outcome(lambda v=v, s=sort_keys: json.dumps(v, sort_keys=s))})  # fmt: skip
        indent, sk = rng.choice([0, 2, 4, -1]), rng.random() < 0.5
        answers.append({"op": "dumps", "args": [tag(v), sk, indent],
                        "out": outcome(lambda v=v, i=indent, s=sk: json.dumps(v, sort_keys=s, indent=i))})  # fmt: skip
        answers.append({"op": "repr", "args": [tag(v)], "out": outcome(lambda v=v: repr(v))})
        answers.append({"op": "str", "args": [tag(v)], "out": outcome(lambda v=v: str(v))})
        answers.append({"op": "truth", "args": [tag(v)], "out": outcome(lambda v=v: bool(v))})
        w = rng.choice(values)
        answers.append(
            {"op": "eq", "args": [tag(v), tag(w)], "out": outcome(lambda v=v, w=w: v == w)}
        )
        answers.append({"op": "eqself", "args": [tag(v)], "out": outcome(lambda v=v: v == v)})
        k = rng.choice([0, 1, -1, 5, "a", "", "b"])
        if isinstance(v, dict) and v and rng.random() < 0.5:
            k = rng.choice(list(v))
        answers.append(
            {"op": "index", "args": [tag(v), tag(k)], "out": outcome(lambda v=v, k=k: v[k], True)}
        )
        # dumps of the text, loaded back
        text = json.dumps(v)
        answers.append(
            {"op": "loads", "args": [tag(text)], "out": outcome(lambda t=text: json.loads(t), True)}
        )
    for num in [0, 1, -1, 1.0, 2.0, True, False, 2**63, 1.5]:
        for other in [0, 1, 1.0, True, False, 2**63, float(2**63), 1.5, None, "1"]:
            answers.append(
                {
                    "op": "eq",
                    "args": [tag(num), tag(other)],
                    "out": outcome(lambda a=num, b=other: a == b),
                }
            )
    for text in VALID + INVALID:
        answers.append(
            {"op": "loads", "args": [tag(text)], "out": outcome(lambda t=text: json.loads(t), True)}
        )
    for _ in range(1500):  # mutated texts
        t = list(json.dumps(rand_value(rng)))
        for _ in range(rng.randint(1, 3)):
            i = rng.randrange(len(t) + 1)
            op = rng.random()
            if op < 0.4 and t:
                del t[min(i, len(t) - 1)]
            elif op < 0.8:
                t.insert(i, rng.choice(list('{}[],:"\\ \n0123456789.eE+-tfnaluxINy')))
            elif t:
                t[min(i, len(t) - 1)] = rng.choice(list('{}[],:"\\ .e-'))
        text = "".join(t)
        answers.append(
            {"op": "loads", "args": [tag(text)], "out": outcome(lambda t=text: json.loads(t), True)}
        )
    OUT.write_text(json.dumps(answers, separators=(",", ":")) + "\n")
    print(f"{len(answers)} answers -> {OUT}")


if __name__ == "__main__":
    main()
