# Results: multi-project plumbing (G8)

Pre-registration: `2026-10-15-multi-project-plumbing-prereg.md` (with Amendment 1). Branch `np-plumbing`.
Data: `docs/results/data/2026-10-15-multi-project-plumbing/`.

## What was built
- **Workspace discovery from the project:** when the flag, the environment and the launch directory find no
  manifest and `--project` is given, the MCP server walks up from the first project and uses a manifest
  that loads and declares that project; otherwise it says why on stderr and behaves as at a4ab4a1.
- **Read-only Studio routing:** for non-first projects the model, lint and change records are read from the
  hub (`<base>/p/<name>`, matched by real path); `ask`, `new_change` and `check` stay local. A 404 listing
  or no match falls back to local with one stderr line.
- **Guard walk-down:** a destructive command on a directory that contains a protected project below it is
  denied (breadth-first, depth 5, budget 4000 `scandir`s, fail open when spent). 13 of 30 table rows flip
  allow to deny; none flip the other way.

## Result: every prediction held
76/76 probes pass, each 10/10 identical (orchestrator re-run at 2 runs: 76/76). Mutants 17/17 killed
(control passes). Tests: `tests/mcp` 211 -> 276 (65 new, 0 failures); broad suite
`tests/mcp tests/workspace tests/contracts tests/studio tests/test_cli.py` 1031 -> 1096 passed;
`dogfood/studio/check.sh` PASS 435 cases; `dogfood/multi_mutants.py` MM1-MM6 caught;
`dogfood/contracts/fixes_probes.py` 6/6. Real hub, reported only: two projects listed by real path,
reads came through the hub, stderr empty.

## Findings
1. The cost bound C16 does not discriminate PM4 (no walk budget): the walk finishes in ~0.09 s anyway.
   PM4 is caught by G16 and a unit test.
2. A locally proposed change on a non-first project does not appear in that project's `change_list` until
   its Studio reloads state (a consequence of keeping `change_propose` local; not a bug).
3. `rmdir` is not in the guard's command list, so it is not guarded; adding it would widen the guard.

## Not done
The `rm -rf ~/Documents` reported-only probe was deliberately not run. The plugin README paragraph named in
the pre-registration was added by the orchestrator after the build.
