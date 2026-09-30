"""Regenerates t2text_answers.json: what Python 3.14 answers for the string methods and
regular expressions compiled T2 procedures call (strs.go, regex.go). t2text_test.go and
regex_test.go assert against it. Run from the repository root:

    uv run python lattice/gogen/rt/testdata/gen_t2text_answers.py

* "sre": the SRE opcode, AT and CATEGORY numbers and MAXREPEAT of this Python (the Go
  engine's constants must equal them);
* "patterns": pattern -> its code as `lattice.gogen.regex.compile_regex` compiles it
  (with re.ASCII, as the Python target does), its group count and names;
* "regex": for each (pattern, input): search / match / fullmatch as the spans of every
  group (null when a group did not participate; null for no match), finditer's spans,
  findall (tuples for 2+ groups), split (with the groups, null when unmatched) and sub
  with a few replacements (the result, and the number of matches and longest match
  `T2Fuel.sub` charges from);
* "str": {"fn": method, "args": [...], "out": ...} for the Str methods, the result as
  JSON (a str, int, bool, list of str) or "!<Kind>: <message>".

Random patterns come from a small grammar over a few characters (seeded, so the file
is reproducible), with the ones the checker refuses (RE2 subset, L926) left out.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from lattice.check.re2 import re2_problem  # noqa: E402
from lattice.gogen.regex import compile_regex, compile_template  # noqa: E402

try:
    from re import _constants as _k  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_constants as _k  # type: ignore[no-redef]
import _sre  # noqa: E402

OUT = Path(__file__).with_name("t2text_answers.json")
ASCII = re.ASCII


def spans(m: re.Match[str] | None) -> list[list[int] | None] | None:
    if m is None:
        return None
    out: list[list[int] | None] = []
    for g in range(m.re.groups + 1):
        s = m.span(g)
        out.append(None if s == (-1, -1) else [s[0], s[1]])
    return out


def outcome(f):  # noqa: ANN001, ANN201
    try:
        return {"v": f()}
    except Exception as e:  # noqa: BLE001 - the answer is the exception
        kind = "PatternError" if isinstance(e, re.error) else type(e).__name__
        return {"exc": f"{kind}: {e}"}


# ---- regular expressions --------------------------------------------------------------

FIXED_PATTERNS = [
    r"(?P<word>[a-z]+)",
    r"[0-9]+",
    r"wor",
    r"(\w+)@(\w+)",
    r"(?P<a>[a-z]+)-(?P<b>[a-z]+)",
    r"x",
    r"([a-z]+)\d+",
    r",",
    r"\d+",
    r"zz",
    r"a*",
    r"|a",
    r"x*",
    r"$",
    r"^",
    r"\s*$",
    r"a$",
    r"(?m)^\w+$",
    r"(?s).+",
    r".+",
    r"\b",
    r"\B",
    r"\bfoo\b",
    r"(?i)straße",
    r"(?i)[a-z]+",
    r"(?i)É",
    r"(a)|b",
    r"(?:(a)|b)*",
    r"(a|)*",
    r"(a*)*",
    r"(a*)+",
    r"(a|b)*?c",
    r"(?:ab)*",
    r"(?:ab)+?",
    r"a{2,3}",
    r"a{2,3}?",
    r"(a{0,2}){2}",
    r"(?P<y>\d{4})-(?P<m>\d\d)",
    r"\s+",
    r"[\s\S]",
    r"[^\d\s]+",
    r"[é-ü]+",
    r"[Ā-￿]+",
    r"é|é",
    r"\Aab|b\Z",
    r"(?x) a  b # comment",
    r"a.c",
    r"(?s)a.c",
    r"[-a-c]",
    r"[^-a]",
    r"\W+",
    r"\D",
    r"\S+",
    r"((a)|(b))+",
    r"(a)(b)?",
    r"(?:a|ab)(?:c|bcd)(d*)",
]

FIXED_INPUTS = [
    "",
    "a",
    "ab",
    "hello",
    "héllo world",
    "alice@example",
    "foo-bar",
    "a1 bb22 ccc333",
    ",a,,b,",
    "12٣45",
    "baac",
    "a\n",
    "a\nb\n",
    "x\n\n",
    "foo bar_foo",
    "STRASSE Straße",
    "É é",
    "aab",
    "abcd",
    "2024-03 1999-12",
    "a \t\v\f\r\nb",
    "é é",
    "aaaa",
    "abab",
    "👋a👋",
]

_ODD = "".join(chr(0x100 + 2 * k) for k in range(14))  # 14 scattered code points
BIG_CLASSES = [
    f"[{_ODD}]",
    f"[^{_ODD}]+",
    f"(?i)[{_ODD}A-C]+",
    f"[Ѐ-ӿ{_ODD}]+",
]
DEEP_REPEATS = [r"(?:ab)*", r"(a|b)*", r"(?:(?:a|b)c)*", r"((?:ab)+c)*", r"(?:ab)*?$"]

REPLS = ["", "-", r"<\g<0>>", r"\1", "$1", r"[\\]", r"\n"]


def random_pattern(rng: random.Random, depth: int = 0) -> str:
    atoms = [
        "a",
        "b",
        "é",
        ".",
        r"\d",
        r"\w",
        r"\s",
        r"\W",
        "[ab]",
        "[^a]",
        "[a-c]",
        r"[\d\s]",
        "\\n",
        " ",
    ]
    anchors = ["^", "$", r"\A", r"\Z", r"\b", r"\B"]
    parts: list[str] = []
    for _ in range(rng.randint(1, 4)):
        r = rng.random()
        if r < 0.1:
            parts.append(rng.choice(anchors))
            continue
        if r < 0.3 and depth < 2:
            inner = random_pattern(rng, depth + 1)
            if rng.random() < 0.3:
                inner += "|" + random_pattern(rng, depth + 1)
            kind = rng.choice(["(", "(?:", f"(?P<g{rng.randint(0, 99)}>"])
            atom = kind + inner + ")"
        else:
            atom = rng.choice(atoms)
        q = rng.random()
        if q < 0.15:
            atom += "*"
        elif q < 0.25:
            atom += "+"
        elif q < 0.35:
            atom += "?"
        elif q < 0.4:
            atom += f"{{{rng.randint(0, 1)},{rng.randint(1, 3)}}}"
        if q < 0.4 and rng.random() < 0.3:
            atom += "?"
        parts.append(atom)
    out = "".join(parts)
    if depth == 0 and rng.random() < 0.15:
        out = rng.choice(["(?i)", "(?m)", "(?s)"]) + out
    return out


def random_input(rng: random.Random) -> str:
    pool = "aab é\nb1_A-"
    return "".join(rng.choice(pool) for _ in range(rng.randint(0, 8)))


def regex_answers() -> tuple[dict[str, object], list[dict[str, object]]]:
    rng = random.Random(20260929)
    pairs: list[tuple[str, str]] = []
    for i, p in enumerate(FIXED_PATTERNS):  # each against 12 of the inputs, in rotation
        for j in range(12):
            pairs.append((p, FIXED_INPUTS[(i * 7 + j) % len(FIXED_INPUTS)]))
    for p in (r"$", r"\s*$", r"a$", r"(?m)^\w+$", r"x*", r"a*", r"|a", r"\b", r"\B", r"(a|)*"):
        for s in ("", "a", "a\n", "a\nb\n", "x\n\n", "baac", "foo bar_foo", "\n"):
            pairs.append((p, s))
    # BIGCHARSET classes (characters beyond Latin-1), plain, negated and (?i)
    for p in BIG_CLASSES:
        for s in ("", "ĀāĂăÀa", "zĂĄ Ѐӿ Ԁ", "abcABCĚě", "Ā" * 3 + "￿\U0001f44b"):
            pairs.append((p, s))
    # the deep repeats (regex_test.go runs them over millions of characters too)
    for p in DEEP_REPEATS:
        for s in ("", "ab", "abab", "abac", "acbcac", "ababcabc", "abcababc", "aba"):
            pairs.append((p, s))
    seen: set[str] = set(FIXED_PATTERNS)
    while len(seen) < len(FIXED_PATTERNS) + 500:
        p = random_pattern(rng)
        if p in seen:
            continue
        try:
            re.compile(p, ASCII)
        except re.error:
            continue
        if re2_problem(p) is not None:
            continue
        seen.add(p)
        for _ in range(3):
            pairs.append((p, random_input(rng)))
    for p in BIG_CLASSES:
        assert _k.BIGCHARSET in compile_regex(p).code, p
    patterns: dict[str, object] = {}
    answers: list[dict[str, object]] = []
    for p, s in pairs:
        if p not in patterns:
            c = compile_regex(p)
            assert c.error is None, (p, c.error)
            patterns[p] = {"code": list(c.code), "groups": c.groups, "names": dict(c.names)}
        rx = re.compile(p, ASCII)
        a: dict[str, object] = {
            "pattern": p,
            "s": s,
            "search": spans(rx.search(s)),
            "match": spans(rx.match(s)),
            "fullmatch": spans(rx.fullmatch(s)),
            "finditer": [spans(m) for m in rx.finditer(s)],
        }
        a["findall"] = rx.findall(s)  # tuples (as lists) for 2+ groups
        a["split"] = rx.split(s)  # the groups too, None (null) when unmatched
        subs = []
        for repl in REPLS if p in FIXED_PATTERNS else REPLS[2:4]:
            t = compile_template(p, repl)
            res = outcome(lambda rx=rx, repl=repl, s=s: rx.sub(repl, s))
            subs.append({"repl": repl, "out": res, "tmpl_error": t.error is not None})
            if t.error is None:
                assert "exc" not in res, (p, repl, res)
            else:
                assert res.get("exc") == f"{t.error[0]}: {t.error[1]}", (p, repl, res, t.error)
        a["sub"] = subs
        ms = list(rx.finditer(s))
        a["sub_count"] = len(ms)
        a["sub_longest"] = max((m.end() - m.start() for m in ms), default=0)
        answers.append(a)
    return patterns, answers


# ---- string methods -----------------------------------------------------------------

TEXTS = [
    "",
    " ",
    "a",
    "hello",
    "  a \t b\n",
    "héllo👋",
    "banana",
    "key=value",
    "a=b=c",
    "novalue",
    "-42",
    "+42",
    "42",
    "-",
    "ab",
    "straße",
    "İ",
    "hello wORLD o'neil",
    "ΣΑΣ ΑΣ.",
    "ǅungla ǈ",
    "a\nb\r\nc",
    "a\rb\x0bc\x0cd\x1ce\x1df\x1eg\x85h i j",
    "line\n",
    "\n\n",
    "a,,b,",
    ",",
    "x  y  z  ",
    "　a b\xa0",
    "²³½",
    "٣4",
    "Ⅻ",
    "ABC",
    "Abc",
    "abc1",
    "1a",
    "ß",
    "ﬁ",
    "ŉ",
    "\t",
]
SEPS = ["", "a", "=", "an", "  ", "é", "👋", "\n"]
FILLS = ["*", "é", "👋", "", "ab"]


def str_answers() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []

    def add(fn: str, args: list[object], f) -> None:  # noqa: ANN001
        out.append({"fn": fn, "args": args, "out": outcome(f)})

    for s in TEXTS:
        for m in (
            "lower",
            "upper",
            "casefold",
            "title",
            "capitalize",
            "splitlines",
            "isdigit",
            "isalpha",
            "isalnum",
            "isspace",
            "islower",
            "isupper",
        ):
            add(m, [s], lambda s=s, m=m: getattr(s, m)())
        for m in ("strip", "lstrip", "rstrip"):
            add(m, [s], lambda s=s, m=m: getattr(s, m)())
            for chars in ("", "xy", " \t", "é👋"):
                add(m + "_chars", [s, chars], lambda s=s, m=m, c=chars: getattr(s, m)(c))
        for m in ("split", "rsplit"):
            add(m, [s], lambda s=s, m=m: getattr(s, m)())
            for sep in SEPS:
                add(m + "_sep", [s, sep], lambda s=s, m=m, sep=sep: getattr(s, m)(sep))
                for k in (-3, 1):
                    add(
                        m + "_sep_max",
                        [s, sep, k],
                        lambda s=s, m=m, sep=sep, k=k: getattr(s, m)(sep, k),
                    )
        for sep in SEPS:
            for m in (
                "startswith",
                "endswith",
                "find",
                "rfind",
                "count",
                "partition",
                "rpartition",
            ):
                add(m, [s, sep], lambda s=s, m=m, sep=sep: getattr(s, m)(sep))
            for new in ("", "X", "éé"):
                add("replace", [s, sep, new], lambda s=s, a=sep, b=new: s.replace(a, b))
                for k in (1,):
                    add(
                        "replace_count",
                        [s, sep, new, k],
                        lambda s=s, a=sep, b=new, k=k: s.replace(a, b, k),
                    )
        for w in (-1, 1, 5, 6):
            add("zfill", [s, w], lambda s=s, w=w: s.zfill(w))
            for m in ("ljust", "rjust", "center"):
                add(m, [s, w], lambda s=s, m=m, w=w: getattr(s, m)(w))
                for fill in FILLS:
                    add(
                        m + "_fill", [s, w, fill], lambda s=s, m=m, w=w, f=fill: getattr(s, m)(w, f)
                    )
    for sep in ("", ", ", "é"):
        for items in ([], ["a"], ["a", "b", "c"], ["", ""], ["👋", "é"]):
            add("join", [sep, items], lambda sep=sep, items=items: sep.join(items))
    return out


def main() -> None:
    patterns, regex = regex_answers()
    data = {
        "sre": {
            "opcodes": {str(o): int(o) for o in _k.OPCODES},
            "atcodes": {str(o): int(o) for o in _k.ATCODES},
            "chcodes": {str(o): int(o) for o in _k.CHCODES},
            "maxrepeat": _sre.MAXREPEAT,
            "magic": _k.MAGIC,
        },
        "patterns": patterns,
        "regex": regex,
        "str": str_answers(),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{OUT}: {len(patterns)} patterns, {len(regex)} regex, {len(data['str'])} str answers")


if __name__ == "__main__":
    main()
