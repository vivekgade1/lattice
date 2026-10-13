# streak-widget

A tiny consumer of the Streak API: the consumer of the layer 3 second-provider experiment
(`docs/results/2026-10-13-contracts-streak-prereg.md`).

- `client.py`: a home-screen widget (login as `ann`, `me`, `home`, habits, progress, check-in).
- `lattice.imports.toml`: what each call W1-W7 relies on (top-level fields only).
- Check: `lattice contracts pin|check streak-widget`, with a workspace linking it to Streak
  (`contract = "static"`; provider at a0db033).
- `../golden-streak/lattice.contracts.lock.json` is the lock pinned against that commit.
- The client is never run by the checks: only its imports are read.
