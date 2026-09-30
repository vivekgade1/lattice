# Lattice — Language Rules

> Status: binding · 2026-09-28 · Applies to every change to the Lattice language, its checker, its IRs and its code targets. Companion to `docs/lattice-vision.md` (§3 design principles) and the semantics table in `lattice/targets.py`.

Lattice has more than one code target (Python today, Go next, others later). The risk with several targets is a slow slide towards the lowest common denominator: each new feature is cut down to what every target can do, until the language can express less than any of its targets. These rules prevent that. They also keep the guarantees, which are the reason Lattice exists.

## 1. The meaning comes first, then the targets

**R1. Lattice has one meaning, defined once.** Semantics are written in `lattice/targets.py` (the semantics table) and pinned by the shared semantics suite (`tests/semantics_v11/`). No target defines them. Python's behaviour is a reference for spelling and error text, never the definition.

**R2. Every target implements the full meaning, or rejects the program before code is generated.** A target may say "not available here yet" (a check-time diagnostic from `p8_target`). It may never run a program with a different result, whether that is a different value, a different error, a different order or a different fuel point.

**R2a. Client targets.** A client target (the TypeScript client) carries the *API request semantics* — parameter conversion and validation, wire formats, error bodies (`API_PARAM_RULES` in `lattice/targets.py`) — and not T2. It must accept exactly the requests the server accepts, with the same 422 detail, for a caller the endpoint admits.

## 2. When a restriction is allowed

**R3. A rule may apply to every target only when the targets would otherwise give different answers.** Examples: RE2-only regular expressions, insertion-ordered sets, 64-bit `Int`. A language-wide restriction therefore needs:
- a concrete input on which the targets diverge;
- proof that a portable meaning is impossible or unreasonably costly;
- a documented rewrite for programs that used the removed form.

**R4. Everything else is target-gated, never removed.** If only one target lacks a feature, reject it for that target (L890/L891/L892 style) and keep it everywhere else. A gate is a temporary debt, tracked in §5, not a language decision.

**R5. Never remove to simplify an emitter.** "It's hard to generate on target X" is a reason to gate for X or to implement it in X's runtime, not to take it out of the language.

## 3. How expressiveness grows

**R6. Grow with explicit, portable types and constructs, not host-language quirks.** When a restriction removes capability (for example, integers beyond 64 bits), the answer is a new portable construct (`BigInt`, `Decimal`) with one meaning on every target. The answer is never to expose one host language's behaviour.

**R7. Every new construct ships with, in the same change:**
1. its checker rule and diagnostic (with an exact fix hint);
2. its lowering into the IR;
3. semantics cases in the shared suite, run on **every** target;
4. either an implementation on every target or an explicit target gate with a tracking entry (§5);
5. differential tests at the gate level across every target that carries the construct (Python ≡ Go for T2; Python ≡ TS client for API parameters).

A construct missing any of these is not part of the language.

**R8. Evidence decides what is added.** Constructs are added when benchmarks, imports or repair loops show that models or real code need them (vision §3). The same applies in reverse: a restriction must show the divergence it prevents (R3).

## 4. Guarantees

**R9. Guarantees are never traded for portability or expressiveness.** Types, ownership, bounds, declared errors, fuel and single-statement reads hold on every target. A target that cannot uphold a guarantee rejects the program; it doesn't run it with a weaker one.

**R10. Determinism is part of the meaning.** Iteration orders, float text, error messages and fuel exhaustion points are specified, so the same program gives the same answer on every run and every target. Anything left unspecified is a bug in the semantics table.

## 5. Governance

**R11. Every language-wide restriction and every target gate has an entry in the decision log below.** The entry records the rule, the divergence (R3) or the missing target work (R4), the rewrite, the removal condition, and the date.

**R12. Gates have exit conditions.** Each gate names what would lift it (for example, "Go natives: when a Go native interface exists"). A gate that has lasted more than two sub-projects is reviewed: implement it, or promote it to a documented language rule under R3.

**R13. Semantics changes are versioned.** A change to meaning bumps the semantics version (`SEMANTICS_VERSION`, the `# lattice: vX.Y` header), is listed in the version notes, and must be stricter-only (a program either behaves the same or is rejected at check time) unless the change fixes a divergence under R3.

**R14. The checks are automated.** CI must fail when:
- a construct has no semantics case on some target;
- a semantics case's result differs between targets;
- a `p8_target` gate has no decision-log entry;
- a new language-wide diagnostic appears without a decision-log entry;
- an API parameter feature (PARAM_FEATURES) or rule (API_PARAM_RULES) has no fixture case on a client target.

## 6. Decision log

| Date | Kind | Rule | Why (divergence or missing work) | Rewrite | Lifted when |
|---|---|---|---|---|---|
| 2026-09-28 | Language rule (R3) | `Int` is signed 64-bit; overflow → R623 | Python ints are unbounded, Go's are 64-bit | a portable `BigInt` type (roadmap) | never; superseded by `BigInt` for large values |
| 2026-09-28 | Language rule (R3) | Regex is the RE2 subset (L926) | Portability to future targets (JavaScript and others, whose engines lack some of these constructs or give them other semantics) and a bounded matching cost (no backtracking through lookaround or backreferences). Since 2026-09-29 (plan 5a Task 9) Go runs Python's own SRE engine (a port), so this is no longer a Go divergence | two matches, a split, or a loop in a procedure | never |
| 2026-09-28 | Language rule (R3) | `\d \w \s \b` are ASCII | Portability to future targets (JavaScript and others, whose class definitions differ from Python's Unicode-aware ones) and answers independent of the Unicode version. Since 2026-09-29 (plan 5a Task 9) Go runs Python's SRE engine with `re.ASCII`, so this is no longer a Go divergence | an explicit character class | never |
| 2026-09-28 | Language rule (R3, R10) | Sets iterate in insertion order; set algebra is left operand first; `Counter` ties keep insertion order | Python's string hashing differs per process | — | never (determinism) |
| 2026-09-28 | Language rule (R3) | `xs[a:b] += ys` rejected (L913) | Slice assignment is already rejected; the augmented form had no portable meaning | `xs = xs[:a] + (xs[a:b] + ys) + xs[b:]` | never |
| 2026-09-28 | Target gate (R4) | Natives on Go (L890). Since 2026-09-29 (plan 5a Task 13) a native used inside a procedure is L890 only, not also L892 for the `native` namespace's value kind, so a program with natives is rejected for Go with L890 alone | No Go native interface yet | compile for Python | a Go native interface exists |
| 2026-09-28 | Target gate (R4) | Tabular profile on Go (L891) | Tabular plan holds Python AST; no polars in Go | compile for Python | tabular plan lowered via SQL/DuckDB |
| 2026-09-28 | Target gate (R4) | T2 constructs not yet on Go (L892). Since 2026-09-29 (plan 5a Task 10) every T2 call is implemented on Go (`GO_T2_CALLS` holds the catalog's whole call set) and every value kind (the endpoint layer's `Row` and `Caller` since plan 5a Task 12, 2026-09-29: generated structs from the API profile's tables); what remains gated: `str()` / f-string fields of containers, a non-literal `re.sub` replacement, and a `strftime` format (or a Date/DateTime f-string spec) that is not a literal of the directives Go implements (`%Y %m %d %H %M %S %f %y %a %A %b %B %j %p %%`, C locale) | Go runtime work outstanding | compile for Python; format containers / other directives yourself | Go implements the construct (tracked in `GO_T2_CALLS`, `GO_T2_TYPES`, `p8_target`). The forms whose Python result was an exact int typed Float (`Int ** n` with a non-literal `n`, `round(Int, n)`, `min`/`max` of Int and Float arguments) were gated here too; lifted 2026-09-28 by R-widen (plan 5a Task 8b) |
| 2026-09-28 | Language rule (R3) | R-widen: an Int value in a place the checker types Float is converted to a float there, on every target. The places: an assignment (declared type), an unpacking or `for` target, an item or key store, `return`, a procedure's, record's, builtin's, method's or stdlib function's argument, a record default, a list / set / dict literal element (`**d` included), an if-expression branch, an `and` / `or` operand, a tuple element read with a non-literal index or iterated. `sum` of Floats starts at `0.0`, and `Int ** n` typed Float (a non-literal `n`) is Float arithmetic. Not a site: a comparison (an Int and a Float compare exactly, at any depth: `(2**53 + 1, 0) == (2.0**53, 0.0)` is False, and tuple ordering decides on the first items that differ exactly), `round(Int)` (an Int), and -- since 2026-09-29 (plan 5a Task 10) -- membership and lookup, which are exact, consistent with `==`: a probe holding an Int where the container holds a Float, at any depth (a tuple's items, an optional value), in `x in list / tuple / set / dict / view`, `d[k]` (also the read of `d[k] op= v`), `d.get(k)`, `d.pop(k)`, `xs.index/count/remove(k)`, `s.discard/remove(k)` is compared exactly (`2**53 + 1 in [2.0**53]`, `2**53 in (2**53 + 1, 1.5)` and `(2**53 + 1, 0) in {(2.0**53, 0.0)}` are False; a KeyError shows the probe). A lookup that stores (a defaultdict's missing key, and `d[k] op= v` on a defaultdict or Counter whose exact key is absent) stores the key widened, as `d[k] = v` does | Python kept the exact int: `x: Float = n; str(x)` was `1` on Python and `1.0` on Go, `int(x * x)` exact on Python and rounded on Go, an empty `sum` of Floats `0`, `2 ** n` `8` | none needed; `float(n)` makes the conversion visible | never |
| 2026-09-28 | Language rule (R3) | R-pow: a Float `**` whose base is negative and finite and whose exponent is finite and not integral raises `ValueError` (`math domain error`, as `math.pow`), a catchable error, on every target | Python's `**` returns a complex number from Float-typed code (`(-4.0) ** 0.5`); Go has no such value | test the base first, or take `abs(x) ** y` when the sign is not meant | never |
| 2026-09-29 | Language rule (R3/R9) | findall/split result types follow the pattern's groups: `re.findall` of a literal pattern with k ≥ 2 capturing groups is `list[tuple[Str, …k]]` (0 or 1 group: `list[Str]`); `re.split` by a pattern with groups is `list[Str \| None]` (the groups are inserted, None for one that did not participate) | Python returns tuples / inserts groups, so the former `list[Str]` typing let `re.findall(r"(a)(b)", s)[0].upper()` check and then raise an uncatchable AttributeError | use the tuple's items, or narrow the split items (`x or ""`); a pattern without capturing groups `(?:...)` keeps `list[Str]` | never |
| 2026-09-29 | Language rule (R3/R9) | A Counter's counts are Ints: `Counter(d)` of a dict needs `dict[K, Int]` (L904; the dict's values are the counts), and `Counter.update` of a non-empty Counter adds counts with R623 outside the 64-bit range (plan 5a Task 10) | `Counter(d)` took a `dict[K, Str]`'s strings as counts while typed `Counter[K]`; the sum of two counts could leave the 64-bit Int range unchecked | `Counter(d.keys())` to count keys | never |
| 2026-09-29 | Language rule (R1) | A dict or set changed while it is iterated stops or raises `RuntimeError` exactly where CPython 3.14's iterator does, on every target (Go's dict reproduces CPython's entry layout and resize policy) | the checker allows the mutation (only a change of size is always refused, at the next step) | iterate a copy (`for k in list(d)`) | never |
| 2026-09-29 | Language rule (R9) | `json.dumps` charges each node of its value as it counts it (the same total as before): a Json that holds itself runs out of fuel (R620) on every target | the count walked a self-holding value forever before charging anything | — | never |
| 2026-09-29 | Known limitation | `math.exp/log/log10/pow` and Float `**` are correctly rounded on Go; CPython calls the platform's libm, which is off by one in the last place for rare inputs (macOS 26 arm64: 9 of 22,403 sampled math answers, rt/testdata/gen_math_answers.py) | a portable, deterministic result needs one algorithm; the platform libm differs between machines even for Python | — | never (Go's answer is the correctly rounded one) |
| 2026-09-29 | Known limitation | JSON nesting deeper than the host's C stack: CPython's `json.loads`/`json.dumps` raise RecursionError at a depth set by the machine's stack (about 50,000 on macOS); Go has no such limit | Python's limit is not a fixed number | — | never |
| 2026-09-28 | Known limitation | Unicode 15 (Go, x/text) vs 16 (Python 3.14) | Library versions | — | Go's x/text reaches Unicode 16 |
| 2026-09-29 | Representation (client) | Int typed by declared range: `number` when ge and le lie within ±(2^53−1), else `bigint`; response Int columns `bigint`; page counts `number`, decoded exactly | JS `number` is exact only to 2^53 | — | never (representation) |
| 2026-09-29 | Known limitation (client) | A path `Str` that is empty, `.`, `..` or contains `/` is sent unvalidated for routing | `fetch` normalises `.`/`..`; Starlette routes on the decoded path | use a query or body parameter | never (HTTP routing) |
| 2026-09-29 | Target gate (R4) | L893: the TS client cannot be generated for a plan (an untranslatable pattern, a non-finite bound, an unknown success kind) | emitter coverage | compile without `--target ts` | the emitter handles the construct |

## 7. Recommendations (not yet in place)

1. **Add `BigInt` and `Decimal` as portable types** (R6) before any user needs integers beyond 64 bits or exact decimal money. Go has `math/big`; Python has `int` and `decimal`.
2. **Treat `lattice/targets.py` as the single source of truth** for both the semantics table and the gates. Generate the decision-log rows for gates from it, so the log can't drift from the code.
3. **Review gates at the end of each sub-project** (R12). Each review either lifts a gate or turns it into a language rule, and writes down which.
4. **Choose the next target by what it adds, not by its reach.** Before adding a third target, check its gaps against this log. A target that would need many new language-wide rules is a sign it should be gated or deferred, not accommodated.

Done: **the R14 CI checks** (plan 5a Tasks 6 and 13): `tests/test_language_rules.py` fails when a `p8_target` code or a v1.1 language-wide rule has no decision-log row, or when a semantics case is missing on a target.

## 8. Version notes (R13)

### v1.1 changes (2026-09-28)

A v1 program under v1.1 either behaves the same or is rejected at check time, except
for these changes, each fixing a divergence between targets (R3, §6):

- **64-bit `Int`**: a value outside the signed 64-bit range fails at runtime with R623 (it
  was unbounded up to 65,536 bits).
- **Insertion-ordered sets**: sets iterate in insertion order, and set algebra keeps the
  left operand's order first; `Counter.most_common` ties keep insertion order.
- **ASCII regex classes**: `\d \w \s \b` match ASCII only.
- **RE2 subset**: lookaround, backreferences, conditionals, possessive repeats and atomic
  groups are rejected at check time (L926).
- **L913 for augmented slice assignment**: `xs[a:b] += ys` is rejected, as slice
  assignment already was.
- **Explicit widening (R-widen)**: an Int value in a Float-typed place becomes a float
  there. `x: Float = n; str(x)` is `'1.0'` (was `'1'`), an empty `sum` of Floats is `0.0`
  (was `0`), `Int ** n` with a non-literal `n` is Float arithmetic (`str(2 ** n)` is
  `'8.0'`, was `'8'`; a huge power overflows with OverflowError instead of R622), and
  `min`/`max` of an Int and a Float return a float.
- **Exact membership and lookup**: `in`, `d[k]` (and the read of `d[k] op= v`), `get`, `pop`, `index`, `count`, `remove`, `discard` no longer widen an Int probe among Floats, at any depth (nested tuples, optional values): they compare exactly, as `==` does (`2**53 + 1 in [2.0**53]` is False, was True; a KeyError shows the Int).
- **Float power domain (R-pow)**: a negative base raised to a non-integral Float exponent
  raises ValueError (`math domain error`) instead of returning a complex number.
