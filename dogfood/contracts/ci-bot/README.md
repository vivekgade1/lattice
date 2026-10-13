# ci-bot

A tiny consumer of the job scheduler API, the second half of the layer 3 (contracts between
projects) experiment: `docs/results/2026-10-13-contracts-prereg.md`.

- `client.py` logs in as `eve` (executor of application `alpha`), starts the newest version of a
  job, waits for the run and, on a timeout, lists the job's runs and cancels its own.
- `lattice.imports.toml` states what each call relies on (calls I1-I7): endpoint, status, auth,
  and the request and response fields it reads or sends.
- Check it against the provider's spec: `lattice contracts pin ci-bot` then
  `lattice contracts check ci-bot`, with a `lattice.workspace.toml` that links `ci-bot` to the
  provider project (`contract = "static"`). The provider here is job-scheduler at ef17cf8.
- `../golden/lattice.contracts.lock.json` is the lock pinned against that commit.

The client is never run by the checks: only its imports are read.
