# Spikes: research and plan (2026-10-11)

Status: **research and plan only. No code is written or proposed to be written until the owner picks a spike.**
Source: session `perf-pen`, brainstorming of "can the Lattice spec drive performance work, an attack
swarm, a module marketplace, self-improvement and MCP intake?". Each spike is a feasibility question whose
output is a recommendation.

| # | Spike | Verdict | Doc |
|---|---|---|---|
| 1a | Performance improvement of a feature, steered by the spec | Yes, partly: spec is a correctness guard, not yet a target | `01a-feature-performance.md` |
| 1b | Attack / pentest swarm on the built app | Yes, as a **separate module outside the build gates** | `01b-security-attack-module.md` |
| 2 | Module marketplace (local or shared) | Yes; design exists and is parked; smallest proof is one descriptor | `02-module-marketplace.md` |
| 3 | Self-improving Lattice from use | Yes, if a signal log exists first; humans merge | `03-self-improving-lattice.md` |
| 4 | Connected MCPs available in Studio | Yes; small seam; one real unknown to test | `04-mcp-in-studio.md` |

## Decisions already taken by the owner (this session)

1. Attack / swarm testing is **outside the build plan and the build gates** (A, B, C, D, X): a
   completely separate module, run on the built system, scope **per feature or whole system, the user's
   choice**. (The owner's sentence was cut off after "separate module and"; to be confirmed.)
2. MCP: **use the MCP servers already configured in the Claude Code session; just make them available**
   in Studio. No new registry, no per-project config.
3. Project rules that apply to every spike: never use the Anthropic API key (CLI backend only);
   pre-register experiments and report blind vs post-hoc separately; agents never commit; never merge a
   rebuild automatically; run complete plans as an epg graph.

## Recommended order and dependencies

```
4 MCP intake ──────────────┐ (smallest; its tool-scoping idea is reused by 1b)
1a perf budget + timings ──┼─► 2 marketplace descriptor ─► 1b security module ─► 3 improve loop
                           └─ timings feed 3's signal log; module events feed 3
```

Why this order: 4 is a clean small seam with immediate value. 1a's per-request timings are already planned
(G1 in the build-performance phase) and both 1b and 3 need them. 1b is best built as the first real tenant
of 2, so 2's descriptor probe comes before it. 3 needs signals produced by 1a, 2 and 1b.

## Open questions across spikes

- Confirm decision 1's truncated sentence.
- Where should shared modules live (separate git index repo is the earlier recommendation)?
- Cost ceiling per attack run and per improve run?
- Is Docker acceptable as a requirement for engine and resource modules (earlier plan, decision 3)?
