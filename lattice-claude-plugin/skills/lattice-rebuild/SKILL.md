---
name: lattice-rebuild
description: Use when generated or hand-written code fails Lattice checks, when asked to rebuild, regenerate, or fix findings against the spec, or when ./check.sh reports failures. Runs the read-findings, fix-code, rerun-check loop and never changes the spec to make a check pass.
---

# Lattice rebuild loop

1. Run `./check.sh` (or the `check_run` tool). Record the failing rule ids.
2. For each finding, read the rule with `rule_get` and the implementing code with `code_links`.
3. Fix the code, not the spec. Smallest change that satisfies the rule. Do not edit requirements, spec, or trace files, and do not weaken or delete tests/checks.
4. Rerun `./check.sh`. Repeat until it passes.
5. If the same finding survives three attempts, or the rule seems wrong or contradictory, stop. Use `change_propose` to suggest a spec change and tell the user to review it in Studio.
6. Report: rules fixed, files touched, final check result.

Prefer incremental edits and re-check after each group of fixes; do not rewrite whole modules.
