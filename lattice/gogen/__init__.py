"""Go target checks: the pieces of the former Go code generator the checker still uses.

The Go code generator (`lattice gogen`: T2 procedures and endpoint plans to a Go binary, with
its Go runtime under rt/) was removed on 2026-10-12 with the language/codegen direction (it
stays in git history before that date). What remains is what `lattice.check.p8_target`
needs to decide whether a program can target Go: `regex` (the RE2-subset rules for a
pattern) and `types` (which T2 value kinds exist in Go).
"""
