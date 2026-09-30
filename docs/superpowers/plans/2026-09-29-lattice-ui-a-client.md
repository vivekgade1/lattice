# Lattice UI option A — Proven TypeScript Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **PARKED.** Do not start execution until the option 1 experiment has reported and the user has confirmed the project continues (memory `project-state`, `docs/option1-handoff.md`). On resume: `git -C .claude/worktrees/ui-target rebase go-target`, re-run the full suite, then start at Task 0.

**Goal:** Generate, from a checked Lattice API-profile program, a TypeScript client and React hooks whose request validation is proven identical to the server's by a differential gate.

**Architecture:** A new `lattice/tsgen/` package emits `api.ts` and `hooks.ts` from the lowered `EndpointPlan`s and vendors a zero-dependency runtime, `lattice-rt.ts`, which is a line-by-line port of the server prelude's `parse_params` (as `lattice/gogen/rt/params.go` is for Go). The client serializes each request to the exact wire form first, then runs `parseParams` on that wire. Four gate layers (L1 runtime fixtures, L2 direct differential, L3 HTTP differential, L4 types) prove equivalence against Python.

**Tech Stack:** Python 3.14 (existing repo), Node 22 (`node --test`, `--experimental-strip-types`), TypeScript ≥ 5.8 (`tsc --noEmit`, L4 only), React 19 and `@tanstack/react-query` 5 (peer dependencies of `hooks.ts`, L4 only), uvicorn (L3).

**Spec:** `docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md`

## Global Constraints

- Generated core (`lattice-rt.ts`, `api.ts`) has **zero runtime dependencies**; only `hooks.ts` imports `react` and `@tanstack/react-query`.
- TypeScript source must be **erasable syntax only** (no `enum`, `namespace`, parameter properties): it runs under `node --experimental-strip-types`. Relative imports use the `.ts` extension.
- `Int` in TS is `number` iff the param's `ge` and `le` are both set and within ±(2^53 − 1); otherwise `bigint`. Response `Int` columns are `bigint`.
- Equivalence bar: **0 mismatches**, no tolerance.
- Never modify `lattice/gogen/`, the 5a plan, or the go-target worktree. Go fixtures (`lattice/gogen/rt/testdata/prelude_answers.json`) are read as data only.
- Node-dependent tests skip when `node` is absent, and **fail** instead when `LATTICE_REQUIRE_NODE=1` (there is no CI in this repo; this env var is the "required" mode).
- Commits: `git commit -- <paths>` (only the task's paths), messages end with the session's `Co-Authored-By` line.
- Line length 100 (ruff). Match the surrounding code's docstring and comment density.

## Spec deltas (found while planning; Task 0 writes them into the spec)

1. **Auth runs before validation** (`lattice/api/codegen.py:1080-1086`): a server answers 401/403/inactive before any 422. Claim 2 therefore holds **for a caller the endpoint admits**; a local 422 predicts the server's answer once authentication passes. The L3 gate uses an admin token (the admin role is admitted everywhere, `codegen.py:867`).
2. **R-a widens:** a path `Str` that is empty, `.`, `..` or contains `/` cannot be routed faithfully (URL normalisation by `fetch`, Starlette routing). The client sends it; the corpus excludes it; the decision-log row names all four.
3. **Page envelope counts** (`limit`, `offset`, `total`) are `number`, decoded exactly (a value beyond 2^53 − 1 is a decode error, never rounded).
4. **Hooks take `opts` as a required argument** (no provider/context).
5. **CLI:** there is no `lattice codegen` command; the plan adds `python -m lattice.tsgen`.
6. **"Required in CI"** becomes `LATTICE_REQUIRE_NODE=1` (no CI config exists).
7. **Generation refusal code:** `L893`.

## Review Focus

1. **Unadmitted caller with invalid params** — server answers 401/403 first, client says 422 locally; expected: documented behaviour (spec delta 1), pinned by a test in Task 9.
2. **Path `Str` values `""`, `.`, `..`, containing `/`** — expected: client sends, server does not answer 422, no crash; pinned in Task 9.
3. **Lone surrogates in a Date/DateTime/Uuid value or in a path/query value** — expected: the client never accepts locally what the server rejects (TextEncoder turns them into U+FFFD, `fromisoformat` rejects them); pinned in Task 4 and Task 6.
4. **Non-safe JS numbers on the wire** (`2**60`, `-0`, `NaN`, `Infinity` for Int/Float params) — expected: the wire text Python parses to the same value the client validated; pinned in Task 3.
5. **Response drift** (missing/extra column, Int encoded as float, null in a non-nullable column) — expected: `Unexpected` with `reason: "decode"`, never a silently wrong value; pinned in Task 6.

---

## File structure

```
lattice/targets.py                          (modify) API_PARAM_RULES, PARAM_FEATURES, CLIENT_TARGETS, TS_REFUSAL
docs/lattice-language-rules.md              (modify) client target, R7.5, R14, decision-log rows
docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md  (modify) spec deltas
lattice/tsgen/__init__.py                   generate_ts, compile_source_to_ts
lattice/tsgen/__main__.py                   CLI
lattice/tsgen/regex.py                      RE2-subset pattern -> JS source
lattice/tsgen/specs.py                      ParamSpec -> fixture-format dict
lattice/tsgen/pyref.py                      the Python reference: prelude answers for wire cases
lattice/tsgen/emit.py                       api.ts and hooks.ts text
lattice/tsgen/rt/package.json               {"type": "module"}
lattice/tsgen/rt/lattice-rt.ts              the vendored runtime
lattice/tsgen/rt/replay.ts                  fixture replay (L1, L2)
lattice/tsgen/rt/rt.test.ts                 L1 (node:test)
lattice/tsgen/rt/testdata/gen_ts_answers.py JS-hazard fixture generator
lattice/tsgen/rt/testdata/ts_answers.json   generated
tests/tsgen/__init__.py
tests/tsgen/node.py                         need_node(), run_node()
tests/tsgen/test_governance.py
tests/tsgen/test_regex.py
tests/tsgen/test_rt.py                      runs L1
tests/tsgen/test_wire.py
tests/tsgen/test_emit.py
tests/tsgen/test_hooks.py
tests/tsgen/gate/__init__.py
tests/tsgen/gate/apps.py                    proof apps: plans, setup, admin token
tests/tsgen/gate/corpus.py                  wire + typed corpora
tests/tsgen/gate/corpus/*.json              checked-in corpora (generated)
tests/tsgen/gate/http_driver.ts             L3 node driver
tests/tsgen/test_gate_direct.py             L2
tests/tsgen/test_gate_http.py               L3
tests/ts/package.json, package-lock.json, tsconfig.json   L4 toolchain
tests/tsgen/test_types.py                   L4
```

---

### Task 0: Language rules, semantics table, spec deltas

**Files:**
- Modify: `lattice/targets.py`
- Modify: `docs/lattice-language-rules.md`
- Modify: `docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md`
- Test: `tests/tsgen/__init__.py` (empty), `tests/tsgen/test_governance.py`

**Interfaces:**
- Produces: `lattice.targets.API_PARAM_RULES: Mapping[str, str]` (ids `P1`..`P14`), `PARAM_FEATURES: tuple[str, ...]`, `ClientTarget`, `CLIENT_TARGETS: Mapping[str, ClientTarget]`, `TS_REFUSAL = "L893"`.

- [ ] **Step 1: Write the failing test**

`tests/tsgen/test_governance.py`:

```python
"""R14 checks for the client target (spec 2026-09-29 ui-a §6)."""

from __future__ import annotations

from pathlib import Path

from lattice.targets import API_PARAM_RULES, CLIENT_TARGETS, PARAM_FEATURES, TS_REFUSAL

ROOT = Path(__file__).resolve().parents[2]
RULES_DOC = (ROOT / "docs" / "lattice-language-rules.md").read_text()


def test_rule_ids_are_contiguous() -> None:
    assert list(API_PARAM_RULES) == [f"P{i}" for i in range(1, len(API_PARAM_RULES) + 1)]


def test_ts_client_carries_every_param_feature() -> None:
    assert CLIENT_TARGETS["ts"].param_features == frozenset(PARAM_FEATURES)


def test_every_client_gate_has_a_decision_log_row() -> None:
    assert TS_REFUSAL in RULES_DOC
    for row in ("Int typed by declared range", "path `Str`"):
        assert row in RULES_DOC, row
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/tsgen/test_governance.py -v`
Expected: FAIL with `ImportError: cannot import name 'API_PARAM_RULES'`

- [ ] **Step 3: Add the semantics table entries**

Append to `lattice/targets.py`:

```python
# ---- API request semantics (spec 2026-09-29 ui-a §4, §6) -----------------------------------
# The parameter rules every target carrying the API profile implements: parse_params in the
# Python prelude (lattice/api/codegen.py), rt/params.go on Go, lattice-rt.ts on the
# TypeScript client. Each id is pinned by fixture cases tagged with it (ts_answers.json).
API_PARAM_RULES: Mapping[str, str] = MappingProxyType(
    {
        "P1": "Str length counts code points",
        "P2": "a lone surrogate in a Str is 'must be valid UTF-8 text'",
        "P3": "Int/Float/Bool text is stripped of Python whitespace",
        "P4": "Int is signed 64-bit, exact from text and JSON (3.0 is 3)",
        "P5": "Float must be finite; NaN/Infinity tokens parse, then fail",
        "P6": "numbers in messages print as Python prints them",
        "P7": "pattern is re.fullmatch with re.ASCII over the RE2 subset",
        "P8": "one_of messages quote values as Python repr",
        "P9": "Date/DateTime parse as date/datetime.fromisoformat; naive is UTC",
        "P10": "Uuid is hyphenated or 32 hex digits, any case, canonical lowercase",
        "P11": "Bool text is true/1/yes/on or false/0/no/off, lowercased",
        "P12": "a JSON body is read as Python's json.loads reads bytes",
        "P13": "422 detail: parameters in order, first failure per parameter, exact loc/msg",
        "P14": "missing, null, UNSET and defaults as the prelude resolves them",
    }
)
PARAM_FEATURES = (
    "ge", "le", "min_len", "max_len", "one_of", "pattern", "email",
    "optional", "nullable", "default", "form",
)  # fmt: skip
TS_REFUSAL = "L893"  # the TypeScript client cannot be generated for a plan (decision log)


@dataclass(frozen=True)
class ClientTarget:
    """A target that carries the API request semantics (not T2): it validates requests
    exactly as the server does before sending them."""

    name: str
    param_features: frozenset[str]


CLIENT_TARGETS: Mapping[str, ClientTarget] = MappingProxyType(
    {"ts": ClientTarget("ts", frozenset(PARAM_FEATURES))}
)
```

- [ ] **Step 4: Update the language rules**

In `docs/lattice-language-rules.md`:

- In §1, after R2, add:

```markdown
**R2a. Client targets.** A client target (the TypeScript client) carries the *API request semantics* — parameter conversion and validation, wire formats, error bodies (`API_PARAM_RULES` in `lattice/targets.py`) — and not T2. It must accept exactly the requests the server accepts, with the same 422 detail, for a caller the endpoint admits.
```

- Replace R7 item 5 with: `5. differential tests at the gate level across every target that carries the construct (Python ≡ Go for T2; Python ≡ TS client for API parameters).`
- Append to R14's list: `- an API parameter feature (PARAM_FEATURES) or rule (API_PARAM_RULES) has no fixture case on a client target.`
- Append three rows to the §6 decision log:

```markdown
| 2026-09-29 | Representation (client) | Int typed by declared range: `number` when ge and le lie within ±(2^53−1), else `bigint`; response Int columns `bigint`; page counts `number`, decoded exactly | JS `number` is exact only to 2^53 | — | never (representation) |
| 2026-09-29 | Known limitation (client) | A path `Str` that is empty, `.`, `..` or contains `/` is sent unvalidated for routing | `fetch` normalises `.`/`..`; Starlette routes on the decoded path | use a query or body parameter | never (HTTP routing) |
| 2026-09-29 | Target gate (R4) | L893: the TS client cannot be generated for a plan (an untranslatable pattern, a non-finite bound, an unknown success kind) | emitter coverage | compile without `--target ts` | the emitter handles the construct |
```

- [ ] **Step 5: Record the spec deltas**

In the spec, add a section `## 10. Amendments (2026-09-29, from planning)` listing the seven "Spec deltas" above verbatim, and change §5.1 claim 2 to: "Over real HTTP, for a caller the endpoint admits, the client reports a local 422 exactly when the running server answers 422, with identical detail."

- [ ] **Step 6: Run the tests**

Run: `pytest tests/tsgen/test_governance.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add lattice/targets.py docs/lattice-language-rules.md docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md tests/tsgen/__init__.py tests/tsgen/test_governance.py
git commit -m "feat(targets): client target, API parameter rules P1-P14, L893 and decision-log rows" -- lattice/targets.py docs/lattice-language-rules.md docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md tests/tsgen/__init__.py tests/tsgen/test_governance.py
```

---

### Task 1: Node test helpers and the regex translator

**Files:**
- Create: `tests/tsgen/node.py`
- Create: `lattice/tsgen/__init__.py` (docstring only for now), `lattice/tsgen/regex.py`
- Test: `tests/tsgen/test_regex.py`

**Interfaces:**
- Produces: `tests.tsgen.node.need_node() -> str`, `run_node(args: list[str], *, cwd: Path, stdin: str | None = None, timeout: float = 600) -> subprocess.CompletedProcess[str]`, `node_json(script: str, payload: object, *, cwd: Path) -> object`.
- Produces: `lattice.tsgen.regex.fullmatch_source(pattern: str) -> str` (a JS source to be compiled with flag `u`, already anchored), `Untranslatable(Exception)`.

- [ ] **Step 1: Write the node helper**

`tests/tsgen/node.py`:

```python
"""Running Node from pytest (spec 2026-09-29 ui-a §5.4): skip without node, unless
LATTICE_REQUIRE_NODE=1, where a missing node fails the test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE_FLAGS = ["--experimental-strip-types", "--no-warnings=ExperimentalWarning"]


def need_node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("LATTICE_REQUIRE_NODE") == "1":
            pytest.fail("node is required (LATTICE_REQUIRE_NODE=1)")
        pytest.skip("node is not installed")
    return node


def run_node(
    args: list[str], *, cwd: Path, stdin: str | None = None, timeout: float = 600
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [need_node(), *NODE_FLAGS, *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def node_json(script: str, payload: object, *, cwd: Path) -> object:
    """Run `script` (an ES module reading JSON from stdin, printing JSON) on `payload`."""
    path = cwd / "_script.mjs"
    path.write_text(script)
    done = run_node([str(path)], cwd=cwd, stdin=json.dumps(payload))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
```

- [ ] **Step 2: Write the failing regex test**

`tests/tsgen/test_regex.py`:

```python
"""tsgen/regex.py: Python re.fullmatch(p, s, re.ASCII) == new RegExp(js, "u").test(s)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lattice.tsgen.regex import Untranslatable, fullmatch_source
from tests.tsgen.node import node_json

SAMPLES = [
    "", "a", "A", "ab", "a\n", "\na", "a\nb", "0", "9", "٣", "é", "_", " ", "\t", "\x0b",
    "\x85", "\xa0", " ", "K", "ſ", "k", "K", "s", "S", "foo", "foo bar",
    "x@y.z", "a@b", "it's5", "😀", "a😀b", "-", "]", "^", "\\", "aaa", "abab",
]  # fmt: skip
PATTERNS = [
    r"a", r"a$", r"^a", r"a\Z", r"\Aa", r".", r"(?s).", r".+", r"a.b", r"(?s)a.b",
    r"\d+", r"\D", r"\w+", r"\W", r"\s", r"\S+", r"\bfoo\b", r"foo\B.*", r"[a-z]+",
    r"[^a]", r"[^\d]", r"[\d\s]", r"[^\w\s]", r"(?i)k", r"(?i)s", r"(?i)[a-c]+",
    r"(?i)[^a]", r"(?m)^a$", r"(?m)a$\nb", r"a|b|", r"(ab)+", r"(?:ab){2}", r"a{2,}",
    r"a{,2}", r"a+?", r"a*b?", r"[\]\\^-]", r"[^@\s]+@[^@\s]+\.[^@\s]+", r"[a-z]+\d?",
    r"it's\d", r"😀+", r"[😀-😂]", r"(?x) a b # c", r"(?i:A)b", r"[K]",
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
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/tsgen/test_regex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lattice.tsgen'`

- [ ] **Step 4: Implement the translator**

`lattice/tsgen/__init__.py`:

```python
"""TypeScript client target (spec docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md)."""
```

`lattice/tsgen/regex.py`:

```python
"""RE2-subset pattern (Python `re` syntax, run with re.ASCII) -> JavaScript regex source
with the same fullmatch meaning (spec 2026-09-29 ui-a §4 row 7).

Translated from the parsed tree, never by text substitution: every literal becomes
`\\u{X}`, classes become alternations, ASCII categories become explicit classes, and
IGNORECASE folds ASCII letters only (JS's `i` flag folds Unicode, e.g. K and U+212A). The
result is compiled with the `u` flag and no other flag."""

from __future__ import annotations

import re
from typing import Any

try:  # Python 3.11+
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["Untranslatable", "fullmatch_source"]

_ALLOWED = re.IGNORECASE | re.DOTALL | re.MULTILINE | re.VERBOSE | re.ASCII
_WORD = "[A-Za-z0-9_]"
_CATS = {
    "CATEGORY_DIGIT": "[0-9]",
    "CATEGORY_NOT_DIGIT": "[^0-9]",
    "CATEGORY_WORD": _WORD,
    "CATEGORY_NOT_WORD": "[^A-Za-z0-9_]",
    "CATEGORY_SPACE": "[\\t\\n\\v\\f\\r ]",
    "CATEGORY_NOT_SPACE": "[^\\t\\n\\v\\f\\r ]",
}
_BOUNDARY = f"(?:(?<={_WORD})(?!{_WORD})|(?<!{_WORD})(?={_WORD}))"
_NOT_BOUNDARY = f"(?:(?<={_WORD})(?={_WORD})|(?<!{_WORD})(?!{_WORD}))"


class Untranslatable(Exception):
    """The pattern has no exact JS translation (outside the RE2 subset or an unknown flag)."""


def fullmatch_source(pattern: str) -> str:
    """JS source (flag `u`) that matches a whole string exactly when
    `re.fullmatch(pattern, s, re.ASCII)` does."""
    try:
        parsed = _p.parse(pattern, re.ASCII)
    except re.error as err:
        raise Untranslatable(str(err)) from None
    flags = parsed.state.flags
    if flags & ~_ALLOWED:
        raise Untranslatable(f"flags {flags:#x}")
    return "^(?:" + _seq(parsed, flags) + ")$"


def _seq(items: Any, flags: int) -> str:  # noqa: ANN401 - re._parser internals
    return "".join(_item(str(op), av, flags) for op, av in items)


def _esc(cp: int) -> str:
    return f"\\u{{{cp:X}}}"


def _lit(cp: int, flags: int) -> str:
    ch = chr(cp)
    if flags & re.IGNORECASE and ch.isascii() and ch.isalpha():
        return f"[{_esc(ord(ch.lower()))}{_esc(ord(ch.upper()))}]"
    return _esc(cp)


def _range(lo: int, hi: int, flags: int) -> list[str]:
    out = [f"[{_esc(lo)}-{_esc(hi)}]"]
    if flags & re.IGNORECASE:
        for a, b, shift in ((97, 122, -32), (65, 90, 32)):
            s, e = max(lo, a), min(hi, b)
            if s <= e:
                out.append(f"[{_esc(s + shift)}-{_esc(e + shift)}]")
    return out


def _class(items: Any, flags: int) -> str:  # noqa: ANN401
    negate = bool(items) and str(items[0][0]) == "NEGATE"
    alts: list[str] = []
    for op, av in items[1:] if negate else items:
        name = str(op)
        if name == "LITERAL":
            alts.append(_lit(av, flags))
        elif name == "RANGE":
            alts.extend(_range(av[0], av[1], flags))
        elif name == "CATEGORY":
            alts.append(_CATS[str(av)])
        else:
            raise Untranslatable(f"class item {name}")
    body = "(?:" + "|".join(alts) + ")"
    return f"(?!{body})[\\s\\S]" if negate else body


def _item(name: str, av: Any, flags: int) -> str:  # noqa: ANN401, C901, PLR0911, PLR0912
    if name == "LITERAL":
        return _lit(av, flags)
    if name == "NOT_LITERAL":
        return f"(?!{_lit(av, flags)})[\\s\\S]"
    if name == "ANY":
        return "[\\s\\S]" if flags & re.DOTALL else "[^\\n]"
    if name == "IN":
        return _class(av, flags)
    if name == "BRANCH":
        return "(?:" + "|".join(_seq(b, flags) for b in av[1]) + ")"
    if name == "SUBPATTERN":
        _group, add, delete, sub = av
        return "(?:" + _seq(sub, (flags | add) & ~delete) + ")"
    if name in ("MAX_REPEAT", "MIN_REPEAT"):
        lo, hi, sub = av
        top = "" if hi == _c.MAXREPEAT else str(hi)
        lazy = "?" if name == "MIN_REPEAT" else ""
        return "(?:" + _seq(sub, flags) + f"){{{lo},{top}}}" + lazy
    if name == "AT":
        at = str(av)
        multiline = bool(flags & re.MULTILINE)
        return {
            "AT_BEGINNING": "(?:^|(?<=\\n))" if multiline else "^",
            "AT_BEGINNING_STRING": "^",
            "AT_END": "(?=\\n|$)" if multiline else "(?=\\n?$)",
            "AT_END_STRING": "$",
            "AT_BOUNDARY": _BOUNDARY,
            "AT_NON_BOUNDARY": _NOT_BOUNDARY,
        }[at]
    raise Untranslatable(f"{name} is outside the RE2 subset")
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/tsgen/test_regex.py -v`
Expected: all passed. If a sample mismatches, fix the translation rule for that node type (never special-case a sample).

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(tsgen): node test helpers and the RE2-subset regex translator" -- tests/tsgen/node.py lattice/tsgen/__init__.py lattice/tsgen/regex.py tests/tsgen/test_regex.py
```

(`git add` the new files first.)

---

### Task 2: The Python reference and the JS-hazard fixtures

**Files:**
- Create: `lattice/tsgen/specs.py`, `lattice/tsgen/pyref.py`
- Create: `lattice/tsgen/rt/testdata/gen_ts_answers.py`, generated `lattice/tsgen/rt/testdata/ts_answers.json`
- Modify: `tests/tsgen/test_governance.py`

**Interfaces:**
- Produces: `lattice.tsgen.specs.spec_dict(spec: ParamSpec, null_ok: Sequence[str]) -> dict[str, object]`, `plan_specs(plan: EndpointPlan) -> list[dict[str, object]]`, `tag_default(v: object) -> dict[str, object]`. Spec dicts use the Go fixture format: keys `name, source, scalar` plus any of `ge, le, min_len, max_len, one_of, pattern, default, optional, nullable, email`.
- Produces: `lattice.tsgen.pyref.prelude() -> dict`, `tag(v) -> dict`, `to_param(d: dict) -> object`, `case(name, specs, *, path=None, query="", body=b"", form=False, ctype=None, rules=()) -> dict`, `answer(c: dict) -> dict`, `pattern_map(*case_lists) -> dict[str, str]`.
- Produces: `ts_answers.json` keys: `python`, `params` (cases, each with `rules`), `patterns` ({python pattern: js source} for every pattern in both fixture files), `floats` ([{bits, repr}]), `strs` ([{s, repr, strip, len}]), `json` ([{b64, value}]), `qs` ([{raw, pairs}]).

- [ ] **Step 1: Write `specs.py`**

```python
"""An endpoint parameter as the fixture-format dict every client tool shares (spec
2026-09-29 ui-a §4): the keys of the prelude's Param, exactly as codegen._param emits it."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from lattice.api.ir import EndpointPlan
from lattice.check.api_info import MISSING, UNSET, ParamSpec
from lattice.runtime import LatticeInternalError


def tag_default(v: object) -> dict[str, object]:
    """A default value, tagged as the fixtures tag parameter values."""
    if v is None:
        return {"t": "none"}
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": str(v)}
    if isinstance(v, float):
        return {"t": "float", "v": repr(v)}
    if isinstance(v, str):
        return {"t": "str", "v": v}
    if isinstance(v, dt.datetime):
        utc = v.astimezone(dt.UTC).replace(tzinfo=None)
        return {"t": "datetime", "v": utc.isoformat(timespec="microseconds") + "Z"}
    if isinstance(v, dt.date):
        return {"t": "date", "v": v.isoformat()}
    raise LatticeInternalError(f"default {v!r} has no tag")


def spec_dict(spec: ParamSpec, null_ok: Sequence[str]) -> dict[str, object]:
    if spec.scalar is None:
        raise LatticeInternalError(f"parameter '{spec.name}' has no scalar type")
    d: dict[str, object] = {"name": spec.name, "source": spec.source, "scalar": spec.scalar.value}
    for key in ("ge", "le", "min_len", "max_len"):
        value = getattr(spec, key)
        if value is not None:
            d[key] = value
    if spec.one_of is not None:
        d["one_of"] = list(spec.one_of)
    if spec.pattern is not None:
        d["pattern"] = spec.pattern
    if spec.default is UNSET:
        d["default"] = {"t": "unset"}
    elif spec.default is not MISSING:
        d["default"] = tag_default(spec.default)
    if spec.optional:
        d["optional"] = True
        if spec.name in null_ok:
            d["nullable"] = True
    if spec.email:
        d["email"] = True
    return d


def plan_specs(plan: EndpointPlan) -> list[dict[str, object]]:
    """The request parameters (not `user: CurrentUser`), in plan order."""
    return [spec_dict(p, plan.null_ok) for p in plan.params if p.source != "user"]
```

- [ ] **Step 2: Write `pyref.py`**

```python
"""The Python reference for client fixtures: the server prelude (lattice/api/codegen.py
_PRELUDE) run on wire cases, in the fixture format of lattice/gogen/rt/testdata. Imports
FastAPI; never imported by lattice.tsgen itself."""

from __future__ import annotations

import base64
import datetime as dt
import functools
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

from starlette.datastructures import QueryParams

from lattice.api.codegen import _PRELUDE
from lattice.tsgen.regex import fullmatch_source


@functools.cache
def prelude() -> dict[str, Any]:
    ns: dict[str, Any] = {}
    exec(compile(_PRELUDE, "prelude", "exec"), ns)  # noqa: S102 - the reference itself
    return ns


def tag(v: object) -> dict[str, object]:
    p = prelude()
    if v is p["UNSET"]:
        return {"t": "unset"}
    if v is None:
        return {"t": "none"}
    if isinstance(v, bool):
        return {"t": "bool", "v": v}
    if isinstance(v, int):
        return {"t": "int", "v": str(v)}
    if isinstance(v, float):
        return {"t": "float", "v": repr(v)}
    if isinstance(v, str):
        return {"t": "str", "v": v}
    if isinstance(v, dt.datetime):
        return {"t": "datetime", "v": p["datetime_text"](v)}
    if isinstance(v, dt.date):
        return {"t": "date", "v": v.isoformat()}
    if isinstance(v, uuid.UUID):
        return {"t": "uuid", "v": str(v)}
    raise TypeError(type(v))


def _untag(d: dict[str, Any]) -> object:
    t = d["t"]
    if t == "unset":
        return prelude()["UNSET"]
    if t == "none":
        return None
    if t == "int":
        return int(d["v"])
    if t == "float":
        return float(d["v"])
    if t == "date":
        return dt.date.fromisoformat(d["v"])
    if t == "datetime":
        return dt.datetime.fromisoformat(d["v"])
    return d["v"]


def to_param(d: dict[str, Any]) -> object:
    kw = {k: v for k, v in d.items() if k not in ("name", "source", "scalar", "default")}
    if "one_of" in kw:
        kw["one_of"] = tuple(kw["one_of"])
    if "default" in d:
        kw["default"] = _untag(d["default"])
    return prelude()["Param"](d["name"], d["source"], d["scalar"], **kw)


def case(
    name: str,
    specs: Sequence[dict[str, object]],
    *,
    path: dict[str, str] | None = None,
    query: str = "",
    body: bytes = b"",
    form: bool = False,
    ctype: str | None = None,
    rules: Iterable[str] = (),
) -> dict[str, object]:
    """A wire case: `query` is the raw query string as latin-1 text."""
    return {
        "name": name,
        "specs": list(specs),
        "path": path or {},
        "query": query,
        "body": base64.b64encode(body).decode(),
        "form": form,
        "ctype": ctype,
        "rules": list(rules),
    }


def _body_text(status: int, code: str, detail: object, style: str) -> str:
    p = prelude()
    p["ERROR_BODY"] = style
    try:
        return p["json_error"](status, code, detail).body.decode("utf-8")
    finally:
        p["ERROR_BODY"] = "lattice"


def answer(c: dict[str, Any]) -> dict[str, Any]:
    """`c` with the server's answer: `values` and `order`, or `status`, `lattice`, `fastapi`."""
    p = prelude()
    params = [to_param(s) for s in c["specs"]]
    query = dict(QueryParams(c["query"].encode("latin-1").decode("latin-1")))
    body = base64.b64decode(c["body"])
    out = dict(c)
    try:
        values = p["parse_params"](params, c["path"], query, body, c["form"], c["ctype"])
        out["values"] = {k: tag(v) for k, v in values.items()}
        out["order"] = list(values)
    except p["ErrorResponse"] as err:
        out["status"] = err.status
        out["lattice"] = _body_text(err.status, err.code, err.detail, "lattice")
        out["fastapi"] = _body_text(err.status, err.code, err.detail, "fastapi")
    return out


def pattern_map(*case_lists: Iterable[dict[str, Any]]) -> dict[str, str]:
    """{python pattern: JS source} for every pattern in the cases' specs."""
    out: dict[str, str] = {}
    for cases in case_lists:
        for c in cases:
            for s in c["specs"]:
                if "pattern" in s:
                    out[s["pattern"]] = fullmatch_source(s["pattern"])
    return dict(sorted(out.items()))
```

- [ ] **Step 3: Write the fixture generator**

`lattice/tsgen/rt/testdata/gen_ts_answers.py`:

```python
"""Generate ts_answers.json: Python answers for the JS hazards of spec 2026-09-29 ui-a §4.

Run from the repository root: python lattice/tsgen/rt/testdata/gen_ts_answers.py
Every params case carries the API_PARAM_RULES ids it pins (tests/tsgen/test_governance.py
fails when a rule has no case)."""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from starlette.datastructures import QueryParams  # noqa: E402

from lattice.tsgen.pyref import answer, case, pattern_map  # noqa: E402

HERE = Path(__file__).resolve().parent
GO = json.loads((ROOT / "lattice/gogen/rt/testdata/prelude_answers.json").read_text())


def sp(name: str, source: str, scalar: str, **kw: object) -> dict[str, object]:
    return {"name": name, "source": source, "scalar": scalar, **kw}


cases: list[dict[str, object]] = []


def add(name: str, specs: list[dict[str, object]], rules: tuple[str, ...], **kw: object) -> None:
    cases.append(case(name, specs, rules=rules, **kw))  # type: ignore[arg-type]


LEN = sp("s", "query", "Str", min_len=2, max_len=3)
for q in ["s=%F0%9F%91%8B%F0%9F%91%8B", "s=%F0%9F%91%8B", "s=ab%F0%9F%91%8B",
          "s=abcd", "s=%F0%9F%91%8Ba%F0%9F%91%8Bb", "s=%E2%80%8B%E2%80%8B"]:  # fmt: skip
    add(f"len {q}", [LEN], ("P1",), query=q)
BODY_S = sp("s", "body", "Str", max_len=5)
for b in [b'{"s": "\\ud800"}', b'{"s": "a\\ud83d\\ude00"}', b'{"s": "\\udc00\\ud800"}',
          b'{"s": "\\ud83d"}', '{"s": "\U0001f600\U0001f600"}'.encode(),
          b'{"s": "\xed\xa0\x80"}']:  # fmt: skip
    add(f"surrogate {b!r}", [BODY_S], ("P1", "P2", "P12"), body=b)
INT_Q, FLOAT_Q, BOOL_Q = sp("n", "query", "Int"), sp("x", "query", "Float"), sp("b", "query", "Bool")
for pad in ["%1C", "%1F", "%C2%85", "%EF%BB%BF", "%E3%80%80", "%C2%A0", "%0B", "%E2%80%8B"]:
    add(f"strip int {pad}", [INT_Q], ("P3",), query=f"n={pad}5{pad}")
    add(f"strip float {pad}", [FLOAT_Q], ("P3",), query=f"x={pad}1.5")
    add(f"strip bool {pad}", [BOOL_Q], ("P3", "P11"), query=f"b={pad}true")
INT_B = sp("n", "body", "Int")
for b in [b'{"n": 9007199254740993}', b'{"n": 9007199254740993.0}', b'{"n": 3.0}',
          b'{"n": -0.0}', b'{"n": 1e300}', b'{"n": 9223372036854775808}',
          b'{"n": -9223372036854775809}', b'{"n": 1.5}', b'{"n": true}', b'{"n": "5"}',
          b'{"n": NaN}', b'{"n": ' + b"9" * 4301 + b"}"]:  # fmt: skip
    add(f"int body {b[:40]!r}", [INT_B], ("P4", "P12"), body=b)
for q in ["n=9007199254740993", "n=-9223372036854775808", "n=00009", "n=+0", "n=-0"]:
    add(f"int text {q}", [INT_Q], ("P4",), query=q)
FLOAT_B = sp("x", "body", "Float")
for b in [b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b'{"x": 1e400}',
          b'{"x": 1' + b"0" * 400 + b"}", b'{"x": 9007199254740993}', b'{"x": -0.0}',
          b'{"x": 1e-400}']:  # fmt: skip
    add(f"float body {b[:40]!r}", [FLOAT_B], ("P5", "P12"), body=b)
for q in ["x=nan", "x=inf", "x=1e400", "x=5.", "x=.5", "x=+.5", "x=1E5", "x=1e-400"]:
    add(f"float text {q}", [FLOAT_Q], ("P5",), query=q)
for ge, le, x, n in [(2.5e-5, None, "0", "0"), (None, 1.0000000000000002, "2", "2"),
                     (1e16, None, "0", "0"), (1e22, None, "0", "0"), (0.1, None, "0", "0"),
                     (-1.5e-7, None, "-1", "-1"),
                     (None, 123456789012345680.0, "1e18", "123456789012345681")]:  # fmt: skip
    kw = {k: v for k, v in (("ge", ge), ("le", le)) if v is not None}
    add(f"message float {kw}", [sp("x", "query", "Float", **kw)], ("P6",), query=f"x={x}")
    add(f"message int {kw}", [sp("n", "query", "Int", **kw)], ("P6",), query=f"n={n}")
ONE = sp("s", "query", "Str", one_of=["it's", 'say "hi"', "both'\"", "tab\t", "é", "\x7f",
                                      "​", "\U0001f600", "back\\slash", "퟿"])  # fmt: skip
for q in ["s=zz", "s=%C3%A9", "s=tab%09"]:
    add(f"one_of {q}", [ONE], ("P8",), query=q)
for pat, qs in [
    (r"a$", ["a", "a%0A"]), (r"a.b", ["a%0Ab", "axb"]), (r"\s+", ["%C2%85", "%C2%A0", "%0B", "%20"]),
    (r"\w+", ["%C3%A9", "a_1"]), (r"\bfoo\b", ["foo"]), (r"(?i)k", ["%E2%84%AA", "K"]),
    (r"(?i)s", ["%C5%BF", "S"]), (r"[^a]", ["%0A", "b"]), (r"\d", ["%D9%A3", "3"]),
    (r"(?s).+", ["a%0Ab"]), (r"(?m)a$\nb", ["a%0Ab"]), (r"it's\d", ["it's5"]),
]:  # fmt: skip
    for q in qs:
        add(f"pattern {pat!r} {q}", [sp("s", "query", "Str", pattern=pat)], ("P7",), query=f"s={q}")
for q in ["t=2026-09-28T10:11:12.1234567", "t=2026-09-28T24:00", "t=2026-02-28T24:00",
          "t=0001-01-01T00:00:00%2B01:00", "t=9999-12-31T23:59:59-01:00", "t=2026-W53-1",
          "t=2020-W53-7T01", "t=20260928T101112", "t=2026-09-28%E2%80%8310:00"]:  # fmt: skip
    add(f"datetime {q}", [sp("t", "query", "DateTime")], ("P9",), query=q)
for q in ["d=2026-09-28", "d=20260928", "d=2026-W01-1", "d=2026-02-30", "d=2026-09-28T00"]:
    add(f"date {q}", [sp("d", "query", "Date")], ("P9",), query=q)
add("datetime surrogate body", [sp("t", "body", "DateTime")], ("P2", "P9"),
    body=b'{"t": "2026-09-28\\ud80010:00"}')  # fmt: skip
for q in ["u=0123456789ABCDEF0123456789ABCDEF", "u=01234567-89ab-cdef-0123-456789abcdef",
          "u=01234567-89abcdef-0123-456789abcdef", "u=0123456789abcdef0123456789abcde"]:
    add(f"uuid {q}", [sp("u", "query", "Uuid")], ("P10",), query=q)
for q in ["b=TRUE", "b=Yes", "b=oN", "b=0", "b=OFF", "b=maybe", "b=", "b=%C4%B0"]:
    add(f"bool {q}", [BOOL_Q], ("P11",), query=q)
TWO = [sp("a", "query", "Int", ge=1.0), sp("b", "body", "Str", max_len=1), sp("c", "path", "Uuid")]
add("detail order", TWO, ("P13",), path={"c": "nope"}, query="a=0", body=b'{"b": "xy"}')
add("detail first failure", [sp("s", "query", "Str", min_len=5, pattern="[a-z]+")], ("P13",),
    query="s=A")  # fmt: skip
add("body not object", TWO, ("P13", "P12"), path={"c": "x"}, body=b"[1]")
OPT = sp("s", "body", "Str", default={"t": "unset"}, optional=True)
OPT_NULL = sp("s", "body", "Str", default={"t": "unset"}, optional=True, nullable=True)
DEF_NONE = sp("s", "body", "Str", default={"t": "none"})
DEF_INT = sp("n", "query", "Int", default={"t": "int", "v": "7"})
for specs, b in [([OPT], b'{"s": null}'), ([OPT], b"{}"), ([OPT_NULL], b'{"s": null}'),
                 ([DEF_NONE], b'{"s": null}'), ([DEF_NONE], b"")]:  # fmt: skip
    add(f"null {specs[0].get('nullable')} {b!r}", specs, ("P14",), body=b)
add("default int", [DEF_INT], ("P14",), query="")
FORM = [sp("s", "body", "Str", max_len=1), sp("n", "body", "Int")]
add("form hazards", FORM, ("P1", "P3", "P12"), body=b"s=%F0%9F%91%8B&n=%1C5", form=True,
    ctype="application/x-www-form-urlencoded; charset=utf-8")  # fmt: skip

params = [answer(c) for c in cases]

floats = []
for x in [0.1, 1e16, 1e-5, 1.5e-7, 123456789012345680.0, 1e22, 5e-324, 1.7976931348623157e308,
          -0.0, 0.0, 2.0**53, 2.0**53 + 2, 1 / 3, 100.0, 1e15, 9.999999999999999e15]:  # fmt: skip
    floats.append({"bits": struct.pack(">d", x).hex(), "repr": repr(x)})
strs = []
for s in ["it's", 'say "hi"', "both'\"", "\x00\x1f\x7f\x80\x9f\xa0\xad", "​ 　",
          "\ud800", "\U0001f600", "\U000e0001", "\x1c5\x1f", "\x85x\xa0", "﻿x", " \t\n"]:  # fmt: skip
    strs.append({"s": s, "repr": repr(s), "strip": s.strip(), "len": len(s)})


def jtag(v: object) -> object:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return {"int": str(v)}
    if isinstance(v, float):
        return {"float": repr(v)}
    if isinstance(v, str):
        return {"str": v}
    if isinstance(v, list):
        return [jtag(x) for x in v]
    return {"obj": [[k, jtag(x)] for k, x in v.items()]}  # type: ignore[union-attr]


json_cases = []
for raw in [b'{"a": 1}', b"\xef\xbb\xbf{}", '{"a": "é"}'.encode("utf-16"),
            '{"a": "é"}'.encode("utf-16-le"), '{"a": "é"}'.encode("utf-16-be"),
            '{"a": 1}'.encode("utf-32"), '{"a": 1}'.encode("utf-32-le"), b'"\xed\xa0\x80"',
            b"NaN", b"-Infinity", b"[1, 1.0, -0, -0.0, 1e2, 1E+2]", b"01", b"1.", b"-",
            b'"a\x01"', b'{"a": 1, "a": 2, "b": 3}', b'"\\ud800"', b'"\\ud83d\\ude00"',
            b" \t\n\r1\r\n", b"1 2", b"\x0c1", b"9" * 4301, b"[" * 50 + b"]" * 50,
            b'"\\/"', b'"\\x"', b"\xff"]:  # fmt: skip
    try:
        value: object = {"ok": jtag(json.loads(raw))}
    except ValueError:
        value = {"error": True}
    json_cases.append({"b64": base64.b64encode(raw).decode(), "value": value})

qs = []
for raw in ["a=1&b=2", "a=1&a=2", "a", "a=", "=1", "a=%zz", "a=%C3", "a=%C3%A9", "a+b=c+d",
            "a=\xe9", "&&a=1&&", "a=%E2%82", "a=%F0%9F%91%8B", "a=1;b=2"]:  # fmt: skip
    qs.append({"raw": raw, "pairs": dict(QueryParams(raw))})

out = {
    "python": sys.version.split()[0],
    "params": params,
    "patterns": pattern_map(params, GO["params"]),
    "floats": floats,
    "strs": strs,
    "json": json_cases,
    "qs": qs,
}
(HERE / "ts_answers.json").write_text(json.dumps(out, indent=1, ensure_ascii=True) + "\n")
print(f"wrote ts_answers.json ({len(params)} parameter cases)")
```

- [ ] **Step 4: Generate the fixtures**

Run: `python lattice/tsgen/rt/testdata/gen_ts_answers.py`
Expected: `wrote ts_answers.json (N parameter cases)` with N ≥ 150.

- [ ] **Step 5: Add the R14 fixture-coverage tests**

Append to `tests/tsgen/test_governance.py`:

```python
import json

TS = json.loads((ROOT / "lattice/tsgen/rt/testdata/ts_answers.json").read_text())
GO = json.loads((ROOT / "lattice/gogen/rt/testdata/prelude_answers.json").read_text())


def test_every_rule_has_a_fixture_case() -> None:
    covered = {r for c in TS["params"] for r in c["rules"]}
    assert set(API_PARAM_RULES) <= covered, set(API_PARAM_RULES) - covered


def test_every_param_feature_has_a_fixture_case() -> None:
    used = {k for c in TS["params"] + GO["params"] for s in c["specs"] for k in s}
    if any(c["form"] for c in TS["params"] + GO["params"]):
        used.add("form")
    assert set(PARAM_FEATURES) <= used, set(PARAM_FEATURES) - used


def test_every_fixture_pattern_is_translated() -> None:
    pats = {s["pattern"] for c in TS["params"] + GO["params"] for s in c["specs"] if "pattern" in s}
    assert pats == set(TS["patterns"])
```

Run: `pytest tests/tsgen/test_governance.py -v` — Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add lattice/tsgen/specs.py lattice/tsgen/pyref.py lattice/tsgen/rt/testdata
git commit -m "feat(tsgen): Python reference answers and the JS-hazard fixtures" -- lattice/tsgen/specs.py lattice/tsgen/pyref.py lattice/tsgen/rt/testdata tests/tsgen/test_governance.py
```

---

### Task 3: `lattice-rt.ts` core — strings, floats, JSON, query strings, wire

**Files:**
- Create: `lattice/tsgen/rt/package.json`, `lattice/tsgen/rt/lattice-rt.ts`, `lattice/tsgen/rt/rt.test.ts`
- Create: `tests/tsgen/test_rt.py`, `tests/tsgen/test_wire.py`

**Interfaces:**
- Consumes: `ts_answers.json` (`floats`, `strs`, `json`, `qs`).
- Produces (exports of `lattice-rt.ts`): `cpLen(s): number`, `pyStrip(s): string`, `pyRepr(s): string`, `reprFloat(x): string`, `num(v): string`, `INT_MIN`, `INT_MAX` (bigint), types `JsonVal`, `JsonStr`, `JsonObj`, `isJsonStr`, `isJsonObj`, `loadsJson(b: Uint8Array): JsonVal | undefined` (undefined = Python raises), `dumpsWire(v: unknown): string`, `wireText(v: unknown): string`, `pctEncode(s): string`, `parseQS(raw): Map<string, string>`, `unquote(s): string`, `latin1(b: Uint8Array): string`, `DecodeError`.

- [ ] **Step 1: Write the failing tests**

`lattice/tsgen/rt/package.json`:

```json
{ "type": "module", "private": true }
```

`lattice/tsgen/rt/rt.test.ts`:

```ts
// L1 (spec 2026-09-29 ui-a §5.4): lattice-rt.ts against Python-generated answers.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as rt from "./lattice-rt.ts";

const load = (rel: string) => JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8"));
export const GO = load("../../gogen/rt/testdata/prelude_answers.json");
export const TS = load("./testdata/ts_answers.json");
const b64 = (s: string) => new Uint8Array(Buffer.from(s, "base64"));

function jtag(v: rt.JsonVal): unknown {
  if (v === null || typeof v === "boolean") return v;
  if (typeof v === "bigint") return { int: v.toString() };
  if (typeof v === "number") return { float: rt.reprFloat(v) };
  if (Array.isArray(v)) return v.map(jtag);
  if (rt.isJsonStr(v)) return { str: v.s };
  return { obj: v.keys.map((k) => [k, jtag(v.vals.get(k)!)]) };
}

test("reprFloat", () => {
  for (const c of TS.floats) {
    const x = new DataView(Buffer.from(c.bits, "hex").buffer).getFloat64(0);
    assert.equal(rt.reprFloat(x), c.repr, c.bits);
  }
});

test("strings: repr, strip, len", () => {
  for (const c of TS.strs) {
    assert.equal(rt.pyRepr(c.s), c.repr, c.repr);
    assert.equal(rt.pyStrip(c.s), c.strip, c.repr);
    assert.equal(rt.cpLen(c.s), c.len, c.repr);
  }
});

test("loadsJson", () => {
  for (const c of TS.json) {
    const v = rt.loadsJson(b64(c.b64));
    assert.deepEqual(v === undefined ? { error: true } : { ok: jtag(v) }, c.value, c.b64);
  }
});

test("parseQS", () => {
  for (const c of TS.qs) assert.deepEqual(Object.fromEntries(rt.parseQS(c.raw)), c.pairs, c.raw);
});
```

`tests/tsgen/test_rt.py`:

```python
"""L1: node --test over lattice/tsgen/rt (spec 2026-09-29 ui-a §5.4)."""

from __future__ import annotations

from pathlib import Path

from tests.tsgen.node import run_node

RT = Path(__file__).resolve().parents[2] / "lattice" / "tsgen" / "rt"


def test_runtime_fixtures() -> None:
    done = run_node(["--test", "rt.test.ts"], cwd=RT)
    assert done.returncode == 0, done.stdout[-6000:] + done.stderr[-2000:]
```

`tests/tsgen/test_wire.py` (Review Focus 4):

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/tsgen/test_rt.py tests/tsgen/test_wire.py -v`
Expected: FAIL (`lattice-rt.ts` does not exist).

- [ ] **Step 3: Implement the core**

`lattice/tsgen/rt/lattice-rt.ts`:

```ts
// lattice-rt.ts — the Lattice client runtime (spec
// docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md §4). Vendored into every
// generated client; no dependencies. A port of the server prelude's parameter parsing
// (lattice/api/codegen.py _PRELUDE), as lattice/gogen/rt/params.go is for Go.

// ---- strings ------------------------------------------------------------------------------

/** len(s): code points (a lone surrogate counts one). */
export function cpLen(s: string): number {
  let n = 0;
  for (const _ of s) n++;
  return n;
}

// str.isspace(): bidi WS/B/S or category Zs.
const PY_SPACE = new Set<number>([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
  0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
  0x205f, 0x3000,
]);

/** s.strip() */
export function pyStrip(s: string): string {
  const cps = Array.from(s);
  let i = 0;
  let j = cps.length;
  while (i < j && PY_SPACE.has(cps[i].codePointAt(0)!)) i++;
  while (j > i && PY_SPACE.has(cps[j - 1].codePointAt(0)!)) j--;
  return cps.slice(i, j).join("");
}

const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u;
const hex = (prefix: string, cp: number, width: number) =>
  prefix + cp.toString(16).padStart(width, "0");

/** repr(s) */
export function pyRepr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (let i = 0; i < s.length; ) {
    const cp = s.codePointAt(i)!;
    i += cp > 0xffff ? 2 : 1;
    const ch = String.fromCodePoint(cp);
    if (ch === quote || ch === "\\") out += "\\" + ch;
    else if (cp === 9) out += "\\t";
    else if (cp === 10) out += "\\n";
    else if (cp === 13) out += "\\r";
    else if (cp < 0x20 || cp === 0x7f) out += hex("\\x", cp, 2);
    else if (cp < 0x7f) out += ch;
    else if (NOT_PRINTABLE.test(ch))
      out += cp <= 0xff ? hex("\\x", cp, 2) : cp <= 0xffff ? hex("\\u", cp, 4) : hex("\\U", cp, 8);
    else out += ch;
  }
  return out + quote;
}

// ---- numbers ------------------------------------------------------------------------------

export const INT_MIN = -(1n << 63n);
export const INT_MAX = (1n << 63n) - 1n;

/** repr(x) for a float: the shortest round-trip digits, Python's layout. */
export function reprFloat(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (x === Infinity) return "inf";
  if (x === -Infinity) return "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0.0" : "0.0";
  const [mant, expText] = Math.abs(x).toExponential().split("e");
  const digits = mant.replace(".", "");
  const exp = Number(expText);
  const sign = x < 0 ? "-" : "";
  if (exp < -4 || exp >= 16) {
    const m = digits.length > 1 ? digits[0] + "." + digits.slice(1) : digits;
    const e = String(Math.abs(exp)).padStart(2, "0");
    return `${sign}${m}e${exp < 0 ? "-" : "+"}${e}`;
  }
  if (exp < 0) return sign + "0." + "0".repeat(-exp - 1) + digits;
  if (digits.length <= exp + 1) return sign + digits + "0".repeat(exp + 1 - digits.length) + ".0";
  return sign + digits.slice(0, exp + 1) + "." + digits.slice(exp + 1);
}

/** The prelude's _num: a bound as Python prints it. */
export function num(v: number): string {
  return Number.isInteger(v) ? BigInt(v).toString() : reprFloat(v);
}

// ---- JSON (json.loads of request bytes) ---------------------------------------------------

export type JsonStr = { kind: "str"; s: string; bad: boolean };
export type JsonObj = { kind: "obj"; keys: string[]; vals: Map<string, JsonVal> };
export type JsonVal = null | boolean | bigint | number | JsonStr | JsonVal[] | JsonObj;
export const isJsonStr = (v: unknown): v is JsonStr =>
  typeof v === "object" && v !== null && (v as JsonStr).kind === "str";
export const isJsonObj = (v: unknown): v is JsonObj =>
  typeof v === "object" && v !== null && (v as JsonObj).kind === "obj";

export class DecodeError extends Error {}

function fromCodePoints(cps: readonly number[]): string {
  let out = "";
  for (const cp of cps) out += String.fromCodePoint(cp);
  return out;
}

function decodeUtf8Pass(b: Uint8Array, start: number): number[] | null {
  const out: number[] = [];
  for (let i = start; i < b.length; ) {
    const c = b[i];
    if (c < 0x80) {
      out.push(c);
      i++;
      continue;
    }
    let need = 0;
    let cp = 0;
    let lo = 0x80;
    let hi = 0xbf;
    if (c >= 0xc2 && c <= 0xdf) [need, cp] = [1, c & 0x1f];
    else if (c >= 0xe0 && c <= 0xef) {
      [need, cp] = [2, c & 0x0f];
      if (c === 0xe0) lo = 0xa0; // "surrogatepass": ED A0..BF is a lone surrogate
    } else if (c >= 0xf0 && c <= 0xf4) {
      [need, cp] = [3, c & 0x07];
      if (c === 0xf0) lo = 0x90;
      if (c === 0xf4) hi = 0x8f;
    } else return null;
    if (i + need >= b.length) return null;
    for (let k = 1; k <= need; k++) {
      const d = b[i + k];
      if (d < (k === 1 ? lo : 0x80) || d > (k === 1 ? hi : 0xbf)) return null;
      cp = (cp << 6) | (d & 0x3f);
    }
    out.push(cp);
    i += need + 1;
  }
  return out;
}

function decodeUtf16(b: Uint8Array, start: number, be: boolean): number[] | null {
  if ((b.length - start) % 2 !== 0) return null;
  const units: number[] = [];
  for (let i = start; i < b.length; i += 2) units.push(be ? (b[i] << 8) | b[i + 1] : (b[i + 1] << 8) | b[i]);
  const out: number[] = [];
  for (let i = 0; i < units.length; i++) {
    const u = units[i];
    const lo = units[i + 1];
    if (u >= 0xd800 && u < 0xdc00 && lo !== undefined && lo >= 0xdc00 && lo < 0xe000) {
      out.push(0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00));
      i++;
    } else out.push(u);
  }
  return out;
}

function decodeUtf32(b: Uint8Array, start: number, be: boolean): number[] | null {
  if ((b.length - start) % 4 !== 0) return null;
  const out: number[] = [];
  for (let i = start; i < b.length; i += 4) {
    const u = be
      ? ((b[i] << 24) >>> 0) + (b[i + 1] << 16) + (b[i + 2] << 8) + b[i + 3]
      : ((b[i + 3] << 24) >>> 0) + (b[i + 2] << 16) + (b[i + 1] << 8) + b[i];
    if (u > 0x10ffff) return null;
    out.push(u);
  }
  return out;
}

/** json.detect_encoding, then decode with "surrogatepass". */
function decodeJsonBytes(b: Uint8Array): number[] | null {
  const has = (...p: number[]) => b.length >= p.length && p.every((x, i) => b[i] === x);
  if (has(0, 0, 0xfe, 0xff)) return decodeUtf32(b, 4, true);
  if (has(0xff, 0xfe, 0, 0)) return decodeUtf32(b, 4, false);
  if (has(0xfe, 0xff)) return decodeUtf16(b, 2, true);
  if (has(0xff, 0xfe)) return decodeUtf16(b, 2, false);
  if (has(0xef, 0xbb, 0xbf)) return decodeUtf8Pass(b, 3);
  if (b.length >= 4) {
    if (b[0] === 0) return b[1] !== 0 ? decodeUtf16(b, 0, true) : decodeUtf32(b, 0, true);
    if (b[1] === 0) return b[2] !== 0 || b[3] !== 0 ? decodeUtf16(b, 0, false) : decodeUtf32(b, 0, false);
  } else if (b.length === 2) {
    if (b[0] === 0) return decodeUtf16(b, 0, true);
    if (b[1] === 0) return decodeUtf16(b, 0, false);
  }
  return decodeUtf8Pass(b, 0);
}

class JsonParser {
  i = 0;
  s: number[];
  constructor(s: number[]) {
    this.s = s;
  }
  ws(): void {
    while (this.i < this.s.length && [0x20, 0x09, 0x0a, 0x0d].includes(this.s[this.i])) this.i++;
  }
  lit(word: string): boolean {
    for (let k = 0; k < word.length; k++) if (this.s[this.i + k] !== word.charCodeAt(k)) return false;
    this.i += word.length;
    return true;
  }
  value(): JsonVal | undefined {
    const c = this.s[this.i];
    if (c === undefined) return undefined;
    if (c === 0x7b) return this.object();
    if (c === 0x5b) return this.array();
    if (c === 0x22) {
      this.i++;
      return this.str();
    }
    if (c === 0x6e && this.lit("null")) return null;
    if (c === 0x74 && this.lit("true")) return true;
    if (c === 0x66 && this.lit("false")) return false;
    if (c === 0x4e && this.lit("NaN")) return NaN;
    if (c === 0x49 && this.lit("Infinity")) return Infinity;
    if (c === 0x2d && this.s[this.i + 1] === 0x49) {
      this.i++;
      return this.lit("Infinity") ? -Infinity : undefined;
    }
    if (c === 0x2d || (c >= 0x30 && c <= 0x39)) return this.number();
    return undefined;
  }
  digits(): number {
    const start = this.i;
    while (this.i < this.s.length && this.s[this.i] >= 0x30 && this.s[this.i] <= 0x39) this.i++;
    return this.i - start;
  }
  number(): JsonVal | undefined {
    const start = this.i;
    if (this.s[this.i] === 0x2d) this.i++;
    if (this.i >= this.s.length) return undefined;
    if (this.s[this.i] === 0x30) this.i++;
    else if (this.digits() === 0) return undefined;
    let isFloat = false;
    const d = this.s[this.i + 1];
    if (this.s[this.i] === 0x2e && d !== undefined && d >= 0x30 && d <= 0x39) {
      this.i++;
      this.digits();
      isFloat = true;
    }
    if (this.s[this.i] === 0x65 || this.s[this.i] === 0x45) {
      const save = this.i;
      this.i++;
      if (this.s[this.i] === 0x2b || this.s[this.i] === 0x2d) this.i++;
      if (this.digits() === 0) this.i = save;
      else isFloat = true;
    }
    const text = fromCodePoints(this.s.slice(start, this.i));
    if (isFloat) return Number(text);
    if (text.replace("-", "").length > 4300) return undefined; // CPython's int digit limit
    return BigInt(text);
  }
  hex4(): number | undefined {
    if (this.i + 4 > this.s.length) return undefined;
    let u = 0;
    for (let k = 0; k < 4; k++) {
      const c = this.s[this.i + k];
      const d = c >= 0x30 && c <= 0x39 ? c - 0x30 : c >= 0x61 && c <= 0x66 ? c - 87 : c >= 0x41 && c <= 0x46 ? c - 55 : -1;
      if (d < 0) return undefined;
      u = (u << 4) | d;
    }
    this.i += 4;
    return u;
  }
  str(): JsonStr | undefined {
    const out: number[] = [];
    let bad = false;
    const ESC: Record<number, number> = { 0x22: 0x22, 0x5c: 0x5c, 0x2f: 0x2f, 0x62: 8, 0x66: 12, 0x6e: 10, 0x72: 13, 0x74: 9 };
    for (;;) {
      if (this.i >= this.s.length) return undefined;
      const c = this.s[this.i++];
      if (c === 0x22) return { kind: "str", s: fromCodePoints(out), bad };
      if (c < 0x20) return undefined; // strict: an unescaped control character
      if (c !== 0x5c) {
        if (c >= 0xd800 && c < 0xe000) bad = true;
        out.push(c);
        continue;
      }
      const e = this.s[this.i++];
      if (e === undefined) return undefined;
      if (e in ESC) {
        out.push(ESC[e]);
        continue;
      }
      if (e !== 0x75) return undefined;
      const u = this.hex4();
      if (u === undefined) return undefined;
      if (u >= 0xd800 && u < 0xdc00 && this.s[this.i] === 0x5c && this.s[this.i + 1] === 0x75) {
        const save = this.i;
        this.i += 2;
        const lo = this.hex4();
        if (lo !== undefined && lo >= 0xdc00 && lo < 0xe000) {
          out.push(0x10000 + ((u - 0xd800) << 10) + (lo - 0xdc00));
          continue;
        }
        this.i = save;
      }
      if (u >= 0xd800 && u < 0xe000) bad = true;
      out.push(u);
    }
  }
  array(): JsonVal[] | undefined {
    this.i++;
    const out: JsonVal[] = [];
    this.ws();
    if (this.s[this.i] === 0x5d) {
      this.i++;
      return out;
    }
    for (;;) {
      this.ws();
      const v = this.value();
      if (v === undefined) return undefined;
      out.push(v);
      this.ws();
      const c = this.s[this.i++];
      if (c === 0x5d) return out;
      if (c !== 0x2c) return undefined;
    }
  }
  object(): JsonObj | undefined {
    this.i++;
    const obj: JsonObj = { kind: "obj", keys: [], vals: new Map() };
    this.ws();
    if (this.s[this.i] === 0x7d) {
      this.i++;
      return obj;
    }
    for (;;) {
      this.ws();
      if (this.s[this.i] !== 0x22) return undefined;
      this.i++;
      const k = this.str();
      if (k === undefined) return undefined;
      this.ws();
      if (this.s[this.i] !== 0x3a) return undefined;
      this.i++;
      this.ws();
      const v = this.value();
      if (v === undefined) return undefined;
      if (!obj.vals.has(k.s)) obj.keys.push(k.s);
      obj.vals.set(k.s, v);
      this.ws();
      const c = this.s[this.i++];
      if (c === 0x7d) return obj;
      if (c !== 0x2c) return undefined;
    }
  }
}

/** json.loads(bytes); undefined where Python raises ValueError. Ints are bigint. */
export function loadsJson(b: Uint8Array): JsonVal | undefined {
  const text = decodeJsonBytes(b);
  if (text === null) return undefined;
  const p = new JsonParser(text);
  p.ws();
  const v = p.value();
  if (v === undefined) return undefined;
  p.ws();
  return p.i === text.length ? v : undefined;
}

// ---- query strings (dict(parse_qsl(raw, keep_blank_values=True)), as Starlette) -----------

const UTF8_REPLACE = new TextDecoder("utf-8", { fatal: false, ignoreBOM: true });

function unquoteToBytes(s: string): Uint8Array {
  const parts = s.split("%");
  const out: number[] = [];
  const push = (t: string) => {
    for (let k = 0; k < t.length; k++) out.push(t.charCodeAt(k));
  };
  push(parts[0]);
  for (const item of parts.slice(1)) {
    if (/^[0-9A-Fa-f]{2}/.test(item)) {
      out.push(parseInt(item.slice(0, 2), 16));
      push(item.slice(2));
    } else {
      out.push(0x25);
      push(item);
    }
  }
  return new Uint8Array(out);
}

/** urllib.parse.unquote(s): ASCII runs percent-decoded as UTF-8 with replacement. */
export function unquote(s: string): string {
  if (!s.includes("%")) return s;
  let out = "";
  for (let i = 0; i < s.length; ) {
    let j = i;
    if (s.charCodeAt(i) < 0x80) {
      while (j < s.length && s.charCodeAt(j) < 0x80) j++;
      out += UTF8_REPLACE.decode(unquoteToBytes(s.slice(i, j)));
    } else {
      while (j < s.length && s.charCodeAt(j) >= 0x80) j++;
      out += s.slice(i, j);
    }
    i = j;
  }
  return out;
}

/** `raw` is the query as latin-1 text (one char per byte). A repeated name keeps its last value. */
export function parseQS(raw: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const field of raw.split("&")) {
    if (field === "") continue;
    const eq = field.indexOf("=");
    const name = eq < 0 ? field : field.slice(0, eq);
    const value = eq < 0 ? "" : field.slice(eq + 1);
    out.set(unquote(name.replaceAll("+", " ")), unquote(value.replaceAll("+", " ")));
  }
  return out;
}

/** bytes.decode("latin-1") */
export function latin1(b: Uint8Array): string {
  let out = "";
  for (const x of b) out += String.fromCharCode(x);
  return out;
}

// ---- the wire (what the client sends) -----------------------------------------------------

function wireNumber(x: number): string {
  if (Number.isNaN(x)) return "NaN";
  if (x === Infinity) return "Infinity";
  if (x === -Infinity) return "-Infinity";
  if (Object.is(x, -0)) return "-0.0";
  return Number.isSafeInteger(x) ? String(x) : reprFloat(x);
}

function wireString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += "\\\\";
    else if (c >= 0x20 && c < 0x7f) out += s[i];
    else out += "\\u" + c.toString(16).padStart(4, "0"); // keeps lone surrogates
  }
  return out + '"';
}

/** ASCII-only JSON the server reads back exactly: bigint as an integer, Python's NaN and
 * Infinity tokens, undefined members left out. */
export function dumpsWire(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "number") return wireNumber(v);
  if (typeof v === "string") return wireString(v);
  if (Array.isArray(v)) return "[" + v.map(dumpsWire).join(", ") + "]";
  const o = v as Record<string, unknown>;
  const members = Object.keys(o).filter((k) => o[k] !== undefined);
  return "{" + members.map((k) => wireString(k) + ": " + dumpsWire(o[k])).join(", ") + "}";
}

/** A value as path, query or form text. */
export function wireText(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "number") return Number.isSafeInteger(v) && !Object.is(v, -0) ? String(v) : reprFloat(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

const ENCODER = new TextEncoder();

/** Percent-encode the UTF-8 bytes (a lone surrogate becomes U+FFFD, as TextEncoder does). */
export function pctEncode(s: string): string {
  let out = "";
  for (const b of ENCODER.encode(s)) {
    const c = String.fromCharCode(b);
    out += /[A-Za-z0-9\-_.~]/.test(c) ? c : "%" + b.toString(16).toUpperCase().padStart(2, "0");
  }
  return out;
}
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/tsgen/test_rt.py tests/tsgen/test_wire.py -v`
Expected: PASS. A `reprFloat` or `pyRepr` mismatch on a Unicode-category character is a Node ICU vs Python Unicode version difference: record it as a known limitation row in the decision log (like Go's Unicode 15 vs 16 row) and remove only that sample.

- [ ] **Step 5: Commit**

```bash
git add lattice/tsgen/rt tests/tsgen/test_rt.py tests/tsgen/test_wire.py
git commit -m "feat(tsgen): lattice-rt core — strings, floats, json.loads, query strings, wire" -- lattice/tsgen/rt tests/tsgen/test_rt.py tests/tsgen/test_wire.py
```

---

### Task 4: `lattice-rt.ts` dates and UUIDs

**Files:**
- Modify: `lattice/tsgen/rt/lattice-rt.ts` (append), `lattice/tsgen/rt/rt.test.ts` (append)

**Interfaces:**
- Produces: `parseISODate(s: string): string | null` (canonical `YYYY-MM-DD`), `parseDateTime(s: string): bigint | null` (µs since the epoch, UTC), `dateTimeText(t: bigint): string` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`), `uuidText(s: string): string | null`.

- [ ] **Step 1: Write the failing tests**

Append to `rt.test.ts`:

```ts
test("dates", () => {
  for (const c of GO.dates) assert.equal(rt.parseISODate(c.in), c.out, JSON.stringify(c.in));
});

test("datetimes", () => {
  for (const c of GO.datetimes) {
    const t = rt.parseDateTime(c.in);
    assert.equal(t === null ? null : rt.dateTimeText(t), c.out, JSON.stringify(c.in));
  }
});

test("uuids", () => {
  for (const c of GO.uuids) assert.equal(rt.uuidText(c.in), c.out, JSON.stringify(c.in));
});

test("lone surrogates never parse (Review Focus 3)", () => {
  assert.equal(rt.parseDateTime("2026-09-28\ud80010:00"), null);
  assert.equal(rt.parseISODate("2026-09-2\ud800"), null);
  assert.equal(rt.uuidText("0123456789abcdef0123456789abcde\ud800"), null);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/tsgen/test_rt.py -v` — Expected: FAIL (`rt.parseISODate is not a function`).

- [ ] **Step 3: Implement (a port of `lattice/gogen/rt/dates.go`, CPython's C parser over UTF-8 bytes)**

Append to `lattice-rt.ts`:

```ts
// ---- dates (CPython's date/datetime.fromisoformat, over the UTF-8 bytes) ------------------

const MIN_YEAR = 1;
const MAX_YEAR = 9999;
const EPOCH_ORD = 719163; // date(1970, 1, 1).toordinal()
const US_PER_DAY = 86_400_000_000n;
const DAYS_BEFORE_MONTH = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

const isLeap = (y: number) => y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
const daysInMonth = (y: number, m: number) => (m === 2 && isLeap(y) ? 29 : DAYS_IN_MONTH[m]);
const daysBeforeYear = (y: number) => {
  const p = y - 1;
  return p * 365 + Math.floor(p / 4) - Math.floor(p / 100) + Math.floor(p / 400);
};
const ymdToOrd = (y: number, m: number, d: number) =>
  daysBeforeYear(y) + DAYS_BEFORE_MONTH[m] + (m > 2 && isLeap(y) ? 1 : 0) + d;
const validDate = (y: number, m: number, d: number) =>
  y >= MIN_YEAR && y <= MAX_YEAR && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m);

function ordToYMD(ord: number): [number, number, number] {
  let n = ord - 1;
  const n400 = Math.floor(n / 146097);
  n %= 146097;
  let year = n400 * 400 + 1;
  const n100 = Math.floor(n / 36524);
  n %= 36524;
  const n4 = Math.floor(n / 1461);
  n %= 1461;
  const n1 = Math.floor(n / 365);
  n %= 365;
  year += n100 * 100 + n4 * 4 + n1;
  if (n1 === 4 || n100 === 4) return [year - 1, 12, 31];
  const leap = n1 === 3 && (n4 !== 24 || n100 === 3);
  let month = (n + 50) >> 5;
  let preceding = DAYS_BEFORE_MONTH[month] + (month > 2 && leap ? 1 : 0);
  if (preceding > n) {
    month -= 1;
    preceding -= DAYS_IN_MONTH[month] + (month === 2 && leap ? 1 : 0);
  }
  return [year, month, n - preceding + 1];
}

const pad = (n: number, w: number) => String(n).padStart(w, "0");
const isDigit = (c: number) => c >= 0x30 && c <= 0x39;
const at = (b: Uint8Array, i: number) => (i >= 0 && i < b.length ? b[i] : 0);

function digitsAt(b: Uint8Array, i: number, n: number): [number, number] | null {
  let v = 0;
  for (let k = 0; k < n; k++) {
    const c = at(b, i + k);
    if (!isDigit(c)) return null;
    v = v * 10 + (c - 0x30);
  }
  return [v, i + n];
}

function isoToYMD(year: number, week: number, day: number): [number, number, number] | null {
  if (year < MIN_YEAR || year > MAX_YEAR) return null;
  if (week <= 0 || week >= 53) {
    const first = ymdToOrd(year, 1, 1) % 7;
    if (!(week === 53 && (first === 4 || (first === 3 && isLeap(year))))) return null;
  }
  if (day <= 0 || day >= 8) return null;
  const first = ymdToOrd(year, 1, 1);
  const wd = (first + 6) % 7;
  const monday = first - wd + (wd > 3 ? 7 : 0);
  return ordToYMD(monday + (week - 1) * 7 + day - 1);
}

function parseDatePart(b: Uint8Array, n: number): [number, number, number] | null {
  const y = digitsAt(b, 0, 4);
  if (y === null) return null;
  let p = y[1];
  const sep = at(b, p) === 0x2d;
  if (sep) p++;
  if (at(b, p) === 0x57) {
    const w = digitsAt(b, p + 1, 2);
    if (w === null) return null;
    p = w[1];
    let day = 1;
    if (p < n) {
      if (sep) {
        if (at(b, p) !== 0x2d) return null;
        p++;
      }
      const d = digitsAt(b, p, 1);
      if (d === null) return null;
      day = d[0];
    }
    return isoToYMD(y[0], w[0], day);
  }
  const m = digitsAt(b, p, 2);
  if (m === null) return null;
  p = m[1];
  if (sep) {
    if (at(b, p) !== 0x2d) return null;
    p++;
  }
  const d = digitsAt(b, p, 2);
  return d === null ? null : [y[0], m[0], d[0]];
}

/** date.fromisoformat(s).isoformat(), or null where Python raises. */
export function parseISODate(s: string): string | null {
  if (!s.isWellFormed()) return null;
  const b = ENCODER.encode(s);
  if (b.length !== 7 && b.length !== 8 && b.length !== 10) return null;
  const ymd = parseDatePart(b, b.length);
  if (ymd === null || !validDate(...ymd)) return null;
  return `${pad(ymd[0], 4)}-${pad(ymd[1], 2)}-${pad(ymd[2], 2)}`;
}

function isoSeparator(b: Uint8Array): number {
  const n = b.length;
  if (n === 7) return 7;
  if (at(b, 4) === 0x2d) {
    if (at(b, 5) === 0x57) {
      if (n < 8) return -1;
      if (n > 8 && at(b, 8) === 0x2d) {
        if (n === 9) return -1;
        return n > 10 && isDigit(at(b, 10)) ? 8 : 10;
      }
      return 8;
    }
    return 10;
  }
  if (at(b, 4) === 0x57) {
    let idx = 7;
    while (idx < n && isDigit(b[idx])) idx++;
    if (idx < 9) return idx;
    return idx % 2 === 0 ? 7 : 8;
  }
  return 8;
}

/** CPython's parse_hh_mm_ss_ff over b[:end]: rv 0 at the end, 1 when more follows, <0 error. */
function parseHMSF(b: Uint8Array, end: number): [number, number, number, number, number] {
  const vals = [0, 0, 0];
  let p = 0;
  let hasSep = true;
  let i = 0;
  for (; i < 3; i++) {
    const v = digitsAt(b, p, 2);
    if (v === null) return [0, 0, 0, 0, -3];
    [vals[i], p] = v;
    const c = at(b, p);
    p++;
    if (i === 0) hasSep = c === 0x3a;
    if (p >= end) return [vals[0], vals[1], vals[2], 0, c !== 0 ? 1 : 0];
    if (hasSep && c === 0x3a) {
      if (i === 2) return [0, 0, 0, 0, -4];
      continue;
    }
    if (c === 0x2e || c === 0x2c) {
      if (i < 2) return [0, 0, 0, 0, -3];
      break;
    }
    if (!hasSep) p--;
    else return [0, 0, 0, 0, -4];
  }
  const remains = end - p;
  if (remains <= 0) return [0, 0, 0, 0, -3];
  const toParse = Math.min(remains, 6);
  const f = digitsAt(b, p, toParse);
  if (f === null) return [0, 0, 0, 0, -3];
  let frac = f[0];
  for (let k = toParse; k < 6; k++) frac *= 10;
  p = f[1];
  while (isDigit(at(b, p))) p++;
  return [vals[0], vals[1], vals[2], frac, at(b, p) !== 0 ? 1 : 0];
}

/** CPython's parse_isoformat_time: [h, m, s, us, offset µs] or null. */
function parseISOTime(b: Uint8Array): [number, number, number, number, number] | null {
  let tz = b.findIndex((c) => c === 0x5a || c === 0x2b || c === 0x2d);
  if (tz < 0) tz = b.length;
  const [h, m, s, us, rv] = parseHMSF(b, tz);
  if (rv < 0) return null;
  if (tz >= b.length) return rv === 1 ? null : [h, m, s, us, 0];
  if (b[tz] === 0x5a) return tz + 1 === b.length ? [h, m, s, us, 0] : null;
  const sign = b[tz] === 0x2d ? -1 : 1;
  const rest = b.subarray(tz + 1);
  const [th, tm, ts, tus, trv] = parseHMSF(rest, rest.length);
  if (th > 23 || tm > 59 || ts > 59 || trv !== 0) return null;
  return [h, m, s, us, sign * ((th * 3600 + tm * 60 + ts) * 1_000_000 + tus)];
}

function makeDateTime(y: number, mo: number, d: number, h: number, mi: number, s: number, us: number): bigint {
  return BigInt(ymdToOrd(y, mo, d) - EPOCH_ORD) * US_PER_DAY + BigInt(((h * 60 + mi) * 60 + s) * 1_000_000 + us);
}
const MIN_DT = makeDateTime(1, 1, 1, 0, 0, 0, 0);
const MAX_DT = makeDateTime(9999, 12, 31, 23, 59, 59, 999_999);

/** The prelude's parse_datetime: µs since the epoch (UTC; naive is UTC), or null. */
export function parseDateTime(s: string): bigint | null {
  if (!s.isWellFormed() || cpLen(s) < 7) return null;
  const b = ENCODER.encode(s);
  const sepAt = isoSeparator(b);
  if (sepAt < 0) return null;
  const ymd = parseDatePart(b, sepAt);
  if (ymd === null) return null;
  let [y, mo, d] = ymd;
  let [h, mi, sec, us, off] = [0, 0, 0, 0, 0];
  if (b.length > sepAt) {
    const lead = b[sepAt];
    const size = lead < 0x80 ? 1 : lead < 0xe0 ? 2 : lead < 0xf0 ? 3 : 4;
    const t = parseISOTime(b.subarray(sepAt + size));
    if (t === null) return null;
    [h, mi, sec, us, off] = t;
  }
  if (h === 24 && mo >= 1 && mo <= 12) {
    const dim = daysInMonth(y, mo);
    if (d <= dim) {
      if (mi !== 0 || sec !== 0 || us !== 0) return null;
      h = 0;
      d++;
      if (d > dim) {
        d = 1;
        mo++;
        if (mo > 12) [mo, y] = [1, y + 1];
      }
    }
  }
  if (!validDate(y, mo, d) || h > 23 || mi > 59 || sec > 59) return null;
  const t = makeDateTime(y, mo, d, h, mi, sec, us) - BigInt(off);
  return t < MIN_DT || t > MAX_DT ? null : t;
}

/** The prelude's datetime_text. */
export function dateTimeText(t: bigint): string {
  let days = t / US_PER_DAY;
  let rem = t % US_PER_DAY;
  if (rem < 0n) {
    rem += US_PER_DAY;
    days -= 1n;
  }
  const [y, mo, d] = ordToYMD(Number(days) + EPOCH_ORD);
  const us = Number(rem);
  const secs = Math.floor(us / 1_000_000);
  return (
    `${pad(y, 4)}-${pad(mo, 2)}-${pad(d, 2)}T${pad(Math.floor(secs / 3600), 2)}:` +
    `${pad(Math.floor(secs / 60) % 60, 2)}:${pad(secs % 60, 2)}.${pad(us % 1_000_000, 6)}Z`
  );
}

const UUID_TEXT = /^[0-9a-fA-F]{8}(-?)[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{4}\1[0-9a-fA-F]{12}$/;

/** The prelude's uuid_text: canonical lowercase hyphenated, or null. */
export function uuidText(s: string): string | null {
  if (!UUID_TEXT.test(s)) return null;
  const h = s.replaceAll("-", "").toLowerCase();
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/tsgen/test_rt.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(tsgen): lattice-rt dates, datetimes and UUIDs (port of CPython's fromisoformat)" -- lattice/tsgen/rt/lattice-rt.ts lattice/tsgen/rt/rt.test.ts
```

---

### Task 5: `parseParams`, error bodies, response decoding — L1 green

**Files:**
- Modify: `lattice/tsgen/rt/lattice-rt.ts` (append), `lattice/tsgen/rt/rt.test.ts` (append)
- Create: `lattice/tsgen/rt/replay.ts`

**Interfaces:**
- Consumes: Tasks 3–4 exports.
- Produces: types `Scalar`, `PyValue`, `ParamSpec`, `FieldError`, `Parsed`; `parseParams(specs, path: ReadonlyMap<string,string>, query: ReadonlyMap<string,string>, body: Uint8Array, form: boolean, ctype: string | null): Parsed`; `formFields(body, ctype): Map<string,string>`; `EMAIL_MAX_LEN`; `decodeErrorBody(status: number, body: Uint8Array): { code: string | null; detail: unknown }` (throws `DecodeError`); `Col`, `decodeRow(v, cols)`, `decodePage(v, cols, shape)`, `decodeJson(bytes): JsonVal` (throws `DecodeError`).
- Produces (`replay.ts`): `pyValueOf(tag): PyValue`, `tagOf(v: PyValue)`, `specOf(d, patterns): ParamSpec`, `replayParams(cases, patterns): string[]` (mismatch messages); run as a script `node replay.ts <corpus.json>` prints `{"cases": n, "mismatches": [...]}`.

- [ ] **Step 1: Write the failing tests**

Append to `rt.test.ts`:

```ts
import { replayParams } from "./replay.ts";

test("params: Go corpus", () => {
  const bad = replayParams(GO.params, TS.patterns);
  assert.deepEqual(bad, [], bad.slice(0, 10).join("\n"));
});

test("params: JS-hazard corpus", () => {
  const bad = replayParams(TS.params, TS.patterns);
  assert.deepEqual(bad, [], bad.slice(0, 10).join("\n"));
});

test("error bodies", () => {
  for (const c of GO.error_bodies) {
    const body = new TextEncoder().encode(c.body);
    const wantDetail = c.detail ?? (c.style === "fastapi" ? GO.phrases[String(c.status)] : null);
    if (c.status === 422 && !Array.isArray(wantDetail)) {
      assert.throws(() => rt.decodeErrorBody(c.status, body), rt.DecodeError);
      continue;
    }
    const got = rt.decodeErrorBody(c.status, body);
    assert.deepEqual(got, { code: c.style === "lattice" ? c.code : null, detail: wantDetail }, c.body);
  }
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/tsgen/test_rt.py -v` — Expected: FAIL (`Cannot find module './replay.ts'`).

- [ ] **Step 3: Implement parameter parsing and decoding**

Append to `lattice-rt.ts`:

```ts
// ---- parameters (the prelude's _convert, _constrain, parse_params) ------------------------

export type Scalar = "Int" | "Float" | "Str" | "Bool" | "Date" | "DateTime" | "Uuid";
export type PyValue =
  | { t: "unset" }
  | { t: "none" }
  | { t: "bool"; v: boolean }
  | { t: "int"; v: bigint }
  | { t: "float"; v: number }
  | { t: "str"; v: string }
  | { t: "date"; v: string }
  | { t: "datetime"; v: string }
  | { t: "uuid"; v: string };
export type ParamSpec = {
  name: string;
  source: "path" | "query" | "body";
  scalar: Scalar;
  ge?: number;
  le?: number;
  min_len?: number;
  max_len?: number;
  one_of?: readonly string[];
  default?: PyValue;
  optional?: boolean;
  nullable?: boolean;
  pattern?: { py: string; js: string };
  email?: boolean;
};
export type FieldError = { loc: string[]; msg: string };
export type Parsed = { ok: true; values: Map<string, PyValue> } | { ok: false; detail: FieldError[] };

export const EMAIL_MAX_LEN = 254;
const TYPE_NAMES: Record<Scalar, string> = {
  Int: "an integer",
  Float: "a number",
  Str: "a string",
  Bool: "a boolean",
  Date: "an ISO date (YYYY-MM-DD)",
  DateTime: "an ISO date-time",
  Uuid: "a UUID",
};
const OUT_OF_RANGE = "must be an integer between -9223372036854775808 and 9223372036854775807";
const RE_INT = /^[+-]?[0-9]+$/;
const RE_FLOAT = /^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
const TRUE = new Set(["true", "1", "yes", "on"]);
const FALSE = new Set(["false", "0", "no", "off"]);

type Raw = string | JsonVal;
type Problem = { problem: string };
const inRange = (n: bigint): PyValue | Problem =>
  n < INT_MIN || n > INT_MAX ? { problem: OUT_OF_RANGE } : { t: "int", v: n };

function convert(spec: ParamSpec, raw: Raw, text: boolean): PyValue | Problem {
  const bad: Problem = { problem: "must be " + TYPE_NAMES[spec.scalar] };
  let str: string | null = typeof raw === "string" ? raw : null;
  if (isJsonStr(raw)) {
    if (raw.bad) return spec.scalar === "Str" ? { problem: "must be valid UTF-8 text" } : bad;
    str = raw.s;
  }
  switch (spec.scalar) {
    case "Int": {
      if (text && str !== null) {
        const t = pyStrip(str);
        if (!RE_INT.test(t)) return bad;
        if (t.replace(/^[+-]/, "").length > 4300) return { problem: OUT_OF_RANGE };
        return inRange(BigInt(t.startsWith("+") ? t.slice(1) : t));
      }
      if (text) return bad;
      if (typeof raw === "bigint") return inRange(raw);
      if (typeof raw === "number") {
        if (!Number.isFinite(raw) || !Number.isInteger(raw)) return bad;
        return inRange(BigInt(raw));
      }
      return bad;
    }
    case "Float": {
      let v: number;
      if (text && str !== null) {
        const t = pyStrip(str);
        if (!RE_FLOAT.test(t)) return bad;
        v = Number(t);
      } else if (!text && typeof raw === "bigint") v = Number(raw);
      else if (!text && typeof raw === "number") v = raw;
      else return bad;
      return Number.isFinite(v) ? { t: "float", v } : bad;
    }
    case "Str":
      return str !== null ? { t: "str", v: str } : bad;
    case "Bool":
      if (text && str !== null) {
        const l = str.toLowerCase();
        return TRUE.has(l) ? { t: "bool", v: true } : FALSE.has(l) ? { t: "bool", v: false } : bad;
      }
      return !text && typeof raw === "boolean" ? { t: "bool", v: raw } : bad;
    case "DateTime": {
      const t = str === null ? null : parseDateTime(str);
      return t === null ? bad : { t: "datetime", v: dateTimeText(t) };
    }
    case "Uuid": {
      const u = str === null ? null : uuidText(str);
      return u === null ? bad : { t: "uuid", v: u };
    }
    case "Date": {
      const d = str === null ? null : parseISODate(str);
      return d === null ? bad : { t: "date", v: d };
    }
  }
}

/** A parameter value (int or float) compared exactly with a float bound, as Python does. */
function cmpNum(v: PyValue, bound: number): number {
  if (v.t === "float") return v.v < bound ? -1 : v.v > bound ? 1 : 0;
  if (v.t !== "int") return 0;
  if (!Number.isFinite(bound)) return bound > 0 ? -1 : 1;
  if (Number.isInteger(bound)) {
    const b = BigInt(bound);
    return v.v < b ? -1 : v.v > b ? 1 : 0;
  }
  return v.v <= BigInt(Math.floor(bound)) ? -1 : 1;
}

const PATTERNS = new Map<string, RegExp>();
function fullMatch(js: string, s: string): boolean {
  let re = PATTERNS.get(js);
  if (re === undefined) PATTERNS.set(js, (re = new RegExp(js, "u")));
  return re.test(s);
}

function constrain(spec: ParamSpec, value: PyValue): string | null {
  if (spec.one_of !== undefined && !(value.t === "str" && spec.one_of.includes(value.v)))
    return "must be one of " + spec.one_of.map(pyRepr).join(", ");
  if (value.t === "int" || value.t === "float") {
    if (spec.ge !== undefined && cmpNum(value, spec.ge) < 0) return "must be >= " + num(spec.ge);
    if (spec.le !== undefined && cmpNum(value, spec.le) > 0) return "must be <= " + num(spec.le);
  }
  if (value.t === "str") {
    const n = cpLen(value.v);
    if (spec.min_len !== undefined && n < spec.min_len) return `must have at least ${spec.min_len} characters`;
    if (spec.max_len !== undefined && n > spec.max_len) return `must have at most ${spec.max_len} characters`;
    if (spec.email && n > EMAIL_MAX_LEN) return `must have at most ${EMAIL_MAX_LEN} characters`;
    if (spec.pattern !== undefined && !fullMatch(spec.pattern.js, value.v))
      return spec.email ? "must be an email address" : "must match the pattern " + pyRepr(spec.pattern.py);
  }
  return null;
}

function defaultValue(spec: ParamSpec): PyValue {
  const d = spec.default!;
  if (spec.scalar === "Uuid" && d.t === "str") {
    const u = uuidText(d.v);
    if (u !== null) return { t: "uuid", v: u };
  }
  return d;
}

/** The prelude's form_fields. */
export function formFields(body: Uint8Array, ctype: string | null): Map<string, string> {
  const media = pyStrip((ctype ?? "").split(";")[0]).toLowerCase();
  return media === "application/x-www-form-urlencoded" ? parseQS(latin1(body)) : new Map();
}

const isBlank = (b: Uint8Array) => b.every((c) => c === 0x20 || (c >= 0x09 && c <= 0x0d));

/** The prelude's parse_params: every parameter converted and validated, in order; else the
 * 422 detail listing each problem in order. */
export function parseParams(
  specs: readonly ParamSpec[],
  path: ReadonlyMap<string, string>,
  query: ReadonlyMap<string, string>,
  body: Uint8Array,
  form: boolean,
  ctype: string | null,
): Parsed {
  const detail: FieldError[] = [];
  const values = new Map<string, PyValue>();
  let data: ReadonlyMap<string, Raw> = new Map();
  if (form) data = formFields(body, ctype);
  else if (specs.some((s) => s.source === "body")) {
    let parsed: JsonVal | undefined = { kind: "obj", keys: [], vals: new Map() };
    if (!isBlank(body)) parsed = loadsJson(body);
    if (!isJsonObj(parsed)) return { ok: false, detail: [{ loc: ["body"], msg: "the body must be a JSON object" }] };
    data = parsed.vals;
  }
  for (const spec of specs) {
    const source: ReadonlyMap<string, Raw> = spec.source === "path" ? path : spec.source === "query" ? query : data;
    const loc = [spec.source, spec.name];
    const present = source.has(spec.name);
    const raw = source.get(spec.name);
    if (spec.optional && present && raw === null) {
      if (spec.nullable) values.set(spec.name, { t: "none" });
      else detail.push({ loc, msg: "must not be null" });
      continue;
    }
    if (!present || raw === null || raw === undefined) {
      if (spec.default !== undefined) values.set(spec.name, defaultValue(spec));
      else detail.push({ loc, msg: "field required" });
      continue;
    }
    const value = convert(spec, raw, spec.source !== "body" || form);
    if ("problem" in value) {
      detail.push({ loc, msg: value.problem });
      continue;
    }
    const problem = constrain(spec, value);
    if (problem !== null) {
      detail.push({ loc, msg: problem });
      continue;
    }
    values.set(spec.name, value);
  }
  return detail.length > 0 ? { ok: false, detail } : { ok: true, values };
}

// ---- responses ----------------------------------------------------------------------------

/** A response body (UTF-8 JSON, ints kept exact). */
export function decodeJson(bytes: Uint8Array): JsonVal {
  const v = loadsJson(bytes);
  if (v === undefined) throw new DecodeError("the body is not JSON");
  return v;
}

const isFieldErrors = (d: unknown): d is FieldError[] =>
  Array.isArray(d) &&
  d.every(
    (e) =>
      typeof e === "object" && e !== null && Array.isArray(e.loc) &&
      e.loc.every((x: unknown) => typeof x === "string") && typeof e.msg === "string",
  );

/** An error body in either style: {"error": code, "detail"?} or {"detail": ...}. A 422's
 * detail must be a list of {loc, msg}; any other status's a string or absent. */
export function decodeErrorBody(status: number, body: Uint8Array): { code: string | null; detail: unknown } {
  let o: unknown;
  try {
    o = JSON.parse(UTF8_REPLACE.decode(body));
  } catch {
    throw new DecodeError("the error body is not JSON");
  }
  if (typeof o !== "object" || o === null || Array.isArray(o)) throw new DecodeError("not an object");
  const rec = o as Record<string, unknown>;
  const code = typeof rec.error === "string" ? rec.error : null;
  const detail = rec.detail ?? null;
  if (status === 422 ? !isFieldErrors(detail) : !(detail === null || typeof detail === "string"))
    throw new DecodeError(`unexpected ${status} detail`);
  return { code, detail };
}

export type Col = readonly [name: string, scalar: Scalar, nullable: boolean];

function decodeScalar(v: JsonVal | undefined, scalar: Scalar, nullable: boolean, where: string): unknown {
  if (v === null) {
    if (nullable) return null;
    throw new DecodeError(`${where}: null`);
  }
  if (scalar === "Int" && typeof v === "bigint") return v;
  if (scalar === "Float" && typeof v === "number") return v;
  if (scalar === "Float" && typeof v === "bigint") return Number(v);
  if (scalar === "Bool" && typeof v === "boolean") return v;
  if ((scalar === "Str" || scalar === "Uuid" || scalar === "Date" || scalar === "DateTime") && isJsonStr(v) && !v.bad)
    return v.s;
  throw new DecodeError(`${where}: not ${scalar}`);
}

/** A row: exactly the declared columns, each of its declared type. */
export function decodeRow(v: JsonVal | undefined, cols: readonly Col[]): Record<string, unknown> {
  if (!isJsonObj(v)) throw new DecodeError("the row is not an object");
  if (v.keys.length !== cols.length || cols.some(([n]) => !v.vals.has(n)))
    throw new DecodeError(`columns ${v.keys.join(",")} are not ${cols.map((c) => c[0]).join(",")}`);
  const out: Record<string, unknown> = {};
  for (const [n, s, nullable] of cols) out[n] = decodeScalar(v.vals.get(n), s, nullable, n);
  return out;
}

/** A Page envelope: `shape` is [page_shape key, JSON field]; counts are exact numbers. */
export function decodePage(
  v: JsonVal | undefined,
  cols: readonly Col[],
  shape: readonly (readonly [string, string])[],
): Record<string, unknown> {
  if (!isJsonObj(v) || v.keys.length !== shape.length) throw new DecodeError("not the page envelope");
  const out: Record<string, unknown> = {};
  for (const [key, field] of shape) {
    const x = v.vals.get(field);
    if (key === "items") {
      if (!Array.isArray(x)) throw new DecodeError(`${field}: not a list`);
      out[field] = x.map((r) => decodeRow(r, cols));
    } else {
      if (typeof x !== "bigint" || x < 0n || x > BigInt(Number.MAX_SAFE_INTEGER))
        throw new DecodeError(`${field}: not a count`);
      out[field] = Number(x);
    }
  }
  return out;
}
```

`lattice/tsgen/rt/replay.ts`:

```ts
// Replay fixture-format wire cases through parseParams (L1 and the L2 gate).
import { isDeepStrictEqual } from "node:util";
import { readFileSync } from "node:fs";
import { parseParams, parseQS, reprFloat, type ParamSpec, type PyValue } from "./lattice-rt.ts";

const pyFloat = (s: string) => (s === "inf" ? Infinity : s === "-inf" ? -Infinity : s === "nan" ? NaN : Number(s));

export function pyValueOf(d: any): PyValue {
  if (d.t === "int") return { t: "int", v: BigInt(d.v) };
  if (d.t === "float") return { t: "float", v: pyFloat(d.v) };
  return d;
}

export function tagOf(v: PyValue): Record<string, unknown> {
  if (v.t === "int") return { t: "int", v: v.v.toString() };
  if (v.t === "float") return { t: "float", v: reprFloat(v.v) };
  return v;
}

export function specOf(d: any, patterns: Record<string, string>): ParamSpec {
  const s: any = { ...d };
  if (d.default !== undefined) s.default = pyValueOf(d.default);
  if (d.pattern !== undefined) {
    const js = patterns[d.pattern];
    if (js === undefined) throw new Error("no JS source for pattern " + d.pattern);
    s.pattern = { py: d.pattern, js };
  }
  return s;
}

const show = (v: unknown) => JSON.stringify(v, (_k, x) => (typeof x === "bigint" ? `${x}n` : x));

export function replayParams(cases: any[], patterns: Record<string, string>): string[] {
  const bad: string[] = [];
  for (const c of cases) {
    const specs = c.specs.map((d: any) => specOf(d, patterns));
    const body = new Uint8Array(Buffer.from(c.body, "base64"));
    const r = parseParams(specs, new Map(Object.entries(c.path)), parseQS(c.query), body, c.form, c.ctype);
    const got = r.ok
      ? { values: Object.fromEntries([...r.values].map(([k, v]) => [k, tagOf(v)])), order: [...r.values.keys()] }
      : { status: 422, detail: r.detail };
    const want = "status" in c
      ? { status: c.status, detail: JSON.parse(c.lattice).detail }
      : { values: c.values, order: c.order };
    if (!isDeepStrictEqual(got, want)) bad.push(`${c.name}: got ${show(got)} want ${show(want)}`);
  }
  return bad;
}

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop()!)) {
  const file = process.argv[2];
  if (file) {
    const corpus = JSON.parse(readFileSync(file, "utf8"));
    const mismatches = replayParams(corpus.params, corpus.patterns);
    console.log(JSON.stringify({ cases: corpus.params.length, mismatches }));
  }
}
```

- [ ] **Step 4: Run L1**

Run: `pytest tests/tsgen/test_rt.py -v` — Expected: PASS (every Go and TS params case, error bodies).

- [ ] **Step 5: Commit**

```bash
git add lattice/tsgen/rt/replay.ts
git commit -m "feat(tsgen): parseParams, error bodies and response decoding; L1 green" -- lattice/tsgen/rt/lattice-rt.ts lattice/tsgen/rt/rt.test.ts lattice/tsgen/rt/replay.ts
```

---

### Task 6: `api.ts` — the emitter, the call path, and the CLI

**Files:**
- Modify: `lattice/tsgen/rt/lattice-rt.ts` (append the call path)
- Create: `lattice/tsgen/emit.py`, `lattice/tsgen/__main__.py`; modify `lattice/tsgen/__init__.py`
- Test: `tests/tsgen/test_emit.py`

**Interfaces:**
- Consumes: `plan_specs` (Task 2), `fullmatch_source` (Task 1), rt exports (Tasks 3–5).
- Produces (rt): `CallOpts`, `Result<T, E>`, `Unexpected`, `ValidationError`, `Endpoint`, `Wire`, `toWire(ep, p): Wire`, `validate(ep, p): FieldError[]`, `call(ep, p, o): Promise<Result<unknown, unknown>>`, `canonicalKey(v): string`.
- Produces (Python): `lattice.tsgen.emit.api_ts(plans) -> str`, `hooks_ts(plans) -> str` (Task 7 fills it), `TsGenRefusal(Exception)` with `.code == "L893"`, `error_statuses(plan) -> list[int]`, `KIND: dict[int, str]`; `lattice.tsgen.generate_ts(plans) -> dict[str, str]` (keys `lattice-rt.ts`, `api.ts`, `hooks.ts`); `compile_source_to_ts(source, profile) -> tuple[CheckResult, dict[str, str] | None]`.
- Emitted `api.ts` per endpoint `x_y`: types `XYParams`, `XYResponse`, `XYError`; const `XY: rt.Endpoint`; `validateXY(p)`, `xY(p, o)`; plus `export const ENDPOINTS: Record<string, rt.Endpoint>` keyed by plan name.

- [ ] **Step 1: Write the failing tests**

`tests/tsgen/test_emit.py`:

```python
"""api.ts: generated for every proof program; its validation equals Python's on the wire it
builds; responses that drift from the schema are decode errors (Review Focus 5)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import check_source
from lattice.tsgen import generate_ts
from lattice.tsgen.emit import TsGenRefusal, camel, pascal, ts_spec
from lattice.tsgen.pyref import answer, case
from lattice.tsgen.specs import plan_specs
from tests.api.test_codegen_differential import PROGRAMS, setup_of
from tests.tsgen.node import node_json


def plans_of(pid: str) -> tuple[EndpointPlan, ...]:
    source, _task = PROGRAMS[pid]
    result = check_source(source, setup_of(pid).profile)
    assert result.program is not None
    return tuple(lower_api(result.program))


def write(tmp: Path, plans: tuple[EndpointPlan, ...]) -> Path:
    for name, text in generate_ts(plans).items():
        (tmp / name).write_text(text)
    return tmp


@pytest.mark.parametrize("pid", sorted(PROGRAMS))
def test_emits_every_endpoint(pid: str) -> None:
    plans = plans_of(pid)
    api = generate_ts(plans)["api.ts"]
    for plan in plans:
        assert f"export function validate{pascal(plan.name)}(" in api
        assert f"export function {camel(plan.name)}(" in api


SCRIPT = """
import { readFileSync } from "node:fs";
const { dir, calls } = JSON.parse(readFileSync(0, "utf8"));
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");
console.log(JSON.stringify(calls.map(([name, params]) => {
  const ep = api.ENDPOINTS[name];
  const w = rt.toWire(ep, params);
  return { detail: rt.validate(ep, params), path: Object.fromEntries(w.pathValues), query: w.query,
           body: w.body === null ? "" : Buffer.from(w.body).toString("base64"), ctype: w.ctype };
})));
"""


@pytest.mark.parametrize("pid", ["realapps", "gaps", "t2"])
def test_empty_request_matches_python(pid: str, tmp_path: Path) -> None:
    plans = plans_of(pid)
    write(tmp_path, plans)
    calls = [[p.name, {}] for p in plans]
    out = node_json(SCRIPT, {"dir": tmp_path.as_uri(), "calls": calls}, cwd=tmp_path)
    for plan, got in zip(plans, out, strict=True):
        c = case(plan.name, plan_specs(plan), path=got["path"], query=got["query"],
                 body=base64.b64decode(got["body"]), form=plan.body == "form",
                 ctype=got["ctype"])  # fmt: skip
        want = answer(c)
        want_detail = json.loads(want["lattice"])["detail"] if "status" in want else []
        assert got["detail"] == want_detail, plan.name


DRIFT = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
const cols = [["id", "Int", false], ["name", "Str", true]];
const enc = (s) => new TextEncoder().encode(s);
const tryRow = (s) => { try { rt.decodeRow(rt.decodeJson(enc(s)), cols); return "ok"; }
                        catch (e) { return e instanceof rt.DecodeError ? "decode" : "crash"; } };
console.log(JSON.stringify([
  tryRow('{"id": 1, "name": null}'), tryRow('{"id": 1}'), tryRow('{"id": 1, "name": "a", "x": 2}'),
  tryRow('{"id": 1.0, "name": "a"}'), tryRow('{"id": null, "name": "a"}'),
  tryRow('{"id": 9223372036854775807, "name": "a"}'),
]));
"""


def test_response_drift_is_a_decode_error(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(DRIFT, tmp_path.as_uri(), cwd=tmp_path)
    assert got == ["ok", "decode", "decode", "decode", "decode", "ok"]


SURROGATE = """
import { readFileSync } from "node:fs";
const dir = JSON.parse(readFileSync(0, "utf8"));
const rt = await import(dir + "/lattice-rt.ts");
const ep = { method: "GET", path: "/x/{u}", form: false, success: 200, errors: {}, errorStyle: "lattice",
  numberInts: [], decode: () => undefined,
  params: [{ name: "u", source: "path", scalar: "Uuid" }, { name: "t", source: "query", scalar: "DateTime" }] };
const w = rt.toWire(ep, { u: "0123456789abcdef0123456789abcde\\ud800", t: "2026-09-28T10:00\\ud800" });
console.log(JSON.stringify({ path: w.url, detail: rt.validate(ep, { u: "0123456789abcdef0123456789abcde\\ud800",
  t: "2026-09-28T10:00\\ud800" }) }));
"""


def test_lone_surrogates_in_path_and_query(tmp_path: Path) -> None:
    write(tmp_path, plans_of("realapps"))
    got = node_json(SURROGATE, tmp_path.as_uri(), cwd=tmp_path)
    assert "%EF%BF%BD" in got["path"]
    assert [d["loc"] for d in got["detail"]] == [["path", "u"], ["query", "t"]]


def test_untranslatable_pattern_is_l893() -> None:
    plan = plans_of("realapps")[0]
    with pytest.raises(TsGenRefusal, match="L893"):
        ts_spec({"name": "s", "source": "query", "scalar": "Str", "pattern": "(a)\\1"}, plan)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/tsgen/test_emit.py -v` — Expected: FAIL (`cannot import name 'generate_ts'`).

- [ ] **Step 3: Add the call path to the runtime**

Append to `lattice-rt.ts`:

```ts
// ---- the call path (api.ts calls these) ---------------------------------------------------

export type Result<T, E> = { ok: true; value: T } | { ok: false; error: E };
export type ValidationError = { kind: "validation"; status: 422; detail: FieldError[] };
export type Unexpected = { kind: "unexpected"; status: number; reason: "network" | "status" | "decode"; body: string };
export type CallOpts = {
  baseUrl: string;
  token?: string | (() => string | null);
  fetch?: typeof fetch;
  signal?: AbortSignal;
  /** Test-only: send without validating locally (the L3 gate compares both answers). */
  unchecked?: boolean;
};
export type Endpoint = {
  method: string;
  path: string;
  params: readonly ParamSpec[];
  form: boolean;
  success: number;
  errors: Readonly<Record<number, string>>;
  numberInts: readonly string[];
  decode: (v: JsonVal | undefined) => unknown;
};
export type Wire = {
  url: string;
  pathValues: Map<string, string>;
  query: string;
  body: Uint8Array | null;
  ctype: string | null;
};

const present = (v: unknown) => v !== undefined && v !== null;

/** The request exactly as it will be sent, and the path values the server will decode. */
export function toWire(ep: Endpoint, p: Record<string, unknown>): Wire {
  const pathValues = new Map<string, string>();
  const url = ep.path.replace(/\{([^}]+)\}/g, (_m, name: string) => {
    const text = present(p[name]) ? wireText(p[name]) : "";
    pathValues.set(name, text.toWellFormed());
    return pctEncode(text);
  });
  const query = ep.params
    .filter((s) => s.source === "query" && present(p[s.name]))
    .map((s) => pctEncode(s.name) + "=" + pctEncode(wireText(p[s.name])))
    .join("&");
  const bodySpecs = ep.params.filter((s) => s.source === "body");
  let body: Uint8Array | null = null;
  let ctype: string | null = null;
  if (bodySpecs.length > 0 && ep.form) {
    const fields = bodySpecs.filter((s) => present(p[s.name]));
    body = ENCODER.encode(fields.map((s) => pctEncode(s.name) + "=" + pctEncode(wireText(p[s.name]))).join("&"));
    ctype = "application/x-www-form-urlencoded";
  } else if (bodySpecs.length > 0) {
    const obj: Record<string, unknown> = {};
    for (const s of bodySpecs) if (p[s.name] !== undefined) obj[s.name] = p[s.name];
    body = ENCODER.encode(dumpsWire(obj));
    ctype = "application/json";
  }
  return { url: query ? `${url}?${query}` : url, pathValues, query, body, ctype };
}

function validateWire(ep: Endpoint, w: Wire): FieldError[] {
  const r = parseParams(ep.params, w.pathValues, parseQS(w.query), w.body ?? new Uint8Array(), ep.form, w.ctype);
  return r.ok ? [] : r.detail;
}

/** The server's 422 detail for these params (empty when it would accept them). */
export function validate(ep: Endpoint, p: Record<string, unknown>): FieldError[] {
  return validateWire(ep, toWire(ep, p));
}

const unexpected = (status: number, reason: Unexpected["reason"], body: Uint8Array | string): { ok: false; error: Unexpected } => ({
  ok: false,
  error: { kind: "unexpected", status, reason, body: typeof body === "string" ? body : UTF8_REPLACE.decode(body) },
});

export async function call(ep: Endpoint, p: Record<string, unknown>, o: CallOpts): Promise<Result<unknown, unknown>> {
  const w = toWire(ep, p);
  if (!o.unchecked) {
    const detail = validateWire(ep, w);
    if (detail.length > 0) return { ok: false, error: { kind: "validation", status: 422, detail } };
  }
  const headers: Record<string, string> = {};
  const token = typeof o.token === "function" ? o.token() : o.token;
  if (token !== undefined && token !== null) headers["authorization"] = "Bearer " + token;
  if (w.ctype !== null) headers["content-type"] = w.ctype;
  let res: Response;
  try {
    res = await (o.fetch ?? fetch)(o.baseUrl.replace(/\/$/, "") + w.url, {
      method: ep.method,
      headers,
      body: w.body ?? undefined,
      signal: o.signal,
    });
  } catch (e) {
    return unexpected(0, "network", String(e));
  }
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (res.status === ep.success) {
    try {
      return { ok: true, value: ep.decode(res.status === 204 ? undefined : decodeJson(bytes)) };
    } catch (e) {
      if (e instanceof DecodeError) return unexpected(res.status, "decode", bytes);
      throw e;
    }
  }
  const kind = ep.errors[res.status];
  if (kind === undefined) return unexpected(res.status, "status", bytes);
  try {
    return { ok: false, error: { kind, status: res.status, detail: decodeErrorBody(res.status, bytes).detail } };
  } catch (e) {
    if (e instanceof DecodeError) return unexpected(res.status, "decode", bytes);
    throw e;
  }
}

/** A stable query key for params (bigint-safe, key order independent). */
export function canonicalKey(v: unknown): string {
  if (typeof v === "bigint") return v.toString() + "n";
  if (v === undefined) return "u";
  if (typeof v === "number" && !Number.isFinite(v)) return String(v);
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(canonicalKey).join(",") + "]";
  const o = v as Record<string, unknown>;
  return (
    "{" +
    Object.keys(o)
      .filter((k) => o[k] !== undefined)
      .sort()
      .map((k) => JSON.stringify(k) + ":" + canonicalKey(o[k]))
      .join(",") +
    "}"
  );
}
```

- [ ] **Step 4: Write the emitter**

`lattice/tsgen/emit.py`:

```python
"""EndpointPlan -> api.ts (spec 2026-09-29 ui-a §3). hooks_ts is added by Task 7."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from lattice.api.ir import EndpointPlan, ErrorSpec
from lattice.catalog import API_ERRORS
from lattice.targets import TS_REFUSAL
from lattice.tsgen.regex import Untranslatable, fullmatch_source
from lattice.tsgen.specs import plan_specs
from lattice.typesys import Schema

SAFE = 2**53 - 1
KIND = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
        409: "conflict", 422: "validation"}  # fmt: skip
SUCCESS = {"One": 200, "Page": 200, "Created": 201, "NoContent": 204}
TS_SCALAR = {"Str": "string", "Bool": "boolean", "Float": "number", "Int": "bigint",
             "Uuid": "string", "Date": "string", "DateTime": "string"}  # fmt: skip


class TsGenRefusal(Exception):
    """L893: the TypeScript client cannot be generated for this plan."""

    code = TS_REFUSAL

    def __init__(self, plan: EndpointPlan, message: str) -> None:
        super().__init__(f"{plan.span.line}:{plan.span.col}: error {TS_REFUSAL}: {message}")


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def pascal(name: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in name.split("_"))


def kind_of(status: int) -> str:
    return KIND.get(status, f"status_{status}")


def error_statuses(plan: EndpointPlan) -> list[int]:
    """Every error status the server can answer for this plan (not 5xx)."""
    out = {422}
    if plan.auth != "Public":
        out |= {401, 403}
        if plan.active_column is not None:
            out.add(plan.inactive_status)
        for answer in (plan.token_invalid, plan.token_unknown):
            if answer is not None:
                out.add(answer[0])
    out |= {API_ERRORS[e] for e in plan.errors if e in API_ERRORS}
    for step in plan.steps:
        for attr in ("error", "conflict", "missing"):
            spec = getattr(step, attr, None)
            if isinstance(spec, ErrorSpec):
                out.add(spec.status)
    return sorted(out)


def js_number(plan: EndpointPlan, v: float) -> str:
    if not math.isfinite(v):
        raise TsGenRefusal(plan, f"the bound {v!r} is not finite")
    return repr(float(v)) if isinstance(v, float) else str(v)


def ts_value(tagged: dict[str, object]) -> str:
    if tagged["t"] == "int":
        return f'{{ t: "int", v: {tagged["v"]}n }}'
    if tagged["t"] == "float":
        return f'{{ t: "float", v: {tagged["v"]} }}'
    return json.dumps(tagged)


def ts_spec(d: dict[str, object], plan: EndpointPlan) -> str:
    parts = [f"name: {json.dumps(d['name'])}", f'source: "{d["source"]}"', f'scalar: "{d["scalar"]}"']
    for key in ("ge", "le"):
        if key in d:
            parts.append(f"{key}: {js_number(plan, d[key])}")  # type: ignore[arg-type]
    for key in ("min_len", "max_len"):
        if key in d:
            parts.append(f"{key}: {d[key]}")
    if "one_of" in d:
        parts.append(f"one_of: {json.dumps(d['one_of'])}")
    if "default" in d:
        parts.append(f"default: {ts_value(d['default'])}")  # type: ignore[arg-type]
    for key in ("optional", "nullable", "email"):
        if d.get(key):
            parts.append(f"{key}: true")
    if "pattern" in d:
        try:
            js = fullmatch_source(d["pattern"])  # type: ignore[arg-type]
        except Untranslatable as err:
            raise TsGenRefusal(plan, f"pattern {d['pattern']!r}: {err}") from None
        parts.append(f"pattern: {{ py: {json.dumps(d['pattern'])}, js: {json.dumps(js)} }}")
    return "{ " + ", ".join(parts) + " }"


def int_is_number(d: dict[str, object]) -> bool:
    ge, le = d.get("ge"), d.get("le")
    return isinstance(ge, int | float) and isinstance(le, int | float) and -SAFE <= ge and le <= SAFE


def param_field(d: dict[str, object]) -> str:
    t = "number" if d["scalar"] == "Int" and int_is_number(d) else TS_SCALAR[d["scalar"]]  # type: ignore[index]
    name = d["name"]
    if "default" not in d:
        return f"  {name}: {t};"
    nullable = d["default"] == {"t": "none"} or bool(d.get("nullable"))
    return f"  {name}?: {t}{' | null' if nullable else ''};"


def row_type(schema: Schema) -> str:
    fields = [
        f"  {n}: {TS_SCALAR[s.value]}{' | null' if n in schema.nullable else ''};"
        for n, s in schema.columns
    ]
    return "{\n" + "\n".join(fields) + "\n}"


def cols_literal(schema: Schema) -> str:
    cols = (f'[{json.dumps(n)}, "{s.value}", {"true" if n in schema.nullable else "false"}]'
            for n, s in schema.columns)  # fmt: skip
    return "[" + ", ".join(cols) + "]"


def page_shape(plan: EndpointPlan) -> list[tuple[str, str]]:
    total = plan.page is not None and plan.page.total
    return [(k, f) for k, f in plan.page_shape if f and (k != "total" or total)]


def _response(plan: EndpointPlan, row: str) -> tuple[str, str]:
    """(the response type, the decode function)."""
    if plan.success not in SUCCESS:
        raise TsGenRefusal(plan, f"success kind {plan.success!r}")
    if plan.success == "NoContent":
        return "void", "() => undefined"
    cols = cols_literal(plan.schema)
    if plan.success == "Page":
        shape = page_shape(plan)
        fields = [f"{f}: {row}[]" if k == "items" else f"{f}: number" for k, f in shape]
        shape_ts = "[" + ", ".join(f"[{json.dumps(k)}, {json.dumps(f)}]" for k, f in shape) + "]"
        return "{ " + "; ".join(fields) + " }", f"(v) => rt.decodePage(v, {cols}, {shape_ts})"
    return row, f"(v) => rt.decodeRow(v, {cols})"


def _endpoint(plan: EndpointPlan, models: dict[str, str]) -> str:
    specs = plan_specs(plan)
    name, fn = pascal(plan.name), camel(plan.name)
    if plan.response is not None:
        models.setdefault(plan.response, row_type(plan.schema))
        row = plan.response
    else:
        row = row_type(plan.schema)
    resp, decode = _response(plan, row)
    statuses = error_statuses(plan)
    variants = ["rt.ValidationError"] + [
        f'{{ kind: "{kind_of(s)}"; status: {s}; detail: string | null }}' for s in statuses if s != 422
    ] + ["rt.Unexpected"]
    errors = ", ".join(f'{s}: "{kind_of(s)}"' for s in statuses)
    number_ints = [d["name"] for d in specs if d["scalar"] == "Int" and int_is_number(d)]
    fields = "\n".join(param_field(d) for d in specs)
    return f"""
export type {name}Params = {{
{fields}
}};
export type {name}Response = {resp};
export type {name}Error =
  | {"\n  | ".join(variants)};
const {plan.name.upper()}: rt.Endpoint = {{
  method: {json.dumps(plan.method)},
  path: {json.dumps(plan.path)},
  params: [{", ".join(ts_spec(d, plan) for d in specs)}],
  form: {"true" if plan.body == "form" else "false"},
  success: {SUCCESS[plan.success]},
  errors: {{ {errors} }},
  numberInts: {json.dumps(number_ints)},
  decode: {decode},
}};
export function validate{name}(p: {name}Params): rt.FieldError[] {{
  return rt.validate({plan.name.upper()}, p);
}}
export function {fn}(p: {name}Params, o: rt.CallOpts): Promise<rt.Result<{name}Response, {name}Error>> {{
  return rt.call({plan.name.upper()}, p, o) as Promise<rt.Result<{name}Response, {name}Error>>;
}}
"""


HEADER = """// Generated by lattice.tsgen from a checked Lattice program. Do not edit.
// Request validation here is proven identical to the server's (spec 2026-09-29 ui-a §5).
import * as rt from "./lattice-rt.ts";
export type { CallOpts, FieldError, Result, Unexpected, ValidationError } from "./lattice-rt.ts";
"""


def api_ts(plans: Sequence[EndpointPlan]) -> str:
    models: dict[str, str] = {}
    bodies = [_endpoint(p, models) for p in plans]
    model_text = "".join(f"\nexport type {m} = {t};\n" for m, t in models.items())
    registry = ", ".join(f"{json.dumps(p.name)}: {p.name.upper()}" for p in plans)
    return (HEADER + model_text + "".join(bodies)
            + f"\nexport const ENDPOINTS: Record<string, rt.Endpoint> = {{ {registry} }};\n")  # fmt: skip


def hooks_ts(plans: Sequence[EndpointPlan]) -> str:
    return "// hooks.ts: generated by Task 7\nexport {};\n"
```

`lattice/tsgen/__init__.py`:

```python
"""TypeScript client target (spec docs/superpowers/specs/2026-09-29-lattice-ui-a-client-design.md):
a client whose request validation is proven identical to the server's."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from lattice.api.ir import EndpointPlan, lower_api
from lattice.check import CheckResult, check_source
from lattice.profile import Profile
from lattice.tsgen.emit import api_ts, hooks_ts

RUNTIME = Path(__file__).resolve().parent / "rt" / "lattice-rt.ts"


def generate_ts(plans: Sequence[EndpointPlan]) -> dict[str, str]:
    """{filename: source} for the client of `plans` (raises TsGenRefusal, L893)."""
    return {
        "lattice-rt.ts": RUNTIME.read_text(),
        "api.ts": api_ts(plans),
        "hooks.ts": hooks_ts(plans),
    }


def compile_source_to_ts(source: str, profile: Profile) -> tuple[CheckResult, dict[str, str] | None]:
    """Check -> lower -> generate. (check result, None) when the program is rejected."""
    result = check_source(source, profile)
    if result.program is None:
        return result, None
    return result, generate_ts(lower_api(result.program))
```

`lattice/tsgen/__main__.py`:

```python
"""python -m lattice.tsgen SOURCE --profile MODULE:ATTR --out DIR"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from lattice.tsgen import compile_source_to_ts
from lattice.tsgen.emit import TsGenRefusal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lattice.tsgen")
    p.add_argument("source", type=Path)
    p.add_argument("--profile", required=True, help="module:attr (a Profile or a function returning one)")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    module, _, attr = args.profile.partition(":")
    profile = getattr(importlib.import_module(module), attr)
    if callable(profile):
        profile = profile()
    try:
        result, files = compile_source_to_ts(args.source.read_text(), profile)
    except TsGenRefusal as err:
        print(f"{args.source}:{err}", file=sys.stderr)
        return 1
    if files is None:
        for d in result.diagnostics:
            print(f"{args.source}:{d}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (args.out / name).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/tsgen/test_emit.py -v` — Expected: PASS for every program in `PROGRAMS`.

- [ ] **Step 6: Try the CLI on the template**

Run: `python -m lattice.tsgen tests/fixtures/template/items.lattice.py --profile bench.template_profile:template_profile --out /tmp/claude-ui-a && ls /tmp/claude-ui-a`
Expected: `api.ts hooks.ts lattice-rt.ts`. (If the items program alone fails to check without the others, use `bench.template_profile.template_program()` through a two-line script instead and note it in the task report.)

- [ ] **Step 7: Commit**

```bash
git add lattice/tsgen/emit.py lattice/tsgen/__main__.py tests/tsgen/test_emit.py
git commit -m "feat(tsgen): api.ts emitter, the client call path and python -m lattice.tsgen" -- lattice/tsgen tests/tsgen/test_emit.py
```

---

### Task 7: `hooks.ts`

**Files:**
- Modify: `lattice/tsgen/emit.py` (replace `hooks_ts`)
- Test: `tests/tsgen/test_hooks.py`

**Interfaces:**
- Consumes: emitted `api.ts` names (`XYParams`, `xY`, `XYResponse`, `XYError`), `rt.canonicalKey`.
- Produces: per GET plan `useXY(p: api.XYParams, o: CallOpts)` returning `{ data, error, isPending, refetch }`; per other plan `useXY(o: CallOpts)` returning `{ mutate, mutateAsync, data, error, isPending, reset }`.

- [ ] **Step 1: Write the failing test**

`tests/tsgen/test_hooks.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tsgen/test_hooks.py -v` — Expected: FAIL on `use...(` not in hooks.

- [ ] **Step 3: Implement**

Replace `hooks_ts` in `emit.py`:

```python
HOOKS_HEADER = """// Generated by lattice.tsgen. React hooks over api.ts (TanStack Query 5). They never
// throw: `error` is the endpoint's typed error union.
import { useMutation, useQuery } from "@tanstack/react-query";
import * as api from "./api.ts";
import { canonicalKey, type CallOpts } from "./lattice-rt.ts";
"""


def _query_hook(plan: EndpointPlan) -> str:
    n, fn = pascal(plan.name), camel(plan.name)
    return f"""
export function use{n}(p: api.{n}Params, o: CallOpts) {{
  const q = useQuery({{
    queryKey: [{json.dumps(plan.name)}, canonicalKey(p)],
    queryFn: ({{ signal }}) => api.{fn}(p, {{ ...o, signal }}),
  }});
  const r = q.data;
  return {{
    data: r?.ok ? r.value : undefined,
    error: r !== undefined && !r.ok ? r.error : null,
    isPending: q.isPending,
    refetch: q.refetch,
  }};
}}
"""


def _mutation_hook(plan: EndpointPlan) -> str:
    n, fn = pascal(plan.name), camel(plan.name)
    return f"""
export function use{n}(o: CallOpts) {{
  const m = useMutation({{ mutationFn: (p: api.{n}Params) => api.{fn}(p, o) }});
  const r = m.data;
  return {{
    mutate: m.mutate,
    mutateAsync: m.mutateAsync,
    data: r?.ok ? r.value : undefined,
    error: r !== undefined && !r.ok ? r.error : null,
    isPending: m.isPending,
    reset: m.reset,
  }};
}}
"""


def hooks_ts(plans: Sequence[EndpointPlan]) -> str:
    body = "".join(_query_hook(p) if p.method == "GET" else _mutation_hook(p) for p in plans)
    return HOOKS_HEADER + body
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/tsgen/test_hooks.py tests/tsgen/test_emit.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/tsgen/test_hooks.py
git commit -m "feat(tsgen): TanStack Query hooks with typed, non-throwing errors" -- lattice/tsgen/emit.py tests/tsgen/test_hooks.py
```

---

### Task 8: L2 — corpora and the direct differential

**Files:**
- Create: `tests/tsgen/gate/__init__.py`, `tests/tsgen/gate/apps.py`, `tests/tsgen/gate/corpus.py`, `tests/tsgen/gate/corpus/*.json` (generated), `tests/tsgen/test_gate_direct.py`

**Interfaces:**
- Consumes: `pyref.case/answer/pattern_map`, `specs.plan_specs`, `replay.ts` (script mode).
- Produces: `apps.App` (`name`, `plans`, `cases: tuple[WireSeed, ...]`, `setup`), `apps.APPS: dict[str, App]` (every `PROGRAMS` id plus `"template"`); `corpus.wire_corpus(app) -> dict` (`{"params": [...answered cases], "patterns": {...}}`), `corpus.typed_corpus(app) -> list[dict]` (Task 9); `corpus.R_A(value) -> bool`.

- [ ] **Step 1: Write the app registry**

`tests/tsgen/gate/apps.py`:

```python
"""The proof apps (spec 2026-09-29 ui-a §5.2): every API program in the codegen differential
registry, plus the FastAPI template."""

from __future__ import annotations

import dataclasses
import functools
from typing import Any

from bench.template_profile import template_plans
from bench.template_suite import TEMPLATE_SUITE
from lattice.api.ir import EndpointPlan, lower_api
from lattice.tsgen import compile_source_to_ts
from tests.api.test_codegen_differential import PROGRAMS, setup_of


@dataclasses.dataclass(frozen=True)
class Request:
    """A request from an existing suite: method, concrete path, query dict, JSON body."""

    method: str
    path: str
    query: dict[str, Any]
    json: Any
    form: dict[str, str] | None = None


@dataclasses.dataclass(frozen=True)
class App:
    name: str
    plans: tuple[EndpointPlan, ...]
    requests: tuple[Request, ...]


@functools.cache
def app(name: str) -> App:
    if name == "template":
        reqs = tuple(Request(c.method, c.path, dict(c.params or {}), c.json, c.form) for c in TEMPLATE_SUITE)
        return App(name, tuple(template_plans()), reqs)
    source, task = PROGRAMS[name]
    result, files = compile_source_to_ts(source, setup_of(name).profile)
    assert files is not None, result.diagnostics
    plans = tuple(lower_api(result.program))
    reqs = tuple(Request(c.method, c.path, dict(c.query), c.json) for c in task.cases)
    return App(name, plans, reqs)


APP_NAMES = sorted(PROGRAMS) + ["template"]
```

- [ ] **Step 2: Write the corpus generator**

`tests/tsgen/gate/corpus.py`:

```python
"""Request corpora for the gate (spec 2026-09-29 ui-a §5.3): (a) existing suites' requests,
(b) boundary values from each parameter spec, (c) 200 seeded random requests per endpoint.
Wire cases (L2) are answered by the server prelude; typed cases (L3) are sent by the client."""

from __future__ import annotations

import base64
import json
import math
import random
import re
import urllib.parse
import zlib
from typing import Any

from lattice.api.ir import EndpointPlan
from lattice.tsgen.pyref import answer, case, pattern_map, prelude, to_param
from lattice.tsgen.specs import plan_specs
from tests.tsgen.gate.apps import App

RANDOM_PER_ENDPOINT = 200


def R_A(value: object) -> bool:
    """A path value the client cannot route faithfully (decision log: path `Str`)."""
    return isinstance(value, str) and (value in ("", ".", "..") or "/" in value)


# Candidate raw values per scalar: text form for path/query/form, JSON form for bodies.
TEXT = {
    "Int": ["0", "1", "-1", "5", " 5", "\x1c5", "5.0", "1e3", "abc", "", "9007199254740993",
            "9223372036854775807", "9223372036854775808", "-9223372036854775809", "+7", "007"],
    "Float": ["0", "1.5", "-2.5", ".5", "5.", "nan", "inf", "1e16", "1e400", "1_0", "\x1c1.5"],
    "Str": ["", "a", "ab", "abc", "a\n", "é", "👋", "👋👋👋", " x ", "K", "A1_",
            "x@y.z", "it's5"],
    "Bool": ["true", "TRUE", "yes", "on", "1", "0", "off", "no", "maybe", "", "\x1ctrue"],
    "Date": ["2026-09-28", "20260928", "2026-W01-1", "2026-02-30", "", "2026-09-28T00:00"],
    "DateTime": ["2026-09-28T10:11:12", "2026-09-28T10:11:12Z", "2026-09-28T10:11:12+01:00",
                 "2026-09-28T24:00", "2026-09-28 10:00", "20260928T101112", "bad",
                 "0001-01-01T00:00:00+01:00", "2026-09-28T10:11:12.1234567"],
    "Uuid": ["0123456789abcdef0123456789abcdef", "01234567-89AB-CDEF-0123-456789ABCDEF",
             "01234567-89abcdef-0123-456789abcdef", "nope", ""],
}  # fmt: skip
JSON_EXTRA: dict[str, list[Any]] = {
    "Int": [0, 1, -1, 3.0, 1.5, 9007199254740993, 2**63, -(2**63) - 1, 1e300, True, "5", None],
    "Float": [0, 1.5, 9007199254740993, 10**400, True, "1.5", None],
    "Str": ["\ud800", "a\ud83d", 5, None, ["a"]],
    "Bool": [True, False, 1, "true", None],
    "Date": [20260928, None],
    "DateTime": [0, None],
    "Uuid": [0, None],
}  # fmt: skip


def _bounds(d: dict[str, Any]) -> list[str]:
    out: list[str] = []
    s = d["scalar"]
    for key, delta in (("ge", -1), ("le", 1)):
        if key in d and s == "Int":
            b = math.ceil(d[key]) if key == "ge" else math.floor(d[key])
            out += [str(b), str(b + delta)]
        elif key in d and s == "Float":
            out += [repr(float(d[key])), repr(float(d[key]) + delta * 0.5)]
    for key in ("min_len", "max_len"):
        if key in d:
            n = d[key]
            out += ["a" * max(n - 1, 0), "a" * n, "a" * (n + 1), "👋" * n, "👋" * (n + 1)]
    out += list(d.get("one_of", []))
    out += [v.upper() for v in d.get("one_of", [])]
    return out


def text_values(d: dict[str, Any]) -> list[str]:
    return TEXT[d["scalar"]] + _bounds(d)


def json_values(d: dict[str, Any]) -> list[Any]:
    base = [int(v) if d["scalar"] == "Int" and re.fullmatch(r"-?\d+", v) else v for v in text_values(d)]
    return base + JSON_EXTRA[d["scalar"]]


def _accepts(d: dict[str, Any], raw: object, text: bool) -> bool:
    p = prelude()
    try:
        value = p["_convert"](to_param(d), raw, text=text)
        p["_constrain"](to_param(d), value)
    except ValueError:
        return False
    return True


def valid_value(d: dict[str, Any], seen: list[object], text: bool) -> object | None:
    """A value the server accepts for `d` (None: none found, the parameter is left out)."""
    pool = seen + (text_values(d) if text else json_values(d))
    return next((v for v in pool if not (d["source"] == "path" and R_A(v)) and _accepts(d, v, text)), None)


def _route(plan: EndpointPlan, path: str) -> dict[str, str] | None:
    names = re.findall(r"\{([^}]+)\}", plan.path)
    rx = re.escape(plan.path)
    for n in names:
        rx = rx.replace(re.escape("{" + n + "}"), f"(?P<{n}>[^/]+)")
    m = re.fullmatch(rx, path.split("?")[0])
    return None if m is None else {k: urllib.parse.unquote(v) for k, v in m.groupdict().items()}


def _qtext(v: object) -> str:
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def _wire(plan: EndpointPlan, specs: list[dict[str, Any]], name: str, path: dict[str, str],
          query: dict[str, object], body: dict[str, object] | None, rules: tuple[str, ...] = ()
          ) -> dict[str, Any]:  # fmt: skip
    q = "&".join(f"{urllib.parse.quote(k)}={urllib.parse.quote(_qtext(v))}" for k, v in query.items())
    if plan.body == "form":
        raw = urllib.parse.urlencode({k: _qtext(v) for k, v in (body or {}).items()}).encode()
        return case(name, specs, path=path, query=q, body=raw, form=True,
                    ctype="application/x-www-form-urlencoded", rules=rules)  # fmt: skip
    raw = b"" if body is None else json.dumps(body).encode()
    return case(name, specs, path=path, query=q, body=raw, rules=rules)


def _endpoint_cases(app: App, plan: EndpointPlan) -> list[dict[str, Any]]:
    specs = plan_specs(plan)
    if not specs:
        return []
    has_body = any(d["source"] == "body" for d in specs)
    seen: dict[str, list[object]] = {d["name"]: [] for d in specs}
    out: list[dict[str, Any]] = []
    # (a) the suites' requests
    for i, r in enumerate(app.requests):
        if r.method != plan.method or (path := _route(plan, r.path)) is None:
            continue
        body = r.form if plan.body == "form" else r.json
        out.append(_wire(plan, specs, f"{plan.name} suite {i}", path, r.query,
                         body if has_body else None))  # fmt: skip
        for d in specs:
            src = path if d["source"] == "path" else r.query if d["source"] == "query" else (body or {})
            if d["name"] in src:
                seen[d["name"]].append(src[d["name"]])
    text = {d["name"]: d["source"] != "body" or plan.body == "form" for d in specs}
    valid = {d["name"]: valid_value(d, seen[d["name"]], text[d["name"]]) for d in specs}

    def request(over: dict[str, object], drop: str | None = None) -> tuple[dict, dict, dict | None]:
        path, query, body = {}, {}, ({} if has_body else None)
        for d in specs:
            n = d["name"]
            v = over.get(n, valid[n])
            if n == drop or v is None and n not in over:
                continue
            target = path if d["source"] == "path" else query if d["source"] == "query" else body
            target[n] = v  # type: ignore[index]
        return path, query, body

    # (b) boundaries: one parameter varied, the others valid
    for d in specs:
        n = d["name"]
        values = text_values(d) if text[n] else json_values(d)
        for k, v in enumerate(values):
            if d["source"] == "path" and (v is None or R_A(v)):
                continue
            if d["source"] == "query" and v is None:
                continue
            out.append(_wire(plan, specs, f"{plan.name} {n} b{k}", *request({n: v})))
        if d["source"] != "path":
            out.append(_wire(plan, specs, f"{plan.name} {n} missing", *request({}, drop=n)))
    # (c) seeded random
    rng = random.Random(zlib.crc32(f"{app.name}/{plan.name}".encode()))
    for k in range(RANDOM_PER_ENDPOINT):
        over: dict[str, object] = {}
        for d in specs:
            roll = rng.random()
            pool = text_values(d) if text[d["name"]] else json_values(d)
            if roll < 0.4:
                v = rng.choice(pool)
                if d["source"] == "path" and (v is None or R_A(v)):
                    continue
                if d["source"] == "query" and v is None:
                    continue
                over[d["name"]] = v
        out.append(_wire(plan, specs, f"{plan.name} r{k}", *request(over)))
    return out


def wire_corpus(app: App) -> dict[str, Any]:
    cases = [answer(c) for plan in app.plans for c in _endpoint_cases(app, plan)]
    return {"app": app.name, "params": cases, "patterns": pattern_map(cases)}
```

- [ ] **Step 3: Write the L2 test**

`tests/tsgen/test_gate_direct.py`:

```python
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
```

- [ ] **Step 4: Generate and run**

Run: `LATTICE_REGEN_CORPUS=1 pytest tests/tsgen/test_gate_direct.py -v`
Expected: every app PASS with 0 mismatches. **Stop point (spec §7):** if any mismatch remains, fix `lattice-rt.ts` (or record a semantics-table rule in Task 0's files) before Task 9. Then run once more without the env var to confirm the checked-in corpora are stable.

- [ ] **Step 5: Commit**

```bash
git add tests/tsgen/gate tests/tsgen/test_gate_direct.py
git commit -m "test(tsgen): L2 direct differential over every proof app's corpus" -- tests/tsgen/gate tests/tsgen/test_gate_direct.py
```

---

### Task 9: L3 — the HTTP differential

**Files:**
- Modify: `tests/tsgen/gate/apps.py` (add `serve`, `ADMIN_TOKENS`), `tests/tsgen/gate/corpus.py` (add `typed_corpus`)
- Create: `tests/tsgen/gate/http_driver.ts`, `tests/tsgen/test_gate_http.py`

**Interfaces:**
- Consumes: `apps.app`, corpus value pools, generated `api.ts` (`ENDPOINTS`), `rt.validate`, `rt.call` with `unchecked`.
- Produces: `apps.serve(name, workdir) -> contextmanager[str]` (base URL of a running generated app on a seeded DB); `apps.ADMIN_TOKENS: dict[str, str]`; `corpus.typed_corpus(app) -> list[dict]` (`{"name", "endpoint", "params": {name: tagged}}`, tags as fixtures plus `{"t": "json", "v": any}` for raw wrong-type values).

- [ ] **Step 1: Add the server runner and admin tokens**

Append to `tests/tsgen/gate/apps.py`:

```python
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from lattice.api.codegen import compile_source_to_fastapi
from tests.api.test_codegen_differential import KIT_PATH

ROOT = Path(__file__).resolve().parents[3]

# A token of a user in the admin role (admitted by every endpoint, codegen.py:867), per app.
# Fill each from the app's seed data: bench.api_datasets.TOKENS, the module TOKENS of
# tests/api/<module>.py, or (template) create_access_token for the seeded superuser.
# test_admin_tokens_are_admitted checks every entry.
ADMIN_TOKENS: dict[str, str] = {}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve(name: str, workdir: Path) -> Iterator[str]:
    """The generated FastAPI app of `name`, on a fresh seeded app.db, over real HTTP."""
    if name == "template":
        from bench.template_app.seed import build_template_db
        from bench.template_profile import template_profile, template_program

        _res, code = compile_source_to_fastapi(template_program(), template_profile(),
                                               natives_module="bench.template_profile")  # fmt: skip
        build_template_db(workdir / "app.db")
    else:
        source, _task = PROGRAMS[name]
        setup = setup_of(name)
        _res, code = compile_source_to_fastapi(source, setup.profile)
        if setup.natives_file is not None:
            shutil.copyfile(setup.natives_file, workdir / "natives.py")
        setup.build(workdir / "app.db")
    assert code is not None
    (workdir / "app.py").write_text(code)
    shutil.copyfile(KIT_PATH, workdir / "platform_kit.py")
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": f"{workdir}{os.pathsep}{ROOT}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(port), "--log-level", "warning"],
        cwd=workdir, env=env,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"{name}: the app did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        proc.wait(10)
```

(If the template's generated app does not read `app.db` through `platform_kit`, build it the way `tests/template/test_template_differential.py:214` does and keep the same `serve` signature; the admin-token test below proves the wiring.)

- [ ] **Step 2: Add the typed corpus**

Append to `tests/tsgen/gate/corpus.py`:

```python
def _typed(d: dict[str, Any], raw: object, text: bool) -> dict[str, Any]:
    """A client-side value: what a program would pass for this parameter."""
    s = d["scalar"]
    if raw is None:
        return {"t": "none"}
    if text and isinstance(raw, str):
        if s == "Int" and re.fullmatch(r"-?\d+", raw):
            return {"t": "int", "v": str(int(raw))}
        if s == "Bool" and raw in ("true", "false"):
            return {"t": "bool", "v": raw == "true"}
        if s in ("Str", "Date", "DateTime", "Uuid"):
            return {"t": "str", "v": raw}
        return {"t": "json", "v": raw}  # a wrong-typed string: sent as its text
    if isinstance(raw, bool):
        return {"t": "bool", "v": raw}
    if isinstance(raw, int):
        return {"t": "int", "v": str(raw)}
    if isinstance(raw, float):
        return {"t": "float", "v": repr(raw)}
    if isinstance(raw, str) and s in ("Str", "Date", "DateTime", "Uuid"):
        return {"t": "str", "v": raw}
    return {"t": "json", "v": raw}


def typed_corpus(app: App) -> list[dict[str, Any]]:
    """L3 cases: the wire corpus's requests as client-side values (JSON-safe)."""
    out: list[dict[str, Any]] = []
    for plan in app.plans:
        specs = {d["name"]: d for d in plan_specs(plan)}
        for c in _endpoint_cases(app, plan):
            params: dict[str, Any] = {}
            params.update(c["path"])
            params.update(dict(urllib.parse.parse_qsl(c["query"], keep_blank_values=True)))
            body = base64.b64decode(c["body"])
            if body and plan.body == "form":
                params.update(dict(urllib.parse.parse_qsl(body.decode(), keep_blank_values=True)))
            elif body:
                params.update(json.loads(body))
            text = lambda n: specs[n]["source"] != "body" or plan.body == "form"  # noqa: E731
            typed = {n: _typed(specs[n], v, text(n)) for n, v in params.items() if n in specs}
            if any(isinstance(v, float) and not math.isfinite(v) for v in params.values()):
                continue  # JSON cannot carry a non-finite float to Node; L1/L2 cover it
            out.append({"name": c["name"], "endpoint": plan.name, "params": typed})
    return out
```

- [ ] **Step 3: Write the Node driver**

`tests/tsgen/gate/http_driver.ts`:

```ts
// L3 driver: for each case, the client's local verdict and the server's real answer.
// argv: <dir with api.ts> <baseUrl> <token>; stdin: JSON list of typed cases.
import { readFileSync } from "node:fs";

const [dir, baseUrl, token] = process.argv.slice(2);
const api = await import(dir + "/api.ts");
const rt = await import(dir + "/lattice-rt.ts");

function value(t: any, asNumber: boolean): unknown {
  switch (t.t) {
    case "none": return null;
    case "int": return asNumber ? Number(t.v) : BigInt(t.v);
    case "float": return t.v === "nan" ? NaN : t.v === "inf" ? Infinity : t.v === "-inf" ? -Infinity : Number(t.v);
    default: return t.v; // bool, str, json
  }
}

const show = (v: unknown): unknown =>
  typeof v === "bigint" ? { $int: v.toString() } : Array.isArray(v) ? v.map(show)
  : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, show(x)])) : v;

const out = [];
for (const c of JSON.parse(readFileSync(0, "utf8"))) {
  const ep = api.ENDPOINTS[c.endpoint];
  const p = Object.fromEntries(Object.entries(c.params).map(([k, t]) => [k, value(t, ep.numberInts.includes(k))]));
  const local = rt.validate(ep, p);
  const r = await rt.call(ep, p, { baseUrl, token, unchecked: true });
  out.push({ name: c.name, local, ok: r.ok, value: r.ok ? show(r.value) : undefined, error: r.ok ? undefined : r.error });
}
console.log(JSON.stringify(out));
```

- [ ] **Step 4: Write the L3 test**

`tests/tsgen/test_gate_http.py`:

```python
"""L3 (spec 2026-09-29 ui-a §5.1 claims 2 and 3) over real HTTP, for an admitted caller."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lattice.tsgen import generate_ts
from tests.tsgen.gate.apps import ADMIN_TOKENS, APP_NAMES, app, serve
from tests.tsgen.gate.corpus import typed_corpus
from tests.tsgen.node import run_node

DRIVER = Path(__file__).resolve().parent / "gate" / "http_driver.ts"


def _run(name: str, tmp: Path, cases: list[dict], token: str | None) -> list[dict]:
    client = tmp / "client"
    client.mkdir()
    for fname, text in generate_ts(app(name).plans).items():
        (client / fname).write_text(text)
    server = tmp / "server"
    server.mkdir()
    with serve(name, server) as base:
        done = run_node([str(DRIVER), client.as_uri(), base, token or ""], cwd=tmp,
                        stdin=json.dumps(cases))  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout)


def test_every_app_has_an_admin_token() -> None:
    assert set(ADMIN_TOKENS) == set(APP_NAMES)


@pytest.mark.parametrize("name", APP_NAMES)
def test_http_differential(name: str, tmp_path: Path) -> None:
    cases = typed_corpus(app(name))
    results = _run(name, tmp_path, cases, ADMIN_TOKENS[name])
    bad = []
    admitted = 0
    for c, r in zip(cases, results, strict=True):
        err = r.get("error") or {}
        status = 200 if r["ok"] else err.get("status")
        if status not in (401, 403):
            admitted += 1
        server_422 = not r["ok"] and status == 422
        if bool(r["local"]) != server_422:
            bad.append((c["name"], "claim 2", r["local"], err))
        elif server_422 and r["local"] != err["detail"]:
            bad.append((c["name"], "claim 2 detail", r["local"], err["detail"]))
        if not r["ok"] and err.get("kind") == "unexpected" and (err["reason"] == "decode" or err["status"] < 500):
            bad.append((c["name"], "claim 3", err))
    assert admitted > 0, "the admin token was never admitted"
    assert not bad, bad[:20]


def test_unadmitted_caller_gets_auth_error_first(tmp_path: Path) -> None:
    """Review Focus 1 / spec delta 1: the server authenticates before it validates."""
    name = "realapps"
    plan = next(p for p in app(name).plans if p.auth != "Public" and any(
        s.source == "query" and s.scalar is not None and s.scalar.value == "Int" for s in p.params))  # fmt: skip
    param = next(s.name for s in plan.params if s.source == "query" and s.scalar.value == "Int")
    cases = [{"name": "bad token, bad param", "endpoint": plan.name,
              "params": {param: {"t": "json", "v": "not-a-number"}}}]  # fmt: skip
    [r] = _run(name, tmp_path, cases, "no-such-token")
    assert r["local"] != []  # the client alone says 422
    assert r["error"]["status"] == 401  # the server answers the auth error first


def test_unroutable_path_values(tmp_path: Path) -> None:
    """Review Focus 2 / R-a: sent, never a 422 from the server, never a crash."""
    name = "realapps"
    plan = next(p for p in app(name).plans if "{" in p.path)
    param = next(s for s in plan.params if s.source == "path")
    results = _run(name, tmp_path, [
        {"name": f"R-a {v!r}", "endpoint": plan.name, "params": {param.name: {"t": "str", "v": v}}}
        for v in ["", ".", "..", "a/b"]
    ], ADMIN_TOKENS[name])  # fmt: skip
    for r in results:
        assert r["ok"] or r["error"]["status"] != 422 or r["local"] != []
```

(If `realapps` has no authenticated endpoint with an Int query parameter, pick the first registry app that does; keep the assertion.)

- [ ] **Step 5: Fill `ADMIN_TOKENS` and run**

Fill `ADMIN_TOKENS` from each app's seed (`grep -n "TOKENS\s*=" bench/api_datasets.py tests/api/*.py`; template: `create_access_token` from `bench/template_profile.py` for the superuser in `bench/template_app/seed.py`).

Run: `pytest tests/tsgen/test_gate_http.py -v`
Expected: all PASS. A claim-3 failure on a Public endpoint answering 401/403 means `error_statuses` must include them for Public plans too: fix it in `emit.py` and rerun Tasks 6–9's tests.

- [ ] **Step 6: Commit**

```bash
git add tests/tsgen/gate/http_driver.ts tests/tsgen/test_gate_http.py
git commit -m "test(tsgen): L3 HTTP differential; auth-first and R-a behaviour pinned" -- tests/tsgen/gate tests/tsgen/test_gate_http.py
```

---

### Task 10: L4 — strict types, the exhaustiveness negative test, required mode

**Files:**
- Create: `tests/ts/package.json`, `tests/ts/tsconfig.json`, `tests/ts/package-lock.json` (from `npm install`), `tests/ts/.gitignore`
- Create: `tests/tsgen/test_types.py`

**Interfaces:**
- Consumes: `generate_ts`, `apps.app`, `emit.error_statuses`, `emit.kind_of`, `emit.pascal`, `emit.camel`.

- [ ] **Step 1: Add the toolchain**

`tests/ts/package.json`:

```json
{
  "private": true,
  "type": "module",
  "devDependencies": {
    "@tanstack/react-query": "^5.0.0",
    "@types/react": "^19.0.0",
    "react": "^19.0.0",
    "typescript": "^5.8.0"
  }
}
```

`tests/ts/tsconfig.json`:

```json
{
  "compilerOptions": {
    "strict": true,
    "noEmit": true,
    "target": "ES2022",
    "lib": ["ES2024", "DOM"],
    "module": "preserve",
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "erasableSyntaxOnly": true,
    "skipLibCheck": true
  },
  "include": ["gen/**/*.ts"]
}
```

`tests/ts/.gitignore`:

```
node_modules/
gen/
```

Run: `npm install --prefix tests/ts` — Expected: `package-lock.json` written.

- [ ] **Step 2: Write the test**

`tests/tsgen/test_types.py`:

```python
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
```

- [ ] **Step 3: Run**

Run: `pytest tests/tsgen/test_types.py -v` — Expected: 2 passed. Fix any strict-mode error in `emit.py` or `lattice-rt.ts` (not in `tsconfig.json`).

- [ ] **Step 4: Run the whole gate in required mode**

Run: `LATTICE_REQUIRE_NODE=1 pytest tests/tsgen -v` and then `pytest -q` (the whole suite).
Expected: every tsgen test passes with no skips; the full suite is as green as before this plan.

- [ ] **Step 5: Commit**

```bash
git add tests/ts tests/tsgen/test_types.py
git commit -m "test(tsgen): L4 strict type-check and the exhaustive error-handling negative test" -- tests/ts tests/tsgen/test_types.py
```

---

## Done when (spec §9)

- `LATTICE_REQUIRE_NODE=1 pytest tests/tsgen` is green: L1–L4 with 0 mismatches over the template and every registry program.
- The negative exhaustiveness test fails `tsc` as intended.
- `docs/lattice-language-rules.md` has the three client rows and R2a; `tests/tsgen/test_governance.py` (R14 client checks) passes.
