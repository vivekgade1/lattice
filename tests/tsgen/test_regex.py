"""tsgen/regex.py: Python re.fullmatch(p, s, re.ASCII) == new RegExp(js, "u").test(s)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lattice.tsgen.regex import Untranslatable, fullmatch_source
from tests.tsgen.node import node_json

SAMPLES = [
    "", "a", "A", "ab", "a\n", "\na", "a\nb", "0", "9", "٣", "é", "_", " ", "\t", "\x0b",
    "\x85", "\xa0", " ", "K", "ſ", "k", "K", "s", "S", "foo", "foo bar",
    "x@y.z", "a@b", "it's5", "😀", "a😀b", "-", "]", "^", "\\", "aaa", "abab",
]  # fmt: skip
PATTERNS = [
    r"a", r"a$", r"^a", r"a\Z", r"\Aa", r".", r"(?s).", r".+", r"a.b", r"(?s)a.b",
    r"\d+", r"\D", r"\w+", r"\W", r"\s", r"\S+", r"\bfoo\b", r"foo\B.*", r"[a-z]+",
    r"[^a]", r"[^\d]", r"[\d\s]", r"[^\w\s]", r"(?i)k", r"(?i)s", r"(?i)[a-c]+",
    r"(?i)[^a]", r"(?m)^a$", r"(?m)a$\nb", r"a|b|", r"(ab)+", r"(?:ab){2}", r"a{2,}",
    r"a{,2}", r"a+?", r"a*b?", r"[\]\\^-]", r"[^@\s]+@[^@\s]+\.[^@\s]+", r"[a-z]+\d?",
    r"it's\d", r"😀+", r"[😀-😂]", r"(?x) a b # c", r"(?i:A)b", r"[K]",
]  # fmt: skip
SCRIPT = """
import { readFileSync } from "node:fs";
const cases = JSON.parse(readFileSync(0, "utf8"));
console.log(JSON.stringify(cases.map(([js, s]) => new RegExp(js, "u").test(s))));
"""


def test_matches_python(tmp_path: Path) -> None:
    cases, want = [], []
    for p in PATTERNS:
        js = fullmatch_source(p)
        for s in SAMPLES:
            cases.append([js, s])
            want.append(re.fullmatch(p, s, re.ASCII) is not None)
    got = node_json(SCRIPT, cases, cwd=tmp_path)
    bad = [(c, w) for c, g, w in zip(cases, got, want, strict=True) if g != w]
    assert not bad, bad[:20]


@pytest.mark.parametrize("p", [r"(?<=a)b", r"(a)\1", r"(?(1)a|b)", r"a++", r"(?>a)"])
def test_outside_re2_is_untranslatable(p: str) -> None:
    with pytest.raises(Untranslatable):
        fullmatch_source(p)
