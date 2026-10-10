---
name: lattice-spec
description: Use when a project has a Lattice spec (requirements, spec, or trace files; a .lattice directory; ./check.sh) and the task involves behavior, requirements, rules, or findings. Treat the spec as the contract, read it through the lattice MCP tools, and request spec changes with change_propose instead of editing spec files.
---

# Lattice spec as the contract

The Lattice spec defines what the system must do. Code is judged against it.

## When to read it
- Before implementing or changing behavior: `model_get` for the model, `rule_get` for the rule text, `code_links` for where a rule is implemented.
- Before answering "what should this do": `spec_ask` and cite the rule ids it returns.
- After editing code: `check_run` (or `./check.sh`).

## Reading a finding
A finding has a layer, a rule id, a message, and usually a location. Fix the code the finding points at. Quote the rule id when reporting. If the finding looks wrong, say so to the user; do not silence it.

## Never edit spec files
Requirements, spec, and trace files are protected (a hook denies edits and shell writes). Do not try to work around the guard. If the spec itself must change:
1. Call `change_propose` with the request and a short rationale.
2. Tell the user a proposal exists (`change_get` shows it) and that they must review and accept it in Lattice Studio.
3. Continue only with work that does not depend on the change.

There is no accept or reject tool by design.
