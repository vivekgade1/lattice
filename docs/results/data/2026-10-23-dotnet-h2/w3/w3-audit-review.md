# W3 transcript audit: human review (prereg 9.5), 2026-10-23

`audit_transcript.py` returned `verdict: void` (exit 5 of `dispatch_mutants.sh`): forbidden 0, outside 1, review 0.
The one outside item is call 6 (Bash), token `../../../mutants/m$n.diff)`, extracted as the path `/Users/mutants/m/Users/vivekgade/Documents/dn-h2-w/$1.diff)`.
Read the call: `for n in 01 .. 16; do d=scratch/build/m$n; ... (cd $d && patch -p1 -s < ../../../mutants/m$n.diff) ...`, run with cwd
`~/Documents/dn-h2-w`. From `scratch/build/mNN`, `../../../mutants/mNN.diff` is `~/Documents/dn-h2-w/mutants/mNN.diff`: inside W3's directory.
The audit's token is a heuristic misparse of the shell variables `$d` and `$n` and the `../` hops. All 8 tool calls were read (1 Read of BRIEF.md, 7 Bash); the
two cwds are `~/Documents/dn-h2-w` and `~/Documents/dn-h2-w/tests/spec/apiary`; no access to any `~/Documents/lattice*`, `~/.claude`, or another
author's directory. Finding: not a real outside access; VOID not confirmed; the deliverable stands.
Because the dispatch script exited 5 before its copy step, the copy of `MUTANTS.md` and `m01..m16.diff` to `h2-mutants-run/` was done by hand with the script's
own commands (`cp -p` then `cmp`, lines 145-150 of dispatch_mutants.sh); all 17 files compared equal.
W3 spend: USD 0.2296 (all so far USD 0.8251 of the 4.50 cap).
