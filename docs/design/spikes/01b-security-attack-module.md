# Spike 1b: security attack module (agent swarm against the built app)

Status: research and plan. No code.

## Question

Using the Lattice spec, can a swarm of agents attack the built app to find security holes (penetration
testing, security review), with scope chosen by the user per feature or for the whole system?

## Owner decisions (fixed)

- **Outside the build plan and outside the build gates (A, B, C, D, X).** Nothing in an epg build graph,
  `check.sh` or the rebuild loop depends on it; it can never block or edit a build.
- **A completely separate module** (see `02-module-marketplace.md`; first intended tenant).
- **Scope is the user's choice per run:** one feature or the whole system.
- (The sentence after "separate module and" was cut off; confirm.)

## What exists (evidence)

| Piece | Where | Relevance |
|---|---|---|
| The attack map | spec model: endpoints, roles, ownership (`owner_via`, L80x), bounds (L81x), enums, state machines, invariants (`lattice/spec/model.py`, `docs/lattice-reference.md`) | Tells an attacker where the trust boundaries are and which statements must hold. |
| Ground truth | a violated rule or invariant is a finding with a replayable request (layer C evaluates invariants after every request) | Gives a low-false-positive oracle, which pure "LLM pentest" lacks. |
| Adversarial precedent | layer X races writes behind count bounds and machines (`lattice/spec/race.py`) | The one existing layer that already attacks the app. It is probabilistic and reports "not refuted", never "verified". Same honesty model applies here. |
| Safe target | process runtime starts the app on loopback with a throwaway DB (`lattice/spec/process.py`, `dbserver.py`) | Attacks go only to a local throwaway instance. |
| Agent sessions and graphs | Studio sessions (`session.py`), plan runner (`plan.py`), durable state/resume (`state.py`) | Run shape for a swarm; reusable as a separate graph kind. |
| Known DSL gaps | cannot express request headers, arbitrary Authorization, carried values, response header reads (Streak backlog item 4) | Several real attack classes (token reuse, header injection, CORS) cannot be written as spec scenarios today. |

## What is missing

1. Attacker agent definitions, a request-script format, and a findings schema.
2. Spec-derived attack classes (the spec says who may call what; nothing derives "call it as someone who
   may not").
3. A confirmation step (replay) so agents cannot report plausible but false holes.
4. A scope and authorization guard (loopback only, no third-party targets).
5. A DSL/transport that can send arbitrary headers and tokens (backlog item 4).

## Attack classes derivable from the spec

| Class | Spec input | Example attack | Confirming oracle |
|---|---|---|---|
| Broken authz / IDOR | roles, ownership rules | user A reads, updates, deletes user B's rows by id | rule violated; response contains B's data |
| Missing auth | endpoints marked authenticated | call without token, with expired or malformed token | 2xx where spec says 401/403 |
| Input bounds and injection | bounds, enums, types | out-of-range, huge, wrong type, SQL/NoSQL/shell metacharacters | 5xx, schema violation, invariant broken |
| Mass assignment / over-posting | request model vs table columns | send fields the spec does not allow (role, owner id) | row changed outside stated fields |
| State-machine abuse | machines | illegal transitions, replay, out-of-order | state reachable that the machine forbids |
| Concurrency | count bounds, machines | double spend, double create (extends X, more intense) | invariant broken after race |
| Resource exhaustion / load | pagination caps | no-limit pages, deep nesting, rapid-fire | latency or memory blow-up, cap not enforced |
| Information leak | error rules, response model | verbose errors, stack traces, ids in errors | content match in error body |

Things the spec cannot see (dependency CVEs, TLS, deployment config, secrets in repo) are out of scope for
the spec-driven swarm; they need separate scanners. State this limit in every report.

## Design (recommended)

- **Module shape:** `lattice-mod-security` with its own manifest, CLI (`lattice attack`), Studio action and
  report. Core exposes only: read the spec model, start the throwaway app, a findings schema.
- **Run:** user picks scope (feature by rule ids or endpoints, or system), attack classes, and a budget
  (agents, minutes, tokens). Planner derives targets from the spec; one attacker agent per class in
  parallel; a judge replays each claimed finding against a fresh seed and keeps only reproduced ones;
  report lists repro request, violated rule, severity, and "not refuted" tallies per class.
- **Modes:** black-box (spec + HTTP) and white-box (plus read-only source). White-box finds more but is a
  weaker oracle for correctness; label findings by mode.
- **Output:** humans only. Findings never edit spec or code. A confirmed hole may be proposed as a new
  scenario via `change_propose` and fixed through the normal rebuild loop.
- **Perf/load:** the same runner, different agent class set (see 1a). Reports share one schema.

## Safety guardrails (must)

1. Only a local app started by the module on loopback with a throwaway database; refuse any other host.
2. Attacker agents have no shell and no network tools other than the target's address; source read-only.
3. CLI backend only, never the API key.
4. Attack logs and credentials from seeds are not written outside the run directory.
5. The module is documented as for the owner's own systems; it must not accept arbitrary URLs.

## Plan

| Phase | Work | Exit (objective, pre-registered) |
|---|---|---|
| S0 | Hand-written attack list from the Streak spec for ONE feature (auth). No agents. | List of attacks and the rule each violates; shows how much the spec already gives |
| S1 | Seeded-hole probe: a throwaway branch with one known hole (ownership check removed). One attacker agent, black-box. | Finds the seeded hole; zero findings on the clean tip over 3 runs; cost and wall time recorded |
| S2 | Swarm probe: all classes on the same feature, plus judge | Precision of confirmed findings, recall on 5 seeded holes (authz, bounds, mass-assign, state, race) |
| S3 | System scope on the whole built app | Wall time and cost; dedupe quality |
| S4 | Package as a module (needs spike 2's descriptor) | Installable, listed, scope flags work |

## Risks

- **Misuse:** an attack tool is dual-use. Mitigation: loopback-only target, no URL input, clear docs.
- **False positives** from agents: judge replay is mandatory.
- **Coverage illusion:** "no findings" is "not refuted by these attacks", never "secure". Report it so.
- **Cost:** a swarm on a whole system may be hours; budgets and per-class caps are required.
- **Prompt injection from the app's own responses** into attacker agents; treat responses as data, fence them.

## Open decisions for the owner

1. Confirm the cut-off sentence.
2. Black-box only first, or include white-box in S1?
3. Authorization wording for the module's docs and CLI banner.
