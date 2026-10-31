# Lattice spec as the contract

This project has a Lattice spec. The spec, not the code, says what the system must do.

- Read it through the `lattice` MCP tools: `model_get` for the model, `rule_get` for a rule's text,
  `spec_ask` for "what should this do" (cite the rule ids it returns), `code_links` for where a rule
  is implemented. Run `check_run` (or `./check.sh`) after you change code.
- Fix the code a finding points at. Quote its rule id. If a finding looks wrong, tell the user; do
  not silence it.
- Never edit `REQUIREMENTS.md`, `spec.py`, `spec.trace.json`, `check.sh` or `studio.toml`, with the
  editor or from the shell. A hook denies it. If the spec itself must change, call `change_propose`
  with the change in plain words, poll `change_get`, and tell the user to review and accept it in
  Lattice Studio. There is no accept tool by design.
- Do not try to get around the hook (copying the file, writing it through a script, changing the
  hook config). Continue only with work that does not depend on the proposed change.
