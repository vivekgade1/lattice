# Spike 2: module marketplace (support for new technologies, local or shared)

Status: research and plan. No code. The full architecture already exists and is **parked by the owner**;
this spike asks whether the parking gate is met and what the smallest proof is.

## Question

Can new technologies (a database, a cache, a framework, an in-house library, a security module) be added
to Lattice as installable modules, locally or from a shared index, without editing the core?

## What exists

- **Architecture:** `docs/superpowers/plans/2026-10-07-module-marketplace.md` (parked 2026-10-07). Module =
  pip package (+ optional Go module) with `lattice-module.toml`; kinds engine / resource / compute /
  framework / custom; SDK extension points (`Recognizer`, `FrontEnd`, `Engine`, `Resource`, `Contract`
  vocabulary, `Native`, `Check`, `Guide`); discovery by Python entry points; index = a git repo; trust tiers
  core / verified / community (community runs sandboxed); LLM-drafted modules gated by a conformance kit;
  phases M0 (SDK extraction) to M5 (self-extension).
- **Its stated parking gate:** finish experiment D, run Lattice end to end on one real project, extract
  interfaces only once **three real instances** exist.
- **Evidence since then:** an end-to-end real project (Streak, Go + MySQL) is running; MySQL was added as
  core code (`lattice/spec/db.py`, `dbserver.py`), as was the Go target (`gocheck`, `gogen`); the importer
  has front ends for FastAPI, Django, Flask; the Studio self-hosts (dogfood). That is arguably three real
  engine/target instances (SQLite, MySQL, Go), plus the security module as a fourth, different-kind tenant.
- **Today's seams that still require core edits:** `lattice/importer/frontends`, `lattice/targets.py`,
  `lattice/catalog.py`, `suite.py`, `probes.py` (from the plan's section 1).

## Findings

1. Gate is plausibly met for the **engine** kind; not for resource or compute (no real instance).
2. The plan's biggest cost is M0 (moving existing code behind extension points with no behavior change).
   Doing that for everything at once is a large refactor; a **descriptor-only** first slice avoids it.
3. A module that only *describes* existing behavior (a manifest naming an engine, its provision command,
   its recogniser imports, and a guide) is useful immediately for: Studio's catalog view, the draft
   prompts (`guide.md`), and as the registration point for the security module.
4. The risk the plan already names is the right one: modules run code inside the checker. Pin by hash,
   sandbox community tier, show the manifest before enabling.
5. "Local or shared" maps cleanly: local = a path entry; shared = a git index file with name, version,
   `lattice_api` range, artefact hash, trust tier.

## Design (recommended minimal slice)

- **Manifest first, SDK later.** Define `lattice-module.toml` (the plan's section 3 schema) and a loader
  that only reads it. Core modules (sqlite, mysql, go) get manifests that point to *existing* code.
- **Registration in the project:** `studio.toml` / project config lists modules by name and version or by
  path; Studio shows installed modules and what each provides.
- **Shared index:** a plain git repo with one metadata file per module; `lattice module search|info|install`
  from the plan's section 5, starting with install-from-path and install-from-git-url.
- **Security module** (spike 1b) is the first non-engine tenant: a `custom` kind with its own CLI entry.
- **Self-extension** (LLM-drafted modules) stays last; it reuses the conformance kit and a human review gate.

## Plan

| Phase | Work | Exit (objective) |
|---|---|---|
| M-R | Research only: list every core seam hit when adding MySQL and Go (read git history of those commits), classify each as manifest-able or SDK-needed | A table of seams with line counts; recommendation on SDK size |
| M-P | Descriptor probe, throwaway: write a manifest for MySQL over existing code, loader reads it, Studio lists it, project declares it | Project resolves engine via manifest, `./check.sh` result identical to before |
| M-L | Local install and shared index probe: path + git URL + index file | Install, pin by hash, uninstall; manifest shown before enable |
| M-S | Trust: community tier sandbox feasibility (subprocess, no network) | A module's provision step runs sandboxed with declared paths only |
| M-1 | Decide: continue to M0 (SDK extraction) per the parked plan, or keep descriptor-only | Owner decision with M-R/M-P evidence |

All probes pre-registered; results in `docs/results/`.

## Risks

- Premature SDK: extracting interfaces from 1-2 instances guesses wrong; descriptor-only limits the damage.
- Supply chain: installing third-party code into the checker. Hash pinning, signing for verified tier,
  sandbox for community, never read `.env`.
- Per-target support: every module construct must state per-target status (language rules R4, no drift
  across targets). Modules for one language must not silently cut down others.
- Docker dependency for engine/resource pilots; skip cleanly when it is not running.

## Open decisions for the owner

1. Un-park the plan for the descriptor slice only (recommended), or keep fully parked?
2. Index home: separate git repo (recommended earlier) or a directory in this repo for now?
3. Is the security module (1b) allowed to be the first non-core tenant?
